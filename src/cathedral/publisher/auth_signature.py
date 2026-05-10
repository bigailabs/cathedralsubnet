"""FastAPI dependency for `X-Cathedral-Signature` / `X-Cathedral-Hotkey`.

Returns the `(hotkey, signature)` tuple if both headers are present and
look well-formed. Actual cryptographic verification happens inside
`submit.py` AFTER the bundle hash has been computed from the uploaded
bytes — that ordering is required to enforce the
"signed-hash-equals-uploaded-hash" rule from CONTRACTS.md Section 6.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, status

# ss58 addresses are base58, ~47-48 chars in practice. Be generous on the
# upper bound; we don't validate base58 here — `Keypair(ss58_address=...)`
# will reject anything malformed during signature verification.
_SS58_MIN_LEN = 32
_SS58_MAX_LEN = 64
_SIG_MAX_LEN = 256  # base64 of 64 bytes is 88; allow padding/extension


@dataclass(frozen=True)
class HotkeyAuth:
    hotkey_ss58: str
    signature_b64: str


async def hotkey_auth_header(
    x_cathedral_hotkey: str = Header(default=""),
    x_cathedral_signature: str = Header(default=""),
) -> HotkeyAuth:
    """Pull the auth headers and return a typed object.

    Cryptographic verification happens later (in `submit.py`) once the
    server-side `bundle_hash` is known. This dep just rejects obviously
    missing/malformed headers fast.
    """
    if not x_cathedral_hotkey or not x_cathedral_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-Cathedral-Hotkey or X-Cathedral-Signature",
        )
    hk = x_cathedral_hotkey.strip()
    sig = x_cathedral_signature.strip()
    if not (_SS58_MIN_LEN <= len(hk) <= _SS58_MAX_LEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid hotkey signature",
        )
    if not (1 <= len(sig) <= _SIG_MAX_LEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid hotkey signature",
        )
    return HotkeyAuth(hotkey_ss58=hk, signature_b64=sig)
