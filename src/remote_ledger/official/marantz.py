"""Marantz's AV receiver and processor command charts (``marantz-master-ir.xls`` and its successors).

Each workbook's ``AVR Commands`` sheet is a chart: a row per command with its RC-5 *system*, *command*
and *extension* (Marantz's RC-5x adds an extension byte), a column per model with an ``X`` where the
model accepts it, and on the right, for the basic commands, the **Pronto hex** Marantz publishes. The
Pronto strings are the manufacturer's own signal, extension commands included, so they are imported
verbatim, as ``pronto`` forms. A command with no hex and no extension is plain RC-5 and is imported as
an ``irp`` RC5 form; **one with an extension and no hex is RC-5x** (``RC5x``, DESIGN section 57) and is
imported as an ``irp`` RC5x form: the system is D, the command S and the extension F, as the 367 of the
chart's 395 extension rows that carry their own hex read (D226, D227). An RC5x file is its own file
(a file names one protocol): ``<model> [RC-5x]``, which only carries the model's own name when the model
has nothing else.

The three workbooks overlap (the later one lists many of the earlier one's models). A model takes its
file from the **newest** workbook that lists it.

**The direct-access sheets** (``Direct Vol.`` and four more, DESIGN section 60) are the same kind of chart for
the commands that set a volume level, a preset or a channel in one command: a row per value with its
system, command and extension, a column per model. Each row is an ``irp`` RC5x form like an extension row of
the main chart, and a model's rows are a file of their own, ``<model> [direct]``, so that its main files
keep the keys a person presses. A model takes each sheet from the newest workbook that lists it in *that* sheet.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from ..protocols import REGISTRY
from ..pronto import ProntoParseError, _word_to_hz, parse_words
from .common import IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "marantz"
BRAND = "Marantz"
HOST = "marantz.com archive-downloads"
SHEET = "AVR Commands"
#: Oldest first: a later workbook replaces an earlier one's file for a model both list.
DOCUMENTS = ("marantz-master-ir.xls", "marantz-2014-ir-command-sheet.xls",
             "marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls", "Marantz_FY20_SR_NR_IR_CODE_V03_20190827182355425.xls")
#: where a document is from, when it is not marantz.com's archive downloads: the FY20 workbook is the Wayback Machine's copy of SnapAV's (DESIGN section 89)
HOSTS = {"Marantz_FY20_SR_NR_IR_CODE_V03_20190827182355425.xls": "snapav.com Marantz IR code workbook"}
_PRONTO = re.compile(r"^(?:[0-9A-Fa-f]{4} ?)+$")
_SUPPORTED = ("X", "@")
RC5_WORD = 0x0073


@dataclass(frozen=True)
class Model:
    column: int
    name: str
    aliases: tuple[str, ...]
    category: str


@dataclass(frozen=True)
class Command:
    row: int          # 1-based, as the sheet numbers it
    name: str
    system: str
    command: str
    extension: str
    hex: str
    function: str
    marks: dict[int, str]
    #: the sheet a direct-access row is from (``""`` for the main chart) and the zone its block is for
    sheet: str = ""
    zone: str = ""

    @property
    def has_extension(self) -> bool:
        return self.extension not in ("", "---")

    @property
    def triple(self) -> str:
        return " ".join(p for p in (self.system, self.command, "" if not self.has_extension else self.extension) if p)


def _cell(row: list[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def parse_sheet(rows: list[list[str]]) -> tuple[list[Model], list[Command]]:
    """The models (columns) and commands (rows) of one ``AVR Commands`` sheet."""
    header = next(i for i, r in enumerate(rows) if {"Command Name", "System", "Command", "Extension"} <= {c.strip() for c in r})
    labels = [c.strip() for c in rows[header]]
    at = {label: labels.index(label) for label in ("Command Name", "System", "Command", "Extension")}
    first = max(at["Extension"], labels.index("For search") if "For search" in labels else 0) + 1
    stops = [i for i, label in enumerate(labels) if label == "FUNCTION" or label.startswith("Hex Code")]
    stop = min(stops) if stops else len(labels)
    hex_col = next((i for i, label in enumerate(labels) if label.startswith("Hex Code")), None)
    function_col = labels.index("FUNCTION") if "FUNCTION" in labels else None
    groups = rows[header - 1] if header else []
    models = []
    for column in range(first, stop):
        names = [n.strip() for n in labels[column].split("\n") if n.strip()]
        if names and not labels[column].startswith("For "):
            category = " ".join(_cell(groups, column).split())
            models.append(Model(column, names[0], tuple(names[1:]), category))
    commands: list[Command] = []
    last_name = ""
    for r in range(header + 1, len(rows)):
        row = rows[r]
        name, system, command = (_cell(row, at[k]) for k in ("Command Name", "System", "Command"))
        if name:
            last_name = name
        if not (system or command):
            continue
        name = name or last_name        # a command written on two lines: its name is on the first
        if not name:
            continue
        marks = {m.column: _cell(row, m.column) for m in models}
        commands.append(Command(
            r + 1, " ".join(name.split()), system, command, _cell(row, at["Extension"]),
            " ".join(_cell(row, hex_col).split()), " ".join(_cell(row, function_col).split()), marks))
    return models, commands


#: The direct-access sheets, and what a row of each is called.
DIRECT_SHEETS = {
    "Direct Vol.": "Direct volume",
    "Zone Speaker Direct Vol.": "Zone speaker direct volume",
    "AM FM Preset Direct": "AM/FM preset",
    "XM SIRIUS Preset Direct": "XM/SIRIUS preset",
    "XM SIRIUS Ch. Direct": "XM/SIRIUS channel",
}
#: What a mark of a model's column says it supports: ``X``, ``X/S`` (XM and Sirius), ``S`` (Sirius), ``X(-∞)``; ``-`` and ``---`` say it does not.
_DIRECT_SUPPORTED = ("X", "S", "@")


def parse_direct_sheet(rows: list[list[str]], sheet: str) -> tuple[list[Model], list[Command]]:
    """The models (columns) and commands (rows) of one direct-access sheet: ``Zone, <value>, System, Command, Extension`` then a
    column per model. A zone is written down the first column a letter-group at a time (``MAIN``, ``ZONE``), so a row's zone is
    the group that last started."""
    header = next(i for i, r in enumerate(rows) if _cell(r, 0) == "Zone" and _cell(r, 2) == "Command")
    labels = rows[header]
    models = []
    for column in range(5, len(labels)):
        names = [n.strip() for n in labels[column].split("\n") if n.strip()]
        if names:
            models.append(Model(column, names[0], tuple(names[1:]), ""))
    kind = DIRECT_SHEETS[sheet]
    # the zones first: a group of cells in the first column, one under the other, is one label that holds until the next group starts
    groups: list[tuple[int, str]] = []
    cells: list[str] = []
    for r in range(header + 1, len(rows)):
        cell = _cell(rows[r], 0)
        if cell and cells and _cell(rows[r - 1], 0):
            cells.append(cell)
        elif cell:
            cells = [cell]
            groups.append((r, ""))
        if cell and groups:
            groups[-1] = (groups[-1][0], " ".join(cells))
    commands: list[Command] = []
    for r in range(header + 1, len(rows)):
        row = rows[r]
        system, command, extension, value = _cell(row, 2), _cell(row, 3), _cell(row, 4), _cell(row, 1)
        if not (system.isdigit() and command.isdigit()):
            continue
        zone = next((label for start, label in reversed(groups) if start <= r), "")
        marks = {m.column: _cell(row, m.column) for m in models}
        commands.append(Command(r + 1, " ".join(f"{kind} {zone} {value}".split()), system, command, extension, "", "", marks, sheet, zone))
    return models, commands


def _int(text: str) -> int | None:
    return int(text) if re.fullmatch(r"\d{1,3}", text) else None


def key_names(commands: list[Command]) -> dict[int, str]:
    """``{row: key name}``: ``KEY_<folded name>``; where names fold alike each gets its codes."""
    base = {c.row: "KEY_" + (fold_label(c.name) or "COMMAND") for c in commands}
    carriers: dict[str, list[int]] = defaultdict(list)
    for row, name in base.items():
        carriers[name].append(row)
    out: dict[int, str] = {}
    taken: set[str] = set()
    by_row = {c.row: c for c in commands}
    for row, name in sorted(base.items()):
        if len(carriers[name]) > 1:
            name = f"{name}_{by_row[row].triple.replace(' ', '_')}"
        candidate, n = name, 2
        while candidate in taken:
            candidate, n = f"{name}_{n}", n + 1
        taken.add(candidate)
        out[row] = candidate
    return out


def where(snapshot: Snapshot, document: str) -> str:
    """``host/document@hash (retrieved date[, from the Wayback Machine snapshot ...])``: a document fetched later than the rest has its own date, and a copy of
    the Wayback Machine's names its snapshot."""
    entry = snapshot.documents[document]
    return (f"{HOSTS.get(document, HOST)}/{document}@{snapshot.sha(document)} (retrieved {entry.get('retrieved', snapshot.retrieved)}"
            + (f", from the Wayback Machine snapshot {entry['snapshot']}" if "snapshot" in entry else "") + ")")


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    """Every file the maker's documents yield, as ``(repo path, document)``."""
    refused: dict[tuple[str, int], str] = {}
    # model -> (document, Model, commands of that document)
    chosen: dict[str, tuple[str, Model, list[Command]]] = {}
    for document in DOCUMENTS:
        models, commands = parse_sheet(snapshot.sheet_of(document, SHEET))
        report.count(MAKER, f"{document}: commands", len(commands))
        for model in models:
            chosen[model.name.casefold()] = (document, model, commands)
    gate: dict[tuple, str | None] = {}
    direct = _direct_rows(snapshot, chosen, report)
    direct_refused: set[tuple[str, str, int, str]] = set()      # (document, sheet, row, reason): a row is one finding, whatever models list it
    out: list[tuple[str, dict[str, Any]]] = []
    for key in sorted(chosen):
        document, model, commands = chosen[key]
        files = _model_files(snapshot, document, model, commands, report, gate, refused)
        files += _direct_files(snapshot, model, direct.get(key, []), report, gate, direct_refused)
        for target, doc in files:
            if (BRAND.casefold(), doc["model"].casefold()) in authored:
                report.collisions.append((MAKER, target))
                continue
            out.append((target, doc))
    for document, _sheet, _row, reason in sorted(direct_refused):
        report.unrepresented[(MAKER, document, reason)] += 1
    # a row no model can use is one finding, however many models list it
    by_doc = {d: {c.row: c for c in parse_sheet(snapshot.sheet_of(d, SHEET))[1]} for d in DOCUMENTS}
    for (document, row), reason in sorted(refused.items()):
        report.unrepresented[(MAKER, document, reason)] += 1
        if reason.startswith("the chart's Pronto hex"):
            report.notes.append((MAKER, f"{document} row {row}", f"'{by_doc[document][row].name}': {reason}"))
    return out


