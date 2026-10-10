"""GI_Cable invariants (D18 gate 3, DESIGN section 45).

The decoder is written from the frame layout, not from the encoder: an 18,9-unit lead-in, sixteen bits least
significant first (F:8, D:4, C:4; a one-unit mark and a space of 4.5 units for a 0 and 9 for a 1), a stop mark and an
84-unit gap, then the repeat (18, 4.5, 1 and 178 units). ``C`` is checked against ``-(D + F:4 + F:4:4)``.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import GI_CABLE

UNIT = GI_CABLE.unit_us
CARRIER = 38_700


def _encode(device=0, function=1, **kw):
    return GI_CABLE.encode(device=device, subdevice=None, function=function, carrier_hz=CARRIER, **kw)


def _decode(intro, unit=UNIT):
    """``(D, F)`` or None, the check nibble verified."""
    u = [d / unit for d in intro]
    if len(u) != 36 or (u[0], u[1]) != (18, 9) or (u[-2], u[-1]) != (1, 84):
        return None
    bits = []
    for mark, space in zip(u[2:-2:2], u[3:-2:2]):
        if mark != 1 or space not in (4.5, 9):
            return None
        bits.append(int(space == 9))
    f = sum(b << i for i, b in enumerate(bits[:8]))
    d = sum(b << i for i, b in enumerate(bits[8:12]))
    c = sum(b << i for i, b in enumerate(bits[12:16]))
    return (d, f) if c == (-(d + (f & 15) + (f >> 4))) & 15 else None


def test_registry_metadata():
    assert GI_CABLE.name == "GI_Cable" and GI_CABLE.unit_us == 490 and GI_CABLE.bits == 16
    assert GI_CABLE.nominal_carrier_hz == 38_700 and GI_CABLE.extent_us is None
    assert GI_CABLE.irp.startswith("{38.7k,490}<1,-4.5|1,-9>(18,-9,F:8,D:4,C:4,1,-84,(18,-4.5,1,-178)*)")


def test_every_device_and_function_round_trips():
    """4,096 frames, all of them, the check nibble included."""
    for device in range(16):
        for function in range(256):
            assert _decode(_encode(device, function).intro) == (device, function), (device, function)


def test_the_repeat_is_the_short_frame_and_the_same_for_every_key():
    assert (18 * UNIT, UNIT, 178 * UNIT) == (8_820, 490, 87_220)
    for device, function in ((0, 0), (15, 255), (3, 77)):
        assert _encode(device, function).repeat == (8_820, 2_205, 490, 87_220)


def test_the_words_have_an_intro_of_18_pairs_and_a_repeat_of_two():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "006B" and words[2:4] == [0x12, 0x02]


def test_the_fields_are_sent_function_then_device_then_the_check_least_significant_first():
    spaces = lambda s: [d // 245 for d in s.intro[3:-2:2]]                  # 9 for a 4.5-unit space, 18 for 9 units
    one = _encode(device=0, function=1)
    assert spaces(one)[:8] == [18] + [9] * 7                                  # F = 1: its one is the first bit
    assert spaces(one)[8:12] == [9] * 4                                       # D = 0
    assert spaces(one)[12:16] == [18] * 4                                     # C = -(0 + 1 + 0) = 15
    assert spaces(_encode(device=1, function=0))[8:12] == [18, 9, 9, 9]       # D = 1, least significant first


def test_the_check_nibble_makes_the_nibble_sum_vanish_modulo_16():
    for device in range(16):
        for function in range(0, 256, 7):
            bits = [int(d // 245 == 18) for d in _encode(device, function).intro[3:-2:2]]
            c = sum(b << i for i, b in enumerate(bits[12:16]))
            assert (device + (function & 15) + (function >> 4) + c) % 16 == 0


def test_the_intro_has_the_irps_fixed_84_unit_gap_so_its_period_follows_the_bits():
    """The IRP's gap is fixed; the capture of a real remote pads every frame to one period of about 98.95 ms
    (tests/vectors/CITATIONS.md). The encoder follows the IRP, which this pins: the intro is 98,000 us plus 2,205 us
    for each one bit."""
    for device, function in ((0, 0), (0, 1), (15, 255), (3, 77)):
        signal = _encode(device, function)
        ones = sum(d > 3_000 for d in signal.intro[3:-2:2])
        assert sum(signal.intro) == 98_000 + 2_205 * ones


def test_a_half_unit_rounds_half_up_on_integers():
    signal = _encode(device=0, function=0, unit_us=491)                       # 4.5 * 491 = 2209.5
    assert signal.intro[3] == 2210 and signal.repeat[1] == 2210
    assert signal.intro[0] == 18 * 491 and signal.intro[-1] == 84 * 491 and signal.repeat[-1] == 178 * 491


def test_a_broken_check_nibble_is_not_a_frame():
    intro = list(_encode(device=0, function=1).intro)
    intro[2 + 2 * 12 + 1] = 2_205                  # the first bit of C: a 1 becomes a 0
    assert _decode(tuple(intro)) is None


@pytest.mark.parametrize("kwargs, match", [
    (dict(device=16), "D:4"), (dict(device=-1), "D:4"), (dict(function=256), "F:8"), (dict(function=-1), "F:8"),
])
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="no subdevice"):
        GI_CABLE.encode(device=0, subdevice=1, function=1, carrier_hz=CARRIER)
