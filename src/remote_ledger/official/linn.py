"""Linn's RC-5 codes for its network products (DESIGN section 67).

LinnDocs, Linn's support wiki, has a page ``RC5 Codes``: "the current list of RC5 codes supported by Linn's network controlled products
(DS/DSM/etc). These codes can be used to program 3rd party remotes to work with these products." It is **one table of 128 command
codes against the three device addresses the products answer to: the amplifier 0x10 (16), the CD 0x14 (20) and the DVD 0x19 (25)**,
each cell the name of what that code does at that address (``Zero``, ``Volume +``, ``Skip +``), with notes under it (``Held Function:
Scroll Metadata``, ``Linn DSM - HDMI2``, ``Reserved (REM019)``) and a ``.`` where the address has nothing. A second document, a
one-page PDF gives the Pronto hex of the amplifier's 51 codes ("untested - derived via MAKEHEX").

A key is the ledger's ``RC5``: the address the device and the command the function. **One file for each address** (``DS/DSM Amplifier``,
``DS/DSM CD`` and ``DS/DSM DVD``). A cell's first line is the key's label, brackets removed (``[Direct Source PHONO]``); a cell that
begins ``Reserved (REM019)`` is a function reserved for that Linn remote and its label is the bracketed function after it, and one
that has none is no key; every other line is a note in the citation. **Where the PDF gives a code's hex it is decoded and must agree
with the code** (the address 0x10 and the function), as Hegel's hex is (D261).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug
from .hegel import decode_rc5

MAKER = "linn"
BRAND = "Linn"
PROTOCOL = {"name": "RC5", "carrierHz": 36000, "minSends": 1}
PAGE = "RC5_Codes.html"
PRONTO = "Linn_DS-DSM_Pronto_codes.pdf"
_CODE = re.compile(r"^0x([0-9A-Fa-f]{2}) \((\d{1,3})\)$")
_DEVICE = re.compile(r"^(?P<kind>.+?) Address / 0x(?P<hex>[0-9A-Fa-f]{2}) \((?P<dec>\d+)\)$")
_RESERVED = re.compile(r"^Reserved \((REM\d+)\)$")
_FOOTNOTE = re.compile(r"\^(\d+)")
_HEX = re.compile(r"0000 [0-9A-Fa-f]{4} 0000 [0-9A-Fa-f]{4}(?: [0-9A-Fa-f]{4})+")
_FUNCTION = re.compile(r"Function: ([0-9A-Fa-f]{2})")


def parse_cell(text: str) -> tuple[str, str, list[str]] | None:
    """``(label, the REM it is reserved for or empty, the notes)`` of a cell, or None where the address has nothing there (``.``) or
    the cell only says ``Reserved (REM019)``."""
    text = _FOOTNOTE.sub("", text).strip()
    if not text or text == ".":
        return None
    lines = [part.strip() for part in text.split(" / ") if part.strip() and part.strip() != "."]
    if not lines:
        return None
    reserved = ""
    if m := _RESERVED.match(lines[0]):
        reserved, lines = m[1], lines[1:]
        if not lines:
            return None
    label = lines[0].replace("[", "").replace("]", "").strip()
    return (label, reserved, lines[1:]) if label else None


def parse_table(rows: list[list[str]]) -> tuple[list[tuple[str, int]], list[tuple[int, int, list[str]]]]:
    """``(the devices, the rows)``: the header's ``Amplifier Address 0x10 (16)`` columns as ``(kind, address)``, and each row's
    ``(its position in the table, its command code, its cells)``."""
    devices = []
    for cell in rows[0][1:]:
        m = _DEVICE.match(cell)
        if not m:
            raise ValueError(f"a device column that is not 'Kind Address 0xNN (N)': {cell!r}")
        if int(m["hex"], 16) != int(m["dec"]):
            raise ValueError(f"{cell!r}: the hex and the decimal are not one address")
        devices.append((m["kind"], int(m["dec"])))
    out = []
    for position, row in enumerate(rows[1:], 2):
        m = _CODE.match(row[0])
        if not m or int(m[1], 16) != int(m[2]):
            raise ValueError(f"row {position}: {row[0]!r} is not a code with its hex and decimal agreeing")
        out.append((position, int(m[2]), row[1:]))
    return devices, out


def hex_rows(rows: list[list[str]]) -> dict[int, tuple[str, str]]:
    """The PDF's rows: command code -> (the Pronto hex, its row's name): a row's first cell is ``0x0C (12)``, the second the name and
    the third the hex, with ``Device Code: 10 Function: 13`` in front of it in some."""
    out: dict[int, tuple[str, str]] = {}
    for row in rows:
        if row[1] != "row" or len(row) < 7:
            continue
        m = _CODE.match(row[4])
        h = _HEX.search(row[6])
        if m and h:
            out[int(m[2])] = (h[0], row[5])
    return out


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    devices, table = parse_table(snapshot.sheet_of(PAGE, "table"))
    hexes = hex_rows(snapshot.sheet_of(PRONTO, "flow"))
    sha, pronto_sha = snapshot.sha(PAGE), snapshot.sha(PRONTO)
    report.count(MAKER, f"{PAGE}: rows read", len(table))
    gate: dict[tuple, str | None] = {}
    checks: dict[tuple[int, int], tuple[str, str]] = {}          # (address, command) -> (what the PDF's hex says, why it is doubtful)
    agreed = disagreed = 0
    for code, (hex_text, name) in sorted(hexes.items()):
        decoded = decode_rc5(hex_text)
        if decoded and decoded[:2] == (devices[0][1], code):
            checks[(devices[0][1], code)] = (f"the PDF's Pronto hex ('{name}') decodes to the same address and command (toggle bit "
                                              f"{decoded[2]})", "")
            agreed += 1
        else:
            why = (f"the PDF's Pronto hex for command {code} decodes to " + (f"address {decoded[0]} command {decoded[1]}" if decoded
                                                                           else "no RC-5 frame") + " and not to this code")
            checks[(devices[0][1], code)] = ("", why)
            disagreed += 1
            report.notes.append((MAKER, PRONTO, why))
    out: list[tuple[str, dict[str, Any]]] = []
    for column, (kind, address) in enumerate(devices):
        built: list[tuple[str, dict[str, Any]]] = []
        for position, command, cells in table:
            cell = cells[column]
            parsed = parse_cell(cell)
            if parsed is None:
                if cell.strip() not in ("", "."):
                    report.unrepresented[(MAKER, PAGE, f"a cell that only says '{_FOOTNOTE.sub('', cell).strip()}': reserved with no function")] += 1
                continue
            label, reserved, notes = parsed
            if (address, command) not in gate:
                gate[(address, command)] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                    "id": "primary.irp", "type": "irp", "device": address, "function": command, "confidence": TIER, "source": "probe"})
            if gate[(address, command)]:
                report.unrepresented[(MAKER, PAGE, f"does not compile: {gate[(address, command)]}")] += 1
                continue
            cite = (f"docs.linn.co.uk RC-5 codes/{PAGE}@{sha} (retrieved {snapshot.retrieved}) sheet 'table' row {position} (code "
                    f"0x{command:02X}, the {kind} address 0x{address:02X}): RC5 address {address} command {command}; the cell says '{cell}'")
            if reserved:
                cite += f"; reserved for the Linn remote {reserved}"
            said, doubt = checks.get((address, command), ("", ""))
            if said:
                cite += f"; {said} ({PRONTO}@{pronto_sha})"
            if doubt:
                cite += f"; {doubt}"
            built.append((label, {"id": "primary.irp", "type": "irp", "device": address, "function": command,
                                  "confidence": DOUBTFUL_TIER if doubt else TIER, "source": cite}))
        if not built:
            continue
        model = f"DS/DSM {kind}"
        if (BRAND.casefold(), model.casefold()) in authored:
            report.collisions.append((MAKER, model))
            continue
        keys = {n: {"label": b[0], "forms": [b[1]]} for n, b in zip(key_names([b[0] for b in built]), built)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(model)}.json",
                    {"manufacturer": BRAND, "model": model, "aliases": [], "controls": [f"{BRAND} DS", f"{BRAND} DSM"],
                     "protocol": dict(PROTOCOL), "keys": keys}))
    report.count(MAKER, "Pronto hex that decodes to its row's address and command", agreed)
    report.count(MAKER, "Pronto hex that decodes to another code", disagreed)
    return out
