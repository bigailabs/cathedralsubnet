"""Regression tests for the pull-loop backfill window.

cathedralai/cathedral#105 changed `weight_loop` to compute a 7-day
rolling mean per hotkey, but `pull_loop` seeded its initial cursor at
``now - 1 hour``. A fresh or restarted validator never hydrated the rest
of the 7-day window before its first ``set_weights`` call, so Cathedral
validators on SN39 produced different ``weights_pre_burn`` vectors
depending on how long they'd been running.

A first attempt at the fix (PR #109 first pass) used `max(ran_at)` from
local DB to pick the cursor. That was wrong for the upgrade path:
existing validators with recent rows from the old `now - 1h` cursor
would resume from those recent rows and never walk the older end of the
new 7-day window. So Rizzo/Kraken/RT21 stayed divergent from TAO.com.

The current fix uses a **durable marker** in a new ``pull_loop_meta``
table: until a validator records that it has completed a 7-day backfill
under the current code version, every startup forces a seed at
``now - 7 days`` regardless of what's in ``pulled_eval_runs``. After
the first drained catch-up pass the marker is written, so subsequent
restarts use the cheap resume-from-local-max path.

This module pins:

1. Fresh DB → cursor seeds at ``now - 7 days``.
2. **Upgrade case**: DB has recent rows but no marker → still seed at
   ``now - 7 days``. This is the load-bearing case.
3. Marker present and fresh row inside window → resume from
   ``max(ran_at)``.
4. Marker present but max row older than window → reseed at
   ``now - 7 days``.
5. Repeated startup with marker present does not duplicate rows or
   skew score aggregates.
6. End-to-end: fresh validator backfill hydrates the weight window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cathedral.validator import pull_loop
from cathedral.validator.db import connect
from cathedral.validator.pull_loop import (
    _INITIAL_BACKFILL_DAYS,
    _backfill_completed_under_current_code,
    _initial_cursor,
    _mark_backfill_complete,
    latest_pulled_score_per_hotkey,
    upsert_pulled_eval,
)


@pytest.mark.asyncio
async def test_initial_cursor_on_fresh_db_returns_backfill_floor(tmp_path) -> None:
    """Case 1: empty DB, no marker → seed at ``now - 7 days``.

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
        parsed = datetime.fromisoformat(cursor_ran_at)
        assert before <= parsed <= after, (
            f"fresh DB must seed at now - {_INITIAL_BACKFILL_DAYS} days; "
            f"got {parsed}, expected between {before} and {after}"
        )
        assert not await _backfill_completed_under_current_code(conn), (
            "fresh DB must NOT report backfill complete before any pulls"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_upgrade_path_forces_backfill_despite_recent_rows(tmp_path) -> None:
    """**Load-bearing case** for the production upgrade story.

    Existing validators already have recent rows in ``pulled_eval_runs``
    from the old ``now - 1h`` cursor. After auto-update + restart they
    must still walk the older end of the new 7-day window — otherwise
    they stay divergent from TAO.com forever.

    Pre-fix (PR #109 first pass): max(ran_at) was 30 min ago, so the
    cursor resumed there and never backfilled days 1-7. Post-fix: no
    backfill marker present → force seed at now - 7d regardless of
    what's in pulled_eval_runs.
    """
    conn = await connect(str(tmp_path / "v.db"))
    try:
        recent = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        await upsert_pulled_eval(
            conn,
            eval_run={
                "id": "eval-pre-upgrade",
                "weighted_score": 0.8,
                "ran_at": recent,
            },
            miner_hotkey="hk-from-old-cursor",
        )

        # Sanity: no backfill marker yet (upgrade just happened).
        assert not await _backfill_completed_under_current_code(conn)

        cursor_ran_at, cursor_id = await _initial_cursor(conn)
        floor_dt = datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS)
        # Allow a 30-second jitter window — clock between assertion and
        # the helper's `datetime.now(UTC)` ticks slightly.
        lower = floor_dt - timedelta(seconds=30)
        upper = floor_dt + timedelta(seconds=30)
        parsed = datetime.fromisoformat(cursor_ran_at)
        assert lower <= parsed <= upper, (
            "upgrade path must seed at now - 7 days even with recent "
            f"rows present; got {parsed} (recent row was at {recent})"
        )
        assert cursor_id == "", (
            "upgrade path must reset cursor id to empty; tuple cursor "
            "needs to include rows older than the recent local one"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_marker_present_resumes_from_max_ran_at(tmp_path) -> None:
    """Case 3: backfill completed earlier → cheap resume path.

    Steady-state restart of a validator that already did the 7-day
    backfill at least once under the current code. The marker is
    present, so we trust local state and resume from max(ran_at).
    """
    conn = await connect(str(tmp_path / "v.db"))
    try:
        recent = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        await upsert_pulled_eval(
            conn,
            eval_run={
                "id": "eval-recent",
                "weighted_score": 0.7,
                "ran_at": recent,
            },
            miner_hotkey="hk-1",
        )
        await _mark_backfill_complete(conn)
        assert await _backfill_completed_under_current_code(conn)

        cursor_ran_at, cursor_id = await _initial_cursor(conn)
        assert cursor_ran_at == recent, (
            "marker-present restart must resume from max(ran_at), got "
            f"{cursor_ran_at} expected {recent}"
        )
        assert cursor_id == "eval-recent"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_marker_present_but_stale_max_resets_to_floor(tmp_path) -> None:
    """Case 4: marker present but local max is older than 7d → reseed.

    A validator down for >7d has no useful state in its
    ``pulled_eval_runs`` table for the current scoring window. Even
    with the marker present, the resume-from-max path would re-pull
    nothing useful and miss the new 7-day window. Reset to the floor.
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
        await _mark_backfill_complete(conn)

        cursor_ran_at, cursor_id = await _initial_cursor(conn)
        assert cursor_id == "", (
            "long-downtime reseed must clear the id portion; saved id "
            "is older than the backfill floor"
        )
        parsed = datetime.fromisoformat(cursor_ran_at)
        floor = datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS + 1)
        assert parsed > floor, (
            f"long-downtime restart must seed at now - {_INITIAL_BACKFILL_DAYS} days; got {parsed}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_repeated_startup_backfill_is_idempotent(tmp_path) -> None:
    """Repeated startups (with the marker) do not duplicate rows or skew scores.

    Even if the publisher re-serves the same row across multiple
    backfills (e.g. operator nukes the marker by hand to force a
    re-walk), ``upsert_pulled_eval``'s ON CONFLICT clause absorbs it.
    """
    conn = await connect(str(tmp_path / "v.db"))
    try:
        eval_run = {
            "id": "eval-rep",
            "weighted_score": 0.42,
            "ran_at": datetime.now(UTC).isoformat(),
        }
        # Simulate three startups each pulling the same row.
        for _ in range(3):
            await upsert_pulled_eval(conn, eval_run=eval_run, miner_hotkey="hk-1")
            await _mark_backfill_complete(conn)

        cur = await conn.execute(
            "SELECT COUNT(*) FROM pulled_eval_runs WHERE eval_run_id = ?",
            ("eval-rep",),
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 1, (
            f"upsert must dedupe by eval_run_id; got {row[0]} rows after 3 repeated startups"
        )

        scores = await latest_pulled_score_per_hotkey(conn, since_days=7)
        assert scores.get("hk-1") == pytest.approx(0.42), (
            f"repeated startup re-pulls must not skew the mean — got {scores.get('hk-1')}"
        )

        # Marker row count stays at 1 too (single key, repeated upsert).
        cur = await conn.execute("SELECT COUNT(*) FROM pull_loop_meta")
        row = await cur.fetchone()
        assert row is not None and row[0] == 1, (
            f"pull_loop_meta must keep one row per key; got {row[0]}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_production_upgrade_failure_pattern(tmp_path) -> None:
    """The exact failure observed in production:

    - Validator DB contains a recent eval from 30 minutes ago (left over
      from the pre-#109 ``now - 1h`` cursor era).
    - Publisher has an older positive eval from 4 days ago that the
      validator never saw.
    - On startup, pull_loop must request from ``now - 7d``, not from
      the recent local row.
    - After pull, ``latest_pulled_score_per_hotkey(..., since_days=7)``
      includes both hotkeys.
    """
    conn = await connect(str(tmp_path / "v.db"))
    try:
        # Pre-existing recent row from old code era (no backfill marker).
        recent = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        await upsert_pulled_eval(
            conn,
            eval_run={
                "id": "eval-recent",
                "weighted_score": 0.5,
                "ran_at": recent,
            },
            miner_hotkey="hk-existing",
        )

        # Confirm the cursor _initial_cursor returns is the 7-day floor,
        # NOT the recent row. This is the load-bearing assertion.
        cursor_ran_at, cursor_id = await _initial_cursor(conn)
        floor_dt = datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS)
        parsed = datetime.fromisoformat(cursor_ran_at)
        assert parsed < datetime.fromisoformat(recent), (
            "cursor must point BEFORE the recent local row so the older "
            f"window gets pulled; got cursor={parsed}, recent={recent}"
        )
        assert abs((parsed - floor_dt).total_seconds()) < 30, (
            f"cursor must be at the 7-day floor (~{floor_dt}); got {parsed}"
        )
        assert cursor_id == ""

        # Simulate the publisher serving an older positive eval that
        # the pull loop now picks up because the cursor was reset.
        four_days_ago = (datetime.now(UTC) - timedelta(days=4)).isoformat()
        await upsert_pulled_eval(
            conn,
            eval_run={
                "id": "eval-older-backfilled",
                "weighted_score": 0.9,
                "ran_at": four_days_ago,
            },
            miner_hotkey="hk-backfilled",
        )

        scores = await latest_pulled_score_per_hotkey(conn, since_days=7)
        assert "hk-existing" in scores, "recent row's hotkey must survive"
        assert "hk-backfilled" in scores, (
            "backfilled older hotkey must appear in the 7-day score aggregator after backfill"
        )
        assert scores["hk-existing"] == pytest.approx(0.5)
        assert scores["hk-backfilled"] == pytest.approx(0.9)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fresh_validator_backfill_hydrates_weight_window(tmp_path) -> None:
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

    seed_ran_at, _seed_id = await _initial_cursor(conn)
    parsed = datetime.fromisoformat(seed_ran_at)
    floor = datetime.now(UTC) - timedelta(days=_INITIAL_BACKFILL_DAYS + 1)
    assert parsed > floor

    pull_loop.verify_eval_output_signature(payload_entry, pk)
    await upsert_pulled_eval(
        conn,
        eval_run=payload_entry,
        miner_hotkey=output_card["worker_owner_hotkey"],
    )

    scores = await latest_pulled_score_per_hotkey(conn, since_days=7)
    assert scores.get("5HotkeyOfMasonBackfilledByFreshValidator") == pytest.approx(0.66)

    await conn.close()
