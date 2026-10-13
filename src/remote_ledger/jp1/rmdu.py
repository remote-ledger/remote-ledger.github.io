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
PIONEER_3DEV = "Pioneer 3DEV"
PIONEER_4DEV = "Pioneer 4DEV"
PIONEER_DVD = "Pioneer DVD"
PIONEER_DVD2 = "Pioneer DVD2"
RC56_COMBO = "RC5/6 combo"
RC55X_COMBO = "RC-5/5x Combo"
XMP_FAMILY = ("XMP", "XMP (JP1)", "XMP (Slingbox)", "XMP (Slingbox)(No repeat)", "XMP (UEI)")
PANASONIC_HACKED = "Panasonic Multi-Device (Hacked)"
RECS80_68 = "RECS80 (68)"
JVC_48 = "JVC-48"
TEAC_K = "Teac-K"
RCA = "RCA"
RCA_56 = "RCA-56"
RCA_38 = "RCA-38"
RCA_38_OFFICIAL = "RCA-38 Official"
RCA_FAMILY = (RCA, RCA_56, RCA_38, RCA_38_OFFICIAL)
RCA_COMBO = "RCA Combo (Official)"
TDC = "TDC"
GI_CABLE = "GI Cable"
RC6_M_20N = "RC6-M-20n"
MCE = "MCE"
NOKIA32 = "Nokia32"
PANASONIC_OLD = "Panasonic (old)"
NEC1_F16_OFFICIAL = "NEC1-f16 Official"
NEC1_RNC = "NEC1-rnc"
#: ``NEC-Yamaha``'s styles Y1 to Y3 (IrpTransmogrifier's IRP, DESIGN D158): the fourth byte is the third with these bits
#: complemented (``E0=(~Y:1:1)^(F:1)``, ``~F:6:1``, ``E7=(~Y:1)^(F:1:7)``).
YAMAHA_E_XOR = {1: 0x7F, 2: 0xFE, 3: 0x7E}
TWO_BYTE = (SONY_COMBO, NEC1_COMBO, NEC_4DEV, NEC_4DEV_YAMAHA, NEC_2DEV, JVC_COMBO, DENON_COMBO, SHARP_COMBO,
            PANASONIC_COMBO, DENON_K, PIONEER_MIX, PANASONIC_HACKED, TEAC_K, RCA_COMBO, PIONEER_3DEV, PIONEER_4DEV, RC56_COMBO,
            RC55X_COMBO, PIONEER_DVD2)
