"""GI_Cable, the General Instrument cable boxes' remote (IrpTransmogrifier's ``G.I.Cable``).

IRP, verbatim from IrpTransmogrifier's protocol database::

    {38.7k,490}<1,-4.5|1,-9>(18,-9,F:8,D:4,C:4,1,-84,(18,-4.5,1,-178)*){C = -(D + F:4 + F:4:4)} [D:0..15,F:0..255]

Least significant bit first (the IRP has no ``msb``), in units of 490 us. The sequence has two parts:

============  ==============================================================
intro         an 18-unit mark and a 9-unit space, then F:8, D:4 and C:4, a 1-unit stop mark and an **84-unit** gap
repeat        an 18-unit mark and a **4.5-unit** space, a 1-unit mark and a **178-unit** gap
============  ==============================================================

A ``0`` is a one-unit mark and a 4.5-unit space, a ``1`` a one-unit mark and a nine-unit space. ``C`` is a four-bit
check: the negated sum of D and the two nibbles of F, modulo 16. So the first send is the whole frame and the rest of a
key held down are the short repeat, which is why this protocol has an intro and a repeat of different shapes, as NEC
does. **The gaps are gaps, not an extent** (``extent_us`` is ``None``, D3). A form's ``device`` is D, its ``function`` is
F, and a ``subdevice`` is an error; ``C`` is computed.

The half unit means a duration is ``4.5 * unit``; the encoder rounds half up on the integer ``(9 * unit + 1) // 2``, which
for the IRP's own 490 us is exactly 2,205 us and for another unit never depends on the float rounding.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{38.7k,490}<1,-4.5|1,-9>(18,-9,F:8,D:4,C:4,1,-84,(18,-4.5,1,-178)*)"
    "{C = -(D + F:4 + F:4:4)} [D:0..15,F:0..255]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L1013 (bengtmartensson/IrpTransmogrifier), "
    "protocol `G.I.Cable`, verbatim, and identical in the 1.2.14 release (`list -i G.I.Cable`). Its `alt_name` is "
    "`GI Cable`. Hardware captures of a remote that sends it are in IrpTransmogrifier's own test data (see "
    "tests/vectors/CITATIONS.md)."
)
_UNIT_US = 490
_BITS = 16


def _bits_lsb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count)]


def check_nibble(device: int, function: int) -> int:
    """``C = -(D + F:4 + F:4:4)`` modulo 16."""
    return -(device + (function & 0xF) + (function >> 4)) & 0xF


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
            f"GI_Cable has no subdevice but one was given ({subdevice}); the address is the four-bit device alone"
        )
    if not 0 <= device <= 0xF:
        raise EncodeError(f"GI_Cable device is {device}; D:4 holds 0-15")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"GI_Cable function is {function}; F:8 holds 0-255")

    half = (9 * unit + 1) // 2                     # 4.5 units
    bits = (_bits_lsb_first(function, 8) + _bits_lsb_first(device, 4)
            + _bits_lsb_first(check_nibble(device, function), 4))
    assert len(bits) == _BITS

    intro: list[int] = [18 * unit, 9 * unit]
    for bit in bits:
        intro += [unit, 2 * half if bit else half]
    intro += [unit, 84 * unit]                     # the stop mark, then the gap
    repeat = (18 * unit, half, unit, 178 * unit)
    return IrSignal(carrier_hz=carrier_hz, intro=tuple(intro), repeat=repeat)


GI_CABLE = Protocol(
    name="GI_Cable",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=38_700,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
