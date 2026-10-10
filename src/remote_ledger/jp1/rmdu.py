"""A JP1 device upgrade (``.rmdu``) read into plain data, and what its numbers mean.

An upgrade is what a person with a JP1 remote (a universal remote with a cable to a PC)
programs into it so that it sends one device's codes: ``Protocol.name`` names an executor
of the remote's, ``ProtocolParms`` holds the device parameters, and every function has a
name and an OBC byte, ``Function.<n>.hex``. The OBC is not the command: the executor's
translator stores it bit-reversed, and for NEC complemented too. The rules below are the
ones the protocol definitions of ``hifiremote/Protocols`` give (``CmdTranslator``), and
each is checked against the ledger's own codes (DESIGN D119).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9.]*)=(.*)$")
_FUNCTION = re.compile(r"^Function\.(\d+)\.(name|hex|notes)$")
_HEX_BYTE = re.compile(r"^[0-9A-Fa-f]{2}$")


@dataclass
class Function:
    index: int
    name: str | None = None
    hex: str | None = None
    notes: str | None = None


@dataclass
class Upgrade:
    fields: dict[str, str]
    functions: list[Function] = field(default_factory=list)
    #: ``ExtFunction.*`` lines: functions of another device the upgrade borrows, not this one's
    ext_functions: int = 0

    @property
    def protocol_name(self) -> str:
        return self.fields.get("Protocol.name", "").strip()

    @property
    def parms(self) -> list[str]:
        return self.fields.get("ProtocolParms", "").split()


def decode_text(data: bytes) -> str:
    """UTF-8 where the file is, else Windows-1252 (27 of 3,251 files are)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def parse(text: str) -> Upgrade:
    """The fields and functions of one upgrade. A line that is not ``key=value`` continues
    the one before (``Notes`` wraps) and is ignored."""
    fields: dict[str, str] = {}
    funcs: dict[int, Function] = {}
    ext = set()
    for raw in text.splitlines():
        m = _FIELD.match(raw)
        if m is None:
            continue
        key, value = m[1], m[2].strip()
        f = _FUNCTION.match(key)
        if f is not None:
            fn = funcs.setdefault(int(f[1]), Function(int(f[1])))
            setattr(fn, f[2], value)
        elif key.startswith("ExtFunction."):
            ext.add(key.split(".")[1])
        else:
            fields.setdefault(key, value)
    return Upgrade(fields, [funcs[i] for i in sorted(funcs)], len(ext))


def rev8(value: int) -> int:
    """The bits of a byte in the other order (the executors send least significant first)."""
    return int(f"{value:08b}"[::-1], 2)


def obc_byte(hex_text: str | None) -> int | None:
    """The one OBC byte of a function, or None where it is not exactly one byte."""
    if hex_text is None:
        return None
    parts = hex_text.split()
    return int(parts[0], 16) if len(parts) == 1 and _HEX_BYTE.match(parts[0]) else None


def obc_bytes(hex_text: str | None) -> tuple[int, ...] | None:
    """The OBC bytes of a function as written, or None where the text is not a list of bytes."""
    if hex_text is None:
        return None
    parts = hex_text.split()
    return tuple(int(p, 16) for p in parts) if parts and all(_HEX_BYTE.match(p) for p in parts) else None


def rev_bits(value: int, bits: int) -> int:
    """The low ``bits`` bits of ``value`` in the other order (``Translate.reverse(v, bits)``)."""
    return int(f"{value:0{bits}b}"[::-1], 2)


def device_from_fixed_data(upgrade: Upgrade) -> int | None:
    """The NEC device number of an upgrade whose ``Device Number`` parameter is ``null``: the
    executor keeps it in the second byte of ``FixedData`` as the complement of its reverse. The
    ledger holds 91.5% of the codes this reads (2,127 of 2,325), as it holds 87.6% of the others."""
    parts = upgrade.fields.get("FixedData", "").split()
    if len(parts) < 2 or not _HEX_BYTE.match(parts[1]):
        return None
    return ~rev8(int(parts[1], 16)) & 0xFF


def parm(parms: list[str], index: int) -> int | None:
    """A device parameter as a number, or None for ``null``, absent or not a number."""
    if index >= len(parms) or not re.fullmatch(r"\d{1,3}", parms[index]):
        return None
    return int(parms[index])


@dataclass(frozen=True)
class Signal:
    """What one function sends, in the ledger's terms."""

    protocol: str
    device: int
    subdevice: int | None
    function: int


