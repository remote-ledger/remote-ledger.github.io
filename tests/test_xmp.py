"""XMP-1 invariants (D18 gate 3, DESIGN section 52).

The decoder is written from the frame layout, not from the encoder: two subframes of eight nibbles, each nibble a 210 us
mark and a space of 760 us plus 136 us times its value (the sixth, 1,449 us, is the database's own figure), each subframe
followed by a mark and a gap (13.8 ms, 80.4 ms); the first subframe S's high nibble, C1, S's low nibble, 15, OEM and D, the
second S's high nibble, C2, the toggle nibble, S's low nibble, F and two zero nibbles. The checksums are written here from
the IRP's formulas and not copied from the encoder. A transmission is the intro (toggle nibble 0) and the repeat (8).
"""

import random
from pathlib import Path

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import XMP1
from remote_ledger.remote import load_remote

ROOT = Path(__file__).resolve().parent.parent
CARRIER = 38_000
SPACES = (760, 896, 1032, 1168, 1304, 1449, 1576, 1712, 1848, 1984, 2120, 2256, 2392, 2528, 2664, 2800)


def _encode(device=62, subdevice=16, function=4, **kw):
    return XMP1.encode(device=device, subdevice=subdevice, function=function, carrier_hz=CARRIER, **kw)


def _checksum(*values):
    return -sum(values) & 15


def decode(frame, scale=1.0):
    """``(D, S, F, T, OEM)`` of one transmission of 36 durations, or None where it is not XMP-1's."""
    if len(frame) != 36:
        return None
    nibbles = []
    for start in (0, 18):
        part = frame[start:start + 18]
        if not 100 < part[16] < 400 or part[17] < 8_000:
            return None
        values = []
        for mark, space in zip(part[0:16:2], part[1:16:2]):
            if not 100 < mark < 400:                    # 210 us in the IRP; a conf's marks run from 150 to 300
                return None
            n = min(range(16), key=lambda i: abs(space / scale - SPACES[i]))
            if abs(space / scale - SPACES[n]) > 60:
                return None
            values.append(n)
        nibbles.append(values)
    (s_hi, c1, s_lo, fixed, oem_hi, oem_lo, d_hi, d_lo), (s2_hi, c2, t, s2_lo, f_hi, f_lo, z1, z2) = nibbles
    s, oem, d, f = s_hi << 4 | s_lo, oem_hi << 4 | oem_lo, d_hi << 4 | d_lo, f_hi << 4 | f_lo
    if (s2_hi, s2_lo) != (s_hi, s_lo) or fixed != 15 or (z1, z2) != (0, 0):
        return None
    if c1 != _checksum(s, s_hi, 15, oem, oem_hi, d, d_hi) or c2 != _checksum(s, s_hi, t, 256 * f, 16 * f, f, f_hi):
        return None
    return d, s, f, t, oem


def test_registry_metadata():
    assert XMP1.name == "XMP-1" and XMP1.nominal_carrier_hz == 38_000 and XMP1.bits == 64
    assert XMP1.extent_us is None, "the gaps are gaps and not an extent"
    assert XMP1.irp.startswith("{38k,136,msb}<210u,-760u|") and "(F*256):16" in XMP1.irp and "OEM:0..255=68" in XMP1.irp


def test_every_function_of_several_addresses_every_device_of_several_subdevices_and_every_subdevice_round_trips():
    for device, subdevice in ((84, 0), (62, 16), (0, 0), (255, 255), (71, 17), (26, 48)):
        for function in range(256):
            signal = _encode(device, subdevice, function)
            assert decode(signal.intro) == (device, subdevice, function, 0, 68)
            assert decode(signal.repeat) == (device, subdevice, function, 8, 68)
    for subdevice in (0, 16, 255):
        for device in range(256):
            assert decode(_encode(device, subdevice, 33).intro) == (device, subdevice, 33, 0, 68)
    for device in (0, 62, 255):
        for subdevice in range(256):
            assert decode(_encode(device, subdevice, 33).repeat) == (device, subdevice, 33, 8, 68)


