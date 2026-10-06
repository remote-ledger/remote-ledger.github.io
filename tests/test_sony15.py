"""Sony15 invariants (D18 gate 3).

Sony15 differs from Sony12 and Sony20 only in how many address bits follow the
command, which is exactly where an unverified encoder hides. What anchors it
to the outside world is tests/test_sony_vectors.py (the one published Sony15
string, asserted by IrpTransmogrifier to decode as D=164, F=61, and 49
captures from its teaser set) and the IrpTransmogrifier render in
tests/vectors/CITATIONS.md. The frame reader is in sirc_reference.py and is
written from the layout, not from the encoder.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import REGISTRY, SONY15
from sirc_reference import EXTENT, UNIT, field, read_bits


def _encode(device=164, function=61, **extra):
    return SONY15.encode(device=device, subdevice=None, function=function,
                         carrier_hz=40_000, **extra)


def test_registered_under_the_name_irptransmogrifier_uses():
    assert REGISTRY["Sony15"] is SONY15
    assert SONY15.name == "Sony15"


def test_registry_metadata():
    assert SONY15.unit_us == 600
    assert SONY15.extent_us == 45_000
    assert SONY15.bits == 15
    assert SONY15.nominal_carrier_hz == 40_000
    assert "(4,-1,F:7,D:8,^45m)" in SONY15.irp
    assert "D:0..255" in SONY15.irp


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_device_and_function_round_trips():
    """32,768 frames, the whole space."""
    for device in range(256):
        for function in range(128):
            bits = read_bits(_encode(device, function).repeat)
            assert bits is not None and len(bits) == 15, (device, function)
            assert (field(bits, 0, 7), field(bits, 7, 8)) == (function, device)


def test_every_frame_pads_to_its_extent():
    for device in range(256):
        for function in range(128):
            assert sum(_encode(device, function).repeat) == EXTENT


def test_pair_count_is_lead_in_plus_fifteen_bits():
    assert len(_encode().repeat) == 2 * 16
    assert pronto.parse_words(pronto.encode(_encode()))[3] == 16


def test_the_extent_lengthens_the_final_space_rather_than_appending():
    repeat = _encode().repeat
    assert len(repeat) % 2 == 0
    assert repeat[-1] > UNIT


def test_the_device_is_eight_bits_not_five():
    """Sony12's and Sony20's device is five bits. Sony15's eight-bit field is
    the *device*, so a value past 31 is valid here and an error there."""
    assert read_bits(_encode(device=0xFF, function=0).repeat)[7:] == [1] * 8
    with pytest.raises(EncodeError, match="D:5 holds"):
        REGISTRY["Sony20"].encode(device=0xFF, subdevice=0, function=0,
                                  carrier_hz=40_000)


def test_device_bits_start_at_bit_seven_least_significant_first():
    for bit in range(8):
        marks = list(_encode(device=1 << bit, function=0).repeat[2::2])
        assert marks == [UNIT] * (7 + bit) + [2 * UNIT] + [UNIT] * (7 - bit)


def test_unit_scales_every_duration():
    base = _encode().repeat
    scaled = _encode(unit_us=300).repeat
    assert scaled[:-1] == tuple(d // 2 for d in base[:-1])
    assert sum(scaled) == EXTENT


@pytest.mark.parametrize(
    "params,match",
    [
        (dict(function=128), "F:7 holds 0-127"),
        (dict(function=-1), "F:7 holds 0-127"),
        (dict(device=256), "D:8 holds 0-255"),
        (dict(device=-1), "D:8 holds 0-255"),
    ],
)
def test_parameter_bounds(params, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**params)


def test_a_subdevice_is_refused_and_points_at_sony20():
    with pytest.raises(EncodeError, match="Sony20"):
        SONY15.encode(device=1, subdevice=7, function=21, carrier_hz=40_000)
