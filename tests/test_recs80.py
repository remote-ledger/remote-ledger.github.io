"""RECS80 and RECS80-0068 invariants (D18 gate 3), and the published assertions.

IrpTransmogrifierNGTest.java @ c945e76 asserts two decodes for RECS80:

* ``testDecodeRecs80Junk`` (L333-L336): eleven exact durations decode, strict,
  as ``RECS80: {D=6,F=56,T=1}``. This encoder must reproduce them to the
  microsecond, which is a stronger check than the Pronto vectors (those are
  renders, not published strings).
* ``testDecodeRecs80Multiple`` (L348-L351): a real capture, with the jitter and
  30 ms gaps of a real remote, decodes to ``D=2,F=1`` with ``T=1`` and then
  ``T=0``. Only the bit pattern is comparable.

The decoder is written from the frame layout, not from the encoder.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import RECS80, RECS80_0068

#: (protocol, unit, carrier)
BOTH = [(RECS80, 158, 38_000), (RECS80_0068, 180, 33_300)]
IDS = ["RECS80", "RECS80-0068"]

#: IrpTransmogrifierNGTest.java L333, verbatim, signs and all.
PUBLISHED_RAW = (
    "+158 -7426 +158 -7426 +158 -7426 +158 -7426 +158 -4898 +158 -7426 "
    "+158 -7426 +158 -7426 +158 -4898 +158 -4898 +158 -4898 +158 -45000"
)

#: IrpTransmogrifierNGTest.java L348, verbatim: five frames of a real capture.
PUBLISHED_CAPTURE = (
    "+200 -7300 +200 -7350 +150 -4850 +200 -7350 +150 -4850 +200 -4800 +200 -4850 +150 -4850 "
    "+200 -4800 +200 -4850 +150 -7350 +200 -30100 +150 -7350 +200 -7350 +150 -4850 +200 -7300 "
    "+200 -4850 +150 -4850 +200 -4800 +200 -4850 +150 -4850 +150 -4850 +200 -7350 +150 -30100 "
    "+150 -7350 +200 -4800 +200 -4850 +150 -7350 +200 -4800 +200 -4850 +150 -4850 +200 -4800 "
    "+200 -4850 +150 -4850 +150 -7350 +200 -30100 +200 -7350 +150 -4850 +200 -4800 +200 -7350 "
    "+150 -4850 +200 -4800 +200 -4850 +150 -4850 +200 -4800 +200 -4850 +150 -7350 +200 -30100 "
    "+200 -7300 +200 -4850 +150 -4850 +200 -7350 +150 -4850 +150 -4850 +200 -4800 +200 -4850 "
    "+150 -4850 +200 -4800 +200 -7350 +150 -30100"
)


def _durations(text):
    return [abs(int(t)) for t in text.split()]


def _encode(proto=RECS80, device=6, function=56, toggle=0, unit_us=None):
    carrier = dict((p.name, c) for p, _, c in BOTH)[proto.name]
    return proto.encode(device=device, subdevice=None, function=function,
                        carrier_hz=carrier, toggle=toggle, unit_us=unit_us)


def _bits_of(repeat, unit):
    """The eleven bits of a frame, or None. A space near 31 units is a zero,
    near 47 a one; anything else is not a RECS80 bit."""
    if len(repeat) != 24:
        return None
    bits = []
    for mark, space in zip(repeat[0:22:2], repeat[1:22:2]):
        ratio = space / unit
        if ratio > 39:
            bits.append(1)
        elif ratio > 23:
            bits.append(0)
        else:
            return None
    return bits


def _fields(bits):
    """``(start, T, D, F)`` from eleven MSB-first bits."""
    value = lambda b: int("".join(map(str, b)), 2)
    return bits[0], bits[1], value(bits[2:5]), value(bits[5:11])


@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_registry_metadata(proto, unit, carrier):
    assert proto.unit_us == unit
    assert proto.nominal_carrier_hz == carrier
    assert proto.bits == 11
    assert "1:1,T:1,D:3,F:6,1," in proto.irp


def test_the_two_differ_in_unit_carrier_and_how_they_end():
    assert RECS80.extent_us is None            # -45m is a plain gap
    assert RECS80_0068.extent_us == 138_000    # ^138m pads the frame
    assert _encode(RECS80).repeat[-1] == 45_000
    assert sum(_encode(RECS80_0068).repeat) == 138_000


@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_there_is_no_intro_sequence(proto, unit, carrier):
    signal = _encode(proto)
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_every_device_function_and_toggle_round_trips(proto, unit, carrier):
    """8 x 64 x 2 = 1,024 frames, exhaustive, for each protocol."""
    for device in range(8):
        for function in range(64):
            for toggle in (0, 1):
                repeat = _encode(proto, device, function, toggle).repeat
                assert _fields(_bits_of(repeat, unit)) == (1, toggle, device, function), (
                    device, function, toggle)


@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_bits_are_msb_first_with_a_constant_start_bit(proto, unit, carrier):
    bits = _bits_of(_encode(proto, device=0b100, function=0b000001).repeat, unit)
    assert bits == [1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1]


@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_toggle_changes_only_the_toggle_bit(proto, unit, carrier):
    a = _bits_of(_encode(proto, toggle=0).repeat, unit)
    b = _bits_of(_encode(proto, toggle=1).repeat, unit)
    assert (a[1], b[1]) == (0, 1)
    assert a[:1] + a[2:] == b[:1] + b[2:]


@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_a_file_cannot_set_the_toggle_so_it_defaults_to_zero(proto, unit, carrier):
    """D3b, as for RC5: ``toggle`` is for the tests; the IRP's default is T=0."""
    plain = proto.encode(device=6, subdevice=None, function=56, carrier_hz=carrier)
    assert plain == _encode(proto, toggle=0)


