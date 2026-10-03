"""A Sony SIRC frame reader for the invariant tests, written from the frame
layout rather than from the encoder.

A SIRC frame is a 4-unit mark, a 1-unit space, then one mark/space pair per
bit -- a 1-unit mark for a 0 and a 2-unit mark for a 1, each followed by a
1-unit space -- least significant bit first, command (7 bits) before the
address bits. The last space is lengthened to the 45 ms extent.
"""

from __future__ import annotations

UNIT = 600
EXTENT = 45_000


def read_bits(repeat, unit: int = UNIT) -> list[int] | None:
    """The data bits of one frame, or None if it is not a well-formed one.

    ``repeat`` is the mark/space durations ending in the lengthened space.
    Anything that is not a lead-in followed by whole 1-or-2-unit marks each
    followed by exactly one unit of space (the last space excepted) is
    rejected, so a misshapen frame is not silently misread.
    """
    if len(repeat) < 4 or len(repeat) % 2:
        return None
    if repeat[0] != 4 * unit or repeat[1] != unit:
        return None
    bits = []
    pairs = list(zip(repeat[2::2], repeat[3::2]))
    for i, (mark, space) in enumerate(pairs):
        if mark not in (unit, 2 * unit):
            return None
        last = i == len(pairs) - 1
        if not last and space != unit:
            return None
        if last and space < unit:
            return None
        bits.append(1 if mark == 2 * unit else 0)
    return bits


def field(bits: list[int], start: int, width: int) -> int:
    return sum(b << i for i, b in enumerate(bits[start:start + width]))
