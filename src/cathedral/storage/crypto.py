"""AES-256-GCM bundle encryption with master-key wrapping.

Per CONTRACTS.md Section 5: each bundle gets its own 32-byte data key
(`secrets.token_bytes(32)`). The data key is wrapped using AES-key-wrap
with a master KEK from `CATHEDRAL_KEK_HEX` (a.k.a.
`CATHEDRAL_MASTER_ENCRYPTION_KEY`) and stored as

    kms-local:<base64 wrapped key>:<base64 nonce>

in `agent_submissions.encryption_key_id`. v2 will swap to a real KMS;
the column shape is forward-compatible (just change the prefix).

The bundle bytes themselves are encrypted with AES-256-GCM keyed by the
data key. Output layout is `nonce(12) || ciphertext_with_tag(...)`.
"""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import (
    InvalidUnwrap,
    aes_key_unwrap,
    aes_key_wrap,
)

_NONCE_LEN = 12
_DATA_KEY_LEN = 32  # AES-256
_KEY_ID_PREFIX = "kms-local"
_KEK_ENV = "CATHEDRAL_KEK_HEX"
_KEK_FALLBACK_ENV = "CATHEDRAL_MASTER_ENCRYPTION_KEY"


class EncryptionError(Exception):
    """Failed to wrap/encrypt the bundle. Caller treats as 5xx."""


class DecryptionError(Exception):
    """Failed to unwrap/decrypt. Caller fails the eval, NEVER leaks bytes."""


@dataclass(frozen=True)
class EncryptedBundle:
    ciphertext: bytes  # nonce(12) || aesgcm_ciphertext_with_tag
    encryption_key_id: str  # opaque token to round-trip through DB


def _load_master_kek() -> bytes:
    raw = os.environ.get(_KEK_ENV) or os.environ.get(_KEK_FALLBACK_ENV)
    if not raw:
        raise EncryptionError(
            f"{_KEK_ENV} (or {_KEK_FALLBACK_ENV}) must be set "
            "to a 32-byte hex master key"
        )
    try:
        kek = bytes.fromhex(raw.strip())
    except ValueError as e:
        raise EncryptionError("master KEK must be hex") from e
    if len(kek) != 32:
        raise EncryptionError(f"master KEK must be 32 bytes, got {len(kek)}")
    return kek


def pack_key_id(wrapped_key: bytes, nonce: bytes) -> str:
    """`kms-local:<b64 wrapped>:<b64 nonce>` round-trip token."""
    return (
        f"{_KEY_ID_PREFIX}:"
        f"{base64.b64encode(wrapped_key).decode('ascii')}:"
        f"{base64.b64encode(nonce).decode('ascii')}"
    )


def unpack_key_id(key_id: str) -> tuple[bytes, bytes]:
    parts = key_id.split(":")
    if len(parts) != 3 or parts[0] != _KEY_ID_PREFIX:
        raise DecryptionError(f"unsupported key id format: {key_id[:20]}...")
    try:
        wrapped = base64.b64decode(parts[1], validate=True)
        nonce = base64.b64decode(parts[2], validate=True)
    except (ValueError, TypeError) as e:
        raise DecryptionError("key id base64 invalid") from e
    return wrapped, nonce


def encrypt_bundle(plaintext: bytes) -> EncryptedBundle:
    """Generate per-bundle data key, encrypt plaintext, wrap data key."""
    if not plaintext:
        raise EncryptionError("refusing to encrypt empty bundle")

    kek = _load_master_kek()
    data_key = secrets.token_bytes(_DATA_KEY_LEN)
    nonce = secrets.token_bytes(_NONCE_LEN)

    aesgcm = AESGCM(data_key)
    try:
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    except Exception as e:
        raise EncryptionError(f"AES-GCM encrypt failed: {e}") from e

    # AES key-wrap requires 8-byte aligned input; data_key is 32 bytes ✓.
    try:
        wrapped = aes_key_wrap(kek, data_key)
    except Exception as e:
        raise EncryptionError(f"key wrap failed: {e}") from e

    blob = nonce + ciphertext
    return EncryptedBundle(ciphertext=blob, encryption_key_id=pack_key_id(wrapped, nonce))


def decrypt_bundle(blob: bytes, encryption_key_id: str) -> bytes:
    """Inverse of `encrypt_bundle`. Returns plaintext bytes only on full success."""
    if len(blob) <= _NONCE_LEN:
        raise DecryptionError("ciphertext too short")
    nonce_in_blob = blob[:_NONCE_LEN]
    ciphertext = blob[_NONCE_LEN:]

    wrapped, nonce_in_key = unpack_key_id(encryption_key_id)
    if nonce_in_blob != nonce_in_key:
        raise DecryptionError("nonce mismatch between blob and key id")

    kek = _load_master_kek()
    try:
        data_key = aes_key_unwrap(kek, wrapped)
    except InvalidUnwrap as e:
        raise DecryptionError("data key unwrap failed") from e
    except Exception as e:
        raise DecryptionError(f"unwrap raised: {e}") from e

    aesgcm = AESGCM(data_key)
    try:
        return aesgcm.decrypt(nonce_in_blob, ciphertext, associated_data=None)
    except InvalidTag as e:
        raise DecryptionError("authentication tag invalid") from e
    except Exception as e:
        raise DecryptionError(f"decrypt raised: {e}") from e