def test_the_published_decode_assertion_is_reproduced_to_the_microsecond():
    """IrpTransmogrifierNGTest L333-L336: these durations are RECS80
    {D=6,F=56,T=1}."""
    published = _durations(PUBLISHED_RAW)
    assert list(_encode(RECS80, device=6, function=56, toggle=1).repeat) == published
    assert _fields(_bits_of(published, 158)) == (1, 1, 6, 56)


def test_the_published_capture_decodes_to_the_published_fields():
    """L348-L351: five frames, two groups: D=2,F=1 with T=1 (frames 0-1 are one
    press repeated), then T=0. The capture is jittery, so compare bits."""
    durations = _durations(PUBLISHED_CAPTURE)
    frames = [durations[i:i + 24] for i in range(0, len(durations), 24)]
    assert len(frames) == 5
    decoded = [_fields(_bits_of(f, 158)) for f in frames]
    assert decoded == [(1, 1, 2, 1)] * 2 + [(1, 0, 2, 1)] * 3
    for (_, toggle, device, function), frame in zip(decoded, frames):
        assert _bits_of(_encode(RECS80, device, function, toggle).repeat, 158) == \
            _bits_of(frame, 158)


def test_the_decoder_rejects_a_broken_frame():
    good = list(_encode(RECS80).repeat)
    assert _bits_of(good, 158) is not None
    assert _bits_of(good[:-2], 158) is None
    bad = list(good)
    bad[1] = 10 * 158                              # neither 31 nor 47 units
    assert _bits_of(bad, 158) is None


@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_canonical_words(proto, unit, carrier):
    words = pronto.parse_words(pronto.encode(_encode(proto)))
    assert (words[2], words[3]) == (0, 12)
    assert set(words[4:-1:2]) == {6}               # the one-unit marks, 158 / 180 us


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=8), "D:3 holds 0-7"),
        (dict(device=-1), "D:3 holds 0-7"),
        (dict(function=64), "F:6 holds 0-63"),
        (dict(function=-1), "F:6 holds 0-63"),
        (dict(toggle=2), "T:1 is 0 or 1"),
    ],
)
@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_out_of_range_fields_are_refused(proto, unit, carrier, kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(proto, **kwargs)


@pytest.mark.parametrize("proto, unit, carrier", BOTH, ids=IDS)
def test_a_subdevice_is_refused_not_ignored(proto, unit, carrier):
    with pytest.raises(EncodeError, match="no subdevice"):
        proto.encode(device=6, subdevice=3, function=56, carrier_hz=carrier)
