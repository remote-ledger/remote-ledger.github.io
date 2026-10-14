"""NAD's IR command tables for its T-series receivers and processors (DESIGN section 72).

NAD's control documents for the T175, T765, T775, T785, T187, T777 and T787 each hold three pages, ``IR Commands by Model``: a table of
every function the unit's remote sends, ``Function``, ``Format`` (``NEC``), the ``Address`` and the ``Data`` byte of the NEC code in
hexadecimal and in decimal, and an empty ``Comment``. They are plain text on the page with no ruling lines, so each page is read as its
cells at a place (``tools/official_sheets_to_csv.py``, ``cells``): a row is ``page, y, x, text, x, text...``. The page header ``Model
T187`` names the model, and the header row (``Function``, ``Format``, ``Address``, ``Data``, ``Address``, ``Data``, ``Comment``, with
``Hex`` and ``Dec`` under the four numbers) gives each column's place.

A key is the ledger's ``NEC1``: the address ``877C`` is two bytes, **the device 0x87 and the subdevice 0x7C**, as NAD's remotes send it
(the same pair as in every other source's NAD remote), and the data byte the function. **Both the hexadecimal and the decimal of a
number are read and must agree**; a row whose two disagree is an error, not a guess. The label is the function as written
(``Zone Tune Up``, ``DB (XM or DAB)``). The tables are the codes of IR channel 0 (the default), which is standard NEC; NAD's
``IR Channels`` note says that a channel above 0 is not (the tables do not list them, and the note is not pinned).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "nad"
BRAND = "NAD"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
#: the document -> the model its table is for, and the file's name where that is not the model's
DOCUMENTS: dict[str, tuple[str, str]] = {
    "T175_IR_Commands.pdf": ("T175", "T175"),
    "T765_IR_Commands.pdf": ("T765", "T765"),
    "T775_IR_Commands_2008-02.pdf": ("T775", "T775 (February 2008 table)"),
    "T775_IR_Commands.pdf": ("T775", "T775"),
    "T785_IR_Commands.pdf": ("T785", "T785"),
    "T187_IR_Commands.pdf": ("T187", "T187"),
    "T777_IR_Commands.pdf": ("T777", "T777"),
    "T787_IR_Commands.pdf": ("T787", "T787"),
}
HEADER = ("Function", "Format", "Address", "Data", "Address", "Data", "Comment")
_MODEL = re.compile(r"^Model (\S+)$")


def cells_of(row: list[str]) -> tuple[int, int, list[tuple[int, str]]]:
    """``(page, y, [(x, text), ...])`` of one row of the ``cells`` sheet."""
    return int(row[0]), int(row[1]), [(int(x), text) for x, text in zip(row[2::2], row[3::2])]


def columns(rows: list[tuple[int, int, list[tuple[int, str]]]]) -> dict[str, int]:
    """The place of each column, from the header row and the ``Hex``/``Dec`` row under it."""
    head = next(((y, cells) for _, y, cells in rows if tuple(t for _, t in cells) == HEADER), None)
    if head is None:
        raise ValueError(f"no header row {HEADER}")
    y, cells = head
    below = next((cells for _, y2, cells in rows if 0 < y2 - y < 30 and [t for _, t in cells] == ["Hex", "Hex", "Dec", "Dec"]), None)
    if below is None:
        raise ValueError("no Hex/Hex/Dec/Dec row under the header")
    xs = [x for x, _ in cells]
    return {"function": xs[0], "format": xs[1], "address_hex": below[0][0], "data_hex": below[1][0], "address_dec": below[2][0],
            "data_dec": below[3][0], "comment": xs[6]}


def parse(sheet: list[list[str]]) -> tuple[str, list[tuple[int, str, str, int, int, str]]]:
    """``(the model, [(page, function, format, address, data, comment), ...])`` of one document."""
    rows = [cells_of(r) for r in sheet]
    models = {m[1] for _, _, cells in rows for _, t in cells if (m := _MODEL.match(t))}
    if len(models) != 1:
        raise ValueError(f"the pages name {len(models)} models: {sorted(models)}")
    places = columns(rows)
    out = []
    for page, y, cells in rows:
        if not cells or cells[0][1] in ("Function", "Hex") or _MODEL.match(cells[0][1]) or not any(t == "NEC" for _, t in cells):
            continue
        by: dict[str, str] = {}
        for x, text in cells:
            name, place = min(places.items(), key=lambda kv: abs(kv[1] - x))
            if abs(place - x) > 25:
                raise ValueError(f"page {page} y {y}: {text!r} at {x} is in no column")
            by[name] = by[name] + " " + text if name in by else text
        missing = {"function", "format", "address_hex", "data_hex", "address_dec", "data_dec"} - set(by)
        if missing:
            raise ValueError(f"page {page} y {y}: no {sorted(missing)} in {by}")
        address, data = int(by["address_hex"], 16), int(by["data_hex"], 16)
        if address != int(by["address_dec"]) or data != int(by["data_dec"]):
            raise ValueError(f"page {page} y {y} '{by['function']}': the hexadecimal and the decimal are not one number: {by}")
        out.append((page, by["function"], by["format"], address, data, by.get("comment", "")))
    return models.pop(), out


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
    for file, (expected, name) in DOCUMENTS.items():
        model, rows = parse(snapshot.sheet_of(file, "cells"))
        if model != expected:
            raise ValueError(f"{file} is for the model {model}, not {expected}")
        sha = snapshot.sha(file)
        report.count(MAKER, f"{file}: rows read", len(rows))
        built: list[tuple[str, dict[str, Any]]] = []
        for page, function, fmt, address, data, comment in rows:
            if fmt != "NEC" or not 0 <= address <= 0xFFFF or not 0 <= data <= 0xFF:
                report.unrepresented[(MAKER, file, f"format {fmt!r}, address {address:#x}, data {data:#x}: not NEC with a 16-bit address")] += 1
                continue
            device, subdevice = address >> 8, address & 0xFF
            if (device, subdevice, data) not in gate:
                gate[(device, subdevice, data)] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                    "id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": data,
                    "confidence": TIER, "source": "probe"})
            if gate[(device, subdevice, data)]:
                report.unrepresented[(MAKER, file, f"does not compile: {gate[(device, subdevice, data)]}")] += 1
                continue
            cite = (f"nadelectronics.com IR command tables/{file}@{sha} (retrieved {snapshot.retrieved}) page {page} row '{function}' "
                    f"(NEC, address {address:04X}, data {data} = 0x{data:02X}): NEC1 device 0x{device:02X} subdevice 0x{subdevice:02X} "
                    f"function {data}")
            if comment:
                cite += f"; the table's comment: {comment}"
            built.append((function, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": data,
                                     "confidence": TIER, "source": cite}))
        final = name
        while (BRAND.casefold(), final.casefold()) in authored:         # R15: another source has the name; the file is kept under another
            renamed = f"{final} (NAD IR table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        keys = {n: {"label": b[0], "forms": [b[1]]} for n, b in zip(key_names([b[0] for b in built]), built)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"],
                     "protocol": dict(PROTOCOL), "keys": keys}))
    return out
