"""Decode card -> preflight -> score -> first-mover delta -> sign -> store.

CONTRACTS.md Section 6 step 4-5. The scorer (`cathedral.cards.score_card`)
and preflight (`cathedral.cards.preflight`) are reused from the existing
codebase.

First-mover delta logic (Section 7.2):
    if submission is the first mover for (card_id, fingerprint):
        multiplier = 1.0
    elif weighted >= incumbent_best + 0.05:
        multiplier = 1.0       # late but materially better
    elif (now - first_mover_at).days > 30:
        multiplier = 1.0       # window closed
    else:
        multiplier = 0.50      # within window, not materially better
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import aiosqlite
import blake3
import structlog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cathedral.cards import preflight, score_card
from cathedral.cards.preflight import PreflightError
from cathedral.cards.registry import CardRegistry, RegistryEntry
from cathedral.eval.scoring import (
    FIRST_MOVER_DELTA,
    FIRST_MOVER_PENALTY_MULTIPLIER,
    FIRST_MOVER_WINDOW_DAYS,
    first_mover_multiplier,
)
from cathedral.publisher import repository
from cathedral.types import Card
from cathedral.v1_types import canonical_json

logger = structlog.get_logger(__name__)


_FIRST_MOVER_WINDOW_DAYS = FIRST_MOVER_WINDOW_DAYS
_FIRST_MOVER_DELTA_THRESHOLD = FIRST_MOVER_DELTA
_FIRST_MOVER_PENALTY_MULTIPLIER = FIRST_MOVER_PENALTY_MULTIPLIER


@dataclass(frozen=True)
class ScoredEval:
    """Result the orchestrator persists into `eval_runs`."""

    eval_run_id: str
    output_card_json: dict[str, Any]
    output_card_hash: str
    score_parts: dict[str, Any]
    weighted_score: float
    weighted_score_pre_multiplier: float
    multiplier: float
    cathedral_signature: str
    errors: list[str]


class EvalSigner:
    """Wraps `Ed25519PrivateKey` for signing eval-run records.

    Loaded from `CATHEDRAL_EVAL_SIGNING_KEY` (32-byte raw private key in
    hex), matching the Polaris convention (`POLARIS_CATHEDRAL_SIGNING_KEY`).
    """

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._sk = private_key

    @classmethod
    def from_env_hex(cls, hex_str: str) -> EvalSigner:
        try:
            raw = bytes.fromhex(hex_str.strip())
        except ValueError as e:
            raise ValueError("CATHEDRAL_EVAL_SIGNING_KEY must be hex") from e
        if len(raw) != 32:
            raise ValueError(
                f"signing key must be 32 bytes, got {len(raw)}"
            )
        return cls(Ed25519PrivateKey.from_private_bytes(raw))

    def sign(self, eval_run_dict: dict[str, Any]) -> str:
        payload = canonical_json(eval_run_dict)
        return base64.b64encode(self._sk.sign(payload)).decode("ascii")


def card_hash(card: Card | dict[str, Any]) -> str:
    """`blake3(canonical_json(card))` — used as the leaf input.

    CRIT-8: must hash the EXACT bytes that the publisher serves (and stores
    in `eval_runs.output_card_json`). For dict input we hash the canonical
    serialization of the dict directly. For Card input (legacy callers /
    tests) we render via Pydantic; that path is NOT used by the live scoring
    pipeline — the live pipeline always passes the literal `output_card_json`
    dict so the hash matches `blake3(canonical_json(served_output_card))`.
    """
    if isinstance(card, Card):
        d = card.model_dump(by_alias=True, mode="json")
    else:
        d = card
    payload = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return blake3.blake3(payload).hexdigest()


async def score_and_sign(
    conn: aiosqlite.Connection,
    *,
    submission: dict[str, Any],
    epoch: int,
    round_index: int,
    polaris_agent_id: str,
    polaris_run_id: str,
    task_json: dict[str, Any],
    output_card_json: dict[str, Any],
    duration_ms: int,
    polaris_errors: list[str],
    registry: CardRegistry,
    signer: EvalSigner,
) -> ScoredEval:
    """Run preflight + scorer, apply first-mover delta, build + sign EvalRun.

    Inserts the row into `eval_runs` and updates `agent_submissions`
    rolling score + rank. Returns the persisted record for caller use.
    """
    errors = list(polaris_errors)
    miner_hotkey = submission["miner_hotkey"]
    submission_id = submission["id"]
    card_id = submission["card_id"]

    # Card decode + preflight + score.
    # CONTRACTS §1.4: worker_owner_hotkey, polaris_agent_id, and id are
    # "filled by validator from claim" — they MUST come from server-trusted
    # state and OVERRIDE anything the bundle's agent emitted (CRIT-9). Use
    # unconditional assignment, never setdefault.
    raw_card = dict(output_card_json)
    raw_card["worker_owner_hotkey"] = miner_hotkey
    raw_card["polaris_agent_id"] = polaris_agent_id
    raw_card["id"] = card_id
    # Re-pin in the storage dict too so any downstream view of
    # output_card_json sees the trusted attribution.
    output_card_json = dict(output_card_json)
    output_card_json["worker_owner_hotkey"] = miner_hotkey
    output_card_json["polaris_agent_id"] = polaris_agent_id
    output_card_json["id"] = card_id

    weighted_pre = 0.0
    score_dict: dict[str, Any] = {
        "source_quality": 0.0,
        "freshness": 0.0,
        "specificity": 0.0,
        "usefulness": 0.0,
        "clarity": 0.0,
        "maintenance": 0.0,
    }
    try:
        card = Card.model_validate(raw_card)
    except (ValueError, TypeError) as e:
        errors.append(f"card validation: {e}")
        card = None  # type: ignore[assignment]

    if card is not None:
        try:
            preflight(card)
            entry: RegistryEntry | None = registry.lookup(card.id)
            parts = score_card(card, entry)
            score_dict = parts.model_dump()
            weighted_pre = parts.weighted()
        except PreflightError as e:
            errors.append(f"preflight: {e}")

    # First-mover delta lookup
    multiplier = await _first_mover_multiplier(
        conn,
        submission=submission,
        weighted_score_pre=weighted_pre,
    )
    weighted_after_first_mover = weighted_pre * multiplier

    # Verified-runtime multiplier per CONTRACTS.md §7.3 + Fred's Moltbook
    # decision: BYO-compute miners (no polaris_agent_id) score normally.
    # Miners who ran on Polaris and produced a manifest that verified get
    # a 1.10x quality bonus. Capped at 1.0 afterwards so a top-tier BYO
    # miner can still hit the ceiling.
    #
    # The orchestrator only passes a non-empty polaris_agent_id when the
    # manifest fetch + signature verification both succeeded. Empty or
    # None means BYO-compute or failed verification — no multiplier.
    polaris_verified = bool(polaris_agent_id)
    verified_multiplier = 1.10 if polaris_verified else 1.0
    weighted_final = min(1.0, weighted_after_first_mover * verified_multiplier)

    # CRIT-8: hash the literal `output_card_json` bytes that the publisher
    # both STORES (eval_runs.output_card_json) AND SERVES (EvalOutput.output_card).
    # Do NOT hash the Pydantic-rendered Card — Pydantic re-rendering applies
    # defaults/normalization and yields different bytes than what gets served,
    # making the hash externally unverifiable.
    output_card_hash = card_hash(output_card_json)
    eval_run_id = str(uuid4())
    ran_at = datetime.now(UTC)
    ran_at_iso = _ms_iso(ran_at)

    # CONTRACTS.md §1.10 + §4.2 + L8 + tests/v1: the cathedral_signature
    # covers the public EvalOutput projection (the wire shape the validator
    # pull loop verifies). Signing over the projection means downstream
    # verifiers don't have to invert the public response back into the
    # storage row to verify. `output_card_hash` is included per L8 so the
    # frontend / validators can pin the byte-exact card the cathedral
    # signed. `merkle_epoch` is appended post-anchor and is EXCLUDED from
    # the signed bytes via `canonical_json` (see v1_types).
    display_name = submission.get("display_name", "")
    public_payload = {
        "id": eval_run_id,
        "agent_id": str(submission_id),
        "agent_display_name": display_name,
        "card_id": card_id,
        "output_card": output_card_json,
        "output_card_hash": output_card_hash,
        "weighted_score": weighted_final,
        "polaris_verified": polaris_verified,
        "ran_at": ran_at_iso,
    }
    signature = signer.sign(public_payload)

    await repository.insert_eval_run(
        conn,
        id=eval_run_id,
        submission_id=str(submission_id),
        epoch=epoch,
        round_index=round_index,
        polaris_agent_id=polaris_agent_id,
        polaris_run_id=polaris_run_id,
        task_json=task_json,
        output_card_json=output_card_json,
        output_card_hash=output_card_hash,
        score_parts=score_dict,
        weighted_score=weighted_final,
        ran_at=ran_at,
        ran_at_iso=ran_at_iso,
        duration_ms=duration_ms,
        errors=errors if errors else None,
        cathedral_signature=signature,
        polaris_verified=polaris_verified,
    )

    # Update rolling 30-day average + rank
    avg = await repository.rolling_avg_score(conn, str(submission_id), days=30)
    if avg is None:
        avg = weighted_final
    rank = await _compute_rank(conn, card_id, avg)
    await repository.update_submission_score(
        conn, str(submission_id), current_score=avg, current_rank=rank
    )

    logger.info(
        "eval_run_persisted",
        eval_run_id=eval_run_id,
        submission_id=submission_id,
        weighted=weighted_final,
        multiplier=multiplier,
        rank=rank,
    )

    return ScoredEval(
        eval_run_id=eval_run_id,
        output_card_json=output_card_json,
        output_card_hash=output_card_hash,
        score_parts=score_dict,
        weighted_score=weighted_final,
        weighted_score_pre_multiplier=weighted_pre,
        multiplier=multiplier,
        cathedral_signature=signature,
        errors=errors,
    )


# --------------------------------------------------------------------------
# First-mover helpers
# --------------------------------------------------------------------------


async def _first_mover_multiplier(
    conn: aiosqlite.Connection,
    *,
    submission: dict[str, Any],
    weighted_score_pre: float,
) -> float:
    fingerprint = submission["metadata_fingerprint"]
    card_id = submission["card_id"]
    submission_id = submission["id"]

    first = await repository.first_mover_for_fingerprint(conn, card_id, fingerprint)
    is_first = first is None or str(first["id"]) == str(submission_id)

    submitted_at = _parse_iso(submission.get("submitted_at"))
    incumbent_best = await repository.incumbent_best_score(
        conn, card_id, submitted_at
    ) or 0.0
    first_mover_at = _parse_iso(
        first.get("first_mover_at") if first else submission.get("first_mover_at")
    )
    days_since = (datetime.now(UTC) - first_mover_at).days

    return first_mover_multiplier(
        is_first_mover=is_first,
        weighted_score=weighted_score_pre,
        incumbent_best_weighted=incumbent_best,
        days_since_first=days_since,
    )


async def _compute_rank(
    conn: aiosqlite.Connection, card_id: str, my_score: float
) -> int:
    """1-indexed rank within `card_id` by current_score DESC."""
    cur = await conn.execute(
        "SELECT COUNT(*) FROM agent_submissions "
        "WHERE card_id = ? AND status='ranked' AND current_score > ?",
        (card_id, my_score),
    )
    row = await cur.fetchone()
    higher = int(row[0]) if row else 0
    return higher + 1


def _parse_iso(s: str | datetime | None) -> datetime:
    if s is None:
        return datetime.now(UTC)
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(UTC)


def _ms_iso(dt: datetime) -> str:
    """ISO-8601 UTC, ms precision, trailing Z (CONTRACTS.md §9 lock #6)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    s = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    return s + "Z"


__all__ = [
    "EvalSigner",
    "ScoredEval",
    "card_hash",
    "score_and_sign",
]


def _unused(_: timedelta) -> None:
    """Keep `timedelta` import used for type clarity above; harmless."""
    return None
