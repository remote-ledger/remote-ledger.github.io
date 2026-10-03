"""SwiftRemote DB hexcodes of the NEC family -> ledger ``(protocol, D, S, F)``.

The DB protocols ``NEC``, ``NEC2``, ``NECx1`` and ``NECx2`` all store the same
thing: eight hex digits, a 32-bit word that the app sends **MSB first**
(``lib/utils/ir.dart`` ``buildNecPatternFromStoredCodeMSBFirst``, L126-L143,
for ``NEC``; ``_to32Bits`` and the loop over its string at nec2.dart L60,
necx1.dart L82 and necx2.dart L49 for the rest).

NEC's wire order is the opposite: ``D:8,S:8,F:8,~F:8`` are each sent
least-significant-bit first. So the first bit on the wire is bit 7 of the
hex's first byte, and every byte of the hex is the *bit reversal* of the NEC
byte it carries::

    D = rev8(hex byte 0)   S = rev8(hex byte 1)
    F = rev8(hex byte 2)   ~F = rev8(hex byte 3)

That is the convention IrpTransmogrifier calls ``NEC-Shirriff-32``, after the
original Arduino IRremote library: ``{38.4k,msb,564}<1,-1|1,-3>(16,-8,
data:32,1,^108m)``, IrpProtocols.xml @c945e76 L1536. Three things back it,
and they are independent of one another:

* the IRP's own byte order, worked by hand in the tests;
* the ledger's own Samsung remote (remotes/samsung/BN59-01199F.json, D/S/F
  from IRDB): all eight of its keys invert to hexcodes the DB holds, 74 to
  128 times each under ``NECx2`` (``POWER`` = D 7, S 7, F 2 = ``E0E040BF``);
* the oracle: every representable code reproduces the app's waveform, which
  a map that forgot the reversal does not (tests/test_irblaster_hex_nec.py).

Bit reversal maps ``x ^ 0xFF`` to ``rev8(x) ^ 0xFF``, so "byte 4 is the
complement of byte 3" holds in the hex exactly when it holds on the wire; the
validity check needs no knowledge of the bit order, and the reversal does.

**What the ledger can hold.** All four ledger protocols spell the fourth byte
``~F:8``. A code whose fourth byte is not the complement of its third is none
of NEC1, NEC2, NECx1 or NECx2, and the only members of the IRP database that
can hold it are the relaxed ``-f16`` forms (``E:8``, independent), which are
not in the registry. Such a code raises ``ValueError``. The reason says which
pattern the fourth byte follows, using DecodeIR's own list of the variants
(hifi-remote.com/johnsfine/DecodeIR.html, "Variant IRstreams in NEC
protocols"):

* ``y1``  ``D,S,F,~F:7,F:1:7``        complement all of F except the MSB
* ``y2``  ``D,S,F,F:1,~F:7:1``        complement all of F except the LSB
* ``y3``  ``D,S,F,F:1,~F:6:1,F:1:7``  complement all of F except MSB and LSB
* ``rnc`` ``D,S,F,~F:4:4,~F:4``       complement F and reverse the nibbles
* anything else: byte 4 is independent of byte 3

The reasons are stable text with no hexcode in them, so an importer can group
by them. Naming a pattern is a classification of the bits, not a claim that
the code is that remote's: nothing here says whether such a code is a real
``-f16`` device or a mis-conversion.

**Which ledger protocol a DB protocol lands on is the app's waveform, not a
guess about the device.** The app sends a DB ``NEC`` code with a 9000/4500 us
lead-in (16 units), so it lands on ``NEC1`` whatever its D and S. 82 of those
codes (231 keys, 6 remotes) carry D = S = 7, a Samsung address that IRDB
files as NECx2 (8-unit lead-in) and the same DB holds as 147 ``E0E0xxxx``
codes under ``NECx2``. Mapping them to NECx2 would make the ledger compile a
signal the app does not send, so they are not re-labelled; they are reported
in NOTES/nec.md.

``NEC`` maps to ``NEC1`` (the app sends one frame and no ditto, which is
NEC1's intro). ``NEC2``, ``NECx1`` and ``NECx2`` map to themselves.
"""

from __future__ import annotations

from typing import Callable

#: Bit reversal of one byte. ``_REV[0xA2] == 0x45``.
_REV: tuple[int, ...] = tuple(int(f"{b:08b}"[::-1], 2) for b in range(256))

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

#: How byte 4 relates to byte 3, per DecodeIR's variant list. The mask forms
#: are the same patterns written as bit operations on the wire bytes.
_STYLE_TEXT = {
    "y1": "it complements bits 0-6 only (Yamaha style y1)",
    "y2": "it complements bits 1-7 only (Yamaha style y2)",
    "y3": "it complements bits 1-6 only (Yamaha style y3)",
    "rnc": "it is the complement with the nibbles swapped (style rnc)",
    "other": "it is unrelated to byte 3",
}


def _fourth_byte_style(f: int, e: int) -> str:
    """``ok`` when ``e == ~f``, else the first variant ``e`` follows."""
    if e == (~f) & 0xFF:
        return "ok"
    if e == (((~f) & 0x7F) | (f & 0x80)):
        return "y1"
    if e == (((~f) & 0xFE) | (f & 0x01)):
        return "y2"
    if e == (((~f) & 0x7E) | (f & 0x81)):
        return "y3"
    swapped = (~f) & 0xFF
    if e == (((swapped & 0x0F) << 4) | (swapped >> 4)):
        return "rnc"
    return "other"


def _make(ledger_name: str) -> Callable[[str], tuple[str, int, int | None, int]]:
    def from_db_hex(hexcode: str) -> tuple[str, int, int | None, int]:
        if len(hexcode) != 8 or not set(hexcode) <= _HEX_DIGITS:
            raise ValueError("the hexcode is not exactly 8 hex digits")
        d, s, f, e = (_REV[b] for b in bytes.fromhex(hexcode))
        style = _fourth_byte_style(f, e)
        if style != "ok":
            raise ValueError(
                f"byte 4 is not the complement of byte 3: {_STYLE_TEXT[style]}; "
                f"only the unregistered {ledger_name}-f16 can hold it"
            )
        return (ledger_name, d, s, f)

    from_db_hex.__name__ = f"from_db_hex_{ledger_name.lower()}"
    return from_db_hex


#: DB protocol name -> function(hexcode) -> (ledger protocol, D, S, F).
FROM_DB_HEX: dict[str, Callable[[str], tuple[str, int, int | None, int]]] = {
    "NEC": _make("NEC1"),
    "NEC2": _make("NEC2"),
    "NECx1": _make("NECx1"),
    "NECx2": _make("NECx2"),
}

#: ``protocol.minSends`` per DB protocol; a missing name means 1.
#: The app sends NECx2 as two back-to-back copies of the frame
#: (``lib/ir/protocols/necx2.dart`` L75-L76) and every other one of these as a
#: single frame. The ledger's NECx2 carries its frame once, in the repeat
#: slot, so the second copy is what ``minSends`` is for (DESIGN D3a).
MIN_SENDS: dict[str, int] = {"NECx2": 2}
