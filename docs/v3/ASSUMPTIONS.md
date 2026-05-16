# Cathedral v3: Assumptions and Defaults

This document captures every assumption made during the v3 rewrite so an operator can challenge or override each one. Where a default ships with the code, the env var and config field are listed alongside.

## Hard assumptions (would require a redesign to change)

1. **Tool-mediated execution is the verification primitive.** v3 observes by owning the tool catalog. Any miner that escapes the `ToolBus` (e.g. opens a raw socket from inside its model) defeats observation. The threat model is "miners try to game the score with their model output", not "miners try to root the runtime". For a full attestation story we still rely on Polaris-deploy or SSH-probe runtimes from v1; those are now `MinerAgent` implementations.

2. **One-process default is intentional.** Job generator, miner runner, validator, scorer, signer, and archive all live in one Python process by default. The component boundaries are designed so any of them can be lifted into its own service, but doing so on day one would force premature wire-format decisions. The boundaries are pydantic models; that's enough.

3. **SQLite is enough for the local archive.** Trajectories are append-only, queries are bounded by miner and time, and a single validator generates O(10k) rows/day. Postgres is a config switch (`CATHEDRAL_V3_DB_URL`) when it matters.

4. **Signatures use ed25519 by default.** This branch only implements ed25519, so `signature_scheme` on the wire is always `"ed25519"`. The receipt schema is a versioned blob with a `scheme` field, so adding sr25519 (via a bittensor wallet) in a later branch is forward-compatible; `CATHEDRAL_V3_WALLET` today only configures the on-chain weight push, not signing.

5. **Scores live in `[0, 1]`.** Every rubric must produce this range. Per-dimension weights compose multiplicatively for the final score; the weight loop only sees the composed score.

## Soft assumptions (overridable via config or env)

**Implemented overrides** (the env var is read by code on this branch):

| Assumption | Default | Override |
|---|---|---|
| LLM base URL | `https://llm.chutes.ai/v1` | `CATHEDRAL_V3_LLM_BASE_URL` |
| LLM API key | unset (LLM miner falls back to heuristic) | `CATHEDRAL_V3_LLM_API_KEY` |
| LLM model | `MiniMax-M2.5-TEE` | `CATHEDRAL_V3_LLM_MODEL` |
| Archive dir | `~/.cathedral/v3/` | `CATHEDRAL_V3_HOME` (or `--home` on the CLI) |
| Signing key | ed25519 keypair auto-generated at first run, persisted to `$CATHEDRAL_V3_HOME/signer.key` | `CATHEDRAL_V3_SIGNING_KEY` (hex seed) |
| EMA half-life for weights | 50 trajectories | `CATHEDRAL_V3_EMA_HALF_LIFE` |
| Distillation `gold` threshold | score ≥ 0.85 | `CATHEDRAL_V3_GOLD_THRESHOLD` |
| On-chain weight push (stubbed, unverified) | off | `CATHEDRAL_V3_CHAIN_ENABLED=1` plus `CATHEDRAL_V3_WALLET`, `CATHEDRAL_V3_NETUID`, `CATHEDRAL_V3_NETWORK` |
| Miners spawned by `serve` | 3 (echo, heuristic, llm) | `--miners echo,heuristic,llm` |
| Task types per tick | all five | `--task-types research,code_patch,...` |
| Tick interval between ticks in `serve` | `0` seconds (back-to-back) | `--interval 30` |

**Planned overrides** (referenced in some places but **not read by code on this branch**):

| Knob | Status | Notes |
|---|---|---|
| `CATHEDRAL_V3_DB_URL` | planned | The archive is hardcoded to `$CATHEDRAL_V3_HOME/archive.db` (SQLite WAL). Postgres support is a future toggle. |
| `CATHEDRAL_V3_ARTIFACTS_DIR` | planned | Artifacts ride inside the trajectory record today; there is no separate artifact dir. |
| `CATHEDRAL_V3_MAX_TRAJECTORY_BYTES` | planned | No enforcement today. |
| `--tool-timeout` | planned | The `code_patch` fixture runner has a hardcoded 15s timeout (`_FIXTURE_TIMEOUT_SECONDS` in `validator/tools.py`); other tools are unbounded. |

## What v3 does NOT assume

- That you have a bittensor wallet. Local mode works without one.
- That you have an LLM key. Echo + heuristic miners cover the loop end-to-end with no network access. The `llm` miner gracefully degrades when `CATHEDRAL_V3_LLM_API_KEY` is unset.
- That you have Polaris or SSH access to miners. Those are alternative `MinerAgent` implementations not required for the local loop.
- That v1 is shut down. v3 lives in `src/cathedral/v3/` and shares nothing with v1's import surface. You can run v1 validators and v3 alongside on the same machine with different DBs.

## What the operator MUST decide

1. Pick an LLM key (or skip and run echo+heuristic only). Set `CATHEDRAL_V3_LLM_API_KEY` if you want the `llm` miner active.
2. Decide whether to seed the archive (`cathedral-v3 seed-jobs --count 20`) or start empty.
3. Decide whether weights go on-chain (`CATHEDRAL_V3_CHAIN_ENABLED=1`) or stay local. Default is local.

That's it. Everything else has a working default.

## Known limitations

- The shipped `code_patch` task type only covers single-file Python diffs. Multi-file patches and other languages are flagged as next-step work.
- The `multi_step` task type uses a fixture-based world (a key-value store + a fake search index). A real world (real APIs, real shell, real browser) requires sandbox infrastructure that doesn't ship in this rewrite.
- The `bug_repro` Docker sandbox runner (`cathedral.v3.sandbox`) is **alpha and dev-harness only**. Production `bug_repro` execution belongs in the **Cathedral publisher SSH runner**, mirroring the v1 ssh-probe pattern: publisher SSHs into miner box, runs candidate against hidden oracle, signs result with the existing Cathedral key; validators verify signed output. Tracked in #123. Until the publisher SSH runner ships (Phase 1.5 in `ROADMAP.md`), `bug_repro` stays `OPERATOR_REVIEW` by default and carries no rewardable weight on mainnet.
- Replay is single-miner. Differential replay across an entire historical batch is a v3.1 item.
- Dataset export emits JSONL but does not yet ship a packing / tokenization pipeline; that's the trainer's job; we provide the rows.
- No metagraph fetch in local mode; weights are normalized over miners-present-this-tick.
