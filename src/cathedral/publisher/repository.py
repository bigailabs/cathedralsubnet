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


async def get_card_definition(conn: aiosqlite.Connection, card_id: str) -> dict[str, Any] | None:
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
    attestation_mode: str = "polaris",
    attestation_type: str | None = None,
    attestation_blob: bytes | None = None,
    attestation_verified_at: datetime | None = None,
    discovery_only: bool = False,
) -> None:
    """Insert an `agent_submissions` row.

    `submitted_at_iso` is the canonical wire-format timestamp (ms precision,
    trailing 'Z'). When omitted, derived from `submitted_at` (and may carry
    a `+00:00` offset rather than `Z`).

    The attestation fields default to the back-compat ``polaris`` mode so
    existing call sites are unaffected. Callers explicitly write
    ``attestation_mode='tee'`` (+ blob / type / verified_at) when a
    miner submitted a TEE attestation, and ``attestation_mode='unverified'``
    + ``discovery_only=True`` for discovery submissions.
    """
    await conn.execute(
        """
        INSERT INTO agent_submissions (
            id, miner_hotkey, card_id, bundle_blob_key, bundle_hash,
            bundle_size_bytes, encryption_key_id, bundle_signature,
            display_name, bio, logo_url, soul_md_preview,
            metadata_fingerprint, similarity_check_passed,
            rejection_reason, submitted_at, status, first_mover_at,
            attestation_mode, attestation_type, attestation_blob,
            attestation_verified_at, discovery_only
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?)
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
            attestation_mode,
            attestation_type,
            attestation_blob,
            _to_z(attestation_verified_at) if attestation_verified_at else None,
            1 if discovery_only else 0,
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
    cur = await conn.execute("SELECT * FROM agent_submissions WHERE id = ?", (submission_id,))
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_submission(row, cur.description)


async def list_submissions_by_hotkey(
    conn: aiosqlite.Connection, hotkey: str
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM agent_submissions WHERE miner_hotkey = ? ORDER BY submitted_at DESC",
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
    verified_only: bool = True,
) -> list[dict[str, Any]]:
    """List submissions for a card.

    `verified_only=True` (default) restricts to the leaderboard surface:
    attestation_mode IN ('polaris','tee'), status NOT 'discovery',
    discovery_only=0. The public read endpoints all rely on this filter
    so unverified submissions never appear on the leaderboard, card
    overview, or recent-eval feeds.

    Pass `verified_only=False` only from internal callers that need the
    full set (e.g. discovery-counting helpers, miner-profile fan-out).
    """
    if sort == "score":
        order = "current_score DESC NULLS LAST, submitted_at DESC"
    elif sort == "recent":
        order = "submitted_at DESC"
    elif sort == "oldest":
        order = "submitted_at ASC"
    else:
        raise ValueError(f"invalid sort: {sort}")
    if verified_only:
        cur = await conn.execute(
            f"SELECT * FROM agent_submissions WHERE card_id = ? "
            f"AND status IN ('queued','evaluating','ranked') "
            f"AND attestation_mode IN ('polaris','tee') "
            f"AND discovery_only = 0 "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            (card_id, limit, offset),
        )
    else:
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
    verified_only: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """List all submissions across cards (paginated, with total).

    Same verified_only semantics as `list_submissions_for_card`: defaults
    to the leaderboard surface; callers that want the full table must
    explicitly opt out.
    """
    if sort == "score":
        order = "current_score DESC NULLS LAST, submitted_at DESC"
    elif sort == "recent":
        order = "submitted_at DESC"
    elif sort == "oldest":
        order = "submitted_at ASC"
    else:
        raise ValueError(f"invalid sort: {sort}")
    if verified_only:
        where = (
            "WHERE status IN ('queued','evaluating','ranked') "
            "AND attestation_mode IN ('polaris','tee') "
            "AND discovery_only = 0"
        )
    else:
        where = "WHERE status IN ('queued','evaluating','ranked')"
    cur = await conn.execute(
        f"SELECT * FROM agent_submissions {where} ORDER BY {order} LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cur.fetchall()
    cur2 = await conn.execute(f"SELECT COUNT(*) FROM agent_submissions {where}")
    total_row = await cur2.fetchone()
    total = int(total_row[0]) if total_row else 0
    return [_row_to_submission(r, cur.description) for r in rows], total


# --------------------------------------------------------------------------
# Discovery surface (unverified submissions)
# --------------------------------------------------------------------------


async def list_discovery_submissions_for_card(
    conn: aiosqlite.Connection,
    card_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List unverified / discovery-only submissions for a card.

    The inverse of the verified leaderboard surface: only rows with
    ``attestation_mode='unverified'`` and ``status='discovery'``. Ordered
    by ``submitted_at DESC`` because discovery rows carry no score.
    """
    cur = await conn.execute(
        "SELECT * FROM agent_submissions WHERE card_id = ? "
        "AND attestation_mode = 'unverified' AND status = 'discovery' "
        "ORDER BY submitted_at DESC LIMIT ? OFFSET ?",
        (card_id, limit, offset),
    )
    rows = await cur.fetchall()
    return [_row_to_submission(r, cur.description) for r in rows]


async def count_discovery_submissions_for_card(conn: aiosqlite.Connection, card_id: str) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) FROM agent_submissions WHERE card_id = ? "
        "AND attestation_mode = 'unverified' AND status = 'discovery'",
        (card_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def list_discovery_submissions_recent(
    conn: aiosqlite.Connection,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Cross-card discovery feed for the top-level /research page."""
    cur = await conn.execute(
        "SELECT * FROM agent_submissions "
        "WHERE attestation_mode = 'unverified' AND status = 'discovery' "
        "ORDER BY submitted_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cur.fetchall()
    return [_row_to_submission(r, cur.description) for r in rows]


async def count_verified_agents_for_card(conn: aiosqlite.Connection, card_id: str) -> int:
    """Count verified (non-discovery, attested) submissions for a card.

    Used by `/v1/cards/{card_id}` `agent_count` so the public card overview
    reflects only verified agents — the same surface the leaderboard does.
    """
    cur = await conn.execute(
        "SELECT COUNT(*) FROM agent_submissions WHERE card_id = ? "
        "AND status IN ('queued','evaluating','ranked') "
        "AND attestation_mode IN ('polaris','tee') "
        "AND discovery_only = 0",
        (card_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


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
        "UPDATE agent_submissions SET current_score=?, current_rank=?, status='ranked' WHERE id=?",
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
    if "discovery_only" in out:
        out["discovery_only"] = bool(out["discovery_only"])
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
    polaris_verified: bool = False,
    polaris_attestation: dict[str, Any] | None = None,
    trace_json: dict[str, Any] | None = None,
    polaris_manifest: dict[str, Any] | None = None,
) -> None:
    """Insert an eval_runs row.

    `ran_at_iso` is the canonical wire-format timestamp (ms precision,
    trailing 'Z'). It MUST match the value the cathedral signature was
    computed over, otherwise downstream verifiers will reject. When omitted,
    falls back to `ran_at.isoformat()` (legacy callers).

    `polaris_verified` is True when the eval ran on a Polaris-managed
    runtime and the manifest verified. False for BYO-compute miners. The
    1.10x multiplier is already applied to weighted_score upstream; this
    flag persists the verification status for audit + frontend display.

    `polaris_attestation` is the Polaris-signed proof of execution
    (legacy Tier A flow — cathedral-runtime image). Stored as JSON so
    future verifiers can re-check the Ed25519 signature without re-running
    the eval. None for BYO-compute and legacy stub paths.

    `trace_json` is the Hermes-emitted structured trace from the v2
    Polaris-native deploy flow (tool_calls + model_calls + ...). Stored
    as an UNSIGNED sidecar — not part of cathedral_signature bytes —
    so old validators verify v2 rows unchanged. Promoted to signed in
    v2.1 once the schema settles.

    `polaris_manifest` is the verified manifest pulled from
    `/api/cathedral/v1/agents/{id}/manifest` after the v2 deploy. Stored
    so future audits can re-verify the Ed25519 signature.
    """
    await conn.execute(
        """
        INSERT INTO eval_runs (
            id, submission_id, epoch, round_index, polaris_agent_id,
            polaris_run_id, task_json, output_card_json, output_card_hash,
            score_parts, weighted_score, ran_at, duration_ms, errors,
            cathedral_signature, polaris_verified, polaris_attestation,
            trace_json, polaris_manifest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            1 if polaris_verified else 0,
            json.dumps(polaris_attestation) if polaris_attestation is not None else None,
            json.dumps(trace_json) if trace_json is not None else None,
            json.dumps(polaris_manifest) if polaris_manifest is not None else None,
        ),
    )
    await conn.commit()


async def list_eval_runs_for_submission(
    conn: aiosqlite.Connection, submission_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM eval_runs WHERE submission_id = ? ORDER BY ran_at DESC LIMIT ?",
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
    """Used for `/v1/cards/{id}/feed` and `/v1/cards/{id}/history`.

    Joined against `agent_submissions` and filtered to the verified
    surface — discovery rows can never produce eval_runs in the first
    place (status='discovery' never enters the eval queue), but the
    join is gated for defense-in-depth.
    """
    if since:
        cur = await conn.execute(
            """
            SELECT er.* FROM eval_runs er
            JOIN agent_submissions sub ON sub.id = er.submission_id
            WHERE sub.card_id = ? AND er.ran_at >= ?
              AND sub.status != 'discovery'
              AND sub.attestation_mode IN ('polaris','tee')
              AND sub.discovery_only = 0
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
              AND sub.status != 'discovery'
              AND sub.attestation_mode IN ('polaris','tee')
              AND sub.discovery_only = 0
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
    """Cross-card recent feed used by the validator pull loop AND the
    public `/v1/leaderboard/recent` endpoint.

    Joined against `agent_submissions` and gated on the verified surface
    so unverified discovery submissions never appear on the leaderboard
    feed. Discovery rows never produce eval_runs at all, but the join
    is defense-in-depth in case a future code path inserts one.
    """
    cur = await conn.execute(
        """
        SELECT er.* FROM eval_runs er
        JOIN agent_submissions sub ON sub.id = er.submission_id
        WHERE er.ran_at >= ?
          AND sub.status != 'discovery'
          AND sub.attestation_mode IN ('polaris','tee')
          AND sub.discovery_only = 0
        ORDER BY er.ran_at ASC LIMIT ?
        """,
        (since.isoformat(), limit),
    )
    rows = await cur.fetchall()
    return [_row_to_eval_run(r, cur.description) for r in rows]


async def list_eval_runs_in_window(
    conn: aiosqlite.Connection, *, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM eval_runs WHERE ran_at >= ? AND ran_at < ? ORDER BY id ASC",
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
        "SELECT AVG(weighted_score) FROM eval_runs WHERE submission_id = ? AND ran_at >= ?",
        (submission_id, since),
    )
    row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


async def best_eval_run_for_card(conn: aiosqlite.Connection, card_id: str) -> dict[str, Any] | None:
    """Best (highest-scoring) eval run for a card — verified surface only.

    Discovery / unverified submissions never enter the eval queue, so
    they never have eval_runs rows. But the join is still gated on
    attestation_mode for defense-in-depth: the public `best_eval` field
    on `/v1/cards/{id}` must reflect only attested runs.
    """
    cur = await conn.execute(
        """
        SELECT er.* FROM eval_runs er
        JOIN agent_submissions sub ON sub.id = er.submission_id
        WHERE sub.card_id = ?
          AND sub.status != 'discovery'
          AND sub.attestation_mode IN ('polaris','tee')
          AND sub.discovery_only = 0
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


async def queued_submissions(conn: aiosqlite.Connection, limit: int = 4) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT * FROM agent_submissions WHERE status='queued' ORDER BY submitted_at ASC LIMIT ?",
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
    if out.get("polaris_attestation"):
        out["polaris_attestation"] = json.loads(out["polaris_attestation"])
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


async def get_merkle_anchor(conn: aiosqlite.Connection, epoch: int) -> dict[str, Any] | None:
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
