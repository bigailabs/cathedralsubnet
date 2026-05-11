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


def test_pull_loop_persists_verified_entries(
    pull_loop_module, alice_keypair, monkeypatch
):
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


def test_pull_loop_skips_bad_signature_entries(
    pull_loop_module, monkeypatch
):
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
