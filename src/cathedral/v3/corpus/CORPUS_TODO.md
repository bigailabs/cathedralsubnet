# v3 corpus: how we build it

## What ships today

`PILOT_CORPUS` in `seed_pilot.py` is empty.

The v3 `bug_isolation_v1` plumbing is fully merged on `main`:

- PR #127 (merged): scorer, claim extraction, schema, sampler, sign helper.
- PR #128 (merged at `59d17af`): SSH Hermes dispatch, publisher
  score/sign/persist, validator pull + verification of schema v3,
  weight-blend env (`CATHEDRAL_V3_BUG_ISOLATION_WEIGHT`),
  `epoch_salt` bound into the signed subset.

The machinery is live behind a flag. The reward corpus is not.
`CATHEDRAL_V3_FEED_ENABLED=false` and `CATHEDRAL_V3_BUG_ISOLATION_WEIGHT=0.0`
both stay until corpus rows exist and one testnet E2E passes.

Shipping the framework with an empty production corpus is honest.
Shipping it with plausible-but-unverified rows would be the failure
mode the launch debate explicitly rejected.

## Corpus direction (decided)

**Cathedral-owned, fresh-fix curated.**

We do not seed `PILOT_CORPUS` from SWE-bench, MultiSWE-bench, or any
public benchmark. Those rows are guessable: a miner can search the
problem text, the repo + base_commit, or known benchmark IDs and
recover the gold patch. Public benchmarks are also already in many
training sets.

Instead:

- **Source:** recent merged bug-fix PRs (last 30-90 days) and Python
  CVEs (GHSA, OSV) from the watchlist below.
- **Selection:** clean single-file logic bugs with a tight line range,
  shaped around failure modes where AI coding agents actually struggle.
- **Encoding:** we manually verify each row and write it as a
  `ChallengeRow` in `seed_pilot.py`.
- **Optional bootstrap:** SWE-bench Verified may be used only as
  testnet-smoke calibration material, never inside `PILOT_CORPUS`,
  never on a row that pays weight. Treat it as a public canary lane.

Why this direction:

- Less benchmark contamination than SWE-bench.
- Better control over difficulty and scope (we pick statically-scoreable
  single-file bugs).
- Better public story: real upstream bugs, Cathedral-curated.
- Lower lookup risk than public benchmark rows (still not lookup-proof;
  see threat model below).

## Watchlist repos

**Dense-logic targets (good signal-to-noise):**

- Data/math: `pandas-dev/pandas`, `numpy/numpy`, `scipy/scipy`
- Web/frameworks: `pydantic/pydantic`, `django/django`,
  `tiangolo/fastapi`, `pallets/flask`, `encode/starlette`,
  `encode/httpx`
- CLI/tools: `pallets/click`, `Textualize/rich`, `psf/requests`,
  `urllib3/urllib3`
- Tooling: `pytest-dev/pytest`, `python/mypy`, `astral-sh/ruff`

**Avoid for pilot:** massive multi-language monorepos (e.g. pytorch),
projects requiring external services to reproduce, projects without
clear bug labels or merged PR discussion.

## Failure modes we want to target

The corpus should bias toward bug classes where AI coding agents are
known to fall down. The point is not novelty for novelty's sake; it
is to make the benchmark actually discriminate between miners.

Target buckets:

- **Localization in a large repo.** Right symptom, wrong file/function.
- **Multi-hop reasoning.** Bug surfaces in module A but lives in B.
- **State mutation.** Mutable defaults, shared module state, accidental
  aliasing.
- **None handling.** `Optional` paths the happy path never hits.
- **Async/state ordering.** Cancellation, task cleanup, race-y init.
- **Error handling.** Wrong exception class, swallowed traceback,
  retry vs raise.
- **Parsing edge cases.** Empty input, whitespace, unicode, very large.
- **Time/date handling.** Naive vs aware datetimes, DST, timezone.
- **Path handling / security boundaries.** Traversal, normalization,
  symlink.
- **Config/env defaults.** Behavior changes when an env var is unset.
- **Schema/version compatibility.** Backwards-compat regressions.

A short companion note at `docs/v3/corpus/AGENT_FAILURE_MODES.md`
(to be added with the first corpus PR) should cite the papers/blogs
informing this list, and explain why each bucket is a good Cathedral
task. Use papers to bias selection only; never copy benchmark data.

## Sourcing recipe (per row)

1. Find a candidate PR or advisory:
   - GitHub search per repo:
     - `repo:<owner>/<repo> is:pr is:merged label:bug merged:>2025-01-01`
     - `repo:<owner>/<repo> is:pr is:merged "regression"`
     - `repo:<owner>/<repo> is:pr is:merged "IndexError"`
     - `repo:<owner>/<repo> is:pr is:merged "None"`
   - GitHub Security Advisories: filter Ecosystem=pip, Severity≥Moderate.
   - OSV.dev for cross-checking advisory ranges and fix commits.
2. Apply the selection filter:
   - 1-3 files changed, at least one `.py` source file
   - clear logic flaw (validation, parsing, None, path, async, time)
   - linked issue or substantive PR description
   - fix maps to one culprit file, ideally one symbol, with a tight
     line range
   - bug can be described as a generic user symptom without leaking
     the PR
3. Reject:
   - dependency bumps, docs-only, test-only, large refactors
   - performance-only tuning
   - anything requiring external services
   - anything where the hidden oracle would be ambiguous
