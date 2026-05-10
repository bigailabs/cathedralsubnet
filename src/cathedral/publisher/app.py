"""FastAPI app for the publisher process (api.cathedral.computer).

Owns:
- POST /v1/agents/submit + reads (cathedral.publisher.submit + reads)
- background eval orchestrator (cathedral.eval.orchestrator)
- on-demand merkle close via CLI command (cathedral.publisher.merkle)

Does NOT own:
- Bittensor weight setting (that's the validator binary)
- the existing /v1/claim Polaris-evidence flow (that's the validator
  binary's `cathedral.validator.app` — left untouched for backward
  compat with current miners until they migrate)
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from cathedral.cards.registry import CardRegistry
from cathedral.eval.orchestrator import run_eval_loop
from cathedral.eval.polaris_runner import (
    HttpPolarisRunner,
    HttpPolarisRunnerConfig,
    PolarisRunner,
    StubPolarisRunner,
)
from cathedral.eval.scoring_pipeline import EvalSigner
from cathedral.publisher import repository
from cathedral.publisher.reads import router as reads_router
from cathedral.publisher.submit import router as submit_router
from cathedral.storage import HippiusClient, HippiusConfig, StubHippiusClient
from cathedral.validator.db import connect

logger = structlog.get_logger(__name__)


@dataclass
class PublisherContext:
    """Wired into `app.state.ctx`. Holds connections + background dependencies."""

    db: aiosqlite.Connection
    hippius: HippiusClient
    polaris: PolarisRunner
    signer: EvalSigner
    registry: CardRegistry
    submissions_paused: bool = False
    background_tasks: list[asyncio.Task[Any]] = field(default_factory=list)


def build_publisher_app(
    ctx_factory: Any, *, start_eval_loop: bool = True
) -> FastAPI:
    """Build the FastAPI app. `ctx_factory` is an async callable returning
    a `PublisherContext` — kept indirect so tests can inject mocks.

    `start_eval_loop=False` (used by `build_app` in tests) skips the
    background scheduler so tests can drive ticks deterministically via
    `cathedral.eval.orchestrator.run_once()`.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx: PublisherContext = await ctx_factory()
        app.state.ctx = ctx

        stop = asyncio.Event()
        if start_eval_loop:
            eval_task = asyncio.create_task(
                run_eval_loop(
                    db=ctx.db,
                    hippius=ctx.hippius,
                    polaris=ctx.polaris,
                    signer=ctx.signer,
                    registry=ctx.registry,
                    poll_interval_secs=10.0,
                    max_concurrent=2,
                    stop=stop,
                )
            )
            ctx.background_tasks.append(eval_task)
        try:
            yield
        finally:
            stop.set()
            for t in ctx.background_tasks:
                t.cancel()
            await asyncio.gather(*ctx.background_tasks, return_exceptions=True)
            await ctx.db.close()

    app = FastAPI(title="Cathedral Publisher", lifespan=lifespan)

    # Always render `{"detail": "<string>"}` per CONTRACTS.md Section 9 lock #3.
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            # Some endpoints (e.g. /health 503) intentionally pass a dict
            # body — render it directly.
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(detail) if detail else exc.__class__.__name__},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Surface a single readable line; the full pydantic detail is
        # logged separately for the operator dashboard.
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid request"}
        return JSONResponse(
            status_code=400,
            content={"detail": str(first.get("msg", "invalid request"))},
        )

    @app.exception_handler(HTTPException)
    async def _api_http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
        # Same as Starlette handler but covers the FastAPI subclass.
        detail = exc.detail
        if isinstance(detail, dict):
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(detail) if detail else "error"},
        )

    app.include_router(submit_router)
    app.include_router(reads_router)
    return app


# --------------------------------------------------------------------------
# Production wiring
# --------------------------------------------------------------------------


