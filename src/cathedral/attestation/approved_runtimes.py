"""Approved Hermes runtime image measurements.

For each supported attestation type we maintain a small allowlist of
runtime image measurements. The submit handler rejects any TEE
attestation whose measurement isn't in the corresponding set.

Entries are lowercase hex strings, no leading ``0x``. Population of
these lists is the responsibility of the runtime build pipeline (out of
scope for the submit endpoint); this file is the scaffolding the
verifier looks at. Until a build pipeline writes here, no real
production miners can use the ``tee`` mode — which is the desired
fail-closed default.

Measurement semantics by type:

- ``nitro-v1``   — PCR8 of the Nitro Enclave (the EIF signing-cert hash).
                   AWS docs:
                   https://docs.aws.amazon.com/enclaves/latest/user/set-up-attestation.html
- ``tdx-v1``     — MRTD (the Intel TDX runtime measurement). Verifier
                   not yet wired; reserved for the next agent.
- ``sev-snp-v1`` — MEASUREMENT field of the AMD SEV-SNP attestation
                   report. Verifier not yet wired.
"""

from __future__ import annotations

from typing import Final

# Nitro PCR8 values. Empty by default; build pipeline populates as new
# Hermes EIF images are blessed. The submit handler treats an empty set
# as "no production Nitro images approved yet" and rejects all Nitro
# attestations — fail closed.
APPROVED_NITRO_PCR8: Final[frozenset[str]] = frozenset(
    {
        # Example placeholder so the schema is obvious. Real entries
        # appended by the ops/build-pipeline PR that ships a signed EIF.
        # "deadbeef" * 12,  # 96 hex chars = 48-byte PCR
    }
)

# Intel TDX MRTD values. Empty until the TDX verifier ships.
APPROVED_TDX_MRTD: Final[frozenset[str]] = frozenset()

# AMD SEV-SNP MEASUREMENT values. Empty until the SEV-SNP verifier ships.
APPROVED_SEV_SNP_MEASUREMENT: Final[frozenset[str]] = frozenset()


def is_approved(attestation_type: str, measurement_hex: str) -> bool:
    """Return True if ``measurement_hex`` is approved for this type.

    ``measurement_hex`` is normalized to lowercase, ``0x``-stripped before
    comparison so callers don't have to.
    """
    m = measurement_hex.lower()
    if m.startswith("0x"):
        m = m[2:]
    if attestation_type == "nitro-v1":
        return m in APPROVED_NITRO_PCR8
    if attestation_type == "tdx-v1":
        return m in APPROVED_TDX_MRTD
    if attestation_type == "sev-snp-v1":
        return m in APPROVED_SEV_SNP_MEASUREMENT
    return False
