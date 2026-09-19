"""The protocol registry (D3, D18).

**The v1 registry is exactly what the seed data proves.** Adding a protocol
is a self-contained change requiring all three of: its IRP string in the
module with the source it came from; at least one *independently cited*
golden vector (D10); and an invariant test. The gate applies to protocols
that look like trivial variations of one already present -- ``NEC2`` and
``NEC`` are a few lines' difference from ``NEC1``, and waving them through on
that basis is precisely how an unverified encoder ships.

Backlog, each blocked on that gate and none scheduled: ``NEC2``, ``NEC``
(``S`` defaulted to ``~D``), ``Sony12``, ``Sony15``, ``RC5``, ``RC6``.

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
from .nec import NEC1
from .sony import SONY20

REGISTRY: dict[str, Protocol] = {p.name: p for p in (NEC1, SONY20)}

__all__ = ["Protocol", "REGISTRY", "NEC1", "SONY20"]


def get(name: str) -> Protocol:
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise ValidationError(
            f"unknown protocol {name!r}; the v1 registry holds {known} (D18)"
        ) from None
