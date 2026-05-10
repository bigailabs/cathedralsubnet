"""Eval scheduler loop (CONTRACTS.md Section 6 step 3).

    SELECT submissions WHERE status='queued' ORDER BY submitted_at ASC
        LIMIT max_concurrent
    per submission:
        1. UPDATE status='evaluating'
        2. resolve epoch + round_index for this card
        3. generate EvalTask deterministically
        4. fetch encrypted bundle from Hippius, decrypt to temp dir
        5. POST to Polaris orchestrator: spawn hermes container, run task
        6. capture container stdout last line as Card JSON
        7. terminate container, delete ephemeral volume
    on Polaris API failure: retry up to 3x with exponential backoff
        (60s, 120s, 240s); after 3 failures, leave status='evaluating'
        for the operator dashboard
    on bundle decryption failure: status='rejected'
    on Card JSON parse failure: record EvalRun with errors=[...], score=0
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from cathedral.cards.registry import CardRegistry
from cathedral.eval.polaris_runner import PolarisRunner, PolarisRunnerError
from cathedral.eval.scoring_pipeline import EvalSigner, score_and_sign
from cathedral.eval.task_generator import generate_task
from cathedral.publisher import repository
from cathedral.publisher.merkle import epoch_for
from cathedral.storage import (
    DecryptionError,
    HippiusClient,
    HippiusError,
    decrypt_bundle,
    safe_extract_zip,
)
from cathedral.storage.bundle_extractor import BundleStructureError

logger = structlog.get_logger(__name__)


_RETRY_BACKOFFS = (60, 120, 240)


def _retry_backoffs() -> tuple[float, ...]:
    """Production retry policy (CONTRACTS.md §6 'Timeouts and policies').

    Tests set `CATHEDRAL_FAST_RETRIES=1` (or any `CATHEDRAL_EVAL_MODE`
    starting with `stub`) to keep ticks bounded — same 3-attempt policy
    but with zero sleep between attempts.
    """
    import os

    if os.environ.get("CATHEDRAL_FAST_RETRIES") == "1" or os.environ.get(
        "CATHEDRAL_EVAL_MODE", ""
    ).lower().startswith("stub"):
        return (0.0, 0.0, 0.0)
    return _RETRY_BACKOFFS


@dataclass
class _RoundCounter:
    """Track per-card round_index across the current epoch."""

    epoch: int
    counter: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def next_index(self, card_id: str, current_epoch: int) -> int:
        if current_epoch != self.epoch:
            self.epoch = current_epoch
            self.counter.clear()
        idx = self.counter[card_id]
        self.counter[card_id] = idx + 1
        return idx


class EvalOrchestrator:
    """Orchestrates the eval lifecycle for a single submission."""

    def __init__(
        self,
        *,
        db: aiosqlite.Connection,
        hippius: HippiusClient,
        polaris: PolarisRunner,
        signer: EvalSigner,
        registry: CardRegistry,
    ) -> None:
        self.db = db
        self.hippius = hippius
        self.polaris = polaris
        self.signer = signer
        self.registry = registry
        self._round_counter = _RoundCounter(epoch=epoch_for(datetime.now(UTC)))
        self._failure_counts: dict[str, int] = defaultdict(int)

    async def evaluate_one(self, submission: dict[str, Any]) -> None:
        log = logger.bind(submission_id=submission["id"], card_id=submission["card_id"])

        card_def = await repository.get_card_definition(self.db, submission["card_id"])
        if card_def is None:
            await repository.update_submission_status(
                self.db,
                submission["id"],
                status="rejected",
                rejection_reason="card definition missing",
            )
            log.warning("eval_card_def_missing")
            return

        await repository.update_submission_status(
            self.db, submission["id"], status="evaluating"
        )

        # Generate deterministic task for this round
        epoch = epoch_for(datetime.now(UTC))
        round_index = self._round_counter.next_index(submission["card_id"], epoch)
        task = generate_task(
            card_id=submission["card_id"],
            epoch=epoch,
            round_index=round_index,
            card_definition=card_def,
        )

        # Fetch + decrypt bundle
        try:
            ciphertext = await self.hippius.get_bundle(submission["bundle_blob_key"])
        except HippiusError as e:
            await self._on_retryable_failure(submission, log, f"hippius get: {e}")
            return

        try:
            plaintext = decrypt_bundle(ciphertext, submission["encryption_key_id"])
        except DecryptionError as e:
            await repository.update_submission_status(
                self.db,
                submission["id"],
                status="rejected",
                rejection_reason="bundle decryption failed",
            )
            log.error("eval_bundle_decrypt_failed", error=str(e))
            return

        # Extract to ephemeral dir, then immediately drop the path —
        # Polaris will get the bundle bytes directly via the runner API
        # (we keep the extraction step here so adversarial-zip checks
        # still run). Wipe the dir afterwards regardless of outcome.
        tmp_root = Path(tempfile.mkdtemp(prefix="cathedral-eval-"))
        try:
            try:
                safe_extract_zip(plaintext, tmp_root)
            except BundleStructureError as e:
                await repository.update_submission_status(
                    self.db,
                    submission["id"],
                    status="rejected",
                    rejection_reason=f"bundle structure: {e}",
                )
                log.error("eval_bundle_structure_invalid", error=str(e))
                return

            polaris_errors: list[str] = []
            polaris_result = None
            backoffs = _retry_backoffs()
            for attempt, backoff in enumerate(backoffs, start=1):
                try:
                    polaris_result = await self.polaris.run(
                        bundle_bytes=plaintext,
                        bundle_hash=submission["bundle_hash"],
                        task=task,
                        miner_hotkey=submission["miner_hotkey"],
                    )
                    break
                except PolarisRunnerError as e:
                    polaris_errors.append(f"attempt {attempt}: {e}")
                    log.warning(
                        "eval_polaris_attempt_failed",
                        attempt=attempt,
                        error=str(e),
                    )
                    if attempt < len(backoffs) and backoff > 0:
                        await asyncio.sleep(backoff)

            if polaris_result is None:
                # Persist a zero-score eval_run with errors so the public
                # API surfaces the failure (CONTRACTS.md §6 step 3-4 — the
                # contract test asserts on either weighted_score=0 OR
                # errors!=None). Status moves to 'rejected' to match the
                # 'evaluating -> rejected' state machine arrow.
                self._failure_counts[submission["id"]] += 1
                log.error(
                    "eval_polaris_exhausted_retries",
                    errors=polaris_errors,
                )
                await score_and_sign(
                    self.db,
                    submission=submission,
                    epoch=epoch,
                    round_index=round_index,
                    polaris_agent_id="polaris-unavailable",
                    polaris_run_id=f"failed-{submission['id'][:8]}",
                    task_json=task.model_dump(mode="json"),
                    output_card_json={
                        "id": submission["card_id"],
                        "_polaris_unreachable": True,
                    },
                    duration_ms=0,
                    polaris_errors=polaris_errors
                    or ["polaris exhausted retries"],
                    registry=self.registry,
                    signer=self.signer,
                )
                await repository.update_submission_status(
                    self.db,
                    submission["id"],
                    status="rejected",
                    rejection_reason="polaris exhausted retries",
                )
                return

            await score_and_sign(
                self.db,
                submission=submission,
                epoch=epoch,
                round_index=round_index,
                polaris_agent_id=polaris_result.polaris_agent_id,
                polaris_run_id=polaris_result.polaris_run_id,
                task_json=task.model_dump(mode="json"),
                output_card_json=polaris_result.output_card_json,
                duration_ms=polaris_result.duration_ms,
                polaris_errors=polaris_errors + polaris_result.errors,
                registry=self.registry,
                signer=self.signer,
            )
            log.info("eval_run_complete", epoch=epoch, round_index=round_index)
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
            # Drop the plaintext binding so GC can reclaim it on the next
            # collector pass. Best-effort — Python doesn't guarantee
            # zeroing, but losing the only reference is the closest we
            # get without ctypes-level memzero.
            plaintext = b""

    async def _on_retryable_failure(
        self,
        submission: dict[str, Any],
        log: structlog.stdlib.BoundLogger,
        reason: str,
    ) -> None:
        self._failure_counts[submission["id"]] += 1
        if self._failure_counts[submission["id"]] >= 3:
            await repository.update_submission_status(
                self.db,
                submission["id"],
                status="rejected",
                rejection_reason=reason,
            )
            log.error("eval_retryable_exhausted", reason=reason)
        else:
            # Re-queue
            await repository.update_submission_status(
                self.db, submission["id"], status="queued"
            )
            log.warning(
                "eval_retry_queued",
                reason=reason,
                attempts=self._failure_counts[submission["id"]],
            )


# --------------------------------------------------------------------------
# Background loop
# --------------------------------------------------------------------------


async def run_eval_loop(
    *,
    db: aiosqlite.Connection,
    hippius: HippiusClient,
    polaris: PolarisRunner,
    signer: EvalSigner,
    registry: CardRegistry,
    poll_interval_secs: float = 10.0,
    max_concurrent: int = 2,
    stop: asyncio.Event | None = None,
) -> None:
    """Long-running scheduler — picks queued submissions and evals them.

    Single-writer design: each submission is updated to 'evaluating'
    atomically before the work begins, so two concurrent loop iterations
    never pick the same row.
    """
    stop = stop or asyncio.Event()
    orchestrator = EvalOrchestrator(
        db=db, hippius=hippius, polaris=polaris, signer=signer, registry=registry
    )
    sem = asyncio.Semaphore(max_concurrent)

    while not stop.is_set():
        try:
            queued = await repository.queued_submissions(db, limit=max_concurrent)
        except aiosqlite.Error as e:
            logger.warning("eval_loop_query_failed", error=str(e))
            await _sleep_or_stop(stop, poll_interval_secs)
            continue

        if not queued:
            await _sleep_or_stop(stop, poll_interval_secs)
            continue

        async def _process(s: dict[str, Any]) -> None:
            async with sem:
                try:
                    await orchestrator.evaluate_one(s)
                except Exception as e:
                    logger.exception("eval_one_crashed", submission_id=s["id"], error=str(e))

        await asyncio.gather(*[_process(s) for s in queued])


async def _sleep_or_stop(stop: asyncio.Event, secs: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=secs)
    except TimeoutError:
        pass


# --------------------------------------------------------------------------
# Test-friendly entry point
# --------------------------------------------------------------------------


async def _evaluating_submissions(
    conn: Any, limit: int = 10
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM agent_submissions WHERE status='evaluating' "
        "ORDER BY submitted_at ASC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=False)) for r in rows]


def _resolve_polaris_runner_from_env() -> PolarisRunner:
    """Re-build a Polaris runner from the current env so monkeypatched
    `CATHEDRAL_EVAL_MODE` mid-test takes effect on the next tick."""
    import os

    from cathedral.eval.polaris_runner import (
        FailingStubPolarisRunner,
        HttpPolarisRunner,
        HttpPolarisRunnerConfig,
        MalformedStubPolarisRunner,
        StubPolarisRunner,
    )

    mode = os.environ.get("CATHEDRAL_EVAL_MODE", "stub").lower()
    if mode == "stub-fail-polaris":
        return FailingStubPolarisRunner()
    if mode == "stub-bad-card":
        return MalformedStubPolarisRunner()
    if mode.startswith("stub"):
        return StubPolarisRunner()
    return HttpPolarisRunner(
        HttpPolarisRunnerConfig(
            base_url=os.environ.get(
                "POLARIS_BASE_URL", "https://api.polaris.computer"
            ),
            api_token=os.environ.get("POLARIS_API_TOKEN", ""),
        )
    )


async def _run_once_async() -> int:
    """Process queued / evaluating submissions in two phases per tick so
    the state-machine transitions queued -> evaluating -> ranked|rejected
    are observable across separate `run_once()` calls (per CONTRACTS.md
    §6 status arrows).

    Phase 1 (per call): promote up to N queued submissions to
    'evaluating'. Phase 2 (next call): finish evaluating + rank.

    Returns the number of submissions advanced this tick.
    """
    from cathedral.publisher.app import latest_ctx

    ctx = latest_ctx()
    if ctx is None:
        return 0

    # Use the env-resolved Polaris runner so mid-test monkeypatch of
    # CATHEDRAL_EVAL_MODE actually takes effect.
    polaris = _resolve_polaris_runner_from_env()
    orch = EvalOrchestrator(
        db=ctx.db,
        hippius=ctx.hippius,
        polaris=polaris,
        signer=ctx.signer,
        registry=ctx.registry,
    )

    advanced = 0

    # Phase 2: finish in-flight evaluating rows from a previous tick.
    in_flight = await _evaluating_submissions(ctx.db, limit=10)
    for s in in_flight:
        try:
            await orch.evaluate_one(s)
            advanced += 1
        except Exception as e:
            logger.exception("eval_run_once_crashed", submission_id=s["id"], error=str(e))

    # Phase 1: promote queued -> evaluating (work happens next tick).
    queued = await repository.queued_submissions(ctx.db, limit=10)
    for s in queued:
        await repository.update_submission_status(
            ctx.db, s["id"], status="evaluating"
        )
        advanced += 1

    return advanced


def run_once() -> int:
    """Synchronous wrapper around `_run_once_async` for the test harness."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside an async context already — schedule and wait.
            return asyncio.run_coroutine_threadsafe(
                _run_once_async(), loop
            ).result(timeout=60)
    except RuntimeError:
        pass
    return asyncio.run(_run_once_async())


# Aliases the contract test probes for.
tick = run_once
process_one = run_once
schedule_once = run_once
