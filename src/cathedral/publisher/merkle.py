"""Weekly Merkle root computation + on-chain anchor (CONTRACTS.md 4.5/4.6).

Default epoch = ISO calendar week. `epoch_for(dt)` computes the integer
epoch index (year * 100 + iso_week). Job runs Monday 00:05 UTC and
anchors the just-completed week.

Tree construction:
    leaf_i = blake3(":".join([id, output_card_hash, str(weighted_score),
                              cathedral_signature]))
    sort leaves by eval_run_id ascending
    if odd, duplicate the last leaf at each level (Bitcoin style)
    parent = blake3(left_hex + right_hex)  # ASCII hex concatenation
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import blake3
import structlog

from cathedral.chain.anchor import Anchorer, AnchorError
from cathedral.publisher import repository

logger = structlog.get_logger(__name__)


# --------------------------------------------------------------------------
# Epoch math
# --------------------------------------------------------------------------


def epoch_for(dt: datetime) -> int:
    """ISO calendar week as `year * 100 + week` (e.g., 202618 == 2026 W18).

    Lock-in for v1: this matches the "weekly Mon-Mon UTC" cadence in
    Section 6 step 7 because ISO weeks start Monday.
    """
    iso = dt.isocalendar()
    return int(iso.year * 100 + iso.week)


def epoch_window(epoch: int) -> tuple[datetime, datetime]:
    """Return `(start, end)` UTC timestamps for the ISO-calendar epoch."""
    year, week = divmod(epoch, 100)
    # ISO week 1 contains the first Thursday of the year; build via
    # `date.fromisocalendar(year, week, weekday=1)` (Mon).
    from datetime import date

    monday = date.fromisocalendar(year, week, 1)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
    end = start + timedelta(days=7)
    return start, end


# --------------------------------------------------------------------------
# Tree
# --------------------------------------------------------------------------


def merkle_leaf(run: dict[str, Any]) -> str:
    """`blake3(id : output_card_hash : str(weighted_score) : cathedral_signature)`."""
    parts = [
        str(run["id"]),
        str(run["output_card_hash"]),
        str(run["weighted_score"]),
        str(run["cathedral_signature"]),
    ]
    return blake3.blake3(":".join(parts).encode("utf-8")).hexdigest()


def merkle_root(leaves_hex_sorted: list[str]) -> str:
    """Bitcoin-style binary tree over hex-string concatenated parents."""
    if not leaves_hex_sorted:
        return blake3.blake3(b"").hexdigest()
    layer = list(leaves_hex_sorted)
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer = [*layer, layer[-1]]
        layer = [
            blake3.blake3((a + b).encode("utf-8")).hexdigest()
            for a, b in zip(layer[::2], layer[1::2], strict=False)
        ]
    return layer[0]


# --------------------------------------------------------------------------
# Job runner
# --------------------------------------------------------------------------


async def close_epoch(
    conn: aiosqlite.Connection,
    epoch: int,
    *,
    anchorer: Anchorer | None = None,
) -> dict[str, Any]:
    """Compute root for the given epoch, persist anchor row, optionally
    submit on-chain extrinsic.

    Returns the persisted anchor as a dict.
    """
    start, end = epoch_window(epoch)
    runs = await repository.list_eval_runs_in_window(conn, start=start, end=end)

    sorted_runs = sorted(runs, key=lambda r: str(r["id"]))
    leaves = [merkle_leaf(r) for r in sorted_runs]
    root = merkle_root(leaves)
    computed_at = datetime.now(UTC)

    on_chain_block: int | None = None
    on_chain_idx: int | None = None
    if anchorer is not None and leaves:
        try:
            result = await anchorer.anchor(epoch, root)
            on_chain_block = result.block
            on_chain_idx = result.extrinsic_index
        except AnchorError as e:
            # Persist anyway so we have the off-chain root; caller can
            # retry the on-chain submission separately.
            logger.warning("merkle_anchor_extrinsic_failed", epoch=epoch, error=str(e))

    await repository.insert_merkle_anchor(
        conn,
        epoch=epoch,
        merkle_root=root,
        eval_count=len(leaves),
        leaf_hashes=leaves,
        computed_at=computed_at,
        on_chain_block=on_chain_block,
        on_chain_extrinsic_index=on_chain_idx,
    )
    await repository.link_eval_runs_to_epoch(conn, [str(r["id"]) for r in sorted_runs], epoch)
    logger.info(
        "merkle_anchor_persisted",
        epoch=epoch,
        root=root,
        eval_count=len(leaves),
        on_chain_block=on_chain_block,
    )

    return {
        "epoch": epoch,
        "merkle_root": root,
        "eval_count": len(leaves),
        "leaf_hashes": leaves,
        "computed_at": computed_at.isoformat(),
        "on_chain_block": on_chain_block,
        "on_chain_extrinsic_index": on_chain_idx,
    }


def previous_epoch(now: datetime | None = None) -> int:
    """The epoch number for "last full week" — used by the cron job."""
    now = now or datetime.now(UTC)
    return epoch_for(now - timedelta(days=7))
