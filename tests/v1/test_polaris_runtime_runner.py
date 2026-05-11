"""Unit tests for `PolarisRuntimeRunner` — Tier A (Polaris-hosted miners).

The runner deploys a miner's bundle to Polaris's marketplace
runtime-evaluate endpoint, blocks for the response, then verifies the
Ed25519 attestation that Polaris signs over the eval result. These
tests pin the contract:

- valid attestation -> returns the Card dict
- task_hash mismatch -> PolarisAttestationError
- output_hash mismatch -> PolarisAttestationError
- bad signature -> PolarisAttestationError
- wrong attestation public key -> PolarisAttestationError
- missing attestation -> PolarisAttestationError
- HTTP 4xx / 5xx -> PolarisRunnerError
- malformed JSON output -> PolarisRunnerError
- non-dict output -> PolarisRunnerError
- presigned bundle URL is forwarded to Polaris in `env_overrides`
- `submission_id` is echoed onto the request path
"""

from __future__ import annotations

import base64
import importlib.util as _ilu
import json
import sys
from pathlib import Path
from typing import Any

import blake3
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cathedral.v1_types import EvalTask

# Same importlib trick used by test_bundle_card_runner.py:
# `cathedral.eval.__init__` triggers a circular import via the publisher
# bootstrap, but `polaris_runner.py` is cycle-free, so we load it directly.
_PR_PATH = Path(__file__).parent.parent.parent / "src/cathedral/eval/polaris_runner.py"
_spec = _ilu.spec_from_file_location("_polaris_runner_for_runtime_test", _PR_PATH)
assert _spec and _spec.loader
_pr = _ilu.module_from_spec(_spec)
sys.modules["_polaris_runner_for_runtime_test"] = _pr
_spec.loader.exec_module(_pr)
PolarisRuntimeRunner = _pr.PolarisRuntimeRunner
PolarisRuntimeRunnerConfig = _pr.PolarisRuntimeRunnerConfig
PolarisRunnerError = _pr.PolarisRunnerError
PolarisAttestationError = _pr.PolarisAttestationError


SUBMISSION_ID = "sub_cathedral_runtime_v1"
PRESIGNED_BUNDLE_URL = "https://r2.example.invalid/bundles/abc123?sig=...&exp=..."
DEPLOYMENT_ID = "dep_abc123"


# --------------------------------------------------------------------------
# Test helpers
# --------------------------------------------------------------------------


def _make_task(prompt: str = "summarise EU AI Act developments in the last 24h") -> EvalTask:
    return EvalTask(
        card_id="eu-ai-act",
        epoch=42,
        round_index=3,
        prompt=prompt,
        sources=[],
        deadline_minutes=25,
    )


def _expected_task_id(task: EvalTask) -> str:
    return f"cathedral-{task.card_id}-e{task.epoch}r{task.round_index}"


def _valid_card_json() -> str:
    return json.dumps(
        {
            "jurisdiction": "eu",
            "topic": "EU AI Act",
            "title": "EU AI Act ramp continues",
            "summary": "Real LLM-generated card body, not a stub.",
            "what_changed": "GPAI obligations live since 2025-08-02.",
            "why_it_matters": "Providers face up to 3% turnover fines.",
            "action_notes": "Map deployments to Annex III categories.",
            "risks": "Penalties phase in alongside obligations.",
            "citations": [
                {
                    "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
                    "class": "official_journal",
                    "fetched_at": "2026-05-10T10:00:00.000Z",
                    "status": 200,
                    "content_hash": "a" * 64,
                }
            ],
            "confidence": 0.72,
            "no_legal_advice": True,
            "last_refreshed_at": "2026-05-10T10:00:00.000Z",
            "refresh_cadence_hours": 24,
        }
    )


