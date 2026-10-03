"""RC6 invariants (D18 gate 3).

Like RC5's, the framing tests catch a broken *frame*; what anchors the
constants to the outside world is the cited vectors (tests/vectors/
CITATIONS.md): two published Pronto strings and three published raw
sequences, the last reproduced here to the microsecond.

The decoder below is written from the frame layout, not from the encoder. It
reads half-units, so the double-width trailer bit is told apart from two
ordinary bits only by where it sits.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import RC6

UNIT = RC6.unit_us


def _decode(repeat, unit=UNIT):
    """A frame back to (start, mode, trailer, device, function), or None.

    ``repeat`` is mark/space durations ending in the gap. The leader must be
    exactly six units of mark and two of space; after it come the start bit,
    three mode bits, the double-width trailer and sixteen data bits, each
    biphase (a 1 is mark then space). Anything else returns None.
    """
    levels = []
    for i, duration in enumerate(repeat[:-1]):
        levels += [1 if i % 2 == 0 else 0] * round(duration / unit)
    if levels[:8] != [1] * 6 + [0] * 2:
        return None
    body = levels[8:]
    pos = 0
    bits = []

    def take(width):
        nonlocal pos
        chunk = body[pos:pos + 2 * width]
        if len(chunk) < 2 * width:
            # a last bit of 1 ends on a space, which the extent lengthened
            # into the final element, so the gap holds that half-bit
            chunk = chunk + [0] * (2 * width - len(chunk))
        first, second = chunk[:width], chunk[width:]
        if len(set(first)) != 1 or len(set(second)) != 1 or first[0] == second[0]:
            return None
        pos += 2 * width
        return 1 if first[0] == 1 else 0

    # start, three of mode, the double-width trailer, then sixteen data bits
    for width in (1, 1, 1, 1, 2) + (1,) * 16:
        bit = take(width)
        if bit is None:
            return None
        bits.append(bit)
    start, mode, trailer = bits[0], bits[1] * 4 + bits[2] * 2 + bits[3], bits[4]
    device = int("".join(map(str, bits[5:13])), 2)
    function = int("".join(map(str, bits[13:21])), 2)
    return start, mode, trailer, device, function


def _encode(device=0, function=12, toggle=0):
    return RC6.encode(device=device, subdevice=None, function=function,
                      carrier_hz=36_000, toggle=toggle)


def test_registry_metadata():
    assert RC6.unit_us == 444
    assert RC6.extent_us == 107_000
    assert RC6.bits == 21
    assert RC6.nominal_carrier_hz == 36_000
    assert "<-2,2|2,-2>(T:1),D:8,F:8" in RC6.irp
    assert "0:3" in RC6.irp, "mode 0 only; RC6-6-20 and the RC6-M-* family are not registered"


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_address_command_and_toggle_round_trips():
    """131,072 frames. Exhaustive, because the space is small and the
    failure modes -- a swapped nibble, a toggle that is not double width --
    sit at specific values a sample would skip."""
    for device in range(256):
        for function in range(256):
            for toggle in (0, 1):
                got = _decode(_encode(device, function, toggle).repeat)
                assert got == (1, 0, toggle, device, function), (device, function, toggle)


def test_the_frame_opens_on_the_six_unit_leader():
    repeat = _encode().repeat
    assert repeat[:2] == (6 * UNIT, 2 * UNIT)


def test_the_toggle_bit_is_double_width():
    """T=1 is two units of mark then two of space, T=0 the reverse. It follows
    mode 000, whose last half-bit is a mark, so a 1 widens that mark to three
    units and a 0 leaves the mark alone and starts a two-unit space and a
    two-unit mark. (With device and function 0 the bits after it each begin
    with a space, which a 1's trailing space joins.)"""
    zero = _encode(device=0, function=0, toggle=0).repeat
    one = _encode(device=0, function=0, toggle=1).repeat
    assert zero[:8] == one[:8], "identical through the start bit and mode"
    assert (one[8], one[9]) == (3 * UNIT, 3 * UNIT)
    assert (zero[8], zero[9], zero[10]) == (UNIT, 2 * UNIT, 2 * UNIT)


def test_toggle_changes_only_the_toggle_bit():
    a = _decode(_encode(19, 12, toggle=0).repeat)
    b = _decode(_encode(19, 12, toggle=1).repeat)
    assert a[2] == 0 and b[2] == 1
    assert a[:2] + a[3:] == b[:2] + b[3:]


def test_a_file_cannot_set_the_toggle_so_it_defaults_to_zero():
    """D3b: ``toggle`` exists for the tests and for a future second candidate
    group; the default is the IRP's own T=0."""
    plain = RC6.encode(device=0, subdevice=None, function=12, carrier_hz=36_000)
    assert plain == _encode(toggle=0)


@pytest.mark.parametrize("function", [0, 1, 2, 3, 12, 127, 128, 254, 255])
def test_the_frame_pads_to_its_extent_from_the_first_mark(function):
    """Both endings: a last bit of 1 ends the frame on a space that the
    extent lengthens, a 0 ends it on a mark that needs a gap appended.
    Either way the whole sequence is 107 ms."""
    assert sum(_encode(function=function).repeat) == RC6.extent_us


def test_the_frame_before_its_gap_is_fifty_two_units():
    """8 of leader, 2 of start, 6 of mode, 4 of trailer, 32 of data."""
    repeat = _encode(function=0).repeat       # ends on a mark: the gap is its own element
    assert sum(repeat[:-1]) == 52 * UNIT
    ends_on_space = _encode(function=1).repeat
    assert sum(ends_on_space[:-1]) + UNIT == 52 * UNIT   # the lengthened space was one unit


def test_a_frame_with_a_missing_leader_is_rejected():
    assert _decode(_encode().repeat[2:]) is None


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "0073"            # 36 kHz
    # Biphase at 444 us: runs of one, two or three units, never more.
    cycles_per_unit = 16
    assert set(w // cycles_per_unit for w in words[4:-1]) <= {1, 2, 3, 6}


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=256), "D:8 holds 0-255"),
        (dict(device=-1), "D:8 holds 0-255"),
        (dict(function=256), "F:8 holds 0-255"),
        (dict(function=-1), "F:8 holds 0-255"),
        (dict(toggle=2), "T:1 is 0 or 1"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="no subdevice"):
        RC6.encode(device=0, subdevice=3, function=12, carrier_hz=36_000)


