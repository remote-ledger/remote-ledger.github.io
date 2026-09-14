"""Pinned decimal arithmetic and D28's numeric bounds.

Everything here exists because "byte-reproducible" (D20) and "byte-identical
Pronto output" (R12, D12) are only true if the arithmetic cannot drift. Two
distinct hazards, handled separately:

* **Rounding mode.** Python's ``round()`` is banker's rounding, so ``x.5``
  cases depend on parity. D6 rule 5 requires ``ROUND_HALF_UP`` on ``Decimal``.
* **Ambient context.** ``decimal`` reads precision and rounding from a
  *process-wide* context any imported library may mutate. D28 therefore pins
  a local context for arithmetic, and — see ``canon_decimal`` — avoids the
  context entirely for serialization.
"""

from __future__ import annotations

import decimal
from contextlib import contextmanager
from decimal import Decimal
from typing import Iterator

# D6 rule 2: the Pronto base clock, as a string-constructed Decimal so no
# float ever enters the calculation.
PRONTO_CLOCK_US = Decimal("0.241246")

# D28: pinned locally on every use; never read from decimal.getcontext().
DECIMAL_PRECISION = 34
DECIMAL_ROUNDING = decimal.ROUND_HALF_UP

# --- D28 bounds -------------------------------------------------------------
# Real IR carriers are 30-60 kHz. This is a wide margin that still catches a
# units mistake (38 where 38000 was meant).
CARRIER_HZ_MIN, CARRIER_HZ_MAX = 10_000, 500_000
# Four hex digits, and never zero: a burst that rounds away is a real failure.
FREQ_WORD_MIN, FREQ_WORD_MAX = 1, 0xFFFF
BURST_CYCLES_MIN, BURST_CYCLES_MAX = 1, 0xFFFF
PAIR_COUNT_MIN, PAIR_COUNT_MAX = 0, 0xFFFF
RAW_DURATION_US_MIN, RAW_DURATION_US_MAX = 1, 1_000_000
# Duration entries -- `n` in D31's notation -- not Pronto burst pairs, of
# which this is 1024.
MAX_DURATIONS_PER_SEQUENCE = 2048
UNIT_US_MIN, UNIT_US_MAX = 1, 10_000
DEFAULT_GAP_US_MIN, DEFAULT_GAP_US_MAX = 1, 1_000_000
MIN_SENDS_MIN, MIN_SENDS_MAX = 1, 10
TOLERANCE_ABS_US_MIN, TOLERANCE_ABS_US_MAX = 0, 10_000
# Past 50% the check stops distinguishing a wrong code from a right one.
TOLERANCE_RELATIVE_MIN, TOLERANCE_RELATIVE_MAX = Decimal(0), Decimal("0.5")
TOLERANCE_GAP_US_MIN, TOLERANCE_GAP_US_MAX = 0, 1_000_000


@contextmanager
def decimal_context() -> Iterator[decimal.Context]:
    """Pin precision and rounding locally for the duration of a calculation.

    D28. Never use ``decimal.getcontext()`` directly -- its state is global
    and writable by anything in the process.
    """
    with decimal.localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        ctx.rounding = DECIMAL_ROUNDING
        yield ctx


def round_half_up(value: Decimal) -> int:
    """Round to the nearest integer, halves away from zero (D6 rule 5)."""
    with decimal_context():
        return int(value.quantize(Decimal(1), rounding=decimal.ROUND_HALF_UP))


def canon_decimal(d: Decimal) -> str:
    """Serialize a Decimal canonically, independent of the ambient context.

    D20/D28. The guarantee is *canonical, value-stable* output, not lexical
    preservation: numerically equal values always produce identical bytes, and
    a second pass is a no-op.

    This deliberately does **not** use ``Decimal.normalize()``. ``normalize()``
    rounds to the ambient precision, and at Python's default ``prec = 28`` it
    silently turns a 39-digit value into ``0.1`` -- data loss inside a function
    whose whole job is preserving a value. Operating on the coefficient from
    ``as_tuple()`` cannot round: ``as_tuple()`` reads stored values, the
    ``Decimal((sign, digits, exp))`` constructor applies no context, and
    ``format(..., "f")`` without a precision is context-independent.

        >>> [canon_decimal(Decimal(x)) for x in ("1.0", "1.00", "1e-2", "-0")]
        ['1', '1', '0.01', '0']
    """
    sign, digits, exp = d.as_tuple()
    if not isinstance(exp, int):  # 'n', 'N', 'F' -> NaN / sNaN / Infinity
        raise ValueError(f"non-finite Decimal cannot be serialized: {d!r}")
    if not digits or not any(digits):
        return "0"  # collapses -0, 0.00, 0E+9 alike
    coefficient = list(digits)
    while exp < 0 and coefficient[-1] == 0:
        coefficient.pop()
        exp += 1
    return format(Decimal((sign, tuple(coefficient), exp)), "f")


def check_bounds(name: str, value: int | Decimal, low, high) -> None:
    """Raise BoundsError unless low <= value <= high (D28)."""
    from .errors import BoundsError

    if not (low <= value <= high):
        raise BoundsError(
            f"{name} is {value}; D28 requires {low} <= {name} <= {high}"
        )
