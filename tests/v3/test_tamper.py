"""Tamper-evidence: mutating a stored trajectory must invalidate the receipt.

The receipt signs the canonical bundle hash. The trajectory's
canonical_bytes() includes its body but excludes bundle_hash itself, so
mutating any body field changes the recomputed hash, and the receipt that
was signed over the *original* hash no longer matches the mutated body.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nacl.signing import SigningKey

from cathedral.v3.jobs import generate_job
from cathedral.v3.receipt import ReceiptSigner, verify_receipt
from cathedral.v3.types import (
    AgentResult,
    ScoreParts,
    TaskType,
    Trajectory,
)


def _build_traj(seed: int = 0) -> Trajectory:
    job = generate_job(TaskType.CLASSIFY, seed=seed)
    now = datetime.now(UTC)
    return Trajectory(
        job=job,
        miner_hotkey="hk_test",
        miner_kind="echo",
        tool_calls=[],
        result=AgentResult(final_output="bug", structured={}, artifacts={}),
        score=ScoreParts.empty(),
        started_at=now,
        ended_at=now,
    )


@pytest.fixture()
def signer() -> ReceiptSigner:
    return ReceiptSigner(SigningKey.generate())


def test_receipt_valid_on_unmodified_trajectory(signer: ReceiptSigner) -> None:
    traj = _build_traj()
    receipt = signer.sign(traj)
    assert verify_receipt(receipt)


def test_tampering_with_final_output_breaks_verification(signer: ReceiptSigner) -> None:
    traj = _build_traj()
    receipt = signer.sign(traj)
    original_hash = traj.bundle_hash
    assert verify_receipt(receipt)

    # Mutate the body. The receipt's bundle_hash still binds the original
    # bytes; recomputing the hash now must differ.
    tampered = traj.model_copy(
        update={
            "result": AgentResult(
                final_output="praise",  # was "bug"
                structured={},
                artifacts={},
            ),
            "bundle_hash": "",
        }
    )
    recomputed = tampered.compute_bundle_hash()
    assert recomputed != original_hash, "tamper went undetected: bundle hash unchanged"

    # The receipt itself, when re-checked against the tampered body's
    # bundle_hash, must not verify (signing payload includes bundle_hash).
    forged_receipt = receipt.model_copy(update={"bundle_hash": recomputed})
    assert not verify_receipt(forged_receipt), (
        "receipt verified for a different bundle hash — tamper-evidence broken"
    )


def test_tampering_with_score_breaks_verification(signer: ReceiptSigner) -> None:
    traj = _build_traj()
    receipt = signer.sign(traj)
    assert verify_receipt(receipt)

    forged = receipt.model_copy(update={"score": receipt.score + 0.5})
    assert not verify_receipt(forged)


def test_tampering_with_miner_breaks_verification(signer: ReceiptSigner) -> None:
    traj = _build_traj()
    receipt = signer.sign(traj)
    assert verify_receipt(receipt)

    forged = receipt.model_copy(update={"miner_hotkey": "hk_other"})
    assert not verify_receipt(forged)


def test_bundle_hash_is_blake3_64_hex() -> None:
    traj = _build_traj()
    h = traj.compute_bundle_hash()
    # BLAKE3 default output is 32 bytes -> 64 hex chars; blake2b@32 was also 64.
    # The test that matters: it's hex and stable.
    assert len(h) == 64
    int(h, 16)  # raises if non-hex
    assert h == traj.compute_bundle_hash(), "bundle hash not deterministic"
