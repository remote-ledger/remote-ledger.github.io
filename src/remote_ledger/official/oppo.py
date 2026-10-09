"""Oppo's remote code workbooks (``UDP-203_Remote_Code_v1.2.xls`` and the BDP-103/105 ones).

Oppo's download host no longer resolves, so the documents are the Wayback Machine's copies (D131). Each workbook has three sheets, ``Remote Code 1`` to ``3``: the three code sets a
player's remote can be set to so that two players in one room do not answer each other. A sheet is NEC1 with one
*custom code* (49, 61 and 43), and a row gives a key's NEC function in hex and decimal, the Pronto string Pronto's
own TSU3000 remote takes (``900A 006D 0000 0001 49B6 1AE5``: the device bytes and the function and its complement)
and the Pronto classic hex.

The codes are read from the hex columns as ``irp`` NEC1 forms, and **each is checked against the sheet's other
columns**: the decimal column, the TSU3000 string, and the NEC frame decoded bit by bit from the classic hex must all
say the same device and function. **The classic Pronto hex is the signal**, so a row whose classic hex does not decode
to the key's code is Untested, and a row where only another column differs (a typo in the derived TSU3000 string, say)
stays Plausible with the discrepancy in its citation.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .anthem import pronto_bytes
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "oppo"
BRAND = "Oppo"
SHEETS = ("Remote Code 1", "Remote Code 2", "Remote Code 3")
NOTES_SHEET = "Notes"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
#: Oldest first, with the models each names: a later workbook replaces an earlier one's file for a model both name.
#: The names are the ones the file's own name gives, and Oppo's UDP-205 page links the ``UDP-203`` workbook, whose
#: sheets say ``UDP-20X``.
DOCUMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BDP-103_BDP-105_Remote_Code_v1.1.xls", ("BDP-103", "BDP-105")),
    ("BDP-103_BDP-103D_Remote_Code_v1.2.xls", ("BDP-103", "BDP-103D")),
    ("UDP-203_Remote_Code_v1.2.xls", ("UDP-203", "UDP-205")),
)
_HEX = re.compile(r"^[0-9A-Fa-f]{1,2}$")
_DEVICE = re.compile(r"^[0-9A-Fa-f]{4}$")


@dataclass(frozen=True)
class Key:
    row: int
    name: str
    hex: str
    decimal: str
    tsu: str
    classic: str


@dataclass(frozen=True)
class CodeSet:
    sheet: str
    number: int
    product: str
    protocol: str
    custom: str
    device: str
    keys: tuple[Key, ...]


def parse_sheet(sheet: str, rows: list[list[str]]) -> CodeSet:
    """One ``Remote Code N`` sheet: what its head says, and a ``Key`` row for each key under it."""
    def cell(row: list[str], i: int) -> str:
        return row[i].strip() if i < len(row) else ""

    head: dict[str, str] = {}
    start = None
    for i, row in enumerate(rows):
        label = cell(row, 0)
        if label == "Key":
            start = i + 1
            break
        if label in ("Product", "Protocol", "Custom Code", "Device"):
            head[label] = cell(row, 1)
    if start is None or not {"Product", "Custom Code", "Device"} <= head.keys():
        raise ValueError(f"{sheet!r} has no Product, Custom Code and Device lines above a Key table")
    keys = tuple(Key(i + 1, cell(r, 0), cell(r, 1), cell(r, 2), " ".join(cell(r, 3).split()), " ".join(cell(r, 4).split()))
                 for i, r in enumerate(rows) if i >= start and cell(r, 0))
    return CodeSet(sheet, int(sheet.rsplit(" ", 1)[1]), head["Product"], head.get("Protocol", ""), head["Custom Code"], head["Device"], keys)


def _decimal(text: str) -> int | None:
    try:
        return int(float(text))
    except ValueError:
        return None


def check_key(code_set: CodeSet, key: Key) -> tuple[int, int, int] | tuple[str, ...]:
    """``(device, subdevice, function)`` when the row is readable, else the reasons it is not as a tuple of text.

    The device and subdevice are the custom code and its complement: the sheet's ``Device`` cell is both bytes."""
    if not _HEX.match(code_set.custom) or not _DEVICE.match(code_set.device):
        return ("the custom code or the device of the sheet is not hex",)
    if not _HEX.match(key.hex):
        return ("the key's hex is not a byte",)
    return int(code_set.custom, 16), int(code_set.device[2:], 16), int(key.hex, 16)


def disagreements(code_set: CodeSet, key: Key, triple: tuple[int, int, int]) -> tuple[list[str], list[str]]:
    """``(signal, other)``: where the sheet's classic Pronto hex (the signal) does not say the code, and where
    another column does not. Both are empty when every column agrees."""
    device, subdevice, function = triple
    out: list[str] = []
    if code_set.device.upper() != f"{device:02X}{subdevice:02X}":
        out.append(f"the sheet's device {code_set.device} is not custom code {code_set.custom} and its complement")
    if _decimal(key.decimal) != function:
        out.append(f"its decimal column says {key.decimal}")
    want = f"900A 006D 0000 0001 {code_set.device.upper()} {function:02X}{~function & 0xFF:02X}"
    if key.tsu.upper() != want:
        out.append(f"its TSU3000 string is {key.tsu or 'empty'}, not {want}")
    got = pronto_bytes(key.classic)
    signal = []
    if got != (device, subdevice, function, ~function & 0xFF):
        signal.append("its classic Pronto hex decodes to "
                      + ("nothing NEC-shaped" if got is None else f"device {got[0]}, sub device {got[1]}, function {got[2]}"))
    return signal, out


