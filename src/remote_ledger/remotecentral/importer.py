"""``rl import remotecentral``: RemoteCentral's Infrared Hex Code Database, imported under R19 (DESIGN section 30).

The database is learned codes: people captured a remote's buttons with a programmable remote and
uploaded the Pronto hex, and the site says so ("some codes may be duplicated and/or imperfect learns").
That is a source nothing cross-checks, and one that disclaims itself, so every key is **Untested**, the
lowest tier (SPEC section 5): offered, not confirmed. The Pronto string is the signal and is kept as the
page gives it (a ``pronto`` form, no protocol name), so the importer decides nothing about what the code
means; a decoder over the whole ledger can name the protocols later.

``snapshot`` reads the pinned brand files (``tools/fetch_remotecentral.py``); ``build`` makes the files;
``write_import`` writes ``remotes/remotecentral/`` wholesale and the report.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ValidationError
from ..fmt import format_document
from ..import_common import authored_names as _authored_names
from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from ..official.common import Report as _Report
from ..official.common import slug
from ..pronto import _word_to_hz
from ..serialize import dumps

IMPORT_ROOT = "remotes/remotecentral"
SNAPSHOT = "sources/remotecentral"
MANIFEST = "MANIFEST.json"
REPORT = "IMPORT.md"
TIER = "untested"
HOST = "remotecentral.com"
MAKER = "remotecentral"
_HEX = re.compile(r"\A[0-9A-Fa-f]{4}(?: [0-9A-Fa-f]{4})*\Z")


class Report(_Report):
    TITLE = "RemoteCentral Infrared Hex Code Database, import report"
    COMMAND, SECTION = "rl import remotecentral", "30"
    PINS = "The pages are pinned in `sources/remotecentral/` as one file for each brand, with its SHA-256,"
    UNIT, SOURCE_COLUMN, WHERE_COLUMN = "Codes", "Brand file", "Source"
    COLLISIONS = "Pages skipped because the ledger already has a remote of that name (R15, R19 condition 4)"
    COLLISION_HEADER = ["Would have been", "The ledger already has"]


@dataclass
class Snapshot:
    directory: Path
    retrieved: str
    announced: dict[str, int] | None
    brands: dict[str, dict[str, Any]]
    notes: list[str]

    def sha(self, file: str) -> str:
        return self.brands[file]["sha256"][:8]

    def read(self, file: str) -> dict[str, Any]:
        return json.loads(_raw(self.directory / file))


def _raw(path: Path) -> bytes:
    try:
        return gzip.decompress(path.read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise ValidationError(f"{path}: listed in the manifest but unreadable: {exc}") from exc


def load_snapshot(directory: Path) -> Snapshot:
    """The snapshot, every brand file checked against the manifest (and no brand file it does not list)."""
    path = directory / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: cannot read the snapshot manifest: {exc}") from exc
    for name, entry in manifest["brands"].items():
        if hashlib.sha256(_raw(directory / name)).hexdigest() != entry["sha256"]:
            raise ValidationError(f"{directory / name} is not the file the manifest pins (SHA-256 differs); run "
                                  "tools/fetch_remotecentral.py again to re-pin")
    unlisted = sorted(p.name for p in directory.glob("*.json.gz") if p.name not in manifest["brands"])
    if unlisted:
        raise ValidationError(f"{directory}: brand files the manifest does not list: {', '.join(unlisted)}")
    return Snapshot(directory, manifest["retrieved"], manifest.get("announced"), manifest["brands"], manifest.get("notes", []))


def model_name(brand: str, title: str) -> str:
    """The page title without the brand it begins with (``Denon AVC-3020``: ``AVC-3020``)."""
    title = " ".join(title.split())
    if title.casefold().startswith(brand.casefold() + " ") and title[len(brand):].strip():
        return title[len(brand):].strip()
    return title


def read_code(label: str, hex_: str) -> tuple[int, bool, str] | str:
    """``(frequency word, has an intro, the hex in upper case)`` of a learned code, or why it is not one.

    The ledger's parser accepts what Pronto's own tools write and more; a code is taken only when it
    is the learned modulated format (type 0000) and its length is the one its header says."""
    if not label:
        return "a code with no function name"
    if not _HEX.match(hex_):
        return "the hex is not four-digit Pronto words"
    words = [int(w, 16) for w in hex_.split()]
    if len(words) < 6:
        return "fewer words than a header and one burst pair"
    if words[0] != 0:
        return f"Pronto type {words[0]:04X}, not the learned modulated type 0000"
    once, repeat = words[2], words[3]
    if len(words) != 4 + 2 * (once + repeat):
        return "the length of the string is not the one its header gives"
    if words[1] == 0:
        return "a frequency word of 0"
    return words[1], once > 0, " ".join(w.upper() for w in hex_.split())


@dataclass
class Code:
    label: str
    hex: str
    page: int
    remote: str
    word: int
    intro: bool


def units(model: dict[str, Any]) -> list[tuple[str, list[tuple[str, str, int, str]]]]:
    """``[(suffix of the model name, [(label, hex, page, remote model)])]``: how the page's codes are
    divided into files before the carrier divides them again."""
    rows = [(label, hex_, page, group["remote"]) for group in model["groups"] for label, hex_, page in group["rows"]]
    return [("", rows)]


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    gate: dict[tuple, str | None] = {}
    for file in sorted(snapshot.brands):
        data = snapshot.read(file)
        brand = data["brand"]
        taken: set[str] = set()
        for model in data["models"]:
            for target, doc in _model_files(snapshot, file, brand, data["slug"], model, report, gate, taken):
                winner = authored.get((brand.casefold(), doc["model"].casefold()))
                if winner:
                    report.collisions.append((target, winner))
                    report.count(MAKER, "keys of the skipped pages", len(doc["keys"]))
                    continue
                report.count(MAKER, "files")
                report.count(MAKER, "keys", len(doc["keys"]))
                out.append((target, doc))
    return out


def _unique(taken: set[str], name: str) -> str:
    """``name``, or ``name (2)``, ``name (3)``... until neither the name nor the path it makes is taken:
    two models of one brand may not share a name (R15), and ``52CC(0)`` and ``52CC_0`` share a file."""
    candidate, n = name, 2
    while candidate.casefold() in taken or "path:" + slug(candidate).casefold() in taken:
        candidate, n = f"{name} ({n})", n + 1
    taken.update((candidate.casefold(), "path:" + slug(candidate).casefold()))
    return candidate


def _model_files(snapshot: Snapshot, file: str, brand: str, brand_slug: str, model: dict[str, Any], report: Report,
                 gate: dict[tuple, str | None], taken: set[str]) -> list[tuple[str, dict[str, Any]]]:
    name = model_name(brand, model["title"])
    base = f"{HOST}/cgi-bin/codes/{brand_slug}/{model['slug']}/"
    sha = snapshot.sha(file)
    files: list[tuple[str, dict[str, Any]]] = []
    report.count(MAKER, "models read")
    for suffix, rows in units(model):
        buckets: dict[tuple[int, bool], list[Code]] = defaultdict(list)
        seen: set[tuple[str, str]] = set()
        for label, hex_, page, remote in rows:
            report.count(MAKER, "codes read")
            read = read_code(label, hex_)
            if isinstance(read, str):
                report.unrepresented[(MAKER, file, read)] += 1
                continue
            word, intro, upper = read
            if (label.casefold(), upper) in seen:
                report.unrepresented[(MAKER, file, "the same code listed again under the same name")] += 1
                continue
            seen.add((label.casefold(), upper))
            buckets[(word, intro)].append(Code(label, upper, page, remote, word, intro))
        order = sorted(buckets, key=lambda k: (-len(buckets[k]), k))
        for rank, (word, intro) in enumerate(order):
            carrier = _word_to_hz(word)
            protocol = {"carrierHz": carrier, "minSends": 1}
            keys: dict[str, dict[str, Any]] = {}
            names: Counter = Counter()
            for code in buckets[(word, intro)]:
                gated = (word, intro, code.hex)
                if gated not in gate:
                    probe = {"id": "primary.pronto", "type": "pronto", "hex": code.hex, "confidence": TIER, "source": "probe"}
                    gate[gated] = form_compiles(IMPORT_ROOT, protocol, "K", probe)
                if gate[gated]:
                    report.unrepresented[(MAKER, file, f"the hex does not compile: {gate[gated]}")] += 1
                    continue
                base_key = "KEY_" + (fold_label(code.label) or "CODE")
                names[base_key] += 1
                key = base_key if names[base_key] == 1 else f"{base_key}_{names[base_key]}"
                where = base + (f"page-{code.page}/" if code.page > 1 else "")
                source = f"{where} (snapshot {file}@{sha}, retrieved {snapshot.retrieved}) "
                source += f"remote model '{code.remote}', function '{code.label}'" if code.remote else f"function '{code.label}'"
                keys[key] = {"label": code.label, "forms": [
                    {"id": "primary.pronto", "type": "pronto", "hex": code.hex, "confidence": TIER, "source": source}]}
            if not keys:
                continue
            tail = ("" if rank == 0 else f" [{carrier / 1000:.1f} kHz{', intro' if intro else ''}]")
            mname = _unique(taken, f"{name}{suffix}{tail}")
            doc = {"manufacturer": brand, "model": mname, "aliases": [], "controls": [f"{brand} {name}"],
                   "protocol": protocol, "keys": keys}
            files.append((f"{IMPORT_ROOT}/{slug(brand_slug)}/{slug(mname)}.json", doc))
    return files


def import_tree(root: Path, snapshot: Path, authored: dict[tuple[str, str], str]):
    report = Report()
    snap = load_snapshot(snapshot)
    report.retrieved[MAKER] = snap.retrieved
    out: dict[str, dict[str, Any]] = {}
    for target, doc in build(snap, authored, report):
        if target in out:
            raise ValueError(f"{target} is yielded twice")
        out[target] = doc
    for note in snap.notes:
        report.notes.append((MAKER, "the fetch", note))
    return out, report


def write_import(root: Path, snapshot: Path | None = None, authored: dict[tuple[str, str], str] | None = None) -> Report:
    """Rewrite ``remotes/remotecentral/`` wholesale: every ``*.json`` and ``IMPORT.md`` are the importer's;
    anything else there (``README.md``) is authored and left alone. ``authored`` is the names the ledger
    already holds (the tree's own, unless a test gives another)."""
    directory = snapshot if snapshot is not None else root / SNAPSHOT
    docs, report = import_tree(root, directory, _authored_names(root, IMPORT_ROOT) if authored is None else authored)
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
