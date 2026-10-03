"""The Kaseikyo family: six 48-bit frames that share one waveform.

Every protocol here has the same shape -- an 8-unit mark and 4-unit space,
forty-eight LSB-first bits coded ``<1,-1|1,-3>``, a stop mark, and a gap --
and differs in which of the six bytes are fixed, which are checksums, and how
long the gap is. IrpTransmogrifier lists them as separate protocols (it also
lists ``Kaseikyo`` itself, a generic form with the vendor bytes as parameters,
which is *not* registered: its ``D`` is four bits wide and its last byte is a
parity pair, so it cannot carry a Panasonic ``D:8``).

IRP, each verbatim from IrpTransmogrifier's protocol database::

    Panasonic  {37k,432}<1,-1|1,-3>(8,-4,2:8,32:8,D:8,S:8,F:8,(D^S^F):8,1,-173)*
    JVC-48     {37k,432}<1,-1|1,-3>(8,-4,3:8,1:8,D:8,S:8,F:8,(D^S^F):8,1,-173)*
    Fujitsu    {37k,432}<1,-1|1,-3>(8,-4,20:8,99:8,0:4,E:4,D:8,S:8,F:8,1,-110)*
    Teac-K     {37k,432}<1,-1|1,-3>(8,-4,67:8,83:8,X:4,D:4,S:8,F:8,T:8,1,-100,(8,-8,1,-100)*)
    Denon-K    {37k,432}<1,-1|1,-3>(8,-4,84:8,50:8,0:4,D:4,S:4,F:12,((D*16)^S^(F*16)^(F:8:4)):8,1,-173)*
    SharpDVD   {38k,400}<1,-1|1,-3>(8,-4,170:8,90:8,15:4,D:4,S:8,F:8,E:4,C:4,1,-48)*

Three things the encoders have to get right:

* **Fields are LSB-first and need not be byte aligned.** Fujitsu's ``0:4,E:4``,
  Teac-K's ``X:4,D:4`` and Denon-K's ``S:4,F:12`` share bytes, so a frame is
  built from ``(value, width)`` pairs, never from whole bytes.
* **Only Teac-K has an intro.** The others end in ``)*`` and put the whole
  frame in the repeat, the shape IrpTransmogrifier renders and the one NECx2
  and Sony20 use here (D6 rule 6: ``NNNN = 0000``). Teac-K's frame is sent
  once, then repeated as a shorter ``8,-8,1,-100``.
* **The gap is a fixed number of units, not an extent.** None of the six
  writes ``^``, so ``extent_us`` is ``None`` and D31 supplies a gap only for a
  truncated capture. The gap differs between members -- 173 units for
  Panasonic, JVC-48 and Denon-K, 110 for Fujitsu, 100 for Teac-K, 48 for
  SharpDVD -- and the app this was audited against uses 173 for all of them.

Parameters the IRP defaults and the remote file cannot name (Fujitsu ``E``,
Teac-K ``X``, SharpDVD ``E``) are fixed at the IRP's default. A code that
needs another value is not representable here, and the encoder says so
rather than guessing.
"""

from __future__ import annotations

from ..errors import EncodeError
from ..numeric import UNIT_US_MAX, UNIT_US_MIN, check_bounds
from ..signal import IrSignal
from .base import Protocol

_UNIT_US = 432
_BITS = 48

_IRP_SOURCE_TEMPLATE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml, {where} "
    "(bengtmartensson/IrpTransmogrifier), verbatim. {note}"
)


def _bits_lsb_first(value: int, width: int) -> list[int]:
    return [(value >> i) & 1 for i in range(width)]


def _frame(fields: list[tuple[int, int]], unit: int, gap_units: int) -> list[int]:
    """Header ``8,-4``, the fields LSB-first, a stop mark, and the gap."""
    frame: list[int] = [8 * unit, 4 * unit]
    for value, width in fields:
        for bit in _bits_lsb_first(value, width):
            frame += [unit, 3 * unit] if bit else [unit, unit]
    frame += [unit, gap_units * unit]
    return frame


def _check_field(name: str, label: str, value: int, width: int, field: str) -> None:
    if not 0 <= value < (1 << width):
        raise EncodeError(
            f"{name} {label} is {value}; {field}:{width} holds 0-{(1 << width) - 1}"
        )


def _need_subdevice(name: str, subdevice: int | None, width: int) -> int:
    if subdevice is None:
        raise EncodeError(
            f"{name} requires an explicit subdevice (S:{width}); the ledger "
            "states it rather than assuming the IRP's default"
        )
    _check_field(name, "subdevice", subdevice, width, "S")
    return subdevice


