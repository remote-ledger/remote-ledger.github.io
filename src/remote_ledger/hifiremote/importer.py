"""``rl import hifi-remote``: hifi-remote.com's Sony code pages, imported under R19.

DESIGN.md section 27 (D109 to D115) is the contract. The source is a person's compilation of
Sony's infrared command numbers, one page per kind of device, each page a few
tables of ``Command Code | Command(s)`` headed by the device codes they hold for.
It has no remote model and no capture: a table says that a device code answers
to a command number, and that is all this import records.

Every table becomes one file per device code it is headed with; every row
becomes a key carrying a Sony12, Sony15 or Sony20 ``irp`` form, Plausible, and a
citation that names the page, its SHA-256, the table, the row and the marker
line. The pages are not a git repository, so the pin is a snapshot directory
(``sources/hifi-remote``, written by ``tools/fetch_hifi_remote.py``); the output
is a pure function of that directory (R19.5, D111).
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
from ..irblaster.importer import fold_label
from ..protocols import REGISTRY
from ..serialize import dumps
from .pages import DEFAULT_COLOUR, Page, Row, Table, read_page

UPSTREAM = "hifi-remote.com/sony"
UPSTREAM_URL = "https://www.hifi-remote.com/sony/"
IMPORT_ROOT = "remotes/hifi-remote"
#: Where the snapshot is kept, relative to the repository (D111).
SNAPSHOT = "sources/hifi-remote"
MANIFEST = "MANIFEST.json"
REPORT = "IMPORT.md"
MAKER = "Sony"
MAKER_DIR = "sony"
#: The pages are Word's output and say so in their ``<meta>``.
ENCODING = "cp1252"
#: R19.3: nothing is imported above Plausible. A row the page itself doubts is
#: Untested, the tier for a candidate nobody has confirmed (SPEC section 5).
TIER = "plausible"
DOUBTFUL_TIER = "untested"
#: SIRC repeats a frame at least three times for one press (RMT-B118P's file, D3).
MIN_SENDS = 3
#: A command number is seven bits in all three frame shapes.
MAX_COMMAND = 127
HEADER = ["Command Code", "Command(s)"]

#: What a colour on a command says, as the page's own legend words it, and the
#: device codes it limits the command to (None: the legend only describes the
#: command, and every device code of the table has it). A colour is the page's
#: annotation; only the Blu-ray page's legend says "this device and no other"
#: (D113). Keys are ``(page, css colour)``.
LIMITED_TO: dict[tuple[str, str], frozenset[str]] = {
    ("Sony_bluray.htm", "fuchsia"): frozenset({"26.135"}),
    ("Sony_bluray.htm", "#3366ff"): frozenset({"26.164"}),
}
#: The legend's colour names as Word writes them in a style attribute.
COLOUR_NAMES = {
    "Magenta": "fuchsia", "Fuchsia": "fuchsia", "Blue": "#3366ff", "Olive": "olive",
    "Orange": "#ff6600", "Green": "lime", "Pink": "#ff99cc", "Lavender": "#cc99ff",
    "Light Blue": "aqua", "Red": "red",
}
_LEGEND = re.compile(r"^(Magenta|Fuchsia|Blue|Olive|Orange|Green|Pink|Lavender|Light Blue|Red)\s*=\s*(.+)$")
#: ``Sony:26.226; 26.234`` and ``Sony15:26`` / ``Sony 15 :26``: the optional bit
#: count is the page saying which frame the codes are for.
_MARKER = re.compile(r"^Sony\s*(12|15|20)?\s*:\s*(.+)$")
_CODE = re.compile(r"[\s;,]*(\(\s*)?(\d+)(?:\.(\d+))?(\s*\))?(?=[\s;,()]|$)")
_TITLE = re.compile(r"^Sony\s+(?P<type>.+?)\s*(?:\(\s*\d[\d.,;\s]*\))?\s*$")
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2212": "-"})
_DISCRETE = re.compile(r"\s*\(discrete\)\s*$", re.I)
_DOUBT = re.compile(r"probably incorrect", re.I)


@dataclass(frozen=True)
class Code:
    device: int
    subdevice: int | None
    #: The page writes the code in parentheses (an AV2-mode code it has not confirmed).
    parenthesised: bool

    @property
    def text(self) -> str:
        return str(self.device) if self.subdevice is None else f"{self.device}.{self.subdevice}"


@dataclass(frozen=True)
class Marker:
    text: str
    bits: int | None
    codes: tuple[Code, ...]


@dataclass(frozen=True)
class Alternative:
    """One name a command goes by, with the annotation colours it is written in."""

    text: str
    colours: frozenset[str]


@dataclass
class Report:
    retrieved: str
    pages: Counter = field(default_factory=Counter)
    tables: Counter = field(default_factory=Counter)
    remotes: Counter = field(default_factory=Counter)
    keys: Counter = field(default_factory=Counter)
    #: (page, reason) for pages that yield nothing.
    skipped_pages: list[tuple[str, str]] = field(default_factory=list)
    #: (page, table, reason) for tables that yield nothing.
    skipped_tables: list[tuple[str, int, str]] = field(default_factory=list)
    #: (page, table, command code, text, reason) for rows that yield nothing.
    skipped_rows: list[tuple[str, int, str, str, str]] = field(default_factory=list)
    #: (page, device code, reason) for device codes that yield nothing.
    skipped_codes: list[tuple[str, str, str]] = field(default_factory=list)
    #: (page, table, target path) for authored-data collisions.
    collisions: list[tuple[str, int, str]] = field(default_factory=list)
    #: protocol -> files
    protocols: Counter = field(default_factory=Counter)
    #: colour -> (rows that carry it, meaning), by page
    colours: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    legends: dict[str, str] = field(default_factory=dict)
    #: rows a colour limited to other device codes, by page
    limited: Counter = field(default_factory=Counter)

    def render(self) -> str:
        out = [
            "# hifi-remote.com Sony import report",
            "",
            "Generated by `rl import hifi-remote` (DESIGN.md section 27); never",
            "hand-edited.",
            f"Upstream: `{UPSTREAM_URL}`, retrieved {self.retrieved}, as the snapshot in",
            f"`{SNAPSHOT}/` (its `{MANIFEST}` holds each page's SHA-256 and server headers).",
            "",
            "Everything the snapshot holds that does not become a file is listed here",
            "with its reason, not dropped silently (SPEC R19, condition 5).",
            "",
            "## Totals",
            "",
            "| | Count |",
            "|---|---|",
        ]
        for label, counter in (("Pages", self.pages), ("Tables", self.tables),
                               ("Files", self.remotes), ("Keys", self.keys),
                               ("Files by protocol", self.protocols)):
            for what, n in sorted(counter.items()):
                out.append(f"| {label}: {what} | {n:,} |")
        out += ["", "## Colours", "",
                "A colour on a command is the page's annotation. The meaning is the page's",
                "own legend line; a colour with no legend is recorded as that, not guessed.",
                "The count is of keys carrying the colour, one per device code of the table.", ""]
        rows = [(page, colour, n, self.legends.get(f"{page} {colour}", "no legend on the page"))
                for page, counter in sorted(self.colours.items())
                for colour, n in sorted(counter.items())]
        out.append("| Page | Colour | Keys | Legend |")
        out.append("|---|---|---|---|")
        for page, colour, n, legend in rows:
            out.append(f"| `{page}` | `{colour}` | {n:,} | {_md_text(legend)} |")
        if self.limited:
            out += ["", "Rows a colour limits to other device codes of the same table, left out",
                    "of the files they do not apply to (one per row and device code): "
                    + ", ".join(f"{page} {n:,}" for page, n in sorted(self.limited.items())) + "."]
        sections = (
            ("Pages that yield nothing", ["Page", "Reason"], sorted(self.skipped_pages)),
            ("Tables that yield nothing", ["Page", "Table", "Reason"], sorted(self.skipped_tables)),
            ("Device codes that yield nothing", ["Page", "Device code", "Reason"],
             sorted(self.skipped_codes)),
            ("Files skipped because an authored remote wins (R19, condition 4)",
             ["Page", "Table", "Would have been"], sorted(self.collisions)),
            ("Rows skipped", ["Page", "Table", "Command code", "Text", "Reason"],
             sorted(self.skipped_rows)),
        )
        for title, header, body in sections:
            out += ["", f"## {title} ({len(body):,})", ""]
            if not body:
                out.append("None.")
                continue
            out.append("| " + " | ".join(header) + " |")
            out.append("|" + "---|" * len(header))
            for row in body:
                out.append("| " + " | ".join(_md(v) for v in row) + " |")
        return "\n".join(out) + "\n"


def _md_text(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _md(value: Any) -> str:
    text = _md_text(str(value))
    return f"`{text}`" if text and not text[0].isdigit() else text


# --- the snapshot ---------------------------------------------------------------------


def load_snapshot(directory: Path) -> tuple[str, dict[str, bytes]]:
    """``(retrieval date, {page name: bytes})``, every page checked against the
    manifest's SHA-256 so that a changed page cannot pass for the pinned one."""
    path = directory / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: cannot read the snapshot manifest: {exc}") from exc
    pages: dict[str, bytes] = {}
    for name, entry in sorted(manifest["pages"].items()):
        try:
            data = (directory / name).read_bytes()
        except OSError as exc:
            raise ValidationError(f"{directory / name}: listed in the manifest but unreadable: {exc}") from exc
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValidationError(
                f"{directory / name} is not the page the manifest pins (SHA-256 differs); "
                "run tools/fetch_hifi_remote.py again to re-pin, or restore the page")
        pages[name] = data
    return manifest["retrieved"], pages