def _build_attestation_payload(
    *,
    task: EvalTask,
    output_bytes: bytes,
    submission_id: str = SUBMISSION_ID,
    deployment_id: str = DEPLOYMENT_ID,
) -> dict[str, Any]:
    return {
        "submission_id": submission_id,
        "task_id": _expected_task_id(task),
        "task_hash": blake3.blake3(task.prompt.encode("utf-8")).hexdigest(),
        "output_hash": blake3.blake3(output_bytes).hexdigest(),
        "deployment_id": deployment_id,
        "completed_at": "2026-05-10T10:01:23.456Z",
    }


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class _Keypair:
    def __init__(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        pub = self.private_key.public_key()
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

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


# Pin the real `httpx.AsyncClient` at import time so subsequent test
# monkeypatching can be reliably reversed. We rebind the symbol on the
# `_pr.httpx` namespace (which is the imported httpx module), so the
# patched factory must be reset to THIS reference and not to whatever
# `httpx.AsyncClient` currently points at (which is the patch itself
# after `_make_runner`).
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _make_runner(
    *,
    public_key_hex: str,
    transport: httpx.MockTransport,
    submission_id: str = SUBMISSION_ID,
    bundle_kek: str = "",
) -> tuple[Any, _FixedUrlResolver]:
    """Wrap the real runner with a mock transport so no real HTTP is issued."""
    resolver = _FixedUrlResolver()
    config = PolarisRuntimeRunnerConfig(
        base_url="https://api.polaris.computer",
        api_token="test-token-xyz",
        submission_id=submission_id,
        attestation_public_key_hex=public_key_hex,
        bundle_url_resolver=resolver,
        bundle_encryption_key_hex=bundle_kek,
    )
    runner = PolarisRuntimeRunner(config)

    # Monkey-patch `httpx.AsyncClient` to route through the mock transport.
    # We can't do this via dependency injection cleanly without changing the
    # runner's public API, so the test substitutes the AsyncClient factory.
    def _patched_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(*args, transport=transport, **kwargs)

    _pr.httpx.AsyncClient = _patched_client  # type: ignore[attr-defined]
    return runner, resolver


def _restore_httpx() -> None:
    _pr.httpx.AsyncClient = _REAL_ASYNC_CLIENT  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_card_and_attestation() -> None:
    """Valid attestation + valid output -> runner returns parsed Card."""
    kp = _Keypair()
    task = _make_task()
    card_json = _valid_card_json()
    output_bytes = card_json.encode("utf-8")
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)
    signature = kp.sign(payload)

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "output_json": json.loads(card_json),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": signature,
                    "public_key": kp.public_hex,
                },
                "duration_ms": 1234,
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, resolver = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        result = await runner.run(
            bundle_bytes=b"unused-polaris-fetches-via-presigned-url",
            bundle_hash="deadbeef" * 8,
            task=task,
            miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        )
    finally:
        _restore_httpx()

    # Card came through cleanly.
    assert result.output_card_json["jurisdiction"] == "eu"
    assert result.output_card_json["title"] == "EU AI Act ramp continues"
    # Attestation is attached for downstream persistence.
    assert result.attestation is not None
    assert result.attestation.version == "polaris-v1"
    assert result.attestation.signature == signature
    assert result.attestation.public_key == kp.public_hex
    # Polaris-reported duration is preferred over wall-clock.
    assert result.duration_ms == 1234
    # polaris_agent_id encodes the deployment so the scoring pipeline
    # treats it as Polaris-verified (non-empty agent id).
    assert result.polaris_agent_id == f"polaris-runtime:{DEPLOYMENT_ID}"
    assert result.polaris_run_id == _expected_task_id(task)
    assert result.errors == []

    # The request hit the right path + body.
    assert (
        captured["url"] == f"https://api.polaris.computer/api/marketplace/submissions/"
        f"{SUBMISSION_ID}/runtime-evaluate"
    )
    assert captured["headers"]["authorization"] == "Bearer test-token-xyz"
    body = captured["body"]
    assert body["task"] == task.prompt
    assert body["task_id"] == _expected_task_id(task)
    assert body["timeout_seconds"] == 600
    assert body["env_overrides"]["CARD_ID"] == "eu-ai-act"
    assert body["env_overrides"]["MINER_BUNDLE_URL"] == PRESIGNED_BUNDLE_URL
    # Resolver received the synthetic submission shim built from the task.
    assert resolver.calls == [
        {
            "bundle_hash": "deadbeef" * 8,
            "card_id": "eu-ai-act",
            "epoch": 42,
            "round_index": 3,
        }
    ]


