"""Validator pull loop — CONTRACTS.md §6 + ARCHITECTURE_V1 §"end-to-end flow" step 8.

The validator polls cathedral's `GET /v1/leaderboard/recent?since=…`,
verifies the cathedral Ed25519 signature on each EvalOutput, persists
verified entries to its local DB, and the weight loop reads from there.

Test approach:
- Hand-rolled fake "cathedral" that returns a fixed leaderboard payload.
- Real Ed25519 signing for both good and bad signatures.
- Verify the loop's observable effects: rows inserted, bad-sig entries
  skipped + logged.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from cathedral.types import canonical_json_for_signing

# --------------------------------------------------------------------------
# Fake cathedral server
# --------------------------------------------------------------------------


def make_signed_eval_output(sk: Ed25519PrivateKey, *, idx: int = 0) -> dict[str, Any]:
    """Build a contract-shaped EvalOutput (§1.10 + L8) signed by the fake cathedral.

    The signature covers the immutable projection. `merkle_epoch` is appended
    after signing — it gets populated by the weekly merkle close job and
    therefore cannot be part of the bytes the cathedral signs (CRIT-7).
    `output_card_hash` IS in the signed bytes (locked decision L8 — frontend
    + validators rely on it as the visible trust-chain anchor).
    """
    output_card = {"id": "eu-ai-act", "topic": "demo"}
    import blake3 as _blake3
    import json as _json

    output_card_hash = _blake3.blake3(
        _json.dumps(output_card, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    signed = {
        "id": f"00000000-0000-4000-8000-{idx:012d}",
        "agent_id": f"11111111-1111-4111-8111-{idx:012d}",
        "agent_display_name": f"Agent {idx}",
        "card_id": "eu-ai-act",
        "output_card": output_card,
        "output_card_hash": output_card_hash,
        "weighted_score": 0.5 + 0.01 * idx,
        "polaris_verified": False,
        "ran_at": "2026-05-10T12:00:00.000Z",
    }
    blob = canonical_json_for_signing(signed)
    sig = base64.b64encode(sk.sign(blob)).decode("ascii")
    payload = dict(signed)
    payload["cathedral_signature"] = sig
    payload["merkle_epoch"] = None  # post-anchor metadata, NOT in signed bytes
    return payload


def tamper_signature(entry: dict[str, Any]) -> dict[str, Any]:
    """Flip a byte to corrupt the signature without changing the payload."""
    sig = base64.b64decode(entry["cathedral_signature"])
    tampered = bytes([sig[0] ^ 0x01]) + sig[1:]
    entry = dict(entry)
    entry["cathedral_signature"] = base64.b64encode(tampered).decode("ascii")
    return entry


# --------------------------------------------------------------------------
# Locate the pull loop module
# --------------------------------------------------------------------------


def _find_pull_loop() -> Any | None:
    for name in (
        "cathedral.validator.pull_loop",
        "cathedral.validator.pull",
        "cathedral.validator.cathedral_pull",
    ):
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    return None


@pytest.fixture
def pull_loop_module():
    mod = _find_pull_loop()
    if mod is None:
        pytest.skip(
            "validator pull loop not importable yet — implementer must "
            "expose cathedral.validator.pull_loop per CONTRACTS.md §6 "
            "(validator → cathedral leaderboard sync)"
        )
    return mod


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_pull_loop_persists_verified_entries(pull_loop_module, alice_keypair, monkeypatch):
    """ARCHITECTURE step 8: validator pulls + verifies + persists.

    The implementer should expose a `pull_once(...)` (or similar) entry
    point that takes a fetcher + a sink and runs one cycle. We try the
    most plausible signatures.
    """
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    entries = [make_signed_eval_output(sk, idx=i) for i in range(3)]

    async def fake_fetch(since: str | None = None, limit: int = 200) -> dict[str, Any]:
        return {"items": list(entries), "next_since": None, "merkle_epoch_latest": None}

    persisted: list[dict[str, Any]] = []

    async def fake_persist(entry: dict[str, Any]) -> None:
        persisted.append(entry)

    runner = _find_pull_runner(pull_loop_module)
    if runner is None:
        pytest.skip(
            "no pull_once / run_once entry point on pull_loop module — "
            "implementer should expose one for tests"
        )

    invoked = False
    for attempt in (
        lambda: runner(fetcher=fake_fetch, sink=fake_persist, public_key=pk),
        lambda: runner(fake_fetch, fake_persist, pk),
        lambda: runner(fetch=fake_fetch, persist=fake_persist, public_key=pk),
    ):
        try:
            result = attempt()
            if asyncio.iscoroutine(result):
                asyncio.get_event_loop().run_until_complete(result)
            invoked = True
            break
        except TypeError:
            continue

    if not invoked:
        pytest.skip(
            "pull_loop runner signature not recognized — implementer should "
            "accept (fetcher, sink, public_key) or similar"
        )

    assert len(persisted) == 3, (
        f"§6: validator must persist all verified entries; got {len(persisted)}"
    )


def test_pull_loop_skips_bad_signature_entries(pull_loop_module, monkeypatch):
    """§6 — entries with invalid cathedral signature must be SKIPPED, not persisted."""
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    good = make_signed_eval_output(sk, idx=1)
    bad = tamper_signature(make_signed_eval_output(sk, idx=2))
    entries = [good, bad]

    persisted: list[dict[str, Any]] = []

    async def fake_fetch(since=None, limit=200):
        return {"items": list(entries), "next_since": None, "merkle_epoch_latest": None}

    async def fake_persist(entry):
        persisted.append(entry)

    runner = _find_pull_runner(pull_loop_module)
    if runner is None:
        pytest.skip("no pull_once entry point exposed")

    for attempt in (
        lambda: runner(fetcher=fake_fetch, sink=fake_persist, public_key=pk),
        lambda: runner(fake_fetch, fake_persist, pk),
        lambda: runner(fetch=fake_fetch, persist=fake_persist, public_key=pk),
    ):
        try:
            r = attempt()
            if asyncio.iscoroutine(r):
                asyncio.get_event_loop().run_until_complete(r)
            break
        except TypeError:
            continue
    else:
        pytest.skip("runner signature not recognized")

    assert len(persisted) == 1, (
        f"§6: bad-sig entry must be skipped; persisted {len(persisted)} entries"
    )
    assert persisted[0]["id"] == good["id"], (
        f"§6: only good-sig entry must be persisted; got {persisted[0]['id']!r}"
    )


def test_pull_loop_uses_since_pagination(pull_loop_module):
    """§2.9 — pull loop must thread `since` from previous cursor.

    Best-effort observability: ensure the fetcher gets a non-None since
    on the second invocation when next_since was set on the first.
    """
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    page_1 = [make_signed_eval_output(sk, idx=i) for i in range(2)]
    page_2 = [make_signed_eval_output(sk, idx=i + 100) for i in range(2)]

    calls: list[Any] = []

    async def fake_fetch(since=None, limit=200):
        calls.append(since)
        if since is None:
            return {
                "items": page_1,
                "next_since": "2026-05-10T13:00:00.000Z",
                "merkle_epoch_latest": None,
            }
        return {"items": page_2, "next_since": None, "merkle_epoch_latest": None}

    async def fake_persist(entry):
        return None

    runner = _find_pull_runner(pull_loop_module)
    if runner is None:
        pytest.skip("no pull_once entry point exposed")

    # Call twice. If the implementer does paging within one tick we'll see
    # two fetcher calls already; otherwise call again.
    for _ in range(2):
        for attempt in (
            lambda: runner(fetcher=fake_fetch, sink=fake_persist, public_key=pk),
            lambda: runner(fake_fetch, fake_persist, pk),
        ):
            try:
                r = attempt()
                if asyncio.iscoroutine(r):
                    asyncio.get_event_loop().run_until_complete(r)
                break
            except TypeError:
                continue
        else:
            pytest.skip("runner signature not recognized")

    # We need to see at least one non-None `since` value, proving the
    # cursor was threaded.
    assert any(c is not None for c in calls), (
        f"§2.9: pull loop must thread `since` from prior next_since; "
        f"all calls had since=None: {calls}"
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _find_pull_runner(mod) -> Callable | None:
    for attr in ("pull_once", "run_once", "tick", "sync_once", "pull"):
        fn = getattr(mod, attr, None)
        if callable(fn):
            return fn
    return None


# --------------------------------------------------------------------------
# CRIT-7 regression — sign + verify round-trip across both code paths
# --------------------------------------------------------------------------


def test_legacy_verify_accepts_wire_shape_records(pull_loop_module):
    """CRIT-7: ``verify_eval_run_signature`` (legacy entry point) must
    verify against the same canonical wire EvalOutput projection that the
    publisher's :func:`scoring_pipeline.score_and_sign` signs.

    Prior to the fix the legacy verifier reconstructed a storage-shaped
    dict and compared bytes against the wire shape — guaranteed to
    diverge → ``InvalidSignature`` for every legitimate record →
    validator pull loop persists nothing → ZERO weights set on chain.
    """
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    record = make_signed_eval_output(sk, idx=42)

    pull_loop_module.verify_eval_output_signature(record, pk)
    pull_loop_module.verify_eval_run_signature(record, pk)


def test_legacy_verify_rejects_tampered_record(pull_loop_module):
    """CRIT-7: tampering must still fail on the legacy verifier path."""
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    record = make_signed_eval_output(sk, idx=7)
    record["weighted_score"] = 0.99

    with pytest.raises(pull_loop_module.PullVerificationError):
        pull_loop_module.verify_eval_run_signature(record, pk)


# --------------------------------------------------------------------------
# v1.1.0 — tuple cursor + saturation pull + versioned verifier
# --------------------------------------------------------------------------


def _make_signed_eval_output_at(sk: Ed25519PrivateKey, *, idx: int, ran_at: str) -> dict[str, Any]:
    """Variant of make_signed_eval_output with a caller-chosen ``ran_at``.

    Used by the millisecond-collision regression test that writes many
    rows at the exact same ``ran_at`` to force the page-boundary case
    flagged in ``2026-05-12-track-3-pull-cursor-audit.md`` Risk 2.
    """
    import json as _json

    import blake3 as _blake3

    output_card = {"id": "eu-ai-act", "topic": "demo", "idx": idx}
    output_card_hash = _blake3.blake3(
        _json.dumps(output_card, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    signed = {
        "id": f"00000000-0000-4000-8000-{idx:012d}",
        "agent_id": f"11111111-1111-4111-8111-{idx:012d}",
        "agent_display_name": f"Agent {idx}",
        "card_id": "eu-ai-act",
        "output_card": output_card,
        "output_card_hash": output_card_hash,
        "weighted_score": 0.5 + 0.001 * idx,
        "polaris_verified": False,
        "ran_at": ran_at,
    }
    blob = canonical_json_for_signing(signed)
    sig = base64.b64encode(sk.sign(blob)).decode("ascii")
    payload = dict(signed)
    payload["cathedral_signature"] = sig
    payload["merkle_epoch"] = None
    return payload


def test_pull_loop_drains_250_rows_at_same_ms(pull_loop_module):
    """v1.1.0 regression: 250 rows with identical ``ran_at`` must all
    reach the validator exactly once across consecutive pulls.

    This is the case ``2026-05-12-track-3-pull-cursor-audit.md`` Risk 2
    flagged: under cadence eval the scoring pipeline writes many rows in
    the same millisecond. The pre-fix code advanced the cursor by
    ``max(ran_at)`` with ``>=`` comparison and an unordered tiebreak,
    leaking rows at the page boundary. The tuple cursor fixes it.
    """
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    ran_at = "2026-05-10T12:00:00.000Z"
    entries = [_make_signed_eval_output_at(sk, idx=i, ran_at=ran_at) for i in range(250)]

    persisted_ids: list[str] = []

    async def persist(entry):
        persisted_ids.append(entry["id"])

    # Stateful fake publisher: paginates ``entries`` using the tuple
    # cursor exactly as the real publisher does. Sorts by (ran_at, id)
    # ASC and applies WHERE (ran_at, id) > (since_ran_at, since_id).
    sorted_entries = sorted(entries, key=lambda r: (r["ran_at"], r["id"]))
    page_size = 100  # smaller than 250 to force pagination

    async def fetch(since_ran_at=None, since_id=None, limit=page_size, **_extra):
        cursor = (since_ran_at or "", since_id or "")
        remaining = [r for r in sorted_entries if (r["ran_at"], r["id"]) > cursor]
        page = remaining[:limit]
        if page and len(page) == limit:
            last = page[-1]
            next_ran_at = last["ran_at"]
            next_id = last["id"]
        else:
            next_ran_at = None
            next_id = None
        return {
            "items": page,
            "next_since_ran_at": next_ran_at,
            "next_since_id": next_id,
            "next_since": next_ran_at,
            "merkle_epoch_latest": None,
        }

    pull_loop_module.reset_pull_cursor()

    # Run pull_once until the loop reports it's caught up. The saturation
    # inner-pull cap means a single pull_once drains up to
    # _MAX_INNER_PULLS pages; we run pull_once in a small outer loop too
    # so the test doesn't depend on the exact saturation cap.
    runner = _find_pull_runner(pull_loop_module)
    if runner is None:
        pytest.skip("no pull_once entry point on pull_loop module")

    for _ in range(20):
        before = len(persisted_ids)
        result = runner(fetcher=fetch, sink=persist, public_key=pk, limit=page_size)
        if asyncio.iscoroutine(result):
            asyncio.get_event_loop().run_until_complete(result)
        if len(persisted_ids) == before:
            break

    assert len(persisted_ids) == 250, (
        f"v1.1.0: all 250 ms-colliding rows must reach the validator; got {len(persisted_ids)}"
    )
    assert len(set(persisted_ids)) == 250, (
        f"v1.1.0: each row must reach the validator exactly once; "
        f"got {len(persisted_ids)} persists with "
        f"{len(set(persisted_ids))} unique ids"
    )
    assert set(persisted_ids) == {e["id"] for e in entries}, (
        "v1.1.0: persisted set must equal the published set"
    )


def test_pull_loop_saturation_inner_pull(pull_loop_module):
    """v1.1.0 — saturation-driven inner pull drains backlog without sleeping.

    See ``2026-05-12-track-3-pull-cursor-audit.md`` Risk 1. When a page
    returns exactly ``limit`` rows, the loop pulls again immediately
    inside the same outer tick rather than sleeping. Cap is enforced at
    ``_MAX_INNER_PULLS``.
    """
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    # Build 3 saturated pages + 1 short page = 4 fetcher invocations
    # consumed in a single ``pull_once`` call.
    page_size = 10
    total_rows = page_size * 3 + 5
    base_ran_at = "2026-05-10T12:00:00.000Z"
    entries = [
        _make_signed_eval_output_at(sk, idx=i, ran_at=base_ran_at) for i in range(total_rows)
    ]
    sorted_entries = sorted(entries, key=lambda r: (r["ran_at"], r["id"]))

    fetch_calls: list[tuple[str | None, str | None]] = []

    async def fetch(since_ran_at=None, since_id=None, limit=page_size, **_extra):
        fetch_calls.append((since_ran_at, since_id))
        cursor = (since_ran_at or "", since_id or "")
        remaining = [r for r in sorted_entries if (r["ran_at"], r["id"]) > cursor]
        page = remaining[:limit]
        next_ran_at = page[-1]["ran_at"] if page and len(page) == limit else None
        next_id = page[-1]["id"] if page and len(page) == limit else None
        return {
            "items": page,
            "next_since_ran_at": next_ran_at,
            "next_since_id": next_id,
            "next_since": next_ran_at,
            "merkle_epoch_latest": None,
        }

    persisted: list[str] = []

    async def persist(entry):
        persisted.append(entry["id"])

    pull_loop_module.reset_pull_cursor()
    runner = _find_pull_runner(pull_loop_module)
    if runner is None:
        pytest.skip("no pull_once entry point on pull_loop module")

    result = runner(fetcher=fetch, sink=persist, public_key=pk, limit=page_size)
    if asyncio.iscoroutine(result):
        asyncio.get_event_loop().run_until_complete(result)

    # Saturation cap should drain at least up to _MAX_INNER_PULLS pages
    # in a single pull_once. With 4 pages (3 saturated + 1 short) and the
    # cap at 4, we expect all 35 rows in one outer call.
    assert len(persisted) == total_rows, (
        f"v1.1.0: saturation inner pull must drain backlog within one "
        f"outer tick (capped at _MAX_INNER_PULLS); got {len(persisted)} "
        f"persists across {len(fetch_calls)} fetcher calls"
    )
    assert len(fetch_calls) >= 4, (
        f"v1.1.0: saturated pages must trigger inner pulls; got {len(fetch_calls)} fetcher calls"
    )


# --------------------------------------------------------------------------
# v1.1.0 — versioned signature verifier dispatcher
# --------------------------------------------------------------------------


def test_verify_default_version_still_works(pull_loop_module):
    """v1.1.0: records without ``eval_output_schema_version`` default to
    v1 and verify against the v1 key set unchanged. Required so v1.0.x
    publisher output continues to verify on a v1.1.0 validator during
    the rollout window before the miner-side agent emits v2.
    """
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    record = make_signed_eval_output(sk, idx=1)
    assert "eval_output_schema_version" not in record, (
        "test premise: legacy records do not carry the version field"
    )
    pull_loop_module.verify_eval_output_signature(record, pk)


def test_verify_rejects_unknown_schema_version(pull_loop_module):
    """v1.1.0: an unknown ``eval_output_schema_version`` must raise
    ``PullVerificationError`` with a clear error — never silently
    fall back to v1 verification.
    """
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    record = make_signed_eval_output(sk, idx=1)
    record["eval_output_schema_version"] = 999

    with pytest.raises(pull_loop_module.PullVerificationError) as exc:
        pull_loop_module.verify_eval_output_signature(record, pk)
    assert "unknown_schema_version" in str(exc.value), (
        f"expected unknown_schema_version error; got {exc.value!r}"
    )


def test_verify_dispatches_to_locked_v2_key_set(pull_loop_module):
    """v1.1.0: the locked v2 key set from cathedralai/cathedral#75 PR 4
    must verify a record signed with the canonical v2 fields.

    Cross-branch contract: this key set is mirrored byte-for-byte in
    src/cathedral/eval/v2_payload.py on the miner-side branch. If they
    drift, signatures fail at runtime.

    The locked v2 set drops output_card, output_card_hash, and
    polaris_verified; adds eval_card_excerpt and
    eval_artifact_manifest_hash.
    """
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    # The exact v2 fields per cathedralai/cathedral#75 PR 4.
    v2_record_unsigned = {
        "id": "00000000-0000-4000-8000-000000000002",
        "agent_id": "11111111-1111-4111-8111-000000000002",
        "agent_display_name": "Test agent",
        "card_id": "eu-ai-act",
        "eval_card_excerpt": {
            "id": "eu-ai-act",
            "title": "Test card",
            "summary": "Test summary",
            "confidence": 0.84,
        },
        "eval_artifact_manifest_hash": "0" * 64,  # blake3 hex
        "weighted_score": 0.84,
        "ran_at": "2026-05-10T12:00:00.000Z",
    }
    blob = canonical_json_for_signing(v2_record_unsigned)
    sig = base64.b64encode(sk.sign(blob)).decode("ascii")
    record = dict(v2_record_unsigned)
    record["cathedral_signature"] = sig
    record["eval_output_schema_version"] = 2
    # Decorate with unsigned wire-envelope fields that must be ignored
    # during verification (drops in the v2 wire shape per PR 4).
    record["eval_artifact_bundle_url"] = "s3://hippius/test"
    record["eval_artifact_manifest_url"] = "s3://hippius/test/manifest"
    record["merkle_epoch"] = None

    pull_loop_module.verify_eval_output_signature(record, pk)


def test_v2_key_set_matches_cross_branch_contract(pull_loop_module):
    """Cross-branch contract guard: the v2 key set in pull_loop must
    match the canonical set documented in cathedralai/cathedral#75 PR 4
    comment-4436018189 byte-for-byte. If this test fails, the miner-side
    branch and validator-side branch have drifted on the wire contract
    and runtime signature verification will silently fail.
    """
    expected_v2 = frozenset(
        {
            "id",
            "agent_id",
            "agent_display_name",
            "card_id",
            "eval_card_excerpt",
            "eval_artifact_manifest_hash",
            "weighted_score",
            "ran_at",
        }
    )
    assert pull_loop_module._SIGNED_KEYS_BY_VERSION[2] == expected_v2, (
        "v2 key set drifted from cross-branch contract — "
        "see cathedralai/cathedral#75 PR 4"
    )
