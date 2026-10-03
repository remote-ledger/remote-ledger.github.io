"""NECx1 invariants (D18 gate 3).

NECx1 is NECx2's 8-unit-lead-in frame followed by a short repeat frame that
carries one bit, ``~D:1``. These check the framing, the byte order, and that
repeat's bit against the IRP's definition for every device. They cannot catch
a consistently wrong unit; the vectors in tests/vectors/CITATIONS.md do that.
"""

import pytest

from nec_family_helpers import EXTENT_US, UNIT_US, read_frame
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import NECX1, NECX2, REGISTRY
from remote_ledger.pronto import encode, parse_words

PARAMS = dict(device=0x2C, subdevice=0x2C, function=0x04, carrier_hz=38_400)


@pytest.fixture
def signal():
    return NECX1.encode(**PARAMS)


def test_registered_and_machine_readable():
    assert REGISTRY["NECx1"] is NECX1
    assert NECX1.unit_us == UNIT_US
    assert NECX1.extent_us == EXTENT_US
    assert NECX1.bits == 32
    assert NECX1.nominal_carrier_hz == 38_400
    assert "D:8,S:8,F:8,~F:8" in NECX1.irp
    assert "(8,-8,~D:1,1,^108m)*" in NECX1.irp


def test_each_sequence_pads_to_its_own_extent(signal):
    """D31: the extent sits inside a sequence, so intro and repeat each pad
    to 108 ms and are never summed."""
    assert sum(signal.intro) == EXTENT_US
    assert sum(signal.repeat) == EXTENT_US


def test_pair_counts(signal):
    """Intro: 1 lead-in + 32 bits + 1 stop-and-gap = 34 pairs. Repeat: lead-in,
    one bit, stop-and-gap = 3 pairs."""
    words = parse_words(encode(signal))
    assert words[2] == 34
    assert words[3] == 3


def test_both_lead_ins_are_eight_units_and_eight(signal):
    assert signal.intro[:2] == (8 * UNIT_US, 8 * UNIT_US) == (4512, 4512)
    assert signal.repeat[:2] == (4512, 4512)


def test_intro_is_exactly_necx2s_frame():
    """NECx1 and NECx2 share the 8-unit frame; only what follows differs."""
    assert NECX1.encode(**PARAMS).intro == NECX2.encode(**PARAMS).repeat


def test_bytes_are_device_subdevice_function_and_its_complement(signal):
    d, s, f, e = read_frame(signal.intro, 8, 8)
    assert (d, s, f) == (0x2C, 0x2C, 0x04)
    assert e == 0xFB
    assert f ^ e == 0xFF


def test_repeat_is_lead_in_one_bit_stop_mark_and_gap(signal):
    mark, space, bit_mark, bit_space, stop, gap = signal.repeat
    assert (mark, space, bit_mark, stop) == (4512, 4512, 564, 564)
    assert bit_space in (564, 1692)
    assert gap == EXTENT_US - (mark + space + bit_mark + bit_space + stop)


def test_repeat_bit_is_the_low_bit_of_the_complement_of_d():
    """``~D:1``: a one-bit (3 units of space) when D is even, a zero-bit when
    D is odd. Every D, so neither polarity rests on a single example."""
    for device in range(256):
        sig = NECX1.encode(
            device=device, subdevice=0x34, function=0x56, carrier_hz=38_400
        )
        want = 3 * UNIT_US if device % 2 == 0 else UNIT_US
        assert sig.repeat[3] == want, f"D={device}"


def test_repeat_does_not_depend_on_subdevice_or_function():
    a = NECX1.encode(device=0x2C, subdevice=0x00, function=0x00, carrier_hz=38_400)
    b = NECX1.encode(device=0x2C, subdevice=0xFF, function=0xFF, carrier_hz=38_400)
    assert a.repeat == b.repeat
    assert a.intro != b.intro


def test_round_trip_every_device_and_function():
    """All 65,536 device/function pairs, with S = D as most NECx1 signals
    have it."""
    for device in range(256):
        for function in range(256):
            sig = NECX1.encode(
                device=device, subdevice=device, function=function,
                carrier_hz=38_400,
            )
            assert read_frame(sig.intro, 8, 8) == [
                device, device, function, function ^ 0xFF,
            ]


def test_round_trip_every_subdevice():
    for subdevice in range(256):
        sig = NECX1.encode(
            device=0x5A, subdevice=subdevice, function=0xC3, carrier_hz=38_400
        )
        assert read_frame(sig.intro, 8, 8) == [0x5A, subdevice, 0xC3, 0x3C]


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
        NECX1.encode(carrier_hz=38_400, **params)


def test_oversized_unit_cannot_exceed_the_extent():
    """D31: an over-extent frame is an error, never a clamp, and the message
    names NECx1."""
    with pytest.raises(EncodeError, match=r"NECx1 intro.*Clamping would fabricate"):
        NECX1.encode(**{**PARAMS, "unit_us": 1100})


def test_carrier_is_the_files_not_the_registrys(signal):
    at_38k = NECX1.encode(**{**PARAMS, "carrier_hz": 38_000})
    assert at_38k.carrier_hz == 38_000
    assert signal.carrier_hz == 38_400
    assert (at_38k.intro, at_38k.repeat) == (signal.intro, signal.repeat)
