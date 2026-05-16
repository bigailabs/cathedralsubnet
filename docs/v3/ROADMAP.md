# Cathedral v3: Roadmap to distillation

The v3 spike on `experimental/cathedral-v3-launch` (originating from the earlier `experimental/cathedral-v2-agentic-workforce` branch) is the seed. The roadmap below is how the seed becomes a model.

## Phase 0: what shipped (this branch)

- Five generic task types: research, code_patch, tool_route, multi_step, classify
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

## Phase 1: coding-job substrate (weeks 1-4)

Goal: add the missing infra that coding-job families (bug_repro, test_gen) need before they can be public tasks. Sequence is load-bearing; do not skip ahead.

**Phase 1 status: alpha-shipped on this branch.** Items 1-6 below are implemented and tested. Calibration, gaming-detection, and reward-weight tuning still in progress before public exposure.

1. **Sandbox runner (alpha / dev-harness only).** [shipped] Docker-based (`DockerBackend`) with `--network=none`, read-only root, tmpfs work dir, env allowlist, CPU/RAM/wallclock limits, no host file mounts, no Linux capabilities. `SubprocessBackend` ships as degraded fallback for CI; `available_backend()` prefers Docker when the daemon responds. **This is the substrate development path, not the production execution path.** Production `bug_repro` execution belongs in the Cathedral evaluator service (Phase 1.5); validators should not run arbitrary miner-submitted test code locally on mainnet.
2. **Repo bundle builder.** [shipped] `src/cathedral/v3/bundle/builder.py`: signed manifest, per-file BLAKE3, aggregate BLAKE3, ed25519 signature, materialize with path-escape refusal, full tamper-evidence test coverage.
3. **Coding-trajectory schema fields.** [shipped] `TaskSplit` enum (`train_exportable`, `public_leaderboard`, `heldout_eval`, `operator_review`) on `JobSpec`. `JobSpec.hidden_context` separated from `JobSpec.context`; `public_view()` excludes hidden context. Export firewall (`prompt_visible_to_miner()`, `_collect_hidden_strings()`) scrubs hidden context, oracle outputs, and held-out splits from SFT/DPO/RM JSONL.
4. **Code-specific failure-reason enum.** [shipped] `CodingFailureClass`: `sandbox_violation`, `no_bug_repro`, `fixed_commit_fails`, `flake` (plus generic `FailureClass` for non-code task types).
5. **`bug_repro` task type, alpha.** [shipped] `TaskType.BUG_REPRO` with 3 curated fixtures (`off_by_one_sum`, `wrong_default_arg`, `divide_by_zero_guard`). Oracle: `fails_on_buggy`, `passes_on_fixed`, `symptom_match`. Defaults to `TaskSplit.OPERATOR_REVIEW`. Sandbox gate refuses positive score unless `DockerBackend` is available (or `CATHEDRAL_V3_BUG_REPRO_ALLOW_SUBPROCESS=1` for trusted-fixture smoke testing, which still tags readiness as `negative`).
6. **SFT / DPO / RM exports for `bug_repro`.** [shipped] Hidden-field firewall scrubs `fixed_source`, `expected_symptom`, `reference_test_source`, oracle result values (`fails_on_buggy`, `passes_on_fixed`, `symptom_match`, `sandbox_backend`) from all three exports. DPO export refuses unsafe `bug_repro` pairs (subprocess or trusted-fixture mode). `HELDOUT_EVAL` is unconditionally non-exportable.

Exit: scaled to ≥10 curated `bug_repro` jobs (currently 3), operator-review queue surfaces gaming attempts on real submissions, and the exports produce defensible SFT/DPO/RM rows under real miner traffic.

## Phase 1.5: Cathedral evaluator service (gate to rewardable bug_repro)