#: The JP1 executors this import reads, with the ledger protocol each is (D119 has the
#: hit rate of each against the ledger's own codes). Everything else the files hold is
#: listed in the report by name.
NEC_FAMILY = {"NEC1": "NEC1", "NEC1 (No Repeats)": "NEC1", "NEC2": "NEC2",
              "NECx1": "NECx1", "NECx2": "NECx2"}
SONY_1215 = "Sony 12/15"
SONY_20 = "Sony20"
RC5 = "RC-5"
SONY_COMBO = "Sony Combo (12/15/20)"
NEC1_COMBO = "NEC1 Combo"
NEC_4DEV = "NEC 4DEV Combo"
NEC_4DEV_YAMAHA = "NEC 4DEV Yamaha Combo"
#: The combos whose functions are two OBC bytes. The others are one.
RC6 = "RC-6"
JVC = "JVC"
JVC_COMBO = "JVC Combo"
AIWA = "Aiwa"
DENON = "Denon"
DENON_COMBO = "Denon Combo (Official)"
PANASONIC = "Panasonic"
PANASONIC_COMBO = "Panasonic Combo"
SHARP = "Sharp"
SHARP_COMBO = "Sharp Combo (Official)"      # the executor of Denon Combo (Official): the same PID, 00 9C
SAMSUNG36 = "Samsung36"
PROTON = "Proton"
F12 = "F12"
RECS80_45 = "RECS80 (45)"
DENON_K = "Denon-K"
NEC_2DEV = "NEC 2DEV Combo"
PIONEER = "Pioneer"
PIONEER_MIX = "Pioneer MIX"
PANASONIC_HACKED = "Panasonic Multi-Device (Hacked)"
RECS80_68 = "RECS80 (68)"
JVC_48 = "JVC-48"
TEAC_K = "Teac-K"
RCA = "RCA"
RCA_56 = "RCA-56"
RCA_38 = "RCA-38"
RCA_38_OFFICIAL = "RCA-38 Official"
RCA_FAMILY = (RCA, RCA_56, RCA_38, RCA_38_OFFICIAL)
TWO_BYTE = (SONY_COMBO, NEC1_COMBO, NEC_4DEV, NEC_4DEV_YAMAHA, NEC_2DEV, JVC_COMBO, DENON_COMBO, SHARP_COMBO,
            PANASONIC_COMBO, DENON_K, PIONEER_MIX, PANASONIC_HACKED, TEAC_K)
MAPPED = (*NEC_FAMILY, SONY_1215, SONY_20, RC5, RC6, JVC, AIWA, DENON, PANASONIC, SHARP, SAMSUNG36, PROTON, F12,
          RECS80_45, PIONEER, RECS80_68, JVC_48, *RCA_FAMILY, *TWO_BYTE)


def signal_of(upgrade: Upgrade, function: Function) -> Signal | str:
    """The signal of one function, or why it has none.

    NEC: ``ProtocolParms`` is ``Device, Sub Device, ...`` (a ``null`` device is read from
    ``FixedData``) and a ``null`` sub device is the complement of the device; the OBC byte is the command complemented and bit-reversed
    (``Translator(lsb,comp)``). Sony 12/15: the parameters are device 1, 0, device 2, 0, the
    OBC byte's lowest bit says which device a function uses and its other seven the command
    reversed (``Translator(0,1,7) Translator(lsb,1,7)``); a device below 32 is the 12-bit frame
    and the rest the 15-bit one. Sony20: device, sub device, and the OBC byte reversed. RC-5:
    :func:`rc5_signal`.
    """
    name, parms = upgrade.protocol_name, upgrade.parms
    if name in TWO_BYTE:
        pair = obc_bytes(function.hex)
        if pair is None or len(pair) != 2:
            return "the function has no two OBC bytes"
        if name == PIONEER_MIX:
            return pioneer_mix_signal(upgrade, pair[0], pair[1])
        return combo_signal(name, parms, pair[0], pair[1], upgrade.fields.get("FixedData", "").split())
    byte = obc_byte(function.hex)
    if byte is None:
        return "the function has no single OBC byte"
    if name in RCA_FAMILY:
        return rca_signal(upgrade, byte)
    if name in NEC_FAMILY:
        device = parm(parms, 0)
        if device is None:
            device = device_from_fixed_data(upgrade)
        if device is None:
            return "the device parameter is missing"
        sub = parm(parms, 1)
        if sub is None:
            if name in ("NECx1", "NECx2"):
                return "the sub device parameter is missing"
            sub = ~device & 0xFF
        return Signal(NEC_FAMILY[name], device, sub, rev8(~byte & 0xFF))
    if name == SONY_1215:
        device = parm(parms, 2 if byte & 1 else 0)
        if device is None:
            return "the device the function selects is not set"
        frame = "Sony12" if device < 32 else "Sony15"
        return Signal(frame, device, None, rev8(byte) & 0x7F)
    if name == SONY_20:
        device, sub = parm(parms, 0), parm(parms, 1)
        if device is None or sub is None:
            return "the device or the sub device parameter is missing"
        command = rev8(byte)
        if command > 127:
            return "the command does not fit Sony's seven bits"
        return Signal("Sony20", device, sub, command)
    if name == RC5:
        return rc5_signal(parms, byte)
    return simple_signal(name, parms, byte, upgrade.fields.get("FixedData", "").split())


