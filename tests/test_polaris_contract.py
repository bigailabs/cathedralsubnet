"""Cross-repo contract test pinning the Polaris<->Cathedral signing wire format.

The same fixture file is mirrored in bigailabs/polariscomputer at
`tests/fixtures/cathedral_contract_golden_vectors.json`. If either side
drifts (canonicalization changes, key rotation without coordination,
schema rename), both test suites fail and the bad change is caught
before it breaks production claim verification.

When updating the contract:
1. Regenerate vectors on the Polaris side via `polaris.services.cathedral_signing`.
2. Copy the fresh JSON into BOTH `tests/fixtures/`.
3. Bump `meta.note` describing what changed.
4. Land both PRs together.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cathedral.types import (
    PolarisArtifactRecord,
    PolarisManifest,
    PolarisRunRecord,
    PolarisUsageRecord,
    canonical_json_for_signing,
)
from cathedral.evidence.verify import (
    VerificationError,
    verify_artifact_record,
    verify_manifest,
    verify_run,
    verify_usage,
)

GOLDEN_VECTORS_PATH = (
    Path(__file__).parent / "fixtures" / "polaris_contract_golden_vectors.json"
)


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN_VECTORS_PATH.read_text())


@pytest.fixture(scope="module")
def polaris_pubkey(golden) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(golden["meta"]["public_key_hex"])
    )


def test_canonicalization_matches_polaris(golden):
    """Cathedral's canonical_json_for_signing produces bytes that
    Ed25519-verify against the signature Polaris generated. If the
    serializers diverge by one byte, this fails.

    The expected string uses Pydantic's `mode="json"` datetime form
    (`Z` for UTC, not `+00:00`). Both repos must produce the same
    bytes for the same record."""
    record = dict(golden["records"]["manifest"])
    canonical = canonical_json_for_signing(record).decode()
    expected = (
        '{"created_at":"2026-05-01T00:00:00Z",'
        '"owner_wallet":"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",'
        '"polaris_agent_id":"agt_test_eu_ai_act",'
        '"schema":"polaris.manifest.v1"}'
    )
    assert canonical == expected


def test_manifest_signature_verifies(golden, polaris_pubkey):
    record = golden["records"]["manifest"]
    manifest = PolarisManifest.model_validate(record)
    verify_manifest(manifest, polaris_pubkey)  # raises on failure


def test_run_signature_verifies(golden, polaris_pubkey):
    record = golden["records"]["run"]
    run = PolarisRunRecord.model_validate(record)
    verify_run(run, polaris_pubkey)


def test_artifact_signature_verifies(golden, polaris_pubkey):
    record = golden["records"]["artifact"]
    artifact = PolarisArtifactRecord.model_validate(record)
    verify_artifact_record(artifact, polaris_pubkey)


def test_usage_external_signature_verifies(golden, polaris_pubkey):
    record = golden["records"]["usage_external"]
    usage = PolarisUsageRecord.model_validate(record)
    verify_usage(usage, polaris_pubkey)


def test_usage_self_loop_signature_verifies(golden, polaris_pubkey):
    """Self-loop is a filtering decision, not a signature failure.
    The record must still verify cryptographically — the filter runs
    after verification."""
    record = golden["records"]["usage_self_loop"]
    usage = PolarisUsageRecord.model_validate(record)
    verify_usage(usage, polaris_pubkey)


def test_tampered_manifest_fails_verification(golden, polaris_pubkey):
    """Flipping any field invalidates the signature."""
    record = dict(golden["records"]["manifest"])
    record["polaris_agent_id"] = "agt_attacker_substituted"
    manifest = PolarisManifest.model_validate(record)
    with pytest.raises(VerificationError):
        verify_manifest(manifest, polaris_pubkey)


def test_wrong_pubkey_fails_verification(golden):
    """Using a different key rejects valid records — defends against
    operator misconfiguration of the polaris.public_key_hex setting."""
    other = Ed25519PublicKey.from_public_bytes(b"\x00" * 32)
    record = golden["records"]["manifest"]
    manifest = PolarisManifest.model_validate(record)
    with pytest.raises(VerificationError):
        verify_manifest(manifest, other)
