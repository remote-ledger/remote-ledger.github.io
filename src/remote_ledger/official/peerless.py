"""Peerless-AV's IR code sheet for its Neptune outdoor TVs (DESIGN section 93).

``IR REMOTE CONTROL CODES: Neptune Partial Sun & Full Sun Outdoor Smart TVs`` (2025): one page of one ruled table, a row for each of 52 keys with its
``COMMAND``, its ``ENCODING`` (``NEC T/3T-32``), ``DATA`` (``0xF708FB04``), ``GROUP`` (``0x04FB``), ``CODE`` (``0x08``) and, on the first row, the
``Frequency`` (``38.1KHz``). The PDF is a live file on a Shopify CDN (the Neptune line is Peerless-AV's, and its product page is where the search that found the
file says it is linked); it is pinned by SHA-256 and its table is converted to CSV by ``tools/official_sheets_to_csv.py`` (``tables``).

**A key is the ledger's NEC1**: ``GROUP`` is the first two bytes sent (the device 0x04 and the subdevice 0xFB, which is its complement) and ``CODE`` the function,
and ``DATA`` is the whole frame as a 32-bit number whose bytes, least significant first, are the four sent: ``0xF708FB04`` is ``04 FB 08 F7``. **The reader checks
that ``DATA`` says what ``GROUP`` and ``CODE`` say** (the third byte the code, the fourth its complement); a row where it does not is Untested. **Six rows name an
encoding that is not the 32-bit one** (``NEC T/3T-33``, ``-34`` and ``-35``: ``AV Input``, ``Discrete Off``, ``Discrete On`` and ``HDMI 1`` to ``3``): the sheet does not
say how they differ, so their frame is read as the NEC1's the data gives and they are Untested. The carrier is the sheet's 38.1 kHz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "peerless"
BRAND = "Peerless-AV"
DOCUMENT = "Remote_Control_Codes_2025_Final.pdf"
HOST = "cdn.shopify.com Peerless-AV Neptune IR code sheet"
STANDARD = "NEC T/3T-32"
_HEX = re.compile(r"^0[xX]([0-9A-Fa-f]+)$")
_TITLE = re.compile(r"\b(Ne?t?ptune)\W*\s+(Partial Sun)\s*&\s*(Full Sun)", re.I)
_FREQUENCY = re.compile(r"^(\d+(?:\.\d+)?)\s*KHz$", re.I)


@dataclass(frozen=True)
class Row:
    number: int                     # its place in the table
    command: str
    encoding: str
    data: int
    group: int
    code: int


@dataclass(frozen=True)
class Sheet:
    models: tuple[str, ...]
    written: str                    # how the title spells the line
    carrier_hz: int
    rows: tuple[Row, ...]


def parse(table: list[list[str]]) -> Sheet:
    """The sheet of its ``tables`` (``page, table, cells``): the title in the first row, the heads ``COMMAND, ENCODING, DATA, GROUP, CODE, Frequency`` and a row for each key.
    The frequency is the one cell of the last column."""
    title = " ".join(" ".join(row[2:]) for row in table[:1])
    m = _TITLE.search(title.replace("™", ""))
    if m is None:
        raise ValueError(f"the title {title!r} does not name the Neptune Partial Sun and Full Sun TVs")
    models = (f"Neptune {m.group(2)}", f"Neptune {m.group(3)}")           # the sheet's title spells the line ``Netptune``
    rows: list[Row] = []
    carrier: int | None = None
    started = False
    for row in table[1:]:
        cells = [" ".join(c.split()) for c in row[2:]]
        if cells[:5] == ["COMMAND", "ENCODING", "DATA", "GROUP", "CODE"]:
            started = True
            continue
        if not started or len(cells) < 6 or not cells[1]:
            continue
        data, group, code = (_HEX.match(c) for c in cells[2:5])
        if not (data and group and code):
            raise ValueError(f"row {len(rows) + 1} '{cells[0]}': DATA, GROUP and CODE are {cells[2:5]}, not hex")
        if cells[5]:
            f = _FREQUENCY.match(cells[5])
            if f is None or carrier is not None:
                raise ValueError(f"row {len(rows) + 1} '{cells[0]}': the frequency {cells[5]!r} is not one cell 'N KHz'")
            carrier = round(float(f.group(1)) * 1000)
        rows.append(Row(len(rows) + 1, cells[0], cells[1], int(data.group(1), 16), int(group.group(1), 16), int(code.group(1), 16)))
    if not rows or carrier is None:
        raise ValueError("no rows with the heads COMMAND, ENCODING, DATA, GROUP and CODE, or no frequency")
    return Sheet(models, m.group(1), carrier, tuple(rows))


def key_names(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    sheet = parse(snapshot.sheet_of(DOCUMENT, "tables"))
    entry, sha = snapshot.documents[DOCUMENT], snapshot.sha(DOCUMENT)
    prefix = f"{HOST}/{DOCUMENT}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)})"
    protocol = {"name": "NEC1", "carrierHz": sheet.carrier_hz, "minSends": 1}
    report.count(MAKER, "rows read", len(sheet.rows))
    if sheet.written != "Neptune":
        report.notes.append((MAKER, DOCUMENT, f"the title spells the line '{sheet.written}': the files are named for Peerless-AV's Neptune"))
    gate: dict[tuple, str | None] = {}
    by_code: dict[int, list[str]] = {}
    for row in sheet.rows:
        by_code.setdefault(row.code, []).append(row.command)
    members: list[tuple[str, dict[str, Any]]] = []
    for row in sheet.rows:
        device, subdevice = row.group >> 8, row.group & 0xFF
        key = (device, subdevice, row.code)
        if key not in gate:
            gate[key] = form_compiles(IMPORT_ROOT, protocol, "K", {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": row.code,
                                                                  "confidence": TIER, "source": "probe"})
        if gate[key]:
            report.unrepresented[(MAKER, DOCUMENT, f"does not compile: {gate[key]}")] += 1
            continue
        cite = (f"{prefix} page 1 table row 'No. {row.number} {row.command}' (encoding {row.encoding}, DATA 0x{row.data:08X}, GROUP 0x{row.group:04X}, CODE 0x{row.code:02X}): "
                f"NEC1 device {device} subdevice {subdevice} function {row.code}")
        doubts: list[str] = []
        if (row.data & 0xFF, (row.data >> 8) & 0xFF, (row.data >> 16) & 0xFF, row.data >> 24) != (device, subdevice, row.code, row.code ^ 0xFF):
            doubts.append(f"its DATA 0x{row.data:08X} is not the bytes {device:02X} {subdevice:02X} {row.code:02X} {row.code ^ 0xFF:02X} that GROUP and CODE say")
        if row.encoding != STANDARD:
            doubts.append(f"its encoding is {row.encoding}, not {STANDARD}, and the sheet does not say how they differ: the frame the DATA gives is read as NEC1's")
        if len(by_code[row.code]) > 1:
            doubts.append("the sheet gives this code to " + " and ".join(f"'{n}'" for n in by_code[row.code]) + ", and a code is one function")
        for d in doubts:
            report.notes.append((MAKER, f"{DOCUMENT} row {row.number}", f"'{row.command}': {d}"))
        if doubts:
            cite += "; " + "; ".join(doubts)
        members.append((row.command, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": row.code,
                                      "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
    out: list[tuple[str, dict[str, Any]]] = []
    for model in sheet.models:
        name = model
        while (BRAND.casefold(), name.casefold()) in authored:
            renamed = f"{name} (Peerless-AV sheet)"
            report.notes.append((MAKER, name, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), name.casefold())]} has the name (R15)"))
            name = renamed
        keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(name)}.json",
                    {"manufacturer": BRAND, "model": name, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(protocol), "keys": keys}))
    return out
