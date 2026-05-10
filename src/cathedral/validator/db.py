"""Sqlite schema, connection, and migrations.

The validator is a single writer; readers are tolerated. WAL mode keeps the
HTTP path lock-free against the verification worker.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    miner_hotkey TEXT NOT NULL,
    owner_wallet TEXT NOT NULL,
    work_unit TEXT NOT NULL,
    polaris_agent_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','verifying','verified','rejected')),
    rejection_reason TEXT,
    submitted_at TEXT NOT NULL,
    verified_at TEXT,
    UNIQUE(miner_hotkey, work_unit, polaris_agent_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);

CREATE TABLE IF NOT EXISTS evidence_bundles (
    claim_id INTEGER PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,
    bundle_json TEXT NOT NULL,
    filtered_usage_count INTEGER NOT NULL,
    verified_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
    claim_id INTEGER PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,
    miner_hotkey TEXT NOT NULL,
    source_quality REAL NOT NULL,
    freshness REAL NOT NULL,
    specificity REAL NOT NULL,
    usefulness REAL NOT NULL,
    clarity REAL NOT NULL,
    maintenance REAL NOT NULL,
    weighted REAL NOT NULL,
    scored_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_hotkey_time ON scores(miner_hotkey, scored_at DESC);

CREATE TABLE IF NOT EXISTS health_kv (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Cards live on Cathedral (not Polaris). One row per (card_id, miner_hotkey)
-- — the miner's latest verified version of that card. The validator writes
-- here on every verified claim; cathedral.computer reads from here to
-- display "what miner X currently says about card Y."
CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT NOT NULL,
    miner_hotkey TEXT NOT NULL,
    polaris_agent_id TEXT NOT NULL,
    owner_wallet TEXT NOT NULL,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    card_json TEXT NOT NULL,
    weighted_score REAL NOT NULL,
    last_refreshed_at TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (card_id, miner_hotkey)
);
CREATE INDEX IF NOT EXISTS idx_cards_card_id ON cards(card_id);
CREATE INDEX IF NOT EXISTS idx_cards_verified_at ON cards(verified_at DESC);
"""


async def connect(database_path: str) -> aiosqlite.Connection:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(database_path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    await conn.commit()
    return conn
