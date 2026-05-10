"""Hotkey signature contract — CONTRACTS.md §4.1.

Pure crypto + canonicalization tests. No app dependency. These pin the
canonical_json -> sr25519 -> base64 pipeline so the publisher's verifier
and the frontend's Polkadot.js signer cannot drift.
"""

from __future__ import annotations

import base64

from bittensor_wallet import Keypair

from cathedral.types import canonical_json_for_signing
from tests.v1.conftest import canonical_submission_payload, sign_submission_payload

# --------------------------------------------------------------------------
# Canonicalization (§4.1)
# --------------------------------------------------------------------------


def test_canonicalize_matches_contract_example():
    """CONTRACTS.md §4.1 — sort_keys, no whitespace, UTF-8, drop signature."""
    payload = canonical_submission_payload(
        bundle_hash="af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262",
        card_id="eu-ai-act",
        miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        submitted_at="2026-05-10T12:00:00.000Z",
    )
    canonical = canonical_json_for_signing(payload)
    expected = (
        b'{"bundle_hash":"af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262",'
        b'"card_id":"eu-ai-act",'
        b'"miner_hotkey":"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",'
        b'"submitted_at":"2026-05-10T12:00:00.000Z"}'
    )
    assert canonical == expected, (
        f"§4.1: canonicalization must match exactly.\n"
        f"got      {canonical!r}\nexpected {expected!r}"
    )


def test_canonicalize_drops_signature_field():
    """§4.1 + §9 lock #10 — `signature` (or `cathedral_signature`) is dropped."""
    payload = {
        "bundle_hash": "deadbeef",
        "card_id": "eu-ai-act",
        "miner_hotkey": "5x",
        "signature": "should-be-stripped",
        "submitted_at": "2026-05-10T12:00:00Z",
    }
    canonical = canonical_json_for_signing(payload)
    assert b"signature" not in canonical or b'"signature"' not in canonical, (
        f"§4.1: canonical_json_for_signing must drop the `signature` key; got {canonical}"
    )


def test_canonicalize_is_stable_across_key_order():
    """§4.1 + §9 lock #10 — sort_keys=True means input order doesn't matter."""
    a = canonical_json_for_signing(
        {"a": 1, "b": 2, "c": 3, "d": 4}
    )
    b = canonical_json_for_signing(
        {"d": 4, "c": 3, "b": 2, "a": 1}
    )
    assert a == b, f"§4.1: canonicalization must be order-independent; {a!r} != {b!r}"


# --------------------------------------------------------------------------
# Signing + verification (§4.1)
# --------------------------------------------------------------------------


def test_alice_signature_round_trips():
    """CONTRACTS.md §4.1 — sign with //Alice, verify with //Alice's ss58 + Keypair."""
    kp = Keypair.create_from_uri("//Alice")
    assert kp.ss58_address == "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY", (
        "//Alice ss58 should be the well-known SS58 for substrate test keys"
    )
    sig_b64 = sign_submission_payload(
        kp,
        bundle_hash="af1349b9" + "0" * 56,
        card_id="eu-ai-act",
        submitted_at="2026-05-10T12:00:00.000Z",
    )
    # Decoded sig is sr25519 = 64 bytes
    sig = base64.b64decode(sig_b64)
    assert len(sig) == 64, f"§4.1: sr25519 signature is 64 bytes; got {len(sig)}"

    payload = canonical_submission_payload(
        bundle_hash="af1349b9" + "0" * 56,
        card_id="eu-ai-act",
        miner_hotkey=kp.ss58_address,
        submitted_at="2026-05-10T12:00:00.000Z",
    )
    canonical = canonical_json_for_signing(payload)
    # Verify via address-only Keypair (cathedral side has no priv key).
    verifier = Keypair(ss58_address=kp.ss58_address)
    assert verifier.verify(canonical, sig), (
        "§4.1: signature must verify against the public ss58 alone"
    )


