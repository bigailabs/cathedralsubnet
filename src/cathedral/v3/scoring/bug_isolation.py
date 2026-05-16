"""Static scorer for bug_isolation_v1 claims.

Pure function: claim dict + hidden oracle row -> ScoreParts +
weighted_score. No I/O, no network, no subprocess. Safe to run on
Railway.

Weights (spec section 8):

- ``culprit_file`` normalized path match: 35%
- ``culprit_symbol`` normalized match:    20%
- ``line_range`` IoU vs hidden:           25%
- ``failure_mode`` keyword match:         20%

Rules:

- Paths normalize via ``_normalize_path``: strip leading ``./``,
  unify separators, lowercase, strip trailing slashes.
- Symbols normalize via ``_normalize_symbol``: strip whitespace,
  lowercase. (We deliberately do NOT collapse ``ClassName.method``
  to ``method``; partial-class matches are out of scope for v3.0.)
- ``culprit_symbol`` is optional. When the oracle's
  ``culprit_symbol`` is None, the symbol slice scores 0 and the
  composite is capped at 0.80 so a file-level bug cannot reach a
  perfect score. When the oracle has a symbol but the claim
  doesn't, the symbol slice scores 0 and the cap does NOT apply
  (the miner just lost the 20%).
- Line IoU is intersection-over-union on inclusive ranges. The
  predicted span is capped at ``_MAX_PREDICTED_SPAN`` lines before
  IoU so whole-file guesses cannot max the slice.
- Failure-mode keyword match: hidden ``required_failure_keywords``
  is a tuple. The miner's ``failure_mode`` string must contain at
  least ``ceil(n/2)`` keywords as case-insensitive substrings,
  with a floor of 1. Below threshold scores 0; at threshold scores
  fraction matched / n.

Shadow metrics (latency penalty, cluster dedup) are computed
elsewhere and never multiplied into the signed score in v3.0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Whole-file guesses cap. 80 lines is the budget; predicted spans
# longer than this are truncated for IoU purposes only (the claim
# itself is stored unchanged).
_MAX_PREDICTED_SPAN: int = 80

# Composite cap when the oracle doesn't have a symbol (file-level
# / config-level bugs). Spec: max total score = 0.80.
_NO_SYMBOL_COMPOSITE_CAP: float = 0.80

# Per-dimension weights. Must sum to 1.0.
_W_FILE: float = 0.35
_W_SYMBOL: float = 0.20
_W_LINES: float = 0.25
_W_FAILURE: float = 0.20

assert math.isclose(_W_FILE + _W_SYMBOL + _W_LINES + _W_FAILURE, 1.0)


@dataclass(frozen=True)
class BugIsolationScoreParts:
    """Per-dimension scores in [0, 1] and the composite weighted score.

    The composite is what lands in the signed payload's
    ``weighted_score`` field; ``parts`` lands in ``score_parts`` so
    validators and the public feed can see how the composite
    decomposed.
    """

    culprit_file: float
    culprit_symbol: float
    line_range: float
    failure_mode: float
    weighted_score: float

    def to_parts_dict(self) -> dict[str, float]:
        return {
            "culprit_file": round(self.culprit_file, 4),
            "culprit_symbol": round(self.culprit_symbol, 4),
            "line_range": round(self.line_range, 4),
            "failure_mode": round(self.failure_mode, 4),
        }


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------


def _normalize_path(p: str | None) -> str:
    if not p:
        return ""
    s = p.strip().replace("\\", "/").lower()
    if s.startswith("./"):
        s = s[2:]
    return s.rstrip("/")


def _normalize_symbol(s: str | None) -> str:
    if not s:
        return ""
    return s.strip().lower()


# --------------------------------------------------------------------------
# Per-dimension scorers
# --------------------------------------------------------------------------


def _score_file(claim_path: str | None, oracle_path: str) -> float:
    return 1.0 if _normalize_path(claim_path) == _normalize_path(oracle_path) else 0.0


def _score_symbol(claim_symbol: str | None, oracle_symbol: str | None) -> float:
    # Scoring matrix:
    #   oracle is None:  symbol slice contributes 0; composite cap applies upstream
    #   oracle present, claim missing/None:  0
    #   oracle present, claim present:  1.0 iff normalized strings match
    if oracle_symbol is None:
        return 0.0
    if not claim_symbol:
        return 0.0
    return 1.0 if _normalize_symbol(claim_symbol) == _normalize_symbol(oracle_symbol) else 0.0


def _score_line_iou(
    claim_range: tuple[int, int] | list[int] | None,
    oracle_range: tuple[int, int],
) -> float:
    if claim_range is None or len(claim_range) != 2:
        return 0.0
    try:
        c_start, c_end = int(claim_range[0]), int(claim_range[1])
        o_start, o_end = int(oracle_range[0]), int(oracle_range[1])
    except (TypeError, ValueError):
        return 0.0
    if c_start > c_end or o_start > o_end:
        return 0.0
    # Cap predicted span so a whole-file guess cannot dominate.
    if (c_end - c_start + 1) > _MAX_PREDICTED_SPAN:
        # Keep the start, truncate the end. The cap is symmetric
        # in spirit (we don't try to guess which half was the
        # "real" guess) but anchoring at start is deterministic.
        c_end = c_start + _MAX_PREDICTED_SPAN - 1
    inter_start = max(c_start, o_start)
    inter_end = min(c_end, o_end)
    if inter_start > inter_end:
        return 0.0
    inter = inter_end - inter_start + 1
    union = max(c_end, o_end) - min(c_start, o_start) + 1
    return inter / union if union > 0 else 0.0


def _score_failure_mode(
    claim_failure_mode: str | None,
    required_keywords: tuple[str, ...],
) -> float:
    if not claim_failure_mode or not required_keywords:
        return 0.0
    body = claim_failure_mode.lower()
    matches = sum(1 for kw in required_keywords if kw.lower() in body)
    n = len(required_keywords)
    # Threshold: ceil(n/2) with a floor of 1.
    threshold = max(1, math.ceil(n / 2))
    if matches < threshold:
        return 0.0
    return matches / n


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------


def score_bug_isolation_claim(
    *,
    claim: dict[str, Any],
    oracle_culprit_file: str,
    oracle_culprit_symbol: str | None,
    oracle_line_range: tuple[int, int],
    oracle_required_keywords: tuple[str, ...],
) -> BugIsolationScoreParts:
    """Score a parsed claim against the hidden oracle.

    The caller is responsible for validating the claim's shape and
    for matching ``claim['challenge_id']`` to the oracle. This
    function does NOT trust ``claim['challenge_id']``; it scores
    whatever fields it gets and lets the caller refuse if the
    challenge_id mismatches.

    Caller is also responsible for malformed-claim handling: if
    parsing failed upstream, do not call this; just emit a zero
    composite with reason ``malformed_claim``.
    """
    file_score = _score_file(claim.get("culprit_file"), oracle_culprit_file)
    symbol_score = _score_symbol(claim.get("culprit_symbol"), oracle_culprit_symbol)
    line_score = _score_line_iou(claim.get("line_range"), oracle_line_range)
    failure_score = _score_failure_mode(
        claim.get("failure_mode"), oracle_required_keywords
    )

    composite = (
        _W_FILE * file_score
        + _W_SYMBOL * symbol_score
        + _W_LINES * line_score
        + _W_FAILURE * failure_score
    )

    if oracle_culprit_symbol is None:
        composite = min(composite, _NO_SYMBOL_COMPOSITE_CAP)

    return BugIsolationScoreParts(
        culprit_file=file_score,
        culprit_symbol=symbol_score,
        line_range=line_score,
        failure_mode=failure_score,
        weighted_score=round(composite, 4),
    )
