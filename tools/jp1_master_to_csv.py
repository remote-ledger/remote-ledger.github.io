#!/usr/bin/env python3
"""Convert ``jp1-master.xls`` of hifiremote/deviceupgrades to the CSV the importer reads (DESIGN D118).

The forum's master index lists every upgrade with its curated brand and category. The workbook is
BIFF, which the standard library cannot read, so this one development-time tool (it needs ``xlrd``,
which the ledger does not depend on) converts it once, and writes beside the CSV the commit it
was made from and the SHA-256 of the workbook, which ``rl import jp1`` checks.

    python3 tools/jp1_master_to_csv.py CHECKOUT [--out sources/jp1-device-upgrades]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import xlrd

COLUMNS = ["Category", "Brand", "File Description (from web)", "File Name", "Type", "PID", "Protocol"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("checkout", help="a git checkout of github.com/hifiremote/deviceupgrades")
    parser.add_argument("--out", default="sources/jp1-device-upgrades")
    args = parser.parse_args(argv)
    checkout = Path(args.checkout)
    xls = checkout / "jp1-master.xls"
    commit = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    sheet = xlrd.open_workbook(str(xls)).sheet_by_name("JP1 Data")
    header = [str(c.value).strip() for c in sheet.row(0)]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        print(f"the workbook has no column {missing}", file=sys.stderr)
        return 1
    index = {name: header.index(name) for name in COLUMNS}
    rows = []
    for r in range(1, sheet.nrows):
        cells = sheet.row(r)
        rows.append([" ".join(str(cells[index[c]].value).split()) for c in COLUMNS])
    rows.sort()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "jp1-master.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    meta = {"upstream": "github.com/hifiremote/deviceupgrades", "commit": commit,
            "xlsSha256": hashlib.sha256(xls.read_bytes()).hexdigest(), "rows": len(rows),
            "tool": "tools/jp1_master_to_csv.py"}
    (out / "MASTER.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"{len(rows)} rows from {commit[:7]} in {out}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
