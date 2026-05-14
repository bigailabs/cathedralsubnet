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
async def test_promoted_bug_repro_exports_but_hides_oracles(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense-in-depth: even if every other gate is bypassed, no hidden
    oracle string can appear in an exported row.

    We exercise this by:
      1. Running bug_repro under the trusted-fixture mode so the
         subprocess sandbox is permitted (CI/dev path).
      2. Operator-promoting OPERATOR_REVIEW + accepting NEGATIVE rows
         via RM export (RM has no NEGATIVE refusal, unlike SFT).
      3. Asserting that no row contains any hidden-context value.
    """
    monkeypatch.setenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", "1")
    miners: list[MinerAgent] = [HeuristicAgent("hk_h"), EchoAgent("hk_e")]
    rt = Runtime(home=tmp_home, miners=miners, task_types=[TaskType.BUG_REPRO])
    await rt.tick()
    archive = TrajectoryArchive(tmp_home)

    # Pull the hidden strings the heuristic miner pasted into outputs.
    # We assert at least one of these would have leaked without scrubbing.
    sample = next(iter(archive.iter_all()))
    hidden_strings = []
    for k in ("fixed_source", "reference_test_source", "expected_symptom"):
        v = sample.job.hidden_context.get(k)
        if isinstance(v, str) and v:
            hidden_strings.append(v)
    assert hidden_strings, "bug_repro fixtures must have hidden_context strings"

    # Promote OPERATOR_REVIEW; use RM since it doesn't refuse NEGATIVE.
    promoted = frozenset({TaskSplit.OPERATOR_REVIEW, TaskSplit.TRAIN_EXPORTABLE})

    rm_out = tmp_home / "rm.jsonl"
    m_rm = export_rm(archive, rm_out, signer=rt.signer, allowed_splits=promoted)
    assert m_rm["row_count"] > 0
    rows = [json.loads(line) for line in rm_out.read_text().splitlines() if line]
    for row in rows:
        blob = json.dumps(row, default=str)
        # Hidden field names must not appear.
        for forbidden_key in ("fixed_source", "reference_test_source", "expected_symptom"):
            assert forbidden_key not in blob, (
                f"hidden field {forbidden_key!r} leaked into row {row.get('trajectory_id')}"
            )
        # Hidden field VALUES (the actual reference test source / fixed
        # source / expected symptom) must not appear either. This is the
        # property that matters most.
        for hv in hidden_strings:
            # Skip very short values that could appear by accident
            if len(hv) >= 24:
                assert hv not in blob, (
                    f"hidden value (len={len(hv)}) leaked into RM row: {row.get('trajectory_id')}"
                )


@pytest.mark.asyncio
async def test_promoted_bug_repro_sft_scrubs_tool_args_and_final_output(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strict SFT case: even when the heuristic pastes
    `reference_test_source` into submit_test args AND into final_output,
    the SFT row must not contain it.

    Note: under the hard sandbox gate, bug_repro trajectories from
    subprocess are NEGATIVE and SFT refuses NEGATIVE. So we cannot
    actually emit SFT rows from a subprocess run. We instead synthesize
    a "what if it were exportable" view by lowering the SFT readiness
    bar in this test via direct call to `_sft_row`.
    """
    from cathedral.v3.export.datasets import _sft_row

    monkeypatch.setenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", "1")
    rt = Runtime(
        home=tmp_home,
        miners=[HeuristicAgent("hk_h")],
        task_types=[TaskType.BUG_REPRO],
    )
    r = await rt.tick()
    assert r.trajectories
    traj = r.trajectories[0]
    hidden_strings = [
        v for v in traj.job.hidden_context.values() if isinstance(v, str) and len(v) >= 24
    ]
    assert hidden_strings, "bug_repro must have substantial hidden strings"

    row = _sft_row(traj)
    blob = json.dumps(row, default=str)
    # No hidden value (notably reference_test_source) may appear anywhere.
    for hv in hidden_strings:
        assert hv not in blob, (
            f"hidden value (len={len(hv)}) leaked into SFT row via tool args or final_output"
        )

    # Raw submit_test.test_source must not appear either: the heuristic
    # passes the reference test in as the test_source arg.
    # We verify the assistant turn that called submit_test scrubbed it.
    assistant_calls = [
        m for m in row["messages"] if isinstance(m, dict) and m.get("role") == "assistant"
    ]
    for m in assistant_calls:
        content = m.get("content") or ""
        # The sentinel should be present where the test_source was.
        if "submit_test" in content:
            for hv in hidden_strings:
                if "from buggy" in hv:  # only assert against actual test source
                    assert hv not in content, "submit_test args still carry raw test_source"


@pytest.mark.asyncio
async def test_promoted_bug_repro_rm_view_carries_no_hidden_fields(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS", "1")
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


@pytest.mark.asyncio
async def test_heldout_eval_refused_even_when_explicitly_allowed(tmp_home: Path) -> None:
    """The hard contract: passing `allowed_splits={HELDOUT_EVAL}` MUST
    still export 0 rows. Held-out trajectories in any training row
    invalidate the eval set; we belt-and-braces this at
    `_is_exportable`.
    """
    rt = Runtime(home=tmp_home, miners=[HeuristicAgent("hk_h")])
    job = generate_job(TaskType.CLASSIFY, seed=0).model_copy(
        update={"task_split": TaskSplit.HELDOUT_EVAL}
    )
    t = await rt.run_one(job, rt.miners[0])
    archive = TrajectoryArchive(tmp_home)
    assert archive.get(t.trajectory_id) is not None

    # Try every export with explicit HELDOUT_EVAL allow.
    explicit = frozenset({TaskSplit.HELDOUT_EVAL})

    rm_out = tmp_home / "rm.jsonl"
    m_rm = export_rm(archive, rm_out, signer=rt.signer, allowed_splits=explicit)
    assert m_rm["row_count"] == 0

    sft_out = tmp_home / "sft.jsonl"
    m_sft = export_sft(archive, sft_out, signer=rt.signer, allowed_splits=explicit, min_score=0.0)
    assert m_sft["row_count"] == 0

    # Also: passing a SET that includes both HELDOUT_EVAL and another
    # split must still filter out HELDOUT_EVAL trajectories.
    mixed = frozenset({TaskSplit.HELDOUT_EVAL, TaskSplit.TRAIN_EXPORTABLE})
    rm_out2 = tmp_home / "rm_mixed.jsonl"
    m_rm2 = export_rm(archive, rm_out2, signer=rt.signer, allowed_splits=mixed)
    assert m_rm2["row_count"] == 0
