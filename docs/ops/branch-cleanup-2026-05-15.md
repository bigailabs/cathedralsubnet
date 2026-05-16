> **Deletion-ready only after §5b review sign-off.**

# Cathedral Branch Cleanup Report, 2026-05-15

Generated 2026-05-15 (UTC date 2026-05-16 at write time). Repo: `github.com/cathedralai/cathedral`.
Origin main tip: `de95e7c docs(readme): point latest-release ... (#121)` at `v1.1.15-2-gde95e7c`.
Local primary checkout main tip: `de95e7c` (synced this pass).
v3 head: `005609d` on `experimental/cathedral-v3-launch` (origin/main now an ancestor).

**This is a report only.** No branches deleted in this pass. The five sections below classify every branch (local + origin) into a deletion-safety bucket. §5b is split strictly by `git ls-remote --heads origin <name>`: every branch is in exactly one of "has origin ref" or "local-only".

---

## 1. Protected, never delete

| Branch | Why |
|---|---|
| `main` (local + `origin/main`) | Production trunk |
| `release/v1.1.0-cathedral-bell` (local) | Historical release branch, tag anchor |

Origin has no `release/v1.1.0-cathedral-bell` ref (the tag carries it now). Local copy is fully merged but kept for parity.

---

## 2. Open-PR-backed, do not delete

These branches back currently-open PRs. Deleting them closes the PR.

| Branch | PR |
|---|---|
| `experimental/cathedral-v3-launch` (local + origin) | #112 (draft, v3 substrate) |
| `docs/hermes-bittensor-dossiers` (local + origin) | #74 |
| `dependabot/docker/docker/cathedral-runtime/python-3.14-slim` (origin) | #68 |
| `dependabot/github_actions/docker/build-push-action-7` (origin) | #67 |
| `dependabot/github_actions/actions/checkout-6` (origin) | #66 |
| `dependabot/github_actions/github/codeql-action-4` (origin) | #65 |
| `dependabot/github_actions/docker/setup-buildx-action-4` (origin) | #64 |
| `dependabot/github_actions/docker/login-action-4` (origin) | #63 |

Also:
- PR #107 head is `Crimzor3086:commit-changes`, external fork, not in our refspace.
- PR #118 head is `zp6:main`, external fork.

`docs/readme-release-pointer` (local + origin) backed #121, which merged earlier this pass as `de95e7c`. The remote auto-delete was blocked because a local worktree pins the branch at `~/Documents/projects/cathedralsubnet-validator-testnet`. Treat it as cleanup candidate once the worktree is detached (see §3 and §5).

---

## 3. Active worktree-backed, do not delete

`git worktree list` shows these checked out somewhere on disk. Deleting the branch breaks the worktree.

| Branch | Worktree path |
|---|---|
| `main` | `~/Documents/projects/cathedralsubnet` |
| `experimental/cathedral-v3-launch` | `~/Documents/PROJECTS/cathedralsubnet-v2-launch` |
| `docs/post-v1-1-0-reconcile` | `~/Documents/projects/cathedralsubnet-miner` |
| `docs/readme-release-pointer` | `~/Documents/projects/cathedralsubnet-validator-testnet` |
| `worktree-agent-a1a0b724` | `.claude/worktrees/agent-a1a0b724` (locked) |
| `feature/byo-compute-flow` | `.claude/worktrees/agent-a4f7fbc8` (locked) |
| `worktree-resilient-twirling-dream` | `.claude/worktrees/resilient-twirling-dream` |
| `worktree-v1-conform-rebase` | `.claude/worktrees/v1-conform-rebase` |

Detached-HEAD review worktrees (not pinning any branch, safe to ignore for branch cleanup):
- `cathedral-main-review` @ `f3cf146`
- `cathedral-pr113-review` @ `99a5d78`
- `cathedralsubnet-pr109-review` @ `dab467c`
- `cathedralsubnet-pr117-review` @ `dd6b55e`

---

## 4. Locked Claude worktree branches, do not delete

These are inside `.claude/worktrees/` and marked `locked` by `git worktree list`. Removing them needs `git worktree unlock` first.

| Branch | Worktree |
|---|---|
| `worktree-agent-a1a0b724` | `.claude/worktrees/agent-a1a0b724` (locked) |
| `feature/byo-compute-flow` | `.claude/worktrees/agent-a4f7fbc8` (locked) |

Unlocked but Claude-managed (review later):
- `worktree-resilient-twirling-dream` @ `846c8c2` (matches `v1.1.15` tip)
- `worktree-v1-conform-rebase` @ `f3cf146` (matches `v1.1.13` tag)

---

## 5. Proposed delete candidates (REPORT ONLY, not deleting now)

Each candidate is annotated with the reason it looks safe and any caveat. Verify before any future deletion pass.

### 5a. Local branches that are strict ancestors of `origin/main`

From `git branch --merged main`. Safe to delete locally without losing commits.

