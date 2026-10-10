"""RCA, the 58 kHz original of the frame ``rca38.py`` carries at 38.7 kHz.

IRP, verbatim from IrpTransmogrifier's protocol database::

    {58k,460,msb}<1,-2|1,-4>(8,-8,D:4,F:8,~D:4,~F:8,1,-16)*[D:0..15,F:0..255]

The frame is ``RCA-38``'s, field for field and duration for duration (see ``rca38.py``); the two differ only in the
carrier. IrpTransmogrifier's documentation says RCA and ``RCA(Old)`` "are so similar that most RCA devices will accept
either", and that ``RCA(Old)`` has an extended lead-in and a double-length stop mark; ``RCA(Old)`` (``[40][8]``,
``2``, a ``+`` repeat) is **not** this entry and stays unregistered. The ledger's ``function`` is F, ``device`` is D and
a ``subdevice`` is an error.

The nominal carrier is the IRP's 58 kHz. Remotes of the family have been measured at 57.3 kHz (IrpTransmogrifier's
capture of an ``RCA(Old)`` remote) and the JP1 ``RCA`` executor sends 56 kHz: a file's ``carrierHz`` is authoritative
(D3) and the receivers take both.
"""

from __future__ import annotations

from ..signal import IrSignal
from .base import Protocol
from .rca38 import encode_frame

_IRP = "{58k,460,msb}<1,-2|1,-4>(8,-8,D:4,F:8,~D:4,~F:8,1,-16)*[D:0..15,F:0..255]"
_IRP_SOURCE = (
    "IrpTransmogrifier src/main/resources/IrpProtocols.xml @c945e76 L2231 (bengtmartensson/IrpTransmogrifier), "
    "protocol `RCA`, verbatim, and identical in the 1.2.14 release (`list -i RCA`). Its documentation says RCA and "
    "RCA(Old) differ only in the lead-in and the stop mark. The field layout and bit timings are confirmed by hardware "
    "captures of the same frame at 38.7 kHz (`RCA-38`) and of RCA(Old) at 57.3 kHz in IrpTransmogrifier's own test "
    "data (see tests/vectors/CITATIONS.md)."
)
_UNIT_US = 460


def encode(
    *,
    device: int,
    subdevice: int | None,
    function: int,
    carrier_hz: int,
    unit_us: int | None = None,
) -> IrSignal:
    return encode_frame("RCA", device=device, subdevice=subdevice, function=function,
                        carrier_hz=carrier_hz, unit_us=unit_us)


RCA = Protocol(
    name="RCA",
    irp=_IRP,
    irp_source=_IRP_SOURCE,
    unit_us=_UNIT_US,
    nominal_carrier_hz=58_000,
    extent_us=None,
    bits=24,
    encode=encode,
)
