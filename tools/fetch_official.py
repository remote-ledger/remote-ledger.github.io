#!/usr/bin/env python3
"""Fetch the IR documents a manufacturer publishes into ``sources/official/<maker>/`` (DESIGN D123).

The documents are what ``rl import official`` is pinned to: a manufacturer's page is not a
repository, so the pin is the file as the server sent it, and ``MANIFEST.json`` records its URL,
size, SHA-256, the retrieval date (today: do not pass --date) and the server's ``Last-Modified``
(and, for a copy from the Wayback Machine, the address and snapshot it was archived from). Run it, then
``tools/official_sheets_to_csv.py <maker>`` for the spreadsheets, review the diff, and import.

    python3 tools/fetch_official.py marantz|anthem|oppo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

WAYBACK = re.compile(r"^https://web\.archive\.org/web/(?P<snapshot>\d{14})id_/(?P<original>https?://.+)$")
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
    # Oppo's own host no longer resolves (the company stopped in 2018 and 2019): these are the Wayback Machine's
    # copies, each the snapshot named in its URL (``id_``, the raw bytes: the plain form answers a script with a
    # page of its own), which the manifest records with the original address.
    "oppo": [
        ("BDP-103_BDP-103D_Remote_Code_v1.2.xls",
         "https://web.archive.org/web/20240105021353id_/http://download.oppodigital.com/BDP103/BDP-103_BDP-103D_Remote_Code_v1.2.xls", None),
        ("BDP-103_BDP-105_Remote_Code_v1.1.xls",
         "https://web.archive.org/web/20260308143620id_/http://download.oppodigital.com/BDP103/BDP-103_BDP-105_Remote_Code_v1.1.xls", None),
        ("UDP-203_Remote_Code_v1.2.xls",
         "https://web.archive.org/web/20251211233638id_/http://download.oppodigital.com/UDP203/UDP-203_Remote_Code_v1.2.xls", None),
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
        if name.endswith(".xls") and not data.startswith(b"\xd0\xcf\x11\xe0"):
            raise SystemExit(f"{name}: {url} did not answer with a spreadsheet ({data[:40]!r}); nothing was pinned")
        (target / name).write_bytes(data)
        entry = {"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        archived = WAYBACK.match(url)
        if archived:
            # a copy from the Wayback Machine: the address it was archived from and when, and what the origin said
            entry["original"], entry["snapshot"] = archived["original"], archived["snapshot"]
        if "last-modified" in headers:
            entry["lastModified"] = headers["last-modified"]
        elif "x-archive-orig-last-modified" in headers:
            entry["lastModified"] = headers["x-archive-orig-last-modified"]
        manifest["documents"][name] = entry
        print(f"{name}: {len(data):,} bytes", file=sys.stderr)
    manifest["documents"] = dict(sorted(manifest["documents"].items()))
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