def rc5_signal(parms: list[str], byte: int) -> Signal | str:
    """The ``RC-5`` executor: a quickie combo of up to three RC-5 devices (DESIGN D123's neighbour, D129).

    ``ProtocolParms`` is ``Device 1, OBC>63, Device 2, OBC>63, Device 3, OBC>63``. The OBC byte is
    what ``Rc5Translator`` (RemoteMaster) keeps: its low two bits select the device slot (a slot above
    the third is the first) and its top six bits are the command, complemented. A slot's ``OBC>63``
    flag adds the seventh bit; a slot with no device is the nearest earlier slot that has one, as
    the executor's own defaults make it."""
    select = byte & 3
    if select > 2:
        select = 0
    command = 63 - (byte >> 2)
    slot = select
    while slot >= 0 and parm(parms, 2 * slot) is None:
        slot -= 1
    if slot < 0:
        return "the device the function selects is not set"
    device = parm(parms, 2 * slot)
    if device is None or device > 31:
        return "the RC-5 device is not five bits"
    if parm(parms, 2 * slot + 1):
        command |= 64
    return Signal("RC5", device, None, command)


def fixed_byte(fixed: list[str], index: int) -> int | None:
    """A device parameter the executor keeps in byte ``index`` of ``FixedData`` as ``Translator(lsb,comp,...,8)``
    does (complemented, then reversed): the value it has where the upgrade says ``null``."""
    if index < len(fixed) and _HEX_BYTE.match(fixed[index]):
        return rev8(~int(fixed[index], 16) & 0xFF)
    return None


def rca_signal(upgrade: Upgrade, byte: int) -> Signal | str:
    """The RCA executors, read only where they send the 38 kHz the ledger's ``RCA-38`` has (DESIGN D152).

    ``RCA-38`` and ``RCA-38 Official`` are ``Translator()``: the function is the OBC as it is. ``RCA`` (its
    second variant) and ``RCA-56`` are ``Translator(comp)``, the function the OBC complemented, and say their
    carrier in parameter 1 (1 is 38 kHz, 0 is 57 or 56 kHz); the first variant of ``RCA`` has no such
    parameter and sends 56 kHz. The device is the four-bit parameter 0: a ``null`` one is not read, because the
    one upgrade that has it (``RCA-38 Official``, 84 functions) matches none of the ledger's codes under the
    executor's default."""
    name, parms = upgrade.protocol_name, upgrade.parms
    variant = upgrade.fields.get("Protocol.variantName", "").strip()
    if name == RCA and variant != "2":
        return "the carrier is not 38 kHz, which the ledger's RCA-38 has"
    complemented = name in (RCA, RCA_56)
    if complemented and parm(parms, 1) != 1:
        return "the carrier is not 38 kHz, which the ledger's RCA-38 has"
    device = parm(parms, 0)
    if device is None or device > 15:
        return "the device parameter is missing or is not four bits"
    return Signal("RCA-38", device, None, ~byte & 0xFF if complemented else byte)


#: Where ``FixedData`` keeps each parameter of ``Pioneer MIX``'s variants 2 and 3 (``Device 1, Device 2, Cmd1 (OBC),
#: Cmd2 (OBC)`` and two more commands in variant 3), as the ``DeviceTranslator`` offsets say.
PIONEER_MIX_FIXED = {"2": (0, 2, 1, 3), "3": (0, 5, 1, 2, 3, 4)}


