"""Back-compat smoke tests: v1.0.7 validator <-> v1.1.0 publisher.

On v1.1.0 deploy day, every SN39 validator is running a v1.0.7 binary. The
publisher must keep serving them correctly until they PM2-cycle to v1.1.0.
The audit at INBOX/2026-05-12-track-3-pull-cursor-audit.md walked through
why this is silently dangerous: a wire-shape break here means validators
persist nothing, the weight loop keeps voting for the subnet owner, and
nobody notices for hours.

Each test below asserts one piece of the v1.0.7-readable contract against
the live dockerized v1.1.0 publisher.

These tests are SKIPPED when docker is unavailable — see conftest. A green
test run with all 5 skipped is a valid result on a laptop without docker;
a green test run with all 5 passing is what CI is supposed to produce on
every PR to this branch.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.smoke.conftest import (
    DOCKER_AVAILABLE,
    PublisherFixture,
    load_v107_pull_loop,
    make_signed_eval_output,
    seed_eval_run,
)

pytestmark = pytest.mark.skipif(
    not DOCKER_AVAILABLE,
    reason="docker not available — smoke tests require a running daemon",
)


# --------------------------------------------------------------------------
# Helpers shared across tests
# --------------------------------------------------------------------------


def _public_key_from_hex(hex_str: str) -> Any:
    """Decode a 32-byte hex Ed25519 public key into a cryptography object.

    The v1.0.7 verifier expects an Ed25519PublicKey instance.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_str))


def _v107_fetcher(base_url: str) -> Any:
    """Build an httpx-backed fetcher with the v1.0.7 call signature.

    v1.0.7 sends GET /v1/leaderboard/recent?since=<iso>&limit=<int>. The
    `since` arg is what the validator threads across pulls — None on the
    very first call, then the publisher's `next_since` thereafter.
    """

    async def fetch(since: str | None = None, limit: int = 200) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if since is not None:
            params["since"] = since
        else:
            # v1.1.0's endpoint rejects requests with no cursor at all.
            # The real v1.0.7 loop seeds `last_seen` with `now - 1h` on
            # boot (pull_loop.py:177 in the v1.0.7 tag), so we mirror
            # that behavior here.
            params["since"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base_url}/v1/leaderboard/recent", params=params)
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            return body

    return fetch


# --------------------------------------------------------------------------
# Test 1 — legacy string cursor still drives a working pull
# --------------------------------------------------------------------------