4. Extract:
   - `commit` = **parent of the fix commit** (broken tree). On the
     fix commit's GitHub page, copy the SHA from the "Parents:" line.
   - `culprit_file`, `culprit_symbol`, `line_range` from the fix diff,
     mapped to the broken tree (not the patched tree).
   - `required_failure_keywords`: 3-5 lowercase substrings drawn from
     the actual failure mode (scorer is case-insensitive substring with
     `ceil(n/2)` threshold).
   - `source_url`: PR / advisory / commit URL. **Hidden oracle only.
     Never on the miner-visible surface.**
5. Paraphrase `issue_text` into a generic symptom report:
   - Strip issue number, PR number, CVE/GHSA, contributor names,
     repo name, exact upstream title.
   - Strip traceback line numbers if they reveal the fix location.
   - Describe what the user was doing and what unexpected behavior
     they observed.

## Verification pass (every production row)

Before a row lands in `seed_pilot.py`:

1. Open `source_url`. Confirm the advisory / issue / PR describes
   the bug claimed in `issue_text`.
2. Identify the fix commit. Confirm `commit` is its **parent** SHA.
3. Read the file at the parent commit on GitHub. Confirm
   `culprit_file` and `line_range` exist there and contain the
   defect.
4. Read the fix diff. Confirm `required_failure_keywords` are drawn
   from the actual change, not from generic English.
5. Confirm `issue_text` does not contain any of these forbidden
   markers: `SWE-bench`, `instance_id`, `github.com/`, `pull/`,
   `issues/`, `CVE-`, `GHSA-`.
6. Confirm `id` does not carry an `UNVERIFIED_` prefix.
7. Run `PYTHONPATH=src python3 -m pytest tests/v3 -q`.

Rows that fail any step stay in `tests/v3/fixtures/corpus/` (in
either `unverified_examples.py` or a new `fresh_fix_candidates.py`),
never in `seed_pilot.py`.

## Threat model: what verification does and does not buy

**Paraphrasing helps. It is not a defense.** A motivated miner who
sees `repo` + broken `commit` can:

- diff forward from that commit and read the next merged PR
- search merged PRs by description and match symptoms
- correlate broken `commit` against CI failures in upstream history

So:

- Fresh-fix curated rows are materially harder to look up than
  public benchmark rows.
- They are not lookup-proof.
- Treat the pilot corpus as a low-weight bootstrap, not a final
  competitive benchmark.
- Durable anti-gaming is executable repros, delayed settlement, and
  rotating private rows. That work is post-pilot.

## Legacy unverified candidates (PR #127 era)

Eight rows live at `tests/v3/fixtures/corpus/unverified_examples.py`
from the original framework PR. Use them only as starting points,
not as finished work:

| Candidate | Repo | Status |
|---|---|---|
| `UNVERIFIED_requests_proxy_authz_leak` | psf/requests | real advisory; verify SHA before promoting |
| `UNVERIFIED_urllib3_cookie_redirect` | urllib3/urllib3 | real advisory; verify SHA before promoting |
| `UNVERIFIED_urllib3_authz_downgrade` | urllib3/urllib3 | real advisory; verify SHA before promoting |
| `UNVERIFIED_flask_session_samesite_default` | pallets/flask | drop; design critique, not a discrete fix commit |
| `UNVERIFIED_click_mutable_default_envvar` | pallets/click | plausible class; verify or replace |
| `UNVERIFIED_cryptography_padding_off_by_one` | pyca/cryptography | plausible class; verify or replace |
| `UNVERIFIED_fastapi_dependency_override_leak` | tiangolo/fastapi | real issue; fix commit not pinned |
| `UNVERIFIED_pydantic_validator_skips_default` | pydantic/pydantic | plausible class; verify or replace |

Anything that cannot be re-grounded against a real upstream fix in
under ~15 minutes per row should be replaced with a fresh-fix find,
not preserved.

## Live-feed gate

`CATHEDRAL_V3_FEED_ENABLED` defaults to `false`. No v3 rows hit the
public validator feed until **all** of these hold:

1. `PILOT_CORPUS` has at least **7** independently-verified rows.
   (Stricter goal: 10 before any non-trivial weight.)
2. One testnet E2E has passed end-to-end: a real miner via SSH
   Hermes, signed v3 row appears on the publisher feed, a validator
   pulls it, weight blend is observed at `CATHEDRAL_V3_BUG_ISOLATION_WEIGHT=0.05`.
3. Validator fleet is on a release that accepts
   `eval_output_schema_version=3` with `epoch_salt` in the signed
   keyset. (Lands with the next signed tag after PR #128.)
4. Publisher passes a real per-epoch `epoch_salt` to
   `build_signed_v3_bug_isolation_row` (not `None`). The framework
   default is `None`, signed as JSON `null`; production must rotate
   the salt per epoch so `challenge_id_public` cannot be used for
   cross-epoch answer-sharing.
5. The flag is explicitly flipped in the publisher env after 1-4.

Mainnet weight cap during pilot: `0.05`. Hold at that level until
miner behavior on v3 has been observed for at least one week.

## Followups (not blocking the corpus PR)

- Split `/v1/leaderboard/recent` into a public projection (drops
  raw `challenge_id` and `epoch_salt`) and a validator-only
  projection (keeps both for verification). Today the same endpoint
  serves both audiences; acceptable while the feed is off.
- Add `EvalSigner.sign_bytes()` so `cathedral.v3.sign` can stop
  reaching into the signer's private `_sk` attribute.
- Once `PILOT_CORPUS` is non-empty, add a CI test asserting every
  production row passes the verification rules (no `UNVERIFIED_`
  prefix, no forbidden markers in `issue_text`, 40-char lowercase
  SHA, `source_url` present in hidden metadata).
