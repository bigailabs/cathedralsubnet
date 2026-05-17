"""Synthetic public corpus fixtures for v3 bug_isolation_v1 tests.

Every row here MUST be obviously fake. These exist so the v3 plumbing
can be exercised in CI without ever committing a real bug into the
public repo. Real production rows are loaded at publisher runtime
from a private file via
``cathedral.v3.corpus.private_loader.load_private_corpus``.

If you find yourself wanting to add a row that points at a real repo,
a real SHA, or anything resembling a real upstream advisory, STOP.
That row belongs in the operator-curated private JSON, not here.

Rejection rules (locked by ``tests/v3/test_no_public_real_corpus.py``):
  - ``repo`` must contain ``example.invalid``
  - ``commit`` must be a deterministic placeholder (e.g. ``0...0001``)
  - ``source_url`` must not contain ``github.com``, ``CVE-``,
    ``GHSA-``, ``swebench``, or ``SWE-bench``
  - ``id`` must not start with ``UNVERIFIED_``
"""

from __future__ import annotations

from cathedral.v3.corpus.schema import ChallengeRow

SYNTHETIC_ROWS: tuple[ChallengeRow, ...] = (
    ChallengeRow(
        id="synthetic_001",
        repo="https://example.invalid/synthetic-test",
        commit="0000000000000000000000000000000000000001",
        issue_text="Generic placeholder symptom for plumbing test 001.",
        culprit_file="synthetic/module.py",
        culprit_symbol="synthetic_function",
        line_range=(1, 10),
        required_failure_keywords=("generic", "placeholder"),
        difficulty="easy",
        bucket="synthetic",
        source_url="https://example.invalid/",
    ),
    ChallengeRow(
        id="synthetic_002",
        repo="https://example.invalid/synthetic-test",
        commit="0000000000000000000000000000000000000002",
        issue_text="Generic placeholder symptom for plumbing test 002.",
        culprit_file="synthetic/other.py",
        culprit_symbol="another_function",
        line_range=(20, 30),
        required_failure_keywords=("synthetic", "fixture"),
        difficulty="medium",
        bucket="synthetic",
        source_url="https://example.invalid/",
    ),
    ChallengeRow(
        id="synthetic_003",
        repo="https://example.invalid/synthetic-test",
        commit="0000000000000000000000000000000000000003",
        issue_text="Generic placeholder symptom for plumbing test 003.",
        culprit_file="synthetic/third.py",
        culprit_symbol=None,
        line_range=(5, 15),
        required_failure_keywords=("placeholder",),
        difficulty="hard",
        bucket="synthetic",
        source_url="https://example.invalid/",
    ),
)


__all__ = ["SYNTHETIC_ROWS"]
