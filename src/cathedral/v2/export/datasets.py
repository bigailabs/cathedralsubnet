"""SFT / DPO / RM dataset exporters.

Each writes a JSONL file plus a sibling manifest.json with the filter, row
count, and BLAKE3 hash of every row's canonical bytes. The manifest is
signed by the configured ReceiptSigner if one is provided.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import blake3

from cathedral.v2.archive import TrajectoryArchive
from cathedral.v2.receipt import ReceiptSigner
from cathedral.v2.types import (
    DistillationReadiness,
    TaskType,
    Trajectory,
    canonical_json,
)


def export_sft(
    archive: TrajectoryArchive,
    out_path: Path,
    task_type: TaskType | None = None,
    signer: ReceiptSigner | None = None,
    min_score: float = 0.85,
    limit: int = 100_000,
) -> dict:
    """Write `sft.jsonl`: only gold (or score≥min_score) trajectories."""

    def is_gold(t: Trajectory) -> bool:
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
) -> dict:
    """Write `dpo.jsonl` preference pairs (chosen vs. rejected)."""
    pairs = archive.preference_pairs(task_type=task_type, limit=limit, min_delta=min_delta)

    def rows() -> Iterable[dict]:
        for p in pairs:
            winner = archive.get(p.winner_trajectory_id)
            loser = archive.get(p.loser_trajectory_id)
            if not winner or not loser:
                continue
            yield {
                "prompt": _prompt_for(winner),
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
) -> dict:
    """Write `rm.jsonl`: every trajectory with score + dimensions."""

    def rows() -> Iterable[dict]:
        for t in archive.iter_all():
            if task_type and t.job.task_type != task_type:
                continue
            yield {
                "prompt": _prompt_for(t),
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
        filter_={"task_type": task_type.value if task_type else None},
        signer=signer,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sft_row(t: Trajectory) -> dict:
    """SFT row in OpenAI chat format. Includes the tool trace so the
    fine-tuned model learns tool use, not just final answers."""
    messages = [
        {"role": "system", "content": "You are a Cathedral agent. Solve the job."},
        {"role": "user", "content": _prompt_for(t)},
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
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(tc.result, default=str, sort_keys=True)[:2000],
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
    return t.job.prompt


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
