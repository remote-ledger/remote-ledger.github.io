"""Sony SIRC, in its 12-, 15- and 20-bit variants.

The three differ only in how many address bits follow the seven command
bits, and IrpTransmogrifier registers them as three protocols, not one
protocol with a width parameter. So do we. Each frame is the lead-in
``4,-1`` then the fields below, least significant bit first, and ``^45m``::

    Sony12   (4,-1,F:7,D:5,^45m)           D 0-31
    Sony15   (4,-1,F:7,D:8,^45m)           D 0-255
    Sony20   (4,-1,F:7,D:5,S:8,^45m)       D 0-31, S 0-255

Sony12 and Sony15 are IrpTransmogrifier's verbatim strings (``IrpProtocols.xml``
@c945e76 L2581 and L2591), which use ``*`` where DecodeIR's documentation has
``+``; both emit the whole frame as the repeat here.

Sony20's IRP, from John Fine's DecodeIR documentation on hifi-remote.com::

    {40k,600}<1,-1|2,-1>(4,-1,F:7,D:5,S:8,^45m)+

Two things differ from NEC1 and both matter to the encoder:

* There is **no intro sequence.** The whole frame is the repeat, marked by
  ``+`` rather than NEC's separate ``(...)*`` tail -- so D6 rule 6 emits
  ``NNNN = 0000`` and puts everything in the second slot.
* The frame **ends on a space**, because the last bit contributes one. The
  ``^45m`` extent therefore *lengthens that final space* rather than
  appending a gap, which is what NEC1 does after its stop mark. Appending
  would add a burst pair that no Sony decoder expects.

``minSends`` is 3 for SIRC -- a fact about the hardware that D3a keeps out
of the waveform and in the compiled artifact's metadata. It holds for all
three variants.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_UNIT_US = 600
_EXTENT_US = 45_000

_SONY12_IRP = "{40k,600}<1,-1|2,-1>(4,-1,F:7,D:5,^45m)*[D:0..31,F:0..127]"
_SONY12_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2579-L2587 "
    "(bengtmartensson/IrpTransmogrifier), verbatim; identical in the 1.2.14 "
    "release's copy (L2560-L2568). hifi-remote.com/johnsfine/DecodeIR.html "
    "(John Fine) gives the same timings and F:7,D:5 field layout with '+' "
    "where this has '*'."
)
_SONY15_IRP = "{40k,600}<1,-1|2,-1>(4,-1,F:7,D:8,^45m)*[D:0..255,F:0..127]"
_SONY15_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2589-L2595 "
    "(bengtmartensson/IrpTransmogrifier), verbatim; identical in the 1.2.14 "
    "release's copy (L2570-L2576). hifi-remote.com/johnsfine/DecodeIR.html "
    "(John Fine) gives the same timings and F:7,D:8 field layout with '+' "
    "where this has '*'."
)
_SONY20_IRP = "{40k,600}<1,-1|2,-1>(4,-1,F:7,D:5,S:8,^45m)+"
_SONY20_SOURCE = (
    "hifi-remote.com/johnsfine/DecodeIR.html (John Fine, DecodeIR "
    "documentation), retrieved 2026-09-19. Gives Sony12, Sony15 and Sony20 "
    "as {40k,600}<1,-1|2,-1> with lead-in 4,-1, a 45 ms extent, and Sony20's "
    "field layout as F:7,D:5,S:8."
)


def _bits_lsb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count)]


def _frame(
    name: str,
    fields: tuple[tuple[int, int], ...],
    carrier_hz: int,
    unit_us: int | None,
) -> IrSignal:
    """One SIRC frame: lead-in, then each ``(value, width)`` least significant
    bit first, the last space lengthened to the 45 ms extent."""
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    frame: list[int] = [4 * unit, unit]
    for value, width in fields:
        for bit in _bits_lsb_first(value, width):
            frame += [2 * unit, unit] if bit else [unit, unit]

    # The frame already ends on a space, so ^45m lengthens it rather than
    # appending a pair.
    head = sum(frame[:-1])
    gap = _EXTENT_US - head
    if gap < unit:
        raise EncodeError(
            f"{name}: marks and spaces already total {head} us against an "
            f"extent of {_EXTENT_US} us, leaving {gap} us -- below one unit "
            f"({unit} us). Clamping would fabricate a waveform that fails the "
            "extent it claims (D31)"
        )
    frame[-1] = gap

    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


def encode12(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    if not 0 <= function <= 0x7F:
        raise EncodeError(f"Sony12 function is {function}; F:7 holds 0-127")
    if not 0 <= device <= 0x1F:
        raise EncodeError(f"Sony12 device is {device}; D:5 holds 0-31")
    if subdevice is not None:
        raise EncodeError(
            f"Sony12 has no subdevice but one was given ({subdevice}); the "
            "address is the five-bit device alone. A subdevice means "
            "Sony20 (D:5,S:8)"
        )
    return _frame("Sony12", ((function, 7), (device, 5)), carrier_hz, unit_us)


def encode15(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    if not 0 <= function <= 0x7F:
        raise EncodeError(f"Sony15 function is {function}; F:7 holds 0-127")
    if not 0 <= device <= 0xFF:
        raise EncodeError(f"Sony15 device is {device}; D:8 holds 0-255")
    if subdevice is not None:
        raise EncodeError(
            f"Sony15 has no subdevice but one was given ({subdevice}); the "
            "address is the eight-bit device alone. A subdevice means "
            "Sony20 (D:5,S:8)"
        )
    return _frame("Sony15", ((function, 7), (device, 8)), carrier_hz, unit_us)


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    if not 0 <= function <= 0x7F:
        raise EncodeError(f"Sony20 function is {function}; F:7 holds 0-127")
    if not 0 <= device <= 0x1F:
        raise EncodeError(
            f"Sony20 device is {device}; D:5 holds 0-31. Sony's device number "
            "is five bits here -- the eight-bit field is the subdevice"
        )
    if subdevice is None:
        raise EncodeError(
            "Sony20 requires an explicit subdevice (S:8). A 12- or 15-bit "
            "Sony frame is Sony12 or Sony15, which are separate protocols"
        )
    if not 0 <= subdevice <= 0xFF:
        raise EncodeError(f"Sony20 subdevice is {subdevice}; S:8 holds 0-255")
    return _frame(
        "Sony20", ((function, 7), (device, 5), (subdevice, 8)), carrier_hz, unit_us
    )


SONY12 = Protocol(
    name="Sony12",
    irp=_SONY12_IRP,
    irp_source=_SONY12_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=40_000,
    extent_us=_EXTENT_US,
    bits=12,
    encode=encode12,
)

SONY15 = Protocol(
    name="Sony15",
    irp=_SONY15_IRP,
    irp_source=_SONY15_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=40_000,
    extent_us=_EXTENT_US,
    bits=15,
    encode=encode15,
)

SONY20 = Protocol(
    name="Sony20",
    irp=_SONY20_IRP,
    irp_source=_SONY20_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=40_000,
    extent_us=_EXTENT_US,
    bits=20,
    encode=encode,
)
