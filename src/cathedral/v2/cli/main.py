"""cathedral-v2 CLI.

Subcommands:
  serve           run the full loop for N ticks
  tick            run exactly one tick
  submit-job      run one (task_type, miner) pair and print the trajectory
  inspect         show one trajectory by id
  archive stats   show counts/score distribution
  archive best    show best trajectories for a task type
  archive fails   show failure clusters
  archive miner   show recent trajectories for a miner
  export sft|dpo|rm     write a dataset jsonl + manifest
  replay          re-run a historical job against a different miner
  seed-jobs       run M ticks immediately and exit (warm-start the archive)
  weights         compute & print current per-miner weights
  verify-receipt  verify a stored receipt's signature
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from cathedral.v2.archive import TrajectoryArchive
from cathedral.v2.export import export_dpo, export_rm, export_sft
from cathedral.v2.receipt import (
    ReceiptSigner,
    load_or_create_signing_key,
    verify_receipt,
)
from cathedral.v2.replay import replay
from cathedral.v2.runtime import Runtime, default_home, miner_by_name
from cathedral.v2.scoring import compute_weights, score_trajectory
from cathedral.v2.types import TaskType


def _parse_task_types(s: str | None) -> list[TaskType] | None:
    if not s:
        return None
    return [TaskType(t.strip()) for t in s.split(",") if t.strip()]


def _build_runtime(args) -> Runtime:
    miners = None
    if getattr(args, "miners", None):
        miners = [miner_by_name(m) for m in args.miners.split(",")]
    task_types = _parse_task_types(getattr(args, "task_types", None))
    home = Path(args.home) if getattr(args, "home", None) else default_home()
    return Runtime(home=home, miners=miners, task_types=task_types)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cathedral-v2", description="Cathedral v2 — agentic workforce")
    p.add_argument("--home", help="Override CATHEDRAL_V2_HOME for this command.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="Run the loop for N ticks.")
    sp.add_argument("--ticks", type=int, default=3)
    sp.add_argument("--interval", type=float, default=0.0, help="Seconds between ticks.")
    sp.add_argument("--miners", default=None, help="Comma list: echo,heuristic,llm")
    sp.add_argument("--task-types", default=None)

    sp = sub.add_parser("tick", help="Run exactly one tick.")
    sp.add_argument("--miners", default=None)
    sp.add_argument("--task-types", default=None)

    sp = sub.add_parser("submit-job", help="Run one (task_type, miner) pair.")
    sp.add_argument("--task-type", required=True)
    sp.add_argument("--miner", default="heuristic")
    sp.add_argument("--seed", type=int, default=0)

    sp = sub.add_parser("inspect", help="Show one trajectory by id.")
    sp.add_argument("trajectory_id")

    sp = sub.add_parser("archive", help="Query the archive.")
    asub = sp.add_subparsers(dest="acmd", required=True)
    asub.add_parser("stats")
    a_best = asub.add_parser("best")
    a_best.add_argument("--task-type", required=True)
    a_best.add_argument("--k", type=int, default=5)
    a_fail = asub.add_parser("fails")
    a_fail.add_argument("--task-type", default=None)
    a_miner = asub.add_parser("miner")
    a_miner.add_argument("hotkey")
    a_miner.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("export", help="Export a dataset.")
    esub = sp.add_subparsers(dest="ecmd", required=True)
    for fmt in ("sft", "dpo", "rm"):
        e = esub.add_parser(fmt)
        e.add_argument("--out", required=True)
        e.add_argument("--task-type", default=None)
        if fmt == "sft":
            e.add_argument("--min-score", type=float, default=0.85)
        if fmt == "dpo":
            e.add_argument("--min-delta", type=float, default=0.20)

    sp = sub.add_parser("replay", help="Replay a job against another miner.")
    sp.add_argument("trajectory_id")
    sp.add_argument("--miner", default="heuristic")
    sp.add_argument("--persist", action="store_true")

    sp = sub.add_parser("seed-jobs", help="Run M ticks immediately and exit.")
    sp.add_argument("--count", type=int, default=10)
    sp.add_argument("--miners", default=None)
    sp.add_argument("--task-types", default=None)

    sub.add_parser("weights", help="Show current per-miner weights.")

    sp = sub.add_parser("verify-receipt", help="Verify a stored receipt.")
    sp.add_argument("trajectory_id")

    args = p.parse_args(argv)
    return _dispatch(args)


def _dispatch(args) -> int:
    home = Path(args.home) if args.home else default_home()
    if args.cmd == "serve":
        rt = _build_runtime(args)
        results = asyncio.run(rt.serve(args.ticks, args.interval))
        total = sum(len(r.trajectories) for r in results)
        last_w = results[-1].weights if results else None
        print(f"served {args.ticks} tick(s), produced {total} trajectories")
        if last_w:
            print("weights:", json.dumps(last_w.per_miner, indent=2))
        return 0

    if args.cmd == "tick":
        rt = _build_runtime(args)
        r = asyncio.run(rt.tick())
        print(f"tick produced {len(r.trajectories)} trajectories")
        for t in r.trajectories:
            print(f"  {t.trajectory_id}  {t.miner_kind:>10}  {t.job.task_type.value:>11}  score={t.score.weighted:.3f}  fail={t.score.failure_class.value}")
        return 0

    if args.cmd == "submit-job":
        rt = Runtime(home=home, miners=[miner_by_name(args.miner)])
        from cathedral.v2.jobs import generate_job
        job = generate_job(TaskType(args.task_type), seed=args.seed)
        t = asyncio.run(rt.run_one(job, rt.miners[0]))
        print(json.dumps(t.model_dump(mode="json"), indent=2, default=str))
        return 0

    if args.cmd == "inspect":
        archive = TrajectoryArchive(home)
        t = archive.get(args.trajectory_id)
        if not t:
            print(f"not found: {args.trajectory_id}", file=sys.stderr)
            return 1
        print(json.dumps(t.model_dump(mode="json"), indent=2, default=str))
        return 0

    if args.cmd == "archive":
        archive = TrajectoryArchive(home)
        if args.acmd == "stats":
            s = archive.stats()
            print(json.dumps(s.__dict__, indent=2))
            return 0
        if args.acmd == "best":
            rows = archive.best_of(TaskType(args.task_type), k=args.k)
            for t in rows:
                print(f"{t.trajectory_id}  {t.miner_kind:>10}  score={t.score.weighted:.3f}  "
                      f"failure={t.score.failure_class.value}  ready={t.score.readiness.value}")
            return 0
        if args.acmd == "fails":
            tt = TaskType(args.task_type) if args.task_type else None
            for c in archive.failure_clusters(task_type=tt):
                print(f"{c.task_type.value:>12}  {c.failure_class.value:>22}  n={c.count}  "
                      f"samples={','.join(c.sample_trajectory_ids[:2])}")
            return 0
        if args.acmd == "miner":
            for t in archive.by_miner(args.hotkey, limit=args.limit):
                print(f"{t.trajectory_id}  {t.job.task_type.value:>11}  score={t.score.weighted:.3f}  ready={t.score.readiness.value}")
            return 0
        return 2

    if args.cmd == "export":
        archive = TrajectoryArchive(home)
        sk = load_or_create_signing_key(home)
        signer = ReceiptSigner(sk)
        out = Path(args.out)
        tt = TaskType(args.task_type) if args.task_type else None
        if args.ecmd == "sft":
            m = export_sft(archive, out, task_type=tt, signer=signer, min_score=args.min_score)
        elif args.ecmd == "dpo":
            m = export_dpo(archive, out, task_type=tt, signer=signer, min_delta=args.min_delta)
        elif args.ecmd == "rm":
            m = export_rm(archive, out, task_type=tt, signer=signer)
        else:
            return 2
        print(json.dumps(m, indent=2))
        return 0

    if args.cmd == "replay":
        archive = TrajectoryArchive(home)
        miner = miner_by_name(args.miner)
        div = asyncio.run(replay(archive, args.trajectory_id, miner, persist=args.persist))
        print(json.dumps(
            {
                "original_trajectory_id": div.original.trajectory_id,
                "replayed_trajectory_id": div.replayed.trajectory_id,
                "first_divergent_step": div.first_divergent_step,
                "same_final_output": div.same_final_output,
                "score_delta": div.score_delta,
                "original_score": div.original.score.weighted,
                "replayed_score": div.replayed.score.weighted,
            },
            indent=2,
        ))
        return 0

    if args.cmd == "seed-jobs":
        rt = _build_runtime(args)
        results = asyncio.run(rt.serve(args.count, interval_seconds=0.0))
        total = sum(len(r.trajectories) for r in results)
        print(f"seeded {total} trajectories across {args.count} tick(s)")
        return 0

    if args.cmd == "weights":
        archive = TrajectoryArchive(home)
        w = compute_weights(archive)
        print(json.dumps(w.model_dump(mode="json"), indent=2, default=str))
        return 0

    if args.cmd == "verify-receipt":
        archive = TrajectoryArchive(home)
        r = archive.get_receipt(args.trajectory_id)
        if not r:
            print(f"no receipt for: {args.trajectory_id}", file=sys.stderr)
            return 1
        ok = verify_receipt(r)
        print(json.dumps({"trajectory_id": args.trajectory_id, "valid": ok, "scheme": r.signature_scheme}, indent=2))
        return 0 if ok else 1

    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
