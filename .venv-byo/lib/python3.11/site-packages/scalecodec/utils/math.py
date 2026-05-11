"""Some simple math-related utility functions not present in the standard
library.
"""

from decimal import Decimal
from math import ceil, log2
from typing import TypedDict

try:
    from scalecodec.utils._math import trailing_zeros, next_power_of_two
except ImportError:

    def trailing_zeros(value: int) -> int:
        """Returns the number of trailing zeros in the binary representation of
        the given integer.
        """
        num_zeros = 0
        while value & 1 == 0:
            num_zeros += 1
            value >>= 1
        return num_zeros

    def next_power_of_two(value: int) -> int:
        """Returns the smallest power of two that is greater than or equal
        to the given integer.
        """
        if value < 0:
            raise ValueError("Negative integers not supported")
        return 1 if value == 0 else 1 << ceil(log2(value))


class FixedPoint(TypedDict):
    """
    Represents a fixed point `U64F64` number.
    Where `bits` is a U128 representation of the fixed point number.
    """

    bits: int


class FixedPointV2(TypedDict):
    mantissa: int
    exponent: int


_FixedInput = int | FixedPoint | FixedPointV2


def _is_v2(value: _FixedInput) -> bool:
    return isinstance(value, dict) and "mantissa" in value


def _extract_bits(value: _FixedInput) -> int:
    if isinstance(value, dict):
        if "mantissa" in value:
            raise TypeError(
                "V2 FixedPoint (mantissa/exponent) has no raw bits; "
                "use fixed_to_float or fixed_to_decimal directly."
            )
        return int(value["bits"])
    return int(value)


def fixed_to_float(value: _FixedInput, frac_bits: int = 64) -> float:
    """Decode a fixed-point value to a Python float.

    Supports two input shapes:

    * **V1 (binary Q notation)** — raw integer bits, or ``{'bits': ...}``.
      The default ``frac_bits=64`` corresponds to ``U64F64`` / ``I64F64``.
    * **V2 (decimal mantissa/exponent)** —
      ``{'mantissa': ..., 'exponent': ...}`` representing
      ``mantissa * 10**exponent``. ``frac_bits`` is ignored for V2.

    Parameters
    ----------
    value:
        Raw integer bits, ``{'bits': ...}``, or ``{'mantissa': ..., 'exponent': ...}``.
    frac_bits:
        Number of fractional bits for V1 Q-format inputs. Common values:
        ``64`` for U64F64 / I64F64 (default), ``32`` for U32F32 / I32F32.

    Returns
    -------
    float
    """
    if _is_v2(value):
        return float(Decimal(int(value["mantissa"])).scaleb(int(value["exponent"])))
    bits = _extract_bits(value)
    frac_mask = (1 << frac_bits) - 1
    integer_part = bits >> frac_bits
    fractional_part = bits & frac_mask
    return integer_part + fractional_part / (1 << frac_bits)


def fixed_to_decimal(value: _FixedInput, frac_bits: int = 64) -> Decimal:
    """Decode a fixed-point value to a ``decimal.Decimal``.

    Prefer this over :func:`fixed_to_float` when precision matters (e.g.
    token amounts, prices), since ``float`` cannot represent most fractional
    values exactly.

    Supports two input shapes:

    * **V1 (binary Q notation)** — raw integer bits, or ``{'bits': ...}``.
    * **V2 (decimal mantissa/exponent)** —
      ``{'mantissa': ..., 'exponent': ...}`` representing
      ``mantissa * 10**exponent`` (exact). ``frac_bits`` is ignored for V2.

    Parameters
    ----------
    value:
        Raw integer bits, ``{'bits': ...}``, or ``{'mantissa': ..., 'exponent': ...}``.
    frac_bits:
        Number of fractional bits for V1 Q-format inputs. Defaults to ``64``.

    Returns
    -------
    decimal.Decimal
    """
    if _is_v2(value):
        return Decimal(int(value["mantissa"])).scaleb(int(value["exponent"]))
    bits = _extract_bits(value)
    frac_mask = (1 << frac_bits) - 1
    integer_part = bits >> frac_bits
    fractional_part = bits & frac_mask
    return Decimal(integer_part) + Decimal(fractional_part) / Decimal(1 << frac_bits)