def from_settings(database_path: str = "data/publisher.db") -> FastAPI:
    """Build the publisher app for production use.

    Environment variables consumed:
      CATHEDRAL_KEK_HEX or CATHEDRAL_MASTER_ENCRYPTION_KEY (32-byte hex)
      CATHEDRAL_EVAL_SIGNING_KEY (32-byte hex Ed25519 private key)
      CATHEDRAL_EVAL_MODE (optional: "stub" enables StubPolarisRunner)
      HIPPIUS_S3_ACCESS_KEY / HIPPIUS_S3_SECRET_KEY / HIPPIUS_S3_ENDPOINT
        / HIPPIUS_S3_REGION / HIPPIUS_S3_BUCKET
      POLARIS_BASE_URL + POLARIS_API_TOKEN (when not in stub mode)
    """

    async def _factory() -> PublisherContext:
        conn = await connect(database_path)
        hippius = HippiusClient(HippiusConfig.from_env())

        signing_hex = os.environ.get("CATHEDRAL_EVAL_SIGNING_KEY")
        if not signing_hex:
            raise RuntimeError(
                "CATHEDRAL_EVAL_SIGNING_KEY env var required (32-byte hex)"
            )
        signer = EvalSigner.from_env_hex(signing_hex)

        polaris: PolarisRunner
        if os.environ.get("CATHEDRAL_EVAL_MODE", "").lower() == "stub":
            polaris = StubPolarisRunner()
        else:
            polaris = HttpPolarisRunner(
                HttpPolarisRunnerConfig(
                    base_url=os.environ.get(
                        "POLARIS_BASE_URL", "https://api.polaris.computer"
                    ),
                    api_token=os.environ.get("POLARIS_API_TOKEN", ""),
                )
            )

        return PublisherContext(
            db=conn,
            hippius=hippius,
            polaris=polaris,
            signer=signer,
            registry=CardRegistry.baseline(),
        )

    return build_publisher_app(_factory)


# --------------------------------------------------------------------------
# Test-friendly builder
# --------------------------------------------------------------------------


# v1 launch card_ids per CONTRACTS.md §9 lock #12. Seeded into a fresh DB
# so contract tests against `eu-ai-act` etc. find a card definition.
_V1_LAUNCH_CARDS: tuple[dict[str, Any], ...] = (
    {
        "id": "eu-ai-act",
        "display_name": "EU AI Act",
        "jurisdiction": "eu",
        "topic": "EU AI Act enforcement and guidance",
    },
    {
        "id": "us-ai-eo",
        "display_name": "US AI Executive Order",
        "jurisdiction": "us",
        "topic": "US executive orders and federal AI guidance",
    },
    {
        "id": "uk-ai-whitepaper",
        "display_name": "UK AI White Paper",
        "jurisdiction": "uk",
        "topic": "UK pro-innovation AI regulation framework",
    },
    {
        "id": "singapore-pdpc",
        "display_name": "Singapore PDPC",
        "jurisdiction": "sg",
        "topic": "Singapore PDPC enforcement and guidance",
    },
    {
        "id": "japan-meti-mic",
        "display_name": "Japan METI / MIC",
        "jurisdiction": "jp",
        "topic": "Japan METI/MIC AI and data guidance",
    },
)


_DEFAULT_RUBRIC: dict[str, Any] = {
    "source_quality_weight": 0.30,
    "maintenance_weight": 0.20,
    "freshness_weight": 0.15,
    "specificity_weight": 0.15,
    "usefulness_weight": 0.10,
    "clarity_weight": 0.10,
    "required_source_classes": ["official_journal", "regulator"],
    "min_summary_chars": 40,
    "max_summary_chars": 800,
    "min_citations": 1,
}


async def _seed_default_card_definitions(conn: aiosqlite.Connection) -> None:
    for card in _V1_LAUNCH_CARDS:
        await repository.insert_card_definition(
            conn,
            id=card["id"],
            display_name=card["display_name"],
            jurisdiction=card["jurisdiction"],
            topic=card["topic"],
            description=f"{card['display_name']} regulatory monitoring card.",
            eval_spec_md=(
                f"# {card['display_name']} eval spec\n\n"
                "Default v1 stub eval spec. Real card definitions are "
                "populated from the cathedral-eval-spec content repo.\n"
            ),
            source_pool=[
                {
                    "url": "https://example.invalid/source",
                    "class": "regulator",
                    "name": "stub source",
                }
            ],
            task_templates=[
                f"Summarize material {card['display_name']} developments "
                "in the last 24 hours."
            ],
            scoring_rubric=dict(_DEFAULT_RUBRIC),
            refresh_cadence_hours=24,
            status="active",
        )


# Module-level ctx pointer used by `run_once()` test entry point. Set by
# `build_app` when the FastAPI app's lifespan starts; cleared on shutdown.
_LATEST_CTX: PublisherContext | None = None


