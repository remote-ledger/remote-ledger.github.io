"""``rl import flipper-irdb``: the CC0 files of Flipper-IRDB, imported under R19 (DESIGN section 96).

The source is ``github.com/Lucaslhm/Flipper-IRDB``, the community's collection of Flipper Zero ``.ir`` files, one file for a
device with a button for each of its codes. Its licence is **CC0-1.0 from 2025-08-07** (PR #960 added the ``LICENSE`` and the
README's "by submitting you agree to license your work under CC0"): a file added before that has no licence of its own and is
not here (SPEC section 4). ``tools/fetch_flipper.py`` pins the files added after it, each with its adding commit, in
``sources/flipper-irdb``; this importer reads that snapshot and nothing else.

A ``parsed`` entry is mapped to a ledger protocol by :mod:`.mapping` (read from the firmware's decoders and checked against its
captures); a ``raw`` entry is a raw form, ``truncated`` because Flipper's raw data ends on a mark and the firmware's own silence
of 180 ms before a raw transmission is the gap (D355). A file with several protocols is one remote file for each, as the JP1
import's are. Every form is Plausible and cites the repository's commit, the file and line, and the commit that added the file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ValidationError
from ..fmt import format_document
from ..import_common import authored_names as _authored_names
from ..import_common import form_compiles
from ..irblaster.importer import PROTOCOL_EXTRAS, fold_label
from ..numeric import UNIT_US_MAX
from ..protocols import REGISTRY
from ..serialize import dumps
from . import ir, mapping

UPSTREAM = "flipper-irdb"
UPSTREAM_URL = "https://github.com/Lucaslhm/Flipper-IRDB"
IMPORT_ROOT = "remotes/flipper-irdb"
SNAPSHOT = "sources/flipper-irdb"
MANIFEST = "MANIFEST.json"
REPORT = "IMPORT.md"
TIER = "plausible"
#: A sent frame repeats: Sony's three times (Flipper's own SIRC_REPEAT_COUNT_MIN), the others' once.
MIN_SENDS = {"Sony12": 3, "Sony15": 3, "Sony20": 3}
DEFAULT_MIN_SENDS = 1
#: The firmware's silence before every raw transmission (``INFRARED_RAW_TX_TIMING_DELAY_US``), the gap a raw signal that ends on a mark has.
RAW_GAP_US = 180_000
FIRMWARE = "https://github.com/flipperdevices/flipperzero-firmware/blob/3fb1d51727733d2007abc5ec636a89328857803e/"
RAW_GAP_CLAIM = {
    "reason": ("Flipper's raw data ends on a mark and records no gap after it; the firmware sends a silence of 180 ms before every raw "
               "transmission, so that is the space between one send of the signal and the next"),
    "source": (f"{FIRMWARE}lib/infrared/encoder_decoder/infrared.h#L17 (INFRARED_RAW_TX_TIMING_DELAY_US 180000) and "
               f"{FIRMWARE}lib/infrared/worker/infrared_transmit.c#L23-L27 (the silence it sends first)"),
}
RAW_FORM_CLAIM = {
    "reason": "the capture ends on its last mark: Flipper's raw data has no final space, so the signal's closing gap is the protocol block's defaultGapUs and not evidence",
    "source": f"{FIRMWARE}lib/infrared/worker/infrared_transmit.c#L15-L36 (the raw data is sent as given, a mark first, and ends with its last duration)",
}
_SLUG = re.compile(r"[^A-Za-z0-9._+-]")
_SPACE = re.compile(r"[\s_]+")


def slug(text: str) -> str:
    return _SLUG.sub("_", text.strip()).strip("_.") or "_"


def spaced(text: str) -> str:
    """A name as the repository's file names spell it: an underscore is a space."""
    return _SPACE.sub(" ", text).strip()


# --- the snapshot ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    manifest: dict[str, Any]
    #: repository path -> the file's text
    files: dict[str, str]

    @property
    def commit(self) -> str:
        return self.manifest["commit"]


def load_snapshot(directory: Path) -> Snapshot:
    """The pinned files, after checking each is the one the manifest names (SHA-256) and that nothing else is there."""
    try:
        manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{directory}: cannot read {MANIFEST}: {exc}; run tools/fetch_flipper.py") from exc
    files: dict[str, str] = {}
    for path, entry in sorted(manifest["files"].items()):
        data = (directory / path).read_bytes() if (directory / path).is_file() else None
        if data is None or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValidationError(f"{directory / path} is not the file {MANIFEST} pins")
        files[path] = data.decode("utf-8", errors="replace")
    on_disk = {p.relative_to(directory).as_posix() for p in directory.rglob("*.ir")}
    if on_disk != set(files):
        raise ValidationError(f"{directory}: {sorted(on_disk ^ set(files))[:3]} is in one of the directory and {MANIFEST}, not the other")
    return Snapshot(manifest, files)


