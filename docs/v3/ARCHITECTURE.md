# Cathedral v3 — Verifiable Agentic Workforce (Trajectory Data Substrate)

> Their compute. Their models. Their cognition. Our verification.
>
> v1 verified that a single LLM call happened in a sealed runtime against a miner's `soul.md` and emitted a six-dimension regulatory card. v3 expands the loop into a generalized agentic workforce: validators issue heterogeneous jobs, miners run real agentic loops with tool calls, every step of execution is captured, scored, signed, and persisted as a structured trajectory. The trajectory archive is the data substrate that future code-eval job families (bug_repro, test_gen, ...) and other verified-run job families will land on top of.

## Scope

This branch ships the **v3 trajectory data substrate**: the generalized agent loop, validator tool bus, scoring, ed25519 receipts, BLAKE3 bundle hashing, SQLite archive, dataset exports, replay engine, EMA weight computation. It is **not** a replacement for the v1 validator/miner/publisher stack; v1 continues to run unchanged at `cathedral.*` outside this package.

Coding-job families build on this substrate. **`bug_repro` (Phase 1 alpha), the Docker-backed sandbox runner (`cathedral.v3.sandbox`), the signed repo bundle builder (`cathedral.v3.bundle`), the coding-specific failure-class enum (`CodingFailureClass`), and the hidden-field firewall for SFT/DPO/RM exports are all included in this PR.** `test_gen` and the mutation harness remain out of scope; they land in Phase 2 after `bug_repro` is calibrated.

A hard sandbox gate enforces the trust boundary: `bug_repro` refuses to award any positive score when the sandbox backend is anything other than Docker, unless the operator explicitly opts into the trusted-fixture escape hatch via `CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS=1`, in which case readiness stays permanently `NEGATIVE`.

### Trust boundary: validator-local sandbox vs Cathedral evaluator service

The Docker-backed `cathedral.v3.sandbox` runner shipped on this branch is an **alpha and dev-harness path only**. It exists so the substrate, oracle, scoring rubric, export firewall, and `bug_repro` fixtures can be exercised end-to-end on a single operator's box. It is **not** the production execution path.

In production, validators should not be executing arbitrary miner-submitted `bug_repro` test code locally. That follows the same trust model as v1 regulatory cards: validators verify signed evaluator output, they do not run untrusted user code themselves. The production `bug_repro` flow is:

1. Miner submits a candidate test against a job.
2. A Cathedral-controlled **evaluator service** (separate process, isolated infra, hardened image) executes the candidate against the hidden buggy source, the hidden fixed source, and the symptom oracle.
3. The evaluator emits a signed result (oracle outputs + readiness + failure class + bundle hashes), using the same ed25519 receipt posture as v1 Cathedral signatures.
4. Validators consume the signed evaluator result, verify the signature, and feed the verified result into the archive and weight loop.

This branch ships steps 1, the local execution backend that step 2 will replace, the scoring/rubric that step 4 will consume, and the export firewall that protects the held-out oracle fields. Steps 2 and 3 (the central evaluator service plus its signing key, attestation surface, and operator runbook) are tracked in #123 and are required before `bug_repro` carries any rewardable weight on mainnet. Until that issue lands, `bug_repro` stays `OPERATOR_REVIEW` by default and is exported only after manual operator promotion.

## The thesis, restated

A subnet is only as valuable as the data it generates. Cathedral v1 generated **answers**. Cathedral v3 generates **labour**: full trajectories of agent reasoning, tool use, intermediate artifacts, scored outcomes, and signed receipts. Every miner-validator interaction emits a row of training data. After N weeks the archive *is* the SFT corpus, the DPO preference set, the reward-model training signal, and the distillation target.

This is not an eval subnet. It is a labour market where the labour is the product.

## Capability status

This branch is the **launch candidate for the v3 trajectory data substrate plus the `bug_repro` Phase 1 alpha**. It is not a drop-in replacement for the v1 subnet.

