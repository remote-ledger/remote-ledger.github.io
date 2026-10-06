"""DB hexcode -> ledger parameters for REC80, RCC2026 and RCC0082.

These three DB protocol names appear in no published protocol list, and the
app that defined them says so (iodn/android-ir-blaster ``report-source.md``:
"No authoritative public protocol definition was found"). They are *not*
protocols of their own. Each waveform is a known one, found by matching the
timings and bit counts against IrpTransmogrifier's database; the full
argument is in tests/vectors/CITATIONS.md and tests/test_irblaster_unknown.py.

=========  ==============================================================
RCC0082    ``Blaupunkt`` (IRP alternate name Motorola): 30.3 kHz, a
           ``1,-5`` sync, a ten-bit biphase frame, a closing sync
RCC2026    ``Aiwa``: 38.1 kHz, 550 us unit, 42 bits ``D:8,S:5,~D:8,~S:5,
           F:8,~F:8``, and a ``16,-8,1,-165`` tail. The DB hex is the 42
           bits *left-aligned* in 44, which is what upstream's app reads
           and what SwiftRemote's stale copy does not (see ``rcc2026`` below)
REC80      a bag of six Kaseikyo-family frames, told apart by their first
           two bytes: Panasonic, JVC-48, Fujitsu, Teac-K, Denon-K, SharpDVD
=========  ==============================================================

``FROM_DB_HEX`` maps a DB protocol name to a pure function from the DB's
hexcode to ``(ledger_protocol, device, subdevice_or_None, function)``. A code
the registry cannot hold raises ``ValueError`` with one fixed line (no
hexcode in it, so an importer can group by the text).

``MIN_SENDS`` is how many sends the app makes per press, in the ledger's
``min_repeat + 1`` sense (DESIGN D38); a name left out means 1.

``FROM_DB_HEX`` is the reading the evidence supports, which the importer uses.
``FROM_DB_HEX_APP`` has the same keys and reads each code the way SwiftRemote
does today. It differs from ``FROM_DB_HEX`` only for RCC2026, where
SwiftRemote's copy of the encoder is stale; REC80 and RCC0082 are the same
function in both. ``FROM_DB_HEX`` follows upstream's fixed encoder (1,210 valid
Aiwa frames of 1,231), not that copy; see ``rcc2026`` below.
"""

from __future__ import annotations

from typing import Callable

_Mapped = tuple[str, int, "int | None", int]


def _digits(hexcode: str, count: int, name: str) -> int:
    """The app's own check (``_validateHex*``): exactly ``count`` hex digits."""
    text = hexcode.strip()
    if len(text) != count or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ValueError(f"{name} hexcode is not {count} hexadecimal digits")
    return int(text, 16)


def _lsb_first(bits: str) -> int:
    """A run of transmitted bits (first sent first) as an LSB-first field."""
    return int(bits[::-1], 2)


# --- REC80 -----------------------------------------------------------------------


def rec80(hexcode: str) -> _Mapped:
    n = _digits(hexcode, 12, "REC80")
    # The app sends the 48 bits of the hex number most-significant first, so
    # the hex is the wire order; each byte of an IRP frame is LSB-first.
    bits = f"{n:048b}"
    b = [_lsb_first(bits[i:i + 8]) for i in range(0, 48, 8)]
    vendor = (b[0], b[1])

    if vendor == (2, 32):
        if b[5] != b[2] ^ b[3] ^ b[4]:
            raise ValueError("REC80 Panasonic frame: check byte is not D^S^F")
        return ("Panasonic", b[2], b[3], b[4])

    if vendor == (3, 1):
        if b[5] != b[2] ^ b[3] ^ b[4]:
            raise ValueError("REC80 JVC-48 frame: check byte is not D^S^F")
        return ("JVC-48", b[2], b[3], b[4])

    if vendor == (20, 99):
        if b[2] & 15:
            raise ValueError("REC80 Fujitsu frame: the fixed 0:4 nibble is not 0")
        if b[2] >> 4:
            raise ValueError("REC80 Fujitsu frame: E is not 0, which a ledger form cannot name")
        return ("Fujitsu", b[3], b[4], b[5])

    if vendor == (67, 83):
        device, x = b[2] >> 4, b[2] & 15
        if x != 1:
            raise ValueError("REC80 Teac-K frame: X is not 1, which a ledger form cannot name")
        t = device + (b[3] & 15) + (b[3] >> 4) + (b[4] & 15) + (b[4] >> 4)
        if b[5] != t:
            raise ValueError("REC80 Teac-K frame: check byte T is not D+S+F nibble sum")
        return ("Teac-K", device, b[3], b[4])

    if vendor == (84, 50):
        if b[2] & 15:
            raise ValueError("REC80 Denon-K frame: the fixed 0:4 nibble is not 0")
        device, sub = b[2] >> 4, b[3] & 15
        function = (b[3] >> 4) | (b[4] << 4)
        check = ((device * 16) ^ sub ^ (function * 16) ^ ((function >> 4) & 0xFF)) & 0xFF
        if b[5] != check:
            raise ValueError("REC80 Denon-K frame: check byte does not match")
        return ("Denon-K", device, sub, function)

    if vendor == (170, 90):
        if b[2] & 15 != 15:
            raise ValueError("REC80 SharpDVD frame: the fixed 15:4 nibble is not 15")
        device, e, c = b[2] >> 4, b[5] & 15, b[5] >> 4
        if e != 1:
            raise ValueError("REC80 SharpDVD frame: E is not 1, which a ledger form cannot name")
        calc = device ^ (b[3] & 15) ^ (b[3] >> 4) ^ (b[4] & 15) ^ (b[4] >> 4) ^ e
        if c != calc:
            raise ValueError("REC80 SharpDVD frame: check nibble C does not match")
        return ("SharpDVD", device, b[3], b[4])

    raise ValueError("REC80 frame: first two bytes are not a Kaseikyo-family member the registry holds")


