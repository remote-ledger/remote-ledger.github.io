"""DB hexcode -> ledger form, for the SwiftRemote protocols Pioneer, JVC, Sharp and Denon.

``FROM_DB_HEX[<DB protocol name>](hexcode)`` returns ``(ledger protocol,
device, subdevice, function)`` or raises ``ValueError`` with a one-line,
hexcode-free reason, so the importer can group its report by the message.
The functions are pure.

**What the hexcode means is the wire reading.** The database stores every
code as the bit string that goes on the wire, first bit the most significant
bit of the hexcode. ``FROM_DB_HEX`` reads it that way, so the ledger holds the
frame a real remote sends. SwiftRemote's own code
(``lib/utils/db_button_import.dart`` ``_deriveProtocolFieldTextFromHex`` and
``lib/ir/protocols/{pioneer,jvc,sharp,denon}.dart``) reads the same codes four
different ways, all wrong for that data (JVC and Pioneer send each byte least
significant bit first, Sharp unpacks a register layout the data does not have,
and Denon takes its thirteenth bit from the wrong place). The evidence is in
``NOTES/japan.md``.

``FROM_DB_HEX_APP`` (same keys, same signature) reads the codes the way the app
does today. It is not for import: the oracle tools use it to prove the ledger's
encoders reproduce what the app transmits
(``tools/irblaster_oracle_japan.py``), and the importer uses it to report the
codes on which the app and the ledger disagree
(``tools/irblaster_oracle_japan.py --reading wire`` counts them).

The ledger protocol is ``Pioneer-2Part``, ``JVC``, ``Sharp`` or ``Denon``.
``Pioneer-2Part`` packs two bytes into each of ``device`` and ``function``
(first frame in the high byte); see ``protocols/pioneer.py``.

``MIN_SENDS`` is 1 for all four: SwiftRemote sends each signal once per press,
the database holds no repeat count, and the encoders' own intro and repeat
carry what the protocol needs.
"""

from __future__ import annotations

import re
from typing import Callable

Mapped = tuple[str, int, "int | None", int]

#: Per DB protocol name; a name left out would mean 1.
MIN_SENDS: dict[str, int] = {"Pioneer": 1, "JVC": 1, "Sharp": 1, "Denon": 1}

_NOT_HEX = re.compile(r"[^0-9A-F]")


def _clean(hexcode: str) -> str:
    """The app's ``_cleanHex``: trim, upper-case, drop everything not 0-9A-F."""
    return _NOT_HEX.sub("", hexcode.strip().upper())


def _digits(hexcode: str, name: str, count: int, *, truncate: bool) -> int:
    """The code as an integer, applying the app's length rules.

    The app strips a hexcode to hex digits first. JVC and Denon declare a
    4-digit field, so a longer code keeps its *last* four digits; Sharp and
    Pioneer do not truncate. Anything the app cannot send is refused.
    """
    text = _clean(hexcode)
    if not text:
        raise ValueError(f"{name} hexcode is empty or has no hex digits")
    if truncate and len(text) > count:
        text = text[-count:]
    if len(text) != count:
        raise ValueError(f"{name} hexcode is not {count} hex digits")
    return int(text, 16)


def _rev(value: int, width: int) -> int:
    """Reverse the low ``width`` bits."""
    return int(format(value, f"0{width}b")[::-1], 2)


# --- what the app does (FROM_DB_HEX_APP) --------------------------------------------------------


