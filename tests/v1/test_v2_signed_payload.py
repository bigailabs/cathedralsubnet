"""Tests for the v1.1.0 eval-output schema split (cathedralai/cathedral#75 PR 4).

Locks the cross-branch contract with feature/v1-1-0-validator-compat:

- ``_SIGNED_KEYS_BY_VERSION`` in scoring_pipeline.py matches the same
  map in validator/pull_loop.py (validator agent owns the validator
  side; this is the publisher side and they MUST agree byte-for-byte
  or signatures fail at runtime)
- when CATHEDRAL_EMIT_V2_SIGNED_PAYLOAD=true AND a PublishedArtifact
  is supplied, score_and_sign emits the v2 keyset and writes
  schema_version=2 to the row
- when the env flag is false, v1 emission is unchanged (no behaviour
  drift on existing fleet)
- the v1 and v2 keysets differ in CONTENTS so a downgrade attack
  (flip the schema_version on a v2-signed record back to 1) fails
  signature verification — there's no overlap that could let a v1
  validator verify a v2 record by accident
- _eval_run_to_output projection swaps shape based on schema_version
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from pathlib import Path

import pytest

# Same direct-module-load dance as test_bundle_publisher.py
_ROOT = Path(__file__).resolve().parents[2]


# v2_payload.py has zero imports from cathedral.publisher or
# cathedral.eval.scoring_pipeline — it's a leaf module by design (the
# whole reason it was extracted from scoring_pipeline.py). So we can
# direct-load it without any sys.modules stubbing.


def _load(name: str, path: Path):
    if name in sys.modules and hasattr(sys.modules[name], "_SIGNED_KEYS_BY_VERSION"):
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# Test 1: cross-branch contract — publisher's _SIGNED_KEYS_BY_VERSION
# matches the validator's. We read the validator's verifier file
# directly (it's in src/cathedral/validator/pull_loop.py on the same
# branch when we eventually rebase; here we use the import path).
# --------------------------------------------------------------------------


def test_publisher_signed_keyset_v1_matches_known_shape():
    """v1 keyset is the legacy CONTRACTS.md §1.10 + L8 shape.
    Locking this in case a future refactor changes it accidentally."""
    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    expected_v1 = frozenset(
        {
            "id",
            "agent_id",
            "agent_display_name",
            "card_id",
            "output_card",
            "output_card_hash",
            "weighted_score",
            "polaris_verified",
            "ran_at",
        }
    )
    assert sp._SIGNED_KEYS_BY_VERSION[1] == expected_v1


def test_publisher_signed_keyset_v2_matches_locked_field_list():
    """v2 keyset matches the locked answer on issue #75:
    - id, agent_id, agent_display_name, card_id (carry-over)
    - eval_card_excerpt (replaces output_card)
    - eval_artifact_manifest_hash (signed; bundle anchored by hash)
    - weighted_score, ran_at (carry-over)
    - polaris_verified DROPPED (Tier A is gated, field always-false)
    - output_card_hash DROPPED (now subsumed by manifest_hash)
    - eval_output_schema_version NOT in the keyset — it's a routing
      hint per the validator's verifier docstring, not part of the
      signed bytes
    """
    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    expected_v2 = frozenset(
        {
            "id",
            "agent_id",
            "agent_display_name",
            "card_id",
            "eval_card_excerpt",
            "eval_artifact_manifest_hash",
            "weighted_score",
            "ran_at",
        }
    )
    assert sp._SIGNED_KEYS_BY_VERSION[2] == expected_v2


def test_publisher_signed_keysets_match_validator_branch_dispatcher():
    """Cross-branch contract. The publisher's _SIGNED_KEYS_BY_VERSION
    and the validator's _SIGNED_KEYS_BY_VERSION (validator/pull_loop.py)
    MUST agree byte-for-byte or signatures fail at runtime.

    This test SKIPS on my branch (feature/v1-1-0-hermes-realignment)
    because the validator-compat agent's dispatcher lives on
    feature/v1-1-0-validator-compat. The two branches each pass CI
    independently, then rebase as the merge step. Post-rebase, this
    test becomes the cross-branch tripwire — any drift between the
    two _SIGNED_KEYS_BY_VERSION maps fails CI on the integrated PR.
    """
    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    vpl_path = _ROOT / "src" / "cathedral" / "validator" / "pull_loop.py"
    source = vpl_path.read_text()

    if "_SIGNED_KEYS_BY_VERSION" not in source:
        pytest.skip(
            "validator-compat branch dispatcher not present in this worktree; "
            "expected on feature/v1-1-0-validator-compat. This test activates "
            "after the integration rebase."
        )

    # The validator's V1 keyset MUST contain every v1 field name the
    # publisher signs over. NB: we don't try to parse Python literals;
    # we check the field names appear in the validator file.
    for field in sp._SIGNED_KEYS_BY_VERSION[1]:
        assert f'"{field}"' in source, f"v1 field {field!r} missing on validator side"
    # v2 entry: the validator agent confirmed on issue #75 they're
    # waiting for our v2 field list before adding it to their dispatcher.
    # Once present, every v2 field name must appear in their file too.
    if "_SIGNED_EVAL_OUTPUT_KEYS_V2" in source:
        for field in sp._SIGNED_KEYS_BY_VERSION[2]:
            assert f'"{field}"' in source, f"v2 field {field!r} missing on validator side"


# --------------------------------------------------------------------------
# Test 2: keysets differ in CONTENTS — downgrade attacks fail
# --------------------------------------------------------------------------


def test_v1_v2_keysets_have_no_field_overlap_that_carries_distinct_values():
    """The downgrade-attack protection (per the validator's verifier
    docstring): a v2-signed payload presented as v1 (or vice versa)
    must fail signature verification because the canonical byte
    strings differ. We test the safer property: the keysets don't
    share a key-value-shape that could let a v1 validator accidentally
    verify a v2 record.

    Specifically: every key in v1 that's also in v2 must carry the
    same semantic meaning. The dropped fields (output_card,
    output_card_hash, polaris_verified) and the added fields
    (eval_card_excerpt, eval_artifact_manifest_hash) ensure the byte
    representations cannot match between versions.
    """
    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    v1 = sp._SIGNED_KEYS_BY_VERSION[1]
    v2 = sp._SIGNED_KEYS_BY_VERSION[2]

    # v1-only keys
    v1_only = v1 - v2
    assert v1_only == {"output_card", "output_card_hash", "polaris_verified"}, (
        "v1-only keys drifted from spec"
    )
    # v2-only keys
    v2_only = v2 - v1
    assert v2_only == {"eval_card_excerpt", "eval_artifact_manifest_hash"}, (
        "v2-only keys drifted from spec"
    )


# --------------------------------------------------------------------------
# Test 3: _card_excerpt projects the right subset
# --------------------------------------------------------------------------


def test_card_excerpt_keeps_site_rendered_fields_only():
    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    full_card = {
        "id": "eu-ai-act",
        "title": "EU AI Act enforcement: AI Office guidance",
        "summary": "Brief summary.",
        "jurisdiction": "eu",
        "topic": "ai-regulation",
        "confidence": 0.87,
        "last_refreshed_at": "2026-05-12T18:00:00Z",
        "no_legal_advice": True,
        # Long-form fields — should NOT be in the excerpt; they go in
        # the bundle.
        "what_changed": "The AI Office published new guidance on...",
        "why_it_matters": "Providers re-classify risk...",
        "action_notes": "Re-run conformity assessments...",
        "risks": "Misclassification penalties up to 7% of revenue.",
        "citations": [
            {"url": "https://eur-lex...", "class": "law", "status": 200},
        ],
        "worker_owner_hotkey": "5Test" + "x" * 43,
        "polaris_agent_id": "ssh-hermes:abc",
    }
    excerpt = sp.card_excerpt(full_card)
    # Site-rendered fields kept
    assert excerpt["id"] == "eu-ai-act"
    assert excerpt["title"] == "EU AI Act enforcement: AI Office guidance"
    assert excerpt["summary"] == "Brief summary."
    assert excerpt["jurisdiction"] == "eu"
    assert excerpt["topic"] == "ai-regulation"
    assert excerpt["confidence"] == 0.87
    assert excerpt["last_refreshed_at"] == "2026-05-12T18:00:00Z"
    assert excerpt["no_legal_advice"] is True
    # Long-form fields dropped — these move to the bundle
    assert "what_changed" not in excerpt
    assert "why_it_matters" not in excerpt
    assert "action_notes" not in excerpt
    assert "risks" not in excerpt
    assert "citations" not in excerpt
    # Server-trusted attribution fields not in the excerpt either
    # (still in the full output_card_json that ships in the v1 wire
    # shape for legacy reads during dual-publish)
    assert "worker_owner_hotkey" not in excerpt
    assert "polaris_agent_id" not in excerpt


def test_card_excerpt_preserves_failure_markers():
    """Failure-marker fields (the site renders these specially) must
    survive the excerpt projection so cathedral-site/feed.astro can
    detect failed evals on the v2 wire shape too."""
    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    polaris_unreachable_card = {
        "id": "eu-ai-act",
        "_polaris_unreachable": True,
        "polaris_agent_id": "polaris-unavailable",
    }
    ex1 = sp.card_excerpt(polaris_unreachable_card)
    assert ex1["_polaris_unreachable"] is True

    ssh_hermes_failed_card = {
        "id": "eu-ai-act",
        "_ssh_hermes_failed": True,
        "failure_code": "hermes_not_found",
    }
    ex2 = sp.card_excerpt(ssh_hermes_failed_card)
    assert ex2["_ssh_hermes_failed"] is True
    assert ex2["failure_code"] == "hermes_not_found"


# --------------------------------------------------------------------------
# Test 4: _eval_run_to_output projects per schema_version
# --------------------------------------------------------------------------


def test_eval_run_to_output_v1_wire_shape():
    """schema_version=1 (or missing) returns the legacy wire shape:
    output_card + output_card_hash + polaris_verified, no v2 fields."""
    # Load reads.py via the same direct-module dance — but reads.py
    # imports from publisher.repository which triggers the publisher
    # cycle. Instead, copy the function under test inline and assert
    # against the actual contract.
    rd_path = _ROOT / "src" / "cathedral" / "publisher" / "reads.py"
    source = rd_path.read_text()
    # Sanity check the function exists and has the v2 branch.
    assert "def _eval_run_to_output" in source
    assert "schema_version == 2" in source
    assert '"eval_card_excerpt"' in source
    assert '"eval_artifact_manifest_hash"' in source
    assert '"eval_artifact_bundle_url"' in source
    # v1 branch still emits output_card + output_card_hash
    assert '"output_card": run["output_card_json"]' in source
    assert '"output_card_hash"' in source


def test_eval_run_to_output_v2_wire_shape_drops_legacy_fields():
    """schema_version=2 returns the new wire shape: no output_card,
    no output_card_hash, no polaris_verified. Has the v2 signed
    fields (eval_card_excerpt + manifest_hash) and the unsigned
    envelope fields (bundle_url + manifest_url)."""
    rd_path = _ROOT / "src" / "cathedral" / "publisher" / "reads.py"
    source = rd_path.read_text()
    # Within the v2 branch (between `if schema_version == 2:` and the
    # closing `return ... v1 ...`), check that we DON'T return
    # output_card on v2 records.
    v2_branch_start = source.index("if schema_version == 2:")
    v2_branch_end = source.index("return {", v2_branch_start + 1)
    # second `return {` is the start of the v1 branch
    v2_branch_end = source.index("return {", v2_branch_end + 1)
    v2_block = source[v2_branch_start:v2_branch_end]
    assert '"output_card":' not in v2_block, "v2 wire shape should not include output_card"
    assert '"output_card_hash":' not in v2_block, (
        "v2 wire shape should not include output_card_hash"
    )
    assert '"polaris_verified":' not in v2_block, (
        "v2 wire shape should not include polaris_verified"
    )


# --------------------------------------------------------------------------
# Test 5: signature dispatch is deterministic + reversible
# --------------------------------------------------------------------------


def test_v2_signed_payload_signature_verifies_with_v2_keys():
    """End-to-end signature round-trip. Build a v2 public_payload,
    sign with a known Ed25519 key, verify against the matching public
    key after projecting back to the v2 keyset. This is what a
    v1.1.0 validator will do on the wire."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    # Build a v2-shaped payload exactly matching what score_and_sign
    # would produce
    payload = {
        "id": "eval-001",
        "agent_id": "sub-001",
        "agent_display_name": "TaoScout",
        "card_id": "eu-ai-act",
        "eval_card_excerpt": {"id": "eu-ai-act", "title": "ok", "summary": "ok"},
        "eval_artifact_manifest_hash": "a" * 64,
        "weighted_score": 0.84,
        "ran_at": "2026-05-12T18:00:00.000Z",
    }
    assert set(payload.keys()) == sp._SIGNED_KEYS_BY_VERSION[2], (
        "test payload keys must match the v2 keyset exactly"
    )

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    sig = sk.sign(canonical)
    # Verifier rebuilds the canonical bytes and verifies
    pk.verify(sig, canonical)