# --- reading a marker, a cell, a title -------------------------------------------------


def parse_marker(text: str) -> Marker | None:
    """``Sony:26.226; 26.234 (note)`` as its codes, or None if ``text`` is not a marker.

    The codes are the run of numbers at the start; what follows the first thing
    that is not a code is the author's remark, kept in the text for the citation.
    """
    m = _MARKER.match(text)
    if m is None:
        return None
    rest = m[2]
    codes: list[Code] = []
    at = 0
    while True:
        c = _CODE.match(rest, at)
        if c is None:
            break
        codes.append(Code(int(c[2]), int(c[3]) if c[3] is not None else None, bool(c[1])))
        at = c.end()
    return Marker(text, int(m[1]) if m[1] else None, tuple(codes))


def protocol_of(bits: int | None, code: Code) -> tuple[str | None, str | None]:
    """``(protocol, None)`` or ``(None, why not)``.

    The page's own bit count when it gives one; a dotted code is Sony20; a bare
    one is the 12-bit frame below 32 and the 15-bit frame from 32 up, because
    the 12-bit frame's device field is five bits and the 15-bit one's is eight.
    """
    if bits is not None:
        name = f"Sony{bits}"
    elif code.subdevice is not None:
        name = "Sony20"
    else:
        name = "Sony12" if code.device < 32 else "Sony15"
    device_max = 31 if name in ("Sony12", "Sony20") else 255
    if code.device > device_max:
        return None, f"device {code.device} does not fit {name}'s device field (0 to {device_max})"
    if name == "Sony20" and (code.subdevice is None or code.subdevice > 255):
        return None, "Sony20 needs a subdevice from 0 to 255"
    if name != "Sony20" and code.subdevice is not None:
        return None, f"{name} has no subdevice"
    return name, None


