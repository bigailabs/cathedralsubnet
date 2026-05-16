"""DPO safety gate.

The previous review reproduced: a TRAIN_EXPORTABLE bug_repro pair where
the winner has trusted_fixture_mode=True / readiness=NEGATIVE /
sandbox_backend=subprocess made it through export_dpo and emitted a
row. These tests reproduce that case and assert the gate now refuses
it. Also: a docker-backed positive pair still exports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cathedral.v3.archive import TrajectoryArchive
from cathedral.v3.archive.store import PreferencePair as _PrefPair
from cathedral.v3.export import export_dpo
from cathedral.v3.jobs import generate_job
from cathedral.v3.miner import EchoAgent, HeuristicAgent
from cathedral.v3.miner.base import MinerAgent
from cathedral.v3.runtime import Runtime
from cathedral.v3.types import (
    AgentResult,
    CodingFailureClass,
    DistillationReadiness,
    FailureClass,
    ScoreParts,
    TaskSplit,
    TaskType,
    Trajectory,
)


@pytest.fixture()
def tmp_home(tmp_path: Path) -> Path:
    return tmp_path / "cathedral_v3"


# ---------------------------------------------------------------------------
# Forge a same-job bug_repro pair with the dangerous flags set, then make
# sure export_dpo emits 0 rows. Forging is the only way to drive a
# `trusted_fixture_mode=True` + `TRAIN_EXPORTABLE` pair through the
# archive — the normal rubric path locks split=OPERATOR_REVIEW.
# ---------------------------------------------------------------------------


def _forge_bug_repro_trajectory(
    *,
    split: TaskSplit,
    sandbox_backend: str,
    trusted_fixture_mode: bool,
    readiness: DistillationReadiness,
    weighted: float,
    coding_failure: CodingFailureClass = CodingFailureClass.NONE,
    miner_kind: str = "heuristic",
) -> Trajectory:
    job = generate_job(TaskType.BUG_REPRO, seed=0).model_copy(update={"task_split": split})
    now = datetime.now(UTC)
    return Trajectory(
        job=job,
        miner_hotkey=f"hk_{miner_kind}",
        miner_kind=miner_kind,
        tool_calls=[],
        result=AgentResult(final_output="ok"),
        score=ScoreParts(
            dimensions={"submitted": 1.0},
            weighted=weighted,
            failure_class=FailureClass.IRRELEVANT
            if coding_failure != CodingFailureClass.NONE
            else FailureClass.NONE,
            coding_failure=coding_failure,
            verifier_metrics={
                "sandbox_backend": sandbox_backend,
                "sandbox_is_real": sandbox_backend == "docker",
                "trusted_fixture_mode": trusted_fixture_mode,
                "fails_on_buggy": True,
                "passes_on_fixed": True,
                "symptom_match": True,
            },
            readiness=readiness,
        ),
        started_at=now,
        ended_at=now,
    )


def _seed_archive_pair(
    archive: TrajectoryArchive, winner: Trajectory, loser: Trajectory, score_delta: float
) -> _PrefPair:
    # Force the shared-job invariant the archive expects from preference_pairs().
    loser = loser.model_copy(update={"job": winner.job})
    archive.insert(winner)
    archive.insert(loser)
    return _PrefPair(
        job_id=winner.job.job_id,
        winner_trajectory_id=winner.trajectory_id,
        loser_trajectory_id=loser.trajectory_id,
        score_delta=score_delta,
    )


def test_dpo_refuses_trusted_fixture_mode_pair(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's reproduction. Both sides forged into
    TRAIN_EXPORTABLE with trusted_fixture_mode=True; DPO must emit 0
    rows.
    """
    winner = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="subprocess",
        trusted_fixture_mode=True,
        readiness=DistillationReadiness.NEGATIVE,
        weighted=0.6,
        miner_kind="heuristic",
    )
    loser = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="subprocess",
        trusted_fixture_mode=True,
        readiness=DistillationReadiness.NEGATIVE,
        weighted=0.1,
        miner_kind="echo",
    )
    archive = TrajectoryArchive(tmp_home)
    pair = _seed_archive_pair(archive, winner, loser, score_delta=0.5)

    # preference_pairs() reads the archive; monkeypatch it to return our forged pair
    # so we don't depend on the archive's internal scoring/pairing logic.
    monkeypatch.setattr(archive, "preference_pairs", lambda **kw: [pair])

    out = tmp_home / "dpo.jsonl"
    m = export_dpo(
        archive,
        out,
        signer=None,
        min_delta=0.05,
        allowed_splits=frozenset({TaskSplit.TRAIN_EXPORTABLE}),
    )
    assert m["row_count"] == 0, f"DPO emitted a pair with trusted_fixture_mode=True: {m}"