# --- RCC2026 ---------------------------------------------------------------------


def _rcc2026(hexcode: str, left_aligned: bool) -> _Mapped:
    n = _digits(hexcode, 11, "RCC2026")
    bits44 = f"{n:044b}"
    # Left-aligned: 42 wire bits, then two padding bits. SwiftRemote's copy of
    # the encoder takes the LAST 42 instead: upstream iodn/android-ir-blaster
    # fixed that in 3bb60e3178. Read left-aligned, 1,210 of the 1,231 DB codes
    # are valid Aiwa frames; read the stale way, 71 are.
    if left_aligned:
        bits, padding = bits44[:42], bits44[42:]
        if padding != "00":
            raise ValueError("RCC2026 hexcode: the two padding bits are not 0")
    else:
        # What SwiftRemote sends today. The two bits it drops are not padding
        # (934 of the 1,231 codes have them set), so nothing is checked there.
        bits = bits44[2:]
    d = _lsb_first(bits[0:8])
    s = _lsb_first(bits[8:13])
    nd = _lsb_first(bits[13:21])
    ns = _lsb_first(bits[21:26])
    f = _lsb_first(bits[26:34])
    nf = _lsb_first(bits[34:42])
    wrong = [
        name
        for name, ok in (
            ("~D", nd == (~d & 0xFF)),
            ("~S", ns == (~s & 0x1F)),
            ("~F", nf == (~f & 0xFF)),
        )
        if not ok
    ]
    if wrong:
        raise ValueError(
            "RCC2026 bits are not an Aiwa frame: " + ", ".join(wrong) + " is not the complement"
        )
    return ("Aiwa", d, s, f)


def rcc2026(hexcode: str) -> _Mapped:
    """The reading the evidence supports: the first 42 bits."""
    return _rcc2026(hexcode, left_aligned=True)


def rcc2026_as_swiftremote_sends_it(hexcode: str) -> _Mapped:
    """The reading SwiftRemote's stale encoder applies: the last 42 bits."""
    return _rcc2026(hexcode, left_aligned=False)


# --- RCC0082 ---------------------------------------------------------------------


def rcc0082(hexcode: str) -> _Mapped:
    n = _digits(hexcode, 3, "RCC0082")
    n0, n1, n2 = (n >> 8) & 15, (n >> 4) & 15, n & 15
    # The app builds "0" + (n0 last 3 bits) + (n1) + (n2 first 2 bits): nine
    # payload bits, so the top bit of n0 and the low two bits of n2 are unused.
    if n0 & 8 or n2 & 3:
        raise ValueError("RCC0082 hexcode: the unused bits are not 0")
    payload = f"{n0 & 7:03b}{n1:04b}{n2 >> 2:02b}"
    # The app's transition coder emits a hex 1 as the half-bit order that
    # IrpTransmogrifier writes as a 0 (space,mark); the oracle comparison in
    # tools/irblaster_oracle_unknown.py is what pins this polarity.
    wire = [1 - int(c) for c in payload]
    function = sum(bit << i for i, bit in enumerate(wire[0:6]))
    device = sum(bit << i for i, bit in enumerate(wire[6:9]))
    return ("Blaupunkt", device, None, function)


#: The reading the evidence supports: what the importer uses.
FROM_DB_HEX: dict[str, Callable[[str], _Mapped]] = {
    "REC80": rec80,
    "RCC2026": rcc2026,
    "RCC0082": rcc0082,
}

#: What SwiftRemote transmits today for each code. Not for import. The one
#: place the DB's hexcodes mean something other than what SwiftRemote does with
#: them is RCC2026, where its copy of the encoder is stale; REC80 and RCC0082
#: are the same functions as in ``FROM_DB_HEX``. Only 71 of the 1,231 RCC2026
#: codes are an Aiwa frame under the stale reading, so the rest raise.
FROM_DB_HEX_APP: dict[str, Callable[[str], _Mapped]] = {
    **FROM_DB_HEX,
    "RCC2026": rcc2026_as_swiftremote_sends_it,
}

#: The app sends RCC2026's frame and then its tail once per press, which is
#: one intro plus one repeat: ``min_repeat + 1`` = 2. Everything else is one
#: frame.
MIN_SENDS: dict[str, int] = {"RCC2026": 2}
