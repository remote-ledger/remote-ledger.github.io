"""MCE invariants (D18 gate 3, DESIGN section 41).

The decoder is written from the frame layout, not from the encoder: a 6-unit mark and 2-unit space, a start bit, the
mode ``110``, a double-width trailer, then 32 payload bits (OEM1, S, T, D, F), every bit biphase at one unit a half
(a ``1`` is a mark then a space). The payload is small enough to sweep: every D and F for several S, every S and F
for several D, and a seeded sample.
"""

import json
import random
from pathlib import Path

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import MCE, RC6
from remote_ledger.remote import load_remote

ROOT = Path(__file__).resolve().parent.parent
UNIT = MCE.unit_us
EXTENT = MCE.extent_us
CARRIER = 36_000


def _encode(device=116, subdevice=15, function=40, **kw):
    return MCE.encode(device=device, subdevice=subdevice, function=function, carrier_hz=CARRIER, **kw)


def _levels(durations, unit):
    """Alternating mark and space durations as per-unit levels, 1 a mark."""
    out = []
    for i, d in enumerate(durations):
        out += [1 - i % 2] * max(1, round(d / unit))
    return out


def decode(durations, unit=UNIT):
    """``(mode, trailer, OEM1, S, T, D, F)`` of a frame from its durations, or None where it is not one."""
    if len(durations) < 4 or round(durations[0] / unit) != 6 or round(durations[1] / unit) != 2:
        return None
    levels = _levels(durations, unit)[8:]
    position = 0

    def bit(width=1):
        nonlocal position
        pair = (levels[position], levels[position + width])
        if any(v != pair[0] for v in levels[position:position + width]) or \
                any(v != pair[1] for v in levels[position + width:position + 2 * width]):
            raise ValueError("not biphase")
        if pair not in ((1, 0), (0, 1)):
            raise ValueError("not biphase")
        position += 2 * width
        return int(pair == (1, 0))

    def field(count):
        value = 0
        for _ in range(count):
            value = value << 1 | bit()
        return value

    try:
        if bit() != 1:
            return None
        mode = field(3)
        trailer = bit(2)
        return (mode, trailer, field(8), field(8), field(1), field(7), field(8))
    except (ValueError, IndexError):
        return None


def test_registry_metadata():
    assert MCE.unit_us == 444 and MCE.extent_us == 107_000 and MCE.nominal_carrier_hz == 36_000
    assert MCE.bits == 37
    assert "6:3,-2,2,OEM1:8,S:8,T:1,D:7,F:8" in MCE.irp and "{OEM1=128}" in MCE.irp


def test_the_frame_is_the_leader_a_start_bit_mode_six_a_zero_trailer_and_oem1_128():
    got = decode(_encode().repeat)
    assert got[:3] == (6, 0, 128)
    # the leader, in microseconds
    assert _encode().repeat[:2] == (6 * UNIT, 2 * UNIT)


def test_every_device_and_function_round_trips_for_several_second_oem_bytes_and_every_s_and_f_for_several_devices():
    for s in (0, 15, 255):
        for d in range(128):
            for f in range(256):
                assert decode(_encode(d, s, f).repeat) == (6, 0, 128, s, 0, d, f), (d, s, f)
    for d in (0, 4, 116, 127):
        for s in range(256):
            for f in (0, 12, 40, 255):
                assert decode(_encode(d, s, f).repeat) == (6, 0, 128, s, 0, d, f), (d, s, f)


def test_a_seeded_sample_round_trips_with_the_toggle_either_way():
    rng = random.Random(32)
    for _ in range(3_000):
        d, s, f, t = rng.randrange(128), rng.randrange(256), rng.randrange(256), rng.randrange(2)
        assert decode(_encode(d, s, f, toggle=t).repeat) == (6, 0, 128, s, t, d, f)


def test_there_is_no_intro_and_the_frame_pads_to_its_extent_from_the_first_mark():
    signal = _encode()
    assert signal.intro == () and pronto.parse_words(pronto.encode(signal))[2] == 0
    for d, s, f in ((0, 0, 0), (127, 255, 255), (116, 15, 40), (4, 15, 128)):
        assert sum(_encode(d, s, f).repeat) == EXTENT


