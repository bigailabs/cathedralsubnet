"""Validator — drives one job through a miner and emits a Trajectory."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from cathedral.v3.miner.base import MinerAgent
from cathedral.v3.types import AgentResult, JobSpec, ScoreParts, Trajectory
from cathedral.v3.validator.toolbus import ToolBus
from cathedral.v3.validator.tools import build_handlers


class Validator:
    """Owns the ToolBus, dispatches the job, collects the trajectory."""

    def __init__(self, tool_timeout_seconds: float = 30.0) -> None:
        self._timeout = tool_timeout_seconds

    async def dispatch(self, job: JobSpec, miner: MinerAgent) -> Trajectory:
        handlers = build_handlers(job)
        bus = ToolBus(handlers, timeout_seconds=self._timeout)
        started = datetime.now(UTC)
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(miner.run(job, bus), timeout=job.deadline_seconds)
        except TimeoutError:
            result = AgentResult(final_output="", agent_error="job_deadline_exceeded")
        except Exception as e:
            result = AgentResult(final_output="", agent_error=f"agent_exception: {e}")
        ended = datetime.now(UTC)
        wall_ms = (time.perf_counter() - t0) * 1000.0
        if result.wall_time_ms is None:
            result = result.model_copy(update={"wall_time_ms": wall_ms})

        traj = Trajectory(
            job=job,
            miner_hotkey=miner.hotkey,
            miner_kind=miner.kind,
            tool_calls=bus.flush_calls(),
            result=result,
            score=ScoreParts.empty(),
            started_at=started,
            ended_at=ended,
        )
        # Tuck the sink values into structured so the scorer can use them
        # without re-running tools. Validator-only handlers are prefixed
        # with __sink_* and never called by miners (unknown to them).
        sinks = {k.strip("_"): handlers[k]({}) for k in handlers if k.startswith("__sink_")}
        if sinks:
            traj.result.structured.setdefault("_sinks", {}).update(sinks)
        return traj
