"""The runtime that wires the full loop together.

One Runtime instance owns:
  - the archive
  - the receipt signer
  - the validator
  - a job generator
  - a list of miners

`runtime.tick()` runs one round of the loop:
  generate jobs -> dispatch each to each miner -> score -> sign -> archive
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from cathedral.v2.archive import TrajectoryArchive
from cathedral.v2.jobs import JobGenerator
from cathedral.v2.miner.base import MinerAgent
from cathedral.v2.miner.echo import EchoAgent
from cathedral.v2.miner.heuristic import HeuristicAgent
from cathedral.v2.miner.llm import LLMAgent
from cathedral.v2.receipt import ReceiptSigner, load_or_create_signing_key
from cathedral.v2.scoring import WeightLoop, score_trajectory
from cathedral.v2.types import JobSpec, TaskType, Trajectory, Weights
from cathedral.v2.validator.observer import Validator


def default_home() -> Path:
    return Path(os.environ.get("CATHEDRAL_V2_HOME") or (Path.home() / ".cathedral" / "v2"))


def build_default_miners() -> list[MinerAgent]:
    return [
        EchoAgent(hotkey="hk_echo"),
        HeuristicAgent(hotkey="hk_heuristic"),
        LLMAgent(hotkey="hk_llm"),
    ]


def miner_by_name(name: str) -> MinerAgent:
    name = name.strip().lower()
    if name == "echo":
        return EchoAgent(hotkey="hk_echo")
    if name == "heuristic":
        return HeuristicAgent(hotkey="hk_heuristic")
    if name == "llm":
        return LLMAgent(hotkey="hk_llm")
    raise ValueError(f"unknown miner: {name}")


@dataclass
class TickResult:
    trajectories: list[Trajectory] = field(default_factory=list)
    weights: Weights | None = None


class Runtime:
    def __init__(
        self,
        home: Path | None = None,
        miners: list[MinerAgent] | None = None,
        task_types: list[TaskType] | None = None,
        tool_timeout_seconds: float = 30.0,
    ) -> None:
        self.home = home or default_home()
        self.archive = TrajectoryArchive(self.home)
        self._sk = load_or_create_signing_key(self.home)
        self.signer = ReceiptSigner(self._sk)
        self.validator = Validator(tool_timeout_seconds=tool_timeout_seconds)
        self.generator = JobGenerator(task_types=task_types)
        self.miners = miners or build_default_miners()
        self.weights = WeightLoop(self.archive)

    async def run_one(self, job: JobSpec, miner: MinerAgent) -> Trajectory:
        traj = await self.validator.dispatch(job, miner)
        traj.score = score_trajectory(traj)
        receipt = self.signer.sign(traj)
        self.archive.insert(traj, receipt=receipt)
        return traj

    async def tick(self) -> TickResult:
        jobs = self.generator.tick()
        trajs: list[Trajectory] = []
        # one job -> all miners (creates preference pairs naturally)
        coros = [self.run_one(j, m) for j in jobs for m in self.miners]
        for t in await asyncio.gather(*coros, return_exceptions=True):
            if isinstance(t, Trajectory):
                trajs.append(t)
        w = self.weights.step()
        return TickResult(trajectories=trajs, weights=w)

    async def serve(self, ticks: int, interval_seconds: float = 30.0) -> list[TickResult]:
        results: list[TickResult] = []
        for i in range(ticks):
            r = await self.tick()
            results.append(r)
            if i < ticks - 1 and interval_seconds > 0:
                await asyncio.sleep(interval_seconds)
        return results
