"""Nokia32 invariants (D18 gate 3, DESIGN section 43).

The decoder is written from the frame layout, not from the encoder: a 15-period mark and 10-period space, sixteen
symbols (a 6-period mark and a space of 10, 16, 22 or 28 periods, two bits each, most significant first) and a 6-period
stop mark, with every time in periods of the file's carrier. The 32 bits are D:8, S:8, T:1, X:7, F:8.
"""

import random

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import NOKIA32

EXTENT = NOKIA32.extent_us


def _encode(device=37, subdevice=240, function=128, carrier_hz=36_000, **kw):
    return NOKIA32.encode(device=device, subdevice=subdevice, function=function, carrier_hz=carrier_hz, **kw)


def _decode(repeat, carrier_hz=36_000):
    """``(D, S, T, X, F)`` of a frame from its durations, or None where it is not one."""
    period = 1_000_000 / carrier_hz
    if len(repeat) != 36:
        return None
    periods = [d / period for d in repeat[:-1]]
    if round(periods[0]) != 15 or round(periods[1]) != 10 or round(periods[-1]) != 6:
        return None
    word = 0
    for mark, space in zip(periods[2:34:2], periods[3:34:2]):
        steps = (round(space) - 10) / 6
        if round(mark) != 6 or steps not in (0, 1, 2, 3):
            return None
        word = word << 2 | int(steps)
    return word >> 24, word >> 16 & 0xFF, word >> 15 & 1, word >> 8 & 0x7F, word & 0xFF


def test_registry_metadata():
    assert NOKIA32.nominal_carrier_hz == 36_000 and NOKIA32.extent_us == 100_000 and NOKIA32.bits == 32
    assert "{36k,1p,msb}" in NOKIA32.irp and "D:8,S:8,T:1,X:7,F:8" in NOKIA32.irp


def test_the_function_is_x_then_f_and_the_toggle_is_the_bit_between_s_and_x():
    assert _decode(_encode(function=0x0583).repeat) == (37, 240, 0, 5, 0x83)
    assert _decode(_encode(function=0x0583, toggle=1).repeat) == (37, 240, 1, 5, 0x83)


def test_every_function_round_trips_for_several_devices_and_every_device_and_subdevice_for_several_functions():
    for d, s in ((0, 0), (37, 240), (255, 255)):
        for function in range(0x8000):
            assert _decode(_encode(d, s, function).repeat) == (d, s, 0, function >> 8, function & 0xFF), (d, s, function)
    for function in (0, 128, 0x7FFF):
        for d in range(256):
            for s in (0, 1, 240, 255):
                assert _decode(_encode(d, s, function).repeat)[:2] == (d, s)


def test_a_seeded_sample_round_trips_with_the_toggle_either_way():
    rng = random.Random(36)
    for _ in range(3_000):
        d, s, fn, t = rng.randrange(256), rng.randrange(256), rng.randrange(0x8000), rng.randrange(2)
        assert _decode(_encode(d, s, fn, toggle=t).repeat) == (d, s, t, fn >> 8, fn & 0xFF)


def test_every_time_is_in_periods_of_the_carrier_so_the_frame_follows_it():
    """``1p`` is one period: at 38 kHz every mark and space is shorter in microseconds, and still the same number of
    periods; and the frame still totals its extent."""
    at36, at38 = _encode().repeat, _encode(carrier_hz=38_000).repeat
    assert at36[:2] == (417, 278) and at38[:2] == (395, 263)
    assert _decode(at38, carrier_hz=38_000) == (37, 240, 0, 0, 128)
    assert sum(at36) == sum(at38) == EXTENT


def test_there_is_no_intro_and_the_frame_pads_to_its_extent_from_the_first_mark():
    signal = _encode()
    assert signal.intro == () and pronto.parse_words(pronto.encode(signal))[2] == 0
    for d, s, fn in ((0, 0, 0), (255, 255, 0x7FFF), (37, 240, 128)):
        assert sum(_encode(d, s, fn).repeat) == EXTENT


def test_the_symbols_are_pulse_positions():
    """00, 01, 10 and 11 are spaces of 10, 16, 22 and 28 periods after a 6-period mark: D = 0b00011011 is all four."""
    repeat = _encode(device=0b00011011, subdevice=0, function=0).repeat
    assert [round(s * 36_000 / 1_000_000) for s in repeat[3:11:2]] == [10, 16, 22, 28]


@pytest.mark.parametrize("kwargs, match", [
    (dict(device=256), "D:8"), (dict(device=-1), "D:8"), (dict(subdevice=256), "S:8"), (dict(subdevice=None), "explicit subdevice"),
    (dict(function=0x8000), "X:F"), (dict(function=-1), "X:F"), (dict(toggle=2), "T:1"),
])
def test_out_of_range_fields_are_refused(kwargs, match):
    args = dict(device=1, subdevice=2, function=3, carrier_hz=36_000)
    args.update(kwargs)
    with pytest.raises(EncodeError, match=match):
        NOKIA32.encode(**args)
