"""Where each remote's generated shards live (D19, D40).

Every per-remote artifact mirrors the remote's own path under ``remotes/``,
so two remotes can never collide on an artifact. The earlier scheme --
lower-cased manufacturer plus model -- would have put ``Sony`` and an
imported ``sony`` in one directory, and a model like ``CMT-CP100 [band1]``
in a filename. For every remote authored before D40 the two schemes give
the same path, which is why adopting this one changed no committed file.
"""

from __future__ import annotations

from pathlib import Path

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
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


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
    return next((root for root in IMPORTS if where.startswith(root)), None)