def type_of(page: Page) -> str:
    """The kind of device a page is about: its title without ``Sony`` and the
    device codes. A title that says nothing (``Sony x (x)``) is replaced by the
    page's first line."""
    for text in (page.title, page.intro[0] if page.intro else ""):
        m = _TITLE.match(text)
        # `Sony x (x)` is a title that says nothing; `Sony TV (1, 164)` is not
        if m and len(re.sub(r"\([^)]*\)", "", m["type"]).strip()) >= 2:
            return m["type"].strip()
    return "device"


def split_alternatives(spans: list) -> list[Alternative]:
    """A command cell's names, split at commas outside brackets, each with the
    annotation colours its text is written in."""
    chars: list[tuple[str, str]] = []
    for span in spans:
        chars += [(ch, span.colour) for ch in span.text]
    out: list[Alternative] = []
    depth = 0
    cur: list[tuple[str, str]] = []

    def close() -> None:
        text = re.sub(r"\s+", " ", "".join(c for c, _ in cur)).strip()
        colours = frozenset(col for c, col in cur if not c.isspace() and col != DEFAULT_COLOUR)
        if text:
            out.append(Alternative(text, colours))
        cur.clear()

    for ch, colour in chars:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            close()
        else:
            cur.append((ch, colour))
    close()
    return out


