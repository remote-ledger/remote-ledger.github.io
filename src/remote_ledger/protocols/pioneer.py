"""Pioneer-2Part: Pioneer's two-signal form.

IRP, verbatim from IrpTransmogrifier's protocol database (``Pioneer-2Part``,
alias ``Pioneer-Mix``)::

    {40k,564}<1,-1|1,-3>(16,-8,D0:8,~D0:8,F0:8,~F0:8,1,^90m,(16,-8,D:8,~D:8,F:8,~F:8,1,^90m)+) [D0:0..255,F0:0..255,D:0..255=D0,F:0..255=F0]

A frame is NEC-shaped: a 16-unit mark and 8-unit space, four LSB-first bytes
(a device, its complement, a function, its complement), a one-unit stop mark,
and a space that pads the frame to 90 ms. The signal is **two different
frames**: the intro is ``(D0, F0)`` followed by ``(D, F)``, and the repeat,
which the ``+`` makes mandatory in the intro as well, is ``(D, F)`` alone.
That is IrpTransmogrifier's own rendering and the encoder reproduces it frame
for frame, which is why ``intro`` holds two frames and ``repeat`` one.

Which Pioneer? IrpTransmogrifier also has plain ``Pioneer``, ``(16,-8,D:8,S:8,
F:8,~F:8,1,^108m)*``, a single frame repeated. **Only this one is registered.**
Every SwiftRemote database code sends two frames, the second possibly
different from the first, so the 2-part form is the one that covers all of
them; a code whose two halves are equal is the degenerate case of it
(``D = D0``, ``F = F0``) and renders to the same two frames, where plain
``Pioneer`` would render one frame at a different extent. IrpTransmogrifier
itself prefers plain ``Pioneer`` for such a signal when it *decodes* one, and
nothing here depends on that. Registering a protocol nothing uses is what D18
is written to prevent.

**Parameters.** A remote file's ``irp`` form has three numbers, this protocol
has four. ``device`` and ``function`` each carry a *pair* of bytes, first
frame in the high byte and second frame in the low byte::

    device   = D0 * 256 + D       (16 bits)
    function = F0 * 256 + F       (16 bits)
    subdevice is not used and must be omitted

so ``device=0xAAAF, function=0x5B24`` is IrpTransmogrifier's
``{D0=170,F0=91,D=175,F=36}``. Writing it this way never assumes the IRP's
defaults (``D=D0``, ``F=F0``): a frame pair that is meant to match is written
out, ``0xADAD``, not abbreviated.

``extent_us`` is ``None``: D31 pads a truncated sequence to *one* extent, and
the intro here holds two frames that each carry their own.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from ._pulse import lsb_bits, pad_to_extent, pulse_distance
from .base import Protocol

_IRP = (
    "{40k,564}<1,-1|1,-3>(16,-8,D0:8,~D0:8,F0:8,~F0:8,1,^90m,"
    "(16,-8,D:8,~D:8,F:8,~F:8,1,^90m)+) "
    "[D0:0..255,F0:0..255,D:0..255=D0,F:0..255=F0]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2060 "
    "(bengtmartensson/IrpTransmogrifier), protocol `Pioneer-2Part`, verbatim; "
    "the same string is in the 1.2.14 release's IrpProtocols.xml (L2041), "
    "which tests/vectors/irpt_pioneer2part_D170_F91_D175_F36.pronto was "
    "rendered from. Its documentation describes the intro as the 'normal' "
    "signal (D0, F0) and the repeat as the one determined by D and F. The "
    "field layout is corroborated by IrpTransmogrifier's own teaser capture "
    "of a Pioneer receiver, src/test/teaserfiles/PioneerMix2.ict @c945e76, "
    "which its PioneerMix2.exp decodes as {D0=170,F0=91,D=175,F=36} "
    "(see tests/vectors/CITATIONS.md)."
)
_UNIT_US = 564
_EXTENT_US = 90_000
_BITS = 32          # data bits in one frame


def _frame(device: int, function: int, unit: int) -> list[int]:
    body = [16 * unit, 8 * unit]
    for byte in (device, (~device) & 0xFF, function, (~function) & 0xFF):
        body += pulse_distance(lsb_bits(byte, 8), unit, 1, 3)
    body.append(unit)  # the stop mark, before ^90m pads the frame
    return pad_to_extent(body, _EXTENT_US, unit, "Pioneer-2Part", "frame")


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    """Render ``{D0, D, F0, F}`` as an :class:`IrSignal`.

    ``device`` is ``D0 * 256 + D`` and ``function`` is ``F0 * 256 + F``; see
    the module docstring.
    """
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    if subdevice is not None:
        raise EncodeError(
            f"Pioneer-2Part has no subdevice but one was given ({subdevice}); "
            "device is the pair D0:D and function the pair F0:F"
        )
    if not 0 <= device <= 0xFFFF:
        raise EncodeError(
            f"Pioneer-2Part device is {device}; it is the pair D0:D, "
            "two bytes, 0-65535"
        )
    if not 0 <= function <= 0xFFFF:
        raise EncodeError(
            f"Pioneer-2Part function is {function}; it is the pair F0:F, "
            "two bytes, 0-65535"
        )
    d0, d = device >> 8, device & 0xFF
    f0, f = function >> 8, function & 0xFF

    first = _frame(d0, f0, unit)
    second = _frame(d, f, unit)
    # `+` makes one pass of the repeat mandatory, so the intro already holds
    # it; the repeat is that frame alone.
    return IrSignal(
        carrier_hz=carrier_hz,
        intro=tuple(first + second),
        repeat=tuple(second),
    )


PIONEER_2PART = Protocol(
    name="Pioneer-2Part",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=40_000,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
