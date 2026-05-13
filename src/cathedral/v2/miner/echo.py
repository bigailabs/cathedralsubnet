"""Baseline echo miner — produces a useful zero-effort trajectory."""

from __future__ import annotations

from cathedral.v2.types import AgentResult, JobSpec
from cathedral.v2.validator.toolbus import ToolBus


class EchoAgent:
    kind = "echo"

    def __init__(self, hotkey: str = "echo_agent") -> None:
        self.hotkey = hotkey

    async def run(self, job: JobSpec, tools: ToolBus) -> AgentResult:  # noqa: ARG002
        return AgentResult(
            final_output=job.prompt,
            structured={"strategy": "echo"},
            model_id="echo",
        )
