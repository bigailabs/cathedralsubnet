"""Unit tests for `PolarisDeployRunner` — Cathedral v2 Polaris-native flow.

The v2 runner calls Polaris's `/api/cathedral/v1/deploy` to spin up a real
Hermes agent, drives `/chat` to capture the Card + structured trace, then
pulls and verifies the signed manifest. These tests pin the contract:

- happy path -> Card + trace come through, manifest signature verifies
- chat stream missing final event -> PolarisRunnerError
- manifest signature mismatch -> PolarisAttestationError
- deploy 5xx -> PolarisRunnerError
- deploy 4xx -> PolarisRunnerError
- presigned bundle URL is forwarded to Polaris in the deploy body
"""

from __future__ import annotations

import base64
import importlib.util as _ilu
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from cathedral.v1_types import EvalTask

# Same importlib trick used elsewhere in the v1 test suite — avoids the
# `cathedral.eval.__init__` -> publisher bootstrap cycle that bites when
# running these tests against an env without the full publisher stack.
# First, load polaris_runner directly (it owns the canonical error classes
# and PolarisRunResult). The deploy_runner imports these lazily inside
# run() so the error / result types must be reachable via the regular
# package path — which works because polaris_runner.py itself doesn't
# trigger the publisher cycle.
_PR_PATH = Path(__file__).parent.parent.parent / "src/cathedral/eval/polaris_runner.py"
_pr_spec = _ilu.spec_from_file_location("_polaris_runner_for_deploy_test", _PR_PATH)
assert _pr_spec and _pr_spec.loader
_pr = _ilu.module_from_spec(_pr_spec)
sys.modules["_polaris_runner_for_deploy_test"] = _pr
# Also register under the cathedral.eval namespace so the lazy imports
# inside polaris_deploy_runner resolve through the same module
# instance (otherwise we'd have two PolarisRunnerError classes and
# `isinstance` checks would silently mis-fire).
sys.modules.setdefault("cathedral.eval.polaris_runner", _pr)
_pr_spec.loader.exec_module(_pr)
sys.modules["cathedral.eval.polaris_runner"] = _pr

_PDR_PATH = Path(__file__).parent.parent.parent / "src/cathedral/eval/polaris_deploy_runner.py"
_spec = _ilu.spec_from_file_location("_polaris_deploy_runner_for_test", _PDR_PATH)
assert _spec and _spec.loader
_pdr = _ilu.module_from_spec(_spec)
sys.modules["_polaris_deploy_runner_for_test"] = _pdr
_spec.loader.exec_module(_pdr)
PolarisDeployRunner = _pdr.PolarisDeployRunner
PolarisDeployRunnerConfig = _pdr.PolarisDeployRunnerConfig
PolarisRunnerError = _pr.PolarisRunnerError
PolarisAttestationError = _pr.PolarisAttestationError


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


PRESIGNED_BUNDLE_URL = "https://r2.example.invalid/bundles/abc123?sig=...&exp=..."
DEPLOYMENT_ID = "dep_v2_abc123"
ACCESS_URL = "https://agt_v2_xyz.up.railway.app"
CHAT_ENDPOINT = ACCESS_URL + "/chat"
MANIFEST_BASE = {
    "polaris_agent_id": DEPLOYMENT_ID,
    "owner_wallet": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
    "created_at": "2026-05-11T10:00:00Z",
    "schema": "polaris.manifest.v1",
    "runtime_image": "ghcr.io/bigailabs/polaris-hermes:latest",
    "runtime_mode": "hermes_agentic",
}


def _make_task(prompt: str = "summarise EU AI Act developments in the last 24h") -> EvalTask:
    return EvalTask(
        card_id="eu-ai-act",
        epoch=42,
        round_index=3,
        prompt=prompt,
        sources=[],
        deadline_minutes=25,
    )


def _valid_card_dict() -> dict[str, Any]:
    return {
        "jurisdiction": "eu",
        "topic": "EU AI Act",
        "title": "EU AI Act ramp continues",
        "summary": "Real Hermes-generated card body via v2 flow.",
        "what_changed": "GPAI obligations remain in force.",
        "why_it_matters": "Providers face up to 3% turnover fines.",
        "action_notes": "Map deployments to Annex III categories.",
        "risks": "Penalties phase in alongside obligations.",
        "citations": [
            {
                "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
                "class": "official_journal",
                "fetched_at": "2026-05-11T10:00:00.000Z",
                "status": 200,
                "content_hash": "a" * 64,
            }
        ],
        "confidence": 0.74,
        "no_legal_advice": True,
        "last_refreshed_at": "2026-05-11T10:00:00.000Z",
        "refresh_cadence_hours": 24,
    }