def test_dpo_refuses_sandbox_violation_pair(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="subprocess",
        trusted_fixture_mode=False,
        readiness=DistillationReadiness.NEGATIVE,
        weighted=0.0,
        coding_failure=CodingFailureClass.SANDBOX_VIOLATION,
        miner_kind="heuristic",
    )
    loser = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="docker",
        trusted_fixture_mode=False,
        readiness=DistillationReadiness.NEGATIVE,
        weighted=0.0,
        miner_kind="echo",
    )
    archive = TrajectoryArchive(tmp_home)
    pair = _seed_archive_pair(archive, winner, loser, score_delta=0.5)
    monkeypatch.setattr(archive, "preference_pairs", lambda **kw: [pair])

    out = tmp_home / "dpo.jsonl"
    m = export_dpo(
        archive,
        out,
        signer=None,
        min_delta=0.0,
        allowed_splits=frozenset({TaskSplit.TRAIN_EXPORTABLE}),
    )
    assert m["row_count"] == 0


def test_dpo_refuses_bug_repro_with_non_docker_backend(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="subprocess",
        trusted_fixture_mode=False,
        readiness=DistillationReadiness.GOLD,
        weighted=1.0,
        miner_kind="heuristic",
    )
    loser = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="docker",
        trusted_fixture_mode=False,
        readiness=DistillationReadiness.NEGATIVE,
        weighted=0.0,
        miner_kind="echo",
    )
    archive = TrajectoryArchive(tmp_home)
    pair = _seed_archive_pair(archive, winner, loser, score_delta=1.0)
    monkeypatch.setattr(archive, "preference_pairs", lambda **kw: [pair])

    out = tmp_home / "dpo.jsonl"
    m = export_dpo(
        archive,
        out,
        signer=None,
        min_delta=0.0,
        allowed_splits=frozenset({TaskSplit.TRAIN_EXPORTABLE}),
    )
    assert m["row_count"] == 0, "winner ran on subprocess, must be rejected"


def test_dpo_accepts_docker_backed_pair(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: a real docker-backed positive pair still exports."""
    winner = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="docker",
        trusted_fixture_mode=False,
        readiness=DistillationReadiness.GOLD,
        weighted=1.0,
        miner_kind="heuristic",
    )
    loser = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="docker",
        trusted_fixture_mode=False,
        readiness=DistillationReadiness.NEGATIVE,
        weighted=0.0,
        miner_kind="echo",
    )
    archive = TrajectoryArchive(tmp_home)
    pair = _seed_archive_pair(archive, winner, loser, score_delta=1.0)
    monkeypatch.setattr(archive, "preference_pairs", lambda **kw: [pair])

    out = tmp_home / "dpo.jsonl"
    m = export_dpo(
        archive,
        out,
        signer=None,
        min_delta=0.0,
        allowed_splits=frozenset({TaskSplit.TRAIN_EXPORTABLE}),
    )
    assert m["row_count"] == 1


def test_dpo_refuses_negative_winner(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Winner must be a safe positive example, not NEGATIVE-readiness."""
    winner = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="docker",
        trusted_fixture_mode=False,
        readiness=DistillationReadiness.NEGATIVE,  # <- bad
        weighted=0.1,
        miner_kind="heuristic",
    )
    loser = _forge_bug_repro_trajectory(
        split=TaskSplit.TRAIN_EXPORTABLE,
        sandbox_backend="docker",
        trusted_fixture_mode=False,
        readiness=DistillationReadiness.NEGATIVE,
        weighted=0.0,
        miner_kind="echo",
    )
    archive = TrajectoryArchive(tmp_home)
    pair = _seed_archive_pair(archive, winner, loser, score_delta=0.1)
    monkeypatch.setattr(archive, "preference_pairs", lambda **kw: [pair])

    out = tmp_home / "dpo.jsonl"
    m = export_dpo(
        archive,
        out,
        signer=None,
        min_delta=0.0,
        allowed_splits=frozenset({TaskSplit.TRAIN_EXPORTABLE}),
    )
    assert m["row_count"] == 0


# ---------------------------------------------------------------------------
# Generic-task DPO still works end-to-end (the regression we caught while
# writing this fix).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_dpo_still_exports_for_non_bug_repro_tasks(tmp_home: Path) -> None:
    miners: list[MinerAgent] = [HeuristicAgent("hk_h"), EchoAgent("hk_e")]
    rt = Runtime(
        home=tmp_home,
        miners=miners,
        task_types=[TaskType.CODE_PATCH, TaskType.TOOL_ROUTE, TaskType.CLASSIFY],
    )
    await rt.tick()
    await rt.tick()
    archive = TrajectoryArchive(tmp_home)
    out = tmp_home / "dpo.jsonl"
    m = export_dpo(archive, out, signer=rt.signer, min_delta=0.05)
    assert m["row_count"] > 0, (
        "non-bug_repro DPO pairs must still flow through: the new "
        "safety gate must not collapse normal preference data"
    )
