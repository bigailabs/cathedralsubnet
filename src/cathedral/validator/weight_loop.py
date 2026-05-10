"""Weight-set loop — joins scores to uids and pushes to chain on a timer."""

from __future__ import annotations

import asyncio

import aiosqlite
import structlog

from cathedral.chain import Chain, normalize
from cathedral.chain.client import WeightStatus
from cathedral.validator import queue
from cathedral.validator.health import Health

logger = structlog.get_logger(__name__)


async def run_weight_loop(
    conn: aiosqlite.Connection,
    chain: Chain,
    health: Health,
    interval_secs: int = 1200,
    disabled: bool = False,
    stop: asyncio.Event | None = None,
) -> None:
    stop = stop or asyncio.Event()
    if disabled:
        # Even in dry mode we still want metagraph reads + registration check
        # so the runbook surfaces real state. We just skip set_weights.
        await health.update(weight_status=WeightStatus.DISABLED)
        while not stop.is_set():
            try:
                metagraph = await chain.metagraph()
                registered = await chain.is_registered()
                await health.update(current_block=metagraph.block, registered=registered)
                await health.heartbeat("last_metagraph_at")
            except Exception as e:
                logger.warning("metagraph_read_error", error=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_secs)
            except TimeoutError:
                pass
        return

    while not stop.is_set():
        try:
            metagraph = await chain.metagraph()
            registered = await chain.is_registered()
            await health.update(
                current_block=metagraph.block,
                registered=registered,
            )
            await health.heartbeat("last_metagraph_at")

            scores = await queue.latest_score_per_hotkey(conn)
            uid_by_hotkey = metagraph.hotkey_to_uid()
            raw = [(uid_by_hotkey[hk], s) for hk, s in scores.items() if hk in uid_by_hotkey]
            normalized = normalize(raw)

            status = await chain.set_weights(normalized)
            await health.update(weight_status=status)
            await health.heartbeat("last_weight_set_at")
            logger.info(
                "weights_set",
                count=len(normalized),
                status=status.value,
            )
        except Exception as e:
            logger.warning("weight_loop_error", error=str(e))
            await health.update(weight_status=WeightStatus.BLOCKED_BY_TRANSACTION_ERROR)

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_secs)
        except TimeoutError:
            pass