# --- names ---------------------------------------------------------------------------------


def names_of(path: str, categories: set[str]) -> tuple[str, str]:
    """``(brand, model)`` of a file by its path ``<category>/<brand>/<file>.ir``.

    A directory that is itself a category (``Car_Multimedia/DVD_Players/Lexus``) is skipped; a file straight in its category
    (``MiniDisc/Pioneer_CU-MJO11.ir``) takes its brand from the file name's first word. The model is the file name without
    the brand it begins with, an underscore a space."""
    parts = path.split("/")
    stem = parts[-1].removesuffix(".ir")
    if len(parts) == 2:
        brand, _, rest = stem.partition("_")
        return spaced(brand), spaced(rest) or spaced(stem)
    below = parts[1:-1]
    while len(below) > 1 and below[0] in categories:
        below = below[1:]
    brand = spaced(below[0])
    model = spaced(stem)
    if model.casefold().startswith(brand.casefold() + " "):
        model = model[len(brand):].strip(" -:,") or model
    return brand, model


def block(protocol: str) -> dict[str, Any]:
    """What a remote file's ``protocol`` carries: the nominal carrier, as every other source's (one code compiles to one signal
    whichever source it came from), the repeat count and the IR Blaster import's own extras."""
    out: dict[str, Any] = {"name": protocol, "carrierHz": REGISTRY[protocol].nominal_carrier_hz,
                           "minSends": MIN_SENDS.get(protocol, DEFAULT_MIN_SENDS)}
    out.update(PROTOCOL_EXTRAS.get(protocol, {}))
    return out


def raw_block(frequency: int, shortest: int, where: str) -> dict[str, Any]:
    """The ``protocol`` block of a file of raw forms. A truncated raw form needs a unit (D24) and a raw capture has none: it is the shortest
    duration of the file's raw data, the largest unit the capture can have, and no duration is computed from it."""
    unit = max(1, min(shortest, UNIT_US_MAX))
    claim = {"reason": (f"the file names no protocol, and D24 wants a unit to check the closing gap against; the shortest duration of its raw data, {unit} us, "
                        "is the largest unit the capture can have, and no duration is computed from it"),
             "source": where}
    return {"carrierHz": frequency, "minSends": DEFAULT_MIN_SENDS, "unitUs": unit, "defaultGapUs": RAW_GAP_US,
            "claims": {"unitUs": claim, "defaultGapUs": RAW_GAP_CLAIM}}


def key_names(entries: list[ir.Entry]) -> list[str]:
    """``KEY_<folded name>`` for each entry; a name that two entries fold to, or that folds to nothing, gets ``_<n>`` by order."""
    base = ["KEY_" + (fold_label(e.name) or "FUNCTION") for e in entries]
    count = Counter(base)
    seen: Counter = Counter()
    out = []
    for name in base:
        seen[name] += 1
        out.append(f"{name}_{seen[name]}" if count[name] > 1 or name == "KEY_FUNCTION" else name)
    return out


# --- the report --------------------------------------------------------------------------------


@dataclass
class Report:
    commit: str
    files: Counter = field(default_factory=Counter)
    remotes: Counter = field(default_factory=Counter)
    keys: Counter = field(default_factory=Counter)
    protocols: Counter = field(default_factory=Counter)
    #: (path, reason) for files that yield nothing
    skipped_files: list[tuple[str, str]] = field(default_factory=list)
    #: (path, entry, reason) for entries that yield nothing
    skipped_keys: list[tuple[str, str, str]] = field(default_factory=list)
    #: (path, model it was kept as, the ledger's file that has the name)
    renamed: list[tuple[str, str, str]] = field(default_factory=list)
    #: (path, entry) for an entry that repeats a name and a code the file already has
    repeated: list[tuple[str, str]] = field(default_factory=list)

    def render(self, manifest: dict[str, Any]) -> str:
        licence = manifest["licence"]
        out = [
            "# Flipper-IRDB import report",
            "",
            "Generated by `rl import flipper-irdb` (DESIGN.md section 96); never hand-edited.",
            f"Upstream: `{UPSTREAM_URL}` @ `{self.commit}`. Licence: {licence['name']} from {licence['date']} "
            f"(PR #{licence['pullRequest']}, commit `{licence['commit'][:7]}`): only the files added after it are here.",
            "",
            "## Totals",
            "",
            "| | Count |",
            "|---|---|",
        ]
        for label, counter in (("Upstream files", self.files), ("Remotes", self.remotes), ("Keys", self.keys),
                               ("Remote files by ledger protocol", self.protocols)):
            for what, n in sorted(counter.items()):
                out.append(f"| {label}: {what} | {n:,} |")
        sections = (
            ("Files that yield no remote", ["File", "Reason"], sorted(self.skipped_files)),
            ("Files kept under another model name because the ledger already has the name (R15)", ["File", "Kept as", "The ledger already has"],
             sorted(self.renamed)),
            ("Entries skipped", ["File", "Entry", "Reason"], sorted(self.skipped_keys)),
            ("Entries that repeat a name and a code the file already has (one is kept)", ["File", "Entry"], sorted(self.repeated)),
        )
        for title, header, rows in sections:
            out += ["", f"## {title} ({len(rows):,})", ""]
            if not rows:
                out.append("None.")
                continue
            out.append("| " + " | ".join(header) + " |")
            out.append("|" + "---|" * len(header))
            for row in rows:
                out.append("| " + " | ".join(_md(v) for v in row) + " |")
        return "\n".join(out) + "\n"


