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
``Sony20`` and ``Samsung32`` land in Phase 3 with the seed data.
"""

from __future__ import annotations

from .base import Protocol
from .nec import NEC1

REGISTRY: dict[str, Protocol] = {p.name: p for p in (NEC1,)}

__all__ = ["Protocol", "REGISTRY", "NEC1"]


def get(name: str) -> Protocol:
    from ..errors import ValidationError

    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise ValidationError(
            f"unknown protocol {name!r}; the v1 registry holds {known} (D18)"
        ) from None
