# v3 corpus: how we build it

## What ships in this repo

`PILOT_CORPUS` in `seed_pilot.py` is permanently `()`. The production
v3 `bug_isolation_v1` corpus is a hidden oracle and lives entirely
outside this public repository.

At publisher runtime, the orchestrator calls
`cathedral.v3.corpus.private_loader.load_private_corpus`, which reads
operator-curated JSON from the path in `CATHEDRAL_V3_CORPUS_PATH`,
validates each entry through the `ChallengeRow` pydantic model, and
returns a tuple of rows.

Storage backend, Railway Volume requirements, and the boot-time
verification ritual live in
`docs/v3/corpus/PRIVATE_CORPUS_STORAGE.md`.

## Why public benchmarks were rejected

`PILOT_CORPUS` does not pull from SWE-bench, MultiSWE-bench, or any
other public dataset. Those rows are guessable: a miner can search
problem text, repo + base_commit, or known benchmark IDs and recover
the gold patch. Many are already in agent training sets. Defense in
depth: the loader rejects rows whose `source_url` mentions
`swebench` / `SWE-bench`.

The reward corpus is Cathedral-owned, manually curated from recent
real GitHub bug fixes and CVEs, biased toward failure modes where AI
coding agents actually struggle.

## Curation playbook (operator-side)

Curation happens on the operator's machine, **outside** this
repository, and the result is uploaded into the publisher's Railway
Volume per `PRIVATE_CORPUS_STORAGE.md`. The following recipe is
public; the rows it produces are not.

### Watchlist repos

Dense-logic Python projects with clear bug labels:

- Data/math: `pandas-dev/pandas`, `numpy/numpy`, `scipy/scipy`
- Web/frameworks: `pydantic/pydantic`, `django/django`,
  `tiangolo/fastapi`, `pallets/flask`, `encode/starlette`,
  `encode/httpx`
- CLI/tools: `pallets/click`, `Textualize/rich`, `psf/requests`,
  `urllib3/urllib3`
- Tooling: `pytest-dev/pytest`, `python/mypy`, `astral-sh/ruff`

Avoid: massive multi-language monorepos, projects requiring external
services to reproduce, projects without clear bug discussion.

### Failure-mode buckets to target

- Multi-hop reasoning. Bug surfaces in module A but lives in B.
- State mutation. Mutable defaults, shared module state.
- None handling. Optional paths the happy path never hits.
- Async / state ordering. Cancellation, task cleanup, race-y init.
- Error handling. Wrong exception class, swallowed traceback.
- Parsing edge cases. Empty input, whitespace, unicode.
- Time / date handling. Naive vs aware datetimes, DST.
- Path / security boundaries. Traversal, normalization, symlink.
- Config / env defaults. Behavior changes when an env var is unset.
- Schema / version compatibility. Backwards-compat regressions.

### GitHub search queries

```
repo:<owner>/<repo> is:pr is:merged label:bug merged:>2025-01-01
repo:<owner>/<repo> is:pr is:merged "regression" merged:>2025-01-01
repo:<owner>/<repo> is:pr is:merged "IndexError"
repo:<owner>/<repo> is:pr is:merged "None"
```

### Selection filter

Accept only PRs that:
- modify 1 to 3 files, at least one Python source file
- fix a clear logic / validation / parsing / path / async / time bug
- have a linked issue or substantive PR description
- localize to one culprit file, one symbol where possible, with a
  tight line range
- can be described as a generic user symptom without leaking the PR

Reject: dependency bumps, docs-only, test-only, large refactors,
performance-only tuning, anything requiring external services.

### Per-row extraction

- `commit` = parent of the fix commit, **not the fix commit itself**.
  On the fix commit's GitHub page, copy the SHA from the
  `Parents:` line. The miner inspects the broken tree.
- `culprit_file`, `culprit_symbol`, `line_range`: read off the fix
  diff, mapped to the broken tree.
- `required_failure_keywords`: 3-5 lowercase substrings drawn from
  the actual fix, not from generic English. The scorer matches case
  insensitive with a `ceil(n/2)` threshold (floor of 1).
- `source_url`: PR / advisory / commit URL. Hidden-oracle only.

### Paraphrasing the miner-visible `issue_text`

`issue_text` is the only field the miner sees in the prompt. It must
read like a generic user symptom report. Strip:

- issue number, PR number, CVE / GHSA / OSV IDs
- contributor names and exact repo names
- exact tracebacks if they reveal the fix line
- phrases copied verbatim from the upstream issue title

Paraphrasing is friction, not security. A motivated miner who sees
`repo` + broken `commit` can still diff forward and find the merged
fix. Treat the pilot corpus as a low-weight bootstrap; durable
anti-gaming (executable repros, delayed settlement, rotating private
rows) is post-pilot work.

### Verification pass (every row, before upload)

1. Open `source_url`. Confirm the advisory / PR describes the bug
   in `issue_text`.
2. Confirm `commit` is the parent of the fix commit.
3. Open the file at `commit` on GitHub. Confirm `culprit_file` and
   `line_range` exist and contain the defect.
4. Confirm `required_failure_keywords` come from the actual fix.
5. Confirm `issue_text` does not contain forbidden markers
   (`github.com`, `pull/`, `issues/`, `CVE-`, `GHSA-`, exact title).
6. Confirm `id` does not start with `UNVERIFIED_`.

## Loader behavior summary

`load_private_corpus`:

- reads `CATHEDRAL_V3_CORPUS_PATH`; returns `()` and logs
  `corpus_unavailable` if unset, missing, or unparseable
- expects a top-level JSON list
- constructs each entry via `ChallengeRow.model_validate(...)`
- drops rows whose `id` starts with `UNVERIFIED_`
- drops rows whose `source_url` mentions `swebench` / `SWE-bench`
- does NOT filter `github.com` markers; production rows
  legitimately reference real GitHub bugs
- caches in process memory; `clear_private_corpus_cache()` exists
  for tests; production refreshes only on restart

## Legacy unverified candidates (PR #127 era)

Eight rows live at `tests/v3/fixtures/corpus/unverified_examples.py`
from the original framework PR. They are not loaded by anything in
production. Use them only as starting points for upstream sleuthing;
do not promote them into the private corpus without rebuilding the
oracle from a freshly verified upstream fix.

## Live-feed gate

`CATHEDRAL_V3_FEED_ENABLED` defaults to `false`. The flag stays off
until all of these hold:

1. Loader reports a non-empty corpus on boot (`corpus_loaded
   path=... rows=N`).
2. One testnet end-to-end run has produced a signed v3 row, the
   validator pulled it, and weight blended at
   `CATHEDRAL_V3_BUG_ISOLATION_WEIGHT=0.05`.
3. Validator fleet is on a release that accepts
   `eval_output_schema_version=3` with `epoch_salt` in the signed
   keyset.
4. Publisher passes a real per-epoch `epoch_salt` to
   `build_signed_v3_bug_isolation_row` (the framework default is
   `None`).
5. Flag is explicitly flipped in publisher env after 1-4.

Mainnet weight cap during pilot: `0.05`. Hold there for at least one
week of observation before raising.

## Followups (not blocking)

- Split `/v1/leaderboard/recent` into a public projection (drops raw
  `challenge_id` and `epoch_salt`) and a validator-only projection.
- Add `EvalSigner.sign_bytes()` so `cathedral.v3.sign` can stop
  reaching into the signer's private `_sk` attribute.
- Operator-only endpoint that reports `corpus row count + ids` (not
  bodies) for live auditing without exposing the oracle.
