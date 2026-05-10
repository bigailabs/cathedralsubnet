"""Claim queue operations against sqlite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from cathedral.types import EvidenceBundle, PolarisAgentClaim, ScoreParts


@dataclass
class StoredClaim:
    id: int
    miner_hotkey: str
    work_unit: str
    polaris_agent_id: str
    payload: PolarisAgentClaim
    status: str


async def insert_claim(conn: aiosqlite.Connection, claim: PolarisAgentClaim) -> int:
    """Insert a claim and return its id. If a duplicate (miner_hotkey, work_unit,
    polaris_agent_id) is submitted, return the existing id without changing it."""
    payload = claim.model_dump_json()
    cur = await conn.execute(
        """
        INSERT INTO claims (miner_hotkey, owner_wallet, work_unit, polaris_agent_id,
                            payload_json, status, submitted_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        ON CONFLICT (miner_hotkey, work_unit, polaris_agent_id) DO NOTHING
        RETURNING id
        """,
        (
            claim.miner_hotkey,
            claim.owner_wallet,
            claim.work_unit,
            claim.polaris_agent_id,
            payload,
            claim.submitted_at.isoformat(),
        ),
    )
    row = await cur.fetchone()
    if row is None:
        cur = await conn.execute(
            "SELECT id FROM claims WHERE miner_hotkey=? AND work_unit=? AND polaris_agent_id=?",
            (claim.miner_hotkey, claim.work_unit, claim.polaris_agent_id),
        )
        existing = await cur.fetchone()
        await conn.commit()
        assert existing is not None
        return int(existing[0])
    await conn.commit()
    return int(row[0])


async def claim_pending(conn: aiosqlite.Connection, limit: int = 8) -> list[StoredClaim]:
    """Atomically take up to `limit` pending claims and mark them `verifying`."""
    cur = await conn.execute(
        """
        SELECT id, miner_hotkey, work_unit, polaris_agent_id, payload_json
        FROM claims
        WHERE status='pending'
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = await cur.fetchall()
    out: list[StoredClaim] = []
    for r in rows:
        await conn.execute("UPDATE claims SET status='verifying' WHERE id=?", (r[0],))
        out.append(
            StoredClaim(
                id=int(r[0]),
                miner_hotkey=str(r[1]),
                work_unit=str(r[2]),
                polaris_agent_id=str(r[3]),
                payload=PolarisAgentClaim.model_validate_json(r[4]),
                status="verifying",
            )
        )
    await conn.commit()
    return out


async def mark_verified(
    conn: aiosqlite.Connection,
    claim_id: int,
    miner_hotkey: str,
    bundle: EvidenceBundle,
    score: ScoreParts,
) -> None:
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO evidence_bundles (claim_id, bundle_json, filtered_usage_count, verified_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(claim_id) DO UPDATE SET
            bundle_json=excluded.bundle_json,
            filtered_usage_count=excluded.filtered_usage_count,
            verified_at=excluded.verified_at
        """,
        (
            claim_id,
            bundle.model_dump_json(),
            bundle.filtered_usage_count,
            bundle.verified_at.isoformat(),
        ),
    )
    await conn.execute(
        """
        INSERT INTO scores (claim_id, miner_hotkey, source_quality, freshness, specificity,
                            usefulness, clarity, maintenance, weighted, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(claim_id) DO UPDATE SET
            source_quality=excluded.source_quality,
            freshness=excluded.freshness,
            specificity=excluded.specificity,
            usefulness=excluded.usefulness,
            clarity=excluded.clarity,
            maintenance=excluded.maintenance,
            weighted=excluded.weighted,
            scored_at=excluded.scored_at
        """,
        (
            claim_id,
            miner_hotkey,
            score.source_quality,
            score.freshness,
            score.specificity,
            score.usefulness,
            score.clarity,
            score.maintenance,
            score.weighted(),
            now,
        ),
    )
    await conn.execute(
        "UPDATE claims SET status='verified', verified_at=? WHERE id=?",
        (now, claim_id),
    )
    await conn.commit()


async def mark_rejected(conn: aiosqlite.Connection, claim_id: int, reason: str) -> None:
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "UPDATE claims SET status='rejected', rejection_reason=?, verified_at=? WHERE id=?",
        (reason, now, claim_id),
    )
    await conn.commit()


async def latest_score_per_hotkey(conn: aiosqlite.Connection) -> dict[str, float]:
    """Return latest weighted score per miner hotkey across all verified claims."""
    cur = await conn.execute(
        """
        SELECT miner_hotkey, weighted
        FROM scores s
        WHERE scored_at = (
            SELECT MAX(scored_at) FROM scores WHERE miner_hotkey = s.miner_hotkey
        )
        """
    )
    rows = await cur.fetchall()
    return {str(r[0]): float(r[1]) for r in rows}


async def counts_by_status(conn: aiosqlite.Connection) -> dict[str, int]:
    cur = await conn.execute("SELECT status, COUNT(*) FROM claims GROUP BY status")
    rows = await cur.fetchall()
    return {str(r[0]): int(r[1]) for r in rows}
