"""Static scorer tests for bug_isolation_v1.

Locks the weight table, the symbol-optional 0.80 cap, the IoU span
cap, and the ceil(n/2) keyword threshold. These rules ship to
mainnet via the signed payload; regressions here are mainnet bugs.
"""

from __future__ import annotations

import pytest

from cathedral.v3.scoring.bug_isolation import (
    _MAX_PREDICTED_SPAN,
    _NO_SYMBOL_COMPOSITE_CAP,
    score_bug_isolation_claim,
)


# --------------------------------------------------------------------------
# Reference oracle and a "perfect" claim that matches every field
# --------------------------------------------------------------------------


def _oracle() -> dict:
    return {
        "culprit_file": "src/foo/bar.py",
        "culprit_symbol": "Bar.method",
        "line_range": (100, 120),
        "required_keywords": ("off-by-one", "loop", "boundary"),
    }


def _perfect_claim() -> dict:
    return {
        "challenge_id": "ch_x",
        "culprit_file": "src/foo/bar.py",
        "culprit_symbol": "Bar.method",
        "line_range": [100, 120],
        "failure_mode": "off-by-one loop boundary at end of range",
    }


def _score(claim: dict, oracle: dict | None = None):
    o = oracle or _oracle()
    return score_bug_isolation_claim(
        claim=claim,
        oracle_culprit_file=o["culprit_file"],
        oracle_culprit_symbol=o["culprit_symbol"],
        oracle_line_range=o["line_range"],
        oracle_required_keywords=o["required_keywords"],
    )


# --------------------------------------------------------------------------
# Happy path + per-dimension weight math
# --------------------------------------------------------------------------


def test_perfect_claim_scores_one() -> None:
    s = _score(_perfect_claim())
    assert s.culprit_file == 1.0
    assert s.culprit_symbol == 1.0
    assert s.line_range == 1.0
    assert s.failure_mode == 1.0
    assert s.weighted_score == 1.0


def test_only_file_matches_yields_35_percent() -> None:
    claim = {
        "culprit_file": "src/foo/bar.py",
        "culprit_symbol": "TotallyWrong",
        "line_range": [1, 2],
        "failure_mode": "unrelated",
    }
    s = _score(claim)
    assert s.weighted_score == 0.35


def test_only_symbol_matches_yields_20_percent() -> None:
    claim = {
        "culprit_file": "src/wrong.py",
        "culprit_symbol": "Bar.method",
        "line_range": [1, 2],
        "failure_mode": "unrelated",
    }
    s = _score(claim)
    assert s.weighted_score == 0.20


def test_only_lines_match_iou_one_yields_25_percent() -> None:
    claim = {
        "culprit_file": "src/wrong.py",
        "culprit_symbol": "Wrong",
        "line_range": [100, 120],
        "failure_mode": "unrelated",
    }
    s = _score(claim)
    assert s.weighted_score == 0.25


def test_only_failure_mode_full_match_yields_20_percent() -> None:
    claim = {
        "culprit_file": "src/wrong.py",
        "culprit_symbol": "Wrong",
        "line_range": [1, 2],
        "failure_mode": "off-by-one in the loop crossing the boundary",
    }
    s = _score(claim)
    assert s.weighted_score == 0.20


# --------------------------------------------------------------------------
# Path / symbol normalization
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim_path",
    [
        "src/foo/bar.py",
        "./src/foo/bar.py",
        "SRC/Foo/Bar.py",
        "src\\foo\\bar.py",
        "src/foo/bar.py/",
    ],
)
def test_file_path_normalizations_match(claim_path: str) -> None:
    claim = _perfect_claim() | {"culprit_file": claim_path}
    s = _score(claim)
    assert s.culprit_file == 1.0


def test_symbol_whitespace_and_case_normalized() -> None:
    claim = _perfect_claim() | {"culprit_symbol": "  bar.method  "}
    s = _score(claim)
    assert s.culprit_symbol == 1.0


# --------------------------------------------------------------------------
# Line IoU
# --------------------------------------------------------------------------


def test_disjoint_line_ranges_score_zero() -> None:
    claim = _perfect_claim() | {"line_range": [200, 220]}
    s = _score(claim)
    assert s.line_range == 0.0


def test_partial_line_overlap_iou() -> None:
    # oracle = 100-120 (21 lines), claim = 110-125 (16 lines)
    # intersection 110-120 = 11, union 100-125 = 26 -> ~0.423
    claim = _perfect_claim() | {"line_range": [110, 125]}
    s = _score(claim)
    assert 0.40 < s.line_range < 0.45


def test_whole_file_guess_is_capped_before_iou() -> None:
    """A miner claiming lines [1, 10000] must not max the line slice.
    The predicted span is capped to _MAX_PREDICTED_SPAN before IoU."""
    claim = _perfect_claim() | {"line_range": [1, 100000]}
    s = _score(claim)
    # After cap, claim becomes [1, _MAX_PREDICTED_SPAN]. Oracle is
    # [100, 120]. With the cap at 80 lines (1-80), there's no
    # overlap with 100-120 at all.
    assert _MAX_PREDICTED_SPAN < 100  # sanity: cap doesn't reach oracle
    assert s.line_range == 0.0


def test_line_range_inverted_scores_zero() -> None:
    claim = _perfect_claim() | {"line_range": [200, 100]}
    s = _score(claim)
    assert s.line_range == 0.0


def test_line_range_wrong_length_scores_zero() -> None:
    claim = _perfect_claim() | {"line_range": [100, 110, 120]}
    s = _score(claim)
    assert s.line_range == 0.0


