"""Denon.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {38k,264}<1,-3|1,-7>(D:5,F:8,0:2,1,^67m,(D:5,~F:8,3:2,1,^67m,D:5,F:8,0:2,1,^67m)*)[D:0..31,F:0..255]

Read left to right: unit 264 us at 38 kHz; a zero bit is one unit of mark and
three of space, a one bit one unit of mark and seven of space. A frame is a
five-bit device, an eight-bit function, two bits ``0:2`` (both zero), a stop
mark, and a space that pads it to 67 ms. The signal is **three frames**: the
function (the intro), the function's complement with the two extra bits set
(``3:2``, both one), and the function again. The last two are the repeat.
So a Denon signal carries its own check, which is why a decoder given only
half of it reports ``Denon{1}`` or ``Denon{2}``; neither is registered, both
are decode-only in IrpTransmogrifier.

Not this protocol: ``Denon-K`` is a Kaseikyo-family frame (``84,50`` OEM
codes, a 432 us unit, 37 kHz, 48 bits) used by later Denon equipment. The
name is shared and nothing else is.

IrpTransmogrifier's database keeps a superseded form of this IRP in a comment
beside the live one::

    {38k,264}<1,-3|1,-7>(D:5,F:8,0:2,1,-165,D:5,~F:8,3:2,1,-165)*

with a *fixed* 165-unit (43,560 us) gap after each frame instead of an extent.
The live form is registered, per the project's rule that the source's own
active definition is the one cited. The difference is only in the gaps; see
``NOTES/japan.md`` for what that does to the comparison with SwiftRemote.

``extent_us`` is ``None``: the repeat holds two frames, each padded to its own
67 ms, and D31 pads a truncated sequence to one figure.
"""

from __future__ import annotations

from ..signal import IrSignal
from . import _addr_cmd
from .base import Protocol

_IRP = (
    "{38k,264}<1,-3|1,-7>(D:5,F:8,0:2,1,^67m,"
    "(D:5,~F:8,3:2,1,^67m,D:5,F:8,0:2,1,^67m)*)[D:0..31,F:0..255]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L494 "
    "(bengtmartensson/IrpTransmogrifier), protocol `Denon`, verbatim; the "
    "same string is in the 1.2.14 release's IrpProtocols.xml (L494), which "
    "tests/vectors/irpt_denon_D8_F175.pronto was rendered from. Its "
    "documentation says a Denon signal has two halves, either enough to "
    "decode it. The frame layout and bit order are corroborated by "
    "IrpTransmogrifier's own published Denon Pronto string "
    "(ShortProntoNGTest.java L21 @c945e76, which decodes as {D=1,F=3}) and "
    "by its teaser capture Denon.ict, decoded by Denon_left.exp as "
    "{D=8,F=175} (see tests/vectors/CITATIONS.md)."
)


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    """Render Denon ``{D, F}`` as an :class:`IrSignal`."""
    return _addr_cmd.encode(
        "Denon", 0, 3,
        device=device, subdevice=subdevice, function=function,
        carrier_hz=carrier_hz, unit_us=unit_us,
    )


DENON = Protocol(
    name="Denon",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_addr_cmd.UNIT_US,
    nominal_carrier_hz=38_000,
    extent_us=None,
    bits=_addr_cmd.BITS,
    encode=encode,
)
