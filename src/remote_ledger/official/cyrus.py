"""Cyrus Audio's RC-5 code tables (DESIGN section 85).

Cyrus (the English maker of the Cyrus amplifiers, CD players, tuners and the DVD 7) published for each product a one-page PDF, ``AMPLIFIER REMOTE CODE
TABLE`` or ``CD PLAYER REMOTE CODE TABLE``, that is a table of the RC-5 codes 0 to 63 (and 64 to 127 beside them, where the table has two) with two
columns of names: **the name Philips' RC-5 standard gives the code for that class of device** (``RC5 Amplifier 1 (16)``: the system code 16) and **the name
the Cyrus product gives it** (``Cyrus 3i``), blank where the product has no key for it. The tables are the Wayback Machine's copies of Cyrus' downloads, read
from the CSV of each page's cells by place (``cells``, D283) and pinned by SHA-256 (D123).

**A key is the ledger's ``RC5``**: the system code of the heading the device, the row's code the function (RC-5 has 7 command bits: 64 to 127 are the codes of the
second table), **and the label the product's name for it**, where the standard's name for the code (``Volume +``) is in the citation. A product with no name for a
code has no key for it. **The columns are told apart by place**: a name left of the middle of the two headings is the standard's, right of it the product's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "cyrus"
BRAND = "Cyrus"
PROTOCOL = {"name": "RC5", "carrierHz": 36000, "minSends": 1}
HOST = "cyrusaudio.com remote code tables"


@dataclass(frozen=True)
class Document:
    file: str
    system: int                        # the heading's RC-5 system code
    models: tuple[str, ...]            # a file for each
    split: int                         # the x between the standard's names and the product's, in the first table
    right: tuple[int, int] | None = None   # (x of the second table's codes, x between its two columns), where there is one
    note: str = ""                     # for the citation


DOCUMENTS: tuple[Document, ...] = (
    Document("ACA75PreXremotecodes.pdf", 16, ("aCA7.5", "PreX"), 162),
    Document("AV5_AV8remotecodes.pdf", 19, ("AV5", "AV8"), 153, (320, 408), note="a name marked * is for software version 1.2 and later, ** AV8 only"),
    Document("AVMasterremotecodes.pdf", 19, ("AV Master",), 154),
    Document("CDremotecodes.pdf", 20, ("CD player",), 188),
    Document("Cyrus3iremotecodes.pdf", 16, ("3i",), 181),
    Document("Cyrus3remotecodes.pdf", 16, ("3",), 162, (325, 424)),
    Document("Cyrus5_6_7_8remotecodes.pdf", 16, ("5", "6", "7", "8"), 164,
             note="the document names the Cyrus 5 and Cyrus 7 and its address says 5&6&7&8"),
    Document("CyrusPreremotecodes.pdf", 16, ("Pre",), 189),
    Document("DVD7remotecodes.pdf", 25, ("DVD 7",), 158, (324, 417)),
    Document("FM7FM7.5andFMXremotecodes.pdf", 16, ("FM7", "FM7.5", "FMX"), 165, note="the document names the Cyrus FM7 and FM7.5 and its address says FM7FM7.5andFMX"),
    Document("Quattroremotecodes.pdf", 21, ("Quattro",), 160),
    Document("dad1remotecodes.pdf", 20, ("dAD1",), 180),
    Document("originaltunerremotecodes.pdf", 17, ("original tuner",), 168, note="the document's heading is `Mission Cyrus tuner`"),
)


@dataclass
class Row:
    page: int
    y: int
    code: int
    name: str                          # the product's
    standard: str                      # the RC-5 standard's name for the code ("" where the cell is blank)


def tidy(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def tables(document: Document) -> list[tuple[int, int, int]]:
    """``(the x of a table's codes less a margin, the x between its columns, the x its cells end at)`` of each table of the page."""
    if not document.right:
        return [(60, document.split, 10_000)]
    return [(60, document.split, document.right[0] - 15), (document.right[0] - 15, document.right[1], 10_000)]


def parse(rows: list[list[str]], document: Document) -> list[Row]:
    """The keys of a table's cells (``page, y, x, text, x, text...``): a row has a code at the left of a table, then the standard's name left of the middle of the
    two headings and the product's right of it; a blank product cell is no key."""
    out: list[Row] = []
    for row in rows:
        page, y = int(row[0]), int(row[1])
        cells = [(int(row[i]), tidy(row[i + 1])) for i in range(2, len(row), 2)]
        for lo, split, end in tables(document):
            inside = [(x, t) for x, t in cells if lo <= x < end]
            if not inside or not re.fullmatch(r"\d{1,3}", inside[0][1]) or inside[0][0] > lo + 40:
                continue
            names = [t for x, t in inside[1:] if x >= split]
            if names:
                out.append(Row(page, y, int(inside[0][1]), " ".join(names), " ".join(t for x, t in inside[1:] if x < split)))
    return out


def key_names(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for document in DOCUMENTS:
        entry = snapshot.documents[document.file]
        sha = snapshot.sha(document.file)
        prefix = f"{HOST}/{document.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']})"
        rows = parse(snapshot.sheet_of(document.file, "cells"), document)
        by_code: dict[int, list[str]] = {}
        for r in rows:
            by_code.setdefault(r.code, []).append(r.name)
        members: list[tuple[str, dict[str, Any]]] = []
        for r in rows:
            report.count(MAKER, f"{document.file}: rows read")
            key = (document.system, r.code)
            if key not in gate:
                gate[key] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": document.system, "function": r.code,
                                                                     "confidence": TIER, "source": "probe"})
            if gate[key]:
                report.unrepresented[(MAKER, document.file, f"does not compile: {gate[key]}")] += 1
                continue
            cite = (f"{prefix} page {r.page} row '{r.name}' (RC-5 system code {document.system}, code {r.code}"
                    + (f", the standard's name for it '{r.standard}'" if r.standard else "") + f"): RC5 device {document.system} function {r.code}"
                    + (f"; {document.note}" if document.note else ""))
            doubts = []
            if len(by_code[r.code]) > 1:
                doubts.append("the table gives this code to " + " and ".join(f"'{n}'" for n in by_code[r.code]) + ", and a code is one function")
            if doubts:
                cite += "; " + "; ".join(doubts)
            members.append((r.name.rstrip("*").strip() or r.name, {"id": "primary.irp", "type": "irp", "device": document.system, "function": r.code,
                                                                  "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
        if not members:
            continue
        for model in document.models:
            final = model
            while (BRAND.casefold(), final.casefold()) in authored:
                renamed = f"{final} (Cyrus table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                        {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(PROTOCOL), "keys": keys}))
    return out
