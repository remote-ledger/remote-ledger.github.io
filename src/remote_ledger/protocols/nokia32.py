"""Nokia32: a pulse-position code of Nokia's digital receivers, 36 kHz, two bits to a symbol, every time in carrier periods.

IRP, verbatim from IrpTransmogrifier's protocol database (``Nokia32``; the space before the parameter block is the
database's own)::

    {36k,1p,msb}<6,-10|6,-16|6,-22|6,-28>((15,-10,D:8,S:8,T:1,X:7,F:8,6,^100m)*,T=1-T) [D:0..255,S:0..255,F:0..255,T@:0..1=0,X:0..127]

``1p`` is **one period of the carrier**: every duration is a number of periods, so it follows the file's
``carrierHz`` (27.8 us at 36 kHz). A frame, most significant bit first:

=========  ==================================================================
lead-in    a 15-period mark and a 10-period space
symbols    sixteen, two bits each: a 6-period mark then a space of 10, 16, 22 or 28 periods for ``00``, ``01``,
           ``10`` and ``11``. The 32 bits are D:8, S:8, T:1 and X:7 (one byte between them), F:8
stop       a 6-period mark, then the space that makes the frame ``^100m`` from its first mark
=========  ==================================================================

A form has a device, a subdevice and a function. ``device`` is D, ``subdevice`` is S and **``function`` is the fifteen
bits ``X:F``**, ``X * 256 + F`` (a packing the ledger chose, as for Samsung36's ``E:F``). The toggle ``T`` is not in the
file (D3b): everything compiled has ``T=0``, the IRP's own default, and ``encode`` takes ``toggle`` for the tests.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{36k,1p,msb}<6,-10|6,-16|6,-22|6,-28>((15,-10,D:8,S:8,T:1,X:7,F:8,6,^100m)*,T=1-T) "
    "[D:0..255,S:0..255,F:0..255,T@:0..1=0,X:0..127]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 (bengtmartensson/IrpTransmogrifier), protocol "
    "`Nokia32`, verbatim, and identical in the 1.2.14 release (`list -i Nokia32`). Hardware captures of a remote that "
    "sends it are in IrpTransmogrifier's own test data (see tests/vectors/CITATIONS.md). The ledger's `function` is "
    "`X * 256 + F`, a packing the ledger chose, since a form has no fourth field."
)
_EXTENT_US = 100_000
#: Nominal period of 36 kHz, rounded: informational (the unit is the file's own carrier period).
_UNIT_US = 28
#: Bits after the lead-in.
_BITS = 32
_SPACES = (10, 16, 22, 28)       # periods of space after a 6-period mark, for the symbols 00, 01, 10 and 11


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
    toggle: int = 0,
) -> IrSignal:
    if unit_us is not None:
        check_bounds("unitUs", unit_us, UNIT_US_MIN, UNIT_US_MAX)
    if subdevice is None:
        raise EncodeError("Nokia32 requires an explicit subdevice (S:8)")
    if not 0 <= device <= 0xFF:
        raise EncodeError(f"Nokia32 device is {device}; D:8 holds 0-255")
    if not 0 <= subdevice <= 0xFF:
        raise EncodeError(f"Nokia32 subdevice is {subdevice}; S:8 holds 0-255")
    if not 0 <= function <= 0x7FFF:
        raise EncodeError(f"Nokia32 function is {function}; it is the pair X:F, 15 bits (X * 256 + F), 0-32767")
    if toggle not in (0, 1):
        raise EncodeError(f"Nokia32 toggle is {toggle!r}; T:1 is 0 or 1")
    x, f = function >> 8, function & 0xFF

    period = (1_000_000 / carrier_hz) if unit_us is None else unit_us

    def us(periods: int) -> int:
        return round(periods * period)

    word = device << 24 | subdevice << 16 | toggle << 15 | x << 8 | f
    frame: list[int] = [us(15), us(10)]
    for shift in range(30, -1, -2):                      # sixteen symbols, the most significant first
        frame += [us(6), us(_SPACES[(word >> shift) & 3])]
    frame.append(us(6))                                  # the stop mark
    gap = _EXTENT_US - sum(frame)
    if gap < period:
        raise EncodeError(
            f"Nokia32: marks and spaces already total {sum(frame)} us against an extent of {_EXTENT_US} us, leaving "
            f"{gap} us -- below one period ({period:.1f} us). Clamping would fabricate a waveform that fails the "
            "extent it claims (D31)"
        )
    frame.append(gap)
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


NOKIA32 = Protocol(
    name="Nokia32",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=36_000,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