Goal: move `bug_repro` execution off validators and onto a Cathedral-controlled evaluator service so the production trust flow matches v1 (validators verify signed evaluator output; they do not run arbitrary miner code locally). This phase is **load-bearing**: until it ships, `bug_repro` stays `OPERATOR_REVIEW` by default and carries no rewardable weight.

1. **Evaluator service.** Separate process and image with hardened isolation (network egress denied, read-only root, ephemeral work dirs, CPU/RAM/wallclock limits). Owns the hidden buggy/fixed sources and the symptom oracle. Receives candidate tests from the validator over an authenticated channel; executes them; returns `fails_on_buggy`, `passes_on_fixed`, `symptom_match`, readiness, and failure class.
2. **Evaluator signing key + receipt.** ed25519 key (same posture as v1 Cathedral signatures). Result is signed; canonical payload includes job id, candidate bundle BLAKE3, oracle outputs, readiness, failure class, evaluator image digest, wall-clock.
3. **Validator-side verifier.** Validator no longer runs `cathedral.v3.sandbox` for production `bug_repro`. It signs and forwards the candidate to the evaluator, verifies the signed result, and feeds it into the archive and the weight loop. Local sandbox stays available behind a dev-only flag.
4. **Operator runbook + key rotation.** How to provision the evaluator, rotate its key, publish the trust set to validators, and roll a new image digest.
5. **Tracking.** Filed as #123; this PR ships the substrate that the validator-side verifier will consume.

Exit: rewardable `bug_repro` runs end-to-end on testnet using a Cathedral-hosted evaluator with no validator-local execution of miner code.

## Phase 2: `test_gen` and scale (weeks 4-10)

Goal: extend to the harder coding family and start scaling. test_gen comes AFTER bug_repro calibration; mutation infra comes BEFORE test_gen as a public task.

1. **Mutation harness.** Module-level mutation operators, mutant kill rate computation, kill-rate floors, coverage delta + branch delta tracking, anti-gaming detectors (target-symbol reachability, monkeypatch abuse, snapshot caps, importing-existing-tests block).
2. **`test_gen` task type, alpha.** 10 curated modules. Operator-reviewed; low or no reward weight. Audit high-scoring weird tests manually.
3. **Scale to 50 issues / 50 modules** only after the alpha exposes the failure modes and the anti-gaming surface holds.
4. **Bring v1 Polaris-deploy and SSH-probe runners online as MinerAgent implementations**, unlocking real third-party miners feeding v3.
5. **Replay-batch command** that re-runs a historical job set against a candidate miner and produces a diff report.

Exit: the archive holds ≥50k coding trajectories across `bug_repro` and `test_gen`, the failure-cluster surface shows a long tail (not just 3 clusters per task type), and preference-pair coverage ≥30% of jobs.

## Phase 3: feed a trainer (months 3-5)

Goal: produce the first Cathedral-distilled coding agent.

- Build a packer service that converts `sft.jsonl` exports into the trainer's preferred format (chat templates, tokenizer-specific). Lives in a sibling repo, consumes the manifest hashes from this archive.
- Run SFT on a 7B base targeting `bug_repro` + `test_gen` first; multi_step + research as secondary objectives.
- Run DPO on the preference pairs.
- Train a reward model on the RM exports.

Exit: a Cathedral-distilled coding agent is live as a `MinerAgent` and earns weights on the subnet using only data the subnet itself produced. The flywheel closes.

## Phase 4: generalize the labour (months 5-9)

Goal: become the data substrate other teams build on.

- Open the job-generation surface: external parties submit jobs (under a Cathedral-signed `JobSpec`) and pay TAO to have the workforce execute them.
- Open the archive read API with row-level signed receipts so external trainers can verify the data they consume.
- Add task types driven by real demand: SQL-from-NL, frontend-from-spec, customer-support-trajectory, judge-of-judges (RM training data).
- Add inter-validator agreement scoring: multiple validators score the same trajectory, disagreement becomes a signal both for the score and for retraining the rubric.

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
