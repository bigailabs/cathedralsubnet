"""Stall watchdog — surfaces silent loops in `/health` (issue #1)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import aiosqlite

from cathedral.validator import queue
from cathedral.validator.health import Health


async def run_stall_watchdog(
    conn: aiosqlite.Connection,
    health: Health,
    after_secs: int = 600,
    interval_secs: float = 30.0,
    stop: asyncio.Event | None = None,
) -> None:
    stop = stop or asyncio.Event()
    while not stop.is_set():
        snap = await health.get()
        now = datetime.now(UTC)
        stalled = (
            snap.last_metagraph_at is None
            or (now - snap.last_metagraph_at).total_seconds() > after_secs
        )

        counts = await queue.counts_by_status(conn)
        await health.update(
            stalled=stalled,
            claims_pending=counts.get("pending", 0),
            claims_verifying=counts.get("verifying", 0),
            claims_verified=counts.get("verified", 0),
            claims_rejected=counts.get("rejected", 0),
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_secs)
        except TimeoutError:
            pass
