"""Hegel's RC-5 code tables (DESIGN section 66).

Hegel publishes on its support site, for each product, a page of its **RC-5 codes**: ``Hegel H190 RC-5 code table``, a date, then a
table headed ``System 16 (Preamp 1)`` (and ``System 19 (Preamp 2)``, or ``System 20 (CD-Player)`` for the Mohican) of rows
``Function | Command | Descripton``. The three tables of 2020 (the H390/H590, the H95/H120/H190 and the P20/P30) add each code's **Pronto
hex** beside its row, and a second row ``(alt)`` for the code's twin with the other toggle bit: a command meant to be repeated
alternates the two, "to prevent the command from freezing". The pages are one-page PDFs (Excel printed), 12 documents for 14 products.

A key is the ledger's ``RC5``: the system code the device, the command the function (D3b: the toggle bit is not in a file, so an
``(alt)`` row is the same key). **Where a row has Pronto hex it is decoded and must agree with the row**: the hex's system and
command are the row's, and its ``(alt)`` twin is the same with the other toggle bit; where they do not the key is Untested and says so.
A function written ``XLR 2 (H590)`` or ``ECO (P30 only)`` is for that model of the document's list only. A product named by two
documents (the H190 and H590) has one file, the newer document's rows plus the older document's rows for the codes the newer does not
have, each key naming the other document's name for the code where it differs, **unless the older document contradicts the newer**
(a code the two give different names): then the older document's rows are kept as written in a file of their own, named
``<model> (<year> table)_questionable`` and Untested.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "hegel"
BRAND = "Hegel"
#: The ledger's RC5 block as the JP1 and IR Blaster imports give it.
PROTOCOL = {"name": "RC5", "carrierHz": 36000, "minSends": 1}


@dataclass(frozen=True)
class Document:
    file: str
    models: tuple[str, ...]


#: Newest first: of two documents that give one product's code, the first named wins and the other is cited beside it. Each
#: document's title (``Hegel H95, H120, H190 RC-5 code table``) names its models, and the importer checks that it does.
DOCUMENTS: tuple[Document, ...] = (
    Document("Hegel_H390_H590_IR_codes_v1.pdf", ("H390", "H590")),
    Document("Hegel_H95_H120_H190_IR_codes_v2.pdf", ("H95", "H120", "H190")),
    Document("Hegel_P20_P30_IR_codes_v2.pdf", ("P20", "P30")),
    Document("Hegel_Rost_IR_Codes.pdf", ("Röst",)),
    Document("Hegel_H190_IR_Codes.pdf", ("H190",)),
    Document("Hegel_H590_IR_Codes.pdf", ("H590",)),
    Document("Hegel_H90_IR_Codes.pdf", ("H90",)),
    Document("Hegel_Mohican_IR_Codes.pdf", ("Mohican",)),
    Document("Hegel_HD30_IR_Codes.pdf", ("HD30",)),
    Document("Hegel_H360_IR_Codes.pdf", ("H360",)),
    Document("Hegel_HD12_IR_Codes.pdf", ("HD12",)),
    Document("Hegel_HD25_IR_Codes.pdf", ("HD25",)),
)
_TITLE = re.compile(r"^Hegel (.+?) RC-5 code table$")
_DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_SYSTEM = re.compile(r"^System (\d{1,3}) \((.+)\)$")
_ALT = re.compile(r"^(\d{1,3}) \(alt\.?\)$")
_PRONTO = re.compile(r"^0000 [0-9A-Fa-f]{4} 0000 [0-9A-Fa-f]{4}( [0-9A-Fa-f]{4})+$")
_INTEGER = re.compile(r"^\d{1,3}$")
_UNIT = 0x20
MERGED = "<merged>"
#: Names the tables write differently for one function (``BAL`` and ``Balanced``, ``ECO`` and ``Power``), squashed to lower case and
#: letters and digits; every other difference between two documents' names for a code is read as a contradiction.
_SYNONYMS = {"bal": "balanced", "xlr1": "balanced", "an1": "analog1", "htan2": "analog2", "coax1": "coaxial", "net": "network",
             "eco": "power"}


def same_name(a: str, b: str) -> bool:
    """Whether two documents' names for one code are one function: equal once squashed, or synonyms (``ECO`` is ``Power``)."""
    squash = lambda text: _SYNONYMS.get(re.sub(r"[^a-z0-9]", "", text.casefold()), re.sub(r"[^a-z0-9]", "", text.casefold()))  # noqa: E731
    return squash(a) == squash(b)


