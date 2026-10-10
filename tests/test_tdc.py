"""TDC-38 and TDC-56 invariants (D18 gate 3, DESIGN section 45).

The decoder is written from the frame layout, not from the encoder: a one-unit mark and space, then 17 biphase bits
(a 1 is a mark then a space, a 0 a space then a mark), most significant first, and the 89 ms gap. Biphase merges
neighbouring half-bits of one level, so the decoder expands every duration back into half-bits and reads pairs.
"""

import random

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import TDC38, TDC56

VARIANTS = [(TDC38, 38_000, 315), (TDC56, 56_300, 213)]
GAP = 89_000


def _encode(proto, device=6, subdevice=10, function=2, unit_us=None):
    return proto.encode(device=device, subdevice=subdevice, function=function, carrier_hz=proto.nominal_carrier_hz,
                        unit_us=unit_us)


def _decode(repeat, unit):
    """``(D, S, F)`` or None."""
    body = list(repeat[:-1])                    # the last duration is the gap, a space
    halves: list[int] = []
    for i, d in enumerate(body):
        run = round(d / unit)
        if run not in (1, 2):
            return None
        halves += [1 - i % 2] * run             # even index a mark, odd a space
    if len(halves) == 35:                       # the last bit was a 1: its space is inside the gap
        if repeat[-1] != GAP + unit:
            return None
        halves.append(0)
    elif repeat[-1] != GAP:
        return None
    if len(halves) != 36 or halves[:2] != [1, 0]:
        return None
    word = 0
    for hi, lo in zip(halves[2::2], halves[3::2]):
        if (hi, lo) not in ((1, 0), (0, 1)):
            return None
        word = word << 1 | hi
    return word >> 12, word >> 7 & 0x1F, word & 0x7F


@pytest.mark.parametrize("proto, carrier, unit", VARIANTS)
def test_registry_metadata(proto, carrier, unit):
    assert proto.unit_us == unit and proto.nominal_carrier_hz == carrier and proto.bits == 17
    assert proto.extent_us is None, "the IRP ends -89m, a gap and not an extent"
    assert proto.irp.startswith("{38k,315,msb}" if unit == 315 else "{56.3k,213,msb}")
    assert "<-1,1|1,-1>(1,-1,D:5,S:5,F:7,-89m)*" in proto.irp


@pytest.mark.parametrize("proto, carrier, unit", VARIANTS)
def test_every_function_of_several_addresses_and_every_address_of_several_functions_round_trips(proto, carrier, unit):
    for device, subdevice in ((0, 0), (6, 10), (14, 10), (31, 31), (17, 5)):
        for function in range(128):
            signal = _encode(proto, device, subdevice, function)
            assert _decode(signal.repeat, unit) == (device, subdevice, function), (device, subdevice, function)
    for function in (0, 127, 42, 85):
        for device in range(32):
            for subdevice in range(32):
                assert _decode(_encode(proto, device, subdevice, function).repeat, unit) == (device, subdevice, function)


@pytest.mark.parametrize("proto, carrier, unit", VARIANTS)
def test_a_seeded_sample_round_trips(proto, carrier, unit):
    rng = random.Random(1789)
    for _ in range(3000):
        d, s, f = rng.randrange(32), rng.randrange(32), rng.randrange(128)
        assert _decode(_encode(proto, d, s, f).repeat, unit) == (d, s, f)


@pytest.mark.parametrize("proto, carrier, unit", VARIANTS)
def test_there_is_no_intro_and_the_gap_is_89_ms_after_a_mark_or_added_to_a_space(proto, carrier, unit):
    for function, ends_on in ((2, "mark"), (5, "space")):                 # F's last bit: 0 ends on a mark, 1 on a space
        signal = _encode(proto, function=function)
        assert signal.intro == () and pronto.parse_words(pronto.encode(signal))[2] == 0
        if ends_on == "mark":
            assert signal.repeat[-2:] == (unit, GAP) and len(signal.repeat) % 2 == 0
        else:
            assert signal.repeat[-1] == GAP + unit and len(signal.repeat) % 2 == 0


@pytest.mark.parametrize("proto, carrier, unit", VARIANTS)
def test_the_start_merges_with_a_leading_zero_s_space(proto, carrier, unit):
    """D = 16 starts with a 1 (mark, space), so the start's space stays one unit; D = 0 starts with a 0 (space, mark)
    and the two spaces are one of two units."""
    assert _encode(proto, device=16).repeat[:3] == (unit, unit, unit)
    assert _encode(proto, device=0).repeat[:3] == (unit, 2 * unit, unit)


def test_the_two_variants_are_one_frame_at_two_units():
    for function in range(0, 128, 7):
        a = _encode(TDC38, 6, 10, function)
        b = _encode(TDC56, 6, 10, function, unit_us=315)
        assert a.repeat == b.repeat


def test_a_burst_is_twelve_carrier_cycles_in_both_variants():
    """What the database says of the pair: the same number of cycles in a burst, so the unit follows the carrier."""
    for proto, carrier, unit in VARIANTS:
        assert round(unit * carrier / 1e6) == 12


def test_the_unit_scales_the_halves_and_not_the_gap():
    signal = _encode(TDC38, function=5, unit_us=400)
    assert _decode(signal.repeat, 400) == (6, 10, 5) and signal.repeat[-1] == GAP + 400


@pytest.mark.parametrize("proto", [TDC38, TDC56])
@pytest.mark.parametrize("kwargs, match", [
    (dict(device=32), "D:5"), (dict(device=-1), "D:5"), (dict(subdevice=32), "S:5"), (dict(subdevice=-1), "S:5"),
    (dict(function=128), "F:7"), (dict(function=-1), "F:7"),
])
def test_out_of_range_fields_are_refused(proto, kwargs, match):
    args = dict(device=1, subdevice=2, function=3)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        _encode(proto, **args)


@pytest.mark.parametrize("proto", [TDC38, TDC56])
def test_a_missing_subdevice_is_refused(proto):
    with pytest.raises(EncodeError, match="subdevice"):
        proto.encode(device=1, subdevice=None, function=3, carrier_hz=proto.nominal_carrier_hz)
