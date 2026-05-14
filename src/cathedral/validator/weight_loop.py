"""Weight-set loop — joins scores to uids and pushes to chain on a timer."""

from __future__ import annotations

import asyncio

import aiosqlite
import structlog

from cathedral.chain import Chain, apply_burn, normalize
from cathedral.chain.client import WeightStatus
from cathedral.validator import queue
from cathedral.validator.health import Health
from cathedral.validator.pull_loop import latest_pulled_score_per_hotkey

logger = structlog.get_logger(__name__)


async def run_weight_loop(
    conn: aiosqlite.Connection,
    chain: Chain,
    health: Health,
    interval_secs: int = 1200,
    disabled: bool = False,
    burn_uid: int = 204,
    forced_burn_percentage: float = 98.0,
    stop: asyncio.Event | None = None,
    initial_backfill_complete: asyncio.Event | None = None,
    initial_backfill_timeout_secs: float = 120.0,
) -> None:
    stop = stop or asyncio.Event()
    # Wait for the pull_loop's first drained catch-up pass before the
    # first weight set. Without this, a freshly-upgraded validator can
    # publish a vector computed from a half-hydrated 7-day window. The
    # event is set permanently after the first complete pass, so
    # subsequent iterations are unblocked. Timeout caps the wait so a
    # broken pull loop can't pin the weight loop forever — if the
    # backfill hasn't completed in 2 minutes, fall through and publish
    # with whatever's in the local DB (better than no weights at all).
    if initial_backfill_complete is not None and not initial_backfill_complete.is_set():
        logger.info(
            "weight_loop_waiting_for_backfill",
            timeout_secs=initial_backfill_timeout_secs,
        )
        try:
            await asyncio.wait_for(
                initial_backfill_complete.wait(),
                timeout=initial_backfill_timeout_secs,
            )
            logger.info("weight_loop_backfill_signal_received")
        except TimeoutError:
            logger.warning(
                "weight_loop_backfill_timeout",
                timeout_secs=initial_backfill_timeout_secs,
            )
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
            # V1: blend pulled scores from publisher (canonical going forward).
            # Pulled scores override legacy claim-derived scores per hotkey.
            try:
                # 7-day window matches the cadence of card refresh + miner
                # iteration. A 30-day mean penalises a miner who fixed a
                # schema bug yesterday by averaging in last week's zero
                # scores; in practice that anti-recency bias is what's
                # been pinning legitimate masons near zero weight. 7d
                # lets a debugged miner climb within a day of shipping
                # a real card, while still smoothing out single-eval
                # noise. Full time-decayed mean is the next iteration.
                pulled = await latest_pulled_score_per_hotkey(conn, since_days=7)
                scores.update(pulled)
            except Exception as ex:
                logger.debug("pulled_scores_unavailable", error=str(ex))
            uid_by_hotkey = metagraph.hotkey_to_uid()
            # Observability: surface which hotkeys we know vs. which the
            # metagraph drops. Without this you only see `count=N` in the
            # weights_set line and have no idea why N is smaller than the
            # number of masons producing scored cards. Drops are usually
            # test hotkeys that never registered on chain.
            unmapped = [hk for hk in scores if hk not in uid_by_hotkey]
            raw = [(uid_by_hotkey[hk], s) for hk, s in scores.items() if hk in uid_by_hotkey]
            positive = [(uid, s) for uid, s in raw if s > 0]
            logger.info(
                "weights_pre_burn",
                total_hotkeys=len(scores),
                mapped_hotkeys=len(raw),
                positive_hotkeys=len(positive),
                unmapped_count=len(unmapped),
                # 8-char prefixes keep logs scannable without leaking too much
                unmapped_sample=[hk[:8] for hk in unmapped[:5]],
                positive_sample=[(uid, round(s, 3)) for uid, s in positive[:5]],
            )
            burned = apply_burn(
                raw,
                burn_uid=burn_uid,
                forced_burn_percentage=forced_burn_percentage,
            )
            normalized = normalize(burned)

            status = WeightStatus.DISABLED if disabled else await chain.set_weights(normalized)
            await health.update(weight_status=status)
            await health.heartbeat("last_weight_set_at")
            logger.info(
                "weights_set",
                count=len(normalized),
                status=status.value,
                # Surface which uids actually shipped so operators can sanity-
                # check against the on-chain weight set without diffing logs.
                uids=[uid for uid, _ in normalized][:20],
            )
        except Exception as e:
            logger.warning("weight_loop_error", error=str(e))
            await health.update(weight_status=WeightStatus.BLOCKED_BY_TRANSACTION_ERROR)

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_secs)
        except TimeoutError:
            pass
