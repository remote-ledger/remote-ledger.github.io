"""The generated index (R14, D13, OD4).

"Automatic" means the index is computed from the files, never authored
separately -- there is no second copy of "which remote goes with which
device" to keep in sync by hand. OD4 chose to *commit* it anyway, for
diffability and so it can be browsed without running anything; D19's
whole-tree check is what keeps those two facts compatible.

It also folds in ``unresolved.json``, which is what gives a lookup R20's
three distinguishable answers instead of two.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths
from .errors import LedgerError
from .forms import CONFIDENCE_RANK, PRIMARY, select
from .remote import Remote, load_remote
from .validate import corpus_files

UNRESOLVED = "unresolved.json"


def rolled_up_confidence(remote: Remote) -> str | None:
    """The **weakest** primary-group tier across a remote's keys.

    Weakest rather than best, deliberately: a file is only as trustworthy as
    the button you happen to press, and rolling up the best tier would let
    one verified Power key vouch for forty untested ones.
    """
    tiers = []
    for key in remote.keys:
        groups = remote.groups(key)
        if PRIMARY in groups:
            tiers.append(select(groups[PRIMARY]).confidence)
    if not tiers:
        return None
    return max(tiers, key=lambda t: CONFIDENCE_RANK[t])


def summarise(remote: Remote, where: str | None = None) -> dict[str, Any]:
    """One remote's identity and roll-ups -- and, since D40, nothing per key.

    The per-key detail (each candidate's tier, citation and Pronto) already
    lives in the remote's own compiled artifact, which the summary names.
    Repeating it here made the index grow with every key in the corpus: at
    the LIRC import's ~115k keys, a 40 MB file rewritten by any change.
    """
    alternates = 0
    for key in remote.keys:
        for name, group in remote.groups(key).items():
            # D32's risk note: make open questions countable rather than
            # letting untested alternates accumulate silently.
            if name != PRIMARY and select(group).confidence in ("untested", "plausible"):
                alternates += 1

    where = where or remote.where
    summary: dict[str, Any] = {
        "file": where,
        "artifact": paths.artifact(where),
        "manufacturer": remote.manufacturer,
        "model": remote.model,
        "aliases": sorted(remote.raw.get("aliases") or []),
        "controls": sorted(remote.raw.get("controls") or []),
        "keyCount": len(remote.keys),
        "unresolvedAlternates": alternates,
    }
    if root := paths.imported_from(where):
        summary["importedFrom"] = root
    if remote.protocol.name:
        summary["protocol"] = remote.protocol.name
    confidence = rolled_up_confidence(remote)
    if confidence:
        summary["confidence"] = confidence
    return summary


def alias_conflicts(summaries: list[dict[str, Any]]) -> list[str]:
    """R15: two files claiming the same alias is surfaced, not allowed.

    A remote is identified by its model *or* any of its aliases -- R2 says
    aliases name the identical physical remote -- so a name claimed twice
    makes a lookup ambiguous in exactly the way this format exists to
    prevent.

    **Within one manufacturer** (SPEC v0.9). A model name is only ever
    unique inside a maker's own catalogue, as R1's ``<manufacturer>/<model>``
    path already says: Apple's ``CD`` and Pioneer's ``CD`` are two remotes,
    not one remote claimed twice. The global check was written against
    three files and held; the LIRC import has 55 such pairs.
    """
    claims: dict[tuple[str, str], list[str]] = {}
    for summary in summaries:
        maker = summary["manufacturer"].casefold()
        for name in {summary["model"], *summary["aliases"]}:
            claims.setdefault((maker, name.casefold()), []).append(summary["file"])
    return [
        f"{name!r} is claimed by {len(files)} {maker!r} files: {', '.join(sorted(files))}"
        for (maker, name), files in sorted(claims.items())
        if len(files) > 1
    ]


def load_unresolved(root: Path) -> list[dict[str, Any]]:
    path = root / UNRESOLVED
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8"))
    return sorted(entries, key=lambda e: e["device"])


def build_index(root: Path) -> tuple[dict[str, Any], list[str]]:
    """Compute the index from the files. Returns (index, problems)."""
    summaries = []
    problems: list[str] = []
    for path in corpus_files(root):
        try:
            summaries.append(summarise(load_remote(path), paths.rel(root, path)))
        except LedgerError as exc:
            problems.append(f"{path.as_posix()}: {exc}")
    summaries.sort(key=lambda s: (s["manufacturer"].casefold(), s["model"].casefold()))
    problems += alias_conflicts(summaries)
    return (
        {
            "schemaVersion": 1,
            "remotes": summaries,
            "unresolved": load_unresolved(root),
        },
        problems,
    )
