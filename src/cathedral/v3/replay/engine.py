"""Replay a historical trajectory against a (possibly different) miner.

Reconstruct the JobSpec exactly, run the new miner with the same tool
catalog, and compute a divergence: where in the tool trace does the new
miner first take a different action?
"""

from __future__ import annotations

from dataclasses import dataclass

from cathedral.v3.archive import TrajectoryArchive
from cathedral.v3.miner.base import MinerAgent
from cathedral.v3.scoring import score_trajectory
from cathedral.v3.types import Trajectory
from cathedral.v3.validator import Validator


@dataclass
class ReplayDivergence:
    original: Trajectory
    replayed: Trajectory
    first_divergent_step: int | None  # 0-based step where traces differ; None = identical
    same_final_output: bool
    score_delta: float


async def replay(
    archive: TrajectoryArchive,
    trajectory_id: str,
    miner: MinerAgent,
    persist: bool = False,
) -> ReplayDivergence:
    original = archive.get(trajectory_id)
    if original is None:
        raise ValueError(f"trajectory not found: {trajectory_id}")
    validator = Validator()
    replayed = await validator.dispatch(original.job, miner)
    replayed.score = score_trajectory(replayed)
    if persist:
        archive.insert(replayed)

    div = _first_divergence(original, replayed)
    return ReplayDivergence(
        original=original,
        replayed=replayed,
        first_divergent_step=div,
        same_final_output=original.result.final_output.strip()
        == replayed.result.final_output.strip(),
        score_delta=round(replayed.score.weighted - original.score.weighted, 4),
    )


def _first_divergence(a: Trajectory, b: Trajectory) -> int | None:
    n = min(len(a.tool_calls), len(b.tool_calls))
    for i in range(n):
        ca, cb = a.tool_calls[i], b.tool_calls[i]
        if ca.tool_name != cb.tool_name or ca.args != cb.args:
            return i
    if len(a.tool_calls) != len(b.tool_calls):
        return n
    return None


__all__ = ["ReplayDivergence", "replay"]