def test_a_seeded_sample_round_trips():
    rng = random.Random(1952)
    for _ in range(4000):
        d, s, f = rng.randrange(256), rng.randrange(256), rng.randrange(256)
        signal = _encode(d, s, f)
        assert decode(signal.intro) == (d, s, f, 0, 68) and decode(signal.repeat) == (d, s, f, 8, 68)


def test_a_transmission_is_the_intro_with_the_toggle_nibble_0_and_the_repeat_with_it_8_and_no_final_frame():
    signal = _encode()
    assert len(signal.intro) == len(signal.repeat) == 36 and signal.ending == ()
    differing = [i for i, (a, b) in enumerate(zip(signal.intro, signal.repeat)) if a != b]
    # the toggle nibble (the third of the second subframe) and the checksum nibble that holds it
    assert differing == [18 + 2 * 1 + 1, 18 + 2 * 2 + 1]
    words = pronto.parse_words(pronto.encode(signal))
    assert (words[2], words[3]) == (18, 18) and f"{words[1]:04X}" == "006D"


def test_every_mark_is_210_us_the_gaps_are_13_8_and_80_4_ms_and_the_spaces_are_the_ircs():
    signal = _encode(84, 0, 84)
    for frame in (signal.intro, signal.repeat):
        assert set(frame[0:16:2] + frame[18:34:2]) == {210} and frame[16] == frame[34] == 210
        assert (frame[17], frame[35]) == (13_800, 80_400)
        assert {*frame[1:16:2], *frame[19:34:2]} <= set(SPACES)
    # the sixth space is the database's 1,449 us and not 1,440: D = 84 puts a 5 in subframe 1
    assert signal.intro[1 + 2 * 6] == 1449


def test_the_fields_are_sent_s_then_the_checksum_then_oem_d_and_f_most_significant_nibble_first():
    """A swapped order or nibble order fails here, and a round trip through the same order would not notice."""
    s = _encode(0, 0x10, 0).intro
    assert [SPACES.index(x) for x in s[1:16:2]] == [1, 8, 0, 15, 4, 4, 0, 0]         # S = 0x10: C1 = -(16 + 1 + 15 + 68 + 4) = 8
    d = _encode(0x21, 0, 0).intro
    assert [SPACES.index(x) for x in d[13:16:2]] == [2, 1]
    f = _encode(0, 0, 0x34).intro
    assert [SPACES.index(x) for x in f[18 + 1 + 8:18 + 16:2]] == [3, 4, 0, 0]


def test_a_unit_is_refused_and_a_missing_subdevice_and_out_of_range_fields_too():
    with pytest.raises(EncodeError, match="no unit to scale"):
        _encode(unit_us=136)
    with pytest.raises(EncodeError, match="subdevice"):
        XMP1.encode(device=1, subdevice=None, function=1, carrier_hz=CARRIER)
    for kwargs, match in ((dict(device=256), "D:8"), (dict(device=-1), "D:8"), (dict(subdevice=256), "S:8"),
                          (dict(subdevice=-1), "S:8"), (dict(function=256), "F:8"), (dict(function=-1), "F:8")):
        with pytest.raises(EncodeError, match=match):
            _encode(**kwargs)


# --- the LIRC project's remotes, from another source -----------------------------------------------------------------

#: The files of ``remotes/lirc`` whose raw keys are XMP-1 transmissions by the layout decoder above (166 sequences).
#: They are confs expanded by lircd, not hardware captures.
LIRC_XMP = (
    "lirc/cisco/RNG-150.json", "lirc/comcast/3067BC2-R.json", "lirc/comcast/RNG-150.json", "lirc/motorola/DTA100.json",
    "lirc/pace/DS830NS.json", "lirc/pace/DTA100.json", "lirc/panasat/IRD_DSD_1110.json",
    "lirc/scientific_atlanta/RNG-150.json", "lirc/telenet/DB-AD100.json", "lirc/thomson/DCI104ICS.json",
)


