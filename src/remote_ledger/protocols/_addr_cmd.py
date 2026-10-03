"""The frame Sharp and Denon share, and how each sends it.

Both are ``{38k,264}<1,-3|1,-7>``: a five-bit address ``D``, an eight-bit
command ``F`` and two more bits, all LSB-first, then a one-unit stop mark and
a space that pads the frame to 67 ms. They differ in only two things, which
are the whole difference between the protocols:

==========  ====================  ====================
            Sharp                 Denon
==========  ====================  ====================
normal      ``D, F,  1:2``        ``D, F,  0:2``
inverted    ``D, ~F, 2:2``        ``D, ~F, 3:2``
==========  ====================  ====================

and both send the same sequence of frames: the intro is the normal frame, and
the repeat is the inverted frame then the normal one again::

    (normal,^67m,(inverted,^67m,normal,^67m)*)

IrpTransmogrifier's rendering of that (verified in tests/test_sharp.py and
tests/test_denon.py against its 1.2.14 output) puts one frame in the intro and
two in the repeat, each padded to its own 67 ms.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from ._pulse import check_byte, lsb_bits, pad_to_extent, pulse_distance

EXTENT_US = 67_000
UNIT_US = 264
#: Data bits in one frame: D:5, F:8 and the two extra bits.
BITS = 15


def frame(device: int, function: int, extra: int, unit: int, name: str,
          what: str) -> list[int]:
    bits = lsb_bits(device, 5) + lsb_bits(function, 8) + lsb_bits(extra, 2)
    body = pulse_distance(bits, unit, 3, 7)
    body.append(unit)  # the stop mark, before ^67m pads the frame
    return pad_to_extent(body, EXTENT_US, unit, name, what)


def encode(
    name: str,
    normal_extra: int,
    inverted_extra: int,
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None,
) -> IrSignal:
    unit = UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    if subdevice is not None:
        raise EncodeError(
            f"{name} has no subdevice but one was given ({subdevice}); the "
            "frame is D:5 and F:8 alone"
        )
    check_byte("device", device, name, "D", bits=5)
    check_byte("function", function, name, "F")

    normal = frame(device, function, normal_extra, unit, name, "normal frame")
    inverted = frame(
        device, (~function) & 0xFF, inverted_extra, unit, name, "inverted frame"
    )
    return IrSignal(
        carrier_hz=carrier_hz,
        intro=tuple(normal),
        repeat=tuple(inverted + normal),
    )
