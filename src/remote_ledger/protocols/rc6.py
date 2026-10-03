"""Philips RC-6, mode 0, with a sixteen-bit payload (RC6-0-16).

IRP, verbatim from IrpTransmogrifier's protocol database (the space before
the parameter block is the database's own)::

    {36k,444,msb}<-1,1|1,-1>((6,-2,1:1,0:3,<-2,2|2,-2>(T:1),D:8,F:8,^107m)*,T=1-T) [D:0..255,F:0..255,T@:0..1=0]

A frame, in units of 444 us, most significant bit first:

=========  ==================================================================
leader     a 6-unit mark and a 2-unit space (2,664 and 888 us)
start      one ``1`` bit
mode       three ``0`` bits. The database's ``RC6-M-16`` is this frame with
           the mode a parameter ``M``; this entry is its ``M=0`` case, which
           the database prefers it over (``prefer-over RC6-M-16``). Modes
           other than 0 are **not** registered: ``RC6-6-20`` (mode 6, a
           four-bit subdevice) is a different frame, and nothing here has
           been checked against it.
trailer    the toggle bit ``T`` at **double width**: a ``1`` is two units of
           mark then two of space, a ``0`` the reverse
D:8, F:8   address and command
=========  ==================================================================

A bit other than the trailer is biphase at one unit per half: a ``1`` is a
mark then a space and a ``0`` a space then a mark. That is the *opposite*
polarity to RC-5 (``<1,-1|-1,1>`` there).

Three things shape the encoder, each as for RC-5:

* **The frame opens on the leader's mark**, so unlike RC-5 nothing is dropped
  from the front.
* **The ``^107m`` extent is measured from that first mark.** The frame is 52
  units (23,088 us); the rest of the 107,000 us is the gap. A last command
  bit of ``1`` ends the frame on a space, which the extent lengthens; a ``0``
  ends it on a mark, which gets the gap appended.
* **The frame has no intro.** It is the repeat, as for RC-5.

**The toggle is not in the remote file** (D3b). ``encode`` takes ``toggle``
for the tests and for whoever adds the second state as a candidate; a file
cannot set it, so everything compiled here has ``T=0``, the IRP's own
default.

**Note what the 107 ms is.** The standard's minimum idle time before the next
frame is only six units (2,664 us); 107 ms is the *repeat period* the
database's IRP uses. SwiftRemote's encoder ends a frame after the six-unit
idle instead (``rc6.dart``), so against that app the ledger's last duration
is deliberately longer. See ``irblaster/hex_philips.py``.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{36k,444,msb}<-1,1|1,-1>((6,-2,1:1,0:3,<-2,2|2,-2>(T:1),D:8,F:8,^107m)*,T=1-T) "
    "[D:0..255,F:0..255,T@:0..1=0]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2143-L2145 "
    "(bengtmartensson/IrpTransmogrifier), verbatim. Its documentation names "
    "this the first member of the RC6 family, technically RC6-0-16, and "
    "points to sbprojects.net/knowledge/ir/rc6.php as the tutorial. The "
    "frame, the double-width trailer bit and the 107 ms extent are "
    "confirmed by the published vectors in tests/vectors/CITATIONS.md."
)
_UNIT_US = 444
_EXTENT_US = 107_000
#: Bits after the leader, as RC5's 14 counts its start bits: start, three of
#: mode, the trailer, eight of address and eight of command.
_BITS = 21
_MODE = 0


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

    if subdevice is not None:
        raise EncodeError(
            f"RC6 has no subdevice but one was given ({subdevice}); the "
            "address is the eight-bit device alone"
        )
    if not 0 <= device <= 0xFF:
        raise EncodeError(f"RC6 device is {device}; D:8 holds 0-255")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"RC6 function is {function}; F:8 holds 0-255")
    if toggle not in (0, 1):
        raise EncodeError(f"RC6 toggle is {toggle!r}; T:1 is 0 or 1")

    levels = [1] * 6 + [0] * 2                       # leader 6,-2
    levels += _bit_levels(1)                          # start bit
    for bit in _bits_msb_first(_MODE, 3):             # mode
        levels += _bit_levels(bit)
    levels += _bit_levels(toggle, units_per_half=2)   # trailer, double width
    for bit in _bits_msb_first(device, 8) + _bits_msb_first(function, 8):
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

    # As for RC-5: a frame ending on a mark gets the gap appended, one ending
    # on a space has that space lengthened, since a Pronto sequence is
    # mark/space pairs and may not end on a bare mark.
    ends_on_space = len(frame) % 2 == 0
    head = sum(frame[:-1]) if ends_on_space else sum(frame)
    gap = _EXTENT_US - head
    if gap < unit:
        raise EncodeError(
            f"RC6: marks and spaces already total {head} us against an "
            f"extent of {_EXTENT_US} us, leaving {gap} us -- below one unit "
            f"({unit} us). Clamping would fabricate a waveform that fails the "
            "extent it claims (D31)"
        )
    if ends_on_space:
        frame[-1] = gap
    else:
        frame.append(gap)

    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


RC6 = Protocol(
    name="RC6",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=36_000,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