@pytest.mark.asyncio
async def test_bundle_kek_propagates_when_configured() -> None:
    """When `bundle_encryption_key_hex` is set, it ships via env_overrides."""
    kp = _Keypair()
    task = _make_task()
    output_bytes = _valid_card_json().encode("utf-8")
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": kp.sign(payload),
                    "public_key": kp.public_hex,
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(
        public_key_hex=kp.public_hex,
        transport=transport,
        bundle_kek="aa" * 32,
    )
    try:
        await runner.run(
            bundle_bytes=b"",
            bundle_hash="00" * 32,
            task=task,
            miner_hotkey="hk",
        )
    finally:
        _restore_httpx()

    assert captured["body"]["env_overrides"]["CATHEDRAL_BUNDLE_KEK"] == "aa" * 32


# --------------------------------------------------------------------------
# Attestation failure modes
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_hash_mismatch_raises_attestation_error() -> None:
    """Attestation payload claims a task_hash that doesn't match the prompt."""
    kp = _Keypair()
    task = _make_task()
    output_bytes = _valid_card_json().encode("utf-8")
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)
    payload["task_hash"] = "0" * 64  # tampered
    signature = kp.sign(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": signature,
                    "public_key": kp.public_hex,
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisAttestationError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "task_hash mismatch" in str(exc.value)


@pytest.mark.asyncio
async def test_output_hash_mismatch_raises_attestation_error() -> None:
    """Attestation output_hash doesn't match the base64-decoded output bytes."""
    kp = _Keypair()
    task = _make_task()
    output_bytes = _valid_card_json().encode("utf-8")
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)
    payload["output_hash"] = "f" * 64  # tampered
    signature = kp.sign(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": signature,
                    "public_key": kp.public_hex,
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisAttestationError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "output_hash mismatch" in str(exc.value)


@pytest.mark.asyncio
async def test_bad_signature_raises_attestation_error() -> None:
    """Attestation payload was signed by a different key."""
    real_kp = _Keypair()
    attacker_kp = _Keypair()  # signs the payload — verifier rejects.
    task = _make_task()
    output_bytes = _valid_card_json().encode("utf-8")
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)
    signature = attacker_kp.sign(payload)  # wrong signer

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": signature,
                    "public_key": real_kp.public_hex,  # advertises real key
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=real_kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisAttestationError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "signature does not verify" in str(exc.value)


@pytest.mark.asyncio
async def test_wrong_advertised_public_key_raises_attestation_error() -> None:
    """Response advertises a different public_key than the configured one."""
    real_kp = _Keypair()
    attacker_kp = _Keypair()
    task = _make_task()
    output_bytes = _valid_card_json().encode("utf-8")
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": attacker_kp.sign(payload),
                    "public_key": attacker_kp.public_hex,  # not the configured key
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=real_kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisAttestationError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "public_key does not match" in str(exc.value)


@pytest.mark.asyncio
async def test_missing_attestation_raises() -> None:
    """200 response with no attestation field is rejected."""
    kp = _Keypair()
    task = _make_task()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "output": base64.b64encode(b"{}").decode("ascii"),
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisAttestationError):
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()


