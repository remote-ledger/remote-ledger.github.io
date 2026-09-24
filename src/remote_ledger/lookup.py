"""``rl lookup`` (R16, R20).

Minimum viable by design: works offline, needs no hosting, ships first. It
answers the question SPEC section 1 opens with -- *people know their device,
not the remote model that shipped with it* -- by searching manufacturer,
model, aliases and controls at once.

R20 is the part worth getting right. A lookup has **three** answers, not
two, and the middle one is why `unresolved.json` exists: a device someone
spent an afternoon failing to find must not read the same as one nobody has
ever typed in, or that afternoon gets repeated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

#: R6's order, best first, for display.
TIER_ORDER = ("confirmed", "verified", "plausible", "untested", "derived")


@dataclass(frozen=True)
class Match:
    kind: str          # "remote" | "unresolved"
    entry: dict[str, Any]
    matched_on: str    # which field the query hit


def _fields(remote: dict[str, Any]) -> Iterable[tuple[str, str]]:
    yield "manufacturer", remote["manufacturer"]
    yield "model", remote["model"]
    for alias in remote.get("aliases", []):
        yield "alias", alias
    for device in remote.get("controls", []):
        yield "controls", device


_NOT_ALNUM = re.compile(r"[\W_]+")


def normalise(text: str) -> str:
    """Lower case with every space and punctuation mark removed.

    Model numbers are written every way at once: a user types
    ``BDP-S360``, an upstream header says ``SONY BLU RAY BDP S360``, a label
    reads ``BDP.S360``. Plain substring search missed that, and a miss is
    worse than it looks: it reports R20's third state, *nobody has looked*,
    about a device that is in the ledger. Removing separators on both sides
    makes all three spellings the same string. The site's ``norm()`` applies
    the same rule.
    """
    return _NOT_ALNUM.sub("", text.lower())


def _matches(query: str, values: list[str]) -> bool:
    """The whole query inside one value, or else every word of it inside
    some value -- each value normalised on its own, so that no match can
    straddle two fields (``sony`` + ``RM`` must not answer ``nyrm``)."""
    needle = normalise(query)
    if not needle:
        return False
    norm = [normalise(v) for v in values]
    if any(needle in v for v in norm):
        return True
    words = [w for w in (normalise(w) for w in query.split()) if w]
    return len(words) > 1 and all(any(w in v for v in norm) for w in words)


def search(index: dict[str, Any], query: str) -> list[Match]:
    """Search every identifying field, ignoring case, spaces and punctuation."""
    if not normalise(query):
        return []

    matches: list[Match] = []
    for remote in index.get("remotes", []):
        fields = list(_fields(remote))
        needle = normalise(query)
        hit = next(
            (field for field, value in fields if needle in normalise(value)),
            None,
        )
        # A query naming both maker and model ("Sony BDP-BX510") is inside
        # neither field alone, so fall back to matching every word.
        if hit is None and _matches(query, [v for _, v in fields]):
            hit = "combined"
        if hit:
            matches.append(Match("remote", remote, hit))

    for entry in index.get("unresolved", []):
        if _matches(query, [entry["device"]]):
            matches.append(Match("unresolved", entry, "device"))

    return matches


def _render_remote(
    remote: dict[str, Any], matched_on: str, keys: Mapping[str, Any]
) -> list[str]:
    """``keys`` is the remote's compiled artifact's key map: since D40 the
    index carries identity and roll-ups only, and per-key detail lives in
    each remote's own artifact."""
    lines = [
        f"{remote['manufacturer']} {remote['model']}"
        + (f"  [{remote['confidence']}]" if remote.get("confidence") else "")
    ]
    lines.append(f"  file      {remote['file']}")
    if remote.get("protocol"):
        lines.append(f"  protocol  {remote['protocol']}")
    if remote.get("aliases"):
        lines.append(f"  aliases   {', '.join(remote['aliases'])}")
    if remote.get("controls"):
        lines.append(f"  controls  {', '.join(remote['controls'])}")
    if remote.get("importedFrom"):
        lines.append(
            f"  imported  from {remote['importedFrom']} -- not authored here; "
            "no key above plausible (SPEC R19)"
        )
    if remote.get("unresolvedAlternates"):
        lines.append(
            f"  open      {remote['unresolvedAlternates']} untested "
            "alternate candidate(s) -- see the keys below"
        )
    lines.append(f"  matched   on {matched_on}")

    for key in sorted(keys):
        candidates = keys[key]["candidates"]
        lines.append(f"  {key}")
        for name in sorted(candidates, key=lambda n: (n != "primary", n)):
            entry = candidates[name]
            label = entry.get("label", name)
            tier = entry["confidence"]
            marker = " " if name == "primary" else "-"
            lines.append(f"    {marker} {label:<18} {tier}")
            if entry.get("source"):
                lines.append(f"        {entry['source']}")
    return lines


def keys_for(root: Path, matches: list[Match]) -> dict[str, Any]:
    """Each matched remote's per-key detail, compiled from its file (D40).

    Compiled here rather than read from ``build/``, so a lookup never shows a
    code the committed tree has not caught up with yet.
    """
    from .cli import compiled_artifact
    from .remote import load_remote

    return {
        m.entry["file"]: compiled_artifact(load_remote(root / m.entry["file"]))["keys"]
        for m in matches if m.kind == "remote"
    }


def _render_unresolved(entry: dict[str, Any]) -> list[str]:
    """R20's middle state, stated out loud."""
    return [
        f"{entry['device']}  [checked, nothing found]",
        f"  checked   {entry['checked']}",
    ]


def render(
    matches: list[Match], query: str,
    keys: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """``keys`` maps a remote's ``file`` to its artifact's key map."""
    keys = keys or {}
    if not matches:
        # R20's third state. Deliberately distinct from the second: nobody
        # has looked, as opposed to somebody looked and failed.
        return (
            f"No entry for {query!r}, and nothing recorded as having been "
            "searched for it either -- so this is a device nobody has looked "
            "up yet, not one checked and found to have no known remote."
        )
    blocks = []
    for match in matches:
        if match.kind == "remote":
            blocks.append("\n".join(_render_remote(
                match.entry, match.matched_on, keys.get(match.entry["file"], {}),
            )))
        else:
            blocks.append("\n".join(_render_unresolved(match.entry)))
    return "\n\n".join(blocks)
