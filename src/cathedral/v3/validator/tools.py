"""Tool implementations bound per-job.

Tools here are the *real* logic that runs when a miner asks the ToolBus to
perform an action. Each task type binds its own set; the ToolBus then
routes the miner's calls into these handlers.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cathedral.v3.types import JobSpec, TaskType


def build_handlers(job: JobSpec) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Return the ToolBus handler map for this job."""
    if job.task_type is TaskType.RESEARCH:
        return _research_tools(job)
    if job.task_type is TaskType.CODE_PATCH:
        return _code_patch_tools(job)
    if job.task_type is TaskType.TOOL_ROUTE:
        return _tool_route_tools(job)
    if job.task_type is TaskType.MULTI_STEP:
        return _multi_step_tools(job)
    if job.task_type is TaskType.CLASSIFY:
        return _classify_tools(job)
    return {}


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


def _research_tools(job: JobSpec) -> dict[str, Callable]:
    passages: list[dict] = list(job.context.get("passages", []))
    cited: list[str] = []

    def search_corpus(args: dict) -> list[dict]:
        q = (args.get("query") or "").lower()
        if not q:
            return [{"id": p["id"], "text": p["text"]} for p in passages]
        terms = [t for t in q.split() if t]
        ranked = []
        for p in passages:
            hits = sum(1 for t in terms if t in p["text"].lower())
            if hits:
                ranked.append((hits, p))
        ranked.sort(key=lambda x: -x[0])
        return [{"id": p["id"], "text": p["text"]} for _, p in ranked[:5]]

    def cite(args: dict) -> dict:
        pid = args.get("passage_id", "")
        if not any(p["id"] == pid for p in passages):
            return {"ok": False, "error": f"unknown passage id: {pid}"}
        if pid not in cited:
            cited.append(pid)
        return {"ok": True, "cited": list(cited)}

    return {
        "search_corpus": search_corpus,
        "cite": cite,
        "__sink_cited__": lambda _a: list(cited),  # internal: validator reads after run
    }


# ---------------------------------------------------------------------------
# code_patch
# ---------------------------------------------------------------------------


def _code_patch_tools(job: JobSpec) -> dict[str, Callable]:
    source = job.context["source"]
    test = job.context["failing_test"]
    state = {"source": source, "patched": None, "test_result": None, "submitted_diff": None}

    def read_file(_args: dict) -> dict:
        return {"filename": job.context["source_filename"], "content": state["source"]}

    def apply_patch(args: dict) -> dict:
        diff = (args.get("diff") or "").strip()
        if not diff:
            return {"ok": False, "error": "empty diff"}
        state["submitted_diff"] = diff
        new_src = _apply_unified_diff(state["source"], diff)
        if new_src is None:
            return {"ok": False, "error": "patch did not apply"}
        state["patched"] = new_src
        return {"ok": True, "patched_bytes": len(new_src)}

    def run_test(_args: dict) -> dict:
        src = state["patched"] or state["source"]
        ok, err = _run_python_test(src, test)
        state["test_result"] = {"passed": ok, "error": err}
        return state["test_result"]

    handlers = {
        "read_file": read_file,
        "apply_patch": apply_patch,
        "run_test": run_test,
        "__sink_state__": lambda _a: dict(state),
    }
    return handlers


def _apply_unified_diff(source: str, diff: str) -> str | None:
    """Tolerant unified-diff applier. Supports single-hunk patches.

    Looks for '-' lines in `source` in order and replaces with '+' lines.
    Context lines and headers are ignored. Good enough for our fixtures and
    forgiving of model output that drops the `@@` line.
    """
    minus_lines: list[str] = []
    plus_lines: list[str] = []
    for ln in diff.splitlines():
        if ln.startswith("---") or ln.startswith("+++") or ln.startswith("@@"):
            continue
        if ln.startswith("-"):
            minus_lines.append(ln[1:])
        elif ln.startswith("+"):
            plus_lines.append(ln[1:])
        # context lines we just drop

    if not minus_lines and not plus_lines:
        return None

    src_lines = source.splitlines(keepends=False)
    out: list[str] = []
    i = 0
    consumed = False
    while i < len(src_lines):
        # try to match the minus block here
        if not consumed and minus_lines and _block_match(src_lines, i, minus_lines):
            out.extend(plus_lines)
            i += len(minus_lines)
            consumed = True
        else:
            out.append(src_lines[i])
            i += 1
    if not consumed and not minus_lines:
        # pure addition — append
        out.extend(plus_lines)
        consumed = True
    if not consumed:
        return None
    return "\n".join(out) + ("\n" if source.endswith("\n") else "")


def _block_match(src: list[str], start: int, block: list[str]) -> bool:
    if start + len(block) > len(src):
        return False
    return all(src[start + j] == block[j] for j in range(len(block)))


_MODULE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_FIXTURE_TIMEOUT_SECONDS = 15


