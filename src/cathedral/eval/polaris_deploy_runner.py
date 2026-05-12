"""Cathedral v2 — Polaris-native Hermes deploy runner.

Replaces `PolarisRuntimeRunner` (which used the `cathedral-runtime`
LLM-shim). For each eval round, this runner:

  1. Resolves a presigned R2 URL for the miner's encrypted bundle.
  2. Calls `POST /api/cathedral/v1/deploy` on Polaris — Polaris fetches
     and decrypts the bundle inside a real Hermes container.
  3. POSTs the eval task to the agent's `/chat` endpoint, captures the
     Card JSON + structured trace from the streamed NDJSON response.
  4. Pulls the signed manifest from `/api/cathedral/v1/agents/{id}/manifest`
     and Ed25519-verifies it against the pinned attestation public key.
  5. Returns a `PolarisRunResult` with output_card, trace, and the
     verified manifest.

The runner is deliberately thin — Polaris owns the deploy lifecycle
(provision, healthcheck, 30-min TTL teardown). Cathedral's only job is
to drive the agent loop and persist the result.

Contract reference: `cathedral-redesign/POLARIS_NATIVE_V2.md`.

Cycle note: this module is loaded BOTH via the standard package path
(production wiring through the orchestrator) and via direct file load
in unit tests (mirroring `test_polaris_runtime_runner.py`). The
package-path load eagerly evaluates `cathedral.eval.__init__`, which
pulls the publisher app into the import graph. Direct loads skip that.
To make both work, the module imports `PolarisRunnerError` /
`PolarisAttestationError` / `PolarisRunResult` LAZILY inside `run`,
not at module scope.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cathedral.v1_types import EvalTask

logger = structlog.get_logger(__name__)


class BundleUrlResolver(Protocol):
    """Local mirror of `cathedral.eval.polaris_runner.BundleUrlResolver`.

    Duplicated to keep this module cycle-free at import time. Production
    wiring passes a `HippiusPresignedUrlResolver` instance (defined in
    `polaris_runner.py`) which satisfies both Protocol shapes by virtue
    of `url_for(submission)` returning a string.
    """

    def url_for(self, submission: dict[str, Any]) -> str: ...


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PolarisDeployRunnerConfig:
    """Static config built from env at orchestrator startup.

    Mirrors `PolarisRuntimeRunnerConfig` plus a couple of v2-only knobs
    (TTL minutes, chat timeout). Nothing here knows about cathedral-
    runtime image names — that path is going away.

    `bundle_url_resolver` is pluggable so tests inject a deterministic
    URL without hitting R2 / Hippius. In production it's
    `HippiusPresignedUrlResolver` from `cathedral.eval.polaris_runner`.
    """

    base_url: str
    api_token: str
    bundle_url_resolver: BundleUrlResolver
    attestation_public_key_hex: str
    bundle_encryption_key_hex: str
    ttl_minutes: int = 30
    deploy_timeout_secs: float = 600.0  # Polaris blocks until RUNNING (~8m cap)
    chat_timeout_secs: float = 600.0  # Hermes loop can take minutes
    manifest_timeout_secs: float = 30.0
    # When True, Cathedral pins its own Chutes key for this miner via
    # the deploy request body. When False, Hermes uses whatever
    # CHUTES_API_KEY is in the host vault for the Polaris user that
    # owns the deployment. v2 default: False. Operators flip this when
    # the Polaris-side vault isn't configured.
    pin_chutes_key: bool = False
    chutes_api_key: str = ""


# --------------------------------------------------------------------------
# Re-import shim
# --------------------------------------------------------------------------


def _err_classes() -> tuple[type, type]:
    """Lazy-load `PolarisRunnerError` and `PolarisAttestationError`.

    Doing this at module import time would re-enter
    `cathedral.eval.__init__` and break direct-file-path test loads.
    Resolving inside `run` is cheap — Python caches the import.
    """
    from cathedral.eval.polaris_runner import (
        PolarisAttestationError,
        PolarisRunnerError,
    )

    return PolarisRunnerError, PolarisAttestationError


def _result_cls() -> type:
    from cathedral.eval.polaris_runner import PolarisRunResult

    return PolarisRunResult


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


class PolarisDeployRunner:
    """v2 runner — Polaris deploys a real Hermes agent, Cathedral drives /chat.

    Failure modes (all surface as `PolarisRunnerError` / subclasses so
    the orchestrator's existing retry path applies uniformly):

      - HTTP transport / 5xx       -> PolarisRunnerError
      - 4xx                        -> PolarisRunnerError (permanent-ish)
      - chat returned no Card JSON -> PolarisRunnerError
      - manifest signature invalid -> PolarisAttestationError
      - manifest wallet mismatch   -> PolarisAttestationError
    """

    def __init__(self, config: PolarisDeployRunnerConfig) -> None:
        self.config = config
        PolarisRunnerError, _ = _err_classes()  # noqa: N806
        try:
            raw = bytes.fromhex(config.attestation_public_key_hex.strip())
        except ValueError as e:
            raise PolarisRunnerError("POLARIS_ATTESTATION_PUBLIC_KEY must be hex-encoded") from e
        if len(raw) != 32:
            raise PolarisRunnerError(
                f"POLARIS_ATTESTATION_PUBLIC_KEY must be 32 bytes, got {len(raw)}"
            )
        self._verify_key = Ed25519PublicKey.from_public_bytes(raw)

    async def run(
        self,
        *,
        bundle_bytes: bytes,
        bundle_hash: str,
        task: EvalTask,
        miner_hotkey: str,
        submission: dict[str, Any] | None = None,
    ) -> Any:  # PolarisRunResult, but lazy-imported below
        del bundle_bytes  # Polaris fetches the encrypted bundle via URL.

        PolarisRunnerError, _ = _err_classes()  # noqa: N806
        PolarisRunResult = _result_cls()  # noqa: N806

        if submission is None:
            # Tests can call us without a real DB row by passing a None
            # submission; we fabricate a minimal shim with the fields
            # the resolver actually needs.
            submission = {
                "id": f"sub-{task.card_id}-e{task.epoch}r{task.round_index}",
                "bundle_hash": bundle_hash,
                "card_id": task.card_id,
                "epoch": task.epoch,
                "round_index": task.round_index,
            }

        bundle_url = self.config.bundle_url_resolver.url_for(submission)
        encryption_key_id = str(submission.get("encryption_key_id") or "")
        if not encryption_key_id:
            raise PolarisRunnerError(
                "submission missing encryption_key_id — cannot deploy v2 bundle"
            )

        start = datetime.now(UTC)
        deploy_resp = await self._request_deploy(
            bundle_url=bundle_url,
            encryption_key_id=encryption_key_id,
            miner_hotkey=miner_hotkey,
            card_id=task.card_id,
            submission_id=str(submission.get("id") or ""),
        )

        chat_payload = await self._post_chat(
            chat_endpoint=deploy_resp["chat_endpoint"],
            prompt=task.prompt,
        )

        card = self._extract_card(chat_payload)
        trace = chat_payload.get("trace") or {}

        manifest = await self._pull_manifest(deploy_resp["deployment_id"])
        self._verify_manifest_signature(manifest)

        duration_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
        agent_id = f"polaris-deploy:{deploy_resp['deployment_id']}"
        run_id = f"cathedral-{task.card_id}-e{task.epoch}r{task.round_index}"

        return PolarisRunResult(
            polaris_agent_id=agent_id,
            polaris_run_id=run_id,
            output_card_json=card,
            duration_ms=duration_ms,
            errors=[],
            attestation=None,  # v2 doesn't sign per-task; manifest covers it
            trace=trace,
            manifest=manifest,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _request_deploy(
        self,
        *,
        bundle_url: str,
        encryption_key_id: str,
        miner_hotkey: str,
        card_id: str,
        submission_id: str,
    ) -> dict[str, Any]:
        """Call POST /api/cathedral/v1/deploy and return its parsed body.

        The endpoint blocks until the Hermes deployment reaches RUNNING
        (or fails), so this HTTP call has a generous timeout. Errors
        surface as `PolarisRunnerError` so the orchestrator's existing
        retry-with-backoff path handles them uniformly with the other
        runner failure modes.
        """
        PolarisRunnerError, _ = _err_classes()  # noqa: N806
        body: dict[str, Any] = {
            "submission_id": submission_id,
            "miner_hotkey": miner_hotkey,
            "card_id": card_id,
            "bundle_url": bundle_url,
            "encryption_key_id": encryption_key_id,
            "bundle_kek_hex": self.config.bundle_encryption_key_hex,
            "ttl_minutes": self.config.ttl_minutes,
        }
        if self.config.pin_chutes_key and self.config.chutes_api_key:
            body["chutes_api_key"] = self.config.chutes_api_key

        logger.info(
            "cathedral_v2_deploy_request",
            submission_id=submission_id,
            card_id=card_id,
            miner_hotkey=miner_hotkey[:12],
            bundle_url_host=bundle_url.split("?")[0][:120],
            ttl_minutes=self.config.ttl_minutes,
        )
        try:
            async with httpx.AsyncClient(
                base_url=self.config.base_url,
                headers={"Authorization": f"Bearer {self.config.api_token}"},
                timeout=self.config.deploy_timeout_secs,
            ) as client:
                resp = await client.post("/api/cathedral/v1/deploy", json=body)
        except httpx.HTTPError as e:
            raise PolarisRunnerError(f"deploy transport: {e}") from e

        if resp.status_code >= 500:
            raise PolarisRunnerError(f"deploy 5xx: {resp.status_code} {resp.text[:512]}")
        if resp.status_code >= 400:
            raise PolarisRunnerError(f"deploy 4xx: {resp.status_code} {resp.text[:512]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise PolarisRunnerError(f"deploy non-JSON response: {e}") from e

        for field in ("deployment_id", "access_url", "chat_endpoint"):
            if not data.get(field):
                raise PolarisRunnerError(f"deploy response missing required field: {field}")
        return data

    async def _post_chat(self, *, chat_endpoint: str, prompt: str) -> dict[str, Any]:
        """POST the eval prompt to the agent's /chat, return the final event.

        Hermes streams NDJSON. We consume the stream, ignore the
        progress `tool_calls` events, capture the terminal `final`
        event, and return its parsed body.
        """
        PolarisRunnerError, _ = _err_classes()  # noqa: N806
        body = {"message": prompt}
        logger.info(
            "cathedral_v2_chat_request",
            chat_endpoint=chat_endpoint[:120],
            prompt_len=len(prompt),
        )
        final_event: dict[str, Any] | None = None
        error_event: dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient(timeout=self.config.chat_timeout_secs) as client:
                async with client.stream("POST", chat_endpoint, json=body) as resp:
                    if resp.status_code >= 400:
                        body_text = (await resp.aread()).decode("utf-8", "replace")
                        raise PolarisRunnerError(f"chat {resp.status_code}: {body_text[:512]}")
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        if event.get("type") == "final":
                            final_event = event
                        elif event.get("error") and not event.get("type"):
                            # NDJSON error events from `_run_agent`.
                            error_event = event
                        elif event.get("type") == "error":
                            error_event = event
        except httpx.HTTPError as e:
            raise PolarisRunnerError(f"chat transport: {e}") from e

        if final_event is None:
            if error_event is not None:
                msg = error_event.get("error") or error_event.get("detail") or "?"
                raise PolarisRunnerError(f"chat returned no final event: {msg}")
            raise PolarisRunnerError("chat stream ended without a final event")
        return final_event

    def _extract_card(self, chat_payload: dict[str, Any]) -> dict[str, Any]:
        """Pull the Card JSON out of the final event.

        Prefer the structured `card_json` field (Hermes v2 extracts it
        from the response content). Fall back to parsing the
        `content` string when the structured field is absent — early
        miners on the legacy soul.md template might not emit Card JSON
        in a fence-friendly way.
        """
        PolarisRunnerError, _ = _err_classes()  # noqa: N806
        card = chat_payload.get("card_json")
        if isinstance(card, dict) and card:
            return card
        content = chat_payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise PolarisRunnerError("chat final event has no card_json and empty content")
        candidate = content.strip()
        if candidate.startswith("```"):
            lines = candidate.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            while lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as e:
            raise PolarisRunnerError(
                f"chat final content is not parseable as Card JSON: {e}"
            ) from e
        if not isinstance(parsed, dict):
            raise PolarisRunnerError(
                f"chat final content is not a JSON object: got {type(parsed).__name__}"
            )
        return parsed

    async def _pull_manifest(self, deployment_id: str) -> dict[str, Any]:
        """GET /api/cathedral/v1/agents/{deployment_id}/manifest."""
        PolarisRunnerError, _ = _err_classes()  # noqa: N806
        url = f"/api/cathedral/v1/agents/{deployment_id}/manifest"
        try:
            async with httpx.AsyncClient(
                base_url=self.config.base_url,
                headers={"Authorization": f"Bearer {self.config.api_token}"},
                timeout=self.config.manifest_timeout_secs,
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise PolarisRunnerError(f"manifest transport: {e}") from e
        if resp.status_code >= 400:
            raise PolarisRunnerError(f"manifest fetch failed: {resp.status_code} {resp.text[:512]}")
        try:
            return resp.json()
        except ValueError as e:
            raise PolarisRunnerError(f"manifest non-JSON response: {e}") from e

    def _verify_manifest_signature(self, manifest: dict[str, Any]) -> None:
        """Ed25519-verify the manifest signature against the pinned public key.

        The cathedral-contract `attach_signature` signs the canonical
        JSON of the manifest's `model_dump(by_alias=True, mode="json",
        exclude_none=True)` form minus the `signature` field. We
        reconstruct the same bytes and verify.
        """
        _, PolarisAttestationError = _err_classes()  # noqa: N806
        sig_b64 = manifest.get("signature")
        if not isinstance(sig_b64, str) or not sig_b64:
            raise PolarisAttestationError("manifest has no signature field")
        try:
            signature = base64.b64decode(sig_b64, validate=True)
        except (ValueError, binascii.Error) as e:
            raise PolarisAttestationError(f"manifest signature not base64: {e}") from e

        # Pin the public key — manifest carries the key inline for the
        # simple case, but Cathedral verifies against the configured-
        # and-trusted key. Anything else means a downstream MITM
        # substituted a key it controls.
        announced_key = (manifest.get("public_key") or "").strip().lower()
        config_key = self.config.attestation_public_key_hex.strip().lower()
        if announced_key and announced_key != config_key:
            raise PolarisAttestationError(
                "manifest public_key does not match configured POLARIS_ATTESTATION_PUBLIC_KEY"
            )

        body = {k: v for k, v in manifest.items() if k != "signature"}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
        try:
            self._verify_key.verify(signature, canonical)
        except InvalidSignature as e:
            raise PolarisAttestationError(
                "manifest signature does not verify against configured "
                "POLARIS_ATTESTATION_PUBLIC_KEY"
            ) from e
