"""Hisense's discrete IR codes for its Prosumer TVs (DESIGN section 103).

Hisense USA's ``RS-232/IR Protocol for Hisense Prosumer TV`` (version 3.6, 17 April 2017, 18 pages) lists on its first page **five models, each by two names**
(``55F1600 | LTDN55K2205BWUS``, ``55U1600 | HU55K324UBWG``, ``65U1600 | LTDN65K550GUBWUS``, ``75U1600 | LTDN75K700GUBWUS``, ``86U1600 | LTDN86XT910MUBWUS``), and on pages 4 to 9
**a table of 64 discrete IR commands**: a function, the low and the high byte of the customer code (``04`` ``FB``: a byte and its complement), the data byte and its complement, the
complete code (``04FB 708F``) and a Pronto code for each key. The serial protocol on pages 10 to 18 is not read. The page says to check the user manual for the commands a model has.

A key is the ledger's ``NEC1``: the customer code's two bytes as sent the device and the subdevice, the data byte the function, at 38.4 kHz (the Pronto codes' ``006C``). **The
Pronto code of each key is decoded and held against the bytes of the row** (the carrier, the leader and the 32 bits: the same decoder as Dune HD's, D375), and a row that disagrees, or
whose bytes are not what the table's columns say, is Untested or not read. **A file for each of the five models** (named by the first name, the second in the ``controls``), the same
64 keys in each: the table is for all five and does not say which model has which command.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug
from .dune import pronto_bytes

MAKER = "hisense"
BRAND = "Hisense"
FILE = "Hisense-RS-232-and-IR-Protocol-English_2.pdf"
HOST = "assets.hisense-usa.com RS-232 and IR protocol PDF"
PROTOCOL = {"name": "NEC1", "carrierHz": 38400, "minSends": 1}
CARRIER_WORD = 0x6C
HEADING = ["FUNCTION", "LOW CUSTOM", "HIGH CUSTOM (LOW CUSTOM COMPLEMENT)", "DATA CODE", "DATA CODE COMPLEMENT", "COMPLETE HEX CODE", "PRONTO CCF CODE FILE"]
_HEX = re.compile(r"^[0-9A-F]{2}$")


@dataclass(frozen=True)
class Row:
    page: int
    function: str
    low: int
    high: int
    data: int
    complement: int
    complete: str
    doubt: str = ""


def parse_models(rows: list[list[str]]) -> list[tuple[str, str]]:
    """The first page's title and its models: ``RS-232/IR Protocol for`` ``Hisense® Prosumer TV``, ``Version 3.6``, and a line ``A | B`` for each model after ``Models``."""
    lines = [r[1] for r in rows if r[0] == "1"]
    text = " ".join(lines)
    for need in ("RS-232/IR Protocol for", "Prosumer TV", "Version 3.6", "17 April 2017"):
        if need not in text:
            raise ValueError(f"{FILE} page 1: the title does not say `{need}`")
    try:
        start = lines.index("Models") + 1
    except ValueError as exc:
        raise ValueError(f"{FILE} page 1: no `Models`") from exc
    models = []
    for line in lines[start:]:
        m = re.fullmatch(r"(\S+) \| (\S+)", line)
        if not m:
            break
        models.append((m[1], m[2]))
    if len(models) != 5:
        raise ValueError(f"{FILE} page 1: {len(models)} models, not 5: {models}")
    return models


