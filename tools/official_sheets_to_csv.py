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

    python3 tools/official_sheets_to_csv.py marantz|anthem|oppo|kef|jvc|arcam|cambridge
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
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


def rows_of(path: Path, sheet: str) -> list[list[str]]:
    if path.suffix.lower() == ".pdf":
        import pymupdf
        pdf = pymupdf.open(str(path))
        if sheet == "tables":       # every table the library finds on every page: page, table number and the cells as text
            return [[str(n), str(i)] + [" ".join((cell or "").split()) for cell in row]
                    for n, page in enumerate(pdf, 1) for i, table in enumerate(page.find_tables().tables, 1)
                    for row in table.extract()]
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
