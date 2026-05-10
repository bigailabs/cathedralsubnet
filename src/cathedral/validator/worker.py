"""Verification worker — drains the claim queue.

For each pending claim:
1. Run `EvidenceCollector.collect()`.
2. Decode the first verified artifact as a Card; if none decode, the claim
   is rejected with `no_card_artifact`.
3. Run preflight; failures reject the claim with the preflight reason.
4. Run the scorer; persist score + bundle, mark claim verified.
"""

from __future__ import annotations

import asyncio
import json

import aiosqlite
import structlog

from cathedral.cards import preflight, score_card
from cathedral.cards.preflight import PreflightError
from cathedral.cards.registry import CardRegistry
from cathedral.evidence import CollectionError, EvidenceCollector
from cathedral.types import Card
from cathedral.validator import queue
from cathedral.validator.health import Health

logger = structlog.get_logger(__name__)


async def run_worker(
    conn: aiosqlite.Connection,
    collector: EvidenceCollector,
    registry: CardRegistry,
    health: Health,
    poll_interval_secs: float = 5.0,
    max_concurrent: int = 4,
    stop: asyncio.Event | None = None,
) -> None:
    sem = asyncio.Semaphore(max_concurrent)
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            batch = await queue.claim_pending(conn, limit=max_concurrent)
        except aiosqlite.Error as e:
            logger.warning("queue_read_failed", error=str(e))
            await asyncio.sleep(poll_interval_secs)
            continue

        if not batch:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_secs)
            except TimeoutError:
                pass
            continue

        async def _process(stored: queue.StoredClaim) -> None:
            async with sem:
                await _verify_one(conn, collector, registry, health, stored)

        await asyncio.gather(*[_process(s) for s in batch])
        await health.heartbeat("last_evidence_pass_at")


async def _verify_one(
    conn: aiosqlite.Connection,
    collector: EvidenceCollector,
    registry: CardRegistry,
    health: Health,
    stored: queue.StoredClaim,
) -> None:
    log = logger.bind(claim_id=stored.id, miner_hotkey=stored.miner_hotkey)
    try:
        bundle = await collector.collect(stored.payload)
    except CollectionError as e:
        await queue.mark_rejected(conn, stored.id, f"collection: {e}")
        log.info("claim_rejected", reason=str(e))
        return

    card = _decode_card(stored.payload, bundle, stored.miner_hotkey)
    if card is None:
        await queue.mark_rejected(conn, stored.id, "no_card")
        log.info("claim_rejected", reason="no_card")
        return

    try:
        preflight(card)
    except PreflightError as e:
        await queue.mark_rejected(conn, stored.id, f"preflight: {e}")
        log.info("claim_rejected", reason=f"preflight: {e}")
        return

    entry = registry.lookup(card.id)
    parts = score_card(card, entry)
    await queue.mark_verified(conn, stored.id, stored.miner_hotkey, bundle, parts, card)
    log.info("claim_verified", weighted=parts.weighted())


def _decode_card(claim, bundle, miner_hotkey: str) -> Card | None:  # type: ignore[no-untyped-def]
    """Resolve the Card from a verified claim.

    Prefers the inline `card_payload` on the claim — cards live on
    Cathedral and miners submit them inline. Falls back to decoding
    from `bundle.artifacts[*].report_hash` for backward compatibility
    with earlier-spec miners that pushed the card through Polaris.

    Returns None if neither path produces a valid Card.
    """
    if claim.card_payload is not None:
        return _coerce_card(claim.card_payload, claim.work_unit, miner_hotkey, bundle)
    return _decode_card_from_bundle(bundle, claim.work_unit, miner_hotkey)


def _coerce_card(  # type: ignore[no-untyped-def]
    raw: dict, work_unit: str, miner_hotkey: str, bundle
) -> Card | None:
    """Fill in the three fields that come from claim context, then validate."""
    if not isinstance(raw, dict):
        return None
    card_id = work_unit.removeprefix("card:") if work_unit.startswith("card:") else work_unit
    raw = dict(raw)
    raw.setdefault("id", card_id)
    raw.setdefault("worker_owner_hotkey", miner_hotkey)
    raw.setdefault("polaris_agent_id", bundle.manifest.polaris_agent_id)
    try:
        return Card.model_validate(raw)
    except (ValueError, TypeError):
        return None


def _decode_card_from_bundle(bundle, work_unit: str, miner_hotkey: str) -> Card | None:  # type: ignore[no-untyped-def]
    """Legacy path: decode a Card from the first artifact whose
    `report_hash` parses. Kept for backward compatibility while in-flight
    miners still submit cards via the Polaris artifact endpoint."""
    for art in bundle.artifacts:
        if not art.report_hash:
            continue
        try:
            raw = json.loads(art.report_hash) if art.report_hash.startswith("{") else None
        except json.JSONDecodeError:
            raw = None
        if raw is None:
            continue
        card = _coerce_card(raw, work_unit, miner_hotkey, bundle)
        if card is not None:
            return card
    return None
