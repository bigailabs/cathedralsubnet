"""Per-task-type rubrics.

Each rubric returns a ScoreParts: per-dimension values in [0, 1], a
composed `weighted` score, a FailureClass, and a readiness flag.

The composed score is the dimension-weighted mean using the per-rubric
weights below.
"""

from __future__ import annotations

import os

from cathedral.v3.types import (
    CodingFailureClass,
    DistillationReadiness,
    FailureClass,
    ScoreParts,
    TaskType,
    Trajectory,
)

_GOLD_THRESHOLD = float(os.environ.get("CATHEDRAL_V3_GOLD_THRESHOLD", "0.85"))
_NEGATIVE_THRESHOLD = 0.25


def score_trajectory(traj: Trajectory) -> ScoreParts:
    if traj.result.agent_error:
        fc = (
            FailureClass.TIMEOUT
            if "deadline" in (traj.result.agent_error or "")
            else FailureClass.AGENT_ERROR
        )
        return ScoreParts(
            dimensions={},
            weighted=0.0,
            failure_class=fc,
            readiness=DistillationReadiness.NEGATIVE,
            notes=traj.result.agent_error,
        )

    tt = traj.job.task_type
    if tt is TaskType.RESEARCH:
        parts = _score_research(traj)
    elif tt is TaskType.CODE_PATCH:
        parts = _score_code_patch(traj)
    elif tt is TaskType.TOOL_ROUTE:
        parts = _score_tool_route(traj)
    elif tt is TaskType.MULTI_STEP:
        parts = _score_multi_step(traj)
    elif tt is TaskType.CLASSIFY:
        parts = _score_classify(traj)
    elif tt is TaskType.BUG_REPRO:
        parts = _score_bug_repro(traj)
    else:
        parts = ScoreParts(
            dimensions={},
            weighted=0.0,
            failure_class=FailureClass.WRONG_FORMAT,
            readiness=DistillationReadiness.DISCARD,
        )

    parts.readiness = _readiness(parts)
    return parts


def _readiness(parts: ScoreParts) -> DistillationReadiness:
    # Sandbox violations and trusted-fixture-mode runs are PERMANENTLY
    # NEGATIVE regardless of score, because their oracle output cannot
    # be trusted as training signal.
    if parts.coding_failure == CodingFailureClass.SANDBOX_VIOLATION:
        return DistillationReadiness.NEGATIVE
    if parts.verifier_metrics.get("trusted_fixture_mode") is True:
        return DistillationReadiness.NEGATIVE
    if parts.failure_class != FailureClass.NONE and parts.weighted < _NEGATIVE_THRESHOLD:
        return DistillationReadiness.NEGATIVE
    if parts.weighted >= _GOLD_THRESHOLD and parts.failure_class == FailureClass.NONE:
        return DistillationReadiness.GOLD
    if parts.weighted < _NEGATIVE_THRESHOLD:
        return DistillationReadiness.NEGATIVE
    return DistillationReadiness.DISCARD  # eligible to become preference pair via archive query


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


def _score_research(traj: Trajectory) -> ScoreParts:
    ctx = traj.job.context
    answer = (traj.result.final_output or "").lower()
    truth = (ctx.get("ground_truth_answer") or "").lower()
    required = set(ctx.get("required_citations") or [])
    cited = set(_sink(traj, "cited") or [])

    # crude string overlap as correctness proxy
    truth_tokens = {t for t in truth.split() if len(t) > 3}
    answer_tokens = {t for t in answer.split() if len(t) > 3}
    if not truth_tokens:
        correctness = 0.5
    else:
        overlap = len(truth_tokens & answer_tokens) / len(truth_tokens)
        correctness = min(1.0, overlap * 1.5)

    groundedness = 1.0 if required and required.issubset(cited) else (0.0 if required else 0.5)

    citations_present = 1.0 if cited else 0.0
    citation_precision = (
        ((len(required & cited) / len(cited)) if cited else 0.0)
        if required
        else (1.0 if cited else 0.0)
    )

    fc = FailureClass.NONE
    if not cited:
        fc = FailureClass.HALLUCINATED_CITATION
    elif required and not required.issubset(cited):
        fc = FailureClass.HALLUCINATED_CITATION
    elif overlap_eq(answer_tokens, truth_tokens, 0.1) is False:
        fc = FailureClass.IRRELEVANT

    dims = {
        "correctness": correctness,
        "groundedness": groundedness,
        "citations_present": citations_present,
        "citation_precision": citation_precision,
    }
    weighted = (
        0.45 * correctness
        + 0.30 * groundedness
        + 0.15 * citations_present
        + 0.10 * citation_precision
    )
    return ScoreParts(
        dimensions=dims,
        weighted=round(weighted, 4),
        failure_class=fc,
        readiness=DistillationReadiness.DISCARD,
    )


