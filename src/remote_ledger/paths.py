"""Where each remote's generated shards live (D19, D40).

Every per-remote artifact mirrors the remote's own path under ``remotes/``,
so two remotes can never collide on an artifact. The earlier scheme --
lower-cased manufacturer plus model -- would have put ``Sony`` and an
imported ``sony`` in one directory, and a model like ``CMT-CP100 [band1]``
in a filename. For every remote authored before D40 the two schemes give
the same path, which is why adopting this one changed no committed file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import ValidationError

REMOTES = "remotes/"

#: Directories whose remotes were imported rather than authored (SPEC R19,
#: D34). The path is the licence boundary and the trust boundary at once, so
#: the index and site label every remote under one of these.
IMPORTS: dict[str, dict[str, str]] = {
    "remotes/lirc/": {
        "name": "LIRC remotes database",
        "licence": "GPL-2.0-or-later",
        "readme": "remotes/lirc/README.md",
    },
    "remotes/smartir/": {
        "name": "SmartIR codes database",
        "licence": "MIT",
        "readme": "remotes/smartir/README.md",
    },
    "remotes/irblaster/": {
        "name": "IR Blaster database (as shipped in SwiftRemote)",
        "licence": "GPL-3.0-only",
        "readme": "remotes/irblaster/README.md",
    },
    "remotes/hifi-remote/": {
        "name": "hifi-remote.com Sony code pages",
        "licence": "none: a table of reference codes",
        "readme": "remotes/hifi-remote/README.md",
    },
    "remotes/official/": {
        "name": "Manufacturers' own IR code tables",
        "licence": "none: tables of reference codes",
        "readme": "remotes/official/README.md",
    },
    "remotes/jp1/": {
        "name": "JP1 device upgrades of hifi-remote.com's forum",
        "licence": "none: a table of reference codes",
        "readme": "remotes/jp1/README.md",
    },
}


#: Import roots whose remotes are indexed in their own shard files instead of
#: in ``index.json`` (D69), as root -> shard name. The roots stay in
#: ``IMPORTS`` too: a shard changes where an entry is *listed*, not what it is.
#:
#: Moving a root in or out of this table changes ``index.json`` for every
#: reader of it, including the SwiftRemote app in the field, which reads only
#: that file. Do it on purpose.
SHARDED: dict[str, str] = {"remotes/irblaster/": "irblaster"}

#: Where the index's shards live, relative to ``build/`` and to ``site/``.
#: ``build/index/`` and ``site/index/`` sit beside ``index.json``, so a path
#: that ``index.json`` advertises resolves the same in both.
SHARD_DIR = "index"


def shard_of(where: str) -> str | None:
    """The shard a remote is indexed in, or None if ``index.json`` lists it."""
    root = imported_from(where)
    return SHARDED.get(root) if root else None


def shard_manifest(name: str) -> str:
    """``irblaster`` -> ``index/irblaster/manifest.json``."""
    return f"{SHARD_DIR}/{name}/manifest.json"


def shard_part(name: str, key: str) -> str:
    """``irblaster``, ``a`` -> ``index/irblaster/a.json``."""
    return f"{SHARD_DIR}/{name}/{key}.json"


def shard_script(name: str, key: str) -> str:
    """The page's copy of a part: ``index/irblaster/a.js`` (D40's reason)."""
    return f"{SHARD_DIR}/{name}/{key}.js"


#: What the index files were computed from, which ``rl lookup`` compares with
#: the files on disk before it trusts them (D69). Written under ``build/`` only.
INDEX_INPUTS = f"{SHARD_DIR}/inputs.json"


#: The app API's tree (D74), relative to the repository. The ``app`` stage owns
#: it; the ``site`` stage owns the rest of ``site/`` and says so by excluding it,
#: so every generated file has exactly one owner.
APP_API = "site/app/v1"

#: The import root the app API is a function of.
APP_API_SOURCE = "remotes/irblaster/"


#: The canonical key vocabulary and its alias table (D83). They are ledger data
#: that is written by hand and never generated, so no stage owns them and
#: ``build/`` and ``site/`` do not hold a copy. They ship as package data, like
#: the schemas (``validate.SCHEMA_DIR``): a consumer that installs the package
#: has no checkout, and ``canonical_id`` has to work for it.
VOCABULARY_DIR = Path(__file__).resolve().parent / "vocabulary"

#: The vocabulary's files, relative to ``VOCABULARY_DIR`` and the schema each
#: is validated against (``schema/``).
KEYS_FILE = "keys.json"
ALIASES_FILE = "aliases.json"
VOCABULARY_SCHEMAS = {KEYS_FILE: "keys.schema.json", ALIASES_FILE: "aliases.schema.json"}

#: Where those files sit in the repository, for the messages that name them.
VOCABULARY_REPO_DIR = "src/remote_ledger/vocabulary"


def rel(root: Path, path: Path) -> str:
    """A remote's path relative to the corpus root, as POSIX text.

    Computed from the root the generators were handed, never from the
    working directory: ``Remote.where`` falls back to a bare filename
    outside the repo, which would put every artifact of a throwaway corpus
    at the same path.
    """
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        for extra in _EXTRA:
            if resolved.is_relative_to(extra.path):
                return resolved.relative_to(extra.path).as_posix()
        raise


def _relative(where: str) -> str:
    if not (where.startswith(REMOTES) and where.endswith(".json")):
        raise ValueError(f"{where!r} is not a remote file under {REMOTES}")
    return where[len(REMOTES):-len(".json")]


def artifact(where: str) -> str:
    """``remotes/a/b.json`` -> ``build/pronto/a/b.json``."""
    return f"build/pronto/{_relative(where)}.json"


def site_script(where: str) -> str:
    """``remotes/a/b.json`` -> ``r/a/b.js``, relative to ``site/`` (D40)."""
    return f"r/{_relative(where)}.js"


def imported_from(where: str) -> str | None:
    """The import root a remote lives under, or None if it was authored."""
    return next((root for root in imports() if where.startswith(root)), None)


# --- extra roots (D128) ---------------------------------------------------------------------------------

#: What an extra root says about its sources, and where it keeps them.
IMPORTS_FILE = "imports.json"
_KINDS = ("none", "reading", "stated", "inherited")
_SOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class ExtraRoot:
    """A directory laid out like the repository whose remotes join the corpus of one run (D128).

    ``sources`` is its ``imports.json``: for each source under its ``remotes/``, the name and licence
    statement ``IMPORTS`` holds for a public one, and the facts the bundle's notices state."""
    path: Path
    sources: dict[str, dict[str, Any]]