def pioneer_mix_signal(upgrade: Upgrade, first: int, second: int) -> Signal | str:
    """``Pioneer MIX``, variants 2 and 3 (DESIGN D151): ``Translator(lsb,comp,3) PioneerMixTranslator()``.

    The first byte is the function, ``rev8(~OBC)``; the second is a flag byte (``PioneerMixTranslator``'s
    ``mask`` of 7): its lowest bit says a two-part signal, and then its next two pick which of the upgrade's
    prefix commands is the first part's. A one-part signal is ``Device 1`` and the function; a two-part one is
    ``Device 1`` and the prefix command, then ``Device 2`` and the function, which is the ledger's
    ``Pioneer-2Part`` as ``D0:D`` and ``F0:F``. A parameter that is ``null`` is in ``FixedData``."""
    layout = PIONEER_MIX_FIXED.get(upgrade.fields.get("Protocol.variantName", "").strip())
    if layout is None:
        return "the variant is not read (its devices are chosen by more flag bits)"
    if second > 7:
        return "the flag byte has bits the variant does not use"
    fixed = upgrade.fields.get("FixedData", "").split()
    values = [parm(upgrade.parms, i) if parm(upgrade.parms, i) is not None else fixed_byte(fixed, at)
              for i, at in enumerate(layout)]
    function = rev8(~first & 0xFF)
    if values[0] is None:
        return "the device parameter is missing"
    if any(v is not None and v > 255 for v in values):
        return "a parameter is not a byte"
    if not second & 1:
        return Signal("Pioneer-2Part", values[0] << 8 | values[0], None, function << 8 | function)
    slot = 2 + ((second >> 1) & 3)
    if slot >= len(values):
        return "the function selects a prefix command the variant does not have"
    if values[1] is None or values[slot] is None:
        return "the device or the prefix command parameter is missing"
    return Signal("Pioneer-2Part", values[0] << 8 | values[1], None, values[slot] << 8 | function)


#: The ledger protocol of the four styles an NEC 4DEV combo function names.
NEC_STYLES = ("NEC1", "NEC2", "NECx1", "NECx2")