def _canonical(payload: dict[str, Any]) -> bytes:
    """Match `polaris.services.cathedral_signing.canonical_json_for_signing`.

    Sorted keys, no whitespace, UTF-8, with `default=str` so datetimes
    serialize through Python's repr (Polaris uses `model_dump(mode="json")`
    which yields strings already; `default=str` is harmless when the
    input is already JSON-compatible).
    """
    body = {k: v for k, v in payload.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


class _Keypair:
    def __init__(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        pub = self.private_key.public_key()
        self.public_hex = pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    def sign(self, payload: dict[str, Any]) -> str:
        return base64.b64encode(self.private_key.sign(_canonical(payload))).decode("ascii")


class _FixedUrlResolver:
    def __init__(self, url: str = PRESIGNED_BUNDLE_URL) -> None:
        self.url = url
        self.calls: list[dict[str, Any]] = []

    def url_for(self, submission: dict[str, Any]) -> str:
        self.calls.append(submission)
        return self.url


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _make_runner(
    *,
    public_key_hex: str,
    transport: httpx.MockTransport,
) -> tuple[PolarisDeployRunner, _FixedUrlResolver]:
    resolver = _FixedUrlResolver()
    config = PolarisDeployRunnerConfig(
        base_url="https://api.polaris.computer",
        api_token="test-token-xyz",
        bundle_url_resolver=resolver,
        attestation_public_key_hex=public_key_hex,
        bundle_encryption_key_hex="0" * 64,
        ttl_minutes=30,
    )
    runner = PolarisDeployRunner(config)

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(*args, transport=transport, **kwargs)

    _pdr.httpx.AsyncClient = _patched_client  # type: ignore[attr-defined]
    return runner, resolver


def _restore_httpx() -> None:
    _pdr.httpx.AsyncClient = _REAL_ASYNC_CLIENT  # type: ignore[attr-defined]


def _build_manifest_response(keypair: _Keypair) -> dict[str, Any]:
    manifest = dict(MANIFEST_BASE)
    manifest["public_key"] = keypair.public_hex
    manifest["signature"] = keypair.sign(manifest)
    return manifest


def _build_chat_ndjson(card: dict[str, Any]) -> bytes:
    """Encode the Hermes NDJSON stream for a single happy chat run."""
    trace = {
        "tool_calls": [
            {
                "name": "fetch_url",
                "args_schema": {"url": "str"},
                "result_preview": '{"status":200}',
                "duration_ms": 142,
                "error": False,
            }
        ],
        "model_calls": [
            {
                "model": "hermes-agentic",
                "prompt_tokens": 512,
                "completion_tokens": 256,
                "cached_tokens": 0,
                "duration_ms": 870,
            }
        ],
        "source_fetches": [],
        "agentic_loop_depth": 2,
        "start_at": "2026-05-11T10:00:00Z",
        "end_at": "2026-05-11T10:00:05Z",
    }
    progress = {
        "type": "tool_calls",
        "step": 1,
        "calls": [{"name": "fetch_url", "id": "tc1"}],
    }
    final = {
        "type": "final",
        "content": json.dumps(card),
        "session_id": "sess_abc",
        "ttft_ms": 200,
        "ttc_ms": 1500,
        "steps": 2,
        "cache": {"cached_tokens": 0, "prompt_tokens": 512},
        "card_json": card,
        "trace": trace,
    }
    terminal = {"type": "trace", "trace": trace}
    return (
        json.dumps(progress).encode()
        + b"\n"
        + json.dumps(final).encode()
        + b"\n"
        + json.dumps(terminal).encode()
        + b"\n"
    )


def _build_deploy_response() -> dict[str, Any]:
    return {
        "submission_id": "sub_polaris_xyz",
        "deployment_id": DEPLOYMENT_ID,
        "access_url": ACCESS_URL,
        "chat_endpoint": CHAT_ENDPOINT,
        "status": "running",
        "expires_at": "2026-05-11T10:30:00Z",
    }


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_card_trace_and_manifest() -> None:
    kp = _Keypair()
    task = _make_task()
    card = _valid_card_dict()
    captured: dict[str, Any] = {"deploy_body": None}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/cathedral/v1/deploy"):
            captured["deploy_body"] = json.loads(request.content)
            captured["deploy_auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_build_deploy_response())
        if url == CHAT_ENDPOINT:
            return httpx.Response(
                200,
                content=_build_chat_ndjson(card),
                headers={"content-type": "application/x-ndjson"},
            )
        if url.endswith(f"/api/cathedral/v1/agents/{DEPLOYMENT_ID}/manifest"):
            return httpx.Response(200, json=_build_manifest_response(kp))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    runner, resolver = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        result = await runner.run(
            bundle_bytes=b"unused-polaris-fetches-via-presigned-url",
            bundle_hash="deadbeef" * 8,
            task=task,
            miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
            submission={
                "id": "sub_test",
                "encryption_key_id": (
                    "kms-local:"
                    "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU2Nzg5MTIzNDU2:"
                    "MTIzNDU2Nzg5MDEy"
                ),
                "card_id": task.card_id,
                "epoch": task.epoch,
                "round_index": task.round_index,
            },
        )
    finally:
        _restore_httpx()

    assert result.output_card_json["jurisdiction"] == "eu"
    assert result.polaris_agent_id == f"polaris-deploy:{DEPLOYMENT_ID}"
    assert result.trace is not None
    assert len(result.trace["tool_calls"]) == 1
    assert result.trace["tool_calls"][0]["name"] == "fetch_url"
    assert len(result.trace["model_calls"]) == 1
    assert result.trace["agentic_loop_depth"] == 2
    # Manifest came back verified.
    assert result.manifest is not None
    assert result.manifest["polaris_agent_id"] == DEPLOYMENT_ID

    # Resolver got the submission.
    assert resolver.calls and resolver.calls[0]["card_id"] == "eu-ai-act"
    # Deploy body carried the right fields.
    body = captured["deploy_body"]
    assert body["bundle_url"] == PRESIGNED_BUNDLE_URL
    assert body["bundle_kek_hex"] == "0" * 64
    assert body["miner_hotkey"].startswith("5Grwva")
    assert body["card_id"] == "eu-ai-act"
    assert body["ttl_minutes"] == 30
    assert captured["deploy_auth"] == "Bearer test-token-xyz"


# --------------------------------------------------------------------------
# Failure paths
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_500_surfaces_runner_error() -> None:
    kp = _Keypair()
    task = _make_task()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/api/cathedral/v1/deploy"):
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError, match="deploy 5xx"):
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="d" * 64,
                task=task,
                miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                submission={
                    "id": "sub_test",
                    "encryption_key_id": "kms-local:YWJj:MTIz",
                },
            )
    finally:
        _restore_httpx()


@pytest.mark.asyncio
async def test_deploy_4xx_surfaces_runner_error() -> None:
    kp = _Keypair()
    task = _make_task()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/api/cathedral/v1/deploy"):
            return httpx.Response(409, json={"detail": "conflict"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError, match="deploy 4xx"):
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="d" * 64,
                task=task,
                miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                submission={
                    "id": "sub_test",
                    "encryption_key_id": "kms-local:YWJj:MTIz",
                },
            )
    finally:
        _restore_httpx()


@pytest.mark.asyncio
async def test_chat_missing_final_event_fails() -> None:
    kp = _Keypair()
    task = _make_task()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/cathedral/v1/deploy"):
            return httpx.Response(200, json=_build_deploy_response())
        if url == CHAT_ENDPOINT:
            # Only a progress event, no final.
            return httpx.Response(
                200,
                content=json.dumps({"type": "tool_calls", "step": 1, "calls": []}).encode() + b"\n",
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError, match="without a final event"):
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="d" * 64,
                task=task,
                miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                submission={
                    "id": "sub_test",
                    "encryption_key_id": "kms-local:YWJj:MTIz",
                },
            )
    finally:
        _restore_httpx()


