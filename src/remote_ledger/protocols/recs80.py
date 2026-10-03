"""RECS80, in the two forms the SwiftRemote database uses.

IRPs, verbatim from IrpTransmogrifier's protocol database::

    RECS80       {38k,158,msb}<1,-31|1,-47>(1:1,T:1,D:3,F:6,1,-45m)* {}[D:0..7,F:0..63, T@:0..1=0]
    RECS80-0068  {33.3k,180,msb}<1,-31|1,-47>(1:1,T:1,D:3,F:6,1,^138m)* [D:0..7,F:0..63, T@:0..1=0]

(Both are written here as they appear, including the empty ``{}`` and the
space before ``T@``.) They are one frame shape at two clock rates. Eleven bits,
most significant first: a constant start bit ``1``, the toggle ``T``, three
device bits, six function bits. A zero is a one-unit mark and 31 units of
space, a one a one-unit mark and 47 units; then a stop mark and a gap.

====  ===============  ==========================================
      ``RECS80``       ``RECS80-0068``
====  ===============  ==========================================
unit  158 us           180 us
bits  4,898 / 7,426 us 5,580 / 8,460 us
gap   ``-45m``         ``^138m``
====  ===============  ==========================================

The gap is the other real difference: ``-45m`` is a **plain gap** of 45 ms
after the stop mark, ``^138m`` an **extent** that pads the whole frame to
138 ms. So ``RECS80`` records no extent (D31 falls back to ``defaultGapUs``)
and ``RECS80-0068`` records 138,000 us.

IrpTransmogrifier also lists ``RECS80-0045``, which is the same IRP as
``RECS80`` without the ``{}``, and ``RECS80-0090``, whose carrier is ``0k``.
Neither is registered. ``RECS80-0045`` would be a second name for the same
waveform, and ``RECS80-0090`` has no carrier to modulate.

``T`` flips on every key *press* and a remote file cannot set it (D3b), exactly
as for RC5. ``encode`` takes ``toggle`` for the tests; everything compiled
from a file has ``T=0``, the IRP's own default.

The frame has no intro (``*``), so D6 rule 6 emits ``NNNN = 0000``.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP_RECS80 = (
    "{38k,158,msb}<1,-31|1,-47>(1:1,T:1,D:3,F:6,1,-45m)* {}[D:0..7,F:0..63, T@:0..1=0]"
)
_IRP_RECS80_0068 = (
    "{33.3k,180,msb}<1,-31|1,-47>(1:1,T:1,D:3,F:6,1,^138m)* [D:0..7,F:0..63, T@:0..1=0]"
)
_SOURCE_RECS80 = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 "
    "L2272-L2275 (bengtmartensson/IrpTransmogrifier), verbatim and identical "
    "in the 1.2.14 release; the file's own comment says it is 'like "
    "DecodeIR'. Field layout, bit order and timings are confirmed by a "
    "published decode assertion in IrpTransmogrifierNGTest.java "
    "(testDecodeRecs80Junk, L333-L336), see tests/vectors/CITATIONS.md."
)
_SOURCE_RECS80_0068 = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 "
    "L2288-L2290 (bengtmartensson/IrpTransmogrifier), verbatim and identical "
    "in the 1.2.14 release. Same frame as RECS80 at a 180 us unit and 33.3 "
    "kHz with a 138 ms extent; its uei-executor is 0068. The frame layout is "
    "corroborated by the published RECS80 decode assertion cited for RECS80 "
    "(same fields and bit order, different clock), see "
    "tests/vectors/CITATIONS.md."
)
_BITS = 11


def _bits_msb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count - 1, -1, -1)]


def _make_encoder(name: str, unit_default: int, extent_us: int | None,
                  gap_us: int | None):
    """One frame shape; ``extent_us`` xor ``gap_us`` says how it ends."""

    def encode(
        *,
        device: int,
        subdevice: int | None,
        function: int,
        carrier_hz: int,
        unit_us: int | None = None,
        toggle: int = 0,
    ) -> IrSignal:
        unit = unit_default if unit_us is None else unit_us
        check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

        if subdevice is not None:
            raise EncodeError(
                f"{name} has no subdevice but one was given ({subdevice}); "
                "the address is the three-bit device alone"
            )
        if not 0 <= device <= 7:
            raise EncodeError(f"{name} device is {device}; D:3 holds 0-7")
        if not 0 <= function <= 0x3F:
            raise EncodeError(f"{name} function is {function}; F:6 holds 0-63")
        if toggle not in (0, 1):
            raise EncodeError(f"{name} toggle is {toggle!r}; T:1 is 0 or 1")

        bits = [1, toggle] + _bits_msb_first(device, 3) + _bits_msb_first(function, 6)
        assert len(bits) == _BITS
        frame: list[int] = []
        for bit in bits:
            frame += [unit, 47 * unit] if bit else [unit, 31 * unit]
        frame.append(unit)                         # the stop mark: the IRP's `1`

        if extent_us is not None:
            gap = extent_us - sum(frame)
            if gap < unit:
                raise EncodeError(
                    f"{name}: marks and spaces already total {sum(frame)} us "
                    f"against an extent of {extent_us} us, leaving {gap} us "
                    f"-- below one unit ({unit} us). Clamping would fabricate "
                    "a waveform that fails the extent it claims (D31)"
                )
        else:
            gap = gap_us
        frame.append(gap)
        return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))

    return encode


RECS80 = Protocol(
    name="RECS80",
    irp=_IRP_RECS80,
    irp_source=_SOURCE_RECS80,
    unit_us=158,
    nominal_carrier_hz=38_000,
    extent_us=None,
    bits=_BITS,
    encode=_make_encoder("RECS80", 158, None, 45_000),
)

RECS80_0068 = Protocol(
    name="RECS80-0068",
    irp=_IRP_RECS80_0068,
    irp_source=_SOURCE_RECS80_0068,
    unit_us=180,
    nominal_carrier_hz=33_300,
    extent_us=138_000,
    bits=_BITS,
    encode=_make_encoder("RECS80-0068", 180, 138_000, None),
)
