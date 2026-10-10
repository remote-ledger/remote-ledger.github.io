"""MCE: Windows Media Center's remote protocol, RC-6 mode 6 with a 32-bit payload (RC6-6-32).

IRP, verbatim from IrpTransmogrifier's protocol database (``MCE``, which its database also reaches as
``RC6-6-32``)::

    {36k,444,msb}<-1,1|1,-1>((6,-2,1:1,6:3,-2,2,OEM1:8,S:8,T:1,D:7,F:8,^107m)*,T=1-T) {OEM1=128}[D:0..127,S:0..255,F:0..255,T@:0..1=0]

A frame, in units of 444 us, most significant bit first, as RC6's (``rc6.py``) with a longer payload:

=========  ==================================================================
leader     a 6-unit mark and a 2-unit space
start      one ``1`` bit
mode       ``6``: the bits ``1 1 0``
trailer    a fixed ``0`` at **double width** (a 2-unit space, a 2-unit mark): the standard RC-6 toggle bit's
           place, which MCE does not toggle
OEM1:8     ``128`` (0x80), fixed by the IRP
S:8        the second OEM byte; Microsoft's own remotes send ``0x0F`` (``0x800F`` is their code)
T:1        a toggle bit of single width: MCE's own, added to the RC-6 family's. A ledger file cannot set it, so it is 0
D:7, F:8   device and command
=========  ==================================================================

Of the three fields a ledger form has, ``device`` is D (7 bits), ``subdevice`` is S and ``function`` is F; the
toggle is not in the file (D3b), as for RC6, and ``encode`` takes ``toggle`` for the tests. A bit other than the
trailer is biphase at one unit per half: a ``1`` is a mark then a space and a ``0`` a space then a mark.

Everything else is RC6's: the frame opens on the leader's mark, the ``^107m`` extent is measured from it (the frame is
74 units, 32,856 us), a frame ending on a space has that space lengthened and one ending on a mark gets the gap
appended, and there is no intro. The standard's minimum idle time is shorter than 107 ms; 107 ms is the repeat
period the database's IRP uses.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{36k,444,msb}<-1,1|1,-1>((6,-2,1:1,6:3,-2,2,OEM1:8,S:8,T:1,D:7,F:8,^107m)*,T=1-T) "
    "{OEM1=128}[D:0..127,S:0..255,F:0..255,T@:0..1=0]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 (bengtmartensson/IrpTransmogrifier), protocol "
    "`MCE`, verbatim, which its documentation calls RC6-6-32 with the standard toggle bit zero, OEM1 equal to 128 and "
    "a nonstandard toggle bit added. The field widths are the same in RemoteMaster's own definition of its `MCE` "
    "executor (hifiremote/Protocols protocols.ini: `Device:7`, `OEM2:8`, OEM1 128 in its fixed data), and the frame is "
    "read off the LIRC project's remotes of four MCE devices (see tests/vectors/CITATIONS.md)."
)
_UNIT_US = 444
_EXTENT_US = 107_000
#: Bits after the leader: start, three of mode, the trailer, and the 32 of the payload.
_BITS = 37
_MODE = 6
_OEM1 = 128


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
        raise EncodeError(
            "MCE requires an explicit subdevice (S:8, the second OEM byte); Microsoft's remotes send 15"
        )
    if not 0 <= device <= 0x7F:
        raise EncodeError(f"MCE device is {device}; D:7 holds 0-127")
    if not 0 <= subdevice <= 0xFF:
        raise EncodeError(f"MCE subdevice is {subdevice}; S:8 holds 0-255")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"MCE function is {function}; F:8 holds 0-255")
    if toggle not in (0, 1):
        raise EncodeError(f"MCE toggle is {toggle!r}; T:1 is 0 or 1")

    levels = [1] * 6 + [0] * 2                        # leader 6,-2
    levels += _bit_levels(1)                           # start bit
    for bit in _bits_msb_first(_MODE, 3):              # mode 6
        levels += _bit_levels(bit)
    levels += _bit_levels(0, units_per_half=2)         # the trailer, fixed 0 at double width: -2,2
    for bit in (_bits_msb_first(_OEM1, 8) + _bits_msb_first(subdevice, 8) + [toggle]
                + _bits_msb_first(device, 7) + _bits_msb_first(function, 8)):
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

    # As for RC6: a frame ending on a mark gets the gap appended, one ending on a space has that space lengthened,
    # since a Pronto sequence is mark/space pairs and may not end on a bare mark.
    ends_on_space = len(frame) % 2 == 0
    head = sum(frame[:-1]) if ends_on_space else sum(frame)
    gap = _EXTENT_US - head
    if gap < unit:
        raise EncodeError(
            f"MCE: marks and spaces already total {head} us against an extent of {_EXTENT_US} us, leaving {gap} us "
            f"-- below one unit ({unit} us). Clamping would fabricate a waveform that fails the extent it claims (D31)"
        )
    if ends_on_space:
        frame[-1] = gap
    else:
        frame.append(gap)

    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


MCE = Protocol(
    name="MCE",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=36_000,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
