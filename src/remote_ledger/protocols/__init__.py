"""The protocol registry (D3, D18).

**The v1 registry is exactly what the seed data proves.** Adding a protocol
is a self-contained change requiring all three of: its IRP string in the
module with the source it came from; at least one *independently cited*
golden vector (D10); and an invariant test. The gate applies to protocols
that look like trivial variations of one already present -- ``NEC2`` and
``NEC`` are a few lines' difference from ``NEC1``, and waving them through on
that basis is precisely how an unverified encoder ships.

Backlog, each blocked on that gate and none scheduled: ``NEC``
(``S`` defaulted to ``~D``).

``RC5`` left the backlog once the Meridian MSR needed it (DESIGN section 16).
Every other protocol below the first four joined for the SwiftRemote database
import (DESIGN section 18), each through the same three gates: ``NEC2``,
``NECx1``, ``Sony12``, ``Sony15``, ``RC6`` (mode 0 only), ``RCA-38``,
``Thomson7``, ``Pioneer-2Part``, ``JVC``, ``Sharp``, ``Denon``, ``Samsung36``,
``Proton``, ``F12_relaxed``, ``RECS80``, ``RECS80-0068``, ``Aiwa``,
``Blaupunkt``, ``Panasonic``, ``JVC-48``, ``Fujitsu``, ``Teac-K``, ``Denon-K``
and ``SharpDVD``.

``Samsung32`` is **not** backlogged -- it does not exist. D18's table carried
an IRP string for it that was written from memory during design and never
checked against a source; two reads of DecodeIR and one of
IrpTransmogrifier's database find no 32-bit Samsung protocol with the fields
``D:8,S:8,F:8,~F:8``. The BN59-01199F speaks NECx2 (IRDB, device 7, subdevice
7). ``Samsung36`` (``{37.9k,560,33%}``, a ``4500u`` header) is a different
protocol and joined with the database import (DESIGN D65).
"""

from __future__ import annotations

from ..errors import ValidationError
from .aiwa import AIWA
from .base import Protocol
from .blaupunkt import BLAUPUNKT
from .denon import DENON
from .f12 import F12_RELAXED
from .jvc import JVC
from .kaseikyo import DENON_K, FUJITSU, JVC48, PANASONIC, SHARP_DVD, TEAC_K
from .nec import NEC1, NEC2, NECX1, NECX2
from .pioneer import PIONEER_2PART
from .proton import PROTON
from .rc5 import RC5
from .rc6 import RC6
from .rca38 import RCA38
from .recs80 import RECS80, RECS80_0068
from .samsung36 import SAMSUNG36
from .sharp import SHARP
from .sony import SONY12, SONY15, SONY20
from .thomson7 import THOMSON7

_PROTOCOLS = (
    NEC1, NECX2, RC5, SONY20,
    NEC2, NECX1, SONY12, SONY15, RC6, RCA38, THOMSON7,
    PIONEER_2PART, JVC, SHARP, DENON,
    SAMSUNG36, PROTON, F12_RELAXED, RECS80, RECS80_0068,
    AIWA, BLAUPUNKT, PANASONIC, JVC48, FUJITSU, TEAC_K, DENON_K, SHARP_DVD,
)

REGISTRY: dict[str, Protocol] = {p.name: p for p in _PROTOCOLS}

__all__ = [
    "Protocol", "REGISTRY",
    "NEC1", "NECX2", "RC5", "SONY20",
    "NEC2", "NECX1", "SONY12", "SONY15", "RC6", "RCA38", "THOMSON7",
    "PIONEER_2PART", "JVC", "SHARP", "DENON",
    "SAMSUNG36", "PROTON", "F12_RELAXED", "RECS80", "RECS80_0068",
    "AIWA", "BLAUPUNKT", "PANASONIC", "JVC48", "FUJITSU", "TEAC_K", "DENON_K", "SHARP_DVD",
]


def get(name: str) -> Protocol:
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise ValidationError(
            f"unknown protocol {name!r}; the v1 registry holds {known} (D18)"
        ) from None
