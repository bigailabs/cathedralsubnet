"""Short-oracle-string scrubbing.

`_scrub_text` previously substring-scrubbed only hidden values of
length >= 24, which let short oracle strings like
``expected_symptom = "ZeroDivisionError"`` leak through prose like
"the hidden oracle was ZeroDivisionError." in final outputs.

These tests assert the new key-aware policy:
  - `expected_symptom` (and a handful of similar short-oracle keys)
    get scrubbed when they appear as whole-word substrings in any
    exported text, regardless of length.
  - Common English words are not over-scrubbed.
  - Long source/reference strings keep the existing >=24-char
    threshold behaviour.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cathedral.v3.archive import TrajectoryArchive
from cathedral.v3.archive.store import PreferencePair as _PrefPair
from cathedral.v3.export import export_dpo, export_rm, export_sft
from cathedral.v3.export.datasets import (
    _collect_hidden_strings,
    _scrub_text,
    _sft_row,
)
from cathedral.v3.jobs import generate_job
from cathedral.v3.types import (
    AgentResult,
    DistillationReadiness,
    ScoreParts,
    TaskSplit,
    TaskType,
    Trajectory,
)


def _forge_short_oracle_trajectory(
    *,
    final_output: str,
    expected_symptom: str,
    readiness: DistillationReadiness = DistillationReadiness.GOLD,
    weighted: float = 1.0,
) -> Trajectory:
    """Build a bug_repro trajectory whose hidden_context.expected_symptom
    is a short oracle identifier and whose final_output contains the
    same identifier in prose. The scrubber must redact it.

    Important: we replace the public `prompt` with something neutral
    so any leak of `expected_symptom` we observe is coming from the
    miner output, not from the fixture's issue title.
    """
    base = generate_job(TaskType.BUG_REPRO, seed=0)
    new_hidden = dict(base.hidden_context)
    new_hidden["expected_symptom"] = expected_symptom
    job = base.model_copy(
        update={
            "hidden_context": new_hidden,
            "task_split": TaskSplit.TRAIN_EXPORTABLE,
            "prompt": "Find the bug and write a regression test.",
        }
    )
    now = datetime.now(UTC)
    return Trajectory(
        job=job,
        miner_hotkey="hk_test",
        miner_kind="heuristic",
        tool_calls=[],
        result=AgentResult(final_output=final_output),
        score=ScoreParts(
            dimensions={"submitted": 1.0},
            weighted=weighted,
            verifier_metrics={
                "sandbox_backend": "docker",
                "sandbox_is_real": True,
                "trusted_fixture_mode": False,
            },
            readiness=readiness,
        ),
        started_at=now,
        ended_at=now,
    )


# ---------------------------------------------------------------------------
# direct _scrub_text behaviour
# ---------------------------------------------------------------------------


def test_short_oracle_value_is_scrubbed_in_prose() -> None:
    traj = _forge_short_oracle_trajectory(
        final_output="the test should raise ZeroDivisionError on b=0",
        expected_symptom="ZeroDivisionError",
    )
    hidden = _collect_hidden_strings(traj)
    out = _scrub_text(traj.result.final_output, hidden)
    assert out is not None
    assert "ZeroDivisionError" not in out


def test_short_oracle_value_is_scrubbed_when_followed_by_punctuation() -> None:
    traj = _forge_short_oracle_trajectory(
        final_output="raises ZeroDivisionError.",
        expected_symptom="ZeroDivisionError",
    )
    hidden = _collect_hidden_strings(traj)
    out = _scrub_text(traj.result.final_output, hidden)
    assert out is not None
    assert "ZeroDivisionError" not in out


def test_short_oracle_does_not_scrub_embedded_substrings() -> None:
    """Whole-word match: don't mangle a longer identifier that happens
    to embed the oracle name."""
    traj = _forge_short_oracle_trajectory(
        final_output="we use MyZeroDivisionErrorWrapper here",
        expected_symptom="ZeroDivisionError",
    )
    hidden = _collect_hidden_strings(traj)
    out = _scrub_text(traj.result.final_output, hidden)
    assert out is not None
    # The bare "ZeroDivisionError" is embedded inside another identifier;
    # we should leave it alone to avoid breaking unrelated code/text.
    assert "MyZeroDivisionErrorWrapper" in out


def test_short_oracle_with_common_english_does_not_overscrub() -> None:
    """If an operator stuffed an English word into expected_symptom,
    don't go on a redaction rampage across normal prose. The
    `_looks_like_oracle_identifier` guard requires identifier-shaped
    values, so 'the' or 'error' would be skipped.
    """
    # Plain English "error" is identifier-shaped, so it WOULD be
    # treated as a short oracle and scrubbed inside its own word
    # boundary. Operators are expected to put identifiers there.
    # We mainly want to confirm we don't scrub partial overlaps.
    traj = _forge_short_oracle_trajectory(
        final_output="this errored out earlier and the errors compounded",
        expected_symptom="error",
    )
    hidden = _collect_hidden_strings(traj)
    out = _scrub_text(traj.result.final_output, hidden)
    assert out is not None
    # "errored" and "errors" both embed "error" but should be left intact.
    assert "errored" in out
    assert "errors" in out


def test_long_hidden_strings_still_substring_scrubbed() -> None:
    """The existing threshold for long values must still hold."""
    # Default fixture has a real reference_test_source >=24 chars.
    traj = _forge_short_oracle_trajectory(
        final_output="dummy",
        expected_symptom="X",  # too short to matter here
    )
    # Insert a long hidden string and reference it in the output.
    long_value = traj.job.hidden_context.get("reference_test_source", "")
    assert isinstance(long_value, str) and len(long_value) >= 24
    output_with_leak = f"i am leaking the reference:\n{long_value}\n"
    out = _scrub_text(output_with_leak, _collect_hidden_strings(traj))
    assert out is not None
    assert long_value not in out


# ---------------------------------------------------------------------------
# end-to-end: SFT / DPO / RM must not contain the short oracle string
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_home(tmp_path: Path) -> Path:
    return tmp_path / "cathedral_v3"


def test_sft_row_strips_short_oracle() -> None:
    traj = _forge_short_oracle_trajectory(
        final_output="hint: the bug raises ZeroDivisionError when b=0",
        expected_symptom="ZeroDivisionError",
    )
    row = _sft_row(traj)
    blob = json.dumps(row, default=str)
    assert "ZeroDivisionError" not in blob


def test_rm_export_strips_short_oracle(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    traj = _forge_short_oracle_trajectory(
        final_output="hint: raises ZeroDivisionError when divisor is zero",
        expected_symptom="ZeroDivisionError",
    )
    archive = TrajectoryArchive(tmp_home)
    archive.insert(traj)
    out = tmp_home / "rm.jsonl"
    m = export_rm(
        archive,
        out,
        signer=None,
        allowed_splits=frozenset({TaskSplit.TRAIN_EXPORTABLE}),
    )
    assert m["row_count"] >= 1
    text = out.read_text()
    assert "ZeroDivisionError" not in text


def test_dpo_export_strips_short_oracle(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    winner = _forge_short_oracle_trajectory(
        final_output="raises ZeroDivisionError when b is zero",
        expected_symptom="ZeroDivisionError",
        readiness=DistillationReadiness.GOLD,
        weighted=1.0,
    )
    loser = _forge_short_oracle_trajectory(
        final_output="returns nothing useful, never raises ZeroDivisionError",
        expected_symptom="ZeroDivisionError",
        readiness=DistillationReadiness.DISCARD,
        weighted=0.4,
    )
    # Share job between winner and loser so they make a real pair.
    loser = loser.model_copy(update={"job": winner.job})
    archive = TrajectoryArchive(tmp_home)
    archive.insert(winner)
    archive.insert(loser)
    pair = _PrefPair(
        job_id=winner.job.job_id,
        winner_trajectory_id=winner.trajectory_id,
        loser_trajectory_id=loser.trajectory_id,
        score_delta=0.6,
    )
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
    text = out.read_text()
    assert "ZeroDivisionError" not in text


def test_sft_export_strips_short_oracle(tmp_home: Path) -> None:
    traj = _forge_short_oracle_trajectory(
        final_output="hint: bug raises ZeroDivisionError on b=0",
        expected_symptom="ZeroDivisionError",
        readiness=DistillationReadiness.GOLD,
        weighted=1.0,
    )
    archive = TrajectoryArchive(tmp_home)
    archive.insert(traj)
    out = tmp_home / "sft.jsonl"
    m = export_sft(
        archive,
        out,
        signer=None,
        min_score=0.5,
        allowed_splits=frozenset({TaskSplit.TRAIN_EXPORTABLE}),
    )
    assert m["row_count"] >= 1
    text = out.read_text()
    assert "ZeroDivisionError" not in text