def _unit(unit_us: int | None, default: int) -> int:
    unit = default if unit_us is None else unit_us
    check_bounds("unitUs", unit, UNIT_US_MIN, UNIT_US_MAX)
    return unit


# --- Panasonic and JVC-48: vendor bytes, D:8 S:8 F:8, XOR check -------------------


def _xor_checked(name: str, vendor: tuple[int, int], gap: int, default_unit: int):
    def encode(
        *,
        device: int,
        subdevice: int | None,
        function: int,
        carrier_hz: int,
        unit_us: int | None = None,
    ) -> IrSignal:
        unit = _unit(unit_us, default_unit)
        _check_field(name, "device", device, 8, "D")
        sub = _need_subdevice(name, subdevice, 8)
        _check_field(name, "function", function, 8, "F")
        fields = [
            (vendor[0], 8), (vendor[1], 8),
            (device, 8), (sub, 8), (function, 8),
            (device ^ sub ^ function, 8),
        ]
        return IrSignal(carrier_hz=carrier_hz, repeat=tuple(_frame(fields, unit, gap)))

    return encode


encode_panasonic = _xor_checked("Panasonic", (2, 32), 173, _UNIT_US)
encode_jvc48 = _xor_checked("JVC-48", (3, 1), 173, _UNIT_US)


# --- Fujitsu: no check byte, a 4-bit zero and a 4-bit E ----------------------------


def encode_fujitsu(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    name = "Fujitsu"
    unit = _unit(unit_us, _UNIT_US)
    _check_field(name, "device", device, 8, "D")
    sub = _need_subdevice(name, subdevice, 8)
    _check_field(name, "function", function, 8, "F")
    e = 0  # the IRP's E:0..15=0; a remote file has no field for it
    fields = [(20, 8), (99, 8), (0, 4), (e, 4), (device, 8), (sub, 8), (function, 8)]
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(_frame(fields, unit, 110)))


# --- Teac-K: X:4 D:4 S:8 F:8 T:8, and a different repeat --------------------------


