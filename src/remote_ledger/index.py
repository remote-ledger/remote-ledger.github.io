"""The generated index (R14, D13, OD4).

"Automatic" means the index is computed from the files, never authored
separately -- there is no second copy of "which remote goes with which
device" to keep in sync by hand. OD4 chose to *commit* it anyway, for
diffability and so it can be browsed without running anything; D19's
whole-tree check is what keeps those two facts compatible.

It also folds in ``unresolved.json``, which is what gives a lookup R20's
three distinguishable answers instead of two.

**Shards (D57).** ``index.json`` is read in the field by a shipped app that
downloads the whole file on every search, so it must not grow with the
imports. A root in ``paths.SHARDED`` is therefore indexed in separate files,
``index/<name>/<key>.json``, one per initial letter of the manufacturer, with
the same entry shape as ``index.json``'s ``remotes``; ``index.json`` gains one
small ``shards`` entry per root and nothing else, and none at all while there
is no shard, so a corpus without an imported database indexes byte for byte as
before. A search that needs the whole ledger reads the index *and* every part
(:func:`merge`): the entries' ``controls`` name other makers' devices, so no
part can be skipped, and "not in the ledger" is only true across all of them.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .errors import LedgerError
from .forms import CONFIDENCE_RANK, PRIMARY, select
from .remote import Remote, load_remote
from .serialize import dumps
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


@dataclass(frozen=True)
class Shard:
    """The index entries of one sharded import root (D57)."""

    name: str
    root: str
    #: part key -> its entries, each list in index order; keys in sorted order.
    parts: dict[str, list[dict[str, Any]]]

    @property
    def remotes(self) -> int:
        return sum(len(entries) for entries in self.parts.values())


@dataclass(frozen=True)
class Built:
    #: What ``index.json`` holds: every remote outside the shards.
    index: dict[str, Any]
    shards: tuple[Shard, ...]
    problems: list[str]


def shard_key(manufacturer: str) -> str:
    """Which part of its shard a manufacturer's remotes are listed in.

    The first letter or digit of the name: an ASCII letter as its lower-case
    self, an ASCII digit as ``0``, and anything else -- a first character
    outside ASCII, or a name with nothing alphanumeric in it -- as ``_``.
    Leading ASCII spaces and punctuation are skipped, as a search skips them.
    ASCII only, as D49's key folding is: ``str.casefold`` and ``str.isalnum``
    move with the interpreter's Unicode tables, and a regenerated part must
    not.

    A remote stays in the same part until its manufacturer changes, so a
    regeneration rewrites only the parts whose brands it touched, and a
    client holding the others can keep them.
    """
    for ch in manufacturer:
        if "a" <= ch <= "z":
            return ch
        if "A" <= ch <= "Z":
            return ch.lower()
        if "0" <= ch <= "9":
            return "0"
        if ch.isascii():
            continue  # ASCII space or punctuation
        return "_"
    return "_"


def _shard(name: str, entries: list[dict[str, Any]]) -> Shard:
    root = next(r for r, n in paths.SHARDED.items() if n == name)
    parts: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        parts.setdefault(shard_key(entry["manufacturer"]), []).append(entry)
    return Shard(name, root, {key: parts[key] for key in sorted(parts)})


def build_all(root: Path) -> Built:
    """Compute the index and its shards from the files (R14, D13, D57)."""
    summaries = []
    problems: list[str] = []
    for path in corpus_files(root):
        try:
            summaries.append(summarise(load_remote(path), paths.rel(root, path)))
        except LedgerError as exc:
            problems.append(f"{path.as_posix()}: {exc}")
    summaries.sort(key=_sort_key)
    problems += alias_conflicts(summaries)

    listed: list[dict[str, Any]] = []
    sharded: dict[str, list[dict[str, Any]]] = {}
    for summary in summaries:
        name = paths.shard_of(summary["file"])
        if name is None:
            listed.append(summary)
        else:
            sharded.setdefault(name, []).append(summary)
    shards = tuple(_shard(name, sharded[name]) for name in sorted(sharded))

    index: dict[str, Any] = {
        "schemaVersion": 1,
        "remotes": listed,
        "unresolved": load_unresolved(root),
    }
    if shards:
        # Additive, and all that readers of index.json alone see of the
        # shards. `path` is relative to index.json's own location, so it is
        # the same in build/ and in site/.
        index["shards"] = [
            {
                "name": shard.name,
                "path": paths.shard_manifest(shard.name),
                "remotes": shard.remotes,
                "root": shard.root,
            }
            for shard in shards
        ]
    return Built(index, shards, problems)


def build_index(root: Path) -> tuple[dict[str, Any], list[str]]:
    """What ``index.json`` holds, and the problems found on the way."""
    built = build_all(root)
    return built.index, built.problems


def _sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    return entry["manufacturer"].casefold(), entry["model"].casefold()


def shard_entries(shards: tuple[Shard, ...]) -> list[dict[str, Any]]:
    return [e for shard in shards for part in shard.parts.values() for e in part]


def merge(index: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The whole ledger as one index, for a search that must not miss a part.

    Ordered as an unsharded index would be, so that sharding changes nothing a
    lookup prints. ``shards`` is dropped: the entries are merged in.
    """
    merged = {k: v for k, v in index.items() if k != "shards"}
    merged["remotes"] = sorted([*index["remotes"], *entries], key=_sort_key)
    return merged