def _pioneer_app(hexcode: str) -> Mapped:
    """Eight digits are address, command, secondary address, secondary command.

    The app sends frame(address, command) then frame(secondary address,
    secondary command), each byte LSB first with its complement beside it.
    """
    v = _digits(hexcode, "Pioneer", 8, truncate=False)
    a, c, sa, sc = (v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    return "Pioneer-2Part", (a << 8) | sa, None, (c << 8) | sc


def _jvc_app(hexcode: str) -> Mapped:
    """Two bytes, each sent LSB first: the first is D, the second F."""
    v = _digits(hexcode, "JVC", 4, truncate=True)
    return "JVC", v >> 8, None, v & 0xFF


def _sharp_app(hexcode: str) -> Mapped:
    """``packed = hex & 0x1FFF``; address is its bits 12-8, command bits 7-0.

    The top three bits of the code are discarded.
    """
    v = _digits(hexcode, "Sharp", 4, truncate=False) & 0x1FFF
    return "Sharp", (v >> 8) & 0x1F, None, v & 0xFF


def _denon_app(hexcode: str) -> Mapped:
    """A 13-bit field of the first three nibbles' twelve bits plus bit 0.

    Bits are sent in order, the first five being D and the next eight F, each
    LSB first. The thirteenth bit is the *last* bit of the fourth nibble
    (hex bit 0), so hex bits 3, 2 and 1 are discarded.
    """
    v = _digits(hexcode, "Denon", 4, truncate=True)
    sent = [(v >> (15 - i)) & 1 for i in range(12)] + [v & 1]
    device = sum(b << i for i, b in enumerate(sent[:5]))
    function = sum(b << i for i, b in enumerate(sent[5:13]))
    return "Denon", device, None, function


#: What SwiftRemote transmits today for each code. Not for import.
FROM_DB_HEX_APP: dict[str, Callable[[str], Mapped]] = {
    "Pioneer": _pioneer_app,
    "JVC": _jvc_app,
    "Sharp": _sharp_app,
    "Denon": _denon_app,
}


# --- what the data means (FROM_DB_HEX) ------------------------------------------------------
# Every code is the bit string that goes on the wire, first bit most
# significant. A byte or field that the protocol sends LSB first therefore
# appears here bit-reversed.


def _pioneer_wire(hexcode: str) -> Mapped:
    v = _digits(hexcode, "Pioneer", 8, truncate=False)
    a, c, sa, sc = (_rev((v >> s) & 0xFF, 8) for s in (24, 16, 8, 0))
    return "Pioneer-2Part", (a << 8) | sa, None, (c << 8) | sc


def _jvc_wire(hexcode: str) -> Mapped:
    v = _digits(hexcode, "JVC", 4, truncate=True)
    return "JVC", _rev(v >> 8, 8), None, _rev(v & 0xFF, 8)


def _sharp_wire(hexcode: str) -> Mapped:
    """Fifteen wire bits, then one pad bit: D (5), F (8), the trailer (2).

    The trailer is ``1:2`` in a normal frame and ``2:2`` in the complement
    frame, whose F is inverted; a code may be a recording of either half.
    """
    v = _digits(hexcode, "Sharp", 4, truncate=False)
    if v & 1:
        raise ValueError("Sharp hexcode has its pad bit set")
    trailer = (v >> 1) & 3            # wire bit 13 is the high bit of this pair
    device, function = _rev((v >> 11) & 0x1F, 5), _rev((v >> 3) & 0xFF, 8)
    if trailer == 0b10:               # wire bits 1, 0: the trailer 1:2
        return "Sharp", device, None, function
    if trailer == 0b01:               # wire bits 0, 1: the trailer 2:2
        return "Sharp", device, None, function ^ 0xFF
    raise ValueError("Sharp hexcode trailer bits are neither 1:2 nor 2:2")


def _denon_wire(hexcode: str) -> Mapped:
    """Thirteen wire bits, D (5) and F (8), then three bits the app ignores.

    Hex bits 2 and 1 are ``00`` or ``11`` in the database and make no
    difference to the frame the real remotes send (see NOTES/japan.md); bit 0
    is always 0.
    """
    v = _digits(hexcode, "Denon", 4, truncate=True)
    if v & 1:
        raise ValueError("Denon hexcode has its last bit set")
    return "Denon", _rev((v >> 11) & 0x1F, 5), None, _rev((v >> 3) & 0xFF, 8)


#: What the data means: the reading the importer uses.
FROM_DB_HEX: dict[str, Callable[[str], Mapped]] = {
    "Pioneer": _pioneer_wire,
    "JVC": _jvc_wire,
    "Sharp": _sharp_wire,
    "Denon": _denon_wire,
}
