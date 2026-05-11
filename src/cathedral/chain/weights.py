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


def apply_burn(
    scores: list[tuple[int, float]],
    *,
    burn_uid: int,
    forced_burn_percentage: float,
) -> list[tuple[int, float]]:
    """Allocate forced_burn_percentage of total weight to burn_uid.

    Pre-normalize: returns a vector of (uid, weight) where burn_uid carries
    forced_burn_percentage/100 of the total mass, and the remaining mass is
    split proportionally across the original scores. Caller still passes the
    result through `normalize` to renormalize to sum=1.0 and drop bad values.

    If scores is empty (or sums to non-positive), returns [(burn_uid, 1.0)] so
    the validator still emits weights (all to burn).

    forced_burn_percentage must be in [0.0, 100.0].
    """
    if not 0.0 <= forced_burn_percentage <= 100.0:
        raise ValueError(
            f"forced_burn_percentage must be in [0.0, 100.0], got {forced_burn_percentage}"
        )

    burn_frac = forced_burn_percentage / 100.0
    miner_frac = 1.0 - burn_frac

    cleaned = [(uid, s) for uid, s in scores if math.isfinite(s) and s > 0 and uid != burn_uid]
    total = sum(s for _, s in cleaned)
    if total <= 0:
        return [(burn_uid, 1.0)]

    out = [(uid, (s / total) * miner_frac) for uid, s in cleaned]
    out.append((burn_uid, burn_frac))
    return out
