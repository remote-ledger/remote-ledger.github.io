#!/usr/bin/env python3
"""Fetch hifi-remote.com's Sony code pages into a snapshot directory (DESIGN D111).

The site is not a git repository, so the pin of ``rl import hifi-remote`` is a
snapshot: every page as the server sent it, and a ``MANIFEST.json`` naming the
date, each page's SHA-256, its size and the headers that date it. The importer
reads only that directory, so a re-run reproduces every imported file byte for
byte, whatever the live site does later.

    python3 tools/fetch_hifi_remote.py [DIR] [--date YYYY-MM-DD]

DIR is ``sources/hifi-remote`` by default. A page is fetched once a second, with
a User-Agent that says what is asking. Running it again overwrites the pages and
the manifest: review the diff, then run ``rl import hifi-remote``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

BASE = "https://www.hifi-remote.com/sony/"
INDEX = "index.htm"
USER_AGENT = "remote-ledger snapshot (+https://github.com/remote-ledger)"
PAUSE_SECONDS = 1.0
_LINK = re.compile(rb'href="(Sony_[A-Za-z0-9_]+\.htm)(?:#[^"]*)?"')


def fetch(url: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        return response.read(), headers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", nargs="?", default="sources/hifi-remote")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="the retrieval date to record (default: today)")
    args = parser.parse_args(argv)
    target = Path(args.directory)
    target.mkdir(parents=True, exist_ok=True)

    index, index_headers = fetch(BASE)
    names = sorted(set(m.decode() for m in _LINK.findall(index)))
    pages: dict[str, dict[str, object]] = {}

    def keep(name: str, data: bytes, headers: dict[str, str]) -> None:
        (target / name).write_bytes(data)
        entry: dict[str, object] = {
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        for header, field in (("last-modified", "lastModified"), ("etag", "etag")):
            if header in headers:
                entry[field] = headers[header]
        pages[name] = entry

    keep(INDEX, index, index_headers)
    for name in names:
        time.sleep(PAUSE_SECONDS)
        data, headers = fetch(BASE + name)
        keep(name, data, headers)
        print(f"{name}: {len(data):,} bytes", file=sys.stderr)
    manifest = {"source": BASE, "retrieved": args.date,
                "tool": "tools/fetch_hifi_remote.py", "pages": dict(sorted(pages.items()))}
    (target / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(pages)} pages in {target}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