| Capability | Status | Notes |
|---|---|---|
| Five generic task types end-to-end on fixtures | implemented | research, code_patch, tool_route, multi_step, classify |
| `bug_repro` coding task (Phase 1 alpha) | implemented | 3 curated fixtures, validator-side oracle, `task_split=OPERATOR_REVIEW` by default |
| `CodingFailureClass` enum | implemented | `sandbox_violation`, `no_bug_repro`, `fixed_commit_fails`, `flake`, ... |
| `TaskSplit` enum + export filter | implemented | default exports refuse `OPERATOR_REVIEW` and `HELDOUT_EVAL`; `HELDOUT_EVAL` is **never** exportable even with explicit `allowed_splits` |
| Sandbox runner | implemented, **alpha / dev-harness only** | `cathedral.v3.sandbox` with `DockerBackend` (real isolation) and `SubprocessBackend` (degraded fallback). Local-validator execution is for substrate development; production `bug_repro` execution belongs in a Cathedral evaluator service (see "Trust boundary" above). `bug_repro` rubric refuses any positive score from subprocess unless `CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS=1` is set, and even then readiness stays `NEGATIVE`. |
| Cathedral evaluator service (signed `bug_repro` results) | **planned** | Production path. Validators consume signed evaluator output, do not execute miner test code locally. Tracked separately. |
| Signed repo bundle builder | implemented | `cathedral.v3.bundle`: per-file BLAKE3, aggregate BLAKE3, ed25519 signature. `verify_bundle` re-validates every entry path; `materialize_bundle` refuses to write outside `dest` |
| Hidden-field export firewall | implemented | `hidden_context` strings are scrubbed out of SFT tool args, SFT final output, DPO `chosen`/`rejected`, RM `completion`. Oracle result keys retain their schema but values become `<oracle-output>` |
| Echo / heuristic / LLM reference miners | implemented | LLM falls back to heuristic when `CATHEDRAL_V3_LLM_API_KEY` is unset |
| Tool-bus observation | implemented | in-process, per-job handler set |
| Per-task rubric scoring | implemented | scores in `[0, 1]`, failure class + readiness enum |
| ed25519 receipts + BLAKE3 bundle hash | implemented | `receipt_version="v3"` |
| SQLite trajectory archive | implemented | indexed, queryable |
| SFT / DPO / RM export + signed manifest | implemented | manifest hashes are BLAKE3 |
| Replay engine | implemented | single-miner, against a stored trajectory |
| Local EMA weight computation | implemented | normalized across miners present |
| `code_patch` fixture-only test runner | implemented, fixture-only | `subprocess.run` argv list, no shell, hard timeout, fresh tempdir. NOT a sandbox; separate from `cathedral.v3.sandbox`. |
| On-chain `set_weights` push | stubbed, unverified | code path exists behind `CATHEDRAL_V3_CHAIN_ENABLED=1`; not exercised on a live netuid in this branch |
| `test_gen` task type | **out of scope** | Phase 2, after `bug_repro` calibration |
| Mutation harness | **out of scope** | Phase 2 |
| HTTP recording proxy / egress allowlist | planned | LLM miner calls outbound directly |
| Validator quorum / inter-validator agreement | planned | Phase 4 |
| External job submission API | planned | Phase 4 |

## Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Cathedral v3 — one process                       │
│                                                                         │
│   ┌──────────────┐  job_id      ┌──────────────┐  trajectory            │
│   │ JobGenerator │ ───────────▶ │ JobDispatcher│ ──────────┐            │
│   └──────────────┘              └──────────────┘           │            │
│         ▲                              │                   ▼            │
│         │                              ▼            ┌─────────────┐     │
│   JobRegistry                  ┌──────────────┐    │ ValidatorObs │     │
│   (task types,                 │ MinerRunner  │◀──▶│  (records    │     │
│    rubrics, fixtures)          │ (per miner)  │    │   every step)│     │
│                                └──────────────┘    └──────────────┘     │
│                                                          │              │
│                                                          ▼              │
│                                                  ┌──────────────┐       │
│                                                  │   Scorer     │       │
│                                                  │ (rubric +    │       │
│                                                  │  task-type)  │       │
│                                                  └──────────────┘       │
│                                                          │              │
│                                                          ▼              │
│                                                  ┌──────────────┐       │
│                                                  │ ReceiptSigner│       │
│                                                  │  (ed25519)   │       │
│                                                  └──────────────┘       │
│                                                          │              │
│                       ┌──────────────────────────────────┘              │
│                       ▼                                                 │
│                ┌─────────────────────────────────────────────┐          │
│                │  TrajectoryArchive (SQLite + artifacts/)    │          │
│                │  - query by miner / score / task / time     │          │
│                │  - best-of and failure-cluster surfaces     │          │
│                │  - dataset export (SFT / DPO / RM)          │          │
│                │  - replay engine                            │          │
│                └─────────────────────────────────────────────┘          │
│                                                          │              │
│                                                          ▼              │
│                                                  ┌──────────────┐       │
│                                                  │ WeightSetter │       │
│                                                  │ (in-memory   │       │
│                                                  │  metagraph)  │       │
│                                                  └──────────────┘       │
└─────────────────────────────────────────────────────────────────────────┘
```

Each component is a small Python module under `src/cathedral/v3/`. The default deployment runs all of them inside one process with an asyncio event loop and a SQLite archive — same shape as v1, but the boundaries are clean enough that any component can be lifted into its own service later without rewriting the wire format.

### 1. Job generation (`cathedral.v3.jobs`)

A `JobSpec` is a typed task description with deterministic seeding. Five task types ship in v1:

| Task type | What miners do | Why this task |
|---|---|---|
| `research` | Answer a question with citations to a corpus | Tests retrieval-augmented reasoning. Carries v1 reg-intel forward. |
| `code_patch` | Produce a unified diff that makes a failing test pass | Trains future code agents. Deterministic ground truth. |
| `tool_route` | Pick the right tool and args from a tool catalog given a goal | Generates tool-use preference pairs. |
| `multi_step` | Chain ≥3 tool calls to reach a stated end state | Long-horizon agentic behaviour. The high-value trajectory class. |
| `classify` | Label inputs against a rubric (e.g. severity, jurisdiction, intent) | Cheap to grade, useful as fast warm-up jobs. |

A job carries `task_type`, `prompt`, `context` (sources / files / fixtures), `tools` (available tool catalog), `expected_artifacts`, and a `rubric` describing how the result will be graded. The generator can synthesize jobs from templates, replay jobs from the archive, or accept jobs from an external feed.

### 2. Miner agent loop (`cathedral.v3.miner`)

A miner is anything that implements:

```python
class MinerAgent(Protocol):
    hotkey: str
    async def run(self, job: JobSpec, tools: ToolBus) -> AgentResult: ...
