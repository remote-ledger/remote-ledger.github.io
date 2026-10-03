"""IrpTransmogrifier's teaser captures, as gate 2a evidence for the japan family.

``tests/vectors/irpt_teaser_japan.json`` holds excerpts of the files
IrpTransmogrifier publishes under ``src/test/teaserfiles`` at ``c945e76``,
each with the decode its own ``.exp`` file gives for it. Our encoder, given
*their* published parameters, must produce *their* waveform.

What that can and cannot prove is the same as for any capture (see
CITATIONS.md, Sony20): the instrument has bias, so durations are compared
with the oracle tolerance rather than exactly, and the idle gaps are skipped
because they depend on the extent, not on the protocol's marks and spaces.
What it does pin is the *layout*: the number of durations, where each frame
ends, and every data bit. The tolerance (12 %, 150 us) is far narrower than
the gap between a zero-space and a one-space (a factor of 2 to 3), so a swapped
bit order or a wrong complement fails; the tests below include a case that
proves it does.
"""

from __future__ import annotations

import json
from pathlib import Path

VECTORS = Path(__file__).parent / "vectors"
DATA = json.loads((VECTORS / "irpt_teaser_japan.json").read_text())

#: A space at least this long is an idle gap, not a bit or a lead-in. The
#: longest space in any frame here is Pioneer's and JVC's 8-unit lead-in
#: (about 4.5 ms).
GAP_US = 8_000


def entries(protocol: str) -> list[dict]:
    found = [e for e in DATA["entries"] if e["protocol"] == protocol]
    assert found, f"no teaser capture for {protocol}"
    return found


def within(a: int, b: int) -> bool:
    """The oracle tolerance: 12 % of the larger, or 150 us, whichever is more."""
    return abs(a - b) <= max(150, 0.12 * max(a, b))


def problems(signal, entry: dict) -> list[str]:
    """Where ``signal`` disagrees with the captured excerpt, as readable lines."""
    theirs = entry["durations"]
    # The excerpt is one pass of the signal, so it is compared with the same
    # number of durations from the front of intro-then-repeat. It must end on
    # a gap, so a frame cut short cannot pass.
    ours = (list(signal.intro) + list(signal.repeat))[: len(theirs)]
    out: list[str] = []
    if len(ours) != len(theirs):
        out.append(f"only {len(ours)} durations against the capture's {len(theirs)}")
        return out
    if not (ours[-1] >= GAP_US and theirs[-1] >= GAP_US):
        out.append("the excerpt does not end on an idle gap in both")
    for i, (a, b) in enumerate(zip(ours, theirs)):
        gap_a, gap_b = i % 2 == 1 and a >= GAP_US, i % 2 == 1 and b >= GAP_US
        if gap_a != gap_b:
            out.append(f"[{i}] a gap in only one of them: {a} against {b}")
        elif not gap_a and not within(a, b):
            out.append(f"[{i}] {a} against the capture's {b}")
    return out