def lirc_frames():
    """``(file, key, (D, S, F, T, OEM))`` of every raw sequence of the LIRC remotes that decodes, at the scale its first
    transmission's fixed nibble 15 gives (a conf's own times are only about right)."""
    out = []
    for name in LIRC_XMP:
        for key, forms in load_remote(ROOT / "remotes" / name).keys.items():
            form = forms[0]
            for sequence in (form.intro, form.repeat):
                frame = list(sequence)[:36]
                if form.type != "raw" or len(frame) != 36:
                    continue
                got = decode(frame, scale=frame[7] / 2800)
                if got is not None:
                    out.append((name, key, got))
    return out


def test_the_lirc_remotes_of_xmp_boxes_decode_to_transmissions_the_encoder_builds():
    """166 sequences of ten LIRC remotes (Comcast, Cisco, Motorola, Pace, Thomson and Scientific Atlanta boxes at device 62
    and sub device 16, another Pace at 49 and 16, a Panasat and a Telenet) decode by the layout decoder, and the encoder
    builds a transmission that decodes to the same fields."""
    frames = lirc_frames()
    assert len(frames) == 166
    assert {(d, s) for _, _, (d, s, f, t, oem) in frames} == {(62, 16), (49, 16), (72, 18), (71, 17), (42, 17)}
    assert {oem for *_, (d, s, f, t, oem) in frames} == {68}
    for _, _, (d, s, f, t, oem) in frames:
        signal = _encode(d, s, f)
        assert decode(signal.intro if t == 0 else signal.repeat) == (d, s, f, t, 68)


# --- XMPff-1: XMP-1 with the final frame that is sent when a key is released (D217) -----------------------------------

from remote_ledger.protocols import XMPFF1


def _final(device=26, subdevice=48, function=33):
    return XMPFF1.encode(device=device, subdevice=subdevice, function=function, carrier_hz=CARRIER)


def test_xmpff1_is_xmp1_with_an_ending_that_is_the_same_frame_with_the_toggle_nibble_9():
    assert XMPFF1.name == "XMPff-1" and XMPFF1.extent_us is None and XMPFF1.bits == 64
    assert "[T=0][T=8][T=9]" in XMPFF1.irp and "[-80.4m][-80.4m][-13.8m]" in XMPFF1.irp
    for device, subdevice, function in ((26, 48, 33), (84, 0, 84), (62, 16, 0), (255, 255, 255)):
        final, plain = _final(device, subdevice, function), _encode(device, subdevice, function)
        assert (final.intro, final.repeat) == (plain.intro, plain.repeat) and plain.ending == ()
        assert decode(final.ending) == (device, subdevice, function, 9, 68)


def test_the_ending_has_the_short_gap_where_the_others_have_80_4_ms_and_differs_in_the_toggle_and_the_checksum_only():
    signal = _final()
    assert (signal.ending[17], signal.ending[35]) == (13_800, 13_800) and signal.repeat[35] == 80_400
    differing = [i for i, (a, b) in enumerate(zip(signal.repeat, signal.ending)) if a != b]
    assert differing == [18 + 2 * 1 + 1, 18 + 2 * 2 + 1, 35]       # C2's space, the toggle nibble's, the last gap


def test_every_function_of_several_addresses_has_an_ending_that_round_trips():
    for device, subdevice in ((26, 48), (0, 0), (255, 255)):
        for function in range(256):
            assert decode(_final(device, subdevice, function).ending) == (device, subdevice, function, 9, 68)


def test_the_pronto_string_of_a_final_frame_signal_is_xmp1s_and_the_ending_is_a_string_of_its_own():
    final, plain = _final(), _encode(26, 48, 33)
    assert pronto.encode(final) == pronto.encode(plain)
    ending = pronto.encode_ending(final)
    assert ending.startswith("0000 006D 0012 0000 ") and len(ending.split()) == 4 + 36


def test_a_final_frame_unit_and_missing_subdevice_are_refused_in_its_own_name():
    with pytest.raises(EncodeError, match="XMPff-1 has no unit"):
        XMPFF1.encode(device=1, subdevice=2, function=3, carrier_hz=CARRIER, unit_us=136)
    with pytest.raises(EncodeError, match="XMPff-1 requires an explicit subdevice"):
        XMPFF1.encode(device=1, subdevice=None, function=3, carrier_hz=CARRIER)
    with pytest.raises(EncodeError, match="XMPff-1 function is 256"):
        _final(function=256)
