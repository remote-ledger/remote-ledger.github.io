"""Variant declaration and expansion (D17, D22, D23, D26, D33).

D16's candidate tag alone would make SPEC section 1's BX510 miserable to
author: its alternate subdevice applies to *every* key, so tagging forms by
hand means duplicating forty forms to change one byte. ``variants`` states it
once, at the level the fact actually lives at.

Two rules keep that from becoming a provenance laundry:

* Expansion never inherits evidence (D22). An expanded form carries **two
  distinct claims needing two distinct citations** -- where the address
  hypothesis came from, and where the inherited parameters came from.
* A form carrying ``expandedFrom`` is a regenerated cache, never an authored
  override (D33). Deleting the field is the deliberate act that transfers
  authority from the variant to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ValidationError
from .forms import EVIDENCE_FIELDS, Form, PRIMARY, _as_int, load_forms, select_irp

#: D23: one protocol per remote file in v1, so an override may not name one.
OVERRIDABLE = ("device", "subdevice", "function")


@dataclass(frozen=True)
class Variant:
    name: str
    confidence: str
    source: str
    label: str
    override: dict[str, int] | None

    @property
    def expands(self) -> bool:
        """An entry without an override declares metadata only (D26)."""
        return self.override is not None


def load_variants(raw: Mapping[str, Any] | None) -> dict[str, Variant]:
    out: dict[str, Variant] = {}
    for name, spec in (raw or {}).items():
        if name == PRIMARY:
            raise ValidationError(
                "`primary` is the default candidate group and needs no "
                "`variants` entry; its label is fixed (D26)"
            )
        override = spec.get("override")
        if override is not None:
            unknown = set(override) - set(OVERRIDABLE)
            if unknown:
                raise ValidationError(
                    f"variant {name!r} overrides {sorted(unknown)}; only "
                    f"{list(OVERRIDABLE)} may be overridden. One protocol per "
                    "remote file in v1 (D23)"
                )
            override = {
                k: _as_int(v, f"variant {name}.{k}") for k, v in override.items()
            }
        out[name] = Variant(
            name=name,
            confidence=spec["confidence"],
            source=spec["source"],
            label=spec.get("label", name),  # defaults to the candidate id (D26)
            override=override,
        )
    return out


def check_declared(key: str, forms: list[Form], variants: Mapping[str, Variant]) -> None:
    """D26: every non-``primary`` candidate tag resolves to a variants entry.

    This is what closes the labelling gap: a compiled fallback cannot exist
    without a label, because its group could not have validated without a
    declaration.
    """
    for form in forms:
        if form.candidate != PRIMARY and form.candidate not in variants:
            raise ValidationError(
                f"{key}: form {form.id!r} is tagged candidate "
                f"{form.candidate!r}, which no `variants` entry declares. "
                "Every non-primary group is declared there, with a label, a "
                "confidence and a citation (D26)"
            )


def check_cache_cardinality(key: str, forms: list[Form]) -> None:
    """D33: exactly one cache per (key, variant), one irp form per variant group.

    D21's id uniqueness is per *key*, so two forms with distinct explicit ids
    can both claim one variant and make "replace the one match" meaningless.
    """
    by_variant: dict[str, list[Form]] = {}
    for form in forms:
        if form.is_cache:
            by_variant.setdefault(form.expanded_from["variant"], []).append(form)
    for variant, caches in by_variant.items():
        if len(caches) > 1:
            ids = ", ".join(sorted(f.id for f in caches))
            raise ValidationError(
                f"{key}: {len(caches)} forms claim to be the expansion of "
                f"variant {variant!r} ({ids}); at most one may carry "
                "`expandedFrom` for a given variant (D33)"
            )

    irp_by_candidate: dict[str, list[Form]] = {}
    for form in forms:
        if form.type == "irp" and form.candidate != PRIMARY:
            irp_by_candidate.setdefault(form.candidate, []).append(form)
    for candidate, irps in irp_by_candidate.items():
        if len(irps) > 1:
            ids = ", ".join(sorted(f.id for f in irps))
            raise ValidationError(
                f"{key}: candidate {candidate!r} holds {len(irps)} irp forms "
                f"({ids}); a variant group holds at most one -- either the "
                "cache or an authored form, never both, since an authored irp "
                "form is precisely what suppresses expansion (D33)"
            )


def check_cache_premises(
    key: str, forms: list[Form], variants: Mapping[str, Variant]
) -> None:
    """D33: a cache is valid only while all of its premises still hold.

    Cardinality alone would let a cache outlive the thing it caches: delete
    the parent form, or turn a variant into a metadata-only entry, and the
    orphan still satisfies "exactly one per (key, variant)".
    """
    by_id = {f.id: f for f in forms}
    primary = [f for f in forms if f.candidate == PRIMARY]
    parent = select_irp(primary)

    for form in forms:
        if not form.is_cache:
            continue
        origin = form.expanded_from
        variant = variants.get(origin["variant"])
        if variant is None:
            raise ValidationError(
                f"{key}: form {form.id!r} caches variant "
                f"{origin['variant']!r}, which no longer exists. Run "
                "`rl fmt --refresh` to remove it (D33)"
            )
        if not variant.expands:
            raise ValidationError(
                f"{key}: form {form.id!r} caches variant {variant.name!r}, "
                "which is now metadata-only -- it carries no `override`, so it "
                "expands to nothing and this cache is stale. Run "
                "`rl fmt --refresh` to remove it (D33)"
            )
        if origin["form"] not in by_id:
            raise ValidationError(
                f"{key}: form {form.id!r} was expanded from "
                f"{origin['form']!r}, which no longer exists (D33)"
            )
        if parent is None or origin["form"] != parent.id:
            actual = parent.id if parent else "no irp form"
            raise ValidationError(
                f"{key}: form {form.id!r} was expanded from "
                f"{origin['form']!r}, but the primary group's selected irp "
                f"form is now {actual}. The cache is derived from the wrong "
                "parent. Run `rl fmt --refresh` (D33)"
            )


def _comparable(form: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a raw form dict so 0xEA and 234 compare equal."""
    out = dict(form)
    for field in OVERRIDABLE:
        if out.get(field) is not None:
            out[field] = _as_int(out[field], field)
    origin = out.get("expandedFrom")
    if isinstance(origin, dict):
        out["expandedFrom"] = {
            k: sorted(v) if isinstance(v, list) else v for k, v in origin.items()
        }
    return out


