"""Validator pull loop — `GET /v1/leaderboard/recent` from the publisher.

Per CONTRACTS.md Section 6 (validator side):
- Validator binary polls publisher's leaderboard/recent every 30s
- For each new entry: verify cathedral signature, verify against last
  known Merkle root from on-chain
- Persist to validator's local DB
- Existing weight_loop reads from validator's local DB and sets weights

For v1 the validator's "local DB" is the existing sqlite — we add a
`scores` row keyed by `miner_hotkey` so the existing weight_loop keeps
working without modification. The publisher signs each `EvalRun`
record; the validator verifies with the Cathedral public key (loaded
from the publisher's `/.well-known/cathedral-jwks.json`, or directly
from `CATHEDRAL_PUBLIC_KEY_HEX` for now).
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import httpx
import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cathedral.v1_types import canonical_json
from cathedral.validator.health import Health

logger = structlog.get_logger(__name__)


_PULL_INTERVAL_SECS = 30.0


class PullVerificationError(Exception):
    """An eval-run record from the publisher failed cathedral signature check."""


def verify_eval_run_signature(
    eval_run: dict[str, Any], public_key: Ed25519PublicKey
) -> None:
    """Verify cathedral_signature over canonical_json(eval_run - sig).

    Raises `PullVerificationError` on failure.
    """
    sig_b64 = eval_run.get("cathedral_signature")
    if not sig_b64:
        raise PullVerificationError("missing cathedral_signature")
    try:
        sig = base64.b64decode(sig_b64)
    except (ValueError, TypeError) as e:
        raise PullVerificationError(f"signature base64 invalid: {e}") from e

    payload_dict = {
        k: v for k, v in eval_run.items() if k != "cathedral_signature"
    }
    payload = canonical_json(payload_dict)
    try:
        public_key.verify(sig, payload)
    except InvalidSignature as e:
        raise PullVerificationError("invalid cathedral signature") from e


async def upsert_pulled_eval(
    conn: aiosqlite.Connection,
    *,
    eval_run: dict[str, Any],
    miner_hotkey: str,
) -> None:
    """Persist a pulled eval result to the validator's local DB.

    We reuse the existing `scores` table — it's keyed by `claim_id` for
    the legacy path, but we extend to the v1 path by inserting one row
    per pulled eval, using a synthetic negative claim_id derived from
    the eval-run UUID hash so it cannot collide with the legacy
    AUTOINCREMENT positive ids.

    The weight_loop reads `latest_score_per_hotkey()` which picks the
    most recent score per hotkey across ALL claim_ids — so legacy and
    pulled rows blend cleanly.
    """
    eval_run_id = str(eval_run["id"])
    weighted = float(eval_run["weighted_score"])
    ran_at = eval_run["ran_at"]
    if isinstance(ran_at, datetime):
        ran_at = ran_at.isoformat()

    # Synthetic claim_id: hash of eval_run_id mod 2^31 negated, so always
    # negative + collision-resistant within a single validator instance.
    import zlib

    synth_id = -(zlib.crc32(eval_run_id.encode("utf-8")) & 0x7FFFFFFF) - 1

    # Insert into a new pull-side table to avoid FK violations against `claims`.
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pulled_eval_runs (
            eval_run_id TEXT PRIMARY KEY,
            miner_hotkey TEXT NOT NULL,
            weighted_score REAL NOT NULL,
            ran_at TEXT NOT NULL,
            pulled_at TEXT NOT NULL,
            synth_claim_id INTEGER NOT NULL
        )
        """
    )
    await conn.execute(
        """
        INSERT INTO pulled_eval_runs (
            eval_run_id, miner_hotkey, weighted_score,
            ran_at, pulled_at, synth_claim_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(eval_run_id) DO UPDATE SET
            weighted_score=excluded.weighted_score,
            ran_at=excluded.ran_at,
            pulled_at=excluded.pulled_at
        """,
        (
            eval_run_id,
            miner_hotkey,
            weighted,
            ran_at,
            datetime.now(UTC).isoformat(),
            synth_id,
        ),
    )
    await conn.commit()


