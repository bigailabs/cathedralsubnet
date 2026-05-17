"""Tests for the private bug_isolation_v1 corpus loader.

The loader reads operator-curated JSON from a path in
``CATHEDRAL_V3_CORPUS_PATH``. It must:
  - return ``()`` when env or file is missing
  - return a tuple of ``ChallengeRow`` (attribute access, not dict)
  - cache between calls, and rebuild after
    ``clear_private_corpus_cache``
  - reject ``UNVERIFIED_`` ids and ``swebench`` source markers

These tests only ever use synthetic rows. They never touch real
production corpus material.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cathedral.v3.corpus.private_loader import (
    clear_private_corpus_cache,
    load_private_corpus,
)
from cathedral.v3.corpus.schema import ChallengeRow


def _row_dict(
    *,
    id: str = "synthetic_loader_001",
    source_url: str = "https://example.invalid/",
) -> dict:
    return {
        "id": id,
        "repo": "https://example.invalid/synthetic-test",
        "commit": "0000000000000000000000000000000000000099",
        "issue_text": "Generic placeholder symptom.",
        "culprit_file": "synthetic/module.py",
        "culprit_symbol": "synthetic_function",
        "line_range": [1, 10],
        "required_failure_keywords": ["generic", "placeholder"],
        "difficulty": "easy",
        "bucket": "synthetic",
        "source_url": source_url,
    }


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_private_corpus_cache()
    yield
    clear_private_corpus_cache()


def test_missing_env_returns_empty_tuple() -> None:
    """No env var means no corpus; loader degrades silently to ``()``."""
    assert load_private_corpus(env={}) == ()


def test_missing_file_returns_empty_tuple(tmp_path: Path) -> None:
    """Env var pointing at a nonexistent file is a soft failure."""
    missing = tmp_path / "does_not_exist.json"
    result = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(missing)})
    assert result == ()


def test_unparseable_file_returns_empty_tuple(tmp_path: Path) -> None:
    """Bad JSON does not crash the publisher; corpus is reported empty."""
    path = tmp_path / "corpus.json"
    path.write_text("not json at all", encoding="utf-8")
    result = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    assert result == ()


def test_non_list_top_level_returns_empty_tuple(tmp_path: Path) -> None:
    """Top-level JSON must be a list of row dicts."""
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps({"rows": []}), encoding="utf-8")
    result = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    assert result == ()


def test_valid_json_loads_as_tuple_of_challenge_rows(tmp_path: Path) -> None:
    """Happy path: file parses, rows validate, attribute access works."""
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([_row_dict()]), encoding="utf-8")
    result = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    assert isinstance(result, tuple)
    assert len(result) == 1
    row = result[0]
    assert isinstance(row, ChallengeRow)
    # Attribute access (not dict). If a future change accidentally
    # returned raw dicts these would AttributeError.
    assert row.id == "synthetic_loader_001"
    assert row.culprit_file == "synthetic/module.py"
    assert row.line_range == (1, 10)


def test_loader_caches_between_calls(tmp_path: Path) -> None:
    """Once loaded, subsequent calls return the same tuple object."""
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([_row_dict()]), encoding="utf-8")
    first = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    # Mutating the file does not affect the cached result.
    path.write_text(json.dumps([_row_dict(id="synthetic_loader_002")]), encoding="utf-8")
    second = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    assert first is second
    assert second[0].id == "synthetic_loader_001"


def test_clear_cache_picks_up_new_contents(tmp_path: Path) -> None:
    """``clear_private_corpus_cache`` forces the next call to re-read."""
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([_row_dict()]), encoding="utf-8")
    load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    path.write_text(
        json.dumps([_row_dict(id="synthetic_loader_002")]), encoding="utf-8"
    )
    clear_private_corpus_cache()
    refreshed = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    assert refreshed[0].id == "synthetic_loader_002"


def test_unverified_id_is_rejected(tmp_path: Path) -> None:
    """Rows with the legacy ``UNVERIFIED_`` prefix must not load."""
    path = tmp_path / "corpus.json"
    rows = [
        _row_dict(id="UNVERIFIED_should_be_rejected"),
        _row_dict(id="synthetic_loader_keep"),
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    result = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    assert [row.id for row in result] == ["synthetic_loader_keep"]


def test_swebench_source_marker_is_rejected(tmp_path: Path) -> None:
    """Rows whose ``source_url`` mentions swebench are filtered.

    Production stance: SWE-bench is not used as the reward corpus.
    Defense in depth: if an operator's curation script ever pulls a
    row from there, the loader drops it.
    """
    path = tmp_path / "corpus.json"
    rows = [
        _row_dict(
            id="synthetic_loader_keep",
            source_url="https://example.invalid/internal-issue",
        ),
        _row_dict(
            id="synthetic_loader_drop_a",
            source_url="https://example.invalid/swebench-mirror",
        ),
        _row_dict(
            id="synthetic_loader_drop_b",
            source_url="https://example.invalid/SWE-bench-mirror",
        ),
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    result = load_private_corpus(env={"CATHEDRAL_V3_CORPUS_PATH": str(path)})
    assert [row.id for row in result] == ["synthetic_loader_keep"]
