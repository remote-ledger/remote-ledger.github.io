"""Aiwa: a 42-bit NEC-like frame with a 550 us unit and a header-only repeat.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {38.123k,550}<1,-1|1,-3>(16,-8,D:8,S:5,~D:8,~S:5,F:8,~F:8,1,-42,(16,-8,1,-165)*)[D:0..255,S:0..31,F:0..255]

Read left to right: a 16-unit mark and 8-unit space, then forty-two LSB-first
bits -- the device byte, a five-bit subdevice, the complement of each, then
the function byte and its complement -- a stop mark, and a 42-unit gap. The
repeat is a bare ``16,-8,1`` followed by a 165-unit gap, so a held key sends
the whole frame once and then that short tail over and over.

Two things shape the encoder:

* **The complements are computed, not given.** ``~D``, ``~S`` and ``~F`` are
  derived from the parameters, so only a frame whose complements actually
  disagree with its parameters is out of reach -- and that is *not* a corner
  case in the data this was built for: SwiftRemote's stale encoder sends a
  different frame for every RCC2026 code, and all but 71 of the 1,231 fail it
  (tests/test_irblaster_unknown.py).
* **The intro is the frame and the repeat is the tail.** That is the shape
  IrpTransmogrifier renders (``render -r``: ``[frame][tail]``), the same
  split NEC1 uses, so D6 rule 6 puts the frame in the first slot.

IrpTransmogrifier also lists ``Aiwa2``, the same frame as ``(...)*`` with no
tail. It is not registered: nothing here needs it.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{38.123k,550}<1,-1|1,-3>(16,-8,D:8,S:5,~D:8,~S:5,F:8,~F:8,1,-42,"
    "(16,-8,1,-165)*)[D:0..255,S:0..31,F:0..255]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml L223 (release "
    "1.2.14 and @c945e76 alike; bengtmartensson/IrpTransmogrifier), verbatim. "
    "Also present, commented out, in its ProtocolNGTest.java L108 @c945e76. "
    "Corroborated by a hardware capture in the same repository, "
    "src/test/teaserfiles/Aiwa_left.ict, whose expected decode "
    "(Aiwa_left.exp) is Aiwa {D=8,S=0,F=21}."
)
_UNIT_US = 550
_BITS = 42


def _bits_lsb_first(value: int, width: int) -> list[int]:
    return [(value >> i) & 1 for i in range(width)]


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

    if not 0 <= device <= 0xFF:
        raise EncodeError(f"Aiwa device is {device}; D:8 holds 0-255")
    if subdevice is None:
        raise EncodeError(
            "Aiwa requires an explicit subdevice (S:5, 0-31); the IRP gives "
            "it no default"
        )
    if not 0 <= subdevice <= 0x1F:
        raise EncodeError(f"Aiwa subdevice is {subdevice}; S:5 holds 0-31")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"Aiwa function is {function}; F:8 holds 0-255")

    fields = (
        (device, 8), (subdevice, 5),
        (~device & 0xFF, 8), (~subdevice & 0x1F, 5),
        (function, 8), (~function & 0xFF, 8),
    )
    frame: list[int] = [16 * unit, 8 * unit]
    for value, width in fields:
        for bit in _bits_lsb_first(value, width):
            frame += [unit, 3 * unit] if bit else [unit, unit]
    frame += [unit, 42 * unit]

    tail = [16 * unit, 8 * unit, unit, 165 * unit]
    return IrSignal(carrier_hz=carrier_hz, intro=tuple(frame), repeat=tuple(tail))


AIWA = Protocol(
    name="Aiwa",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=38_123,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
