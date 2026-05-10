"""sr25519 hotkey signature helpers.

Submissions to `/v1/agents/submit` carry an `X-Cathedral-Signature` HTTP
header — base64 sr25519 over canonical_json of the locked payload shape:

    {
      "bundle_hash":  "<blake3 lowercase hex of plaintext zip>",
      "card_id":      "<card_definitions.id>",
      "miner_hotkey": "<ss58 ascii>",
      "submitted_at": "<iso 8601 UTC, ms precision, trailing Z>"
    }

Reference: CONTRACTS.md Section 4.1.

We use `substrateinterface.Keypair` for verification (the standard
Bittensor / Substrate sr25519 implementation). Signing helpers exist
mostly for tests; production miners use `bittensor.wallet.hotkey.sign`.
"""

from __future__ import annotations

import base64
from typing import Any

from cathedral.v1_types import canonical_json


class InvalidSignatureError(Exception):
    """Hotkey signature failed to verify against canonical claim bytes."""


def _load_keypair_class() -> Any:
    """Resolve a sr25519 Keypair implementation.

    Production deploys ship `bittensor` (which bundles `bittensor_wallet`
    and/or `substrateinterface`). We try both so the verifier works in
    every environment without forcing a particular dependency.
    """
    last_err: Exception | None = None
    for module_name in ("bittensor_wallet", "substrateinterface"):
        try:
            mod = __import__(module_name, fromlist=["Keypair"])
        except ImportError as e:
            last_err = e
            continue
        keypair_cls = getattr(mod, "Keypair", None)
        if keypair_cls is not None:
            return keypair_cls
    raise InvalidSignatureError(
        f"no sr25519 Keypair implementation available: {last_err}"
    )


def canonical_claim_bytes(
    *,
    bundle_hash: str,
    card_id: str,
    miner_hotkey: str,
    submitted_at: str,
) -> bytes:
    """Return the exact bytes the miner signs.

    The dict shape is the locked payload from CONTRACTS.md Section 4.1.
    Built deterministically here so callers cannot accidentally drift
    from the canonicalization rule.
    """
    payload: dict[str, Any] = {
        "bundle_hash": bundle_hash,
        "card_id": card_id,
        "miner_hotkey": miner_hotkey,
        "submitted_at": submitted_at,
    }
    return canonical_json(payload)


def verify_hotkey_signature(
    *,
    hotkey_ss58: str,
    signature_b64: str,
    bundle_hash: str,
    card_id: str,
    submitted_at: str,
) -> None:
    """Verify the sr25519 signature; raise `InvalidSignatureError` on failure.

    The hotkey passed in MUST match the `miner_hotkey` field that the
    miner included when signing. We re-derive the canonical bytes from
    the trusted server-side values and require the signature to match
    those exactly. This means a miner cannot sign a payload claiming a
    different bundle_hash than the one cathedral computed from the
    uploaded bytes (Section 6 step 1).
    """
    keypair_cls = _load_keypair_class()

    try:
        sig_bytes = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as e:
        raise InvalidSignatureError(f"signature is not valid base64: {e}") from e

    payload = canonical_claim_bytes(
        bundle_hash=bundle_hash,
        card_id=card_id,
        miner_hotkey=hotkey_ss58,
        submitted_at=submitted_at,
    )

    try:
        kp = keypair_cls(ss58_address=hotkey_ss58)
    except (ValueError, TypeError) as e:
        raise InvalidSignatureError(f"invalid ss58 hotkey: {e}") from e

    try:
        ok = kp.verify(payload, sig_bytes)
    except Exception as e:
        raise InvalidSignatureError(f"verify raised: {e}") from e

    if not ok:
        raise InvalidSignatureError("invalid hotkey signature")


def sign_claim(
    *,
    seed_hex: str,
    bundle_hash: str,
    card_id: str,
    miner_hotkey: str,
    submitted_at: str,
) -> str:
    """Sign a claim payload with a raw sr25519 seed (hex). Test/CLI helper.

    Production miners should use `bittensor.wallet.hotkey.sign(...)` to
    keep keys in their wallet. The output is base64 (standard, padded).
    """
    keypair_cls = _load_keypair_class()
    kp = keypair_cls.create_from_seed(seed_hex, crypto_type=1)  # 1 == sr25519
    if kp.ss58_address != miner_hotkey:
        raise ValueError(
            f"seed does not derive the requested hotkey "
            f"(seed -> {kp.ss58_address}, asked for {miner_hotkey})"
        )
    payload = canonical_claim_bytes(
        bundle_hash=bundle_hash,
        card_id=card_id,
        miner_hotkey=miner_hotkey,
        submitted_at=submitted_at,
    )
    sig = kp.sign(payload)
    return base64.b64encode(sig).decode("ascii")
