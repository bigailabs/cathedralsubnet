"""Private bug_isolation_v1 corpus loader.

The bug_isolation_v1 corpus is a hidden oracle. Real rows must never
be committed to this public repository, because the publisher-side
``culprit_file``, ``culprit_symbol``, ``line_range``,
``required_failure_keywords``, and ``source_url`` fields are exactly
what miners would need to skip running their agent and answer from
memory.

This module loads the production corpus from publisher-controlled
storage at runtime. The default storage is a JSON file whose path is
configured via ``CATHEDRAL_V3_CORPUS_PATH``. On Railway, that path
must point inside a Railway Volume (see
``docs/v3/corpus/PRIVATE_CORPUS_STORAGE.md``) or the file is wiped on
every deploy and the loader returns an empty corpus.

The loader is intentionally minimal:
  - read the file path from env
  - read JSON
  - construct each entry via ``ChallengeRow.model_validate(...)``
  - reject obvious leakage (``UNVERIFIED_`` ids, ``swebench`` markers)
  - cache the result in process memory for the lifetime of the run

Why not validate ``github.com`` markers here: production rows will
legitimately be real GitHub bugs. The github-domain rejection lives
on the public synthetic fixtures, not on the loader.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import structlog

from cathedral.v3.corpus.schema import ChallengeRow

_log = structlog.get_logger(__name__)

_CORPUS_PATH_ENV = "CATHEDRAL_V3_CORPUS_PATH"
_REJECTED_ID_PREFIXES: tuple[str, ...] = ("UNVERIFIED_",)
_REJECTED_SOURCE_MARKERS: tuple[str, ...] = ("swebench", "swe-bench")

_cache: tuple[ChallengeRow, ...] | None = None


def clear_private_corpus_cache() -> None:
    """Drop the in-process corpus cache.

    Tests use this between cases that exercise different on-disk
    contents. Production never calls it: the loader caches for the
    lifetime of the publisher process and a refresh requires a
    restart, which matches the operator workflow documented in
    ``docs/v3/corpus/PRIVATE_CORPUS_STORAGE.md``.
    """
    global _cache
    _cache = None


def _is_rejected(row: ChallengeRow) -> tuple[bool, str | None]:
    for prefix in _REJECTED_ID_PREFIXES:
        if row.id.startswith(prefix):
            return True, f"rejected_id_prefix:{prefix}"
    lowered_source = row.source_url.lower()
    for marker in _REJECTED_SOURCE_MARKERS:
        if marker in lowered_source:
            return True, f"rejected_source_marker:{marker}"
    return False, None


def load_private_corpus(
    env: Mapping[str, str] | None = None,
) -> tuple[ChallengeRow, ...]:
    """Load the bug_isolation_v1 corpus from private storage.

    Returns an empty tuple (and logs ``corpus_unavailable``) when:
      - ``CATHEDRAL_V3_CORPUS_PATH`` is unset
      - the file at that path does not exist
      - the file fails to parse as a JSON list

    Returned rows are always ``ChallengeRow`` instances; downstream
    code uses attribute access (``row.culprit_file``), never dict
    access.

    ``env`` is for tests; production passes ``None`` and we read
    ``os.environ``.
    """
    global _cache
    if _cache is not None:
        return _cache

    values = os.environ if env is None else env
    path_str = values.get(_CORPUS_PATH_ENV)
    if not path_str:
        _log.info("corpus_unavailable", reason="env_unset", env=_CORPUS_PATH_ENV)
        _cache = ()
        return _cache

    path = Path(path_str)
    if not path.is_file():
        _log.info("corpus_unavailable", reason="file_missing", path=str(path))
        _cache = ()
        return _cache

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.info(
            "corpus_unavailable",
            reason="parse_error",
            path=str(path),
            error=str(exc),
        )
        _cache = ()
        return _cache

    if not isinstance(raw, list):
        _log.info(
            "corpus_unavailable",
            reason="not_a_list",
            path=str(path),
            type=type(raw).__name__,
        )
        _cache = ()
        return _cache

    rows: list[ChallengeRow] = []
    rejected = 0
    for entry in raw:
        # ChallengeRow has extra=forbid + regex on commit, so any
        # malformed entry raises ValidationError. We let that bubble
        # up: a corrupt private corpus must fail loudly, not silently
        # ship a partial set.
        row = ChallengeRow.model_validate(entry)
        is_rejected, reason = _is_rejected(row)
        if is_rejected:
            rejected += 1
            _log.warning(
                "corpus_row_rejected",
                row_id=row.id,
                reason=reason,
            )
            continue
        rows.append(row)

    _cache = tuple(rows)
    _log.info(
        "corpus_loaded",
        path=str(path),
        rows=len(_cache),
        rejected=rejected,
    )
    return _cache


__all__ = [
    "clear_private_corpus_cache",
    "load_private_corpus",
]
