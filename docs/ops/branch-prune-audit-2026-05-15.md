# Cathedral Branch Prune Audit, 2026-05-15

Companion to `BRANCH_CLEANUP_2026-05-15.md`. Adds per-branch evidence so a human can sign off on a local prune. **No deletions performed in this pass.**

Audit method:
- For §5a and §5b1, ancestor relationship verified by `git branch --merged main` and `git log <branch> --not main --oneline`.
- For §5b2, ran `git log <branch> --not main --oneline` to count branch-unique commits, then searched `git log main --oneline --grep="<subject prefix>"` to find the squash landing on main.
- For §5c, ran `gh pr list --state all --search "head:<branch>"` to find merged or closed PR.

`main` HEAD at audit time: `de95e7c`.

---

## A. Local prune, deletion-ready

All 45 entries below are verified safe to delete locally (11 in §A1, 34 in §A2). Two subgroups, evidence type differs.

### A1. §5a, strict ancestors of main (11 branches)

`git log <branch> --not main --oneline` is empty for every entry, so the branch is fully contained in main.

| Local branch | Tip | Unique vs main | Evidence | Caveat |
|---|---|---|---|---|
| `docs/post-v1-1-0-reconcile` | `a283b9f` | 0 | Strict ancestor of main | Pinned by `cathedralsubnet-miner` worktree, detach first |
| `docs/v2-launch-and-validator-quickstart` | `14ba592` | 0 | Strict ancestor of main | None |
| `feature/discovery-surface` | `35bfe66` | 0 | Strict ancestor of main | None |
| `feature/publisher-validator-split` | `565052c` | 0 | Strict ancestor of main | None |
| `feature/v1-1-0-validator-compat` | `2ccdd13` | 0 | Strict ancestor of main; also merged on origin (PR #76) | Has live `origin/feature/v1-1-0-validator-compat`; local delete only |
| `fix/persistence-floor-phase-1` | `7ea4e10` | 0 | Strict ancestor of main | None |
| `worktree-agent-a22944e0` | `c7d9cce` | 0 | Strict ancestor of main, stale agent worktree | None |
| `worktree-agent-a688a642` | `c7d9cce` | 0 | Same | None |
| `worktree-agent-a8db8fdd` | `c7d9cce` | 0 | Same | None |
| `worktree-agent-a94ee584` | `c7d9cce` | 0 | Same | None |
| `worktree-agent-afd6f2b5` | `c7d9cce` | 0 | Same | None |

### A2. §5b2, local-only with squash-merged landing on main (34 branches)

Each branch has non-zero commits when compared to main (squash-merge produces a different SHA), but the subject line matches a squash commit already in main. Action: drop the local copy; the content is on main.

| Local branch | Tip | Squash on main | Evidence (`gh pr` not needed, message match) |
|---|---|---|---|
| `chore/enable-signed-commits` | `dc0fffe` | `8e08d79` | `chore: enable SSH-signed commits (#59)` |
| `chore/local-validator-testnet` | `a0213ed` | `e39c2cb` | `Fix dry-run validator weight logging (#108)` |
| `codex-review-pr117` | `dd6b55e` | `085ba58` | `fix(orchestrator,reads): preserve ranked on cadence refresh (#117)` |
| `codex/validator-auto-mainnet-config` | `f0dd4aa` | `d123755` | `fix(validator): migrate managed hosts to mainnet config` (commit landed by SHA) |
| `docs/miner-quickstart-v2` | `624801b` | `c54784c` | `docs: miner QUICKSTART for v2 (#57)` |
| `docs/v1-release-notes` | `37cac17` | `d051d55` | `docs: v1.0.7 release notes + README one-liner (#56)` |
| `docs/validator-current-truth-cleanup` | `e79750d` | `ac7c226` | `docs(validator): align current v1 validator docs` (subject reworded; same 11 files, branch is behind main) |
| `docs/validator-networking-faq` | `6810aff` | `256d2b9` | `docs(validator): networking section + FAQ (#102)` |
| `docs/validator-quickstart-clarify` | `18b3788` | `c7a0b90` | `docs(validator): hardware specs + quickstart (#101)` |
| `feat/publish-cathedral-jwks` | `bc33a00` | `723aa6c` | `feat(publisher): publish cathedral-jwks.json (#58)` |
| `feature/inline-card-payload` | `9804be0` | `565052c` | `feat(cards): inline payload + storage + read API (#7)` |
| `feature/polaris-contract-golden-vectors` | `dcd59e4` | `3fe5c11` | `test(polaris-contract): golden vectors (#5)` |
| `feature/polaris-native-eval` | `05222ff` | `c161b68` | `feat(eval): v2 Polaris-native Hermes runner (#49)` |
| `feature/security-tooling` | `251dcce` | `89a38e8` | `feature: enable security tooling (#60)` |
| `feature/v1-launch` | `c39de29` | `c0c4f5c` | `feat(v1-launch): publisher + validator + eval + storage + Merkle anchor (#9)` |
| `fix/configurable-bundle-prefix` | `355c4f0` | `c341f95` | `fix(storage): env-overridable bucket + key prefix (#11)` |
| `fix/drop-max-turns` | `199ffa4` | `f6927c4` | `fix(prober): drop --max-turns (#84)` |
| `fix/exclude-none-canonicalization` | `32d8658` | `d289240` | `fix(verify): exclude_none in canonicalization (#8)` |
| `fix/hermes-chat-quiet` | `97c4946` | `3185b74` | `fix(prober): add -Q flag (#87)` |
| `fix/hermes-q-order` | `63cc59d` | `c447944` | `fix(prober): swap -Q -q order (#88)` |
| `fix/launch-prefix-health-loader` | `0600f15` | `8c4a792` | `fix(launch): /api/cathedral prefix + relaxed health (#13)` |
| `fix/leaderboard-dedupe-by-hotkey` | `badd532` | `61b158c` | `fix(publisher): dedupe leaderboard by hotkey (#100)` |
| `fix/leaderboard-include-ssh-probe` | `574d48f` | `8fb102e` | `fix(reads): include ssh-probe + polaris-deploy + bundle (#94)` |
| `fix/publisher-cors-for-cathedral-site` | `a86a5e3` | `17ca9b7` | `fix(publisher): CORS for cathedral.computer (#96)` |
| `fix/pull-loop-7d-backfill` | `2f570f8` | `c9b59c7` | `fix(pull_loop): backfill cursor to 7 days (#109)` |
| `fix/pull-loop-backfill-marker-only-on-success` | `dab467c` | `f3cf146` | `fix(pull_loop): marker only after drained tick (#110)` |
| `fix/source-pool-in-prompt` | `6c1fe47` | `70ab298` | `fix(prober): include source_pool URLs (#95)` |
| `fix/task-prompt-json-instruction` | `9870f62` | `731a035` | `fix(prober): JSON-only output contract (#93)` |
| `fix/weight-interval-mainnet-1500` | `b7db826` | `043f44a` | `fix(weights): mainnet interval 1500s (#106)` |
| `fix/weight-interval-rate-limit` | `c11a75d` | `79e0cd0` | `fix(weights): 7-day window + observability (#105)` |
| `infra/railway-deploy` | `405446c` | `6facc33` | `infra: Dockerfile + railway.toml (#10)` |
| `infra/verify-railway-auto-deploy` | `82a0741` | `cab1ed7` | `docs: publisher auto-deploys (#16)` |
| `pr-49` | `05222ff` | `c161b68` | Alias of `feature/polaris-native-eval`, same squash (#49) |
| `review/pr-109-backfill` | `07efcec` | `c9b59c7` | Review checkout of #109 (already on main) |

### Suggested local-prune commands (do NOT run unblocked)

```bash
cd ~/Documents/projects/cathedralsubnet
# A1, ancestors:
git branch -d \
  docs/v2-launch-and-validator-quickstart \
  feature/discovery-surface \
  feature/publisher-validator-split \
  feature/v1-1-0-validator-compat \
  fix/persistence-floor-phase-1 \
  worktree-agent-a22944e0 \
  worktree-agent-a688a642 \
  worktree-agent-a8db8fdd \
  worktree-agent-a94ee584 \
  worktree-agent-afd6f2b5
# docs/post-v1-1-0-reconcile is pinned by cathedralsubnet-miner worktree; detach first:
#   git worktree remove ~/Documents/projects/cathedralsubnet-miner
#   git branch -d docs/post-v1-1-0-reconcile

# A2, squash-merged (use -D because git won't see them as merged):
git branch -D \
  chore/enable-signed-commits chore/local-validator-testnet codex-review-pr117 \
  codex/validator-auto-mainnet-config docs/miner-quickstart-v2 docs/v1-release-notes \
  docs/validator-current-truth-cleanup docs/validator-networking-faq docs/validator-quickstart-clarify \
  feat/publish-cathedral-jwks feature/inline-card-payload feature/polaris-contract-golden-vectors \
  feature/polaris-native-eval feature/security-tooling feature/v1-launch \
  fix/configurable-bundle-prefix fix/drop-max-turns fix/exclude-none-canonicalization \
  fix/hermes-chat-quiet fix/hermes-q-order fix/launch-prefix-health-loader \
  fix/leaderboard-dedupe-by-hotkey fix/leaderboard-include-ssh-probe fix/publisher-cors-for-cathedral-site \
  fix/pull-loop-7d-backfill fix/pull-loop-backfill-marker-only-on-success \
  fix/source-pool-in-prompt fix/task-prompt-json-instruction fix/weight-interval-mainnet-1500 \
  fix/weight-interval-rate-limit infra/railway-deploy infra/verify-railway-auto-deploy \
  pr-49 review/pr-109-backfill
```

---

## B. Remote prune review (§5c), DO NOT DELETE YET

Each origin branch matched to its PR via `gh pr list --search "head:<branch>"`. Default recommendation is delete-yes for MERGED PRs whose merge commit is in `origin/main`, delete-yes for CLOSED-not-merged where the work is explicitly superseded, and delete-no otherwise.

| Origin branch | Merged PR | Last tip | Reason | Delete |
|---|---|---|---|---|
| `chore/org-rename-cathedralai` | #27 MERGED | `68a5ce1` | bigailabs to cathedralai org rename, shipped | yes |
| `chore/repo-rename-sweep` | #26 MERGED | `59805ed` | cathedralsubnet to cathedral rename, shipped | yes |
| `chore/runtime-v1.0.7-cathedralai` | #28 MERGED | `2873858` | Runtime republish under cathedralai ghcr, shipped | yes |
| `docs/v1-runbook-pm2` | #37 MERGED | `848a8e0` | Validator RUNBOOK PM2 rewrite, shipped | yes |
| `experimental/cathedral-v2-launch` | #111 CLOSED | `933e783` | v2 naming was misleading, superseded by v3 (#112) | yes |
| `feature/v1-1-0-validator-compat` | #76 MERGED | `2ccdd13` | v1.1.0 validator prep, shipped | yes |
| `feature/v1-auto-updater` | #34 MERGED | `493f420` | v1 auto-updater, shipped | yes |
| `feature/v1-pm2-ecosystem` | #33 MERGED | `7c6e618` | PM2 ecosystem config, shipped | yes |
| `feature/v1-probe-mode` | #38 MERGED | `72748c5` | Probe-mode endpoints + signing, shipped | yes |
| `feature/v1-probe-runner` | #39 MERGED | `99add66` | Publisher-side ProbeRunner, shipped | yes |
| `feature/v1-provision-miner` | #35 MERGED | `72d1c9e` | Miner-probe provisioner, shipped | yes |
| `feature/v1-provision-validator` | #36 MERGED | `abeb057` | Validator provisioner, shipped | yes |
| `fix/bt-config-argv-collision` | #45 MERGED | `3ed8448` | bittensor argparse fix, shipped | yes |
| `fix/cadence-state-leak` | #117 MERGED | `dbd9b9f` | Orchestrator/reads cadence fix, shipped as `085ba58` | yes |
| `fix/default-attestation-mode-bundle` | #53 MERGED | `e318778` | Default attestation_mode=bundle, shipped | yes |
| `fix/miner-probe-port-mapping` | #42 MERGED | `0f056c0` | Probe container port mapping, shipped | yes |
| `fix/miner-provisioner-docker-detection` | #41 MERGED | `fbe08a5` | Miner provisioner bug fixes, shipped | yes |
| `fix/provisioner-python-version` | #40 MERGED | `34f1103` | Provisioner Python version fix, shipped | yes |
| `fix/pull-loop-include-polaris-verified` | #43 MERGED | `15ac5a2` | Include polaris_verified in canonical payload, shipped | yes |
| `fix/pull-loop-strip-unsigned-fields` | #44 MERGED | `86726f8` | Strip non-signed fields before verify, shipped | yes |
| `fix/testnet-burn-uid-to-owner` | #46 MERGED | `83bf183` | Testnet burn uid fix, shipped | yes |
| `docs/v1-job-vocab-and-sweep` | #47 MERGED | `1b6f094` | README reframe to verifiable AI workforce, shipped | yes |
| `experimental/cathedral-v2-agentic-workforce` | #104 CLOSED | `34b5885` | v2 experimental, explicitly superseded by v3 (#112). Supersede comment exists on #104 | yes |
| `docs/readme-release-pointer` | #121 MERGED | `089355f` | README latest-release pointer, shipped as `de95e7c`. Local worktree still pins this branch, detach first | yes (after worktree detach) |

### Suggested remote-prune commands (do NOT run unblocked)

```bash
cd ~/Documents/projects/cathedralsubnet
git push origin --delete \
  chore/org-rename-cathedralai chore/repo-rename-sweep chore/runtime-v1.0.7-cathedralai \
  docs/v1-runbook-pm2 docs/v1-job-vocab-and-sweep \
  experimental/cathedral-v2-launch experimental/cathedral-v2-agentic-workforce \
  feature/v1-1-0-validator-compat feature/v1-auto-updater feature/v1-pm2-ecosystem \
  feature/v1-probe-mode feature/v1-probe-runner feature/v1-provision-miner feature/v1-provision-validator \
  fix/bt-config-argv-collision fix/cadence-state-leak fix/default-attestation-mode-bundle \
  fix/miner-probe-port-mapping fix/miner-provisioner-docker-detection fix/provisioner-python-version \
  fix/pull-loop-include-polaris-verified fix/pull-loop-strip-unsigned-fields fix/testnet-burn-uid-to-owner
# docs/readme-release-pointer is pinned by cathedralsubnet-validator-testnet worktree:
#   git worktree remove ~/Documents/projects/cathedralsubnet-validator-testnet
#   git push origin --delete docs/readme-release-pointer
```

Dependabot PR branches (#63-#68) are intentionally excluded; they back open PRs.

---

## C. Cleanup report placement

`BRANCH_CLEANUP_2026-05-15.md` currently sits at repo root and shows up as untracked. Three options:

1. **Commit to `docs/ops/branch-cleanup-2026-05-15.md`** (recommended). Repo already has a `docs/` tree (`ARCHITECTURE.md`, `ATTESTATION_CONTRACT.md`, `runbooks/`, `validator/`). Adding `docs/ops/` keeps ops history reviewable in-repo, and future cleanup reports go under the same folder. Same applies to this audit (`BRANCH_PRUNE_AUDIT_2026-05-15.md` would move to `docs/ops/`).
2. Move to `~/Documents/INBOX/`. Keeps repo root clean but separates the audit from the repo, which makes future cross-reference harder.
3. Leave at repo root and `.gitignore` both files. Quick, but adds permanent noise to `git status`.

Recommendation: option 1. Sequence:

```bash
cd ~/Documents/projects/cathedralsubnet
mkdir -p docs/ops
git mv BRANCH_CLEANUP_2026-05-15.md docs/ops/branch-cleanup-2026-05-15.md
# (this audit file too, if you keep it)
git mv BRANCH_PRUNE_AUDIT_2026-05-15.md docs/ops/branch-prune-audit-2026-05-15.md
git checkout -b docs/ops-branch-cleanup-report
git add docs/ops/
git commit -m "docs(ops): land branch cleanup + prune audit (2026-05-15)"
gh pr create --title "docs(ops): branch cleanup + prune audit (2026-05-15)" --body "Lands the inventory and per-branch evidence so future prune passes have a written baseline."
```

If you prefer to keep this strictly local and not introduce a docs PR, option 2 (move to `~/Documents/INBOX/`) is fine.

---

## Summary

- 45 local branches deletion-ready once §A is signed off: 11 ancestors (§A1) plus 34 squash-merged (§A2). 44 immediately deletable; `docs/post-v1-1-0-reconcile` requires detaching the `cathedralsubnet-miner` worktree first.
- 24 remote branches deletion-ready once §B is signed off: 23 immediately, 1 (`docs/readme-release-pointer`) after `cathedralsubnet-validator-testnet` worktree detach.
- 0 deletions performed in this pass.
- Open question: choose §C placement before next pass.