def _parse_module_name(test_source: str) -> str:
    """Extract the module-under-test name from the first import line.

    Strict: must be a single bare identifier (must start with a letter, no
    dunders, no dots, no stdlib shadowing). Anything else falls back to a
    fixed name. We never let an attacker-controlled string become a
    filename.
    """
    stdlib_block = {"os", "sys", "subprocess", "pathlib", "io", "typing", "tempfile"}
    for ln in test_source.splitlines():
        ln = ln.strip()
        first = None
        if ln.startswith("from "):
            parts = ln.split()
            if len(parts) >= 2:
                first = parts[1]
        elif ln.startswith("import "):
            parts = ln.split()
            if len(parts) >= 2:
                first = parts[1].split(",")[0].strip()
        if first and _MODULE_NAME_RE.match(first) and first not in stdlib_block:
            return first
    return "module_under_test"


def _run_python_test(source: str, test: str) -> tuple[bool, str | None]:
    """FIXTURE-ONLY: run a v3 fixture's `failing_test` against a candidate source.

    This handler exists to score the v3 ``code_patch`` task on the curated
    fixtures in ``cathedral/v3/jobs/fixtures.py``. It is NOT a sandbox; it
    is NOT a generic code execution service; and it is NOT exposed to
    arbitrary miner-supplied test source. Both `source` and `test` come
    from the validator-owned fixture set; only `source` may be modified by
    the miner (via apply_patch's tolerant unified-diff applier).

    Guardrails:
      - subprocess.run with a list argv (never shell=True)
      - written into a fresh tempfile.TemporaryDirectory (cleaned on exit)
      - hard wall-clock timeout (TimeoutExpired -> failure)
      - module name parsed strictly (single bare identifier, no path chars,
        no stdlib shadowing) before being used as a filename
    """
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        module = _parse_module_name(test)
        (td_path / f"{module}.py").write_text(source)
        (td_path / "_test_runner.py").write_text(test)
        try:
            r = subprocess.run(
                [sys.executable, "_test_runner.py"],
                cwd=td_path,
                capture_output=True,
                text=True,
                timeout=_FIXTURE_TIMEOUT_SECONDS,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return False, "timeout"
        if r.returncode == 0:
            return True, None
        tail = (r.stderr or r.stdout or "non-zero exit").strip().splitlines()
        return False, (tail[-1][:200] if tail else "non-zero exit")


# ---------------------------------------------------------------------------
# tool_route
# ---------------------------------------------------------------------------


def _tool_route_tools(job: JobSpec) -> dict[str, Callable]:
    chosen: dict[str, Any] = {"tool": None, "args": None}

    def make(name: str):
        def _h(args: dict) -> dict:
            chosen["tool"] = name
            chosen["args"] = args
            return {"ok": True, "tool": name, "args": args}

        return _h

    handlers = {td.name: make(td.name) for td in job.tools}
    handlers["__sink_chosen__"] = lambda _a: dict(chosen)
    return handlers


# ---------------------------------------------------------------------------
# multi_step
# ---------------------------------------------------------------------------


def _multi_step_tools(job: JobSpec) -> dict[str, Callable]:
    kv: dict[str, str] = dict(job.context.get("initial_state", {}))
    done_called = {"flag": False}
    # tiny fake search index keyed off goal hints
    search_index = {
        "cathedral verifier": [
            {"url": "https://cathedral.computer/docs/verifier", "title": "Cathedral Verifier"},
        ],
    }

    def kv_get(args: dict) -> dict:
        k = args.get("key", "")
        return {"key": k, "value": kv.get(k), "exists": k in kv}

    def kv_set(args: dict) -> dict:
        k = args.get("key", "")
        v = args.get("value", "")
        kv[k] = str(v)
        return {"ok": True, "key": k}

    def kv_list(args: dict) -> dict:
        prefix = args.get("prefix", "")
        return {"keys": [k for k in kv if k.startswith(prefix)]}

    def search(args: dict) -> dict:
        q = (args.get("query") or "").lower()
        for k, v in search_index.items():
            if k in q or q in k:
                return {"results": v}
        return {"results": []}

    def done(_args: dict) -> dict:
        done_called["flag"] = True
        return {"ok": True}

    handlers = {
        "kv_get": kv_get,
        "kv_set": kv_set,
        "kv_list": kv_list,
        "search": search,
        "done": done,
        "__sink_state__": lambda _a: {"kv": dict(kv), "done": done_called["flag"]},
    }
    return handlers


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def _classify_tools(job: JobSpec) -> dict[str, Callable]:
    chosen = {"label": None}

    def label(args: dict) -> dict:
        chosen["label"] = args.get("label")
        return {"ok": True, "label": chosen["label"]}

    return {
        "label": label,
        "__sink_chosen__": lambda _a: dict(chosen),
    }


__all__ = ["build_handlers"]
