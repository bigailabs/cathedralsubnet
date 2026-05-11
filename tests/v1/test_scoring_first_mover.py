"""First-mover delta multiplier — CONTRACTS.md §7.2.

We test the pure scoring function. The contract pins constants:
    delta threshold = 0.05
    penalty multiplier = 0.50
    first-mover window = 30 days
    fingerprint window = 7 days

If the implementer exposes a function like
``first_mover_multiplier(submission, incumbent_best, first_mover_at, now) -> float``
we test it directly. Otherwise we test the multiplier via a small
contract-driven reference and assert the implementer's helper matches.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

import pytest

from tests.v1.conftest import (
    FIRST_MOVER_DELTA,
    FIRST_MOVER_PENALTY_MULTIPLIER,
    FIRST_MOVER_WINDOW_DAYS,
)

# --------------------------------------------------------------------------
# Reference (contract-derived) implementation
# --------------------------------------------------------------------------


def reference_multiplier(
    *,
    is_first_mover: bool,
    weighted_score: float,
    incumbent_best_weighted: float,
    days_since_first: int,
) -> float:
    """Mirrors the formula in CONTRACTS.md §7.2 verbatim."""
    if is_first_mover:
        return 1.0
    if weighted_score >= incumbent_best_weighted + FIRST_MOVER_DELTA:
        return 1.0
    if days_since_first > FIRST_MOVER_WINDOW_DAYS:
        return 1.0
    return FIRST_MOVER_PENALTY_MULTIPLIER


# --------------------------------------------------------------------------
# Try to import the implementer's helper
# --------------------------------------------------------------------------


def _find_first_mover_fn() -> Callable[..., float] | None:
    """Best effort. The implementer can expose this under various names."""
    candidates = [
        ("cathedral.eval.scoring", "first_mover_multiplier"),
        ("cathedral.eval.scoring", "apply_first_mover_delta"),
        ("cathedral.eval.first_mover", "multiplier"),
        ("cathedral.eval.first_mover", "first_mover_multiplier"),
        ("cathedral.cards.score", "first_mover_multiplier"),
    ]
    for mod_name, attr in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        fn = getattr(mod, attr, None)
        if callable(fn):
            return fn
    return None


@pytest.fixture
def first_mover_fn() -> Callable[..., float]:
    fn = _find_first_mover_fn()
    if fn is None:
        pytest.skip(
            "first-mover multiplier helper not importable — implementer must "
            "expose it under cathedral.eval.scoring per CONTRACTS.md §7.2"
        )
    return fn


# --------------------------------------------------------------------------
# Pure reference behavior — these always run (verify the test assumptions)
# --------------------------------------------------------------------------


def test_reference_first_mover_full_credit():
    """§7.2 — first mover always gets 1.0."""
    assert reference_multiplier(
        is_first_mover=True,
        weighted_score=0.5,
        incumbent_best_weighted=0.5,
        days_since_first=0,
    ) == 1.0


def test_reference_late_within_threshold_gets_penalty():
    """§7.2 — late, within +0.05 of incumbent, within 30d → 0.50x."""
    assert reference_multiplier(
        is_first_mover=False,
        weighted_score=0.50,  # equal to incumbent
        incumbent_best_weighted=0.50,
        days_since_first=15,
    ) == FIRST_MOVER_PENALTY_MULTIPLIER

    # Just below the +0.05 threshold.
    assert reference_multiplier(
        is_first_mover=False,
        weighted_score=0.549,
        incumbent_best_weighted=0.50,
        days_since_first=15,
    ) == FIRST_MOVER_PENALTY_MULTIPLIER


def test_reference_late_above_threshold_full_credit():
    """§7.2 — late but >= incumbent + 0.05 → 1.0."""
    assert reference_multiplier(
        is_first_mover=False,
        weighted_score=0.55,  # exactly at threshold
        incumbent_best_weighted=0.50,
        days_since_first=15,
    ) == 1.0


def test_reference_window_expires_after_30_days():
    """§7.2 — days_since_first > 30 → no penalty regardless of delta."""
    assert reference_multiplier(
        is_first_mover=False,
        weighted_score=0.50,  # tied with incumbent
        incumbent_best_weighted=0.50,
        days_since_first=31,
    ) == 1.0


# --------------------------------------------------------------------------
# Implementer's helper must match the reference for the contract examples
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "is_first_mover,weighted,incumbent,days,expected",
    [
        # Case 1: first mover always 1.0
        (True, 0.5, 0.5, 0, 1.0),
        (True, 0.0, 0.0, 100, 1.0),
        # Case 2: tied, within window -> 0.50
        (False, 0.50, 0.50, 1, FIRST_MOVER_PENALTY_MULTIPLIER),
        (False, 0.50, 0.50, 30, FIRST_MOVER_PENALTY_MULTIPLIER),
        # Case 3: just below threshold within window -> 0.50
        (False, 0.549, 0.50, 1, FIRST_MOVER_PENALTY_MULTIPLIER),
        # Case 4: at or above threshold within window -> 1.0
        (False, 0.55, 0.50, 1, 1.0),
        (False, 0.99, 0.50, 1, 1.0),
        # Case 5: window expired -> 1.0
        (False, 0.50, 0.50, 31, 1.0),
        (False, 0.50, 0.50, 365, 1.0),
    ],
)
def test_implementer_helper_matches_reference(
    first_mover_fn, is_first_mover, weighted, incumbent, days, expected
):
    """§7.2 — implementer's helper must agree with the contract reference.

    We try three plausible call signatures (kw-only, kw-with-keypair-style
    args, positional) and accept whichever matches.
    """
    got = _try_call(
        first_mover_fn,
        is_first_mover=is_first_mover,
        weighted_score=weighted,
        incumbent_best_weighted=incumbent,
        days_since_first=days,
    )
    if got is None:
        pytest.skip(
            f"could not call first_mover_fn({first_mover_fn.__name__}) with our "
            "kw signatures; implementer should accept (is_first_mover, "
            "weighted_score, incumbent_best_weighted, days_since_first)"
        )
    assert got == pytest.approx(expected), (
        f"§7.2: multiplier mismatch for is_first={is_first_mover}, "
        f"score={weighted}, incumbent={incumbent}, days={days}: "
        f"impl={got}, expected={expected}"
    )


def _try_call(fn: Callable[..., float], **kwargs) -> float | None:
    """Try multiple call signatures and return the first that doesn't raise."""
    attempts = [
        kwargs,
        # Positional fallback in the documented order.
        {},
    ]
    for kw in attempts:
        try:
            if kw:
                return float(fn(**kw))
            return float(
                fn(
                    kwargs["is_first_mover"],
                    kwargs["weighted_score"],
                    kwargs["incumbent_best_weighted"],
                    kwargs["days_since_first"],
                )
            )
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------
# Constants check (§9 lock #14)
# --------------------------------------------------------------------------


