"""Public GET endpoints (CONTRACTS.md Section 2.2 - 2.11).

All responses match the wire shapes in Section 1 / Section 2 exactly.
Errors always render as `{"detail": "<string>"}` (Section 9 lock #3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status

from cathedral.publisher import repository

if TYPE_CHECKING:
    from cathedral.publisher.app import PublisherContext

logger = structlog.get_logger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# 2.2 GET /v1/agents/{id}
# --------------------------------------------------------------------------


@router.get("/v1/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    sub = await repository.get_agent_submission(ctx.db, agent_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="agent not found")

    runs = await repository.list_eval_runs_for_submission(ctx.db, agent_id, limit=20)
    score_history = [{"date": r["ran_at"], "score": r["weighted_score"]} for r in reversed(runs)]
    return {
        "id": sub["id"],
        "display_name": sub["display_name"],
        "bio": sub["bio"],
        "logo_url": sub["logo_url"],
        "miner_hotkey": sub["miner_hotkey"],
        "card_id": sub["card_id"],
        "bundle_hash": sub["bundle_hash"],
        "bundle_size_bytes": sub["bundle_size_bytes"],
        "status": sub["status"],
        "current_score": sub["current_score"],
        "current_rank": sub["current_rank"],
        "submitted_at": sub["submitted_at"],
        "recent_evals": [_eval_run_to_output(r, sub) for r in runs],
        "score_history": score_history,
    }


# --------------------------------------------------------------------------
# 2.3 GET /v1/agents
# --------------------------------------------------------------------------


@router.get("/v1/agents")
async def list_agents(
    request: Request,
    card: str | None = Query(default=None),
    sort: str = Query(default="score"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    if sort not in ("score", "recent", "oldest"):
        raise HTTPException(status_code=400, detail=f"invalid sort: {sort}")
    if card:
        items = await repository.list_submissions_for_card(
            ctx.db, card, sort=sort, limit=limit, offset=offset
        )
        # No COUNT(*) for filtered query — return len; UI uses next-page
        # heuristic on the `items` length anyway.
        total = len(items)
    else:
        items, total = await repository.list_submissions_all(
            ctx.db, sort=sort, limit=limit, offset=offset
        )
    return {
        "items": [
            _submission_to_leaderboard_entry(s) for s in items if s["current_score"] is not None
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------------------
# 2.4 GET /v1/cards/{card_id}
# --------------------------------------------------------------------------


@router.get("/v1/cards/{card_id}")
async def get_card_summary(card_id: str, request: Request) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    card_def = await repository.get_card_definition(ctx.db, card_id)
    if card_def is None:
        raise HTTPException(status_code=404, detail="card not found")

    best = await repository.best_eval_run_for_card(ctx.db, card_id)
    submissions = await repository.list_submissions_for_card(
        ctx.db, card_id, sort="score", limit=200, offset=0
    )
    agent_count = len([s for s in submissions if s["status"] == "ranked"])
    latest = max(
        (r["ran_at"] for r in await repository.list_eval_runs_for_card(ctx.db, card_id, limit=1)),
        default=None,
    )

    best_eval_output = None
    if best is not None:
        owner_sub = await repository.get_agent_submission(ctx.db, best["submission_id"])
        if owner_sub:
            best_eval_output = _eval_run_to_output(best, owner_sub)

    return {
        "card_id": card_def["id"],
        "best_eval": best_eval_output,
        "definition": {
            "id": card_def["id"],
            "display_name": card_def["display_name"],
            "jurisdiction": card_def["jurisdiction"],
            "topic": card_def["topic"],
            "description": card_def["description"],
            "status": card_def["status"],
        },
        "agent_count": agent_count,
        "latest_eval_at": latest,
    }


# --------------------------------------------------------------------------
# 2.5 GET /v1/cards/{card_id}/history
# --------------------------------------------------------------------------


@router.get("/v1/cards/{card_id}/history")
async def get_card_history(
    card_id: str,
    request: Request,
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    since: str | None = Query(default=None),
) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    if (await repository.get_card_definition(ctx.db, card_id)) is None:
        raise HTTPException(status_code=404, detail="card not found")
    since_dt = _parse_since(since)

    if agent_id:
        runs = await repository.list_eval_runs_for_submission(ctx.db, agent_id, limit=limit)
        if since_dt:
            since_iso = since_dt.isoformat()
            runs = [r for r in runs if r.get("ran_at") and r["ran_at"] >= since_iso]
    else:
        runs = await repository.list_eval_runs_for_card(
            ctx.db, card_id, since=since_dt, limit=limit
        )

    items: list[dict[str, Any]] = []
    for r in runs:
        sub = await repository.get_agent_submission(ctx.db, r["submission_id"])
        if sub:
            items.append(_eval_run_to_output(r, sub))
    next_since = items[-1]["ran_at"] if len(items) == limit else None
    return {"items": items, "next_since": next_since}


# --------------------------------------------------------------------------
# 2.6 GET /v1/cards/{card_id}/eval-spec
# --------------------------------------------------------------------------


@router.get("/v1/cards/{card_id}/eval-spec")
async def get_card_eval_spec(card_id: str, request: Request) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    card_def = await repository.get_card_definition(ctx.db, card_id)
    if card_def is None:
        raise HTTPException(status_code=404, detail="card not found")
    return {
        "card_id": card_def["id"],
        "display_name": card_def["display_name"],
        "jurisdiction": card_def["jurisdiction"],
        "description_md": card_def["description"],
        "eval_spec_md": card_def["eval_spec_md"],
        "scoring_rubric": card_def["scoring_rubric"],
        "task_templates": card_def["task_templates"],
        "source_pool": card_def["source_pool"],
        "refresh_cadence_hours": card_def["refresh_cadence_hours"],
    }


# --------------------------------------------------------------------------
# 2.7 GET /v1/cards/{card_id}/feed
# --------------------------------------------------------------------------


@router.get("/v1/cards/{card_id}/feed")
async def get_card_feed(
    card_id: str,
    request: Request,
    since: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    if (await repository.get_card_definition(ctx.db, card_id)) is None:
        raise HTTPException(status_code=404, detail="card not found")
    since_dt = _parse_since(since)
    runs = await repository.list_eval_runs_for_card(ctx.db, card_id, since=since_dt, limit=limit)
    items: list[dict[str, Any]] = []
    for r in runs:
        sub = await repository.get_agent_submission(ctx.db, r["submission_id"])
        if sub:
            items.append(_eval_run_to_output(r, sub))
    next_since = items[-1]["ran_at"] if len(items) == limit else None
    return {"items": items, "next_since": next_since}


# --------------------------------------------------------------------------
# 2.8 GET /v1/leaderboard
# --------------------------------------------------------------------------


@router.get("/v1/leaderboard")
async def get_leaderboard(
    request: Request,
    card: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    if not card:
        raise HTTPException(status_code=400, detail="card parameter required")
    if (await repository.get_card_definition(ctx.db, card)) is None:
        raise HTTPException(status_code=404, detail="card not found")
    submissions = await repository.list_submissions_for_card(
        ctx.db, card, sort="score", limit=limit, offset=0
    )
    items = [
        _submission_to_leaderboard_entry(s) for s in submissions if s["current_score"] is not None
    ]
    return {"items": items, "computed_at": _now_iso()}


# --------------------------------------------------------------------------
# 2.9 GET /v1/leaderboard/recent
# --------------------------------------------------------------------------


@router.get("/v1/leaderboard/recent")
async def get_leaderboard_recent(
    request: Request,
    since: str = Query(...),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    since_dt = _parse_since(since)
    if since_dt is None:
        raise HTTPException(status_code=400, detail="since parameter must be ISO-8601")
    runs = await repository.list_eval_runs_recent(ctx.db, since=since_dt, limit=limit)
    items: list[dict[str, Any]] = []
    for r in runs:
        sub = await repository.get_agent_submission(ctx.db, r["submission_id"])
        if sub:
            items.append(_eval_run_to_output(r, sub))
    next_since = items[-1]["ran_at"] if len(items) == limit else None
    latest_epoch = await repository.latest_merkle_epoch(ctx.db)
    return {
        "items": items,
        "next_since": next_since,
        "merkle_epoch_latest": latest_epoch,
    }


# --------------------------------------------------------------------------
# 2.10 GET /v1/merkle/{epoch}
# --------------------------------------------------------------------------


@router.get("/v1/merkle/{epoch}")
async def get_merkle_anchor(epoch: int, request: Request) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    anchor = await repository.get_merkle_anchor(ctx.db, epoch)
    if anchor is None:
        raise HTTPException(status_code=404, detail="epoch not anchored")
    return anchor


# --------------------------------------------------------------------------
# 2.11 GET /v1/miners/{hotkey}/agents
# --------------------------------------------------------------------------


@router.get("/v1/miners/{hotkey}/agents")
async def get_miner_agents(hotkey: str, request: Request) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    subs = await repository.list_submissions_by_hotkey(ctx.db, hotkey)
    items: list[dict[str, Any]] = []
    for s in subs:
        runs = await repository.list_eval_runs_for_submission(ctx.db, s["id"], limit=20)
        items.append(
            {
                "id": s["id"],
                "display_name": s["display_name"],
                "bio": s["bio"],
                "logo_url": s["logo_url"],
                "miner_hotkey": s["miner_hotkey"],
                "card_id": s["card_id"],
                "bundle_hash": s["bundle_hash"],
                "bundle_size_bytes": s["bundle_size_bytes"],
                "status": s["status"],
                "current_score": s["current_score"],
                "current_rank": s["current_rank"],
                "submitted_at": s["submitted_at"],
                "recent_evals": [_eval_run_to_output(r, s) for r in runs],
                "score_history": [
                    {"date": r["ran_at"], "score": r["weighted_score"]} for r in reversed(runs)
                ],
            }
        )
    return {"items": items}


# --------------------------------------------------------------------------
# 2.12 GET /health
# --------------------------------------------------------------------------


@router.get("/health")
async def get_health(request: Request) -> dict[str, Any]:
    ctx: PublisherContext = request.app.state.ctx
    checks: dict[str, str] = {"db": "ok"}
    try:
        cur = await ctx.db.execute("SELECT 1")
        await cur.fetchone()
    except Exception:
        checks["db"] = "fail"

    # Storage check: weak by design. We probe whether the client is
    # constructed (== env vars are set), not whether HeadBucket succeeds.
    # The HeadBucket variant gated deploys on bucket-list scope, which
    # tokens scoped to a single bucket (the common case for shared S3
    # tenants like Hippius's per-account buckets) lack — even though
    # PutObject works fine. Submission failures surface at write time
    # with a clear 5xx, not at deploy time as a silent crash.
    if ctx.hippius is not None:
        try:
            healthy = await ctx.hippius.healthcheck()
            checks["hippius"] = "ok" if healthy else "degraded"
        except Exception:
            # Healthcheck shouldn't itself fail the request; just mark degraded.
            checks["hippius"] = "degraded"
    else:
        checks["hippius"] = "skipped"

    checks["polaris"] = "ok"  # publisher does not poll Polaris directly

    # Only DB failure is fatal for /health. Storage degradation is
    # surfaced in the response body but does NOT 503 — the publisher can
    # still serve reads (cards, leaderboard, skill.md) without storage.
    fatal = checks["db"] != "ok"
    payload = {
        "status": "ok" if all(v in ("ok", "skipped") for v in checks.values()) else "degraded",
        "checks": checks,
    }
    if fatal:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=payload,  # type: ignore[arg-type]
        )
    return payload


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _eval_run_to_output(run: dict[str, Any], sub: dict[str, Any]) -> dict[str, Any]:
    """Build the public EvalOutput projection (CONTRACTS.md §1.10 + L8).

    Field order is the publication order — keys matter to clients but not
    to canonical-JSON signing (which sorts internally).

    `output_card_hash` is included per locked decision L8: frontend renders
    it as the visible trust-chain anchor, and validators verify the
    cathedral signature against the same byte-exact projection (CRIT-7).
    `merkle_epoch` is post-anchor metadata; it is NOT covered by the
    cathedral signature (see `cathedral.v1_types.canonical_json`).
    """
    return {
        "id": run["id"],
        "agent_id": sub["id"],
        "agent_display_name": sub["display_name"],
        "card_id": sub["card_id"],
        "output_card": run["output_card_json"],
        "output_card_hash": run["output_card_hash"],
        "weighted_score": run["weighted_score"],
        "polaris_verified": bool(run.get("polaris_verified", 0)),
        "polaris_attestation": run.get("polaris_attestation"),
        "ran_at": run["ran_at"],
        "cathedral_signature": run["cathedral_signature"],
        "merkle_epoch": run.get("merkle_epoch"),
    }


def _submission_to_leaderboard_entry(sub: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_id": sub["id"],
        "display_name": sub["display_name"],
        "logo_url": sub["logo_url"],
        "miner_hotkey": sub["miner_hotkey"],
        "card_id": sub["card_id"],
        "current_score": sub["current_score"] or 0.0,
        "current_rank": sub["current_rank"] or 0,
        "last_eval_at": sub.get("submitted_at", _now_iso()),
    }


def _parse_since(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