MAPPED = (*NEC_FAMILY, SONY_1215, SONY_20, RC5, RC6, JVC, AIWA, DENON, PANASONIC, SHARP, SAMSUNG36, PROTON, F12,
          RECS80_45, PIONEER, PIONEER_DVD, RECS80_68, JVC_48, *RCA_FAMILY, NEC1_F16_OFFICIAL, NEC1_RNC, MCE, NOKIA32, PANASONIC_OLD, TDC, GI_CABLE, RC6_M_20N,
          *XMP_FAMILY, *TWO_BYTE)


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
    if name in XMP_FAMILY:
        return xmp_signal(upgrade, function)
    if name == NEC1_F16_OFFICIAL:
        # four OBC bytes, ``Translator(lsb,comp,i,8,8 * i)`` each: device, sub device, F and E of the frame
        four = obc_bytes(function.hex)
        if four is None or len(four) != 4:
            return "the function has no four OBC bytes"
        device, sub, f, e = (rev8(~b & 0xFF) for b in four)
        return Signal("NEC1-f16", device, sub, f << 8 | e)
    if name in TWO_BYTE:
        pair = obc_bytes(function.hex)
        if pair is None or len(pair) != 2:
            return "the function has no two OBC bytes"
        if name == PIONEER_MIX:
            return pioneer_mix_signal(upgrade, pair[0], pair[1])
        if name == RCA_COMBO:
            return rca_combo_signal(upgrade, pair[0], pair[1])
        if name in (PIONEER_3DEV, PIONEER_4DEV):
            return pioneer_multi_signal(upgrade, pair[0], pair[1])
        if name == RC56_COMBO:
            return rc56_combo_signal(upgrade, pair[0], pair[1])
        if name == RC55X_COMBO:
            return rc55x_combo_signal(upgrade, pair[0], pair[1])
        if name == PIONEER_DVD2:
            return pioneer_dvd_signal(upgrade, pair[0], pair[1])
        return combo_signal(name, parms, pair[0], pair[1], upgrade.fields.get("FixedData", "").split())
    byte = obc_byte(function.hex)
    if byte is None:
        return "the function has no single OBC byte"
    if name in RCA_FAMILY:
        return rca_signal(upgrade, byte)
    if name == TDC:
        return tdc_signal(upgrade, byte)
    if name == GI_CABLE:
        return Signal("GI_Cable", 0, None, rev8(byte))
    if name == PIONEER_DVD:
        return pioneer_dvd_signal(upgrade, byte, None)
    if name == RC6_M_20N:
        return rc6_m_20n_signal(upgrade, byte)
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
    """The RCA executors (DESIGN D152, D189).

    ``RCA-38`` and ``RCA-38 Official`` are ``Translator()``, the function the OBC as it is, at 38 kHz: the
    ledger's ``RCA-38``. The first variant of ``RCA`` is ``Translator()`` too and sends 56 kHz, which the ledger's
    ``RCA`` carries. ``RCA`` (its second variant) and ``RCA-56`` are ``Translator(comp)``, the function the OBC
    complemented, and say their carrier in parameter 1: 1 is 38 kHz (``RCA-38``), 0 is 57 or 56 kHz (``RCA``). The device
    is the four-bit parameter 0: a ``null`` one is not read, because the one upgrade that has it (``RCA-38 Official``,
    84 functions) matches none of the ledger's codes under the executor's default."""
    name, parms = upgrade.protocol_name, upgrade.parms
    variant = upgrade.fields.get("Protocol.variantName", "").strip()
    device = parm(parms, 0)
    if device is None or device > 15:
        return "the device parameter is missing or is not four bits"
    if name in (RCA_38, RCA_38_OFFICIAL):
        return Signal("RCA-38", device, None, byte)
    if name == RCA and variant == "":
        return Signal("RCA", device, None, byte)
    if name == RCA_56 or (name == RCA and variant == "2"):
        carrier = parm(parms, 1)
        if carrier not in (0, 1):
            return "the carrier parameter is missing"
        return Signal("RCA-38" if carrier == 1 else "RCA", device, None, ~byte & 0xFF)
    return "the variant is not read"


def rca_combo_signal(upgrade: Upgrade, first: int, second: int) -> Signal | str:
    """``RCA Combo (Official)``: two OBC bytes, the device and the function (DESIGN D189).

    The first variant is ``Translator(0,4,0) Translator(1,8,8)``: the device is the top four bits of the first byte,
    whose low four are the executor's fixed 15 (``DefaultCmd`` ``0f 00``), and the function the second byte as it is. The
    second is ``Translator(0,4,0,comp) Translator(1,2,6) Translator(2,8,8,comp)``: the device complemented in the top
    four bits, a two-bit prefix choice in the lowest two (0 is none; the others send another code first, which one signal
    cannot say) and the function complemented. Neither says its carrier; the family's is the ledger's ``RCA``."""
    variant = upgrade.fields.get("Protocol.variantName", "").strip()
    if variant == "":
        if first & 0x0F != 0x0F:
            return "the first byte's low four bits are not the executor's fixed 15"
        return Signal("RCA", first >> 4, None, second)
    if variant == "2":
        if first & 0x0F:
            return "the function sends a prefix code first or sets a bit the variant does not use"
        return Signal("RCA", ~(first >> 4) & 0x0F, None, ~second & 0xFF)
    return "the variant is not read"


