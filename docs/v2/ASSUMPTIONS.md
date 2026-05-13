# Cathedral v2 — Assumptions and Defaults

This document captures every assumption made during the v2 rewrite so an operator can challenge or override each one. Where a default ships with the code, the env var and config field are listed alongside.

## Hard assumptions (would require a redesign to change)

1. **Tool-mediated execution is the verification primitive.** v2 observes by owning the tool catalog. Any miner that escapes the `ToolBus` (e.g. opens a raw socket from inside its model) defeats observation. The threat model is "miners try to game the score with their model output", not "miners try to root the runtime". For a full attestation story we still rely on Polaris-deploy or SSH-probe runtimes from v1; those are now `MinerAgent` implementations.

2. **One-process default is intentional.** Job generator, miner runner, validator, scorer, signer, and archive all live in one Python process by default. The component boundaries are designed so any of them can be lifted into its own service, but doing so on day one would force premature wire-format decisions. The boundaries are pydantic models; that's enough.

3. **SQLite is enough for the local archive.** Trajectories are append-only, queries are bounded by miner and time, and a single validator generates O(10k) rows/day. Postgres is a config switch (`CATHEDRAL_V2_DB_URL`) when it matters.

4. **Signatures use ed25519 by default.** sr25519 is supported when a bittensor wallet is present (`CATHEDRAL_V2_WALLET=…`); the wire format includes `signature_scheme` so verifiers dispatch correctly. The receipt schema does not assume either scheme — it's just a versioned blob with a `scheme` field.

5. **Scores live in `[0, 1]`.** Every rubric must produce this range. Per-dimension weights compose multiplicatively for the final score; the weight loop only sees the composed score.

## Soft assumptions (overridable via config or env)

| Assumption | Default | Override |
|---|---|---|
| LLM provider | Chutes (`https://llm.chutes.ai/v1`) | `CATHEDRAL_V2_LLM_BASE_URL`, `CATHEDRAL_V2_LLM_API_KEY` |
| LLM model | `MiniMax-M2.5-TEE` | `CATHEDRAL_V2_LLM_MODEL` |
| Archive dir | `~/.cathedral/v2/` | `CATHEDRAL_V2_HOME` |
| DB path | `$CATHEDRAL_V2_HOME/archive.db` | `CATHEDRAL_V2_DB_URL` (supports `sqlite:///` and `postgres://`) |
| Artifact dir | `$CATHEDRAL_V2_HOME/artifacts/` | `CATHEDRAL_V2_ARTIFACTS_DIR` |
| Signing key | ed25519 keypair auto-generated at first run | `CATHEDRAL_V2_SIGNING_KEY` (hex seed) or `CATHEDRAL_V2_WALLET` (bittensor wallet name) |
| Number of miners spawned by `serve` | 3 (echo, heuristic, llm) | `cathedral-v2 serve --miners echo,heuristic,llm` |
| Jobs per tick | 1 of each task type | `--task-types research,code_patch,...` |
| Tick interval | 30s | `--tick-interval 30` |
| EMA half-life for weights | 50 trajectories | `CATHEDRAL_V2_EMA_HALF_LIFE` |
| Tool timeout | 30s per tool call | `--tool-timeout 30` |
| Max trajectory bytes | 10 MiB | `CATHEDRAL_V2_MAX_TRAJECTORY_BYTES` |
| On-chain weight setting | off | `CATHEDRAL_V2_CHAIN_ENABLED=1` + wallet config |
| Distillation `gold` threshold | score ≥ 0.85 | `CATHEDRAL_V2_GOLD_THRESHOLD` |

## What v2 does NOT assume

- That you have a bittensor wallet. Local mode works without one.
- That you have an LLM key. Echo + heuristic miners cover the loop end-to-end with no network access. The `llm` miner gracefully degrades when `CATHEDRAL_V2_LLM_API_KEY` is unset.
- That you have Polaris or SSH access to miners. Those are alternative `MinerAgent` implementations not required for the local loop.
- That v1 is shut down. v2 lives in `src/cathedral/v2/` and shares nothing with v1's import surface. You can run v1 validators and v2 alongside on the same machine with different DBs.

## What the operator MUST decide

1. Pick an LLM key (or skip and run echo+heuristic only). Set `CATHEDRAL_V2_LLM_API_KEY` if you want the `llm` miner active.
2. Decide whether to seed the archive (`cathedral-v2 seed-jobs --count 20`) or start empty.
3. Decide whether weights go on-chain (`CATHEDRAL_V2_CHAIN_ENABLED=1`) or stay local. Default is local.

That's it. Everything else has a working default.

## Known limitations

- The shipped `code_patch` task type only covers single-file Python diffs. Multi-file patches and other languages are flagged as next-step work.
- The `multi_step` task type uses a fixture-based world (a key-value store + a fake search index). A real world (real APIs, real shell, real browser) requires sandbox infrastructure that doesn't ship in this rewrite.
- Replay is single-miner. Differential replay across an entire historical batch is a v2.1 item.
- Dataset export emits JSONL but does not yet ship a packing / tokenization pipeline — that's the trainer's job; we provide the rows.
- No metagraph fetch in local mode; weights are normalized over miners-present-this-tick.
