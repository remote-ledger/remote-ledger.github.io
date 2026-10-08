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
MAPPED = (*NEC_FAMILY, SONY_1215, SONY_20)


def signal_of(upgrade: Upgrade, function: Function) -> Signal | str:
    """The signal of one function, or why it has none.

    NEC: ``ProtocolParms`` is ``Device, Sub Device, ...`` (a ``null`` device is read from
    ``FixedData``) and a ``null`` sub device is the complement of the device; the OBC byte is the command complemented and bit-reversed
    (``Translator(lsb,comp)``). Sony 12/15: the parameters are device 1, 0, device 2, 0, the
    OBC byte's lowest bit says which device a function uses and its other seven the command
    reversed (``Translator(0,1,7) Translator(lsb,1,7)``); a device below 32 is the 12-bit frame
    and the rest the 15-bit one. Sony20: device, sub device, and the OBC byte reversed.
    """
    byte = obc_byte(function.hex)
    if byte is None:
        return "the function has no single OBC byte"
    name, parms = upgrade.protocol_name, upgrade.parms
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
    return f"the executor {name!r} is not read by this import"
