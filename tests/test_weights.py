import math

import pytest

from cathedral.chain import apply_burn, normalize


def test_normalize_basic() -> None:
    out = normalize([(0, 1.0), (1, 1.0), (2, 2.0)])
    total = sum(w for _, w in out)
    assert math.isclose(total, 1.0)


def test_normalize_zero_returns_empty() -> None:
    assert normalize([(0, 0.0), (1, 0.0)]) == []


def test_normalize_drops_negative_and_nan() -> None:
    out = normalize([(0, -1.0), (1, math.nan), (2, 1.0)])
    weights = {uid: w for uid, w in out}
    assert math.isclose(weights[2], 1.0)
    assert weights[0] == 0.0
    assert weights[1] == 0.0


def test_apply_burn_single_miner_98_percent() -> None:
    out = apply_burn([(1, 1.0)], burn_uid=204, forced_burn_percentage=98.0)
    weights = dict(out)
    assert math.isclose(weights[1], 0.02)
    assert math.isclose(weights[204], 0.98)
    assert math.isclose(sum(weights.values()), 1.0)


def test_apply_burn_empty_scores_all_burn() -> None:
    out = apply_burn([], burn_uid=204, forced_burn_percentage=98.0)
    assert out == [(204, 1.0)]


def test_apply_burn_zero_percentage_passthrough() -> None:
    out = apply_burn([(1, 1.0), (2, 1.0)], burn_uid=204, forced_burn_percentage=0.0)
    weights = dict(out)
    assert math.isclose(weights[1], 0.5)
    assert math.isclose(weights[2], 0.5)
    assert math.isclose(weights.get(204, 0.0), 0.0)


def test_apply_burn_full_percentage_all_to_burn() -> None:
    out = apply_burn([(1, 1.0), (2, 5.0)], burn_uid=204, forced_burn_percentage=100.0)
    weights = dict(out)
    assert math.isclose(weights[204], 1.0)
    assert math.isclose(weights.get(1, 0.0), 0.0)
    assert math.isclose(weights.get(2, 0.0), 0.0)


def test_apply_burn_proportional_split_of_remainder() -> None:
    out = apply_burn(
        [(1, 1.0), (2, 3.0)],
        burn_uid=204,
        forced_burn_percentage=98.0,
    )
    weights = dict(out)
    assert math.isclose(weights[1], 0.02 * 0.25)
    assert math.isclose(weights[2], 0.02 * 0.75)
    assert math.isclose(weights[204], 0.98)


def test_apply_burn_excludes_burn_uid_from_miner_scores() -> None:
    out = apply_burn(
        [(204, 5.0), (1, 1.0)],
        burn_uid=204,
        forced_burn_percentage=98.0,
    )
    weights = dict(out)
    assert math.isclose(weights[1], 0.02)
    assert math.isclose(weights[204], 0.98)


def test_apply_burn_invalid_percentage_raises() -> None:
    with pytest.raises(ValueError):
        apply_burn([(1, 1.0)], burn_uid=204, forced_burn_percentage=-1.0)
    with pytest.raises(ValueError):
        apply_burn([(1, 1.0)], burn_uid=204, forced_burn_percentage=101.0)


def test_apply_burn_then_normalize_sums_to_one() -> None:
    burned = apply_burn(
        [(1, 1.0), (2, 1.0)],
        burn_uid=204,
        forced_burn_percentage=98.0,
    )
    out = normalize(burned)
    assert math.isclose(sum(w for _, w in out), 1.0)


def test_apply_burn_filters_nan_and_negative_scores() -> None:
    out = apply_burn(
        [(1, math.nan), (2, -1.0), (3, 1.0)],
        burn_uid=204,
        forced_burn_percentage=98.0,
    )
    weights = dict(out)
    assert math.isclose(weights[3], 0.02)
    assert math.isclose(weights[204], 0.98)
    assert 1 not in weights
    assert 2 not in weights
