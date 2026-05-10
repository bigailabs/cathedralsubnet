"""Ed25519 verification of Polaris records and BLAKE3 artifact-bytes hash check."""

from __future__ import annotations

from typing import Any

import blake3
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cathedral.types import (
    PolarisArtifactRecord,
    PolarisManifest,
    PolarisRunRecord,
    PolarisUsageRecord,
    canonical_json_for_signing,
)


class VerificationError(Exception):
    """Signature or hash check failed."""


def _verify(
    record_dict: dict[str, Any],
    signature_b64: str,
    pubkey: Ed25519PublicKey,
    label: str,
) -> None:
    import base64

    payload = canonical_json_for_signing(record_dict)
    try:
        sig = base64.b64decode(signature_b64)
        pubkey.verify(sig, payload)
    except (InvalidSignature, ValueError, TypeError) as e:
        raise VerificationError(f"{label} signature invalid") from e


# NOTE: every verify_* call uses model_dump(..., exclude_none=True). This MUST
# match Polaris's signing canonicalization (polaris/services/cathedral_signing.py
# `_to_canonical_dict`, also exclude_none=True). The `exclude_none` setting is
# what makes the manifest contract additive: when a new optional field like
# `runtime_image` or `runtime_mode` (CONTRACTS.md L3) is added with default
# None, manifests signed before the field existed continue to verify
# byte-for-byte because both sides drop the null key from canonical bytes.
# If you change one side without the other, every signature breaks.


def verify_manifest(m: PolarisManifest, pubkey: Ed25519PublicKey) -> None:
    _verify(
        m.model_dump(by_alias=True, mode="json", exclude_none=True),
        m.signature,
        pubkey,
        "manifest",
    )


def verify_run(r: PolarisRunRecord, pubkey: Ed25519PublicKey) -> None:
    _verify(
        r.model_dump(by_alias=True, mode="json", exclude_none=True),
        r.signature,
        pubkey,
        "run",
    )


def verify_artifact_record(a: PolarisArtifactRecord, pubkey: Ed25519PublicKey) -> None:
    _verify(
        a.model_dump(by_alias=True, mode="json", exclude_none=True),
        a.signature,
        pubkey,
        "artifact",
    )


def verify_usage(u: PolarisUsageRecord, pubkey: Ed25519PublicKey) -> None:
    _verify(
        u.model_dump(by_alias=True, mode="json", exclude_none=True),
        u.signature,
        pubkey,
        "usage",
    )


def verify_artifact_bytes(record: PolarisArtifactRecord, raw: bytes) -> None:
    expected_hex = record.content_hash.lower()
    computed = blake3.blake3(raw).hexdigest()
    if computed != expected_hex:
        raise VerificationError(f"artifact {record.artifact_id} hash mismatch")
