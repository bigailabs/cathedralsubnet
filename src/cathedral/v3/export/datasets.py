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
    CodingFailureClass,
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

    Both sides of a pair must pass:
      - the split filter (`allowed_splits`)
      - `_is_safe_for_preference_training(t)`, which refuses NEGATIVE,
        trusted-fixture-mode, sandbox-violation, and any bug_repro
        trajectory that did not run in a real (Docker) sandbox.

    DPO is the most leak-prone export: a model trained on a
    chosen/rejected pair learns the *delta* in behaviour, so even one
    unsandboxed or trusted-fixture-mode trajectory on either side
    poisons the preference signal. We refuse them at the row level,
    regardless of whether the operator promoted the split.
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
            # Asymmetric gate: the winner must be a safe positive
            # example (oracle trustworthy AND readiness != NEGATIVE);
            # the loser only needs a trustworthy oracle (NEGATIVE is
            # the canonical "rejected" signal). Either side carrying
            # trusted_fixture_mode / SANDBOX_VIOLATION / non-Docker
            # bug_repro poisons the pair.
            if not _winner_is_safe_for_preference(winner):
                continue
            if not _loser_is_safe_for_preference(loser):
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


def _is_safe_oracle_for_preference(t: Trajectory) -> bool:
    """Either side of a DPO pair must have a trustworthy oracle.

    Untrustworthy oracle = the validator did not run the candidate
    under real isolation. The split filter (`_is_exportable`) is not
    enough — once an operator promotes OPERATOR_REVIEW into
    `allowed_splits`, oracle trustworthiness must still gate every
    row.

    Reject:
      - verifier_metrics["trusted_fixture_mode"] is True
      - coding_failure == SANDBOX_VIOLATION
      - bug_repro trajectories whose sandbox is not real (Docker)
    """
    if t.score.verifier_metrics.get("trusted_fixture_mode") is True:
        return False
    if t.score.coding_failure == CodingFailureClass.SANDBOX_VIOLATION:
        return False
    if t.job.task_type is TaskType.BUG_REPRO:
        vm = t.score.verifier_metrics
        if vm.get("sandbox_is_real") is False:
            return False
        # For bug_repro: if the verifier ran without explicit
        # sandbox metadata (older trajectory shape), refuse — safer
        # default than allowing through.
        if vm.get("sandbox_backend", "") != "docker":
            return False
    return True


def _winner_is_safe_for_preference(t: Trajectory) -> bool:
    """The chosen side of a DPO pair must additionally not be NEGATIVE.

    A NEGATIVE-readiness *loser* is fine — it's exactly the signal DPO
    learns from. But a NEGATIVE winner means we'd be training the
    model to imitate a bad output, which inverts the preference signal.
    """
    if not _is_safe_oracle_for_preference(t):
        return False
    if t.score.readiness == DistillationReadiness.NEGATIVE:
        return False
    return True


def _loser_is_safe_for_preference(t: Trajectory) -> bool:
    """The rejected side of a DPO pair must have a trustworthy oracle.

    NEGATIVE readiness is allowed (and useful — it's the canonical
    rejected signal).
    """
    return _is_safe_oracle_for_preference(t)


def _is_safe_for_preference_training(t: Trajectory) -> bool:
    """Back-compat helper: the strict gate used by tests/callers that
    want a single yes/no without distinguishing winner from loser.
    Equivalent to the winner gate.
    """
    return _winner_is_safe_for_preference(t)


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
# A non-oracle hidden string shorter than this is treated as too small to
# risk a substring match against (would scrub legitimate words like
# "from"). The bug_repro source/reference strings are multi-line and
# above this length, so the threshold only matters for incidental short
# values; SHORT_ORACLE_KEYS below get scrubbed regardless.
_MIN_HIDDEN_SUBSTRING_LEN = 24

# Hidden-context keys whose VALUES are oracle signals that must be
# scrubbed even when shorter than the substring threshold. Examples:
#   expected_symptom        -- e.g. "ZeroDivisionError", "division by
#                              zero", "state leaked", "got None"
#   expected_label          -- e.g. "bug", "feature_request"
#   expected_tool           -- e.g. "kv_set"
#   expected_exception      -- exception class name
#
# An identifier-shape rule used to gate scrubbing, but the reviewer
# correctly pointed out that real expected_symptom values are arbitrary
# failure-output substrings, not necessarily identifiers. Anything an
# operator places under one of these keys is treated as oracle data and
# scrubbed from exported text.
_SHORT_ORACLE_KEYS: frozenset[str] = frozenset(
    {
        "expected_symptom",
        "expected_label",
        "expected_tool",
        "expected_exception",
    }
)

