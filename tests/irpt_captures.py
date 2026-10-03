"""Hardware captures from IrpTransmogrifier's test resources, for gate 2a.

``vectors/irpt-teaser-captures.json`` holds one frame per protocol, copied
from ``src/test/teaserfiles`` at the pinned commit, each with the decode that
IrpTransmogrifier's own teaser test asserts for it. A capture carries
instrument bias -- marks run long, spaces short, the carrier is measured, and
the long gaps are whatever the capturing hardware saw -- so these establish
layout and ratios. Absolute durations are gate 2b's job.
"""

import json
from pathlib import Path

CAPTURES = json.loads(
    (Path(__file__).parent / "vectors" / "irpt-teaser-captures.json").read_text()
)["entries"]

#: Symmetric: |a - b| <= REL * max(a, b). Wide enough for a real capture's
#: bias, narrow enough that a bit read as 1 instead of 0 (3x) cannot pass.
REL = 0.12
ABS_US = 150


def capture(protocol: str) -> dict:
    (entry,) = [e for e in CAPTURES if e["protocol"] == protocol]
    return entry


def close(a: int, b: int) -> bool:
    return abs(a - b) <= max(REL * max(a, b), ABS_US)


def disagreements(ours, captured) -> list[tuple[int, int, int]]:
    """``(index, ours, captured)`` for every duration outside the tolerance.

    A different length is reported as a single entry at index -1."""
    if len(ours) != len(captured):
        return [(-1, len(ours), len(captured))]
    return [(i, a, b) for i, (a, b) in enumerate(zip(ours, captured)) if not close(a, b)]
