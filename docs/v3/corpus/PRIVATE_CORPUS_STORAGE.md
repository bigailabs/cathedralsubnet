# v3 Private Corpus Storage

## Why this is private

The v3 `bug_isolation_v1` corpus is a hidden oracle. Every row
contains the answer the miner is supposed to discover:

- `culprit_file`
- `culprit_symbol`
- `line_range`
- `required_failure_keywords`
- `source_url` (real upstream fix / advisory)

Anything committed to this repository is public. Real rows MUST
never live in `src/cathedral/v3/corpus/seed_pilot.py`, in
`tests/v3/fixtures/corpus/`, or anywhere else inside this repo.

`PILOT_CORPUS` in `seed_pilot.py` is permanently `()`. The
production corpus is loaded at runtime by
`cathedral.v3.corpus.private_loader.load_private_corpus` from a JSON
file whose path is configured via `CATHEDRAL_V3_CORPUS_PATH`.

## Operator workflow

1. Curate `rows.json` on your local machine, **outside** this
   repository's working tree. Follow the curation rules in
   `src/cathedral/v3/corpus/CORPUS_TODO.md`.
2. The file must be a JSON list of objects, each shaped like
   `cathedral.v3.corpus.schema.ChallengeRow`:

   ```json
   [
     {
       "id": "v3_pilot_<repo>_<short_topic>",
       "repo": "https://github.com/<owner>/<repo>",
       "commit": "<40 char lowercase parent-of-fix SHA>",
       "issue_text": "Paraphrased symptom only.",
       "culprit_file": "<path/in/repo>",
       "culprit_symbol": "<function_or_class_or_null>",
       "line_range": [<start>, <end>],
       "required_failure_keywords": ["<kw1>", "<kw2>", "<kw3>"],
       "difficulty": "easy|medium|hard",
       "bucket": "<failure_mode_bucket>",
       "source_url": "https://github.com/<owner>/<repo>/pull/<n>"
     }
   ]
   ```
3. Copy `rows.json` onto the publisher host.
4. Set `CATHEDRAL_V3_CORPUS_PATH` in the publisher's environment to
   the absolute path of the file.
5. Restart the publisher process. The loader caches in process
   memory for the lifetime of the process; a new corpus requires a
   restart, on purpose.

The loader rejects rows whose `id` starts with `UNVERIFIED_` or
whose `source_url` mentions `swebench` / `SWE-bench`. Anything else
that fails `ChallengeRow` validation raises and the publisher will
refuse to start, which is the correct behavior for a corrupt
hidden-oracle file.

## Railway persistence (critical)

On Railway, the container filesystem is ephemeral. A file copied
into the image at build time, or written into the container at
runtime, is wiped on every deploy and restart. If the corpus path
points at ephemeral storage, the loader will silently return `()`
after the next restart and the v3 lane will skip every submission
with `v3_bug_isolation_skipped reason=empty_corpus`.

To persist the corpus on Railway:

1. Provision a Railway Volume on the publisher service.
2. Mount it at a stable path, e.g. `/app/data`.
3. Place `corpus.json` inside the volume:
   `/app/data/corpus.json`.
4. Set the env var on the service:
   `CATHEDRAL_V3_CORPUS_PATH=/app/data/corpus.json`.
5. Redeploy. Confirm the loader logs `corpus_loaded path=... rows=N`
   in the boot logs, where `N` is the row count you uploaded.

If you see `corpus_unavailable reason=file_missing` after a deploy,
the path is not on a volume.

## Pre-flight before flipping the feed

`CATHEDRAL_V3_FEED_ENABLED` MUST stay `false` until ALL of these
hold:

1. The loader reports a non-empty corpus on the publisher you intend
   to enable, after a fresh restart. Check the boot log line
   `corpus_loaded path=... rows=N`.
2. At least one testnet end-to-end run has scored a real miner via
   SSH Hermes, produced a signed v3 row, the validator pulled it,
   and weight blended at `CATHEDRAL_V3_BUG_ISOLATION_WEIGHT=0.05`.
3. Validator fleet is on a release that accepts
   `eval_output_schema_version=3` with `epoch_salt` in the signed
   keyset (lands with the next signed tag after PR #128).
4. The publisher passes a real per-epoch `epoch_salt` to
   `build_signed_v3_bug_isolation_row`, not the framework default
   of `None`.

Only after 1-4 are confirmed should the operator flip the feed.

## Auditing the live corpus without exposing it

To check what is loaded on a running publisher without exposing the
contents, prefer:

- the boot log `corpus_loaded path=<path> rows=<n>` line
- a future operator-only endpoint that returns row IDs only

Do not print row bodies to shared logs, dashboards, or Slack.

## What lives in this repo

| File | Purpose |
|---|---|
| `src/cathedral/v3/corpus/private_loader.py` | Reads private JSON at runtime |
| `src/cathedral/v3/corpus/seed_pilot.py` | Permanently empty placeholder |
| `tests/v3/fixtures/corpus/synthetic_rows.py` | Obviously fake rows for plumbing tests |
| `tests/v3/test_no_public_real_corpus.py` | In-memory guardrail tests |
| `tests/v3/test_corpus_loader.py` | Loader behavior tests |
| `docs/v3/corpus/PRIVATE_CORPUS_STORAGE.md` | This file |
| `src/cathedral/v3/corpus/CORPUS_TODO.md` | Curation playbook |

The private `rows.json` lives nowhere in this repo. It lives on the
operator's machine and inside the Railway Volume on the publisher.
