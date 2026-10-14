"""NAD's IR command tables for its T-series receivers and processors (DESIGN section 72) and its Crestron IR files for its older products
(DESIGN section 73, at the end of this docstring).

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

**The Crestron files** (January 2005, 16 ``.ir`` files and a ``readme.pdf``) are binary records, ``length, tag, length - 2 bytes`` (the
length counts itself and the tag), written out by ``tools/official_sheets_to_csv.py`` as ``number, tag, body`` in hexadecimal. A header
(tags ``f0`` to ``f7``: ``IR``, a comment, the date ``012005``, ``NAD``, the models, ``<none>``, two bytes, the kind of product) is
followed by one record for each key naming it (tag = its number, body = its name), a record ``ff`` and one record for each key with
its signal: **a timing table, then 32 data bits as the bytes 0 and 1, then a trailer**. **There are two such frames**: the one with the
timing table 21, 64, 86, 171, 343 carrier cycles (a 9 ms mark and a 4.5 ms space for the lead, 0.55 ms marks, 1.68 ms spaces for a
one) is NEC; the one with 21, 64, 171, 1692, 3707 (a 4.5 ms mark and a 4.5 ms space, no 9 ms) is ``NECx1``. **The bits go least
significant first** (the receivers' are then 0x87, 0x7C, the function and its complement, as the T-series tables and every other
source's NAD remote have them), and **the fourth byte must be the complement of the third**. The readme says which file is for which
model; each model of a file is a file of its own.
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
PROTOCOLS = {"NEC1": PROTOCOL, "NECx1": {"name": "NECx1", "carrierHz": 38000, "minSends": 1}}
README = "crestron_readme.pdf"
CRESTRON = ("cd1", "cd2", "cd3", "cd4", "cd5", "dvd1", "dvd2", "receiver1", "receiver2_1", "receiver2_2", "receiver3", "receiver4", "tape",
            "tuner", "tv1", "tv2")
#: a key's signal: the head up to the 32 data bits and the tail after them, for NEC (A) and for NECx1 (B)
FRAMES = {"NEC1": (bytes.fromhex("6922020700150040005600ab015705f10e5043"), bytes.fromhex("054206")),
          "NECx1": (bytes.fromhex("692203050015004000ab069c0e7b22"), bytes.fromhex("03220004"))}
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


def decode_frame(body: bytes) -> tuple[str, tuple[int, int, int, int]] | None:
    """``(protocol, the four bytes)`` of a key's signal, or None where it is neither of the two frames: the head, 32 bits as the bytes 0 and
    1, the tail. The bits go least significant first, so the first eight are the first byte."""
    for protocol, (head, tail) in FRAMES.items():
        if body.startswith(head) and body.endswith(tail) and len(body) == len(head) + 32 + len(tail):
            bits = body[len(head):len(head) + 32]
            if set(bits) <= {0, 1}:
                v = tuple(sum(bits[8 * i + k] << k for k in range(8)) for i in range(4))
                return protocol, v          # type: ignore[return-value]
    return None


def parse_ir(rows: list[list[str]]) -> tuple[dict[int, str], dict[int, str], list[tuple[int, int, bytes]]]:
    """``(header, names, keys)`` of a Crestron file's records: the header's tags ``f0`` to ``f7`` as text, the key names by number, and
    each key's ``(number, the record's number, the body)`` after the record ``ff``."""
    header: dict[int, str] = {}
    names: dict[int, str] = {}
    keys: list[tuple[int, int, bytes]] = []
    past = False
    for number, tag, body in ((int(r[0]), int(r[1], 16), bytes.fromhex(r[2])) for r in rows):
        if tag == 0xFF:
            past = True
        elif tag >= 0xF0 and not past:
            header[tag] = body.decode("latin1") if all(32 <= c < 127 for c in body) else body.hex()
        elif not past:
            names[tag] = body.decode("latin1")
        else:
            keys.append((tag, number, body))
    return header, names, keys


def readme_models(rows: list[list[str]]) -> dict[str, str]:
    """The readme's ``file -> the models text``: ``dvd1.ir L55, L56, T550, T562`` on one line, or the models on the line after the file."""
    lines = [r[1].strip() for r in rows]
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = re.match(r"^(\w+\.ir)\s*(.*)$", line)
        if m:
            out[m[1]] = m[2].strip() or (lines[i + 1] if i + 1 < len(lines) and ".ir" not in lines[i + 1] else "")
    return out