def legends_of(page: Page, name: str, report: Report) -> dict[str, str]:
    """``{css colour: the legend's words}`` from the page's prose."""
    found: dict[str, str] = {}
    for paragraph in page.intro:
        m = _LEGEND.match(paragraph)
        if m:
            colour = COLOUR_NAMES[m[1]]
            found[colour] = paragraph
            report.legends[f"{name} {colour}"] = paragraph
    return found


def label_of(text: str) -> str:
    """The label a name is shown as: the page's words, dashes made plain, and a
    trailing ``(discrete)`` left to the citation, so that ``Power On (discrete)``
    is the key the vocabulary calls ``POWER_ON`` (D84)."""
    return _DISCRETE.sub("", text.translate(_DASHES)).strip()


# --- one table, one device code ---------------------------------------------------------


def _key_names(members: list[tuple[int, str]]) -> dict[int, str]:
    """``{command code: key name}``: ``KEY_<folded label>``, and where two codes
    fold alike every one of them gets ``_<code>``, so a name does not depend on
    the rows' order (as D49's does for the IR Blaster import)."""
    base = {code: "KEY_" + (fold_label(label) or "COMMAND") for code, label in members}
    carriers: dict[str, list[int]] = defaultdict(list)
    for code, name in base.items():
        carriers[name].append(code)
    return {code: (f"{name}_{code}" if len(carriers[name]) > 1 or name == "KEY_COMMAND" else name)
            for code, name in base.items()}


