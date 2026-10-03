"""SwiftRemote database hexcodes for the Philips-family protocols, as ledger forms.

A database key is ``(label, hexcode, protocol)``. What a hexcode *means* is
whatever SwiftRemote's own code does with it, not what a specification says,
so every function here is a transcription of the Dart it names, and
``tools/irblaster_oracle_philips.py`` checks each against the signal the app
actually produces for every distinct database code.

Each function takes the hexcode and returns ``(ledger_protocol_name, device,
subdevice, function)``. A code the ledger cannot represent raises
``ValueError`` with a one-line reason that is the same text for every code in
its class (the importer groups by it, so the hexcode is never in the message).

``FROM_DB_HEX`` is keyed by the protocol name exactly as the database spells
it, and is what the importer uses: the wire reading. For RC5, RC6 and RCA_38
that is what the app does too. For Thomson7 it is not (the app builds a
different frame from the same twelve bits, and the database means the one the
real remote sends), so ``FROM_DB_HEX_APP`` (same keys, same signature) holds
the app's reading. The oracle tool compares the ledger's encoders with what the
app transmits under ``FROM_DB_HEX_APP``. ``MIN_SENDS`` omits a name to mean 1.

What none of these can carry is the toggle bit (DESIGN D3b). The database
stores no toggle, and SwiftRemote alternates it itself on every press, so a
compiled form carries the ledger's own ``T=0``.
"""

from __future__ import annotations

from typing import Callable

_HEX = frozenset("0123456789abcdefABCDEF")


def _digits(hexcode: str, protocol: str, max_digits: int) -> int:
    """The value of ``hexcode``, refusing what the app would silently trim.

    The app strips anything that is not a hex digit and ``int.parse``s the
    rest. Every code in the database is a clean run of digits, so a stray
    character is refused rather than repaired, and so are more digits than the
    app keeps -- it drops the leading ones without a word.
    """
    if not hexcode or not set(hexcode) <= _HEX:
        raise ValueError(f"{protocol}: hexcode is not a run of hex digits")
    if len(hexcode) > max_digits:
        raise ValueError(
            f"{protocol}: hexcode is longer than the {max_digits} digits the "
            "app keeps, and it drops the leading ones"
        )
    return int(hexcode, 16)


def _reverse(value: int, bits: int) -> int:
    return int(f"{value:0{bits}b}"[::-1], 2)


def rc5(hexcode: str) -> tuple[str, int, int | None, int]:
    """``_deriveProtocolFieldTextFromHex``, ``protocolId == rc5``
    (lib/utils/db_button_import.dart L226-235).

    Twelve bits: the second start bit, five of address, six of command. The
    app turns the start bit back into command bit 6 by inverting it
    (``fieldBit == 0`` adds 0x40), which is the IRP's ``~F:1:6``. All 4,096
    twelve-bit codes are representable.
    """
    packed = _digits(hexcode, "RC5", 3)
    address = (packed >> 6) & 0x1F
    field_bit = (packed >> 11) & 1
    command = (packed & 0x3F) | (0 if field_bit else 0x40)
    return "RC5", address, None, command


def rc6(hexcode: str) -> tuple[str, int, int | None, int]:
    """``Rc6ProtocolEncoder.encode`` (lib/ir/protocols/rc6.dart L42-105).

    Sixteen bits, address then command, in a mode-0 frame (the app hard-codes
    the mode bits to ``000``). All 65,536 codes are representable.
    """
    value = _digits(hexcode, "RC6", 4)
    return "RC6", value >> 8, None, value & 0xFF


def rca_38(hexcode: str) -> tuple[str, int, int | None, int]:
    """``_deriveProtocolFieldTextFromHex``, ``protocolId == rca38``
    (db_button_import.dart L216-224): three digits, an address nibble and a
    command byte. All 4,096 codes are representable."""
    packed = _digits(hexcode, "RCA_38", 3)
    return "RCA-38", packed >> 8, None, packed & 0xFF


