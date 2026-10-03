"""The SwiftRemote database's Sony hexcodes (``SONY12``, ``SONY15``, ``SONY20``).

A pure function from a DB hexcode to ``(protocol, device, subdevice,
function)``, plus the number of times the hardware wants the frame sent.

**Two readings of the same hexcode exist, and they disagree.** The app
(``lib/utils/db_button_import.dart`` ``_deriveProtocolFieldTextFromHex``)
treats the hex as a packed integer, ``cmd | addr << 7``, whose *low* bit is
sent first. The database's data is written the other way round: the frame's
bits in the order they go on the wire, the first one **most** significant,
right-padded with zeros to a whole number of hex digits (so a 15-bit frame
is four digits with the last bit zero). Sony's best-known code shows it:
TV power is device 1, function 21, which goes out as ``1010100 10000`` --
``A90``, which is also the hint text the app itself shows in its SONY12
field, and which the app reads as command 0x10 on address 0x15.

The evidence that the data means the second:

* frames whose parameters come from somewhere other than the database are
  in it spelled this way, and almost never the app's way
  (``tools/irblaster_oracle_sony.py --db`` reproduces this): all 38 keys of
  the ledger's hardware-verified ``remotes/sony/RMT-B118P.json`` (Sony20,
  device 26, subdevice 226) are in the database as this reading's hex and
  none as the app's packing; all 25 of Girr's Sony12 D=1 commands (app's
  packing: 7, about what chance gives in a space the database fills a fifth
  of); 26 of 30 Sony15 D=84 and 19 of 22 Sony20 D=26 S=42 functions from a
  Sony projector protocol manual (app's packing: 1 and 0);
* across the database, keys labelled with a standard Sony function (digits,
  volume, mute, power, channel) have that function under this reading for
  64-77% of them (of 665 distinct label/hex pairs), and under the app's for
  1-3%;
* every SONY15 hexcode (738 of 738) has its last bit zero, which is what
  right-padding a 15-bit frame to sixteen bits leaves.

``FROM_DB_HEX`` therefore implements the **database's** reading, which is the
one that puts the right frame in the ledger. ``FROM_DB_HEX_APP`` implements
the app's, and ``tools/irblaster_oracle_sony.py`` proves it reproduces what
the app transmits today for every distinct code, so the disagreement is
measured rather than assumed. Swapping the two is one line in the importer.

The ledger fields, for either reading (both give a 7-bit function and an
address of 5, 8 or 13 bits):

========  =========  ======================================  ==========
DB name   ledger     address -> device / subdevice            minSends
========  =========  ======================================  ==========
SONY12    Sony12     5 bits -> D, no subdevice                3
SONY15    Sony15     8 bits -> D, no subdevice                3
SONY20    Sony20     13 bits -> D = low 5, S = high 8         3
========  =========  ======================================  ==========

The carrier is 40 kHz for all three, which is both the registry's nominal
and what the app sends.
"""

from __future__ import annotations

from typing import Callable

_FUNCTION_BITS = 7

#: DB protocol name -> (ledger protocol, frame bits, hex digits in the DB,
#: address bits).
SHAPES = {
    "SONY12": ("Sony12", 12, 3, 5),
    "SONY15": ("Sony15", 15, 4, 8),
    "SONY20": ("Sony20", 20, 5, 13),
}

#: Sony's hardware wants a frame at least three times.
MIN_SENDS: dict[str, int] = {"SONY12": 3, "SONY15": 3, "SONY20": 3}

_Result = tuple[str, int, "int | None", int]


def _parse(hexcode: str) -> tuple[int, int]:
    """(value, digit count) of a hexcode; ValueError if it is not hex."""
    text = hexcode.strip()
    if not text or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ValueError("hexcode is not hexadecimal")
    return int(text, 16), len(text)


def _reverse(value: int, bits: int) -> int:
    out = 0
    for _ in range(bits):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def fields_from_packed(db_name: str, packed: int) -> _Result:
    """A packed ``function | address << 7`` integer as ledger fields."""
    name, _bits, _hex_digits, address_bits = SHAPES[db_name]
    function = packed & ((1 << _FUNCTION_BITS) - 1)
    address = packed >> _FUNCTION_BITS
    assert address < 1 << address_bits
    if db_name == "SONY20":
        # F:7,D:5,S:8 -- the 13 address bits go out device first.
        return name, address & 0x1F, address >> 5, function
    return name, address, None, function


def _from_db_hex(db_name: str, hexcode: str) -> _Result:
    """The database's reading: the frame in transmission order, first bit
    most significant, zero-padded on the right to whole digits."""
    _name, bits, digits, _address_bits = SHAPES[db_name]
    value, count = _parse(hexcode)
    if count != digits:
        raise ValueError(f"{db_name} hexcode is not {digits} digits")
    pad = digits * 4 - bits
    if value & ((1 << pad) - 1):
        raise ValueError(
            f"{db_name} hexcode has its pad bit set; a {bits}-bit frame "
            f"fills {bits} of its {digits * 4} bits"
        )
    return fields_from_packed(db_name, _reverse(value >> pad, bits))


def _from_app_hex(db_name: str, hexcode: str) -> _Result:
    """The app's reading: ``cmd | addr << 7`` with the low bit sent first.

    The app also masks the integer to the frame width without a word, so a
    hexcode wider than the frame transmits a *different* code from the one it
    spells. That is refused here rather than reproduced.
    """
    _name, bits, digits, _address_bits = SHAPES[db_name]
    value, count = _parse(hexcode)
    if count > digits:
        raise ValueError(f"{db_name} hexcode is longer than {digits} digits")
    if value >> bits:
        raise ValueError(
            f"{db_name} hexcode has bits above the {bits}-bit frame; the app "
            "masks them away and transmits a different code"
        )
    return fields_from_packed(db_name, value)


def _bind(reading: Callable[[str, str], _Result], db_name: str) -> Callable[[str], _Result]:
    def convert(hexcode: str) -> _Result:
        return reading(db_name, hexcode)

    convert.__name__ = f"{reading.__name__.lstrip('_')}_{db_name.lower()}"
    return convert


#: What the importer should use: the database's own reading of its codes.
FROM_DB_HEX: dict[str, Callable[[str], _Result]] = {
    name: _bind(_from_db_hex, name) for name in SHAPES
}

#: What the SwiftRemote app does with the same hexcodes today. Not for import.
FROM_DB_HEX_APP: dict[str, Callable[[str], _Result]] = {
    name: _bind(_from_app_hex, name) for name in SHAPES
}
