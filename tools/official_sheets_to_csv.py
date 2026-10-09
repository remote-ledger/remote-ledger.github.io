#!/usr/bin/env python3
"""Convert the spreadsheets of ``sources/official/<maker>/`` to CSV, one file per sheet read (DESIGN D123).

The spreadsheets are BIFF (``.xls``) and Office Open XML (``.xlsx``), which the standard library cannot
read, so this one development-time tool (it needs ``xlrd`` and ``openpyxl``, which the ledger does not
depend on) converts the sheets the importer reads, every cell as text, and records each CSV's SHA-256 and
the document and sheet it came from in the manifest. ``rl import official`` reads the CSVs and refuses one
that is not what the manifest says.

    python3 tools/official_sheets_to_csv.py marantz|anthem
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
        "marantz-master-ir.xls": ["AVR Commands"],
        "marantz-2014-ir-command-sheet.xls": ["AVR Commands"],
        "marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls": ["AVR Commands"],
    },
    "oppo": {
        "BDP-103_BDP-103D_Remote_Code_v1.2.xls": ["Remote Code 1", "Remote Code 2", "Remote Code 3", "Notes"],
        "BDP-103_BDP-105_Remote_Code_v1.1.xls": ["Remote Code 1", "Remote Code 2", "Remote Code 3", "Notes"],
        "UDP-203_Remote_Code_v1.2.xls": ["Remote Code 1", "Remote Code 2", "Remote Code 3", "Notes"],
    },
    "anthem": {"AVM-MRXx40-IR-hex-20251202185749500.xlsx": ["MRX x10-x40 remote layout", "MRX x10-x40 IR hex codes"]},
}


def text(value) -> str:
    """A cell as text: an integral number without ``.0``, anything else as written, trimmed."""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def rows_of(path: Path, sheet: str) -> list[list[str]]:
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
