"""Cathedral v3 — verifiable agentic workforce, trajectory data substrate.

This package is the v3 spike of the Cathedral subnet built around a
generalized agentic loop with first-class trajectory capture. It is the
data substrate that v3 coding-job families (bug_repro, test_gen, ...)
will land on top of. v1 lives at the parent `cathedral.*` package and is
untouched.

See docs/v3/ARCHITECTURE.md for the full design.
"""

from cathedral.v3.types import (
    AgentResult,
    CodingFailureClass,
    DistillationReadiness,
    FailureClass,
    JobSpec,
    Receipt,
    ScoreParts,
    TaskSplit,
    TaskType,
    ToolCall,
    Trajectory,
    Weights,
)

__all__ = [
    "AgentResult",
    "CodingFailureClass",
    "DistillationReadiness",
    "FailureClass",
    "JobSpec",
    "Receipt",
    "ScoreParts",
    "TaskSplit",
    "TaskType",
    "ToolCall",
    "Trajectory",
    "Weights",
]
