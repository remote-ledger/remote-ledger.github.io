"""JVC, in its 16-bit form.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {37.9k,527,33%}<1,-1|1,-3>(16,-8,D:8,F:8,1,^59.08m,(D:8,F:8,1,^46.42m)*) [D:0..255,F:0..255]

The one thing that makes JVC unlike NEC1 is that **only the first frame has a
lead-in**. The intro is a 16-unit mark, an 8-unit space, two LSB-first bytes
(device, function) and a one-unit stop mark, padded to 59.08 ms. The repeat is
the same two bytes and stop mark with *no* lead-in, padded to 46.42 ms, so it
is 8,432 + 4,216 us shorter than the intro before padding and the two
sequences have different extents. The encoder emits both, as
IrpTransmogrifier renders them.

The registry holds this one only, from the family IrpTransmogrifier lists:

* ``JVC{2}`` -- the repeat frame alone, the lead-in missing. IrpTransmogrifier
  documents it as the same protocol seen without its lead-in, and its IRP is
  this one's repeat.
* ``JVC_squashed`` -- decode-only, for signals whose repeat finder has
  over-reduced them.
* ``JVC-48`` and ``JVC-56`` -- members of the Kaseikyo family (``3,1`` OEM
  codes, a 432 us unit, 48 and 56 bits). The same brand, a different
  protocol: not 16 bits, not this carrier, not this timing.

The IRP's ``33%`` duty cycle is not modelled: an :class:`IrSignal` carries a
carrier frequency and durations only, as for every other registered protocol.

``extent_us`` is ``None``. The intro and repeat have *different* extents
(59.08 and 46.42 ms), and D31 pads a truncated sequence to one figure.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from ._pulse import check_byte, lsb_bits, pad_to_extent, pulse_distance
from .base import Protocol

_IRP = (
    "{37.9k,527,33%}<1,-1|1,-3>(16,-8,D:8,F:8,1,^59.08m,"
    "(D:8,F:8,1,^46.42m)*) [D:0..255,F:0..255]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L1207 "
    "(bengtmartensson/IrpTransmogrifier), protocol `JVC`, verbatim; the same "
    "string is in the 1.2.14 release's IrpProtocols.xml (L1207), which "
    "tests/vectors/irpt_jvc_D5_F19.pronto was rendered from. Its "
    "documentation states that JVC has a lead-in on the first frame only and "
    "cites JVC's own remote-code document "
    "(support.jvc.com/consumer/support/documents/RemoteCodes.pdf). The field "
    "layout, bit order and both frame shapes are corroborated by "
    "IrpTransmogrifier's teaser capture src/test/teaserfiles/JVC.ict @c945e76, "
    "decoded by its JVC.exp as {D=5,F=n} (see tests/vectors/CITATIONS.md)."
)
_UNIT_US = 527
_INTRO_EXTENT_US = 59_080
_REPEAT_EXTENT_US = 46_420
_BITS = 16


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    """Render JVC ``{D, F}`` as an :class:`IrSignal`."""
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    if subdevice is not None:
        raise EncodeError(
            f"JVC has no subdevice but one was given ({subdevice}); the "
            "16-bit frame is D:8 and F:8 alone (JVC-48 and JVC-56 are "
            "Kaseikyo-family protocols with a subdevice, and are not "
            "registered)"
        )
    check_byte("device", device, "JVC", "D")
    check_byte("function", function, "JVC", "F")

    data = pulse_distance(lsb_bits(device, 8) + lsb_bits(function, 8), unit, 1, 3)
    intro = pad_to_extent(
        [16 * unit, 8 * unit, *data, unit], _INTRO_EXTENT_US, unit, "JVC", "intro"
    )
    repeat = pad_to_extent(
        [*data, unit], _REPEAT_EXTENT_US, unit, "JVC", "repeat"
    )
    return IrSignal(carrier_hz=carrier_hz, intro=tuple(intro), repeat=tuple(repeat))


JVC = Protocol(
    name="JVC",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=37_900,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
