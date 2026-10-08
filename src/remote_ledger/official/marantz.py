"""Marantz's AV receiver and processor command charts (``marantz-master-ir.xls`` and its successors).

Each workbook's ``AVR Commands`` sheet is a chart: a row per command with its RC-5 *system*, *command*
and *extension* (Marantz's RC-5x adds an extension byte), a column per model with an ``X`` where the
model accepts it, and on the right, for the basic commands, the **Pronto hex** Marantz publishes. The
Pronto strings are the manufacturer's own signal, extension commands included, so they are imported
verbatim, as ``pronto`` forms. A command with no hex and no extension is plain RC-5 and is imported as
an ``irp`` RC5 form; one with an extension and no hex needs a protocol the ledger does not have, and
is counted in the report (D124).

The three workbooks overlap (the later one lists many of the earlier one's models). A model takes its
file from the **newest** workbook that lists it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
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
             "marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls")
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
    out: list[tuple[str, dict[str, Any]]] = []
    for key in sorted(chosen):
        document, model, commands = chosen[key]
        files = _model_files(snapshot, document, model, commands, report, gate, refused)
        for target, doc in files:
            if (BRAND.casefold(), doc["model"].casefold()) in authored:
                report.collisions.append((MAKER, target))
                continue
            out.append((target, doc))
    # a row no model can use is one finding, however many models list it
    by_doc = {d: {c.row: c for c in parse_sheet(snapshot.sheet_of(d, SHEET))[1]} for d in DOCUMENTS}
    for (document, row), reason in sorted(refused.items()):
        report.unrepresented[(MAKER, document, reason)] += 1
        if reason.startswith("the chart's Pronto hex"):
            report.notes.append((MAKER, f"{document} row {row}", f"'{by_doc[document][row].name}': {reason}"))
    return out


def _model_files(snapshot: Snapshot, document: str, model: Model, commands: list[Command], report: Report,
                 gate: dict[tuple, str | None], refused: dict[tuple[str, int], str]) -> list[tuple[str, dict[str, Any]]]:
    sha = snapshot.sha(document)
    # (frequency word, has an intro) -> [(command, form fields, tier)]. One file holds one carrier, and
    # the bundle needs one play rule per file, so strings that differ in either are different files (D78).
    buckets: dict[tuple[int, bool], list[tuple[Command, dict[str, Any], str]]] = defaultdict(list)
    seen: set[tuple] = set()
    for command in commands:
        mark = command.marks.get(model.column, "")
        if not mark.upper().startswith(_SUPPORTED):
            continue
        pronto = command.hex if _PRONTO.match(command.hex) else ""
        word = None
        intro = False
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
            if command.has_extension:
                refused[(document, command.row)] = "an RC-5 extension command with no Pronto hex"
                continue
            if system is None or cmd is None or system > 31 or cmd > 127:
                refused[(document, command.row)] = "no RC-5 system and command the ledger can send"
                continue
            word = RC5_WORD
            form = {"id": "primary.irp", "type": "irp", "device": system, "function": cmd}
            how = f"RC-5 system {system} command {cmd} with no extension, read as RC5 device {system} function {cmd}"
        signature = (word, intro, form.get("hex") or (form["device"], form["function"]), command.name)
        if signature in seen:
            continue
        seen.add(signature)
        source = (f"{HOST}/{document}@{sha} (retrieved {snapshot.retrieved}) sheet '{SHEET}' row {command.row} "
                  f"'{command.name}' (RC-5 {command.triple}), the column of {model.name}: {how}")
        if mark.upper() != "X":
            source += f"; the chart marks it '{mark}'"
        if command.function:
            source += f"; the chart's description: '{command.function}'"
        form["source"] = source
        buckets[(word, intro)].append((command, form, TIER))
    if not buckets:
        report.notes.append((MAKER, f"{document}: {model.name}", "the chart gives this model no command it can send"))
        return []
    # the largest bucket carries the model's own name; the others say what they are
    order = sorted(buckets, key=lambda k: (-len(buckets[k]), k))
    files = []
    for rank, (word, intro) in enumerate(order):
        members = buckets[(word, intro)]
        irp = any(f["type"] == "irp" for _c, f, _t in members)
        protocol: dict[str, Any] = {"carrierHz": 36000 if word == RC5_WORD else _word_to_hz(word), "minSends": 1}
        if irp:
            protocol = {"name": "RC5", **protocol}
        if rank == 0:
            suffix = ""
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
            gated = (word, intro, full["type"], full.get("hex") or (full["device"], full["function"]))
            if gated not in gate:
                probe = {**full, "source": "probe"}
                gate[gated] = form_compiles(IMPORT_ROOT, protocol, "K", probe)
            if gate[gated]:
                refused[(document, command.row)] = f"the chart's Pronto hex does not compile: {gate[gated]}"
                continue
            label = command.name if dupes[command.name] == 1 else f"{command.name} ({command.triple})"
            # the form's own field order: id, type, parameters, confidence, source
            ordered = {k: full[k] for k in ("id", "type", "hex", "device", "function") if k in full}
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
