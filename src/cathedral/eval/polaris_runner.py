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

When `CATHEDRAL_EVAL_MODE=bundle`, the orchestrator wires
`BundleCardRunner`, the BYO-compute path: the miner already ran their
agent, baked the resulting Card JSON into the bundle at
`artifacts/last-card.json`, and the publisher's job is just to read +
score it. No Polaris call, no fabrication.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
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


# --------------------------------------------------------------------------
# BYO-compute runner — score the miner's pre-baked card from the bundle
# --------------------------------------------------------------------------


# Bundle paths the miner is allowed to use for the pre-baked card. Order
# matters: the first match wins. `artifacts/last-card.json` is the
# canonical Hermes convention (matches the test miner script); `card.json`
# at root is accepted as a friendlier alias for hand-crafted bundles.
_BUNDLE_CARD_PATHS: tuple[str, ...] = (
    "artifacts/last-card.json",
    "card.json",
)


def _find_card_in_bundle(bundle_bytes: bytes) -> tuple[str, bytes] | None:
    """Locate the miner-supplied card JSON inside a zip bundle.

    Returns (member_name, raw_bytes) for the first match in
    `_BUNDLE_CARD_PATHS`, or None if the bundle has no recognised card
    file. Tolerates a single top-level directory prefix (matches the
    same convention as `bundle_extractor._find_first`).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    except zipfile.BadZipFile:
        return None
    with zf:
        names = {info.filename.replace("\\", "/"): info for info in zf.infolist()}
        for candidate in _BUNDLE_CARD_PATHS:
            if candidate in names:
                return candidate, zf.read(names[candidate])
            # Allow a single-directory nesting prefix, e.g.
            # `my-agent/artifacts/last-card.json`.
            for member, info in names.items():
                if (
                    member.endswith("/" + candidate)
                    and member.count("/") == candidate.count("/") + 1
                ):
                    return member, zf.read(info)
    return None


class BundleCardRunner:
    """Path A — score the miner's pre-baked Card JSON instead of running an eval.

    Wired by `CATHEDRAL_EVAL_MODE=bundle`. The orchestrator already
    decrypted the bundle and handed us the plaintext bytes; we look for
    `artifacts/last-card.json` (or `card.json`), parse it as JSON, and
    return it as the runner's output. The downstream scoring pipeline
    runs preflight + scorer against this dict exactly as if it had come
    from a Polaris-spawned Hermes container — the only difference is
    that the LLM work happened on the miner's hardware.

    Failure modes are signalled via `PolarisRunnerError` so the
    orchestrator's existing retry path (CONTRACTS.md §6) handles them
    uniformly:

      - missing card file       -> PolarisRunnerError("bundle missing ...")
      - unreadable zip          -> PolarisRunnerError("bundle not a valid zip")
      - malformed JSON          -> PolarisRunnerError("card.json malformed: ...")
      - non-object JSON root    -> PolarisRunnerError("card.json must be a JSON object")

    After 3 such errors, the orchestrator marks the submission
    `rejected` with `rejection_reason="polaris exhausted retries"` and
    records a zero-score eval_run with the error list — same surface
    behaviour as a Polaris-side failure.
    """

    def __init__(self) -> None:
        # No I/O, no state — runner instances are cheap and stateless,
        # matching the StubPolarisRunner contract.
        pass

    async def run(
        self,
        *,
        bundle_bytes: bytes,
        bundle_hash: str,
        task: EvalTask,
        miner_hotkey: str,
    ) -> PolarisRunResult:
        del bundle_hash, miner_hotkey  # not needed; scoring_pipeline re-pins attribution

        start = datetime.now(UTC)
        located = _find_card_in_bundle(bundle_bytes)
        if located is None:
            raise PolarisRunnerError(
                f"bundle missing card file (looked for {', '.join(_BUNDLE_CARD_PATHS)})"
            )
        member, raw = located
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as e:
            raise PolarisRunnerError(f"{member} is not valid utf-8: {e}") from e
        except json.JSONDecodeError as e:
            raise PolarisRunnerError(f"{member} malformed: {e}") from e
        if not isinstance(decoded, dict):
            raise PolarisRunnerError(
                f"{member} must be a JSON object, got {type(decoded).__name__}"
            )

        # BYO-compute path: no polaris_agent_id, no polaris_run_id (Polaris
        # didn't run anything). scoring_pipeline.score_and_sign treats an
        # empty polaris_agent_id as "not Polaris-verified" and skips the
        # 1.10x runtime multiplier — that's the correct semantics here.
        duration_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
        return PolarisRunResult(
            polaris_agent_id="",
            polaris_run_id=f"bundle-{task.card_id}-e{task.epoch}r{task.round_index}",
            output_card_json=decoded,
            duration_ms=duration_ms,
            errors=[],
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
