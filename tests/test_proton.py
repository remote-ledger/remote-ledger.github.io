"""Proton invariants (D18 gate 3).

The decoder is written from the frame layout, not from the encoder: a 16-unit
mark and 8-unit space, eight bits, a one-unit mark with an 8-unit space, eight
bits, a stop mark and a gap. The space is small enough (65,536 frames) to
round-trip exhaustively.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import PROTON

UNIT = PROTON.unit_us
CARRIER = 38_500


def _encode(device=20, function=1, unit_us=None):
    return PROTON.encode(device=device, subdevice=None, function=function,
                         carrier_hz=CARRIER, unit_us=unit_us)


def _lsb(bits):
    return sum(b << i for i, b in enumerate(bits))


def _decode(repeat, unit=UNIT):
    """``(D, F)`` or None when the frame is not Proton's."""
    if len(repeat) != 38:
        return None
    pairs = list(zip(repeat[0::2], repeat[1::2]))
    if pairs[0] != (16 * unit, 8 * unit):
        return None
    body = pairs[1:-1]                               # 8 bits, divider, 8 bits
    if body[8] != (unit, 8 * unit) or pairs[-1][0] != unit:
        return None

    def bit(mark, space):
        if mark != unit or space not in (unit, 3 * unit):
            return None
        return int(space == 3 * unit)

    d = [bit(*p) for p in body[:8]]
    f = [bit(*p) for p in body[9:]]
    if None in d or None in f:
        return None
    return _lsb(d), _lsb(f)


def test_registry_metadata():
    assert PROTON.unit_us == 500
    assert PROTON.extent_us == 63_000
    assert PROTON.bits == 16
    assert PROTON.nominal_carrier_hz == 38_500
    assert "D:8,1,-8,F:8" in PROTON.irp


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_device_and_function_round_trips():
    """65,536 frames, exhaustive."""
    for device in range(256):
        for function in range(256):
            got = _decode(_encode(device, function).repeat)
            assert got == (device, function), (device, function)


def test_the_frame_pads_to_its_extent_from_the_first_mark():
    for d, f in ((0, 0), (255, 255), (20, 1), (0x12, 0x35)):
        assert sum(_encode(d, f).repeat) == PROTON.extent_us


def test_device_is_sent_first_and_function_second():
    """The IRP's order, and the real capture's (CITATIONS.md). A swapped
    encoder fails here and the round trip alone would not notice."""
    repeat = _encode(device=0x01, function=0x00).repeat
    pairs = list(zip(repeat[0::2], repeat[1::2]))[1:-1]
    assert [s for _, s in pairs[:8]] == [3 * UNIT] + [UNIT] * 7     # D, LSB first
    assert [s for _, s in pairs[9:]] == [UNIT] * 8                  # then F


def test_unit_us_scales_everything_but_the_extent():
    repeat = _encode(unit_us=530).repeat
    assert _decode(repeat, unit=530) == (20, 1)
    assert sum(repeat) == PROTON.extent_us


def test_the_decoder_rejects_a_broken_frame():
    good = list(_encode().repeat)
    assert _decode(good) is not None
    assert _decode(good[:-2]) is None
    no_divider = list(good)
    no_divider[19] = UNIT
    assert _decode(no_divider) is None
    wrong_lead = list(good)
    wrong_lead[0] = 15 * UNIT
    assert _decode(wrong_lead) is None


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "006C"              # 38.5 kHz
    assert (words[2], words[3]) == (0, 19)


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=256), "D:8 holds 0-255"),
        (dict(device=-1), "D:8 holds 0-255"),
        (dict(function=256), "F:8 holds 0-255"),
        (dict(function=-1), "F:8 holds 0-255"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="no subdevice"):
        PROTON.encode(device=20, subdevice=3, function=1, carrier_hz=CARRIER)
