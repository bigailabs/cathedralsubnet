"""Hotkey signature helpers (sr25519, base64-over-canonical-json)."""

from cathedral.auth.hotkey_signature import (
    InvalidSignatureError,
    canonical_claim_bytes,
    sign_claim,
    verify_hotkey_signature,
)

__all__ = [
    "InvalidSignatureError",
    "canonical_claim_bytes",
    "sign_claim",
    "verify_hotkey_signature",
]
