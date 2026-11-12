"""Cary Audio's DMS IR remote codes (DESIGN section 71).

Cary Audio's ``DMS IR Remote Codes`` is one page (Word to PDF, 2 November 2018): "All codes - to Philips RC-5 standard", a table of
38 keys, each a ``Key Printing`` (what is printed on the remote's key), a ``Function``, **two system codes, ``A`` (0x10) and ``B``
(0x09)**, a ``Key Command`` and a ``Notes`` column that is empty. The page does not say which products it is for beyond ``DMS``, and
does not say why a key has two system codes.

A key is the ledger's ``RC5`` (the system code the device, the command the function). **One file for each system code**, ``DMS`` for the
column ``A`` and ``DMS (system code B)`` for ``B``, since a unit answers to one of them as Cambridge's ``24 or 27`` units do (that the
DMS does is not said; the table lists both for every key). The label is the key printing as written, except a printing that is a sign
beside a word (``-  VOLUME``, ``VOLUME  +``), which is the function's words (``VOLUME DOWN``, ``VOLUME UP``); the function, which the
table writes as it likes (``NAVIGATION LIFT``, ``NETWORD INPUT``), is in the citation as written.

**Ten more sheets of the same site and layout (DESIGN section 99)** have one system code: ``Key Printing``, ``Function``, ``System``, ``Command`` and
``Notes``, "All codes - to Philips RC-5 standard", dated 2004 to 2007 (the DAC-200ts's has no date): the CD303, CD303/200, CD303/300, CD306 and CD306/200 CD
players, the DVD 6 and DVD 7, the CPA 1 / CPA 2 / SLP-03 preamplifiers, the Cinema 11a's two handsets (a page each) and the DAC-200ts. Their tables have no
ruling, so they are read by the position of their words: a row's text is its printing and its function (the boundary is the middle between the two headings;
the columns are centred), and its last two codes are the system and the command (``00`` and ``0DH``, or ``0x13`` and ``0x0C``).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "cary"
BRAND = "Cary Audio"
FILE = "DMS-IR-Remote-Codes.pdf"
MODEL = "DMS"
PROTOCOL = {"name": "RC5", "carrierHz": 36000, "minSends": 1}
HEADER = ["Key Printing", "Function", "System Code A", "System Code B", "Key Command", "Notes"]
_SIGNED = re.compile(r"^(?:[-+]\s*[A-Za-z]+|[A-Za-z]+\s*[-+])$")


def parse(rows: list[list[str]]) -> list[tuple[int, str, str, int, int, int, str]]:
    """``(its row, key printing, function, system code A, system code B, command, notes)`` of each row under the header: a row is
    ``page, table number, cells``. A header that is not the six columns, or a cell that is not a hex byte, is an error: the page is
    one table and a change of it is a change to read."""
    header = next((i for i, row in enumerate(rows) if row[2:] == HEADER), None)
    if header is None:
        raise ValueError(f"no row of the six columns {HEADER}")
    out = []
    for position, row in enumerate(rows[header + 1:], header + 2):
        cells = [" ".join(c.split()) for c in row[2:]]
        if len(cells) != 6:
            raise ValueError(f"row {position}: {len(cells)} cells, not 6")
        printing, function, a, b, command, notes = cells
        try:
            values = [int(x, 16) for x in (a, b, command)]
        except ValueError as exc:
            raise ValueError(f"row {position}: a system code or a command that is not hex: {cells}") from exc
        out.append((position, printing, function, *values, notes))
    return out


def label_of(printing: str, function: str) -> str:
    """The key printing, or the function where the printing is a sign beside a word (``-  VOLUME``)."""
    return function if _SIGNED.match(printing) else printing


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build_dms(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    rows = parse(snapshot.sheet_of(FILE, "tables"))
    sha = snapshot.sha(FILE)
    report.count(MAKER, "rows read", len(rows))
    gate: dict[tuple[int, int], str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for column, (letter, model) in enumerate((("A", MODEL), ("B", f"{MODEL} (system code B)"))):
        built: list[tuple[str, dict[str, Any]]] = []
        for position, printing, function, a, b, command, notes in rows:
            system = (a, b)[column]
            if (system, command) not in gate:
                gate[(system, command)] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                    "id": "primary.irp", "type": "irp", "device": system, "function": command, "confidence": TIER, "source": "probe"})
            if gate[(system, command)]:
                report.unrepresented[(MAKER, FILE, f"does not compile: {gate[(system, command)]}")] += 1
                continue
            cite = (f"caryaudio.com DMS IR Remote Codes/{FILE}@{sha} (retrieved {snapshot.retrieved}) page 1 table 1 row '{printing}' "
                    f"(function '{function}', system code A 0x{a:02X}, system code B 0x{b:02X}, key command 0x{command:02X}): "
                    f"RC5 system {system} command {command}, the table's system code {letter}")
            if notes:
                cite += f"; the table's note: {notes}"
            built.append((label_of(printing, function), {"id": "primary.irp", "type": "irp", "device": system, "function": command,
                                                          "confidence": TIER, "source": cite}))
        final = model
        while (BRAND.casefold(), final.casefold()) in authored:         # R15: another source has the name; the file is kept under another
            renamed = f"{final} (Cary Audio table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        keys = {n: {"label": b_[0], "forms": [b_[1]]} for n, b_ in zip(key_names([b_[0] for b_ in built]), built)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {MODEL}"],
                     "protocol": dict(PROTOCOL), "keys": keys}))
    return out


# --- the sheets of one system code (section 99) ---------------------------------------------------------------------------------------------

SHEET_HOST = "caryaudio.com IR Remote Codes"
_NUMBER = re.compile(r"^(?:0[xX]([0-9A-Fa-f]{1,2})|([0-9A-Fa-f]{1,2})[Hh]|(\d{1,3}))$")   # ``0x2D``, ``2DH`` (hexadecimal) or ``23`` (decimal)
_HEADING = re.compile(r"^[A-Z][A-Z0-9 ]* USE ONLY$")
_DATE = re.compile(r"(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}")


@dataclass(frozen=True)
class Sheet:
    file: str
    page: int
    models: tuple[str, ...]            # a file for each: what the title names (and, for the CPA sheet, what the file's name adds)
    title: tuple[str, ...]             # squashed words the page's title must have: a sheet is not the one its name says if it does not
    note: str = ""                     # what the citations say besides the row


SHEETS: tuple[Sheet, ...] = (
    Sheet("CD303_IR.pdf", 1, ("CD303",), ("CARYCD303IRREMOTECODES",)),
    Sheet("CD303200_IR.pdf", 1, ("CD303/200",), ("CARYCD303200IRREMOTECODES",)),
    Sheet("CD303.300_IRCodes.pdf", 1, ("CD303/300",), ("CARYCD303300IRREMOTECODES",)),
    Sheet("CD306_IR.pdf", 1, ("CD306",), ("CARYCD306IRREMOTECODES",)),
    Sheet("CD306200_IR.pdf", 1, ("CD306/200",), ("CARYCD306200IRREMOTECODES",)),
    Sheet("DVD6_IRCodes.pdf", 1, ("DVD 6",), ("CARYDVD6IRREMOTECODES",)),
    Sheet("DVD7_IRCodes.pdf", 1, ("DVD 7",), ("CARYDVD7IRREMOTECODES",)),
    Sheet("CPA-1-CPA-2-SLP-03-IR-Remote-Codes2.pdf", 1, ("CPA 1", "CPA 2", "SLP-03"), ("CPA1ANDSLP03IRREMOTECODES",),
          "the title names the CPA 1 and the SLP-03, the file's name the CPA-2 as well"),
    Sheet("Cinema-11a-Zone-1-2-IR-Remote-Codes-2.pdf", 1, ("Cinema 11a",), ("CINEMA11AMAINZONEREMOTEHANDSETIRCODES",)),
    Sheet("Cinema-11a-Zone-1-2-IR-Remote-Codes-2.pdf", 2, ("Cinema 11a (Zone 2)",), ("CINEMA11A", "2NDZONEREMOTEHANDSETIRCODES")),
    Sheet("DAC-200ts-IR-Remote-Codes.pdf", 1, ("DAC-200ts",), ("DAC200TSUSEIRREMOTECODES",)),
)


def _lines(rows: list[list[str]], page: int) -> list[tuple[float, list[tuple[int, str]]]]:
    """``[(y, [(x, word)])]`` of a page of a ``words`` CSV (``page, y, x, word, x, word...``; the ruled rows ``page, R, y0, y1`` are not lines)."""
    return [(int(r[1]), [(int(r[i]), r[i + 1]) for i in range(2, len(r), 2)]) for r in rows if r[0] == str(page) and r[1] != "R"]


def _number(word: str, h_after: bool = False) -> int | None:
    """A code of a sheet: **hexadecimal with a ``0x`` prefix or an ``H`` after it (the text layer sets that ``H`` apart on some lines: ``h_after``), a bare number is decimal**
    (the DVD sheets write system ``23`` and commands ``1`` to ``36``; read as hexadecimal, 23 would be device 35, which RC-5 has not)."""
    m = _NUMBER.match(word + ("H" if h_after and word[-1:] not in "Hh" else ""))
    return None if m is None else int(m[1] or m[2], 16) if (m[1] or m[2]) else int(m[3])


@dataclass(frozen=True)
class SheetRow:
    y: int
    printing: str
    function: str
    system: int
    command: int
    written: str          # the two codes as the sheet writes them (``00 0DH``)
    section: str = ""     # the heading the row is under (``CD PLAYER USE ONLY``: the rows that follow it are for another product of the same handset), else ""


def parse_sheet(rows: list[list[str]], sheet: Sheet) -> tuple[str, list[SheetRow], list[str]]:
    """``(the title's text, the rows, what is not a row)`` of a sheet: the words of the first lines are the title, the line with ``Printing``, ``Function``, ``System``
    and ``Command`` is the header, and a line under it that ends in two hexadecimal codes is a row. A line with words and no codes is a note (a heading such as
    ``CD PLAYER USE ONLY``), never dropped silently; a title that lacks the sheet's words or a header that is not the five columns is an error."""
    lines = _lines(rows, sheet.page)
    header_at = next((i for i, (_, ws) in enumerate(lines) if {w.upper() for _, w in ws} >= {"PRINTING", "FUNCTION", "SYSTEM", "COMMAND"}), None)
    if header_at is None:
        raise ValueError(f"{sheet.file} page {sheet.page}: no header line of Key Printing, Function, System and Command")
    title = " ".join(w for _, ws in lines[:header_at] for _, w in ws)
    squashed = re.sub(r"[^A-Z0-9]", "", title.upper())
    for words in sheet.title:
        if words not in squashed:
            raise ValueError(f"{sheet.file} page {sheet.page}: the title {title!r} does not say {words!r}")
    x = {w.upper(): px for px, w in lines[header_at][1]}
    boundary, system_at = (x["PRINTING"] + x["FUNCTION"]) / 2, x["SYSTEM"] - 10
    out: list[SheetRow] = []
    notes: list[str] = []
    last_y = None                                             # the line of the last row or of its wrapped function: a line of function words under it goes on that row's function
    section = ""
    for y, words in lines[header_at + 1:]:
        h_after = {px for (px, _), (_, w) in zip(words, words[1:]) if w in ("H", "h")}         # the ``H`` of ``17H`` that the text layer sets apart goes on the code before it
        words = [(px, w) for px, w in words if w not in ("H", "h")]
        codes = [(px, w, _number(w, px in h_after)) for px, w in words if px >= system_at]
        if len(codes) == 2 and all(c is not None for _, _, c in codes):
            left = [(px, w) for px, w in words if px < system_at]
            printing = " ".join(w for px, w in left if px < boundary)
            function = " ".join(w for px, w in left if px >= boundary)
            if not printing:
                raise ValueError(f"{sheet.file} page {sheet.page}: the row at y {y} has no key printing")
            shown = [w + ("H" if px in h_after and w[-1:] not in "Hh" else "") for px, w, _ in codes]
            out.append(SheetRow(y, printing, function, codes[0][2], codes[1][2], f"{shown[0]} {shown[1]}", section))
            last_y = y
        elif words and last_y is not None and y - last_y <= 16 and all(px >= boundary for px, _ in words) and not any(px >= system_at for px, _ in words):
            r = out[-1]
            out[-1] = SheetRow(r.y, r.printing, " ".join([r.function, *(w for _, w in words)]).strip(), r.system, r.command, r.written, r.section)
            last_y = y
        elif words and _HEADING.match(" ".join(w for _, w in words)):
            section = " ".join(w for _, w in words)
            notes.append(f"page {sheet.page}, the line at y {y} is the heading {section!r}: the rows under it carry it in their citations")
            last_y = None
        elif words:
            notes.append(f"page {sheet.page}, the line at y {y} ({' '.join(w for _, w in words)!r}) is not a row of a printing, a function, a system code and a command")
            last_y = None
    return title, out, notes


def sheet_labels(rows: list[SheetRow]) -> list[str]:
    """A row's label is its printing, or its function where the printing is a sign beside a word (§71), or where **two rows of one sheet have the same printing** (the CPA
    sheet's ``POWER`` of the amplifier and of the CD player): then the function tells them apart; where the function is the printing too (the CD303/300's ``MUTE`` of
    the player and of the amplifier), the heading the row is under does (``MUTE (AMPLIFIER)``)."""
    count = Counter(r.printing for r in rows)
    labels = [r.function if (count[r.printing] > 1 and r.function and r.function != r.printing) or _SIGNED.match(r.printing) else r.printing for r in rows]
    again = Counter(labels)
    return [f"{label} ({r.section.removesuffix(' USE ONLY')})" if again[label] > 1 and r.section else label for r, label in zip(rows, labels)]


def build_sheets(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple[int, int], str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for sheet in SHEETS:
        title, rows, notes = parse_sheet(snapshot.sheet_of(sheet.file, "words"), sheet)
        for note in notes:
            report.notes.append((MAKER, sheet.file, note if "is the heading" in note else f"not read: {note}"))
        dated = _DATE.search(title)
        entry, sha = snapshot.documents[sheet.file], snapshot.sha(sheet.file)
        report.count(MAKER, f"{sheet.file} page {sheet.page}: rows read", len(rows))
        labels = sheet_labels(rows)
        names: dict[tuple[int, int], list[str]] = {}                  # the printings of each code: **a code that the sheet gives to two keys is one the sheet is wrong about in one of them**
        for row in rows:
            names.setdefault((row.system, row.command), []).append(row.printing)
        for model in sheet.models:
            built: list[tuple[str, dict[str, Any]]] = []
            for row, label in zip(rows, labels):
                if (row.system, row.command) not in gate:
                    gate[(row.system, row.command)] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                        "id": "primary.irp", "type": "irp", "device": row.system, "function": row.command, "confidence": TIER, "source": "probe"})
                if gate[(row.system, row.command)]:
                    report.unrepresented[(MAKER, sheet.file, f"does not compile: {gate[(row.system, row.command)]}")] += 1
                    continue
                cite = (f"{SHEET_HOST}/{sheet.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}) page {sheet.page} table row '{row.printing}' "
                        f"(function '{row.function}', system {row.written.split()[0]}, command {row.written.split()[1]}"
                        + (f", the sheet dated {dated[0]}" if dated else "") + (f", under the heading '{row.section}'" if row.section else "")
                        + f"): RC5 system {row.system} command {row.command}"
                        + (f"; the sheet gives this code to {' and '.join(repr(n) for n in names[(row.system, row.command)])}" if len(names[(row.system, row.command)]) > 1 else ""))
                if sheet.note and model != sheet.models[0]:
                    cite += f"; {sheet.note}"
                built.append((label, {"id": "primary.irp", "type": "irp", "device": row.system, "function": row.command,
                                      "confidence": DOUBTFUL_TIER if len(names[(row.system, row.command)]) > 1 else TIER, "source": cite}))
            final = model
            while (BRAND.casefold(), final.casefold()) in authored:
                renamed = f"{final} (Cary Audio table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            keys = {n: {"label": b_[0], "forms": [b_[1]]} for n, b_ in zip(key_names([b_[0] for b_ in built]), built)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                        {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(PROTOCOL), "keys": keys}))
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    return build_dms(snapshot, authored, report) + build_sheets(snapshot, authored, report)
