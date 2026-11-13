"""Binary's IR codes for its matrix switchers (DESIGN section 106).

Binary is SnapAV's own brand, and the protocols-and-drivers folder of its media distribution products (``snapav.com/.../MediaDistribution/ProtocolsAndDrivers/``) has **four documents of IR
codes, each a table of Pronto codes** (the Wayback Machine's copies, as Yamaha's and BenQ's on the same site are): the **B-660-MTRX 4x2 and 4x4** (a PDF each: an output, an action, the
*standard code* ``0x00 0x09`` and the *expanded code*, the Pronto code), the **B-200-AVMATRIX 6x4** (a workbook of the Pronto code and the action of each of its 25 keys) and the
**B-500-MTRX-230** (a workbook of 256 single-command switching codes and 21 remote buttons).

A key is the ledger's ``NEC1``: the first two bytes of the frame as sent the device and the subdevice (``0x12 0xED``: a byte and its complement), the data byte the function; the carrier is
NEC1's 38 kHz whatever the sheet's frequency word is (the B-500's is ``006F``, 37.3 kHz: the citation says so). **Each Pronto code is decoded** (:func:`decode`: the leader, 32 bits and the
stop bit of an NEC frame, then whatever repeat the sheet's code sends) **and held against what else its document says**: the B-660's standard code must be the frame's device byte and data byte,
the B-660 4x4's first two outputs must be the 4x2's, and the B-500's switching code of ``Output o Input i`` must be device ``0x12`` and data ``(o - 1) * 16 + (i - 1)``; a key that breaks any of
them, and a code two differently named keys share, is Untested. One file for each model, named as the documents name it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "binary"
BRAND = "Binary"
HOST = "snapav.com Binary IR code documents"
NEC1 = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
NOMINAL_WORD = 0x6D                                   # the Pronto word of 38 kHz: a sheet with another word has it in its citations

B660 = {"B-660-MTRX-4x2_Pronto_IR_HEX_Codes.pdf": "B-660-MTRX-4x2", "B-660-MTRX-4x4_Pronto_IR_HEX_Codes.pdf": "B-660-MTRX-4x4"}
B200 = "B-200-AVMATRIX-6x4-IR_CCF_Hex_Codes.xls"
B200_SHEET = "RS232 Commands"                         # the sheet's own name; its content is the IR commands
B500 = "B-500-MTRX-230_IR-Protocol.xlsx"
B500_SWITCHING = "Single Command Switching codes"
B500_REMOTE = "Remote Button Codes"
B500_DEVICE = 0x12

_HEX_LINE = re.compile(r"^(?:[0-9A-F]{4} ?)+$")
_STANDARD = re.compile(r"^0x([0-9A-F]{2}) 0x([0-9A-F]{2})$")


@dataclass
class Row:
    where: str                                        # ``page 1 row 'Output 1 Source 1'``, ``sheet 'RS232 Commands' row 12``
    label: str
    pronto: str
    standard: tuple[int, int] | None = None           # the B-660's standard code: the device byte and the data byte
    output: int | None = None                         # the number of the output a B-660 or B-500 row is for
    action: str = ""                                  # the B-660's action
    expected: int | None = None                       # the data byte the B-500's grid says the row has


@dataclass
class Key:
    row: Row
    bytes_: list[int]
    word: int
    shape: str
    doubts: list[str] = field(default_factory=list)


def decode(pronto: str) -> tuple[int, list[int], str] | str:
    """The carrier word, the four bytes of the frame as sent, and the shape of the sequences (``once 34, repeat 2``), or why it is not an NEC frame. **The first 34 pairs are the frame**: a leader
    (a mark of 0x140 to 0x170 and a space of 0x90 to 0xB0 units), 32 bits (a mark of about 0x15 and a space of about 0x15 for a zero or about 0x40 for a one: a space that is neither is not a
    frame) and the stop bit; **a pair after them is a repeat** (the repeat leader of 0x50 to 0x5A units, or a unit mark and a gap) and any other is refused."""
    try:
        words = [int(w, 16) for w in pronto.split()]
    except ValueError:
        return "not hexadecimal"
    if len(words) < 4 + 2 * 34 or words[0] != 0:
        return "not a Pronto code of a frame"
    once, repeat = words[2], words[3]
    pairs = list(zip(words[4::2], words[5::2]))
    if len(words) != 4 + 2 * (once + repeat) or len(pairs) != once + repeat or (once and once != 34):
        return f"the sequences (once {once}, repeat {repeat}) do not hold the pairs written ({len(pairs)})"
    if not (0x140 <= pairs[0][0] <= 0x170 and 0x90 <= pairs[0][1] <= 0xB0):
        return "no NEC leader"
    bits = []
    for mark, space in pairs[1:33]:
        if not 0x10 <= mark <= 0x1A:
            return "a bit's mark is not about 0x15"
        if 0x10 <= space <= 0x1C:
            bits.append(0)
        elif 0x38 <= space <= 0x48:
            bits.append(1)
        else:
            return f"a bit's space {space:04X} is neither a zero's nor a one's"
    if not 0x10 <= pairs[33][0] <= 0x1A:
        return "no stop bit after the 32 bits"
    for mark, space in pairs[34:]:
        leader = 0x140 <= mark <= 0x170 and 0x50 <= space <= 0x5A
        if not (leader or 0x10 <= mark <= 0x1A):
            return f"a pair after the frame ({mark:04X} {space:04X}) is neither a repeat leader nor a gap"
    shape = f"once {once}, repeat {repeat}" if once else f"a repeat sequence of {repeat} pairs"
    return words[1], [sum(b << i for i, b in enumerate(bits[j * 8:(j + 1) * 8])) for j in range(4)], shape


def parse_b660(rows: list[list[str]], document: str) -> list[Row]:
    """The PDF's lines (``pages``): the heading ``Output``, ``Action``, ``Standard Code``, ``Expanded Code``, then a record of an output (``Output 1``), an action (``Source 1``, ``Right``,
    ``CEC ON``), the standard code (``0x00 0x09``) and the lines of the Pronto code. A record that is not so, or a file with no record, is refused."""
    lines = [r[1].strip() for r in rows]
    if lines[:4] != ["Output", "Action", "Standard Code", "Expanded Code"]:
        raise ValueError(f"{document}: the heading {lines[:4]} is not the table's")
    out: list[Row] = []
    i = 4
    while i < len(lines):
        if i + 3 >= len(lines) or not re.fullmatch(r"Output \d+", lines[i]) or not (m := _STANDARD.match(lines[i + 2])) or not _HEX_LINE.match(lines[i + 3]):
            raise ValueError(f"{document}: lines {lines[i:i + 4]} are not an output, an action, a standard code and a Pronto code")
        j = i + 3
        pronto = []
        while j < len(lines) and _HEX_LINE.match(lines[j]):
            pronto.append(lines[j])
            j += 1
        action = lines[i + 1]
        out.append(Row(f"page {rows[i][0]} row '{lines[i]} {action}'", f"{lines[i]} {action}", " ".join(" ".join(pronto).split()),
                       (int(m[1], 16), int(m[2], 16)), int(lines[i].split()[1]), action))
        i = j
    if not out:
        raise ValueError(f"{document}: no record")
    return out


def parse_b200(rows: list[list[str]]) -> list[Row]:
    """The sheet ``RS232 Commands`` of the B-200's workbook: a title ``SNAP AV B-200-AVMATRIX-6x4 IR Commands``, the heading ``OUTPUT``, ``CCF HEX COMMAND``, ``ACTION``, a key a row (the output's
    name is written on the first row of its keys only) and a ``REV`` table after them. A code with no action, or no key, is refused."""
    if not rows or "B-200-AVMATRIX-6x4 IR Commands" not in rows[1][0]:
        raise ValueError(f"{B200}: the title {rows[1][0] if len(rows) > 1 else None!r} is not the B-200's")
    heading = [n for n, r in enumerate(rows) if [c.strip() for c in r[1:4]] == ["OUTPUT", "CCF HEX COMMAND", "ACTION"]]
    if len(heading) != 1:
        raise ValueError(f"{B200}: the heading `OUTPUT, CCF HEX COMMAND, ACTION` is not once in the sheet")
    out: list[Row] = []
    for n in range(heading[0] + 1, len(rows)):
        cells = [c.strip() for c in rows[n]]
        if cells[1] == "REV":
            break
        code = " ".join(cells[2].split())
        if not code and not cells[3]:
            continue
        if not code.startswith("0000 ") or not cells[3]:
            raise ValueError(f"{B200} row {n + 1}: `{cells[1:4]}` is not an action and a Pronto code")
        out.append(Row(f"sheet '{B200_SHEET}' row {n + 1}", " ".join(cells[3].split()), code))
    if not out:
        raise ValueError(f"{B200}: no key read")
    return out


def parse_b500_switching(rows: list[list[str]]) -> list[Row]:
    """The sheet of the B-500's 256 switching codes: ``Output o``, ``Input i`` and the Pronto code a row, the outputs and inputs 1 to 16 once each; **the row's data byte is expected to be
    ``(o - 1) * 16 + (i - 1)``**. A row that is not so, or a missing pair, is refused."""
    out: list[Row] = []
    for n, row in enumerate(rows, 1):
        cells = [c.strip() for c in row]
        if len(cells) >= 5 and cells[0] == "Output" and cells[2] == "Input":
            if not (cells[1].isdigit() and cells[3].isdigit() and " ".join(cells[4].split()).startswith("0000 ")):
                raise ValueError(f"{B500} {B500_SWITCHING} row {n}: `{cells}` is not an output, an input and a Pronto code")
            o, i = int(cells[1]), int(cells[3])
            out.append(Row(f"sheet '{B500_SWITCHING}' row {n}", f"Output {o} Input {i}", " ".join(cells[4].split()), output=o, expected=(o - 1) * 16 + (i - 1)))
    if sorted((r.output, r.expected) for r in out) != sorted((o, (o - 1) * 16 + i) for o in range(1, 17) for i in range(16)):
        raise ValueError(f"{B500} {B500_SWITCHING}: the rows are not the 16 outputs and 16 inputs once each")
    return out


def parse_b500_remote(rows: list[list[str]]) -> list[Row]:
    """The sheet of the B-500's remote buttons: the heading ``Remote Key``, ``CCF`` and a key a row."""
    heading = [n for n, r in enumerate(rows) if [c.strip() for c in r[:2]] == ["Remote Key", "CCF"]]
    if len(heading) != 1:
        raise ValueError(f"{B500} {B500_REMOTE}: the heading `Remote Key, CCF` is not once in the sheet")
    out: list[Row] = []
    for n in range(heading[0] + 1, len(rows)):
        cells = [c.strip() for c in rows[n]]
        if not any(cells):
            continue
        code = " ".join(cells[1].split())
        if not cells[0] or not code.startswith("0000 "):
            raise ValueError(f"{B500} {B500_REMOTE} row {n + 1}: `{cells[:2]}` is not a key and a Pronto code")
        out.append(Row(f"sheet '{B500_REMOTE}' row {n + 1}", " ".join(cells[0].split()), code))
    if not out:
        raise ValueError(f"{B500} {B500_REMOTE}: no key read")
    return out


