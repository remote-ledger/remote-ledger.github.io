"""Samsung36, the 36-bit Samsung frame with a mid-frame divider.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {37.9k,560,33%}<1,-1|1,-3>(4500u,-4500u,D:8,S:8,1,-9,E:4,F:8,~F:8,1,^108m)*[D:0..255,S:0..255,F:0..255,E:0..15]

A Samsung-specific frame, **not** the NEC variant (NECx2) that the
BN59-01199F speaks. The registry docstring's shorthand for it,
``{38k,500}...(9,-9,...)``, is inexact: the header is 4500 us *absolute*
(``4500u``), the unit is 560 us and the carrier is 37.9 kHz.

Read left to right: a 4500 us mark and a 4500 us space (the ``u`` suffix makes
these absolute microseconds, so they do not scale with the unit); 16 bits
(``D``, ``S``) of unit 560 us, a zero being one unit of mark and one of space
and a one being one mark and three spaces; a **divider** of one unit of mark
and nine of space; 20 more bits (``E``, ``F`` and ``~F``); a stop mark; and
whatever space pads the frame to 108 ms. Every field is least-significant bit
first, because IRP's default bit order is LSB-first and this IRP does not say
``msb``.

Three things shape the encoder:

* **There is no intro sequence.** The ``*`` repeats the whole frame, so D6
  rule 6 emits ``NNNN = 0000`` and everything goes in the repeat slot, the
  same shape as Sony20.
* **The ledger form has three numbers, the IRP has four.** ``E`` is a 4-bit
  extension that sits directly in front of ``F`` on the wire, and the
  remote-file schema gives a form only ``device``, ``subdevice`` and
  ``function`` (``additionalProperties: false``). So ``function`` here is the
  12-bit value ``E:F`` -- ``function = E * 256 + F`` -- and ``encode`` splits
  it. That is a packing the *ledger* chose, not something IrpTransmogrifier's
  parameter list says: its ``F`` is the low byte alone. It is lossless and
  contiguous on the wire (``E``, ``F``, ``~F`` are one run of 20 bits), and
  it is the only way every code in the SwiftRemote database can be written
  without a fourth field -- 269 of its 643 distinct Samsung36 codes have
  ``E != 0``.
  NOTES/misc.md records it as a decision for the owner.
* **The 560 us unit is the IRP's, and real hardware measures nearer 500 us.**
  See ``tests/vectors/CITATIONS.md``. ``unit_us`` is the knob for that: it
  rescales the 560 us parts and leaves ``4500u`` alone.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{37.9k,560,33%}<1,-1|1,-3>(4500u,-4500u,D:8,S:8,1,-9,E:4,F:8,~F:8,1,^108m)*"
    "[D:0..255,S:0..255,F:0..255,E:0..15]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 "
    "L2409-L2411 (bengtmartensson/IrpTransmogrifier), verbatim and identical "
    "in the 1.2.14 release. Its documentation cites "
    "elektrolab.wz.cz/katalog/samsung_protocol.pdf. Field layout and bit "
    "order are confirmed by eight hardware captures of a Samsung Blu-ray "
    "remote in IrpTransmogrifier's own test data (src/test/teaserfiles/"
    "Samsung36.ict, expected decodes in Samsung36.exp), see "
    "tests/vectors/CITATIONS.md. Those captures measure the unit nearer "
    "500 us than 560 us and the inter-frame gap near 60 ms, which the IRP's "
    "^108m extent does not reproduce; the IRP is followed anyway."
)
_UNIT_US = 560
_EXTENT_US = 108_000
_HEADER_US = 4500
_BITS = 36
#: ``function`` is E:F, twelve bits.
FUNCTION_MAX = 0xFFF


def _bits_lsb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count)]


def _field(frame: list[int], value: int, width: int, unit: int) -> None:
    for bit in _bits_lsb_first(value, width):
        frame += [unit, 3 * unit] if bit else [unit, unit]


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    """Render Samsung36 parameters as an :class:`IrSignal`.

    ``function`` is ``E * 256 + F`` (see the module docstring).
    """
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    if not 0 <= device <= 0xFF:
        raise EncodeError(f"Samsung36 device is {device}; D:8 holds 0-255")
    if subdevice is None:
        raise EncodeError(
            "Samsung36 requires an explicit subdevice (S:8); the IRP gives "
            "it no default"
        )
    if not 0 <= subdevice <= 0xFF:
        raise EncodeError(f"Samsung36 subdevice is {subdevice}; S:8 holds 0-255")
    if not 0 <= function <= FUNCTION_MAX:
        raise EncodeError(
            f"Samsung36 function is {function}; the ledger packs E:4 and F:8 "
            "into one value, so it holds 0-4095 (E = function >> 8, "
            "F = function & 255)"
        )
    extension, command = function >> 8, function & 0xFF

    frame: list[int] = [_HEADER_US, _HEADER_US]
    _field(frame, device, 8, unit)
    _field(frame, subdevice, 8, unit)
    frame += [unit, 9 * unit]                      # the divider: 1,-9
    _field(frame, extension, 4, unit)
    _field(frame, command, 8, unit)
    _field(frame, (~command) & 0xFF, 8, unit)
    frame.append(unit)                             # stop mark, before ^108m

    head = sum(frame)
    gap = _EXTENT_US - head
    if gap < unit:
        raise EncodeError(
            f"Samsung36: marks and spaces already total {head} us against an "
            f"extent of {_EXTENT_US} us, leaving {gap} us -- below one unit "
            f"({unit} us). Clamping would fabricate a waveform that fails the "
            "extent it claims (D31)"
        )
    frame.append(gap)
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


SAMSUNG36 = Protocol(
    name="Samsung36",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=37_900,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
