"""``rl fmt``: canonicalise hand-authored files (D9, D17, D20, D33).

Three jobs, each idempotent:

* ``--refresh`` regenerates ``derived`` forms and variant caches, so neither
  can rot. Stale is an error at check time; this is the fix.
* ``--expand`` writes variant expansions out longhand. Safe to run at any
  time because an expanded form is a cache (D33), not authority.
* Always: a declared key order, canonical hex and decimal spelling.

Arrays are never reordered here: order is semantic for ``forms`` (D7's final
tie-break is array position) and for a layout's ``areas`` (row order is the
geometry). ``--sort`` normalises *set-like* arrays only (D20).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .forms import load_forms, select, group_by_candidate
from .pronto import encode
from .remote import load_remote
from .serialize import SOURCE_KEY_ORDER, dumps, load, order_keys
from .variants import expand, merge_expansions

#: D20: set-like arrays carry no meaning in their order.
SET_LIKE = ("aliases", "controls")


def _hex(value: Any) -> Any:
    """Canonical spelling for an address: humans read `0x11`, not `17`."""
    return f"0x{value:02X}" if isinstance(value, int) and not isinstance(value, bool) else value


def format_document(path: Path, *, refresh: bool = False,
                    expand_variants: bool = False, sort: bool = False) -> str:
    doc = load(path)

    if expand_variants or refresh:
        remote = load_remote(path, expand_variants=False)
        for key, spec in doc.get("keys", {}).items():
            forms = load_forms(key, spec["forms"])
            fresh = expand(key, forms, remote.variants)
            if expand_variants:
                spec["forms"], _ = merge_expansions(spec["forms"], fresh)
            elif refresh:
                # Refresh existing caches in place, but do not create new ones.
                have = {
                    f.get("expandedFrom", {}).get("variant")
                    for f in spec["forms"] if isinstance(f.get("expandedFrom"), dict)
                }
                spec["forms"], _ = merge_expansions(
                    spec["forms"],
                    [f for f in fresh if f["expandedFrom"]["variant"] in have],
                )
        # Drop caches whose variant no longer expands (D33's premise 1).
        for spec in doc.get("keys", {}).values():
            spec["forms"] = [
                f for f in spec["forms"]
                if not isinstance(f.get("expandedFrom"), dict)
                or remote.variants.get(f["expandedFrom"]["variant"]) is not None
                and remote.variants[f["expandedFrom"]["variant"]].expands
            ]

    if refresh:
        remote = load_remote(path, expand_variants=False)
        for key, spec in doc.get("keys", {}).items():
            forms = load_forms(key, spec["forms"])
            by_id = {f.id: f for f in forms}
            for raw, form in zip(spec["forms"], forms):
                if form.is_derived and form.derived_from in by_id:
                    raw["hex"] = encode(remote.render(by_id[form.derived_from]))

    for name in SET_LIKE:
        if sort and isinstance(doc.get(name), list):
            doc[name] = sorted(doc[name])

    for spec in doc.get("keys", {}).values():
        spec["forms"] = [
            order_keys("form", {
                k: (_hex(v) if k in ("device", "subdevice", "function") else v)
                for k, v in form.items()
            })
            for form in spec["forms"]
        ]
    if isinstance(doc.get("protocol"), dict):
        doc["protocol"] = order_keys("protocol", doc["protocol"])
    for name, variant in (doc.get("variants") or {}).items():
        if isinstance(variant.get("override"), dict):
            variant["override"] = {k: _hex(v) for k, v in variant["override"].items()}
        doc["variants"][name] = order_keys("variant", variant)
    for name, layout in (doc.get("layouts") or {}).items():
        doc["layouts"][name] = order_keys("layout", layout)

    return dumps(order_keys("remote", doc), sort_keys=False)
