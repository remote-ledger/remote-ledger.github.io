"""JVC invariants (D18 gate 3).

JVC's distinguishing structure is that only the *first* frame has a lead-in.
The decoder below is written from the IRP's frame layout, not from the
encoder, and exercises both frame shapes. The space is small enough to be
exhaustive: 65,536 (device, function) pairs.
"""

import pytest

import teaser_japan
from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import JVC, REGISTRY

UNIT = JVC.unit_us
LEAD_IN = 2                    # a mark and a space
DATA = 2 * 16 + 1 + 1          # 16 bit pairs, the stop mark and the gap
CARRIER = 37_900


def _data(frame, unit=UNIT):
    """Sixteen data bits of ``frame`` (which starts at the first bit), or None."""
    if len(frame) != DATA or frame[-2] != unit:
        return None
    bits = []
    for i in range(16):
        mark, space = frame[2 * i], frame[2 * i + 1]
        if mark != unit or space not in (unit, 3 * unit):
            return None
        bits.append(1 if space == 3 * unit else 0)
    return (sum(b << i for i, b in enumerate(bits[:8])),
            sum(b << i for i, b in enumerate(bits[8:])))


def _decode(signal, unit=UNIT):
    """(intro device/function, repeat device/function), checking that only
    the intro has a lead-in."""
    intro, repeat = signal.intro, signal.repeat
    if len(intro) != LEAD_IN + DATA or tuple(intro[:2]) != (16 * unit, 8 * unit):
        return None
    return _data(intro[LEAD_IN:], unit), _data(repeat, unit)


def _encode(device=5, function=19, **kw):
    return JVC.encode(device=device, subdevice=None, function=function,
                      carrier_hz=CARRIER, **kw)


def test_registry_metadata():
    assert REGISTRY["JVC"] is JVC
    assert JVC.unit_us == 527
    assert JVC.bits == 16
    assert JVC.nominal_carrier_hz == 37_900
    assert JVC.extent_us is None
    assert "(16,-8,D:8,F:8,1,^59.08m,(D:8,F:8,1,^46.42m)*)" in JVC.irp


def test_every_device_and_function_round_trips_in_both_frames():
    """65,536 pairs: the frame, the byte order (device first) and the bit
    order, for the intro and for the repeat."""
    for device in range(256):
        for function in range(256):
            got = _decode(_encode(device, function))
            assert got == ((device, function), (device, function)), (device, function)


def test_only_the_first_frame_has_a_lead_in():
    signal = _encode()
    assert len(signal.intro) == len(signal.repeat) + 2
    assert tuple(signal.intro[:2]) == (16 * UNIT, 8 * UNIT) == (8_432, 4_216)
    assert signal.intro[2:-1] == signal.repeat[:-1]
    # the repeat opens on a data bit's mark, not on a lead-in
    assert signal.repeat[0] == UNIT and signal.repeat[1] in (UNIT, 3 * UNIT)


def test_the_two_frames_pad_to_their_own_extents():
    for device, function in [(0, 0), (255, 255), (5, 19), (0xC0, 0x0F)]:
        signal = _encode(device, function)
        assert sum(signal.intro) == 59_080
        assert sum(signal.repeat) == 46_420


def test_the_gap_depends_on_the_data_because_the_extent_is_fixed():
    """A constant extent means a data-dependent gap: more ones, shorter gap.
    (The app sends a fixed 21 ms; see NOTES/japan.md.)"""
    zeros, ones = _encode(0, 0), _encode(255, 255)
    assert zeros.intro[-1] - ones.intro[-1] == 16 * 2 * UNIT
    assert zeros.intro[-1] == 59_080 - (8_432 + 4_216 + 32 * UNIT + UNIT)


def test_pronto_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "006D"
    assert (words[2], words[3]) == (18, 17)      # intro pairs, repeat pairs


def test_the_unit_override_scales_marks_and_spaces_but_not_extents():
    signal = _encode(0x12, 0x34, unit_us=500)
    assert _decode(signal, unit=500) == ((0x12, 0x34), (0x12, 0x34))
    assert sum(signal.intro) == 59_080 and sum(signal.repeat) == 46_420


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=256), "D:8 holds 0-255"),
        (dict(device=-1), "D:8 holds 0-255"),
        (dict(function=256), "F:8 holds 0-255"),
        (dict(function=-1), "F:8 holds 0-255"),
        (dict(subdevice=0), "no subdevice"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    args = dict(device=5, subdevice=None, function=19, carrier_hz=CARRIER)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        JVC.encode(**args)


def test_a_unit_that_leaves_no_room_for_the_extent_is_refused():
    with pytest.raises(EncodeError, match="below one unit"):
        _encode(unit_us=4_000)


# --- gate 2a: IrpTransmogrifier's published JVC signals ----------------------


@pytest.mark.parametrize("entry", teaser_japan.entries("JVC"), ids=lambda e: e["note"])
def test_the_published_signal_matches_the_encoder_on_its_published_decode(entry):
    p = entry["params"]
    assert teaser_japan.problems(_encode(p["D"], p["F"]), entry) == []


def test_the_published_check_has_teeth():
    entry = teaser_japan.entries("JVC")[1]
    p = entry["params"]
    assert p["F"] != int(format(p["F"], "08b")[::-1], 2)
    wrong = _encode(p["D"], int(format(p["F"], "08b")[::-1], 2))
    assert teaser_japan.problems(wrong, entry)