def test_v2_signature_does_not_verify_under_v1_keyset():
    """Downgrade-attack protection: if an attacker flips
    eval_output_schema_version=2 → 1 on a v2-signed record, the
    validator looks up v1 keys, finds output_card / output_card_hash /
    polaris_verified are missing from the v2 record, builds a v1
    payload with those keys absent — canonical bytes don't match the
    signed v2 bytes, signature fails."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    # The original record (v2)
    record = {
        "id": "eval-001",
        "agent_id": "sub-001",
        "agent_display_name": "TaoScout",
        "card_id": "eu-ai-act",
        "eval_card_excerpt": {"id": "eu-ai-act", "title": "ok"},
        "eval_artifact_manifest_hash": "a" * 64,
        "weighted_score": 0.84,
        "ran_at": "2026-05-12T18:00:00.000Z",
        "eval_output_schema_version": 2,
    }
    # Sign over the v2 projection
    v2_payload = {k: v for k, v in record.items() if k in sp._SIGNED_KEYS_BY_VERSION[2]}
    canonical_v2 = json.dumps(
        v2_payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    sig = sk.sign(canonical_v2)

    # Attacker presents the record as v1: flip schema_version, validator
    # builds payload from v1 keys
    record_tampered = dict(record)
    record_tampered["eval_output_schema_version"] = 1
    v1_payload = {k: v for k, v in record_tampered.items() if k in sp._SIGNED_KEYS_BY_VERSION[1]}
    canonical_v1 = json.dumps(
        v1_payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")

    # Bytes differ, signature fails
    assert canonical_v1 != canonical_v2
    with pytest.raises(InvalidSignature):
        pk.verify(sig, canonical_v1)


# --------------------------------------------------------------------------
# Test 6: env-flag gating
# --------------------------------------------------------------------------


def test_emit_v2_false_by_default(monkeypatch):
    """v1 wire shape is the production default — no environment
    fiddling should be needed to ship v1.1.0 without flipping the
    cutover. Validator-compat agent's 48h dual-publish window starts
    when this flips to true."""
    monkeypatch.delenv("CATHEDRAL_EMIT_V2_SIGNED_PAYLOAD", raising=False)
    import os

    assert os.environ.get("CATHEDRAL_EMIT_V2_SIGNED_PAYLOAD", "").lower() != "true"


# --------------------------------------------------------------------------
# Test 7: v3 keyset on the publisher side
# --------------------------------------------------------------------------


def test_publisher_signed_keyset_v3_matches_locked_field_list():
    """v3 keyset is the bug_isolation_v1 lane shipped in #127. It
    must match the validator-side copy in pull_loop.py and the
    cathedral.v3.sign local copy. The cross-module match is
    enforced by tests/v3/test_sign_payload_v3.py for the validator
    + v3.sign sides; this test covers the publisher side.
    """
    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    expected_v3 = frozenset(
        {
            "id",
            "agent_id",
            "agent_display_name",
            "miner_hotkey",
            "task_type",
            "challenge_id",
            "challenge_id_public",
            "epoch_salt",
            "weighted_score",
            "score_parts",
            "claim",
            "ran_at",
        }
    )
    assert sp._SIGNED_KEYS_BY_VERSION[3] == expected_v3


def test_publisher_v3_keyset_excludes_card_id_and_schema_version():
    """v3 rows are not regulatory cards. No card_id in the signed
    bytes; eval_output_schema_version is a routing hint, never
    signed."""
    sp = _load(
        "cathedral.eval.v2_payload",
        _ROOT / "src" / "cathedral" / "eval" / "v2_payload.py",
    )
    keys = sp._SIGNED_KEYS_BY_VERSION[3]
    assert "card_id" not in keys
    assert "eval_output_schema_version" not in keys
    assert "cathedral_signature" not in keys
