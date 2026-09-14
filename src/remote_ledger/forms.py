"""Forms, candidate groups, identity and precedence (D7, D16, D21, D30, D5a).

The load-bearing idea is R4's: "several forms on one key" does two unrelated
jobs, and conflating them made SPEC section 1's own BX510 example
unrepresentable.

* Forms **within** a candidate are representations of one signal. They must
  agree, and R13 checks that they do.
* Candidates **within** a key are hypotheses about which signal the device
  answers to. They are supposed to disagree.

So every form carries a ``candidate`` tag defaulting to ``primary``, and every
comparison is scoped to a group.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .errors import ValidationError
from .numeric import check_bounds, DEFAULT_GAP_US_MAX, DEFAULT_GAP_US_MIN
from .signal import IrSignal

PRIMARY = "primary"

#: R6 step 2. ``derived`` is absent deliberately: D7 excludes it from
#: selection, so it has no rank.
CONFIDENCE_RANK = {"confirmed": 0, "verified": 1, "plausible": 2, "untested": 3}
#: R6 step 3. A parametric form is what you would want to re-render from.
TYPE_RANK = {"irp": 0, "raw": 1, "pronto": 2}

DERIVED = "derived"
#: Evidence fields that assert something about a *specific* parameter set, and
#: so become false claims when a variant re-addresses the form (D22).
EVIDENCE_FIELDS = ("confidence", "source", "verifiedBy", "derivedFrom", "id")


def _as_int(value: Any, what: str) -> int:
    """Accept 17 or "0x11"; the schema permits both, the loader normalises."""
    if isinstance(value, bool):
        raise ValidationError(f"{what} is a boolean, not a number")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lower().startswith("0x"):
        try:
            return int(value, 16)
        except ValueError:
            pass
    raise ValidationError(f"{what} is {value!r}; write 17 or \"0x11\"")


@dataclass(frozen=True)
class Form:
    """One representation of one signal, as authored."""

    type: str
    candidate: str
    id: str
    confidence: str
    index: int
    key: str
    source: str | None = None
    verified_by: str | None = None
    derived_from: str | None = None
    expanded_from: dict[str, Any] | None = None
    claims: dict[str, Any] = field(default_factory=dict)
    # irp
    device: int | None = None
    subdevice: int | None = None
    function: int | None = None
    # raw
    intro: tuple[int, ...] = ()
    repeat: tuple[int, ...] = ()
    truncated: bool = False
    carrier_hz: int | None = None
    # pronto
    hex: str | None = None

    @property
    def is_derived(self) -> bool:
        return self.confidence == DERIVED

    @property
    def is_cache(self) -> bool:
        """A form carrying ``expandedFrom`` is generated, never authored (D33)."""
        return self.expanded_from is not None

    @property
    def where(self) -> str:
        return f"{self.key}:{self.id}"


def _auto_id(candidate: str, type_: str) -> str:
    return f"{candidate}.{type_}"


def load_forms(key: str, raw_forms: Sequence[dict[str, Any]]) -> list[Form]:
    """Normalise a key's ``forms`` array into :class:`Form` objects.

    Assigns D21's ids. The auto id is ``<candidate>.<type>`` and is used
    **only where it is unique within the group**: if a group holds two forms
    of one type, every form of that type must carry an explicit ``id``.
    v0.3 of the design appended ``.2``, ``.3`` in array order, which made ids
    anything but stable -- reordering two same-type forms silently retargeted
    every reference to them. Positional suffixes are gone entirely.
    """
    # Count (candidate, type) pairs so we know which auto ids are ambiguous.
    counts: dict[tuple[str, str], int] = {}
    for raw in raw_forms:
        pair = (raw.get("candidate", PRIMARY), raw["type"])
        counts[pair] = counts.get(pair, 0) + 1

    forms: list[Form] = []
    for i, raw in enumerate(raw_forms):
        candidate = raw.get("candidate", PRIMARY)
        type_ = raw["type"]
        explicit = raw.get("id")
        if explicit is None:
            if counts[(candidate, type_)] > 1:
                raise ValidationError(
                    f"{key}: candidate {candidate!r} holds "
                    f"{counts[(candidate, type_)]} {type_!r} forms, so each "
                    "needs an explicit `id`. Auto ids are "
                    "`<candidate>.<type>` and are never positional, because a "
                    "positional id silently retargets references when forms "
                    "are reordered (D21)"
                )
            form_id = _auto_id(candidate, type_)
        else:
            form_id = explicit

        forms.append(
            Form(
                type=type_,
                candidate=candidate,
                id=form_id,
                confidence=raw["confidence"],
                index=i,
                key=key,
                source=raw.get("source"),
                verified_by=raw.get("verifiedBy"),
                derived_from=raw.get("derivedFrom"),
                expanded_from=raw.get("expandedFrom"),
                claims=raw.get("claims", {}),
                device=_as_int(raw["device"], f"{key}.device") if "device" in raw else None,
                subdevice=(
                    _as_int(raw["subdevice"], f"{key}.subdevice")
                    if raw.get("subdevice") is not None else None
                ),
                function=_as_int(raw["function"], f"{key}.function") if "function" in raw else None,
                intro=tuple(raw.get("intro", ())),
                repeat=tuple(raw.get("repeat", ())),
                truncated=bool(raw.get("truncated", False)),
                carrier_hz=raw.get("carrierHz"),
                hex=raw.get("hex"),
            )
        )

    _check_ids_unique(key, forms)
    return forms


def _check_ids_unique(key: str, forms: Iterable[Form]) -> None:
    """Ids are unique within the *key*, not merely within a group."""
    seen: dict[str, Form] = {}
    for form in forms:
        if form.id in seen:
            raise ValidationError(
                f"{key}: two forms share the id {form.id!r} (positions "
                f"{seen[form.id].index} and {form.index}); ids are unique "
                "within a key (D21)"
            )
        seen[form.id] = form


def group_by_candidate(forms: Iterable[Form]) -> dict[str, list[Form]]:
    """Partition forms into candidate groups, preserving array order."""
    groups: dict[str, list[Form]] = {}
    for form in forms:
        groups.setdefault(form.candidate, []).append(form)
    return groups


def select(group: Sequence[Form]) -> Form:
    """R6/D7: the form a candidate group compiles to. Total, never a coin flip.

    1. ``derived`` forms are excluded from selection entirely -- a derived
       form is the compiler's own prior output, so it can never *be* the
       answer (D5a).
    2. Confidence, 3. form type, 4. array order. The fourth rule is what
       makes selection total; without it two same-tier same-type forms would
       be a coin flip and R12's byte-identical guarantee would not hold.
    """
    eligible = [f for f in group if not f.is_derived]
    if not eligible:
        candidate = group[0].candidate if group else "?"
        raise ValidationError(
            f"candidate group {candidate!r} holds only derived forms, so "
            "nothing can be selected from it and it cannot compile. Every "
            "group needs at least one non-derived form (D21)"
        )
    return min(
        eligible,
        key=lambda f: (CONFIDENCE_RANK[f.confidence], TYPE_RANK[f.type], f.index),
    )


def select_irp(group: Sequence[Form]) -> Form | None:
    """The group's selected *IRP* form: D7's precedence over irp forms only.

    Not simply "the D7-selected form": D7 ranks confidence before type, so a
    ``confirmed`` raw capture outranks a ``verified`` irp form, and a variant
    handed that has no ``device``/``subdevice``/``function`` to override
    (D17). Filtering first, then ranking, keeps the choice deterministic
    while guaranteeing the parent is addressable.
    """
    eligible = [f for f in group if f.type == "irp" and not f.is_derived]
    if not eligible:
        return None
    return min(eligible, key=lambda f: (CONFIDENCE_RANK[f.confidence], f.index))


def substitute_truncated_gap(
    durations: Sequence[int],
    *,
    extent_us: int | None,
    default_gap_us: int | None,
    unit_us: int,
    where: str,
) -> tuple[int, ...]:
    """D31's gap formula, for a sequence of a ``truncated`` raw form.

    The extent sits *inside* a sequence in IRP notation, so intro and repeat
    each pad to their own and are never summed. A declared-bad final space is
    **discarded, not adjusted**: a value declared untrustworthy is not
    evidence, so it contributes nothing -- not even a lower bound.
    """
    n = len(durations)
    if n == 0:
        raise ValidationError(
            f"{where}: a present sequence holds at least one duration; omit "
            "the key rather than writing [] (D31)"
        )
    m = n if n % 2 else n - 1  # durations KEPT
    head = sum(durations[:m])

    if extent_us is not None:
        gap = extent_us - head
        if gap < unit_us:
            raise ValidationError(
                f"{where}: marks and spaces already total {head} us against "
                f"an extent of {extent_us} us, leaving {gap} us -- below one "
                f"unit ({unit_us} us). Clamping would fabricate a waveform "
                "that fails the extent it claims, so this is an error rather "
                "than a clamp (D31)"
            )
    else:
        if default_gap_us is None:
            raise ValidationError(
                f"{where}: truncated, but the protocol declares no extent and "
                "the file sets no `defaultGapUs`, so there is nothing to "
                "substitute. A truncated form with neither is an error, never "
                "a guess (D4a, D24)"
            )
        check_bounds(
            "defaultGapUs", default_gap_us, DEFAULT_GAP_US_MIN, DEFAULT_GAP_US_MAX
        )
        gap = default_gap_us

    return tuple(durations[:m]) + (gap,)
