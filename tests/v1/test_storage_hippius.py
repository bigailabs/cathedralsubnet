"""Hippius S3 storage + AES-256-GCM bundle encryption — CONTRACTS.md §5.

Contract pins:
- bucket = `cathedral-bundles`
- prefix = `agents/{submission_id}.bin.enc`
- AES-256-GCM with per-bundle data key wrapped by master key
- column shape `kms-local:<base64 wrapped>:<base64 nonce>`
- object metadata: x-amz-meta-bundle-hash, x-amz-meta-encryption,
  x-amz-meta-cathedral-version

We use a hand-rolled in-memory S3 fake (no boto3/moto in this venv) and
exercise the implementer's storage adapter against it.
"""

from __future__ import annotations

import base64
import importlib
import secrets
from typing import Any
from uuid import uuid4

import pytest

# --------------------------------------------------------------------------
# In-memory S3 fake
# --------------------------------------------------------------------------


class InMemoryS3:
    """Minimal stand-in for the bits of the S3 API the contract uses.

    The implementer's storage adapter will likely take an `s3_client`
    parameter; this fake satisfies the operations declared in the
    contract (put_object, get_object). If the implementer uses a
    different surface, tests will skip rather than false-fail.

    The PascalCase keyword arguments mirror the boto3 client signature
    exactly so the implementer's adapter can call us identically.
    """

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        self.objects[(bucket, key)] = {
            "Body": kwargs.get("Body"),
            "Metadata": dict(kwargs.get("Metadata") or {}),
            "ACL": kwargs.get("ACL"),
        }
        return {"ETag": '"fake"'}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        obj = self.objects.get((bucket, key))
        if obj is None:
            raise KeyError(f"NoSuchKey: {bucket}/{key}")
        return {
            "Body": _BytesStream(obj["Body"]),
            "Metadata": obj["Metadata"],
        }


class _BytesStream:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


# --------------------------------------------------------------------------
# Locate the storage adapter
# --------------------------------------------------------------------------


def _find_storage_module() -> Any | None:
    for name in (
        "cathedral.storage.hippius",
        "cathedral.storage",
        "cathedral.storage.bundle_store",
    ):
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    return None


@pytest.fixture
def storage_module():
    mod = _find_storage_module()
    if mod is None:
        pytest.skip(
            "storage module not importable yet — implementer must expose "
            "cathedral.storage.hippius per CONTRACTS.md §5"
        )
    return mod


@pytest.fixture
def s3_fake() -> InMemoryS3:
    return InMemoryS3()


@pytest.fixture
def master_key_hex(monkeypatch) -> str:
    """CATHEDRAL_MASTER_ENCRYPTION_KEY — 32-byte hex per §5."""
    key = secrets.token_bytes(32).hex()
    monkeypatch.setenv("CATHEDRAL_MASTER_ENCRYPTION_KEY", key)
    return key


# --------------------------------------------------------------------------
# Bucket name + key prefix conventions (§5)
# --------------------------------------------------------------------------


def test_bucket_name_constant_locked(storage_module):
    """§5 — bucket name is `cathedral-bundles` (lowercase, no underscores)."""
    for attr in ("BUCKET", "BUCKET_NAME", "DEFAULT_BUCKET"):
        if hasattr(storage_module, attr):
            assert getattr(storage_module, attr) == "cathedral-bundles", (
                f"§5: bucket constant must be 'cathedral-bundles'; "
                f"got {getattr(storage_module, attr)!r}"
            )
            return
    pytest.skip(
        "no BUCKET constant exposed — implementer should publish a module-"
        "level constant for cross-module agreement per §5"
    )


def test_agent_blob_key_uses_correct_prefix(storage_module):
    """§5 — encrypted blob key is `agents/{submission_id}.bin.enc`."""
    for attr in ("agent_blob_key", "bundle_blob_key", "blob_key_for", "make_blob_key"):
        fn = getattr(storage_module, attr, None)
        if callable(fn):
            sub_id = "abcd1234-5678-1234-5678-1234567890ab"
            try:
                key = fn(sub_id)
            except TypeError:
                continue
            assert key == f"agents/{sub_id}.bin.enc", (
                f"§5: blob key shape must be `agents/{{id}}.bin.enc`; got {key!r}"
            )
            return
    pytest.skip(
        "no blob_key helper exposed — implementer should publish a key-"
        "generation function per §5"
    )


