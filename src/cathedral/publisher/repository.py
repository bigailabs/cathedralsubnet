"""Sqlite reads/writes for v1 launch tables.

Wraps the raw aiosqlite calls used by submit/reads/eval/merkle into
typed helpers. Keeps the SQL in one place so the per-table indexes
(CONTRACTS.md Section 3) stay obvious.

The publisher is the SOLE writer to `card_definitions`,
`agent_submissions`, `eval_runs`, and `merkle_anchors`. The validator
binary only reads from these via the publisher's HTTP API; it never
opens the publisher's sqlite file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

# --------------------------------------------------------------------------
# Card definitions
# --------------------------------------------------------------------------


async def insert_card_definition(
    conn: aiosqlite.Connection,
    *,
    id: str,
    display_name: str,
    jurisdiction: str,
    topic: str,
    description: str,
    eval_spec_md: str,
    source_pool: list[dict[str, Any]],
    task_templates: list[str],
    scoring_rubric: dict[str, Any],
    refresh_cadence_hours: int = 24,
    status: str = "active",
) -> None:
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO card_definitions (
            id, display_name, jurisdiction, topic, description,
            eval_spec_md, source_pool, task_templates, scoring_rubric,
            refresh_cadence_hours, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            display_name=excluded.display_name,
            jurisdiction=excluded.jurisdiction,
            topic=excluded.topic,
            description=excluded.description,
            eval_spec_md=excluded.eval_spec_md,
            source_pool=excluded.source_pool,
            task_templates=excluded.task_templates,
            scoring_rubric=excluded.scoring_rubric,
            refresh_cadence_hours=excluded.refresh_cadence_hours,
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (
            id,
            display_name,
            jurisdiction,
            topic,
            description,
            eval_spec_md,
            json.dumps(source_pool),
            json.dumps(task_templates),
            json.dumps(scoring_rubric),
            refresh_cadence_hours,
            status,
            now,
            now,
        ),
    )
    await conn.commit()


async def get_card_definition(
    conn: aiosqlite.Connection, card_id: str
) -> dict[str, Any] | None:
    cur = await conn.execute(
        """
        SELECT id, display_name, jurisdiction, topic, description,
               eval_spec_md, source_pool, task_templates, scoring_rubric,
               refresh_cadence_hours, status, created_at, updated_at
        FROM card_definitions WHERE id = ?
        """,
        (card_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_card_def(row)


async def list_card_definitions(
    conn: aiosqlite.Connection, *, only_active: bool = True
) -> list[dict[str, Any]]:
    if only_active:
        cur = await conn.execute(
            "SELECT id, display_name, jurisdiction, topic, description, "
            "eval_spec_md, source_pool, task_templates, scoring_rubric, "
            "refresh_cadence_hours, status, created_at, updated_at "
            "FROM card_definitions WHERE status='active'"
        )
    else:
        cur = await conn.execute(
            "SELECT id, display_name, jurisdiction, topic, description, "
            "eval_spec_md, source_pool, task_templates, scoring_rubric, "
            "refresh_cadence_hours, status, created_at, updated_at "
            "FROM card_definitions"
        )
    rows = await cur.fetchall()
    return [_row_to_card_def(r) for r in rows]


def _row_to_card_def(row: aiosqlite.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "display_name": row[1],
        "jurisdiction": row[2],
        "topic": row[3],
        "description": row[4],
        "eval_spec_md": row[5],
        "source_pool": json.loads(row[6]),
        "task_templates": json.loads(row[7]),
        "scoring_rubric": json.loads(row[8]),
        "refresh_cadence_hours": int(row[9]),
        "status": row[10],
        "created_at": row[11],
        "updated_at": row[12],
    }


# --------------------------------------------------------------------------
# Agent submissions
# --------------------------------------------------------------------------


async def insert_agent_submission(
    conn: aiosqlite.Connection,
    *,
    id: str,
    miner_hotkey: str,
    card_id: str,
    bundle_blob_key: str,
    bundle_hash: str,
    bundle_size_bytes: int,
    encryption_key_id: str,
    bundle_signature: str,
    display_name: str,
    bio: str | None,
    logo_url: str | None,
    soul_md_preview: str | None,
    metadata_fingerprint: str,
    similarity_check_passed: bool,
    rejection_reason: str | None,
    status: str,
    submitted_at: datetime,
    first_mover_at: datetime | None,
    submitted_at_iso: str | None = None,
) -> None:
    """Insert an `agent_submissions` row.

    `submitted_at_iso` is the canonical wire-format timestamp (ms precision,
    trailing 'Z'). When omitted, derived from `submitted_at` (and may carry
    a `+00:00` offset rather than `Z`).
    """
    await conn.execute(
        """
        INSERT INTO agent_submissions (
            id, miner_hotkey, card_id, bundle_blob_key, bundle_hash,
            bundle_size_bytes, encryption_key_id, bundle_signature,
            display_name, bio, logo_url, soul_md_preview,
            metadata_fingerprint, similarity_check_passed,
            rejection_reason, submitted_at, status, first_mover_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id,
            miner_hotkey,
            card_id,
            bundle_blob_key,
            bundle_hash,
            bundle_size_bytes,
            encryption_key_id,
            bundle_signature,
            display_name,
            bio,
            logo_url,
            soul_md_preview,
            metadata_fingerprint,
            1 if similarity_check_passed else 0,
            rejection_reason,
            submitted_at_iso or _to_z(submitted_at),
            status,
            _to_z(first_mover_at) if first_mover_at else None,
        ),
    )
    await conn.commit()


