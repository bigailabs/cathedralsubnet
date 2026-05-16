"""MinerAgent protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from cathedral.v3.types import AgentResult, JobSpec

if TYPE_CHECKING:  # avoid the validator -> miner -> validator cycle at import time
    from cathedral.v3.validator.toolbus import ToolBus


class MinerAgent(Protocol):
    hotkey: str
    kind: str

    async def run(self, job: JobSpec, tools: ToolBus) -> AgentResult: ...