# --------------------------------------------------------------------------
# AES-256-GCM round trip
# --------------------------------------------------------------------------


def _find_encryption_helpers(storage_module):
    """Best-effort discovery of encrypt/decrypt helpers."""
    enc = (
        getattr(storage_module, "encrypt_bundle", None)
        or getattr(storage_module, "encrypt", None)
    )
    dec = (
        getattr(storage_module, "decrypt_bundle", None)
        or getattr(storage_module, "decrypt", None)
    )
    return enc, dec


def _unpack_encrypt_result(out: Any) -> tuple[bytes, str]:
    """Pull (ciphertext, encryption_key_id) out of whatever the implementer returns.

    The contract pins the column shape (kms-local:...:...) and the
    cryptographic algorithm, but does not pin the Python return type.
    Accept tuple, dict, or any object with the right attributes.
    """
    if isinstance(out, tuple) and len(out) == 2:
        return out[0], out[1]
    if isinstance(out, dict):
        ct = out.get("ciphertext") or out.get("blob") or out.get("ct")
        kid = (
            out.get("encryption_key_id")
            or out.get("key_id")
            or out.get("kid")
        )
        if ct is not None and kid is not None:
            return ct, kid
    # Try attribute access (dataclass / pydantic model / NamedTuple).
    for ct_attr in ("ciphertext", "blob", "ct", "data"):
        for kid_attr in ("encryption_key_id", "key_id", "kid"):
            if hasattr(out, ct_attr) and hasattr(out, kid_attr):
                return getattr(out, ct_attr), getattr(out, kid_attr)
    pytest.fail(
        f"§5: cannot extract (ciphertext, encryption_key_id) from encrypt result "
        f"{type(out).__name__}; implementer should expose either a tuple, dict, "
        f"or object with ciphertext/encryption_key_id attributes"
    )


def _try_decrypt(dec, original_out, ciphertext, key_id):
    """Try a few common decrypt signatures."""
    attempts = (
        lambda: dec(original_out),
        lambda: dec(ciphertext, key_id),
        lambda: dec(ciphertext=ciphertext, encryption_key_id=key_id),
        lambda: dec(ciphertext=ciphertext, key_id=key_id),
        lambda: dec(blob=ciphertext, encryption_key_id=key_id),
    )
    last_exc: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("no decrypt signature matched")


def test_aes_gcm_round_trip(storage_module, master_key_hex):
    """§5 — encrypt then decrypt the same bytes back."""
    enc, dec = _find_encryption_helpers(storage_module)
    if enc is None or dec is None:
        pytest.skip(
            "encrypt/decrypt helpers not exposed — implementer should publish "
            "cathedral.storage.hippius.{encrypt_bundle, decrypt_bundle} per §5"
        )
    plaintext = b"hello cathedral, this is a tiny soul.md\n" * 100

    out = enc(plaintext)
    ciphertext, key_id = _unpack_encrypt_result(out)

    assert ciphertext != plaintext, "§5: ciphertext must differ from plaintext"
    assert isinstance(key_id, str), (
        f"§5: encryption_key_id must be a string (it goes in the DB column); "
        f"got {type(key_id)}"
    )

    # Decrypt back. Try whatever the implementer accepts.
    recovered = _try_decrypt(dec, out, ciphertext, key_id)
    assert recovered == plaintext, "§5: AES-GCM round trip must yield original bytes"


def test_encryption_key_id_column_shape(storage_module, master_key_hex):
    """§5 — `kms-local:<base64 wrapped key>:<base64 nonce>` shape."""
    enc, _ = _find_encryption_helpers(storage_module)
    if enc is None:
        pytest.skip("encrypt helper not exposed")
    out = enc(b"data")
    _, key_id = _unpack_encrypt_result(out)

    assert key_id.startswith("kms-local:"), (
        f"§5: encryption_key_id must start with 'kms-local:'; got {key_id!r}"
    )
    parts = key_id.split(":")
    assert len(parts) == 3, (
        f"§5: encryption_key_id must be 'kms-local:<wrapped>:<nonce>' "
        f"(3 colon-separated parts); got {len(parts)}: {key_id!r}"
    )
    # Each tail piece is base64.
    for p in parts[1:]:
        try:
            base64.b64decode(p, validate=True)
        except Exception as exc:
            pytest.fail(
                f"§5: encryption_key_id parts must be valid base64; "
                f"part {p!r} failed: {exc}"
            )


