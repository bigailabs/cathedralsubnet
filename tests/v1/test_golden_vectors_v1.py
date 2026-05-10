"""Cross-repo golden vectors — CONTRACTS.md §8.

These vectors must reproduce byte-for-byte across repos. The fixture
file at ``tests/fixtures/v1_golden_vectors.json`` is the single source.
If it doesn't exist yet, the first test run generates it deterministically
from the contract spec and saves it. Subsequent runs verify against the
pinned values.

When a contract change demands new vectors, delete the file and rerun.
The PR diff makes the regeneration explicit + reviewable.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import blake3
import pytest
from bittensor_wallet import Keypair
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from cathedral.types import canonical_json_for_signing
from tests.v1.conftest import GOLDEN_VECTORS_PATH

# --------------------------------------------------------------------------
# Reference (mirrors of CONTRACTS.md helpers)
# --------------------------------------------------------------------------


def _reference_merkle_leaf(run: dict[str, Any]) -> str:
    parts = [
        run["id"],
        run["output_card_hash"],
        str(run["weighted_score"]),
        run["cathedral_signature"],
    ]
    return blake3.blake3(":".join(parts).encode("utf-8")).hexdigest()


def _reference_merkle_root(leaves_hex_sorted: list[str]) -> str:
    if not leaves_hex_sorted:
        return blake3.blake3(b"").hexdigest()
    layer = list(leaves_hex_sorted)
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer = [*layer, layer[-1]]
        layer = [
            blake3.blake3((a + b).encode("utf-8")).hexdigest()
            for a, b in zip(layer[::2], layer[1::2], strict=True)
        ]
    return layer[0]


# --------------------------------------------------------------------------
# Vector specifications (from CONTRACTS.md §8)
# --------------------------------------------------------------------------

# §8.1 — submission signature
SUBMISSION_SPEC = {
    "submission": {
        "bundle_hash": "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262",
        "card_id": "eu-ai-act",
        "miner_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        "submitted_at": "2026-05-10T12:00:00.000Z",
    },
    # //Alice URI yields ss58 5GrwvaEF... — we use it directly to keep the
    # fixture reproducible without checking in a private key by hand.
    "test_only_keypair_uri": "//Alice",
}

# §8.2 — eval run
EVAL_RUN_SPEC = {
    "eval_run": {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "submission_id": "11111111-2222-3333-4444-555555555555",
        "epoch": 1,
        "round_index": 0,
        "polaris_agent_id": "agt_test_eu_ai_act",
        "polaris_run_id": "run_test_001",
        "task_json": {
            "card_id": "eu-ai-act",
            "epoch": 1,
            "round_index": 0,
            "prompt": "Summarize material AI Act developments in the last 24 hours.",
            "sources": [],
            "deadline_minutes": 25,
        },
        "output_card_json": {
            "id": "eu-ai-act",
            "jurisdiction": "eu",
            "topic": "AI regulation",
            "worker_owner_hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
            "polaris_agent_id": "agt_test_eu_ai_act",
            "title": "...",
            "summary": "...",
            "what_changed": "...",
            "why_it_matters": "...",
            "action_notes": "...",
            "risks": "...",
            "citations": [],
            "confidence": 0.7,
            "no_legal_advice": True,
            "last_refreshed_at": "2026-05-10T12:00:00Z",
            "refresh_cadence_hours": 24,
        },
        "score_parts": {
            "source_quality": 0.0,
            "freshness": 1.0,
            "specificity": 0.6,
            "usefulness": 0.5,
            "clarity": 1.0,
            "maintenance": 1.0,
        },
        "weighted_score": 0.535,
        "ran_at": "2026-05-10T12:30:00.000Z",
        "duration_ms": 1234,
        "errors": None,
    },
    "test_only_cathedral_seed_hex": (
        "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
    ),
}

# §8.3 — Merkle anchor
MERKLE_SPEC = {
    "epoch": 1,
    "leaves_input": [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "output_card_hash": "aa",
            "weighted_score": 0.5,
            "cathedral_signature": "sig1",
        },
        {
            "id": "00000000-0000-0000-0000-000000000002",
            "output_card_hash": "bb",
            "weighted_score": 0.7,
            "cathedral_signature": "sig2",
        },
        {
            "id": "00000000-0000-0000-0000-000000000003",
            "output_card_hash": "cc",
            "weighted_score": 0.9,
            "cathedral_signature": "sig3",
        },
    ],
}


# --------------------------------------------------------------------------
# Compute the vectors deterministically
# --------------------------------------------------------------------------


def _compute_submission_signature() -> str:
    kp = Keypair.create_from_uri(SUBMISSION_SPEC["test_only_keypair_uri"])
    assert kp.ss58_address == SUBMISSION_SPEC["submission"]["miner_hotkey"]
    sig = kp.sign(canonical_json_for_signing(SUBMISSION_SPEC["submission"]))
    return base64.b64encode(sig).decode("ascii")


def _compute_output_card_hash() -> str:
    """§4.4 + §6 step 5 — `output_card_hash = blake3(canonical_json(card))`."""
    return blake3.blake3(
        canonical_json_for_signing(EVAL_RUN_SPEC["eval_run"]["output_card_json"])
    ).hexdigest()


def _compute_eval_run_signature(output_card_hash: str) -> str:
    """§4.2 — Ed25519 over canonical_json of the EvalRun without the sig."""
    seed_hex = EVAL_RUN_SPEC["test_only_cathedral_seed_hex"]
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    record = dict(EVAL_RUN_SPEC["eval_run"])
    record["output_card_hash"] = output_card_hash
    blob = canonical_json_for_signing(record)
    return base64.b64encode(sk.sign(blob)).decode("ascii")


def _compute_merkle() -> dict[str, Any]:
    leaves = [
        _reference_merkle_leaf(item) for item in MERKLE_SPEC["leaves_input"]
    ]
    leaves_sorted_with_input = sorted(
        zip(leaves, MERKLE_SPEC["leaves_input"], strict=True),
        key=lambda pair: pair[1]["id"],
    )
    leaves_sorted = [pair[0] for pair in leaves_sorted_with_input]
    return {
        "expected_leaf_hashes_sorted": leaves_sorted,
        "expected_merkle_root": _reference_merkle_root(leaves_sorted),
    }


def _build_full_vectors() -> dict[str, Any]:
    sub_sig = _compute_submission_signature()
    output_card_hash = _compute_output_card_hash()
    eval_sig = _compute_eval_run_signature(output_card_hash)
    merkle = _compute_merkle()
    return {
        "meta": {
            "description": (
                "Cathedral v1 cross-repo golden vectors. Generated from "
                "CONTRACTS.md §8 spec. Test-only keys; never use in prod."
            ),
            "spec_version": "CONTRACTS.md 2026-05-10",
        },
        "submission": {
            "payload": SUBMISSION_SPEC["submission"],
            "test_only_keypair_uri": SUBMISSION_SPEC["test_only_keypair_uri"],
            "expected_signature_b64": sub_sig,
        },
        "eval_run": {
            "record": EVAL_RUN_SPEC["eval_run"],
            "test_only_cathedral_seed_hex": EVAL_RUN_SPEC[
                "test_only_cathedral_seed_hex"
            ],
            "expected_output_card_hash": output_card_hash,
            "expected_cathedral_signature_b64": eval_sig,
        },
        "merkle": {
            "epoch": MERKLE_SPEC["epoch"],
            "leaves_input": MERKLE_SPEC["leaves_input"],
            **merkle,
        },
    }


# --------------------------------------------------------------------------
# Generate-or-pin fixture
# --------------------------------------------------------------------------


def _ensure_fixture_exists() -> dict[str, Any]:
    if not GOLDEN_VECTORS_PATH.exists():
        GOLDEN_VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        vectors = _build_full_vectors()
        GOLDEN_VECTORS_PATH.write_text(json.dumps(vectors, indent=2, sort_keys=True))
        return vectors
    return json.loads(GOLDEN_VECTORS_PATH.read_text())


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return _ensure_fixture_exists()


# --------------------------------------------------------------------------
# Tests — these MUST hold for cross-repo agreement
# --------------------------------------------------------------------------


def test_submission_signature_verifies(golden):
    """§8.1 — submission signature reproduces and verifies."""
    payload = golden["submission"]["payload"]
    expected_sig = golden["submission"]["expected_signature_b64"]
    kp = Keypair.create_from_uri(golden["submission"]["test_only_keypair_uri"])
    canonical = canonical_json_for_signing(payload)

    # 1. Signature decodes + verifies under //Alice's ss58.
    sig = base64.b64decode(expected_sig)
    verifier = Keypair(ss58_address=kp.ss58_address)
    assert verifier.verify(canonical, sig), (
        f"§8.1: golden submission signature failed verification under "
        f"hotkey {kp.ss58_address}"
    )


def test_eval_run_cathedral_signature_verifies(golden):
    """§8.2 — cathedral signature on EvalRun verifies + reproduces."""
    record = dict(golden["eval_run"]["record"])
    record["output_card_hash"] = golden["eval_run"]["expected_output_card_hash"]
    expected_sig = golden["eval_run"]["expected_cathedral_signature_b64"]
    seed_hex = golden["eval_run"]["test_only_cathedral_seed_hex"]

    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    pk: Ed25519PublicKey = sk.public_key()

    blob = canonical_json_for_signing(record)
    pk.verify(base64.b64decode(expected_sig), blob)  # raises on mismatch

    # And reproduce from the seed deterministically.
    fresh_sig = base64.b64encode(sk.sign(blob)).decode("ascii")
    assert fresh_sig == expected_sig, (
        f"§8.2: cathedral signature is non-deterministic — Ed25519 should "
        f"produce identical bytes from same key + payload\n"
        f"  expected: {expected_sig}\n"
        f"  fresh:    {fresh_sig}"
    )


def test_eval_run_output_card_hash_is_blake3_of_canonical(golden):
    """§4.4 + §6 — output_card_hash = blake3(canonical_json(card))."""
    card = golden["eval_run"]["record"]["output_card_json"]
    expected = golden["eval_run"]["expected_output_card_hash"]
    actual = blake3.blake3(canonical_json_for_signing(card)).hexdigest()
    assert actual == expected, (
        f"§4.4: output_card_hash drift\n  expected: {expected}\n  actual:   {actual}"
    )


def test_merkle_root_reproduces_from_leaves(golden):
    """§8.3 — merkle root reproduces from the leaves input deterministically."""
    leaves = [
        _reference_merkle_leaf(item) for item in golden["merkle"]["leaves_input"]
    ]
    leaves_sorted_with_input = sorted(
        zip(leaves, golden["merkle"]["leaves_input"], strict=True),
        key=lambda pair: pair[1]["id"],
    )
    leaves_sorted = [pair[0] for pair in leaves_sorted_with_input]
    assert leaves_sorted == golden["merkle"]["expected_leaf_hashes_sorted"], (
        "§8.3: leaf hashes drifted from pinned values"
    )
    root = _reference_merkle_root(leaves_sorted)
    assert root == golden["merkle"]["expected_merkle_root"], (
        f"§8.3: merkle root drifted\n"
        f"  expected: {golden['merkle']['expected_merkle_root']}\n"
        f"  got:      {root}"
    )
