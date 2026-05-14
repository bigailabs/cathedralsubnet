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
            yield {
                "prompt": prompt_visible_to_miner(winner),
                "chosen": winner.result.final_output,
                "rejected": loser.result.final_output,
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
            yield {
                "prompt": prompt_visible_to_miner(t),
                "completion": t.result.final_output,
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

    Includes the tool trace so the fine-tuned model learns tool use,
    but ALL tool results pass through `_sanitize_tool_result` so
    validator-owned oracle output (fails_on_buggy, passes_on_fixed,
    symptom_match, ...) is replaced with a sentinel before
    serialization.
    """
    messages = [
        {"role": "system", "content": "You are a Cathedral agent. Solve the job."},
        {"role": "user", "content": prompt_visible_to_miner(t)},
    ]
    for tc in t.tool_calls:
        if tc.tool_name.startswith("__"):
            continue
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps({"tool": tc.tool_name, "args": tc.args}, sort_keys=True),
            }
        )
        if tc.ok:
            sanitized = _sanitize_tool_result(tc)
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(sanitized, default=str, sort_keys=True)[:2000],
                }
            )
        else:
            messages.append({"role": "tool", "content": f"error: {tc.error}"})
    messages.append({"role": "assistant", "content": t.result.final_output})
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