def _md(value: Any) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return f"`{text}`" if text and not text[0].isdigit() else text


# --- one file ---------------------------------------------------------------------------------


def _form(entry: ir.Entry, protocol: str, signal: mapping.Signal | None, source: str) -> dict[str, Any]:
    if signal is not None:
        form: dict[str, Any] = {"id": "primary.irp", "type": "irp", "device": signal.device}
        if signal.subdevice is not None:
            form["subdevice"] = signal.subdevice
        form["function"] = signal.function
        form["confidence"] = TIER
        form["source"] = source
        return form
    return {"id": "primary.raw", "type": "raw", "repeat": list(entry.data), "truncated": True, "confidence": TIER, "source": source,
            "claims": {"truncated": RAW_FORM_CLAIM}}


class Importer:
    def __init__(self, snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report):
        self.snapshot, self.authored, self.report = snapshot, authored, report
        self.sha = snapshot.commit[:7]
        self.categories = {p.split("/")[0] for p in snapshot.files}
        self._compiles: dict[tuple, str | None] = {}
        self._models: dict[tuple[str, str], str] = {}

    def gate(self, group: str, entry: ir.Entry, signal: mapping.Signal | None) -> str | None:
        """None if the entry's form compiles, else why not (R12); by what determines the compiled signal."""
        if signal is not None:
            key = (signal.protocol, signal.device, signal.subdevice, signal.function)
            if key not in self._compiles:
                probe = {k: v for k, v in block(signal.protocol).items() if k != "claims"}
                self._compiles[key] = form_compiles(IMPORT_ROOT, probe, "K", _form(entry, group, signal, "probe"))
            return self._compiles[key]
        key = (entry.frequency, entry.data)
        if key not in self._compiles:
            probe = raw_block(entry.frequency, min(entry.data, default=1), "probe")
            self._compiles[key] = form_compiles(IMPORT_ROOT, probe, "K", _form(entry, group, None, "probe"))
        return self._compiles[key]

    def citation(self, path: str, entry: ir.Entry, signal: mapping.Signal | None) -> str:
        info = self.snapshot.manifest["files"][path]
        where = f"{UPSTREAM}@{self.sha} {path}:{entry.line} name '{entry.name}' (added in {info['added'][:7]} on {info['date']}; "
        if signal is not None:
            addr = " ".join(f"{b:02X}" for b in entry.address)
            cmd = " ".join(f"{b:02X}" for b in entry.command)
            return f"{where}parsed {entry.protocol}, address {addr}, command {cmd}): {signal.protocol} device {signal.device}" + (
                f" subdevice {signal.subdevice}" if signal.subdevice is not None else "") + f" function {signal.function}: {signal.how}"
        return (f"{where}raw, {entry.frequency} Hz, duty cycle {entry.duty_cycle:g}, {len(entry.data)} durations ending on a mark): "
                "the durations as given, a mark first, the closing space the firmware's silence before a raw transmission (180 ms)")

    def import_file(self, path: str, text: str) -> list[tuple[str, dict[str, Any]]]:
        report = self.report
        try:
            parsed = ir.parse(text)
        except ir.FlipperParseError as exc:
            report.files["skipped: not a readable .ir file"] += 1
            report.skipped_files.append((path, str(exc)))
            return []
        if not parsed.entries:
            report.files["skipped: no entries"] += 1
            report.skipped_files.append((path, "no entries"))
            return []
        groups: dict[str, list[tuple[ir.Entry, mapping.Signal | None]]] = defaultdict(list)
        for entry in parsed.entries:
            if entry.kind == "parsed":
                signal = mapping.signal_of(entry)
                if isinstance(signal, str):
                    report.keys[f"skipped: {entry.protocol}"] += 1
                    report.skipped_keys.append((path, entry.name, signal))
                    continue
                why = self.gate(signal.protocol, entry, signal)
                group = signal.protocol
            else:
                signal, why, group = None, self.gate("raw", entry, None), f"raw {entry.frequency} Hz"
            if why:
                reason = f"does not compile to Pronto: {why}"
                report.keys["skipped: does not compile"] += 1
                report.skipped_keys.append((path, entry.name, reason))
                continue
            groups[group].append((entry, signal))
        if not groups:
            report.files["skipped: no entry yields a key"] += 1
            report.skipped_files.append((path, "no entry yields a key"))
            return []
        brand, model = names_of(path, self.categories)
        stem = path.rsplit("/", 1)[-1].removesuffix(".ir")
        out = []
        for group in sorted(groups):
            suffix = f" [{group}]" if len(groups) > 1 else ""
            full = f"{model}{suffix}"
            if (brand.casefold(), full.casefold()) in self._models:
                full = f"{model} ({stem}){suffix}"
            while (brand.casefold(), full.casefold()) in self.authored or (brand.casefold(), full.casefold()) in self._models:
                renamed = f"{full} (Flipper-IRDB)"
                report.renamed.append((path, renamed, self.authored.get((brand.casefold(), full.casefold()), "this import")))
                full = renamed
            target = (f"{IMPORT_ROOT}/{slug(brand)}/{slug(path.split('/')[0])}-{slug(stem)}"
                      f"{('-' + slug(group).lower()) if len(groups) > 1 else ''}.json")
            self._models[(brand.casefold(), full.casefold())] = target
            out.append((target, self.document(path, brand, full, group, groups[group])))
        return out

    def document(self, path: str, brand: str, model: str, group: str, members: list[tuple[ir.Entry, mapping.Signal | None]]) -> dict[str, Any]:
        report = self.report
        kept: list[tuple[ir.Entry, mapping.Signal | None]] = []
        seen: set[tuple[str, tuple]] = set()
        for entry, signal in members:
            code = (signal.protocol, signal.device, signal.subdevice, signal.function) if signal else ("raw", entry.frequency, entry.data)
            if (entry.name.casefold(), code) in seen:
                report.repeated.append((path, entry.name))
                continue
            seen.add((entry.name.casefold(), code))
            kept.append((entry, signal))
        keys: dict[str, dict[str, Any]] = {}
        for (entry, signal), name in zip(kept, key_names([e for e, _ in kept])):
            form = _form(entry, group, signal, self.citation(path, entry, signal))
            keys[name] = {"label": entry.name, "forms": [form]} if entry.name else {"forms": [form]}
            report.keys[f"imported: {group}"] += 1
        report.remotes["imported"] += 1
        report.protocols[group] += 1
        return {"manufacturer": brand, "model": model, "aliases": [], "controls": [model if model.casefold().startswith(brand.casefold()) else f"{brand} {model}"],
                "protocol": (raw_block(int(group.split()[1]), min(d for e, _ in kept for d in e.data), f"{UPSTREAM}@{self.sha} {path}: the raw data of "
                                       "its buttons") if group.startswith("raw ") else block(group)), "keys": keys}


