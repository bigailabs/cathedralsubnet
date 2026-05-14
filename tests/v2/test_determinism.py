"""Regression: (task_type, seed) -> JobSpec must be stable across processes.

Python's built-in hash() is salted per process by PYTHONHASHSEED, so anything
that uses hash() for seeding produces different output in different processes.
The job generator must not rely on hash(); this test enforces that by
spawning a fresh interpreter (with a different PYTHONHASHSEED) and comparing
the resulting JobSpec bytes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cathedral.v2.jobs import generate_job
from cathedral.v2.types import TaskType

ROOT = Path(__file__).resolve().parents[2]


_DETERMINISTIC_FIELDS = (
    "task_type",
    "prompt",
    "context",
    "tools",
    "expected_artifacts",
    "rubric_id",
    "seed",
    "deadline_seconds",
)


def _deterministic_view(job_dict: dict[str, Any]) -> dict[str, Any]:
    return {k: job_dict.get(k) for k in _DETERMINISTIC_FIELDS}


def _dump_job_in_subprocess(task_type: str, seed: int, py_hash_seed: str) -> dict[str, Any]:
    """Generate a job in a fresh interpreter with the given PYTHONHASHSEED."""
    script = (
        "import json, sys\n"
        "from cathedral.v2.jobs import generate_job\n"
        "from cathedral.v2.types import TaskType\n"
        f"job = generate_job(TaskType('{task_type}'), seed={seed})\n"
        "fields = ('task_type','prompt','context','tools','expected_artifacts',"
        "          'rubric_id','seed','deadline_seconds')\n"
        "d = job.model_dump(mode='json')\n"
        "view = {k: d.get(k) for k in fields}\n"
        "sys.stdout.write(json.dumps(view, sort_keys=True, default=str))\n"
    )
    env = {
        "PYTHONHASHSEED": py_hash_seed,
        "PYTHONPATH": str(ROOT / "src"),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=30,
    )
    data: dict[str, Any] = json.loads(result.stdout)
    return data


@pytest.mark.parametrize(
    "task_type",
    ["research", "code_patch", "tool_route", "multi_step", "classify"],
)
@pytest.mark.parametrize("seed", [0, 1, 42, 1009])
def test_generate_job_stable_across_processes(task_type: str, seed: int) -> None:
    # Two subprocesses with different PYTHONHASHSEED values. If the generator
    # uses hash() anywhere, these will diverge.
    a = _dump_job_in_subprocess(task_type, seed, py_hash_seed="0")
    b = _dump_job_in_subprocess(task_type, seed, py_hash_seed="12345")
    assert a == b, f"job determinism broken for {task_type} seed={seed}"


@pytest.mark.parametrize(
    "task_type",
    [
        TaskType.RESEARCH,
        TaskType.CODE_PATCH,
        TaskType.TOOL_ROUTE,
        TaskType.MULTI_STEP,
        TaskType.CLASSIFY,
    ],
)
def test_generate_job_stable_in_process(task_type: TaskType) -> None:
    j1 = generate_job(task_type, seed=7)
    j2 = generate_job(task_type, seed=7)
    assert _deterministic_view(j1.model_dump(mode="json")) == _deterministic_view(
        j2.model_dump(mode="json")
    )


def test_generate_job_differs_by_seed() -> None:
    # Sanity: different seeds should pick different fixtures (sometimes).
    seen = {generate_job(TaskType.RESEARCH, seed=s).prompt for s in range(20)}
    assert len(seen) > 1
