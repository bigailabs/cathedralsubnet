"""End-to-end smoke test for Cathedral v2.

Runs a full tick across every task type and miner, verifies:
  - trajectories persist
  - receipts verify
  - exports produce non-empty jsonl
  - replay produces a divergence record
  - weights compute and normalize to ~1.0
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cathedral.v2.archive import TrajectoryArchive
from cathedral.v2.export import export_dpo, export_rm, export_sft
from cathedral.v2.jobs import generate_job
from cathedral.v2.miner import EchoAgent, HeuristicAgent
from cathedral.v2.miner.llm import LLMAgent
from cathedral.v2.receipt import verify_receipt
from cathedral.v2.replay import replay
from cathedral.v2.runtime import Runtime
from cathedral.v2.scoring import compute_weights
from cathedral.v2.types import TaskType


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    return tmp_path / "cathedral_v2"


@pytest.mark.asyncio
async def test_one_tick_end_to_end(tmp_home: Path) -> None:
    miners = [EchoAgent("hk_echo"), HeuristicAgent("hk_heuristic"), LLMAgent("hk_llm")]
    rt = Runtime(home=tmp_home, miners=miners)
    r = await rt.tick()
    # 5 task types x 3 miners = 15 trajectories
    assert len(r.trajectories) == 15
    # every trajectory has a bundle hash
    for t in r.trajectories:
        assert t.bundle_hash, f"missing bundle hash on {t.trajectory_id}"
    # archive recall works
    archive = TrajectoryArchive(tmp_home)
    stats = archive.stats()
    assert stats.total == 15
    assert set(stats.by_task) == {tt.value for tt in TaskType}
    # the heuristic miner should win at least once
    assert any(t.miner_kind == "heuristic" and t.score.weighted >= 0.85 for t in r.trajectories), (
        "heuristic should hit gold somewhere"
    )


@pytest.mark.asyncio
async def test_receipt_signatures_verify(tmp_home: Path) -> None:
    rt = Runtime(home=tmp_home, miners=[HeuristicAgent("hk_h")])
    job = generate_job(TaskType.CLASSIFY, seed=1)
    t = await rt.run_one(job, rt.miners[0])
    archive = TrajectoryArchive(tmp_home)
    receipt = archive.get_receipt(t.trajectory_id)
    assert receipt is not None
    assert verify_receipt(receipt)
    # tampering breaks the signature
    bad = receipt.model_copy(update={"score": 0.0})
    assert not verify_receipt(bad)


@pytest.mark.asyncio
async def test_exports_produce_nonempty(tmp_home: Path) -> None:
    rt = Runtime(home=tmp_home, miners=[HeuristicAgent("hk_h"), EchoAgent("hk_e")])
    # 2 ticks for preference pairs
    await rt.tick()
    await rt.tick()
    archive = TrajectoryArchive(tmp_home)

    sft_out = tmp_home / "sft.jsonl"
    m_sft = export_sft(archive, sft_out, min_score=0.5, signer=rt.signer)
    assert m_sft["row_count"] > 0, m_sft
    assert sft_out.with_suffix(".manifest.json").exists()

    dpo_out = tmp_home / "dpo.jsonl"
    m_dpo = export_dpo(archive, dpo_out, signer=rt.signer, min_delta=0.05)
    assert m_dpo["row_count"] > 0, m_dpo

    rm_out = tmp_home / "rm.jsonl"
    m_rm = export_rm(archive, rm_out, signer=rt.signer)
    assert m_rm["row_count"] > 0


@pytest.mark.asyncio
async def test_replay_divergence(tmp_home: Path) -> None:
    rt = Runtime(home=tmp_home, miners=[EchoAgent("hk_e")])
    job = generate_job(TaskType.TOOL_ROUTE, seed=7)
    t = await rt.run_one(job, rt.miners[0])
    # replay with a stronger miner
    archive = TrajectoryArchive(tmp_home)
    div = await replay(archive, t.trajectory_id, HeuristicAgent("hk_h"))
    # echo doesn't call the right tool; heuristic does — divergence should be at step 0
    assert div.first_divergent_step is not None
    # heuristic should score higher
    assert div.score_delta > 0


@pytest.mark.asyncio
async def test_weights_normalize(tmp_home: Path) -> None:
    rt = Runtime(home=tmp_home, miners=[EchoAgent("hk_e"), HeuristicAgent("hk_h")])
    await rt.tick()
    w = compute_weights(rt.archive)
    if w.per_miner:
        total = sum(w.per_miner.values())
        assert abs(total - 1.0) < 1e-6, f"weights should sum to 1.0, got {total}"


@pytest.mark.asyncio
async def test_failure_classification(tmp_home: Path) -> None:
    rt = Runtime(home=tmp_home, miners=[EchoAgent("hk_e")])
    job = generate_job(TaskType.CLASSIFY, seed=2)
    t = await rt.run_one(job, rt.miners[0])
    # echo doesn't call the label tool — should be classified as a failure
    assert t.score.weighted < 0.5
    assert t.score.failure_class.value != "none"
