"""D1/D1a: IrSignal's invariants."""

import pytest

from remote_ledger.errors import BoundsError, ValidationError
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


@pytest.mark.parametrize("bad", [0, -5, 1_000_001])
def test_duration_bounds(bad):
    with pytest.raises(BoundsError):
        IrSignal(carrier_hz=38000, intro=(bad, 564))


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
