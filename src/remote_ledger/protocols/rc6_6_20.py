"""RC6-6-20: RC-6 mode 6 with a 20-bit payload, the Sky and Sky+ remotes' frame.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {36k,444,msb}<-1,1|1,-1>((6,-2,1:1,6:3,<-2,2|2,-2>(T:1),D:8,S:4,F:8,-100m)*,T=1-T)[D:0..255,S:0..15,F:0..255,T@:0..1=0]

A frame, in units of 444 us, most significant bit first, as RC6's (``rc6.py``) with the mode fixed at 6 and a longer payload:

=========  ==================================================================
leader     a 6-unit mark and a 2-unit space
start      one ``1`` bit
mode       ``6``: the bits ``1 1 0``
trailer    the toggle bit ``T`` at **double width**: a ``1`` is two units of mark then two of space, a ``0`` the reverse
D:8        the device
S:4        the sub device
F:8        the command
gap        100 ms after the last bit
=========  ==================================================================

A bit other than the trailer is biphase at one unit per half: a ``1`` is a mark then a space and a ``0`` a space then a
mark. The database says "the executors for this protocol do not toggle T. This no-toggle version is commonly used in Sky
and Sky+ remotes."

Of the three fields a ledger form has, ``device`` is D, ``subdevice`` is S (required) and ``function`` is F. The toggle is
not in the file (D3b), as for RC6 and MCE; ``encode`` takes ``toggle`` for the tests.

Two things differ from MCE's frame: the gap is a **gap, not an extent** (the IRP ends ``-100m``, so ``extent_us`` is
``None``, D3, and the explicit 100 ms follows the last bit: a frame ending on a space has that space lengthened by it, one
ending on a mark gets a space of its own), and the toggle is in RC-6's place, between the mode and the payload.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{36k,444,msb}<-1,1|1,-1>((6,-2,1:1,6:3,<-2,2|2,-2>(T:1),D:8,S:4,F:8,-100m)*,T=1-T)"
    "[D:0..255,S:0..15,F:0..255,T@:0..1=0]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2158 (bengtmartensson/IrpTransmogrifier), "
    "protocol `RC6-6-20`, verbatim, and identical in the 1.2.14 release (`list -i RC6-6-20`). Its documentation says the "
    "no-toggle version is commonly used in Sky and Sky+ remotes. A hardware capture of a Sky+ remote is in "
    "IrpTransmogrifier's own test data and the LIRC project's Sky remotes carry the frame (see "
    "tests/vectors/CITATIONS.md)."
)
_UNIT_US = 444
_GAP_US = 100_000
#: Bits after the leader: start, three of mode, the trailer, and the 20 of the payload.
_BITS = 25
_MODE = 6


def _bit_levels(bit: int, units_per_half: int = 1) -> list[int]:
    """One bit as per-unit levels, 1 = mark. A 1 is mark-then-space."""
    first, second = (1, 0) if bit else (0, 1)
    return [first] * units_per_half + [second] * units_per_half


def _bits_msb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count - 1, -1, -1)]


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
    toggle: int = 0,
) -> IrSignal:
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    if subdevice is None:
        raise EncodeError("RC6-6-20 requires an explicit subdevice (S:4); Sky's remotes send 0 or 12")
    if not 0 <= device <= 0xFF:
        raise EncodeError(f"RC6-6-20 device is {device}; D:8 holds 0-255")
    if not 0 <= subdevice <= 0xF:
        raise EncodeError(f"RC6-6-20 subdevice is {subdevice}; S:4 holds 0-15")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"RC6-6-20 function is {function}; F:8 holds 0-255")
    if toggle not in (0, 1):
        raise EncodeError(f"RC6-6-20 toggle is {toggle!r}; T:1 is 0 or 1")

    levels = [1] * 6 + [0] * 2                        # leader 6,-2
    levels += _bit_levels(1)                           # start bit
    for bit in _bits_msb_first(_MODE, 3):              # mode 6
        levels += _bit_levels(bit)
    levels += _bit_levels(toggle, units_per_half=2)    # the trailer: T at double width
    for bit in _bits_msb_first(device, 8) + _bits_msb_first(subdevice, 4) + _bits_msb_first(function, 8):
        levels += _bit_levels(bit)

    frame: list[int] = []
    level = levels[0]
    assert level == 1
    run = 0
    for current in levels:
        if current == level:
            run += unit
        else:
            frame.append(run)
            level = current
            run = unit
    frame.append(run)

    # A frame ending on a space has that space lengthened by the gap, one ending on a mark gets a space of its own.
    if len(frame) % 2:
        frame.append(_GAP_US)
    else:
        frame[-1] += _GAP_US
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


RC6_6_20 = Protocol(
    name="RC6-6-20",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=36_000,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