def rc6_m_20n_signal(upgrade: Upgrade, byte: int) -> Signal | str:
    """``RC6-M-20n`` (DESIGN D196): ``DevParms`` is ``M=6, Device, Sub Device`` (the ``short`` variant adds ``T``) and the
    function is ``Translator()``, the OBC as it is. A ``null`` parameter is the value ``FixedData`` keeps for it, as for
    the other executors, in the layout of the variant: the first variant is ``Translator(0,3,1) Translator(1,8,8)
    Translator(2,4,4)`` (M in bits 1 to 3 of the first byte, the device the second byte, the sub device the low nibble of
    the first), the ``short`` one doubles every bit as the bit and its complement (``BitDoubler``), M at offset 2, the
    device at 12 and the sub device's low two bits at 36 and its high two at 28 and 30. The ledger's ``RC6-6-20`` is mode 6
    alone; the toggle ``T`` is not in a file. The third variant (a 33.6 kHz option) is not read."""
    variant = upgrade.fields.get("Protocol.variantName", "").strip()
    fixed = upgrade.fields.get("FixedData", "").split()
    bits = "".join(f"{int(b, 16):08b}" for b in fixed if _HEX_BYTE.match(b))
    given = [parm(upgrade.parms, i) for i in range(3)]
    if variant == "":
        if len(bits) < 16 and None in given:
            return "FixedData is too short for the null parameters"
        kept = (int(bits[1:4], 2), int(bits[8:16], 2), int(bits[4:8], 2)) if len(bits) >= 16 else (None,) * 3
    elif variant == "short":
        if len(bits) < 40 and None in given:
            return "FixedData is too short for the null parameters"
        kept = ((int(bits[2:8:2], 2), int(bits[12:28:2], 2),
                 int(bits[28] + bits[30] + bits[36] + bits[38], 2)) if len(bits) >= 40 else (None,) * 3)
    else:
        return "the variant is not read"
    mode, device, sub = (g if g is not None else k for g, k in zip(given, kept))
    if mode != 6:
        return "the mode is not 6, which the ledger's RC6-6-20 fixes"
    if device > 255 or sub > 15:
        return "the device is not eight bits or the sub device not four"
    return Signal("RC6-6-20", device, sub, byte)


def xmp_signal(upgrade: Upgrade, function: Function) -> Signal | str:
    """The ``XMP`` executors (DESIGN D213): ``Device, Subdevice, OEM Code, Final Frame Toggle`` and, in the variants of
    five parameters, ``XMP Type``; the function is one OBC byte (two in variant 2 and ``XMP (UEI)``, whose second byte
    is the low byte of XMP-2's 16-bit command).

    A ``null`` parameter is the value ``FixedData`` keeps: the subdevice as its high nibble at the top of byte 0 and its
    low nibble at the top of byte 1, the OEM byte 2 and the device byte 3, then in the five-parameter variants the final
    frame the top bit of byte 6 and the type the complement of its next bit, in variant 2 the final frame the complement
    of bit 3 of byte 1. The ledger's ``XMP-1`` has the device, the subdevice and one function byte, and ``XMPff-1``, which an
    upgrade with the final frame on sends, the same with that third transmission sent when the key is released (the
    signal's ending, D217); **the OEM must be their 68 and the type 0 (XMP-1; 1 is XMP-2)**, and the function is the OBC as
    it is."""
    variant = upgrade.fields.get("Protocol.variantName", "").strip()
    five = len(upgrade.parms) == 5
    if (five and variant not in ("JP1", "JP1Slingbox")) or (not five and variant not in ("2", "")):
        return "the variant is not read"
    fixed = [int(b, 16) for b in upgrade.fields.get("FixedData", "").split() if _HEX_BYTE.match(b)]
    if len(fixed) < (7 if five else 4):
        return "FixedData is too short for the null parameters"
    given = [parm(upgrade.parms, i) for i in range(len(upgrade.parms))]
    device = given[0] if given[0] is not None else fixed[3]
    sub = given[1] if given[1] is not None else (fixed[0] >> 4) << 4 | fixed[1] >> 4
    oem = given[2] if given[2] is not None else fixed[2]
    if five:
        final = given[3] if given[3] is not None else fixed[6] >> 7
        xmp_type = given[4] if given[4] is not None else 1 - (fixed[6] >> 6 & 1)
    else:
        final = given[3] if given[3] is not None else 1 - (fixed[1] >> 3 & 1)
        xmp_type = 0
    if oem != 68:
        return "the OEM code is not 68, which the ledger's XMP-1 fixes"
    if xmp_type != 0:
        return "the type is XMP-2, which the ledger does not register"
    if device > 255 or sub > 255:
        return "a parameter is not a byte"
    obc = obc_bytes(function.hex)
    if obc is None or len(obc) != (1 if five else 2):
        return "the function has no OBC bytes of the executor's shape"
    if not five and obc[1] != 0:
        return "the second OBC byte is not 0, which XMP-1 has no place for"
    return Signal("XMPff-1" if final else "XMP-1", device, sub, obc[0])


