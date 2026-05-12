"""Attestation verifier dispatcher.

The submit handler calls ``verify_attestation`` once it has the raw
attestation bytes, ``attestation_type``, and the (bundle_hash, card_id)
binding the attestation must commit to. We return a typed result; the
caller persists ``attestation_verified_at`` on success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from cathedral.attestation import sev_snp, tdx
from cathedral.attestation.errors import UnsupportedAttestationTypeError
from cathedral.attestation.nitro import verify_nitro_attestation

# Mode and type vocabularies are pinned here so the submit handler, the
# DB schema, and the docs all reference the same source of truth.
ATTESTATION_MODES: Final[frozenset[str]] = frozenset(
    {"polaris", "polaris-deploy", "ssh-probe", "tee", "unverified"}
)
ATTESTATION_TYPES: Final[frozenset[str]] = frozenset({"nitro-v1", "tdx-v1", "sev-snp-v1"})


@dataclass(frozen=True)
class AttestationResult:
    """What the submit handler persists alongside the attestation bytes."""

    attestation_type: str
    measurement_hex: str
    verified_at: datetime


def verify_attestation(
    *,
    attestation_type: str,
    attestation_bytes: bytes,
    bundle_hash: str,
    card_id: str,
    now: datetime | None = None,
) -> AttestationResult:
    """Verify ``attestation_bytes`` for the given type and binding.

    Raises:
        InvalidAttestationError      — bad signature / chain / structure / binding
        UnapprovedRuntimeError       — measurement not in approved list
        UnsupportedAttestationTypeError — TDX / SEV-SNP not yet wired
    """
    verified_at = now or datetime.now(UTC)
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=UTC)

    if attestation_type == "nitro-v1":
        result = verify_nitro_attestation(
            doc_bytes=attestation_bytes,
            bundle_hash=bundle_hash,
            card_id=card_id,
            now=verified_at,
        )
        return AttestationResult(
            attestation_type=attestation_type,
            measurement_hex=result.pcr8_hex,
            verified_at=verified_at,
        )

    if attestation_type == "tdx-v1":
        tdx.raise_unsupported()

    if attestation_type == "sev-snp-v1":
        sev_snp.raise_unsupported()

    raise UnsupportedAttestationTypeError(f"unknown attestation_type: {attestation_type!r}")
