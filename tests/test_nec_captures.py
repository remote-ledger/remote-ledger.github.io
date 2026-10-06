"""The structural vector for NEC2, NECx1 and NECx2 (D18 gate 2a).

IrpTransmogrifier's test data holds one hardware capture of a remote that
sends all three (``src/test/teaserfiles/NECx2_NECx1.ict``), and the tool's own
expected decodes for it (``NECx2_NECx1.exp``), both @c945e76. The
parameters below are the tool's decode of the capture, not ours; this file
feeds them to our encoders and compares the result with what was measured.

A capture carries instrument bias -- its medians sit within about 2 % of the
IRP's, in no one direction -- so this checks layout, ratios and the extent,
not absolute durations (gate 2b does that). What it can catch that nothing else here can:
the lead-in width, LSB-first order and the complement byte *on a real
signal*, NECx1's repeat frame (its shape and its one bit), and the 108 ms
extent. ``tests/vectors/nec-family-captures.json`` keeps medians and the
first frame quantised to 564 us units; no raw durations are copied.
"""

import json
from pathlib import Path

import pytest

from remote_ledger.protocols import REGISTRY

DATA = json.loads(
    (Path(__file__).parent / "vectors" / "nec-family-captures.json").read_text()
)
UNIT = DATA["unitUs"]
CAPTURES = DATA["captures"]
#: Instrument bias on a mark or space, as a fraction of our duration. The
#: capture's own medians sit within 2.5 %; 6 % is what a receiver may add.
BIAS = 0.06


def _signal(capture):
    p = capture["params"]
    return REGISTRY[capture["protocol"]].encode(
        device=p["device"], subdevice=p["subdevice"], function=p["function"],
        carrier_hz=DATA["measuredCarrierHz"],
    )


def _first_frame(signal):
    return signal.intro or signal.repeat


def _units(durations):
    return " ".join(str(round(d / UNIT)) for d in durations)


@pytest.mark.parametrize("name", CAPTURES)
def test_the_decode_is_the_tools_not_ours(name):
    """The parameters come from the published .exp line, so they are checked
    against it here rather than trusted."""
    capture = CAPTURES[name]
    p = capture["params"]
    line = capture["decodeLine"]
    assert line.startswith(capture["protocol"] + ": {")
    assert f"D={p['device']}" in line and f"F={p['function']}" in line
    if p.get("subdeviceDefaulted"):
        assert "S=" not in line and p["subdevice"] == 255 - p["device"]
    else:
        assert f"S={p['subdevice']}" in line


@pytest.mark.parametrize("name", CAPTURES)
def test_first_frame_has_the_measured_unit_pattern(name):
    """Every duration of the captured frame, quantised to 564 us, is the
    multiple our encoder emits: lead-in width, LSB-first bits, the complement
    byte and the stop mark, all on a real signal."""
    capture = CAPTURES[name]
    ours = _first_frame(_signal(capture))
    assert _units(ours[:-1]) == capture["firstFrameUnits"]


@pytest.mark.parametrize("name", CAPTURES)
def test_measured_timings_are_within_instrument_bias(name):
    capture = CAPTURES[name]
    ours = _first_frame(_signal(capture))
    lead_mark, lead_space = ours[0], ours[1]
    bit_mark, zero_space, one_space = UNIT, UNIT, 3 * UNIT
    for key, expect in (
        ("medianLeadMarkUs", lead_mark),
        ("medianLeadSpaceUs", lead_space),
        ("medianBitMarkUs", bit_mark),
        ("medianZeroSpaceUs", zero_space),
        ("medianOneSpaceUs", one_space),
    ):
        measured = capture[key]
        assert abs(measured - expect) <= BIAS * expect, (
            f"{name} {key}: measured {measured} us against {expect} us"
        )


@pytest.mark.parametrize("name", CAPTURES)
def test_first_frame_extent_is_108_ms(name):
    """The IRP's ``^108m``: the frame's active time plus its gap, measured,
    against our 108 000 us. Within 1.5 % (the captures say -0.8 %)."""
    capture = CAPTURES[name]
    ours = _first_frame(_signal(capture))
    assert sum(ours) == 108_000
    measured = capture["firstFrameActiveUs"] + capture["firstFrameGapUs"]
    assert abs(measured - 108_000) <= 0.015 * 108_000


def test_necx1_repeat_frame_is_lead_in_one_bit_and_stop_mark():
    """The repeat shape, and its bit: D=44 is even, so ``~D:1`` is a one,
    seen on the wire as a 3-unit space. Only the even case is in the capture;
    the odd case rests on the rendered vector."""
    capture = CAPTURES["Play"]
    repeat = _signal(capture).repeat
    assert capture["shortFrames"] == 3
    assert _units(repeat[:-1]) == capture["repeatFrame"]["unitsBeforeGap"] == "8 8 1 3 1"
    measured = (
        capture["repeatFrame"]["medianLeadMarkUs"]
        + capture["repeatFrame"]["medianLeadSpaceUs"]
        + 2 * capture["medianBitMarkUs"]
        + capture["medianOneSpaceUs"]
        + capture["repeatFrame"]["medianGapUs"]
    )
    assert sum(repeat) == 108_000
    assert abs(measured - 108_000) <= 0.015 * 108_000


def test_necx2_and_nec2_captures_repeat_the_whole_frame():
    """NEC2 and NECx2 hold their frame in the repeat slot with no intro, and
    the capture shows exactly that: every frame is a full 32-bit frame."""
    for name in ("Vol+ NEC2", "Mute"):
        capture = CAPTURES[name]
        assert capture["shortFrames"] == 0
        assert capture["fullFrames"] >= 3
        assert _signal(capture).intro == ()


def test_the_captured_carrier_agrees_with_irptransmogrifier_not_decodeir():
    """The remote's carrier is 38404 Hz. IrpTransmogrifier gives the family
    38.4k, DecodeIR 38.0k. One remote, so a corroboration and not a proof."""
    measured = DATA["measuredCarrierHz"]
    for name in ("NEC2", "NECx1"):
        nominal = REGISTRY[name].nominal_carrier_hz
        assert abs(measured - nominal) / nominal < 0.001