def overlap_eq(a: set, b: set, threshold: float) -> bool:
    if not b:
        return True
    return (len(a & b) / len(b)) >= threshold


# ---------------------------------------------------------------------------
# code_patch
# ---------------------------------------------------------------------------


def _score_code_patch(traj: Trajectory) -> ScoreParts:
    state = _sink(traj, "state") or {}
    test_result = state.get("test_result") or {}
    passed = bool(test_result.get("passed"))
    patched = state.get("patched") is not None
    diff_submitted = state.get("submitted_diff") is not None

    # efficiency: did the miner read before patching?
    read_first = False
    for call in traj.tool_calls:
        if call.tool_name == "read_file":
            read_first = True
            break
        if call.tool_name == "apply_patch":
            break

    dims = {
        "tests_pass": 1.0 if passed else 0.0,
        "patch_applies": 1.0 if patched else 0.0,
        "submitted_anything": 1.0 if diff_submitted else 0.0,
        "read_before_patch": 1.0 if read_first else 0.0,
    }
    weighted = (
        0.7 * dims["tests_pass"]
        + 0.2 * dims["patch_applies"]
        + 0.05 * dims["submitted_anything"]
        + 0.05 * dims["read_before_patch"]
    )
    fc = FailureClass.NONE
    if not diff_submitted:
        fc = FailureClass.NO_OUTPUT
    elif not patched:
        fc = FailureClass.WRONG_FORMAT
    elif not passed:
        fc = FailureClass.IRRELEVANT
    return ScoreParts(
        dimensions=dims,
        weighted=round(weighted, 4),
        failure_class=fc,
        readiness=DistillationReadiness.DISCARD,
    )


# ---------------------------------------------------------------------------
# tool_route
# ---------------------------------------------------------------------------


def _score_tool_route(traj: Trajectory) -> ScoreParts:
    chosen = _sink(traj, "chosen") or {}
    expected = traj.job.context.get("expected_tool")
    expected_args = traj.job.context.get("expected_args") or {}

    tool_correct = 1.0 if chosen.get("tool") == expected else 0.0
    args_correct = 0.0
    actual_args = chosen.get("args") or {}
    if expected_args:
        # accept any args whose values overlap substantially with expected
        hits = sum(
            1
            for k, v in expected_args.items()
            if k in actual_args and str(actual_args[k]).lower().strip() == str(v).lower().strip()
        )
        args_correct = hits / len(expected_args)
    else:
        args_correct = 1.0

    dims = {
        "tool_select_acc": tool_correct,
        "args_acc": args_correct,
    }
    weighted = 0.7 * tool_correct + 0.3 * args_correct
    fc = FailureClass.NONE if tool_correct else FailureClass.TOOL_MISUSE
    return ScoreParts(
        dimensions=dims,
        weighted=round(weighted, 4),
        failure_class=fc,
        readiness=DistillationReadiness.DISCARD,
    )


# ---------------------------------------------------------------------------
# multi_step
# ---------------------------------------------------------------------------


