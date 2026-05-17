"""Placeholder for the v3 bug_isolation_v1 pilot corpus.

PERMANENTLY EMPTY. DO NOT ADD ROWS TO THIS FILE.

The v3 bug_isolation_v1 corpus is a hidden oracle. Every row contains
the answer the miner is supposed to discover (``culprit_file``,
``culprit_symbol``, ``line_range``, ``required_failure_keywords``,
``source_url``). Anything committed to this public repo is visible to
every miner and defeats the point of the oracle.

Production rows live in operator-controlled private storage and are
loaded at publisher runtime by
``cathedral.v3.corpus.private_loader.load_private_corpus`` from the
path in ``CATHEDRAL_V3_CORPUS_PATH``.

If you are tempted to drop a row here:
  - Read ``docs/v3/corpus/PRIVATE_CORPUS_STORAGE.md`` for the
    operator workflow (Railway Volume + JSON file + env var).
  - Read ``src/cathedral/v3/corpus/CORPUS_TODO.md`` for the curation
    playbook.
  - Put the row in your private ``rows.json``, not here.

This module exists only as a stable import target. Removing it would
break callers that still import ``PILOT_CORPUS`` for tests that
assert "empty by design"; keeping the constant lets those assertions
catch any accidental future row addition loudly.

The live-feed enablement gate is in
``docs/v3/corpus/PRIVATE_CORPUS_STORAGE.md`` and is NOT a function
of ``len(PILOT_CORPUS)``. The relevant precondition is "loader
reports a non-empty corpus on boot", which is a property of the
private store, not of this file.
"""

from __future__ import annotations

from cathedral.v3.corpus.schema import ChallengeRow

# Permanently empty. Real rows live in private storage; see module
# docstring above. The `test_no_public_real_corpus.py` guardrail
# locks this in CI: adding rows here will fail the build.
PILOT_CORPUS: tuple[ChallengeRow, ...] = ()
