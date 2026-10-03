"""Aiwa invariants (D18 gate 3).

The decoder is written from the IRP's frame layout, not from the encoder:
a 16-unit mark and 8-unit space, forty-two ``1,-1`` / ``1,-3`` bits read
LSB-first as D:8, S:5, ~D:8, ~S:5, F:8, ~F:8, a stop mark, and a 42-unit
gap. What anchors the constants to the outside world is outside this file:
the cited golden vector (tests/vectors/CITATIONS.md) and the hardware capture
checked at the bottom.
"""

import random

import pytest

from irpt_captures import capture, disagreements
from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols.aiwa import AIWA as AIWA_PROTOCOL

UNIT = AIWA_PROTOCOL.unit_us
CARRIER = 38_123


def _decode(frame, unit=UNIT):
    """An 88-duration frame -> (D, S, ~D, ~S, F, ~F), or None."""
    if len(frame) != 88 or (frame[0], frame[1]) != (16 * unit, 8 * unit):
        return None
    bits = []
    for i in range(2, 86, 2):
        if frame[i] != unit or frame[i + 1] not in (unit, 3 * unit):
            return None
        bits.append(1 if frame[i + 1] == 3 * unit else 0)
    if (frame[86], frame[87]) != (unit, 42 * unit):
        return None

    def field(start, width):
        return sum(bits[start + k] << k for k in range(width))

    return (field(0, 8), field(8, 5), field(13, 8), field(21, 5), field(26, 8), field(34, 8))


def _encode(device=71, subdevice=24, function=21, **kw):
    return AIWA_PROTOCOL.encode(
        device=device, subdevice=subdevice, function=function, carrier_hz=CARRIER, **kw
    )


def test_registry_metadata():
    assert AIWA_PROTOCOL.name == "Aiwa"
    assert AIWA_PROTOCOL.unit_us == 550
    assert AIWA_PROTOCOL.bits == 42
    assert AIWA_PROTOCOL.nominal_carrier_hz == 38_123
    # the gap after the frame and after the tail are fixed, not an extent
    assert AIWA_PROTOCOL.extent_us is None
    assert "D:8,S:5,~D:8,~S:5,F:8,~F:8" in AIWA_PROTOCOL.irp


def test_the_frame_is_the_intro_and_the_tail_is_the_repeat():
    signal = _encode()
    assert len(signal.intro) == 88
    assert signal.repeat == (16 * UNIT, 8 * UNIT, UNIT, 165 * UNIT)
    words = pronto.parse_words(pronto.encode(signal))
    assert (words[2], words[3]) == (44, 2)


def test_the_frame_has_forty_two_bit_pairs_between_its_header_and_stop():
    frame = _encode().intro
    assert len(frame) == 2 + 42 * 2 + 2
    assert set(frame[2:86:2]) == {UNIT}
    assert set(frame[3:86:2]) <= {UNIT, 3 * UNIT}


def _frames():
    rng = random.Random(42)
    seen = set()
    for d0, s0, f0 in ((0, 0, 0), (255, 31, 255), (0x47, 24, 21), (0x66, 0, 0x80)):
        for s in range(32):
            for f in range(256):
                seen.add((d0, s, f))      # 32 x 256 per fixed D: 8,192 each
        for d in range(256):
            seen.add((d, s0, f0))
        for f in range(256):
            seen.add((d0, s0, f))
    for _ in range(20_000):
        seen.add((rng.randrange(256), rng.randrange(32), rng.randrange(256)))
    return sorted(seen)


def test_every_subdevice_and_function_round_trips_with_complements_intact():
    """Four devices swept over every S and F, every device at fixed S and F,
    and 20,000 seeded random frames. The complements are recomputed in the
    decoder's output, so ``~D == D ^ 0xFF`` etc. is asserted, not assumed."""
    for d, s, f in _frames():
        got = _decode(_encode(d, s, f).intro)
        assert got == (d, s, d ^ 0xFF, s ^ 0x1F, f, f ^ 0xFF), (d, s, f)


def test_the_five_bit_subdevice_complement_is_five_bits_wide():
    """~S is S:5's complement within five bits, not eight: S=0 gives 31."""
    assert _decode(_encode(0, 0, 0).intro)[3] == 31
    assert _decode(_encode(0, 31, 0).intro)[3] == 0


def test_unit_override_scales_every_duration():
    frame = _encode(unit_us=600).intro
    assert _decode(frame, unit=600) is not None


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(device=256), "D:8 holds 0-255"),
        (dict(device=-1), "D:8 holds 0-255"),
        (dict(subdevice=32), "S:5 holds 0-31"),
        (dict(subdevice=-1), "S:5 holds 0-31"),
        (dict(function=256), "F:8 holds 0-255"),
        (dict(function=-1), "F:8 holds 0-255"),
    ],
)
def test_out_of_range_fields_are_refused(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_missing_subdevice_is_refused():
    with pytest.raises(EncodeError, match="explicit subdevice"):
        AIWA_PROTOCOL.encode(device=1, subdevice=None, function=1, carrier_hz=CARRIER)


# --- gate 2a: a hardware capture ---------------------------------------------------------


def test_ours_agrees_with_the_hardware_capture():
    """``Aiwa_left.ict`` from IrpTransmogrifier's test resources: a real
    remote's frame and the header-only tail that follows it, decoded by that
    project's own teaser test as Aiwa {D=8,S=0,F=21}. The capture's carrier is
    37,718 Hz against the IRP's 38,123; layout and ratios only."""
    cap = capture("Aiwa")
    assert cap["expectedDecode"] == "Aiwa: {D=8,S=0,F=21}"
    signal = AIWA_PROTOCOL.encode(carrier_hz=cap["captureCarrierHz"], **cap["params"])
    assert disagreements(list(signal.intro), cap["intro"]) == []
    assert disagreements(list(signal.repeat), cap["repeat"]) == []
