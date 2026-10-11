"""RC6-6-20 invariants (D18 gate 3, DESIGN section 47).

The decoder is written from the frame layout, not from the encoder: a 6-unit mark and 2-unit space, a start bit, the mode
``110``, the toggle at double width, then D:8, S:4 and F:8, every other bit biphase at one unit a half (a ``1`` is a mark
then a space), and a 100 ms gap that a last bit of 1 lengthens its space by. The payload is small enough to sweep: every
function for several addresses, every address for several functions, and a seeded sample.
"""

import random
from pathlib import Path

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import RC6_6_20
from remote_ledger.remote import load_remote

ROOT = Path(__file__).resolve().parent.parent
UNIT = RC6_6_20.unit_us
GAP = 100_000
CARRIER = 36_000


def _encode(device=0, subdevice=12, function=88, **kw):
    return RC6_6_20.encode(device=device, subdevice=subdevice, function=function, carrier_hz=CARRIER, **kw)


def _levels(durations, unit):
    out = []
    for i, d in enumerate(durations):
        out += [1 - i % 2] * max(1, round(d / unit))
    return out


def _fields(levels):
    """``(mode, T, D, S, F)`` of the levels after the leader, or None."""
    position = 0

    def bit(width=1):
        nonlocal position
        first, second = levels[position:position + width], levels[position + width:position + 2 * width]
        if len(first) != width or len(second) != width or len(set(first)) != 1 or len(set(second)) != 1 \
                or first[0] == second[0]:
            raise ValueError("not biphase")
        position += 2 * width
        return int(first[0] == 1)

    def field(count):
        value = 0
        for _ in range(count):
            value = value << 1 | bit()
        return value

    try:
        if bit() != 1:
            return None
        mode = field(3)
        toggle = bit(2)
        return mode, toggle, field(8), field(4), field(8)
    except (ValueError, IndexError):
        return None


def decode(durations, unit=UNIT, gap=GAP):
    """``(mode, T, D, S, F)`` of a frame with its 100 ms gap, or None where it is not one."""
    if len(durations) < 4 or round(durations[0] / unit) != 6 or round(durations[1] / unit) != 2:
        return None
    levels = _levels(durations[:-1], unit)[8:]
    if durations[-1] == gap + unit:                    # the last bit ended on a space: it is part of the gap
        levels.append(0)
    elif durations[-1] != gap:
        return None
    return _fields(levels)


def test_registry_metadata():
    assert RC6_6_20.name == "RC6-6-20" and RC6_6_20.unit_us == 444 and RC6_6_20.bits == 25
    assert RC6_6_20.nominal_carrier_hz == 36_000
    assert RC6_6_20.extent_us is None, "the IRP ends -100m, a gap and not an extent"
    assert "(6,-2,1:1,6:3,<-2,2|2,-2>(T:1),D:8,S:4,F:8,-100m)" in RC6_6_20.irp


def test_every_function_of_several_addresses_round_trips_with_the_toggle_either_way():
    for device, subdevice in ((0, 12), (5, 12), (255, 15), (0, 0), (170, 5)):
        for function in range(256):
            for toggle in (0, 1):
                got = decode(_encode(device, subdevice, function, toggle=toggle).repeat)
                assert got == (6, toggle, device, subdevice, function), (device, subdevice, function, toggle)


def test_every_address_of_several_functions_round_trips():
    for function in (0, 88, 255):
        for subdevice in (0, 12, 15):
            for device in range(256):
                assert decode(_encode(device, subdevice, function).repeat) == (6, 0, device, subdevice, function)
    for device in (0, 5):
        for function in (0, 255):
            for subdevice in range(16):
                assert decode(_encode(device, subdevice, function).repeat) == (6, 0, device, subdevice, function)


def test_a_seeded_sample_round_trips():
    rng = random.Random(1805)
    for _ in range(3000):
        d, s, f, t = rng.randrange(256), rng.randrange(16), rng.randrange(256), rng.randrange(2)
        assert decode(_encode(d, s, f, toggle=t).repeat) == (6, t, d, s, f)


def test_there_is_no_intro_and_the_leader_is_six_units_of_mark_and_two_of_space():
    signal = _encode()
    assert signal.intro == () and pronto.parse_words(pronto.encode(signal))[2] == 0
    assert signal.repeat[:2] == (6 * UNIT, 2 * UNIT)


def test_the_gap_is_100_ms_after_a_mark_or_added_to_a_space():
    """F = 0 ends on a mark (its last bit is a 0: a space then a mark) and the gap is a space of its own; F = 3 ends on a
    space (a 1) and the gap lengthens it, as the pinned release renders both."""
    assert _encode(function=0).repeat[-2:] == (UNIT, GAP)
    assert _encode(function=3).repeat[-1] == GAP + UNIT and len(_encode(function=3).repeat) % 2 == 0


