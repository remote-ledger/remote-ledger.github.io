"""Thomson7, the twelve-bit Thomson frame with a seven-bit command.

IRP, verbatim from IrpTransmogrifier's protocol database (whose XML comment
says ``like DecodeIR``)::

    {33k,500}<1,-4|1,-9>((D:4,T:1,F:7,1,^80m)*,T=1-T) [D:0..15,F:0..127,T@:0..1=0]

A frame, in units of 500 us, **least** significant bit first -- the IRP has
no ``msb``, which is the opposite of RC-5, RC-6 and RCA:

=====  =========================================================================
D:4    the address, low bit first
T      the toggle bit
F:7    the command, low bit first
bit    a 1-unit mark, then a 4-unit space for a 0 and a 9-unit space for a 1
stop   one 1-unit mark
gap    whatever remains of ``^80m``, measured from the first mark
=====  =========================================================================

* **The frame always ends on a mark** (the stop bit), so the extent is always
  met by appending a gap, never by lengthening a space.
* **The frame has no intro**; ``*`` makes it the repeat (D6 rule 6).
* **Not the database's plain ``Thomson``.** That is ``{33k,500}`` with the
  same bit timings but ``D:4,T:1,D:1:4,F:6`` -- a five-bit address and a
  six-bit command. The database lists Thomson7 as preferred over it
  (``prefer-over Thomson``).

**The toggle is not in the remote file** (D3b); ``encode`` takes ``toggle``
for the tests. Everything compiled here has ``T=0``, the IRP's own default.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{33k,500}<1,-4|1,-9>((D:4,T:1,F:7,1,^80m)*,T=1-T) "
    "[D:0..15,F:0..127,T@:0..1=0]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2869-L2873 "
    "(bengtmartensson/IrpTransmogrifier), verbatim; the entry is commented "
    "'like DecodeIR' and carries UEI executor 004B:7. The bit timings are "
    "corroborated by IrpTransmogrifier's hardware capture of the sibling "
    "Thomson protocol, which shares them (see tests/vectors/CITATIONS.md)."
)
_UNIT_US = 500
_EXTENT_US = 80_000
_BITS = 12


def _bits_lsb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count)]


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
            f"Thomson7 has no subdevice but one was given ({subdevice}); the "
            "address is the four-bit device alone"
        )
    if not 0 <= device <= 0xF:
        raise EncodeError(f"Thomson7 device is {device}; D:4 holds 0-15")
    if not 0 <= function <= 0x7F:
        raise EncodeError(f"Thomson7 function is {function}; F:7 holds 0-127")
    if toggle not in (0, 1):
        raise EncodeError(f"Thomson7 toggle is {toggle!r}; T:1 is 0 or 1")

    bits = _bits_lsb_first(device, 4) + [toggle] + _bits_lsb_first(function, 7)
    assert len(bits) == _BITS

    frame: list[int] = []
    for bit in bits:
        frame += [unit, (9 if bit else 4) * unit]
    frame.append(unit)  # the stop mark

    gap = _EXTENT_US - sum(frame)
    if gap < unit:
        raise EncodeError(
            f"Thomson7: marks and spaces already total {sum(frame)} us "
            f"against an extent of {_EXTENT_US} us, leaving {gap} us -- below "
            f"one unit ({unit} us). Clamping would fabricate a waveform that "
            "fails the extent it claims (D31)"
        )
    frame.append(gap)

    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


THOMSON7 = Protocol(
    name="Thomson7",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=33_000,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
