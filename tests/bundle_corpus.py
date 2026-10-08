"""A small ledger with all four sources in it, for the tests of the bundle (D88 to D94).

The IR Blaster part is written by the importer itself (``tests/app_corpus.py``), so
its files have the shape ``rl import irblaster`` gives them; the others are the
smallest files that exercise what the bundle does with each source. The README and
the licence text of every import directory are copied from the real repository, so
the notices are built from, and checked against, the very text the repository holds.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app_corpus import make_import

ROOT = Path(__file__).resolve().parent.parent

RAW = {"id": "primary.raw", "type": "raw", "confidence": "plausible"}


def raw_key(intro: list[int], repeat: list[int], label: str | None = None,
            source: str = "test capture") -> dict:
    key = {"forms": [{**RAW, "source": source, **({"intro": intro} if intro else {}),
                      "repeat": repeat}]}
    if label is not None:
        key["label"] = label
    return key


#: Raw captures of NEC-like frames with and without an intro, as an import writes them.
FRAME = [9000, 4500, 560, 560, 560, 1690, 560, 560, 560, 1690, 560, 40000]
FRAME2 = [9000, 4500, 560, 1690, 560, 560, 560, 1690, 560, 1690, 560, 40000]
REPEAT = [9000, 2250, 560, 96000]


LIRC = {
    # a LIRC remote with an intro, a repeat, free-text controls and an alias, whose
    # keys are LIRC names (no label) and one that the vocabulary does not know
    "remotes/lirc/acme/TV-1.json": {
        "manufacturer": "acme", "model": "TV-1", "aliases": ["TV1 old"],
        "controls": ["ACME Trinitron 25", "Acme Home Cinema"],
        "protocol": {"carrierHz": 38000, "minSends": 2},
        "keys": {
            "KEY_POWER": raw_key(FRAME, REPEAT),
            "KEY_VOLUMEUP": raw_key(FRAME2, REPEAT),
            "KEY_AGAIN": raw_key(FRAME, REPEAT),
            "KEY_MUTE": raw_key(FRAME2, REPEAT),
        },
    },
    # a LIRC remote with no intro: the repeat sequence alone is the frame
    "remotes/lirc/zed/Z-9.json": {
        "manufacturer": "ZED", "model": "Z-9", "aliases": [],
        "protocol": {"carrierHz": 36000, "minSends": 3},
        "keys": {"KEY_1": raw_key([], FRAME), "KEY_2": raw_key([], FRAME2)},
    },
}
SMARTIR = {
    "remotes/smartir/Acme/media_player_7.json": {
        "manufacturer": "Acme", "model": "SmartIR media_player 7",
        "aliases": [], "controls": ["Acme Stream 1", "Acme Stream 2"],
        "protocol": {"carrierHz": 38000, "minSends": 1},
        "keys": {"KEY_POWER": raw_key([], FRAME), "KEY_OFF": raw_key([], FRAME2)},
    },
}


def make_corpus(root: Path, irblaster: dict[int, dict] | None = None) -> Path:
    """Write the corpus under ``root`` and return it. ``irblaster`` replaces the database the
    IR Blaster part is imported from (``tests/app_corpus.py``'s ``REMOTES`` where it is None)."""
    make_import(root) if irblaster is None else make_import(root, irblaster)
    for source, licence in (("lirc", "COPYING"), ("smartir", "LICENSE"), ("irblaster", "LICENSE")):
        directory = root / "remotes" / source
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("README.md", licence):
            shutil.copy(ROOT / "remotes" / source / name, directory / name)
    for source, commit in (("lirc", "291b40f436a4a17cc72b24c8eb411f6213ee88e5"),
                           ("smartir", "e4df2957ad915536f41ffb39daa96886d7cfe040")):
        (root / "remotes" / source / "IMPORT.md").write_text(
            f"# {source} import report\n\nUpstream: `example.invalid/{source}` @ `{commit}`.\n",
            encoding="utf-8")
    for where, doc in {**LIRC, **SMARTIR}.items():
        path = root / where
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    authored = root / "remotes" / "topping" / "RC-15A.json"
    authored.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "tests" / "fixtures" / "topping-rc15a.json", authored)
    return root