# --- the whole snapshot --------------------------------------------------------------------------


def import_tree(snapshot: Snapshot, authored: dict[tuple[str, str], str]):
    """Every remote file the snapshot yields, as ``{repo path: document}``, plus the report."""
    report = Report(commit=snapshot.commit)
    importer = Importer(snapshot, authored, report)
    out: dict[str, dict[str, Any]] = {}
    for path, text in sorted(snapshot.files.items()):
        report.files["files"] += 1
        for target, doc in importer.import_file(path, text):
            base, n = target.removesuffix(".json"), 2
            while target in out:
                target, n = f"{base}-{n}.json", n + 1
            out[target] = doc
    return out, report


def write_import(root: Path, snapshot_dir: Path) -> Report:
    """Rewrite ``remotes/flipper-irdb/`` wholesale: every ``*.json`` and ``IMPORT.md`` are the importer's; anything else there
    (``README.md``) is authored and left alone."""
    snapshot = load_snapshot(snapshot_dir)
    docs, report = import_tree(snapshot, _authored_names(root, IMPORT_ROOT))
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
    (target_root / REPORT).write_text(report.render(snapshot.manifest), encoding="utf-8", newline="\n")
    for empty in sorted((p for p in target_root.rglob("*") if p.is_dir()), reverse=True):
        if not any(empty.iterdir()):
            empty.rmdir()
    return report
