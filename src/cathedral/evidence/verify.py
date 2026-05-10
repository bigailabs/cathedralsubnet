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


def verify_manifest(m: PolarisManifest, pubkey: Ed25519PublicKey) -> None:
    _verify(m.model_dump(by_alias=True, mode="json"), m.signature, pubkey, "manifest")


def verify_run(r: PolarisRunRecord, pubkey: Ed25519PublicKey) -> None:
    _verify(r.model_dump(by_alias=True, mode="json"), r.signature, pubkey, "run")


def verify_artifact_record(a: PolarisArtifactRecord, pubkey: Ed25519PublicKey) -> None:
    _verify(a.model_dump(by_alias=True, mode="json"), a.signature, pubkey, "artifact")


def verify_usage(u: PolarisUsageRecord, pubkey: Ed25519PublicKey) -> None:
    _verify(u.model_dump(by_alias=True, mode="json"), u.signature, pubkey, "usage")


def verify_artifact_bytes(record: PolarisArtifactRecord, raw: bytes) -> None:
    expected_hex = record.content_hash.lower()
    computed = blake3.blake3(raw).hexdigest()
    if computed != expected_hex:
        raise VerificationError(f"artifact {record.artifact_id} hash mismatch")
