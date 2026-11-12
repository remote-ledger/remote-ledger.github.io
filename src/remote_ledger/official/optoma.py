"""Optoma's discrete IR code tables for its projectors (DESIGN section 101).

Optoma writes one small table for a projector (a PDF page of the user manual, or a sheet of its own): **a key (a number, a printing or a description), the NEC code the
key sends**. Almost all of them say ``Optoma Code: 32CD``, the customer code, and give the key code as one byte (``81``, ``2E``; the TH7500's writes it as ``002E``).
The ledger's ``NEC1`` form is that: the two bytes of the customer code as they are sent (``32`` and ``CD``) the device and the subdevice, the key code the function (the
check byte, which NEC sends after it, is the form's). Five layouts, read from the lines of text of each page (``lines``, a row of cells at places):

- ``numbered`` (the HD33 and HD3300, the TH7500-NL; and with the printing of the key and its meaning with the on-screen menu on, the TW1692, TW536, TX615 and TX762):
  ``Key``, ``Description``, ``Key code`` (and two more columns); a row starts at the next number, and a description may wrap to a second line;
- ``codes`` (the EH501, W501 and X501, which are one page of the manual): ``Key code`` and ``Printing-key definition``, no numbers;
- ``bytes`` (the GT750 and GT750E): the NEC code written as four bytes, the customer code in two (``32`` ``CD``) and the data in two (``02`` ``FD``), a ``Format``;
- ``legend`` (the ThemeScene HD82): the NEC code as four bytes, custom code ``00 02`` and data ``00 FF``, the data's first byte in decimal for some keys and in hexadecimal
  for others, which the second byte (the first's complement) says.

**A row that is not a key is never dropped silently**: a key with no code (``Laser Pointer``: ``-``, ``N/A``), a number with no key, and a row whose two bytes of one
code are not each other's complements are in the report (``not read:``).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "optoma"
BRAND = "Optoma"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
AUDIOGENERAL = "audiogeneral.com Optoma IR code PDFs"
OPTOMA_UK = "optoma.co.uk IR code PDFs"
_HEX = re.compile(r"^[0-9A-F]{2}$")
_CODE = re.compile(r"^(?:00)?([0-9A-F]{2})$")
ARROWS = {"\u2191": "\u2191 (Up arrow)", "\u2190": "\u2190 (Left arrow)", "\u2192": "\u2192 (Right arrow)", "\u2193": "\u2193 (Down arrow)"}       # a legend that is a glyph is also named
_CUSTOMER = re.compile(r"(?:Optoma Code|Customer code)\s*:\s*([0-9A-F]{2})([0-9A-F]{2})\b")


@dataclass(frozen=True)
class Sheet:
    file: str
    models: tuple[str, ...]
    layout: str
    title: str                                    # words the title line of the page says (the model's name, or the models')
    host: str = AUDIOGENERAL


SHEETS: tuple[Sheet, ...] = (
    Sheet("eh501_ir.pdf", ("EH501",), "codes", "IR Code"),
    Sheet("w501_ir.pdf", ("W501",), "codes", "IR Code"),
    Sheet("x501_ir.pdf", ("X501",), "codes", "IR Code"),
    Sheet("gt750e_ir.pdf", ("GT750", "GT750E"), "bytes", "GT750 and GT750E Discrete IR Codes"),
    Sheet("hd33_ir.pdf", ("HD33", "HD3300"), "numbered", "HD33 and HD3300 Discrete IR Codes"),
    Sheet("th7500nl_ir.pdf", ("TH7500-NL",), "numbered", "TH7500-NL Discrete IR Codes"),
    Sheet("tw1692_ir.pdf", ("TW1692",), "numbered", "TW1692 Discrete IR Codes"),
    Sheet("tw536_ir.pdf", ("TW536",), "numbered", "TW536 Discrete IR Codes"),
    Sheet("tx615_ir.pdf", ("TX615",), "numbered", "TX615 Discrete IR Codes"),
    Sheet("tx762_ir.pdf", ("TX762",), "numbered", "TX762 Discrete IR Codes"),
    Sheet("HD82-IRE-en-GB.pdf", ("HD82",), "legend", "ThemeScene HD82 Infrared Remote Control Codes", OPTOMA_UK),
)


@dataclass(frozen=True)
class Key:
    page: int
    where: str                                    # ``row '12 Menu'``: how the citation names the row
    label: str
    device: int
    subdevice: int
    function: int
    written: str                                  # the code as the table writes it
    note: str = ""                                # what else the row says (``Format2``, ``printed Up, with the menu on: Enter``)
    doubt: str = ""                               # why the row is Untested


def tidy(text: str) -> str:
    """The words of a cell with no control character in them (the HD82's key with no legend has the text layer's ESC)."""
    return " ".join(re.sub(r"[\x00-\x1f]", " ", text).split())


def lines_of(rows: list[list[str]]) -> list[tuple[int, int, list[tuple[int, str]]]]:
    """``(page, y, [(x, text)...])`` of each line of a ``lines`` CSV."""
    out = []
    for row in rows:
        cells = [(int(row[i]), tidy(row[i + 1])) for i in range(2, len(row) - 1, 2)]
        out.append((int(row[0]), int(row[1]), cells))
    return out


def title_of(lines: list[tuple[int, int, list[tuple[int, str]]]], sheet: Sheet) -> None:
    head = " ".join(t for _, _, cells in lines[:6] for _, t in cells)
    if sheet.title.casefold() not in head.casefold():
        raise ValueError(f"{sheet.file}: the title of the page does not say `{sheet.title}`: {head[:120]!r}")


def customer_of(lines, sheet: Sheet) -> tuple[int, int] | None:
    found = {m.groups() for _, _, cells in lines for _, t in cells for m in [_CUSTOMER.search(t)] if m}
    if len(found) > 1:
        raise ValueError(f"{sheet.file}: more than one customer code: {sorted(found)}")
    if not found:
        return None
    high, low = next(iter(found))
    return int(high, 16), int(low, 16)


def key_code(text: str) -> int | None:
    """A key code as the table writes it: one byte, or a byte after ``00`` (the TH7500's ``002E``); ``-`` and ``N/A`` are none."""
    m = _CODE.match(text.upper())
    return int(m[1], 16) if m else None


def parse_numbered(lines, sheet: Sheet, customer: tuple[int, int], notes: list[str]) -> list[Key]:
    """Rows that start at the next number (the first cell, left of the description) and go on until the next: the description is the cells before the code column
    (a description may wrap), the code is the first cell at or beyond x = 300 and the cells after it are the printing and the meaning with the menu on."""
    out: list[Key] = []
    expected, row = 1, None
    rows: list[list[tuple[int, str]]] = []
    for page, y, cells in lines:
        cells = [(x, t) for x, t in cells if not _CUSTOMER.search(t)]
        if cells and cells[0][0] < 140 and cells[0][1] == str(expected):
            rows.append(list(cells))
            expected += 1
        elif rows and cells:
            rows[-1].extend(cells)
    if not rows:
        raise ValueError(f"{sheet.file}: no row of a numbered table")
    for r in rows:
        n = r[0][1]
        words = [t for x, t in r[1:] if x < 300]
        beyond = [t for x, t in r[1:] if x >= 300]
        label = tidy(" ".join(words))
        if not label:
            notes.append(f"{sheet.file} row {n}: a number with no key ({beyond})")
            continue
        code = key_code(beyond[0]) if beyond else None
        if code is None:
            notes.append(f"{sheet.file} row {n} '{label}': no code (`{beyond[0] if beyond else ''}`)")
            continue
        extra = ""
        if len(beyond) > 1:
            printing, menu = beyond[1], " ".join(beyond[2:])
            extra = f"printed `{printing}`" + (f", with the menu on `{menu}`" if menu and menu != "-" else "")
        out.append(Key(1, f"row '{n} {label}'", label, customer[0], customer[1], code, beyond[0], extra))
    return out


def parse_codes(lines, sheet: Sheet, customer: tuple[int, int], notes: list[str]) -> list[Key]:
    """The manual's page of ``Key code`` and ``Printing-key definition``: a row is a code cell and a definition; the lines that are neither are the page's own (the title, the
    page number, ``The IR codes below are for user-defined remote only``, which is in the citation of the rows under it)."""
    out: list[Key] = []
    section = ""
    for page, y, cells in lines:
        texts = [t for _, t in cells]
        if len(texts) >= 2 and (_HEX.match(texts[0]) or texts[0] == "N/A") and cells[0][0] < 100:
            label = tidy(" ".join(texts[1:]))
            if texts[0] == "N/A":
                notes.append(f"{sheet.file} page {page} '{label}': no code (`N/A`)")
                continue
            out.append(Key(page, f"row '{texts[0]} {label}'", label, customer[0], customer[1], int(texts[0], 16), texts[0], section))
        elif len(texts) == 1 and texts[0].startswith("The IR codes below"):
            section = f"under '{texts[0]}'"
        elif texts and texts[0] not in ("Key code",) and not re.match(r"^(?:EN-\d+|IR Code|Customer code:.*)$", texts[0]):
            notes.append(f"{sheet.file} page {page}: {texts}")
    return out


def parse_bytes(lines, sheet: Sheet, notes: list[str]) -> list[Key]:
    """The GT750's four bytes: ``n, description, MTX repeat, Format1|2, 32, CD, key, check``. A row with no bytes is a number (and a code of the matrix) and not a key; a row
    whose key byte and check byte are not complements, or whose customer code is not ``32 CD``, is not read."""
    out: list[Key] = []
    expected = 1
    rows: list[list[tuple[int, str]]] = []
    for page, y, cells in lines:
        if cells and cells[0][0] < 140 and cells[0][1] == str(expected):
            rows.append(list(cells))
            expected += 1
        elif rows and cells and not (cells[0][0] > 290):
            rows[-1].extend(cells)
    for r in rows:
        n = r[0][1]
        words = [t for x, t in r[1:] if x < 200]
        label = tidy(" ".join(words))
        tail = [t for x, t in r[1:] if x >= 290]
        form = next((t for x, t in r[1:] if t.startswith("Format")), "")
        if len(tail) != 4 or not all(_HEX.match(t) for t in tail):
            notes.append(f"{sheet.file} row {n}{' ' + repr(label) if label else ''}: no NEC code ({[t for _, t in r[1:]]})")
            continue
        b1, b2, b3, b4 = (int(t, 16) for t in tail)
        mtx = next((t for x, t in r[1:] if 200 <= x < 250), "")
        if b3 ^ b4 != 0xFF:
            notes.append(f"{sheet.file} row {n} '{label}': the data {tail[2]} {tail[3]} are not complements")
            continue
        if b2 != b1 ^ 0xFF:
            if b2 == 0xCD:
                notes.append(f"{sheet.file} row {n} '{label}': the customer code is {tail[0]} {tail[1]}, not 32 CD: read as 32 CD and Untested")
                out.append(Key(1, f"row '{n} {label}'", label, 0x32, 0xCD, b3, " ".join(tail), f"{form}, MTX repeat {mtx}",
                               f"the row's first byte is `{tail[0]}` where its second byte `CD` is the complement of 32 and every other row says 32"))
                continue
            notes.append(f"{sheet.file} row {n} '{label}': the customer code {tail[0]} {tail[1]} is not a byte and its complement")
            continue
        out.append(Key(1, f"row '{n} {label}'", label, b1, b2, b3, " ".join(tail), f"{form}, MTX repeat {mtx}"))
    return out


def parse_legend(lines, sheet: Sheet, notes: list[str]) -> list[Key]:
    """The ThemeScene HD82's rows: ``legend, 00, 02, key, check``; the key byte is decimal or hexadecimal and the check byte, its complement, says which."""
    out: list[Key] = []
    for page, y, cells in lines:
        bytes_ = [(x, t) for x, t in cells if x >= 270]
        if len(bytes_) != 4 or not all(_HEX.match(t) for _, t in bytes_):
            if out and cells:                                       # the heading's lines come before the first row
                notes.append(f"{sheet.file}: {[t for _, t in cells]}")
            continue
        legend = re.sub(r"\s*\uFE55\s*", ":", tidy(" ".join(t for x, t in cells if x < 270))) or "(no legend)"
        legend = ARROWS.get(legend, legend)
        b1, b2, b3, b4 = (t for _, t in bytes_)
        check = int(b4, 16) ^ 0xFF
        candidates = {int(b3, 16), int(b3, 10) if b3.isdigit() else -1}
        if check not in candidates:
            notes.append(f"{sheet.file} row '{legend}': the key byte {b3} and the check byte {b4} are not complements as hexadecimal ({int(b3, 16) ^ 0xFF:02X}) or as decimal "
                         f"({(int(b3, 10) ^ 0xFF) if b3.isdigit() else 0:02X}): not read")
            continue
        out.append(Key(1, f"row '{legend}'", legend, int(b1, 16), int(b2, 16), check, f"{b1} {b2} {b3} {b4}",
                       "the key byte is written in decimal" if int(b3, 16) != check else ""))
    return out


def parse_sheet(rows: list[list[str]], sheet: Sheet) -> tuple[list[Key], list[str]]:
    lines = lines_of(rows)
    title_of(lines, sheet)
    notes: list[str] = []
    customer = customer_of(lines, sheet)
    if sheet.layout in ("numbered", "codes"):
        if customer is None:
            raise ValueError(f"{sheet.file}: no `Optoma Code: 32CD` (the customer code)")
        keys = (parse_numbered if sheet.layout == "numbered" else parse_codes)(lines, sheet, customer, notes)
    elif sheet.layout == "bytes":
        keys = parse_bytes(lines, sheet, notes)
    else:
        keys = parse_legend(lines, sheet, notes)
    if not keys:
        raise ValueError(f"{sheet.file}: no key read")
    return keys, notes


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple[int, int, int], str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for sheet in SHEETS:
        keys, notes = parse_sheet(snapshot.sheet_of(sheet.file, "lines"), sheet)
        for note in notes:
            report.notes.append((MAKER, sheet.file, note if "read as" in note else f"not read: {note}"))
        entry, sha = snapshot.documents[sheet.file], snapshot.sha(sheet.file)
        report.count(MAKER, f"{sheet.file}: keys read", len(keys))
        names: dict[tuple[int, int, int], list[str]] = {}               # **a code the sheet gives to two keys is one it is wrong about in one of them**
        for k in keys:
            names.setdefault((k.device, k.subdevice, k.function), []).append(k.label)
        dup = Counter(k.label for k in keys)
        shown = [f"{k.label} ({k.written})" if dup[k.label] > 1 else k.label for k in keys]       # a label two keys share keeps the code that tells them apart
        for model in sheet.models:
            built: list[tuple[str, dict[str, Any]]] = []
            for k, label in zip(keys, shown):
                triple = (k.device, k.subdevice, k.function)
                if triple not in gate:
                    gate[triple] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": k.device, "subdevice": k.subdevice,
                                                                              "function": k.function, "confidence": TIER, "source": "probe"})
                if gate[triple]:
                    report.unrepresented[(MAKER, sheet.file, f"does not compile: {gate[triple]}")] += 1
                    continue
                twice = len(names[triple]) > 1
                cite = (f"{sheet.host}/{sheet.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}) page {k.page} {k.where} (code {k.written}"
                        + (f", {k.note}" if k.note else "") + f"): NEC1 device {k.device} subdevice {k.subdevice} function {k.function}"
                        + (f"; the sheet gives this code to {' and '.join(repr(n) for n in names[triple])}" if twice else "") + (f"; {k.doubt}" if k.doubt else ""))
                built.append((label, {"id": "primary.irp", "type": "irp", "device": k.device, "subdevice": k.subdevice, "function": k.function,
                                        "confidence": DOUBTFUL_TIER if twice or k.doubt else TIER, "source": cite}))
            final = model
            while (BRAND.casefold(), final.casefold()) in authored:
                renamed = f"{final} (Optoma table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            labels = [b_[0] for b_ in built]
            body = {n: {"label": lab, "forms": [b_[1]]} for n, lab, b_ in zip(key_names(labels), labels, built)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(body))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                        {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(PROTOCOL), "keys": body}))
    return out