| Local branch | Tip | Origin ref? | Notes |
|---|---|---|---|
| `docs/post-v1-1-0-reconcile` | `a283b9f` | no | Pinned by `cathedralsubnet-miner` worktree, detach first |
| `docs/v2-launch-and-validator-quickstart` | `14ba592` | no | Plain merged local |
| `feature/discovery-surface` | `35bfe66` | no | Plain merged local |
| `feature/publisher-validator-split` | `565052c` | no | Plain merged local |
| `feature/v1-1-0-validator-compat` | `2ccdd13` | yes (also in §5c) | Merged both sides |
| `fix/persistence-floor-phase-1` | `7ea4e10` | no | Plain merged local |
| `worktree-agent-a22944e0` | `c7d9cce` | no | Stale agent worktree branch, no worktree pin |
| `worktree-agent-a688a642` | `c7d9cce` | no | Same |
| `worktree-agent-a8db8fdd` | `c7d9cce` | no | Same |
| `worktree-agent-a94ee584` | `c7d9cce` | no | Same |
| `worktree-agent-afd6f2b5` | `c7d9cce` | no | Same |

`release/v1.1.0-cathedral-bell` also shows up under `--merged` but is in §1 (protected), excluded here.

### 5b1. Local branches that still have an `origin/<name>` ref

These have a remote-tracking counterpart. Deleting the local branch only is safe (the origin ref stays). Deleting the origin ref is §5c (separate pass).

| Local branch | Tip | Origin ref present | Hypothesis |
|---|---|---|---|
| `docs/v1-job-vocab-and-sweep` | `1b6f094` | yes | Squash-merged, both sides remain |
| `experimental/cathedral-v2-agentic-workforce` | `34b5885` | yes | Superseded by v3; PR #104 closed |
| `fix/bt-config-argv-collision` | `3ed8448` | yes | Squash-merged |
| `fix/cadence-state-leak` | `dbd9b9f` | yes | #117 squash-merged into `085ba58` |
| `fix/default-attestation-mode-bundle` | `e318778` | yes | Squash-merged |
| `fix/miner-probe-port-mapping` | `0f056c0` | yes | Squash-merged |
| `fix/miner-provisioner-docker-detection` | `fbe08a5` | yes | Squash-merged |
| `fix/provisioner-python-version` | `34f1103` | yes | Squash-merged |
| `fix/pull-loop-include-polaris-verified` | `15ac5a2` | yes | Squash-merged |
| `fix/pull-loop-strip-unsigned-fields` | `86726f8` | yes | Squash-merged |
| `fix/testnet-burn-uid-to-owner` | `83bf183` | yes | Squash-merged |

### 5b2. Local-only branches with no `origin/<name>` ref

These exist only locally (`git ls-remote --heads origin <name>` returns empty). Likely squash-merged via PR or abandoned. Deleting the local ref removes the last copy of any unique commits, so confirm with `git log <branch> --not main --oneline` first.

| Local branch | Tip | Hypothesis |
|---|---|---|
| `chore/enable-signed-commits` | `dc0fffe` | Squash-merged |
| `chore/local-validator-testnet` | `a0213ed` | Squash-merged |
| `codex-review-pr117` | `dd6b55e` | Stale review branch, #117 now merged |
| `codex/validator-auto-mainnet-config` | `f0dd4aa` | Stale codex branch |
| `docs/miner-quickstart-v2` | `624801b` | Squash-merged |
| `docs/v1-release-notes` | `37cac17` | Squash-merged |
| `docs/validator-current-truth-cleanup` | `e79750d` | Squash-merged |
| `docs/validator-networking-faq` | `6810aff` | Squash-merged |
| `docs/validator-quickstart-clarify` | `18b3788` | Squash-merged |
| `feat/publish-cathedral-jwks` | `bc33a00` | Squash-merged |
| `feature/inline-card-payload` | `9804be0` | Squash-merged |
| `feature/polaris-contract-golden-vectors` | `dcd59e4` | Squash-merged |
| `feature/polaris-native-eval` | `05222ff` | Squash-merged (also referenced as `pr-49` below) |
| `feature/security-tooling` | `251dcce` | Squash-merged |
| `feature/v1-launch` | `c39de29` | Squash-merged |
| `fix/configurable-bundle-prefix` | `355c4f0` | Squash-merged |
| `fix/drop-max-turns` | `199ffa4` | Squash-merged |
| `fix/exclude-none-canonicalization` | `32d8658` | Squash-merged |
| `fix/hermes-chat-quiet` | `97c4946` | Squash-merged |
| `fix/hermes-q-order` | `63cc59d` | Squash-merged |
| `fix/launch-prefix-health-loader` | `0600f15` | Squash-merged |
| `fix/leaderboard-dedupe-by-hotkey` | `badd532` | Squash-merged |
| `fix/leaderboard-include-ssh-probe` | `574d48f` | Squash-merged |
| `fix/publisher-cors-for-cathedral-site` | `a86a5e3` | Squash-merged |
| `fix/pull-loop-7d-backfill` | `2f570f8` | Squash-merged |
| `fix/pull-loop-backfill-marker-only-on-success` | `dab467c` | Squash-merged |
| `fix/source-pool-in-prompt` | `6c1fe47` | Squash-merged |
| `fix/task-prompt-json-instruction` | `9870f62` | Squash-merged |
| `fix/weight-interval-mainnet-1500` | `b7db826` | Squash-merged |
| `fix/weight-interval-rate-limit` | `c11a75d` | Squash-merged |
| `infra/railway-deploy` | `405446c` | Squash-merged |
| `infra/verify-railway-auto-deploy` | `82a0741` | Squash-merged |
| `pr-49` | `05222ff` | Alias of `feature/polaris-native-eval` |
| `review/pr-109-backfill` | `07efcec` | Stale review checkout |

