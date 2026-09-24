"""D20's serialization contract and D28's parse rules."""

from decimal import Decimal

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.serialize import SOURCE_KEY_ORDER, dumps, loads, order_keys


def test_floats_parse_as_decimal_from_the_literal_text():
    """D28: 0.15 as a binary float would drag D8 onto binary rounding."""
    doc = loads('{"relative": 0.15}')
    assert isinstance(doc["relative"], Decimal)
    assert doc["relative"] == Decimal("0.15")
    assert doc["relative"] != Decimal(0.15)  # the binary approximation


def test_ints_stay_ints():
    assert isinstance(loads('{"carrierHz": 38000}')["carrierHz"], int)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_literals_rejected_at_parse_time(literal):
    """Every minimum/maximum comparison against NaN is silently false."""
    with pytest.raises(ValidationError, match="non-finite"):
        loads('{"relative": %s}' % literal)


def test_dumps_emits_decimals_as_canonical_unquoted_numbers():
    text = dumps({"relative": Decimal("0.150"), "absUs": 100})
    assert '"relative": 0.15' in text
    assert '"0.15"' not in text


def test_dumps_is_byte_stable_for_equal_values():
    a = dumps({"x": Decimal("1.0")})
    b = dumps({"x": Decimal("1.00")})
    c = dumps({"x": Decimal("1")})
    assert a == b == c


def test_dumps_round_trips_through_loads():
    original = {"relative": Decimal("0.15"), "absUs": 100, "name": "NEC1"}
    assert loads(dumps(original)) == original
    assert dumps(loads(dumps(original))) == dumps(original)


def test_dumps_sorts_object_keys():
    assert list(loads(dumps({"b": 1, "a": 2}))) == ["a", "b"]


def test_dumps_never_reorders_arrays():
    """D20: array order is semantic -- D7's tie-break is array position."""
    forms = [{"id": "z"}, {"id": "a"}]
    assert loads(dumps({"forms": forms}))["forms"] == forms


def test_dumps_ends_with_exactly_one_newline():
    text = dumps({"a": 1})
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_dumps_keeps_non_ascii_unescaped():
    assert "µs" in dumps({"note": "564 µs"})


def test_order_keys_puts_declared_keys_first_and_unknown_last():
    """D20's fallback keeps rl fmt total when a schema field is added."""
    form = {"zzz_future": 1, "source": "s", "type": "irp", "device": 1}
    assert list(order_keys("form", form)) == [
        "type", "device", "source", "zzz_future"
    ]


def test_order_keys_is_deterministic_for_several_unknown_keys():
    form = {"b_new": 1, "a_new": 2, "type": "irp"}
    assert list(order_keys("form", form)) == ["type", "a_new", "b_new"]


def test_declared_orders_cover_the_documented_object_kinds():
    assert set(SOURCE_KEY_ORDER) == {
        "remote", "protocol", "form", "variant", "layout"
    }


def test_integer_arrays_serialize_on_one_line():
    """D40 amends D20: a raw sequence is one line, not one number per line,
    which roughly halves an imported remote's size. Order is untouched."""
    text = dumps({"repeat": [9024, 4512, 564, 1692], "flags": [True, 1]})
    assert '"repeat": [9024, 4512, 564, 1692]' in text
    # Booleans are not integers here, so a mixed array keeps json's layout.
    assert '"flags": [\n' in text
    assert loads(text) == {"repeat": [9024, 4512, 564, 1692], "flags": [True, 1]}
