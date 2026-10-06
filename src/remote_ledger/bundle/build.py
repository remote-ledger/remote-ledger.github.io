"""The bundle exporter: ``rl bundle`` (D88).

Like the ``app`` stage (D74) it is a pure function of the committed tree and of
the code, with one function that builds the files in memory and one that writes
them, so ``--check`` and the writer share the build and a test can build a bundle
without a directory. Unlike it, the bundle is **not a stage**: it is a build
artifact an app ships, not a tree the repository commits. It is registered in no
``generators.PIPELINE``, owns no path of D19's table, is written to no directory
of ``build/`` or ``site/`` (the writer refuses one), and so ``rl build`` and
``rl build --check`` cannot see it.

The files, in one directory:

``catalog.sqlite``
    the bundle (D89 to D92);
``notices.json``
    the sources, their licences and what each contributed (D94);
``manifest.json``
    the version, sizes and SHA-256 of the other two, and which brands are in
    (D93); ``rl bundle sign`` adds ``manifest.sig`` beside it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..app_api import compact
from ..errors import ValidationError
from ..keys import load_vocabulary
from . import catalog, corpus, notices, select, writer

BUNDLE_FILE = "catalog.sqlite"
NOTICES_FILE = "notices.json"
MANIFEST_FILE = "manifest.json"
SIGNATURE_FILE = "manifest.sig"
#: Where ``rl bundle`` writes when it is not told, relative to the repository. It is
#: ignored by git (``.gitignore``), and it is not under ``build/`` or ``site/``.
DEFAULT_OUT = "bundle-out"


@dataclass
class Bundle:
    """What :func:`build_bundle` made: the files (name, content), what stopped it, and
    numbers for the report."""

    files: dict[str, bytes] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    selection: select.Selection | None = None
    #: The rows the file was written from, for a report or a test to look at.
    assembled: catalog.Assembled | None = None


def build_manifest(bundle: bytes, notices_json: bytes, assembled: catalog.Assembled,
                   data_version: str, vocabulary_version: int) -> bytes:
    """``manifest.json`` of an unsigned bundle (D93)."""
    return compact({
        "schemaVersion": writer.SCHEMA_VERSION,
        "dataVersion": data_version,
        "vocabularyVersion": vocabulary_version,
        "profile": assembled.profile,
        "bundle": {
            "file": BUNDLE_FILE, "bytes": len(bundle),
            # `mtime=0`: the gzip header holds no clock
            "gzipBytes": len(gzip.compress(bundle, 9, mtime=0)),
            "sha256": hashlib.sha256(bundle).hexdigest(),
        },
        "notices": {
            "file": NOTICES_FILE, "bytes": len(notices_json),
            "sha256": hashlib.sha256(notices_json).hexdigest(),
        },
        "counts": {
            "brands": len(assembled.brands), "models": len(assembled.models),
            "remotes": len(assembled.remotes), "keys": len(assembled.keys),
            "signals": len(assembled.signals),
        },
        "brands": {
            "included": len(assembled.brands), "excluded": len(assembled.excluded),
        },
    })


def build_bundle(root: Path, profile: str = "selected", *, max_bytes: int | None = None,
                 records: list[corpus.RemoteRecord] | None = None) -> Bundle:
    """The whole bundle for the tree under ``root``, in memory. ``records`` are the tree's
    remotes when the caller has read them already (``corpus.read_corpus``)."""
    result = Bundle()
    if profile not in select.PROFILES:
        result.problems.append(f"unknown profile {profile!r}; choose one of "
                               f"{', '.join(select.PROFILES)}")
        return result
    if records is None:
        records, problems = corpus.read_corpus(root)
        if problems:
            result.problems = problems
            return result
    if not records:
        result.problems.append("no remote under remotes/, so there is nothing to export")
        return result
    collected = catalog.collect(records)
    selection = select.choose(collected, profile)
    result.selection = selection
    try:
        sources = notices.build_sources(root, records)
    except ValidationError as exc:
        result.problems.append(str(exc))
        return result
    assembled = catalog.assemble(collected, selection.chosen, profile, sources)
    result.assembled = assembled
    vocabulary_version = load_vocabulary().version
    bundle, data_version = writer.write_database(assembled, vocabulary_version, selection.rule)
    cap = select.SELECTED_MAX_BYTES if (profile == "selected" and max_bytes is None) else max_bytes
    if cap is not None and len(bundle) > cap:
        result.problems.append(
            f"the {profile} bundle is {len(bundle):,} bytes, over the {cap:,} allowed: shorten or "
            "reorder src/remote_ledger/bundle/data/selected_brands.txt, or lower "
            "select.BUDGET_BYTES (D92); --max-bytes N changes the limit")
        return result
    notices_json = compact(notices.notices_document(assembled.sources))
    result.files = {
        BUNDLE_FILE: bundle,
        NOTICES_FILE: notices_json,
        MANIFEST_FILE: build_manifest(bundle, notices_json, assembled, data_version,
                                      vocabulary_version),
    }
    carried = {row[0] for row in assembled.remotes}
    left_out = [r for i, r in enumerate(records) if i + 1 not in carried]
    result.stats = {
        "profile": profile, "bytes": len(bundle), "dataVersion": data_version,
        "leftOutRemotes": len(left_out), "leftOutKeys": sum(len(r.keys) for r in left_out),
        # the app API (D74) serves the IR Blaster import only: a remote of another
        # source that the bundle leaves out is in no static file of the ledger yet
        "leftOutNotInApi": sum(1 for r in left_out if r.source != "irblaster"),
        "brands": len(assembled.brands), "models": len(assembled.models),
        "remotes": len(assembled.remotes), "keys": len(assembled.keys),
        "signals": len(assembled.signals), "excludedBrands": len(assembled.excluded),
        "unreachableBrands": sum(1 for e in assembled.excluded if e[2] is None),
        "ledgerKeys": sum(len(r.keys) for r in records),
        "otherCandidates": sum(r.other_candidates for r in records),
    }
    return result


def refuse_owned_path(root: Path, out: Path) -> None:
    """The bundle is an artifact: never inside a tree ``rl build`` owns (D19)."""
    target = out.resolve()
    for owned in ("build", "site"):
        base = (root / owned).resolve()
        if target == base or base in target.parents:
            raise ValidationError(
                f"{out} is inside {owned}/, which `rl build` owns and `rl build --check` compares "
                "(D19): the bundle is a build artifact and is never written there")


def write_bundle(result: Bundle, out: Path, root: Path) -> None:
    """Write the files of ``result`` to ``out``, replacing what an earlier bundle left
    (and its signature, which would no longer match)."""
    refuse_owned_path(root, out)
    if out.exists():
        for name in (BUNDLE_FILE, NOTICES_FILE, MANIFEST_FILE, SIGNATURE_FILE):
            (out / name).unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    for name, content in result.files.items():
        (out / name).write_bytes(content)


def check_bundle(result: Bundle, out: Path) -> list[str]:
    """``--check``: the files under ``out`` are what a fresh build of this tree gives.
    A signed manifest is compared without its ``signature`` field, and the
    signature itself is ``verify-signature``'s business."""
    problems = []
    for name, fresh in result.files.items():
        path = out / name
        if not path.is_file():
            problems.append(f"{path}: missing; a fresh build writes it")
            continue
        current = path.read_bytes()
        if name == MANIFEST_FILE:
            doc = json.loads(current)
            doc.pop("signature", None)
            current = compact(doc)
        if current != fresh:
            problems.append(f"{path}: differs from a fresh build of this tree")
    return problems
