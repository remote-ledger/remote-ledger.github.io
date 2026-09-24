"""The remote document: protocol, variants, keys, and form rendering.

Ties D16's grouping, D17's expansion and D3's registry together, and is where
every form becomes an :class:`IrSignal` -- the single intermediate
representation that makes cross-checking a stored Pronto string against a
parametric form the same code path as checking two parametric forms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import protocols, pronto
from .errors import ValidationError
from .forms import Form, PRIMARY, group_by_candidate, load_forms, select, substitute_truncated_gap
from .serialize import load
from .signal import IrSignal
from .variants import Variant, expand, load_variants, merge_expansions


@dataclass(frozen=True)
class Protocol:
    """The file's ``protocol`` block (R3, D23, D24)."""

    carrier_hz: int
    min_sends: int
    name: str | None = None
    unit_us: int | None = None
    default_gap_us: int | None = None
    tolerance: dict[str, Any] = field(default_factory=dict)
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def entry(self):
        """The registry record, or None for an unidentified capture (D24)."""
        return protocols.REGISTRY.get(self.name) if self.name else None

    @property
    def extent_us(self) -> int | None:
        entry = self.entry
        return entry.extent_us if entry else None

    @property
    def effective_unit_us(self) -> int:
        """``protocol.unitUs`` if given, else the registry's (D31)."""
        if self.unit_us is not None:
            return self.unit_us
        entry = self.entry
        if entry is None:
            raise ValidationError(
                "no protocol name and no unitUs, so there is no unit to work "
                "from (D24)"
            )
        return entry.unit_us


@dataclass
class Remote:
    path: Path
    manufacturer: str
    model: str
    protocol: Protocol
    keys: dict[str, list[Form]]
    variants: dict[str, Variant]
    raw: dict[str, Any]

    @property
    def where(self) -> str:
        """Repo-relative, always.

        D20 forbids a non-reproducible value in any generated file, and an
        absolute path is one: it differs between a laptop and CI, so
        ``build/warnings.json`` would drift on every machine and
        ``rl build --check`` would fail for no reason.
        """
        path = self.path
        if path.is_absolute():
            try:
                return path.relative_to(Path.cwd()).as_posix()
            except ValueError:
                return path.name
        return path.as_posix()

    def groups(self, key: str) -> dict[str, list[Form]]:
        return group_by_candidate(self.keys[key])

    def render(self, form: Form) -> IrSignal:
        """One form to an IrSignal. Derived forms never reach here (D5a)."""
        if form.is_derived:
            raise ValidationError(
                f"{form.where}: a derived form is never rendered; it is "
                "validated solely by regenerating it and comparing the "
                "canonical string (D5a, D9)"
            )
        if form.type == "irp":
            return self._render_irp(form)
        if form.type == "raw":
            return self._render_raw(form)
        return pronto.decode(form.hex, carrier_hz=self.protocol.carrier_hz)

    def _render_irp(self, form: Form) -> IrSignal:
        entry = self.protocol.entry
        if entry is None:
            raise ValidationError(
                f"{form.where}: an irp form needs protocol.name, which this "
                "file omits (D24)"
            )
        return entry.encode(
            device=form.device,
            subdevice=form.subdevice,
            function=form.function,
            carrier_hz=self.protocol.carrier_hz,
            unit_us=self.protocol.unit_us,
        )

    def _render_raw(self, form: Form) -> IrSignal:
        sequences: dict[str, tuple[int, ...]] = {}
        for name in ("intro", "repeat"):
            durations = getattr(form, name)
            if not durations:
                sequences[name] = ()
                continue
            if form.truncated:
                durations = substitute_truncated_gap(
                    durations,
                    extent_us=self.protocol.extent_us,
                    default_gap_us=self.protocol.default_gap_us,
                    unit_us=self.protocol.effective_unit_us,
                    where=f"{form.where}.{name}",
                )
            sequences[name] = tuple(durations)
        return IrSignal(
            carrier_hz=form.carrier_hz or self.protocol.carrier_hz,
            intro=sequences["intro"],
            repeat=sequences["repeat"],
        )

    def compile_group(self, key: str, candidate: str) -> str:
        """The Pronto string one candidate group compiles to (R12)."""
        return pronto.encode(self.render(select(self.groups(key)[candidate])))


def load_remote(path: Path, *, expand_variants: bool = True) -> Remote:
    """Parse and normalise a remote file.

    Variant expansion happens at load, so the compiler and cross-checker only
    ever see candidate-tagged forms: one mechanism in the engine, one
    shorthand at the authoring layer (D17).
    """
    return remote_from_doc(load(path), path, expand_variants=expand_variants)


def remote_from_doc(
    doc: dict[str, Any], path: Path, *, expand_variants: bool = True
) -> Remote:
    """:func:`load_remote` for a document already in memory.

    The importer (D36) builds a candidate file and cross-checks it before
    deciding what to write, so it needs the engine without a file on disk.
    ``path`` is where the document would live; it only feeds ``where``.
    """
    proto_raw = doc.get("protocol", {})
    protocol = Protocol(
        carrier_hz=proto_raw["carrierHz"],
        min_sends=proto_raw["minSends"],
        name=proto_raw.get("name"),
        unit_us=proto_raw.get("unitUs"),
        default_gap_us=proto_raw.get("defaultGapUs"),
        tolerance=proto_raw.get("tolerance", {}),
        claims=proto_raw.get("claims", {}),
    )
    variants = load_variants(doc.get("variants"))

    keys: dict[str, list[Form]] = {}
    for key, spec in doc.get("keys", {}).items():
        raw_forms = spec["forms"]
        if expand_variants:
            fresh = expand(key, load_forms(key, raw_forms), variants)
            raw_forms, _ = merge_expansions(raw_forms, fresh)
        keys[key] = load_forms(key, raw_forms)

    return Remote(
        path=path,
        manufacturer=doc["manufacturer"],
        model=doc["model"],
        protocol=protocol,
        keys=keys,
        variants=variants,
        raw=doc,
    )