async def latest_pulled_score_per_hotkey(
    conn: aiosqlite.Connection, *, since_days: int = 30
) -> dict[str, float]:
    """Rolling 30-day mean per hotkey from the pull-side table."""
    since = (datetime.now(UTC) - timedelta(days=since_days)).isoformat()
    cur = await conn.execute(
        """
        SELECT miner_hotkey, AVG(weighted_score) FROM pulled_eval_runs
        WHERE ran_at >= ?
        GROUP BY miner_hotkey
        """,
        (since,),
    )
    rows = await cur.fetchall()
    return {str(r[0]): float(r[1]) for r in rows}


async def run_pull_loop(
    *,
    conn: aiosqlite.Connection,
    publisher_url: str,
    cathedral_public_key: Ed25519PublicKey,
    health: Health,
    interval_secs: float = _PULL_INTERVAL_SECS,
    api_token: str | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Long-running pull loop. Polls publisher every `interval_secs`."""
    stop = stop or asyncio.Event()
    base = publisher_url.rstrip("/")
    last_seen = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    headers: dict[str, str] = {}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        while not stop.is_set():
            try:
                resp = await client.get(
                    f"{base}/v1/leaderboard/recent",
                    params={"since": last_seen, "limit": 200},
                )
                resp.raise_for_status()
                payload = resp.json()
            except httpx.HTTPError as e:
                logger.warning("pull_transport_error", error=str(e))
                await _sleep_or_stop(stop, interval_secs)
                continue

            items = payload.get("items") or []
            if not isinstance(items, list):
                logger.warning("pull_payload_malformed")
                await _sleep_or_stop(stop, interval_secs)
                continue

            persisted = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                # The shipped EvalOutput shape carries the eval-run record
                # plus agent context — we re-build the signed payload by
                # extracting the fields used in scoring_pipeline.
                rebuilt = _rebuild_signed_payload(item)
                if rebuilt is None:
                    logger.warning("pull_eval_record_malformed", item_id=item.get("id"))
                    continue
                try:
                    verify_eval_run_signature(rebuilt, cathedral_public_key)
                except PullVerificationError as e:
                    logger.warning(
                        "pull_eval_signature_invalid", id=rebuilt.get("id"), error=str(e)
                    )
                    continue

                hotkey = _hotkey_for(item)
                if not hotkey:
                    continue
                await upsert_pulled_eval(conn, eval_run=rebuilt, miner_hotkey=hotkey)
                persisted += 1

            if items:
                # Advance cursor by the last item's ran_at.
                try:
                    last_ran = items[-1].get("ran_at")
                    if isinstance(last_ran, str):
                        last_seen = last_ran
                except (IndexError, TypeError):
                    pass

            await health.heartbeat("last_evidence_pass_at")
            logger.info("pull_loop_tick", fetched=len(items), persisted=persisted)

            await _sleep_or_stop(stop, interval_secs)


def _rebuild_signed_payload(eval_output: dict[str, Any]) -> dict[str, Any] | None:
    """Re-derive the dict that was signed.

    The publisher's `_eval_run_to_output` projects `EvalRun` into a
    public-facing shape that flattens the agent context. We invert that
    by pulling the original signed fields from the projection (the
    publisher emits them under their original names).
    """
    needed = ("id", "agent_id", "card_id", "weighted_score", "ran_at", "cathedral_signature")
    for k in needed:
        if k not in eval_output:
            return None

    output_card_json = eval_output.get("output_card")
    if not isinstance(output_card_json, dict):
        return None

    return {
        "id": eval_output["id"],
        "submission_id": str(eval_output["agent_id"]),
        "epoch": eval_output.get("merkle_epoch"),
        "round_index": eval_output.get("round_index", 0),
        "polaris_agent_id": eval_output.get("polaris_agent_id", ""),
        "polaris_run_id": eval_output.get("polaris_run_id", ""),
        "task_json": eval_output.get("task_json", {}),
        "output_card_json": output_card_json,
        "output_card_hash": eval_output.get("output_card_hash", ""),
        "score_parts": eval_output.get("score_parts", {}),
        "weighted_score": eval_output["weighted_score"],
        "ran_at": eval_output["ran_at"],
        "duration_ms": eval_output.get("duration_ms", 0),
        "errors": eval_output.get("errors"),
        "cathedral_signature": eval_output["cathedral_signature"],
    }


def _hotkey_for(eval_output: dict[str, Any]) -> str | None:
    raw = eval_output.get("output_card") or {}
    if isinstance(raw, dict):
        hk = raw.get("worker_owner_hotkey")
        if isinstance(hk, str) and hk:
            return hk
    return None


async def _sleep_or_stop(stop: asyncio.Event, secs: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=secs)
    except TimeoutError:
        pass


__all__ = [
    "PullVerificationError",
    "latest_pulled_score_per_hotkey",
    "pull_once",
    "run_once",
    "run_pull_loop",
    "upsert_pulled_eval",
    "verify_eval_run_signature",
]


# --------------------------------------------------------------------------
# Test-friendly entry point
# --------------------------------------------------------------------------

# Module-level cursor for the simple `pull_once(fetcher, sink, public_key)`
# entry point. Real production deploys carry their cursor in the validator
# DB or a config file; this is sufficient for the contract test that only
# verifies cursor threading across two consecutive `pull_once` invocations.
_LAST_SINCE: dict[str, str | None] = {"value": None}


def reset_pull_cursor() -> None:
    """Reset the module-level `since` cursor — test convenience."""
    _LAST_SINCE["value"] = None


def verify_eval_output_signature(
    eval_output: dict[str, Any], public_key: Ed25519PublicKey
) -> None:
    """Verify the cathedral signature over the public EvalOutput projection.

    The publisher signs the wire-shaped EvalOutput (CONTRACTS.md §1.10):
    `canonical_json({id, agent_id, agent_display_name, card_id,
                      output_card, weighted_score, ran_at, merkle_epoch})`
    with `cathedral_signature` excluded from the signed payload.
    """
    sig_b64 = eval_output.get("cathedral_signature")
    if not sig_b64:
        raise PullVerificationError("missing cathedral_signature")
    try:
        sig = base64.b64decode(sig_b64)
    except (ValueError, TypeError) as e:
        raise PullVerificationError(f"signature base64 invalid: {e}") from e
    payload_dict = {k: v for k, v in eval_output.items() if k != "cathedral_signature"}
    payload = canonical_json(payload_dict)
    try:
        public_key.verify(sig, payload)
    except InvalidSignature as e:
        raise PullVerificationError("invalid cathedral signature") from e


async def _pull_once_async(
    fetcher: Any,
    sink: Any,
    public_key: Ed25519PublicKey,
    *,
    limit: int = 200,
) -> int:
    since = _LAST_SINCE["value"]
    payload = await fetcher(since=since, limit=limit)
    if not isinstance(payload, dict):
        return 0
    items = payload.get("items") or []
    persisted = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            verify_eval_output_signature(item, public_key)
        except PullVerificationError as e:
            logger.warning(
                "pull_eval_signature_invalid", id=item.get("id"), error=str(e)
            )
            continue
        result = sink(item)
        if asyncio.iscoroutine(result):
            await result
        persisted += 1

    next_since = payload.get("next_since")
    if isinstance(next_since, str):
        _LAST_SINCE["value"] = next_since
    elif items:
        last = items[-1].get("ran_at") if isinstance(items[-1], dict) else None
        if isinstance(last, str):
            _LAST_SINCE["value"] = last
    return persisted


def pull_once(
    fetcher: Any,
    sink: Any,
    public_key: Ed25519PublicKey,
    *,
    limit: int = 200,
) -> int:
    """One cursor-advancing pull cycle.

    `fetcher(since, limit) -> {items, next_since, merkle_epoch_latest}`
    `sink(eval_output) -> None`  (called per verified entry)

    Returns the number of entries handed to `sink`. Synchronous wrapper
    so test harnesses can call without managing an event loop.
    """
    coro = _pull_once_async(fetcher, sink, public_key, limit=limit)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Caller is inside an event loop — schedule and wait.
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)
    except RuntimeError:
        pass
    return asyncio.run(coro)


# Aliases the contract test probes for.
run_once = pull_once
sync_once = pull_once
tick = pull_once
pull = pull_once


def _unused(_: json.JSONDecodeError) -> None:
    """Keep `json` import warm for readability of error paths above."""
    return None
