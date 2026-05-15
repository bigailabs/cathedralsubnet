"""AMD SEV-SNP attestation verifier stub.

self-TEE SEV-SNP verification is parked for future work; the submit
endpoint surfaces 501 when a miner hits this path. Function shape kept
stable so the next agent only fills in the body.
"""

from __future__ import annotations

from dataclasses import dataclass

from cathedral.attestation.errors import UnsupportedAttestationTypeError


@dataclass(frozen=True)
class SevSnpVerificationResult:
    measurement_hex: str
    timestamp_ms: int


def verify_sev_snp_attestation(
    *,
    doc_bytes: bytes,
    bundle_hash: str,
    card_id: str,
) -> SevSnpVerificationResult:
    """Verify an AMD SEV-SNP attestation. Not yet implemented."""
    _ = (doc_bytes, bundle_hash, card_id)
    raise NotImplementedError("self-TEE SEV-SNP verification pending; use Nitro for v1")


def raise_unsupported() -> None:
    raise UnsupportedAttestationTypeError("self-TEE SEV-SNP verification pending; use Nitro for v1")
