"""TDC-38 and TDC-56, the Danish TDC IPTV and the Italian ALICE Home TV box's remote: one frame at two carriers.

IRPs, verbatim from IrpTransmogrifier's protocol database::

    {38k,315,msb}<-1,1|1,-1>(1,-1,D:5,S:5,F:7,-89m)*[D:0..31,S:0..31,F:0..127]
    {56.3k,213,msb}<-1,1|1,-1>(1,-1,D:5,S:5,F:7,-89m)*[D:0..31,S:0..31,F:0..127]

The database says the two "have the same number of carrier cycles in each burst, which makes the duration of a burst
also differ" (12 cycles: 315 us at 38 kHz, 213 us at 56.3 kHz). A frame is, most significant bit first:

=========  ==================================================================
start      a 1-unit mark and a 1-unit space
D:5 S:5    a five-bit device and a five-bit subdevice
F:7        a seven-bit command
bit        biphase: a 1 is a mark then a space, a 0 a space then a mark
gap        89 ms after the last half-bit
=========  ==================================================================

Three things shape the encoder:

* **The gap is a gap, not an extent**, so ``extent_us`` is ``None`` (D3), and the sequence is closed by an explicit 89 ms
  space. The frame is 18 bits of two units and varies in length with the bits.
* **A frame that ends on a space lengthens it.** A last bit of 1 is a mark then a space and the gap follows that space:
  the two are one space of ``unit + 89,000`` us, which is how IrpTransmogrifier renders it. A zero ends on a mark and the
  gap is a space of its own. Adjacent half-bits of the same level likewise merge (the start's space and a leading 0's).
* **The frame has no intro.** ``*`` marks the whole frame as the repeat, so D6 rule 6 emits ``NNNN = 0000``.

The database notes that the remotes seen use "effectively a 6-bit OBC as bit 0 of F is always the complement of bit 1,
but there are other implementations which do not follow that pattern". The registered ``F`` is the IRP's seven bits as
they are, so both are carried. A form's ``device`` is D, ``subdevice`` is S and ``function`` is F.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP_38 = "{38k,315,msb}<-1,1|1,-1>(1,-1,D:5,S:5,F:7,-89m)*[D:0..31,S:0..31,F:0..127]"
_IRP_56 = "{56.3k,213,msb}<-1,1|1,-1>(1,-1,D:5,S:5,F:7,-89m)*[D:0..31,S:0..31,F:0..127]"
_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L{line} (bengtmartensson/IrpTransmogrifier), "
    "protocol `{name}`, verbatim, and identical in the 1.2.14 release (`list -i {name}`). Its documentation says the two "
    "variants differ in the carrier and so in the duration of a burst, with the same 12 carrier cycles in each. Hardware "
    "captures of remotes that send it are in IrpTransmogrifier's own test data (see tests/vectors/CITATIONS.md)."
)
_GAP_US = 89_000
_BITS = 17


def _bits_msb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count - 1, -1, -1)]


def _encode(
    name: str,
    default_unit_us: int,
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None,
) -> IrSignal:
    unit = default_unit_us if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)
    if subdevice is None:
        raise EncodeError(f"{name} requires an explicit subdevice (S:5)")
    if not 0 <= device <= 0x1F:
        raise EncodeError(f"{name} device is {device}; D:5 holds 0-31")
    if not 0 <= subdevice <= 0x1F:
        raise EncodeError(f"{name} subdevice is {subdevice}; S:5 holds 0-31")
    if not 0 <= function <= 0x7F:
        raise EncodeError(f"{name} function is {function}; F:7 holds 0-127")

    bits = _bits_msb_first(device, 5) + _bits_msb_first(subdevice, 5) + _bits_msb_first(function, 7)
    assert len(bits) == _BITS

    # Levels of the half-bits, 1 a mark: the start is mark, space; a 1 is mark, space; a 0 is space, mark.
    halves = [1, 0]
    for bit in bits:
        halves += [1, 0] if bit else [0, 1]

    durations: list[int] = []
    level, run = 1, 0
    for half in halves:
        if half == level:
            run += unit
        else:
            durations.append(run)
            level, run = half, unit
    durations.append(run)

    if len(durations) % 2:                       # ends on a mark: the gap is a space of its own
        durations.append(_GAP_US)
    else:                                        # ends on a space: the gap lengthens it
        durations[-1] += _GAP_US
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(durations))


def _encode_38(*, device: int, subdevice: int | None, function: int, carrier_hz: int,
               unit_us: int | None = None) -> IrSignal:
    return _encode("TDC-38", 315, device=device, subdevice=subdevice, function=function,
                   carrier_hz=carrier_hz, unit_us=unit_us)


def _encode_56(*, device: int, subdevice: int | None, function: int, carrier_hz: int,
               unit_us: int | None = None) -> IrSignal:
    return _encode("TDC-56", 213, device=device, subdevice=subdevice, function=function,
                   carrier_hz=carrier_hz, unit_us=unit_us)


TDC38 = Protocol(
    name="TDC-38",
    irp=_IRP_38,
    irp_source=_SOURCE.format(line=2821, name="TDC-38"),
    unit_us=315,
    nominal_carrier_hz=38_000,
    extent_us=None,
    bits=_BITS,
    encode=_encode_38,
)

TDC56 = Protocol(
    name="TDC-56",
    irp=_IRP_56,
    irp_source=_SOURCE.format(line=2835, name="TDC-56"),
    unit_us=213,
    nominal_carrier_hz=56_300,
    extent_us=None,
    bits=_BITS,
    encode=_encode_56,
)
