"""Sharp.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {38k,264}<1,-3|1,-7>(D:5,F:8,1:2,1,^67m,(D:5,~F:8,2:2,1,^67m,D:5,F:8,1:2,1,^67m)*)[D:0..31,F:0..255]

Almost identical to Denon (see ``_addr_cmd``): the same unit, bit encoding,
frame width and 67 ms extent, with different two-bit trailers. A frame is a
five-bit device, an eight-bit function, ``1:2`` (a one then a zero, LSB
first), a stop mark and padding. The signal is **three frames**: the function
(the intro), its complement with the trailer ``2:2`` (a zero then a one), and
the function again; the last two are the repeat.

The rest of the Sharp family is not registered, each for a stated reason:

* ``Sharp{1}`` and ``Sharp{2}`` -- the normal and inverted halves on their
  own. IrpTransmogrifier says they "all represent the same protocol when
  correct, but only Sharp is robust".
* ``Sharp_Old`` (and its ``{1}``, ``{2}`` halves) -- a *three*-bit device,
  ``D:3,F:8,0:1`` and a 49 ms extent, an older and different frame.
* ``SharpDVD`` -- a Kaseikyo-family frame (``170,90`` OEM codes, 400 us unit,
  48 bits).

``extent_us`` is ``None``: the repeat holds two frames, each padded to its own
67 ms, and D31 pads a truncated sequence to one figure.
"""

from __future__ import annotations

from ..signal import IrSignal
from . import _addr_cmd
from .base import Protocol

_IRP = (
    "{38k,264}<1,-3|1,-7>(D:5,F:8,1:2,1,^67m,"
    "(D:5,~F:8,2:2,1,^67m,D:5,F:8,1:2,1,^67m)*)[D:0..31,F:0..255]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2461 "
    "(bengtmartensson/IrpTransmogrifier), protocol `Sharp`, verbatim; the "
    "same string is in the 1.2.14 release's IrpProtocols.xml (L2442), which "
    "tests/vectors/irpt_sharp_D1_F22.pronto was rendered from. Its "
    "documentation says a Sharp signal has two halves, either enough to "
    "decode it. The frame layout and bit order are corroborated by "
    "IrpTransmogrifier's own teaser file Sharp_Pronto.txt @c945e76, whose "
    "Sharp_Pronto.exp decodes the 'Power' code as {D=1,F=22} "
    "(see tests/vectors/CITATIONS.md)."
)


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    """Render Sharp ``{D, F}`` as an :class:`IrSignal`."""
    return _addr_cmd.encode(
        "Sharp", 1, 2,
        device=device, subdevice=subdevice, function=function,
        carrier_hz=carrier_hz, unit_us=unit_us,
    )


SHARP = Protocol(
    name="Sharp",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_addr_cmd.UNIT_US,
    nominal_carrier_hz=38_000,
    extent_us=None,
    bits=_addr_cmd.BITS,
    encode=encode,
)