def _keys(rows: list[Row], document: str, report: Report) -> list[Key]:
    keys: list[Key] = []
    for row in rows:
        decoded = decode(row.pronto)
        if isinstance(decoded, str):
            report.notes.append((MAKER, document, f"not read: {row.where} '{row.label}': {decoded}"))
            continue
        word, b, shape = decoded
        key = Key(row, b, word, shape)
        if b[2] ^ b[3] != 0xFF:
            report.notes.append((MAKER, document, f"not read: {row.where} '{row.label}': bytes {' '.join(f'{x:02X}' for x in b)} have a fourth byte that is not the complement of the third"))
            continue
        keys.append(key)
    report.count(MAKER, "Pronto codes", len(rows))
    return keys


def _hold(keys: list[Key], document: str, other: dict[tuple[int, str], list[int]] | None = None) -> None:
    """The checks every document has: a frame two differently named keys share (a doubt on each of them), and, for a B-660 row, its standard code (and, for the 4x4, the 4x2's frame
    of the same output and action)."""
    by_frame: dict[tuple[int, ...], list[Key]] = {}
    for k in keys:
        by_frame.setdefault(tuple(k.bytes_), []).append(k)
    for group in by_frame.values():
        names = {k.row.label for k in group}
        if len(names) > 1:
            for k in group:
                k.doubts.append("the document gives this code to " + " and ".join(repr(n) for n in sorted(names)))
    for k in keys:
        r = k.row
        if r.standard is not None and (k.bytes_[0], k.bytes_[2]) != r.standard:
            k.doubts.append(f"the row's standard code {r.standard[0]:#04x} {r.standard[1]:#04x} is not the Pronto code's device byte and data byte ({k.bytes_[0]:#04x} {k.bytes_[2]:#04x})")
        if r.expected is not None and (k.bytes_[0] != B500_DEVICE or k.bytes_[2] != r.expected):
            k.doubts.append(f"the grid of the sheet says device {B500_DEVICE:#04x} and data byte {r.expected:#04x} for {r.label}, the Pronto code has {k.bytes_[0]:#04x} and {k.bytes_[2]:#04x}")
        if other is not None and r.output is not None and (r.output, r.action) in other and other[(r.output, r.action)] != k.bytes_:
            k.doubts.append("the other B-660-MTRX document has a different code for the same output and action")


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple[int, int, int], str | None] = {}
    files: list[tuple[str, str, list[Key]]] = []           # the model, the document it is cited from, its keys
    # the B-660 pair: the 4x4's first two outputs are held against the 4x2's
    parsed = {name: _keys(parse_b660(snapshot.sheet_of(name, "pages"), name), name, report) for name in B660}
    small = {(k.row.output, k.row.action): k.bytes_ for k in parsed["B-660-MTRX-4x2_Pronto_IR_HEX_Codes.pdf"]}
    large = {(k.row.output, k.row.action): k.bytes_ for k in parsed["B-660-MTRX-4x4_Pronto_IR_HEX_Codes.pdf"]}
    for name, model in B660.items():
        _hold(parsed[name], name, large if "4x2" in name else small)
        files.append((model, name, parsed[name]))
    b200 = _keys(parse_b200(snapshot.sheet_of(B200, B200_SHEET)), B200, report)
    _hold(b200, B200)
    files.append(("B-200-AVMATRIX-6x4", B200, b200))
    b500 = (_keys(parse_b500_switching(snapshot.sheet_of(B500, B500_SWITCHING)), B500, report)
            + _keys(parse_b500_remote(snapshot.sheet_of(B500, B500_REMOTE)), B500, report))
    _hold(b500, B500)
    files.append(("B-500-MTRX-230", B500, b500))
    out: list[tuple[str, dict[str, Any]]] = []
    for model, document, keys in sorted(files):
        entry, sha = snapshot.documents[document], snapshot.sha(document)
        built: list[tuple[str, dict[str, Any]]] = []
        for k in keys:
            device, subdevice, function = k.bytes_[0], k.bytes_[1], k.bytes_[2]
            if (device, subdevice, function) not in gate:
                gate[(device, subdevice, function)] = form_compiles(IMPORT_ROOT, NEC1, "K", {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice,
                                                                                         "function": function, "confidence": TIER, "source": "probe"})
            if gate[(device, subdevice, function)]:
                report.unrepresented[(MAKER, document, f"does not compile: {gate[(device, subdevice, function)]}")] += 1
                continue
            archived = f", from the Wayback Machine snapshot {entry['snapshot']}" if "snapshot" in entry else ""
            cite = (f"{HOST}/{document}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}{archived}) {k.row.where}: NEC1 device {device} subdevice {subdevice} function {function} "
                    f"(bytes {' '.join(f'{b:02X}' for b in k.bytes_)}; Pronto code of {k.shape})"
                    + (f"; the sheet's carrier word is {k.word:04X}, the key is NEC1's 38 kHz" if k.word != NOMINAL_WORD else "") + "".join(f"; {x}" for x in k.doubts))
            built.append((k.row.label, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                                        "confidence": DOUBTFUL_TIER if k.doubts else TIER, "source": cite}))
        if not built:
            report.notes.append((MAKER, document, "no key kept"))
            continue
        final = model
        while (BRAND.casefold(), final.casefold()) in authored:
            renamed = f"{final} (Binary table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        seen: Counter = Counter()
        body: dict[str, Any] = {}
        for label, form in built:
            base = "KEY_" + (fold_label(label) or "COMMAND")
            seen[base] += 1
            body[base if seen[base] == 1 else f"{base}_{seen[base]}"] = {"label": label, "forms": [form]}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(body))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(NEC1), "keys": body}))
    return out
