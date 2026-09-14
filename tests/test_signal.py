"""D1/D1a: IrSignal's invariants."""

import pytest

from remote_ledger.errors import BoundsError, ValidationError
from remote_ledger.numeric import RAW_DURATION_US_MAX, SIGNAL_DURATION_US_MAX
from remote_ledger.signal import IrSignal


def test_minimal_signal():
    s = IrSignal(carrier_hz=38000, intro=(564, 564))
    assert s.sequences == ((564, 564), ())


def test_repeat_only_signal_is_valid():
    """Every Sony variant has no intro -- D6 rule 6 emits NNNN = 0000."""
    s = IrSignal(carrier_hz=40000, repeat=(2400, 600))
    assert s.intro == ()


@pytest.mark.parametrize("carrier", [0, -1, 9_999, 500_001])
def test_carrier_must_be_in_bounds(carrier):
    """D1a: carrier_hz = 0 was a representable state with no defined encoding."""
    with pytest.raises(BoundsError, match="carrierHz"):
        IrSignal(carrier_hz=carrier, intro=(564, 564))


@pytest.mark.parametrize("carrier", [10_000, 38_000, 500_000])
def test_carrier_bounds_are_inclusive(carrier):
    IrSignal(carrier_hz=carrier, intro=(564, 564))


def test_odd_length_sequence_rejected():
    with pytest.raises(ValidationError, match="even length"):
        IrSignal(carrier_hz=38000, intro=(564, 564, 564))


def test_empty_signal_rejected():
    with pytest.raises(ValidationError, match=r"n1 \+ n2 >= 1"):
        IrSignal(carrier_hz=38000)


def test_ending_is_reserved_and_rejected():
    """A non-empty ending would be dropped on emission rather than encoded."""
    with pytest.raises(ValidationError, match="ending is reserved"):
        IrSignal(carrier_hz=38000, intro=(564, 564), ending=(564, 564))


@pytest.mark.parametrize("bad", [0, -5, SIGNAL_DURATION_US_MAX + 1])
def test_duration_bounds(bad):
    with pytest.raises(BoundsError):
        IrSignal(carrier_hz=38000, intro=(bad, 564))


def test_signal_ceiling_allows_one_requantization_above_the_authoring_bound():
    """Two-tier bound, deliberately.

    ``RAW_DURATION_US_MAX`` bounds what a person may *author* (the schema
    enforces it). ``SIGNAL_DURATION_US_MAX`` bounds what may sit inside an
    IrSignal, which includes values that came back through D25's lossy
    cycles -> microseconds step: a duration authored at exactly 1_000_000
    returns as 1_000_004, and rejecting that would break the round-trip
    property for no reason.
    """
    assert SIGNAL_DURATION_US_MAX > RAW_DURATION_US_MAX
    IrSignal(carrier_hz=38000, intro=(RAW_DURATION_US_MAX, 564))
    IrSignal(carrier_hz=38000, intro=(RAW_DURATION_US_MAX + 4, 564))


def test_sequences_are_coerced_to_tuples():
    """A raw form's durations arrive from json.loads as lists.

    Left as a list they defeat ``frozen=True``, make the signal unhashable,
    and make two signals that encode to the identical Pronto string compare
    unequal -- which would break D8's cross-check in Phase 2.
    """
    from_list = IrSignal(carrier_hz=38000, intro=[564, 564])
    from_tuple = IrSignal(carrier_hz=38000, intro=(564, 564))
    assert isinstance(from_list.intro, tuple)
    assert from_list == from_tuple
    assert hash(from_list) == hash(from_tuple)
    assert len({from_list, from_tuple}) == 1


def test_reserved_ending_reports_the_real_reason_not_the_shape():
    """An odd-length ending is reserved-field misuse, not a shape error."""
    with pytest.raises(ValidationError, match="ending is reserved"):
        IrSignal(carrier_hz=38000, intro=(564, 564), ending=(564,))


def test_sequence_length_cap():
    with pytest.raises(ValidationError, match="caps a sequence"):
        IrSignal(carrier_hz=38000, intro=tuple([564] * 2050))


def test_bool_is_not_a_duration():
    with pytest.raises(ValidationError, match="integer microseconds"):
        IrSignal(carrier_hz=38000, intro=(True, 564))


def test_signal_is_frozen():
    s = IrSignal(carrier_hz=38000, intro=(564, 564))
    with pytest.raises(Exception):
        s.carrier_hz = 40000  # type: ignore[misc]
