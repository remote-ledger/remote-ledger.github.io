#!/usr/bin/env python3
"""Convert the spreadsheets of ``sources/official/<maker>/`` to CSV, one file per sheet read (DESIGN D123).

The spreadsheets are BIFF (``.xls``) and Office Open XML (``.xlsx``), which the standard library cannot
read, so this one development-time tool (it needs ``xlrd`` and ``openpyxl``, which the ledger does not
depend on) converts the sheets the importer reads, every cell as text, and records each CSV's SHA-256 and
the document and sheet it came from in the manifest. ``rl import official`` reads the CSVs and refuses one
that is not what the manifest says. **A PDF** (KEF's, DESIGN D243) is read as the text of a page, ``page N``, or of
every page, ``pages`` (it needs PyMuPDF): one non-empty line to a row, in the order the library reads the page (``pages``
puts the page number in a first cell), which the importer parses; or as its tables, ``tables`` (Arcam's, D251): a row is
the page, the table's number on it and the cells of one table row, so a table's merged cells are the library's reading.

    python3 tools/official_sheets_to_csv.py marantz|anthem|oppo|kef|jvc|arcam|cambridge|hegel|simaudio|rotel
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

#: maker -> {document: [sheet names read]}
SHEETS = {
    "marantz": {
        "marantz-master-ir.xls": ["AVR Commands", "Direct Vol.", "Zone Speaker Direct Vol.", "AM FM Preset Direct",
                                  "XM SIRIUS Preset Direct", "XM SIRIUS Ch. Direct"],
        "marantz-2014-ir-command-sheet.xls": ["AVR Commands", "Direct Vol."],
        "marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls": ["AVR Commands", "Direct Vol."],
    },
    "oppo": {
        "BDP-103_BDP-103D_Remote_Code_v1.2.xls": ["Remote Code 1", "Remote Code 2", "Remote Code 3", "Notes"],
        "BDP-103_BDP-105_Remote_Code_v1.1.xls": ["Remote Code 1", "Remote Code 2", "Remote Code 3", "Notes"],
        "UDP-203_Remote_Code_v1.2.xls": ["Remote Code 1", "Remote Code 2", "Remote Code 3", "Notes"],
    },
    "anthem": {"AVM-MRXx40-IR-hex-20251202185749500.xlsx": ["MRX x10-x40 remote layout", "MRX x10-x40 IR hex codes"]},
    "jvc": {"DILA_Remote_Code_Emulation_Long_Hex.pdf": ["pages"]},
    "arcam": {
        "AVR380-450-750_RS232_SH256E_2.pdf": ["tables"],
        "AVR5-10-20-30-40-11-21-31-41_RS232_SH289E_F.pdf": ["tables"],
        "SA10-SA20_RS232_SH277E_B.pdf": ["tables"],
        "SA30_RS232_SH306E_4.pdf": ["tables"],
        "CDS50_RS232_SH275E_C.pdf": ["tables"],
        "UDP411_RS232_SH265_E_1.pdf": ["tables"],
        "Solo_IR_Remote_Codes.pdf": ["tables"],
        "Solo_Mini_RS232.pdf": ["pages"],
    },
    "cambridge": {"AXR85_100_IR_Codes.pdf": ["tables"], "752BD-CXU-CXUHD_remotes_Sept_2024_v2.pdf": ["tables"]},
    "hegel": {name: ["flow"] for name in (
        "Hegel_Rost_IR_Codes.pdf", "Hegel_H190_IR_Codes.pdf", "Hegel_H360_IR_Codes.pdf", "Hegel_H590_IR_Codes.pdf", "Hegel_H90_IR_Codes.pdf",
        "Hegel_HD12_IR_Codes.pdf", "Hegel_HD25_IR_Codes.pdf", "Hegel_HD30_IR_Codes.pdf", "Hegel_Mohican_IR_Codes.pdf",
        "Hegel_H390_H590_IR_codes_v1.pdf", "Hegel_H95_H120_H190_IR_codes_v2.pdf", "Hegel_P20_P30_IR_codes_v2.pdf")},
    "simaudio": {"Simaudio-RC-5-IR-codes-rev1.1.pdf": ["pages"]},
    "rotel": {"Rotel_IR_Codes.html": ["tables"], **{name: ["pages"] for name in (
        "P5-X5_HEX.PDF",
        "P5-P5S2-X5-X5S2_HEX.pdf",
        "Q5_HEX.pdf",
        "X3_HEX.PDF",
        "X3-X3S2_HEX.pdf",
        "X430_HEX.pdf",
        "P5-X5-X5S2_HEX.pdf",
        "CD11_HEX.pdf",
        "CD11-CD11MKII_HEX.pdf",
        "CD14_HEX.pdf",
        "CD14andCD14MKII_HEX.pdf",
        "RCD06SE_HEX.pdf",
        "RCD12_HEX.pdf",
        "RCD1520_HEX.pdf",
        "RCD1570_HEX_0.pdf",
        "RCD1572_HEX.pdf",
        "RCD1572-RCD1572MKII_HEX.pdf",
        "DX3-DX5_HEX.pdf",
        "A10MKII_HEX.pdf",
        "A11_HEX.pdf",
        "A11-A11MKII_HEX.pdf",
        "A12-A14_HEX.pdf",
        "A12-A14-A12MKII-A14MKII_HEX.pdf",
        "A10MKIIandA8_HEX.pdf",
        "RA05SE_HEX.pdf",
        "RA12_HEX.pdf",
        "RA1520_HEX.pdf",
        "RA1570_HEX_0.pdf",
        "RA1572_HEX.pdf",
        "RA1572-RA1572MKII_HEX.pdf",
        "RA1592-RA1592MKII_HEX.pdf",
        "S14_RAS-5000_HEX.pdf",
        "RC1550_HEX.pdf",
        "RC1570_HEX.pdf",
        "RC1572_HEX.pdf",
        "RC1572MKII_HEX.pdf",
        "RC1580_HEX.pdf",
        "RC1590_HEX.pdf",
        "RC1590-RC1590MKII_HEX.pdf",
        "RDD1580_HEX.pdf",
        "RCX1500_HEX.pdf",
        "RAP1580_HEX.pdf",
        "RSP1570_HEX.pdf",
        "RSP1572_HEX.pdf",
        "RSP1576_HEX.pdf",
        "RSP1582_HEX.pdf",
        "RSX1560_HEX.pdf",
        "RSX1562_HEX.pdf",
        "RDG1520_HEX.pdf",
        "RT11_HEX.pdf",
        "RT12_HEX.pdf",
        "T14_HEX.pdf",
    )}},
    "kef": {
        "LS50Wireless-IRcode-INT.pdf": ["page 1", "page 2"],
        "LS50WII_IR-Codes-for-Universal-Remote-Control.pdf": ["page 1"],
        "LS60W_IR_Codes_for_Universal_Remote_Control.pdf": ["page 1"],
        "LSX-IRcode-INT.pdf": ["page 1"],
        "LSX-II_IR-Codes-for-Universal-Remote-Control.pdf": ["page 1"],
        "XIO_IR-Command-Codes-for-Universal-Remote-Control.pdf": ["page 1"],
    },
}


def text(value) -> str:
    """A cell as text: an integral number without ``.0``, anything else as written, trimmed."""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


class _Table(HTMLParser):
    """The first ``<table>`` of an HTML page (``every``: all of them, each row led by its table's number) as rows of cell text:
    ``<br>`` is `` / ``, ``<sup>x</sup>`` is ``^x``, runs of space one, and with ``links`` an anchor's address follows its text in
    braces."""

    def __init__(self, every: bool = False, links: bool = False) -> None:
        super().__init__()
        self.every, self.links = every, links
        self.rows: list[list[str]] = []
        self.cell: list[str] | None = None
        self.href = ""
        self.depth = 0
        self.tables = 0
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table":
            if self.depth == 0:
                self.tables += 1
            self.depth += 1
        elif self.depth and tag == "tr":
            self.rows.append([str(self.tables)] if self.every else [])
        elif self.depth and tag in ("td", "th"):
            self.cell = []
        elif self.cell is not None and tag == "br":
            self.cell.append(" / ")
        elif self.cell is not None and tag == "sup":
            self.cell.append("^")
        elif self.cell is not None and tag == "a" and self.links:
            self.href = dict(attrs).get("href") or ""

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag == "a" and self.cell is not None and self.href:
            self.cell.append(" {" + self.href + "}")
            self.href = ""
        elif tag in ("td", "th") and self.cell is not None:
            self.rows[-1].append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            self.done = self.depth == 0 and not self.every

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def rows_of(path: Path, sheet: str) -> list[list[str]]:
    if path.suffix.lower() == ".html":
        parser = _Table(every=sheet == "tables", links=sheet == "tables")
        parser.feed(path.read_text(encoding="utf-8"))
        return [[re.sub(r"\s*/\s*$", "", cell).strip() for cell in row] for row in parser.rows]
    if path.suffix.lower() == ".pdf":
        import pymupdf
        pdf = pymupdf.open(str(path))
        if sheet == "tables":       # every table the library finds on every page: page, table number and the cells as text
            return [[str(n), str(i)] + [" ".join((cell or "").split()) for cell in row]
                    for n, page in enumerate(pdf, 1) for i, table in enumerate(page.find_tables().tables, 1)
                    for row in table.extract()]
        if sheet == "flow":         # every page in reading order (D259): a line of text is ``page, text, y, line``, a table row
            out = []                # ``page, row, table, y, cells`` (a cell a merged cell covers is ``<merged>``), sorted by ``y``;
            for n, page in enumerate(pdf, 1):          # a cell much taller than a row is ``<block>`` and its lines are lines of text
                tables = page.find_tables().tables
                items, blocks = [], []
                for i, table in enumerate(tables, 1):
                    for row, cells in zip(table.rows, table.extract()):
                        shown = []
                        for box, cell in zip(row.cells, cells):
                            if box is not None and box[3] - box[1] > 40:
                                blocks.append(box)
                                shown.append("<block>")
                            else:
                                shown.append("<merged>" if cell is None else " ".join(cell.split()))
                        items.append((row.bbox[1], 1, [str(n), "row", str(i), str(round(row.bbox[1]))] + shown))
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        text = " ".join("".join(span["text"] for span in line["spans"]).split())
                        x0, y0 = line["bbox"][0], line["bbox"][1]
                        inside = any(t.bbox[0] - 2 <= x0 <= t.bbox[2] + 2 and t.bbox[1] - 2 <= y0 <= t.bbox[3] + 2 for t in tables)
                        in_block = any(b[0] - 2 <= x0 <= b[2] + 2 and b[1] - 2 <= y0 <= b[3] + 2 for b in blocks)
                        if text and (not inside or in_block):
                            items.append((y0, 0, [str(n), "text", str(round(y0)), text]))
                out.extend(row for _, _, row in sorted(items, key=lambda item: (item[0], item[1])))
            return out
        if sheet == "pages":        # every page: a row is the page number and one line of its text
            return [[str(n), line.strip()] for n, page in enumerate(pdf, 1) for line in page.get_text("text").splitlines()
                    if line.strip()]
        return [[line.strip()] for line in pdf[int(sheet.removeprefix("page ")) - 1].get_text("text").splitlines() if line.strip()]
    if path.suffix.lower() == ".xls":
        import xlrd
        ws = xlrd.open_workbook(str(path)).sheet_by_name(sheet)
        return [[text(c.value) for c in ws.row(r)] for r in range(ws.nrows)]
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True)[sheet]
    return [[text(c) for c in row] for row in ws.iter_rows(values_only=True)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("maker", choices=sorted(SHEETS))
    parser.add_argument("--root", default="sources/official")
    args = parser.parse_args(argv)
    target = Path(args.root) / args.maker
    manifest_path = target / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sheets"] = {}
    (target / "csv").mkdir(exist_ok=True)
    for document, sheets in SHEETS[args.maker].items():
        for sheet in sheets:
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{Path(document).stem}__{sheet}") + ".csv"
            rows = rows_of(target / document, sheet)
            while rows and not any(rows[-1]):
                rows.pop()
            out = target / "csv" / name
            with out.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle, lineterminator="\n").writerows(rows)
            manifest["sheets"][name] = {"document": document, "sheet": sheet,
                                        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "rows": len(rows)}
            print(f"{name}: {len(rows)} rows", file=sys.stderr)
    manifest["sheets"] = dict(sorted(manifest["sheets"].items()))
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
