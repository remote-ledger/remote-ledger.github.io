"""XMP-1: the 38 kHz nibble code of Comcast's, Dish's, Dreambox's and other set-top boxes' remotes.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {38k,136,msb}<210u,-760u|210u,-896u|210u,-1032u|210u,-1168u|210u,-1304u|210u,-1449u|210u,-1576u|210u,-1712u|210u,-1848u|210u,-1984u|210u,-2120u|210u,-2256u|210u,-2392u|210u,-2528u|210u,-2664u|210u,-2800u>([T=0][T=8],S:4:4,C1:4,S:4,15:4,OEM:8,D:8,210u,-13.8m,S:4:4,C2:4,T:4,S:4,(F*256):16,210u,-80.4m)+{ C1=-(S+S::4+15+OEM+OEM::4+D+D::4),C2=-(S + S::4 + T + 256*F + 16*F + F + F::4) }[D:0..255, S:0..255, F:0..255, OEM:0..255=68]

Every symbol is a **nibble**: a 210 us mark and a space of 760 us plus 136 us times the nibble's value (the sixth, 1,449 us,
is the database's own figure, 9 us above the line). A transmission is two **subframes**, each of eight nibbles, a 210 us
mark and a gap:

=========  =====================================================================================
subframe 1 S's high nibble, ``C1``, S's low nibble, 15, OEM's two nibbles, D's two nibbles, a 210 us mark and 13.8 ms
subframe 2 S's high nibble, ``C2``, the toggle nibble ``T``, S's low nibble, F's two nibbles and two zero nibbles,
           a 210 us mark and 80.4 ms
=========  =====================================================================================

``C1`` and ``C2`` are nibble checksums: the negated sum of the nibbles, modulo 16. The whole is the **intro** with the
toggle nibble 0, and the **repeat** (sent while a key is held) the same with it 8. The ``+`` of the IRP is that.

A ledger form's ``device`` is D, ``subdevice`` is S (required) and ``function`` is F, eight bits: the IRP's own ``F*256``
puts it in the high byte of the 16-bit command, which is XMP-1's and not XMP-2's (``F:16``, which IrpTransmogrifier also
has). **Two things are not in a form**: ``OEM``, which is the IRP's own 68 (the code of the remotes in the capture
evidence) and which an upgrade that sets another value cannot be written as; and the **final frame** that some remotes send
when a key is released (IrpTransmogrifier's ``XMPff-1``, the same with a third transmission with the toggle nibble 9 and no
long gap), which ``IrSignal`` has no place for (its ``ending`` is reserved, D1) and which is not written.

**Every duration is in microseconds**, so a file's ``unitUs`` has nothing to scale and is refused. The gaps are gaps, not an
extent (``extent_us`` is ``None``, D3).
"""

from __future__ import annotations

from ..errors import EncodeError
from ..signal import IrSignal
from .base import Protocol

_IRP = (
    "{38k,136,msb}<210u,-760u|210u,-896u|210u,-1032u|210u,-1168u|210u,-1304u|210u,-1449u|210u,-1576u|210u,-1712u|"
    "210u,-1848u|210u,-1984u|210u,-2120u|210u,-2256u|210u,-2392u|210u,-2528u|210u,-2664u|210u,-2800u>"
    "([T=0][T=8],S:4:4,C1:4,S:4,15:4,OEM:8,D:8,210u,-13.8m,S:4:4,C2:4,T:4,S:4,(F*256):16,210u,-80.4m)+"
    "{ C1=-(S+S::4+15+OEM+OEM::4+D+D::4),C2=-(S + S::4 + T + 256*F + 16*F + F + F::4) }"
    "[D:0..255, S:0..255, F:0..255, OEM:0..255=68]"
)
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L3023 (bengtmartensson/IrpTransmogrifier), protocol "
    "`XMP-1`, verbatim, and identical in the 1.2.14 release (`list -i XMP-1`). Its documentation points at the "
    "hifi-remote.com wiki's XMP page. Hardware captures of a remote that sends it (with the final frame, as `XMPff-1`) are in "
    "IrpTransmogrifier's own test data (see tests/vectors/CITATIONS.md)."
)
_MARK_US = 210
_SPACES_US = (760, 896, 1032, 1168, 1304, 1449, 1576, 1712, 1848, 1984, 2120, 2256, 2392, 2528, 2664, 2800)
_GAPS_US = (13_800, 80_400)
_OEM = 68
#: Bits in a transmission: sixteen nibbles of four.
_BITS = 64


def _nibble(value: int) -> list[int]:
    return [_MARK_US, _SPACES_US[value]]


def _frame(device: int, subdevice: int, function: int, toggle: int) -> tuple[int, ...]:
    s_high, s_low = subdevice >> 4, subdevice & 0xF
    c1 = -(subdevice + s_high + 15 + _OEM + (_OEM >> 4) + device + (device >> 4)) & 0xF
    c2 = -(subdevice + s_high + toggle + 256 * function + 16 * function + function + (function >> 4)) & 0xF
    first = [s_high, c1, s_low, 15, _OEM >> 4, _OEM & 0xF, device >> 4, device & 0xF]
    second = [s_high, c2, toggle, s_low, function >> 4, function & 0xF, 0, 0]
    out: list[int] = []
    for nibbles, gap in ((first, _GAPS_US[0]), (second, _GAPS_US[1])):
        for value in nibbles:
            out += _nibble(value)
        out += [_MARK_US, gap]
    return tuple(out)


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    if unit_us is not None:
        raise EncodeError("XMP-1 has no unit to scale: every duration is a fixed number of microseconds")
    if subdevice is None:
        raise EncodeError("XMP-1 requires an explicit subdevice (S:8)")
    if not 0 <= device <= 0xFF:
        raise EncodeError(f"XMP-1 device is {device}; D:8 holds 0-255")
    if not 0 <= subdevice <= 0xFF:
        raise EncodeError(f"XMP-1 subdevice is {subdevice}; S:8 holds 0-255")
    if not 0 <= function <= 0xFF:
        raise EncodeError(f"XMP-1 function is {function}; F:8 holds 0-255")
    return IrSignal(carrier_hz=carrier_hz, intro=_frame(device, subdevice, function, 0),
                    repeat=_frame(device, subdevice, function, 8))


XMP1 = Protocol(
    name="XMP-1",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=136,
    nominal_carrier_hz=38_000,
    extent_us=None,
    bits=_BITS,
    encode=encode,
)
