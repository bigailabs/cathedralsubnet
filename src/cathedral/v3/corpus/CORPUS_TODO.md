# Corpus verification TODO

## What ships now

``seed_pilot.py`` contains **4 GHSA-anchored rows**. Every row has
a real upstream advisory or canonical source URL in ``source_url``.
The hidden oracle fields (``culprit_file``, ``culprit_symbol``,
``line_range``, ``required_failure_keywords``) are derived from the
advisory plus the file path indicated in the fix diff.

The pilot floor is 4 honest rows, not 8 half-fake rows. The 4
candidate rows that lacked verified SHAs were moved to
``tests/v3/fixtures/corpus/unverified_examples.py`` and are now
test-only fixtures, never imported from production code.

## Verification pass to expand the corpus

For each candidate in ``unverified_examples.py``:

1. Open ``source_url``.
2. Identify the fix commit. Its **parent** SHA is what ``commit``
   must be (the miner inspects the broken tree).
3. ``git blame`` the file in the parent tree, locate the bug, and
   record the inclusive line range in the fix diff.
4. Read the fix diff and pull 2-4 ``required_failure_keywords``
   from the diff body, not the advisory title.
5. If everything verifies, move the row into ``seed_pilot.py``
   (strip the ``UNVERIFIED_`` prefix from ``id``) and delete it
   from the fixtures file.
6. Run ``pytest tests/v3 -q`` and confirm the new row parses and
   the sampler still passes the distinctness assertion.

## Live-feed gate

``CATHEDRAL_V3_FEED_ENABLED`` defaults to ``false``. No v3 rows
hit the public validator feed until:

- The pilot corpus has been independently reviewed (4 rows is the
  floor; aim for ~15 before broad announcement).
- Validator operators have upgraded to the version that accepts
  ``eval_output_schema_version=3`` (in this PR).
- The flag is explicitly flipped in the publisher env after both
  conditions above hold.

This is a launch-blocker for *live mainnet exposure*, not for
landing the PR. The PR ships the framework; the corpus + flag flip
are a deliberate second step.
