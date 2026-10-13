"""Arcam's custom installation notes: the RC-5 codes of its amplifiers, receivers, players and the Solo family (DESIGN section 64).

Arcam publishes, for each product family, a PDF of its serial control commands and its **RC-5 remote codes**: a table of
functions with the code as ``system-command`` in decimal and as ``Data1 - Data2`` in hex (``Standby  16-12  0x10 - 0x0C``),
except the Solo's, which are a table of system codes and decimal commands, and the Solo Mini's, which are lines of text. A code
is read as the ledger's ``RC5`` ``irp`` form (the system code the device, the command the function, which RC-5 holds to 127),
and **a row's decimal and hex are checked against each other**: where they disagree the key is Untested and says so. So is a
row for a digit (``Number 3``) whose command is not the digit, which every other digit row of every document contradicts.

A function's name may carry the models it is for in parentheses, ``(not AVR5, AVR10)`` or ``(AVR450/750 only)``; a document's
models are the ones its title lists, and each is a file with the functions that apply to it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "arcam"
BRAND = "Arcam"
#: The ledger's RC5 block as the JP1 and IR Blaster imports give it.
PROTOCOL = {"name": "RC5", "carrierHz": 36000, "minSends": 1}


@dataclass(frozen=True)
class Document:
    file: str
    sheet: str                 # ``tables`` or ``pages`` of the manifest
    layout: str                # ``function`` (decimal and hex), ``system`` (the Solo's) or ``lines`` (the Solo Mini's)
    models: tuple[str, ...]


DOCUMENTS: tuple[Document, ...] = (
    Document("AVR380-450-750_RS232_SH256E_2.pdf", "tables", "function", ("AVR380", "AVR450", "AVR750")),
    # the title says AVR5/AVR10/AVR20/AVR30/AV40 and AVR11/AVR21/ARV31/AVR41; its changelog says AV41
    Document("AVR5-10-20-30-40-11-21-31-41_RS232_SH289E_F.pdf", "tables", "function",
             ("AVR5", "AVR10", "AVR20", "AVR30", "AV40", "AVR11", "AVR21", "AVR31", "AV41")),
    Document("SA10-SA20_RS232_SH277E_B.pdf", "tables", "function", ("SA10", "SA20")),
    Document("SA30_RS232_SH306E_4.pdf", "tables", "function", ("SA30",)),
    Document("CDS50_RS232_SH275E_C.pdf", "tables", "function", ("CDS50",)),
    Document("UDP411_RS232_SH265_E_1.pdf", "tables", "function", ("UDP411",)),
    Document("Solo_IR_Remote_Codes.pdf", "tables", "system", ("Solo",)),
    Document("Solo_Mini_RS232.pdf", "pages", "lines", ("Solo Mini",)),
)
_DEC = re.compile(r"^(\d{1,3})\s*-\s*(\d{1,3})\s*\**$")
_HEX = re.compile(r"^0[xX]([0-9A-Fa-f]{1,2})\s*-\s*(?:0[xX])?([0-9A-Fa-f]{1,2})\s*\**$")
_LINE = re.compile(r"^(\d{1,3})-(\d{1,3})\s+(\S.*)$")
_TOKEN = re.compile(r"^([A-Za-z]*)(\d+)$")
_DIGIT = re.compile(r"^(?:(?:CD|Tuner)\s+)?(?:Number\s+)?(\d)$", re.I)


@dataclass(frozen=True)
class Row:
    page: int
    table: int | None
    function: str           # the name as the table writes it, restriction and footnote marks removed
    written: str            # the name as written
    system: int
    command: int
    stated: str             # what the table says of the code, for the citation
    agrees: bool            # the decimal and hex say one code
    doubt: str              # why the code is doubtful in another way, or empty
    only: tuple[str, ...]   # the models it is for, empty for every model of the document
    excluded: tuple[str, ...]


def expand(text: str) -> tuple[str, ...] | None:
    """``AVR450/750`` as ``AVR450, AVR750`` (a number takes the letters before it), or None where the text is not a list."""
    out: list[str] = []
    prefix = ""
    for token in (t.strip() for t in re.split(r"[/,]", text) if t.strip()):
        m = _TOKEN.match(token)
        if not m:
            return None
        prefix = m[1] or prefix
        out.append(f"{prefix}{m[2]}")
    return tuple(out) or None


def restriction(name: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """``(name without it, only, excluded)`` of a function name: ``Zone 2 Mute (not AVR5, AVR10)`` is for every model but those
    two, ``DAB (AVR450/750 only)`` for those two. A parenthesis that is anything else stays in the name."""
    name = re.sub(r"\s*\**\s*$", "", name.strip())
    only: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    for m in list(re.finditer(r"\s*\(([^()]*)\)", name)):
        inner = m[1].strip()
        no = re.match(r"^not\s+(.+)$", inner, re.I)
        yes = re.match(r"^(.+?)\s+only$", inner, re.I)
        if no and (models := expand(no[1])):
            excluded += models
        elif yes and (models := expand(yes[1])):
            only += models
        else:
            continue
        name = name.replace(m[0], "", 1)
    return re.sub(r"\s+", " ", name).strip(), only, excluded


def digit_doubt(function: str, command: int) -> str:
    """Why a digit row is doubtful, or an empty text: a function that is a digit has that digit for its command in 79 of the 80 digit
    rows of the eight documents; the other is the Solo's ``Number 3``, which the table gives command 2, the command of ``Number 2``."""
    m = _DIGIT.match(function)
    if m and int(m[1]) != command:
        return f"its name is the digit {m[1]} and its command is {command}, though the command is the digit in every other digit row"
    return ""