def test_the_toggle_is_the_double_width_bit_after_the_mode_and_the_fixed_mode_is_six():
    """T = 1 is two units of mark then two of space, T = 0 the reverse; the mode bits before it are 1 1 0."""
    on, off = _levels(_encode(toggle=1).repeat[:-1], UNIT), _levels(_encode(toggle=0).repeat[:-1], UNIT)
    assert on[8:16] == off[8:16] == [1, 0, 1, 0, 1, 0, 0, 1]                       # the start bit, then 1 1 0
    assert on[8 + 8:8 + 12] == [1, 1, 0, 0] and off[8 + 8:8 + 12] == [0, 0, 1, 1]


def test_the_fields_are_sent_device_then_sub_device_then_function_most_significant_first():
    """A swapped order or bit order fails here and a round trip through the same order would not notice."""
    base = _levels(_encode(0, 0, 0).repeat[:-1], UNIT)[8 + 2 + 6 + 4:]            # the levels from D on
    assert base[:2] == [0, 1]                                                       # D's first bit is the high one: a 0
    d = _levels(_encode(0x80, 0, 0).repeat[:-1], UNIT)[8 + 2 + 6 + 4:]
    assert d[:2] == [1, 0] and d[2:4] == [0, 1]                                     # D = 0x80: the first bit only
    s = _levels(_encode(0, 8, 0).repeat[:-1], UNIT)[8 + 2 + 6 + 4 + 16:]
    assert s[:2] == [1, 0] and s[2:4] == [0, 1]                                     # S = 8: S's first bit only
    f = _levels(_encode(0, 0, 0x80).repeat[:-1], UNIT)[8 + 2 + 6 + 4 + 16 + 8:]
    assert f[:2] == [1, 0] and f[2:4] == [0, 1]                                     # F = 0x80: F's first bit only


def test_the_unit_scales_the_marks_and_spaces_but_not_the_gap():
    signal = _encode(unit_us=500)
    assert decode(signal.repeat, unit=500) == (6, 0, 0, 12, 88) and signal.repeat[0] == 3_000


@pytest.mark.parametrize("kwargs, match", [
    (dict(device=256), "D:8"), (dict(device=-1), "D:8"), (dict(subdevice=16), "S:4"), (dict(subdevice=-1), "S:4"),
    (dict(function=256), "F:8"), (dict(function=-1), "F:8"), (dict(toggle=2), "T:1"),
])
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_missing_subdevice_is_refused():
    with pytest.raises(EncodeError, match="subdevice"):
        RC6_6_20.encode(device=0, subdevice=None, function=1, carrier_hz=CARRIER)


# --- the LIRC project's Sky remotes, from another source -------------------------------------------------------------

#: The files of ``remotes/lirc`` whose raw keys are RC-6 mode 6 frames with a 20-bit payload by the decoder below
#: (451 keys). They are confs expanded by lircd, not hardware captures.
LIRC_RC6_6_20 = (
    "lirc/foxtel/Digital_Cable_STB.json", "lirc/one_for_all/URC-8203.OneForAll_URC-8203.json", "lirc/pace/DC420N.json",
    "lirc/pace/DIGIBOX.DIGIBOX.json", "lirc/sky/NAVIGATOR.SKY_NAVIGATOR_RC-6.json", "lirc/sky/Navigator.Sky_Navigator.json",
    "lirc/sky/Rev4.json", "lirc/sky/SKY+_DVB-S.json", "lirc/sky/Sky_Rev_6.json",
)


def decode_lirc(durations):
    """``(T, D, S, F)`` of a raw frame that is mode 6 with a 20-bit payload at the unit its own leader has, or None: the
    layout decoder, with nothing but the gap allowed after the payload."""
    if len(durations) < 6:
        return None
    unit = durations[0] / 6
    if not 300 < unit < 600 or round(durations[1] / unit) != 2:
        return None
    levels = _levels(durations, unit)[8:]
    got = _fields(levels)
    if got is None or got[0] != 6:
        return None
    used = 2 + 6 + 4 + 40                                # the start, the mode, the trailer and 20 biphase bits
    if any(levels[used:used + 3]):                       # nothing but the gap may follow
        return None
    return got[1:]


def lirc_frames():
    """``(file, key, (T, D, S, F))`` of every raw sequence of the Sky and other LIRC remotes that decodes."""
    out = []
    for name in LIRC_RC6_6_20:
        for key, forms in load_remote(ROOT / "remotes" / name).keys.items():
            form = forms[0]
            for sequence in (form.intro, form.repeat):
                got = decode_lirc(list(sequence)) if form.type == "raw" and sequence else None
                if got is not None:
                    out.append((name, key, got))
    return out


def test_the_lirc_remotes_of_sky_boxes_decode_to_frames_the_encoder_builds():
    """451 keys of nine LIRC remotes (all device 0; sub devices 0, 3 and 12) decode by the layout decoder at the unit each
    has, and the encoder builds a frame that decodes to the same fields at that unit."""
    frames = lirc_frames()
    assert len(frames) == 451
    assert {(d, s) for _, _, (t, d, s, f) in frames} == {(0, 0), (0, 3), (0, 12)}
    seen = set()
    for name, key, (toggle, device, subdevice, function) in frames:
        durations = list(_encode(device, subdevice, function, toggle=toggle, unit_us=444).repeat)
        assert decode(durations) == (6, toggle, device, subdevice, function)
        seen.add((device, subdevice, function))
    assert len(seen) == 197