# --- published raw sequences (tests/vectors/CITATIONS.md, RC6 gate 2a) ----------
#
# IrpTransmogrifier's analyzer tests at c945e76 hold these as microsecond
# arrays and assert what each decodes to. They are exact multiples of the
# unit, so no quantization is involved: the encoder must reproduce them.

#: BiphaseWithDoubleToggleDecoderNGTest.java L27-L32, decoded at L84-L95 as
#: {A=1,B=0,C=0|1,D=255,E=0}: start 1, mode 0, T, D=255, F=0.
RAW_D255_F0_T0 = [
    2664, 888, 444, 888, 444, 444, 444, 444, 444, 888, 1332, 444, 444, 444, 444,
    444, 444, 444, 444, 444, 444, 444, 444, 444, 444, 888, 444, 444, 444, 444,
    444, 444, 444, 444, 444, 444, 444, 444, 444, 444, 444, 83912,
]
RAW_D255_F0_T1 = [
    2664, 888, 444, 888, 444, 444, 444, 444, 1332, 888, 444, 444, 444, 444, 444,
    444, 444, 444, 444, 444, 444, 444, 444, 444, 444, 888, 444, 444, 444, 444,
    444, 444, 444, 444, 444, 444, 444, 444, 444, 444, 444, 83912,
]
#: BiphaseDecoderNGTest.java L23-L26, decoded at L81-L89 as {A=8,B=30723}: A is
#: start and mode, B is 0x7803, so D=120 and F=3. A last bit of 1, so the gap
#: lengthens a space: this is the published case for that ending.
RAW_D120_F3_T0 = [
    2664, 888, 444, 888, 444, 444, 444, 444, 444, 888, 888, 444, 888, 444, 444,
    444, 444, 444, 444, 888, 444, 444, 444, 444, 444, 444, 444, 444, 444, 444,
    444, 444, 444, 444, 444, 444, 888, 444, 444, 84356,
]


@pytest.mark.parametrize(
    "raw, device, function, toggle",
    [
        (RAW_D255_F0_T0, 255, 0, 0),
        (RAW_D255_F0_T1, 255, 0, 1),
        (RAW_D120_F3_T0, 120, 3, 0),
    ],
    ids=["D255-F0-T0", "D255-F0-T1", "D120-F3-T0"],
)
def test_the_encoder_reproduces_the_published_raw_sequences_exactly(
        raw, device, function, toggle):
    assert list(_encode(device, function, toggle).repeat) == raw
    assert _decode(raw) == (1, 0, toggle, device, function)
