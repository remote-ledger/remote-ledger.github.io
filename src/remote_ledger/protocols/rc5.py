"""Philips RC-5, in its 7-bit-command form.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {36k,msb,889}<1,-1|-1,1>((1,~F:1:6,T:1,D:5,F:6,^114m)*,T=1-T)[D:0..31,F:0..127,T@:0..1=0]

A frame is fourteen biphase bits, most significant bit first:

====  =====================================================================
S1    always 1
S2    ``~F:1:6`` -- the *complement* of command bit 6. Original RC-5 had no
      such bit, so its commands stop at 63; this extension reuses the
      second start bit to reach 127. One encoder covers both, which is why
      this is not two registry entries. Some sources, the LIRC conf for the
      Meridian MSR among them, call the extension "RC-5X". That is **not**
      IrpTransmogrifier's ``RC5x``, a different 20-bit protocol with a
      six-bit subdevice, which stays unregistered.
T     the toggle bit
D:5   the system address
F:6   the low six bits of the command
====  =====================================================================

Three things shape the encoder:

* **Every frame starts with a lone mark.** S1 is a 1, which biphase codes as
  space-then-mark, so the frame opens with 889 us of nothing. That space is
  not part of any recorded waveform, and the published vector confirms it is
  dropped: its first duration is a mark. Leaving S1 out entirely, as one
  LIRC conf for the Meridian MSR does, is a different and invalid frame --
  see DESIGN section 16.
* **The ``^114m`` extent is measured from that first mark.** The frame is 27
  half-bits (24,003 us), so the gap after it is 114,000 - 24,003.
* **The frame has no intro.** It is the repeat, as for Sony, so D6 rule 6
  emits ``NNNN = 0000``.

**The toggle is not in the remote file.** ``T`` flips on every key *press*,
so one Pronto string carries one of its two states (D3b). ``encode`` takes
``toggle`` for the tests and for whoever adds the second state as a
candidate; a file cannot set it, so everything compiled here has ``T=0``,
the IRP's own default.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{36k,msb,889}<1,-1|-1,1>((1,~F:1:6,T:1,D:5,F:6,^114m)*,T=1-T)"
    "[D:0..31,F:0..127,T@:0..1=0]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2101-L2103 "
    "(bengtmartensson/IrpTransmogrifier), verbatim. Its documentation names "
    "Philips as the creator and sbprojects.net/knowledge/ir/rc5.php as the "
    "tutorial. The field layout and the dropped leading space are confirmed "
    "by a hardware capture of a Meridian remote in Flipper-IRDB (see "
    "tests/vectors/CITATIONS.md)."
)
_UNIT_US = 889
_EXTENT_US = 114_000
_BITS = 14


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
            f"RC5 has no subdevice but one was given ({subdevice}); the "
            "address is the five-bit device alone"
        )
    if not 0 <= device <= 0x1F:
        raise EncodeError(f"RC5 device is {device}; D:5 holds 0-31")
    if not 0 <= function <= 0x7F:
        raise EncodeError(f"RC5 function is {function}; F is 0-127")
    if toggle not in (0, 1):
        raise EncodeError(f"RC5 toggle is {toggle!r}; T:1 is 0 or 1")

    bits = [1, 1 - ((function >> 6) & 1), toggle]
    bits += _bits_msb_first(device, 5)
    bits += _bits_msb_first(function & 0x3F, 6)
    assert len(bits) == _BITS

    # Biphase: a 1 is a space then a mark, a 0 a mark then a space.
    halves: list[int] = []
    for bit in bits:
        halves += [0, 1] if bit else [1, 0]

    # S1 is always a 1, so the frame always begins with an idle half-bit.
    # Drop it; what is left starts on a mark.
    assert halves[0] == 0
    halves = halves[1:]

    frame: list[int] = []
    level = 1
    run = 0
    for half in halves:
        if half == level:
            run += unit
        else:
            frame.append(run)
            level = half
            run = unit
    frame.append(run)

    # A frame ending on a mark gets a gap appended; one ending on a space
    # has that space lengthened instead (the Sony rule), since a Pronto
    # sequence is mark/space pairs and may not end on a bare mark.
    ends_on_space = len(frame) % 2 == 0
    head = sum(frame[:-1]) if ends_on_space else sum(frame)
    gap = _EXTENT_US - head
    if gap < unit:
        raise EncodeError(
            f"RC5: marks and spaces already total {head} us against an "
            f"extent of {_EXTENT_US} us, leaving {gap} us -- below one unit "
            f"({unit} us). Clamping would fabricate a waveform that fails the "
            "extent it claims (D31)"
        )
    if ends_on_space:
        frame[-1] = gap
    else:
        frame.append(gap)

    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


RC5 = Protocol(
    name="RC5",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=36_000,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
