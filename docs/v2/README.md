# Cathedral v2 — quickstart

A complete, runnable rewrite of the Cathedral subnet around a verifiable
agentic workforce + first-class trajectory archive. Lives at
`src/cathedral/v2/` and does not touch v1.

> Read first: `docs/v2/ARCHITECTURE.md`, `docs/v2/ASSUMPTIONS.md`,
> `docs/v2/ROADMAP.md`.

## Setup

```bash
# from repo root
uv pip install -e .
uv pip install pynacl pytest pytest-asyncio   # if not already in your venv
```

That's it. No bittensor wallet, no LLM key, no infrastructure required.

## Run the loop

```bash
# 2 ticks × 5 task types × 2 miners = 20 trajectories
python -m cathedral.v2.cli serve --ticks 2 --miners echo,heuristic --interval 0
```

Or with the LLM miner (set `CATHEDRAL_V2_LLM_API_KEY` to a Chutes-style key first;
without a key it falls back to heuristic-shaped trajectories):

```bash
export CATHEDRAL_V2_LLM_API_KEY=...
python -m cathedral.v2.cli serve --ticks 5 --miners echo,heuristic,llm
```

## Inspect the archive

```bash
python -m cathedral.v2.cli archive stats
python -m cathedral.v2.cli archive best --task-type code_patch --k 5
python -m cathedral.v2.cli archive fails
python -m cathedral.v2.cli archive miner hk_heuristic --limit 10
```

## Export training data

```bash
python -m cathedral.v2.cli export sft --out /tmp/sft.jsonl --min-score 0.85
python -m cathedral.v2.cli export dpo --out /tmp/dpo.jsonl --min-delta 0.20
python -m cathedral.v2.cli export rm  --out /tmp/rm.jsonl
```

Each export writes a sibling `.manifest.json` containing the row count,
filter, per-row hash sample, aggregate BLAKE3 hash, and an ed25519
signature of the manifest by the Cathedral key.

## Replay a job against a different miner

```bash
python -m cathedral.v2.cli replay <trajectory_id> --miner heuristic
```

Returns the divergence point (first step where the new miner takes a
different action) and the score delta.

## Verify a stored receipt

```bash
python -m cathedral.v2.cli verify-receipt <trajectory_id>
```

## Submit a single test job

```bash
python -m cathedral.v2.cli submit-job --task-type tool_route --miner heuristic --seed 0
```

## Run the smoke tests

```bash
python -m pytest tests/v2/ -v
```

Phase 0 ships 58 tests: end-to-end smoke (6), cross-process determinism (26), tamper-evidence (5), and code_patch subprocess hardening (21).

## File map

```
src/cathedral/v2/
├── __init__.py            # public type re-exports
├── types.py               # JobSpec, ToolCall, AgentResult, Trajectory, Receipt, Weights
├── runtime.py             # one-process runtime; .tick() / .serve()
├── jobs/
│   ├── generator.py       # deterministic per-task-type job generators
│   └── fixtures.py        # bundled corpora / test fixtures
├── miner/
│   ├── base.py            # MinerAgent protocol
│   ├── echo.py            # baseline
│   ├── heuristic.py       # rule-based per task type
│   └── llm.py             # OpenAI-compatible ReAct loop (Chutes default)
├── validator/
│   ├── observer.py        # Validator.dispatch(job, miner) -> Trajectory
│   ├── toolbus.py         # ToolBus: the observation primitive
│   └── tools.py           # per-task-type tool handlers
├── scoring/
│   ├── rubrics.py         # per-task-type rubric scoring
│   └── weights.py         # EMA weight loop + optional chain push
├── receipt/
│   └── signer.py          # ed25519 receipt signer + verifier
├── archive/
│   └── store.py           # SQLite trajectory store + queries
├── export/
│   └── datasets.py        # SFT / DPO / RM jsonl + signed manifests
├── replay/
│   └── engine.py          # replay a trajectory against a new miner
└── cli/
    ├── main.py            # `cathedral-v2 ...` (also: python -m cathedral.v2.cli)
    └── __main__.py
```

## What this gives you

After one `serve --ticks 100`:

- 1000+ structured trajectories, each with: job spec, full tool trace, intermediate artifacts, final output, scored dimensions, failure class, distillation readiness flag, BLAKE3 bundle hash, ed25519 receipt
- Failure clusters surfaced per task type
- Preference pairs auto-generated from same-job miner sibling pairs
- Per-miner EMA weights, normalized
- Three exportable JSONL datasets ready for SFT / DPO / RM training pipelines
- A replay engine to A/B candidate miners against historical jobs

This is the data substrate. Phase 1 of the roadmap (`ROADMAP.md`) is "fill
it". Phase 2 is "feed a trainer". Phase 3 is "become the data substrate
other teams build on".
