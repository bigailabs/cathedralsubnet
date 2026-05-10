import math

from cathedral.chain import normalize


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
