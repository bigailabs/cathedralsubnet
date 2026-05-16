"""Pilot bug_isolation_v1 corpus.

Every row in PILOT_CORPUS MUST cite a real upstream fix commit, PR,
or GHSA URL in ``source_url``, AND the hidden oracle fields
(``culprit_file``, ``culprit_symbol``, ``line_range``,
``required_failure_keywords``) MUST be verified against the actual
diff at the parent-of-fix commit by a reviewer who opened the URL
and read the code.

No placeholder SHAs. No invented bugs. No guessed line ranges. If a
row cannot be verified by clicking the link and reading the diff,
it does not ship in production.

## Current state

PILOT_CORPUS is **empty** in this PR. The scaffolding (schema,
sampler, scorer, claim extraction, dispatch, sign) is the merge
target; the corpus is a follow-up pass.

Candidate rows pending verification live in
``tests/v3/fixtures/corpus/unverified_examples.py``. They are
test-only fixtures, never imported from production code.

This is deliberate: shipping the framework with an empty production
corpus is honest. Shipping it with 4 plausible-but-unverified rows
would be the failure mode the launch debate explicitly rejected
("4 honest beats 8 half-fake; do not merge placeholders").

## Verification gate to ship rows

For each candidate in ``unverified_examples.py``:

1. Open ``source_url``. Confirm the advisory or fix PR describes
   the bug claimed in ``issue_text``.
2. Identify the fix commit. Its **parent** SHA is what ``commit``
   must be (the miner inspects the broken tree). Click through to
   the commit page and copy the parent SHA from there.
3. ``git blame`` the file in the parent tree, locate the bug, and
   record the inclusive line range in the fix diff body.
4. Pull 2-4 ``required_failure_keywords`` from the fix diff body,
   not the advisory title. Lowercase substrings; the scorer
   matches case-insensitive with a ceil(n/2) threshold.
5. Move the row into ``PILOT_CORPUS`` here (strip the
   ``UNVERIFIED_`` prefix from ``id``) and delete it from the
   fixtures file.
6. Run ``pytest tests/v3 -q`` and confirm the new row parses and
   the sampler still passes the distinctness assertion.

Live-feed enablement (``CATHEDRAL_V3_FEED_ENABLED=true``) is
blocked on:
  - PILOT_CORPUS has at least 10 independently-verified rows.
  - Validator operators have upgraded to the version that accepts
    ``eval_output_schema_version=3`` (in this PR).
  - The flag is then explicitly flipped in publisher env.
"""

from __future__ import annotations

from cathedral.v3.corpus.schema import ChallengeRow

# Empty by design. See module docstring above. Do not pad this with
# unverified rows; demoting rows whose SHAs cannot be confirmed
# from upstream is the only honest path here.
PILOT_CORPUS: tuple[ChallengeRow, ...] = ()


def get_pilot_corpus() -> tuple[ChallengeRow, ...]:
    """Return the in-memory pilot corpus.

    Empty in this PR. The publisher must check ``len(...)`` before
    attempting to dispatch a bug_isolation_v1 challenge, and skip
    the lane entirely when the corpus is empty (or behind the
    feature flag, whichever fires first).
    """
    return PILOT_CORPUS