def key_names(keys: list[Key]) -> dict[int, str]:
    """``{row: key name}``: ``KEY_`` and D49's fold of the label, a later key of one name getting ``_2``."""
    seen: Counter = Counter()
    out: dict[int, str] = {}
    for key in keys:
        base = "KEY_" + (fold_label(key.name) or "COMMAND")
        seen[base] += 1
        out[key.row] = base if seen[base] == 1 else f"{base}_{seen[base]}"
    return out


@dataclass(frozen=True)
class Row:
    """One key of one sheet, read once however many models the workbook serves."""
    code_set: CodeSet
    key: Key
    triple: tuple[int, int, int]
    signal: list[str]       # where the classic Pronto hex does not say the code
    other: list[str]        # where another column does not


def read_document(snapshot: Snapshot, document: str, report: Report, gate: dict[tuple, str | None]) -> list[Row]:
    """Every key of a workbook's three sheets that is a code the ledger can hold, with where its columns disagree.
    The report counts each row once."""
    rows: list[Row] = []
    for sheet in SHEETS:
        code_set = parse_sheet(sheet, snapshot.sheet_of(document, sheet))
        for key in code_set.keys:
            report.count(MAKER, f"{document}: keys read")
            read = check_key(code_set, key)
            if isinstance(read[0], str):
                report.unrepresented[(MAKER, document, read[0])] += 1
                continue
            triple: tuple[int, int, int] = read           # type: ignore[assignment]
            if triple not in gate:
                gate[triple] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                    "id": "primary.irp", "type": "irp", "device": triple[0], "subdevice": triple[1], "function": triple[2],
                    "confidence": TIER, "source": "probe"})
            if gate[triple]:
                report.unrepresented[(MAKER, document, f"does not compile: {gate[triple]}")] += 1
                continue
            signal, other = disagreements(code_set, key, triple)
            if signal:
                report.count(MAKER, "rows whose classic Pronto hex does not say the key's code")
            if other:
                report.count(MAKER, "rows where another column does not say the key's code")
            rows.append(Row(code_set, key, triple, signal, other))
    for note in snapshot.sheet_of(document, NOTES_SHEET):
        text = " ".join(c.strip() for c in note if c.strip())
        if text and text != "Special Notes:":
            report.notes.append((MAKER, f"{document} sheet '{NOTES_SHEET}'", text[:300]))
    return rows


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    chosen: dict[str, tuple[str, list[Row]]] = {}
    for document, models in DOCUMENTS:
        rows = read_document(snapshot, document, report, gate)
        for model in models:
            chosen[model] = (document, rows)
    out: list[tuple[str, dict[str, Any]]] = []
    for model in sorted(chosen):
        document, rows = chosen[model]
        entry = snapshot.documents[document]
        where = entry["original"].split("://", 1)[1]
        sha = snapshot.sha(document)
        for number in (1, 2, 3):
            members = [r for r in rows if r.code_set.number == number]
            if not members:
                continue
            mname = model if number == 1 else f"{model} [code set {number}]"
            if (BRAND.casefold(), mname.casefold()) in authored:
                report.collisions.append((MAKER, mname))
                continue
            names = key_names([r.key for r in members])
            keys: dict[str, dict[str, Any]] = {}
            for r in members:
                code_set, key, (device, subdevice, function) = r.code_set, r.key, r.triple
                source = (f"{where}@{sha} (retrieved {snapshot.retrieved}, from the Wayback Machine snapshot {entry['snapshot']}) "
                          f"sheet '{code_set.sheet}' row {key.row} '{key.name}' ({code_set.product}, custom code {code_set.custom}, "
                          f"device {code_set.device}, key {key.hex}): NEC1 device {device} subdevice {subdevice} function {function}")
                if r.signal:
                    source += "; the sheet contradicts itself here: " + "; ".join(r.signal + r.other)
                elif r.other:
                    source += ("; the classic Pronto hex says the same, and the sheet's other columns do not all: "
                               + "; ".join(r.other))
                else:
                    source += "; the sheet's decimal column, TSU3000 string and classic Pronto hex all say the same"
                keys[names[key.row]] = {"label": key.name, "forms": [{
                    "id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                    "confidence": DOUBTFUL_TIER if r.signal else TIER, "source": source}]}
                report.count(MAKER, "keys")
            report.count(MAKER, "files")
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(mname)}.json",
                        {"manufacturer": BRAND, "model": mname, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(PROTOCOL), "keys": keys}))
    return out
