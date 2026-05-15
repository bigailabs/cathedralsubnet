"""Intel TDX attestation verifier stub.

The verifier here is intentionally a stub: self-TEE TDX verification is
parked for future work and the submit endpoint surfaces 501 when a
miner hits this path. We keep the public function shape stable so the
next implementation only has to fill in the body.
"""

from __future__ import annotations

from dataclasses import dataclass

from cathedral.attestation.errors import UnsupportedAttestationTypeError


@dataclass(frozen=True)
class TdxVerificationResult:
    mrtd_hex: str
    timestamp_ms: int


def verify_tdx_attestation(
    *,
    doc_bytes: bytes,
    bundle_hash: str,
    card_id: str,
) -> TdxVerificationResult:
    """Verify an Intel TDX attestation. Not yet implemented.

    Raises ``NotImplementedError`` so callers get a useful traceback in
    dev; the submit handler catches it and re-raises as
    ``UnsupportedAttestationTypeError`` to map to HTTP 501.
    """
    _ = (doc_bytes, bundle_hash, card_id)
    raise NotImplementedError("self-TEE TDX verification pending; use Nitro for v1")


def raise_unsupported() -> None:
    """Shortcut for the verifier dispatcher to keep the 501 message uniform."""
    raise UnsupportedAttestationTypeError("self-TEE TDX verification pending; use Nitro for v1")
