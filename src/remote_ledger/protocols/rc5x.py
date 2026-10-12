"""Philips RC-5x, the twenty-bit extension of RC-5.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {36k,msb,889}<1,-1|-1,1>((1,~S:1:6,T:1,D:5,-4,S:6,F:6,^114m)*,T=1-T)[D:0..31,S:0..127,F:0..63,T@:0..1=0]

The database's own words: "In Philips' (the creator of the protocol) terminology, D is called
System, S is called Command, and F is called Data." The ledger's ``device`` is D, ``subdevice`` is
S (required: it is the seven-bit command) and ``function`` is F (the six-bit data, which Marantz's
charts call the *extension*, D126). A frame is twenty biphase bits, most significant bit first,
with a silence between the eighth and the ninth:

=========  ===========================================================
S1         always 1
S2         ``~S:1:6``, the complement of bit 6 of S (as RC-5's ``~F:1:6``)
T          the toggle bit
D:5        the system address
``-4``     four units of silence
S:6        the low six bits of S
F:6        the data
=========  ===========================================================

It is **not** RC-5 (``RC5``, a fourteen-bit frame with no gap and no data) and the two do not
decode each other; ``RC5`` says so in its own docstring. Everything about the frame that RC-5
has is the same: the unit is 889 us, the carrier 36 kHz, a frame opens on a lone mark because
S1's idle half-bit is dropped, the extent ``^114m`` is measured from that first mark, the frame
has no intro, and the toggle is not in the remote file (D3b): ``T`` flips on every press, a file
cannot say which state it has, so everything compiled here has ``T=0``, the IRP's own default.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{36k,msb,889}<1,-1|-1,1>((1,~S:1:6,T:1,D:5,-4,S:6,F:6,^114m)*,T=1-T)"
    "[D:0..31,S:0..127,F:0..63,T@:0..1=0]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2133-L2141 "
    "(bengtmartensson/IrpTransmogrifier), verbatim, and identical in the 1.2.14 release. Its "
    "documentation names Philips as the creator. The field layout is confirmed by a hardware "
    "capture of a system-16 remote in its own test data (RC5x.ict) and by the Marantz charts' "
    "own Pronto hex (see tests/vectors/CITATIONS.md)."
)
_UNIT_US = 889
_EXTENT_US = 114_000
_GAP_UNITS = 4
_BITS = 20


def _bits_msb_first(value: int, count: int) -> list[int]:
    return [(value >> i) & 1 for i in range(count - 1, -1, -1)]


def _biphase(bits: list[int]) -> list[int]:
    # A 1 is a space then a mark, a 0 a mark then a space.
    halves: list[int] = []
    for bit in bits:
        halves += [0, 1] if bit else [1, 0]
    return halves


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
    toggle: int = 0,
) -> IrSignal:
    unit = _UNIT_US if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)

    if subdevice is None:
        raise EncodeError(
            "RC5x needs a subdevice: it is S, the seven-bit command (D, the system, and F, "
            "the six-bit data, are the other two fields)"
        )
    if not 0 <= device <= 0x1F:
        raise EncodeError(f"RC5x device is {device}; D:5 holds 0-31")
    if not 0 <= subdevice <= 0x7F:
        raise EncodeError(f"RC5x subdevice is {subdevice}; S is 0-127")
    if not 0 <= function <= 0x3F:
        raise EncodeError(f"RC5x function is {function}; F:6 holds 0-63")
    if toggle not in (0, 1):
        raise EncodeError(f"RC5x toggle is {toggle!r}; T:1 is 0 or 1")

    first = [1, 1 - ((subdevice >> 6) & 1), toggle] + _bits_msb_first(device, 5)
    second = _bits_msb_first(subdevice & 0x3F, 6) + _bits_msb_first(function, 6)
    assert len(first) + len(second) == _BITS

    halves = _biphase(first) + [0] * _GAP_UNITS + _biphase(second)

    # S1 is always a 1, so the frame always begins with an idle half-bit.
    # Drop it; what is left starts on a mark.
    assert halves[0] == 0
    halves = halves[1:]

    frame: list[int] = []
    level = 1
    run = 0
    for half in halves:
        if half == level:
            run += unit
        else:
            frame.append(run)
            level = half
            run = unit
    frame.append(run)

    # A frame ending on a mark gets a gap appended; one ending on a space has that space
    # lengthened instead (the Sony rule), since a Pronto sequence is mark/space pairs and may
    # not end on a bare mark.
    ends_on_space = len(frame) % 2 == 0
    head = sum(frame[:-1]) if ends_on_space else sum(frame)
    gap = _EXTENT_US - head
    if gap < unit:
        raise EncodeError(
            f"RC5x: marks and spaces already total {head} us against an extent of "
            f"{_EXTENT_US} us, leaving {gap} us -- below one unit ({unit} us). Clamping would "
            "fabricate a waveform that fails the extent it claims (D31)"
        )
    if ends_on_space:
        frame[-1] = gap
    else:
        frame.append(gap)

    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(frame))


RC5X = Protocol(
    name="RC5x",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=36_000,
    extent_us=_EXTENT_US,
    bits=_BITS,
    encode=encode,
)
