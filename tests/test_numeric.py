"""D28: the arithmetic must not depend on the ambient decimal context."""

import decimal
from decimal import Decimal

import pytest

from remote_ledger.numeric import canon_decimal, decimal_context, round_half_up

HOSTILE_PRECISIONS = (1, 3, 9, 28, 34, 99)
HOSTILE_ROUNDINGS = (
    decimal.ROUND_DOWN,
    decimal.ROUND_UP,
    decimal.ROUND_HALF_EVEN,
    decimal.ROUND_CEILING,
    decimal.ROUND_FLOOR,
)

CANON_CASES = {
    "0.15": "0.15",
    "1.0": "1",
    "1.00": "1",
    "1": "1",
    "1e-2": "0.01",
    "1.0E+2": "100",
    "100": "100",
    "12.340": "12.34",
    "-2.50": "-2.5",
    "0.5": "0.5",
    "-0": "0",
    "-0.0": "0",
    "0.00": "0",
    # The case that exposed Decimal.normalize(): at Python's DEFAULT prec=28
    # normalize() returns 0.1 for this, silently destroying 38 digits.
    "0.100000000000000000000000000000000000001":
        "0.100000000000000000000000000000000000001",
    "123456789012345678901234567890.5": "123456789012345678901234567890.5",
}


@pytest.fixture
def hostile_context():
    """Leave a deliberately bad process-wide context in place."""
    saved = decimal.getcontext()
    decimal.setcontext(decimal.Context(prec=3, rounding=decimal.ROUND_DOWN))
    yield
    decimal.setcontext(saved)


@pytest.mark.parametrize("raw,expected", CANON_CASES.items())
def test_canon_decimal_values(raw, expected):
    assert canon_decimal(Decimal(raw)) == expected


@pytest.mark.parametrize("raw,expected", CANON_CASES.items())
def test_canon_decimal_ignores_hostile_context(raw, expected, hostile_context):
    """The whole point of D28's rewrite: prec=3/ROUND_DOWN changes nothing."""
    assert canon_decimal(Decimal(raw)) == expected


def test_canon_decimal_identical_across_every_context():
    for prec in HOSTILE_PRECISIONS:
        for rounding in HOSTILE_ROUNDINGS:
            with decimal.localcontext() as ctx:
                ctx.prec = prec
                ctx.rounding = rounding
                got = {k: canon_decimal(Decimal(k)) for k in CANON_CASES}
            assert got == CANON_CASES, f"drifted at prec={prec} {rounding}"


def test_canon_decimal_is_idempotent():
    """"A second `rl fmt` is a no-op" (D20) -- as a test, not an assertion."""
    for raw in CANON_CASES:
        once = canon_decimal(Decimal(raw))
        assert canon_decimal(Decimal(once)) == once


def test_canon_decimal_equal_values_are_byte_identical():
    for group in (("1.0", "1.00", "1", "1.000"), ("-0", "0.00", "0", "-0.0")):
        rendered = {canon_decimal(Decimal(x)) for x in group}
        assert len(rendered) == 1, f"{group} rendered as {rendered}"


def test_canon_decimal_rejects_non_finite():
    for bad in ("NaN", "Infinity", "-Infinity", "sNaN"):
        with pytest.raises(ValueError, match="non-finite"):
            canon_decimal(Decimal(bad))


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0.5", 1), ("1.5", 2), ("2.5", 3),   # half-UP, not banker's
        ("-0.5", -1), ("-1.5", -2),
        ("0.49", 0), ("109.0828", 109), ("171.588", 172), ("21.4483", 21),
    ],
)
def test_round_half_up(value, expected):
    assert round_half_up(Decimal(value)) == expected


def test_round_half_up_differs_from_builtin_round():
    """D6 rule 5: builtin round() is banker's rounding and would flip on parity."""
    assert round_half_up(Decimal("2.5")) == 3
    assert round(Decimal("2.5")) == 2


def test_round_half_up_ignores_hostile_context(hostile_context):
    assert round_half_up(Decimal("171.588")) == 172


def test_decimal_context_is_local():
    saved_prec = decimal.getcontext().prec
    with decimal_context() as ctx:
        assert ctx.prec == 34
        assert ctx.rounding == decimal.ROUND_HALF_UP
    assert decimal.getcontext().prec == saved_prec
