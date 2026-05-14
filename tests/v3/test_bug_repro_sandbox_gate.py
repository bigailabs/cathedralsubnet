"""bug_repro must refuse to award positive scores from an unsandboxed run.

The scorer is the choke point. Even if the tool layer changes, the
rubric must independently reject SubprocessBackend output unless the
trusted-fixture escape hatch is explicitly set, and even then keep the
trajectory permanently NEGATIVE so it cannot flow into training data
as gold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cathedral.v3.jobs import generate_job
from cathedral.v3.miner import HeuristicAgent
from cathedral.v3.runtime import Runtime
from cathedral.v3.scoring.rubrics import score_trajectory
from cathedral.v3.types import (
    AgentResult,
    CodingFailureClass,
    DistillationReadiness,
    ScoreParts,
    TaskType,
    ToolCall,
    Trajectory,
)


def _build_traj(
    *,
    sandbox_backend: str,
    fails_on_buggy: bool,
    passes_on_fixed: bool,
    symptom_match: bool,
    submitted: bool = True,
) -> Trajectory:
    job = generate_job(TaskType.BUG_REPRO, seed=0)
    now = datetime.now(UTC)
    sinks = {
        "sink_bug_repro": {
            "test_source": "assert True\n" if submitted else None,
            "buggy_run": {"ok": False, "backend": sandbox_backend},
            "fixed_run": {"ok": True, "backend": sandbox_backend},
            "fails_on_buggy": fails_on_buggy,
            "passes_on_fixed": passes_on_fixed,
            "symptom_match": symptom_match,
            "sandbox_backend": sandbox_backend,
        }
    }
    return Trajectory(
        job=job,
        miner_hotkey="hk_test",
        miner_kind="heuristic",
        tool_calls=[
            ToolCall(
                step=0,
                tool_name="submit_test",
                args={"test_source": "assert True\n"},
                result={"ok": True},
                started_at=now,
                ended_at=now,
                latency_ms=0.1,
            )
        ],
        result=AgentResult(final_output="ok", structured={"_sinks": sinks}),
        score=ScoreParts.empty(),
        started_at=now,
        ended_at=now,
    )


# ---------------------------------------------------------------------------
# subprocess backend MUST score 0 / sandbox_violation / NEGATIVE
# ---------------------------------------------------------------------------


def test_subprocess_backend_scores_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", raising=False)
    traj = _build_traj(
        sandbox_backend="subprocess",
        fails_on_buggy=True,
        passes_on_fixed=True,
        symptom_match=True,
    )
    score = score_trajectory(traj)
    assert score.weighted == 0.0


def test_subprocess_backend_sets_sandbox_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", raising=False)
    traj = _build_traj(
        sandbox_backend="subprocess",
        fails_on_buggy=True,
        passes_on_fixed=True,
        symptom_match=True,
    )
    score = score_trajectory(traj)
    assert score.coding_failure == CodingFailureClass.SANDBOX_VIOLATION


def test_subprocess_backend_readiness_is_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", raising=False)
    traj = _build_traj(
        sandbox_backend="subprocess",
        fails_on_buggy=True,
        passes_on_fixed=True,
        symptom_match=True,
    )
    score = score_trajectory(traj)
    assert score.readiness == DistillationReadiness.NEGATIVE


def test_subprocess_backend_no_gold_even_when_oracle_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hard guarantee: a perfectly correct test ran outside Docker
    must NEVER reach GOLD readiness. This is the property that matters
    for the export firewall + training data integrity.
    """
    monkeypatch.delenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", raising=False)
    traj = _build_traj(
        sandbox_backend="subprocess",
        fails_on_buggy=True,
        passes_on_fixed=True,
        symptom_match=True,
    )
    score = score_trajectory(traj)
    assert score.readiness != DistillationReadiness.GOLD
    assert score.readiness == DistillationReadiness.NEGATIVE
    assert score.coding_failure == CodingFailureClass.SANDBOX_VIOLATION


# ---------------------------------------------------------------------------
# trusted-fixture mode: scored but locked NEGATIVE
# ---------------------------------------------------------------------------


def test_trusted_fixture_mode_is_scored_but_never_gold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", "1")
    traj = _build_traj(
        sandbox_backend="subprocess",
        fails_on_buggy=True,
        passes_on_fixed=True,
        symptom_match=True,
    )
    score = score_trajectory(traj)
    # Substantive scoring runs (weighted > 0 because the oracle passed).
    assert score.weighted > 0.0
    # But readiness is locked NEGATIVE so this can never become training gold.
    assert score.readiness == DistillationReadiness.NEGATIVE
    assert score.verifier_metrics.get("trusted_fixture_mode") is True


def test_trusted_fixture_mode_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", raising=False)
    traj = _build_traj(
        sandbox_backend="subprocess",
        fails_on_buggy=True,
        passes_on_fixed=True,
        symptom_match=True,
    )
    score = score_trajectory(traj)
    # No trusted_fixture_mode flag means: pure sandbox violation, zero.
    assert score.verifier_metrics.get("trusted_fixture_mode") is not True
    assert score.weighted == 0.0


# ---------------------------------------------------------------------------
# docker backend: normal scoring
# ---------------------------------------------------------------------------


def test_docker_backend_perfect_run_can_reach_gold() -> None:
    traj = _build_traj(
        sandbox_backend="docker",
        fails_on_buggy=True,
        passes_on_fixed=True,
        symptom_match=True,
    )
    score = score_trajectory(traj)
    assert score.weighted == 1.0
    assert score.coding_failure == CodingFailureClass.NONE
    assert score.readiness == DistillationReadiness.GOLD


def test_docker_backend_failing_oracle_still_classified() -> None:
    traj = _build_traj(
        sandbox_backend="docker",
        fails_on_buggy=False,
        passes_on_fixed=True,
        symptom_match=False,
    )
    score = score_trajectory(traj)
    assert score.coding_failure == CodingFailureClass.NO_BUG_REPRO
    assert score.weighted < 0.85  # not gold


# ---------------------------------------------------------------------------
# end-to-end via Runtime: heuristic baseline must not get gold on subprocess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_bug_repro_with_heuristic_never_gold_under_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The heuristic baseline reads hidden_context.reference_test_source
    # and would normally score gold. Under subprocess sandbox (no Docker
    # daemon in CI), it must score NEGATIVE / sandbox_violation.
    monkeypatch.delenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", raising=False)
    rt = Runtime(
        home=tmp_path / "cathedral_v3",
        miners=[HeuristicAgent("hk_h")],
        task_types=[TaskType.BUG_REPRO],
    )
    r = await rt.tick()
    assert r.trajectories
    for t in r.trajectories:
        # In CI / typical dev: subprocess. If a host actually has Docker
        # running the test won't enter the gate, so we only assert the
        # gate when sandbox_backend is subprocess.
        backend = t.score.verifier_metrics.get("sandbox_backend")
        if backend == "subprocess":
            assert t.score.coding_failure == CodingFailureClass.SANDBOX_VIOLATION
            assert t.score.readiness == DistillationReadiness.NEGATIVE
            assert t.score.weighted == 0.0
        elif backend == "docker":
            # If we *do* have docker running here, the heuristic should
            # still hit gold by reading hidden_context. We don't assert
            # this branch strictly because docker presence is host-dependent.
            assert t.score.weighted >= 0.0
