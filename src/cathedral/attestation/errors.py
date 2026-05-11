"""Attestation verification exceptions.

Each verifier raises a subclass of ``AttestationError``; the submit
handler maps them to HTTP statuses:

- ``InvalidAttestationError``         -> 401 "tee attestation invalid: ..."
- ``UnapprovedRuntimeError``          -> 401 "tee attestation invalid: ..."
- ``UnsupportedAttestationTypeError`` -> 501 (TDX/SEV-SNP not wired yet)
- ``AttestationModeError``            -> 400 (unknown attestation_mode value)
"""

from __future__ import annotations


class AttestationError(Exception):
    """Base class for all attestation verification failures."""


class InvalidAttestationError(AttestationError):
    """The attestation document failed cryptographic / structural checks."""


class UnapprovedRuntimeError(AttestationError):
    """The attested runtime image is not in the approved Hermes hash list."""


class UnsupportedAttestationTypeError(AttestationError):
    """The verifier for this attestation_type isn't wired yet (TDX/SEV-SNP)."""


class AttestationModeError(AttestationError):
    """The attestation_mode value isn't one of polaris/tee/unverified."""
