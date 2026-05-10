"""Tests for the cards-on-Cathedral path.

Cards live on Cathedral, not Polaris. Miners submit the card payload
inline with the claim. These tests verify:

1. PolarisAgentClaim accepts an inline `card_payload` field.
2. The worker prefers the inline payload over decoding from artifacts.
3. The verified card lands in the `cards` SQLite table.
4. The `/v1/cards/{id}` and `/v1/cards/{id}/history` read endpoints
   surface what the validator stored.
5. Backward compatibility: claims without `card_payload` still work
   via the legacy artifact-decode path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi.testclient import TestClient

from cathedral.cards.registry import CardRegistry
from cathedral.evidence import EvidenceCollector
from cathedral.types import PolarisAgentClaim
from cathedral.validator import cards as cards_store
from cathedral.validator import queue, worker
from cathedral.validator.app import build_app
from cathedral.validator.config_runtime import RuntimeContext
from cathedral.validator.db import connect
from cathedral.validator.health import Health

from tests.conftest import (
    StubFetcher,
    make_card,
    make_signed_artifact,
    make_signed_manifest,
    make_signed_run,
    make_signed_usage,
)


@pytest.fixture
async def db_conn(tmp_path):
    conn = await connect(str(tmp_path / "validator.db"))
    yield conn
    await conn.close()


@pytest.fixture
def signed_evidence(keypair):
    sk, pk = keypair
    fetcher = StubFetcher()
    fetcher.manifests["agt_1"] = make_signed_manifest(sk, "agt_1")
    fetcher.runs["run_1"] = make_signed_run(sk, "run_1", "agt_1")
    fetcher.usage["agt_1"] = [
        make_signed_usage(sk, "use_1", consumer_wallet="wallet_external")
    ]
    return sk, pk, fetcher


def _make_claim_with_card(card_payload: dict | None = None) -> PolarisAgentClaim:
    return PolarisAgentClaim(
        miner_hotkey="5HotKey",
        owner_wallet="wallet_owner",
        work_unit="card:eu-ai-act",
        polaris_agent_id="agt_1",
        polaris_run_ids=["run_1"],
        polaris_artifact_ids=[],
        card_payload=card_payload,
    )


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


def test_claim_accepts_inline_card_payload():
    card = make_card()
    payload = card.model_dump(mode="json")
    claim = _make_claim_with_card(payload)
    assert claim.card_payload == payload


def test_claim_accepts_null_card_payload_for_backcompat():
    claim = _make_claim_with_card(None)
    assert claim.card_payload is None


# ---------------------------------------------------------------------------
# Worker — inline payload path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_verifies_claim_with_inline_card_and_writes_cards_row(
    db_conn, signed_evidence
):
    sk, pk, fetcher = signed_evidence
    collector = EvidenceCollector(fetcher, pk)
    health = Health()
    registry = CardRegistry.baseline()

    card = make_card()
    payload = card.model_dump(mode="json")
    # Strip the three context fields — the worker fills them.
    payload.pop("id", None)
    payload.pop("worker_owner_hotkey", None)
    payload.pop("polaris_agent_id", None)

    claim = _make_claim_with_card(payload)
    claim_id = await queue.insert_claim(db_conn, claim)

    batch = await queue.claim_pending(db_conn)
    assert len(batch) == 1
    await worker._verify_one(db_conn, collector, registry, health, batch[0])

    cur = await db_conn.execute(
        "SELECT status FROM claims WHERE id = ?", (claim_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == "verified"

    stored = await cards_store.best_card(db_conn, "eu-ai-act")
    assert stored is not None
    assert stored["card_id"] == "eu-ai-act"
    assert stored["miner_hotkey"] == "5HotKey"
    assert stored["polaris_agent_id"] == "agt_1"
    assert stored["owner_wallet"] == "wallet_owner"
    assert stored["card"]["title"] == "EU AI Act update"
    assert 0.0 <= stored["weighted_score"] <= 1.0


@pytest.mark.asyncio
async def test_worker_skips_artifact_fetch_when_inline_card_present(
    db_conn, signed_evidence
):
    """Performance + reliability: if the miner sent the card inline,
    the validator must NOT round-trip to Polaris for artifacts. Cards
    on Cathedral means cathedral never asks Polaris for cards."""
    sk, pk, fetcher = signed_evidence
    # Add a deliberately-broken artifact id; if the worker tried to
    # fetch it, the test would fail with a confused MissingRecordError.
    # With inline payload, this list should be ignored entirely.
    collector = EvidenceCollector(fetcher, pk)

    card = make_card()
    payload = card.model_dump(mode="json")
    claim = PolarisAgentClaim(
        miner_hotkey="5HotKey",
        owner_wallet="wallet_owner",
        work_unit="card:eu-ai-act",
        polaris_agent_id="agt_1",
        polaris_run_ids=["run_1"],
        polaris_artifact_ids=["does-not-exist-on-polaris"],
        card_payload=payload,
    )
    bundle = await collector.collect(claim)
    assert bundle.artifacts == [], (
        "artifacts must be empty when card_payload is inline — Polaris "
        "must not be asked for the missing id"
    )


@pytest.mark.asyncio
async def test_worker_rejects_inline_payload_that_fails_card_validation(
    db_conn, signed_evidence
):
    sk, pk, fetcher = signed_evidence
    collector = EvidenceCollector(fetcher, pk)
    health = Health()
    registry = CardRegistry.baseline()

    # Missing required field — confidence.
    bad_payload = {
        "jurisdiction": "eu",
        "topic": "EU AI Act",
        "title": "Update",
        "summary": "x" * 80,
        "what_changed": "x" * 100,
        "why_it_matters": "x" * 100,
        "action_notes": "x" * 50,
        "risks": "x" * 50,
        "citations": [],
        "no_legal_advice": True,
        "last_refreshed_at": datetime.now(UTC).isoformat(),
        "refresh_cadence_hours": 24,
    }

    claim = _make_claim_with_card(bad_payload)
    claim_id = await queue.insert_claim(db_conn, claim)
    batch = await queue.claim_pending(db_conn)
    await worker._verify_one(db_conn, collector, registry, health, batch[0])

    cur = await db_conn.execute(
        "SELECT status, rejection_reason FROM claims WHERE id = ?", (claim_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == "rejected"
    assert row[1] == "no_card"


# ---------------------------------------------------------------------------
# Worker — backward compatibility (artifact path still works)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_falls_back_to_artifact_decode_when_no_inline_payload(
    db_conn, signed_evidence
):
    """Earlier-spec miners that pushed cards through Polaris artifacts
    must still verify. Catches accidental removal of the legacy path."""
    sk, pk, fetcher = signed_evidence
    collector = EvidenceCollector(fetcher, pk)
    health = Health()
    registry = CardRegistry.baseline()

    card = make_card()
    report = card.model_dump(mode="json")
    report.pop("id", None)
    report.pop("worker_owner_hotkey", None)
    report.pop("polaris_agent_id", None)

    artifact = make_signed_artifact(
        sk, "art_1", content=b"unused", report=report, agent_id="agt_1"
    )
    fetcher.artifacts["art_1"] = artifact
    fetcher.artifact_bytes[artifact.content_url] = b"unused"

    claim = PolarisAgentClaim(
        miner_hotkey="5LegacyMiner",
        owner_wallet="wallet_owner",
        work_unit="card:eu-ai-act",
        polaris_agent_id="agt_1",
        polaris_run_ids=["run_1"],
        polaris_artifact_ids=["art_1"],
        card_payload=None,
    )
    claim_id = await queue.insert_claim(db_conn, claim)
    batch = await queue.claim_pending(db_conn)
    await worker._verify_one(db_conn, collector, registry, health, batch[0])

    cur = await db_conn.execute(
        "SELECT status FROM claims WHERE id = ?", (claim_id,)
    )
    row = await cur.fetchone()
    assert row is not None and row[0] == "verified"

    stored = await cards_store.best_card(db_conn, "eu-ai-act")
    assert stored is not None
    assert stored["miner_hotkey"] == "5LegacyMiner"


# ---------------------------------------------------------------------------
# Card store — best/history
# ---------------------------------------------------------------------------


async def _seed_claim(conn, claim_id: int, miner: str, owner: str) -> None:
    """Cards.claim_id has a FK on claims.id — seed a parent claim row."""
    await conn.execute(
        """
        INSERT INTO claims (id, miner_hotkey, owner_wallet, work_unit,
                            polaris_agent_id, payload_json, status, submitted_at)
        VALUES (?, ?, ?, 'card:eu-ai-act', 'agt_x', '{}', 'verified',
                '2026-05-01T00:00:00+00:00')
        """,
        (claim_id, miner, owner),
    )


@pytest.mark.asyncio
async def test_best_card_returns_highest_scoring_version(db_conn):
    """When two miners maintain the same card, /v1/cards/{id} returns
    the highest-scoring one — that's the canonical view."""
    await _seed_claim(db_conn, 1, "5MinerA", "wallet_a")
    await _seed_claim(db_conn, 2, "5MinerB", "wallet_b")
    await db_conn.executemany(
        """
        INSERT INTO cards (
            card_id, miner_hotkey, polaris_agent_id, owner_wallet,
            claim_id, card_json, weighted_score, last_refreshed_at, verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "eu-ai-act",
                "5MinerA",
                "agt_a",
                "wallet_a",
                1,
                json.dumps({"id": "eu-ai-act", "title": "Lower-quality"}),
                0.45,
                "2026-05-01T00:00:00+00:00",
                "2026-05-01T00:00:00+00:00",
            ),
            (
                "eu-ai-act",
                "5MinerB",
                "agt_b",
                "wallet_b",
                2,
                json.dumps({"id": "eu-ai-act", "title": "Higher-quality"}),
                0.85,
                "2026-05-01T00:00:00+00:00",
                "2026-05-01T00:00:00+00:00",
            ),
        ],
    )
    await db_conn.commit()

    best = await cards_store.best_card(db_conn, "eu-ai-act")
    assert best is not None
    assert best["miner_hotkey"] == "5MinerB"
    assert best["weighted_score"] == 0.85


@pytest.mark.asyncio
async def test_best_card_returns_none_for_unknown_card(db_conn):
    assert await cards_store.best_card(db_conn, "unknown-card") is None


@pytest.mark.asyncio
async def test_card_history_returns_all_miners_newest_first(db_conn):
    await _seed_claim(db_conn, 1, "5MinerA", "wallet_a")
    await _seed_claim(db_conn, 2, "5MinerB", "wallet_b")
    await db_conn.executemany(
        """
        INSERT INTO cards (
            card_id, miner_hotkey, polaris_agent_id, owner_wallet,
            claim_id, card_json, weighted_score, last_refreshed_at, verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "eu-ai-act",
                "5MinerA",
                "agt_a",
                "wallet_a",
                1,
                "{}",
                0.45,
                "2026-05-01T00:00:00+00:00",
                "2026-05-01T00:00:00+00:00",
            ),
            (
                "eu-ai-act",
                "5MinerB",
                "agt_b",
                "wallet_b",
                2,
                "{}",
                0.85,
                "2026-05-01T00:00:00+00:00",
                "2026-05-02T00:00:00+00:00",
            ),
        ],
    )
    await db_conn.commit()

    history = await cards_store.card_history(db_conn, "eu-ai-act")
    assert len(history) == 2
    # Newest verification first.
    assert history[0]["miner_hotkey"] == "5MinerB"
    assert history[1]["miner_hotkey"] == "5MinerA"