def test_the_fields_are_sent_oem1_then_s_then_toggle_then_d_then_f():
    """A swapped order fails here and a round trip through the same order would not notice: S = 0x0F and the other
    fields zero put ones only in the second byte, and D = 0x40 puts a one only in the first bit after the toggle."""
    def ones(**kw):
        got = decode(_encode(**kw).repeat)
        return got[3:]
    assert ones(device=0, subdevice=0x0F, function=0) == (0x0F, 0, 0, 0)
    assert ones(device=0x40, subdevice=0, function=0) == (0, 0, 0x40, 0)
    assert ones(device=0, subdevice=0, function=1) == (0, 0, 0, 1)


def test_the_unit_scales_everything_but_the_extent():
    signal = _encode(unit_us=400)
    assert decode(signal.repeat, unit=400) == (6, 0, 128, 15, 0, 116, 40)
    assert sum(signal.repeat) == EXTENT


def test_it_is_rc6_with_a_longer_payload_where_they_share_a_frame():
    """RC6 mode 0 and MCE open the same way (the leader and the start bit), and differ in the mode, the width of the
    payload and nothing else: the first eight durations that RC6 and MCE share are the leader."""
    assert RC6.encode(device=1, subdevice=None, function=2, carrier_hz=CARRIER).repeat[:3] == _encode().repeat[:3]


# --- the LIRC project's remotes of four MCE devices: an independent reading of the frame ---------------------------

LIRC_MCE = ("lirc/mceusb/mceusb.mceusb.json", "lirc/mceusb/mceusb.mceusb_hauppauge.json",
            "lirc/mceusb/mceusb.vista_mce.json", "lirc/microsoft/Xbox360.json")


def lirc_frames():
    for name in LIRC_MCE:
        remote = load_remote(ROOT / "remotes" / name)
        for key, forms in remote.keys.items():
            form = forms[0]
            if form.type == "raw":
                yield name, key, form.repeat


def test_the_lirc_remotes_of_four_mce_devices_decode_to_mode_6_oem1_128_and_the_frame_the_encoder_builds():
    """Each key of the LIRC project's mceusb, mceusb_hauppauge, vista_mce and Xbox 360 remotes (225), expanded to
    raw by lircd from a conf that says only a header, a bit width, a ``pre_data`` and the codes, is
    read by the decoder above at the unit its leader has (6 units: 442 to 446 us), and every one is mode 6, trailer
    0 and OEM1 128 -- the three things the IRP fixes -- and re-encodes, with the toggle the key has, to the same
    sequence of levels. The confs' bits are 427 to 455 us, 0.96 to 1.03 of the IRP's 444: a level sequence says layout and bit
    order, not durations."""
    seen = 0
    units = set()
    for name, key, durations in lirc_frames():
        unit = durations[0] / 6
        units.add(round(unit))
        got = decode(durations, unit=unit)
        assert got is not None and got[:3] == (6, 0, 128), (name, key)
        _, _, _, s, t, d, f = got
        assert s == 15, (name, key)                       # Microsoft's own second OEM byte
        ours = _encode(d, s, f, toggle=t).repeat
        assert _levels(ours[:-1], UNIT) == _levels(durations[:len(ours) - 1], unit), (name, key)
        seen += 1
    assert seen == 225 and units <= {442, 444, 446}


def test_the_xbox_360_remote_is_device_116_as_the_jp1_upgrade_for_it_says():
    """The forum's `Xbox 360 Universal Media Remote` upgrade has parameters `116 15` (device 116, second OEM byte 15)."""
    devices = {decode(d, unit=d[0] / 6)[5] for name, key, d in lirc_frames() if name.endswith("Xbox360.json")}
    assert devices == {116}


@pytest.mark.parametrize("kwargs, match", [
    (dict(device=128), "D:7"), (dict(device=-1), "D:7"), (dict(subdevice=256), "S:8"), (dict(subdevice=-1), "S:8"),
    (dict(subdevice=None), "explicit subdevice"), (dict(function=256), "F:8"), (dict(function=-1), "F:8"),
    (dict(toggle=2), "T:1"),
])
def test_out_of_range_fields_are_refused(kwargs, match):
    args = dict(device=1, subdevice=15, function=2, carrier_hz=CARRIER)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        MCE.encode(**args)
