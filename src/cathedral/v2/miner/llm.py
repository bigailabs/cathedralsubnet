"""LLM miner — OpenAI-compatible chat API with a ReAct-style tool loop.

Gracefully degrades when no API key is set: falls back to a small
deterministic stub so the loop still runs end-to-end. Wire to a real
Chutes (or any OpenAI-compatible) endpoint via env:

    CATHEDRAL_V2_LLM_BASE_URL   default: https://llm.chutes.ai/v1
    CATHEDRAL_V2_LLM_API_KEY    required for live calls
    CATHEDRAL_V2_LLM_MODEL      default: MiniMax-M2.5-TEE
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from cathedral.v2.types import AgentResult, JobSpec
from cathedral.v2.validator.toolbus import ToolBus, ToolError


_SYSTEM_PROMPT = """You are a Cathedral agent. You solve one job per session.

You may call tools by emitting a single JSON object on its own line in
the following form:

    {"tool": "<name>", "args": {...}}

When you have a final answer, emit:

    {"final": "<answer text>", "structured": {...}}

Use tools sparingly. Always cite for research tasks. Always run the
test for code patches. Always call `done` to finish multi-step jobs.
"""


class LLMAgent:
    kind = "llm"

    def __init__(
        self,
        hotkey: str = "llm_agent",
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_steps: int = 10,
    ) -> None:
        self.hotkey = hotkey
        self.base_url = base_url or os.environ.get(
            "CATHEDRAL_V2_LLM_BASE_URL", "https://llm.chutes.ai/v1"
        )
        self.api_key = api_key or os.environ.get("CATHEDRAL_V2_LLM_API_KEY")
        self.model = model or os.environ.get("CATHEDRAL_V2_LLM_MODEL", "MiniMax-M2.5-TEE")
        self.max_steps = max_steps

    async def run(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        if not self.api_key:
            # no key — fall back to a tiny deterministic stub that still uses
            # the tool bus, so the trajectory is shaped correctly
            return await self._fallback(job, tools)

        try:
            return await self._react_loop(job, tools)
        except Exception as e:
            return AgentResult(final_output="", agent_error=f"llm_error: {e}")

    # -- live path -------------------------------------------------------

    async def _react_loop(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        import httpx

        messages: list[dict] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _format_user_prompt(job, tools.available),
            },
        ]
        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(self.max_steps):
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "messages": messages, "temperature": 0.2},
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"] or ""
                messages.append({"role": "assistant", "content": content})

                action = _parse_action(content)
                if action is None:
                    # no actionable JSON — treat the raw text as final
                    return AgentResult(final_output=content.strip(), model_id=self.model)

                if "final" in action:
                    return AgentResult(
                        final_output=str(action["final"]),
                        structured=action.get("structured", {}) or {},
                        model_id=self.model,
                    )

                tool = action.get("tool")
                args = action.get("args", {}) or {}
                try:
                    result = await tools.call(tool, args)
                except ToolError as e:
                    messages.append(
                        {"role": "user", "content": f"tool error: {e}"}
                    )
                    continue
                messages.append(
                    {"role": "user", "content": f"tool result: {json.dumps(result, default=str)[:1200]}"}
                )
        return AgentResult(
            final_output="",
            model_id=self.model,
            agent_error="exceeded max_steps without final answer",
        )

    # -- fallback path ---------------------------------------------------

    async def _fallback(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        # use the heuristic miner's logic as a degraded but-shaped path
        from cathedral.v2.miner.heuristic import HeuristicAgent

        h = HeuristicAgent(hotkey=self.hotkey)
        r = await h.run(job, tools)
        # mark as llm-fallback so analytics can split it out
        return AgentResult(
            final_output=r.final_output,
            structured={**r.structured, "strategy": "llm_fallback_no_key"},
            artifacts=r.artifacts,
            model_id=f"{self.model} (no key — fallback)",
            agent_error=r.agent_error,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _format_user_prompt(job: JobSpec, available_tools: list[str]) -> str:
    ctx = {k: v for k, v in job.context.items() if k != "expected_patch"}
    tool_lines = []
    for td in job.tools:
        tool_lines.append(f"  - {td.name}: {td.description}")
    return (
        f"task_type: {job.task_type.value}\n"
        f"prompt: {job.prompt}\n\n"
        f"available tools (call only these):\n" + "\n".join(tool_lines) + "\n\n"
        f"context:\n{json.dumps(ctx, indent=2, default=str)[:4000]}\n"
    )


_ACTION_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def _parse_action(content: str) -> dict | None:
    # find the last JSON-looking blob in the content
    matches = _ACTION_RE.findall(content)
    for m in reversed(matches):
        try:
            obj = json.loads(m)
            if isinstance(obj, dict) and ("tool" in obj or "final" in obj):
                return obj
        except json.JSONDecodeError:
            continue
    return None
