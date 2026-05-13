"""Cathedral v2 — verifiable agentic workforce.

This package is the rewrite of the Cathedral subnet around a generalized
agentic loop with first-class trajectory capture. v1 lives at the parent
`cathedral.*` package and is untouched.

See docs/v2/ARCHITECTURE.md for the full design.
"""

from cathedral.v2.types import (
    AgentResult,
    DistillationReadiness,
    FailureClass,
    JobSpec,
    Receipt,
    ScoreParts,
    TaskType,
    ToolCall,
    Trajectory,
    Weights,
)

__all__ = [
    "AgentResult",
    "DistillationReadiness",
    "FailureClass",
    "JobSpec",
    "Receipt",
    "ScoreParts",
    "TaskType",
    "ToolCall",
    "Trajectory",
    "Weights",
]
