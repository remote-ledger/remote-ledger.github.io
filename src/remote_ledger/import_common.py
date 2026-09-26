"""Pieces every importer needs, regardless of the upstream source (R19).

Both ``lirc/importer.py`` and ``smartir/importer.py`` build a candidate
remote in memory, cross-check and compile-gate it before deciding what to
write, and must never let an import shadow an authored remote (R19 condition
4). None of that depends on the upstream format, so it lives here once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .crosscheck import check_key
from .errors import LedgerError
from .remote import Remote, remote_from_doc
from .serialize import load

#: remote.schema.json's bounds on a raw sequence.
RAW_ITEM_MAX, RAW_LEN_MAX = 1_000_000, 2048


def authored_names(root: Path, import_root: str) -> dict[tuple[str, str], str]:
    """(manufacturer, model-or-alias), casefolded, for every file outside
    ``import_root`` -- the names an import must never shadow (R19.4)."""
    from .validate import corpus_files

    names: dict[tuple[str, str], str] = {}
    for path in corpus_files(root):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(import_root + "/"):
            continue
        doc = load(path)
        maker = doc["manufacturer"].casefold()
        for name in [doc["model"], *(doc.get("aliases") or [])]:
            names[(maker, name.casefold())] = rel
    return names


def probe(import_root: str, protocol: dict[str, Any], key: str,
          forms: list[dict[str, Any]]) -> tuple[Remote, list]:
    """Load a one-key remote in memory and cross-check it (R13)."""
    doc = {"manufacturer": "probe", "model": "probe", "protocol": protocol,
           "keys": {key: {"forms": forms}}}
    remote = remote_from_doc(doc, Path(import_root) / "probe.json")
    mismatches, _ = check_key(remote, key)
    return remote, mismatches


def form_compiles(import_root: str, protocol: dict[str, Any], key: str,
                   form: dict[str, Any]) -> str | None:
    """None if the form is schema-legal and compiles to Pronto, else why
    not (the schema's raw bounds, then D28's)."""
    for name in ("intro", "repeat"):
        seq = form.get(name) or []
        if len(seq) > RAW_LEN_MAX:
            return f"{name} has {len(seq)} durations; the schema allows {RAW_LEN_MAX}"
        if any(not 1 <= d <= RAW_ITEM_MAX for d in seq):
            return f"{name} holds a duration outside 1-{RAW_ITEM_MAX} us"
    try:
        remote, _ = probe(import_root, protocol, key, [form])
        remote.compile_group(key, "primary")
    except (LedgerError, ValueError) as exc:
        return str(exc)
    return None