@pytest.mark.asyncio
async def test_manifest_signature_mismatch_fails() -> None:
    kp_signer = _Keypair()
    kp_attacker = _Keypair()  # Different key — signs an evil manifest
    task = _make_task()
    card = _valid_card_dict()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/cathedral/v1/deploy"):
            return httpx.Response(200, json=_build_deploy_response())
        if url == CHAT_ENDPOINT:
            return httpx.Response(200, content=_build_chat_ndjson(card))
        if url.endswith(f"/api/cathedral/v1/agents/{DEPLOYMENT_ID}/manifest"):
            # Sign with attacker key, advertise the legitimate key —
            # the runner pins against the config'd public key so this
            # is a contract violation.
            evil_manifest = dict(MANIFEST_BASE)
            evil_manifest["public_key"] = kp_signer.public_hex
            evil_manifest["signature"] = kp_attacker.sign(evil_manifest)
            return httpx.Response(200, json=evil_manifest)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp_signer.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisAttestationError, match="signature does not verify"):
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="d" * 64,
                task=task,
                miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                submission={
                    "id": "sub_test",
                    "encryption_key_id": "kms-local:YWJj:MTIz",
                },
            )
    finally:
        _restore_httpx()


@pytest.mark.asyncio
async def test_missing_encryption_key_id_fails_fast() -> None:
    kp = _Keypair()
    task = _make_task()

    # Transport should never be hit — we fail at submission validation.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "should not be reached"})

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError, match="encryption_key_id"):
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="d" * 64,
                task=task,
                miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                submission={"id": "sub_test"},  # no encryption_key_id
            )
    finally:
        _restore_httpx()
