#!/usr/bin/env python3
"""Check that every icon of the canonical key vocabulary is a Material Symbols name.

``keys.json`` names an icon per key (``volume_up``, ``settings_input_hdmi``) and the
schema can say no more than that the name is snake_case. Whether the name exists is
a fact about Google's icon set, so it is checked here against the codepoints file
the set publishes, pinned to a commit so the answer does not move::

    python tools/vocabulary_icons.py                 # fetches the file
    python tools/vocabulary_icons.py --codepoints F  # or a copy of it

Not a unit test, because it needs the network. The same file is the list of every
name a Material Symbols font carries, one ``<name> <hex codepoint>`` per line; the
Outlined, Rounded and Sharp styles share their names. The exit status is 1 when a
name is missing.

Material Symbols are licensed under Apache-2.0 (github.com/google/material-design-icons).
This script reads names only and copies no icon.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.keys import load_vocabulary  # noqa: E402

REPO = "google/material-design-icons"
#: The commit the vocabulary was last checked against (2026-10-02, "Update Symbols").
COMMIT = "737e3324305806514d7909874fa1818ae1808232"
FILE = "variablefont/MaterialSymbolsOutlined[FILL,GRAD,opsz,wght].codepoints"


def fetch() -> str:
    url = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{urllib.parse.quote(FILE)}"
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - a pinned https URL
        return response.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--codepoints", help="a local copy of the codepoints file")
    args = parser.parse_args()

    text = Path(args.codepoints).read_text(encoding="utf-8") if args.codepoints else fetch()
    names = {line.split()[0] for line in text.splitlines() if line.strip()}
    used = sorted({k.icon for k in load_vocabulary().keys if k.icon})
    missing = [name for name in used if name not in names]
    print(f"{len(used)} icons used; {len(names)} names in {REPO}@{COMMIT[:7]}; "
          f"{len(missing)} missing")
    for name in missing:
        print(f"MISSING {name}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
