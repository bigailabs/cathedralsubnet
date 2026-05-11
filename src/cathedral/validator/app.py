"""FastAPI app + lifespan that wires the worker, weight loop, and watchdog."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
import structlog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, FastAPI, HTTPException, status

from cathedral.cards.registry import CardRegistry
from cathedral.chain import BittensorChain, Chain
from cathedral.evidence import EvidenceCollector, HttpPolarisFetcher
from cathedral.types import PolarisAgentClaim
from cathedral.validator import cards as cards_store
from cathedral.validator import queue, weight_loop, worker
from cathedral.validator.auth import make_bearer_dep
from cathedral.validator.config_runtime import RuntimeContext
from cathedral.validator.db import connect
from cathedral.validator.health import Health, HealthSnapshot
from cathedral.validator.stall import run_stall_watchdog

logger = structlog.get_logger(__name__)


def build_app(ctx: RuntimeContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = await connect(ctx.settings.storage.database_path)
        app.state.db = conn
        app.state.health = ctx.health
        app.state.bearer = ctx.bearer

        stop = asyncio.Event()
        tasks = [
            asyncio.create_task(
                worker.run_worker(
                    conn,
                    ctx.collector,
                    ctx.registry,
                    ctx.health,
                    poll_interval_secs=ctx.settings.worker.poll_interval_secs,
                    max_concurrent=ctx.settings.worker.max_concurrent_verifications,
                    stop=stop,
                )
            ),
            asyncio.create_task(
                weight_loop.run_weight_loop(
                    conn,
                    ctx.chain,
                    ctx.health,
                    interval_secs=ctx.settings.weights.interval_secs,
                    disabled=ctx.settings.weights.disabled,
                    burn_uid=ctx.settings.weights.burn_uid,
                    forced_burn_percentage=ctx.settings.weights.forced_burn_percentage,
                    stop=stop,
                )
            ),
            asyncio.create_task(
                run_stall_watchdog(
                    conn,
                    ctx.health,
                    after_secs=ctx.settings.stall.after_secs,
                    stop=stop,
                )
            ),
        ]
        try:
            yield
        finally:
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await conn.close()
            if ctx.fetcher_close is not None:
                await ctx.fetcher_close()

    app = FastAPI(title="Cathedral Validator", lifespan=lifespan)
    bearer_dep = make_bearer_dep(ctx.bearer)

    @app.get("/health", response_model=HealthSnapshot)
    async def get_health() -> HealthSnapshot:
        return await ctx.health.get()

    @app.post("/v1/claim", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(bearer_dep)])
    async def post_claim(claim: PolarisAgentClaim) -> dict[str, int | str]:
        try:
            claim_id = await queue.insert_claim(app.state.db, claim)
        except aiosqlite.IntegrityError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"id": claim_id, "status": "pending"}

    @app.get("/v1/cards/{card_id}")
    async def get_card(card_id: str) -> dict:
        """Return the highest-scoring verified version of `card_id`.

        Public read — cards are public information. Used by
        cathedral.computer to display the canonical view of each card.
        404 if no miner has produced a verified version yet.
        """
        row = await cards_store.best_card(app.state.db, card_id)
        if row is None:
            raise HTTPException(status_code=404, detail="card not found")
        return row

    @app.get("/v1/cards/{card_id}/history")
    async def get_card_history(card_id: str) -> list[dict]:
        """Return all verified versions of `card_id` across miners,
        newest verification first. Used by cathedral.computer to show
        which miners are maintaining a card and how their entries
        compare."""
        return await cards_store.card_history(app.state.db, card_id)

    return app


def from_settings(settings_path: str) -> FastAPI:
    """Production builder — used by `cathedral-validator serve`."""
    from cathedral.config import ValidatorSettings  # local import for CLI speed

    settings = ValidatorSettings.from_toml(settings_path)
    bearer = os.environ.get(settings.http.bearer_token_env)
    if not bearer:
        raise RuntimeError(f"missing bearer token: env {settings.http.bearer_token_env} not set")

    pubkey = Ed25519PublicKey.from_public_bytes(bytes.fromhex(settings.polaris.public_key_hex))
    fetcher = HttpPolarisFetcher(settings.polaris.base_url, settings.polaris.fetch_timeout_secs)
    collector = EvidenceCollector(fetcher, pubkey)
    chain: Chain = BittensorChain(
        network=settings.network.name,
        netuid=settings.network.netuid,
        wallet_name=settings.network.wallet_name,
        wallet_hotkey=settings.network.validator_hotkey,
        wallet_path=settings.network.wallet_path,
    )
    ctx = RuntimeContext(
        settings=settings,
        bearer=bearer,
        chain=chain,
        collector=collector,
        registry=CardRegistry.baseline(),
        health=Health(),
        fetcher_close=fetcher.aclose,
    )
    return build_app(ctx)
