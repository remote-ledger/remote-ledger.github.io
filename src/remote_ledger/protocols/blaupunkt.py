"""Blaupunkt (IrpTransmogrifier's alternate name: Motorola).

IRP, verbatim from IrpTransmogrifier's protocol database::

    {30.3k,512}<-1,1|1,-1>(1,-5,1023:10, -44, (1,-5,1:1,F:6,D:3,-236)+ ,1,-5,1023:10,-44)[F:0..63,D:0..7]

A transmission has three parts, and only the middle one carries a code:

====================  ========================================================
sync                  ``1,-5,1023:10,-44`` -- a mark, five units of space, then
                      ten ``1`` bits, which in this biphase coding are ten
                      one-unit marks, and a long gap
frame (repeated)      ``1,-5,1:1,F:6,D:3,-236`` -- the same opening, a fixed
                      ``1`` bit, six function bits and three device bits, all
                      LSB-first, and a gap of 236 units
sync (again)          the same sync, closing the transmission
====================  ========================================================

Biphase here is ``<-1,1|1,-1>``: a ``1`` is a mark then a space, a ``0`` a
space then a mark, so adjacent halves of the same level merge into one longer
duration. Only the run lengths exist in a waveform, which is why the encoder
works on half-bits and merges them (as ``rc5.py`` does) rather than emitting
pairs.

**The closing sync cannot be carried.** ``IrSignal.ending`` is reserved and
must be empty (D1), so the final sync is dropped, exactly as IrpTransmogrifier
drops it when it renders Pronto (it warns: "a (non-empty) ending sequence was
ignored"). The ending here is identical to the opening sync, so the dropped
part is recoverable: ``tools/irblaster_oracle_unknown.py`` checks it as the
opening sync repeated. IrpTransmogrifier's Pronto output also folds one copy
of the frame into the intro, because the ``+`` means "at least once"; so
``intro`` is sync + frame and ``repeat`` is the frame, and a single key press
that sends only the intro is still a complete code.

``D`` is three bits, so a Blaupunkt device is 0-7, and there is no subdevice.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{30.3k,512}<-1,1|1,-1>(1,-5,1023:10, -44, (1,-5,1:1,F:6,D:3,-236)+ "
    ",1,-5,1023:10,-44)[F:0..63,D:0..7]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml L441 (release "
    "1.2.14 and @c945e76 alike; bengtmartensson/IrpTransmogrifier), verbatim; "
    "the same string is the fixture in its ProtocolNGTest.java L100 @c945e76. "
    "The XML gives the alternate name Motorola. Corroborated by a hardware "
    "capture in the same repository, src/test/teaserfiles/Blaupunkt.ict, "
    "whose expected decode (Blaupunkt.exp) is Blaupunkt {F=21,D=2} for 'Ch+'."
)
_UNIT_US = 512
_BITS = 10  # the fixed 1, six function bits, three device bits


def _bits_lsb_first(value: int, width: int) -> list[int]:
    return [(value >> i) & 1 for i in range(width)]


def _runs(halves: list[int], unit: int) -> list[int]:
    """Merge unit-length half-bits (1 = mark, 0 = space) into durations."""
    out: list[int] = []
    level = halves[0]
    run = 0
    for half in halves:
        if half == level:
            run += unit
        else:
            out.append(run)
            level = half
            run = unit
    out.append(run)
    return out


def _with_gap(halves: list[int], gap_units: int) -> list[int]:
    """Append the gap as space half-bits, so a trailing space lengthens."""
    return halves + [0] * gap_units


def _sync(unit: int) -> list[int]:
    # 1,-5, then ten 1 bits (mark,space each), then -44 merging into the last.
    halves = [1] + [0] * 5 + [1, 0] * 10
    return _runs(_with_gap(halves, 44), unit)


def _frame(device: int, function: int, unit: int) -> list[int]:
    halves = [1] + [0] * 5
    bits = [1, *_bits_lsb_first(function, 6), *_bits_lsb_first(device, 3)]
    for bit in bits:
        halves += [1, 0] if bit else [0, 1]
    return _runs(_with_gap(halves, 236), unit)


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
            f"Blaupunkt has no subdevice but one was given ({subdevice}); "
            "the address is the three-bit device alone"
        )
    if not 0 <= device <= 7:
        raise EncodeError(f"Blaupunkt device is {device}; D:3 holds 0-7")
    if not 0 <= function <= 63:
        raise EncodeError(f"Blaupunkt function is {function}; F:6 holds 0-63")

    sync = _sync(unit)
    frame = _frame(device, function, unit)
    return IrSignal(
        carrier_hz=carrier_hz,
        intro=tuple(sync + frame),
        repeat=tuple(frame),
    )


BLAUPUNKT = Protocol(
    name="Blaupunkt",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=30_300,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