_EXTRA: list[ExtraRoot] = []


def extra_roots() -> tuple[ExtraRoot, ...]:
    return tuple(_EXTRA)


def clear_extra_roots() -> None:
    _EXTRA.clear()


def configure_extra_roots(dirs: Iterable[Path], primary: Path | None = None) -> None:
    """Make the remotes of ``dirs`` part of the corpus until :func:`clear_extra_roots`.

    Only the commands that read the corpus and write nothing into ``build/`` or ``site/`` call this, so
    the committed trees are a function of the repository alone. Each directory must name its sources in
    ``imports.json``, and every remote under it must belong to one of them: a remote that no source
    claims would be labelled authored, and an extra root holds nothing the repository's author wrote."""
    loaded: list[ExtraRoot] = []
    taken = set(IMPORTS)
    for directory in dirs:
        path = Path(directory).resolve()
        if primary is not None:
            here = Path(primary).resolve()
            if path == here or path.is_relative_to(here) or here.is_relative_to(path):
                raise ValidationError(f"{directory}: an extra root cannot contain the repository or be inside it")
        if any(path == e.path for e in loaded):
            raise ValidationError(f"{directory} is given twice as an extra root")
        extra = ExtraRoot(path, _read_registry(path, taken))
        taken.update(f"{REMOTES}{name}/" for name in extra.sources)
        for file in sorted((path / "remotes").glob("**/*.json")):
            where = file.relative_to(path).as_posix()
            if not any(where.startswith(f"{REMOTES}{name}/") for name in extra.sources):
                raise ValidationError(f"{path / where}: no source in {path / IMPORTS_FILE} claims this remote; an "
                                      "extra root holds imported data and says where each directory is from")
        loaded.append(extra)
    _EXTRA[:] = loaded


def _read_registry(path: Path, taken: set[str]) -> dict[str, dict[str, Any]]:
    registry = path / IMPORTS_FILE
    try:
        doc = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{registry}: an extra root names its sources in {IMPORTS_FILE}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != 1 or not isinstance(doc.get("sources"), dict):
        raise ValidationError(f'{registry}: expected {{"format": 1, "sources": {{<name>: {{...}}}}}}')
    out: dict[str, dict[str, Any]] = {}
    for name, entry in doc["sources"].items():
        where = f"{registry}: source {name!r}"
        if not _SOURCE_NAME.match(name) or name == "authored":
            raise ValidationError(f"{where}: a source is a lower-case directory name under remotes/, and not 'authored'")
        if f"{REMOTES}{name}/" in taken:
            raise ValidationError(f"{where} is already a source of the repository or of another extra root")
        if not isinstance(entry, dict):
            raise ValidationError(f"{where} is not an object")
        kind = entry.get("kind", "none")
        required = ["name", "licence", "upstream_url"] + (["note"] if kind == "none" else ["licence_file", "readme_sentences"])
        if kind not in _KINDS or any(not entry.get(field) for field in required):
            raise ValidationError(f"{where}: kind is one of {', '.join(_KINDS)}, and {', '.join(required)} are needed for it")
        out[name] = {**entry, "kind": kind, "readme": entry.get("readme", f"{REMOTES}{name}/README.md")}
    return out


def imports() -> dict[str, dict[str, str]]:
    """``IMPORTS`` and the sources of the extra roots, as ``root -> {name, licence, readme}``."""
    out = dict(IMPORTS)
    for extra in _EXTRA:
        for name, entry in extra.sources.items():
            out[f"{REMOTES}{name}/"] = {k: entry[k] for k in ("name", "licence", "readme")}
    return out


def extra_source_names() -> tuple[str, ...]:
    """The sources of the extra roots, in the order the roots were given and then the registry's."""
    return tuple(name for extra in _EXTRA for name in extra.sources)


def locate(root: Path, where: str) -> Path:
    """The file a repository-relative path names: under ``root``, or under the extra root that holds it."""
    for base in (Path(root), *(extra.path for extra in _EXTRA)):
        if (base / where).is_file():
            return base / where
    return Path(root) / where


def extra_source(name: str) -> tuple[Path, dict[str, Any]] | None:
    """``(the extra root's directory, its entry)`` for a source an extra root holds, else None."""
    for extra in _EXTRA:
        if name in extra.sources:
            return extra.path, extra.sources[name]
    return None
