"""The ``Protocol`` record (D3).

A registry entry is a record with **machine-readable metadata**, not just a
function. Two fields exist specifically so that validation reads fields rather
than prose:

* ``extent_us`` lets D31 substitute a truncated capture's gap without parsing
  an IRP string or a docstring.
* ``irp_source`` turns D18's first gate ("its IRP string, with the source it
  came from") from a convention into a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol as _Proto

from ..signal import IrSignal


class Encoder(_Proto):
    def __call__(
        self,
        *,
        device: int,
        subdevice: int | None,
        function: int,
        carrier_hz: int,
        unit_us: int | None = None,
    ) -> IrSignal: ...


@dataclass(frozen=True)
class Protocol:
    name: str
    #: The IRP string, verbatim.
    irp: str
    #: Where the IRP string came from. D18 gate 1; a registry entry with an
    #: empty ``irp_source`` fails the suite.
    irp_source: str
    unit_us: int
    #: INFORMATIONAL ONLY. ``protocol.carrierHz`` in a remote file is required
    #: and authoritative (D3); this records what the IRP definition says, for
    #: documentation and to prefill a new file. Never consulted at compile
    #: time -- renamed from ``default_carrier_hz`` precisely because that
    #: prefix invited the confusion.
    nominal_carrier_hz: int
    #: ``^108m`` -> 108_000. ``None`` when the protocol declares no extent,
    #: which is what sends D31 down its ``defaultGapUs`` branch.
    extent_us: int | None
    bits: int
    encode: Encoder

    def __post_init__(self) -> None:
        if not self.irp.strip():
            raise ValueError(f"{self.name}: D18 gate 1 requires an IRP string")
        if not self.irp_source.strip():
            raise ValueError(
                f"{self.name}: D18 gate 1 requires a source for the IRP string"
            )
