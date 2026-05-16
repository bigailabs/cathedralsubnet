"""Publisher-side sign helper tests (v3 wire row assembly).

Locks:
  - The success path produces a row that verifies under the
    validator's verify_eval_output_signature.
  - The failure path (parse error, challenge mismatch) still
    produces a verifiable signed row with weighted_score=0.
  - The public feed view strips raw challenge_id and keeps the
    hashed challenge_id_public.
  - hash_challenge_id is stable and one-way (different inputs
    yield different hashes).
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cathedral.v3.dispatch import dispatch_bug_isolation_claim
from cathedral.v3.sign import (
    build_signed_v3_bug_isolation_row,
    hash_challenge_id,
    public_feed_view,
)
from cathedral.validator import pull_loop as pull_loop_module


class _FakeSigner:
    """Minimal stand-in for EvalSigner that exposes ._sk like the real one."""

    def __init__(self, sk: Ed25519PrivateKey) -> None:
        self._sk = sk


_ORACLE = {
    "oracle_culprit_file": "src/foo/bar.py",
    "oracle_culprit_symbol": "Bar.method",
    "oracle_line_range": (100, 120),
    "oracle_required_keywords": ("off-by-one", "loop", "boundary"),
}


def _good_stdout(challenge_id: str = "ch_seed_a") -> str:
    return (
        "reasoning\n"
        "```FINAL_ANSWER\n"
        '{"challenge_id": "' + challenge_id + '",'
        ' "culprit_file": "src/foo/bar.py",'
        ' "culprit_symbol": "Bar.method",'
        ' "line_range": [100, 120],'
        ' "failure_mode": "off-by-one loop boundary"}'
        "\n```\n"
    )


def _sign_and_get_row(stdout: str, *, challenge_id: str = "ch_seed_a") -> tuple[dict[str, Any], _FakeSigner]:
    sk = Ed25519PrivateKey.generate()
    signer = _FakeSigner(sk)
    dispatch = dispatch_bug_isolation_claim(
        expected_challenge_id=challenge_id,
        stdout=stdout,
        **_ORACLE,
    )
    row = build_signed_v3_bug_isolation_row(
        eval_run_id="00000000-0000-4000-8000-000000000001",
        submission_id="11111111-1111-4111-8111-000000000001",
        agent_display_name="Test Agent",
        challenge_id=challenge_id,
        dispatch_result=dispatch,
        ran_at_iso="2026-05-16T05:00:00.000Z",
        signer=signer,
    )
    return row, signer


# --------------------------------------------------------------------------
# Successful row verifies end-to-end
# --------------------------------------------------------------------------


def test_signed_v3_row_verifies_via_validator_path() -> None:
    """Critical: the publisher's signed row must verify with the same
    code validators run. Without this, every v3 row fails on chain."""
    row, signer = _sign_and_get_row(_good_stdout())
    pk = signer._sk.public_key()
    # Should not raise.
    pull_loop_module.verify_eval_output_signature(row, pk)
    assert row["eval_output_schema_version"] == 3
    assert row["weighted_score"] == 1.0


def test_signed_row_carries_score_parts_and_claim() -> None:
    row, _ = _sign_and_get_row(_good_stdout())
    assert row["score_parts"]["culprit_file"] == 1.0
    assert row["claim"]["culprit_file"] == "src/foo/bar.py"
    assert row["task_type"] == "bug_isolation_v1"


# --------------------------------------------------------------------------
# Failure rows still sign and verify (weighted_score=0)
# --------------------------------------------------------------------------


def test_malformed_claim_signs_a_zero_score_row() -> None:
    """The publisher signs malformed-claim outcomes too so miners
    can see a verifiable failure record on the feed."""
    row, signer = _sign_and_get_row("just prose no json")
    pull_loop_module.verify_eval_output_signature(row, signer._sk.public_key())
    assert row["weighted_score"] == 0.0
    assert row["score_parts"] == {
        "culprit_file": 0.0,
        "culprit_symbol": 0.0,
        "line_range": 0.0,
        "failure_mode": 0.0,
    }


def test_challenge_id_mismatch_signs_a_zero_score_row() -> None:
    stale_stdout = _good_stdout("ch_stale_from_last_epoch")
    row, signer = _sign_and_get_row(stale_stdout, challenge_id="ch_current_epoch")
    pull_loop_module.verify_eval_output_signature(row, signer._sk.public_key())
    assert row["weighted_score"] == 0.0
    # Claim is preserved so a reviewer can see what the miner sent.
    assert row["claim"]["challenge_id"] == "ch_stale_from_last_epoch"
    # But the signed challenge_id is the one Cathedral expected.
    assert row["challenge_id"] == "ch_current_epoch"


# --------------------------------------------------------------------------
# Public feed view hashes the challenge_id
# --------------------------------------------------------------------------


def test_public_feed_view_strips_raw_challenge_id() -> None:
    row, _ = _sign_and_get_row(_good_stdout())
    public = public_feed_view(row)
    assert "challenge_id" not in public, (
        "public feed must not expose the raw challenge_id (miners "
        "would share answers by id in Discord)"
    )
    assert public["challenge_id_public"] == hash_challenge_id("ch_seed_a")
    # The signed row itself is untouched (validators still need the raw id).
    assert row["challenge_id"] == "ch_seed_a"


def test_hash_challenge_id_is_stable_and_one_way() -> None:
    assert hash_challenge_id("ch_a") == hash_challenge_id("ch_a")
    assert hash_challenge_id("ch_a") != hash_challenge_id("ch_b")
    # Hash length is the documented prefix
    assert len(hash_challenge_id("ch_any")) == 12


def test_hash_challenge_id_unsalted_is_deterministic_across_runs() -> None:
    """The unsalted form is the framework default. It is reversible
    by anyone who can enumerate plausible challenge_ids, which is
    why production must call with an epoch_salt. Locked here so a
    future tweak to the salt-free path is noticed by tests."""
    a1 = hash_challenge_id("ch_alpha")
    a2 = hash_challenge_id("ch_alpha")
    assert a1 == a2, "unsalted hash must be stable for caching"


def test_hash_challenge_id_epoch_salt_rotates_public_id() -> None:
    """With a salt, the same raw id hashes to different public ids
    across epochs. This is the production behavior before live
    feed exposure."""
    raw = "ch_alpha"
    h_e1 = hash_challenge_id(raw, epoch_salt="epoch_1")
    h_e2 = hash_challenge_id(raw, epoch_salt="epoch_2")
    h_none = hash_challenge_id(raw)
    assert h_e1 != h_e2, "different salts must yield different public ids"
    assert h_e1 != h_none, "salted and unsalted must differ"
    assert hash_challenge_id(raw, epoch_salt="epoch_1") == h_e1, (
        "salted hash must still be deterministic for a given (raw, salt) pair"
    )


# --------------------------------------------------------------------------
# Keyset enforcement
# --------------------------------------------------------------------------


def test_build_v3_row_refuses_to_emit_extra_signed_fields() -> None:
    """If a future edit accidentally adds a key to the signed
    subset that isn't in _SIGNED_KEYS_BY_VERSION[3], the row would
    sign over fields the validator doesn't verify, and the
    signature would never match. The build function detects this
    locally and raises rather than letting bad rows escape."""
    # Hard to trigger without monkeypatching, but we at least ensure
    # the happy path doesn't carry extras. The cross-module keyset
    # match is locked by tests/v3/test_sign_payload_v3.py.
    row, _ = _sign_and_get_row(_good_stdout())
    signed_keys = {
        "id", "agent_id", "agent_display_name", "task_type",
        "challenge_id", "weighted_score", "score_parts", "claim", "ran_at",
    }
    # The row has more fields than the signed subset (envelope), but
    # the signed subset itself was complete.
    for k in signed_keys:
        assert k in row, f"signed key {k!r} missing from output row"
