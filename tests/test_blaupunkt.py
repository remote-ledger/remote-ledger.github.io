"""Blaupunkt invariants (D18 gate 3).

The space is 8 devices x 64 functions = 512, so it is swept exhaustively.
The decoder expands durations to unit-length half-bits and reads biphase
pairs, written from the IRP ``<-1,1|1,-1>(1,-5,1023:10,-44,
(1,-5,1:1,F:6,D:3,-236)+,1,-5,1023:10,-44)``, not from the encoder.
"""

import pytest

from irpt_captures import capture, disagreements
from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols.blaupunkt import BLAUPUNKT

UNIT = BLAUPUNKT.unit_us
CARRIER = 30_300


def _halves(durations, unit=UNIT):
    """Unit-length half-bits, 1 = mark, 0 = space, from alternating durations."""
    out = []
    for i, d in enumerate(durations):
        assert d % unit == 0, f"duration {d} is not a whole number of units"
        out += [1 - i % 2] * (d // unit)
    return out


def _decode_frame(frame, unit=UNIT):
    """``1,-5`` then ten biphase bits then 236 units: (fixed, F, D) or None."""
    h = _halves(frame, unit)
    if h[:6] != [1, 0, 0, 0, 0, 0] or len(h) != 6 + 20 + 236 or h[26:] != [0] * 236:
        return None
    bits = []
    for j in range(6, 26, 2):
        pair = (h[j], h[j + 1])
        if pair == (1, 0):
            bits.append(1)
        elif pair == (0, 1):
            bits.append(0)
        else:
            return None
    fixed = bits[0]
    f = sum(bits[1 + k] << k for k in range(6))
    d = sum(bits[7 + k] << k for k in range(3))
    return fixed, f, d


SYNC = [UNIT, 5 * UNIT] + [UNIT, UNIT] * 9 + [UNIT, 45 * UNIT]


def _encode(device=2, function=21, **kw):
    return BLAUPUNKT.encode(
        device=device, subdevice=None, function=function, carrier_hz=CARRIER, **kw
    )


def test_registry_metadata():
    assert BLAUPUNKT.name == "Blaupunkt"
    assert BLAUPUNKT.unit_us == 512
    assert BLAUPUNKT.nominal_carrier_hz == 30_300
    assert BLAUPUNKT.extent_us is None
    assert "1023:10" in BLAUPUNKT.irp and "F:6,D:3" in BLAUPUNKT.irp


def test_sync_is_a_mark_five_units_then_ten_marks_and_a_long_gap():
    """``1023:10`` is ten ``1`` bits, which in this coding are ten one-unit
    marks; the 44-unit gap merges into the last bit's own space, so the
    sequence ends on 45 units."""
    signal = _encode()
    assert list(signal.intro[:22]) == SYNC


def test_intro_is_sync_plus_one_frame_and_repeat_is_the_frame():
    """The shape IrpTransmogrifier gives Pronto for ``(...)+``: the bare ``+``
    means at least once, so one frame is folded into the intro."""
    signal = _encode()
    assert signal.intro == tuple(SYNC) + signal.repeat
    words = pronto.parse_words(pronto.encode(signal))
    assert words[2] == (len(SYNC) + len(signal.repeat)) // 2
    assert words[3] == len(signal.repeat) // 2


def test_every_device_and_function_round_trips():
    for device in range(8):
        for function in range(64):
            signal = _encode(device, function)
            assert _decode_frame(signal.repeat) == (1, function, device), (device, function)
            assert signal.intro[:22] == tuple(SYNC)
            assert signal.intro[22:] == signal.repeat


def test_the_frame_ends_with_a_236_unit_gap_whichever_way_the_last_bit_goes():
    """The last bit is D's top bit. A ``1`` ends on a space the gap lengthens
    (237 units); a ``0`` ends on a mark, so the gap is its own 236."""
    assert _encode(device=0).repeat[-1] == 236 * UNIT           # last bit 0: mark, then gap
    assert _encode(device=4).repeat[-1] == 237 * UNIT           # last bit 1: space, lengthened


def test_the_closing_sync_is_not_carried_but_is_recoverable():
    """``IrSignal.ending`` is reserved (D1). The IRP's ending is the same
    text as its opening sync, so what is dropped is the sync already held in
    the intro."""
    signal = _encode()
    assert signal.ending == ()
    assert list(signal.intro[: len(signal.intro) - len(signal.repeat)]) == SYNC


def test_unit_override_scales_every_duration():
    signal = _encode(unit_us=528)
    assert _decode_frame(signal.repeat, unit=528) == (1, 21, 2)


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=8), "D:3 holds 0-7"),
        (dict(device=-1), "D:3 holds 0-7"),
        (dict(function=64), "F:6 holds 0-63"),
        (dict(function=-1), "F:6 holds 0-63"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="no subdevice"):
        BLAUPUNKT.encode(device=2, subdevice=1, function=21, carrier_hz=CARRIER)


# --- gate 2a: a hardware capture ---------------------------------------------------------


def test_ours_agrees_with_the_hardware_capture():
    """``Blaupunkt.ict`` from IrpTransmogrifier's test resources, key ``Ch+``:
    sync, three frames, the closing sync. That project's own teaser test
    decodes it as Blaupunkt {F=21,D=2}. The capture's carrier is 30,225 Hz
    against the IRP's 30,300 and its unit about 532 us against 512; layout and
    ratios only. Its sync gap (20.6 ms) is 10.7 % shorter than the IRP's 45
    units (23.0 ms), the one place the two differ by more than a few percent."""
    cap = capture("Blaupunkt")
    assert cap["expectedDecode"] == "Blaupunkt: {F=21,D=2}"
    signal = BLAUPUNKT.encode(carrier_hz=cap["captureCarrierHz"], **cap["params"])
    assert disagreements(list(signal.intro), cap["intro"]) == []
    assert disagreements(list(signal.repeat), cap["repeat"]) == []
    # The closing sync the signal cannot carry is the intro's own sync. The
    # capture ends in 500 ms of padding the file format appends after the
    # last key, so its final gap says nothing and is left out.
    assert cap["ending"][-1] == 500_000
    assert disagreements(list(signal.intro[:21]), cap["ending"][:-1]) == []