def crestron_models(file: str, text: str, header: dict[int, str]) -> list[str]:
    """The models of a file: the readme's list; ``All Others`` is the header's kind of product, ``(all others)``; ``Second Zone Commands`` is
    the header's own words. A list must be the header's (a different one is an error)."""
    kind = header.get(0xF4, "")
    if text == "All Others":
        return [f"{kind} (all others)"]
    if text == "Second Zone Commands":
        return [kind]
    models = [m.strip() for m in text.split(",") if m.strip()]
    if models != [m.strip() for m in kind.split(",")]:
        raise ValueError(f"{file}: the readme says {models} and the file says {kind!r}")
    return models


def build_crestron(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    where = readme_models(snapshot.sheet_of(README, "pages"))
    readme_sha = snapshot.sha(README)
    for stem in CRESTRON:
        file = f"crestron_{stem}.ir"
        header, names, keys = parse_ir(snapshot.sheet_of(file, "records"))
        if f"{stem}.ir" not in where:
            raise ValueError(f"the readme does not list {stem}.ir")
        text = where[f"{stem}.ir"]
        models = crestron_models(stem, text, header)
        sha = snapshot.sha(file)
        report.count(MAKER, f"{file}: keys read", len(keys))
        built: list[tuple[str, dict[str, Any], str]] = []
        for tag, number, body in keys:
            decoded = decode_frame(body)
            if decoded is None or tag not in names:
                report.unrepresented[(MAKER, file, "a key whose signal is neither of the two frames" if decoded is None else "a key with no name")] += 1
                continue
            protocol, (device, subdevice, function, complement) = decoded
            if function ^ complement != 0xFF:
                report.unrepresented[(MAKER, file, "a frame whose fourth byte is not the complement of the third")] += 1
                continue
            if (protocol, device, subdevice, function) not in gate:
                gate[(protocol, device, subdevice, function)] = form_compiles(IMPORT_ROOT, PROTOCOLS[protocol], "K", {
                    "id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                    "confidence": TIER, "source": "probe"})
            if gate[(protocol, device, subdevice, function)]:
                report.unrepresented[(MAKER, file, f"does not compile: {gate[(protocol, device, subdevice, function)]}")] += 1
                continue
            name = names[tag]
            cite = (f"nadelectronics.com Crestron IR files/{file}@{sha} (retrieved {snapshot.retrieved}) key {tag} '{name}' (record "
                    f"{number}, frame {device:02X} {subdevice:02X} {function:02X} {complement:02X}): {protocol} device 0x{device:02X} "
                    f"subdevice 0x{subdevice:02X} function {function}; the readme ({README}@{readme_sha}) lists the file for "
                    f"{text if text.startswith(('All', 'Second')) else ', '.join(models)}")
            built.append((name, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                                 "confidence": TIER, "source": cite}, protocol))
        if not built:
            continue
        if len({b[2] for b in built}) != 1:
            raise ValueError(f"{file}: its keys are of two protocols")
        protocol = built[0][2]
        for model in models:
            final = model
            while (BRAND.casefold(), final.casefold()) in authored:
                renamed = f"{final} (NAD Crestron file)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            keys_out = {n: {"label": b[0], "forms": [b[1]]} for n, b in zip(key_names([b[0] for b in built]), built)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys_out))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                        {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(PROTOCOLS[protocol]), "keys": keys_out}))
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    return build_tables(snapshot, authored, report) + build_crestron(snapshot, authored, report)


def build_tables(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
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
