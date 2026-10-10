"""Panasonic_Old: the older Panasonic frame, a 57.6 kHz pulse-distance code with a five-bit device and a six-bit command.

IRP, verbatim from IrpTransmogrifier's protocol database (``Panasonic_Old``; the space before the parameter block is
the database's own)::

    {57.6k,833}<1,-1|1,-3>(4,-4,D:5,F:6,~D:5,~F:6,1,-44m)* [D:0..31,F:0..63]

A frame, in units of 833 us, least significant bit first (the IRP has no ``msb``):

=========  ==================================================================
lead-in    a 4-unit mark and a 4-unit space
D:5, F:6   the device and the command
~D:5, ~F:6 their complements
stop       a 1-unit mark, then a **44 ms space** (``-44m`` is a gap after the stop mark, not an extent)
=========  ==================================================================

A ``0`` is a one-unit mark and a one-unit space, a ``1`` a one-unit mark and a three-unit space. The frame is the
repeat and there is no intro (``)*``). It is **not** the ledger's ``Panasonic`` (the 48-bit Kaseikyo frame at 37 kHz):
this one is a different, older code, which cable boxes of Scientific-Atlanta and Cisco still speak. A form's
``device`` is D, ``function`` is F, and a ``subdevice`` is an error.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = "{57.6k,833}<1,-1|1,-3>(4,-4,D:5,F:6,~D:5,~F:6,1,-44m)* [D:0..31,F:0..63]"
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 (bengtmartensson/IrpTransmogrifier), protocol "
    "`Panasonic_Old`, verbatim, and identical in the 1.2.14 release (`list -i Panasonic_Old`). Hardware captures of "
    "remotes that send it, and a Pronto string, are in IrpTransmogrifier's own test data (see "
    "tests/vectors/CITATIONS.md)."
)
_UNIT_US = 833
_GAP_US = 44_000
#: Bits after the lead-in: D, F and their complements.
_BITS = 22


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
    if subdevice is not None:
        raise EncodeError(
            f"Panasonic_Old has no subdevice but one was given ({subdevice}); the address is the five-bit device alone"
        )
    if not 0 <= device <= 0x1F:
        raise EncodeError(f"Panasonic_Old device is {device}; D:5 holds 0-31")
    if not 0 <= function <= 0x3F:
        raise EncodeError(f"Panasonic_Old function is {function}; F:6 holds 0-63")

    frame: list[int] = [4 * unit, 4 * unit]
    bits = (_bits_lsb_first(device, 5) + _bits_lsb_first(function, 6)
            + _bits_lsb_first(~device & 0x1F, 5) + _bits_lsb_first(~function & 0x3F, 6))
    for bit in bits:
        frame += [unit, 3 * unit] if bit else [unit, unit]
    frame += [unit, _GAP_US]                          # the stop mark, then the 44 ms gap
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


PANASONIC_OLD = Protocol(
    name="Panasonic_Old",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=57_600,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