def thomson7(hexcode: str) -> tuple[str, int, int | None, int]:
    """The twelve bits of the hexcode are the frame, in transmission order.

    **This is not what SwiftRemote's encoder does with them, and the app is
    the one in error.** Read the database's codes as the IRP's frame
    ``D:4,T:1,F:7`` laid out left to right -- the first bit sent is hexcode
    bit 11 -- and the IRP's least-significant-bit-first order gives::

        D = reverse4(hex >> 8)     T = hex bit 7     F = reverse7(hex & 0x7F)

    Every one of the 29 database codes then has ``D = 12``, and five of them
    (VOL+, VOL-, MUTE, J UP, J DOWN) are exactly ``D=12`` with ``F`` = 74, 42,
    80, 104 and 88: the five keys that IrpTransmogrifier's own test data
    decodes from a capture of a real Thomson remote. DESIGN D63 has the
    arithmetic. The app's own mask, ``0xF7F``, also clears bit 7, the toggle's
    place in exactly this layout.

    The app instead sends bits 3..0, its toggle, then bits 11..5; see
    :func:`thomson7_as_the_app_sends`. Hexcode bit 7 is the toggle, which a
    form cannot carry (DESIGN D3b), so it is ignored, as the app's mask does.
    Every code is representable.
    """
    value = _digits(hexcode, "Thomson7", 3)
    return "Thomson7", _reverse(value >> 8, 4), None, _reverse(value & 0x7F, 7)


def thomson7_as_the_app_sends(hexcode: str) -> tuple[int, int]:
    """``(device, function)`` of the frame ``thomson7.dart`` (L51-105) builds.

    It masks with ``0xF7F``, sends ``last4`` (bits 3..0, most significant
    first), its own toggle, then ``first7`` (bits 11..5, most significant
    first), and so skips hexcode bit 4. In the IRP's order that is
    ``D = reverse4(hex & 0xF)`` and ``F = reverse7(masked >> 5)``. Not a
    mapping to import: the oracle tool uses it to show the app's frame
    differs from the ledger's for the reason stated above, and for no other.
    """
    masked = _digits(hexcode, "Thomson7", 3) & 0xF7F
    return _reverse(masked & 0xF, 4), _reverse(masked >> 5, 7)


def thomson7_app(hexcode: str) -> tuple[str, int, int | None, int]:
    """The ledger fields of the frame SwiftRemote sends for a Thomson7 code.

    :func:`thomson7_as_the_app_sends` as a hex map, for ``FROM_DB_HEX_APP``.
    The app's mask drops hexcode bits 4 and 7 (and the order is wrong), so two
    codes may give one frame; every code is representable.
    """
    device, function = thomson7_as_the_app_sends(hexcode)
    return "Thomson7", device, None, function


#: DB protocol name -> function(hexcode) -> (ledger protocol, D, S, F). Thomson7
#: is the wire reading, which the importer uses.
FROM_DB_HEX: dict[str, Callable[[str], tuple[str, int, int | None, int]]] = {
    "RC5": rc5,
    "RC6": rc6,
    "RCA_38": rca_38,
    "Thomson7": thomson7,
}

#: What SwiftRemote transmits today for each code. Not for import. Only
#: Thomson7 differs from ``FROM_DB_HEX``; the other three are the same
#: functions, transcriptions of the app's own.
FROM_DB_HEX_APP: dict[str, Callable[[str], tuple[str, int, int | None, int]]] = {
    **FROM_DB_HEX,
    "Thomson7": thomson7_app,
}

#: The app sends Thomson7's frame twice in every press (thomson7.dart L100-103,
#: "duplicate sequence once"). The capture IrpTransmogrifier's test data holds
#: of a Thomson remote shows five to nine repeats while a key is held, so one
#: frame per press is a floor and two is the app's own choice, not a measured
#: need. Recorded as the app's behaviour.
MIN_SENDS: dict[str, int] = {"Thomson7": 2}
