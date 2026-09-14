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

#: D20: set-like arrays carry no meaning in their order. `expandedFrom`'s two
#: lists are declared set-like in the schema and belong here too.
SET_LIKE = ("aliases", "controls")
SET_LIKE_NESTED = ("overridden", "inherited")


def _hex(value: Any) -> Any:
    """Canonical spelling for an address: humans read `0x11`, not `17`.

    Normalises *strings* as well as ints, so `0x1a` and `0X1A` both become
    `0x1A`. Leaving them alone meant `rl fmt` was not idempotent in spirit:
    two spellings of one value survived side by side.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return f"0x{value:02X}"
    if isinstance(value, str) and value.lower().startswith("0x"):
        try:
            return f"0x{int(value, 16):02X}"
        except ValueError:
            return value
    return value


def _canonical_pronto(text: Any) -> Any:
    """Re-emit a Pronto string in D6's canonical spelling."""
    if not isinstance(text, str):
        return text
    try:
        from .pronto import parse_words
        return " ".join(f"{w:04X}" for w in parse_words(text))
    except Exception:
        return text  # malformed: validation reports it, fmt does not mangle it


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
        # Drop every orphaned cache, not only those whose variant stopped
        # expanding. `expand()` returns exactly the set that should exist, so
        # a cache for any variant outside it is an orphan -- whether the
        # variant vanished, became metadata-only, lost its parent irp form,
        # or was superseded by an authored form in the same group (D33).
        for key, spec in doc.get("keys", {}).items():
            live = {
                f["expandedFrom"]["variant"]
                for f in expand(key, load_forms(key, spec["forms"]), remote.variants)
            }
            spec["forms"] = [
                f for f in spec["forms"]
                if not isinstance(f.get("expandedFrom"), dict)
                or f["expandedFrom"].get("variant") in live
            ]

    if refresh:
        remote = load_remote(path, expand_variants=False)
        for key, spec in doc.get("keys", {}).items():
            forms = load_forms(key, spec["forms"])
            by_id = {f.id: f for f in forms}
            for raw, form in zip(spec["forms"], forms):
                if form.is_derived and form.derived_from in by_id:
                    raw["hex"] = encode(remote.render(by_id[form.derived_from]))

    if sort:
        for name in SET_LIKE:
            if isinstance(doc.get(name), list):
                doc[name] = sorted(doc[name])

    for key, spec in doc.get("keys", {}).items():
        # Materialise auto-assigned ids, so what the file says and what the
        # loader computes are the same thing.
        for raw, form in zip(spec["forms"], load_forms(key, spec["forms"])):
            raw.setdefault("id", form.id)
        canonical = []
        for form in spec["forms"]:
            out: dict[str, Any] = {}
            for k, v in form.items():
                if k in ("device", "subdevice", "function"):
                    v = _hex(v)
                elif k == "hex":
                    v = _canonical_pronto(v)
                elif k == "expandedFrom" and isinstance(v, dict) and sort:
                    v = {
                        kk: (sorted(vv) if kk in SET_LIKE_NESTED and isinstance(vv, list) else vv)
                        for kk, vv in v.items()
                    }
                out[k] = v
            canonical.append(order_keys("form", out))
        spec["forms"] = canonical
    if isinstance(doc.get("protocol"), dict):
        doc["protocol"] = order_keys("protocol", doc["protocol"])
    for name, variant in (doc.get("variants") or {}).items():
        if isinstance(variant.get("override"), dict):
            variant["override"] = {k: _hex(v) for k, v in variant["override"].items()}
        doc["variants"][name] = order_keys("variant", variant)
    for name, layout in (doc.get("layouts") or {}).items():
        doc["layouts"][name] = order_keys("layout", layout)

    return dumps(order_keys("remote", doc), sort_keys=False)
