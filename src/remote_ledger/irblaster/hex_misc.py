"""SwiftRemote DB hexcodes of the ``misc`` family -> ledger ``(protocol, D, S, F)``.

Five DB protocols: ``Samsung36``, ``Proton``, ``F12_relaxed``, ``RECS80`` and
``RECS80_L``. The app's encoder for each is in ``lib/ir/protocols/<name>.dart``
and every one of them reads its hexcode **most significant bit first, in wire
order**. IRP fields are least-significant-bit first (these four IRPs do not say
``msb``; RECS80's does), so a field the hex carries as a byte is the *bit
reversal* of the IRP's field value::

    Samsung36   A B C D -> D=rev8(A) S=rev8(B) E=rev4(C) F=rev8(D)
    F12_relaxed 12 bits -> D=rev3(top 3) S=bit 8 F=rev8(low 8)
    RECS80[_L]  9 bits  -> D=top 3, F=next 6 (msb-first, so no reversal)

That the reversal is right is shown two ways. The oracle comparison
(tools/irblaster_oracle_misc.py) reproduces the app's own waveform for every
code. And the DB's own contents agree with a real remote: 695 of its Samsung36
keys start ``04``, which is rev8(32), and IrpTransmogrifier's hardware capture
of a Samsung Blu-ray remote decodes as ``D=32,S=0,E=7`` -- ``E=7`` is nibble
``E`` -- with ``F=24`` for Up and 25 for Down, and the DB has ``0400E98``
labelled DOWN (``tests/vectors/CITATIONS.md``).

Three things this module decides, each repeated in NOTES/misc.md:

**Samsung36's fourth parameter.** The IRP has ``D,S,E,F``; a ledger form has
three numbers. ``function`` carries ``E:F`` as one 12-bit value,
``E * 256 + F`` (see ``protocols/samsung36.py``). 269 of the DB's 643 distinct
Samsung36 codes have ``E != 0``, so refusing them would lose 42 %.

**Proton's byte order is the app's, and the app's looks reversed.** The app
sends the hex's *low* byte first and its high byte second (``proton.dart``:
"sends last 8 bits, separator, then first 8 bits"). The IRP, and the one real
capture there is, send the device byte first. So this mapping gives
``D = rev8(low byte), F = rev8(high byte)`` -- the signal the app sends today,
which the oracle requires -- but the DB's own structure reads the other way:
in 169 of its 187 Proton remotes the high byte is constant across all keys and
in none is the low byte, which is what an *address* does. DB remote 18's keys
``0``, ``1``, ``8``, ``9`` are ``2800``, ``2880``, ``2810``, ``2890``, and
IrpTransmogrifier's capture of a Proton remote has ``D=20,F=0/1/8/9`` for the
same keys: hex ``28`` is rev8(20), and read high-byte-first it *is* the
capture's frame. ``proton_wire_order`` is that reading. It is **not** in
``FROM_DB_HEX``; switching to it is the owner's decision (NOTES/misc.md).

**RECS80's toggle.** ``T`` is not in the hexcode: the app flips it on every
press. The ledger carries ``T=0`` (D3b). The nine data bits are the top nine of
the hex's twelve; a code with any of the other three set cannot be represented,
because the app drops those bits silently and two different hexcodes would
compile to one signal. None of the DB's 555 RECS80 and RECS80_L codes has one.

A code the protocol cannot hold raises ``ValueError`` with fixed text, no
hexcode in it, so an importer can group by reason.
"""

from __future__ import annotations

from typing import Callable

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

Mapped = tuple[str, int, int | None, int]


def _rev(value: int, width: int) -> int:
    """Reverse the low ``width`` bits of ``value``."""
    return int(f"{value:0{width}b}"[::-1], 2)


def _hex_value(hexcode: str, name: str, lengths: tuple[int, ...], what: str) -> int:
    if len(hexcode) not in lengths or not set(hexcode) <= _HEX_DIGITS:
        raise ValueError(f"{name} hexcode is not {what} hex digits")
    return int(hexcode, 16)


def samsung36(hexcode: str) -> Mapped:
    """Seven digits: ``A(8) B(8) C(4) D(8)``, then the app appends ``~D``."""
    v = _hex_value(hexcode, "Samsung36", (7,), "exactly seven")
    a, b, c, d = v >> 20, (v >> 12) & 0xFF, (v >> 8) & 0xF, v & 0xFF
    # E is the nibble in front of F on the wire: pack it above F's eight bits.
    return ("Samsung36", _rev(a, 8), _rev(b, 8), (_rev(c, 4) << 8) | _rev(d, 8))


def proton(hexcode: str) -> Mapped:
    """Four digits, sent low byte first, the app's order. See the module doc."""
    v = _hex_value(hexcode, "Proton", (4,), "exactly four")
    return ("Proton", _rev(v & 0xFF, 8), None, _rev(v >> 8, 8))


def proton_wire_order(hexcode: str) -> Mapped:
    """Four digits read high byte first, as IrpTransmogrifier's capture is.

    Not registered in ``FROM_DB_HEX``: it is *not* what the app sends.
    """
    v = _hex_value(hexcode, "Proton", (4,), "exactly four")
    return ("Proton", _rev(v >> 8, 8), None, _rev(v & 0xFF, 8))


def f12_relaxed(hexcode: str) -> Mapped:
    """One to three digits, read as a number and left-padded to 12 bits."""
    v = _hex_value(hexcode, "F12_relaxed", (1, 2, 3), "one to three")
    return ("F12_relaxed", _rev(v >> 9, 3), (v >> 8) & 1, _rev(v & 0xFF, 8))


def _recs80(ledger_name: str, db_name: str) -> Callable[[str], Mapped]:
    def from_db_hex(hexcode: str) -> Mapped:
        v = _hex_value(hexcode, db_name, (3,), "exactly three")
        if v & 0b111:
            raise ValueError(
                f"{db_name} carries only the top nine of its twelve bits; "
                "the app drops the low three, so a code with one set is not "
                "a distinct signal"
            )
        return (ledger_name, v >> 9, None, (v >> 3) & 0x3F)

    from_db_hex.__name__ = f"from_db_hex_{db_name.lower()}"
    return from_db_hex


#: DB protocol name -> function(hexcode) -> (ledger protocol, D, S, F).
FROM_DB_HEX: dict[str, Callable[[str], Mapped]] = {
    "Samsung36": samsung36,
    "Proton": proton,
    "F12_relaxed": f12_relaxed,
    "RECS80": _recs80("RECS80", "RECS80"),
    # RECS80_L is IrpTransmogrifier's RECS80-0068, not a carrier variant of
    # RECS80: its unit is 180 us, not 158 us, and it pads to a 138 ms extent
    # where RECS80 ends in a plain 45 ms gap.
    "RECS80_L": _recs80("RECS80-0068", "RECS80_L"),
}

#: ``protocol.minSends`` per DB protocol; a missing name means 1. The app sends
#: one frame per tap for all five (``sendIR`` in lib/utils/ir.dart L403 builds
#: the pattern once; ``remote_view.dart`` L304 re-sends while a key is held).
#: Stated, though it is the default.
MIN_SENDS: dict[str, int] = {
    "Samsung36": 1,
    "Proton": 1,
    "F12_relaxed": 1,
    "RECS80": 1,
    "RECS80_L": 1,
}
