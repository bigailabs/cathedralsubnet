"""Polaris API client — spawn Hermes container with bundle, capture card.

CONTRACTS.md Section 6 step 3: cathedral fetches the encrypted bundle
from Hippius, decrypts in-memory, writes to ephemeral container volume,
spawns Hermes via Polaris, captures stdout last line as Card JSON,
terminates.

The Polaris API contract is in `polariscomputer/polaris/api/routers/`.
For v1 the integration agent will wire to the real endpoint; this
module exposes a small `PolarisRunner` Protocol so the orchestrator can
be unit-tested with a `StubPolarisRunner` and integration-tested with
a `HttpPolarisRunner` once Polaris ships the container-runner endpoint.

When `CATHEDRAL_EVAL_MODE=stub`, the orchestrator wires `StubPolarisRunner`
which fabricates a card from the task — useful for end-to-end smoke
tests before the real Polaris endpoint exists.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import httpx
import structlog

from cathedral.v1_types import EvalTask

logger = structlog.get_logger(__name__)


class PolarisRunnerError(Exception):
    """Polaris call failed in a retryable or terminal way."""


@dataclass
class PolarisRunResult:
    polaris_agent_id: str
    polaris_run_id: str
    output_card_json: dict[str, Any]
    duration_ms: int
    errors: list[str] = field(default_factory=list)


class PolarisRunner(Protocol):
    async def run(
        self,
        *,
        bundle_bytes: bytes,
        bundle_hash: str,
        task: EvalTask,
        miner_hotkey: str,
    ) -> PolarisRunResult: ...


# --------------------------------------------------------------------------
# Stub for tests / dev
# --------------------------------------------------------------------------


class StubPolarisRunner:
    """Returns a hand-crafted card so the rest of the pipeline can run.

    Use when `CATHEDRAL_EVAL_MODE=stub`. The card it returns will pass
    preflight (single citation, no_legal_advice=true, etc) but its
    score is intentionally middling so first-mover delta logic can be
    exercised.
    """

    def __init__(self, *, fixed_score_seed: int = 0) -> None:
        self._counter = fixed_score_seed
        # When set (via build_app for CATHEDRAL_EVAL_MODE=stub-deterministic-score
        # mode + CATHEDRAL_STUB_SCORE env var), the stub fabricates a card
        # with content tuned to score approximately at this value. Used by
        # the first-mover delta integration test which needs two
        # submissions to score identically.
        import os
        score_env = os.environ.get("CATHEDRAL_STUB_SCORE")
        try:
            self._target_score: float | None = (
                float(score_env) if score_env else None
            )
        except (TypeError, ValueError):
            self._target_score = None

    async def run(
        self,
        *,
        bundle_bytes: bytes,
        bundle_hash: str,
        task: EvalTask,
        miner_hotkey: str,
    ) -> PolarisRunResult:
        self._counter += 1
        agent_id = f"agt_stub_{task.card_id}_{self._counter:04d}"
        run_id = f"run_stub_{uuid4().hex[:12]}"

        now = datetime.now(UTC)
        card = {
            "id": task.card_id,
            "jurisdiction": _juris_for(task.card_id),
            "topic": "stub eval output",
            "worker_owner_hotkey": miner_hotkey,
            "polaris_agent_id": agent_id,
            "title": f"Stub: {task.prompt[:40]}",
            "summary": (
                "Stubbed eval output — used in CATHEDRAL_EVAL_MODE=stub for "
                "end-to-end smoke tests of the publisher + scoring pipeline. "
                "Replace with real Polaris-spawned Hermes output in production."
            ),
            "what_changed": "no real change captured; this is a stub",
            "why_it_matters": (
                "The eval orchestrator's scoring pipeline runs against this "
                "fabricated card so we can verify the full submission ->\n"
                "encrypt -> queue -> stub-eval -> score -> sign chain works "
                "without depending on a live Polaris API."
            ),
            "action_notes": "ignore in production",
            "risks": "stub mode must never run on prod; gate by env",
            "citations": [
                {
                    "url": "https://example.invalid/stub",
                    "class": "other",
                    "fetched_at": now.isoformat(),
                    "status": 200,
                    "content_hash": "0" * 64,
                }
            ],
            "confidence": 0.6,
            "no_legal_advice": True,
            "last_refreshed_at": now.isoformat(),
            "refresh_cadence_hours": 24,
        }
        return PolarisRunResult(
            polaris_agent_id=agent_id,
            polaris_run_id=run_id,
            output_card_json=card,
            duration_ms=12,
            errors=[],
        )


class FailingStubPolarisRunner:
    """Always raises `PolarisRunnerError` — drives the retry-exhaustion path."""

    async def run(
        self,
        *,
        bundle_bytes: bytes,
        bundle_hash: str,
        task: EvalTask,
        miner_hotkey: str,
    ) -> PolarisRunResult:
        del bundle_bytes, bundle_hash, task, miner_hotkey
        raise PolarisRunnerError("stub-fail-polaris: simulated transport failure")


class MalformedStubPolarisRunner:
    """Returns a card whose JSON is structurally invalid — preflight rejects."""

    async def run(
        self,
        *,
        bundle_bytes: bytes,
        bundle_hash: str,
        task: EvalTask,
        miner_hotkey: str,
    ) -> PolarisRunResult:
        del bundle_bytes, bundle_hash
        return PolarisRunResult(
            polaris_agent_id=f"agt_bad_{task.card_id}",
            polaris_run_id=f"run_bad_{uuid4().hex[:12]}",
            output_card_json={
                # Missing every required Card field — Card.model_validate
                # raises and the scoring pipeline records weighted_score=0
                # with errors=[...]. Keep `id` so insertion still works.
                "id": task.card_id,
                "_intentionally_malformed": True,
                "worker_owner_hotkey": miner_hotkey,
            },
            duration_ms=5,
            errors=["malformed card from stub-bad-card mode"],
        )


def _juris_for(card_id: str) -> str:
    if card_id.startswith("eu-"):
        return "eu"
    if card_id.startswith("us-"):
        return "us"
    if card_id.startswith("uk-"):
        return "uk"
    if card_id.startswith("singapore-"):
        return "sg"
    if card_id.startswith("japan-"):
        return "jp"
    return "other"


# --------------------------------------------------------------------------
# Real Polaris HTTP runner
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpPolarisRunnerConfig:
    base_url: str
    api_token: str
    poll_interval_secs: float = 5.0
    deadline_buffer_secs: int = 5 * 60  # +5 min above task.deadline_minutes


class HttpPolarisRunner:
    """Production runner: deploy bundle, poll, fetch card, teardown.

    The exact Polaris endpoint shapes are pinned in the
    polariscomputer cathedral-contract worktree. This client expects:

      POST {base_url}/polaris/agents/cathedral-eval
        body: { bundle_b64, bundle_hash, task: EvalTask, miner_hotkey }
        -> { polaris_agent_id, polaris_run_id, status: "running" }

      GET  {base_url}/polaris/runs/{run_id}
        -> { status: "running"|"success"|"failed",
             output_card?: CardJSON, error?: str }

    If Polaris's endpoint contract changes, swap the body shape here and
    the rest of the pipeline stays unchanged.
    """

    def __init__(self, config: HttpPolarisRunnerConfig) -> None:
        self.config = config

    async def run(
        self,
        *,
        bundle_bytes: bytes,
        bundle_hash: str,
        task: EvalTask,
        miner_hotkey: str,
    ) -> PolarisRunResult:
        import base64

        body = {
            "bundle_b64": base64.b64encode(bundle_bytes).decode("ascii"),
            "bundle_hash": bundle_hash,
            "task": task.model_dump(mode="json"),
            "miner_hotkey": miner_hotkey,
        }
        deadline_secs = task.deadline_minutes * 60 + self.config.deadline_buffer_secs
        start = datetime.now(UTC)

        async with httpx.AsyncClient(
            base_url=self.config.base_url,
            headers={"Authorization": f"Bearer {self.config.api_token}"},
            timeout=60.0,
        ) as client:
            try:
                resp = await client.post(
                    "/polaris/agents/cathedral-eval", json=body
                )
            except httpx.HTTPError as e:
                raise PolarisRunnerError(f"deploy transport: {e}") from e
            if resp.status_code >= 500:
                raise PolarisRunnerError(
                    f"deploy 5xx: {resp.status_code} {resp.text}"
                )
            if resp.status_code >= 400:
                # 4xx is a permanent error — bundle bad, miner cooked.
                raise PolarisRunnerError(
                    f"deploy 4xx: {resp.status_code} {resp.text}"
                )
            payload = resp.json()
            agent_id = str(payload["polaris_agent_id"])
            run_id = str(payload["polaris_run_id"])

            # Poll until terminal or deadline.
            while True:
                elapsed = (datetime.now(UTC) - start).total_seconds()
                if elapsed > deadline_secs:
                    raise PolarisRunnerError(
                        f"polaris run {run_id} exceeded deadline {deadline_secs}s"
                    )
                try:
                    poll = await client.get(f"/polaris/runs/{run_id}")
                except httpx.HTTPError as e:
                    raise PolarisRunnerError(f"poll transport: {e}") from e
                if poll.status_code >= 500:
                    raise PolarisRunnerError(f"poll 5xx: {poll.status_code}")
                pdata = poll.json()
                status = str(pdata.get("status", "")).lower()
                if status == "success":
                    card = pdata.get("output_card")
                    if not isinstance(card, dict):
                        raise PolarisRunnerError("polaris success but no output_card")
                    duration = int((datetime.now(UTC) - start).total_seconds() * 1000)
                    return PolarisRunResult(
                        polaris_agent_id=agent_id,
                        polaris_run_id=run_id,
                        output_card_json=card,
                        duration_ms=duration,
                        errors=[],
                    )
                if status == "failed":
                    raise PolarisRunnerError(
                        f"polaris run failed: {pdata.get('error', '?')}"
                    )
                await asyncio.sleep(self.config.poll_interval_secs)