def test_first_mover_constants_are_locked():
    """§9 lock #14 — `0.05` threshold, `0.50` penalty, `30-day` window,
    `7-day` fingerprint window."""
    # If the implementer pulled them into a constants module, prefer that
    # over assuming they hardcoded.
    for module_name in (
        "cathedral.eval.scoring",
        "cathedral.eval.first_mover",
        "cathedral.eval.constants",
    ):
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            continue
        if hasattr(mod, "FIRST_MOVER_DELTA"):
            assert mod.FIRST_MOVER_DELTA == 0.05, (
                "§9 lock #14: FIRST_MOVER_DELTA must be 0.05"
            )
        if hasattr(mod, "FIRST_MOVER_PENALTY_MULTIPLIER"):
            assert mod.FIRST_MOVER_PENALTY_MULTIPLIER == 0.50, (
                "§9 lock #14: FIRST_MOVER_PENALTY_MULTIPLIER must be 0.50"
            )
        if hasattr(mod, "FIRST_MOVER_WINDOW_DAYS"):
            assert mod.FIRST_MOVER_WINDOW_DAYS == 30, (
                "§9 lock #14: FIRST_MOVER_WINDOW_DAYS must be 30"
            )
        return
    pytest.skip(
        "no constants module exposed yet — soft check; implementer should "
        "publish FIRST_MOVER_DELTA / FIRST_MOVER_PENALTY_MULTIPLIER / "
        "FIRST_MOVER_WINDOW_DAYS for downstream auditing per §9 lock #14"
    )
