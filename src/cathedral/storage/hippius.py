"""Module-level Hippius storage helpers.

Re-exports the bucket constant, key-naming convention, and a synchronous
`put_bundle(s3_client, submission_id, plaintext, bundle_hash)` helper so
storage callers can use a hand-rolled S3 fake (no boto3 dependency)
during tests, as well as the production async `HippiusClient`.

Per CONTRACTS.md Section 5:
- bucket = `cathedral-bundles`
- key prefix = `agents/{submission_id}.bin.enc`
- AES-256-GCM with per-bundle wrapped data keys
- object metadata: x-amz-meta-bundle-hash, x-amz-meta-encryption,
  x-amz-meta-cathedral-version
"""

from __future__ import annotations

from typing import Any

from cathedral.storage.crypto import (
    DecryptionError,
    EncryptedBundle,
    EncryptionError,
    decrypt_bundle,
    encrypt_bundle,
)
from cathedral.storage.hippius_client import (
    HippiusClient,
    HippiusConfig,
    HippiusError,
)

import os

#: Default bucket name (CONTRACTS.md Section 5). Overridable via
#: HIPPIUS_S3_BUCKET so a single Hippius account can host bundles
#: under a token-scoped bucket (e.g. `kasparian-testnet`) when the
#: dedicated `cathedral-bundles` bucket isn't yet provisioned.
BUCKET: str = os.environ.get("HIPPIUS_S3_BUCKET", "cathedral-bundles")
BUCKET_NAME: str = BUCKET  # alias used by some tests
DEFAULT_BUCKET: str = BUCKET

#: Default key prefix for agent bundles. Overridable via
#: HIPPIUS_S3_PREFIX so multiple subprojects can share a bucket.
#: Trailing `/` enforced so callers never produce keys like
#: `cathedralagents/abc.bin.enc`.
def _normalize_prefix(raw: str) -> str:
    if not raw:
        return ""
    return raw if raw.endswith("/") else raw + "/"


_AGENT_PREFIX: str = _normalize_prefix(
    os.environ.get("HIPPIUS_S3_PREFIX", "agents/")
)


def agent_blob_key(submission_id: str) -> str:
    """Return the encrypted-blob key shape `<prefix>{submission_id}.bin.enc`.

    Prefix defaults to `agents/`; override via `HIPPIUS_S3_PREFIX` env.
    """
    return f"{_AGENT_PREFIX}{submission_id}.bin.enc"


# Aliases for tooling discovery.
bundle_blob_key = agent_blob_key
blob_key_for = agent_blob_key
make_blob_key = agent_blob_key


_LOGO_PREFIX: str = _normalize_prefix(
    os.environ.get("HIPPIUS_S3_LOGO_PREFIX", "logos/")
)


def logo_blob_key(submission_id: str, ext: str) -> str:
    return f"{_LOGO_PREFIX}{submission_id}.{ext}"


def put_bundle(
    s3_client: Any,
    submission_id: str,
    plaintext: bytes,
    bundle_hash: str,
    *,
    bucket: str = BUCKET,
) -> dict[str, Any]:
    """Encrypt + write the bundle blob via the supplied S3 client.

    Synchronous on purpose — the storage tests pass a hand-rolled in-
    memory fake whose `put_object(**kwargs)` call mirrors the boto3 shape
    (PascalCase `Bucket`/`Key`/`Body`/`Metadata`). Returns a dict with
    `key`, `bucket`, `encryption_key_id` so callers can record what was
    written.
    """
    encrypted: EncryptedBundle = encrypt_bundle(plaintext)
    key = agent_blob_key(submission_id)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=encrypted.ciphertext,
        Metadata={
            "bundle-hash": bundle_hash,
            "encryption": "aes-256-gcm",
            "cathedral-version": "v1",
        },
        ContentType="application/octet-stream",
    )
    return {
        "bucket": bucket,
        "key": key,
        "encryption_key_id": encrypted.encryption_key_id,
        "ciphertext_len": len(encrypted.ciphertext),
    }


# Aliases the tests probe for.
store_bundle = put_bundle
upload_bundle = put_bundle


# --------------------------------------------------------------------------
# Stub Hippius client — no network, in-memory, used by the publisher app
# in tests and CATHEDRAL_EVAL_MODE=stub deploys.
# --------------------------------------------------------------------------


class StubHippiusClient:
    """In-memory Hippius substitute.

    Mirrors the `HippiusClient` async surface that `submit.py` and
    `orchestrator.py` use. Bundles live in a dict keyed by the same
    `s3://...` URI string the production client returns.
    """

    def __init__(self) -> None:
        # Stub credentials are placeholders for the in-memory client; never
        # used to talk to a real S3 service.
        self.config = HippiusConfig(
            access_key="stub",
            secret_key="stub",  # noqa: S106 - placeholder, no network use
            bucket=BUCKET,
        )
        self._objects: dict[str, bytes] = {}
        self._logos: dict[str, bytes] = {}
        self.config_bucket = BUCKET

    def s3_uri(self, key: str) -> str:
        return f"s3://{BUCKET}/{key}"

    async def put_bundle(
        self,
        submission_id: str,
        ciphertext: bytes,
        *,
        bundle_hash_hex: str,
    ) -> str:
        del bundle_hash_hex  # accepted for parity with HippiusClient
        key = agent_blob_key(submission_id)
        uri = self.s3_uri(key)
        self._objects[uri] = ciphertext
        return uri

    async def get_bundle(self, blob_key: str) -> bytes:
        try:
            return self._objects[blob_key]
        except KeyError as e:
            raise HippiusError(f"no such object: {blob_key}") from e

    async def delete_bundle(self, blob_key: str) -> None:
        self._objects.pop(blob_key, None)

    async def put_logo(
        self, submission_id: str, raw: bytes, *, content_type: str, ext: str
    ) -> str:
        del content_type
        key = logo_blob_key(submission_id, ext)
        self._logos[key] = raw
        return f"https://stub.local/{key}"

    async def healthcheck(self) -> bool:
        return True


__all__ = [
    "BUCKET",
    "BUCKET_NAME",
    "DEFAULT_BUCKET",
    "DecryptionError",
    "EncryptedBundle",
    "EncryptionError",
    "HippiusClient",
    "HippiusConfig",
    "HippiusError",
    "StubHippiusClient",
    "agent_blob_key",
    "blob_key_for",
    "bundle_blob_key",
    "decrypt_bundle",
    "encrypt_bundle",
    "logo_blob_key",
    "make_blob_key",
    "put_bundle",
    "store_bundle",
    "upload_bundle",
]
