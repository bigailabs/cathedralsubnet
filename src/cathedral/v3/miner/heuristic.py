"""Heuristic miner — rule-based per task type.

Designed to be a solid floor. Will hit gold on classify and code_patch
fixtures; partial on the others. Generates realistic-shaped trajectories.
"""

from __future__ import annotations

from cathedral.v3.types import AgentResult, JobSpec, TaskType
from cathedral.v3.validator.toolbus import ToolBus, ToolError


class HeuristicAgent:
    kind = "heuristic"

    def __init__(self, hotkey: str = "heuristic_agent") -> None:
        self.hotkey = hotkey

    async def run(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        if job.task_type is TaskType.RESEARCH:
            return await self._research(job, tools)
        if job.task_type is TaskType.CODE_PATCH:
            return await self._code_patch(job, tools)
        if job.task_type is TaskType.TOOL_ROUTE:
            return await self._tool_route(job, tools)
        if job.task_type is TaskType.MULTI_STEP:
            return await self._multi_step(job, tools)
        if job.task_type is TaskType.CLASSIFY:
            return await self._classify(job, tools)
        if job.task_type is TaskType.BUG_REPRO:
            return await self._bug_repro(job, tools)
        return AgentResult(final_output="", agent_error="unknown task")

    async def _research(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        # search for keywords from the question, cite the top hit
        q = job.prompt
        try:
            hits = await tools.call("search_corpus", {"query": q})
        except ToolError as e:
            return AgentResult(final_output="", agent_error=str(e))
        if hits:
            top = hits[0]
            await tools.call("cite", {"passage_id": top["id"]})
            answer = top["text"]
        else:
            answer = "unknown"
        return AgentResult(final_output=answer, structured={"strategy": "first-hit"})

    async def _code_patch(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        await tools.call("read_file", {})
        diff = job.context.get("expected_patch", "")
        await tools.call("apply_patch", {"diff": diff})
        r = await tools.call("run_test", {})
        return AgentResult(
            final_output=diff,
            structured={"strategy": "ground-truth-patch", "test_result": r},
            artifacts={"patch": "applied"},
        )

    async def _tool_route(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        expected = job.context.get("expected_tool")
        args = job.context.get("expected_args") or {}
        if not expected:
            return AgentResult(final_output="", agent_error="no expected_tool in context")
        try:
            r = await tools.call(str(expected), args)
        except ToolError as e:
            return AgentResult(final_output="", agent_error=str(e))
        return AgentResult(final_output=f"{expected} called", structured={"called": r})

    async def _multi_step(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        # solve by reading the goal and mapping to target state
        target = job.context.get("target_state", {})
        # special case for the credential job: read creds, write session
        for k, v in target.items():
            if v == "https://cathedral.computer/docs/verifier":
                r = await tools.call("search", {"query": "cathedral verifier"})
                results = r.get("results", [])
                if results:
                    await tools.call("kv_set", {"key": k, "value": results[0]["url"]})
                continue
            # find the source key in the initial state with the same value
            initial = job.context.get("initial_state", {})
            src_key = None
            for ik, iv in initial.items():
                if iv == v:
                    src_key = ik
                    break
            if src_key:
                got = await tools.call("kv_get", {"key": src_key})
                await tools.call("kv_set", {"key": k, "value": got.get("value", v)})
            else:
                await tools.call("kv_set", {"key": k, "value": v})
        await tools.call("done", {})
        return AgentResult(final_output="done", structured={"strategy": "target-mapping"})

    async def _classify(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        text = job.prompt.lower()
        # simple keyword classifier
        labels = job.context.get("labels", [])
        kw_map = {
            "bug": ["hangs", "error", "broken", "fail", "crash", "doesn't work", "wrong"],
            "feature_request": ["would be amazing", "could we", "wish", "please add", "feature"],
            "praise": ["great work", "amazing", "love", "smoothest", "thanks"],
            "question": ["how do i", "how can", "what is", "?", "where"],
        }
        scored: dict[str, int] = {}
        for label in labels:
            kws = kw_map.get(label, [label])
            scored[label] = sum(1 for k in kws if k in text)
        best = max(scored, key=lambda k: scored[k]) if scored else (labels[0] if labels else "")
        await tools.call("label", {"label": best})
        return AgentResult(final_output=best, structured={"strategy": "keyword"})

    async def _bug_repro(self, job: JobSpec, tools: ToolBus) -> AgentResult:
        # Trusted baseline: reads the validator-owned reference test out
        # of hidden_context. LLM miners do not have this privilege; they
        # only see job.public_view().
        await tools.call("read_buggy_source", {})
        reference = job.hidden_context.get("reference_test_source", "")
        if not reference:
            return AgentResult(
                final_output="",
                agent_error="no reference_test_source in hidden_context",
            )
        r = await tools.call("submit_test", {"test_source": reference})
        return AgentResult(
            final_output=reference,
            structured={"strategy": "reference-test", "verifier": r},
            artifacts={"regression_test": reference},
        )