def _direct_rows(snapshot: Snapshot, chosen: dict[str, tuple[str, Model, list[Command]]],
                 report: Report) -> dict[str, list[tuple[str, Model, Command]]]:
    """``{model key: [(document, the model's column of the sheet, row)]}`` for the direct-access sheets. A column names the model
    and its aliases (``SR6004 SR5004``) and a model may be named by any of them; a model takes each sheet from the newest workbook
    that lists it in that sheet; the one column of the 2015 workbook, ``FY18AVR``, is every model of that workbook's chart."""
    by_name: dict[str, str] = {}
    for key, (_document, model, _commands) in chosen.items():
        by_name[model.name.casefold()] = key
    for key, (_document, model, _commands) in chosen.items():
        for alias in model.aliases:
            by_name.setdefault(alias.casefold(), key)
    per_sheet: dict[tuple[str, str], tuple[str, Model, list[Command]]] = {}
    for document in DOCUMENTS:        # oldest first: a newer workbook's sheet replaces an older one's for a model
        for entry in snapshot.sheets.values():
            sheet = entry["sheet"]
            if entry["document"] != document or sheet not in DIRECT_SHEETS:
                continue
            models, commands = parse_direct_sheet(snapshot.sheet_of(document, sheet), sheet)
            report.count(MAKER, f"{document}: {sheet}: rows", len(commands))
            for model in models:
                names = [model.name, *model.aliases]
                if model.name == "FY18AVR":
                    keys = [k for k, (d, _m, _c) in chosen.items() if d == document]
                else:
                    keys = sorted({by_name[n.casefold()] for n in names if n.casefold() in by_name})
                if not keys:
                    report.notes.append((MAKER, f"{document}: {sheet}", f"the column of {' '.join(names)} names no model of the AVR Commands charts"))
                for key in keys:
                    per_sheet[(key, sheet)] = (document, model, commands)
    out: dict[str, list[tuple[str, Model, Command]]] = defaultdict(list)
    for (key, sheet), (document, model, commands) in sorted(per_sheet.items()):
        for command in commands:
            out[key].append((document, model, command))
    return out


