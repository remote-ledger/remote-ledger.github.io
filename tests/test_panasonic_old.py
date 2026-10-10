"""Panasonic_Old invariants (D18 gate 3, DESIGN section 43).

The decoder is written from the frame layout, not from the encoder: a 4-unit mark and 4-unit space, 22 bits least
significant first (D:5, F:6, ~D:5, ~F:6, a zero a one-unit space and a one a three-unit space), a stop mark and a 44 ms
gap. The space is small enough (32 devices, 64 functions) to round-trip exhaustively.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import PANASONIC_OLD

UNIT = PANASONIC_OLD.unit_us
CARRIER = 57_600


def _encode(device=27, function=28, unit_us=None):
    return PANASONIC_OLD.encode(device=device, subdevice=None, function=function, carrier_hz=CARRIER, unit_us=unit_us)


def _lsb(bits):
    return sum(b << i for i, b in enumerate(bits))


def _decode(repeat, unit=UNIT):
    """``(D, F)`` or None where the frame is not Panasonic_Old's, with the complements checked."""
    if len(repeat) != 48 or (repeat[0], repeat[1]) != (4 * unit, 4 * unit) or repeat[46] != unit:
        return None
    bits = []
    for mark, space in zip(repeat[2:46:2], repeat[3:46:2]):
        if mark != unit or space not in (unit, 3 * unit):
            return None
        bits.append(int(space == 3 * unit))
    d, f, nd, nf = _lsb(bits[:5]), _lsb(bits[5:11]), _lsb(bits[11:16]), _lsb(bits[16:22])
    return (d, f) if nd == (~d & 0x1F) and nf == (~f & 0x3F) else None


def test_registry_metadata():
    assert PANASONIC_OLD.unit_us == 833 and PANASONIC_OLD.nominal_carrier_hz == 57_600
    assert PANASONIC_OLD.extent_us is None and PANASONIC_OLD.bits == 22
    assert "D:5,F:6,~D:5,~F:6" in PANASONIC_OLD.irp


def test_every_device_and_function_round_trips():
    """2,048 frames, exhaustive."""
    for device in range(32):
        for function in range(64):
            assert _decode(_encode(device, function).repeat) == (device, function), (device, function)


def test_there_is_no_intro_and_the_frame_ends_on_the_stop_mark_and_a_44_ms_gap():
    signal = _encode()
    assert signal.intro == () and pronto.parse_words(pronto.encode(signal))[2] == 0
    assert signal.repeat[-2:] == (UNIT, 44_000)


def test_the_fields_are_sent_device_then_function_least_significant_first_and_then_their_complements():
    """A swapped order or bit order fails here and a round trip through the same order would not notice: D = 1 puts
    its one first, and F = 1 puts its one first after D's five bits."""
    pairs = list(zip(_encode(1, 0).repeat[2:46:2], _encode(1, 0).repeat[3:46:2]))
    assert [s for _, s in pairs[:5]] == [3 * UNIT] + [UNIT] * 4                  # D = 1, LSB first
    assert [s for _, s in pairs[5:11]] == [UNIT] * 6                              # F = 0
    pairs = list(zip(_encode(0, 1).repeat[2:46:2], _encode(0, 1).repeat[3:46:2]))
    assert [s for _, s in pairs[5:11]] == [3 * UNIT] + [UNIT] * 5                # F = 1, LSB first
    assert [s for _, s in pairs[11:16]] == [3 * UNIT] * 5                        # ~D = 31


def test_the_unit_scales_the_marks_and_spaces_but_not_the_gap():
    signal = _encode(unit_us=700)
    assert _decode(signal.repeat, unit=700) == (27, 28)
    assert signal.repeat[:2] == (2_800, 2_800) and signal.repeat[-1] == 44_000


@pytest.mark.parametrize("kwargs, match", [
    (dict(device=32), "D:5"), (dict(device=-1), "D:5"), (dict(function=64), "F:6"), (dict(function=-1), "F:6"),
    (dict(subdevice=0), "no subdevice"),
])
def test_out_of_range_fields_are_refused(kwargs, match):
    args = dict(device=1, subdevice=None, function=2, carrier_hz=CARRIER)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        PANASONIC_OLD.encode(**args)
