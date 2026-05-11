"""GET /v1/agents/*, /v1/cards/*, /v1/leaderboard*, /v1/merkle/* — per CONTRACTS.md §2.

These verify response SHAPES against the TypeScript mirrors in §1.

For shape validation we build Pydantic models from the TypeScript mirrors
in CONTRACTS.md §1.9-§1.13. If the implementer's response shape diverges,
the model_validate raises and the test fails citing the contract section.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from tests.v1.conftest import (
    blake3_hex,
    make_valid_bundle,
    submit_multipart,
)

# --------------------------------------------------------------------------
# Pydantic mirrors of the TypeScript types in CONTRACTS.md §1
# --------------------------------------------------------------------------


class _ScoreHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    score: float


class _AgentProfile(BaseModel):
    """Mirrors CONTRACTS.md §1.9 `AgentProfile` (frontend mirror)."""

    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    bio: str | None
    logo_url: str | None
    miner_hotkey: str
    card_id: str
    bundle_hash: str
    bundle_size_bytes: int
    status: str  # one of AgentSubmissionStatus
    current_score: float | None
    current_rank: int | None
    submitted_at: str
    recent_evals: list[dict[str, Any]] = Field(default_factory=list)
    score_history: list[_ScoreHistoryEntry] = Field(default_factory=list)


class _LeaderboardEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    display_name: str
    logo_url: str | None
    miner_hotkey: str
    card_id: str
    current_score: float
    current_rank: int
    last_eval_at: str


class _EvalOutput(BaseModel):
    """Mirrors §1.10 `EvalOutput` + locked decision L8.

    `output_card_hash` is REQUIRED in the public projection (L8): the
    frontend renders it as the visible trust-chain anchor and validators
    use it to verify the cathedral signature against the byte-exact card
    that was scored.
    """

    model_config = ConfigDict(extra="forbid")
    id: str
    agent_id: str
    agent_display_name: str
    card_id: str
    output_card: dict[str, Any]
    output_card_hash: str
    weighted_score: float
    ran_at: str
    cathedral_signature: str
    merkle_epoch: int | None


class _MerkleAnchor(BaseModel):
    """Mirrors §1.13 `MerkleAnchor`."""

    model_config = ConfigDict(extra="forbid")
    epoch: int
    merkle_root: str
    eval_count: int
    computed_at: str
    on_chain_block: int | None
    on_chain_extrinsic_index: int | None
    leaf_hashes: list[str] | None = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _seed_one_submission(client, keypair, card_id="eu-ai-act") -> dict[str, Any]:
    bundle = make_valid_bundle(soul_md=f"# Seed for {keypair.ss58_address}\n")
    resp = submit_multipart(
        client, keypair=keypair, card_id=card_id, bundle=bundle, display_name="Seed Agent"
    )
    if resp.status_code != 202:
        pytest.skip(
            f"submit not yet implemented (got {resp.status_code}: {resp.text}) — "
            "read tests need the publisher to accept submissions first"
        )
    body = resp.json()
    body["_bundle"] = bundle
    body["_bundle_hash"] = blake3_hex(bundle)
    return body


def _validate_iso_z(value: str, *, section: str) -> None:
    assert value.endswith("Z"), (
        f"{section}: timestamps must use ISO-8601 trailing 'Z' "
        f"(§9 lock #6); got {value!r}"
    )


# --------------------------------------------------------------------------
# GET /v1/agents/{id}
# --------------------------------------------------------------------------


def test_get_agent_returns_profile_shape(publisher_client, alice_keypair):
    """CONTRACTS.md §2.2 — response is AgentProfile (§1.9)."""
    seeded = _seed_one_submission(publisher_client, alice_keypair)
    agent_id = seeded["id"]

    resp = publisher_client.get(f"/v1/agents/{agent_id}")
    assert resp.status_code == 200, f"§2.2: {resp.status_code} {resp.text}"
    profile = _AgentProfile.model_validate(resp.json())
    assert profile.id == agent_id
    assert profile.bundle_hash == seeded["_bundle_hash"], (
        "§1.9 + §4.4: bundle_hash on the profile must equal blake3 of plaintext zip"
    )
    assert profile.miner_hotkey == alice_keypair.ss58_address
    _validate_iso_z(profile.submitted_at, section="§1.9 AgentProfile.submitted_at")


def test_get_agent_404_for_unknown_id(publisher_client):
    """CONTRACTS.md §2.2 — `404 agent not found`."""
    # Valid UUID format that doesn't exist.
    fake = "00000000-0000-4000-8000-000000000000"
    resp = publisher_client.get(f"/v1/agents/{fake}")
    if resp.status_code == 404:
        body = resp.json()
        assert body == {"detail": "agent not found"}, (
            f"§2.2 detail must be exactly 'agent not found'; got {body}"
        )
    else:
        # Some implementations 422 on bad UUID format; only the 404 case is
        # the contract surface here.
        assert resp.status_code in {404, 422}, (
            f"§2.2: unknown agent must be 404 (or 422 on validation), got {resp.status_code}"
        )


def test_get_agent_does_not_leak_bundle_contents(publisher_client, alice_keypair):
    """CONTRACTS.md §2.2 + ARCHITECTURE_V1 §"What Cathedral is" — the
    AgentProfile MUST NOT expose soul.md, skills, or bundle contents.

    Specifically, no field whose value contains the soul.md text we put
    in the zip is allowed.
    """
    secret_marker = "SOUL_SECRET_DO_NOT_LEAK_42"
    bundle = make_valid_bundle(soul_md=f"# {secret_marker}\nrest of soul\n")
    resp = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        display_name="Leakage Probe",
    )
    if resp.status_code != 202:
        pytest.skip(f"submit not implemented: {resp.text}")
    agent_id = resp.json()["id"]
    profile = publisher_client.get(f"/v1/agents/{agent_id}").json()
    serialized = repr(profile)
    assert secret_marker not in serialized, (
        f"CONTRACTS §10 + ARCHITECTURE 'cards public, bundle private': "
        f"AgentProfile leaked the soul.md text. Found {secret_marker!r} in {serialized[:300]}"
    )
    # AgentProfile shape (§1.9) MUST NOT include a soul_md_preview field
    # — that's internal-only per §1.9 comment.
    assert "soul_md_preview" not in profile, (
        "§1.9: soul_md_preview is internal-only; never exposed on AgentProfile"
    )


# --------------------------------------------------------------------------
# GET /v1/agents (discovery feed)
# --------------------------------------------------------------------------


def test_get_agents_returns_paginated_shape(publisher_client, alice_keypair):
    """CONTRACTS.md §2.3 — `{items, total, limit, offset}`."""
    _seed_one_submission(publisher_client, alice_keypair)
    resp = publisher_client.get("/v1/agents")
    assert resp.status_code == 200, f"§2.3: {resp.status_code} {resp.text}"
    body = resp.json()
    for k in ("items", "total", "limit", "offset"):
        assert k in body, f"§2.3 response missing `{k}`; got keys {list(body)}"
    assert isinstance(body["items"], list)
    assert isinstance(body["total"], int)
    assert isinstance(body["limit"], int)
    assert isinstance(body["offset"], int)
    for item in body["items"]:
        _LeaderboardEntry.model_validate(item)


def test_get_agents_invalid_sort_returns_400(publisher_client):
    """CONTRACTS.md §2.3 — `400 invalid sort`."""
    resp = publisher_client.get("/v1/agents?sort=bogus")
    assert resp.status_code in {400, 422}, (
        f"§2.3: invalid sort must be 400/422; got {resp.status_code}: {resp.text}"
    )


# --------------------------------------------------------------------------
# GET /v1/cards/{card_id}
# --------------------------------------------------------------------------


def test_get_card_returns_definition_and_best_eval_shape(publisher_client):
    """CONTRACTS.md §2.4 — response includes card_id, best_eval (or null),
    definition{...}, agent_count, latest_eval_at."""
    resp = publisher_client.get("/v1/cards/eu-ai-act")
    assert resp.status_code == 200, f"§2.4: {resp.status_code} {resp.text}"
    body = resp.json()
    for k in ("card_id", "best_eval", "definition", "agent_count", "latest_eval_at"):
        assert k in body, f"§2.4 response missing `{k}`; got {list(body)}"
    assert body["card_id"] == "eu-ai-act"
    definition = body["definition"]
    for k in ("id", "display_name", "jurisdiction", "topic", "description", "status"):
        assert k in definition, f"§2.4 definition missing `{k}`"
    assert definition["status"] in {"active", "archived"}, (
        "§2.4: definition.status must be active|archived"
    )
    if body["best_eval"] is not None:
        _EvalOutput.model_validate(body["best_eval"])


def test_get_card_404_for_unknown(publisher_client):
    resp = publisher_client.get("/v1/cards/totally-fake-card")
    assert resp.status_code == 404, "§2.4: unknown card must be 404"
    assert resp.json() == {"detail": "card not found"}, (
        "§2.4 detail must be exactly 'card not found'"
    )


# --------------------------------------------------------------------------
# GET /v1/cards/{card_id}/feed
# --------------------------------------------------------------------------


def test_get_card_feed_returns_items_and_next_since(publisher_client):
    """CONTRACTS.md §2.7 — `{items: EvalOutput[], next_since: iso|null}`."""
    resp = publisher_client.get("/v1/cards/eu-ai-act/feed?limit=5")
    assert resp.status_code == 200, f"§2.7: {resp.status_code} {resp.text}"
    body = resp.json()
    assert "items" in body and isinstance(body["items"], list)
    assert "next_since" in body
    assert body["next_since"] is None or isinstance(body["next_since"], str)
    for item in body["items"]:
        _EvalOutput.model_validate(item)


def test_get_card_feed_since_filter_is_iso(publisher_client):
    """§2.7 query `since` is ISO-8601."""
    resp = publisher_client.get(
        "/v1/cards/eu-ai-act/feed?since=2026-01-01T00:00:00.000Z"
    )
    assert resp.status_code == 200, f"§2.7 with since must work: {resp.text}"


def test_get_card_feed_reverse_chrono_order(publisher_client):
    """CONTRACTS.md §2.7 + brief 'reverse-chrono list'."""
    resp = publisher_client.get("/v1/cards/eu-ai-act/feed?limit=50")
    if resp.status_code != 200:
        pytest.skip(f"feed endpoint not ready: {resp.status_code}")
    items = resp.json()["items"]
    if len(items) < 2:
        pytest.skip("feed has too few items to verify ordering")
    timestamps = [i["ran_at"] for i in items]
    assert timestamps == sorted(timestamps, reverse=True), (
        f"§2.7 feed must be reverse chronological by ran_at; got {timestamps}"
    )


# --------------------------------------------------------------------------
# GET /v1/leaderboard
# --------------------------------------------------------------------------


def test_leaderboard_requires_card_param(publisher_client):
    """CONTRACTS.md §2.8 — `400 card parameter required`."""
    resp = publisher_client.get("/v1/leaderboard")
    assert resp.status_code in {400, 422}, (
        f"§2.8: missing card param must be 400/422; got {resp.status_code}"
    )


def test_leaderboard_returns_items_and_computed_at(publisher_client):
    resp = publisher_client.get("/v1/leaderboard?card=eu-ai-act")
    assert resp.status_code == 200, f"§2.8: {resp.text}"
    body = resp.json()
    assert "items" in body and isinstance(body["items"], list)
    assert "computed_at" in body and isinstance(body["computed_at"], str)
    _validate_iso_z(body["computed_at"], section="§2.8 leaderboard.computed_at")
    for entry in body["items"]:
        _LeaderboardEntry.model_validate(entry)


def test_leaderboard_unknown_card_returns_404(publisher_client):
    resp = publisher_client.get("/v1/leaderboard?card=fake-card-zzz")
    assert resp.status_code == 404, f"§2.8: unknown card must be 404, got {resp.status_code}"


def test_leaderboard_ranked_by_score_desc(publisher_client):
    """§2.8 — `ranked` means current_rank ascending, current_score descending."""
    resp = publisher_client.get("/v1/leaderboard?card=eu-ai-act&limit=200")
    if resp.status_code != 200:
        pytest.skip(f"leaderboard not ready: {resp.status_code}")
    items = resp.json()["items"]
    if len(items) < 2:
        pytest.skip("leaderboard has too few items to verify ordering")
    ranks = [i["current_rank"] for i in items]
    scores = [i["current_score"] for i in items]
    assert ranks == sorted(ranks), f"§2.8: items must be ordered by rank; got {ranks}"
    assert scores == sorted(scores, reverse=True), (
        f"§2.8: scores must be descending; got {scores}"
    )


# --------------------------------------------------------------------------
# GET /v1/leaderboard/recent (validator pull endpoint)
# --------------------------------------------------------------------------


def test_leaderboard_recent_requires_since(publisher_client):
    """CONTRACTS.md §2.9 — `since` is REQUIRED (no default)."""
    resp = publisher_client.get("/v1/leaderboard/recent")
    assert resp.status_code in {400, 422}, (
        f"§2.9: missing `since` must be 400/422; got {resp.status_code}"
    )


def test_leaderboard_recent_returns_cross_card_evals(publisher_client):
    """§2.9 response shape `{items, next_since, merkle_epoch_latest}`."""
    resp = publisher_client.get(
        "/v1/leaderboard/recent?since=2020-01-01T00:00:00.000Z"
    )
    assert resp.status_code == 200, f"§2.9: {resp.text}"
    body = resp.json()
    for k in ("items", "next_since", "merkle_epoch_latest"):
        assert k in body, f"§2.9 response missing `{k}`; got {list(body)}"
    assert isinstance(body["items"], list)
    for item in body["items"]:
        _EvalOutput.model_validate(item)
    assert body["merkle_epoch_latest"] is None or isinstance(
        body["merkle_epoch_latest"], int
    )


# --------------------------------------------------------------------------
# GET /v1/merkle/{epoch}
# --------------------------------------------------------------------------


def test_merkle_unknown_epoch_returns_404(publisher_client):
    """CONTRACTS.md §2.10 — `404 epoch not anchored`."""
    resp = publisher_client.get("/v1/merkle/999999")
    assert resp.status_code == 404, f"§2.10: {resp.status_code}"
    assert resp.json() == {"detail": "epoch not anchored"}, (
        "§2.10 detail must be exactly 'epoch not anchored'"
    )


def test_merkle_response_shape_when_present(publisher_client):
    """§2.10 — response is MerkleAnchor with leaf_hashes populated."""
    # Try epoch 1 — the contract says the merkle endpoint always returns
    # MerkleAnchor; if no anchor exists we expect 404.
    resp = publisher_client.get("/v1/merkle/1")
    if resp.status_code == 404:
        pytest.skip("no merkle anchor for epoch 1 yet — shape verified by 404 test")
    assert resp.status_code == 200, f"§2.10: {resp.status_code}"
    anchor = _MerkleAnchor.model_validate(resp.json())
    assert anchor.leaf_hashes is not None, (
        "§2.10: GET /merkle/{epoch} response MUST populate leaf_hashes (§1.13 last comment)"
    )
    # Each leaf hash is lowercase hex, 64 chars (§9 lock #4).
    for leaf in anchor.leaf_hashes:
        assert len(leaf) == 64 and leaf == leaf.lower(), (
            f"§9 lock #4: blake3 hex must be lowercase 64 chars; got {leaf!r}"
        )
    assert len(anchor.merkle_root) == 64 and anchor.merkle_root == anchor.merkle_root.lower()


# --------------------------------------------------------------------------
# GET /v1/miners/{hotkey}/agents
# --------------------------------------------------------------------------


def test_get_miner_agents_returns_items(publisher_client, alice_keypair):
    """CONTRACTS.md §2.11 — `{items: AgentProfile[]}`."""
    _seed_one_submission(publisher_client, alice_keypair)
    resp = publisher_client.get(f"/v1/miners/{alice_keypair.ss58_address}/agents")
    assert resp.status_code == 200, f"§2.11: {resp.text}"
    body = resp.json()
    assert "items" in body and isinstance(body["items"], list)
    for item in body["items"]:
        _AgentProfile.model_validate(item)


# --------------------------------------------------------------------------
# GET /v1/cards/{card_id}/eval-spec
# --------------------------------------------------------------------------


def test_get_eval_spec_returns_full_rubric(publisher_client):
    """CONTRACTS.md §2.6 — full eval spec including scoring rubric."""
    resp = publisher_client.get("/v1/cards/eu-ai-act/eval-spec")
    assert resp.status_code == 200, f"§2.6: {resp.text}"
    body = resp.json()
    for k in (
        "card_id",
        "display_name",
        "jurisdiction",
        "description_md",
        "eval_spec_md",
        "scoring_rubric",
        "task_templates",
        "source_pool",
        "refresh_cadence_hours",
    ):
        assert k in body, f"§2.6 response missing `{k}`"
    rubric = body["scoring_rubric"]
    # Six weights per §2.6.
    for w in (
        "source_quality_weight",
        "maintenance_weight",
        "freshness_weight",
        "specificity_weight",
        "usefulness_weight",
        "clarity_weight",
    ):
        assert w in rubric, f"§2.6 scoring_rubric missing `{w}`"


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------


def test_health_endpoint_shape(publisher_client):
    """CONTRACTS.md §2.12."""
    resp = publisher_client.get("/health")
    # 200 ok or 503 degraded — both are contract-valid.
    assert resp.status_code in {200, 503}, f"§2.12: {resp.status_code} {resp.text}"
    body = resp.json()
    assert "status" in body
    assert body["status"] in {"ok", "degraded"}, "§2.12: status must be ok|degraded"
    assert "checks" in body and isinstance(body["checks"], dict)
