"""Denon invariants (D18 gate 3).

Denon sends a frame, the same frame with its command complemented, then the
first again -- Sharp's shape with different trailers. The decoder
(tests/addr_cmd_japan.py) is written from the IRP's frame layout, not from the
encoder, and the space is small enough to be exhaustive: 32 devices x 256
functions.
"""

import pytest

import teaser_japan
from addr_cmd_japan import FRAME, UNIT, decode_signal
from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import DENON, REGISTRY, SHARP

CARRIER = 38_000
NORMAL, INVERTED = 0, 3        # the trailers 0:2 and 3:2

#: IrpTransmogrifier's published Denon string, ShortProntoNGTest.java L21 @c945e76.
#: It decodes, by that tool, as Denon {D=1,F=3}. It is the *superseded* IRP --
#: a fixed 165-unit gap after each frame -- so the gaps differ from the
#: registered ^67m form, and everything else must agree.
PUBLISHED = (
    "0000 006D 0000 0020 000A 0046 000A 001E 000A 001E 000A 001E 000A 001E "
    "000A 0046 000A 0046 000A 001E 000A 001E 000A 001E 000A 001E 000A 001E "
    "000A 001E 000A 001E 000A 001E 000A 0677 000A 0046 000A 001E 000A 001E "
    "000A 001E 000A 001E 000A 001E 000A 001E 000A 0046 000A 0046 000A 0046 "
    "000A 0046 000A 0046 000A 0046 000A 0046 000A 0046 000A 0677"
)


def _encode(device=8, function=175, **kw):
    return DENON.encode(device=device, subdevice=None, function=function,
                        carrier_hz=CARRIER, **kw)


def test_registry_metadata():
    assert REGISTRY["Denon"] is DENON
    assert DENON.unit_us == 264
    assert DENON.bits == 15
    assert DENON.nominal_carrier_hz == 38_000
    assert DENON.extent_us is None
    assert "D:5,F:8,0:2" in DENON.irp and "~F:8,3:2" in DENON.irp


def test_every_device_and_function_round_trips_in_all_three_frames():
    """8,192 pairs: the field order, the bit order, the complement and the
    trailers."""
    for device in range(32):
        for function in range(256):
            intro, repeat = decode_signal(_encode(device, function))
            normal = (device, function, NORMAL)
            inverted = (device, function ^ 0xFF, INVERTED)
            assert intro == [normal], (device, function)
            assert repeat == [inverted, normal], (device, function)


def test_the_signal_is_one_frame_then_two():
    signal = _encode()
    assert len(signal.intro) == FRAME and len(signal.repeat) == 2 * FRAME
    assert signal.repeat[FRAME:] == signal.intro


def test_every_frame_pads_to_sixty_seven_milliseconds():
    for device, function in [(0, 0), (31, 255), (8, 175), (16, 0xAA)]:
        signal = _encode(device, function)
        for sequence in (signal.intro, signal.repeat):
            for i in range(0, len(sequence), FRAME):
                assert sum(sequence[i:i + FRAME]) == 67_000


def test_the_trailer_bits_are_two_zeros_for_the_normal_frame_and_two_ones_inverted():
    """Where Denon differs from Sharp: ``0:2`` and ``3:2``."""
    signal = _encode()
    normal = [signal.intro[2 * i + 1] for i in range(13, 15)]
    inverted = [signal.repeat[2 * i + 1] for i in range(13, 15)]
    assert normal == [3 * UNIT, 3 * UNIT]
    assert inverted == [7 * UNIT, 7 * UNIT]


def test_denon_and_sharp_differ_in_the_trailers_and_nowhere_else():
    denon, sharp = _encode(8, 175), SHARP.encode(
        device=8, subdevice=None, function=175, carrier_hz=CARRIER
    )
    diffs = [i for i, (a, b) in enumerate(zip(denon.intro, sharp.intro)) if a != b]
    # the trailer's first bit (a zero for Denon, a one for Sharp) sits at
    # pair 13; after it the frame pads to the same extent, so the gap moves
    assert diffs == [27, 31]


def test_pronto_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "006D"
    assert (words[2], words[3]) == (16, 32)


def test_the_unit_override_scales_marks_and_spaces_but_not_extents():
    signal = _encode(7, 0x5A, unit_us=300)
    intro, repeat = decode_signal(signal, unit=300)
    assert intro == [(7, 0x5A, NORMAL)]
    assert repeat == [(7, 0x5A ^ 0xFF, INVERTED), (7, 0x5A, NORMAL)]
    assert sum(signal.intro) == 67_000


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=32), "D:5 holds 0-31"),
        (dict(device=-1), "D:5 holds 0-31"),
        (dict(function=256), "F:8 holds 0-255"),
        (dict(function=-1), "F:8 holds 0-255"),
        (dict(subdevice=0), "no subdevice"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    args = dict(device=8, subdevice=None, function=175, carrier_hz=CARRIER)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        DENON.encode(**args)


def test_a_unit_that_leaves_no_room_for_the_extent_is_refused():
    with pytest.raises(EncodeError, match="below one unit"):
        _encode(unit_us=3_000)


# --- gate 2a: IrpTransmogrifier's published Denon material -------------------


def test_the_published_pronto_string_agrees_with_the_encoder_except_in_the_gaps():
    """The string's two frames are our intro and the first frame of our repeat
    for D=1, F=3. Every mark and space agrees to the cycle; only the gap words
    differ, because the string comes from the superseded fixed-gap IRP."""
    words = pronto.parse_words(PUBLISHED)
    assert (words[2], words[3]) == (0, 32)          # the whole signal is its repeat
    body = words[4:]
    signal = _encode(1, 3)
    ours = pronto.parse_words(pronto.encode(signal))[4:]
    # our intro is frame 1 (words 0-31), our repeat starts with frame 2 (32-63)
    frame1, frame2 = body[:32], body[32:64]
    assert ours[:31] == frame1[:31]
    assert ours[32:63] == frame2[:31]
    assert (body[31], body[63]) == (0x0677, 0x0677)  # their fixed 165-unit gaps
    assert ours[31] != body[31]                      # our gap is ^67m's


@pytest.mark.parametrize("entry", teaser_japan.entries("Denon"), ids=lambda e: e["note"])
def test_the_published_capture_matches_the_encoder_on_its_published_decode(entry):
    p = entry["params"]
    assert teaser_japan.problems(_encode(p["D"], p["F"]), entry) == []


def test_the_capture_check_has_teeth():
    entry = teaser_japan.entries("Denon")[0]
    p = entry["params"]
    wrong = _encode(p["D"], p["F"] ^ 0xFF)
    assert teaser_japan.problems(wrong, entry)