def tdc_signal(upgrade: Upgrade, byte: int) -> Signal | str:
    """``TDC`` (DESIGN D189): ``DevParms`` is ``Variant:TDC-38|TDC-56, Device:5=10, Sub Device:5=26`` and the function
    is ``Translator(0,7)``, the seven bits of the OBC byte from the top, so the command is ``OBC >> 1`` (the lowest bit of
    the byte is not part of it). The variant chooses the ledger's ``TDC-38`` (0) or ``TDC-56`` (1); a ``null`` parameter is
    not read."""
    variant, device, sub = (parm(upgrade.parms, i) for i in range(3))
    if variant not in (0, 1) or device is None or sub is None:
        return "the variant, device or sub device parameter is missing"
    if device > 31 or sub > 31:
        return "the device or the sub device is not five bits"
    if byte & 1:
        return "the OBC byte has its lowest bit set, which a seven-bit command does not use"
    return Signal("TDC-56" if variant else "TDC-38", device, sub, byte >> 1)


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


def pioneer_multi_signal(upgrade: Upgrade, first: int, second: int) -> Signal | str:
    """``Pioneer 3DEV`` and ``Pioneer 4DEV`` (DESIGN D200): two hex bytes the executors' ``Pioneer3DevXlator`` and
    ``Pioneer4DevXlator`` read, and a signal of two parts, which the ledger's ``Pioneer-2Part`` carries as ``D0:D`` and
    ``F0:F`` (a one-part signal is the two parts equal). The devices are the parameters, one byte each, or where ``null``
    the byte ``FixedData`` keeps reversed (``Translator(lsb,...)``, no complement).

    **3DEV:** the first byte is the OBC reversed. The top three bits of the second say which device the function uses (4 is
    Device 1, 2 is Device 2, anything else Device 3) and its low five, reversed, are the second part's OBC 2, which with
    ``OBC & 0x80`` and ``OBC & 0x40`` clear add ``0x80`` and ``0x40`` (``adjust``); 0 says no second part. **4DEV:** each
    byte is an OBC reversed with bit 5 (the device bit) set or clear: the device is ``2 * (first & 4 != 0) + (second & 4
    != 0)`` of the four, and the OBCs are the bytes reversed with that bit cleared. Both parts are sent with the chosen
    device, and the function is the OBC as it is."""
    four = upgrade.protocol_name == PIONEER_4DEV
    fixed = upgrade.fields.get("FixedData", "").split()

    def device(index: int) -> int | None:
        given = parm(upgrade.parms, index)
        if given is not None:
            return given
        return rev8(int(fixed[index], 16)) if index < len(fixed) and _HEX_BYTE.match(fixed[index]) else None

    if four:
        index = (2 if first & 4 else 0) + (1 if second & 4 else 0)
        one, two = rev8(first) & 0xDF, rev8(second) & 0xDF
    else:
        index = {4: 0, 2: 1}.get(second >> 5, 2)
        one = rev8(first)
        extra = rev_bits(second & 0x1F, 5)
        two = one if extra == 0 else extra + (0x80 if one & 0x80 else 0) + (0x40 if not one & 0x40 else 0)
    chosen = device(index)
    if chosen is None:
        return "the device the function selects is not set"
    if chosen > 255:
        return "a parameter is not a byte"
    return Signal("Pioneer-2Part", chosen << 8 | chosen, None, one << 8 | two)


