"""Read helpers for the `cards` table.

Writes are performed by `cathedral.validator.queue.mark_verified` as
part of the verified-claim transaction. This module is read-only.
"""

from __future__ import annotations

import json

import aiosqlite


def _row_to_dict(row: aiosqlite.Row | tuple) -> dict:
    """Convert a sqlite row to the JSON shape the HTTP layer returns.

    `card` is the verified Card payload; the surrounding fields are
    the verification context — useful for cathedral.computer to render
    "miner X scored Y on EU AI Act, last refreshed at Z."
    """
    return {
        "card_id": row[0],
        "miner_hotkey": row[1],
        "polaris_agent_id": row[2],
        "owner_wallet": row[3],
        "claim_id": row[4],
        "card": json.loads(row[5]),
        "weighted_score": row[6],
        "last_refreshed_at": row[7],
        "verified_at": row[8],
    }


async def best_card(conn: aiosqlite.Connection, card_id: str) -> dict | None:
    """Return the single highest-scoring verified version of `card_id`.

    Tie-breaker: most recent verification wins. We prefer "the best
    information available right now" over showing every miner's view —
    cathedral.computer surfaces the canonical card on its main page.
    """
    cur = await conn.execute(
        """
        SELECT card_id, miner_hotkey, polaris_agent_id, owner_wallet,
               claim_id, card_json, weighted_score, last_refreshed_at, verified_at
        FROM cards
        WHERE card_id = ?
        ORDER BY weighted_score DESC, verified_at DESC
        LIMIT 1
        """,
        (card_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


async def card_history(
    conn: aiosqlite.Connection, card_id: str
) -> list[dict]:
    """Return all miners' verified versions of `card_id`, newest first.

    Useful for displaying the leaderboard / divergence view: which
    miners are maintaining this card, what they each say, how they're
    scored. cathedral.computer can surface this on a card detail page.
    """
    cur = await conn.execute(
        """
        SELECT card_id, miner_hotkey, polaris_agent_id, owner_wallet,
               claim_id, card_json, weighted_score, last_refreshed_at, verified_at
        FROM cards
        WHERE card_id = ?
        ORDER BY verified_at DESC
        """,
        (card_id,),
    )
    rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]
