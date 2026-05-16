"""Deterministic challenge sampler tests.

Spec: when N active hotkeys share an epoch and corpus has >= N rows,
every hotkey gets a distinct challenge_id. Determinism across runs
is sha256-based, not Python ``hash()``-based, so cross-process
agreement is guaranteed.
"""

from __future__ import annotations

import pytest

from cathedral.v3.corpus import sample_challenge_id_for_hotkey
from cathedral.v3.corpus.seed_pilot import PILOT_CORPUS

# The pilot corpus floor is 4 verified rows. For sampler distinctness
# tests we need >=5 ids so 5 hotkeys can map to 5 distinct challenges;
# we top up from the test-only unverified fixtures.
from tests.v3.fixtures.corpus.unverified_examples import UNVERIFIED_EXAMPLES

CORPUS_IDS = [row.id for row in PILOT_CORPUS] + [
    row.id for row in UNVERIFIED_EXAMPLES
]


def _five_hotkeys() -> list[str]:
    return [
        "5CHsG49J1xCZeSh3J5XvxxgcZTLJYL2bM6vSqXqYW1aB",
        "5CS2c364XJxX5kKp8ZdRfqHsT9YjZ5xBwH2DcG5JdN3F",
        "5CSTYYfaQjJWWZQrK3K3LfCkkH8jQQqHsR8R9zS1nZ5T",
        "5CkdqSwm8K6VsZTQHcQYxJDjBxgvkN3nC8nJ5XzS7P8P",
        "5DFnKviS1KqVnB7LkRzwhgFxJZ3kHWQVqJzKtH6jY8C2",
    ]


def test_five_hotkeys_get_five_distinct_challenges() -> None:
    hotkeys = _five_hotkeys()
    assert len(hotkeys) == 5
    assert len(CORPUS_IDS) >= 5, "test setup needs >= 5 corpus rows"

    assignments = {
        hk: sample_challenge_id_for_hotkey(
            hotkey=hk,
            active_hotkeys=hotkeys,
            corpus_ids=CORPUS_IDS,
            epoch_number=1,
        )
        for hk in hotkeys
    }
    distinct = set(assignments.values())
    assert len(distinct) == 5, (
        f"expected 5 distinct challenge_ids for 5 hotkeys, got "
        f"{len(distinct)}: {assignments}"
    )


def test_sampler_is_deterministic_across_calls() -> None:
    """Calling the sampler twice with the same inputs returns the same id."""
    hotkeys = _five_hotkeys()
    hk = hotkeys[2]
    first = sample_challenge_id_for_hotkey(
        hotkey=hk,
        active_hotkeys=hotkeys,
        corpus_ids=CORPUS_IDS,
        epoch_number=42,
    )
    second = sample_challenge_id_for_hotkey(
        hotkey=hk,
        active_hotkeys=hotkeys,
        corpus_ids=CORPUS_IDS,
        epoch_number=42,
    )
    assert first == second


def test_sampler_changes_id_with_epoch() -> None:
    """Different epoch numbers should permute the challenge order.
    The same hotkey usually maps to a different challenge_id across
    adjacent epochs, otherwise the corpus burns instantly."""
    hotkeys = _five_hotkeys()
    hk = hotkeys[0]
    ids_by_epoch = {
        e: sample_challenge_id_for_hotkey(
            hotkey=hk,
            active_hotkeys=hotkeys,
            corpus_ids=CORPUS_IDS,
            epoch_number=e,
        )
        for e in range(10)
    }
    # Not strictly requiring all distinct (we only have len(corpus) options),
    # but adjacent epochs MUST differ at least once across the first 10.
    assert len(set(ids_by_epoch.values())) > 1


def test_sampler_collisions_when_hotkeys_exceed_corpus() -> None:
    """Documented behavior: with more hotkeys than corpus rows, by
    pigeonhole at least two hotkeys map to the same challenge."""
    corpus = CORPUS_IDS[:3]
    hotkeys = _five_hotkeys()
    assignments = [
        sample_challenge_id_for_hotkey(
            hotkey=hk,
            active_hotkeys=hotkeys,
            corpus_ids=corpus,
            epoch_number=7,
        )
        for hk in hotkeys
    ]
    assert len(assignments) > len(set(assignments)), (
        "expected at least one collision when hotkeys > corpus_ids"
    )


def test_sampler_rejects_hotkey_not_in_active_set() -> None:
    hotkeys = _five_hotkeys()
    with pytest.raises(ValueError, match="not in active_hotkeys"):
        sample_challenge_id_for_hotkey(
            hotkey="5NotInTheSet000000000000000000000000000000000",
            active_hotkeys=hotkeys,
            corpus_ids=CORPUS_IDS,
            epoch_number=1,
        )


def test_sampler_rejects_empty_corpus() -> None:
    with pytest.raises(ValueError, match="corpus_ids is empty"):
        sample_challenge_id_for_hotkey(
            hotkey="5CHsG49J1xCZeSh3J5XvxxgcZTLJYL2bM6vSqXqYW1aB",
            active_hotkeys=["5CHsG49J1xCZeSh3J5XvxxgcZTLJYL2bM6vSqXqYW1aB"],
            corpus_ids=[],
            epoch_number=1,
        )


def test_pilot_corpus_loads_and_validates() -> None:
    """The pilot corpus must parse against the schema. Any malformed
    row (wrong commit length, missing source_url, etc.) fails here
    at import time.

    Floor is 4 verified rows. Pad to 12-15 in a follow-up by working
    through ``CORPUS_TODO.md`` and moving entries out of
    ``tests/v3/fixtures/corpus/unverified_examples.py``.
    """
    assert len(PILOT_CORPUS) >= 4, (
        "pilot corpus floor is 4 verified rows. Do not lower this; "
        "if you cannot verify 4 rows the launch should not ship."
    )
    for row in PILOT_CORPUS:
        assert len(row.commit) == 40, f"{row.id}: commit must be 40-char SHA"
        assert row.source_url.startswith("https://"), (
            f"{row.id}: source_url must be https URL to upstream evidence"
        )
        assert len(row.required_failure_keywords) >= 1, (
            f"{row.id}: at least one failure keyword required"
        )
        # No CVE IDs in public prompt text.
        assert "CVE-" not in row.issue_text.upper().replace("CVE ", "CVE-"), (
            f"{row.id}: issue_text must not contain CVE IDs (paraphrase)"
        )


def test_pilot_corpus_has_at_least_one_symbol_less_row() -> None:
    """Spec: optional culprit_symbol. We must exercise the symbol-less
    path in production data so the scorer's 0.80 cap is real."""
    symbol_less = [r for r in PILOT_CORPUS if r.culprit_symbol is None]
    assert symbol_less, (
        "pilot corpus must include at least one row where the bug is "
        "file-level / config-level (culprit_symbol=None) so the "
        "scorer's symbol-less code path exercises against real data"
    )
