"""Flipper's parsed protocols as the ledger's (DESIGN section 96, D354).

Every rule below is read from the firmware's decoders (``lib/infrared/encoder_decoder/*/infrared_decoder_*.c`` of
``flipperdevices/flipperzero-firmware``) and checked against the firmware's own unit-test captures
(``tests/test_flipper_mapping.py`` compiles each mapped code and compares it with the captured frame). An ``address`` or a
``command`` of a ``.ir`` file is four bytes, least significant first (:mod:`.ir`); a protocol that uses fewer bits leaves the
upper bytes 0 and the importer stops at one that does not.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ir import Entry


@dataclass(frozen=True)
class Signal:
    """A parsed entry as a ledger protocol and its parameters."""

    protocol: str
    device: int
    subdevice: int | None
    function: int
    #: the rule, as the citation says it
    how: str


def _word(parts: tuple[int, ...], bytes_used: int) -> int | None:
    """The little-endian value of the first ``bytes_used`` bytes, or None when a higher byte is not 0."""
    if any(parts[bytes_used:]):
        return None
    return sum(b << (8 * i) for i, b in enumerate(parts[:bytes_used]))


def _vendor_parity(low: int, high: int) -> int:
    x = low ^ high
    return (x & 0xF) ^ (x >> 4)


def _kaseikyo(address: int, command: int) -> Signal | str:
    genre1, genre2 = address >> 4 & 0xF, address & 0xF
    vendor = address >> 8 & 0xFFFF
    ident = address >> 24 & 0xFF
    if ident > 3:
        return f"the ID {ident} is more than two bits"
    if command > 0x3FF:
        return f"the command {command:#x} is more than ten bits"
    low, high = vendor & 0xFF, vendor >> 8
    # the frame's bytes in the order they are sent: the vendor's two, then GENRE1 and the vendor parity, GENRE2 and the data, the data and ID
    w2 = genre1 << 4 | _vendor_parity(low, high)
    w3 = (command & 0xF) << 4 | genre2
    w4 = command >> 4 | ident << 6
    frame = f"vendor {vendor:#06x}, GENRE1 {genre1}, GENRE2 {genre2}, ID {ident}, data {command:#x}"
    if (low, high) == (0x02, 0x20):
        return Signal("Panasonic", w2, w3, w4, f"{frame}: the bytes {w2:#04x} {w3:#04x} {w4:#04x} of a Panasonic frame")
    if (low, high) == (0x03, 0x01):
        return Signal("JVC-48", w2, w3, w4, f"{frame}: the bytes {w2:#04x} {w3:#04x} {w4:#04x} of a JVC-48 frame")
    if (low, high) == (0x54, 0x32):
        return Signal("Denon-K", genre1, genre2, command | ident << 10, f"{frame}: GENRE1, GENRE2 and the twelve bits of the data and the ID")
    return f"the Kaseikyo vendor {vendor:#06x} is not a registered protocol"


def signal_of(entry: Entry) -> Signal | str:
    """The ledger's reading of a parsed entry, or why it has none."""
    name = entry.protocol
    if name == "NEC":
        a, c = _word(entry.address, 1), _word(entry.command, 1)
        if a is None or c is None:
            return "NEC takes an 8-bit address and an 8-bit command and a higher byte is not 0"
        return Signal("NEC1", a, 0xFF - a, c, f"address {a:#04x}, command {c:#04x}: the frame {a:#04x} {0xFF - a:#04x} {c:#04x} {0xFF - c:#04x}")
    if name == "NECext":
        a, c = _word(entry.address, 2), _word(entry.command, 2)
        if a is None or c is None:
            return "NECext takes a 16-bit address and a 16-bit command and a higher byte is not 0"
        a0, a1, c0, c1 = a & 0xFF, a >> 8, c & 0xFF, c >> 8
        frame = f"the frame {a0:#04x} {a1:#04x} {c0:#04x} {c1:#04x}"
        if c1 == 0xFF - c0:
            return Signal("NEC1", a0, a1, c0, f"address {a:#06x}, command {c:#06x}: {frame}")
        return Signal("NEC1-f16", a0, a1, c0 << 8 | c1, f"address {a:#06x}, command {c:#06x}: {frame}, whose fourth byte is not the complement of the third")
    if name == "Samsung32":
        a, c = _word(entry.address, 1), _word(entry.command, 1)
        if a is None or c is None:
            return "Samsung32 takes an 8-bit address and an 8-bit command and a higher byte is not 0"
        return Signal("NECx2", a, a, c, f"address {a:#04x}, command {c:#04x}: the frame {a:#04x} {a:#04x} {c:#04x} {0xFF - c:#04x}")
    if name in ("SIRC", "SIRC15", "SIRC20"):
        bits = {"SIRC": 5, "SIRC15": 8, "SIRC20": 13}[name]
        a, c = _word(entry.address, 2), _word(entry.command, 1)
        if a is None or c is None or a >> bits or c > 0x7F:
            return f"{name} takes a {bits}-bit address and a 7-bit command and a bit above them is not 0"
        if name == "SIRC20":
            return Signal("Sony20", a & 0x1F, a >> 5, c, f"address {a:#06x}, command {c:#04x}: device {a & 0x1F} and subdevice {a >> 5} of the 13 address bits")
        return Signal("Sony12" if name == "SIRC" else "Sony15", a, None, c, f"address {a:#04x}, command {c:#04x}")
    if name in ("RC5", "RC5X"):
        a, c = _word(entry.address, 1), _word(entry.command, 1)
        if a is None or c is None or a > 0x1F or c > 0x3F:
            return f"{name} takes a 5-bit address and a 6-bit command and a bit above them is not 0"
        if name == "RC5X":
            return Signal("RC5", a, None, c + 64, f"address {a:#04x}, command {c:#04x}: the second start bit is 0, which is command bit 6")
        return Signal("RC5", a, None, c, f"address {a:#04x}, command {c:#04x}")
    if name == "RC6":
        a, c = _word(entry.address, 1), _word(entry.command, 1)
        if a is None or c is None:
            return "RC6 takes an 8-bit address and an 8-bit command and a higher byte is not 0"
        return Signal("RC6", a, None, c, f"address {a:#04x}, command {c:#04x}, mode 0")
    if name == "Pioneer":
        a, c = _word(entry.address, 1), _word(entry.command, 1)
        if a is None or c is None:
            return "Pioneer takes an 8-bit address and an 8-bit command and a higher byte is not 0"
        return Signal("Pioneer-2Part", a << 8 | a, None, c << 8 | c, f"address {a:#04x}, command {c:#04x}: one frame {a:#04x} {0xFF - a:#04x} {c:#04x} {0xFF - c:#04x}, sent twice")
    if name == "Kaseikyo":
        a, c = _word(entry.address, 4), _word(entry.command, 2)
        if a is None or c is None:
            return "Kaseikyo takes a 32-bit address and a 10-bit command and a higher byte is not 0"
        return _kaseikyo(a, c)
    return f"the protocol {name} is not read"