def build_app(database_path: str = "data/publisher.db") -> FastAPI:
    """Test-friendly publisher app builder.

    Differences from `from_settings`:
    - Seeds v1 launch card definitions on startup (so contract tests
      against `eu-ai-act` etc. find a card definition without external
      seeding).
    - Uses `StubHippiusClient` (in-memory) when Hippius env vars are
      missing — the eval pipeline still round-trips bundles correctly.
    - Auto-generates an Ed25519 signing key when
      `CATHEDRAL_EVAL_SIGNING_KEY` is unset (the publisher tests don't
      need a stable key; the validator pull-loop test brings its own).
    - Auto-generates a 32-byte master KEK when CATHEDRAL_KEK_HEX /
      CATHEDRAL_MASTER_ENCRYPTION_KEY are unset.
    - Wires `StubPolarisRunner` whenever CATHEDRAL_EVAL_MODE starts with
      "stub" (the default in tests).
    - Does NOT auto-start the eval loop background task — tests drive
      ticks via `cathedral.eval.orchestrator.run_once()`. Production
      deploys use `from_settings` which starts the loop.
    """

    async def _factory() -> PublisherContext:
        # Master KEK — encryption depends on this; generate ephemeral if missing.
        if not (
            os.environ.get("CATHEDRAL_KEK_HEX")
            or os.environ.get("CATHEDRAL_MASTER_ENCRYPTION_KEY")
        ):
            os.environ["CATHEDRAL_KEK_HEX"] = secrets.token_bytes(32).hex()

        conn = await connect(database_path)

        # Hippius — try real config from env; fall back to stub.
        hippius: Any
        try:
            hippius = HippiusClient(HippiusConfig.from_env())
        except Exception:
            hippius = StubHippiusClient()

        # Signing key — generate if missing.
        signing_hex = os.environ.get("CATHEDRAL_EVAL_SIGNING_KEY")
        if not signing_hex:
            signing_hex = secrets.token_bytes(32).hex()
            os.environ["CATHEDRAL_EVAL_SIGNING_KEY"] = signing_hex
        signer = EvalSigner.from_env_hex(signing_hex)

        # Polaris runner — stub mode unless explicitly configured.
        eval_mode = os.environ.get("CATHEDRAL_EVAL_MODE", "stub").lower()
        polaris: PolarisRunner
        if eval_mode.startswith("stub"):
            polaris = _build_stub_polaris(eval_mode)
        else:
            polaris = HttpPolarisRunner(
                HttpPolarisRunnerConfig(
                    base_url=os.environ.get(
                        "POLARIS_BASE_URL", "https://api.polaris.computer"
                    ),
                    api_token=os.environ.get("POLARIS_API_TOKEN", ""),
                )
            )

        await _seed_default_card_definitions(conn)

        ctx = PublisherContext(
            db=conn,
            hippius=hippius,
            polaris=polaris,
            signer=signer,
            registry=CardRegistry.baseline(),
        )
        global _LATEST_CTX
        _LATEST_CTX = ctx
        return ctx

    return build_publisher_app(_factory, start_eval_loop=False)


def _build_stub_polaris(mode: str) -> PolarisRunner:
    """Pick a Polaris stub flavor from CATHEDRAL_EVAL_MODE.

    - "stub"                        : default happy-path stub
    - "stub-fail-polaris"           : always raises PolarisRunnerError
    - "stub-bad-card"               : returns malformed Card JSON
    - "stub-deterministic-score"    : returns valid card; score driven by
                                       CATHEDRAL_STUB_SCORE env var
    """
    from cathedral.eval.polaris_runner import (
        FailingStubPolarisRunner,
        MalformedStubPolarisRunner,
    )

    if mode == "stub-fail-polaris":
        return FailingStubPolarisRunner()
    if mode == "stub-bad-card":
        return MalformedStubPolarisRunner()
    return StubPolarisRunner()


def latest_ctx() -> PublisherContext | None:
    """Return the most recently built `PublisherContext` (set by `build_app`).

    Used by the test-friendly `cathedral.eval.orchestrator.run_once()`
    helper to find the live db / hippius / polaris / signer wiring.
    """
    return _LATEST_CTX


# Aliases the test fixture probes for.
app = build_app  # callable, returns FastAPI