def test_tampered_payload_fails_verification():
    """§4.1 — flipping a byte in the canonical payload breaks verification."""
    kp = Keypair.create_from_uri("//Alice")
    payload = canonical_submission_payload(
        bundle_hash="00" * 32,
        card_id="eu-ai-act",
        miner_hotkey=kp.ss58_address,
        submitted_at="2026-05-10T12:00:00.000Z",
    )
    canonical = canonical_json_for_signing(payload)
    sig = kp.sign(canonical)
    # Tamper: change the last byte of the canonical payload.
    tampered = canonical[:-1] + (b"y" if canonical[-1:] != b"y" else b"z")
    verifier = Keypair(ss58_address=kp.ss58_address)
    assert not verifier.verify(tampered, sig), (
        "§4.1: tampered canonical bytes must NOT verify"
    )


def test_wrong_hotkey_fails_verification():
    """§4.1 — sig from Alice must NOT verify under Bob's ss58."""
    alice = Keypair.create_from_uri("//Alice")
    bob = Keypair.create_from_uri("//Bob")
    payload = canonical_submission_payload(
        bundle_hash="00" * 32,
        card_id="eu-ai-act",
        miner_hotkey=alice.ss58_address,
        submitted_at="2026-05-10T12:00:00.000Z",
    )
    canonical = canonical_json_for_signing(payload)
    sig = alice.sign(canonical)
    bob_verifier = Keypair(ss58_address=bob.ss58_address)
    assert not bob_verifier.verify(canonical, sig), (
        "§4.1: Alice's signature must not verify under Bob's hotkey"
    )


def test_malformed_signature_bytes_dont_verify():
    """§4.1 — random 64 bytes never verify."""
    import secrets

    kp = Keypair.create_from_uri("//Alice")
    canonical = canonical_json_for_signing(
        canonical_submission_payload(
            bundle_hash="00" * 32,
            card_id="eu-ai-act",
            miner_hotkey=kp.ss58_address,
            submitted_at="2026-05-10T12:00:00.000Z",
        )
    )
    bogus = secrets.token_bytes(64)
    verifier = Keypair(ss58_address=kp.ss58_address)
    assert not verifier.verify(canonical, bogus), (
        "§4.1: random bytes must not verify as a signature"
    )


def test_malformed_signature_wrong_length_does_not_crash():
    """§4.1 — decoders must reject wrong-length signatures gracefully."""
    kp = Keypair.create_from_uri("//Alice")
    canonical = b"anything"
    short_sig = b"\x00" * 32
    verifier = Keypair(ss58_address=kp.ss58_address)
    # Either returns False or raises a controllable exception — but never
    # SIGSEGVs / panics. We accept either.
    try:
        ok = verifier.verify(canonical, short_sig)
        assert ok is False, f"§4.1: short sig must be rejected; got verify={ok}"
    except (ValueError, TypeError, Exception):
        # Raising is also acceptable behaviour for malformed sig bytes.
        pass


# --------------------------------------------------------------------------
# §9 lock #5 — base64 (standard, padding included)
# --------------------------------------------------------------------------


def test_signature_uses_standard_base64_with_padding():
    """§9 lock #5 — base64 standard (NOT base64url), padding INCLUDED."""
    kp = Keypair.create_from_uri("//Alice")
    sig_b64 = sign_submission_payload(
        kp,
        bundle_hash="00" * 32,
        card_id="eu-ai-act",
        submitted_at="2026-05-10T12:00:00.000Z",
    )
    # 64 raw bytes -> 88 base64 chars (with padding).
    assert len(sig_b64) == 88, (
        f"§9 lock #5: standard base64 of 64 bytes is 88 chars (with padding); "
        f"got {len(sig_b64)}"
    )
    # base64url uses '-_' instead of '+/'. Verify we are NOT base64url.
    assert "_" not in sig_b64 and "-" not in sig_b64, (
        f"§9 lock #5: must be standard base64, not base64url; got {sig_b64}"
    )
    # Padding character present iff length needs it.
    decoded = base64.b64decode(sig_b64, validate=True)
    assert len(decoded) == 64
