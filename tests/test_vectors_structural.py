"""The structural vector (D18 gate 2a).

D10 makes an independently cited golden vector a hard gate because it is
"the only test layer that catches a wrong constant" -- and the example it
gives is "whether the NEC1 lead-in is 16 units or 15".

That specific failure is what this catches. A second, independent
implementation publishes the NEC family's timing constants; feeding our
encoder *their* tick and comparing durations checks every structural
constant we chose -- lead-in width, bit mark, one-space, zero-space, bit
count, field layout -- against something we did not write.

What it does **not** establish is byte-level Pronto output, because their
tick (560 us) differs from the IRP definition's unit (564 us) and their
frame extent (193 ticks = 108080 us) differs from `^108m`. Both are
legitimate implementations of the same protocol; real receivers tolerate
0.7%. So gate 2 is split: 2a structural, met here; 2b byte-level Pronto,
still open. See CITATIONS.md.
"""

import json
from pathlib import Path

import pytest

from remote_ledger.protocols import REGISTRY

VECTORS = Path(__file__).parent / "vectors"
DATA = json.loads((VECTORS / "nec-family-timings.json").read_text())
TICK = DATA["tickUs"]


def _durations(name: str, **params):
    signal = REGISTRY[name].encode(carrier_hz=DATA["carrierHz"], unit_us=TICK, **params)
    return signal.intro or signal.repeat


@pytest.mark.parametrize("name", sorted(DATA["protocols"]))
def test_header_matches_the_published_constants(name):
    """The 16-versus-15 case D10 names, caught directly."""
    expected = DATA["protocols"][name]["publishedHeaderUs"]
    assert list(_durations(name, device=7, subdevice=7, function=2)[:2]) == expected


@pytest.mark.parametrize("name", sorted(DATA["protocols"]))
def test_header_ticks_match(name):
    spec = DATA["protocols"][name]
    mark, space = _durations(name, device=7, subdevice=7, function=2)[:2]
    assert mark == spec["headerMarkTicks"] * TICK
    assert space == spec["headerSpaceTicks"] * TICK


@pytest.mark.parametrize("name", sorted(DATA["protocols"]))
def test_bit_encoding_matches(name):
    spec = DATA["protocols"][name]
    # F = 0x55 gives alternating bits, so both bit shapes appear.
    durations = _durations(name, device=0, subdevice=0, function=0x55)
    body = durations[2:2 + 2 * spec["bits"]]
    marks = {body[i] for i in range(0, len(body), 2)}
    spaces = {body[i] for i in range(1, len(body), 2)}
    assert marks == {spec["bitMarkTicks"] * TICK}
    assert spaces == {spec["zeroSpaceTicks"] * TICK, spec["oneSpaceTicks"] * TICK}


@pytest.mark.parametrize("name", sorted(DATA["protocols"]))
def test_bit_count_matches(name):
    spec = DATA["protocols"][name]
    durations = _durations(name, device=7, subdevice=7, function=2)
    # lead-in pair + bits + stop mark + gap
    assert len(durations) == 2 + 2 * spec["bits"] + 2


def test_necx2_layout_is_customer_customer_command_inverted():
    """IRremoteESP8266 describes the 32 bits as customer + customer + command
    + inverted command, which is D:8, S:8 with S = D, then F:8, ~F:8 --
    exactly what DecodeIR's NECx2 IRP says, and what IRDB's 7,7 shows."""
    assert "customer_byte(same)" in DATA["protocols"]["NECx2"]["layout"]
    durations = _durations("NECx2", device=7, subdevice=7, function=2)
    body = durations[2:2 + 64]
    bits = [1 if body[i + 1] == 3 * TICK else 0 for i in range(0, 64, 2)]
    recovered = [
        sum(b << i for i, b in enumerate(bits[off:off + 8]))
        for off in (0, 8, 16, 24)
    ]
    assert recovered == [7, 7, 2, 0xFD]
    assert recovered[0] == recovered[1]              # customer repeated
    assert recovered[2] ^ recovered[3] == 0xFF       # command inverted


def test_the_unit_constants_genuinely_disagree_between_sources():
    """Recorded rather than smoothed over: IrpTransmogrifier and DecodeIR
    give 564 us; IRremoteESP8266 uses 560. That 0.7% is why this vector
    verifies structure and not bytes."""
    assert TICK == 560
    assert REGISTRY["NEC1"].unit_us == 564
    assert REGISTRY["NECx2"].unit_us == 564
