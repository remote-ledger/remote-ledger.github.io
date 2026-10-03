"""NEC2 invariants (D18 gate 3).

NEC2 is NEC1's frame, repeated whole instead of as a ditto. These check the
framing: lead-in, byte order and the complement relation, the extent, and
the one thing that separates NEC2 from NEC1 -- what the repeat is. They
cannot catch a consistently wrong unit; the vectors in
tests/vectors/CITATIONS.md do that.
"""

import pytest

from nec_family_helpers import EXTENT_US, UNIT_US, read_frame
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import NEC1, NEC2, REGISTRY
from remote_ledger.pronto import encode, parse_words

PARAMS = dict(device=0x1F, subdevice=0xE0, function=0xDF, carrier_hz=38_400)


@pytest.fixture
def signal():
    return NEC2.encode(**PARAMS)


def test_registered_and_machine_readable():
    assert REGISTRY["NEC2"] is NEC2
    assert NEC2.unit_us == UNIT_US
    assert NEC2.extent_us == EXTENT_US
    assert NEC2.bits == 32
    assert NEC2.nominal_carrier_hz == 38_400
    assert "D:8,S:8,F:8,~F:8" in NEC2.irp
    assert NEC2.irp.endswith(")*")


def test_the_frame_is_the_repeat_and_there_is_no_intro(signal):
    """`(...)*` leaves the intro empty: the Pronto says 0000 / 0022."""
    assert signal.intro == ()
    words = parse_words(encode(signal))
    assert words[2] == 0
    assert words[3] == 34


def test_frame_pads_to_its_own_extent(signal):
    assert sum(signal.repeat) == EXTENT_US


def test_lead_in_is_sixteen_units_and_eight(signal):
    assert signal.repeat[0] == 16 * UNIT_US == 9024
    assert signal.repeat[1] == 8 * UNIT_US == 4512


def test_the_frame_is_exactly_nec1s_intro_and_only_the_repeat_differs():
    """What NEC2 shares with NEC1 and what it does not: the same 34 pairs,
    but NEC1 follows them with a 3-pair ditto and NEC2 repeats the lot."""
    nec2 = NEC2.encode(**PARAMS)
    nec1 = NEC1.encode(**PARAMS)
    assert nec2.repeat == nec1.intro
    assert nec2.repeat != nec1.repeat
    assert nec2.intro == ()


def test_bytes_are_device_subdevice_function_and_its_complement(signal):
    d, s, f, e = read_frame(signal.repeat, 16, 8)
    assert (d, s, f) == (0x1F, 0xE0, 0xDF)
    assert e == 0x20
    assert f ^ e == 0xFF


def test_bit_order_is_lsb_first():
    """D=0x01 puts its one-bit first on the wire; D=0x80 puts it last of
    the byte. An MSB-first encoder would swap them."""
    low = NEC2.encode(device=0x01, subdevice=0, function=0, carrier_hz=38_400)
    high = NEC2.encode(device=0x80, subdevice=0, function=0, carrier_hz=38_400)
    first_bit_space = lambda s: s.repeat[3]
    last_bit_space = lambda s: s.repeat[2 + 2 * 7 + 1]
    assert first_bit_space(low) == 3 * UNIT_US and last_bit_space(low) == UNIT_US
    assert first_bit_space(high) == UNIT_US and last_bit_space(high) == 3 * UNIT_US


def test_round_trip_every_device_and_function():
    """All 65,536 device/function pairs, with S = ~D; the encoder does not
    couple S to either, which the next test shows."""
    for device in range(256):
        for function in range(256):
            sig = NEC2.encode(
                device=device, subdevice=device ^ 0xFF, function=function,
                carrier_hz=38_400,
            )
            assert read_frame(sig.repeat, 16, 8) == [
                device, device ^ 0xFF, function, function ^ 0xFF,
            ]


def test_round_trip_every_subdevice():
    for subdevice in range(256):
        sig = NEC2.encode(
            device=0x5A, subdevice=subdevice, function=0xC3, carrier_hz=38_400
        )
        assert read_frame(sig.repeat, 16, 8) == [0x5A, subdevice, 0xC3, 0x3C]


@pytest.mark.parametrize(
    "params,match",
    [
        (dict(device=0x100, subdevice=0, function=0), "D:8 and F:8"),
        (dict(device=0, subdevice=0, function=0x100), "D:8 and F:8"),
        (dict(device=-1, subdevice=0, function=0), "D:8 and F:8"),
        (dict(device=0, subdevice=0x100, function=0), "S:8 holds"),
        (dict(device=0, subdevice=None, function=0), "explicit subdevice"),
    ],
)
def test_parameter_bounds(params, match):
    with pytest.raises(EncodeError, match=match):
        NEC2.encode(carrier_hz=38_400, **params)


def test_oversized_unit_cannot_exceed_the_extent():
    """D31: an over-extent frame is an error, never a clamp, and the message
    names NEC2 rather than NEC1."""
    with pytest.raises(EncodeError, match=r"NEC2 frame.*Clamping would fabricate"):
        NEC2.encode(**{**PARAMS, "unit_us": 900})


def test_carrier_is_the_files_not_the_registrys(signal):
    """D3: nominal_carrier_hz is informational; the file's value is used."""
    at_38k = NEC2.encode(**{**PARAMS, "carrier_hz": 38_000})
    assert at_38k.carrier_hz == 38_000
    assert signal.carrier_hz == 38_400
    assert at_38k.repeat == signal.repeat