```

v3 ships three reference miners:

- `EchoAgent` — returns the prompt unchanged. Baseline; produces useful "what does a zero-effort trajectory look like" data.
- `HeuristicAgent` — rule-based per task type. Solid floor for code-patch / classify.
- `LLMAgent` — calls Chutes (or any OpenAI-compatible endpoint) with a ReAct-style tool-using loop. The canonical real miner.

Every tool call routes through the `ToolBus`, which is what makes the trajectory observable. The `ToolBus` records every (tool_name, args, result, timestamp, latency_ms). By making tools the only side-effect channel the validator wants observed, it captures the full trace without instrumenting the model itself.

**Implemented today (Phase 0):**

- In-process Python handlers per task type (`validator/tools.py`). The miner asks the `ToolBus` for a named handler; the handler runs in the same Python process and returns a value the bus records.
- The `code_patch` `run_test` handler runs the fixture's `failing_test` against the candidate source via `subprocess.run` (argv list, no shell) inside a fresh `TemporaryDirectory` with a hard wall-clock timeout. This is **fixture-only**, designed against the bundled fixtures in `cathedral/v3/jobs/fixtures.py`; it is not a general code-execution sandbox.

**Planned (not in this branch):**

- File-system sandboxing scoped to a per-job `workdir` (currently no `workdir` is provisioned).
- HTTP recording proxy and per-tool egress allowlist (LLM miner currently calls outbound directly).
- Container / runtime attestation for miners that need it (deferred to Phase 1 alongside the Polaris-deploy `MinerAgent`).

### 3. Validator observation (`cathedral.v3.validator`)

The validator dispatches a job to a miner, hands them a `ToolBus` it owns, and watches. On completion it has:

- the prompt + full job context
- every tool call the miner made, with args + results + latencies
- the final output (text + structured fields) plus any `AgentResult.artifacts` dict the miner returned
- runtime metadata that miners populate today: `model_id`, `agent_error`. Token counts, wall time, container id are planned (Phase 1).

There is no per-job `workdir` in this branch; artifacts flow through `AgentResult.artifacts` and the `__sink_*` handler pattern.

This is the **trajectory**. It is the unit of work and the unit of data.

### 4. Scoring (`cathedral.v3.scoring`)

Each task type has a `Rubric` — a dimension list + a scoring function. Generic dimensions (`correctness`, `efficiency`, `cleanliness`, `groundedness`) compose with task-specific ones (`patch_applies`, `tests_pass`, `tool_select_acc`). Scores live in `[0, 1]` and feed both the receipt and the weight loop. The scorer also emits a `failure_class` enum (`tool_misuse`, `hallucinated_citation`, `wrong_format`, `timeout`, `irrelevant`, `none`) so the archive can cluster failures without re-reading every trace.

A `DistillationReadiness` flag is set per trajectory:

- `gold`: score ≥ 0.85 and no failure class — eligible for SFT
- `preference_winner` / `preference_loser`: paired siblings on the same job — eligible for DPO
- `negative`: clear failure with a clear right answer — eligible for reward-model negatives
- `discard`: too noisy to learn from

### 5. Receipt + signing (`cathedral.v3.receipt`)

A `Receipt` is the canonical, signed projection of a trajectory's identity and score. Fields: `trajectory_id`, `job_id`, `miner_hotkey`, `task_type`, `score`, `failure_class`, `bundle_hash` (BLAKE3 of the canonicalized trajectory), `signed_at`, `signature`. The signer is the Cathedral key (ed25519, sr25519-compatible on the bittensor side). Verifiers can validate a trajectory's score without trusting the archive — the signature commits to `bundle_hash`, and the bundle is reproducible from the stored trajectory.

### 6. Trajectory archive (`cathedral.v3.archive`)

SQLite-backed. One row per trajectory; artifacts (large outputs, diffs, traces) stored as files under `archive/artifacts/<trajectory_id>/` and referenced by hash. The archive answers:

- *Who* — `by_miner(hotkey, limit, since)`
- *What* — `by_task_type(type, score_min, limit)`
- *Why* — `failure_clusters(task_type) → list[FailureCluster]`
- *Show me the best* — `best_of(task_type, k)`
- *Show me a pair* — `preference_pair(job_id) → (winner, loser)`
- *Export* — `export_dataset(format, filter) → JSONL`

### 7. Dataset export (`cathedral.v3.export`)

Three formats out of the box:

- `sft.jsonl`: `{messages: [{role, content}], task_type, score}` — only `gold` trajectories
- `dpo.jsonl`: `{prompt, chosen, rejected, score_delta}` — preference pairs
- `rm.jsonl`: `{prompt, completion, score, dimensions}` — reward model training

Each export emits a `manifest.json` with the filter, row count, score distribution, and the cathedral-signed hash of every row, so a downstream trainer can verify provenance.

### 8. Replay (`cathedral.v3.replay`)

Given a trajectory id, the replay engine reconstructs the exact `ToolBus` state it would have seen, lets you swap in a different miner, and shows where the new agent diverges from the original. This is the debugger. It's also how we'll do A/B evaluations of new miner candidates against historical jobs.

### 9. Weight setting (`cathedral.v3.scoring.weights`)

Per-miner score = EMA over their recent trajectories, normalized across miners.

**Implemented today (Phase 0):** `compute_weights(archive)` returns a `Weights` record (hotkey → normalized weight in `[0, 1]`). Local-only — `Weights.on_chain` is always `False`.

**Stubbed but unverified end-to-end:** `WeightLoop._push_to_chain` builds the `bittensor.Subtensor` call when `CATHEDRAL_V3_CHAIN_ENABLED=1` is set. The code path has not been exercised against a live netuid in this branch; treat it as wired-but-untested. Default is off; without the env var weights stay in memory and the loop is testable without a wallet.

### 10. CLI (`cathedral.v3.cli`)

`cathedral-v3` exposes the whole system. The full set of subcommands is documented in `docs/v3/README.md`; the runnable form on this branch is `python -m cathedral.v3.cli <subcommand>`.

## Wire types

All wire records are pydantic v3 models defined in `cathedral.v3.types`. The contract is: any record that crosses an in-process boundary must be a pydantic model. Anything written to disk must be deterministically serializable (sorted keys, ISO timestamps, no NaN). This is what makes signatures replay-stable.

Key models:

- `JobSpec` — what the validator asks for
- `ToolCall` — one observed action
- `AgentResult` — what the miner returns
- `Trajectory` — the joined record (job + tool trace + result + score + receipt)
- `Receipt` — the signed projection
- `ScoreParts` — per-dimension scores + failure class + readiness flag
- `Weights` — per-miner weights at a snapshot time

## Separation of concerns

The boundaries the rewrite enforces, vs. v1 which blended them:

| Boundary | v1 reality | v3 design |
|---|---|---|
| job generation vs scoring | scorer reads task templates from the same registry the runtime uses; coupling through globals | `JobSpec` is the only shared shape; rubric ships *with* the job |
| miner execution vs observation | validator SSHs into miner box and runs Hermes itself; miner is mostly a target | miner is a real participant with its own loop; validator only owns the ToolBus |
| receipt vs storage | scorer emits an EvalRun, publisher signs it, archive is the same DB | scorer → receipt signer → archive, three distinct steps; receipt schema is versioned separately |
| trajectory capture vs export | no first-class trajectory; cards are the only artifact | trajectory is the primitive; exports are derived projections |
| weight setting vs scoring | weight loop reads the same scores table the API serves | scores feed an EMA; the weight loop only sees `Weights` snapshots |

## What v1 keeps, what v3 replaces

**Keeps**: BLAKE3 content hashing, ed25519 signing posture, SQLite as the local store, asyncio loops, the publisher/validator/miner naming. The CONTRACTS.md vocabulary (jobs, cards, runtimes) stays valid; v3 promotes "card" from "the answer" to "the final output field of a trajectory".

**Replaces**: card-shaped scoring (six dimensions designed for regulatory writing) is now one of several rubrics; the Polaris-deploy and SSH-probe pathways become two implementations of the `MinerAgent` protocol; the publisher's encrypted-bundle store becomes the trajectory archive; the weight loop reads from the archive, not from `scores`.

## Why this matters for distillation

A trajectory is structurally what a fine-tuning row needs:

```
input  = job.prompt + job.context + tool_descriptions
output = serialized(tool_call_1, tool_result_1, …, tool_call_N, final_answer)
weight = score × task_type_weight × novelty
```

After 100k trajectories with `readiness=gold` across 5 task types, we have a domain-balanced, score-graded, tool-grounded corpus that can SFT a base model into a competent Cathedral agent. After 100k preference pairs we have DPO data. After 100k negatives we have RM training data. None of this requires changing the subnet — it falls out of the labour itself.

The archive is the moat. The chain is the timestamp.
