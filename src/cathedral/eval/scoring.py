"""First-mover delta scoring helpers (CONTRACTS.md §7.2 + §9 lock #14).

Pure functions — no DB, no async, no I/O. The orchestrator
(`cathedral.eval.scoring_pipeline`) calls these after computing the raw
scoring rubric weights and incumbent-best lookup.

Locked constants per §9 lock #14:
    delta threshold        = 0.05
    penalty multiplier     = 0.50
    first-mover window     = 30 days
    fingerprint window     = 7 days
"""

from __future__ import annotations

#: Above-incumbent delta required to skip the late-mover penalty.
FIRST_MOVER_DELTA: float = 0.05

#: Multiplier applied to a late submission within the protection window.
FIRST_MOVER_PENALTY_MULTIPLIER: float = 0.50

#: Days after the first submission for `(card_id, metadata_fingerprint)`
#: during which late submissions get the penalty.
FIRST_MOVER_WINDOW_DAYS: int = 30

#: Window for fuzzy display-name collision check (§7.1.3).
FIRST_MOVER_FINGERPRINT_WINDOW_DAYS: int = 7


def first_mover_multiplier(
    *,
    is_first_mover: bool,
    weighted_score: float,
    incumbent_best_weighted: float,
    days_since_first: int,
) -> float:
    """Return the multiplier to apply to `weighted_score`.

    Reference (CONTRACTS.md §7.2):
        if is_first_mover:                      -> 1.0
        elif weighted_score >= incumbent + 0.05: -> 1.0
        elif days_since_first > 30:              -> 1.0
        else:                                    -> 0.50
    """
    if is_first_mover:
        return 1.0
    if weighted_score >= incumbent_best_weighted + FIRST_MOVER_DELTA:
        return 1.0
    if days_since_first > FIRST_MOVER_WINDOW_DAYS:
        return 1.0
    return FIRST_MOVER_PENALTY_MULTIPLIER


def apply_first_mover_delta(
    *,
    is_first_mover: bool,
    weighted_score: float,
    incumbent_best_weighted: float,
    days_since_first: int,
) -> float:
    """Convenience wrapper returning the post-multiplier score."""
    mult = first_mover_multiplier(
        is_first_mover=is_first_mover,
        weighted_score=weighted_score,
        incumbent_best_weighted=incumbent_best_weighted,
        days_since_first=days_since_first,
    )
    return weighted_score * mult


__all__ = [
    "FIRST_MOVER_DELTA",
    "FIRST_MOVER_FINGERPRINT_WINDOW_DAYS",
    "FIRST_MOVER_PENALTY_MULTIPLIER",
    "FIRST_MOVER_WINDOW_DAYS",
    "apply_first_mover_delta",
    "first_mover_multiplier",
]
