"""NEC1-f16 and NEC2-f16 invariants (D18 gate 3, DESIGN section 37).

The decoder is written from the frame layout, not from the encoder: a 16-unit mark and 8-unit space, four
LSB-first bytes (device, subdevice, F, E), a stop mark and the padding to 108 ms. The ledger's ``function`` is the
pair ``F * 256 + E``, so every pair (65,536) round-trips for a device and subdevice, and a seeded sample covers the
rest.
"""

import json
import random
from pathlib import Path

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import NEC1, NEC1_F16, NEC2, NEC2_F16

UNIT = NEC1_F16.unit_us
EXTENT = NEC1_F16.extent_us
CARRIER = 38_400
VECTORS = Path(__file__).parent / "vectors"


def _lsb(bits):
    return sum(b << i for i, b in enumerate(bits))


def _decode_frame(frame, unit=UNIT):
    """``(D, S, F, E)`` of one 70-duration frame (lead-in, 32 bits, stop mark, gap), or None."""
    if len(frame) != 68 or (frame[0], frame[1]) != (16 * unit, 8 * unit) or frame[66] != unit:
        return None
    bits = []
    for mark, space in zip(frame[2:66:2], frame[3:66:2]):
        if mark != unit or space not in (unit, 3 * unit):
            return None
        bits.append(int(space == 3 * unit))
    return tuple(_lsb(bits[i:i + 8]) for i in (0, 8, 16, 24))


def _nec1_f16(device=134, subdevice=228, f=2, e=96, **kw):
    return NEC1_F16.encode(device=device, subdevice=subdevice, function=(f << 8) | e, carrier_hz=CARRIER, **kw)


def _nec2_f16(device=248, subdevice=199, f=56, e=7, **kw):
    return NEC2_F16.encode(device=device, subdevice=subdevice, function=(f << 8) | e, carrier_hz=CARRIER, **kw)


def test_registry_metadata():
    for proto in (NEC1_F16, NEC2_F16):
        assert proto.unit_us == 564 and proto.extent_us == 108_000
        assert proto.bits == 32 and proto.nominal_carrier_hz == 38_400
        assert "D:8,S:8,F:8,E:8" in proto.irp
    assert NEC1_F16.irp.endswith("(16,-4,1,^108m)*)") and NEC2_F16.irp.endswith("1,^108m)*")


def test_a_fourth_byte_that_is_the_complement_is_exactly_nec1_and_nec2():
    """The IRP's default is E = 255 - F, so ``F:~F`` is the plain protocol: every F, a spread of D and S."""
    for device, subdevice in ((0, 0), (12, 243), (255, 255), (134, 228)):
        for f in range(256):
            e = (~f) & 0xFF
            assert _nec1_f16(device, subdevice, f, e) == NEC1.encode(
                device=device, subdevice=subdevice, function=f, carrier_hz=CARRIER)
            assert _nec2_f16(device, subdevice, f, e) == NEC2.encode(
                device=device, subdevice=subdevice, function=f, carrier_hz=CARRIER)


def test_every_function_and_e_round_trips():
    """65,536 pairs, exhaustive, for both protocols."""
    for f in range(256):
        for e in range(256):
            assert _decode_frame(_nec1_f16(134, 228, f, e).intro) == (134, 228, f, e), (f, e)
            assert _decode_frame(_nec2_f16(248, 199, f, e).repeat) == (248, 199, f, e), (f, e)


def test_every_device_and_subdevice_round_trips_on_a_seeded_sample():
    rng = random.Random(16)
    for _ in range(2_000):
        d, s, f, e = (rng.randrange(256) for _ in range(4))
        assert _decode_frame(_nec1_f16(d, s, f, e).intro) == (d, s, f, e)
        assert _decode_frame(_nec2_f16(d, s, f, e).repeat) == (d, s, f, e)


def test_the_function_is_f_then_e_and_f_is_the_high_byte():
    """``F * 256 + E``: F is the third byte sent and E the fourth. A swapped packing fails here and the round
    trip alone would not notice."""
    signal = NEC1_F16.encode(device=0, subdevice=0, function=0x0201, carrier_hz=CARRIER)
    assert _decode_frame(signal.intro) == (0, 0, 2, 1)


def test_the_frames_pad_to_their_extent_and_say_what_repeats():
    one, two = _nec1_f16(), _nec2_f16()
    assert sum(one.intro) == EXTENT and sum(two.repeat) == EXTENT
    # NEC1-f16 repeats NEC1's ditto (a 16-unit mark, a 4-unit space, a one-unit mark, padding to 108 ms); NEC2-f16
    # has no intro and repeats the whole frame
    assert one.repeat[:3] == (16 * UNIT, 4 * UNIT, UNIT) and sum(one.repeat) == EXTENT
    assert two.intro == () and pronto.parse_words(pronto.encode(two))[2] == 0


@pytest.mark.parametrize("style,xor", [(1, 0x7F), (2, 0xFE), (3, 0x7E)])
def test_the_yamaha_styles_are_nec1_with_e_a_function_of_f(style, xor):
    """IrpTransmogrifier's ``NEC1-Yamaha``: ``E0=(~Y:1:1)^(F:1)``, ``~F:6:1``, ``E7=(~Y:1)^(F:1:7)``. Y1 leaves bit 7
    of F as it is, Y2 leaves bit 0, Y3 leaves both, and the rest of E is F's complement: E = F ^ 0x7F, 0xFE, 0x7E."""
    for f in range(256):
        e0 = (~(style >> 1) & 1) ^ (f & 1)
        e7 = (~style & 1) ^ ((f >> 7) & 1)
        assert e0 | (~f & 0x7E) | (e7 << 7) == f ^ xor


def test_the_yamaha_capture_is_the_vector_the_registry_is_held_to():
    """FM of the Yamaha receiver IrpTransmogrifier captured (NEC1-Yamaha {D=127,S=1,F=88,Y=1}) is NEC1-f16 with
    E = 88 ^ 0x7F = 39, and its vector is byte for byte IrpTransmogrifier's own Yamaha render."""
    assert 88 ^ 0x7F == 39
    entry = next(v for v in json.loads((VECTORS / "pronto-vectors.json").read_text())
                 if v["file"] == "irpt_nec1f16_D127_S1_F88_E39.pronto")
    assert entry["params"] == {"device": 127, "subdevice": 1, "function": 88 * 256 + 39, "carrier_hz": 38400}
    assert "render -n D=127,S=1,F=88,Y=1 -p NEC1-Yamaha" in entry["reproduce"]


def test_unit_us_scales_everything_but_the_extent():
    signal = _nec1_f16(unit_us=500)
    assert _decode_frame(signal.intro, unit=500) == (134, 228, 2, 96)
    assert sum(signal.intro) == EXTENT


@pytest.mark.parametrize("kwargs, match", [
    (dict(device=256), "D:8"),
    (dict(device=-1), "D:8"),
    (dict(subdevice=256), "S:8"),
    (dict(subdevice=None), "explicit subdevice"),
    (dict(function=0x10000), "F:E"),
    (dict(function=-1), "F:E"),
])
@pytest.mark.parametrize("proto", [NEC1_F16, NEC2_F16])
def test_out_of_range_fields_are_refused(proto, kwargs, match):
    args = dict(device=1, subdevice=2, function=0x0304, carrier_hz=CARRIER)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        proto.encode(**args)
