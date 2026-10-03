"""RCA at 38.7 kHz, the variant IrpTransmogrifier calls ``RCA-38``.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {38.7k,460,msb}<1,-2|1,-4>(8,-8,D:4,F:8,~D:4,~F:8,1,-16)*[D:0..15,F:0..255]

A frame, in units of 460 us, most significant bit first:

=========  ==================================================================
lead-in    an 8-unit mark and an 8-unit space (3,680 us each)
D:4, F:8   a four-bit address and an eight-bit command
~D:4,~F:8  the same two fields complemented, so the frame is its own check
bit        a 1-unit mark, then a 2-unit space for a 0 and a 4-unit space for
           a 1
stop       one 1-unit mark
gap        a fixed 16 units (7,360 us)
=========  ==================================================================

Three things shape the encoder, and the last is a choice between neighbours:

* **The gap is fixed, not an extent.** The IRP ends ``-16``, not ``^`` and a
  period, so the sequence is closed by an explicit 7,360 us space and
  ``extent_us`` is ``None`` (D3), which D31 reads as "this protocol declares
  no extent". The complement half makes every frame carry exactly twelve 1
  bits, so every frame is the same 59,340 us long and the choice changes no
  output; it is kept as the IRP states it.
* **The frame has no intro.** ``*`` marks the whole frame as the repeat, so
  D6 rule 6 emits ``NNNN = 0000``.
* **This is ``RCA-38``, not its neighbours.** The database holds four RCA
  entries and they are different signals. ``RCA`` and ``RCA(Old)`` are at 58
  kHz. ``RCA-38(Old)`` is this frame with a longer first lead-in (``[40][8]``,
  40 units on the first send), a double-length stop mark and a ``+`` repeat.
  SwiftRemote's frame has the plain 8,-8 lead-in, a single stop mark and
  38.7 kHz, which is this entry alone.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = "{38.7k,460,msb}<1,-2|1,-4>(8,-8,D:4,F:8,~D:4,~F:8,1,-16)*[D:0..15,F:0..255]"
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2254-L2256 "
    "(bengtmartensson/IrpTransmogrifier), verbatim. Its documentation says "
    "these are recently discovered variants of RCA that differ from RCA and "
    "RCA(Old) only in the carrier, 38.7 kHz instead of 58. The field layout "
    "and bit timings are confirmed by a hardware capture in "
    "IrpTransmogrifier's own test data (see tests/vectors/CITATIONS.md)."
)
_UNIT_US = 460
_GAP_UNITS = 16
_BITS = 24


def _bits_msb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count - 1, -1, -1)]


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

    if subdevice is not None:
        raise EncodeError(
            f"RCA-38 has no subdevice but one was given ({subdevice}); the "
            "address is the four-bit device alone"
        )
    if not 0 <= device <= 0xF:
        raise EncodeError(f"RCA-38 device is {device}; D:4 holds 0-15")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"RCA-38 function is {function}; F:8 holds 0-255")

    bits = (
        _bits_msb_first(device, 4)
        + _bits_msb_first(function, 8)
        + _bits_msb_first(~device & 0xF, 4)
        + _bits_msb_first(~function & 0xFF, 8)
    )
    assert len(bits) == _BITS

    frame = [8 * unit, 8 * unit]
    for bit in bits:
        frame += [unit, (4 if bit else 2) * unit]
    frame += [unit, _GAP_UNITS * unit]

    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


RCA38 = Protocol(
    name="RCA-38",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=38_700,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
