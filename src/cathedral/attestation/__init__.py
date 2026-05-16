"""self-TEE attestation verifiers.

This module is the door-time verifier for the ``tee`` attestation mode of
``POST /v1/agents/submit`` (see ``cathedral.publisher.submit``). The mode
branching is:

- ``polaris``     - no attestation at submit time; Cathedral re-runs eval
                    inside a Polaris-managed runtime and Polaris's
                    attestation is what counts.
- ``tee``         - miner attaches a TEE attestation document (Nitro /
                    TDX / SEV-SNP) at submission time; this module
                    verifies the signature chain, the runtime image
                    measurement against an approved list, and the
                    bundle+output binding.
- ``unverified``  - no attestation, discovery-only. Bundle is stored but
                    never enters the eval queue.

For v1 we ship Nitro Enclave verification (AWS publishes the root cert
chain at https://aws-nitro-enclaves.amazonaws.com/AWS_NitroEnclaves_Root-G1.zip).
TDX and SEV-SNP land in subsequent work. Those verifiers raise
``NotImplementedError`` with a clear "pending" message, and the submit
endpoint surfaces 501 so future agents know exactly where to wire them.
"""

from __future__ import annotations

from cathedral.attestation.errors import (
    AttestationError,
    AttestationModeError,
    InvalidAttestationError,
    UnapprovedRuntimeError,
    UnsupportedAttestationTypeError,
)
from cathedral.attestation.verifier import (
    ATTESTATION_MODES,
    ATTESTATION_TYPES,
    AttestationResult,
    verify_attestation,
)

__all__ = [
    "ATTESTATION_MODES",
    "ATTESTATION_TYPES",
    "AttestationError",
    "AttestationModeError",
    "AttestationResult",
    "InvalidAttestationError",
    "UnapprovedRuntimeError",
    "UnsupportedAttestationTypeError",
    "verify_attestation",
]
