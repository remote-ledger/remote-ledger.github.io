"""Cambridge Audio's NEC code lists that carry no copyright or rights notice (DESIGN section 65).

Cambridge's driver-updates page lists sixteen IR code PDFs and a list of three remotes' codes; **fifteen of the PDFs (thirteen
documents: two are listed twice) end with ``(c) Copyright Cambridge Audio Ltd`` and eight of the thirteen add "All rights are
reserved", so they are held in the private repository** (D130). Two documents have no such text anywhere in them, which is
the rule D110 holds public: the **AXR85/AXR100** table, ``Remote Button`` and
``IR Code`` (``C0 3F 06 F9``, "All Codes are NEC format"), and the **752BD, CXU and CXUHD** remotes' list, a table whose header
says ``NEC: 49 B6`` and whose rows give a function code and its complement (``04 FB``) and what the button is called on each of
the three products (``N/A`` where it has none).

A code is four bytes, the address, its complement, the function and its complement, which is the ledger's ``NEC1`` with the
address as the device, its complement as the subdevice and the function as the function: the IR Blaster's AXR85 remote holds
``A1`` as device ``0xC0``, subdevice ``0x3F``, function ``0x1B``, which is this table's ``C0 3F 1B E4``. A row whose
complements do not complement is left out, because NEC1 sends the function's complement itself.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "cambridge"
BRAND = "Cambridge Audio"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}


@dataclass(frozen=True)
class Document:
    file: str
    layout: str                 # ``bytes`` (a label and four bytes) or ``columns`` (a function code and a name for each product)
    models: tuple[str, ...]


DOCUMENTS: tuple[Document, ...] = (
    # "Topaz AXR85/100 Stereo Receiver Remote Control Codes": one table for both receivers
    Document("AXR85_100_IR_Codes.pdf", "bytes", ("AXR85", "AXR100")),
    Document("752BD-CXU-CXUHD_remotes_Sept_2024_v2.pdf", "columns", ("752BD", "CXU", "CXUHD")),
)
_FOUR = re.compile(r"^([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})$")
_TWO = re.compile(r"^([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})$")
_HEADER = re.compile(r"^NEC:\s*([0-9A-Fa-f]{2})\s+([0-9A-Fa-f]{2})$")


@dataclass(frozen=True)
class Row:
    page: int
    table: int
    label: str                  # the name as written
    written: str                # the code as written, for the citation
    device: int
    subdevice: int
    function: int
    complements: bool           # the address's and the function's second bytes complement the first
    product: str                # the column it is in, or empty for a one-product table


def _byte_row(page: str, table: str, cells: list[str]) -> Row | None:
    if len(cells) < 2:
        return None
    m = _FOUR.match(" ".join(cells[1].split()))
    if not cells[0].strip() or not m:
        return None
    a, a2, f, f2 = (int(g, 16) for g in m.groups())
    return Row(int(page), int(table), " ".join(cells[0].split()), " ".join(cells[1].split()), a, a2, f,
               a2 == a ^ 0xFF and f2 == f ^ 0xFF, "")


def byte_rows(rows: list[list[str]]) -> list[Row]:
    """The ``Remote Button | IR Code`` table: the label and four bytes a row (the header, ``Remote Button``, is not one)."""
    out = []
    for row in rows:
        parsed = _byte_row(row[0], row[1], row[2:])
        if parsed:
            out.append(parsed)
    return out


def column_rows(rows: list[list[str]]) -> tuple[list[Row], list[str]]:
    """The ``NEC: 49 B6 | 752BD | CXU | CXUHD`` table (and its continuation, which repeats the header): a row whose first cell is
    two bytes is a function code, and each product's cell is what the button is called there, ``N/A`` where it has none.
    Returns the rows and the notes of the rows that send no code (``Backlight``)."""
    out: list[Row] = []
    notes: list[str] = []
    header: tuple[int, int, list[str]] | None = None
    for row in rows:
        page, table, cells = row[0], row[1], [" ".join(c.split()) for c in row[2:]]
        if not cells:
            continue
        h = _HEADER.match(cells[0])
        if h:
            header = (int(h[1], 16), int(h[2], 16), cells[1:])
            continue
        if header is None:
            continue
        two = _TWO.match(cells[0])
        if not two:
            if cells[0] and header:
                notes.append(f"page {page} row '{cells[0]}' ({', '.join(f'{p} {c}' for p, c in zip(header[2], cells[1:]))}): "
                             "a feature of the remote that sends no code")
            continue
        address, address2, products = header
        function, function2 = int(two[1], 16), int(two[2], 16)
        for product, name in zip(products, cells[1:]):
            if not name or name.upper() == "N/A":
                continue
            out.append(Row(int(page), int(table), name, cells[0], address, address2, function,
                           address2 == address ^ 0xFF and function2 == function ^ 0xFF, product))
    return out, notes


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
        sheet = snapshot.sheet_of(document.file, "tables")
        if document.layout == "bytes":
            rows, notes = byte_rows(sheet), []
        else:
            rows, notes = column_rows(sheet)
        for note in notes:
            report.notes.append((MAKER, document.file, note))
        sha = snapshot.sha(document.file)
        where = f"cambridgeaudio.com IR code documents/{document.file}"
        report.count(MAKER, f"{document.file}: rows read", len(rows))
        for model in document.models:
            members: list[tuple[str, dict[str, Any]]] = []
            named: dict[str, str] = {}          # the label of a code of this product -> the code, to find a name used twice
            for row in rows:
                if row.product and row.product != model:
                    continue
                if not row.complements:
                    report.unrepresented[(MAKER, document.file, "the bytes are not an address, its complement, a function and "
                                                                "its complement, which NEC1 sends")] += 1
                    continue
                triple = (row.device, row.subdevice, row.function)
                if triple not in gate:
                    gate[triple] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                        "id": "primary.irp", "type": "irp", "device": row.device, "subdevice": row.subdevice,
                        "function": row.function, "confidence": TIER, "source": "probe"})
                if gate[triple]:
                    report.unrepresented[(MAKER, document.file, f"does not compile: {gate[triple]}")] += 1
                    continue
                under = f", in the {row.product} column" if row.product else ""
                cite = (f"{where}@{sha} (retrieved {snapshot.retrieved}) page {row.page} table {row.table} row '{row.label}' "
                        f"(code {row.written}{under}): NEC1 device {row.device} subdevice {row.subdevice} function {row.function}")
                first = named.setdefault(row.label.casefold(), row.written)
                if first != row.written:
                    why = f"the document gives the name '{row.label}' to another code of {model} as well ({first})"
                    cite += "; " + why
                    report.notes.append((MAKER, f"{document.file} {model}", f"'{row.label}' ({row.written}): {why}"))
                members.append((row.label, {
                    "id": "primary.irp", "type": "irp", "device": row.device, "subdevice": row.subdevice,
                    "function": row.function, "confidence": TIER if first == row.written else DOUBTFUL_TIER, "source": cite}))
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
