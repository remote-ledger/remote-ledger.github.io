"""``rl import official``: manufacturers' own IR code tables, imported under R19 (DESIGN section 29).

A manufacturer's table is the best evidence a citation can point to, and it is still imported at
Plausible (R19.3): the table's word is one source, and a key earns Verified by a second. Each maker
has a reader (:mod:`.marantz`, :mod:`.anthem`) over a pinned snapshot of its documents; this module
runs the readers, writes ``remotes/official/<maker>/`` wholesale and the report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..fmt import format_document
from ..import_common import authored_names as _authored_names
from ..serialize import dumps
from . import anthem, marantz
from .common import IMPORT_ROOT, MANIFEST, REPORT, SNAPSHOT, Report, load_snapshot

#: maker -> its reader. A maker is imported when its directory is in the snapshot.
MAKERS = {"anthem": anthem, "marantz": marantz}


def import_tree(root: Path, snapshot: Path, authored: dict[tuple[str, str], str]):
    """Every file the snapshots yield, as ``{repo path: document}``, plus the report."""
    report = Report()
    out: dict[str, dict[str, Any]] = {}
    for maker, reader in sorted(MAKERS.items()):
        directory = snapshot / maker
        if not (directory / MANIFEST).is_file():
            continue
        snap = load_snapshot(directory)
        report.retrieved[maker] = snap.retrieved
        for target, doc in reader.build(snap, authored, report):
            if target in out:
                raise ValueError(f"{target} is yielded twice")
            out[target] = doc
    return out, report


def write_import(root: Path, snapshot: Path | None = None) -> Report:
    """Rewrite ``remotes/official/`` wholesale: every ``*.json`` and ``IMPORT.md`` are the
    importer's; anything else there (``README.md``) is authored and left alone."""
    directory = snapshot if snapshot is not None else root / SNAPSHOT
    docs, report = import_tree(root, directory, _authored_names(root, IMPORT_ROOT))
    target_root = root / IMPORT_ROOT
    for stale in sorted(target_root.rglob("*.json")) if target_root.exists() else []:
        if stale.relative_to(root).as_posix() not in docs:
            stale.unlink()
    for rel, doc in sorted(docs.items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(doc, sort_keys=False), encoding="utf-8", newline="\n")
        path.write_text(format_document(path), encoding="utf-8", newline="\n")
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / REPORT).write_text(report.render(), encoding="utf-8", newline="\n")
    for empty in sorted((p for p in target_root.rglob("*") if p.is_dir()), reverse=True):
        if not any(empty.iterdir()):
            empty.rmdir()
    return report