def _part_text(entries: list[dict[str, Any]]) -> str:
    # Same serializer and entry shape as index.json; no `unresolved`, which
    # stays in index.json alone.
    return dumps({"remotes": entries, "schemaVersion": 1})


def shard_files(shards: tuple[Shard, ...]) -> dict[str, str]:
    """Every shard file as path -> text, relative to ``build/`` or ``site/``.

    One function writes both trees, so the copies cannot differ (D20).
    """
    files: dict[str, str] = {}
    for shard in shards:
        listing = []
        for key, entries in shard.parts.items():
            text = _part_text(entries)
            files[paths.shard_part(shard.name, key)] = text
            listing.append({
                "bytes": len(text.encode("utf-8")),
                "key": key,
                # Relative to the manifest, which sits in the same directory.
                "path": f"{key}.json",
                "remotes": len(entries),
            })
        files[paths.shard_manifest(shard.name)] = dumps({
            "name": shard.name,
            "parts": listing,
            "remotes": shard.remotes,
            "root": shard.root,
            "schemaVersion": 1,
        })
    return files


def inputs_record(root: Path) -> dict[str, Any]:
    """A digest of everything the index is computed from (D57).

    SHA-256 over every ``remotes/**/*.json`` in corpus order, then
    ``unresolved.json`` when there is one, each as its path, its length and its
    bytes. Content only -- no mtime, which a checkout resets -- so it is the
    same on every machine, and cheap: about a third of a second for the 13k
    files of the full import. It does not cover the generator's own code; a
    changed generator is what ``rl build --check`` exists to catch.
    """
    digest = hashlib.sha256()
    files = [*corpus_files(root)]
    unresolved = root / UNRESOLVED
    if unresolved.exists():
        files.append(unresolved)
    for path in files:
        data = path.read_bytes()
        # Plain string work: pathlib's resolve() and relative_to() cost seconds
        # over 13,000 paths, and every path here was built from `root`.
        name = os.path.relpath(path, root).replace(os.sep, "/")
        digest.update(f"{name}\0{len(data)}\0".encode("utf-8"))
        digest.update(data)
    return {"files": len(files), "schemaVersion": 1, "sha256": digest.hexdigest()}


def load_committed(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The committed index and shards merged, if they describe these files.

    Returns ``(index, None)``, or ``(None, why)`` when they cannot be trusted
    -- a missing file, a part that disagrees with its manifest, or an inputs
    digest that no longer matches the remotes on disk -- and the caller
    rebuilds from the files, as it always did.
    """
    build = root / "build"
    try:
        stamp = json.loads((build / paths.INDEX_INPUTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"build/{paths.INDEX_INPUTS} is missing or unreadable"
    if stamp != inputs_record(root):
        return None, (
            "the remotes or unresolved.json differ from what build/ was "
            "generated from"
        )
    try:
        index = json.loads((build / "index.json").read_text(encoding="utf-8"))
        entries: list[dict[str, Any]] = []
        for advert in index.get("shards", []):
            manifest_path = build / advert["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            found = 0
            for part in manifest["parts"]:
                text = (manifest_path.parent / part["path"]).read_text(encoding="utf-8")
                part_entries = json.loads(text)["remotes"]
                if len(part_entries) != part["remotes"]:
                    return None, (
                        f"{part['path']} holds {len(part_entries)} remotes, "
                        f"its manifest says {part['remotes']}"
                    )
                entries += part_entries
                found += len(part_entries)
            if found != advert["remotes"] or found != manifest["remotes"]:
                return None, (
                    f"the {advert['name']} shard's parts do not add up to the "
                    f"{advert['remotes']} remotes index.json advertises"
                )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"the index files under build/ are unreadable ({exc})"
    return merge(index, entries), None
