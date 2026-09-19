"""NEC1.

IRP, confirmed verbatim by IrpTransmogrifier's protocol database::

    {38.4k,564}<1,-1|1,-3>(16,-8,D:8,S:8,F:8,~F:8,1,^108m,(16,-4,1,^108m)*)

Read left to right: unit 564 us at a nominal 38.4 kHz; a zero bit is one unit
of mark and one of space, a one bit is one unit of mark and three of space;
the intro is a 16-unit mark, an 8-unit space, four LSB-first bytes
(device, subdevice, function, function complement), a 1-unit stop mark, and
then whatever space pads the sequence to 108 ms; the repeat is a 16-unit
mark, a 4-unit space, a 1-unit mark, padded to 108 ms the same way.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = "{38.4k,564}<1,-1|1,-3>(16,-8,D:8,S:8,F:8,~F:8,1,^108m,(16,-4,1,^108m)*)"
_IRP_SOURCE = (
    "IrpTransmogrifier's IrpProtocols.xml (bengtmartensson/IrpTransmogrifier), "
    "retrieved 2026-09-19, which gives this IRP string verbatim. Corroborated "
    "for every timing by hifi-remote.com/johnsfine/DecodeIR.html (John Fine), "
    "retrieved 2026-09-14: unit 564 us, lead-in 16/-8 units, <1,-1|1,-3> bit "
    "encoding, LSB-first D:8,S:8,F:8,~F:8, ^108m extent. The two disagree on "
    "the nominal carrier -- 38.4k against DecodeIR's 38.0k -- and "
    "nominal_carrier_hz follows IrpTransmogrifier, which SPEC section 2 names "
    "as the actively-developed reference implementation. It is informational "
    "either way (D3): a file's carrierHz is authoritative."
)
_UNIT_US = 564
_EXTENT_US = 108_000
_BITS = 32


def _bits_lsb_first(value: int, count: int) -> list[int]:
    """``D:8`` and friends are least-significant-bit first."""
    return [(value >> i) & 1 for i in range(count)]


def _pad_to_extent(durations: list[int], unit_us: int, what: str) -> list[int]:
    """Append the space that pads a sequence to its own extent (D31, R12).

    The extent sits *inside* a sequence in IRP notation, so the intro and the
    repeat each pad to their own 108 ms and are never summed.
    """
    head = sum(durations)
    gap = _EXTENT_US - head
    if gap < unit_us:
        raise EncodeError(
            f"NEC1 {what}: marks and spaces already total {head} us against "
            f"an extent of {_EXTENT_US} us, leaving a gap of {gap} us which is "
            f"below one unit ({unit_us} us). Clamping would fabricate a "
            "waveform that fails the extent it claims (D31)"
        )
    return [*durations, gap]


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    """Render NEC1 parameters as an :class:`IrSignal`.

    ``carrier_hz`` comes from the remote file's ``protocol`` block and is
    authoritative (D3); ``unit_us`` overrides the registry's IRP unit and
    wants a ``claims`` entry when it differs (D24, D27).
    """
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    for name, value in (("device", device), ("function", function)):
        if not 0 <= value <= 0xFF:
            raise EncodeError(f"NEC1 {name} is {value}; D:8 and F:8 hold 0-255")
    if subdevice is None:
        raise EncodeError(
            "NEC1 requires an explicit subdevice (S:8). A capture giving one "
            "address byte is the `NEC` variant, where S defaults to ~D -- "
            "backlogged in D18 and not in the v1 registry"
        )
    if not 0 <= subdevice <= 0xFF:
        raise EncodeError(f"NEC1 subdevice is {subdevice}; S:8 holds 0-255")

    intro: list[int] = [16 * unit, 8 * unit]
    for byte in (device, subdevice, function, (~function) & 0xFF):
        for bit in _bits_lsb_first(byte, 8):
            intro += [unit, 3 * unit] if bit else [unit, unit]
    intro.append(unit)  # the stop mark, before ^108m pads the sequence
    intro = _pad_to_extent(intro, unit, "intro")

    repeat = _pad_to_extent([16 * unit, 4 * unit, unit], unit, "repeat")

    return IrSignal(
        carrier_hz=carrier_hz, intro=tuple(intro), repeat=tuple(repeat)
    )


NEC1 = Protocol(
    name="NEC1",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=38_400,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
