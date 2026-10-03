"""Sony12 invariants (D18 gate 3).

Like Sony20's, these catch broken *framing*. What anchors the constants to the
outside world is the cited golden vectors (tests/vectors/CITATIONS.md):
Girr's published Sony12 reference at D=1, F=21, and an IrpTransmogrifier
render at D=23, F=70. The frame reader is in sirc_reference.py and is written
from the layout, not from the encoder.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import REGISTRY, SONY12
from sirc_reference import EXTENT, UNIT, field, read_bits


def _encode(device=1, function=21, **extra):
    return SONY12.encode(device=device, subdevice=None, function=function,
                         carrier_hz=40_000, **extra)


def test_registered_under_the_name_irptransmogrifier_uses():
    assert REGISTRY["Sony12"] is SONY12
    assert SONY12.name == "Sony12"


def test_registry_metadata():
    assert SONY12.unit_us == 600
    assert SONY12.extent_us == 45_000
    assert SONY12.bits == 12
    assert SONY12.nominal_carrier_hz == 40_000
    assert "(4,-1,F:7,D:5,^45m)" in SONY12.irp
    assert "D:0..31" in SONY12.irp


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_device_and_function_round_trips():
    """4,096 frames, the whole space. A swapped field order or a reversed bit
    order sits at specific values a sample would skip."""
    for device in range(32):
        for function in range(128):
            bits = read_bits(_encode(device, function).repeat)
            assert bits is not None and len(bits) == 12, (device, function)
            assert (field(bits, 0, 7), field(bits, 7, 5)) == (function, device)


def test_every_frame_pads_to_its_extent():
    for device in range(32):
        for function in range(128):
            assert sum(_encode(device, function).repeat) == EXTENT


def test_pair_count_is_lead_in_plus_twelve_bits():
    assert len(_encode().repeat) == 2 * 13
    assert pronto.parse_words(pronto.encode(_encode()))[3] == 13


def test_the_extent_lengthens_the_final_space_rather_than_appending():
    repeat = _encode().repeat
    assert len(repeat) % 2 == 0
    assert repeat[-1] > UNIT


def test_function_is_the_low_seven_bits_on_the_wire_first():
    """F:7 comes before D:5 and is least significant first: F=1 is a long
    first data mark, and D=1 a long eighth."""
    f1 = _encode(device=0, function=1).repeat
    d1 = _encode(device=1, function=0).repeat
    marks = lambda r: [m for m in r[2::2]]
    assert marks(f1) == [2 * UNIT] + [UNIT] * 11
    assert marks(d1) == [UNIT] * 7 + [2 * UNIT] + [UNIT] * 4


def test_tv_power_is_a90_in_transmission_order():
    """Sony's best-known code. D=1, F=21 is 1010100 10000 on the wire, which
    read most-significant-first is 0xA90 -- the same code written the other
    way round is 0x095."""
    bits = read_bits(_encode(device=1, function=21).repeat)
    assert int("".join(map(str, bits)), 2) == 0xA90


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
        (dict(device=32), "D:5 holds 0-31"),
        (dict(device=-1), "D:5 holds 0-31"),
    ],
)
def test_parameter_bounds(params, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**params)


def test_a_subdevice_is_refused_and_points_at_sony20():
    with pytest.raises(EncodeError, match="Sony20"):
        SONY12.encode(device=1, subdevice=7, function=21, carrier_hz=40_000)
