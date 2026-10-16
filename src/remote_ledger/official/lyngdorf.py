"""Lyngdorf's table of NEC codes for its amplifiers, remote and CD player (DESIGN section 79).

One PDF page, ``NEC IR Code Format for Lyngdorf products``: a row for each button and a column for each product (the first column
is four products', ``TDAI 2200``, ``Millennium Mk. III+IV``, ``DPA-1`` and ``SDAI 2175``, then ``RP-1`` and ``CD-1``), each cell two
16-bit words, ``0x10EF 0x0FF0``. The sheet is converted by the ``lines`` mode of ``tools/official_sheets_to_csv.py`` (one cell for each
line, by its place), and a column is the cells at one place. **The first word is the device byte and the byte after it** (``0x10EF`` is
the standard NEC address 0x10 and its complement; the RP-1's ``0x10E0`` and the CD-1's ``0x10E1`` are the extended form, the second byte
free) **and the second word the function and its complement** (``0x0FF0``: 0x0F and 0xF0, which the reader checks), in the order the ledger's
``NEC1`` has them, which the JP1 upgrade of a TDAI 1120 in the ledger shows (it has the TDAI 2200's 21 codes).
"""

from __future__ import annotations

import re
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "lyngdorf"
BRAND = "Lyngdorf"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
DOCUMENT = "Lyngdorf-IR-Codes.pdf"
#: the x of a column in the sheet's lines -> the products it is for, the order the first column lists them in
COLUMNS: dict[int, tuple[str, ...]] = {
    153: ("TDAI 2200", "Millennium Mk. III+IV", "DPA-1", "SDAI 2175"),
    246: ("RP-1",),
    339: ("CD-1",),
}
_CELL = re.compile(r"^0x([0-9A-Fa-f]{3,4}) 0x([0-9A-Fa-f]{4})$")
#: the rows the sheet says are the same for all products (``Note: Mute and Volume commands are the same for all products``)
SHARED = {"Mute", "Volume +", "Volume -"}


def cells_of(rows: list[list[str]], page: int = 1) -> tuple[dict[int, list[str]], list[tuple[str, dict[int, str], int]]]:
    """``(the names above each column's header, the rows)``: a row is ``(button, {column x: cell}, y)``.

    A column is the cells within 3 points of the x of the first cell of the ``Button`` row's header; a row with no cell in a
    column (the ``Stand By`` row of a product the page does not list) has nothing there."""
    names: dict[int, list[str]] = {x: [] for x in COLUMNS}
    out: list[tuple[str, dict[int, str], int]] = []
    header_at = next((i for i, r in enumerate(rows) if int(r[0]) == page and len(r) > 3 and r[3] == "Button"), None)
    if header_at is None:
        raise ValueError(f"page {page} has no 'Button' header row")
    for r in rows[:header_at]:
        if int(r[0]) == page and len(r) == 4 and int(r[2]) in COLUMNS:
            names[int(r[2])].append(r[3])
    header = rows[header_at]
    for x, name in zip(map(int, header[4::2]), header[5::2]):
        if x in COLUMNS:
            names[x].append(name)
    for r in rows[header_at + 1:]:
        if int(r[0]) != page or len(r) < 6 or r[3].startswith("Note:"):
            continue
        cells = {int(x): c for x, c in zip(r[4::2], r[5::2])}
        out.append((r[3], cells, int(r[1])))
    return names, out


def read_cell(cell: str, column_word: int | None) -> tuple[int, int, int, str] | None:
    """``(device, subdevice, function, the doubt or "")`` of one cell, or None for ``-``.

    A cell of three hex digits in the first word (``0x10E``: the last digit is missing in the PDF itself) is read as the column's
    own first word when that begins with them, and is doubtful; a second word whose low byte is not the complement of its high byte
    is doubtful too (and is still read as its high byte)."""
    if cell.strip() == "-":
        return None
    m = _CELL.match(cell.strip())
    if not m:
        raise ValueError(f"cannot read the cell {cell!r}")
    first, second = m.group(1), int(m.group(2), 16)
    doubts = []
    if len(first) == 3:
        if column_word is None or f"{column_word:04X}"[:3] != first.upper():
            raise ValueError(f"the cell {cell!r} lacks a digit and the column's own word does not complete it")
        word = column_word
        doubts.append(f"the cell is '{cell.strip()}' in the PDF, whose first word lacks its last digit, and is read as the column's 0x{column_word:04X}")
    else:
        word = int(first, 16)
    function, check = second >> 8, second & 0xFF
    if check != (~function & 0xFF):
        doubts.append(f"the second word 0x{second:04X} is not a function and its complement")
    return word >> 8, word & 0xFF, function, "; ".join(doubts)


def key_names(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    names, rows = cells_of(snapshot.sheet_of(DOCUMENT, "lines"))
    entry = snapshot.documents[DOCUMENT]
    where = f"lyngdorf.com IR code PDF/{DOCUMENT}"
    sha = snapshot.sha(DOCUMENT)
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for x, products in COLUMNS.items():
        first_row = next((cells for _, cells, _ in rows if x in cells and cells[x] != "-"), None)
        column_word = int(_CELL.match(first_row[x]).group(1), 16) if first_row else None
        column_label = " / ".join(names[x]) if names[x] else products[0]
        if names[x] != list(products):
            raise ValueError(f"column {x}'s header says {names[x]}, not {list(products)}")
        members: list[tuple[str, dict[str, Any]]] = []
        doubtful: list[str] = []
        for button, cells, _ in rows:
            if x not in cells:
                continue
            report.count(MAKER, "rows read")
            read = read_cell(cells[x], column_word)
            if read is None:
                continue
            device, subdevice, function, doubt = read
            triple = (device, subdevice, function)
            if triple not in gate:
                gate[triple] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                    "id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                    "confidence": TIER, "source": "probe"})
            if gate[triple]:
                report.unrepresented[(MAKER, DOCUMENT, f"does not compile: {gate[triple]}")] += 1
                continue
            cite = (f"{where}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']}) "
                    f"page 1 row '{button}' (column {column_label}: {cells[x]}): NEC1 device {device} subdevice {subdevice} function {function}, "
                    f"the first word's bytes in the order they are sent and the second word's high byte")
            if button in SHARED:
                cite += "; the sheet's note says the Mute and Volume commands are the same for all products"
            if doubt:
                cite += "; " + doubt
                doubtful.append(button)
            members.append((button, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                                     "confidence": DOUBTFUL_TIER if doubt else TIER, "source": cite}))
        if doubtful:
            report.notes.append((MAKER, f"{DOCUMENT} page 1", f"column {column_label}: {len(doubtful)} cells ({', '.join(doubtful)}) are 0x10E in the PDF, "
                                 f"the last digit missing, and are read as the column's 0x{column_word:04X}: each of those keys is Untested"))
        for model in products:
            final = model
            while (BRAND.casefold(), final.casefold()) in authored:
                renamed = f"{final} (Lyngdorf table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                        {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(PROTOCOL), "keys": keys}))
    return out