class Importer:
    def __init__(self, retrieved: str, authored: dict[tuple[str, str], str], report: Report):
        self.retrieved = retrieved
        self.authored = authored
        self.report = report
        self._compiles: dict[tuple, str | None] = {}

    def gate(self, protocol: str, code: Code, function: int) -> str | None:
        signature = (protocol, code.device, code.subdevice, function)
        if signature not in self._compiles:
            form: dict[str, Any] = {"id": "primary.irp", "type": "irp", "device": code.device,
                                    "function": function, "confidence": TIER, "source": "probe"}
            if code.subdevice is not None:
                form["subdevice"] = code.subdevice
            block = {"name": protocol, "carrierHz": REGISTRY[protocol].nominal_carrier_hz,
                     "minSends": MIN_SENDS}
            self._compiles[signature] = form_compiles(IMPORT_ROOT, block, "K", form)
        return self._compiles[signature]

    def row_for(self, page_name: str, sha: str, table_no: int, marker: Marker, row: Row,
                code: Code, protocol: str, inferred: bool, legends: dict[str, str]
                ) -> tuple[str, str, str, dict[str, Any]] | None:
        """``(label, command text key, tier, form fields)`` for one row on one
        device code, or None when the row is not for this device code or does not
        parse. Reports its own skips."""
        report = self.report
        where = (page_name, table_no, row.code_text, row.text)
        if not re.fullmatch(r"\d{1,3}", row.code_text) or int(row.code_text) > MAX_COMMAND:
            return None  # reported once per row by the caller, not once per device code
        function = int(row.code_text)
        alternatives = split_alternatives(row.spans)
        if not alternatives and row.text.strip():
            # a command whose name is a punctuation mark (the MD keyboard's comma)
            alternatives = [Alternative(row.text.strip(), frozenset())]
        applicable = []
        for alt in alternatives:
            limits = [LIMITED_TO[(page_name, c)] for c in alt.colours if (page_name, c) in LIMITED_TO]
            if all(code.text in limit for limit in limits):
                applicable.append(alt)
        if not alternatives:
            report.skipped_rows.append((*where, "the command has no name"))
            return None
        if not applicable:
            report.limited[page_name] += 1
            return None
        first = applicable[0]
        label = label_of(first.text)
        if not label:
            report.skipped_rows.append((*where, "no name left after tidying"))
            return None
        why = self.gate(protocol, code, function)
        if why:
            report.skipped_rows.append((*where, f"does not compile to Pronto: {why}"))
            return None

        notes: list[str] = []
        if _DISCRETE.search(first.text.translate(_DASHES)):
            notes.append("the page writes it '(discrete)'")
        others = [a.text for a in applicable[1:]]
        if others:
            notes.append("also named " + ", ".join(f"'{t}'" for t in others))
        used = sorted({c for a in applicable for c in a.colours})
        doubtful = bool(_DOUBT.search(row.text))
        for colour in used:
            meaning = legends.get(colour)
            if meaning is None:
                notes.append(f"the page colours it {colour} and gives no legend")
            else:
                notes.append(f"the page's legend: '{meaning}'")
                doubtful = doubtful or bool(_DOUBT.search(meaning))
            report.colours[page_name][colour] += 1
        if code.parenthesised:
            notes.append("the page writes this device code in parentheses")
        reading = f"{protocol} device {code.device}"
        if code.subdevice is not None:
            reading += f" subdevice {code.subdevice}"
        reading += f" function {function}"
        if inferred:
            reading += " (the frame is inferred from the size of the device code)"
        source = (f"{UPSTREAM}/{page_name}@{sha[:8]} (retrieved {self.retrieved}) table {table_no}, "
                  f"command {function} '{row.text}' under '{marker.text}': {reading}")
        if notes:
            source += "; " + "; ".join(notes)
        form: dict[str, Any] = {"id": "primary.irp", "type": "irp", "device": code.device}
        if code.subdevice is not None:
            form["subdevice"] = code.subdevice
        form["function"] = function
        form["confidence"] = DOUBTFUL_TIER if doubtful else TIER
        form["source"] = source
        return label, str(function), form["confidence"], form

    def tables_of(self, page_name: str, data: bytes) -> list[tuple[str, str, dict[str, Any]]]:
        """Every file one page yields, as ``(target path, model, document)``."""
        report = self.report
        sha = hashlib.sha256(data).hexdigest()
        page = read_page(data.decode(ENCODING, errors="strict"))
        tables = [t for t in page.tables if t.header == HEADER]
        if not tables:
            report.skipped_pages.append((page_name, "no `Command Code | Command(s)` table"))
            return []
        report.pages["read"] += 1
        legends = legends_of(page, page_name, report)
        kind = type_of(page)
        stem = page_name.removeprefix("Sony_").removesuffix(".htm")
        out: list[tuple[str, str, dict[str, Any]]] = []
        for number, table in enumerate(page.tables, start=1):
            if table.header != HEADER:
                continue
            markers = [m for text in table.before if (m := parse_marker(text)) is not None]
            report.tables["read"] += 1
            if not markers or not any(m.codes for m in markers):
                report.skipped_tables.append((page_name, number, "no `Sony:<device code>` line before it"))
                continue
            for row in table.rows:
                if not re.fullmatch(r"\d{1,3}", row.code_text) or int(row.code_text) > MAX_COMMAND:
                    report.skipped_rows.append((page_name, number, row.code_text, row.text,
                                                "the command code is not one number from 0 to 127"))
            for marker in markers:
                for code in marker.codes:
                    protocol, why = protocol_of(marker.bits, code)
                    if protocol is None:
                        report.skipped_codes.append((page_name, code.text, why or ""))
                        continue
                    out_doc = self.document(page_name, sha, number, kind, stem, marker, code,
                                            protocol, table, legends)
                    if out_doc is not None:
                        out.append(out_doc)
        return out

    def document(self, page_name: str, sha: str, number: int, kind: str, stem: str,
                 marker: Marker, code: Code, protocol: str, table: Table,
                 legends: dict[str, str]) -> tuple[str, str, dict[str, Any]] | None:
        report = self.report
        model = f"{kind} {code.text}"
        target = f"{IMPORT_ROOT}/{MAKER_DIR}/{stem}-{code.text}.json"
        if (MAKER.casefold(), model.casefold()) in self.authored:
            report.remotes["skipped: an authored remote wins"] += 1
            report.collisions.append((page_name, number, target))
            return None
        inferred = marker.bits is None and code.subdevice is None
        rows = [r for r in (self.row_for(page_name, sha, number, marker, row, code, protocol,
                                         inferred, legends) for row in table.rows) if r]
        if not rows:
            report.skipped_codes.append((page_name, code.text, "no row of its table applies to it"))
            return None
        names = _key_names([(int(form["function"]), label) for label, _c, _t, form in rows])
        keys: dict[str, dict[str, Any]] = {}
        for label, _command, _tier, form in rows:
            keys[names[form["function"]]] = {"label": label, "forms": [form]}
            report.keys["imported: " + protocol] += 1
        report.remotes["imported"] += 1
        report.protocols[protocol] += 1
        doc: dict[str, Any] = {
            "manufacturer": MAKER, "model": model, "aliases": [],
            "controls": [f"{MAKER} {kind} (device code {code.text})"],
            "protocol": {"name": protocol, "carrierHz": REGISTRY[protocol].nominal_carrier_hz,
                         "minSends": MIN_SENDS},
            "keys": keys,
        }
        return target, model, doc