def pioneer_dvd_signal(upgrade: Upgrade, first: int, second: int | None) -> Signal | str:
    """``Pioneer DVD`` and ``Pioneer DVD2`` (DESIGN D239): Pioneer's DVD players send either one frame or two.

    ``CmdTranslator`` of ``Pioneer DVD`` is ``Translator(lsb,comp,3)`` for the OBC and one-bit translators on the bit at offset 5, so
    the function's byte is the OBC complemented and reversed (``C2`` is 188). **One bit says which**, the three one-bit translators all
    naming offset 5: in the byte, counted from its most significant bit (``0x04``), set is one part and clear two, as the protocol's
    notes say ("bit 5"); in the OBC, the byte complemented and reversed, it is ``0x20`` and the sense is the other: set is two parts, with
    ``Device 1`` and the ``Prefix Command`` and then ``Device 2`` and the OBC (``Power`` of the DV-C505 is ``163 153`` then ``175 188``),
    clear one part, ``Device 1`` and the OBC (``Play`` is ``163 158``). **The 38 functions of the DV-C505 whose notes spell the frame
    out agree, 38 of 38.** ``DVD2`` is the same with a second byte that is only ``FieldCopier(comp,5,15,1)``, the flag again, which
    the reader holds the file to. The ledger's
    ``Pioneer-2Part`` writes a frame as ``D0:D`` and ``F0:F`` (one part is the two equal). The devices are the parameters, in the
    order ``Device 1, Device 2, Prefix Command`` and, where ``null``, the bytes ``FixedData`` keeps as ``Translator(lsb,comp,...,8)``
    does, at 0, 2 and 1."""
    obc = ~rev8(first) & 0xFF
    two = bool(obc & 0x20)
    if second is not None and second != (1 if two else 0):
        return "the second byte is not the flag the executor copies from the first"
    fixed = upgrade.fields.get("FixedData", "").split()

    def value(index: int, at: int) -> int | None:
        given = parm(upgrade.parms, index)
        return given if given is not None else fixed_byte(fixed, at)

    one = value(0, 0)
    if one is None:
        return "the device parameter is missing"
    if not two:
        if one > 255:
            return "a parameter is not a byte"
        return Signal("Pioneer-2Part", one << 8 | one, None, obc << 8 | obc)
    other, prefix = value(1, 2), value(2, 1)
    if other is None or prefix is None:
        return "the device or the prefix command parameter is missing"
    if max(one, other, prefix) > 255:
        return "a parameter is not a byte"
    return Signal("Pioneer-2Part", one << 8 | other, None, prefix << 8 | obc)


def rc56_combo_signal(upgrade: Upgrade, first: int, second: int) -> Signal | str:
    """``RC5/6 combo``, its variant 2 (DESIGN D204): two bytes, a function of RC-5 or of RC-6.

    ``CmdTranslator`` is ``Translator(0,1,15) Translator(1,5,3,comp) Translator(1,1,0,6) Translator(1,1,2,5)
    Translator(2,6,8,comp) Translator(2,1,1,6) Translator(3,2,13) Translator(4,8,0)``, so the low bit of the second byte
    is the type (0 is RC-5). **RC-5:** the device is the low five bits of the first byte complemented (the two bits above,
    a device above 31, are not read), the command the six bits of the second byte above its low two complemented, and the
    seventh the first byte's ``0x40`` as it is (``5F BC`` is device 0 and 16 + 64 = 80, Philips's Up). **RC-6:** the first
    byte is the OBC as it is and bits 1 and 2 of the second pick one of the upgrade's three RC-6 devices (3 is none), which
    are the parameters, or where ``null`` the eight bits ``FixedData`` keeps doubled as the bit and its complement from the
    third byte on (``BitDoubler``). The ledger's ``RC5`` and ``RC6`` (mode 0)."""
    if upgrade.fields.get("Protocol.variantName", "").strip() != "2":
        return "the variant is not read"
    if not second & 1:
        if first & 0xA0:
            return "the RC-5 device is not five bits"
        if second & 2:
            return "the function sets a bit RC-5 does not use"
        return Signal("RC5", ~first & 0x1F, None, (~(second >> 2) & 0x3F) | (first & 0x40))
    chosen = (second >> 1) & 3
    if chosen == 3:
        return "the function selects no RC-6 device"
    device = parm(upgrade.parms, chosen)
    if device is None:
        bits = "".join(f"{int(b, 16):08b}" for b in upgrade.fields.get("FixedData", "").split() if _HEX_BYTE.match(b))
        at = 16 + 16 * chosen
        if len(bits) < at + 16:
            return "the RC-6 device is not set"
        device = int(bits[at:at + 16:2], 2)
    if device > 255:
        return "a parameter is not a byte"
    return Signal("RC6", device, None, first)


