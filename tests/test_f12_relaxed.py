"""F12_relaxed invariants (D18 gate 3).

The decoder is written from the frame layout: twelve four-unit bits, a zero a
narrow mark and wide space and a one the reverse, with ``-80`` merged into the
last space. All 4,096 frames round-trip.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import F12_RELAXED

UNIT = F12_RELAXED.unit_us
CARRIER = 37_900


def _encode(device=3, subdevice=1, function=33, unit_us=None):
    return F12_RELAXED.encode(device=device, subdevice=subdevice, function=function,
                              carrier_hz=CARRIER, unit_us=unit_us)


def _lsb(bits):
    return sum(b << i for i, b in enumerate(bits))


def _decode(repeat, unit=UNIT):
    """``(D, S, F)`` or None when the frame is not F12's."""
    if len(repeat) != 24:
        return None
    pairs = list(zip(repeat[0::2], repeat[1::2]))
    bits = []
    for i, (mark, space) in enumerate(pairs):
        if i == 11:
            space -= 80 * unit                      # the merged -80
        if (mark, space) == (3 * unit, unit):
            bits.append(1)
        elif (mark, space) == (unit, 3 * unit):
            bits.append(0)
        else:
            return None
    return _lsb(bits[:3]), bits[3], _lsb(bits[4:])


def test_registry_metadata():
    assert F12_RELAXED.unit_us == 422
    assert F12_RELAXED.bits == 12
    assert F12_RELAXED.nominal_carrier_hz == 37_900
    assert "D:3,S:1,F:8,-80" in F12_RELAXED.irp
    assert F12_RELAXED.irp.startswith("{37.9k,422}<1,-3|3,-1>")


def test_there_is_no_extent_because_the_gap_is_not_one():
    """``-80`` is a plain gap, not ``^``, so D31 falls back to defaultGapUs."""
    assert F12_RELAXED.extent_us is None


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_device_subdevice_and_function_round_trips():
    """3 x 1 x 8 bits = 4,096 frames, exhaustive."""
    for device in range(8):
        for subdevice in range(2):
            for function in range(256):
                got = _decode(_encode(device, subdevice, function).repeat)
                assert got == (device, subdevice, function), (device, subdevice, function)


def test_every_bit_is_four_units_and_the_gap_is_eighty_more():
    for d, s, f in ((0, 0, 0), (7, 1, 255), (3, 1, 33)):
        repeat = _encode(d, s, f).repeat
        assert sum(repeat) == (12 * 4 + 80) * UNIT


def test_the_gap_merges_into_the_last_bits_own_space():
    """After a zero the last space is 3 + 80 units, after a one 1 + 80."""
    zero_last = _encode(function=0).repeat
    one_last = _encode(function=0x80).repeat
    assert zero_last[-1] == 83 * UNIT == 35_026
    assert one_last[-1] == 81 * UNIT == 34_182


def test_the_decoder_rejects_a_broken_frame():
    good = list(_encode().repeat)
    assert _decode(good) is not None
    bad = list(good)
    bad[0] = 2 * UNIT                              # neither a narrow nor a wide mark
    assert _decode(bad) is None
    assert _decode(good[:-2]) is None
    unmerged = list(good)
    unmerged[-1] -= 80 * UNIT
    assert _decode(unmerged) is None               # the -80 must be there


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "006D"             # 37.9 kHz
    assert (words[2], words[3]) == (0, 12)


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=8), "D:3 holds 0-7"),
        (dict(device=-1), "D:3 holds 0-7"),
        (dict(subdevice=2), "S:1 holds 0-1"),
        (dict(subdevice=-1), "S:1 holds 0-1"),
        (dict(subdevice=None), "requires an explicit subdevice"),
        (dict(function=256), "F:8 holds 0-255"),
        (dict(function=-1), "F:8 holds 0-255"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)