def combo_signal(name: str, parms: list[str], first: int, second: int, fixed: list[str] = ()) -> Signal | str:
    """A function of a combo executor, whose functions are two bytes (DESIGN D137 to D139).

    The rules are the translators' (RemoteMaster's ``Translator``, whose arguments are ``index, bits,
    bit offset`` and which complements, then reverses, what it extracts; offsets count from the
    most significant bit of the two bytes) and each is checked against the ledger's own codes.

    * ``Sony Combo (12/15/20)``, ``SonyComboTranslator() Translator(lsb,3,7)``: the first byte's top
      seven bits are the command reversed, its lowest bit says Sony15; the second byte is the
      device reversed (eight bits) for Sony15, else the device reversed in its top five bits, an index
      of the four ``ProtocolParms`` sub devices in the next two, and its lowest bit says Sony20.
    * ``NEC1 Combo``, ``Translator(lsb,comp) Translator(lsb,comp,1,8,8)`` over ``CmdParms=Sub
      Device,OBC``: the sub device and then the function, each complemented and reversed; the device
      is the one parameter.
    * ``NEC 4DEV Combo``: the function as for NEC, then in the second byte the device slot (its top
      two bits) and the style (the bits at offsets 11 and 15): NEC1, NEC2, NECx1 or NECx2, with the
      slot's device and sub device from ``ProtocolParms``. A ``null`` device or sub device is read
      from ``FixedData``, which holds the executor's effective values, each byte complemented and
      reversed (the device translators are ``Translator(lsb,comp)``). The Yamaha variant adds a Y style in
      the bits at offsets 13 and 14, whose three other styles send a second byte that is not the
      complement of the first, a frame the ledger has no protocol for.
    """
    if name == SONY_COMBO:
        command = rev_bits(first >> 1, 7)
        if first & 1:
            return Signal("Sony15", rev8(second), None, command)
        device = rev_bits(second >> 3, 5)
        if not second & 1:
            return Signal("Sony12", device, None, command)
        sub = parm(parms, (second >> 1) & 3)
        if sub is None:
            return "the Sony20 sub device the function selects is not set"
        return Signal("Sony20", device, sub, command)
    if name == NEC1_COMBO:
        device = parm(parms, 0)
        if device is None and fixed and _HEX_BYTE.match(fixed[0]):
            device = rev8(~int(fixed[0], 16) & 0xFF)
        if device is None:
            return "the device parameter is missing"
        return Signal("NEC1", device, rev8(~first & 0xFF), rev8(~second & 0xFF))
    if name == JVC_COMBO:
        return Signal("JVC", rev8(~first & 0xFF), None, rev8(~second & 0xFF))
    if name == PANASONIC_COMBO:
        device = parm(parms, 0)
        if device is None and len(fixed) > 2 and _HEX_BYTE.match(fixed[2]):
            device = rev8(~int(fixed[2], 16) & 0xFF)      # Translator(lsb,comp,0,8,16): the default is 160
        if device is None:
            return "the device parameter is missing"
        why = _panasonic_oem(parms, 1)
        if why:
            return why
        return Signal("Panasonic", device, rev8(~first & 0xFF), rev8(~second & 0xFF))
    if name == DENON_K:
        # Translator(lsb,comp,0,4) Translator(lsb,comp,1,12,4): a 4-bit field, then the 12-bit function; the OEM
        # bytes are parameters 1 and 2, and the ledger's frame fixes them at 84 and 50, the executor's defaults
        device = parm(parms, 0)
        if device is None and len(fixed) > 2 and _HEX_BYTE.match(fixed[2]):
            device = rev_bits(~int(fixed[2], 16) & 0xF, 4)      # Translator(lsb,comp,0,4,20): FD is 4, the default
        if device is None or device > 15:
            return "the device parameter is missing or is not four bits"
        for index, want in ((1, 84), (2, 50)):
            got = parm(parms, index)
            if got is not None and got != want:
                return f"the OEM byte {got} is not Denon's {want}, which the ledger's frame fixes"
        return Signal("Denon-K", device, rev_bits(~(first >> 4) & 0xF, 4),
                      rev_bits(~(((first & 0xF) << 8) | second) & 0xFFF, 12))
    if name == PANASONIC_HACKED:
        # Translator(lsb,comp,0,8,8) Translator(lsb,comp,1): the sub device is the second byte and the function
        # the first, the reverse of the Panasonic Combo; the OEM bytes are parameters 2 and 3
        device = parm(parms, 0)
        if device is None:
            device = fixed_byte(fixed, 2)
        if device is None:
            return "the device parameter is missing"
        why = _panasonic_oem(parms, 2)
        if why:
            return why
        return Signal("Panasonic", device, rev8(~second & 0xFF), rev8(~first & 0xFF))
    if name == TEAC_K:
        # Translator(lsb,comp,0,8,0): the function is the first byte; the second is not part of it
        device, sub = parm(parms, 0), parm(parms, 1)
        if sub is None:
            sub = fixed_byte(fixed, 3)
        if device is None or sub is None or device > 15:
            return "the device parameter is missing or is not four bits"
        for index, want in ((2, 67), (3, 83)):
            got = parm(parms, index)
            if got is not None and got != want:
                return f"the OEM byte {got} is not Teac's {want}, which the ledger's frame fixes"
        return Signal("Teac-K", device, sub, rev8(~first & 0xFF))
    if name in (DENON_COMBO, SHARP_COMBO):
        device = rev_bits(~(first >> 3) & 0x1F, 5)
        protocol = "Denon" if not (first >> 2) & 1 else "Sharp"
        return Signal(protocol, device, None, rev8(~second & 0xFF))
    function = rev8(~first & 0xFF)
    if name == NEC_4DEV_YAMAHA and second & 0x06:
        return "the Yamaha style sends a second byte that is not the complement of the first"
    slot = second >> 7 if name == NEC_2DEV else second >> 6
    style = NEC_STYLES[((second >> 4) & 1) << 1 | second & 1]
    device, sub = parm(parms, 2 * slot), parm(parms, 2 * slot + 1)
    if device is None and len(fixed) > 2 * slot and _HEX_BYTE.match(fixed[2 * slot]):
        device = rev8(~int(fixed[2 * slot], 16) & 0xFF)
    if device is None:
        return "the device the function selects is not set"
    if sub is None:
        if style in ("NECx1", "NECx2"):
            return "the sub device parameter is missing"
        sub = ~device & 0xFF
    return Signal(style, device, sub, function)


#: What the OEM bytes of a Panasonic upgrade must be for the ledger's ``Panasonic`` frame, which has them fixed.
PANASONIC_OEM = (2, 32)


def _panasonic_oem(parms: list[str], first: int) -> str | None:
    """Why the OEM bytes at ``parms[first:first + 2]`` are not Panasonic's 2 and 32, or None. A ``null`` is the
    executor's default, which is 2 and 32."""
    for index, want in enumerate(PANASONIC_OEM):
        got = parm(parms, first + index)
        if got is not None and got != want:
            return f"the OEM byte {got} is not Panasonic's {want}, which the ledger's frame fixes"
    return None


