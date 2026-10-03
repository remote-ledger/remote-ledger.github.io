"""Proton, a 16-bit frame at 38.5 kHz.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {38.5k,500}<1,-1|1,-3>(16,-8,D:8,1,-8,F:8,1,^63m)*[D:0..255,F:0..255]

A 16-unit mark and 8-unit space, the device byte, a one-unit mark and eight
units of space as a divider, the function byte, a stop mark, and the space
that pads the frame to 63 ms. Both bytes are least-significant bit first.

There is a sibling, ``Proton-40``, with the identical IRP at 40.5 kHz. It is
**not** registered: the SwiftRemote app's Proton encoder carries 38.5 kHz,
which is this one, and a carrier is the remote file's own business (D3) --
the 2 kHz between the two is a ``carrierHz`` choice, not a second encoder.

The frame has no intro (the ``*`` repeats the whole frame), so D6 rule 6 emits
``NNNN = 0000`` and everything goes in the repeat slot.

IrpTransmogrifier's documentation says of it: "This is not a robust protocol,
so spurious decodes are likely." That is a statement about *decoding* -- there
is no checksum -- and does not affect encoding.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = "{38.5k,500}<1,-1|1,-3>(16,-8,D:8,1,-8,F:8,1,^63m)*[D:0..255,F:0..255]"
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 "
    "L2084-L2086 (bengtmartensson/IrpTransmogrifier), verbatim and identical "
    "in the 1.2.14 release. Field layout and bit order are confirmed by nine "
    "hardware captures of a Proton-protocol remote in IrpTransmogrifier's own "
    "test data (src/test/teaserfiles/Proton.ict, expected decodes in "
    "Proton.exp), see tests/vectors/CITATIONS.md."
)
_UNIT_US = 500
_EXTENT_US = 63_000
_BITS = 16


def _bits_lsb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count)]


def _byte(frame: list[int], value: int, unit: int) -> None:
    for bit in _bits_lsb_first(value, 8):
        frame += [unit, 3 * unit] if bit else [unit, unit]


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

    if subdevice is not None:
        raise EncodeError(
            f"Proton has no subdevice but one was given ({subdevice}); the "
            "address is the eight-bit device alone"
        )
    if not 0 <= device <= 0xFF:
        raise EncodeError(f"Proton device is {device}; D:8 holds 0-255")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"Proton function is {function}; F:8 holds 0-255")

    frame: list[int] = [16 * unit, 8 * unit]
    _byte(frame, device, unit)
    frame += [unit, 8 * unit]                      # the divider: 1,-8
    _byte(frame, function, unit)
    frame.append(unit)                             # stop mark, before ^63m

    head = sum(frame)
    gap = _EXTENT_US - head
    if gap < unit:
        raise EncodeError(
            f"Proton: marks and spaces already total {head} us against an "
            f"extent of {_EXTENT_US} us, leaving {gap} us -- below one unit "
            f"({unit} us). Clamping would fabricate a waveform that fails the "
            "extent it claims (D31)"
        )
    frame.append(gap)
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


PROTON = Protocol(
    name="Proton",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=38_500,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