def read_code(decimal: str, hex_text: str) -> tuple[int, int, bool, str] | None:
    """``(system, command, agree, stated)`` of a row's two statements of its code, or None where the decimal is not one."""
    d = _DEC.match(" ".join(decimal.split()))
    if not d:
        return None
    system, command = int(d[1]), int(d[2])
    h = _HEX.match(" ".join(hex_text.split()))
    agrees = bool(h) and (int(h[1], 16), int(h[2], 16)) == (system, command)
    return system, command, agrees, f"{decimal.strip()} / {hex_text.strip()}"


def function_rows(rows: list[list[str]]) -> list[Row]:
    """The rows of the ``Function | RC5 code [system-command] | RC5 code (Data1 - Data2)`` tables of a document's ``tables``
    sheet (each row is the page, the table's number on it and the cells)."""
    out: list[Row] = []
    header: tuple[str, str] | None = None
    for row in rows:
        page, table, cells = row[0], row[1], row[2:]
        key = (page, table)
        if len(cells) >= 3 and cells[0].strip() == "Function" and cells[1].lower().startswith("rc5 code"):
            header = key
            continue
        if header != key or len(cells) < 3 or not cells[0].strip() or cells[1].strip() == "Decimal":
            continue
        code = read_code(cells[1], cells[2])
        if code is None:
            continue
        name, only, excluded = restriction(cells[0])
        out.append(Row(int(page), int(table), name, cells[0].strip(), code[0], code[1], code[3], code[2],
                       digit_doubt(name, code[1]), only, excluded))
    return out


def system_rows(rows: list[list[str]]) -> list[Row]:
    """The Solo's ``System Code | Command | Command Code | Comments`` table: the system code is written on the first row of a
    group and holds for the rows below it."""
    out: list[Row] = []
    header: tuple[str, str] | None = None
    system: int | None = None
    for row in rows:
        page, table, cells = row[0], row[1], row[2:]
        key = (page, table)
        if len(cells) >= 3 and cells[0].strip() == "System Code":
            header, system = key, None
            continue
        if header != key or len(cells) < 3 or not cells[1].strip():
            continue
        if cells[0].strip().isdigit():
            system = int(cells[0])
        if system is None or not cells[2].strip().isdigit():
            continue
        name, only, excluded = restriction(cells[1])
        out.append(Row(int(page), int(table), name, cells[1].strip(), system, int(cells[2]), f"{system}-{cells[2].strip()}",
                       True, digit_doubt(name, int(cells[2])), only, excluded))
    return out


def line_rows(rows: list[list[str]]) -> list[Row]:
    """The Solo Mini's lines, ``16-12 Standby``, after its ``IR remote commands`` heading."""
    out: list[Row] = []
    started = False
    for row in rows:
        page, line = int(row[0]), row[1].strip()
        if "IR remote commands" in line:
            started = True
            continue
        m = _LINE.match(line) if started else None
        if m:
            name, only, excluded = restriction(m[3])
            out.append(Row(page, None, name, m[3].strip(), int(m[1]), int(m[2]), f"{m[1]}-{m[2]}", True,
                           digit_doubt(name, int(m[2])), only, excluded))
    return out


READERS = {"function": function_rows, "system": system_rows, "lines": line_rows}


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for document in DOCUMENTS:
        rows = READERS[document.layout](snapshot.sheet_of(document.file, document.sheet))
        sha = snapshot.sha(document.file)
        where = f"arcam.co.uk custom installation notes/{document.file}"
        report.count(MAKER, f"{document.file}: rows read", len(rows))
        for model in document.models:
            members: list[tuple[str, dict[str, Any]]] = []
            for row in rows:
                if row.only and model not in row.only or model in row.excluded:
                    continue
                key = (row.system, row.command)
                if key not in gate:
                    gate[key] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                        "id": "primary.irp", "type": "irp", "device": row.system, "function": row.command,
                        "confidence": TIER, "source": "probe"})
                if gate[key]:
                    report.unrepresented[(MAKER, document.file, f"does not compile: {gate[key]}")] += 1
                    continue
                place = f"page {row.page}" + (f" table {row.table}" if row.table is not None else "")
                cite = (f"{where}@{sha} (retrieved {snapshot.retrieved}) {place} row '{row.written}' ({row.stated}): "
                        f"RC5 system {row.system} command {row.command}")
                if row.agrees:
                    cite += "; the table's decimal and hex say the same" if document.layout == "function" else ""
                else:
                    cite += "; the table's decimal and hex do not say the same code, so the key rests on neither alone"
                if row.doubt:
                    cite += "; " + row.doubt
                    report.notes.append((MAKER, f"{document.file} {place}", f"'{row.written}' ({row.stated}): {row.doubt}"))
                if row.only:
                    cite += f"; the table says it is for {', '.join(row.only)} only"
                if row.excluded:
                    cite += f"; the table says it is not for {', '.join(row.excluded)}"
                members.append((row.function, {
                    "id": "primary.irp", "type": "irp", "device": row.system, "function": row.command,
                    "confidence": TIER if row.agrees and not row.doubt else DOUBTFUL_TIER, "source": cite}))
            if not members:
                continue
            if (BRAND.casefold(), model.casefold()) in authored:
                report.collisions.append((MAKER, model))
                continue
            names = key_names([m[0] for m in members])
            keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(names, members)}
            report.count(MAKER, "keys", len(keys))
            report.count(MAKER, "files")
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(model)}.json",
                        {"manufacturer": BRAND, "model": model, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(PROTOCOL), "keys": keys}))
    return out
