# Corpus verification TODO

## What ships now

``PILOT_CORPUS`` in ``seed_pilot.py`` is **empty**.

This is deliberate. The scaffolding (schema, sampler, scorer, claim
extraction, dispatcher, sign path, validator schema acceptance) is
what's merging in this PR. The production corpus is a separate pass
that requires someone to open each candidate's ``source_url``,
verify the parent-of-fix SHA exists in upstream history, and pin
the file path, line range, and failure-mode keywords from the
actual fix diff.

Shipping the framework with an empty production corpus is honest.
Shipping it with plausible-but-unverified rows would be the failure
mode the launch debate explicitly rejected.

## Candidate rows

8 candidates live in
``tests/v3/fixtures/corpus/unverified_examples.py``:

| Candidate | Repo | Source URL kind | Risk |
|---|---|---|---|
| ``UNVERIFIED_requests_proxy_authz_leak`` | psf/requests | GHSA advisory | Real advisory, SHA needs verification |
| ``UNVERIFIED_urllib3_cookie_redirect`` | urllib3/urllib3 | GHSA advisory | Real advisory, SHA needs verification |
| ``UNVERIFIED_urllib3_authz_downgrade`` | urllib3/urllib3 | GHSA advisory | Real advisory, SHA needs verification |
| ``UNVERIFIED_flask_session_samesite_default`` | pallets/flask | main HEAD pointer | Design critique, not a discrete fix commit; consider dropping |
| ``UNVERIFIED_click_mutable_default_envvar`` | pallets/click | PR link | Plausible bug class, full verification needed |
| ``UNVERIFIED_cryptography_padding_off_by_one`` | pyca/cryptography | repo source link | Plausible bug class, full verification needed |
| ``UNVERIFIED_fastapi_dependency_override_leak`` | tiangolo/fastapi | issue link | Real issue, fix commit not pinned |
| ``UNVERIFIED_pydantic_validator_skips_default`` | pydantic/pydantic | repo source link | Plausible bug class, full verification needed |

## Verification pass for each candidate

1. Open ``source_url``. Confirm the advisory / issue / PR
   describes the bug claimed in ``issue_text``.
2. Identify the fix commit. Its **parent** SHA is what ``commit``
   should be (the miner inspects the broken tree, not the fixed
   tree). Click through to the commit page on GitHub; copy the
   parent SHA from the "Parents:" line.
3. ``git blame`` the file in the parent tree, locate the bug,
   record the inclusive line range that contains the defect.
4. Read the fix diff and pull 2-4 ``required_failure_keywords``
   from the diff body. Lowercase substrings; the scorer uses
   case-insensitive substring matching with a ceil(n/2) threshold.
5. If everything checks out, move the row into ``seed_pilot.py``
   (strip the ``UNVERIFIED_`` prefix from ``id``) and delete it
   from the fixtures file.
6. Run ``PYTHONPATH=src python3 -m pytest tests/v3 -q`` to confirm
   the row parses and existing tests still pass.

## Live-feed gate

``CATHEDRAL_V3_FEED_ENABLED`` defaults to ``false``. No v3 rows
hit the public validator feed until:

- ``PILOT_CORPUS`` has at least 10 independently-verified rows.
- Validator operators have upgraded to the version that accepts
  ``eval_output_schema_version=3`` (in this PR).
- The flag is explicitly flipped in the publisher env after both
  conditions above hold.

Also pending before live feed:

- **Salted ``challenge_id_public``.** The current sha256-prefix
  hash in ``cathedral.v3.sign.hash_challenge_id`` is unsalted, so
  the mapping ``raw -> public`` is stable across all hosts and
  trivially reversible by anyone who can guess the raw
  challenge_id format. This is acceptable for the framework PR
  (no challenge_ids exist yet) but must be replaced with an
  epoch-salted hash before live exposure so the public id rotates
  per epoch and cannot be used for cross-miner answer-sharing.
  Tracked inline in ``cathedral.v3.sign`` as a docstring warning.
