"""Sharp invariants (D18 gate 3).

Sharp sends a frame, the same frame with its command complemented, then the
first again. The decoder (tests/addr_cmd_japan.py) is written from the IRP's
frame layout, not from the encoder, and the space is small enough to be
exhaustive: 32 devices x 256 functions.
"""

import pytest

import teaser_japan
from addr_cmd_japan import FRAME, UNIT, decode_signal
from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import REGISTRY, SHARP

CARRIER = 38_000
NORMAL, INVERTED = 1, 2        # the trailers 1:2 and 2:2


def _encode(device=1, function=22, **kw):
    return SHARP.encode(device=device, subdevice=None, function=function,
                        carrier_hz=CARRIER, **kw)


def test_registry_metadata():
    assert REGISTRY["Sharp"] is SHARP
    assert SHARP.unit_us == 264
    assert SHARP.bits == 15
    assert SHARP.nominal_carrier_hz == 38_000
    assert SHARP.extent_us is None
    assert "D:5,F:8,1:2" in SHARP.irp and "~F:8,2:2" in SHARP.irp


def test_every_device_and_function_round_trips_in_all_three_frames():
    """8,192 pairs: the field order, the bit order, the complement, and the
    trailers that tell Sharp from Denon."""
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
    for device, function in [(0, 0), (31, 255), (1, 22), (16, 0xAA)]:
        signal = _encode(device, function)
        for sequence in (signal.intro, signal.repeat):
            for i in range(0, len(sequence), FRAME):
                assert sum(sequence[i:i + FRAME]) == 67_000


def test_the_trailer_bits_are_one_then_zero_for_the_normal_frame():
    """``1:2`` is LSB first: a one-bit then a zero-bit. Denon's ``0:2`` would
    be two zero-bits, so this is what makes the frame Sharp."""
    frame = _encode().intro
    spaces = [frame[2 * i + 1] for i in range(13, 15)]
    assert spaces == [7 * UNIT, 3 * UNIT]


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
    args = dict(device=1, subdevice=None, function=22, carrier_hz=CARRIER)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        SHARP.encode(**args)


def test_a_unit_that_leaves_no_room_for_the_extent_is_refused():
    with pytest.raises(EncodeError, match="below one unit"):
        _encode(unit_us=3_000)


# --- gate 2a: IrpTransmogrifier's published Sharp Pronto export ---------------


@pytest.mark.parametrize("entry", teaser_japan.entries("Sharp"), ids=lambda e: e["note"])
def test_the_published_export_matches_the_encoder_on_its_published_decode(entry):
    p = entry["params"]
    assert teaser_japan.problems(_encode(p["D"], p["F"]), entry) == []


def test_the_published_check_has_teeth():
    entry = teaser_japan.entries("Sharp")[0]
    p = entry["params"]
    wrong = _encode(p["D"], p["F"] ^ 0xFF)     # the complement of the published code
    assert teaser_japan.problems(wrong, entry)
