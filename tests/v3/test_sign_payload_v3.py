"""Signed payload v3 round-trip tests for the bug_isolation_v1 lane.

Pins the cross-module contract between
``src/cathedral/eval/v2_payload.py:_SIGNED_KEYS_BY_VERSION[3]`` and
``src/cathedral/validator/pull_loop.py:_SIGNED_KEYS_BY_VERSION[3]``.
If the two diverge, v3 records signed by the publisher will fail
verification on every validator and zero weights set.

The wire shape is locked here so future edits to either keyset can't
silently break v3 launch.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Import canonical_json from the v1_types module (no circular). The v3
# keyset is asserted against the validator-side copy in pull_loop; the
# publisher-side copy in cathedral.eval.v2_payload is verified to match
# by the e2e test which loads the full publisher stack. Keeping this
# unit test off the eval import chain so it runs in isolation.
from cathedral.v1_types import canonical_json
from cathedral.validator import pull_loop as pull_loop_module

# v3 keyset, locked here so a future edit to either module's
# _SIGNED_KEYS_BY_VERSION[3] will fail this test loudly.
EXPECTED_V3_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "agent_id",
        "agent_display_name",
        "miner_hotkey",
        "task_type",
        "challenge_id",
        "weighted_score",
        "score_parts",
        "claim",
        "ran_at",
    }
)


def _build_v3_signed_record(sk: Ed25519PrivateKey, *, idx: int = 0) -> dict[str, Any]:
    """Build a v3 wire record using exactly the v3 keyset, sign it,
    and append the unsigned envelope fields a real publisher emits."""
    signed_subset = {
        "id": f"00000000-0000-4000-8000-{idx:012d}",
        "agent_id": f"11111111-1111-4111-8111-{idx:012d}",
        "agent_display_name": f"Agent {idx}",
        "miner_hotkey": f"5FakeHotkey{idx}",
        "task_type": "bug_isolation_v1",
        "challenge_id": f"ch_seed_{idx}",
        "weighted_score": 0.42 + 0.01 * idx,
        "score_parts": {
            "culprit_file": 1.0,
            "culprit_symbol": 0.0,
            "line_range": 0.5,
            "failure_mode": 1.0,
        },
        "claim": {
            "challenge_id": f"ch_seed_{idx}",
            "culprit_file": "src/foo/bar.py",
            "culprit_symbol": "Bar.method",
            "line_range": [42, 58],
            "failure_mode": "off by one",
            "repro_input": "Bar().method([])",
            "explanation": "method assumes list is non-empty",
        },
        "ran_at": "2026-05-16T05:00:00.000Z",
    }
    # Sanity: every signed field must come from the v3 keyset.
    assert set(signed_subset.keys()) == set(EXPECTED_V3_KEYS), (
        "test payload diverged from v3 keyset; sign+verify will fail"
    )
    sig = base64.b64encode(sk.sign(canonical_json(signed_subset))).decode("ascii")
    record = dict(signed_subset)
    record["cathedral_signature"] = sig
    record["eval_output_schema_version"] = 3
    # Envelope-only fields the publisher attaches but validators don't sign.
    record["merkle_epoch"] = None
    record["shadow_metrics"] = {"latency_ms": 1234, "cluster_dedup": 1.0}
    return record


# --------------------------------------------------------------------------
# Cross-module keyset contract
# --------------------------------------------------------------------------


def test_v3_keyset_validator_matches_expected() -> None:
    """The validator's v3 keyset must equal the expected lock above.
    The publisher-side copy in cathedral.eval.v2_payload is verified
    by the e2e test which can load the full publisher stack."""
    validator_keys = pull_loop_module._SIGNED_KEYS_BY_VERSION[3]
    assert validator_keys == EXPECTED_V3_KEYS, (
        f"validator v3 keyset diverged from expected: "
        f"got {sorted(validator_keys)} want {sorted(EXPECTED_V3_KEYS)}"
    )


def test_v3_keyset_v3_sign_helper_matches_expected() -> None:
    """The cathedral.v3.sign module also carries a local copy of the
    keyset (to stay off the eval circular-import chain). It must
    match the validator-side copy or signing+verify will diverge."""
    from cathedral.v3.sign import _V3_SIGNED_KEYS
    assert _V3_SIGNED_KEYS == EXPECTED_V3_KEYS, (
        f"cathedral.v3.sign._V3_SIGNED_KEYS diverged from expected: "
        f"got {sorted(_V3_SIGNED_KEYS)} want {sorted(EXPECTED_V3_KEYS)}"
    )


def test_v3_keyset_excludes_card_id_and_schema_version() -> None:
    """v3 rows are not regulatory cards: no card_id in signed bytes.
    `eval_output_schema_version` is a routing hint, never signed."""
    keys = pull_loop_module._SIGNED_KEYS_BY_VERSION[3]
    assert "card_id" not in keys
    assert "eval_output_schema_version" not in keys
    assert "cathedral_signature" not in keys


# --------------------------------------------------------------------------
# Sign + verify round-trip
# --------------------------------------------------------------------------


def test_v3_record_sign_verify_roundtrip() -> None:
    """Publisher signs a v3 wire record; validator's
    verify_eval_output_signature accepts it without modification."""
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    record = _build_v3_signed_record(sk, idx=7)
    # Should not raise.
    pull_loop_module.verify_eval_output_signature(record, pk)


def test_v3_record_rejects_tampered_weighted_score() -> None:
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    record = _build_v3_signed_record(sk, idx=11)
    record["weighted_score"] = 0.99
    with pytest.raises(pull_loop_module.PullVerificationError):
        pull_loop_module.verify_eval_output_signature(record, pk)


def test_v3_record_rejects_tampered_claim() -> None:
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    record = _build_v3_signed_record(sk, idx=12)
    record["claim"]["culprit_file"] = "src/totally/different.py"
    with pytest.raises(pull_loop_module.PullVerificationError):
        pull_loop_module.verify_eval_output_signature(record, pk)


def test_v3_record_rejects_tampered_challenge_id() -> None:
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    record = _build_v3_signed_record(sk, idx=13)
    record["challenge_id"] = "ch_attacker_substitute"
    with pytest.raises(pull_loop_module.PullVerificationError):
        pull_loop_module.verify_eval_output_signature(record, pk)


def test_v3_record_rejects_tampered_miner_hotkey() -> None:
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    record = _build_v3_signed_record(sk, idx=16)
    record["miner_hotkey"] = "5DifferentMinerHotkey"
    with pytest.raises(pull_loop_module.PullVerificationError):
        pull_loop_module.verify_eval_output_signature(record, pk)


def test_v3_record_envelope_changes_dont_break_signature() -> None:
    """Unsigned envelope fields (`merkle_epoch`, `shadow_metrics`,
    `eval_output_schema_version` itself) MUST NOT affect verification
    when mutated. They are stripped before canonicalization."""
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    record = _build_v3_signed_record(sk, idx=14)
    record["merkle_epoch"] = 999
    record["shadow_metrics"] = {"latency_ms": 99999, "cluster_dedup": 0.1}
    # Should still verify (envelope fields are out of the signed subset).
    pull_loop_module.verify_eval_output_signature(record, pk)


def test_v3_unknown_schema_version_rejects() -> None:
    """An attacker who flips the schema version to an unknown value
    cannot trick the validator into skipping verification."""
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    record = _build_v3_signed_record(sk, idx=15)
    record["eval_output_schema_version"] = 99
    with pytest.raises(pull_loop_module.PullVerificationError):
        pull_loop_module.verify_eval_output_signature(record, pk)
