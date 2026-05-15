# Architecture

## One-line

A miner submits bundles to the publisher. The publisher runs the eval and signs each projection. Validators pull the signed projections, verify them locally, blend them with legacy `/v1/claim` scores, and set weights.

## Component map

```
            ┌───────────────┐
miner ─────▶│ POST /v1/claim│   bearer-protected (CATHEDRAL_BEARER),
            ├───────────────┤   async insert into sqlite. Legacy path.
            │ FastAPI       │
            └──────┬────────┘
                   │
          ┌────────▼────────┐    ┌────────────────────┐
          │ worker loop     │───▶│ cathedral.evidence │
          │ (legacy /v1/    │    │ fetch + verify +   │
          │  claim drain)   │    │ filter (Ed25519,   │
          └────────┬────────┘    │ BLAKE3)            │
                   │             └────────┬───────────┘
                   ▼                      │
          ┌────────────────┐              │
          │ cathedral.cards│◀─────────────┘
          │ preflight +    │       EvidenceBundle
          │ score          │
          └────────┬───────┘
                   │ ScoreParts -> scores table
                   ▼
          ┌────────────────┐
          │ weight loop    │
          │ (timer)        │
          └────────┬───────┘
                   │ blends scores + pulled_eval_runs
                   ▼
            cathedral.chain (metagraph + set_weights)

publisher ────▶ GET /v1/leaderboard/recent
                       │
                       ▼
          ┌─────────────────────┐
          │ pull loop           │ verifies cathedral_signature
          │ (default 30s tick)  │ with CATHEDRAL_PUBLIC_KEY_HEX,
          │ disabled if         │ writes to pulled_eval_runs.
          │ pubkey unset        │
          └─────────────────────┘
                       │
                       ▼
                  stall watchdog
                       │
                       ▼
            /health   surfaces all of the above
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

`cathedral-validator serve` wires four asyncio tasks inside the FastAPI lifespan:

1. **`run_worker`** (`cathedral.validator.worker`): drains pending `/v1/claim` claims, verifies Polaris evidence, scores, persists. This is the legacy path and is constructed unconditionally, which is why `polaris.public_key_hex` remains required in the TOML.
2. **`run_pull_loop`** (`cathedral.validator.pull_loop`): polls `GET /v1/leaderboard/recent` on the publisher (default cadence 30s, configurable via `publisher.pull_interval_secs`), verifies each `EvalRun` projection with `CATHEDRAL_PUBLIC_KEY_HEX`, and upserts into `pulled_eval_runs`. Heartbeats `last_evidence_pass_at`. Only spawned when `CATHEDRAL_PUBLIC_KEY_HEX` is set; otherwise startup logs `pull_loop_disabled` and the loop is skipped (the `initial_backfill_complete` signal is set immediately so the weight loop never hangs).
3. **`run_weight_loop`** (`cathedral.validator.weight_loop`): every `weights.interval_secs`, awaits `initial_backfill_complete`, reads metagraph, joins the latest score per hotkey across both `scores` and `pulled_eval_runs`, normalizes, calls chain. Heartbeats `last_metagraph_at`, `last_weight_set_at`.
4. **`run_stall_watchdog`** (`cathedral.validator.stall`): every 30s, marks `stalled=true` if any heartbeat is older than `stall.after_secs`, and refreshes claim count fields.

All loops share a `Health` snapshot guarded by `asyncio.Lock`. The HTTP `/health` endpoint reads it without blocking the writers.

## Database

Sqlite with WAL mode. The legacy worker is the single writer for `claims`, `evidence_bundles`, and `scores`. The pull loop writes its own table, `pulled_eval_runs`. The weight loop reads from both. Readers (CLI, `/health`) tolerated:

- `claims`: submitted legacy `/v1/claim` claims and their lifecycle
- `evidence_bundles`: verified bundle JSON per claim
- `scores`: one row per verified legacy claim, joined back to `miner_hotkey`
- `pulled_eval_runs`: rows pulled from `/v1/leaderboard/recent`, keyed by `eval_run_id`, with `miner_hotkey` for the weight join
- `pull_loop_meta`: durable single-row markers (e.g. `initial_backfill_completed_at`) so an upgrade cleanly re-walks the scoring window
- `health_kv`: reserved for future use

## Issue traceability

| Issue | Module(s) | Tests |
|---|---|---|
| #2 verify Polaris worker evidence | `cathedral.evidence`, `cathedral.validator.worker`, `cathedral.validator.queue` | `tests/test_evidence_collector.py`, `tests/test_filter.py`, `tests/test_claim.py` |
| #3 regulatory cards useful and verifiable | `cathedral.cards` | `tests/test_preflight.py`, `tests/test_scorer.py` |
| #1 validator ops safe and observable | `cathedral.validator.{auth,health,stall}`, `cathedral.cli`, `docs/validator/RUNBOOK.md` | `tests/test_validator_http.py`, `tests/test_weights.py` |

## What this repo deliberately omits

- GPU verification, hardware fingerprinting
- Rental flow, billing, k8s/k3s, miner prover daemons
- POM, ModelFactory, cost-collapse marketplace logic
- Public ledger, treasury dashboards, blog content
- Subnet scouting, broad external miner outreach

If a future story crosses into one of those areas, it goes in a sibling repo, not here.

> SSH probing was historically listed as omitted; it landed in v1. The ssh-probe runner (`src/cathedral/eval/ssh_hermes_runner.py`, `src/cathedral/eval/ssh_probe_runner.py`) is the live BYO-compute path, and the submit endpoint requires `ssh_host` + `ssh_user` whenever `attestation_mode='ssh-probe'`. Polaris-hosted Tier A (`PolarisRuntimeRunner`, `PolarisDeployRunner`) remains in the architecture but is gated behind `CATHEDRAL_ENABLE_POLARIS_DEPLOY=true`; with the flag off (the v1 default) the submit boundary rejects Tier A modes with `tier_a_disabled_for_v1`. TEE attestation (Nitro / TDX / SEV-SNP) is spec-only in v1: Nitro verifier is wired but no live TEE miners; TDX and SEV-SNP return 501 from the submit endpoint.
