"""RCA invariants (D18 gate 3, DESIGN section 45).

``RCA`` is ``RCA-38``'s frame at 58 kHz (``rca.py`` shares ``rca38.py``'s encoder), so the proof is two-sided: the frame
decodes with a decoder written from the layout, exhaustively, and **its durations are RCA-38's, field for field**, with
nothing but the carrier different. What anchors the constants outside this repository is the cited evidence
(tests/vectors/CITATIONS.md): IrpTransmogrifier 1.2.14's rendering of two signals and hardware captures of an RCA(Old)
remote and an RCA-38 one, read by ``tools/misc_capture_audit.py``.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import RCA, RCA38

UNIT = RCA.unit_us
CARRIER = 58_000


def _encode(device=15, function=48, **kw):
    return RCA.encode(device=device, subdevice=None, function=function, carrier_hz=CARRIER, **kw)


def _decode(repeat, unit=UNIT):
    """``(D, F)`` or None: an 8,-8 lead-in, 24 bits (a one-unit mark, then two units of space for a 0 and four for a 1),
    a one-unit stop mark and a 16-unit gap, the second twelve bits the complement of the first."""
    if len(repeat) != 2 + 48 + 2:
        return None
    units = [round(d / unit) for d in repeat]
    if units[:2] != [8, 8] or units[-2:] != [1, 16]:
        return None
    word = 0
    for mark, space in zip(units[2:-2:2], units[3:-2:2]):
        if mark != 1 or space not in (2, 4):
            return None
        word = word << 1 | (space == 4)
    d, f = word >> 20, word >> 12 & 0xFF
    return (d, f) if word >> 8 & 0xF == ~d & 0xF and word & 0xFF == ~f & 0xFF else None


def test_registry_metadata():
    assert RCA.name == "RCA" and RCA.unit_us == 460 and RCA.bits == 24
    assert RCA.nominal_carrier_hz == 58_000, "the IRP's 58k; a file's carrierHz is authoritative"
    assert RCA.extent_us is None, "the IRP ends -16, not an extent"
    assert RCA.irp.startswith("{58k,460,msb}") and "~D:4,~F:8" in RCA.irp


def test_every_device_and_function_round_trips():
    """4,096 frames, all of them."""
    for device in range(16):
        for function in range(256):
            assert _decode(_encode(device, function).repeat) == (device, function), (device, function)


def test_the_frame_is_rca_38s_with_nothing_but_the_carrier_different():
    """What the database says (RCA-38 'differs from RCA only in the frequency'), held over every frame."""
    for device in range(16):
        for function in range(0, 256, 3):
            ours = _encode(device, function)
            theirs = RCA38.encode(device=device, subdevice=None, function=function, carrier_hz=38_700)
            assert ours.repeat == theirs.repeat and ours.intro == theirs.intro == ()
            assert ours.carrier_hz == 58_000 and theirs.carrier_hz == 38_700


def test_there_is_no_intro_and_the_frame_is_one_length():
    signal = _encode()
    assert signal.intro == () and pronto.parse_words(pronto.encode(signal))[2] == 0
    assert sum(signal.repeat) == 129 * UNIT == 59_340          # twelve ones in every frame
    assert signal.repeat[-2:] == (UNIT, 16 * UNIT) and signal.repeat[:2] == (8 * UNIT, 8 * UNIT)


def test_bit_order_is_most_significant_first():
    assert _encode(device=8, function=0).repeat[3] == 4 * UNIT
    assert _encode(device=1, function=0).repeat[3] == 2 * UNIT
    assert _encode(device=0, function=0x80).repeat[3 + 2 * 4] == 4 * UNIT


def test_a_broken_complement_is_not_a_frame():
    repeat = list(_encode().repeat)
    repeat[3 + 2 * 14] = 6 * UNIT - repeat[3 + 2 * 14]
    assert _decode(tuple(repeat)) is None


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "0047" and words[3] == 0x1A     # 58 kHz, 26 burst pairs


@pytest.mark.parametrize("kwargs, match", [
    (dict(device=16), "RCA device is 16; D:4"), (dict(device=-1), "D:4"),
    (dict(function=256), "RCA function is 256; F:8"), (dict(function=-1), "F:8"),
])
def test_out_of_range_fields_are_refused_in_its_own_name(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="RCA has no subdevice"):
        RCA.encode(device=15, subdevice=3, function=48, carrier_hz=CARRIER)
