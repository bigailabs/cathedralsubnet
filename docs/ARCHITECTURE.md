# Architecture

## One-line

A miner submits a Polaris agent claim. The validator pulls signed Polaris records, verifies them, scores the resulting card, and sets weights.

## Component map

```
            ┌──────────────┐
miner ─────▶│ POST /v1/claim│   bearer-protected, async insert into sqlite
            ├──────────────┤
            │ FastAPI      │
            └──────┬───────┘
                   │
          ┌────────▼────────┐    ┌────────────────────┐
          │ worker loop     │───▶│ cathedral.evidence │
          │ (asyncio)       │    │ fetch + verify +   │
          └────────┬────────┘    │ filter (Ed25519,   │
                   │             │ BLAKE3)            │
                   ▼             └────────┬───────────┘
          ┌────────────────┐              │
          │ cathedral.cards│◀─────────────┘
          │ preflight +    │       EvidenceBundle
          │ score          │
          └────────┬───────┘
                   │ ScoreParts → sqlite scores table
                   ▼
          ┌────────────────┐    ┌────────────────────┐
          │ weight loop    │───▶│ cathedral.chain    │
          │ (timer)        │    │ metagraph + weights│
          └────────┬───────┘    └────────────────────┘
                   │
                   ▼
          ┌────────────────┐
          │ /health        │   public, surfaces all of the above
          └────────────────┘
```

## Module dependency graph

```
cathedral.types          (no internal deps)
   ▲
   ├── cathedral.chain
   ├── cathedral.evidence
   └── cathedral.cards

cathedral.validator depends on all three + sqlite
cathedral.miner     depends on cathedral.types + httpx
cathedral.cli       depends on httpx + typer
```

## What lives where

| Concern | Module |
|---|---|
| Wire types (claims, manifests, cards) | `cathedral.types` |
| Bittensor metagraph + weight setting | `cathedral.chain` |
| Polaris fetch + Ed25519 + hash check | `cathedral.evidence` |
| Card registry + scoring + preflight | `cathedral.cards` |
| HTTP, sqlite queue, loops, bearer auth | `cathedral.validator` |
| Miner claim submission | `cathedral.miner` |
| Operator inspection commands | `cathedral.cli` |

## Async loops

Three asyncio tasks run inside the FastAPI lifespan:

1. **`run_worker`** (`cathedral.validator.worker`) — drains pending claims, verifies, scores, persists. Heartbeats `last_evidence_pass_at`.
2. **`run_weight_loop`** (`cathedral.validator.weight_loop`) — every `weights.interval_secs`, reads metagraph, joins scores by hotkey to uid, normalizes, calls chain. Heartbeats `last_metagraph_at`, `last_weight_set_at`.
3. **`run_stall_watchdog`** (`cathedral.validator.stall`) — every 30s, marks `stalled=true` if any heartbeat is older than `stall.after_secs`, and refreshes claim count fields.

All three share a `Health` snapshot guarded by `asyncio.Lock`. The HTTP `/health` endpoint reads it without blocking the writers.

## Database

Sqlite with WAL mode. Single writer (the worker), readers tolerated. Schema in `cathedral.validator.db.SCHEMA`:

- `claims` — submitted claims and their lifecycle
- `evidence_bundles` — verified bundle JSON per claim
- `scores` — one row per verified claim, joined back to `miner_hotkey`
- `health_kv` — reserved for future use

## Issue traceability

| Issue | Module(s) | Tests |
|---|---|---|
| #2 verify Polaris worker evidence | `cathedral.evidence`, `cathedral.validator.worker`, `cathedral.validator.queue` | `tests/test_evidence_collector.py`, `tests/test_filter.py`, `tests/test_claim.py` |
| #3 regulatory cards useful and verifiable | `cathedral.cards` | `tests/test_preflight.py`, `tests/test_scorer.py` |
| #1 validator ops safe and observable | `cathedral.validator.{auth,health,stall}`, `cathedral.cli`, `docs/validator/RUNBOOK.md` | `tests/test_validator_http.py`, `tests/test_weights.py` |

## What this repo deliberately omits

- GPU verification, SSH probing, hardware attestation
- Rental flow, billing, k8s/k3s, miner prover daemons
- POM, ModelFactory, cost-collapse marketplace logic
- IP-first miner proof for Polaris-hosted workers
- Public ledger, treasury dashboards, blog content
- Subnet scouting, broad external miner outreach

If a future story crosses into one of those areas, it goes in a sibling repo, not here.