def test_line_range_missing_scores_zero() -> None:
    claim = {k: v for k, v in _perfect_claim().items() if k != "line_range"}
    s = _score(claim)
    assert s.line_range == 0.0


# --------------------------------------------------------------------------
# Failure-mode keyword threshold
# --------------------------------------------------------------------------


def test_failure_mode_meets_threshold_2_of_3() -> None:
    """3 required keywords -> ceil(3/2) = 2 threshold. 2 of 3
    matches scores 2/3 = ~0.667."""
    claim = _perfect_claim() | {"failure_mode": "off-by-one loop bug"}
    s = _score(claim)
    assert abs(s.failure_mode - 2 / 3) < 1e-6


def test_failure_mode_below_threshold_scores_zero() -> None:
    """1 of 3 matches falls under ceil(3/2)=2 threshold -> 0."""
    claim = _perfect_claim() | {"failure_mode": "off-by-one"}
    s = _score(claim)
    assert s.failure_mode == 0.0


def test_failure_mode_ceil_half_threshold_for_4_keywords() -> None:
    """4 keywords -> ceil(4/2) = 2 threshold. Use distinctive
    keywords that don't accidentally appear inside other words."""
    oracle = _oracle() | {
        "required_keywords": ("zappy", "yellow", "quokka", "vivid")
    }
    claim = _perfect_claim() | {"failure_mode": "zappy bug in the yellow path"}
    s = _score(claim, oracle)
    assert s.failure_mode == 2 / 4


def test_failure_mode_ceil_half_threshold_for_5_keywords() -> None:
    """5 keywords -> ceil(5/2) = 3 threshold. 2 matches -> 0; 3 -> 0.6."""
    oracle = _oracle() | {
        "required_keywords": ("zappy", "yellow", "quokka", "vivid", "amber")
    }
    claim_below = _perfect_claim() | {"failure_mode": "zappy yellow path"}
    claim_at = _perfect_claim() | {"failure_mode": "zappy yellow quokka"}
    assert _score(claim_below, oracle).failure_mode == 0.0
    assert abs(_score(claim_at, oracle).failure_mode - 0.6) < 1e-6


def test_failure_mode_single_keyword_floor_at_one() -> None:
    """1 keyword -> max(1, ceil(1/2)) = 1 threshold."""
    oracle = _oracle() | {"required_keywords": ("crash",)}
    claim = _perfect_claim() | {"failure_mode": "crash on startup"}
    s = _score(claim, oracle)
    assert s.failure_mode == 1.0


def test_failure_mode_case_insensitive() -> None:
    claim = _perfect_claim() | {"failure_mode": "OFF-BY-ONE in the LOOP at the BOUNDARY"}
    s = _score(claim)
    assert s.failure_mode == 1.0


# --------------------------------------------------------------------------
# Optional symbol + 0.80 composite cap
# --------------------------------------------------------------------------


def test_oracle_none_symbol_caps_composite_at_080() -> None:
    """When oracle.culprit_symbol is None, even a perfect file +
    line + failure_mode cannot exceed 0.80. Symbol slice scores 0."""
    oracle = _oracle() | {"culprit_symbol": None}
    claim = _perfect_claim() | {"culprit_symbol": "whatever"}  # ignored
    s = _score(claim, oracle)
    assert s.culprit_symbol == 0.0
    # file 0.35 + lines 0.25 + failure 0.20 = 0.80
    assert s.weighted_score == _NO_SYMBOL_COMPOSITE_CAP


def test_oracle_none_symbol_below_cap_unaffected() -> None:
    """The cap only clips; if composite is already below 0.80 the
    cap doesn't change anything."""
    oracle = _oracle() | {"culprit_symbol": None}
    claim = {
        "culprit_file": "src/wrong.py",
        "culprit_symbol": "X",
        "line_range": [100, 120],
        "failure_mode": "unrelated",
    }
    s = _score(claim, oracle)
    # only lines match: 0.25
    assert s.weighted_score == 0.25


def test_oracle_has_symbol_no_cap_applied() -> None:
    """When oracle has a symbol and miner doesn't provide one, the
    miner loses 20% but the 0.80 cap does NOT apply (because the
    oracle wasn't symbol-less)."""
    claim = {
        "culprit_file": "src/foo/bar.py",
        # no culprit_symbol key at all
        "line_range": [100, 120],
        "failure_mode": "off-by-one loop boundary",
    }
    s = _score(claim)
    # 0.35 + 0 + 0.25 + 0.20 = 0.80, coincidence with the cap value
    # but the cap is NOT what's producing it; verify by raising another
    # slice and seeing the score exceed 0.80.
    assert s.culprit_symbol == 0.0
    assert s.weighted_score == 0.80
    # Sanity: with symbol present (oracle has symbol), perfect = 1.0
    perfect_with_oracle_symbol = _score(_perfect_claim())
    assert perfect_with_oracle_symbol.weighted_score == 1.0


# --------------------------------------------------------------------------
# Missing / malformed claim fields
# --------------------------------------------------------------------------


def test_empty_claim_scores_zero() -> None:
    s = _score({})
    assert s.weighted_score == 0.0


def test_claim_with_none_fields_scores_zero() -> None:
    claim = {
        "culprit_file": None,
        "culprit_symbol": None,
        "line_range": None,
        "failure_mode": None,
    }
    s = _score(claim)
    assert s.weighted_score == 0.0
