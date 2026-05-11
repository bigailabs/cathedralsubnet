"""Synthetic Nitro Enclave attestation documents for tests.

We don't have ``aws-nitro-enclaves-attestation-mock`` available in the
test environment, so we build the COSE_Sign1 + cert chain ourselves
from scratch. The verifier accepts our fixture root by accepting an
override of the trusted root via ``CATHEDRAL_NITRO_ROOT_PEM``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

_NOW_PROVIDER: Any = dt.datetime


def _now() -> dt.datetime:
    return _NOW_PROVIDER.now(dt.UTC)


@dataclass
class NitroChain:
    root_key: ec.EllipticCurvePrivateKey
    root_cert: x509.Certificate
    leaf_key: ec.EllipticCurvePrivateKey
    leaf_cert: x509.Certificate

    @property
    def root_pem(self) -> bytes:
        return self.root_cert.public_bytes(serialization.Encoding.PEM)

    @property
    def leaf_der(self) -> bytes:
        return self.leaf_cert.public_bytes(serialization.Encoding.DER)

    @property
    def root_der(self) -> bytes:
        return self.root_cert.public_bytes(serialization.Encoding.DER)


def build_chain(*, valid_days: int = 365) -> NitroChain:
    """Build a self-signed root + leaf signing certificate (ECDSA-P384)."""
    now = _now()
    not_before = now - dt.timedelta(minutes=5)
    not_after = now + dt.timedelta(days=valid_days)

    root_key = ec.generate_private_key(ec.SECP384R1())
    root_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Cathedral Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, "test.nitro-enclaves"),
        ]
    )
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_subject)
        .issuer_name(root_subject)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA384())
    )

    leaf_key = ec.generate_private_key(ec.SECP384R1())
    leaf_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Cathedral Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, "test.nitro-leaf"),
        ]
    )
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_subject)
        .issuer_name(root_subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .sign(root_key, hashes.SHA384())
    )

    return NitroChain(
        root_key=root_key,
        root_cert=root_cert,
        leaf_key=leaf_key,
        leaf_cert=leaf_cert,
    )


def build_nitro_attestation_doc(
    *,
    chain: NitroChain,
    bundle_hash: str,
    card_id: str,
    pcr8_hex: str,
    nonce: bytes | None = None,
    timestamp_ms: int | None = None,
) -> bytes:
    """Build a COSE_Sign1-encoded Nitro attestation document.

    ``user_data`` is a CBOR map ``{"bundle_hash": ..., "card_id": ...}``
    so the verifier's binding check passes.
    """
    if timestamp_ms is None:
        timestamp_ms = int(_now().timestamp() * 1000)

    pcr8 = bytes.fromhex(pcr8_hex)
    if len(pcr8) != 48:
        raise ValueError("pcr8 must be 48 bytes (96 hex chars)")

    payload_map = {
        "module_id": "i-0123456789abcdef0",
        "timestamp": timestamp_ms,
        "digest": "SHA384",
        "pcrs": {
            0: b"\x00" * 48,
            1: b"\x00" * 48,
            2: b"\x00" * 48,
            8: pcr8,
        },
        "certificate": chain.leaf_der,
        "cabundle": [chain.root_der],
        "user_data": cbor2.dumps({"bundle_hash": bundle_hash, "card_id": card_id}),
        "nonce": nonce,
        "public_key": None,
    }
    payload_bytes = cbor2.dumps(payload_map)

    protected_header = cbor2.dumps({1: -35})  # alg = ES384
    sig_structure = cbor2.dumps(["Signature1", protected_header, b"", payload_bytes])

    der_sig = chain.leaf_key.sign(sig_structure, ec.ECDSA(hashes.SHA384()))
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(48, "big") + s.to_bytes(48, "big")

    cose_sign1 = [protected_header, {}, payload_bytes, raw_sig]
    return cbor2.dumps(cose_sign1)


def make_pcr8_hex(*, fill: int = 0xAB) -> str:
    """48-byte PCR value, deterministic for tests."""
    return (bytes([fill]) * 48).hex()