def _direct_files(snapshot: Snapshot, model: Model, rows: list[tuple[str, Model, Command]], report: Report,
                  gate: dict[tuple, str | None], refused: set[tuple[str, str, int, str]]) -> list[tuple[str, dict[str, Any]]]:
    """The file of a model's direct-access commands: ``<model> [direct]``, one ``irp`` RC5x form a command the model's column marks."""
    members: list[tuple[Command, dict[str, Any]]] = []
    seen: set[tuple[str, str, str]] = set()
    cited: dict[int, tuple[str, str]] = {}          # the position of a member -> (document, the column's header)
    for document, column, command in rows:
        mark = command.marks.get(column.column, "")
        if not mark.upper().startswith(_DIRECT_SUPPORTED):
            continue
        system, cmd, extension = _int(command.system), _int(command.command), _int(command.extension)
        if system is None or cmd is None or extension is None or system > 31 or cmd > 127 or extension > 63:
            # `59-63` on the row of -∞ dB: one row for five values that all mean the same, which is a range and not a key
            ranged = re.search(r"\d\D+\d", command.extension) is not None
            refused.add((document, command.sheet, command.row, "an RC-5x extension that is a range of values, a command for each" if ranged
                         else "a direct-access row whose system, command or extension the ledger cannot send"))
            continue
        triple = (command.system, command.command, command.extension)
        if triple in seen:
            report.count(MAKER, "direct rows repeated by another sheet of the same model")
            continue
        seen.add(triple)
        form = {"id": "primary.irp", "type": "irp", "device": system, "subdevice": cmd, "function": extension}
        position = len(members)
        sha = snapshot.sha(document)
        how = (f"RC-5x system {system} command {cmd} extension {extension}, read as RC5x device {system} subdevice {cmd} function {extension}"
               f" (the chart's extension is decimal)")
        form["source"] = (f"{where(snapshot, document)} sheet '{command.sheet}' row {command.row} "
                          f"'{command.name}' (RC-5 {command.triple}), the column of {' '.join([column.name, *column.aliases])}: {how}")
        if mark.upper() != "X":
            form["source"] += f"; the chart marks it '{mark}'"
        members.append((command, form))
        cited[position] = (document, column.name)
    if not members:
        return []
    protocol = {"name": "RC5x", "carrierHz": 36000, "minSends": 1}
    renumbered = [replace(c, row=i + 1) for i, (c, _f) in enumerate(members)]
    names = key_names(renumbered)
    keys: dict[str, dict[str, Any]] = {}
    for i, (command, form) in enumerate(members):
        full = {**form, "confidence": TIER}
        gated = (RC5_WORD, False, True, "irp", (full["device"], full["subdevice"], full["function"]))
        if gated not in gate:
            gate[gated] = form_compiles(IMPORT_ROOT, protocol, "K", {**full, "source": "probe"})
        if gate[gated]:
            refused.add((cited[i][0], command.sheet, command.row, f"a direct-access row that does not compile: {gate[gated]}"))
            continue
        ordered = {k: full[k] for k in ("id", "type", "device", "subdevice", "function") if k in full}
        ordered["confidence"] = full["confidence"]
        ordered["source"] = full["source"]
        keys[names[i + 1]] = {"label": command.name, "forms": [ordered]}
        report.count(MAKER, "keys")
        report.count(MAKER, "direct keys")
    if not keys:
        return []
    report.count(MAKER, "files")
    report.count(MAKER, "direct files")
    mname = f"{model.name} [direct]"
    controls = [f"{BRAND} {n}" for n in (model.name, *model.aliases)]
    doc = {"manufacturer": BRAND, "model": mname, "aliases": [], "controls": controls, "protocol": protocol, "keys": keys}
    return [(f"{IMPORT_ROOT}/{MAKER}/{slug(mname)}.json", doc)]