# Floor length for short-oracle values. Anything shorter (e.g. "a") is
# considered too generic to safely scrub from prose; we accept that
# leak risk rather than redact every single letter that appears.
_MIN_SHORT_ORACLE_LEN = 3


# A "short oracle" is a hidden value that must be scrubbed even when
# below _MIN_HIDDEN_SUBSTRING_LEN. Each entry is (value, is_short_oracle).
HiddenString = tuple[str, bool]


def _collect_hidden_strings(t: Trajectory) -> list[HiddenString]:
    """Flatten every string leaf in `job.hidden_context`.

    Returns a list of (value, is_short_oracle), ordered longest first
    so that scrubbing matches the most specific hidden string before
    any incidental short overlap.

    A value is flagged as `is_short_oracle` when its key (the last
    segment of its path through `hidden_context`) is in
    `_SHORT_ORACLE_KEYS`. Such values are scrubbed regardless of
    length or shape, including multi-word symptoms like
    "division by zero".
    """
    out: list[HiddenString] = []

    def walk(node: object, key_path: tuple[str, ...]) -> None:
        if isinstance(node, str):
            stripped = node.strip()
            if not stripped:
                return
            is_short_oracle = (
                bool(key_path)
                and key_path[-1] in _SHORT_ORACLE_KEYS
                and len(stripped) >= _MIN_SHORT_ORACLE_LEN
            )
            out.append((node, is_short_oracle))
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, (*key_path, str(k)))
        elif isinstance(node, list | tuple):
            for v in node:
                walk(v, key_path)

    walk(t.job.hidden_context, ())
    # Sort longest first so we don't prematurely match a short prefix.
    out.sort(key=lambda hs: len(hs[0]), reverse=True)
    return out


def _scrub_text(text: str | None, hidden: list[HiddenString]) -> str | None:
    if text is None or not isinstance(text, str) or not text:
        return text
    scrubbed = text
    for value, is_short_oracle in hidden:
        if not value:
            continue
        if value == scrubbed:
            return _HIDDEN_SENTINEL
        # Long values: substring-scrub past the length threshold.
        # Short-oracle values (any string under SHORT_ORACLE_KEYS,
        # including multi-word symptoms): scrub on word boundaries so
        # we don't mangle longer identifiers/words that happen to
        # embed them.
        if is_short_oracle:
            scrubbed = _replace_on_word_boundary(scrubbed, value, _HIDDEN_SENTINEL)
        elif len(value) >= _MIN_HIDDEN_SUBSTRING_LEN and value in scrubbed:
            scrubbed = scrubbed.replace(value, _HIDDEN_SENTINEL)
    return scrubbed


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c == "_"


def _replace_on_word_boundary(haystack: str, needle: str, replacement: str) -> str:
    """Replace `needle` in `haystack` only where it sits on a word
    boundary on both sides.

    A "word boundary" here is: the character immediately before/after
    the match is not a word character (alnum or underscore). This
    works for identifiers ("ZeroDivisionError" matches but
    "MyZeroDivisionErrorWrapper" doesn't) AND for multi-word symptoms
    ("division by zero" matches "raises division by zero." but
    leaves "subdivision by zero" alone because of the leading "sub").
    """
    if not needle or needle not in haystack:
        return haystack
    out_parts: list[str] = []
    i = 0
    nlen = len(needle)
    while i < len(haystack):
        j = haystack.find(needle, i)
        if j == -1:
            out_parts.append(haystack[i:])
            break
        before_ok = j == 0 or not _is_word_char(haystack[j - 1])
        after_idx = j + nlen
        after_ok = after_idx >= len(haystack) or not _is_word_char(haystack[after_idx])
        out_parts.append(haystack[i:j])
        if before_ok and after_ok:
            out_parts.append(replacement)
        else:
            out_parts.append(needle)
        i = j + nlen
    return "".join(out_parts)


# Backwards-compat alias for callers still importing the prior name.
_replace_whole_word = _replace_on_word_boundary


def _sanitize_tool_args(args: object, hidden: list[HiddenString]) -> object:
    """Recursively scrub hidden oracle content out of tool args."""
    if isinstance(args, str):
        return _scrub_text(args, hidden)
    if isinstance(args, dict):
        return {k: _sanitize_tool_args(v, hidden) for k, v in args.items()}
    if isinstance(args, list):
        return [_sanitize_tool_args(v, hidden) for v in args]
    return args


def _sanitize_tool_result_values(result: object, hidden: list[HiddenString]) -> object:
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