def _score_multi_step(traj: Trajectory) -> ScoreParts:
    sink = _sink(traj, "state") or {}
    kv = sink.get("kv") or {}
    done = bool(sink.get("done"))
    target = traj.job.context.get("target_state") or {}

    matched = sum(1 for k, v in target.items() if str(kv.get(k, "")) == str(v))
    target_satisfaction = matched / len(target) if target else 0.0

    n_calls = len(traj.tool_calls)
    min_steps = traj.job.context.get("min_steps", 1)
    max_steps = traj.job.context.get("max_steps", 20)
    if n_calls <= min_steps:
        efficiency = 1.0
    elif n_calls >= max_steps:
        efficiency = 0.2
    else:
        efficiency = max(0.2, 1.0 - (n_calls - min_steps) / (max_steps - min_steps))

    dims = {
        "target_satisfaction": target_satisfaction,
        "called_done": 1.0 if done else 0.0,
        "efficiency": efficiency,
    }
    weighted = 0.7 * target_satisfaction + 0.15 * dims["called_done"] + 0.15 * efficiency
    fc = FailureClass.NONE
    if target_satisfaction == 0.0:
        fc = FailureClass.IRRELEVANT
    elif not done:
        fc = FailureClass.WRONG_FORMAT
    elif n_calls >= max_steps:
        fc = FailureClass.TIMEOUT
    return ScoreParts(
        dimensions=dims,
        weighted=round(weighted, 4),
        failure_class=fc,
        readiness=DistillationReadiness.DISCARD,
    )


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def _score_classify(traj: Trajectory) -> ScoreParts:
    chosen = (_sink(traj, "chosen") or {}).get("label")
    expected = traj.job.context.get("expected_label")
    correct = 1.0 if chosen == expected else 0.0
    fc = FailureClass.NONE if correct else FailureClass.IRRELEVANT
    return ScoreParts(
        dimensions={"label_acc": correct},
        weighted=correct,
        failure_class=fc,
        readiness=DistillationReadiness.DISCARD,
    )


# ---------------------------------------------------------------------------
# bug_repro
# ---------------------------------------------------------------------------