def test_v107_validator_can_pull_with_legacy_string_cursor(
    smoke_stack: PublisherFixture,
) -> None:
    """v1.0.7 sends `?since=<iso>` only. The publisher must return a
    parseable `items` array, the legacy `next_since` key MUST be present
    in the response body (even if null when caught up), and every row
    must verify against the cathedral pubkey + persist via v1.0.7's
    pull loop.
    """
    base_ran_at = datetime.now(UTC) - timedelta(minutes=5)
    sk = smoke_stack.keys.private_key

    # Seed 5 rows, each ms-spaced so the v1.0.7 cursor's ran_at-only
    # advancement still threads forward without collisions.
    seeded_ids: list[str] = []
    for i in range(5):
        ran_at_iso = (base_ran_at + timedelta(milliseconds=i * 10)).isoformat()
        # Match scoring_pipeline._ms_iso (ms precision + Z).
        ran_at_iso = (
            (base_ran_at + timedelta(milliseconds=i * 10)).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{((base_ran_at + timedelta(milliseconds=i * 10)).microsecond // 1000):03d}"
            + "Z"
        )
        signed = make_signed_eval_output(
            sk, idx=i, ran_at_iso=ran_at_iso, miner_hotkey="5HotkeyTest1"
        )
        seeded_ids.append(signed["id"])
        seed_eval_run(
            smoke_stack.db_path,
            signed=signed,
            submission_id=signed["agent_id"],
        )

    # Direct shape assertion: legacy `next_since` must be a present key.
    fetcher = _v107_fetcher(smoke_stack.base_url)
    # since=1h ago covers our 5-minute-old rows.
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    raw = asyncio.run(fetcher(since=since, limit=200))

    assert "items" in raw, "publisher response missing `items` array"
    assert "next_since" in raw, (
        "publisher response missing legacy `next_since` key — "
        "v1.0.7 validators read this field to thread the cursor"
    )
    assert isinstance(raw["items"], list), f"`items` is not a list: {type(raw['items'])!r}"
    returned_ids = {item["id"] for item in raw["items"]}
    for seeded in seeded_ids:
        assert seeded in returned_ids, (
            f"seeded row {seeded} did not appear in publisher response; "
            f"got {len(returned_ids)} rows"
        )

    # Now drive v1.0.7's actual pull_once against the live publisher and
    # assert it persists all 5 rows via its own sink.
    v107 = load_v107_pull_loop()
    v107.reset_pull_cursor()
    persisted: list[dict[str, Any]] = []

    async def sink(item: dict[str, Any]) -> None:
        persisted.append(item)

    pubkey = _public_key_from_hex(smoke_stack.public_key_hex)
    v107.pull_once(fetcher, sink, pubkey, limit=200)

    persisted_ids = {row["id"] for row in persisted}
    for seeded in seeded_ids:
        assert seeded in persisted_ids, (
            f"v1.0.7 pull_loop failed to persist seeded row {seeded}; "
            f"persisted {len(persisted_ids)} rows: {persisted_ids}"
        )


# --------------------------------------------------------------------------
# Test 2 — v1-signed payload verifies on v1.0.7's verifier
# --------------------------------------------------------------------------


def test_v107_validator_verifies_v1_signed_payload(
    smoke_stack: PublisherFixture,
) -> None:
    """The cathedral signature must verify cleanly on v1.0.7's verifier.

    v1.0.7's _SIGNED_EVAL_OUTPUT_KEYS is the 9-key set: id, agent_id,
    agent_display_name, card_id, output_card, output_card_hash,
    weighted_score, polaris_verified, ran_at. If the v1.1.0 publisher
    ever drifts to a 10-key (or 8-key) signing set, every row v1.0.7
    pulls fails verification and the validator persists nothing — the
    exact silent-loss failure mode this smoke suite guards against.
    """
    sk = smoke_stack.keys.private_key
    ran_at_iso = (datetime.now(UTC) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    signed = make_signed_eval_output(sk, idx=42, ran_at_iso=ran_at_iso, miner_hotkey="5HotkeyTest2")
    seed_eval_run(
        smoke_stack.db_path,
        signed=signed,
        submission_id=signed["agent_id"],
    )

    fetcher = _v107_fetcher(smoke_stack.base_url)
    raw = asyncio.run(
        fetcher(
            since=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            limit=200,
        )
    )
    seeded_on_wire = next(
        (item for item in raw["items"] if item["id"] == signed["id"]),
        None,
    )
    assert seeded_on_wire is not None, (
        f"seed row {signed['id']} did not appear in publisher response"
    )

    v107 = load_v107_pull_loop()
    pubkey = _public_key_from_hex(smoke_stack.public_key_hex)

    # v1.0.7's verify_eval_output_signature MUST accept the wire row
    # without raising — this is the load-bearing signature contract.
    v107.verify_eval_output_signature(seeded_on_wire, pubkey)

    # End-to-end: pulling through v1.0.7 must persist the row.
    v107.reset_pull_cursor()
    persisted: list[dict[str, Any]] = []

    async def sink(item: dict[str, Any]) -> None:
        persisted.append(item)

    v107.pull_once(fetcher, sink, pubkey, limit=200)
    assert any(row["id"] == signed["id"] for row in persisted), (
        f"v1.0.7 pull_loop did not persist v1-signed row {signed['id']}"
    )


# --------------------------------------------------------------------------
# Test 3 — v1.1.0-specific response fields don't break v1.0.7
# --------------------------------------------------------------------------


def test_v107_validator_ignores_v110_specific_response_fields(
    smoke_stack: PublisherFixture,
) -> None:
    """v1.1.0 publisher emits `next_since_ran_at` and `next_since_id`
    alongside the legacy `next_since`. v1.0.7 must NOT crash on the
    unknown keys — its parser uses `payload.get("next_since")` and
    quietly ignores everything else. Verify both:

    1. The raw response carries all three cursor fields when saturated.
    2. v1.0.7's pull_once consumes the response without raising and
       without dropping rows.
    """
    sk = smoke_stack.keys.private_key
    # Seed 3 rows so the limit=2 page is saturated and the publisher
    # emits non-null cursor fields.
    seeded_ids: list[str] = []
    base = datetime.now(UTC) - timedelta(minutes=10)
    for i in range(3):
        ran_at_iso = (
            (base + timedelta(milliseconds=i * 20)).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{((base + timedelta(milliseconds=i * 20)).microsecond // 1000):03d}"
            + "Z"
        )
        signed = make_signed_eval_output(
            sk, idx=200 + i, ran_at_iso=ran_at_iso, miner_hotkey="5HotkeyTest3"
        )
        seeded_ids.append(signed["id"])
        seed_eval_run(
            smoke_stack.db_path,
            signed=signed,
            submission_id=signed["agent_id"],
        )

    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    # Direct httpx GET — assert all 3 cursor fields present on a saturated
    # response. This is the publisher-side dual-publish contract.
    async def grab() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{smoke_stack.base_url}/v1/leaderboard/recent",
                params={"since": since, "limit": 2},
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            return body

    raw = asyncio.run(grab())
    assert "next_since" in raw, "legacy next_since missing"
    assert "next_since_ran_at" in raw, "v1.1.0 next_since_ran_at missing"
    assert "next_since_id" in raw, "v1.1.0 next_since_id missing"
    # On a saturated page (limit=2, seeded 3 rows), all three must be
    # non-null. The legacy field is what v1.0.7 reads to advance.
    assert raw["next_since"] is not None, (
        "legacy next_since is null on a saturated page — v1.0.7 fleet "
        "will stall the cursor and re-poll the same window forever"
    )
    assert raw["next_since_ran_at"] is not None
    assert raw["next_since_id"] is not None

    # And v1.0.7's pull_once consumes it cleanly across multiple ticks.
    v107 = load_v107_pull_loop()
    v107.reset_pull_cursor()
    pubkey = _public_key_from_hex(smoke_stack.public_key_hex)
    fetcher = _v107_fetcher(smoke_stack.base_url)
    persisted: list[dict[str, Any]] = []

    async def sink(item: dict[str, Any]) -> None:
        persisted.append(item)

    # Two ticks — first saturates at 2/2, second drains the remaining row.
    v107.pull_once(fetcher, sink, pubkey, limit=2)
    v107.pull_once(fetcher, sink, pubkey, limit=2)

    persisted_ids = {row["id"] for row in persisted}
    for seeded in seeded_ids:
        assert seeded in persisted_ids, (
            f"v1.0.7 dropped row {seeded} when v1.1.0 emitted extra "
            f"cursor fields; persisted {sorted(persisted_ids)}"
        )


# --------------------------------------------------------------------------
# Test 4 — ms-collision burst still drains via UPSERT dedupe
# --------------------------------------------------------------------------


def test_v107_validator_handles_ms_collision_via_dedupe(
    smoke_stack: PublisherFixture,
) -> None:
    """The audit's Risk 2 scenario: 250 rows at the same millisecond.

    v1.0.7 lacks the tuple cursor fix. Its only forward-progress mechanism
    is `last_seen = items[-1].ran_at` plus the publisher's row-tuple
    comparison. UPSERT on `pulled_eval_runs.eval_run_id` is supposed to
    cover boundary-row re-delivery.

    The honest test question this exercises: does the v1.1.0 publisher's
    `(ran_at, id) > (since, '')` semantics actually let a v1.0.7 cursor
    walk through a ms-collision burst? If v1.0.7's `last_seen` advances
    to the burst's ran_at and stays there forever (the publisher's
    next page always re-matches the same `id > ''` predicate), only the
    first page worth of rows reaches the validator. The remaining 150
    silently lag — same silent-loss failure mode the audit warned about,
    just on the v1.0.7 side of the wire.

    If this test fails, the deploy plan needs a publisher-side workaround
    (or guaranteed no ms-collision bursts during the rollout window
    before the v1.0.7 fleet PM2-cycles to v1.1.0). Do NOT fix in this
    branch — surface to the validator-compat agent.
    """
    sk = smoke_stack.keys.private_key
    ran_at_iso = (datetime.now(UTC) - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # 250 rows, all at the same ms. Use a unique idx range so we don't
    # collide with rows seeded by other tests in the same session.
    seeded_ids: list[str] = []
    for i in range(250):
        signed = make_signed_eval_output(
            sk,
            idx=500 + i,
            ran_at_iso=ran_at_iso,
            miner_hotkey="5HotkeyTest4",
        )
        seeded_ids.append(signed["id"])
        seed_eval_run(
            smoke_stack.db_path,
            signed=signed,
            submission_id=signed["agent_id"],
        )

    v107 = load_v107_pull_loop()
    v107.reset_pull_cursor()
    pubkey = _public_key_from_hex(smoke_stack.public_key_hex)
    # Use a v1.0.7-shaped fetcher with limit=100 so the burst spans
    # multiple pages and the cursor has to thread through.
    fetcher = _v107_fetcher(smoke_stack.base_url)
    persisted: list[dict[str, Any]] = []

    async def sink(item: dict[str, Any]) -> None:
        persisted.append(item)

    # Seed v1.0.7's cursor with a value that includes our ms-collision
    # window. v1.0.7's pull_once starts with `since=None` and the real
    # v1.0.7 loop seeds it to `now - 1h`; we mirror that behavior via
    # the fetcher, which translates None into 1h-ago.
    seeded_set = set(seeded_ids)
    for _tick in range(30):
        v107.pull_once(fetcher, sink, pubkey, limit=100)
        # Dedupe in the assertion below — v1.0.7's UPSERT dedupes on
        # eval_run_id, the sink dedup we do here mirrors that.
        if seeded_set.issubset({row["id"] for row in persisted}):
            break
    else:
        pytest.fail(
            f"v1.0.7 pull loop did not drain 250 ms-colliding rows in 30 "
            f"ticks; persisted {len({row['id'] for row in persisted})} "
            f"unique ids out of 250"
        )

    # Final assertion: every seeded row was persisted at least once.
    persisted_ids = {row["id"] for row in persisted}
    missing = seeded_set - persisted_ids
    assert not missing, (
        f"v1.0.7 pull loop missed {len(missing)} of 250 ms-colliding "
        f"rows after drain: sample missing={list(missing)[:5]}"
    )


# --------------------------------------------------------------------------
# Test 5 — the publisher-side dual-publish contract assertion
# --------------------------------------------------------------------------


def test_v110_publisher_legacy_cursor_field_present(
    smoke_stack: PublisherFixture,
) -> None:
    """The deploy-day contract: GET /v1/leaderboard/recent on a saturated
    page MUST return all four of `items`, `next_since`, `next_since_ran_at`,
    `next_since_id`. If `next_since` is absent or null on a saturated page,
    every v1.0.7 validator stalls the cursor and re-polls the same window
    forever — the exact silent-loss failure mode flagged in the audit.

    Seed 2 rows and request limit=1 so the page is guaranteed saturated.
    """
    sk = smoke_stack.keys.private_key
    base = datetime.now(UTC) - timedelta(minutes=30)
    seeded_ids: list[str] = []
    for i in range(2):
        ran_at_iso = (
            (base + timedelta(milliseconds=i * 50)).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{((base + timedelta(milliseconds=i * 50)).microsecond // 1000):03d}"
            + "Z"
        )
        signed = make_signed_eval_output(
            sk, idx=900 + i, ran_at_iso=ran_at_iso, miner_hotkey="5HotkeyTest5"
        )
        seeded_ids.append(signed["id"])
        seed_eval_run(
            smoke_stack.db_path,
            signed=signed,
            submission_id=signed["agent_id"],
        )

    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    async def grab() -> httpx.Response:
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.get(
                f"{smoke_stack.base_url}/v1/leaderboard/recent",
                params={"since": since, "limit": 1},
            )

    resp = asyncio.run(grab())
    assert resp.status_code == 200, f"publisher returned {resp.status_code}: {resp.text}"
    body = resp.json()

    # All four fields must be present.
    for key in ("items", "next_since", "next_since_ran_at", "next_since_id"):
        assert key in body, (
            f"deploy-blocker: /v1/leaderboard/recent response is missing "
            f"`{key}` — v1.0.7 validators expect this field"
        )

    # Saturated page (limit=1, >=2 seeded rows) — the legacy cursor MUST
    # be a non-null string. If this fails, the v1.0.7 fleet stalls on
    # deploy day.
    assert body["next_since"] is not None, (
        "deploy-blocker: legacy `next_since` is null on a saturated page. "
        "v1.0.7 validators read this exact field to advance their cursor; "
        "a null value means their cursor never moves and they re-poll the "
        "same 1-hour window indefinitely while the actual eval feed silently "
        "outruns them. Fix in src/cathedral/publisher/reads.py: ensure the "
        "saturated-page branch sets `next_since_legacy = items[-1]['ran_at']`."
    )
    assert isinstance(body["next_since"], str), (
        f"legacy next_since must be a string, got {type(body['next_since'])!r}"
    )
    # And the v1.1.0 tuple cursor fields must also be populated when
    # saturated — they are what v1.1.0 validators will use post-cycle.
    assert body["next_since_ran_at"] is not None, "v1.1.0 next_since_ran_at null on saturated page"
    assert body["next_since_id"] is not None, "v1.1.0 next_since_id null on saturated page"
