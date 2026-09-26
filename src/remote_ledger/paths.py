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
}


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
