"""Stub Polaris server for end-to-end smoke testing.

Signs records with an Ed25519 keypair generated at startup; the public key
is exposed at `GET /pubkey` (hex) so the validator config can be written
on the fly.

Endpoints (mirror the real Polaris API used by `cathedral.evidence.fetch`):
- GET /pubkey                              -> { "public_key_hex": ... }
- GET /v1/agents/{agent_id}/manifest       -> PolarisManifest
- GET /v1/runs/{run_id}                    -> PolarisRunRecord
- GET /v1/artifacts/{artifact_id}          -> PolarisArtifactRecord
- GET /artifact-bytes/{artifact_id}        -> raw bytes (Card JSON)
- GET /v1/agents/{agent_id}/usage          -> [PolarisUsageRecord, ...]
- POST /admin/seed                         -> seed records (dev convenience)
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import blake3
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException, Response

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cathedral.types import (  # noqa: E402
    PolarisArtifactRecord,
    PolarisManifest,
    PolarisRunRecord,
    PolarisUsageRecord,
    canonical_json_for_signing,
)

app = FastAPI(title="Stub Polaris")

_PRIVATE_KEY = Ed25519PrivateKey.generate()


def _sign_model(model_obj: Any) -> str:
    """Sign exactly the bytes the validator's verifier reconstructs."""
    dumped = model_obj.model_dump(by_alias=True, mode="json")
    payload = canonical_json_for_signing(dumped)
    return base64.b64encode(_PRIVATE_KEY.sign(payload)).decode()


def _sign_via_model(record: dict[str, Any], model_cls: Any) -> dict[str, Any]:
    record = dict(record)
    record.setdefault("signature", "")
    obj = model_cls.model_validate(record)
    obj.signature = _sign_model(obj)
    return obj.model_dump(by_alias=True, mode="json")


# In-memory state. Seeded at startup with a default agent + card.
STATE: dict[str, Any] = {
    "manifests": {},
    "runs": {},
    "artifacts": {},
    "artifact_bytes": {},
    "usage": {},
}


def _seed_default() -> None:
    """Seed one agent that produces a valid EU-AI-Act card."""
    agent_id = "agt_demo"
    owner = "5Owner_demo"
    now = datetime.now(UTC)

    manifest = _sign_via_model(
        {
            "polaris_agent_id": agent_id,
            "owner_wallet": owner,
            "created_at": now.isoformat(),
            "schema": "polaris.agent.v1",
        },
        PolarisManifest,
    )
    STATE["manifests"][agent_id] = manifest

    run = _sign_via_model(
        {
            "run_id": "run_demo",
            "polaris_agent_id": agent_id,
            "started_at": now.isoformat(),
            "ended_at": (now + timedelta(seconds=30)).isoformat(),
            "outcome": "ok",
        },
        PolarisRunRecord,
    )
    STATE["runs"]["run_demo"] = run

    card_doc = {
        "jurisdiction": "eu",
        "topic": "EU AI Act enforcement",
        "title": "EU AI Act update — May 2026",
        "summary": (
            "Recent guidance from the Commission tightens transparency requirements "
            "for general-purpose AI models. New thresholds apply from late 2026."
        ),
        "what_changed": (
            "Article 55 obligations now extend to systems above the new compute "
            "threshold; documentation requirements deepened across the board."
        ),
        "why_it_matters": (
            "Providers above the threshold must publish detailed model summaries; "
            "noncompliance carries fines up to 7 percent of global turnover."
        ),
        "action_notes": "Audit your model cards against the new template by Q3.",
        "risks": "Penalties scale with revenue; downstream deployers also at risk.",
        "citations": [
            {
                "url": "https://eur-lex.europa.eu/example",
                "class": "official_journal",
                "fetched_at": now.isoformat(),
                "status": 200,
                "content_hash": "deadbeef",
            },
            {
                "url": "https://digital-strategy.ec.europa.eu/example",
                "class": "regulator",
                "fetched_at": now.isoformat(),
                "status": 200,
                "content_hash": "cafe1234",
            },
        ],
        "confidence": 0.85,
        "no_legal_advice": True,
        "last_refreshed_at": now.isoformat(),
        "refresh_cadence_hours": 24,
    }
    body_bytes = json.dumps(card_doc).encode()

    artifact = _sign_via_model(
        {
            "artifact_id": "art_demo",
            "polaris_agent_id": agent_id,
            "run_id": "run_demo",
            "content_url": "http://127.0.0.1:9444/artifact-bytes/art_demo",
            "content_hash": blake3.blake3(body_bytes).hexdigest(),
            "report_hash": json.dumps(card_doc),
        },
        PolarisArtifactRecord,
    )
    STATE["artifacts"]["art_demo"] = artifact
    STATE["artifact_bytes"]["art_demo"] = body_bytes

    usage_recs = []
    for i in range(3):
        usage_recs.append(
            _sign_via_model(
                {
                    "usage_id": f"u_demo_{i}",
                    "polaris_agent_id": agent_id,
                    "consumer": "external",
                    "consumer_wallet": f"5Consumer_{i}",
                    "used_at": now.isoformat(),
                    "flagged": False,
                    "refunded": False,
                },
                PolarisUsageRecord,
            )
        )
    # plus one self-loop and one flagged, to exercise the filter
    usage_recs.append(
        _sign_via_model(
            {
                "usage_id": "u_demo_self",
                "polaris_agent_id": agent_id,
                "consumer": "external",
                "consumer_wallet": owner,
                "used_at": now.isoformat(),
                "flagged": False,
                "refunded": False,
            },
            PolarisUsageRecord,
        )
    )
    usage_recs.append(
        _sign_via_model(
            {
                "usage_id": "u_demo_flag",
                "polaris_agent_id": agent_id,
                "consumer": "external",
                "consumer_wallet": "5Bad",
                "used_at": now.isoformat(),
                "flagged": True,
                "refunded": False,
            },
            PolarisUsageRecord,
        )
    )
    STATE["usage"][agent_id] = usage_recs


_seed_default()


@app.get("/pubkey")
def pubkey() -> dict[str, str]:
    raw = _PRIVATE_KEY.public_key().public_bytes_raw()
    return {"public_key_hex": raw.hex()}


@app.get("/v1/agents/{agent_id}/manifest")
def get_manifest(agent_id: str) -> dict[str, Any]:
    if agent_id not in STATE["manifests"]:
        raise HTTPException(status_code=404)
    return STATE["manifests"][agent_id]


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    if run_id not in STATE["runs"]:
        raise HTTPException(status_code=404)
    return STATE["runs"][run_id]


@app.get("/v1/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> dict[str, Any]:
    if artifact_id not in STATE["artifacts"]:
        raise HTTPException(status_code=404)
    return STATE["artifacts"][artifact_id]


@app.get("/artifact-bytes/{artifact_id}")
def get_artifact_bytes(artifact_id: str) -> Response:
    body = STATE["artifact_bytes"].get(artifact_id)
    if body is None:
        raise HTTPException(status_code=404)
    return Response(content=body, media_type="application/json")


@app.get("/v1/agents/{agent_id}/usage")
def get_usage(agent_id: str) -> list[dict[str, Any]]:
    return STATE["usage"].get(agent_id, [])


def main() -> None:
    port = int(os.environ.get("STUB_POLARIS_PORT", "9444"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
