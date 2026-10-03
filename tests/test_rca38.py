"""RCA-38 invariants (D18 gate 3).

What anchors the constants to the outside world is the cited evidence
(tests/vectors/CITATIONS.md): IrpTransmogrifier 1.2.14's rendering of one
signal (a reproducible Pronto vector) and a hardware capture of an RCA remote
(checked by ``tools/philips_capture_audit.py``, which needs a checkout this
suite does not carry).

The decoder below is written from the frame layout, not from the encoder.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import RCA38

UNIT = RCA38.unit_us
CARRIER = 38_700


def _decode(repeat, unit=UNIT):
    """A frame back to (device, function), or None.

    ``repeat`` is mark/space durations, the gap last. Anything that is not an
    8,-8 lead-in, twenty-four bits each a one-unit mark and a two- or four-unit
    space, a one-unit stop mark and a sixteen-unit gap returns None, as does a
    frame whose complement half is not the complement of its first.
    """
    if len(repeat) != 2 + 48 + 2:
        return None
    units = [round(d / unit) for d in repeat]
    if units[:2] != [8, 8] or units[-2:] != [1, 16]:
        return None
    bits = []
    for mark, space in zip(units[2:-2:2], units[3:-2:2]):
        if mark != 1 or space not in (2, 4):
            return None
        bits.append(0 if space == 2 else 1)
    value = int("".join(map(str, bits)), 2)
    device, function = value >> 20, (value >> 12) & 0xFF
    if (value >> 8) & 0xF != ~device & 0xF or value & 0xFF != ~function & 0xFF:
        return None
    return device, function


def _encode(device=15, function=144):
    return RCA38.encode(device=device, subdevice=None, function=function,
                        carrier_hz=CARRIER)


def test_registry_metadata():
    assert RCA38.name == "RCA-38"
    assert RCA38.unit_us == 460
    assert RCA38.extent_us is None, "the IRP ends -16, not an extent"
    assert RCA38.bits == 24
    assert RCA38.nominal_carrier_hz == 38_700
    assert "~D:4,~F:8" in RCA38.irp


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_address_and_command_round_trips():
    """4,096 frames, all of them."""
    for device in range(16):
        for function in range(256):
            assert _decode(_encode(device, function).repeat) == (device, function), (
                device, function)


def test_every_frame_has_twelve_ones_and_so_one_length():
    """The complement half balances the ones, so the frame is always 129
    units, 59,340 us, with its fixed gap."""
    for device in range(16):
        for function in range(0, 256, 5):
            assert sum(_encode(device, function).repeat) == 129 * UNIT == 59_340


def test_the_frame_ends_with_a_stop_mark_and_a_sixteen_unit_gap():
    repeat = _encode().repeat
    assert repeat[-2:] == (UNIT, 16 * UNIT) == (460, 7_360)
    assert repeat[:2] == (8 * UNIT, 8 * UNIT)


def test_bit_order_is_most_significant_first():
    """D=8 is 1000 on the air, so its first bit's space is the long one."""
    assert _encode(device=8, function=0).repeat[3] == 4 * UNIT
    assert _encode(device=1, function=0).repeat[3] == 2 * UNIT
    # and the command: F=0x80 sets the first bit after the address nibble
    assert _encode(device=0, function=0x80).repeat[3 + 2 * 4] == 4 * UNIT


def test_the_second_half_is_the_complement_of_the_first():
    repeat = _encode(device=5, function=0xA5).repeat
    bits = [0 if s == 2 * UNIT else 1 for s in repeat[3:-2:2]]
    assert bits[12:] == [1 - b for b in bits[:12]]


def test_a_bad_frame_is_rejected_by_the_decoder():
    """The complement is what makes the decoder a check: break one bit."""
    repeat = list(_encode().repeat)
    index = 3 + 2 * 14           # a bit of the complement half
    repeat[index] = 6 * UNIT - repeat[index]   # 2 <-> 4 units
    assert _decode(tuple(repeat)) is None


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "006B"            # 38.7 kHz
    assert words[3] == 0x1A                       # 26 burst pairs


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=16), "D:4 holds 0-15"),
        (dict(device=-1), "D:4 holds 0-15"),
        (dict(function=256), "F:8 holds 0-255"),
        (dict(function=-1), "F:8 holds 0-255"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="no subdevice"):
        RCA38.encode(device=15, subdevice=3, function=144, carrier_hz=CARRIER)
