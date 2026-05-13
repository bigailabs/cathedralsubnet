"""SQLite-backed trajectory archive.

One row per trajectory. Artifacts (long outputs, diffs, traces) live as
files under `artifacts/<trajectory_id>/` and are referenced by hash.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cathedral.v2.types import (
    DistillationReadiness,
    FailureClass,
    Receipt,
    TaskType,
    Trajectory,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trajectories (
    trajectory_id   TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    miner_hotkey    TEXT NOT NULL,
    miner_kind      TEXT NOT NULL,
    score           REAL NOT NULL,
    failure_class   TEXT NOT NULL,
    readiness       TEXT NOT NULL,
    bundle_hash     TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT NOT NULL,
    tool_call_count INTEGER NOT NULL,
    body_json       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_traj_miner       ON trajectories(miner_hotkey);
CREATE INDEX IF NOT EXISTS ix_traj_task        ON trajectories(task_type);
CREATE INDEX IF NOT EXISTS ix_traj_job         ON trajectories(job_id);
CREATE INDEX IF NOT EXISTS ix_traj_score       ON trajectories(score);
CREATE INDEX IF NOT EXISTS ix_traj_readiness   ON trajectories(readiness);
CREATE INDEX IF NOT EXISTS ix_traj_failure     ON trajectories(failure_class);
CREATE INDEX IF NOT EXISTS ix_traj_started_at  ON trajectories(started_at);

CREATE TABLE IF NOT EXISTS receipts (
    trajectory_id TEXT PRIMARY KEY,
    receipt_json  TEXT NOT NULL,
    signed_at     TEXT NOT NULL,
    FOREIGN KEY (trajectory_id) REFERENCES trajectories(trajectory_id)
);
"""


@dataclass
class ArchiveStats:
    total: int
    by_task: dict[str, int]
    by_readiness: dict[str, int]
    by_miner: dict[str, int]
    mean_score: float
    failure_classes: dict[str, int]


@dataclass
class FailureCluster:
    failure_class: FailureClass
    task_type: TaskType
    count: int
    sample_trajectory_ids: list[str] = field(default_factory=list)


@dataclass
class PreferencePair:
    job_id: str
    winner_trajectory_id: str
    loser_trajectory_id: str
    score_delta: float