def parse_table(rows: list[list[str]], notes: list[str]) -> list[Row]:
    """The tables of pages 4 to 9 (``page, table, function, low, high, data, complement, complete hex, Pronto``): a title row (``DISCRETE CODES``) and the heading begin each page, and
    **the heading must be the one this reader knows**; a row whose customer code is not a byte and its complement, or whose data are not, is not read."""
    out: list[Row] = []
    for row in rows:
        page, cells = int(row[0]), row[2:]
        if len(cells) == 7 and cells[0] == "FUNCTION":
            if cells != HEADING:
                raise ValueError(f"{FILE} page {page}: the heading {cells} is not the table's")
            continue
        if not any(cells[1:]):
            continue                                                         # the title of the table on the page
        if len(cells) != 7:
            raise ValueError(f"{FILE} page {page}: a row of {len(cells)} cells, not 7: {cells[:3]}")
        function, low, high, data, comp, complete, pronto = cells
        if not all(_HEX.match(x) for x in (low, high, data, comp)):
            notes.append(f"page {page} '{function}': `{low} {high} {data} {comp}` are not four bytes")
            continue
        b = [int(x, 16) for x in (low, high, data, comp)]
        if b[0] ^ b[1] != 0xFF or b[2] ^ b[3] != 0xFF:
            notes.append(f"page {page} '{function}': `{low} {high} {data} {comp}` has a byte pair that is not a byte and its complement")
            continue
        doubts = []
        if complete.replace(" ", "") != f"{low}{high}{data}{comp}":
            doubts.append(f"the table's complete code `{complete}` is not `{low}{high} {data}{comp}`")
        decoded = pronto_bytes(pronto)
        if isinstance(decoded, str):
            doubts.append(f"the Pronto code is not an NEC frame ({decoded})")
        elif decoded != (CARRIER_WORD, b):
            doubts.append(f"the table's Pronto code gives carrier {decoded[0]:04X}, bytes {' '.join(f'{x:02X}' for x in decoded[1])}")
        out.append(Row(page, " ".join(function.replace("“", '"').replace("”", '"').split()), b[0], b[1], b[2], b[3], complete, "; ".join(doubts)))
    if not out:
        raise ValueError(f"{FILE}: no key read")
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
    notes: list[str] = []
    models = parse_models(snapshot.sheet_of(FILE, "pages 1-1"))
    rows = parse_table(snapshot.sheet_of(FILE, "tables 4-9"), notes)
    for note in notes:
        report.notes.append((MAKER, FILE, f"not read: {note}"))
    report.count(MAKER, f"{FILE}: keys read", len(rows))
    entry, sha = snapshot.documents[FILE], snapshot.sha(FILE)
    by_code: dict[int, list[Row]] = {}
    for r in rows:
        by_code.setdefault(r.data, []).append(r)
    gate: dict[int, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for first, second in models:
        built: list[tuple[str, dict[str, Any]]] = []
        for r in rows:
            if r.data not in gate:
                gate[r.data] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": r.low, "subdevice": r.high, "function": r.data,
                                                                         "confidence": TIER, "source": "probe"})
            if gate[r.data]:
                report.unrepresented[(MAKER, FILE, f"does not compile: {gate[r.data]}")] += 1
                continue
            twice = len(by_code[r.data]) > 1
            cite = (f"{HOST}/{FILE}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}) page {r.page} row '{r.function}' (code {r.low:02X} {r.high:02X} {r.data:02X} {r.complement:02X}"
                    f"): NEC1 device {r.low} subdevice {r.high} function {r.data}"
                    + (f"; the table gives this code to {' and '.join(repr(x.function) for x in by_code[r.data])}" if twice else "") + (f"; {r.doubt}" if r.doubt else ""))
            built.append((r.function, {"id": "primary.irp", "type": "irp", "device": r.low, "subdevice": r.high, "function": r.data,
                                       "confidence": DOUBTFUL_TIER if twice or r.doubt else TIER, "source": cite}))
        final = first
        while (BRAND.casefold(), final.casefold()) in authored:
            renamed = f"{final} (Hisense table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        body = {n: {"label": lab, "forms": [b_[1]]} for n, lab, b_ in zip(key_names([b_[0] for b_ in built]), [b_[0] for b_ in built], built)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(body))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {first}", f"{BRAND} {second}"], "protocol": dict(PROTOCOL), "keys": body}))
    return out
