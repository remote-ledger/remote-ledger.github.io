"""Hitachi America's remote IR code documents for its televisions (DESIGN section 82).

Hitachi America published for its remotes, in a folder of its support site, small tables of the codes a key or a discrete function sends:
two spreadsheets (the 2003 and 2004 TV remote and its codes, the 2004 remote and the codes of the TV it does not have a key for), three
PDFs of one table each (the DW3's, the L32 and larger's and the L19 and L26's remote) and one PDF of three sections that gives each code's
Pronto hex as well (``AVC``, ``UT Monitor`` and ``PDP``). **The documents are the Wayback Machine's copies** (its site no longer has them),
read from the CSV that ``tools/official_sheets_to_csv.py`` makes of each (a sheet, or a PDF's cells by place) and pinned by SHA-256 (D123).

A code is two 16-bit words, ``50AF 17E8``: **the ledger's ``NEC1``, the bytes in the order they are sent** (0x50 the device, 0xAF the
subdevice, 0x17 the function and 0xE8 its complement, which the ledger's 38 Hitachi remotes at 50 AF have: ``Power`` is 0x17). A table
whose code is in two cells (the L19 and L26's ``Custom Code`` ``50AF`` and ``Key Code`` ``17E8``) is the same. **The Pronto hex of the AVC
document is decoded and must agree with the code beside it.** A key a table says is a ``MODE KEY`` has no code and is no key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug
from .emotiva import Frame, decode_frame

MAKER = "hitachi"
BRAND = "Hitachi"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
HOST = "hitachi-america.us IR code documents"
_CODE = re.compile(r"^([0-9A-Fa-f]{2})([0-9A-Fa-f]{2}) ([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})$")
_WORD = re.compile(r"^[0-9A-Fa-f]{4}$")
#: the spreadsheet of 2003 has its arrow keys in a code page the library reads as two characters
_MOJIBAKE = (("\x81£", "▲"), ("\x81¥", "▼"), ("\x81^", "/"), ("／", "/"))


@dataclass(frozen=True)
class Table:
    file: str
    sheet: str
    model: str
    layout: str                   # keys_xls, label_code, function_hex, custom_key or pronto
    section: str = ""             # the pronto layout's section title
    note: str = ""


TABLES: tuple[Table, ...] = (
    Table("2003_and_2004_HITACHI_IR.xls", "Sheet2", "HL01322 (2003)", "keys_xls", note="the TV of the base model HL01322"),
    Table("2003_and_2004_HITACHI_IR.xls", "Sheet1", "HL01322 (2004)", "keys_xls", note="the TV of the base model HL01322 and the codes new for 2004 models"),
    Table("2004_IR_Codes-1.xls", "2004 Codes", "2004 TV remote", "label_code", note="'RC IR CODES'"),
    Table("2004_IR_Codes-1.xls", "Additional Codes", "2004 TV additional codes", "label_code", note="'ADDITIONAL IR CODES FOR TV NOT INCLUDED IN RC'"),
    Table("2007_Discrete_Codes-DW3.pdf", "cells", "DW3", "function_hex", note="the discrete layer of the remote HL02401, HL02402 and HL02403"),
    Table("2009_Discrete_Codes_L32_and_larger_0211.pdf", "cells", "L32 and larger", "function_hex", note="a 'Discrete Code Table'"),
    Table("2009_RC_Layouts_and_IR_Codes_L19_L26.pdf", "cells", "L19 and L26", "custom_key", note="the remote's keys"),
    Table("AVC_56_Discrete_Codes_UT_Monitor_PDP_Pronto_Hex.pdf", "cells", "AVC (2008)", "pronto", section="08 Hitachi AVC Discrete Hex codes + Pronto Hex Codes"),
    Table("AVC_56_Discrete_Codes_UT_Monitor_PDP_Pronto_Hex.pdf", "cells", "UT Monitor (2008)", "pronto", section="08 UT Monitor Discrete Codes"),
    Table("AVC_56_Discrete_Codes_UT_Monitor_PDP_Pronto_Hex.pdf", "cells", "PDP (2008)", "pronto", section="08 PDP Discrete Codes"),
)


@dataclass
class Entry:
    row: int                       # the sheet's row, or the page for a PDF
    page: int
    label: str
    code: str                      # ``50AF 21DE``
    where: str                     # what the citation says of the place: ``'No. 11'``, the RC key...
    pronto: str = ""
    column: str = ""


def tidy(text: str) -> str:
    for old, new in _MOJIBAKE:
        text = text.replace(old, new)
    text = " ".join(text.replace("\xa0", " ").split())
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text          # a spreadsheet's `1.0` is the key `1`


def cells_of(row: list[str]) -> list[tuple[int, str]]:
    return [(int(row[i]), tidy(row[i + 1])) for i in range(2, len(row), 2)]


def keys_xls(rows: list[list[str]]) -> tuple[list[Entry], int]:
    """``(the keys, the mode keys that have no code)`` of a sheet of rows ``'', No., label, code or MODE KEY, '', label, code``."""
    out: list[Entry] = []
    modes = 0
    for i, row in enumerate(rows, 1):
        if len(row) < 4 or not re.fullmatch(r"\d+(?:\.0)?", row[1].strip()):
            continue
        number = int(float(row[1]))
        label, code = tidy(row[2]), row[3].strip()
        if _CODE.match(code):
            out.append(Entry(i, 0, label, code, f"No. {number}"))
        elif code == "MODE KEY":
            modes += 1
        if len(row) >= 7 and _CODE.match(row[6].strip()):
            out.append(Entry(i, 0, tidy(row[5]), row[6].strip(), f"No. {number}, 'New for 2004 models'"))
    return out, modes


def label_code(rows: list[list[str]]) -> list[Entry]:
    out: list[Entry] = []
    for i, row in enumerate(rows, 1):
        cells = [c.strip() for c in row if c.strip()]
        if len(cells) >= 2 and _CODE.match(cells[-1]):
            out.append(Entry(i, 0, tidy(cells[-2]), cells[-1], ""))
    return out


def pdf_rows(rows: list[list[str]]) -> list[tuple[int, int, list[tuple[int, str]]]]:
    return [(int(r[0]), int(r[1]), cells_of(r)) for r in rows]


def function_hex(rows: list[list[str]]) -> list[Entry]:
    """The rows ``RC KEY / ITEM, FUNCTION, HEX`` of a PDF's cells: the function is the key's name and the first cell goes in the citation."""
    out: list[Entry] = []
    for page, y, cells in pdf_rows(rows):
        if len(cells) >= 3 and _CODE.match(cells[-1][1]):
            out.append(Entry(y, page, cells[-2][1], cells[-1][1], f"the {'item' if cells[0][1].isdigit() else 'RC key'} '{cells[0][1]}'" if len(cells) == 3 else ""))
    return out


def custom_key(rows: list[list[str]]) -> list[Entry]:
    out: list[Entry] = []
    for page, y, cells in pdf_rows(rows):
        if len(cells) == 4 and _WORD.match(cells[2][1]) and _WORD.match(cells[3][1]) and cells[0][1].isdigit():
            out.append(Entry(y, page, cells[1][1], f"{cells[2][1].upper()} {cells[3][1].upper()}", f"No. {cells[0][1]}"))
    return out


def pronto_items(rows: list[list[str]], section: str) -> list[Entry]:
    """The items of one titled section: ``Category Control, Function Name, Hex IR Code`` and the Pronto hex that goes on in the lines below."""
    out: list[Entry] = []
    current: Entry | None = None
    title = ""
    for page, y, cells in pdf_rows(rows):
        if len(cells) == 1 and cells[0][1].startswith("08 "):
            title, current = cells[0][1], None
            continue
        if title != section:
            continue
        right = [t for x, t in cells if x >= 340]
        left = [t for x, t in cells if x < 340]
        if len(left) == 3 and _CODE.match(left[2]):
            current = Entry(y, page, left[1], left[2], f"the category '{left[0]}'", " ".join(right))
            out.append(current)
        elif current is not None and not left and right:
            current.pronto += " " + " ".join(right)
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for table in TABLES:
        rows = snapshot.sheet_of(table.file, table.sheet)
        entry = snapshot.documents[table.file]
        sha = snapshot.sha(table.file)
        prefix = f"{HOST}/{table.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']})"
        if table.layout == "keys_xls":
            entries, modes = keys_xls(rows)
            report.count(MAKER, f"{table.model}: mode keys without a code", modes)
        elif table.layout == "label_code":
            entries = label_code(rows)
        elif table.layout == "function_hex":
            entries = function_hex(rows)
        elif table.layout == "custom_key":
            entries = custom_key(rows)
        else:
            entries = pronto_items(rows, table.section)
        by_code: dict[str, list[str]] = {}
        for e in entries:
            by_code.setdefault(e.code.upper(), []).append(e.label)
        members: list[tuple[str, dict[str, Any]]] = []
        for e in entries:
            report.count(MAKER, f"{table.model}: rows read")
            a, b, f, check = (int(x, 16) for x in _CODE.match(e.code).groups())
            triple = (a, b, f)
            if triple not in gate:
                gate[triple] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": a, "subdevice": b, "function": f,
                                                                           "confidence": TIER, "source": "probe"})
            if gate[triple]:
                report.unrepresented[(MAKER, table.file, f"does not compile: {gate[triple]}")] += 1
                continue
            place = f"sheet '{table.sheet}' row {e.row}" if table.layout in ("keys_xls", "label_code") else f"page {e.page} row"
            cite = (f"{prefix} {place} '{e.label}' ({e.code}" + (f", {e.where}" if e.where else "") + f"): NEC1 device {a} subdevice {b} function {f}, "
                    "the code's bytes in the order they are sent")
            doubts = []
            if len(by_code[e.code.upper()]) > 1:
                doubts.append("the table gives this code to " + " and ".join(f"'{n}'" for n in by_code[e.code.upper()]) + ", and a code is one function")
            if check != (~f & 0xFF):
                doubts.append(f"the last byte 0x{check:02X} is not the complement of the third, 0x{f:02X}")
            if e.pronto:
                frame = decode_frame(e.pronto)
                if isinstance(frame, Frame):
                    cite += f"; the Pronto hex (frequency word 0x{frame.carrier:04X}) is decoded and " + ("agrees" if frame.bytes == (a, b, f, check) else "does not agree")
                    if frame.bytes != (a, b, f, check):
                        doubts.append("the Pronto hex decodes to " + " ".join(f"{x:02X}" for x in frame.bytes))
                else:
                    doubts.append(f"the Pronto hex does not decode: {frame}")
            if doubts:
                cite += "; " + "; ".join(doubts)
            members.append((e.label, {"id": "primary.irp", "type": "irp", "device": a, "subdevice": b, "function": f,
                                      "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
        if not members:
            continue
        final = table.model
        while (BRAND.casefold(), final.casefold()) in authored:
            renamed = f"{final} (Hitachi table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        seen: dict[str, int] = {}
        keys: dict[str, dict[str, Any]] = {}
        for label, form in members:
            base = "KEY_" + (fold_label(label) or "COMMAND")
            seen[base] = seen.get(base, 0) + 1
            keys[base if seen[base] == 1 else f"{base}_{seen[base]}"] = {"label": label, "forms": [form]}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {table.model}"],
                     "protocol": dict(PROTOCOL), "keys": keys}))
    return out
