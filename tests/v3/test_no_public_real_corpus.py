"""In-memory guardrails that real bug rows do not slip into the public repo.

The v3 bug_isolation_v1 corpus is a hidden oracle. Anything checked
into ``src/cathedral/v3/corpus/seed_pilot.py`` or
``tests/v3/fixtures/corpus/`` is visible to anyone who reads the
public repository, which would defeat the point of having an oracle
at all.

We do not regex-scan files (fragile). We import the actual Python
objects and assert on their attributes. If a future developer adds
real rows to either surface, the import succeeds and the assertions
catch it loudly.

The loader itself (``cathedral.v3.corpus.private_loader``) is *not*
constrained by ``github.com`` here, because production private rows
will legitimately point at real GitHub bugs. Only the public-surface
fixtures must reject ``github.com``.
"""

from __future__ import annotations

from cathedral.v3.corpus.seed_pilot import PILOT_CORPUS
from tests.v3.fixtures.corpus.synthetic_rows import SYNTHETIC_ROWS

_FORBIDDEN_SOURCE_MARKERS: tuple[str, ...] = (
    "github.com",
    "cve-",
    "ghsa-",
    "swebench",
    "swe-bench",
)
_FORBIDDEN_REPO_MARKERS: tuple[str, ...] = (
    "github.com",
    "pydantic",
    "django",
    "flask",
    "fastapi",
    "requests",
    "urllib3",
    "pandas",
    "numpy",
    "scipy",
    "click",
    "rich",
    "starlette",
    "httpx",
    "pytest",
    "mypy",
    "ruff",
)


def test_pilot_corpus_is_empty_by_design() -> None:
    """The public ``PILOT_CORPUS`` must stay empty in production.

    Real rows belong in private storage loaded by
    ``cathedral.v3.corpus.private_loader.load_private_corpus``.
    """
    assert len(PILOT_CORPUS) == 0, (
        "PILOT_CORPUS must be empty. Real bug rows are a hidden oracle "
        "and live in private storage, not in this public file."
    )


def test_synthetic_rows_only_reference_example_invalid_repo() -> None:
    """Every synthetic fixture row must use ``example.invalid``."""
    for row in SYNTHETIC_ROWS:
        assert "example.invalid" in row.repo, (
            f"synthetic row {row.id!r} repo must contain example.invalid; "
            f"got {row.repo!r}. Real repos belong in the private corpus."
        )


def test_synthetic_rows_source_url_carries_no_real_markers() -> None:
    """Synthetic ``source_url`` must not look like a real upstream link."""
    for row in SYNTHETIC_ROWS:
        lowered = row.source_url.lower()
        for marker in _FORBIDDEN_SOURCE_MARKERS:
            assert marker not in lowered, (
                f"synthetic row {row.id!r} source_url {row.source_url!r} "
                f"contains forbidden marker {marker!r}. Real provenance "
                f"belongs only in the private corpus."
            )


def test_synthetic_rows_repo_carries_no_real_project_markers() -> None:
    """Synthetic ``repo`` must not name a real watchlist project."""
    for row in SYNTHETIC_ROWS:
        lowered = row.repo.lower()
        for marker in _FORBIDDEN_REPO_MARKERS:
            assert marker not in lowered, (
                f"synthetic row {row.id!r} repo {row.repo!r} contains "
                f"forbidden project marker {marker!r}."
            )


def test_synthetic_rows_ids_carry_no_unverified_prefix() -> None:
    """Synthetic ids must not look like demoted unverified production rows.

    ``UNVERIFIED_`` is reserved for the candidate-fixture file
    (``unverified_examples.py``) that predates this loader work.
    Synthetic rows are their own deliberately-fake category.
    """
    for row in SYNTHETIC_ROWS:
        assert not row.id.startswith("UNVERIFIED_"), (
            f"synthetic row id {row.id!r} must not use the UNVERIFIED_ "
            f"prefix; that namespace is for legacy candidate fixtures."
        )
