# Cathedral v3 — Roadmap to distillation

The v3 spike on `experimental/cathedral-v3-launch` (originating from the earlier `experimental/cathedral-v2-agentic-workforce` branch) is the seed. The roadmap below is how the seed becomes a model.

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

Definition of done for phase 0: `cathedral-v3 serve --ticks 3 --miners echo,heuristic` runs end-to-end, produces ≥15 signed trajectories, and `cathedral-v3 export sft` emits a non-empty JSONL. Verified in `tests/v3/test_e2e.py`.

## Phase 1 — coding-job substrate (weeks 1-4)

Goal: add the missing infra that coding-job families (bug_repro, test_gen) need before they can be public tasks. Sequence is load-bearing — do not skip ahead.

1. **Sandbox runner.** Docker-based, `--network=none`, read-only root, env allowlist, resource limits (CPU/RAM/wallclock), throwaway work dirs, no host file mounts. Ships as standalone infra usable by any task type, not just code.
2. **Repo bundle builder.** Reproducible signed snapshots of a target repo at a given commit, with manifest (file list, blake3 hashes, signature). Miners receive the bundle; validators recreate the same state.
3. **Coding-trajectory schema fields.** Add code-specific fields to the trajectory before adding public coding tasks: `prompt_visible_to_miner`, `tool_trace`, `final_artifact`, `verifier_metrics`, `score`, `failure_reason` (using the code-specific enum below), `provenance`, plus a `task_split` tag (`train_exportable | public_leaderboard | heldout_eval | operator_review`). Hidden verifier fields MUST NOT leak into training prompts or future eval sets.
4. **Code-specific failure-reason enum.** Replace generic enums for code tasks: `collection_failed`, `sandbox_violation`, `flake`, `no_bug_repro`, `fixed_commit_fails`, `missing_repo_symbol`, `coverage_gaming`, `mutation_threshold_miss`.
5. **`bug_repro` task type, alpha.** Start with 10 curated fixed-commit tasks. Oracle: fail on buggy commit, pass on fixed commit, symptom match. Operator-reviewed alpha with low or no reward weight while we calibrate.
6. **SFT / DPO / RM exports for `bug_repro`.** Same export pipeline, with explicit dataset views over the new schema fields so hidden verifier fields stay out of training prompts.

Exit: 10 curated `bug_repro` jobs run end-to-end through the sandbox, the operator-review queue catches gaming attempts, and the exports produce defensible SFT/DPO/RM rows.

## Phase 2 — `test_gen` and scale (weeks 4-10)

Goal: extend to the harder coding family and start scaling. test_gen comes AFTER bug_repro calibration; mutation infra comes BEFORE test_gen as a public task.

1. **Mutation harness.** Module-level mutation operators, mutant kill rate computation, kill-rate floors, coverage delta + branch delta tracking, anti-gaming detectors (target-symbol reachability, monkeypatch abuse, snapshot caps, importing-existing-tests block).
2. **`test_gen` task type, alpha.** 10 curated modules. Operator-reviewed; low or no reward weight. Audit high-scoring weird tests manually.
3. **Scale to 50 issues / 50 modules** only after the alpha exposes the failure modes and the anti-gaming surface holds.
4. **Bring v1 Polaris-deploy and SSH-probe runners online as MinerAgent implementations**, unlocking real third-party miners feeding v3.
5. **Replay-batch command** that re-runs a historical job set against a candidate miner and produces a diff report.

Exit: the archive holds ≥50k coding trajectories across `bug_repro` and `test_gen`, the failure-cluster surface shows a long tail (not just 3 clusters per task type), and preference-pair coverage ≥30% of jobs.

## Phase 3 — feed a trainer (months 3-5)

Goal: produce the first Cathedral-distilled coding agent.

- Build a packer service that converts `sft.jsonl` exports into the trainer's preferred format (chat templates, tokenizer-specific). Lives in a sibling repo, consumes the manifest hashes from this archive.
- Run SFT on a 7B base targeting `bug_repro` + `test_gen` first; multi_step + research as secondary objectives.
- Run DPO on the preference pairs.
- Train a reward model on the RM exports.

Exit: a Cathedral-distilled coding agent is live as a `MinerAgent` and earns weights on the subnet using only data the subnet itself produced. The flywheel closes.

## Phase 4 — generalize the labour (months 5-9)

Goal: become the data substrate other teams build on.

- Open the job-generation surface: external parties submit jobs (under a Cathedral-signed `JobSpec`) and pay TAO to have the workforce execute them.
- Open the archive read API with row-level signed receipts so external trainers can verify the data they consume.
- Add task types driven by real demand: SQL-from-NL, frontend-from-spec, customer-support-trajectory, judge-of-judges (RM training data).
- Add inter-validator agreement scoring — multiple validators score the same trajectory, disagreement becomes a signal both for the score and for retraining the rubric.

Exit: external buyers pay for trajectory exports. The subnet revenue is decoupled from raw TAO emissions.

## What this is not

- Not a one-shot rebuild of all v1 surfaces. The publisher / merkle anchor / Hippius storage paths from v1 stay in place for the regulatory vertical; v3 is the substrate everything else flows into.
- Not a hosted product. The CLI is the interface. A dashboard ships when there is enough data to make one useful.
- Not a competitor to SWE-bench / HELM / BIG-bench. Those are eval suites. v3 is the **labour-and-data pipeline** that feeds the next eval suite.

## Open questions explicitly deferred

1. How to handle adversarial trajectory injection (a miner who submits another miner's trajectory as their own). Mitigation today: receipt commits to `miner_hotkey`; full mitigation requires runtime attestation, which is the v1 Polaris-deploy story.
2. How to price job submission so external job demand doesn't starve subnet-native labour.
3. How to license the resulting models / datasets. Open weights vs. commercial license vs. tiered access.

These are answered in phase 3 conversations, not in code.