Before deleting any of these, run `git log <branch> --not main --oneline` and confirm output is empty, or that any returned commits map to already-shipped squash commits in `origin/main`. Anything that returns non-trivial unique commits should be re-verified, not deleted.

### 5c. Origin branches likely candidates for remote pruning (NOT this pass)

Origin still has some fix branches that have been squash-merged into main. Per the safety rule, no remote deletions this pass. Candidates only:

| Origin branch | Status |
|---|---|
| `origin/chore/org-rename-cathedralai` | Old rename branch |
| `origin/chore/repo-rename-sweep` | Old rename branch |
| `origin/chore/runtime-v1.0.7-cathedralai` | Pre-v1.1.0, stale |
| `origin/docs/v1-runbook-pm2` | Likely merged via squash |
| `origin/experimental/cathedral-v2-launch` | Superseded by v3 launch branch |
| `origin/feature/v1-1-0-validator-compat` | Squash-merged |
| `origin/feature/v1-auto-updater` | v1 launch sub-branch, shipped |
| `origin/feature/v1-pm2-ecosystem` | v1 launch sub-branch, shipped |
| `origin/feature/v1-probe-mode` | v1 launch sub-branch, shipped |
| `origin/feature/v1-probe-runner` | v1 launch sub-branch, shipped |
| `origin/feature/v1-provision-miner` | v1 launch sub-branch, shipped |
| `origin/feature/v1-provision-validator` | v1 launch sub-branch, shipped |
| `origin/fix/bt-config-argv-collision` | Squash-merged |
| `origin/fix/cadence-state-leak` | #117 squash-merged |
| `origin/fix/default-attestation-mode-bundle` | Squash-merged |
| `origin/fix/miner-probe-port-mapping` | Squash-merged |
| `origin/fix/miner-provisioner-docker-detection` | Squash-merged |
| `origin/fix/provisioner-python-version` | Squash-merged |
| `origin/fix/pull-loop-include-polaris-verified` | Squash-merged |
| `origin/fix/pull-loop-strip-unsigned-fields` | Squash-merged |
| `origin/fix/testnet-burn-uid-to-owner` | Squash-merged |
| `origin/docs/v1-job-vocab-and-sweep` | Squash-merged |
| `origin/experimental/cathedral-v2-agentic-workforce` | Closed PR #104 |
| `origin/docs/readme-release-pointer` | #121 squash-merged this pass, not auto-deleted because local worktree pinned it |

Verification step before remote delete (next pass): `gh pr list --state merged --search "head:<branch>"` for each, confirm the merged PR exists, then `git push origin --delete <branch>`. Do not bulk-delete dependabot or external-fork branches without review.

---

## Summary counts

- Protected: 2
- Open-PR-backed (incl. origin dependabot/external): 11 distinct heads
- Worktree-backed: 8 branches across 8 worktrees (4 detached-HEAD review worktrees ignored)
- Locked Claude worktrees: 2
- 5a local-merged candidates: 11 (excluding `release/v1.1.0-cathedral-bell` which sits in §1)
- 5b1 local with origin ref: 11
- 5b2 local-only with no origin ref: 34
- 5c origin candidates: 24

Total local branches: 65. Total origin branches: 33. Total active worktrees: 11 (of which 4 detached, 2 locked).

---

## Notes / next-pass actions

1. The four worktrees `cathedralsubnet-miner`, `cathedralsubnet-validator-testnet`, and the two locked Claude worktrees pin already-merged branches. Detaching them (`git worktree remove <path>` after backing up any uncommitted changes; verify with `git status` inside each first) frees up §5a deletions.
2. The `worktree-agent-a*` series of branches all sit at `c7d9cce` (`docs(claude): document new patterns`), a single throwaway tip referenced by abandoned agent runs. Deleting them locally drops nothing not already on `origin/main`.
3. PR #118 (zp6, docs-only, doesn't satisfy #97) already has a triage comment recommending close. No branch action on our side; it lives on the fork.
4. Untracked working-tree files (`INDEX.md`, `uv.lock`) left alone per safety rules.
