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
TWO_BYTE = (SONY_COMBO, NEC1_COMBO, NEC_4DEV, NEC_4DEV_YAMAHA)
MAPPED = (*NEC_FAMILY, SONY_1215, SONY_20, RC5, *TWO_BYTE)


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
        return combo_signal(name, parms, pair[0], pair[1], upgrade.fields.get("FixedData", "").split())
    byte = obc_byte(function.hex)
    if byte is None:
        return "the function has no single OBC byte"
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
    return f"the executor {name!r} is not read by this import"


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
    function = rev8(~first & 0xFF)
    if name == NEC_4DEV_YAMAHA and second & 0x06:
        return "the Yamaha style sends a second byte that is not the complement of the first"
    slot = second >> 6
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
