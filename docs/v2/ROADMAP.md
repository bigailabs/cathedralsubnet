# Cathedral v2 — Roadmap to distillation

The v2 rewrite shipped on `experimental/cathedral-v2-agentic-workforce` is the seed. The roadmap below is how the seed becomes a model.

## Phase 0 — what shipped (this branch)

- Five task types: research, code_patch, tool_route, multi_step, classify
- Three reference miners: echo, heuristic, llm
- Validator with tool-bus observation
- Per-task-type rubrics, score in `[0, 1]`, failure classification
- ed25519-signed receipts committing to a BLAKE3 bundle hash
- SQLite archive with per-miner / per-task / failure-cluster / best-of / preference-pair queries
- SFT / DPO / RM export with signed manifests
- Replay engine (single-miner)
- CLI: `run`, `submit-job`, `inspect`, `archive`, `export`, `replay`, `seed-jobs`, `serve`, `weights`
- Local-mode weight loop (in-memory metagraph); chain mode gated behind env

Definition of done for phase 0: `cathedral-v2 serve --ticks 3 --miners echo,heuristic` runs end-to-end, produces ≥15 signed trajectories, and `cathedral-v2 export sft` emits a non-empty JSONL. Verified in `tests/v2/test_e2e.py`.

## Phase 1 — fill the archive (weeks 1-4)

Goal: 10k trajectories per task type across ≥10 miner archetypes.

- Bring the v1 Polaris-deploy and SSH-probe runners online as `PolarisRunnerMiner` and `SSHProbeMiner` (implementing `MinerAgent`). This unlocks real third-party miners feeding v2.
- Add `multi_file_code_patch` and `bash_tool` task types. The bash tool runs in a Docker-scoped sandbox.
- Add a `replay-batch` command that re-runs a historical job set against a candidate miner and produces a diff report.
- Add adversarial validators (a small set of miners that try to game each rubric) and patch the rubrics they break.

Exit: the archive holds ≥50k trajectories and the failure-cluster surface shows a long-tail (not just 3 clusters per task type). Preference-pair coverage ≥30% of jobs.

## Phase 2 — feed a trainer (weeks 4-10)

Goal: produce the first Cathedral-distilled model.

- Build a packer service that converts `sft.jsonl` exports into the trainer's preferred format (chat templates, tokenizer-specific). Lives in a sibling repo, consumes the manifest hashes from this archive.
- Run SFT on a 7B base (Llama-3, Qwen2.5, or whatever the team picks) targeting the joined task-type distribution. Target: beat the heuristic miner on every task type with the same prompt; tie or beat the LLM miner on at least research + classify.
- Run DPO on the preference pairs. Target: lift the SFT model another 5-10% on multi_step.
- Train a reward model on the RM exports. Target: ranks held-out trajectories with Spearman ≥ 0.7 vs. the live scorer.

Exit: a Cathedral-distilled model is live as a `MinerAgent` and earns weights on the subnet using only data the subnet itself produced. The flywheel closes.

## Phase 3 — generalize the labour (months 3-6)

Goal: become the data substrate other teams build on.

- Open the job-generation surface: external parties submit jobs (under a Cathedral-signed `JobSpec`) and pay TAO to have the workforce execute them.
- Open the archive read API with row-level signed receipts so external trainers can verify the data they consume.
- Add task types driven by real demand: SQL-from-NL, frontend-from-spec, customer-support-trajectory, judge-of-judges (RM training data).
- Add inter-validator agreement scoring — multiple validators score the same trajectory, disagreement becomes a signal both for the score and for retraining the rubric.

Exit: external buyers pay for trajectory exports. The subnet revenue is decoupled from raw TAO emissions.

## What this is not

- Not a one-shot rebuild of all v1 surfaces. The publisher / merkle anchor / Hippius storage paths from v1 stay in place for the regulatory vertical; v2 is the substrate everything else flows into.
- Not a hosted product. The CLI is the interface. A dashboard ships when there is enough data to make one useful.
- Not a competitor to SWE-bench / HELM / BIG-bench. Those are eval suites. v2 is the **labour-and-data pipeline** that feeds the next eval suite.

## Open questions explicitly deferred

1. How to handle adversarial trajectory injection (a miner who submits another miner's trajectory as their own). Mitigation today: receipt commits to `miner_hotkey`; full mitigation requires runtime attestation, which is the v1 Polaris-deploy story.
2. How to price job submission so external job demand doesn't starve subnet-native labour.
3. How to license the resulting models / datasets. Open weights vs. commercial license vs. tiered access.

These are answered in phase 3 conversations, not in code.
