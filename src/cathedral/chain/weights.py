"""Convert raw scores into a weight vector that sums to 1.0."""

from __future__ import annotations

import math


def normalize(scores: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """Drop NaN/negative, then normalize so weights sum to 1.0.

    Returns an empty list if total is non-positive — caller should NOT call
    `set_weights` in that case.
    """
    cleaned = [(uid, s if math.isfinite(s) and s > 0 else 0.0) for uid, s in scores]
    total = sum(s for _, s in cleaned)
    if total <= 0:
        return []
    return [(uid, s / total) for uid, s in cleaned]
