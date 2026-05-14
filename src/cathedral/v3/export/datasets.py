"""SFT / DPO / RM dataset exporters.

Each writes a JSONL file plus a sibling manifest.json with the filter,
row count, and BLAKE3 hash of every row's canonical bytes. The manifest
is signed by the configured ReceiptSigner if one is provided.

Hidden-field firewall
---------------------
The `prompt_visible_to_miner` helper is the single source of truth for
what reaches a training prompt. It pulls ONLY from `job.public_view()`,
so anything stashed under `job.hidden_context` (fixed_source, expected
symptom, reference_test_source, mutation seeds, ...) cannot leak in.

Tool-call traces in SFT rows are also filtered: results from validator-
owned oracle handlers (anything sink-flagged via `__sink_` or returning
verifier metrics like fails_on_buggy / passes_on_fixed / symptom_match)
are scrubbed before serialization. See `_sanitize_tool_result`.

By default, only trajectories whose `job.task_split` is in
{TRAIN_EXPORTABLE, PUBLIC_LEADERBOARD} are exported. HELDOUT_EVAL and
OPERATOR_REVIEW trajectories are excluded; they must be promoted by an
operator before they can flow into training data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import blake3

from cathedral.v3.archive import TrajectoryArchive
from cathedral.v3.receipt import ReceiptSigner
from cathedral.v3.types import (
    DistillationReadiness,
    TaskSplit,
    TaskType,
    ToolCall,
    Trajectory,
    canonical_json,
)

_DEFAULT_EXPORTABLE_SPLITS: frozenset[TaskSplit] = frozenset(
    {TaskSplit.TRAIN_EXPORTABLE, TaskSplit.PUBLIC_LEADERBOARD}
)

# Tool results from these handlers are validator-owned oracle output.
# Their contents must never appear in training rows; we replace them
# with a sentinel "<oracle-output>" if they would.
_ORACLE_RESULT_KEYS: frozenset[str] = frozenset(
    {
        "fails_on_buggy",
        "passes_on_fixed",
        "symptom_match",
        "sandbox_backend",
    }
)


def export_sft(
    archive: TrajectoryArchive,
    out_path: Path,
    task_type: TaskType | None = None,
    signer: ReceiptSigner | None = None,
    min_score: float = 0.85,
    limit: int = 100_000,
    allowed_splits: frozenset[TaskSplit] | None = None,
) -> dict:
    """Write `sft.jsonl`: only gold (or score≥min_score) trajectories.

    `allowed_splits` defaults to {TRAIN_EXPORTABLE, PUBLIC_LEADERBOARD}.
    Coding-job alphas default to OPERATOR_REVIEW and are therefore
    excluded until an operator promotes them.
    """
    splits = allowed_splits or _DEFAULT_EXPORTABLE_SPLITS

    def is_gold(t: Trajectory) -> bool:
        if not _is_exportable(t, splits):
            return False
        if t.score.weighted < min_score:
            return False
        if t.score.readiness == DistillationReadiness.NEGATIVE:
            return False
        if task_type and t.job.task_type != task_type:
            return False
        return True

    def rows() -> Iterable[dict]:
        for t in archive.iter_all():
            if not is_gold(t):
                continue
            yield _sft_row(t)

    return _write_jsonl(
        out_path,
        rows(),
        format="sft",
        filter_={
            "min_score": min_score,
            "task_type": task_type.value if task_type else None,
            "allowed_splits": sorted(s.value for s in splits),
        },
        signer=signer,
        limit=limit,
    )


def export_dpo(
    archive: TrajectoryArchive,
    out_path: Path,
    task_type: TaskType | None = None,
    signer: ReceiptSigner | None = None,
    min_delta: float = 0.20,
    limit: int = 50_000,
    allowed_splits: frozenset[TaskSplit] | None = None,
) -> dict:
    """Write `dpo.jsonl` preference pairs (chosen vs. rejected).

    Both sides of a pair must pass the split filter.
    """
    splits = allowed_splits or _DEFAULT_EXPORTABLE_SPLITS
    pairs = archive.preference_pairs(task_type=task_type, limit=limit, min_delta=min_delta)

    def rows() -> Iterable[dict]:
        for p in pairs:
            winner = archive.get(p.winner_trajectory_id)
            loser = archive.get(p.loser_trajectory_id)
            if not winner or not loser:
                continue
            if not _is_exportable(winner, splits) or not _is_exportable(loser, splits):
                continue
            # The pair shares the same job, so both trajectories share
            # the same hidden_context.
            hidden = _collect_hidden_strings(winner)
            yield {
                "prompt": prompt_visible_to_miner(winner),
                "chosen": _scrub_text(winner.result.final_output, hidden),
                "rejected": _scrub_text(loser.result.final_output, hidden),
                "score_delta": round(p.score_delta, 4),
                "task_type": winner.job.task_type.value,
                "winner_trajectory_id": winner.trajectory_id,
                "loser_trajectory_id": loser.trajectory_id,
            }

    return _write_jsonl(
        out_path,
        rows(),
        format="dpo",
        filter_={
            "task_type": task_type.value if task_type else None,
            "min_delta": min_delta,
            "allowed_splits": sorted(s.value for s in splits),
        },
        signer=signer,
        limit=limit,
    )


def export_rm(
    archive: TrajectoryArchive,
    out_path: Path,
    task_type: TaskType | None = None,
    signer: ReceiptSigner | None = None,
    limit: int = 100_000,
    allowed_splits: frozenset[TaskSplit] | None = None,
) -> dict:
    """Write `rm.jsonl`: every trajectory with score + dimensions."""
    splits = allowed_splits or _DEFAULT_EXPORTABLE_SPLITS

    def rows() -> Iterable[dict]:
        for t in archive.iter_all():
            if not _is_exportable(t, splits):
                continue
            if task_type and t.job.task_type != task_type:
                continue
            hidden = _collect_hidden_strings(t)
            yield {
                "prompt": prompt_visible_to_miner(t),
                "completion": _scrub_text(t.result.final_output, hidden),
                "score": t.score.weighted,
                "dimensions": t.score.dimensions,
                "task_type": t.job.task_type.value,
                "miner_kind": t.miner_kind,
                "trajectory_id": t.trajectory_id,
            }

    return _write_jsonl(
        out_path,
        rows(),
        format="rm",
        filter_={
            "task_type": task_type.value if task_type else None,
            "allowed_splits": sorted(s.value for s in splits),
        },
        signer=signer,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sft_row(t: Trajectory) -> dict:
    """SFT row in OpenAI chat format.

    Includes the tool trace so the fine-tuned model learns tool use.
    Three sanitizers run on every output string before it lands in a
    training row:
      - `_sanitize_tool_args(args, hidden)` scrubs hidden oracle
        content out of tool args (e.g. `submit_test.test_source` that
        copied `hidden_context.reference_test_source` verbatim).
      - `_sanitize_tool_result_values(result, hidden)` scrubs hidden
        content out of tool results AND replaces known oracle-output
        keys with `<oracle-output>` so the boolean signal is gone.
      - `_scrub_text(final_output, hidden)` scrubs the final assistant
        message for verbatim or substring matches against any hidden
        oracle string.
    """
    hidden = _collect_hidden_strings(t)
    messages: list[dict] = [
        {"role": "system", "content": "You are a Cathedral agent. Solve the job."},
        {"role": "user", "content": prompt_visible_to_miner(t)},
    ]
    for tc in t.tool_calls:
        if tc.tool_name.startswith("__"):
            continue
        sanitized_args = _sanitize_tool_args(tc.args, hidden)
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    {"tool": tc.tool_name, "args": sanitized_args},
                    sort_keys=True,
                    default=str,
                ),
            }
        )
        if tc.ok:
            sanitized_result = _sanitize_tool_result_values(tc.result, hidden)
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(sanitized_result, default=str, sort_keys=True)[:2000],
                }
            )
        else:
            messages.append({"role": "tool", "content": f"error: {tc.error}"})
    messages.append({"role": "assistant", "content": _scrub_text(t.result.final_output, hidden)})
    return {
        "messages": messages,
        "task_type": t.job.task_type.value,
        "score": t.score.weighted,
        "trajectory_id": t.trajectory_id,
        "miner_kind": t.miner_kind,
    }


def _prompt_for(t: Trajectory) -> str:
    """Legacy shim: returns the miner-visible prompt only.

    Kept as an alias for `prompt_visible_to_miner` so existing callers
    do not regress. New code should call the public name directly.
    """
    return prompt_visible_to_miner(t)


def prompt_visible_to_miner(t: Trajectory) -> str:
    """The training-prompt projection.

    Returns only `JobSpec.prompt`, which is a public-view field by
    construction. Hidden context (fixed_source, oracles, mutation seeds,
    reference tests) lives on `job.hidden_context` and is unreachable
    from this function.
    """
    return t.job.prompt


def _is_exportable(t: Trajectory, allowed_splits: frozenset[TaskSplit]) -> bool:
    # Belt-and-braces: HELDOUT_EVAL is NEVER exportable, even if an
    # operator passes `allowed_splits={TaskSplit.HELDOUT_EVAL}` by
    # accident. A held-out trajectory in any training row would
    # invalidate the eval set.
    if t.job.task_split is TaskSplit.HELDOUT_EVAL:
        return False
    return t.job.task_split in allowed_splits


def _sanitize_tool_result(call: ToolCall) -> object:
    """Scrub oracle output before placing a tool result in a training row.

    If the result is a dict that contains any oracle-only key, replace
    those keys with a sentinel string. If the tool name itself is an
    internal sink (starts with `__`), the caller should drop it
    entirely; this function does not handle that case (kept by the
    caller).
    """
    raw = call.result
    if isinstance(raw, dict):
        return {k: ("<oracle-output>" if k in _ORACLE_RESULT_KEYS else v) for k, v in raw.items()}
    return raw


# ---------------------------------------------------------------------------
# hidden-content sanitizer
# ---------------------------------------------------------------------------
#
# The earlier firewall scrubbed oracle *result values* but not the oracle
# content that a miner might copy into its outputs:
#  - tool args (especially `submit_test.test_source` for bug_repro, into
#    which the privileged heuristic miner pastes
#    `hidden_context.reference_test_source`)
#  - final assistant output (the heuristic also returns it as final_output)
#  - DPO chosen/rejected
#  - RM completion
#
# This sanitizer walks any string field through `_scrub_text(s, hidden)`,
# which replaces any whole-string or substring match against the set of
# hidden-context strings with a sentinel. We deliberately err on the side
# of over-redaction for coding-job exports: if a miner output happens to
# coincide with an oracle string, it gets scrubbed.

_HIDDEN_SENTINEL = "<hidden-oracle-content>"
# A miner output shorter than this is treated as too small to risk a
# substring match against (would scrub legitimate words like "from"). The
# bug_repro oracle strings are multi-line tests and full source files, so
# anything below this length cannot meaningfully reveal them.
_MIN_HIDDEN_SUBSTRING_LEN = 24


def _collect_hidden_strings(t: Trajectory) -> list[str]:
    """Flatten every string leaf in `job.hidden_context`.

    Returns a list ordered from longest to shortest so that scrubbing
    matches the most specific (and longest) hidden string first.
    """
    out: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            if node.strip():
                out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list | tuple):
            for v in node:
                walk(v)

    walk(t.job.hidden_context)
    # Sort longest first so we don't prematurely match a short prefix.
    out.sort(key=len, reverse=True)
    return out


def _scrub_text(text: str | None, hidden: list[str]) -> str | None:
    if text is None or not isinstance(text, str) or not text:
        return text
    scrubbed = text
    for h in hidden:
        if not h:
            continue
        if h == scrubbed:
            return _HIDDEN_SENTINEL
        if len(h) >= _MIN_HIDDEN_SUBSTRING_LEN and h in scrubbed:
            scrubbed = scrubbed.replace(h, _HIDDEN_SENTINEL)
    return scrubbed


def _sanitize_tool_args(args: object, hidden: list[str]) -> object:
    """Recursively scrub hidden oracle content out of tool args."""
    if isinstance(args, str):
        return _scrub_text(args, hidden)
    if isinstance(args, dict):
        return {k: _sanitize_tool_args(v, hidden) for k, v in args.items()}
    if isinstance(args, list):
        return [_sanitize_tool_args(v, hidden) for v in args]
    return args


def _sanitize_tool_result_values(result: object, hidden: list[str]) -> object:
    """Like `_sanitize_tool_args` but for tool result values.

    Also runs the oracle-key replacement so the keys-but-not-values
    contract from `_sanitize_tool_result` still holds.
    """
    if isinstance(result, dict):
        out: dict[str, object] = {}
        for k, v in result.items():
            if k in _ORACLE_RESULT_KEYS:
                out[k] = "<oracle-output>"
            else:
                out[k] = _sanitize_tool_result_values(v, hidden)
        return out
    if isinstance(result, list):
        return [_sanitize_tool_result_values(v, hidden) for v in result]
    if isinstance(result, str):
        return _scrub_text(result, hidden)
    return result


def _write_jsonl(
    out_path: Path,
    rows: Iterable[dict],
    format: str,
    filter_: dict,
    signer: ReceiptSigner | None,
    limit: int,
) -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    hashes: list[str] = []
    with out_path.open("w") as f:
        for row in rows:
            if count >= limit:
                break
            line = json.dumps(row, sort_keys=True, default=str)
            f.write(line + "\n")
            hashes.append(blake3.blake3(line.encode()).hexdigest())
            count += 1
    manifest = {
        "format": format,
        "filter": filter_,
        "row_count": count,
        "exported_at": datetime.now(UTC).isoformat(),
        "row_hashes_count": len(hashes),
        "row_hashes_sample": hashes[:10],
        "aggregate_hash": blake3.blake3(canonical_json(hashes)).hexdigest(),
    }
    if signer is not None:
        manifest["signature_scheme"] = "ed25519"
        manifest["signer_pubkey_hex"] = signer.public_hex
        manifest["signature_hex"] = signer.sign_bytes(canonical_json(manifest))
    out_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
    )
    return manifest


__all__ = ["export_dpo", "export_rm", "export_sft"]