def _model_files(snapshot: Snapshot, document: str, model: Model, commands: list[Command], report: Report,
                 gate: dict[tuple, str | None], refused: dict[tuple[str, int], str]) -> list[tuple[str, dict[str, Any]]]:
    sha = snapshot.sha(document)
    # (frequency word, has an intro, RC-5x) -> [(command, form fields, tier)]. One file holds one carrier,
    # and the bundle needs one play rule per file, so strings that differ in either are different files
    # (D78); and one file names one protocol, so the RC5x forms are a file of their own.
    buckets: dict[tuple[int, bool, bool], list[tuple[Command, dict[str, Any], str]]] = defaultdict(list)
    seen: set[tuple] = set()
    for command in commands:
        mark = command.marks.get(model.column, "")
        if not mark.upper().startswith(_SUPPORTED):
            continue
        pronto = command.hex if _PRONTO.match(command.hex) else ""
        word = None
        intro = False
        rc5x = False
        form: dict[str, Any]
        if pronto:
            try:
                words = parse_words(pronto)
            except ProntoParseError as exc:
                refused[(document, command.row)] = f"the chart's Pronto hex does not parse: {exc}"
                continue
            word, intro = words[1], words[2] > 0
            form = {"id": "primary.pronto", "type": "pronto", "hex": " ".join(w.upper() for w in pronto.split())}
            how = "the chart's own Pronto hex"
        else:
            system, cmd = _int(command.system), _int(command.command)
            if system is None or cmd is None or system > 31 or cmd > 127:
                refused[(document, command.row)] = "no RC-5 system and command the ledger can send"
                continue
            word = RC5_WORD
            if command.has_extension:
                extension = _int(command.extension)
                if extension is None or extension > 63:
                    # `00-63`, `01-60`, `00-05`: a range, one row that stands for a command per value (a volume, a preset, a channel)
                    ranged = re.search(r"\d\D+\d", command.extension) is not None
                    refused[(document, command.row)] = ("an RC-5x extension that is a range of values, a command for each"
                                                        if ranged else "no RC-5x extension the ledger can send")
                    continue
                rc5x = True
                form = {"id": "primary.irp", "type": "irp", "device": system, "subdevice": cmd, "function": extension}
                how = (f"RC-5x system {system} command {cmd} extension {extension}, read as RC5x device {system} "
                       f"subdevice {cmd} function {extension} (the chart's extension is decimal)")
            else:
                form = {"id": "primary.irp", "type": "irp", "device": system, "function": cmd}
                how = f"RC-5 system {system} command {cmd} with no extension, read as RC5 device {system} function {cmd}"
        signature = (word, intro, rc5x,
                     form.get("hex") or (form["device"], form.get("subdevice"), form["function"]), command.name)
        if signature in seen:
            continue
        seen.add(signature)
        source = (f"{where(snapshot, document)} sheet '{SHEET}' row {command.row} "
                  f"'{command.name}' (RC-5 {command.triple}), the column of {model.name}: {how}")
        if mark.upper() != "X":
            source += f"; the chart marks it '{mark}'"
        if command.function:
            source += f"; the chart's description: '{command.function}'"
        form["source"] = source
        buckets[(word, intro, rc5x)].append((command, form, TIER))
    if not buckets:
        report.notes.append((MAKER, f"{document}: {model.name}", "the chart gives this model no command it can send"))
        return []
    # the largest bucket carries the model's own name, the RC5x one last whatever its size (so that no file that was
    # there before changes its name); the others say what they are
    order = sorted(buckets, key=lambda k: (k[2], -len(buckets[k]), k))
    files = []
    for rank, (word, intro, rc5x) in enumerate(order):
        members = buckets[(word, intro, rc5x)]
        irp = any(f["type"] == "irp" for _c, f, _t in members)
        protocol: dict[str, Any] = {"carrierHz": 36000 if word == RC5_WORD else _word_to_hz(word), "minSends": 1}
        if irp:
            protocol = {"name": "RC5x" if rc5x else "RC5", **protocol}
        if rank == 0:
            suffix = ""
        elif rc5x:
            suffix = " [RC-5x]"
        elif word == RC5_WORD and not intro:
            suffix = " [RC-5]"
        else:
            suffix = f" [{protocol['carrierHz'] / 1000:.1f} kHz{', intro' if intro else ''}]"
        mname = f"{model.name}{suffix}"
        keys: dict[str, dict[str, Any]] = {}
        names = key_names([c for c, _f, _t in members])
        dupes = Counter(c.name for c, _f, _t in members)
        for command, form, tier in members:
            full = {**form, "confidence": tier}
            gated = (word, intro, rc5x, full["type"], full.get("hex") or (full["device"], full.get("subdevice"), full["function"]))
            if gated not in gate:
                probe = {**full, "source": "probe"}
                gate[gated] = form_compiles(IMPORT_ROOT, protocol, "K", probe)
            if gate[gated]:
                refused[(document, command.row)] = f"the chart's Pronto hex does not compile: {gate[gated]}"
                continue
            label = command.name if dupes[command.name] == 1 else f"{command.name} ({command.triple})"
            # the form's own field order: id, type, parameters, confidence, source
            ordered = {k: full[k] for k in ("id", "type", "hex", "device", "subdevice", "function") if k in full}
            ordered["confidence"] = full["confidence"]
            ordered["source"] = full["source"]
            keys[names[command.row]] = {"label": label, "forms": [ordered]}
            report.count(MAKER, "keys")
        if not keys:
            continue
        report.count(MAKER, "files")
        controls = [f"{BRAND} {n}" for n in (model.name, *model.aliases)]
        # the other names of the model belong to its own file: two files may not claim one alias (R15)
        doc: dict[str, Any] = {"manufacturer": BRAND, "model": mname, "aliases": list(model.aliases) if rank == 0 else [],
                               "controls": controls, "protocol": protocol, "keys": keys}
        files.append((f"{IMPORT_ROOT}/{MAKER}/{slug(mname)}.json", doc))
    return files
