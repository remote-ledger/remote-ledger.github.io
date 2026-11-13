"""BenQ's LK952 and HT5550 code lists (DESIGN sections 91 and 109).

``Appendix 2 - Code List: IR / RS232 / DDC Data`` of BenQ's LK952 laser projector (September 2020), six pages of one table with a row for each of
the 130 codes of the family's remote: its number, the key's function (``Freeze``, ``Source``, ``Menu/Exit``, ``Power Off``), a description, **the code**
(``0x4E``) and **whether the model supports it** (``Yes`` or ``No``), and beside the first rows the table of the IR format: ``Frequency 38 KHz``,
``Protocol NEC Format``, ``Custom Code 0x0030``. The PDF is on the protocols-and-drivers folder for projection screens of SnapAV's site (the Wayback
Machine's copy), and its tables are converted to CSV by ``tools/official_sheets_to_csv.py`` (``tables``).

**The HT5550's list (section 109) is the same appendix, 142 rows, as six pages of pictures with no text at all**: a live file of the same site (9,459,527 bytes;
the Wayback Machine's first copy of it is cut at 1,048,576 bytes), whose table is written down by eye as the LK952's ``tables`` CSV has it
(``HT5550_IR_Codes__transcribed.csv``: the descriptions are written for the supported rows, the ones that make keys) and read by the same code.

**A key is the ledger's NEC1**: the custom code 0x0030 is the device 0x00 and the subdevice 0x30 (the ledger's other BenQ remotes send the same codes
under it: ``Power On`` is 0x4F and ``Power Off`` 0x4E in all of them), the code the function, at 38 kHz. **Only the rows the model supports are keys** (34 of
130): a row that says ``No`` is a code of the family that the LK952 does not take, and is reported. The sheet writes two codes with a letter O (``Ox4F``,
``Ox50``): read as hex, and the citation says so. A code that two supported rows have makes both Untested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "benq"
BRAND = "BenQ"
HOST = "snapav.com BenQ IR code sheet"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
DEVICE, SUBDEVICE = 0x00, 0x30
ROWS_OF_THE_TABLE = {"BenQ_LK952_IR_Codes.pdf": 130, "HT5550_IR_Codes.pdf": 142}


@dataclass(frozen=True)
class Document:
    file: str
    model: str
    sheet: str                      # ``tables`` (the text layer's tables) or ``transcribed`` (a table that is a picture, written down by eye)


DOCUMENTS = (Document("BenQ_LK952_IR_Codes.pdf", "LK952", "tables"),
             Document("HT5550_IR_Codes.pdf", "HT5550", "transcribed"))
_CODE = re.compile(r"^([0Oo])x([0-9A-Fa-f]{2})$")


@dataclass(frozen=True)
class Row:
    page: int
    number: int
    function: str
    description: str
    code: int
    written: str                    # the code as the sheet writes it
    supported: bool


def parse(table: list[list[str]]) -> list[Row]:
    """The rows of the sheet's ``tables`` (``page, table, cells``): a row that begins with a number is a code (``No., Function, Description, Code, Support``), numbered
    1 to 130 in order, and the three cells of the IR format beside the first rows (``Frequency``, ``Protocol``, ``Custom Code``) are checked, not read as codes."""
    out: list[Row] = []
    format_cells: dict[str, str] = {}
    for row in table:
        cells = [" ".join(c.split()) for c in row[2:]]
        if len(cells) >= 7 and cells[5] and cells[6]:
            format_cells[cells[5]] = cells[6]
        if not cells or not re.fullmatch(r"\d+", cells[0]):
            continue
        number = int(cells[0])
        if number != len(out) + 1:
            raise ValueError(f"page {row[0]}: the row {number} is not the next one after {len(out)}")
        m = _CODE.match(cells[3])
        if m is None or cells[4] not in ("Yes", "No"):
            raise ValueError(f"page {row[0]}: row {number} has code {cells[3]!r} and support {cells[4]!r}")
        out.append(Row(int(row[0]), number, cells[1], cells[2], int(m.group(2), 16), cells[3], cells[4] == "Yes"))
    if format_cells != {"Frequency": "38 KHz", "Protocol": "NEC Format", "Custom Code": "0x0030"}:
        raise ValueError(f"the IR format of the sheet is {format_cells}, not 38 KHz, NEC Format and custom code 0x0030")
    return out


def key_names(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for document in DOCUMENTS:
        out.extend(build_document(document, snapshot, authored, report))
    return out


def build_document(document: Document, snapshot: Snapshot, authored: dict[tuple[str, str], str],
                   report: Report) -> list[tuple[str, dict[str, Any]]]:
    file, model = document.file, document.model
    rows = parse(snapshot.sheet_of(file, document.sheet))
    if len(rows) != ROWS_OF_THE_TABLE[file]:
        raise ValueError(f"{file}: {len(rows)} rows, not the table's {ROWS_OF_THE_TABLE[file]}")
    entry, sha = snapshot.documents[file], snapshot.sha(file)
    how = f", from the Wayback Machine snapshot {entry['snapshot']}" if entry.get("snapshot") else ""
    prefix = f"{HOST}/{file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}{how})"
    by_eye = ", read by eye from the page's picture" if document.sheet == "transcribed" else ""
    report.count(MAKER, "rows read", len(rows))
    members = []
    for row in rows:
        if not row.supported:
            report.unrepresented[(MAKER, file, "the sheet says the model does not support the code (Support: No)")] += 1
            continue
        members.append(row)
    by_code: dict[int, list[str]] = {}
    for row in members:
        by_code.setdefault(row.code, []).append(row.function)
    gate: dict[int, str | None] = {}
    final: list[tuple[str, dict[str, Any]]] = []
    for row in members:
        if row.code not in gate:
            gate[row.code] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": DEVICE, "subdevice": SUBDEVICE, "function": row.code,
                                                                       "confidence": TIER, "source": "probe"})
        if gate[row.code]:
            report.unrepresented[(MAKER, file, f"does not compile: {gate[row.code]}")] += 1
            continue
        cite = (f"{prefix} page {row.page} table row 'No. {row.number} {row.function}'{by_eye} (code {row.written}, Support {'Yes' if row.supported else 'No'}): "
                f"NEC1 device {DEVICE} subdevice {SUBDEVICE} function {row.code} (custom code 0x0030, 38 kHz, as the sheet's format table says)")
        if row.description:
            cite += f"; the sheet describes it: {row.description}"
        doubts = []
        if row.written[0] != "0":
            doubts_note = f"the sheet writes the code '{row.written}' with a letter O, read as 0x{row.code:02X}"
            cite += "; " + doubts_note
            report.notes.append((MAKER, f"{file} page {row.page}", f"'{row.function}': {doubts_note}"))
        if len(by_code[row.code]) > 1:
            doubts.append("the sheet gives this code to " + " and ".join(f"'{n}'" for n in by_code[row.code]) + ", and a code is one function")
        if doubts:
            cite += "; " + "; ".join(doubts)
        final.append((row.function, {"id": "primary.irp", "type": "irp", "device": DEVICE, "subdevice": SUBDEVICE, "function": row.code,
                                     "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
    name = model
    while (BRAND.casefold(), name.casefold()) in authored:
        renamed = f"{name} (BenQ sheet)"
        report.notes.append((MAKER, name, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), name.casefold())]} has the name (R15)"))
        name = renamed
    keys = {n: {"label": f[0], "forms": [f[1]]} for n, f in zip(key_names([f[0] for f in final]), final)}
    report.count(MAKER, "files")
    report.count(MAKER, "keys", len(keys))
    return [(f"{IMPORT_ROOT}/{MAKER}/{slug(name)}.json",
             {"manufacturer": BRAND, "model": name, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(PROTOCOL), "keys": keys})]
