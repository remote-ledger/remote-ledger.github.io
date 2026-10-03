"""F12_relaxed, a 12-bit frame whose bits are distinguished by mark width.

IRP, verbatim from IrpTransmogrifier's protocol database (the two spaces
before the parameter list are in the source)::

    {37.9k,422}<1,-3|3,-1>(D:3,S:1,F:8,-80)*  [D:0..7,S:0..1,F:0..255]

The bit encoding is the unusual part: a zero is a one-unit mark then a
three-unit space, and a one is a **three**-unit mark then a one-unit space.
Every bit is four units long and ends on a space. Fields are least
significant bit first: three device bits, one subdevice bit (``S``), eight
function bits.

``-80`` is a gap of 80 units that follows a bit which already ended on a
space, so the two **merge**: the frame's final space is the last bit's own
space plus 80 units, which is 1266 + 33,760 = 35,026 us after a zero and
422 + 33,760 = 34,182 us after a one. It is a plain gap, not an extent
(``^``), so the registry records no extent and D31 falls back to
``defaultGapUs``.

"Relaxed" is IrpTransmogrifier's own word for it. The strict ``F12`` is
``((D:3,S:1,F:8,-80)2)*``, two frames to every repeat unit, and ``F12-0`` /
``F12-1`` are DecodeIR's two ``H`` cases of the same family. Only the relaxed
form is registered. Its frame is the strict one's frame, so what is lost is
the requirement that a receiver be sent it twice, which is a ``minSends``
question (D3a) and not part of the waveform.

The frame has no intro, so D6 rule 6 emits ``NNNN = 0000``.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = "{37.9k,422}<1,-3|3,-1>(D:3,S:1,F:8,-80)*  [D:0..7,S:0..1,F:0..255]"
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 "
    "L893-L895 (bengtmartensson/IrpTransmogrifier), verbatim and identical "
    "in the 1.2.14 release. Its documentation calls it the relaxed version of "
    "the F12 specification, whose DecodeIR form is on "
    "hifi-remote.com/wiki/index.php?title=DecodeIR#F12. Field layout, bit "
    "encoding and bit order are confirmed by eleven hardware captures of a "
    "strict-F12 remote in IrpTransmogrifier's own test data "
    "(src/test/teaserfiles/F12.ict, expected decodes in F12.exp), see "
    "tests/vectors/CITATIONS.md."
)
_UNIT_US = 422
_GAP_UNITS = 80
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
) -> IrSignal:
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    if not 0 <= device <= 0x7:
        raise EncodeError(f"F12_relaxed device is {device}; D:3 holds 0-7")
    if subdevice is None:
        raise EncodeError(
            "F12_relaxed requires an explicit subdevice (S:1, 0 or 1); the "
            "IRP gives it no default"
        )
    if subdevice not in (0, 1):
        raise EncodeError(f"F12_relaxed subdevice is {subdevice}; S:1 holds 0-1")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"F12_relaxed function is {function}; F:8 holds 0-255")

    bits = (
        _bits_lsb_first(device, 3)
        + _bits_lsb_first(subdevice, 1)
        + _bits_lsb_first(function, 8)
    )
    frame: list[int] = []
    for bit in bits:
        frame += [3 * unit, unit] if bit else [unit, 3 * unit]
    frame[-1] += _GAP_UNITS * unit                 # -80 merges into the last space
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


F12_RELAXED = Protocol(
    name="F12_relaxed",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=37_900,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
