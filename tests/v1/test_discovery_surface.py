"""Verified-only leaderboard + discovery surface — per task brief.

Pins the read-side surface split:

* GET /v1/leaderboard?card=...        — verified only (polaris/tee)
* GET /v1/cards/{id}                  — agent_count = verified only
* GET /v1/cards/{id}/discovery        — unverified (discovery) only
* GET /v1/cards/{id}/discovery/count  — cheap counter
* GET /v1/discovery/recent            — cross-card unverified feed

The submit pipeline is deep (signature, similarity, bundle encryption,
Hippius PUT). To isolate the read surface from those concerns we seed
rows directly via the validator-db connection and the repository
helpers — exactly what the publisher writes.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cathedral.publisher import repository
from cathedral.validator.db import connect as connect_db


def _now_iso_z(offset_secs: int = 0) -> str:
    now = datetime.now(UTC) + timedelta(seconds=offset_secs)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}" + "Z"


async def _seed_verified(
    conn: Any,
    *,
    card_id: str,
    display_name: str = "Verified Probe",
    hotkey: str = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
    status: str = "ranked",
    score: float = 0.82,
    rank: int = 1,
) -> str:
    """Insert a polaris-mode submission, optionally pre-ranked.

    We don't run the eval pipeline; we hand-write the row using the same
    repository helper the publisher uses. `status='ranked'` + a real
    `current_score` makes the row appear on the verified leaderboard.
    """
    sub_id = secrets.token_hex(16)
    submitted_at = datetime.now(UTC)
    submitted_at_iso = _now_iso_z()
    await repository.insert_agent_submission(
        conn,
        id=sub_id,
        miner_hotkey=hotkey,
        card_id=card_id,
        bundle_blob_key=f"bundles/{sub_id}.bin",
        bundle_hash="a" * 64,
        bundle_size_bytes=4096,
        encryption_key_id="kek-test",
        bundle_signature="b64:stub",
        display_name=display_name,
        bio=None,
        logo_url=None,
        soul_md_preview=None,
        metadata_fingerprint=secrets.token_hex(8),
        similarity_check_passed=True,
        rejection_reason=None,
        status=status,
        submitted_at=submitted_at,
        submitted_at_iso=submitted_at_iso,
        first_mover_at=None,
        attestation_mode="polaris",
        attestation_verified_at=None,
        discovery_only=False,
    )
    if status == "ranked":
        await repository.update_submission_score(
            conn, sub_id, current_score=score, current_rank=rank
        )
    return sub_id


async def _seed_unverified(
    conn: Any,
    *,
    card_id: str,
    display_name: str = "Unverified Probe",
    hotkey: str = "5FvW8nUb9kRyTaRGAfFnXJ9JdPmZcGEqe7fS3ZkVk5KS7fAa",
    soul_md_preview: str | None = None,
    submitted_offset_secs: int = 0,
) -> str:
    """Insert an attestation_mode='unverified', status='discovery' row."""
    sub_id = secrets.token_hex(16)
    submitted_at = datetime.now(UTC) + timedelta(seconds=submitted_offset_secs)
    submitted_at_iso = _now_iso_z(submitted_offset_secs)
    await repository.insert_agent_submission(
        conn,
        id=sub_id,
        miner_hotkey=hotkey,
        card_id=card_id,
        bundle_blob_key=f"bundles/{sub_id}.bin",
        bundle_hash="d" * 64,
        bundle_size_bytes=2048,
        encryption_key_id="kek-test",
        bundle_signature="b64:stub",
        display_name=display_name,
        bio="Research-grade exploratory agent.",
        logo_url=None,
        soul_md_preview=soul_md_preview,
        metadata_fingerprint=secrets.token_hex(8),
        similarity_check_passed=True,
        rejection_reason=None,
        status="discovery",
        submitted_at=submitted_at,
        submitted_at_iso=submitted_at_iso,
        first_mover_at=None,
        attestation_mode="unverified",
        attestation_verified_at=None,
        discovery_only=True,
    )
    return sub_id


@pytest.fixture
def seeded(publisher_app, tmp_path: Path):
    """Returns ``(client, ids)`` with one verified + one unverified
    submission on ``eu-ai-act`` plus an unverified one on another card.

    Start the TestClient first so the publisher lifespan seeds card
    definitions (needed for the FK on agent_submissions.card_id). Then
    open a second aiosqlite connection to the same WAL DB to seed our
    test rows.
    """
    import asyncio

    from fastapi.testclient import TestClient

    db_path = tmp_path / "publisher.db"

    async def _seed() -> dict[str, str]:
        conn = await connect_db(str(db_path))
        try:
            verified_id = await _seed_verified(
                conn, card_id="eu-ai-act", display_name="Verified Agent"
            )
            unverified_id = await _seed_unverified(
                conn,
                card_id="eu-ai-act",
                display_name="Discovery Agent",
                soul_md_preview="# Soul preview\nThis is the first 500 chars of the soul.",
            )
            # A second unverified on a different card, slightly older —
            # used by the cross-card /v1/discovery/recent test.
            other_id = await _seed_unverified(
                conn,
                card_id="us-ai-eo",
                display_name="Other Card Discovery",
                submitted_offset_secs=-30,
            )
            await conn.commit()
        finally:
            await conn.close()
        return {
            "verified": verified_id,
            "unverified": unverified_id,
            "other": other_id,
        }

    with TestClient(publisher_app) as client:
        # publisher_app lifespan has now seeded card definitions; safe
        # to insert agent_submissions referencing them.
        ids = asyncio.run(_seed())
        yield client, ids


# --------------------------------------------------------------------------
# Verified-only leaderboard / card overview
# --------------------------------------------------------------------------


def test_leaderboard_excludes_unverified(seeded):
    client, ids = seeded
    resp = client.get("/v1/leaderboard?card=eu-ai-act")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    returned_ids = {row["agent_id"] for row in items}
    assert ids["verified"] in returned_ids, "verified agent must appear on the leaderboard"
    assert ids["unverified"] not in returned_ids, (
        f"discovery agent leaked onto the leaderboard: {returned_ids}"
    )


def test_card_overview_agent_count_excludes_unverified(seeded):
    client, _ids = seeded
    resp = client.get("/v1/cards/eu-ai-act")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Exactly one verified agent on eu-ai-act in the fixture.
    assert body["agent_count"] == 1, (
        f"agent_count must count only verified agents; got {body['agent_count']}"
    )


def test_card_overview_mirror_path(seeded):
    """`/api/cathedral/v1/cards/{id}` is the canonical contract surface."""
    client, _ids = seeded
    resp = client.get("/api/cathedral/v1/cards/eu-ai-act")
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_count"] == 1


def test_agents_listing_excludes_unverified(seeded):
    client, ids = seeded
    resp = client.get("/v1/agents?card=eu-ai-act")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    returned_ids = {row["agent_id"] for row in items}
    assert ids["unverified"] not in returned_ids


def test_leaderboard_recent_excludes_unverified(seeded):
    client, ids = seeded
    resp = client.get("/v1/leaderboard/recent?since=2020-01-01T00:00:00.000Z")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # Discovery rows never produce eval_runs, so they could never appear
    # here even before the join was tightened. The test still pins it.
    leaked = [it for it in items if it["agent_id"] == ids["unverified"]]
    assert not leaked, f"discovery agent leaked onto recent: {leaked}"


# --------------------------------------------------------------------------
# Discovery endpoints
# --------------------------------------------------------------------------


_REQUIRED_DISCOVERY_KEYS = {
    "agent_id",
    "display_name",
    "logo_url",
    "bio",
    "miner_hotkey",
    "card_id",
    "bundle_hash",
    "bundle_size_bytes",
    "submitted_at",
    "soul_md_preview",
    "tags",
}


def test_card_discovery_returns_only_unverified(seeded):
    client, ids = seeded
    resp = client.get("/v1/cards/eu-ai-act/discovery")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1, body
    assert body["limit"] == 50 and body["offset"] == 0
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["agent_id"] == ids["unverified"]
    assert set(item.keys()) >= _REQUIRED_DISCOVERY_KEYS, (
        f"missing keys: {_REQUIRED_DISCOVERY_KEYS - set(item.keys())}"
    )
    assert "unverified" in item["tags"]
    assert item["soul_md_preview"] is not None  # we seeded it


def test_card_discovery_mirror_path(seeded):
    """`/api/cathedral/v1/cards/{id}/discovery` is the canonical surface."""
    client, _ids = seeded
    resp = client.get("/api/cathedral/v1/cards/eu-ai-act/discovery")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1


def test_card_discovery_count(seeded):
    client, _ids = seeded
    resp = client.get("/v1/cards/eu-ai-act/discovery/count")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"total": 1}


def test_card_discovery_count_mirror_path(seeded):
    client, _ids = seeded
    resp = client.get("/api/cathedral/v1/cards/eu-ai-act/discovery/count")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"total": 1}


def test_card_discovery_unknown_card_returns_404(seeded):
    client, _ids = seeded
    resp = client.get("/v1/cards/totally-fake/discovery")
    assert resp.status_code == 404


def test_discovery_recent_lists_cross_card(seeded):
    client, ids = seeded
    resp = client.get("/v1/discovery/recent?limit=20")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned_ids = [it["agent_id"] for it in body["items"]]
    assert ids["unverified"] in returned_ids
    assert ids["other"] in returned_ids
    # Cross-card: the verified agent is NOT here.
    assert ids["verified"] not in returned_ids
    # Newest first.
    assert returned_ids.index(ids["unverified"]) < returned_ids.index(ids["other"])


def test_discovery_recent_mirror_path(seeded):
    client, _ids = seeded
    resp = client.get("/api/cathedral/v1/discovery/recent")
    assert resp.status_code == 200, resp.text


def test_card_discovery_pagination_bounds(seeded):
    client, _ids = seeded
    # limit=200 must clamp to 100.
    resp = client.get("/v1/cards/eu-ai-act/discovery?limit=200")
    assert resp.status_code in {400, 422}, resp.text

    resp = client.get("/v1/cards/eu-ai-act/discovery?limit=0")
    assert resp.status_code in {400, 422}, resp.text


# --------------------------------------------------------------------------
# Agent profile attestation_mode pass-through
# --------------------------------------------------------------------------


def test_agent_profile_exposes_attestation_mode_for_verified(seeded):
    client, ids = seeded
    resp = client.get(f"/v1/agents/{ids['verified']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["attestation_mode"] == "polaris"


def test_agent_profile_exposes_attestation_mode_for_unverified(seeded):
    client, ids = seeded
    resp = client.get(f"/v1/agents/{ids['unverified']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attestation_mode"] == "unverified"
    assert body["status"] == "discovery"
