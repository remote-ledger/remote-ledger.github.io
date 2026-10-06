"""Pioneer-2Part invariants (D18 gate 3).

The decoder below is written from the frame layout in the IRP, not from the
encoder: a 16-unit mark, an 8-unit space, four LSB-first bytes (D, ~D, F, ~F),
a stop mark and a gap. What anchors the constants to the outside world is the
cited vector (tests/vectors/CITATIONS.md) and IrpTransmogrifier's published
capture of a Pioneer receiver, both checked here.
"""

import random

import pytest

import teaser_japan
from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import PIONEER_2PART as P, REGISTRY

UNIT = P.unit_us
FRAME = 2 + 64 + 2          # lead-in pair, 32 bit pairs, stop mark and gap
EXTENT = 90_000


def _decode_frame(frame, unit=UNIT):
    """One frame's four bytes, or None unless it is exactly a Pioneer frame."""
    if len(frame) != FRAME:
        return None
    if (frame[0], frame[1]) != (16 * unit, 8 * unit) or frame[-2] != unit:
        return None
    bits = []
    for i in range(32):
        mark, space = frame[2 + 2 * i], frame[3 + 2 * i]
        if mark != unit or space not in (unit, 3 * unit):
            return None
        bits.append(1 if space == 3 * unit else 0)
    return tuple(sum(b << i for i, b in enumerate(bits[8 * k:8 * k + 8])) for k in range(4))


def _frames(sequence):
    assert len(sequence) % FRAME == 0
    return [_decode_frame(sequence[i:i + FRAME]) for i in range(0, len(sequence), FRAME)]


def _encode(d0, f0, d, f, **kw):
    return P.encode(device=(d0 << 8) | d, subdevice=None, function=(f0 << 8) | f,
                    carrier_hz=40_000, **kw)


def _expected(d0, f0, d, f):
    return [(d0, d0 ^ 0xFF, f0, f0 ^ 0xFF), (d, d ^ 0xFF, f, f ^ 0xFF)]


def test_registry_metadata():
    assert P.name == "Pioneer-2Part"
    assert REGISTRY["Pioneer-2Part"] is P
    assert P.unit_us == 564
    assert P.bits == 32
    assert P.nominal_carrier_hz == 40_000
    assert P.extent_us is None
    assert "D0:8,~D0:8,F0:8,~F0:8" in P.irp and "^90m" in P.irp


def test_plain_pioneer_is_deliberately_not_registered():
    assert "Pioneer" not in REGISTRY


def test_intro_is_both_frames_and_repeat_is_the_second_alone():
    """``+`` makes one pass of the repeat mandatory, so it is in the intro
    too -- IrpTransmogrifier's rendering, and the app's 136-duration frame
    pair is exactly this intro."""
    signal = _encode(170, 91, 175, 36)
    assert len(signal.intro) == 2 * FRAME and len(signal.repeat) == FRAME
    first, second = _frames(signal.intro)
    assert first == (170, 85, 91, 164)
    assert second == (175, 80, 36, 219)
    assert _frames(signal.repeat) == [second]
    assert signal.intro[FRAME:] == signal.repeat


def test_every_byte_value_in_every_position_round_trips():
    """1,024 encodes: each of the four bytes takes all 256 values with the
    others held at distinct, asymmetric values, so a swapped byte or a
    swapped bit order shows."""
    base = [0x3C, 0xA5, 0x5A, 0x96]
    for position in range(4):
        for value in range(256):
            args = list(base)
            args[position] = value
            signal = _encode(*args)
            assert _frames(signal.intro) == _expected(*args), (position, value)
            assert _frames(signal.repeat) == _expected(*args)[1:], (position, value)


def test_random_four_byte_combinations_round_trip():
    rng = random.Random(564)
    for _ in range(2_000):
        args = [rng.randrange(256) for _ in range(4)]
        assert _frames(_encode(*args).intro) == _expected(*args), args


def test_every_frame_pads_to_ninety_milliseconds():
    """``^90m`` closes each frame, and a Pioneer frame always holds sixteen
    ones among its 32 bits (a byte and its complement), so the gap is the
    same 21,756 us for every code."""
    gaps = set()
    for args in [(0, 0, 0, 0), (255, 255, 255, 255), (170, 91, 175, 36), (1, 2, 3, 4)]:
        signal = _encode(*args)
        for sequence in (signal.intro, signal.repeat):
            for i in range(0, len(sequence), FRAME):
                assert sum(sequence[i:i + FRAME]) == EXTENT
                gaps.add(sequence[i + FRAME - 1])
    assert gaps == {21_756}


def test_the_lead_in_is_sixteen_units_then_eight():
    signal = _encode(1, 2, 3, 4)
    assert signal.intro[:2] == (9_024, 4_512)
    assert signal.intro[FRAME:FRAME + 2] == (9_024, 4_512)


def test_equal_halves_are_the_degenerate_case_and_still_two_frames():
    """The IRP's defaults (D=D0, F=F0) are not assumed: ``0xADAD`` is written
    out. The result is the same frame twice, which is what a database code
    whose halves match sends."""
    signal = _encode(173, 11, 173, 11)
    one, two = _frames(signal.intro)
    assert one == two == (173, 82, 11, 244)
    assert signal.intro == signal.repeat * 2


def test_pronto_words():
    words = pronto.parse_words(pronto.encode(_encode(170, 91, 175, 36)))
    assert f"{words[1]:04X}" == "0068"            # 40 kHz
    assert (words[2], words[3]) == (68, 34)      # intro 2 frames, repeat 1
    assert (words[4], words[5]) == (0x0168, 0x00B4)


def test_a_unit_override_scales_every_mark_and_space_but_not_the_extent():
    signal = _encode(1, 2, 3, 4, unit_us=500)
    assert _decode_frame(signal.intro[:FRAME], unit=500) == (1, 254, 2, 253)
    assert signal.intro[:2] == (8_000, 4_000)
    assert sum(signal.intro[:FRAME]) == EXTENT


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=0x10000), "D0:D"),
        (dict(device=-1), "D0:D"),
        (dict(function=0x10000), "F0:F"),
        (dict(function=-1), "F0:F"),
        (dict(subdevice=0), "no subdevice"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    args = dict(device=0xAAAF, subdevice=None, function=0x5B24, carrier_hz=40_000)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        P.encode(**args)


def test_a_unit_that_leaves_no_room_for_the_extent_is_refused():
    with pytest.raises(EncodeError, match="below one unit"):
        _encode(1, 2, 3, 4, unit_us=1_800)


# --- gate 2a: IrpTransmogrifier's published capture of a Pioneer receiver ------


@pytest.mark.parametrize("entry", teaser_japan.entries("Pioneer-2Part"),
                         ids=lambda e: e["note"])
def test_the_published_capture_matches_the_encoder_on_its_published_decode(entry):
    p = entry["params"]
    signal = _encode(p["D0"], p["F0"], p["D"], p["F"])
    assert teaser_japan.problems(signal, entry) == []


def test_the_capture_check_has_teeth():
    """Swap the bit order of the second device byte and it must fail: a check
    that passed for any four bytes would prove nothing."""
    entry = teaser_japan.entries("Pioneer-2Part")[0]
    p = entry["params"]
    reversed_d = int(format(p["D"], "08b")[::-1], 2)
    assert reversed_d != p["D"]
    signal = _encode(p["D0"], p["F0"], reversed_d, p["F"])
    assert teaser_japan.problems(signal, entry)