def encode_teac_k(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    name = "Teac-K"
    unit = _unit(unit_us, _UNIT_US)
    _check_field(name, "device", device, 4, "D")
    sub = _need_subdevice(name, subdevice, 8)
    _check_field(name, "function", function, 8, "F")
    x = 1  # the IRP's X:0..15=1
    t = device + (sub & 15) + (sub >> 4) + (function & 15) + (function >> 4)
    fields = [(67, 8), (83, 8), (x, 4), (device, 4), (sub, 8), (function, 8), (t, 8)]
    intro = _frame(fields, unit, 100)
    repeat = [8 * unit, 8 * unit, unit, 100 * unit]
    return IrSignal(
        carrier_hz=carrier_hz, intro=tuple(intro), repeat=tuple(repeat)
    )


# --- Denon-K: S:4 and F:12 share bytes, and the check byte truncates ----------------


def encode_denon_k(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    name = "Denon-K"
    unit = _unit(unit_us, _UNIT_US)
    _check_field(name, "device", device, 4, "D")
    sub = _need_subdevice(name, subdevice, 4)
    _check_field(name, "function", function, 12, "F")
    # ((D*16)^S^(F*16)^(F:8:4)):8 -- the trailing :8 truncates the whole XOR,
    # which is what discards F*16's bits above the eighth.
    check = ((device * 16) ^ sub ^ (function * 16) ^ ((function >> 4) & 0xFF)) & 0xFF
    fields = [
        (84, 8), (50, 8), (0, 4), (device, 4), (sub, 4), (function, 12), (check, 8),
    ]
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(_frame(fields, unit, 173)))


# --- SharpDVD: 15:4 D:4 S:8 F:8 E:4 C:4, a 400 us unit and a short gap -------------


def encode_sharp_dvd(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    name = "SharpDVD"
    unit = _unit(unit_us, 400)
    _check_field(name, "device", device, 4, "D")
    sub = _need_subdevice(name, subdevice, 8)
    _check_field(name, "function", function, 8, "F")
    e = 1  # the IRP's E:0..15=1
    c = device ^ (sub & 15) ^ (sub >> 4) ^ (function & 15) ^ (function >> 4) ^ e
    fields = [
        (170, 8), (90, 8), (15, 4), (device, 4), (sub, 8), (function, 8), (e, 4), (c, 4),
    ]
    return IrSignal(carrier_hz=carrier_hz, repeat=tuple(_frame(fields, unit, 48)))


# --- the registry entries -----------------------------------------------------------

PANASONIC = Protocol(
    name="Panasonic",
    irp="{37k,432}<1,-1|1,-3>(8,-4,2:8,32:8,D:8,S:8,F:8,(D^S^F):8,1,-173)* "
        "[D:0..255,S:0..255,F:0..255]",
    irp_source=_IRP_SOURCE_TEMPLATE.format(
        where="release 1.2.14 L1939 (@c945e76 L1958)",
        note="Its vendor bytes 2 and 32 are Panasonic's OEM codes; D, S and F "
        "are each a whole byte and the sixth byte is their XOR.",
    ),
    unit_us=_UNIT_US,
    nominal_carrier_hz=37_000,
    extent_us=None,
    bits=_BITS,
    encode=encode_panasonic,
)

JVC48 = Protocol(
    name="JVC-48",
    irp="{37k,432}<1,-1|1,-3>(8,-4,3:8,1:8,D:8,S:8,F:8,(D^S^F):8,1,-173)* "
        "[D:0..255,S:0..255,F:0..255]",
    irp_source=_IRP_SOURCE_TEMPLATE.format(
        where="release 1.2.14 L1219 (@c945e76 L1219)",
        note="Panasonic's frame with JVC's vendor bytes 3 and 1.",
    ),
    unit_us=_UNIT_US,
    nominal_carrier_hz=37_000,
    extent_us=None,
    bits=_BITS,
    encode=encode_jvc48,
)

FUJITSU = Protocol(
    name="Fujitsu",
    irp="{37k,432}<1,-1|1,-3>(8,-4,20:8,99:8,0:4,E:4,D:8,S:8,F:8,1,-110)* "
        "[D:0..255,S:0..255=D,F:0..255,E:0..15=0]",
    irp_source=_IRP_SOURCE_TEMPLATE.format(
        where="release 1.2.14 L929 (@c945e76 L929)",
        note="No check byte. S defaults to D in the IRP; this encoder "
        "requires it stated.",
    ),
    unit_us=_UNIT_US,
    nominal_carrier_hz=37_000,
    extent_us=None,
    bits=_BITS,
    encode=encode_fujitsu,
)

TEAC_K = Protocol(
    name="Teac-K",
    irp="{37k,432}<1,-1|1,-3>(8,-4,67:8,83:8,X:4,D:4,S:8,F:8,T:8,1,-100,"
        "(8,-8,1,-100)*) {T=D+S:4:0+S:4:4+F:4:0+F:4:4} "
        "[D:0..15,S:0..255,F:0..255,X:0..15=1]",
    irp_source=_IRP_SOURCE_TEMPLATE.format(
        where="release 1.2.14 L2830 (@c945e76 L2849)",
        note="The only member with an intro and a different repeat frame.",
    ),
    unit_us=_UNIT_US,
    nominal_carrier_hz=37_000,
    extent_us=None,
    bits=_BITS,
    encode=encode_teac_k,
)

DENON_K = Protocol(
    name="Denon-K",
    irp="{37k,432}<1,-1|1,-3>(8,-4,84:8,50:8,0:4,D:4,S:4,F:12,"
        "((D*16)^S^(F*16)^(F:8:4)):8,1,-173)* [D:0..15,S:0..15,F:0..4095]",
    irp_source=_IRP_SOURCE_TEMPLATE.format(
        where="release 1.2.14 L511 (@c945e76 L511)",
        note="A twelve-bit function and a four-bit subdevice.",
    ),
    unit_us=_UNIT_US,
    nominal_carrier_hz=37_000,
    extent_us=None,
    bits=_BITS,
    encode=encode_denon_k,
)

SHARP_DVD = Protocol(
    name="SharpDVD",
    irp="{38k,400}<1,-1|1,-3>(8,-4,170:8,90:8,15:4,D:4,S:8,F:8,E:4,C:4,1,-48)*"
        "{C = D ^ S:4:0 ^ S:4:4 ^ F:4:0 ^ F:4:4 ^ E:4}"
        "[D:0..15,S:0..255,F:0..255,E:0..15=1]",
    irp_source=_IRP_SOURCE_TEMPLATE.format(
        where="release 1.2.14 L2500 (@c945e76 L2519)",
        note="Its documentation calls it the Kaseikyo-family member with OEM "
        "codes 170 and 90, and notes E=1 in every instance seen. Unit 400 and "
        "38 kHz, where the rest of the family is 432 and 37 kHz.",
    ),
    unit_us=400,
    nominal_carrier_hz=38_000,
    extent_us=None,
    bits=_BITS,
    encode=encode_sharp_dvd,
)

FAMILY = (PANASONIC, JVC48, FUJITSU, TEAC_K, DENON_K, SHARP_DVD)