def check_cache_content(
    key: str, raw_forms: list[dict], variants: Mapping[str, Variant]
) -> None:
    """D33 premise 4: the cache must match what expansion recomputes.

    This is the check that makes the other three premises worth having, and
    it was the one missing: loading *replaced* a stale cache before anything
    compared it, so a hand-edited cached subdevice passed both `rl validate`
    and `rl check` with the edit silently discarded. A cache that can be
    edited without complaint is not a cache, it is an unchecked fork of the
    variant.
    """
    fresh = {
        f["expandedFrom"]["variant"]: f
        for f in expand(key, load_forms(key, raw_forms), variants)
    }
    for raw in raw_forms:
        origin = raw.get("expandedFrom")
        if not isinstance(origin, dict):
            continue
        expected = fresh.get(origin.get("variant"))
        if expected is None:
            continue  # the premise checks report the orphan
        if _comparable(raw) != _comparable(expected):
            raise ValidationError(
                f"{key}: form {raw.get('id', '?')!r} caches variant "
                f"{origin['variant']!r}, but recomputing that expansion gives "
                "different content -- the cache has been edited by hand or the "
                "variant has changed. A form carrying `expandedFrom` is a "
                "regenerated cache, not an authored override; delete that "
                "field to take authority, or run `rl fmt --refresh` (D33)"
            )


def expand(key: str, forms: list[Form], variants: Mapping[str, Variant]) -> list[dict]:
    """Recompute every variant expansion for one key, as raw form dicts.

    Expansion draws from the ``primary`` group only -- there are no variants
    of variants in v1 -- and copies the group's **selected IRP form**. A key
    whose primary group has no irp form is skipped silently: a raw-only key
    has no address for a variant to re-target.
    """
    primary = [f for f in forms if f.candidate == PRIMARY]
    parent = select_irp(primary)
    if parent is None:
        return []

    authored_irp = {
        f.candidate for f in forms
        if f.type == "irp" and f.candidate != PRIMARY and not f.is_cache
    }

    out: list[dict] = []
    for variant in variants.values():
        if not variant.expands or variant.name in authored_irp:
            continue  # an authored form is what suppresses expansion (D17)
        out.append(expanded_form(parent, variant))
    return out


def expanded_form(parent: Form, variant: Variant) -> dict[str, Any]:
    """One expansion, with D22's field partition made explicit.

    ``verifiedBy`` is stripped rather than carried because a check that held
    for the original address -- an NEC complement relation, say -- is simply
    *false* at an arbitrary alternate one. Dropping the parent's citation
    outright would be the opposite error, discarding the provenance of the
    fields the variant did not touch, so ``expandedFrom`` records it instead.
    """
    params = {"device": parent.device, "subdevice": parent.subdevice,
              "function": parent.function}
    overridden = sorted(variant.override or ())
    inherited = sorted(k for k, v in params.items() if v is not None and k not in overridden)
    params.update(variant.override or {})

    form: dict[str, Any] = {
        "id": f"{variant.name}.irp",
        "type": "irp",
        "candidate": variant.name,
        "device": params["device"],
        "function": params["function"],
        "confidence": variant.confidence,   # never inherited (D17)
        "source": variant.source,
        "expandedFrom": {
            "variant": variant.name,
            "form": parent.id,
            "overridden": overridden,
            "inherited": inherited,
        },
    }
    if params["subdevice"] is not None:
        form["subdevice"] = params["subdevice"]
    return form


def merge_expansions(
    raw_forms: list[dict], expansions: list[dict]
) -> tuple[list[dict], list[str]]:
    """D33: replace a matching cache **in place**, append only when absent.

    Exactly one of the two happens. The naive implementation -- always
    append -- would give a key two forms for one variant after the first
    ``--expand``, doubling on every run.
    """
    merged = list(raw_forms)
    changed: list[str] = []
    positions = {
        f.get("expandedFrom", {}).get("variant"): i
        for i, f in enumerate(merged)
        if isinstance(f.get("expandedFrom"), dict)
    }
    for fresh in expansions:
        variant = fresh["expandedFrom"]["variant"]
        at = positions.get(variant)
        if at is None:
            merged.append(fresh)
            changed.append(f"+{variant}")
        elif merged[at] != fresh:
            merged[at] = fresh
            changed.append(f"~{variant}")
    return merged, changed