@pytest.mark.asyncio
async def test_attestation_missing_payload_field_raises() -> None:
    """Attestation payload missing a required key (e.g. `task_id`)."""
    kp = _Keypair()
    task = _make_task()
    output_bytes = _valid_card_json().encode("utf-8")
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)
    del payload["task_id"]
    signature = kp.sign(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": signature,
                    "public_key": kp.public_hex,
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisAttestationError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "missing fields" in str(exc.value)


@pytest.mark.asyncio
async def test_submission_id_mismatch_raises() -> None:
    """Attestation submission_id differs from the runtime image we registered."""
    kp = _Keypair()
    task = _make_task()
    output_bytes = _valid_card_json().encode("utf-8")
    payload = _build_attestation_payload(
        task=task, output_bytes=output_bytes, submission_id="sub_someone_else"
    )
    signature = kp.sign(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": "sub_someone_else",
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": signature,
                    "public_key": kp.public_hex,
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisAttestationError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "submission_id mismatch" in str(exc.value)


# --------------------------------------------------------------------------
# HTTP failure paths
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_500_raises_runner_error() -> None:
    """Polaris returns 5xx -> retryable PolarisRunnerError."""
    kp = _Keypair()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service degraded")

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=_make_task(),
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "5xx" in str(exc.value)
    assert "503" in str(exc.value)


@pytest.mark.asyncio
async def test_http_4xx_raises_runner_error() -> None:
    """Polaris returns 4xx -> PolarisRunnerError surfaces as retryable failure."""
    kp = _Keypair()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=_make_task(),
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "4xx" in str(exc.value)


@pytest.mark.asyncio
async def test_malformed_output_bytes_raise_runner_error() -> None:
    """Attestation verifies but output bytes aren't JSON -> PolarisRunnerError."""
    kp = _Keypair()
    task = _make_task()
    # Bad-but-real-utf8 bytes; output_hash is still consistent with what was signed.
    output_bytes = b"this is not json at all"
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)
    signature = kp.sign(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                # Intentionally omit output_json so the runner has to parse `output`.
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": signature,
                    "public_key": kp.public_hex,
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "valid JSON" in str(exc.value)


@pytest.mark.asyncio
async def test_non_object_output_raises_runner_error() -> None:
    """Output decodes as JSON but isn't a dict -> PolarisRunnerError."""
    kp = _Keypair()
    task = _make_task()
    output_bytes = b'["not", "an", "object"]'
    payload = _build_attestation_payload(task=task, output_bytes=output_bytes)
    signature = kp.sign(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": _expected_task_id(task),
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "output": base64.b64encode(output_bytes).decode("ascii"),
                "attestation": {
                    "version": "polaris-v1",
                    "payload": payload,
                    "signature": signature,
                    "public_key": kp.public_hex,
                },
                "status": "succeeded",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=task,
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "JSON object" in str(exc.value)


@pytest.mark.asyncio
async def test_status_failed_raises_runner_error() -> None:
    """Polaris returns status=failed -> PolarisRunnerError, no attestation check."""
    kp = _Keypair()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": "tid",
                "deployment_id": DEPLOYMENT_ID,
                "submission_id": SUBMISSION_ID,
                "status": "failed",
                "error": "anthropic api rate-limited",
            },
        )

    transport = httpx.MockTransport(handler)
    runner, _ = _make_runner(public_key_hex=kp.public_hex, transport=transport)
    try:
        with pytest.raises(PolarisRunnerError) as exc:
            await runner.run(
                bundle_bytes=b"",
                bundle_hash="00" * 32,
                task=_make_task(),
                miner_hotkey="hk",
            )
    finally:
        _restore_httpx()
    assert "failed" in str(exc.value)


# --------------------------------------------------------------------------
# Construction guards
# --------------------------------------------------------------------------


def test_bad_attestation_public_key_hex_raises() -> None:
    """Non-hex key string is rejected at construction time."""
    with pytest.raises(PolarisRunnerError) as exc:
        PolarisRuntimeRunner(
            PolarisRuntimeRunnerConfig(
                base_url="https://api.polaris.computer",
                api_token="t",
                submission_id=SUBMISSION_ID,
                attestation_public_key_hex="zz" * 32,
                bundle_url_resolver=_FixedUrlResolver(),
            )
        )
    assert "hex" in str(exc.value).lower()


def test_attestation_public_key_wrong_length_raises() -> None:
    """Hex key of the wrong byte length is rejected at construction time."""
    with pytest.raises(PolarisRunnerError) as exc:
        PolarisRuntimeRunner(
            PolarisRuntimeRunnerConfig(
                base_url="https://api.polaris.computer",
                api_token="t",
                submission_id=SUBMISSION_ID,
                attestation_public_key_hex="aa" * 16,  # 16 bytes — too short
                bundle_url_resolver=_FixedUrlResolver(),
            )
        )
    assert "32 bytes" in str(exc.value)
