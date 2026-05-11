"""AWS Nitro Enclave attestation verifier.

Reference:
- AWS Nitro Enclaves attestation doc format: COSE_Sign1 of a CBOR map
  with keys ``module_id``, ``timestamp``, ``digest``, ``pcrs``,
  ``certificate``, ``cabundle``, ``public_key``, ``user_data``, ``nonce``.
- Signing: ECDSA P-384 / SHA-384 by the ``certificate`` field; that
  certificate chains to the AWS Nitro Enclaves root via ``cabundle``.
- PCR8 is the EIF signing-cert hash; we treat it as the runtime image
  measurement.
- User-data binds the attestation to a specific submission: cathedral
  requires it to be a CBOR map (or canonical-JSON object) carrying
  ``bundle_hash`` and ``card_id`` matching the submitted form fields.

Out of scope for v1:
- We do NOT fetch a fresh CRL on every request. The submit path is
  latency-sensitive and we accept the cabundle the document carries
  (which AWS roots into G1). A separate ops job is expected to refresh
  the trusted root and rotate ``NITRO_ROOT_PEM`` on cadence.

The AWS root cert is bundled here in PEM form to avoid network calls
from the submit path. It's fetched once from the canonical URL
``https://aws-nitro-enclaves.amazonaws.com/AWS_NitroEnclaves_Root-G1.zip``;
operators rotating to a new root drop a new PEM in via env override.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509.oid import NameOID

from cathedral.attestation.approved_runtimes import is_approved
from cathedral.attestation.errors import (
    InvalidAttestationError,
    UnapprovedRuntimeError,
)

# AWS Nitro Enclaves Root-G1 (DER source: certificate "AWS Nitro Enclaves
# Root-G1"). Bundled in PEM form so the submit handler never has to make
# a network call. Operators can override via env when AWS publishes a
# new root.
_NITRO_ROOT_G1_PEM_DEFAULT = b"""-----BEGIN CERTIFICATE-----
MIICETCCAZagAwIBAgIRAPkxdWgbkK/hHUbMtOTn+FYwCgYIKoZIzj0EAwMwSTELMAkGA1UEBhMCVVMx
DzANBgNVBAoMBkFtYXpvbjEMMAoGA1UECwwDQVdTMRswGQYDVQQDDBJhd3Mubml0cm8tZW5jbGF2ZXMw
HhcNMTkxMDI4MTMyODA1WhcNNDkxMDI4MTQyODA1WjBJMQswCQYDVQQGEwJVUzEPMA0GA1UECgwGQW1h
em9uMQwwCgYDVQQLDANBV1MxGzAZBgNVBAMMEmF3cy5uaXRyby1lbmNsYXZlczB2MBAGByqGSM49AgEG
BSuBBAAiA2IABPwCVOumCMHzaHDimtqQvkY4MpJzbolL//Zy2YlES1BR5TSksfbb48C8WBoyt7F2Bw7e
EtaaP+ohG2bnUs990d0JX28TcPQXCEPZ3BABIeTPYwEoCWZEh8l5YoQwTcU/9KNCMEAwDwYDVR0TAQH/
BAUwAwEB/zAdBgNVHQ4EFgQUkCW1DdkFR+eWw5b6cp3PmanfS5YwDgYDVR0PAQH/BAQDAgGGMAoGCCqG
SM49BAMDA2kAMGYCMQCjfy+Rocm9Xue4YnwWmNJVA44fA0P5W2OpYow9OYCVRaEevL8uO1XYru5xtMPW
rfMCMQCi85sWBbJwKKXdS6BptQFuZbT73o/gBh1qUxl/nNr12UO8Yfwr6wPLb+6NIwLz3/Y=
-----END CERTIFICATE-----
"""


def _trusted_root() -> x509.Certificate:
    """Load the trusted AWS Nitro Enclaves root certificate.

    Operators can override the bundled root with ``CATHEDRAL_NITRO_ROOT_PEM``
    (PEM bytes) when AWS rotates to a new root. We re-parse on every call
    so a hot env change takes effect without process restart; this is
    cheap relative to ECDSA verification anyway.
    """
    pem = (
        os.environ.get("CATHEDRAL_NITRO_ROOT_PEM", "").encode("ascii") or _NITRO_ROOT_G1_PEM_DEFAULT
    )
    try:
        return x509.load_pem_x509_certificate(pem)
    except ValueError as e:
        raise InvalidAttestationError(f"nitro root certificate malformed: {e}") from e


# ----- COSE_Sign1 layout ---------------------------------------------------
#
# A COSE_Sign1 message is a CBOR array tagged 18 (or untagged):
#   [ protected_headers_bstr, unprotected_headers_map, payload_bstr, signature_bstr ]
# The signing payload (Sig_structure) is:
#   [ "Signature1", protected_headers_bstr, b"", payload_bstr ]
#
# AWS Nitro uses ES384 (algorithm = -35) and signs the document body.


_COSE_SIGN1_TAG = 18
_ES384_ALG = -35


@dataclass(frozen=True)
class NitroDocument:
    """Decoded Nitro attestation document (the COSE_Sign1 payload)."""

    module_id: str
    timestamp_ms: int
    pcrs: dict[int, bytes]
    certificate: bytes  # DER
    cabundle: list[bytes]  # DER chain (root first by AWS convention)
    user_data: bytes | None
    nonce: bytes | None
    public_key: bytes | None

    @property
    def pcr8_hex(self) -> str:
        """PCR8 (EIF signing cert hash) as lowercase hex without ``0x``."""
        pcr = self.pcrs.get(8)
        if pcr is None:
            raise InvalidAttestationError("nitro doc missing PCR8")
        return pcr.hex()


def parse_attestation_doc(doc_bytes: bytes) -> tuple[NitroDocument, bytes, bytes, bytes]:
    """Parse a Nitro COSE_Sign1 attestation document.

    Returns ``(decoded_doc, sig_structure_bytes, signature_bytes, protected_bytes)``.
    """
    try:
        outer = cbor2.loads(doc_bytes)
    except cbor2.CBORDecodeError as e:
        raise InvalidAttestationError(f"attestation not valid CBOR: {e}") from e

    # Outer can be either a raw 4-tuple or a tag-18 wrapping that tuple.
    if isinstance(outer, cbor2.CBORTag):
        if outer.tag != _COSE_SIGN1_TAG:
            raise InvalidAttestationError(f"unexpected CBOR tag for COSE_Sign1: {outer.tag}")
        outer = outer.value

    if not isinstance(outer, list) or len(outer) != 4:
        raise InvalidAttestationError("COSE_Sign1 must be a 4-element array")

    protected_bytes, _unprotected, payload_bytes, signature = outer
    if not isinstance(protected_bytes, bytes) or not isinstance(payload_bytes, bytes):
        raise InvalidAttestationError("COSE_Sign1 fields must be byte strings")
    if not isinstance(signature, bytes):
        raise InvalidAttestationError("COSE_Sign1 signature must be byte string")

    # Protected headers carry the algorithm. RFC 8152: alg label = 1.
    try:
        protected = cbor2.loads(protected_bytes) if protected_bytes else {}
    except cbor2.CBORDecodeError as e:
        raise InvalidAttestationError(f"COSE protected header invalid: {e}") from e
    if not isinstance(protected, dict):
        raise InvalidAttestationError("COSE protected header must be a map")
    if protected.get(1) != _ES384_ALG:
        raise InvalidAttestationError(
            f"nitro attestation must use ES384 (alg=-35); got alg={protected.get(1)}"
        )

    # Payload is the actual attestation document map.
    try:
        payload = cbor2.loads(payload_bytes)
    except cbor2.CBORDecodeError as e:
        raise InvalidAttestationError(f"attestation payload invalid CBOR: {e}") from e
    if not isinstance(payload, dict):
        raise InvalidAttestationError("attestation payload must be a CBOR map")

    doc = _payload_to_doc(payload)

    sig_structure = cbor2.dumps(["Signature1", protected_bytes, b"", payload_bytes])
    return doc, sig_structure, signature, protected_bytes


def _payload_to_doc(payload: dict[Any, Any]) -> NitroDocument:
    def _req(key: str, t: type) -> Any:
        if key not in payload:
            raise InvalidAttestationError(f"nitro doc missing field: {key}")
        v = payload[key]
        if not isinstance(v, t):
            raise InvalidAttestationError(f"nitro doc field {key!r} wrong type: {type(v).__name__}")
        return v

    pcrs_raw = _req("pcrs", dict)
    pcrs: dict[int, bytes] = {}
    for k, v in pcrs_raw.items():
        if not isinstance(k, int) or not isinstance(v, bytes):
            raise InvalidAttestationError("nitro doc pcrs entries must be int -> bytes")
        pcrs[k] = v

    cabundle_raw = _req("cabundle", list)
    cabundle: list[bytes] = []
    for entry in cabundle_raw:
        if not isinstance(entry, bytes):
            raise InvalidAttestationError("nitro doc cabundle entries must be bytes")
        cabundle.append(entry)

    return NitroDocument(
        module_id=_req("module_id", str),
        timestamp_ms=int(_req("timestamp", int)),
        pcrs=pcrs,
        certificate=_req("certificate", bytes),
        cabundle=cabundle,
        user_data=payload.get("user_data"),
        nonce=payload.get("nonce"),
        public_key=payload.get("public_key"),
    )


# ----- Certificate chain verification --------------------------------------


def _validate_cert_chain(
    leaf_der: bytes, cabundle_der: list[bytes], *, now: datetime
) -> x509.Certificate:
    """Validate the leaf certificate chains to the trusted root.

    Returns the leaf certificate on success. Raises
    ``InvalidAttestationError`` if any link is broken.

    For v1 we implement a deliberately simple chain walk:
    issuer/subject match plus public-key signature verification at each
    step. We do NOT consult external CRLs (latency-sensitive submit
    path); freshness comes from the leaf validity window check below.
    """
    if not cabundle_der:
        raise InvalidAttestationError("nitro cabundle is empty")
    try:
        leaf = x509.load_der_x509_certificate(leaf_der)
    except ValueError as e:
        raise InvalidAttestationError(f"nitro leaf cert not DER: {e}") from e

    chain: list[x509.Certificate] = []
    for der in cabundle_der:
        try:
            chain.append(x509.load_der_x509_certificate(der))
        except ValueError as e:
            raise InvalidAttestationError(f"nitro intermediate cert not DER: {e}") from e

    # AWS publishes the cabundle as [root, intermediate, ...]. Walk from
    # leaf upward: leaf -> chain[-1] -> chain[-2] -> ... -> chain[0]
    # and require chain[0] to be the trusted root.
    trusted_root = _trusted_root()

    walk: list[x509.Certificate] = [leaf, *reversed(chain)]
    for i, cert in enumerate(walk):
        # Validity period check at every link.
        not_before = _aware(cert.not_valid_before_utc)
        not_after = _aware(cert.not_valid_after_utc)
        if not (not_before <= now <= not_after):
            raise InvalidAttestationError(
                f"nitro cert at depth {i} outside validity window "
                f"({not_before.isoformat()} .. {not_after.isoformat()})"
            )

    for i in range(len(walk) - 1):
        child = walk[i]
        parent = walk[i + 1]
        if child.issuer.rfc4514_string() != parent.subject.rfc4514_string():
            raise InvalidAttestationError(
                f"nitro chain broken at depth {i}: issuer != parent.subject"
            )
        try:
            _verify_cert_signature(child, parent)
        except InvalidSignature as e:
            raise InvalidAttestationError(
                f"nitro cert signature verification failed at depth {i}"
            ) from e

    # The top of the walk (last entry in the reversed chain) is the
    # AWS-supplied root. Require it to match the cathedral-bundled
    # trusted root by fingerprint AND verify it self-signs.
    aws_root = walk[-1]
    if aws_root.fingerprint(hashes.SHA384()) != trusted_root.fingerprint(hashes.SHA384()):
        # Fallback: AWS root self-signs; allow it if the cabundle root's
        # public key matches our trusted root's public key (handles cert
        # re-encoding without changing identity).
        if _spki_bytes(aws_root) != _spki_bytes(trusted_root):
            raise InvalidAttestationError(
                "nitro root in cabundle does not match trusted AWS Nitro root"
            )
    try:
        _verify_cert_signature(aws_root, aws_root)
    except InvalidSignature as e:
        raise InvalidAttestationError("nitro root cert not self-signed") from e

    _ = cast(Any, NameOID)  # keep import for forward extensibility
    return leaf


def _verify_cert_signature(child: x509.Certificate, parent: x509.Certificate) -> None:
    """Verify ``child`` was signed by ``parent``'s public key (ECDSA only)."""
    parent_key = parent.public_key()
    if not isinstance(parent_key, ec.EllipticCurvePublicKey):
        raise InvalidAttestationError(
            f"nitro parent cert uses non-ECDSA key: {type(parent_key).__name__}"
        )
    hash_algo = child.signature_hash_algorithm
    if hash_algo is None:
        raise InvalidAttestationError("nitro child cert missing signature hash algorithm")
    parent_key.verify(
        child.signature,
        child.tbs_certificate_bytes,
        ec.ECDSA(hash_algo),
    )