def rc55x_combo_signal(upgrade: Upgrade, first: int, second: int) -> Signal | str:
    """``RC-5/5x Combo`` (DESIGN D232): two bytes, a function of RC-5 or of RC-5x.

    ``CmdTranslator`` is RemoteMaster's ``RC5_5xComboTranslator`` (offsets count from the most significant bit of the two bytes),
    and the low bit of the second byte is the type (0 is RC-5). **RC-5** is the ``RC5/6 combo``'s: the device the low five bits of
    the first byte complemented, the command the six bits of the second above its low two complemented and the seventh the first
    byte's ``0x40`` as it is (the bits at offsets 0 and 2 and the second byte's bit 1 are zero). **RC-5x:** the sub device is the
    six bits at offsets 0 to 5 complemented, **plus 64 where the slot's flag is set**, the OBC the six bits at offsets 6 to 11
    complemented, the bit at offset 12 is zero, and the two bits at offsets 13 and 14 pick one of the upgrade's four RC-5x device
    slots. A slot's device and its flag are the device parameters (the setup sheet's ``RC5x Device n`` and ``Sub-Device>63``) or,
    where the device is ``null``, what ``FixedData`` keeps: one byte a slot, the device the low five bits complemented and the flag
    its ``0x40`` (``Rc5FlagTranslator`` sets a slot with no device to the opposite flag of the one before it, so a flag parameter
    of an unset slot is not the effective one, and ``FixedData``'s is read). The ledger's ``RC5`` and ``RC5x``: ``device`` is D,
    ``subdevice`` S (the seven-bit command) and ``function`` F (the six-bit data), the reading Marantz's own chart confirms (D233)."""
    if not second & 1:
        if first & 0xA0:
            return "the RC-5 device is not five bits"
        if second & 2:
            return "the function sets a bit RC-5 does not use"
        return Signal("RC5", ~first & 0x1F, None, (~(second >> 2) & 0x3F) | (first & 0x40))
    if second & 8:
        return "the function sets a bit RC-5x does not use"
    slot = (second >> 1) & 3
    fixed = upgrade.fields.get("FixedData", "").split()
    byte = int(fixed[slot], 16) if slot < len(fixed) and _HEX_BYTE.match(fixed[slot]) else None
    device = parm(upgrade.parms, 2 * slot)
    if device is None and byte is not None:
        device = ~byte & 0x1F
    if device is None:
        return "the RC-5x device the function selects is not set"
    flag = (byte >> 6) & 1 if byte is not None else parm(upgrade.parms, 2 * slot + 1)
    if flag is None:
        return "the RC-5x sub device flag the function selects is not set"
    if device > 31 or flag > 1:
        return "a parameter is out of range"
    return Signal("RC5x", device, (~(first >> 2) & 0x3F) + 64 * flag, ~(((first & 3) << 4) | (second >> 4)) & 0x3F)


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
    yamaha = (second >> 1) & 3 if name == NEC_4DEV_YAMAHA else 0       # YStyle: NEC, Y1, Y2, Y3 (bits 13 and 14)
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
    if yamaha:
        # the frame's fourth byte is not F's complement: NEC1-f16 and NEC2-f16 hold it (DESIGN D158, D164)
        if style not in ("NEC1", "NEC2"):
            return "the Yamaha style is sent in an NECx frame, whose relaxed form the ledger does not register"
        return Signal(style + "-f16", device, sub, function << 8 | (function ^ YAMAHA_E_XOR[yamaha]))
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
    if name == NOKIA32:
        # CmdTranslator=Translator(): F is the OBC as it is. The device translators put the mode (``High X-bit``) in
        # byte 0 of FixedData and the Device, Sub Device and X bytes in 1, 2 and 3, as they are (DESIGN D182)
        def given(index: int, at: int) -> int | None:
            value = parm(parms, index)
            if value is None and len(fixed) > at and _HEX_BYTE.match(fixed[at]):
                value = int(fixed[at], 16)
            return value

        device, sub, x, mode = given(0, 1), given(1, 2), given(2, 3), given(3, 0)
        if None in (device, sub, x, mode):
            return "the device, sub device, X or high X-bit parameter is missing"
        if device > 255 or sub > 255 or x > 255:
            return "a parameter is not a byte"
        if mode == 2:
            return "the high X bit is sent at release, which a form cannot say"
        if mode == 0 and x >= 128:
            return "the high X bit is set and constant, which a form cannot send (its toggle is not in a file)"
        return Signal("Nokia32", device, sub, (x & 0x7F) << 8 | byte)
    if name == PANASONIC_OLD:
        # Translator(comp,0,2,6) Translator(lsb,1,6,0): the top six bits of the byte are F reversed and its low two
        # the complement of the device the function uses (0 to 2: Device 1 to 3; 3 is OBC/2). A device the upgrade
        # leaves null is the one in FixedData (Translator(lsb,comp,i,5,...): its five bits after the first, reversed
        # and complemented), which is the executor's default
        selected = ~byte & 3
        if selected == 3:
            return "the function sends OBC/2, which is not read"
        device = parm(parms, selected)
        if device is None and selected < len(fixed) and _HEX_BYTE.match(fixed[selected]):
            device = ~rev_bits((int(fixed[selected], 16) >> 2) & 0x1F, 5) & 0x1F
        if device is None:
            return "the device parameter is missing"
        if device > 31:
            return "the device is not five bits"
        return Signal("Panasonic_Old", device, None, rev_bits(byte >> 2, 6))
    if name == MCE:
        # Translator(0): the function is the OBC as it is; the device is seven bits and the second OEM byte eight, both
        # parameters (``Device:7=4``, ``OEM2:8=15``); the first OEM byte is the executor's 128
        device, oem2 = parm(parms, 0), parm(parms, 1)
        if device is None or oem2 is None:
            return "the device or the second OEM byte parameter is missing"
        if device > 127 or oem2 > 255:
            return "the device is not seven bits"
        return Signal("MCE", device, oem2, byte)
    if name == NEC1_RNC:
        # Translator(lsb): the function is the OBC reversed, and the frame's fourth byte is its complement with the
        # nibbles swapped (IrpTransmogrifier's NEC1-rnc), which NEC1-f16 holds
        # (a ``null`` parameter is in FixedData, each byte reversed: ``80 7F`` is device 1, sub device 254)
        device, sub = parm(parms, 0), parm(parms, 1)
        if device is None and len(fixed) > 0 and _HEX_BYTE.match(fixed[0]):
            device = rev8(int(fixed[0], 16))
        if sub is None and len(fixed) > 1 and _HEX_BYTE.match(fixed[1]):
            sub = rev8(int(fixed[1], 16))
        if device is None or sub is None:
            return "the device or the sub device parameter is missing"
        f = rev8(byte)
        return Signal("NEC1-f16", device, sub, f << 8 | ((~f & 0x0F) << 4) | ((~f >> 4) & 0x0F))
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
