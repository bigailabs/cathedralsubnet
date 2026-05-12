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

-- ----------------------------------------------------------------------
-- V1 launch tables (CONTRACTS.md Section 3)
-- ----------------------------------------------------------------------

-- Curated card metadata. Populated by ops from the cathedral-eval-spec
-- content repo. Read by every public read endpoint and by the eval
-- task generator.
CREATE TABLE IF NOT EXISTS card_definitions (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    jurisdiction    TEXT NOT NULL,
    topic           TEXT NOT NULL,
    description     TEXT NOT NULL,
    eval_spec_md    TEXT NOT NULL,
    source_pool     TEXT NOT NULL,
    task_templates  TEXT NOT NULL,
    scoring_rubric  TEXT NOT NULL,
    refresh_cadence_hours INTEGER NOT NULL DEFAULT 24,
    status          TEXT NOT NULL CHECK (status IN ('active','archived')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Miner-uploaded agent submissions. Bundles live encrypted in Hippius.
--
-- `attestation_mode` branches at intake (see cathedral.publisher.submit):
--   * 'polaris'        — Cathedral re-runs eval on a Polaris-managed
--                        runtime (legacy cathedral-runtime image). No
--                        miner-side attestation needed at submission time.
--                        Kept as a backup during v2 migration.
--   * 'polaris-deploy' — v2. Polaris deploys the canonical Hermes
--                        runtime against the miner's bundle and Cathedral
--                        drives /chat. Manifest is fetched + verified;
--                        no per-task attestation needed because the
--                        deployment is the unit of trust.
--   * 'tee'            — Miner attached a TEE attestation document
--                        (Nitro/TDX/SEV-SNP). `attestation_blob` carries
--                        the raw bytes; `attestation_type` carries the
--                        verifier label; `attestation_verified_at`
--                        records when Cathedral verified it.
--   * 'unverified'     — Discovery-only. No eval is run, no score
--                        persisted. `status` is 'discovery';
--                        `discovery_only` is true.
CREATE TABLE IF NOT EXISTS agent_submissions (
    id                       TEXT PRIMARY KEY,
    miner_hotkey             TEXT NOT NULL,
    card_id                  TEXT NOT NULL REFERENCES card_definitions(id),
    bundle_blob_key          TEXT NOT NULL,
    bundle_hash              TEXT NOT NULL,
    bundle_size_bytes        INTEGER NOT NULL,
    encryption_key_id        TEXT NOT NULL,
    bundle_signature         TEXT NOT NULL,
    display_name             TEXT NOT NULL,
    bio                      TEXT,
    logo_url                 TEXT,
    soul_md_preview          TEXT,
    metadata_fingerprint     TEXT NOT NULL,
    similarity_check_passed  INTEGER NOT NULL,
    rejection_reason         TEXT,
    submitted_at             TEXT NOT NULL,
    status                   TEXT NOT NULL CHECK (status IN
                               ('pending_check','queued','evaluating',
                                'ranked','rejected','withdrawn',
                                'discovery')),
    current_score            REAL,
    current_rank             INTEGER,
    first_mover_at           TEXT,
    attestation_mode         TEXT NOT NULL DEFAULT 'polaris'
                             CHECK (attestation_mode IN
                               ('polaris','polaris-deploy','tee','unverified')),
    attestation_type         TEXT,
    attestation_blob         BLOB,
    attestation_verified_at  TEXT,
    discovery_only           INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_unique
    ON agent_submissions(miner_hotkey, card_id, bundle_hash);
CREATE INDEX IF NOT EXISTS idx_agent_card_status
    ON agent_submissions(card_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_card_score
    ON agent_submissions(card_id, current_score DESC);
CREATE INDEX IF NOT EXISTS idx_agent_first_mover
    ON agent_submissions(card_id, first_mover_at);

-- Each individual eval execution.
--
-- `polaris_verified` is true when the eval ran on a Polaris-managed
-- runtime (manifest fetched and verified). False for BYO-compute miners
-- and for failed Polaris runs. The verified-runtime multiplier
-- (CONTRACTS.md §7.3) is applied at scoring time before the row is
-- inserted; this column persists the verification status for downstream
-- audit and frontend display.
CREATE TABLE IF NOT EXISTS eval_runs (
    id                  TEXT PRIMARY KEY,
    submission_id       TEXT NOT NULL REFERENCES agent_submissions(id) ON DELETE CASCADE,
    epoch               INTEGER NOT NULL,
    round_index         INTEGER NOT NULL,
    polaris_agent_id    TEXT NOT NULL,
    polaris_run_id      TEXT NOT NULL,
    task_json           TEXT NOT NULL,
    output_card_json    TEXT NOT NULL,
    output_card_hash    TEXT NOT NULL,
    score_parts         TEXT NOT NULL,
    weighted_score      REAL NOT NULL,
    ran_at              TEXT NOT NULL,
    duration_ms         INTEGER NOT NULL,
    errors              TEXT,
    cathedral_signature TEXT NOT NULL,
    polaris_verified    INTEGER NOT NULL DEFAULT 0,
    polaris_attestation TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_submission_time
    ON eval_runs(submission_id, ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_epoch ON eval_runs(epoch);
CREATE INDEX IF NOT EXISTS idx_eval_card_score
    ON eval_runs(submission_id, weighted_score DESC);
CREATE INDEX IF NOT EXISTS idx_eval_ran_at ON eval_runs(ran_at DESC);

-- Weekly Merkle anchors of eval results.
CREATE TABLE IF NOT EXISTS merkle_anchors (
    epoch                    INTEGER PRIMARY KEY,
    merkle_root              TEXT NOT NULL,
    eval_count               INTEGER NOT NULL,
    leaf_hashes_json         TEXT NOT NULL,
    computed_at              TEXT NOT NULL,
    on_chain_block           INTEGER,
    on_chain_extrinsic_index INTEGER
);

CREATE TABLE IF NOT EXISTS eval_run_to_epoch (
    eval_run_id TEXT PRIMARY KEY REFERENCES eval_runs(id) ON DELETE CASCADE,
    epoch       INTEGER NOT NULL REFERENCES merkle_anchors(epoch)
);
CREATE INDEX IF NOT EXISTS idx_eval_run_epoch ON eval_run_to_epoch(epoch);
"""


async def connect(database_path: str) -> aiosqlite.Connection:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(database_path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    await _apply_migrations(conn)
    await conn.commit()
    return conn


async def _apply_migrations(conn: aiosqlite.Connection) -> None:
    """Idempotent column additions for tables created by an earlier schema.

    `CREATE TABLE IF NOT EXISTS` is a no-op when the table already exists,
    so new columns must be added via ALTER TABLE here. Each block guards
    on the current column set so re-running the connect() bootstrap is safe.
    """
    # eval_runs.polaris_verified — added for the BYO-compute flow. Defaults
    # to 0 (false) for any rows inserted before the column existed; the
    # scoring pipeline writes the real value going forward.
    cur = await conn.execute("PRAGMA table_info(eval_runs)")
    cols = {row[1] for row in await cur.fetchall()}
    if "polaris_verified" not in cols:
        await conn.execute(
            "ALTER TABLE eval_runs ADD COLUMN polaris_verified INTEGER NOT NULL DEFAULT 0"
        )
    # eval_runs.polaris_attestation — added for the Tier A Polaris-runtime
    # flow. Nullable JSON blob; only populated when the runner returned a
    # verified attestation. Existing rows stay NULL.
    if "polaris_attestation" not in cols:
        await conn.execute("ALTER TABLE eval_runs ADD COLUMN polaris_attestation TEXT")

    # eval_runs.trace_json — added for the v2 Polaris-native Hermes flow.
    # Nullable JSON blob holding the Hermes-emitted trace (tool_calls,
    # model_calls, source_fetches, agentic_loop_depth, start_at, end_at).
    # Stored as an UNSIGNED sidecar: it does NOT participate in the
    # canonical signed bytes (cathedral_signature is computed before the
    # trace is attached), so old validators verify v2 rows unchanged.
    # Promoted to signed in v2.1 once the schema is stable.
    if "trace_json" not in cols:
        await conn.execute("ALTER TABLE eval_runs ADD COLUMN trace_json TEXT")

    # eval_runs.polaris_manifest — v2 deploy runner pulls the signed
    # manifest after the chat round trips so it can be persisted
    # alongside the trace. Nullable; only populated for v2 runs.
    if "polaris_manifest" not in cols:
        await conn.execute("ALTER TABLE eval_runs ADD COLUMN polaris_manifest TEXT")

    # agent_submissions: attestation_mode branching (polaris/tee/unverified).
    # Existing rows default to 'polaris' so back-compat with pre-attestation
    # miners holds — they were always on the verified path. SQLite cannot
    # add a column WITH a CHECK constraint in ALTER TABLE, so the constraint
    # is enforced at the application layer for existing tables; new tables
    # created via SCHEMA above carry the CHECK natively.
    cur = await conn.execute("PRAGMA table_info(agent_submissions)")
    sub_cols = {row[1] for row in await cur.fetchall()}
    if "attestation_mode" not in sub_cols:
        await conn.execute(
            "ALTER TABLE agent_submissions ADD COLUMN attestation_mode "
            "TEXT NOT NULL DEFAULT 'polaris'"
        )
    if "attestation_type" not in sub_cols:
        await conn.execute("ALTER TABLE agent_submissions ADD COLUMN attestation_type TEXT")
    if "attestation_blob" not in sub_cols:
        await conn.execute("ALTER TABLE agent_submissions ADD COLUMN attestation_blob BLOB")
    if "attestation_verified_at" not in sub_cols:
        await conn.execute("ALTER TABLE agent_submissions ADD COLUMN attestation_verified_at TEXT")
    if "discovery_only" not in sub_cols:
        await conn.execute(
            "ALTER TABLE agent_submissions ADD COLUMN discovery_only INTEGER NOT NULL DEFAULT 0"
        )
