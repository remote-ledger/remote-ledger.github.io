"""Thomson7 invariants (D18 gate 3).

What anchors the constants to the outside world is the cited evidence
(tests/vectors/CITATIONS.md): IrpTransmogrifier 1.2.14's rendering of one
signal (a reproducible Pronto vector) and a hardware capture of a Thomson
remote, whose seven keys ``tools/philips_capture_audit.py`` reproduces from a
checkout this suite does not carry. The first five are restated below.

The decoder is written from the frame layout, not from the encoder.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import THOMSON7

UNIT = THOMSON7.unit_us
CARRIER = 33_000


def _decode(repeat, unit=UNIT):
    """A frame back to (device, toggle, function), or None.

    ``repeat`` is mark/space durations, the gap last. Twelve bits, each a
    one-unit mark and a four- or nine-unit space, sent least significant
    first; a one-unit stop mark; then the rest of 80 ms. Anything else is None.
    """
    if len(repeat) != 12 * 2 + 2:
        return None
    units = [d / unit for d in repeat[:-1]]
    if units[-1] != 1:
        return None
    bits = []
    for mark, space in zip(units[0:-1:2], units[1:-1:2]):
        if mark != 1 or space not in (4, 9):
            return None
        bits.append(0 if space == 4 else 1)
    if sum(repeat) != THOMSON7.extent_us:
        return None
    device = sum(b << i for i, b in enumerate(bits[:4]))
    function = sum(b << i for i, b in enumerate(bits[5:]))
    return device, bits[4], function


def _encode(device=12, function=74, toggle=0):
    return THOMSON7.encode(device=device, subdevice=None, function=function,
                           carrier_hz=CARRIER, toggle=toggle)


def test_registry_metadata():
    assert THOMSON7.unit_us == 500
    assert THOMSON7.extent_us == 80_000
    assert THOMSON7.bits == 12
    assert THOMSON7.nominal_carrier_hz == 33_000
    assert "D:4,T:1,F:7" in THOMSON7.irp
    assert "msb" not in THOMSON7.irp, "least significant bit first, unlike RC5 and RC6"


def test_there_is_no_intro_sequence():
    signal = _encode()
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_every_address_command_and_toggle_round_trips():
    """4,096 frames, all of them."""
    for device in range(16):
        for function in range(128):
            for toggle in (0, 1):
                assert _decode(_encode(device, function, toggle).repeat) == (
                    device, toggle, function), (device, function, toggle)


def test_bit_order_is_least_significant_first():
    """D=1 and F=1 each put their one first in their field, the opposite of
    RC5, RC6 and RCA, which send the most significant bit first."""
    only_d0 = _encode(device=1, function=0).repeat
    assert only_d0[1] == 9 * UNIT and only_d0[3] == 4 * UNIT
    only_f0 = _encode(device=0, function=1).repeat
    assert only_f0[11] == 9 * UNIT and only_f0[13] == 4 * UNIT   # bit 5, after T


def test_toggle_is_the_fifth_bit():
    off, on = _encode(toggle=0).repeat, _encode(toggle=1).repeat
    assert off[9] == 4 * UNIT and on[9] == 9 * UNIT
    assert off[:9] == on[:9] and off[10:24] == on[10:24]


def test_a_file_cannot_set_the_toggle_so_it_defaults_to_zero():
    """D3b: ``toggle`` exists for the tests; the default is the IRP's T=0."""
    plain = THOMSON7.encode(device=12, subdevice=None, function=74, carrier_hz=CARRIER)
    assert plain == _encode(toggle=0)


@pytest.mark.parametrize("device, function", [(0, 0), (15, 127), (12, 74), (1, 0), (0, 1)])
def test_the_frame_pads_to_its_extent_from_the_first_mark(device, function):
    """The stop bit is a mark, so the gap is always a new element; the whole
    sequence is 80 ms whatever the data."""
    repeat = _encode(device, function).repeat
    assert sum(repeat) == THOMSON7.extent_us
    assert len(repeat) == 26 and repeat[-2] == UNIT


def test_the_longest_frame_still_fits_in_its_extent():
    """Twelve ones and the stop mark are 121 units, 60.5 ms of 80."""
    assert sum(_encode(15, 127, 1).repeat[:-1]) == 121 * UNIT


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "007E"            # 33 kHz
    assert words[3] == 0x0D                       # 13 burst pairs


#: The five functions IrpTransmogrifier's Thomson-0625 capture decodes at device
#: 12 (Thomson-0625.exp @ c945e76) that SwiftRemote's database shares by name;
#: tests/test_irblaster_philips.py ties them to the database's hexcodes.
@pytest.mark.parametrize("function", [74, 42, 80, 104, 88])
def test_the_captured_frames_are_valid_thomson7_frames(function):
    assert _decode(_encode(12, function).repeat) == (12, 0, function)


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=16), "D:4 holds 0-15"),
        (dict(device=-1), "D:4 holds 0-15"),
        (dict(function=128), "F:7 holds 0-127"),
        (dict(function=-1), "F:7 holds 0-127"),
        (dict(toggle=2), "T:1 is 0 or 1"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="no subdevice"):
        THOMSON7.encode(device=12, subdevice=3, function=74, carrier_hz=CARRIER)