def _score_bug_repro(traj: Trajectory) -> ScoreParts:
    """Score a bug_repro trajectory.

    Three load-bearing oracle signals from the validator's sandbox runs:
      - fails_on_buggy: the candidate test must fail on the buggy source
      - passes_on_fixed: the candidate test must pass on the fixed source
      - symptom_match: the failure output must contain the expected symptom

    Composed score: 0.5 * fails_on_buggy + 0.4 * passes_on_fixed + 0.1 * symptom_match.

    HARD SANDBOX GATE: this rubric refuses to award any positive score
    when the sandbox backend that ran the candidate test is not Docker
    (or another future real sandbox). SubprocessBackend is a degraded
    CI/dev fallback that does NOT isolate miner-supplied code; awarding
    rewardable scores on its output would let an attacker run arbitrary
    code outside any isolation. The gate sets:
      - weighted = 0
      - coding_failure = SANDBOX_VIOLATION
      - readiness = NEGATIVE  (set by `_readiness` once failure_class
                              is non-NONE and score < threshold)

    The gate can be relaxed for a trusted-fixture mode by setting
    ``CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS=1``. Even then the
    rubric tags `verifier_metrics["trusted_fixture_mode"] = True` and
    forces `readiness = NEGATIVE` so the trajectory cannot count as
    SFT/DPO gold; this exists only so dev/CI can smoke-test the loop
    without a running Docker daemon.
    """
    sink = _sink(traj, "bug_repro") or {}
    submitted = sink.get("test_source") is not None
    fails_on_buggy = bool(sink.get("fails_on_buggy"))
    passes_on_fixed = bool(sink.get("passes_on_fixed"))
    symptom_match = bool(sink.get("symptom_match"))
    sandbox_backend = sink.get("sandbox_backend") or ""
    trusted_fixture_mode = os.environ.get("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS") == "1"

    # ----- hard sandbox gate -----
    sandbox_is_real = sandbox_backend == "docker"
    if submitted and not sandbox_is_real and not trusted_fixture_mode:
        return ScoreParts(
            dimensions={
                "submitted": 1.0,
                "fails_on_buggy": 0.0,
                "passes_on_fixed": 0.0,
                "symptom_match": 0.0,
            },
            weighted=0.0,
            failure_class=FailureClass.IRRELEVANT,
            coding_failure=CodingFailureClass.SANDBOX_VIOLATION,
            verifier_metrics={
                "sandbox_backend": sandbox_backend,
                "sandbox_is_real": False,
                "fails_on_buggy": fails_on_buggy,
                "passes_on_fixed": passes_on_fixed,
                "symptom_match": symptom_match,
            },
            readiness=DistillationReadiness.NEGATIVE,
            notes=(
                "sandbox_violation: bug_repro requires Docker. Set "
                "CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS=1 to run in "
                "trusted-fixture mode (still scored as NEGATIVE so it "
                "cannot become training-gold)."
            ),
        )

    dims = {
        "submitted": 1.0 if submitted else 0.0,
        "fails_on_buggy": 1.0 if fails_on_buggy else 0.0,
        "passes_on_fixed": 1.0 if passes_on_fixed else 0.0,
        "symptom_match": 1.0 if symptom_match else 0.0,
    }
    weighted = (
        0.5 * dims["fails_on_buggy"] + 0.4 * dims["passes_on_fixed"] + 0.1 * dims["symptom_match"]
    )

    coding_fc = CodingFailureClass.NONE
    if not submitted:
        coding_fc = CodingFailureClass.NO_BUG_REPRO
    elif not fails_on_buggy and passes_on_fixed:
        # passes on both — does not reproduce the bug
        coding_fc = CodingFailureClass.NO_BUG_REPRO
    elif passes_on_fixed is False and fails_on_buggy:
        # also fails on the fixed commit — too broad / brittle
        coding_fc = CodingFailureClass.FIXED_COMMIT_FAILS
    elif not symptom_match and fails_on_buggy:
        coding_fc = CodingFailureClass.FLAKE

    fc = FailureClass.NONE if coding_fc == CodingFailureClass.NONE else FailureClass.IRRELEVANT

    # Trusted-fixture mode: the substantive scoring still runs (so we
    # can smoke-test the rubric), but the trajectory is permanently
    # marked NEGATIVE so it cannot leak into SFT/DPO gold.
    if not sandbox_is_real and trusted_fixture_mode:
        return ScoreParts(
            dimensions=dims,
            weighted=round(weighted, 4),
            failure_class=fc if fc != FailureClass.NONE else FailureClass.IRRELEVANT,
            coding_failure=coding_fc,
            verifier_metrics={
                "sandbox_backend": sandbox_backend,
                "sandbox_is_real": False,
                "trusted_fixture_mode": True,
                "fails_on_buggy": fails_on_buggy,
                "passes_on_fixed": passes_on_fixed,
                "symptom_match": symptom_match,
            },
            readiness=DistillationReadiness.NEGATIVE,
            notes="trusted_fixture_mode: substrate smoke test only, never gold",
        )

    return ScoreParts(
        dimensions=dims,
        weighted=round(weighted, 4),
        failure_class=fc,
        coding_failure=coding_fc,
        verifier_metrics={
            "sandbox_backend": sandbox_backend,
            "sandbox_is_real": sandbox_is_real,
            "fails_on_buggy": fails_on_buggy,
            "passes_on_fixed": passes_on_fixed,
            "symptom_match": symptom_match,
        },
        readiness=DistillationReadiness.DISCARD,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sink(traj: Trajectory, name: str):
    """Pull a sink value the validator stashed under result.structured._sinks."""
    sinks = (traj.result.structured or {}).get("_sinks") or {}
    return sinks.get(f"sink_{name}") or sinks.get(name)


__all__ = ["score_trajectory"]