def _spki_bytes(cert: x509.Certificate) -> bytes:
    return cert.public_key().public_bytes(
        encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.DER,
        format=__import__(
            "cryptography"
        ).hazmat.primitives.serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# ----- User-data binding ---------------------------------------------------


def _parse_user_data(user_data: bytes | None) -> dict[str, Any]:
    """Decode the attestation user_data binding.

    We accept either CBOR-encoded map bytes or UTF-8 JSON. JSON is
    convenient for test fixtures and for runtimes that don't ship a CBOR
    encoder; CBOR is the canonical Nitro convention.
    """
    if user_data is None:
        raise InvalidAttestationError("nitro user_data missing — submission binding required")
    # Try CBOR first.
    try:
        decoded = cbor2.loads(user_data)
        if isinstance(decoded, dict):
            return {str(k): v for k, v in decoded.items()}
    except cbor2.CBORDecodeError:
        pass
    # Fall back to JSON.
    try:
        decoded = json.loads(user_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InvalidAttestationError(f"nitro user_data not CBOR map or JSON: {e}") from e
    if not isinstance(decoded, dict):
        raise InvalidAttestationError("nitro user_data must encode an object/map")
    return {str(k): v for k, v in decoded.items()}


# ----- Top-level verification ---------------------------------------------


@dataclass(frozen=True)
class NitroVerificationResult:
    pcr8_hex: str
    timestamp_ms: int
    module_id: str


def verify_nitro_attestation(
    *,
    doc_bytes: bytes,
    bundle_hash: str,
    card_id: str,
    now: datetime | None = None,
    max_age: timedelta = timedelta(minutes=10),
) -> NitroVerificationResult:
    """Verify a Nitro Enclave attestation end-to-end.

    Steps:
      1. Parse COSE_Sign1.
      2. Validate certificate chain back to the trusted AWS root.
      3. Verify ECDSA-P384/SHA-384 over the COSE Sig_structure.
      4. Confirm timestamp freshness (default 10-minute window).
      5. Decode user_data and assert (bundle_hash, card_id) match.
      6. Confirm PCR8 is in the approved Nitro image set.

    Raises ``InvalidAttestationError`` for any failure except an
    unapproved runtime, which raises ``UnapprovedRuntimeError``.
    """
    now_dt = now or datetime.now(UTC)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    doc, sig_structure, signature, _protected = parse_attestation_doc(doc_bytes)

    # Chain.
    leaf = _validate_cert_chain(doc.certificate, doc.cabundle, now=now_dt)

    # Signature.
    leaf_key = leaf.public_key()
    if not isinstance(leaf_key, ec.EllipticCurvePublicKey):
        raise InvalidAttestationError(f"nitro leaf key not ECDSA: {type(leaf_key).__name__}")
    der_sig = _raw_to_der_sig(signature)
    try:
        leaf_key.verify(der_sig, sig_structure, ec.ECDSA(hashes.SHA384()))
    except InvalidSignature as e:
        raise InvalidAttestationError("nitro COSE signature did not verify") from e

    # Freshness.
    issued = datetime.fromtimestamp(doc.timestamp_ms / 1000, tz=UTC)
    age = now_dt - issued
    if age > max_age:
        raise InvalidAttestationError(f"nitro attestation too old: age={age}, max_age={max_age}")
    if age < timedelta(seconds=-60):
        # Allow up to 60s of clock skew in the future, no more.
        raise InvalidAttestationError(f"nitro attestation timestamp in the future: age={age}")

    # User-data binding.
    binding = _parse_user_data(doc.user_data)
    if binding.get("bundle_hash") != bundle_hash:
        raise InvalidAttestationError(
            "nitro user_data.bundle_hash does not match submitted bundle_hash"
        )
    if binding.get("card_id") != card_id:
        raise InvalidAttestationError("nitro user_data.card_id does not match submitted card_id")

    # Runtime measurement.
    if not is_approved("nitro-v1", doc.pcr8_hex):
        raise UnapprovedRuntimeError(f"nitro PCR8 not in approved Hermes runtimes: {doc.pcr8_hex}")

    return NitroVerificationResult(
        pcr8_hex=doc.pcr8_hex,
        timestamp_ms=doc.timestamp_ms,
        module_id=doc.module_id,
    )


def _raw_to_der_sig(raw: bytes) -> bytes:
    """COSE signs as concatenated (r, s) of fixed length; cryptography
    expects DER-encoded (r, s)."""
    if len(raw) % 2 != 0:
        raise InvalidAttestationError(f"COSE signature length {len(raw)} not even")
    half = len(raw) // 2
    r = int.from_bytes(raw[:half], "big")
    s = int.from_bytes(raw[half:], "big")
    return encode_dss_signature(r, s)
