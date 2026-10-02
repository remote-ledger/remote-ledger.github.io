"""RC5 invariants (D18 gate 3), and the Meridian MSR they exist to protect.

Like NEC1's and Sony20's, the framing tests catch a broken *frame*. What
anchors the constants to the outside world is the cited golden vectors
(tests/vectors/CITATIONS.md): D=1 F=1 in both toggle states, and D=7 F=5.

The decoder below is deliberately written from the frame layout, not from
the encoder, and restores the one thing a recorded waveform loses: S1's
leading idle half-bit.
"""

import json
from pathlib import Path

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import RC5

ROOT = Path(__file__).resolve().parents[1]
UNIT = RC5.unit_us


def _decode(repeat, unit=UNIT):
    """A 14-bit biphase frame back to (S1, S2, T, device, function).

    ``repeat`` is mark/space durations ending in the gap. Anything that is not
    exactly fourteen valid biphase bits returns None, so a frame short of its
    first start bit is *rejected* rather than misread.
    """
    halves = [0]  # the idle half-bit S1 begins with
    for i, duration in enumerate(repeat[:-1]):
        halves += [1 if i % 2 == 0 else 0] * round(duration / unit)
    if len(halves) % 2:
        halves.append(0)  # a last bit of 0 ends on a space the gap swallowed
    bits = []
    for j in range(0, len(halves), 2):
        pair = (halves[j], halves[j + 1])
        if pair not in ((0, 1), (1, 0)):
            return None
        bits.append(1 if pair == (0, 1) else 0)
    if len(bits) != 14:
        return None
    s1, s2, t = bits[:3]
    device = int("".join(map(str, bits[3:8])), 2)
    low = int("".join(map(str, bits[8:14])), 2)
    return s1, s2, t, device, low + (0 if s2 else 64)


def _encode(device=19, function=12, toggle=0):
    return RC5.encode(device=device, subdevice=None, function=function,
                      carrier_hz=36_000, toggle=toggle)


def test_registry_metadata():
    assert RC5.unit_us == 889
    assert RC5.extent_us == 114_000
    assert RC5.bits == 14
    assert RC5.nominal_carrier_hz == 36_000
    assert "~F:1:6,T:1,D:5,F:6" in RC5.irp


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_address_command_and_toggle_round_trips():
    """8,192 frames. Exhaustive, because the space is small and the failure
    modes -- a swapped bit order, a mis-set S2 at the 63/64 boundary -- sit
    at specific values a sample would skip."""
    for device in range(32):
        for function in range(128):
            for toggle in (0, 1):
                got = _decode(_encode(device, function, toggle).repeat)
                assert got == (1, 1 - (function >> 6), toggle, device, function), (
                    device, function, toggle)


def test_the_frame_opens_on_a_mark_and_carries_its_first_start_bit():
    """S1 is always 1, so a frame is a lone mark first. A frame that begins
    without S1 is the Meridian MSR defect (DESIGN section 16): it comes out
    as thirteen bits, and ``_decode`` rejects it."""
    repeat = _encode().repeat
    assert len(repeat) % 2 == 0
    assert _decode(repeat)[0] == 1


def test_second_start_bit_is_the_complement_of_command_bit_six():
    """What lifts RC-5's command range from 63 to 127."""
    assert _decode(_encode(function=63).repeat)[1] == 1
    assert _decode(_encode(function=64).repeat)[1] == 0
    assert _decode(_encode(function=127).repeat)[1] == 0


def test_toggle_changes_only_the_toggle_bit():
    a = _decode(_encode(19, 12, toggle=0).repeat)
    b = _decode(_encode(19, 12, toggle=1).repeat)
    assert a[2] == 0 and b[2] == 1
    assert a[:2] + a[3:] == b[:2] + b[3:]


def test_a_file_cannot_set_the_toggle_so_it_defaults_to_zero():
    """D3b: ``toggle`` exists for the tests and for a future second candidate
    group; the default is the IRP's own T=0."""
    plain = RC5.encode(device=19, subdevice=None, function=12, carrier_hz=36_000)
    assert plain == _encode(toggle=0)


@pytest.mark.parametrize("function", [0, 1, 12, 63, 64, 127])
def test_the_frame_pads_to_its_extent_from_the_first_mark(function):
    """Both endings: the last bit is F0, and a 0 ends the frame on a space
    that the extent lengthens, where a 1 ends it on a mark that needs a gap
    appended. Either way the whole sequence is 114 ms."""
    assert sum(_encode(function=function).repeat) == RC5.extent_us


def test_the_frame_before_its_gap_is_twenty_seven_half_bits():
    """14 bits make 28 half-bits; the first is the dropped idle."""
    repeat = _encode(function=1).repeat   # ends on a mark, so the gap is its own element
    assert sum(repeat[:-1]) == 27 * UNIT


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "0073"            # 36 kHz
    # Biphase: a run is one half-bit or two, never more.
    assert set(words[4:-1]) <= {0x20, 0x40}


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=32), "D:5 holds 0-31"),
        (dict(device=-1), "D:5 holds 0-31"),
        (dict(function=128), "F is 0-127"),
        (dict(function=-1), "F is 0-127"),
        (dict(toggle=2), "T:1 is 0 or 1"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="no subdevice"):
        RC5.encode(device=19, subdevice=3, function=12, carrier_hz=36_000)


# --- the Meridian MSR ---------------------------------------------------------

MSR = ROOT / "remotes" / "meridian" / "MSR.json"
COMPILED = ROOT / "build" / "pronto" / "meridian" / "MSR.json"

#: What the LIRC import used to ship for the MSR's "Off": lircd's expansion of
#: a conf with ``bits 13`` and no ``plead``. Kept because it is the shape of
#: the bug, not because anything should ever emit it again.
LIRC_OFF = [889, 889, 889, 889, 1778, 889, 889, 1778, 889, 889, 1778, 889,
            889, 1778, 889, 889, 1778, 889, 889, 92456]


def test_the_old_lirc_frame_is_not_an_rc5_frame():
    """Thirteen bits: S1 is missing. A receiver that finds S1 in the first
    mark reads every later bit one place off."""
    assert _decode(LIRC_OFF) is None


def test_the_lirc_frame_is_ours_less_its_first_start_bit():
    """Same address, command and toggle, minus the lone mark and its space."""
    ours = _encode(device=19, function=12, toggle=1).repeat
    assert list(ours[2:-1]) == LIRC_OFF[:-1]


def test_every_msr_key_compiles_to_a_valid_rc5_frame_at_address_19():
    source = json.loads(MSR.read_text())["keys"]
    compiled = json.loads(COMPILED.read_text())["keys"]
    assert source.keys() == compiled.keys()
    for name, spec in source.items():
        form = spec["forms"][0]
        hex_ = compiled[name]["candidates"]["primary"]["prontoHex"]
        signal = pronto.decode(hex_, carrier_hz=36_000)
        frame = _decode(signal.repeat)
        assert frame is not None, f"{name}: not a 14-bit RC-5 frame"
        # the formatter writes these as "0x13"-style strings
        assert frame[0] == 1 and frame[3] == 19 == int(str(form["device"]), 0), name
        assert frame[4] == int(str(form["function"]), 0), name


def test_the_curated_msr_shadows_the_lirc_import():
    """R19.4: an authored remote of the same name wins, so the next import
    skips the broken block instead of writing it back."""
    from remote_ledger.import_common import authored_names

    names = authored_names(ROOT, "remotes/lirc")
    assert names[("meridian", "msr")] == "remotes/meridian/MSR.json"
    assert not (ROOT / "remotes" / "lirc" / "meridian" / "MSR.json").exists()
