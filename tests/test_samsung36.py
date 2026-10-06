"""Samsung36 invariants (D18 gate 3).

What anchors the constants to the outside world is the cited golden vectors
(tests/vectors/CITATIONS.md) and the hardware captures audited by
tools/misc_capture_audit.py. These tests catch a broken *frame*.

The decoder below is written from the frame layout, not from the encoder: a
header of two long durations, sixteen bits, a divider whose space is nine
units, twenty bits whose last eight are the complement of the eight before
them, a stop mark, and a gap.
"""

import random

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import SAMSUNG36

UNIT = SAMSUNG36.unit_us
CARRIER = 37_900


def _encode(device=32, subdevice=0, function=0x718, unit_us=None):
    return SAMSUNG36.encode(device=device, subdevice=subdevice, function=function,
                            carrier_hz=CARRIER, unit_us=unit_us)


def _lsb(bits):
    return sum(b << i for i, b in enumerate(bits))


def _decode(repeat, unit=UNIT):
    """``(D, S, function)`` or None when the frame is not Samsung36's.

    Bits are classified against the unit (a space more than twice the mark is
    a one), so a frame with the wrong shape is *rejected*, not misread.
    """
    if len(repeat) != 78:
        return None
    if repeat[0] != 4500 or repeat[1] != 4500:      # 4500u is absolute
        return None
    pairs = list(zip(repeat[0::2], repeat[1::2]))
    body = pairs[1:-1]                              # 16 + divider + 20
    if len(body) != 37:
        return None

    def bit(mark, space):
        if mark != unit or space not in (unit, 3 * unit):
            return None
        return int(space == 3 * unit)

    first = [bit(*p) for p in body[:16]]
    tail = [bit(*p) for p in body[17:]]
    if None in first or None in tail:
        return None
    if body[16] != (unit, 9 * unit):                # the divider
        return None
    if pairs[-1][0] != unit:                        # the stop mark
        return None
    e, f, nf = _lsb(tail[:4]), _lsb(tail[4:12]), _lsb(tail[12:20])
    if nf != (~f & 0xFF):
        return None
    return _lsb(first[:8]), _lsb(first[8:16]), (e << 8) | f


def test_registry_metadata():
    assert SAMSUNG36.unit_us == 560
    assert SAMSUNG36.extent_us == 108_000
    assert SAMSUNG36.bits == 36
    assert SAMSUNG36.nominal_carrier_hz == 37_900
    assert "D:8,S:8,1,-9,E:4,F:8,~F:8" in SAMSUNG36.irp


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_device_subdevice_and_function_round_trips():
    """Each field swept across its whole range against an asymmetric background,
    so a bit-order or field-position error shows at some value. The full space
    is 2^28 frames; the rest is covered by the seeded sample below."""
    for device in range(256):
        assert _decode(_encode(device=device, subdevice=0x5A, function=0x3C6).repeat) == (
            device, 0x5A, 0x3C6)
    for subdevice in range(256):
        assert _decode(_encode(device=0xA5, subdevice=subdevice, function=0x3C6).repeat) == (
            0xA5, subdevice, 0x3C6)
    for function in range(4096):
        assert _decode(_encode(device=0xA5, subdevice=0x5A, function=function).repeat) == (
            0xA5, 0x5A, function)


def test_a_seeded_sample_of_the_whole_space_round_trips():
    rng = random.Random(36)
    for _ in range(20_000):
        d, s, f = rng.randrange(256), rng.randrange(256), rng.randrange(4096)
        assert _decode(_encode(d, s, f).repeat) == (d, s, f), (d, s, f)


def test_the_frame_pads_to_its_extent_from_the_header():
    for d, s, f in ((0, 0, 0), (255, 255, 4095), (32, 0, 0x718), (1, 128, 1)):
        assert sum(_encode(d, s, f).repeat) == SAMSUNG36.extent_us


def test_function_is_the_extension_nibble_over_the_command_byte():
    """The ledger's packing (module docstring): E is function >> 8, F the low
    byte, and ~F complements F alone."""
    repeat = _encode(function=(0xB << 8) | 0x24).repeat
    pairs = list(zip(repeat[0::2], repeat[1::2]))[1:-1]
    tail = [int(s == 3 * UNIT) for _, s in pairs[17:]]
    assert _lsb(tail[:4]) == 0xB
    assert _lsb(tail[4:12]) == 0x24
    assert _lsb(tail[12:20]) == 0x24 ^ 0xFF


def test_the_extension_is_what_stops_the_command_byte_complementing():
    """Negative control: set function's top nibble and F's complement must not
    change. A packing that XORed E into ~F would."""
    low = _decode(_encode(function=0x024).repeat)
    high = _decode(_encode(function=0xF24).repeat)
    assert low[2] & 0xFF == high[2] & 0xFF == 0x24


def test_the_decoder_rejects_a_broken_frame():
    good = list(_encode().repeat)
    assert _decode(good) is not None
    broken_complement = list(good)
    # [-1] is the gap, [-2] the stop mark, [-3] the space of ~F's last bit
    broken_complement[-3] = 3 * UNIT if broken_complement[-3] == UNIT else UNIT
    assert _decode(broken_complement) is None       # ~F no longer complements F
    no_divider = list(good)
    no_divider[34 + 1] = UNIT
    assert _decode(no_divider) is None
    assert _decode(good[:-2]) is None


def test_the_header_is_absolute_so_unit_us_scales_the_rest_only():
    """``4500u`` is microseconds; ``1,-9`` and the bits are units. A 500 us
    unit is what the SwiftRemote app and the audited captures use."""
    repeat = _encode(unit_us=500).repeat
    assert repeat[:2] == (4500, 4500)
    assert _decode(repeat, unit=500) == (32, 0, 0x718)
    assert (500, 4500) in list(zip(repeat[0::2], repeat[1::2]))   # the divider
    assert sum(repeat) == SAMSUNG36.extent_us


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "006D"             # 37.9 kHz
    assert (words[2], words[3]) == (0, 39)         # repeat only, 39 pairs


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=256), "D:8 holds 0-255"),
        (dict(device=-1), "D:8 holds 0-255"),
        (dict(subdevice=256), "S:8 holds 0-255"),
        (dict(subdevice=-1), "S:8 holds 0-255"),
        (dict(subdevice=None), "requires an explicit subdevice"),
        (dict(function=4096), "holds 0-4095"),
        (dict(function=-1), "holds 0-4095"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)