def _to_z(dt: datetime) -> str:
    """Render a datetime as ISO-8601 UTC with trailing 'Z' (CONTRACTS.md §9 lock #6)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    s = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    return s + "Z"


async def get_agent_submission(
    conn: aiosqlite.Connection, submission_id: str
) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT * FROM agent_submissions WHERE id = ?", (submission_id,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_submission(row, cur.description)


async def list_submissions_by_hotkey(
    conn: aiosqlite.Connection, hotkey: str
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM agent_submissions WHERE miner_hotkey = ? "
        "ORDER BY submitted_at DESC",
        (hotkey,),
    )
    rows = await cur.fetchall()
    return [_row_to_submission(r, cur.description) for r in rows]


async def list_submissions_for_card(
    conn: aiosqlite.Connection,
    card_id: str,
    *,
    sort: str = "score",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if sort == "score":
        order = "current_score DESC NULLS LAST, submitted_at DESC"
    elif sort == "recent":
        order = "submitted_at DESC"
    elif sort == "oldest":
        order = "submitted_at ASC"
    else:
        raise ValueError(f"invalid sort: {sort}")
    cur = await conn.execute(
        f"SELECT * FROM agent_submissions WHERE card_id = ? "
        f"AND status IN ('queued','evaluating','ranked') "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        (card_id, limit, offset),
    )
    rows = await cur.fetchall()
    return [_row_to_submission(r, cur.description) for r in rows]


async def list_submissions_all(
    conn: aiosqlite.Connection,
    *,
    sort: str = "score",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    if sort == "score":
        order = "current_score DESC NULLS LAST, submitted_at DESC"
    elif sort == "recent":
        order = "submitted_at DESC"
    elif sort == "oldest":
        order = "submitted_at ASC"
    else:
        raise ValueError(f"invalid sort: {sort}")
    cur = await conn.execute(
        f"SELECT * FROM agent_submissions "
        f"WHERE status IN ('queued','evaluating','ranked') "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cur.fetchall()
    cur2 = await conn.execute(
        "SELECT COUNT(*) FROM agent_submissions "
        "WHERE status IN ('queued','evaluating','ranked')"
    )
    total_row = await cur2.fetchone()
    total = int(total_row[0]) if total_row else 0
    return [_row_to_submission(r, cur.description) for r in rows], total


async def update_submission_status(
    conn: aiosqlite.Connection,
    submission_id: str,
    *,
    status: str,
    rejection_reason: str | None = None,
) -> None:
    await conn.execute(
        "UPDATE agent_submissions SET status=?, rejection_reason=COALESCE(?, rejection_reason) "
        "WHERE id=?",
        (status, rejection_reason, submission_id),
    )
    await conn.commit()


async def update_submission_score(
    conn: aiosqlite.Connection,
    submission_id: str,
    *,
    current_score: float,
    current_rank: int,
) -> None:
    await conn.execute(
        "UPDATE agent_submissions SET current_score=?, current_rank=?, status='ranked' "
        "WHERE id=?",
        (current_score, current_rank, submission_id),
    )
    await conn.commit()


async def find_existing_bundle_hash(
    conn: aiosqlite.Connection, card_id: str, bundle_hash: str
) -> dict[str, Any] | None:
    """Section 7.1 check #1: same bundle for same card by ANY hotkey."""
    cur = await conn.execute(
        "SELECT * FROM agent_submissions "
        "WHERE card_id = ? AND bundle_hash = ? AND status != 'withdrawn' LIMIT 1",
        (card_id, bundle_hash),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_submission(row, cur.description)


async def find_metadata_fingerprint_collision(
    conn: aiosqlite.Connection,
    card_id: str,
    metadata_fingerprint: str,
    miner_hotkey: str,
) -> dict[str, Any] | None:
    """Section 7.1 check #4: fingerprint clash from a DIFFERENT hotkey."""
    cur = await conn.execute(
        "SELECT * FROM agent_submissions "
        "WHERE card_id = ? AND metadata_fingerprint = ? "
        "AND miner_hotkey != ? AND status != 'withdrawn' LIMIT 1",
        (card_id, metadata_fingerprint, miner_hotkey),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_submission(row, cur.description)


async def list_recent_display_names(
    conn: aiosqlite.Connection, card_id: str, since: datetime
) -> list[tuple[str, str]]:
    """Returns `(submission_id, display_name)` for fuzzy collision check."""
    cur = await conn.execute(
        "SELECT id, display_name FROM agent_submissions "
        "WHERE card_id = ? AND submitted_at >= ? "
        "AND status IN ('queued','evaluating','ranked')",
        (card_id, since.isoformat()),
    )
    rows = await cur.fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


async def first_mover_for_fingerprint(
    conn: aiosqlite.Connection, card_id: str, metadata_fingerprint: str
) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT * FROM agent_submissions "
        "WHERE card_id = ? AND metadata_fingerprint = ? "
        "AND first_mover_at IS NOT NULL "
        "ORDER BY first_mover_at ASC LIMIT 1",
        (card_id, metadata_fingerprint),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_submission(row, cur.description)


def _row_to_submission(
    row: aiosqlite.Row | tuple[Any, ...],
    description: list[tuple[Any, ...]] | Any,
) -> dict[str, Any]:
    cols = [d[0] for d in description]
    out = dict(zip(cols, row, strict=False))
    if "similarity_check_passed" in out:
        out["similarity_check_passed"] = bool(out["similarity_check_passed"])
    return out


# --------------------------------------------------------------------------
# Eval runs
# --------------------------------------------------------------------------


async def insert_eval_run(
    conn: aiosqlite.Connection,
    *,
    id: str,
    submission_id: str,
    epoch: int,
    round_index: int,
    polaris_agent_id: str,
    polaris_run_id: str,
    task_json: dict[str, Any],
    output_card_json: dict[str, Any],
    output_card_hash: str,
    score_parts: dict[str, Any],
    weighted_score: float,
    ran_at: datetime,
    duration_ms: int,
    errors: list[str] | None,
    cathedral_signature: str,
    ran_at_iso: str | None = None,
) -> None:
    """Insert an eval_runs row.

    `ran_at_iso` is the canonical wire-format timestamp (ms precision,
    trailing 'Z'). It MUST match the value the cathedral signature was
    computed over, otherwise downstream verifiers will reject. When omitted,
    falls back to `ran_at.isoformat()` (legacy callers).
    """
    await conn.execute(
        """
        INSERT INTO eval_runs (
            id, submission_id, epoch, round_index, polaris_agent_id,
            polaris_run_id, task_json, output_card_json, output_card_hash,
            score_parts, weighted_score, ran_at, duration_ms, errors,
            cathedral_signature
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id,
            submission_id,
            epoch,
            round_index,
            polaris_agent_id,
            polaris_run_id,
            json.dumps(task_json),
            json.dumps(output_card_json),
            output_card_hash,
            json.dumps(score_parts),
            weighted_score,
            ran_at_iso or ran_at.isoformat(),
            duration_ms,
            json.dumps(errors) if errors is not None else None,
            cathedral_signature,
        ),
    )
    await conn.commit()


async def list_eval_runs_for_submission(
    conn: aiosqlite.Connection, submission_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM eval_runs WHERE submission_id = ? "
        "ORDER BY ran_at DESC LIMIT ?",
        (submission_id, limit),
    )
    rows = await cur.fetchall()
    return [_row_to_eval_run(r, cur.description) for r in rows]


async def list_eval_runs_for_card(
    conn: aiosqlite.Connection,
    card_id: str,
    *,
    since: datetime | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Used for `/v1/cards/{id}/feed` and `/v1/cards/{id}/history`."""
    if since:
        cur = await conn.execute(
            """
            SELECT er.* FROM eval_runs er
            JOIN agent_submissions sub ON sub.id = er.submission_id
            WHERE sub.card_id = ? AND er.ran_at >= ?
            ORDER BY er.ran_at DESC LIMIT ?
            """,
            (card_id, since.isoformat(), limit),
        )
    else:
        cur = await conn.execute(
            """
            SELECT er.* FROM eval_runs er
            JOIN agent_submissions sub ON sub.id = er.submission_id
            WHERE sub.card_id = ?
            ORDER BY er.ran_at DESC LIMIT ?
            """,
            (card_id, limit),
        )
    rows = await cur.fetchall()
    return [_row_to_eval_run(r, cur.description) for r in rows]


async def list_eval_runs_recent(
    conn: aiosqlite.Connection,
    *,
    since: datetime,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Cross-card recent feed for the validator pull endpoint."""
    cur = await conn.execute(
        "SELECT * FROM eval_runs WHERE ran_at >= ? "
        "ORDER BY ran_at ASC LIMIT ?",
        (since.isoformat(), limit),
    )
    rows = await cur.fetchall()
    return [_row_to_eval_run(r, cur.description) for r in rows]


async def list_eval_runs_in_window(
    conn: aiosqlite.Connection, *, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM eval_runs WHERE ran_at >= ? AND ran_at < ? "
        "ORDER BY id ASC",
        (start.isoformat(), end.isoformat()),
    )
    rows = await cur.fetchall()
    return [_row_to_eval_run(r, cur.description) for r in rows]


async def rolling_avg_score(
    conn: aiosqlite.Connection, submission_id: str, *, days: int = 30
) -> float | None:
    """30-day rolling average of weighted scores for a submission."""
    from datetime import timedelta

    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    cur = await conn.execute(
        "SELECT AVG(weighted_score) FROM eval_runs "
        "WHERE submission_id = ? AND ran_at >= ?",
        (submission_id, since),
    )
    row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


async def best_eval_run_for_card(
    conn: aiosqlite.Connection, card_id: str
) -> dict[str, Any] | None:
    cur = await conn.execute(
        """
        SELECT er.* FROM eval_runs er
        JOIN agent_submissions sub ON sub.id = er.submission_id
        WHERE sub.card_id = ?
        ORDER BY er.weighted_score DESC, er.ran_at DESC LIMIT 1
        """,
        (card_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_eval_run(row, cur.description)


async def incumbent_best_score(
    conn: aiosqlite.Connection,
    card_id: str,
    submitted_before: datetime,
) -> float | None:
    """Section 7.2: max weighted_score for any prior submission on this card."""
    cur = await conn.execute(
        """
        SELECT MAX(er.weighted_score) FROM eval_runs er
        JOIN agent_submissions sub ON sub.id = er.submission_id
        WHERE sub.card_id = ? AND sub.submitted_at < ?
        """,
        (card_id, submitted_before.isoformat()),
    )
    row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


async def queued_submissions(
    conn: aiosqlite.Connection, limit: int = 4
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM agent_submissions WHERE status='queued' "
        "ORDER BY submitted_at ASC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    return [_row_to_submission(r, cur.description) for r in rows]


def _row_to_eval_run(
    row: aiosqlite.Row | tuple[Any, ...],
    description: list[tuple[Any, ...]] | Any,
) -> dict[str, Any]:
    cols = [d[0] for d in description]
    out = dict(zip(cols, row, strict=False))
    out["task_json"] = json.loads(out["task_json"])
    out["output_card_json"] = json.loads(out["output_card_json"])
    out["score_parts"] = json.loads(out["score_parts"])
    if out.get("errors"):
        out["errors"] = json.loads(out["errors"])
    return out


# --------------------------------------------------------------------------
# Merkle anchors
# --------------------------------------------------------------------------


async def insert_merkle_anchor(
    conn: aiosqlite.Connection,
    *,
    epoch: int,
    merkle_root: str,
    eval_count: int,
    leaf_hashes: list[str],
    computed_at: datetime,
    on_chain_block: int | None = None,
    on_chain_extrinsic_index: int | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO merkle_anchors (
            epoch, merkle_root, eval_count, leaf_hashes_json,
            computed_at, on_chain_block, on_chain_extrinsic_index
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(epoch) DO UPDATE SET
            merkle_root=excluded.merkle_root,
            eval_count=excluded.eval_count,
            leaf_hashes_json=excluded.leaf_hashes_json,
            computed_at=excluded.computed_at,
            on_chain_block=excluded.on_chain_block,
            on_chain_extrinsic_index=excluded.on_chain_extrinsic_index
        """,
        (
            epoch,
            merkle_root,
            eval_count,
            json.dumps(leaf_hashes),
            computed_at.isoformat(),
            on_chain_block,
            on_chain_extrinsic_index,
        ),
    )
    await conn.commit()


async def get_merkle_anchor(
    conn: aiosqlite.Connection, epoch: int
) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT epoch, merkle_root, eval_count, leaf_hashes_json, "
        "computed_at, on_chain_block, on_chain_extrinsic_index "
        "FROM merkle_anchors WHERE epoch = ?",
        (epoch,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "epoch": int(row[0]),
        "merkle_root": str(row[1]),
        "eval_count": int(row[2]),
        "leaf_hashes": json.loads(row[3]),
        "computed_at": row[4],
        "on_chain_block": int(row[5]) if row[5] is not None else None,
        "on_chain_extrinsic_index": int(row[6]) if row[6] is not None else None,
    }


async def latest_merkle_epoch(conn: aiosqlite.Connection) -> int | None:
    cur = await conn.execute("SELECT MAX(epoch) FROM merkle_anchors")
    row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


async def link_eval_runs_to_epoch(
    conn: aiosqlite.Connection, eval_run_ids: list[str], epoch: int
) -> None:
    if not eval_run_ids:
        return
    await conn.executemany(
        "INSERT OR REPLACE INTO eval_run_to_epoch (eval_run_id, epoch) VALUES (?, ?)",
        [(rid, epoch) for rid in eval_run_ids],
    )
    await conn.commit()
