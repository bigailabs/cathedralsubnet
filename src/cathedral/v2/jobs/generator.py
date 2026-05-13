"""Deterministic job generators.

Given a (task_type, seed) pair, generate_job() returns a JobSpec. The
generator is intentionally small and stub-heavy: real corpora and real
test suites can be plugged in later via the same JobSpec.context shape.
"""

from __future__ import annotations

import random

from cathedral.v2.jobs.fixtures import (
    CLASSIFY_FIXTURES,
    CODE_PATCH_FIXTURES,
    MULTI_STEP_WORLDS,
    RESEARCH_CORPUS,
    TOOL_ROUTE_FIXTURES,
)
from cathedral.v2.types import JobSpec, TaskType, ToolDescriptor


def available_task_types() -> list[TaskType]:
    return list(TaskType)


def generate_job(task_type: TaskType, seed: int = 0) -> JobSpec:
    rng = random.Random(seed * 1009 + hash(task_type.value) & 0xFFFFFFFF)
    if task_type is TaskType.RESEARCH:
        return _research(rng, seed)
    if task_type is TaskType.CODE_PATCH:
        return _code_patch(rng, seed)
    if task_type is TaskType.TOOL_ROUTE:
        return _tool_route(rng, seed)
    if task_type is TaskType.MULTI_STEP:
        return _multi_step(rng, seed)
    if task_type is TaskType.CLASSIFY:
        return _classify(rng, seed)
    raise ValueError(f"unknown task type: {task_type}")


class JobGenerator:
    """Generates a balanced batch each tick."""

    def __init__(self, task_types: list[TaskType] | None = None) -> None:
        self.task_types = task_types or available_task_types()
        self._seed_counter = 0

    def tick(self) -> list[JobSpec]:
        jobs = []
        for tt in self.task_types:
            jobs.append(generate_job(tt, seed=self._seed_counter))
            self._seed_counter += 1
        return jobs


# ---------------------------------------------------------------------------
# per-task-type generators
# ---------------------------------------------------------------------------


def _research(rng: random.Random, seed: int) -> JobSpec:
    item = rng.choice(RESEARCH_CORPUS)
    return JobSpec(
        task_type=TaskType.RESEARCH,
        prompt=item["question"],
        context={
            "corpus_id": item["corpus_id"],
            "passages": item["passages"],
            "ground_truth_answer": item["answer"],
            "required_citations": item["required_citations"],
        },
        tools=[
            ToolDescriptor(
                name="search_corpus",
                description="Search the provided corpus for relevant passages.",
                args_schema={"query": "string"},
            ),
            ToolDescriptor(
                name="cite",
                description="Record a citation. Pass the passage_id.",
                args_schema={"passage_id": "string"},
            ),
        ],
        expected_artifacts=["final_answer", "citations"],
        rubric_id="research_v1",
        seed=seed,
    )


def _code_patch(rng: random.Random, seed: int) -> JobSpec:
    item = rng.choice(CODE_PATCH_FIXTURES)
    return JobSpec(
        task_type=TaskType.CODE_PATCH,
        prompt=item["task"],
        context={
            "source_filename": item["filename"],
            "source": item["source"],
            "failing_test": item["failing_test"],
            "expected_patch": item["expected_patch"],
        },
        tools=[
            ToolDescriptor(
                name="read_file",
                description="Read the source file.",
                args_schema={},
            ),
            ToolDescriptor(
                name="apply_patch",
                description="Submit a unified diff against the source file.",
                args_schema={"diff": "string"},
            ),
            ToolDescriptor(
                name="run_test",
                description="Run the failing test against the patched source.",
                args_schema={},
            ),
        ],
        expected_artifacts=["patch", "test_result"],
        rubric_id="code_patch_v1",
        seed=seed,
    )


def _tool_route(rng: random.Random, seed: int) -> JobSpec:
    item = rng.choice(TOOL_ROUTE_FIXTURES)
    return JobSpec(
        task_type=TaskType.TOOL_ROUTE,
        prompt=item["goal"],
        context={
            "expected_tool": item["expected_tool"],
            "expected_args": item["expected_args"],
        },
        tools=[
            ToolDescriptor(name=t["name"], description=t["description"])
            for t in item["available_tools"]
        ],
        expected_artifacts=["chosen_tool", "chosen_args"],
        rubric_id="tool_route_v1",
        seed=seed,
    )


def _multi_step(rng: random.Random, seed: int) -> JobSpec:
    item = rng.choice(MULTI_STEP_WORLDS)
    return JobSpec(
        task_type=TaskType.MULTI_STEP,
        prompt=item["goal"],
        context={
            "initial_state": item["initial_state"],
            "target_state": item["target_state"],
            "min_steps": item["min_steps"],
            "max_steps": item["max_steps"],
        },
        tools=[
            ToolDescriptor(
                name="kv_get",
                description="Get a key from the KV store.",
                args_schema={"key": "string"},
            ),
            ToolDescriptor(
                name="kv_set",
                description="Set a key in the KV store.",
                args_schema={"key": "string", "value": "string"},
            ),
            ToolDescriptor(
                name="kv_list",
                description="List keys with a prefix.",
                args_schema={"prefix": "string"},
            ),
            ToolDescriptor(
                name="search",
                description="Search the fake search index.",
                args_schema={"query": "string"},
            ),
            ToolDescriptor(
                name="done",
                description="Signal completion.",
                args_schema={},
            ),
        ],
        expected_artifacts=["final_state"],
        rubric_id="multi_step_v1",
        seed=seed,
        deadline_seconds=120.0,
    )


def _classify(rng: random.Random, seed: int) -> JobSpec:
    item = rng.choice(CLASSIFY_FIXTURES)
    return JobSpec(
        task_type=TaskType.CLASSIFY,
        prompt=item["text"],
        context={
            "labels": item["labels"],
            "expected_label": item["expected_label"],
            "task_description": item["task_description"],
        },
        tools=[
            ToolDescriptor(
                name="label",
                description="Submit your chosen label.",
                args_schema={"label": "string"},
            ),
        ],
        expected_artifacts=["chosen_label"],
        rubric_id="classify_v1",
        seed=seed,
        deadline_seconds=20.0,
    )


__all__ = ["JobGenerator", "available_task_types", "generate_job"]
