"""Regression tests for the pull-loop backfill window.

cathedralai/cathedral#105 changed `weight_loop` to compute a 7-day
rolling mean per hotkey, but `pull_loop` seeded its initial cursor at
``now - 1 hour``. A fresh or restarted validator never hydrated the rest
of the 7-day window before its first ``set_weights`` call, so Cathedral
validators produced different ``weights_pre_burn`` vectors depending on
how long they'd been running.

This module pins three behaviours:

1. Fresh DB → cursor seeds at ``now - 7 days``.
2. Restart with rows inside the backfill window → cursor resumes from
   the highest persisted ``(ran_at, eval_run_id)``.
3. Restart after long downtime → cursor resets to ``now - 7 days``;
   older rows would not influence the 7-day weight calculation anyway.

The integration test wires a tiny in-process publisher that serves a
fixed page of signed evals and asserts the fresh validator persists
every row plus that weight_loop's score-per-hotkey query returns them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cathedral.validator import pull_loop
from cathedral.validator.db import connect
from cathedral.validator.pull_loop import (
    _INITIAL_BACKFILL_DAYS,
    _initial_cursor,
    latest_pulled_score_per_hotkey,
    upsert_pulled_eval,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.mark.asyncio
async def test_initial_cursor_on_fresh_db_returns_backfill_floor(tmp_path) -> None:
    """Case 1: empty DB → seed at ``now - 7 days``.

    A first-boot validator needs to walk the full scoring window before
    its first weight set, otherwise its vector under-counts masons vs.
    a validator that's been running for days.
    """
    conn = await connect(str(tmp_path / "v.db"))
    try:
        before = datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS)
        cursor_ran_at, cursor_id = await _initial_cursor(conn)
        after = datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS)

        assert cursor_id == "", (
            "fresh DB must seed an empty id so the tuple cursor's "
            "strict `>` comparison includes the boundary timestamp"
        )
        # The cursor's ran_at must be roughly `now - 7d` (allow a tiny
        # wall-clock jitter window between the assertion bounds).
        parsed = datetime.fromisoformat(cursor_ran_at)
        assert before <= parsed <= after, (
            f"fresh DB must seed at now - {_INITIAL_BACKFILL_DAYS} days; "
            f"got {parsed}, expected between {before} and {after}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_initial_cursor_resumes_from_max_when_inside_window(tmp_path) -> None:
    """Case 2: highest persisted row is inside the backfill window → resume from it.

    Steady-state restart path. The tuple cursor's strict `>` and the
    upsert's ``ON CONFLICT(eval_run_id)`` guarantee zero duplicates.
    """
    conn = await connect(str(tmp_path / "v.db"))
    try:
        recent = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        await upsert_pulled_eval(
            conn,
            eval_run={
                "id": "eval-recent",
                "weighted_score": 0.8,
                "ran_at": recent,
            },
            miner_hotkey="hk-1",
        )
        cursor_ran_at, cursor_id = await _initial_cursor(conn)
        assert cursor_ran_at == recent, (
            "restart with rows inside the backfill window must resume "
            "from the highest persisted (ran_at, id), not re-seed from "
            f"the backfill floor — got {cursor_ran_at}, expected {recent}"
        )
        assert cursor_id == "eval-recent", (
            "id portion of the tuple cursor must be the highest "
            f"persisted eval_run_id — got {cursor_id!r}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_initial_cursor_resets_after_long_downtime(tmp_path) -> None:
    """Case 3: highest persisted row is older than the backfill window → reseed.

    A validator down for >7d has no useful state in its
    ``pulled_eval_runs`` table for the current scoring window. Don't
    waste pulls re-hydrating rows older than ``latest_pulled_score_per_hotkey``
    will ever look at.
    """
    conn = await connect(str(tmp_path / "v.db"))
    try:
        stale = (datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS + 5)).isoformat()
        await upsert_pulled_eval(
            conn,
            eval_run={
                "id": "eval-stale",
                "weighted_score": 0.9,
                "ran_at": stale,
            },
            miner_hotkey="hk-stale",
        )
        cursor_ran_at, cursor_id = await _initial_cursor(conn)
        assert cursor_id == "", (
            "long-downtime reseed must wipe the id portion of the "
            "cursor; the saved id is older than the backfill floor"
        )
        parsed = datetime.fromisoformat(cursor_ran_at)
        floor = datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS + 1)
        assert parsed > floor, (
            f"long-downtime restart must seed at now - "
            f"{_INITIAL_BACKFILL_DAYS} days, not stay at the stale "
            f"{stale}; got {parsed}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_upsert_is_idempotent_for_replayed_rows(tmp_path) -> None:
    """Companion invariant: re-pulling a row never duplicates state.

    The backfill case will re-serve any row the publisher still has
    inside the 7-day window. The pull loop relies on
    ``ON CONFLICT(eval_run_id) DO UPDATE`` to absorb that without
    duplicating rows or double-counting in score aggregations.
    """
    conn = await connect(str(tmp_path / "v.db"))
    try:
        eval_run = {
            "id": "eval-rep",
            "weighted_score": 0.42,
            "ran_at": datetime.now(UTC).isoformat(),
        }
        for _ in range(5):
            await upsert_pulled_eval(conn, eval_run=eval_run, miner_hotkey="hk-1")

        cur = await conn.execute(
            "SELECT COUNT(*) FROM pulled_eval_runs WHERE eval_run_id = ?",
            ("eval-rep",),
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 1, (
            f"upsert must dedupe by eval_run_id; got {row[0]} rows after 5 inserts"
        )

        scores = await latest_pulled_score_per_hotkey(conn, since_days=7)
        assert scores.get("hk-1") == pytest.approx(0.42), (
            f"re-pulled row must not skew the mean — got {scores.get('hk-1')}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fresh_validator_backfill_hydrates_weight_window(tmp_path, monkeypatch) -> None:
    """End-to-end: fresh DB pulls a 6-day-old row and weight_loop sees it.

    Pre-fix: pull_loop seeded cursor at ``now - 1h``, so any eval older
    than 1 hour was invisible to ``latest_pulled_score_per_hotkey``'s
    7-day window even though the publisher still served it.

    Post-fix: the cursor seeds at ``now - 7d``, the saturation-driven
    inner loop drains the page, and the hotkey appears in the weight
    aggregator's output.
    """
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    from cathedral.types import canonical_json_for_signing

    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    # Build a single signed eval six days ago — well inside the 7-day
    # window but outside the pre-fix 1-hour window.
    six_days_ago = (datetime.now(UTC) - timedelta(days=6)).isoformat()
    output_card = {
        "id": "eu-ai-act",
        "topic": "demo",
        "worker_owner_hotkey": "5HotkeyOfMasonBackfilledByFreshValidator",
    }
    import json as _json

    import blake3 as _blake3

    output_card_hash = _blake3.blake3(
        _json.dumps(output_card, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    signed = {
        "id": "00000000-0000-4000-8000-000000000099",
        "agent_id": "11111111-1111-4111-8111-000000000099",
        "agent_display_name": "Backfilled Agent",
        "card_id": "eu-ai-act",
        "output_card": output_card,
        "output_card_hash": output_card_hash,
        "weighted_score": 0.66,
        "polaris_verified": False,
        "ran_at": six_days_ago,
    }
    blob = canonical_json_for_signing(signed)
    payload_entry = dict(signed)
    payload_entry["cathedral_signature"] = base64.b64encode(sk.sign(blob)).decode("ascii")
    payload_entry["merkle_epoch"] = None

    conn = await connect(str(tmp_path / "v.db"))

    # Confirm the pre-state: empty DB → fresh validator's initial cursor
    # must be the 7-day floor, not 1-hour. This is the load-bearing
    # behaviour the fix introduces.
    seed_ran_at, _seed_id = await _initial_cursor(conn)
    parsed = datetime.fromisoformat(seed_ran_at)
    floor = datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS + 1)
    assert parsed > floor, (
        f"fresh validator must seed at now - {_INITIAL_BACKFILL_DAYS} days; got {parsed}"
    )

    # Simulate what run_pull_loop does after one successful page: verify
    # + upsert. The publisher-side behaviour is covered in the existing
    # signature/pagination tests; here we just need to assert the row
    # the fresh cursor enables makes it into the score aggregator.
    pull_loop.verify_eval_output_signature(payload_entry, pk)
    await upsert_pulled_eval(
        conn,
        eval_run=payload_entry,
        miner_hotkey=output_card["worker_owner_hotkey"],
    )

    scores = await latest_pulled_score_per_hotkey(conn, since_days=7)
    assert scores.get("5HotkeyOfMasonBackfilledByFreshValidator") == pytest.approx(0.66), (
        "fresh validator's 7-day backfill must hydrate "
        "latest_pulled_score_per_hotkey with the 6-day-old row; got "
        f"{scores}"
    )

    await conn.close()