@dataclass(frozen=True)
class Row:
    page: int
    table: int
    function: str                   # the name as written, restriction removed
    written: str                    # the name as written
    system: int
    heading: str                    # ``System 16 (Preamp 1)``
    command: int | None             # None where the table says ``N/A``
    description: str
    only: tuple[str, ...]           # the models of the document it is for, empty for all
    hex: str                        # the Pronto hex beside the row, or empty
    alt: str                        # the Pronto hex of its ``(alt)`` row, or empty
    alt_row: bool                   # the table has an ``(alt)`` row for it


@dataclass
class Table:
    title: str
    models: tuple[str, ...]
    date: str
    rows: list[Row] = field(default_factory=list)
    blank: int = 0
    unread: list[str] = field(default_factory=list)


def decode_rc5(pronto: str) -> tuple[int, int, int] | None:
    """``(system, command, toggle)`` of an RC-5 frame in Pronto form (bits of two half-bits of 32 carrier cycles, a first mark
    standing for the start bit's second half), or None where it is not one."""
    try:
        words = [int(w, 16) for w in pronto.split()]
    except ValueError:
        return None
    if len(words) < 4 or words[0] != 0 or words[2] != 0 or len(words) != 4 + 2 * (words[2] + words[3]):
        return None
    pairs = words[4:]
    if any(d % _UNIT for d in pairs[:-1]):
        return None
    levels: list[int] = []
    for i in range(0, len(pairs), 2):                      # a mark and a space of 32 carrier cycles a half-bit; the last space is the gap
        levels += [1] * (pairs[i] // _UNIT)
        if i + 1 < len(pairs) - 1:
            levels += [0] * (pairs[i + 1] // _UNIT)
    levels = [0] + levels
    levels += [0] * (28 - len(levels))
    if len(levels) != 28:
        return None
    bits = []
    for i in range(0, 28, 2):
        pair = (levels[i], levels[i + 1])
        if pair == (0, 1):
            bits.append(1)
        elif pair == (1, 0):
            bits.append(0)
        else:
            return None
    system = int("".join(map(str, bits[3:8])), 2)
    command = int("".join(map(str, bits[8:14])), 2) | ((1 - bits[1]) << 6)
    return system, command, bits[2]


def parse(rows: list[list[str]], document: Document) -> Table:
    """The document's flow (``page, text, y, line`` and ``page, row, table, y, cells``): its title, date, and the rows of its tables,
    each with the Pronto hex that lies on its line and that of its ``(alt)`` row."""
    title = models = date = ""
    hexes: list[tuple[int, str]] = []
    for row in rows:
        if row[1] == "text":
            if m := _TITLE.match(row[3]):
                title = m[1]
            elif _DATE.match(row[3]) and not date:
                date = row[3]
            elif _PRONTO.match(row[3]):
                hexes.append((int(row[2]), row[3]))
    names = tuple(re.sub(r"\s+", " ", n).strip() for n in re.split(r",|\s&\s", title) if n.strip())
    table = Table(title, names, date)
    taken: set[int] = set()
    system = heading = None
    last: Row | None = None

    def hex_of(y: int) -> str:
        best = min((i for i, (hy, _) in enumerate(hexes) if i not in taken and abs(hy - y) <= 3),
                   key=lambda i: abs(hexes[i][0] - y), default=None)
        if best is None:
            return ""
        taken.add(best)
        return hexes[best][1]

    for row in rows:
        if row[1] != "row":
            continue
        page, number, y, cells = int(row[0]), int(row[2]), int(row[3]), ["" if c == MERGED else c for c in row[4:]]
        first = cells[0].strip()
        if m := _SYSTEM.match(first):
            system, heading, last = int(m[1]), first, None
            continue
        if first == "Function":
            continue
        if system is None or heading is None:
            continue
        function, command, description = (cells + ["", "", ""])[:3]
        function, command = function.strip(), command.strip()
        if not function and not command and not description.strip():
            table.blank += 1
            continue
        alt = _ALT.match(command) or _ALT.match(function)
        if alt:                                                  # the twin of the row above, with the other toggle bit
            if last is not None and int(alt[1]) == last.command:
                last_alt = hex_of(y)
                rows_ = table.rows
                rows_[-1] = Row(**{**last.__dict__, "alt": last_alt, "alt_row": True})
                last = rows_[-1]
            else:
                table.unread.append(f"page {page} table {number} row '{function or command}' is an (alt) row with no row above it for the command")
            continue
        only: tuple[str, ...] = ()
        name = function
        if m := re.search(r"\s*\(([^()]*?)(?:\s+only)?\)\s*$", function, re.I):
            wanted = tuple(n for n in document.models if n.casefold() == m[1].strip().casefold())
            if wanted:
                only, name = wanted, function[:m.start()].strip()
        value = int(command) if _INTEGER.match(command) else None
        current = Row(page, number, name, function, system, heading, value, description.strip(), only, hex_of(y), "", False)
        table.rows.append(current)
        last = current
    return table


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


@dataclass
class Member:
    row: Row
    document: Document
    table: Table
    also: list[str] = field(default_factory=list)
    questionable: str = ""


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    tables = {d.file: parse(snapshot.sheet_of(d.file, "flow"), d) for d in DOCUMENTS}
    members: dict[str, list[Member]] = {}
    for document in DOCUMENTS:
        table = tables[document.file]
        if table.models != document.models:
            report.notes.append((MAKER, document.file, f"the title names {', '.join(table.models)} and the importer's list is {', '.join(document.models)}"))
        for text in table.unread:
            report.notes.append((MAKER, document.file, text))
        report.count(MAKER, f"{document.file}: rows read", len(table.rows))
        report.count(MAKER, "blank rows", table.blank)
        for row in table.rows:
            if row.alt_row:
                report.count(MAKER, "(alt) rows, the twin of a row with the other toggle bit")
            if row.command is None:
                report.unrepresented[(MAKER, document.file, f"'{row.written}' has no command ('N/A')")] += 1
        for model in document.models:
            wanted = [r for r in table.rows if r.command is not None and not (r.only and model not in r.only)]
            base = members.get(model, [])
            shared = [(r, m) for r in wanted for m in base if (m.row.system, m.row.command) == (r.system, r.command)]
            differing = [(r, m) for r, m in shared if not same_name(r.function, m.row.function)]
            if base:
                report.count(MAKER, f"{model}: codes {document.file} shares with the newer document", len(shared))
                report.count(MAKER, f"{model}: of them named differently", len(differing))
            if differing:
                # the older document contradicts the newer: its rows are kept as written, apart (a file named for the document's year)
                year = table.date[-4:]
                name = f"{model} ({year} table)_questionable"
                why = (f"{document.file} (dated {table.date}) gives a different name to {len(differing)} of the {len(shared)} codes it shares with "
                       f"the newer document for the {model}, so it is kept apart as written (docs: DESIGN section 66)")
                report.notes.append((MAKER, f"{document.file} -> {name}", why))
                members.setdefault(name, []).extend(Member(r, document, table, [], why) for r in wanted)
                continue
            mine = members.setdefault(model, [])
            for row in wanted:
                same = next((m for m in mine if (m.row.system, m.row.command) == (row.system, row.command)
                             and m.document is not document), None)
                if same is not None:
                    if same.row.function.casefold() != row.function.casefold():
                        same.also.append(f"{document.file} page {row.page} calls it '{row.written}' ({row.description})")
                    else:
                        same.also.append(f"{document.file} page {row.page} lists it too, as '{row.written}'")
                    continue
                mine.append(Member(row, document, table))
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    checks: dict[int, tuple[str, str]] = {}               # a row -> (what its hex says of it, why it is doubtful or empty)
    agreed = disagreed = twins = twin_bad = 0
    for document in DOCUMENTS:
        for row in tables[document.file].rows:
            if row.command is None or not row.hex:
                continue
            decoded, said, doubt = decode_rc5(row.hex), "", ""
            if decoded and decoded[:2] == (row.system, row.command):
                said = f"its Pronto hex decodes to the same system and command (toggle bit {decoded[2]})"
                agreed += 1
                if row.alt:
                    twin = decode_rc5(row.alt)
                    if twin and twin[:2] == decoded[:2] and twin[2] == 1 - decoded[2]:
                        said += ", and its (alt) row is the same with the other toggle bit"
                        twins += 1
                    else:
                        doubt = "its (alt) row's Pronto hex is not the same code with the other toggle bit"
                        twin_bad += 1
            else:
                doubt = ("its Pronto hex decodes to " + (f"system {decoded[0]} command {decoded[1]}" if decoded else "no RC-5 frame")
                         + " and not to the row's code")
                disagreed += 1
            if doubt:
                report.notes.append((MAKER, f"{document.file} page {row.page}", f"'{row.written}' ({row.system}, {row.command}): {doubt}"))
            checks[id(row)] = (said, doubt)
    for model, mine in sorted(members.items()):
        built: list[tuple[str, dict[str, Any]]] = []
        for member in mine:
            row = member.row
            key = (row.system, row.command)
            if key not in gate:
                gate[key] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                    "id": "primary.irp", "type": "irp", "device": row.system, "function": row.command,
                    "confidence": TIER, "source": "probe"})
            if gate[key]:
                report.unrepresented[(MAKER, member.document.file, f"does not compile: {gate[key]}")] += 1
                continue
            sha = snapshot.sha(member.document.file)
            cite = (f"support.hegel.com RC-5 code tables/{member.document.file}@{sha} (retrieved {snapshot.retrieved}) page {row.page} "
                    f"table {row.table} row '{row.written}' ({row.heading}, command {row.command}): RC5 system {row.system} command "
                    f"{row.command}; the table is dated {member.table.date}")
            if row.description:
                cite += f"; description '{row.description}'"
            said, doubt = checks.get(id(row), ("", ""))
            if said:
                cite += "; " + said
            if doubt:
                cite += "; " + doubt
            if member.questionable:
                cite += "; QUESTIONABLE, kept as the document writes it: " + member.questionable
                doubt = doubt or member.questionable
            for text in member.also:
                cite += f"; the older document {text}" if "page" in text else f"; {text}"
            if row.only:
                cite += f"; the table says it is for the {row.only[0]} only"
            built.append((row.function, {"id": "primary.irp", "type": "irp", "device": row.system, "function": row.command,
                                         "confidence": DOUBTFUL_TIER if doubt else TIER, "source": cite}))
        if not built:
            continue
        if (BRAND.casefold(), model.casefold()) in authored:
            report.collisions.append((MAKER, model))
            continue
        names = key_names([b[0] for b in built])
        keys = {n: {"label": b[0], "forms": [b[1]]} for n, b in zip(names, built)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        ascii_name = unicodedata.normalize("NFKD", model).encode("ascii", "ignore").decode()
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(ascii_name)}.json",
                    {"manufacturer": BRAND, "model": model, "aliases": [], "controls": [f"{BRAND} {model}"],
                     "protocol": dict(PROTOCOL), "keys": keys}))
    report.count(MAKER, "Pronto hex that decodes to the row's system and command", agreed)
    report.count(MAKER, "Pronto hex that decodes to another code", disagreed)
    report.count(MAKER, "(alt) twins that are the same code with the other toggle bit", twins)
    report.count(MAKER, "(alt) twins that are not", twin_bad)
    return out
