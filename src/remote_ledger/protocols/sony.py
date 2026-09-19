"""Sony SIRC, 20-bit variant.

IRP, from John Fine's DecodeIR documentation on hifi-remote.com::

    {40k,600}<1,-1|2,-1>(4,-1,F:7,D:5,S:8,^45m)+

Two things differ from NEC1 and both matter to the encoder:

* There is **no intro sequence.** The whole frame is the repeat, marked by
  ``+`` rather than NEC's separate ``(...)*`` tail -- so D6 rule 6 emits
  ``NNNN = 0000`` and puts everything in the second slot.
* The frame **ends on a space**, because the last bit contributes one. The
  ``^45m`` extent therefore *lengthens that final space* rather than
  appending a gap, which is what NEC1 does after its stop mark. Appending
  would add a burst pair that no Sony decoder expects.

``minSends`` is 3 for SIRC -- a fact about the hardware that D3a keeps out
of the waveform and in the compiled artifact's metadata.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = "{40k,600}<1,-1|2,-1>(4,-1,F:7,D:5,S:8,^45m)+"
_IRP_SOURCE = (
    "hifi-remote.com/johnsfine/DecodeIR.html (John Fine, DecodeIR "
    "documentation), retrieved 2026-09-19. Gives Sony12, Sony15 and Sony20 "
    "as {40k,600}<1,-1|2,-1> with lead-in 4,-1, a 45 ms extent, and Sony20's "
    "field layout as F:7,D:5,S:8."
)
_UNIT_US = 600
_EXTENT_US = 45_000
_BITS = 20


def _bits_lsb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count)]


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    if not 0 <= function <= 0x7F:
        raise EncodeError(f"Sony20 function is {function}; F:7 holds 0-127")
    if not 0 <= device <= 0x1F:
        raise EncodeError(
            f"Sony20 device is {device}; D:5 holds 0-31. Sony's device number "
            "is five bits here -- the eight-bit field is the subdevice"
        )
    if subdevice is None:
        raise EncodeError(
            "Sony20 requires an explicit subdevice (S:8). A 12- or 15-bit "
            "Sony frame is Sony12 or Sony15, both backlogged in D18"
        )
    if not 0 <= subdevice <= 0xFF:
        raise EncodeError(f"Sony20 subdevice is {subdevice}; S:8 holds 0-255")

    frame: list[int] = [4 * unit, unit]
    for value, width in ((function, 7), (device, 5), (subdevice, 8)):
        for bit in _bits_lsb_first(value, width):
            frame += [2 * unit, unit] if bit else [unit, unit]

    # The frame already ends on a space, so ^45m lengthens it rather than
    # appending a pair.
    head = sum(frame[:-1])
    gap = _EXTENT_US - head
    if gap < unit:
        raise EncodeError(
            f"Sony20: marks and spaces already total {head} us against an "
            f"extent of {_EXTENT_US} us, leaving {gap} us -- below one unit "
            f"({unit} us). Clamping would fabricate a waveform that fails the "
            "extent it claims (D31)"
        )
    frame[-1] = gap

    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


SONY20 = Protocol(
    name="Sony20",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=40_000,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