def simple_signal(name: str, parms: list[str], byte: int, fixed: list[str] = ()) -> Signal | str:
    """The executors with one OBC byte and one translator each (DESIGN D141): the device is a parameter, the
    function the OBC as the executor's ``CmdTranslator`` keeps it: ``Translator()`` plain for RC-6,
    ``Translator(lsb)`` reversed for Aiwa and Denon, ``Translator(lsb,comp)`` reversed and complemented for
    JVC and Panasonic."""
    if name == RC6:
        device = parm(parms, 0)
        return Signal("RC6", device, None, byte) if device is not None and device < 256 else "the device parameter is missing"
    if name == JVC:
        device = parm(parms, 0)
        return Signal("JVC", device, None, rev8(~byte & 0xFF)) if device is not None else "the device parameter is missing"
    if name == AIWA:
        # the protocol's own defaults: Device 0, Sub Device 0 (range 0..31)
        device, sub = parm(parms, 0), parm(parms, 1)
        device, sub = 0 if device is None else device, 0 if sub is None else sub
        if sub > 31:
            return "the Aiwa sub device is not five bits"
        return Signal("Aiwa", device, sub, rev8(byte))
    if name == DENON:
        device = parm(parms, 0)
        if device is None or device > 31:
            return "the device parameter is missing or is not five bits"
        return Signal("Denon", device, None, rev8(byte))
    if name == SHARP:
        device = parm(parms, 0)
        if device is None or device > 31:
            return "the device parameter is missing or is not five bits"
        return Signal("Sharp", device, None, rev8(byte))
    if name == SAMSUNG36:
        device, sub, extra = parm(parms, 0), parm(parms, 1), parm(parms, 2)
        # a null parameter is in FixedData (Translator(lsb,0) Translator(lsb,1,8,8) Translator(lsb,2,4,16,0))
        if device is None and len(fixed) > 0 and _HEX_BYTE.match(fixed[0]):
            device = rev8(int(fixed[0], 16))
        if sub is None and len(fixed) > 1 and _HEX_BYTE.match(fixed[1]):
            sub = rev8(int(fixed[1], 16))
        if extra is None and len(fixed) > 2 and _HEX_BYTE.match(fixed[2]):
            extra = rev_bits(int(fixed[2], 16) >> 4, 4)
        if device is None or sub is None:
            return "the device or the sub device parameter is missing"
        # the ledger holds Samsung36's four-bit E in the high bits of the function (D65)
        return Signal("Samsung36", device, sub, ((extra or 0) & 15) << 8 | rev8(byte))
    if name == PROTON:
        device = parm(parms, 0)
        return Signal("Proton", device, None, rev8(~byte & 0xFF)) if device is not None else "the device parameter is missing"
    if name == F12:
        device = parm(parms, 0)
        if device is None or device > 15:
            return "the device parameter is missing or is not four bits"
        return Signal("F12_relaxed", device & 7, device >> 3, rev8(byte))
    if name == RECS80_45:
        device = parm(parms, 0)
        if device is None or device > 7:
            return "the device parameter is missing or is not three bits"
        return Signal("RECS80", device, None, 63 - (byte >> 2))
    if name == RECS80_68:
        # the device is kept complemented (Translator(0,3,2,comp)), and the ledger's frame sends what is kept
        device = parm(parms, 0)
        if device is None or device > 7:
            return "the device parameter is missing or is not three bits"
        return Signal("RECS80-0068", 7 - device, None, 63 - (byte >> 2))
    if name == JVC_48:
        device, sub = parm(parms, 0), parm(parms, 1)
        if device is None or sub is None or device > 255 or sub > 255:
            return "the device or the sub device parameter is missing"
        return Signal("JVC-48", device, sub, rev8(~byte & 0xFF))
    if name == PIONEER:
        # Translator(lsb,comp) for both: a single frame, which the ledger writes as its two parts equal (D = D0)
        device = parm(parms, 0)
        if device is None:
            device = fixed_byte(fixed, 0)
        if device is None or device > 255:
            return "the device parameter is missing"
        function = rev8(~byte & 0xFF)
        return Signal("Pioneer-2Part", device << 8 | device, None, function << 8 | function)
    if name == PANASONIC:
        device, sub = parm(parms, 0), parm(parms, 1)
        if device is None:
            return "the device parameter is missing"
        why = _panasonic_oem(parms, 2)
        if why:
            return why
        return Signal("Panasonic", device, sub if sub is not None else 0, rev8(~byte & 0xFF))
    return f"the executor {name!r} is not read by this import"
