"""Probe-mode contract for cathedral-runtime (BUILD_V1.md §B.1, §B.2).

The runtime container lives at `docker/cathedral-runtime/server.py` and
isn't on the install path. These tests load it dynamically with
`importlib.util` so we can exercise the FastAPI app with TestClient
without packaging the runtime as a library.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from bittensor_wallet import Keypair
from fastapi.testclient import TestClient

RUNTIME_PATH = Path(__file__).resolve().parents[1] / "docker" / "cathedral-runtime" / "server.py"


# --------------------------------------------------------------------------
# Helpers — load the runtime server module under controlled env / hotkey
# --------------------------------------------------------------------------


def _write_hotkey_json(path: Path, mnemonic: str, ss58: str) -> None:
    """Substrate-format hotkey JSON, matching the `/etc/cathedral-probe/hotkey.json` shape."""
    keypair = Keypair.create_from_mnemonic(mnemonic)
    pub_hex = "0x" + (keypair.public_key or b"").hex()
    path.write_text(
        json.dumps(
            {
                "accountId": pub_hex,
                "publicKey": pub_hex,
                "secretPhrase": mnemonic,
                "ss58Address": ss58,
            }
        ),
        encoding="utf-8",
    )


def _load_server(
    probe_mode: bool,
    hotkey_path: Path | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Fresh module load — required because probe endpoints register at
    import time based on env. Using importlib avoids polluting sys.modules
    across tests."""
    monkeypatch.setenv("CATHEDRAL_PROBE_MODE", "true" if probe_mode else "false")
    if hotkey_path is not None:
        monkeypatch.setenv("CATHEDRAL_PROBE_HOTKEY_PATH", str(hotkey_path))
    else:
        monkeypatch.setenv("CATHEDRAL_PROBE_HOTKEY_PATH", str(tmp_path / "nonexistent.json"))
    monkeypatch.setenv("CATHEDRAL_PROBE_TRACE_DIR", str(tmp_path / "traces"))

    sys.modules.pop("cathedral_runtime_server", None)
    spec = importlib.util.spec_from_file_location("cathedral_runtime_server", RUNTIME_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cathedral_runtime_server"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def alice_mnemonic() -> str:
    return "bottom drive obey lake curtain smoke basket hold race lonely fit walk"


@pytest.fixture
def alice_ss58(alice_mnemonic: str) -> str:
    return Keypair.create_from_mnemonic(alice_mnemonic).ss58_address


@pytest.fixture
def hotkey_file(tmp_path: Path, alice_mnemonic: str, alice_ss58: str) -> Path:
    p = tmp_path / "hotkey.json"
    _write_hotkey_json(p, alice_mnemonic, alice_ss58)
    return p


# --------------------------------------------------------------------------
# Probe-mode toggle
# --------------------------------------------------------------------------


def test_probe_endpoints_not_registered_when_mode_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CATHEDRAL_PROBE_MODE unset/false → /probe/* must 404. Per-eval
    `/task` and `/healthz` still work."""
    module = _load_server(
        probe_mode=False, hotkey_path=None, tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    with TestClient(module.app) as client:
        assert client.get("/probe/health").status_code == 404
        assert client.post("/probe/run", json={}).status_code == 404
        assert client.post("/probe/reload", json={}).status_code == 404
        assert client.get("/healthz").status_code == 200


def test_probe_endpoints_not_registered_when_mode_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CATHEDRAL_PROBE_MODE", raising=False)
    sys.modules.pop("cathedral_runtime_server", None)
    spec = importlib.util.spec_from_file_location("cathedral_runtime_server", RUNTIME_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cathedral_runtime_server"] = module
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        assert client.get("/probe/health").status_code == 404


# --------------------------------------------------------------------------
# /probe/health
# --------------------------------------------------------------------------


def test_probe_health_returns_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hotkey_file: Path, alice_ss58: str
) -> None:
    module = _load_server(
        probe_mode=True, hotkey_path=hotkey_file, tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    with TestClient(module.app) as client:
        resp = client.get("/probe/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["miner_hotkey"] == alice_ss58
        assert body["runtime_version"]
        assert "last_run_at" in body
        assert body["last_run_at"] is None  # no runs yet


# --------------------------------------------------------------------------
# /probe/run signs ProbeOutput with the loaded hotkey
# --------------------------------------------------------------------------


async def _stub_run_probe(module: Any, alice_ss58: str) -> Any:
    """Drive `_run_probe` end-to-end without hitting the network.

    Returns a probe_run handler bound to a request with stubbed
    LLM / source-fetch functions. We patch the heavy outbound calls
    and exercise the signing + canonicalization path for real.
    """
    return None


def _patch_runtime_io(module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub eval-spec, source fetch, and LLM so /probe/run is hermetic."""

    async def _fake_eval_spec(_publisher: str, _card_id: str) -> dict[str, Any]:
        return {
            "source_pool": [
                {"url": "https://example.com/a", "class": "official_journal", "name": "A"},
            ],
            "scoring_rubric": {"min_citations": 1, "required_source_classes": ["official_journal"]},
            "refresh_cadence_hours": 24,
        }

    async def _fake_sources(_pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "url": "https://example.com/a",
                "class": "official_journal",
                "fetched_at": "2026-05-11T00:00:00.000Z",
                "status": 200,
                "content_hash": "f" * 64,
                "content_type": "text/html",
                "fetch_latency_ms": 42,
                "_excerpt": "regulation body text",
            }
        ]

    async def _fake_llm(
        _key: str,
        _base: str,
        _model: str,
        _soul: str,
        _task: str,
        _card_id: str,
        _cits: list[dict[str, Any]],
        _spec: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        return (
            {
                "jurisdiction": "eu",
                "topic": "ai-act",
                "title": "Test",
                "summary": "x" * 60,
                "what_changed": "x",
                "why_it_matters": "x",
                "action_notes": "x",
                "risks": "x",
                "citations": [
                    {
                        "url": "https://example.com/a",
                        "class": "official_journal",
                        "fetched_at": "claimed",
                        "status": 200,
                        "content_hash": "claimed",
                    }
                ],
                "confidence": 0.9,
                "no_legal_advice": True,
                "last_refreshed_at": "2026-05-11T00:00:00Z",
                "refresh_cadence_hours": 24,
            },
            {"input_tokens": 100, "output_tokens": 200},
        )

    monkeypatch.setattr(module, "_fetch_eval_spec", _fake_eval_spec)
    monkeypatch.setattr(module, "_fetch_and_hash_sources", _fake_sources)
    monkeypatch.setattr(module, "_call_llm", _fake_llm)
    monkeypatch.setenv("CHUTES_API_KEY", "test-key")


def test_probe_run_signs_output_with_loaded_hotkey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hotkey_file: Path, alice_ss58: str
) -> None:
    module = _load_server(
        probe_mode=True, hotkey_path=hotkey_file, tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    _patch_runtime_io(module, monkeypatch)

    payload = {
        "submission_id": "sub-1",
        "card_id": "eu-ai-act",
        "bundle_bytes_b64": base64.b64encode(_make_minimal_bundle()).decode(),
        "prompts": [{"template_id": "t1", "text": "produce the card"}],
    }
    with TestClient(module.app) as client:
        resp = client.post("/probe/run", json=payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()

    assert body["submission_id"] == "sub-1"
    assert body["card_id"] == "eu-ai-act"
    assert body["miner_signature"]
    assert len(body["sources_fetched"]) == 1
    assert len(body["traces"]) == 1
    trace = body["traces"][0]
    assert trace["prompt"]["template_id"] == "t1"
    assert trace["prompt"]["prompt_token_count"] == 100
    assert trace["prompt"]["response_token_count"] == 200
    assert trace["card_render"]["input_prompt_length"] == len("produce the card")
    assert trace["card_render"]["output_json_size"] > 0

    sig_payload = {k: v for k, v in body.items() if k != "miner_signature"}
    canonical = json.dumps(sig_payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    sig_bytes = base64.b64decode(body["miner_signature"])
    assert len(sig_bytes) == 64
    verifier = Keypair(ss58_address=alice_ss58)
    assert verifier.verify(canonical, sig_bytes), "sr25519 verify must succeed"


def test_probe_run_signature_does_not_verify_under_wrong_hotkey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hotkey_file: Path
) -> None:
    module = _load_server(
        probe_mode=True, hotkey_path=hotkey_file, tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    _patch_runtime_io(module, monkeypatch)
    payload = {
        "submission_id": "sub-2",
        "card_id": "eu-ai-act",
        "bundle_bytes_b64": base64.b64encode(_make_minimal_bundle()).decode(),
        "prompts": [{"template_id": "t1", "text": "x"}],
    }
    with TestClient(module.app) as client:
        body = client.post("/probe/run", json=payload).json()

    sig_payload = {k: v for k, v in body.items() if k != "miner_signature"}
    canonical = json.dumps(sig_payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    sig_bytes = base64.b64decode(body["miner_signature"])
    bob = Keypair.create_from_uri("//Bob")
    bob_verifier = Keypair(ss58_address=bob.ss58_address)
    assert not bob_verifier.verify(canonical, sig_bytes)


# --------------------------------------------------------------------------
# /probe/reload
# --------------------------------------------------------------------------


def test_probe_reload_clears_bundle_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hotkey_file: Path
) -> None:
    module = _load_server(
        probe_mode=True, hotkey_path=hotkey_file, tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    module.probe_state.bundle_cache = b"stale bundle bytes"
    module.probe_state.bundle_cache_url = "https://example.com/old.zip"
    with TestClient(module.app) as client:
        resp = client.post("/probe/reload", json={})
        assert resp.status_code == 200
        assert resp.json() == {"reloaded": True}
    assert module.probe_state.bundle_cache is None
    assert module.probe_state.bundle_cache_url is None


def test_probe_reload_swaps_bundle_when_bytes_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hotkey_file: Path
) -> None:
    module = _load_server(
        probe_mode=True, hotkey_path=hotkey_file, tmp_path=tmp_path, monkeypatch=monkeypatch
    )
    new_bytes = b"new bundle bytes"
    with TestClient(module.app) as client:
        resp = client.post(
            "/probe/reload",
            json={"bundle_bytes_b64": base64.b64encode(new_bytes).decode()},
        )
        assert resp.status_code == 200
    assert module.probe_state.bundle_cache == new_bytes


# --------------------------------------------------------------------------
# Local helpers
# --------------------------------------------------------------------------


def _make_minimal_bundle() -> bytes:
    """Smallest valid zip with a soul.md inside — matches what the
    extractor expects."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("soul.md", "# Soul\nI am a regulatory analyst agent.\n")
    return buf.getvalue()