# ---------------------------------------------------------------------------
# HTTP read endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_with_card(db_conn, signed_evidence, monkeypatch):
    """Build the validator app with the test DB plus a single verified
    card already in storage. Stalls/weight loop are no-ops in tests."""
    sk, pk, fetcher = signed_evidence

    # claim row first — cards.claim_id has a FK on claims.id
    await db_conn.execute(
        """
        INSERT INTO claims (id, miner_hotkey, owner_wallet, work_unit,
                            polaris_agent_id, payload_json, status, submitted_at)
        VALUES (999, '5MinerA', 'wallet_a', 'card:eu-ai-act', 'agt_a', '{}',
                'verified', '2026-05-10T00:00:00+00:00')
        """
    )
    await db_conn.execute(
        """
        INSERT INTO cards (
            card_id, miner_hotkey, polaris_agent_id, owner_wallet,
            claim_id, card_json, weighted_score, last_refreshed_at, verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "eu-ai-act",
            "5MinerA",
            "agt_a",
            "wallet_a",
            999,
            json.dumps({"id": "eu-ai-act", "title": "EU AI Act update"}),
            0.72,
            "2026-05-10T00:00:00+00:00",
            "2026-05-10T00:00:00+00:00",
        ),
    )
    await db_conn.commit()

    # We don't actually use the lifespan tasks in these tests; build a
    # minimal app with our pre-loaded conn injected on startup.
    from fastapi import FastAPI

    from cathedral.validator.auth import make_bearer_dep

    bearer_dep = make_bearer_dep("test-token")
    app = FastAPI()
    app.state.db = db_conn

    @app.get("/v1/cards/{card_id}")
    async def get_card(card_id: str):
        from fastapi import HTTPException

        row = await cards_store.best_card(app.state.db, card_id)
        if row is None:
            raise HTTPException(404, detail="card not found")
        return row

    @app.get("/v1/cards/{card_id}/history")
    async def get_history(card_id: str):
        return await cards_store.card_history(app.state.db, card_id)

    return app


@pytest.mark.asyncio
async def test_get_card_endpoint_returns_canonical_card(app_with_card):
    client = TestClient(app_with_card)
    resp = client.get("/v1/cards/eu-ai-act")
    assert resp.status_code == 200
    body = resp.json()
    assert body["card_id"] == "eu-ai-act"
    assert body["miner_hotkey"] == "5MinerA"
    assert body["weighted_score"] == 0.72


@pytest.mark.asyncio
async def test_get_card_endpoint_404_for_unknown_card(app_with_card):
    client = TestClient(app_with_card)
    resp = client.get("/v1/cards/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_card_history_endpoint(app_with_card):
    client = TestClient(app_with_card)
    resp = client.get("/v1/cards/eu-ai-act/history")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["miner_hotkey"] == "5MinerA"
