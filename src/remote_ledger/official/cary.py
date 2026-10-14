"""Cary Audio's DMS IR remote codes (DESIGN section 71).

Cary Audio's ``DMS IR Remote Codes`` is one page (Word to PDF, 2 November 2018): "All codes - to Philips RC-5 standard", a table of
38 keys, each a ``Key Printing`` (what is printed on the remote's key), a ``Function``, **two system codes, ``A`` (0x10) and ``B``
(0x09)**, a ``Key Command`` and a ``Notes`` column that is empty. The page does not say which products it is for beyond ``DMS``, and
does not say why a key has two system codes.

A key is the ledger's ``RC5`` (the system code the device, the command the function). **One file for each system code**, ``DMS`` for the
column ``A`` and ``DMS (system code B)`` for ``B``, since a unit answers to one of them as Cambridge's ``24 or 27`` units do (that the
DMS does is not said; the table lists both for every key). The label is the key printing as written, except a printing that is a sign
beside a word (``-  VOLUME``, ``VOLUME  +``), which is the function's words (``VOLUME DOWN``, ``VOLUME UP``); the function, which the
table writes as it likes (``NAVIGATION LIFT``, ``NETWORD INPUT``), is in the citation as written.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "cary"
BRAND = "Cary Audio"
FILE = "DMS-IR-Remote-Codes.pdf"
MODEL = "DMS"
PROTOCOL = {"name": "RC5", "carrierHz": 36000, "minSends": 1}
HEADER = ["Key Printing", "Function", "System Code A", "System Code B", "Key Command", "Notes"]
_SIGNED = re.compile(r"^(?:[-+]\s*[A-Za-z]+|[A-Za-z]+\s*[-+])$")


def parse(rows: list[list[str]]) -> list[tuple[int, str, str, int, int, int, str]]:
    """``(its row, key printing, function, system code A, system code B, command, notes)`` of each row under the header: a row is
    ``page, table number, cells``. A header that is not the six columns, or a cell that is not a hex byte, is an error: the page is
    one table and a change of it is a change to read."""
    header = next((i for i, row in enumerate(rows) if row[2:] == HEADER), None)
    if header is None:
        raise ValueError(f"no row of the six columns {HEADER}")
    out = []
    for position, row in enumerate(rows[header + 1:], header + 2):
        cells = [" ".join(c.split()) for c in row[2:]]
        if len(cells) != 6:
            raise ValueError(f"row {position}: {len(cells)} cells, not 6")
        printing, function, a, b, command, notes = cells
        try:
            values = [int(x, 16) for x in (a, b, command)]
        except ValueError as exc:
            raise ValueError(f"row {position}: a system code or a command that is not hex: {cells}") from exc
        out.append((position, printing, function, *values, notes))
    return out


def label_of(printing: str, function: str) -> str:
    """The key printing, or the function where the printing is a sign beside a word (``-  VOLUME``)."""
    return function if _SIGNED.match(printing) else printing


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    rows = parse(snapshot.sheet_of(FILE, "tables"))
    sha = snapshot.sha(FILE)
    report.count(MAKER, "rows read", len(rows))
    gate: dict[tuple[int, int], str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for column, (letter, model) in enumerate((("A", MODEL), ("B", f"{MODEL} (system code B)"))):
        built: list[tuple[str, dict[str, Any]]] = []
        for position, printing, function, a, b, command, notes in rows:
            system = (a, b)[column]
            if (system, command) not in gate:
                gate[(system, command)] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                    "id": "primary.irp", "type": "irp", "device": system, "function": command, "confidence": TIER, "source": "probe"})
            if gate[(system, command)]:
                report.unrepresented[(MAKER, FILE, f"does not compile: {gate[(system, command)]}")] += 1
                continue
            cite = (f"caryaudio.com DMS IR Remote Codes/{FILE}@{sha} (retrieved {snapshot.retrieved}) page 1 table 1 row '{printing}' "
                    f"(function '{function}', system code A 0x{a:02X}, system code B 0x{b:02X}, key command 0x{command:02X}): "
                    f"RC5 system {system} command {command}, the table's system code {letter}")
            if notes:
                cite += f"; the table's note: {notes}"
            built.append((label_of(printing, function), {"id": "primary.irp", "type": "irp", "device": system, "function": command,
                                                          "confidence": TIER, "source": cite}))
        final = model
        while (BRAND.casefold(), final.casefold()) in authored:         # R15: another source has the name; the file is kept under another
            renamed = f"{final} (Cary Audio table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        keys = {n: {"label": b_[0], "forms": [b_[1]]} for n, b_ in zip(key_names([b_[0] for b_ in built]), built)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {MODEL}"],
                     "protocol": dict(PROTOCOL), "keys": keys}))
    return out