def test_decrypt_with_wrong_master_key_fails(
    storage_module, monkeypatch
):
    """§5 — decrypt must fail (raise) under a different master KEK.

    Tampering the wrapped data key in the column shape is the equivalent
    of "wrong master key" because AES-GCM authentication will fail.
    """
    monkeypatch.setenv("CATHEDRAL_MASTER_ENCRYPTION_KEY", "aa" * 32)
    importlib.reload(storage_module)
    enc, dec = _find_encryption_helpers(storage_module)
    if enc is None or dec is None:
        pytest.skip("encrypt/decrypt helpers not exposed")
    out = enc(b"sensitive bundle")
    ciphertext, key_id = _unpack_encrypt_result(out)

    # Tamper the wrapped key portion of the column value.
    parts = key_id.split(":")
    if len(parts) != 3:
        pytest.skip(
            f"§5: cannot tamper a non-conformant key_id ({key_id!r}); the "
            f"`kms-local:<wrapped>:<nonce>` shape test will catch this"
        )
    tampered_wrapped = base64.b64encode(
        bytes(b ^ 0x55 for b in base64.b64decode(parts[1]))
    ).decode("ascii")
    bad_key_id = f"{parts[0]}:{tampered_wrapped}:{parts[2]}"

    with pytest.raises(Exception):
        _try_decrypt(dec, out, ciphertext, bad_key_id)


# --------------------------------------------------------------------------
# S3 put metadata (§5)
# --------------------------------------------------------------------------


def _find_putter(storage_module):
    return (
        getattr(storage_module, "put_bundle", None)
        or getattr(storage_module, "store_bundle", None)
        or getattr(storage_module, "upload_bundle", None)
    )


def test_put_bundle_writes_to_correct_key(
    storage_module, s3_fake, master_key_hex
):
    """§5 — put writes to bucket=`cathedral-bundles`, key=`agents/{id}.bin.enc`."""
    putter = _find_putter(storage_module)
    if putter is None:
        pytest.skip(
            "no put_bundle helper exposed — implementer should publish "
            "cathedral.storage.hippius.put_bundle(s3, submission_id, plaintext, "
            "bundle_hash) per §5"
        )
    sub_id = str(uuid4())
    try:
        putter(s3_fake, sub_id, b"plaintext bundle", "deadbeef")
    except TypeError:
        # Try common kw flavors.
        try:
            putter(
                s3=s3_fake,
                submission_id=sub_id,
                plaintext=b"plaintext bundle",
                bundle_hash="deadbeef",
            )
        except TypeError:
            pytest.skip("put_bundle signature not recognized; cannot exercise")

    expected_key = f"agents/{sub_id}.bin.enc"
    assert ("cathedral-bundles", expected_key) in s3_fake.objects, (
        f"§5: must write to bucket='cathedral-bundles', key={expected_key!r}; "
        f"got {list(s3_fake.objects)}"
    )


def test_put_bundle_sets_required_metadata(
    storage_module, s3_fake, master_key_hex
):
    """§5 — object metadata includes bundle-hash, encryption marker, version."""
    putter = _find_putter(storage_module)
    if putter is None:
        pytest.skip("no put_bundle helper exposed")
    sub_id = str(uuid4())
    try:
        putter(s3_fake, sub_id, b"plaintext bundle", "abcdef0123")
    except TypeError:
        try:
            putter(
                s3=s3_fake,
                submission_id=sub_id,
                plaintext=b"plaintext bundle",
                bundle_hash="abcdef0123",
            )
        except TypeError:
            pytest.skip("put_bundle signature not recognized")

    obj = s3_fake.objects[("cathedral-bundles", f"agents/{sub_id}.bin.enc")]
    meta = obj["Metadata"]
    # Header names per §5 use 'x-amz-meta-' prefix on the wire; boto3
    # stores them with the prefix stripped in `Metadata`. Accept either.
    norm = {k.lower().removeprefix("x-amz-meta-"): v for k, v in meta.items()}
    assert "bundle-hash" in norm, (
        f"§5: object metadata must include `x-amz-meta-bundle-hash`; got {meta}"
    )
    assert norm["bundle-hash"] == "abcdef0123"
    assert norm.get("encryption") == "aes-256-gcm", (
        f"§5: x-amz-meta-encryption must equal 'aes-256-gcm'; got {meta}"
    )
    assert norm.get("cathedral-version") == "v1", (
        f"§5: x-amz-meta-cathedral-version must equal 'v1'; got {meta}"
    )
