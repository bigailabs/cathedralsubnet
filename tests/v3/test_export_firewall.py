"""Dataset export firewall: hidden_context must not leak into training rows.

These tests assert the load-bearing contract from the user's review of
the bug_repro / test_gen job spec:

  Hidden verifier fields MUST NOT leak into training prompts or future
  eval sets.

Concretely:
  1. ``prompt_visible_to_miner`` reads only from ``job.public_view``.
  2. Tool results from validator-owned oracle handlers (anything
     containing oracle keys like fails_on_buggy, passes_on_fixed,
     symptom_match, sandbox_backend) are scrubbed before reaching SFT.
  3. The default exporters refuse to include trajectories whose
     ``job.task_split`` is OPERATOR_REVIEW or HELDOUT_EVAL.
  4. Promoting a coding trajectory to TRAIN_EXPORTABLE lets it through,
     but its hidden_context (fixed_source, reference_test_source,
     expected_symptom) still cannot appear in any exported row.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cathedral.v3.archive import TrajectoryArchive
from cathedral.v3.export import export_dpo, export_rm, export_sft
from cathedral.v3.jobs import generate_job
from cathedral.v3.miner import EchoAgent, HeuristicAgent
from cathedral.v3.miner.base import MinerAgent
from cathedral.v3.runtime import Runtime
from cathedral.v3.types import TaskSplit, TaskType


@pytest.fixture()
def tmp_home(tmp_path: Path) -> Path:
    return tmp_path / "cathedral_v3"


# ---------------------------------------------------------------------------
# bug_repro defaults
# ---------------------------------------------------------------------------


def test_bug_repro_default_split_is_operator_review() -> None:
    job = generate_job(TaskType.BUG_REPRO, seed=0)
    assert job.task_split == TaskSplit.OPERATOR_REVIEW


def test_public_view_excludes_hidden_context() -> None:
    job = generate_job(TaskType.BUG_REPRO, seed=0)
    pv = job.public_view()
    assert "hidden_context" not in pv
    blob = json.dumps(pv, default=str, sort_keys=True)
    assert "fixed_source" not in blob, "hidden field leaked into public_view"
    assert "reference_test_source" not in blob
    assert "expected_symptom" not in blob


# ---------------------------------------------------------------------------
# default exports exclude OPERATOR_REVIEW
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_exports_exclude_operator_review_bug_repro(tmp_home: Path) -> None:
    miners: list[MinerAgent] = [EchoAgent("hk_echo"), HeuristicAgent("hk_h")]
    rt = Runtime(home=tmp_home, miners=miners, task_types=[TaskType.BUG_REPRO])
    await rt.tick()
    await rt.tick()
    archive = TrajectoryArchive(tmp_home)

    sft_out = tmp_home / "sft.jsonl"
    m_sft = export_sft(archive, sft_out, min_score=0.5, signer=rt.signer)
    assert m_sft["row_count"] == 0, (
        "default SFT export must not include OPERATOR_REVIEW bug_repro trajectories"
    )

    dpo_out = tmp_home / "dpo.jsonl"
    m_dpo = export_dpo(archive, dpo_out, signer=rt.signer, min_delta=0.1)
    assert m_dpo["row_count"] == 0, (
        "default DPO export must not include OPERATOR_REVIEW bug_repro trajectories"
    )

    rm_out = tmp_home / "rm.jsonl"
    m_rm = export_rm(archive, rm_out, signer=rt.signer)
    assert m_rm["row_count"] == 0, (
        "default RM export must not include OPERATOR_REVIEW bug_repro trajectories"
    )


# ---------------------------------------------------------------------------
# operator-promoted bug_repro: rows exist but never leak hidden fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promoted_bug_repro_exports_but_hides_oracles(tmp_home: Path) -> None:
    miners: list[MinerAgent] = [HeuristicAgent("hk_h"), EchoAgent("hk_e")]
    rt = Runtime(home=tmp_home, miners=miners, task_types=[TaskType.BUG_REPRO])
    await rt.tick()
    archive = TrajectoryArchive(tmp_home)

    # Promote: operator allows OPERATOR_REVIEW into the alpha export.
    promoted = frozenset({TaskSplit.OPERATOR_REVIEW, TaskSplit.TRAIN_EXPORTABLE})

    sft_out = tmp_home / "sft.jsonl"
    m_sft = export_sft(archive, sft_out, min_score=0.5, signer=rt.signer, allowed_splits=promoted)
    assert m_sft["row_count"] > 0
    rows = [json.loads(line) for line in sft_out.read_text().splitlines() if line]
    for row in rows:
        blob = json.dumps(row, default=str)
        # The hidden fields must not appear ANYWHERE in the row.
        for forbidden in (
            "fixed_source",
            "reference_test_source",
            "expected_symptom",
        ):
            assert forbidden not in blob, (
                f"hidden field {forbidden!r} leaked into SFT row: {row.get('trajectory_id')}"
            )
        # The oracle output values from submit_test must be scrubbed.
        # Keys themselves can remain (they describe the tool's response
        # schema, which is public), but the boolean values that
        # constitute the oracle signal must not appear.
        for forbidden_value in (
            '"fails_on_buggy": true',
            '"fails_on_buggy": false',
            '"passes_on_fixed": true',
            '"passes_on_fixed": false',
            '"symptom_match": true',
            '"symptom_match": false',
        ):
            assert forbidden_value not in blob, (
                f"oracle value {forbidden_value!r} leaked into SFT row"
            )
        # The sentinel should appear in any submit_test tool result.
        tool_msgs = [
            m for m in row.get("messages", []) if isinstance(m, dict) and m.get("role") == "tool"
        ]
        oracle_msgs = [m for m in tool_msgs if "fails_on_buggy" in (m.get("content") or "")]
        for m in oracle_msgs:
            assert "<oracle-output>" in (m.get("content") or ""), (
                f"submit_test result not scrubbed: {m.get('content')!r}"
            )


@pytest.mark.asyncio
async def test_promoted_bug_repro_rm_view_carries_no_hidden_fields(tmp_home: Path) -> None:
    rt = Runtime(
        home=tmp_home,
        miners=[HeuristicAgent("hk_h")],
        task_types=[TaskType.BUG_REPRO],
    )
    await rt.tick()
    archive = TrajectoryArchive(tmp_home)

    promoted = frozenset({TaskSplit.OPERATOR_REVIEW})
    rm_out = tmp_home / "rm.jsonl"
    m_rm = export_rm(archive, rm_out, signer=rt.signer, allowed_splits=promoted)
    assert m_rm["row_count"] > 0
    for line in rm_out.read_text().splitlines():
        if not line:
            continue
        row = json.loads(line)
        blob = json.dumps(row, default=str)
        for forbidden in (
            "fixed_source",
            "reference_test_source",
            "expected_symptom",
        ):
            assert forbidden not in blob


# ---------------------------------------------------------------------------
# heldout eval is never exportable, even with explicit allow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heldout_eval_is_never_in_default_exports(tmp_home: Path) -> None:
    # Build a trajectory whose split is HELDOUT_EVAL and confirm it
    # doesn't appear in default-filter exports.
    rt = Runtime(home=tmp_home, miners=[HeuristicAgent("hk_h")])
    job = generate_job(TaskType.CLASSIFY, seed=0).model_copy(
        update={"task_split": TaskSplit.HELDOUT_EVAL}
    )
    t = await rt.run_one(job, rt.miners[0])
    archive = TrajectoryArchive(tmp_home)
    assert archive.get(t.trajectory_id) is not None

    rm_out = tmp_home / "rm.jsonl"
    m_rm = export_rm(archive, rm_out, signer=rt.signer)
    assert m_rm["row_count"] == 0, "HELDOUT_EVAL must never appear in default exports"
