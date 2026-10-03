"""The protocol registry (D3, D18).

**The v1 registry is exactly what the seed data proves.** Adding a protocol
is a self-contained change requiring all three of: its IRP string in the
module with the source it came from; at least one *independently cited*
golden vector (D10); and an invariant test. The gate applies to protocols
that look like trivial variations of one already present -- ``NEC2`` and
``NEC`` are a few lines' difference from ``NEC1``, and waving them through on
that basis is precisely how an unverified encoder ships.

Backlog, each blocked on that gate and none scheduled: ``NEC``
(``S`` defaulted to ``~D``), ``Sony12``, ``Sony15``, ``RC6``.

``NEC2`` and ``NECx1`` left the backlog for the SwiftRemote import: each met
the gate with IrpTransmogrifier's IRP string, a vector rendered by its 1.2.14
release, and an invariant test.

``RC5`` left the backlog once the Meridian MSR needed it (DESIGN section 16).

``RC6`` (mode 0 only), ``RCA-38`` and ``Thomson7`` joined for the SwiftRemote
database import, each through the same three gates (NOTES/philips.md).

``Samsung32`` is **not** backlogged -- it does not exist. D18's table carried
an IRP string for it that was written from memory during design and never
checked against a source; two reads of DecodeIR and one of
IrpTransmogrifier's database find no 32-bit Samsung protocol with the fields
``D:8,S:8,F:8,~F:8``. What does exist is ``Samsung20``
(``{38.4k,564}...(8,-8,D:6,S:6,F:8,1,...)``) and ``Samsung36``
(``{38k,500}...(9,-9,...)``). Identifying which the BN59-01199F actually
speaks is open work, recorded in ``unresolved.json``.
"""

from __future__ import annotations

from ..errors import ValidationError
from .base import Protocol
from .nec import NEC1, NECX2
from .nec import NEC2, NECX1
from .rc5 import RC5
from .sony import SONY12, SONY15, SONY20

REGISTRY: dict[str, Protocol] = {p.name: p for p in (NEC1, NECX2, RC5, SONY20)}
REGISTRY.update({p.name: p for p in (NEC2, NECX1)})

# --- the japan family (SwiftRemote DB import): Pioneer-2Part, JVC, Sharp, Denon
from .denon import DENON  # noqa: E402
from .jvc import JVC  # noqa: E402
from .pioneer import PIONEER_2PART  # noqa: E402
from .sharp import SHARP  # noqa: E402

REGISTRY.update({p.name: p for p in (PIONEER_2PART, JVC, SHARP, DENON)})

__all__ = ["Protocol", "REGISTRY", "NEC1", "NECX2", "RC5", "SONY20"]
__all__ += ["NEC2", "NECX1"]

# Sony SIRC's other two widths, each through D18's three gates (tests/vectors/CITATIONS.md).
REGISTRY.update({p.name: p for p in (SONY12, SONY15)})
__all__ += ["SONY12", "SONY15"]

# --- philips family (SwiftRemote DB import) ---
from .rc6 import RC6  # noqa: E402
from .rca38 import RCA38  # noqa: E402
from .thomson7 import THOMSON7  # noqa: E402

REGISTRY.update({p.name: p for p in (RC6, RCA38, THOMSON7)})
__all__ += ["RC6", "RCA38", "THOMSON7"]
__all__ += ["PIONEER_2PART", "JVC", "SHARP", "DENON"]


def get(name: str) -> Protocol:
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise ValidationError(
            f"unknown protocol {name!r}; the v1 registry holds {known} (D18)"
        ) from None
