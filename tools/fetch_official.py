#!/usr/bin/env python3
"""Fetch the IR documents a manufacturer publishes into ``sources/official/<maker>/`` (DESIGN D123).

The documents are what ``rl import official`` is pinned to: a manufacturer's page is not a
repository, so the pin is the file as the server sent it, and ``MANIFEST.json`` records its URL,
size, SHA-256, the retrieval date and the server's ``Last-Modified``. Run it, then
``tools/official_sheets_to_csv.py <maker>`` for the spreadsheets, review the diff, and import.

    python3 tools/fetch_official.py marantz|anthem [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

USER_AGENT = "remote-ledger snapshot (+https://github.com/remote-ledger)"
MARANTZ = ("https://www.marantz.com/on/demandware.static/-/Library-Sites-marantz_europe_shared/default/"
           "dw78eade36/archive-downloads/")
#: maker -> [(file name to keep, url, member of the zip to keep or None)]
DOCUMENTS = {
    "marantz": [
        ("marantz-master-ir.xls", MARANTZ + "marantz-master-ir.xls", None),
        ("marantz-2014-ir-command-sheet.xls", MARANTZ + "marantz-2014-ir-command-sheet.xls", None),
        ("marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls",
         MARANTZ + "marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls", None),
    ],
    "anthem": [
        ("AVM-MRXx40-IR-hex-20251202185749500.xlsx",
         "https://storage.googleapis.com/sandbox1-anthemav/an/AVM-MRXx40-IR-hex-20251202185749500.xlsx", None),
    ],
}


def fetch(url: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(urllib.parse.quote(url, safe=":/%?=&"), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read(), {k.lower(): v for k, v in response.headers.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("maker", choices=sorted(DOCUMENTS))
    parser.add_argument("--date", default=date.today().isoformat(), help="the retrieval date to record")
    parser.add_argument("--root", default="sources/official")
    args = parser.parse_args(argv)
    target = Path(args.root) / args.maker
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    manifest.update({"maker": args.maker, "retrieved": args.date})
    manifest.setdefault("documents", {})
    manifest.setdefault("sheets", {})
    for name, url, member in DOCUMENTS[args.maker]:
        time.sleep(1.0)
        data, headers = fetch(url)
        if member is not None:
            data = zipfile.ZipFile(__import__("io").BytesIO(data)).read(member)
        (target / name).write_bytes(data)
        entry = {"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if "last-modified" in headers:
            entry["lastModified"] = headers["last-modified"]
        manifest["documents"][name] = entry
        print(f"{name}: {len(data):,} bytes", file=sys.stderr)
    manifest["documents"] = dict(sorted(manifest["documents"].items()))
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
