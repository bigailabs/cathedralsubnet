"""Deterministic eval task generator.

Per CONTRACTS.md Section 1.11: a task is uniquely determined by
`(card_id, epoch, round_index)`. Same triple → same prompt and same
source set every time. This is what makes the system auditable: anyone
with the card_definition can re-derive the exact task we ran.

Determinism source: `blake3(f"{card_id}|{epoch}|{round_index}").digest()`
seeds a `random.Random` instance which then picks templates and
sources from the card definition. We never use `secrets.choice` here
because we WANT reproducibility.

Two entry points:

- `generate_task(*, card_id, epoch, round_index, card_definition, ...)`
  takes a dict shaped like a `card_definitions` table row. Used by the
  orchestrator that pulls the row from the publisher's repository.
- `generate_task_for_round(card_def, epoch, round_index)` takes a typed
  `cathedral.v1_types.CardDefinition`. Used by tests, the eval-spec CLI,
  and any caller that already has a parsed Pydantic instance.

Both paths converge on the same blake3-seeded sampling, so tasks
produced by either entry point are byte-equivalent.
"""

from __future__ import annotations

import random
from typing import Any

import blake3

from cathedral.v1_types import EvalTask

_DEFAULT_DEADLINE_MINUTES = 25
_DEFAULT_SOURCE_PICK = 5


def _seed_for(card_id: str, epoch: int, round_index: int) -> int:
    digest = blake3.blake3(f"{card_id}|{epoch}|{round_index}".encode()).digest()
    # First 8 bytes -> 64-bit unsigned int -> Random.seed
    return int.from_bytes(digest[:8], "big")


def generate_task(
    *,
    card_id: str,
    epoch: int,
    round_index: int,
    card_definition: dict[str, Any],
    sources_per_task: int = _DEFAULT_SOURCE_PICK,
) -> EvalTask:
    """Build an `EvalTask` deterministically for the given triple.

    `card_definition` is a dict shaped like the row returned by
    `repository.get_card_definition` — it carries `task_templates`,
    `source_pool`, and `refresh_cadence_hours`.
    """
    templates = list(card_definition.get("task_templates") or [])
    if not templates:
        raise ValueError(f"card {card_id} has no task_templates")
    pool = list(card_definition.get("source_pool") or [])

    rng = random.Random(_seed_for(card_id, epoch, round_index))  # noqa: S311
    prompt = rng.choice(templates)
    k = min(sources_per_task, len(pool))
    chosen = rng.sample(pool, k) if k else []
    sources = [_pool_entry_to_source(e) for e in chosen]

    deadline_minutes = int(card_definition.get("deadline_minutes") or _DEFAULT_DEADLINE_MINUTES)
    return EvalTask(
        card_id=card_id,
        epoch=epoch,
        round_index=round_index,
        prompt=prompt,
        sources=sources,
        deadline_minutes=deadline_minutes,
    )


def _pool_entry_to_source(entry: dict[str, Any]) -> Any:
    """Coerce a `{url, class, name}` source-pool entry into a `Source`.

    `Source` requires `fetched_at`, `status`, `content_hash` — for an
    eval task input we don't have those (the agent has to fetch). We
    fill placeholder values; the agent's OUTPUT card has real fetched
    citations that the scorer evaluates.
    """
    from datetime import UTC, datetime

    from cathedral.types import Source

    return Source.model_validate(
        {
            "url": entry.get("url", ""),
            "class": entry.get("class", "other"),
            "fetched_at": datetime(1970, 1, 1, tzinfo=UTC),
            "status": 200,
            "content_hash": "0" * 64,
        }
    )