# --- the whole snapshot -----------------------------------------------------------------


def import_tree(directory: Path, authored: dict[tuple[str, str], str]):
    """Every file the snapshot yields, as ``{repo path: document}``, plus the report."""
    retrieved, pages = load_snapshot(directory)
    report = Report(retrieved=retrieved)
    importer = Importer(retrieved, authored, report)
    out: dict[str, dict[str, Any]] = {}
    models: dict[str, str] = {}
    for name, data in pages.items():
        if name == "index.htm":
            report.skipped_pages.append((name, "the index of the pages, not a device"))
            continue
        for target, model, doc in importer.tables_of(name, data):
            if target in out or model.casefold() in models:
                # two tables of one page for one device code: the second would
                # overwrite the first, or share its model name (R15)
                raise ValidationError(
                    f"{name}: {target} / {model!r} is yielded twice; the snapshot has a table "
                    "the importer does not tell apart")
            out[target] = doc
            models[model.casefold()] = target
    return out, report


def write_import(root: Path, snapshot: Path | None = None) -> Report:
    """Rewrite ``remotes/hifi-remote/`` wholesale: every ``*.json`` and
    ``IMPORT.md`` are the importer's; anything else there (``README.md``) is
    authored and left alone."""
    directory = snapshot if snapshot is not None else root / SNAPSHOT
    docs, report = import_tree(directory, _authored_names(root, IMPORT_ROOT))
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