class TrajectoryArchive:
    """SQLite archive. Thread-safe by way of opening a new connection per call."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.home / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "archive.db"
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    # -- writes ----------------------------------------------------------

    def insert(self, traj: Trajectory, receipt: Receipt | None = None) -> str:
        """Persist a trajectory. Computes bundle_hash if absent. Returns id."""
        if not traj.bundle_hash:
            traj.bundle_hash = traj.compute_bundle_hash()

        # spill long artifacts to disk
        traj_dir = self.artifacts_dir / traj.trajectory_id
        traj_dir.mkdir(parents=True, exist_ok=True)
        (traj_dir / "trajectory.json").write_bytes(traj.canonical_bytes())
        if receipt:
            (traj_dir / "receipt.json").write_text(
                json.dumps(receipt.model_dump(mode="json"), sort_keys=True, indent=2)
            )

        body = traj.model_dump(mode="json")
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO trajectories "
                "(trajectory_id, job_id, task_type, miner_hotkey, miner_kind, score, "
                " failure_class, readiness, bundle_hash, started_at, ended_at, "
                " tool_call_count, body_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    traj.trajectory_id,
                    traj.job.job_id,
                    traj.job.task_type.value,
                    traj.miner_hotkey,
                    traj.miner_kind,
                    traj.score.weighted,
                    traj.score.failure_class.value,
                    traj.score.readiness.value,
                    traj.bundle_hash,
                    traj.started_at.isoformat(),
                    traj.ended_at.isoformat(),
                    len(traj.tool_calls),
                    json.dumps(body, sort_keys=True),
                ),
            )
            if receipt:
                c.execute(
                    "INSERT OR REPLACE INTO receipts (trajectory_id, receipt_json, signed_at) "
                    "VALUES (?,?,?)",
                    (
                        traj.trajectory_id,
                        json.dumps(receipt.model_dump(mode="json"), sort_keys=True),
                        receipt.signed_at.isoformat(),
                    ),
                )
        return traj.trajectory_id

    # -- reads -----------------------------------------------------------

    def get(self, trajectory_id: str) -> Trajectory | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT body_json FROM trajectories WHERE trajectory_id=?",
                (trajectory_id,),
            ).fetchone()
        if not row:
            return None
        return Trajectory.model_validate_json(row["body_json"])

    def get_receipt(self, trajectory_id: str) -> Receipt | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT receipt_json FROM receipts WHERE trajectory_id=?",
                (trajectory_id,),
            ).fetchone()
        if not row:
            return None
        return Receipt.model_validate_json(row["receipt_json"])

    def by_miner(
        self,
        hotkey: str,
        limit: int = 50,
        since: datetime | None = None,
    ) -> list[Trajectory]:
        q = "SELECT body_json FROM trajectories WHERE miner_hotkey=?"
        params: list = [hotkey]
        if since:
            q += " AND started_at >= ?"
            params.append(since.isoformat())
        q += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(q, params).fetchall()
        return [Trajectory.model_validate_json(r["body_json"]) for r in rows]

    def by_task_type(
        self,
        task_type: TaskType,
        score_min: float = 0.0,
        limit: int = 50,
    ) -> list[Trajectory]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT body_json FROM trajectories WHERE task_type=? AND score >= ? "
                "ORDER BY score DESC LIMIT ?",
                (task_type.value, score_min, limit),
            ).fetchall()
        return [Trajectory.model_validate_json(r["body_json"]) for r in rows]

    def best_of(self, task_type: TaskType, k: int = 10) -> list[Trajectory]:
        return self.by_task_type(task_type, score_min=0.0, limit=k)

    def by_job(self, job_id: str) -> list[Trajectory]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT body_json FROM trajectories WHERE job_id=? ORDER BY score DESC",
                (job_id,),
            ).fetchall()
        return [Trajectory.model_validate_json(r["body_json"]) for r in rows]

    def by_readiness(
        self,
        readiness: DistillationReadiness,
        limit: int = 500,
    ) -> Iterable[Trajectory]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT body_json FROM trajectories WHERE readiness=? ORDER BY score DESC LIMIT ?",
                (readiness.value, limit),
            ).fetchall()
        for r in rows:
            yield Trajectory.model_validate_json(r["body_json"])

    def failure_clusters(
        self,
        task_type: TaskType | None = None,
        sample_size: int = 3,
    ) -> list[FailureCluster]:
        q = (
            "SELECT failure_class, task_type, COUNT(*) AS n "
            "FROM trajectories WHERE failure_class != 'none' "
        )
        params: list = []
        if task_type:
            q += "AND task_type=? "
            params.append(task_type.value)
        q += "GROUP BY failure_class, task_type ORDER BY n DESC"
        with self._conn() as c:
            rows = c.execute(q, params).fetchall()
            clusters: list[FailureCluster] = []
            for r in rows:
                samples = c.execute(
                    "SELECT trajectory_id FROM trajectories "
                    "WHERE failure_class=? AND task_type=? LIMIT ?",
                    (r["failure_class"], r["task_type"], sample_size),
                ).fetchall()
                clusters.append(
                    FailureCluster(
                        failure_class=FailureClass(r["failure_class"]),
                        task_type=TaskType(r["task_type"]),
                        count=r["n"],
                        sample_trajectory_ids=[s["trajectory_id"] for s in samples],
                    )
                )
        return clusters

    def preference_pairs(
        self,
        task_type: TaskType | None = None,
        limit: int = 100,
        min_delta: float = 0.05,
    ) -> list[PreferencePair]:
        """For each job with ≥2 trajectories, emit (best, worst) pair."""
        q = "SELECT job_id, trajectory_id, score, task_type FROM trajectories"
        params: list = []
        if task_type:
            q += " WHERE task_type=?"
            params.append(task_type.value)
        q += " ORDER BY job_id, score DESC"
        with self._conn() as c:
            rows = c.execute(q, params).fetchall()

        by_job: dict[str, list] = defaultdict(list)
        for r in rows:
            by_job[r["job_id"]].append(r)

        pairs: list[PreferencePair] = []
        for job_id, ranked in by_job.items():
            if len(ranked) < 2:
                continue
            winner, loser = ranked[0], ranked[-1]
            delta = winner["score"] - loser["score"]
            if delta < min_delta:
                continue
            pairs.append(
                PreferencePair(
                    job_id=job_id,
                    winner_trajectory_id=winner["trajectory_id"],
                    loser_trajectory_id=loser["trajectory_id"],
                    score_delta=delta,
                )
            )
            if len(pairs) >= limit:
                break
        return pairs

    def stats(self) -> ArchiveStats:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM trajectories").fetchone()["n"]
            by_task = {
                r["task_type"]: r["n"]
                for r in c.execute(
                    "SELECT task_type, COUNT(*) AS n FROM trajectories GROUP BY task_type"
                ).fetchall()
            }
            by_readiness = {
                r["readiness"]: r["n"]
                for r in c.execute(
                    "SELECT readiness, COUNT(*) AS n FROM trajectories GROUP BY readiness"
                ).fetchall()
            }
            by_miner = {
                r["miner_hotkey"]: r["n"]
                for r in c.execute(
                    "SELECT miner_hotkey, COUNT(*) AS n FROM trajectories GROUP BY miner_hotkey"
                ).fetchall()
            }
            mean_score_row = c.execute("SELECT AVG(score) AS m FROM trajectories").fetchone()
            mean_score = mean_score_row["m"] or 0.0
            failure_classes = {
                r["failure_class"]: r["n"]
                for r in c.execute(
                    "SELECT failure_class, COUNT(*) AS n FROM trajectories GROUP BY failure_class"
                ).fetchall()
            }
        return ArchiveStats(
            total=total,
            by_task=by_task,
            by_readiness=by_readiness,
            by_miner=by_miner,
            mean_score=mean_score,
            failure_classes=failure_classes,
        )

    def iter_all(self) -> Iterable[Trajectory]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT body_json FROM trajectories ORDER BY started_at ASC"
            ).fetchall()
        for r in rows:
            yield Trajectory.model_validate_json(r["body_json"])
