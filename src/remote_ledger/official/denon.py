"""Denon's AVR-4311CI / AVR-4311 remote code list (DESIGN section 81).

One PDF, ``AVR-4311CI/AVR-4311 Remote Code List Ver. 01`` (April 2010, Denon's UK site), in two parts. **Pages 2 to 7: ``FORMAT SHARP``**,
15 bits (the ledger's ``Denon``): tables of 64 codes for a system address (``C1`` to ``C5``) and the extension bits (``C12``, ``C13``), a
row for each 6-bit data value (``C6`` to ``C11``) with the key's name or none. **Pages 8 to 33: ``FORMAT KASEIKYO``**, 48 bits (the ledger's
``Denon-K``): tables of the 48 bits of each key for a function group (``K4-1 : MAIN ZONE``), whose first 24 bits are the vendor code,
a parity nibble and the group's ``GENRE1`` and whose last 8 are the exclusive or of the 3rd, 4th and 5th bytes. The tables' text is a
word to a cell with its place (``words`` of ``tools/official_sheets_to_csv.py``) and the ruled tables' rows, and the PDF is the Wayback
Machine's copy.

**Sharp**: the address and the data are each first bit first (``01000`` is the device 2, ``100000`` the data 1), the function is the
data and the extension bits above it (``11`` is +192, so ``POWER`` is 0xC1, which the ledger's Denon remotes have); the data's value must
be the row's number. **Kaseikyo**: ``GENRE1`` (bits 21 to 24) is the device, ``GENRE2`` (25 to 28) the subdevice and bits 29 to 40 the
function, each first bit first, which the 8 parity bits must agree with as the ledger's ``Denon-K`` computes them. **Only the tables for
``REMOTE ID : 1`` that give all 48 bits are read**: the tables for IDs 2 to 4 and the iPod, Sirius and HD Radio ones write the ID and parity
bits as ``*`` and are not read. A name that says ``(except AVR4311CI)``, ``(AVR4311CI only)``, ``(AVR4311 only)`` or ``※1`` (the page's note:
``Except AVR4311CI``) or ``※2`` (``EU only``, read as the AVR-4311's) is in the file of the models it names.

**The X-series code lists of 2020 (DESIGN section 90)** are Word documents (the AVR-X2700H, X2700H DAB and S960H; the AVR and AVC-X3700H, X4700H and X6700H) in the same two
formats, as the cells of their tables (``paragraphs`` of ``tools/official_sheets_to_csv.py``). Each key row has an identifier of its own (``RCSHP0230016``: the Sharp
table of address 02 and extension bits 3, row 16; ``RCKSK0470785``: the Kaseikyo table K4-7, function 785), which the reader checks against the table's header and the row's
bits. A table of Kaseikyo rows writes the ID bits and the parity as ``*`` and takes its ``GENRE1`` and ``GENRE2`` from the group's header (remote ID 1's: the document's
table of IDs 2 to 4 is not read, as the AVR-4311's was not).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "denon"
BRAND = "Denon"
DOCUMENT = "AVR4311CI_AVR4311_IR-Codes.pdf"
HOST = "denon.co.uk RC code list"
SHARP = {"name": "Denon", "carrierHz": 38000, "minSends": 1}
KASEIKYO = {"name": "Denon-K", "carrierHz": 37000, "minSends": 1}
#: the document's names of the two models -> the files'
MODELS = {"AVR4311CI": "AVR-4311CI", "AVR4311": "AVR-4311"}
ALL = frozenset(MODELS)
SHARP_PAGES = range(2, 8)
KASEIKYO_PAGES = range(8, 34)
_ADDRESS = re.compile(r"SYSTEM ADDRESS ?\(C1~C5\) : ([01]{5}) , EXTENSION BIT ?\(C12,C13\) : ([01]{2})")
_EXCEPT = re.compile(r"\s*\((?:e|E)xcept (AVR4311(?:CI)?)\)")
_ONLY = re.compile(r"\s*\((AVR4311(?:CI)?) only\)")
_MARK = re.compile(r"\s*※(\d)")
_BIT = re.compile(r"^[01*]$")
BIT_CUT = 386                         # a Kaseikyo row's bits are left of this, its key name right of it
VENDOR = "0010101001001100"            # bits 1 to 16: 0x54 and 0x32, each first bit first
NOTES = {"1": "Except AVR4311CI", "2": "EU only"}


def nf(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def words_of(row: list[str]) -> list[tuple[int, str]]:
    return [(int(row[i]), nf(row[i + 1])) for i in range(2, len(row), 2)]


def value(bits: str) -> int:
    """The number whose bits are given first bit (least significant) first."""
    return sum(int(b) << i for i, b in enumerate(bits))


@dataclass
class Alternative:
    printed: str
    label: str
    models: frozenset[str]
    marks: tuple[str, ...] = ()


def read_name(text: str, models: frozenset[str] = ALL) -> Alternative:
    """``(label, the models it is for)`` of one printed name: ``TUNER(except AVR4311CI)`` is the AVR4311's ``TUNER``, ``HD RADIO(AVR4311CI
    only)`` the CI's, a ``※1`` is ``Except AVR4311CI`` and a ``※2`` ``EU only``."""
    rest, kept = text, set(models)
    for pattern, how in ((_EXCEPT, "except"), (_ONLY, "only")):
        for m in list(pattern.finditer(rest)):
            kept = kept - {m.group(1)} if how == "except" else kept & {m.group(1)}
        rest = pattern.sub("", rest)
    marks = tuple(_MARK.findall(rest))
    for mark in marks:
        kept = kept - {"AVR4311CI"} if mark == "1" else kept & {"AVR4311"}
    rest = _MARK.sub("", rest)
    label = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", " ".join(rest.split())))
    return Alternative(text, label, frozenset(kept), marks)


def alternatives(lines: list[str], models: frozenset[str] = ALL) -> list[Alternative]:
    """The names of a cell's lines: a line that begins with ``(`` goes on the line before, a line that begins with ``/`` is the rest of
    a name, and a name with a model's qualifier is each side of a ``)/``."""
    joined: list[str] = []
    for line in lines:
        qualified = bool(joined) and bool(re.search(r"(except|only)\b[^()]*\)$", joined[-1]))
        if joined and line.startswith("("):
            joined[-1] += " " + line
        elif joined and line.startswith("/") and not qualified:
            joined[-1] += line
        else:
            joined.append(line[1:] if line.startswith("/") else line)          # `/HD RADIO(AVR4311CI only)` after `TUNER(except AVR4311CI)` is the other name
    parts: list[str] = []
    for line in joined:
        parts.extend(re.split(r"(?<=\))/", line) if _EXCEPT.search(line) or _ONLY.search(line) else [line])
    return [read_name(p.strip(), models) for p in parts if p.strip()]


# --- Sharp ---------------------------------------------------------------------------------------------------------


@dataclass
class SharpRow:
    page: int
    address: str
    extension: str
    number: int
    data: str
    names: list[Alternative]

    @property
    def device(self) -> int:
        return value(self.address)

    @property
    def function(self) -> int:
        return value(self.data) + 64 * value(self.extension)


def page_rows(rows: list[list[str]]) -> tuple[dict[int, list[tuple[int, list[tuple[int, str]]]]], dict[int, list[tuple[int, int]]]]:
    """``({page: [(y, words)]}, {page: [(y0, y1)]})`` of the words CSV: the lines of each page and its ruled tables' rows."""
    lines: dict[int, list[tuple[int, list[tuple[int, str]]]]] = {}
    spans: dict[int, list[tuple[int, int]]] = {}
    for row in rows:
        page = int(row[0])
        if row[1] == "R":
            spans.setdefault(page, []).append((int(row[2]), int(row[3])))
        else:
            lines.setdefault(page, []).append((int(row[1]), words_of(row)))
    return lines, spans


def parse_sharp(rows: list[list[str]]) -> list[SharpRow]:
    lines, spans = page_rows(rows)
    out: list[SharpRow] = []
    carried: tuple[int, str, str] | None = None       # a table that goes on on the next page (the AVR4311's 47 and 63 are alone on page 3)
    for page in SHARP_PAGES:
        headers = [(-1, carried[1], carried[2])] if carried else []
        for y, words in lines.get(page, []):
            m = _ADDRESS.search(" ".join(t for _, t in words))
            if m:
                headers.append((y, m.group(1), m.group(2)))
        for y0, y1 in spans.get(page, []):
            header = [h for h in headers if h[0] < y0 + 1]
            if not header:
                continue
            _, address, extension = header[-1]
            carried = header[-1]
            inside = [(y, w) for y, w in lines.get(page, []) if y0 - 2 <= y < y1]
            for left in (True, False):
                number = data = None
                names: list[str] = []
                for y, words in inside:
                    text = [(x, t) for x, t in words if (x < 300) == left]
                    for x, t in text:
                        if re.fullmatch(r"\d{1,2}", t) and x < (100 if left else 350) and number is None:
                            number = int(t)
                        elif re.fullmatch(r"[01]{6}", t):
                            data = t
                    line = " ".join(t for x, t in text if (150 < x < 300 if left else x > 420))
                    if line:
                        names.append(line)
                if number is None:
                    continue
                if data is None or value(data) != number:
                    raise ValueError(f"page {page}: the row {number} has data {data}, whose value is not its number")
                out.append(SharpRow(page, address, extension, number, data, alternatives(names)))
    return out


# --- Kaseikyo ------------------------------------------------------------------------------------------------------


@dataclass
class KRow:
    page: int
    code: str                      # ``K4-1``
    group: str                     # ``MAIN ZONE``
    models: frozenset[str]
    device: int
    subdevice: int
    bits: str                      # bits 29 to 48
    names: list[str] = field(default_factory=list)

    @property
    def function(self) -> int:
        return value(self.bits[:12])

    @property
    def parity(self) -> int:
        return value(self.bits[12:20])

    def computed_parity(self) -> int:
        f = self.function
        return (self.device << 4) ^ self.subdevice ^ ((f << 4) & 0xFF) ^ ((f >> 4) & 0xFF)


def copied_zone(device: int, subdevice: int, function: int, parity: int) -> int | None:
    """The GENRE2 (1, 3, 5 or 7: the main zone, zone 2, zone 3, zone 4) of another table whose parity byte a row's is, or ``None``. **A zone's table is copied from another's
    and keeps that table's parity byte** (§94: the rows the other sources' remotes hold agree in their data bits with the copy's, and only the byte is the other table's), so
    a row whose byte is another zone's has its data bits read as they are and is not doubted for it."""
    for g in (1, 3, 5, 7):
        if g != subdevice and parity == (device << 4) ^ g ^ ((function << 4) & 0xFF) ^ ((function >> 4) & 0xFF):
            return g
    return None


@dataclass
class Line:
    page: int
    y: int
    table: dict[str, Any]
    tokens: list[tuple[int, str]]          # the bits of the line, by place
    name: str                               # what is right of the bits

    @property
    def at(self) -> int:
        return self.page * 1000 + self.y


def parse_kaseikyo(rows: list[list[str]]) -> tuple[list[KRow], list[str]]:
    """``(the rows of the tables for remote ID 1 that give all 48 bits, what is not read)``.

    A ``DENON CODE`` header (the line before it is the group's title) is followed by 24 bits, a ``REMOTE ID SET`` line and a ``K4-1 : MAIN
    ZONE`` one; then a line of bits is a row or part of one (a name of two lines has its bits in the middle: ``0 0`` and then 18 more, by
    place), the key's name is what is right of the bits, and the first row of a table has the 4 bits of ``GENRE2`` before its 20. **A line that
    has no bits is part of a name**: the rest of the row before when it begins ``/``, the first line of the next row when it ends ``(except
    AVR4311CI)``, and otherwise the row whose bits are nearer to it."""
    lines, _ = page_rows(rows)
    skipped: list[str] = []
    seen: list[Line] = []
    header: str | None = None
    title, last_text, id_set = "", "", ""
    table: dict[str, Any] | None = None
    for page in KASEIKYO_PAGES:
        for y, words in lines.get(page, []):
            if y > 795:
                continue                                          # the page number
            text = " ".join(t for _, t in words)
            left = [(x, t) for x, t in words if x < BIT_CUT]
            right = " ".join(t for x, t in words if x >= BIT_CUT)
            bits = [t for _, t in left if _BIT.match(t)]
            all_bits = [t for _, t in words if _BIT.match(t)]
            if len(all_bits) == 24 and len(words) == 24 and words[-1][0] > 440:      # the 24 bits of a header: the vendor, the parity nibble, GENRE1
                header, table = "".join(all_bits), None
                continue
            if text.startswith(("DENON CODE", "Parity")):
                title = last_text
                continue
            if m := re.match(r"REMOTE ID SET : (.+)$", text):
                id_set, table = m.group(1), None
                continue
            if m := re.match(r"(K\d-\w+) : (.+)$", text):
                if header is None or header[:16] != VENDOR or header[16:20] != "0000":
                    raise ValueError(f"page {page}: the table {m.group(1)} has no header of the vendor 0x54 0x32 and a 0 parity nibble")
                table = {"code": m.group(1), "group": m.group(2).strip(), "id": id_set, "genre1": value(header[20:24]), "genre2": None,
                         "models": read_name(title).models if title else ALL}
                if id_set != "1":
                    skipped.append(f"page {page} {m.group(1)} : {m.group(2).strip()} (REMOTE ID SET : {id_set})")
                continue
            last_text = text
            if table is None or table["id"] != "1":
                continue
            if text.startswith("GENRE2") or (re.fullmatch(r"(?:\d+ ?)+", text) and not all(_BIT.match(t) for _, t in words)):
                continue                                          # the column headers
            if left and len(bits) != len(left):
                raise ValueError(f"page {page} y {y}: cannot read the line {text!r}")
            if "*" in bits:
                raise ValueError(f"page {page} y {y}: a table for remote ID 1 has a '*' bit")
            seen.append(Line(page, y, table, left, right))
    out: list[KRow] = []
    pending: list[str] = []
    building: list | None = None                       # [tokens, names, page] of a row whose bits are not all read
    for i, line in enumerate(seen):
        table = line.table
        if not line.tokens:
            if line.name.startswith("/"):
                to_next = False
            elif re.search(r"\(except AVR4311CI\)$", line.name):
                to_next = True
            else:
                before = [o.at for o in seen[:i] if o.tokens and o.table is table]
                after = [o.at for o in seen[i + 1:] if o.tokens and o.table is table]
                to_next = bool(after) and (not before or after[0] - line.at < line.at - before[-1])
            if to_next:
                pending.append(line.name)
            elif building is not None:
                building[1].append(line.name)
            elif out and out[-1].code == table["code"]:
                out[-1].names.append(line.name)
            else:
                raise ValueError(f"page {line.page} y {line.y}: the name {line.name!r} goes with no row")
            continue
        if building is None:
            building = [[], pending, line.page]
            pending = []
        building[0].extend(line.tokens)
        if line.name:
            building[1].append(line.name)
        need = 24 if table["genre2"] is None else 20
        if len(building[0]) < need:
            continue
        if len(building[0]) != need:
            raise ValueError(f"page {line.page} y {line.y}: a row has {len(building[0])} bits, not {need}")
        row_bits = "".join(t for _, t in sorted(building[0]))      # a name of two lines has its bits 39 and 40 on the line above
        if need == 24:
            table["genre2"] = value(row_bits[:4])
            row_bits = row_bits[4:]
        if row_bits[10:12] != "00":
            raise ValueError(f"page {line.page} y {line.y}: the ID bits of a row of a table for remote ID 1 are {row_bits[10:12]}, not 00")
        out.append(KRow(building[2], table["code"], table["group"], table["models"], table["genre1"], table["genre2"], row_bits, building[1]))
        building = None
    return out, skipped


# --- the files -----------------------------------------------------------------------------------------------------


def key_names(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label.replace("–", "-")) or "COMMAND")
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


@dataclass
class Member:
    label: str
    code: tuple
    fields: dict[str, int]
    cite: str
    table: str
    doubts: list[str] = field(default_factory=list)


def build_pdf(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    rows = snapshot.sheet_of(DOCUMENT, "words")
    entry = snapshot.documents[DOCUMENT]
    sha = snapshot.sha(DOCUMENT)
    where = f"{HOST}/{DOCUMENT}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']})"
    sharp = parse_sharp(rows)
    kaseikyo, skipped = parse_kaseikyo(rows)
    for note in skipped:
        report.notes.append((MAKER, DOCUMENT, f"not read: {note}: its rows write the ID and parity bits as '*', or the table is for other remote IDs"))
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for formal in ("AVR4311CI", "AVR4311"):
        model = MODELS[formal]
        for kind, protocol in (("sharp", SHARP), ("kaseikyo", KASEIKYO)):
            members: list[Member] = []
            if kind == "sharp":
                for row in sharp:
                    for alt in row.names:
                        if formal not in alt.models:
                            continue
                        cite = (f"{where} page {row.page} row '{alt.printed}' (system address {row.address}, extension bits {row.extension}, No. {row.number}, "
                                f"data {row.data}): Denon device {row.device} function {row.function}, each field first bit first, the extension bits above the data")
                        members.append(Member(alt.label, (row.device, row.function), {"device": row.device, "function": row.function}, cite,
                                              f"{row.address} {row.extension}"))
                        report.count(MAKER, "sharp rows read")
            else:
                for row in kaseikyo:
                    if formal not in row.models:
                        continue
                    for alt in alternatives(row.names, row.models):
                        if formal not in alt.models:
                            continue
                        cite = (f"{where} page {row.page} row '{alt.printed}' ({row.code} : {row.group}, bits 29 to 48 {row.bits}): Denon-K device {row.device} "
                                f"subdevice {row.subdevice} function {row.function}, GENRE1, GENRE2 and the data each first bit first")
                        member = Member(alt.label, (row.device, row.subdevice, row.function),
                                        {"device": row.device, "subdevice": row.subdevice, "function": row.function}, cite, row.group)
                        if row.computed_parity() != row.parity:
                            copied = copied_zone(row.device, row.subdevice, row.function, row.parity)
                            if copied is None:
                                member.doubts.append(f"the document's parity byte {row.bits[12:20]} (0x{row.parity:02X}) is not 0x{row.computed_parity():02X}, which its data "
                                                     "bits give (the exclusive or of the 3rd, 4th and 5th bytes): the data bits are read and the parity byte is not")
                                report.count(MAKER, "parity bytes that disagree")
                            else:
                                member.cite += f"; the document's parity byte is the one GENRE2 {copied} gives, not this table's (a table copied from another's)"
                                report.count(MAKER, "parity bytes of another zone's table")
                        members.append(member)
                        report.count(MAKER, "kaseikyo rows read")
            if not members:
                continue
            labels_of: dict[tuple, list[str]] = {}
            for m in members:
                labels_of.setdefault(m.code, []).append(m.label)
            seen_labels: dict[str, str] = {}
            final: list[tuple[str, dict[str, Any]]] = []
            for m in members:
                label = m.label
                if label in seen_labels and seen_labels[label] != m.table:
                    label = f"{label} ({m.table})"
                seen_labels.setdefault(label, m.table)
                if (protocol["name"], m.code) not in gate:
                    gate[(protocol["name"], m.code)] = form_compiles(IMPORT_ROOT, protocol, "K", {
                        "id": "primary.irp", "type": "irp", **m.fields, "confidence": TIER, "source": "probe"})
                if gate[(protocol["name"], m.code)]:
                    report.unrepresented[(MAKER, DOCUMENT, f"does not compile: {gate[(protocol['name'], m.code)]}")] += 1
                    continue
                doubts = list(m.doubts)
                if len(labels_of[m.code]) > 1:
                    doubts.append("the document gives this code to " + " and ".join(f"'{n}'" for n in labels_of[m.code]) + ", and a code is one function")
                final.append((label, {"id": "primary.irp", "type": "irp", **m.fields, "confidence": DOUBTFUL_TIER if doubts else TIER,
                                      "source": m.cite + ("; " + "; ".join(doubts) if doubts else "")}))
            name = model if kind == "sharp" else f"{model} (Kaseikyo)"
            while (BRAND.casefold(), name.casefold()) in authored:
                renamed = f"{name} (Denon table)"
                report.notes.append((MAKER, name, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), name.casefold())]} has the name (R15)"))
                name = renamed
            keys = {n: {"label": f[0], "forms": [f[1]]} for n, f in zip(key_names([f[0] for f in final]), final)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(name)}.json",
                        {"manufacturer": BRAND, "model": name, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(protocol), "keys": keys}))
    return out


# --- the master spreadsheet ---------------------------------------------------------------------------------------------


MASTER = "Denon_Master_IR_Hex.xls"
MASTER_HOST = "denon.com Master IR Hex"
#: sheet -> the model the file is for (the legend: ``Denon AVRxx08 Receiver IR Codes`` and ``Denon Receiver IR Codes xx07 and earlier``)
SHEETS: dict[str, str] = {
    **{f"{sheet} ID#{n}": f"AVRxx08 {model} ID#{n}" for sheet, model in (("Main Zone", "Main Zone"), ("Zone 2", "Zone 2"), ("Zone 3", "Zone 3"), ("Analog Tuner", "Tuner"),
                                                                              ("Net Audio", "Network Audio"), ("iPod", "iPod"), ("XM", "XM")) for n in range(1, 5)},
    **{f"Zone 4 ID#{n}" if n != 2 else "Zone 4 ID #2": f"AVRxx08 Zone 4 ID#{n}" for n in range(1, 5)},
    "HD Radio ID#1": "AVRxx08 HD Radio ID#1", "Sirius ID#1": "AVRxx08 Sirius ID#1",
    "RCD-N7": "RCD-N7", "CD Player": "CD Player", "DBP-1611UD": "DBP-1611UD", "BD Players": "BD Players", "S5-BD": "S5-BD", "ASD-51NW": "ASD-51NW",
    "M33 CD Receiver": "D-M33 CD Receiver", "DRA": "DRA",
    "Sharp Device 2": "AVRxx07 and earlier Sharp Device 2", "Sharp device 4": "AVRxx07 and earlier Sharp Device 4", "Sharp Device 12": "AVRxx07 and earlier Sharp Device 12",
    **{f"Denon K {k}": f"AVRxx07 and earlier Kaseikyo {k}" for k in ("4.1", "4.3", "4.5", "4.7", "7.6", "7.7", "7.8")},
}


@dataclass
class MRow:
    row: int                       # the sheet's row
    name: str
    pronto: str
    section: str                   # the heading above it (`Source Input`)
    given: tuple[str, ...] = ()    # the cells before the name: a Sharp or Kaseikyo sheet's `Device Code` and `Function#`


@dataclass(frozen=True)
class Frame:
    protocol: str                  # Denon or Denon-K
    device: int
    subdevice: int | None
    function: int
    carrier: int                   # the Pronto's frequency word


def decode_pronto(text: str) -> Frame | str:
    """The Denon (Sharp, 15 bits and its inverse) or Denon-K (Kaseikyo, 48 bits) frame of a Pronto hex, or why it is not one: a Sharp string is
    two frames of 16 pairs (a mark and a space of three marks for a 0 and seven for a 1), the second with the function inverted and the extension
    bits 3; a Kaseikyo one a leader of eight and four units and 48 bits (a space of a mark for a 0 and three for a 1) whose parity byte is the
    exclusive or of the 3rd, 4th and 5th bytes. The frequency word is the capture's and is not kept: the protocols' own carriers are."""
    try:
        w = [int(x, 16) for x in text.split()]
    except ValueError:
        return "not hex"
    if len(w) < 8 or w[0] != 0 or len(w) != 4 + 2 * (w[2] + w[3]):
        return "the Pronto header does not match the string"
    pairs = [(w[i], w[i + 1]) for i in range(4, len(w), 2)]
    if w[2] == 0 and len(pairs) in (32, 48):
        half = len(pairs) // 2
        frames = []
        for part in (pairs[:half], pairs[half:]):
            bits = [1 if space > 5 * mark else 0 for mark, space in part[:15]]
            frames.append((value(bits[:5]), value(bits[5:13]), value(bits[13:15])))
        (d1, f1, e1), (d2, f2, e2) = frames
        if len(pairs) == 32 and (d1, f1 ^ 0xFF, e1, e2) == (d2, f2, 0, 3):
            return Frame("Denon", d1, None, f1, w[1])
        return "the two frames of a Sharp string are not a function and its inverse"
    if w[2] == 0 and len(pairs) >= 50:
        lead_mark, lead_space = pairs[0]
        if lead_mark < 5 * pairs[1][0] or not 1.5 <= lead_mark / max(lead_space, 1) <= 2.5:
            return "the leader is not eight units and four"
        bits = [1 if space > 2 * mark else 0 for mark, space in pairs[1:49]]
        by = [value(bits[8 * k:8 * k + 8]) for k in range(6)]
        if (by[0], by[1], by[2] & 15) != (0x54, 0x32, 0):
            return "the vendor bytes are not 0x54 0x32 and a 0 parity nibble"
        if by[2] ^ by[3] ^ by[4] != by[5]:
            return "the parity byte is not the exclusive or of the 3rd, 4th and 5th bytes"
        return Frame("Denon-K", by[2] >> 4, by[3] & 15, (by[3] >> 4) | (by[4] << 4), w[1])
    return "neither a Sharp nor a Kaseikyo frame"


def parse_master_sheet(rows: list[list[str]]) -> list[MRow]:
    """The rows of a sheet: a row with a Pronto hex is a key named by the last cell before it, and a row with a name only (after the title and the
    column heads) is the heading of the rows below it."""
    out: list[MRow] = []
    section = ""
    for i, row in enumerate(rows, 1):
        cells = [c.strip() for c in row]
        at = next((j for j, c in enumerate(cells) if c.startswith("0000 ")), None)
        if at is None:
            filled = [c for c in cells[:3] if c]
            if i > 2 and len(filled) == 1 and cells[0] and cells[0] != "Legend":
                section = " ".join(cells[0].split())
            continue
        names = [c for c in cells[:at] if c]
        if names:
            label = " ".join(names[-1].split())
            out.append(MRow(i, label[:-2] if re.fullmatch(r"\d+\.0", label) else label, cells[at], section, tuple(names[:-1])))
    return out


def build_master(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    entry = snapshot.documents[MASTER]
    sha = snapshot.sha(MASTER)
    when = f"retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']}"
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for sheet, model in SHEETS.items():
        rows = parse_master_sheet(snapshot.sheet_of(MASTER, sheet))
        by_protocol: dict[str, list[tuple[MRow, Frame]]] = {"Denon": [], "Denon-K": []}
        for row in rows:
            report.count(MAKER, f"master: strings")
            frame = decode_pronto(row.pronto)
            if isinstance(frame, str):
                report.unrepresented[(MAKER, f"{MASTER} sheet '{sheet}'", frame)] += 1
                continue
            by_protocol[frame.protocol].append((row, frame))
        for protocol, members in by_protocol.items():
            if not members:
                continue
            twins: dict[tuple, list[str]] = {}
            for row, frame in members:
                twins.setdefault((frame.device, frame.subdevice, frame.function), []).append(row.name)
            keys_in: list[tuple[str, dict[str, Any]]] = []
            for row, frame in members:
                fields = {"device": frame.device, **({"subdevice": frame.subdevice} if frame.subdevice is not None else {}), "function": frame.function}
                proto = SHARP if protocol == "Denon" else KASEIKYO
                code = (protocol, frame.device, frame.subdevice, frame.function)
                if code not in gate:
                    gate[code] = form_compiles(IMPORT_ROOT, proto, "K", {"id": "primary.irp", "type": "irp", **fields, "confidence": TIER, "source": "probe"})
                if gate[code]:
                    report.unrepresented[(MAKER, f"{MASTER} sheet '{sheet}'", f"does not compile: {gate[code]}")] += 1
                    continue
                shown = f"{protocol} device {frame.device}" + (f" subdevice {frame.subdevice}" if frame.subdevice is not None else "") + f" function {frame.function}"
                cite = (f"{MASTER_HOST}/{MASTER}@{sha} ({when}) sheet '{sheet}' row {row.row} '{row.name}' (Pronto hex, frequency word 0x{frame.carrier:04X}"
                        + (f", section '{row.section}'" if row.section else "") + f"): {shown}, decoded from the hex")
                doubts = []
                if len(twins[(frame.device, frame.subdevice, frame.function)]) > 1:
                    doubts.append("the sheet gives this code to " + " and ".join(f"'{n}'" for n in twins[(frame.device, frame.subdevice, frame.function)]) + ", and a code is one function")
                if len(row.given) >= 2 and sheet.startswith(("Sharp", "Denon K")):
                    try:
                        device_code, function_no = float(row.given[0]), int(float(row.given[1]))
                    except ValueError:
                        device_code, function_no = -1.0, -1
                    stated = f"{frame.device}.{frame.subdevice}" if frame.subdevice is not None else f"{frame.device}"
                    if function_no != frame.function or f"{device_code:g}" != stated:
                        doubts.append(f"the sheet's own Device Code {row.given[0]} and Function# {row.given[1]} are not the hex's {stated} and {frame.function}")
                    else:
                        report.count(MAKER, "master: rows whose Device Code and Function# the hex agrees with")
                if doubts:
                    cite += "; " + "; ".join(doubts)
                keys_in.append((row.name, {"id": "primary.irp", "type": "irp", **fields, "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
            if not keys_in:
                continue
            name = model if protocol == "Denon" else f"{model} (Kaseikyo)"
            while (BRAND.casefold(), name.casefold()) in authored:
                renamed = f"{name} (Denon table)"
                report.notes.append((MAKER, name, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), name.casefold())]} has the name (R15)"))
                name = renamed
            keys = {n: {"label": f[0], "forms": [f[1]]} for n, f in zip(key_names([f[0] for f in keys_in]), keys_in)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(name)}.json",
                        {"manufacturer": BRAND, "model": name, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(SHARP if protocol == "Denon" else KASEIKYO), "keys": keys}))
    return out


# --- the X-series word documents ----------------------------------------------------------------------------------------

DOC_HOST = "snapav.com Denon IR code documents"
REGIONS = ("NA", "EU", "CHI", "JP")


@dataclass(frozen=True)
class DocDocument:
    file: str
    models: tuple[str, ...]        # the products the title names: a file for each of the two formats (``AVR-X2700H``, ``AVR-X2700H (Kaseikyo)``)


DOC_DOCUMENTS: tuple[DocDocument, ...] = (
    DocDocument("AVR-X2700H_S960H_IR_CODE_V01.doc", ("AVR-X2700H", "AVR-X2700H DAB", "AVR-S960H")),
    DocDocument("AVR-X3700H_AVC-X3700H_IR_CODE_V01_04062020.doc", ("AVR-X3700H", "AVC-X3700H")),
    DocDocument("AVR-X4700H_AVC-X4700H_IR_CODE_V01.doc", ("AVR-X4700H", "AVC-X4700H")),
    DocDocument("AVR-X6700H_AVC-X6700H_IR_CODE_V01.doc", ("AVR-X6700H", "AVC-X6700H")),
    DocDocument("avrx1000_e300_ir_code_v01.doc", ("AVR-X1000", "AVR-E300")),                      # §95: live files of Denon's own host
    DocDocument("avr-x2500h_s940h_ir_code_v01-02072018.doc", ("AVR-X2500H", "AVR-S940H")),
)
_DOC_ADDRESS = re.compile(r"SYSTEM ADDRESS ?\(C1[~\u301c]C5\) : ([01]{5}) , EXTENSION BIT ?\(C12,C13\) : ([01]{2})")
_DOC_MARK = re.compile(r"\s*\u203b+\s*([^\u203b]*?)\s+only\b|\s*\(([^()]*?)\s+only\)", re.I)
_DOC_VARIANT = re.compile(r"\s*\((AVR-\w+ DAB)\)")
_DOC_REGIONS = re.compile(r"\(([A-Z/]+)\)")


def marks_of(text: str, models: tuple[str, ...]) -> tuple[str, frozenset[str]]:
    """``(the label, the models it is for)`` of one printed name of an X-series list. **A mark that names models restricts the key to them** (``\u203bAVR only``: the
    AVR-X4700H and not the AVC-X4700H, which has no tuner; ``\u203bX2700H only``: the AVR-X2700H and its DAB; ``(AVR-X2700H DAB only)``: the DAB; ``(AVR-X2700H/S960H EU
    only)``: those two and the DAB, which is an X2700H), **and a region stays in the label where the mark was**: ``PTY \u203bEU only`` is ``PTY (EU only)``, ``MEMORY
    \u203bAVR(NA/CHI/JP) only`` is ``MEMORY (NA/CHI/JP only)``. A name is for every model when it has no mark."""
    kept = set(models)

    def one(found: re.Match) -> str:
        nonlocal kept
        what = found.group(1) if found.group(1) is not None else found.group(2)
        regions: list[str] = []
        listed = _DOC_REGIONS.search(what)
        if listed:                                            # ``AVR(NA/CHI/JP)``: the family, and its regions
            regions += listed.group(1).split("/")
            what = what[:listed.start()] + what[listed.end():]
        tail = re.search(r"\s+(" + "|".join(REGIONS) + r")$", what)
        if tail:                                              # ``AVR-X2700H/S960H EU``
            regions.append(tail.group(1))
            what = what[:tail.start()]
        refs = [r.strip() for r in what.split("/") if r.strip()]
        for ref in list(refs):
            if ref in REGIONS:
                regions.append(ref)
                refs.remove(ref)
        if refs:
            kept &= {m for m in models if any(ref in m for ref in refs)}
        return " (" + "/".join(dict.fromkeys(regions)) + " only)" if regions else ""

    def variant(found: re.Match) -> str:
        nonlocal kept
        kept &= {m for m in models if found.group(1) in m}
        return ""

    rest = _DOC_VARIANT.sub(variant, text)
    rest = _DOC_MARK.sub(one, rest)
    label = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", " ".join(rest.split())))
    return label, frozenset(kept)


def whole_parenthesis(text: str) -> bool:
    """Whether the whole of ``text`` is one parenthesis: ``(RDS SEARCH \u203bEU only)`` is, ``(FM/AM)TUNING UP`` and ``(DAB) STATION UP (... only)`` are not."""
    depth = 0
    for i, ch in enumerate(text):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and i < len(text) - 1:
            return False
    return text.startswith("(") and depth == 0


def doc_names(parts: list[str], models: tuple[str, ...]) -> list[tuple[str, frozenset[str]]]:
    """The names of a key's cell and the models each is for. **A paragraph that is only a mark (``(AVR-X2700H DAB only)``) or only a parenthesis (``(RDS SEARCH \u203bEU only)``
    under ``DIRECT SEARCH``: the lists of 2013 and 2018), begins with ``/`` or follows one that ends with ``/`` goes on the paragraph before** (``DIRECT SEARCH/`` and ``RDS
    SEARCH (AVR-X2700H/S960H EU only)`` are one name for the markets); **another paragraph is another name** (``(FM/AM)TUNING UP`` and ``(DAB) STATION UP (AVR-X2700H DAB
    only)``), and a name with no mark of models is for the models the others are not for."""
    joined: list[str] = []
    for part in parts:
        if joined and (joined[-1].endswith("/") or part.startswith("/") or re.fullmatch(r"\([^()]*only\)|\(AVR-\w+ DAB\)", part) or whole_parenthesis(part)):
            joined[-1] += ("" if joined[-1].endswith("/") or part.startswith("/") else " ") + part
        else:
            joined.append(part)
    names = [marks_of(text, models) for text in joined]
    if len(names) > 1:
        taken = frozenset().union(*(kept for _, kept in names if len(kept) < len(models)))
        names = [(label, kept if len(kept) < len(models) or not taken or set(models) == taken else frozenset(models) - taken) for label, kept in names]
    return names


@dataclass
class DocRow:
    kind: str                      # ``sharp`` or ``kaseikyo``
    ident: str                     # ``RCSHP0230016``
    table: str                     # Sharp: ``01000 11`` (the address and the extension bits); Kaseikyo: ``K4-7``
    group: str                     # a Kaseikyo group's title as written (``MAIN ZONE``, ``ANALOG TUNER \u203bAVR (NA/CHI) only``); empty for Sharp
    printed: str                   # the name cell as written: its paragraphs with `` | `` between them
    names: list[tuple[str, frozenset[str]]]       # the names it gives and the models each is for (a group's title taken in)
    bits: str                      # Sharp: the data (C6 to C11); Kaseikyo: bits 29 to 38
    device: int
    subdevice: int | None
    function: int
    number: int                    # the row's number its identifier gives
    note: str = ""                 # what the citation says besides the row (a header that is not the table's)
    page: int = 0                  # the page of a PDF the row is on (0: a Word document, whose rows are cited by their identifiers)
    how: str = ""                  # a PDF's row's citation, from its bits on (the list's rows have no identifier of their own)
    doubt: str = ""                # what makes the key Untested besides a number that is not its bits' value


@dataclass
class Group:
    printed: str                   # the title as written
    title: str                     # its label: a mark removed
    models: frozenset[str]
    genre1: int                    # the header's
    genre2: int
    table: tuple[int, int] | None = None       # the rows' identifiers'


def doc_cells(rows: list[list[str]]) -> list[tuple[str, str]]:
    return [(nf(text).strip(), end) for text, end in rows]


def parse_doc(rows: list[list[str]], models: tuple[str, ...]) -> list[DocRow]:
    """The key rows of a document's ``paragraphs`` in order. A Sharp table has the line ``SYSTEM ADDRESS(C1~C5) : 01000 , EXTENSION BIT(C12,C13) : 11`` and rows of two
    ``RCSHP``, 6 data bits and a name (empty for a code with none); a Kaseikyo group has a title, a ``REMOTE ID SET: 1`` line, the 28 bits of the header (the vendor, the parity
    nibble, ``GENRE1`` and ``GENRE2``, after the numbers 1 to 28) and rows of one ``RCKSK``, bits 29 to 38, ten ``*`` (the ID and the parity) and a name. A row whose identifier
    is not its table's, or whose bits are not 0 and 1, is an error; **a row whose number is not its data's value is kept and its key is Untested**. **A group's table is its
    rows' identifiers' (K4-5), which the document's table of remote IDs agrees with** (its first block lists the groups' tables in order): a header that gives another
    ``GENRE2`` (the X4700H's and X6700H's ``ZONE3`` has the ``ZONE2``'s, 3) is a slip of the header, which the rows' citations say."""
    cells = doc_cells(rows)
    out: list[DocRow] = []
    address: tuple[str, str] | None = None
    group: Group | None = None
    groups: list[Group] = []
    i = 0

    def name_at(j: int) -> tuple[list[str], int]:
        parts: list[str] = []
        while j < len(cells):
            text, end = cells[j]
            if text:
                parts.append(text)
            j += 1
            if end == "cell":
                break
        return parts, j

    while i < len(cells):
        text, _ = cells[i]
        if m := _DOC_ADDRESS.search(text):
            address, group = (m.group(1), m.group(2)), None
            i += 1
        elif text == "REMOTE ID SET: 1":
            title = next((t for t, _ in reversed(cells[:i]) if t), "")
            numbers = next(j for j in range(i, len(cells)) if cells[j][0] == "28")
            header = "".join(t for t, _ in cells[numbers + 1:numbers + 30] if t)
            if not re.fullmatch(r"[01]{28}", header):
                raise ValueError(f"cell {i}: the header of the group {title!r} is {header!r}, not 28 bits")
            if header[:16] != VENDOR or header[16:20] != "0000":
                raise ValueError(f"cell {i}: the group {title!r} has no header of the vendor 0x54 0x32 and a 0 parity nibble")
            label, kept = marks_of(title, models)
            group, address = Group(title, label, kept, value(header[20:24]), value(header[24:28])), None
            groups.append(group)
            i = numbers + 30
        elif m := re.fullmatch(r"RCSHP([0-9A-F]{2})(\d)(\d{4})", text):
            if address is None:
                raise ValueError(f"cell {i}: the row {text} is in no table of an address")
            data = cells[i + 1][0]
            if not re.fullmatch(r"[01]{6}", data):
                raise ValueError(f"cell {i}: the row {text} has data {data!r}")
            if (int(m.group(1), 16), int(m.group(2))) != (value(address[0]), value(address[1])):
                raise ValueError(f"cell {i}: the row {text} is not of the table of address {address[0]} and extension bits {address[1]}")
            parts, i = name_at(i + 2)
            out.append(DocRow("sharp", text, f"{address[0]} {address[1]}", "", " | ".join(parts), doc_names(parts, models) if parts else [], data, value(address[0]), None,
                              value(data) + 64 * value(address[1]), int(m.group(3)) % 64))
        elif m := re.fullmatch(r"RCKSK0(\d)(\d)(\d{4})", text):
            if group is None:
                raise ValueError(f"cell {i}: the row {text} is in no group with a header")
            table = (int(m.group(1)), int(m.group(2)))
            if group.table is None:
                group.table = table
            if table != group.table or table[0] != group.genre1:
                raise ValueError(f"cell {i}: the row {text} is not of the table K{group.table[0]}-{group.table[1]} of the group {group.printed!r} (header: GENRE1 {group.genre1})")
            first = [cells[i + k][0] for k in range(1, 15) if i + k < len(cells)]
            skip = 0
            if len(first) == 14 and first[:4] == [""] * 4 and all(re.fullmatch(r"[01]", t) for t in first[4:]) and [cells[i + k][0] for k in range(15, 25) if i + k < len(cells)] == ["*"] * 10:
                skip = 4                                                    # the lists of 2013 to 2018 have four cells for the GENRE2 before each row's data, empty but in a group's first row
            elif len(first) == 14 and all(re.fullmatch(r"[01]", t) for t in first) and [cells[i + k][0] for k in range(15, 25) if i + k < len(cells)] == ["*"] * 10:
                if value("".join(first[:4])) != table[1]:                    # the first row of a group has its GENRE2 there
                    raise ValueError(f"cell {i}: the row {text} has the GENRE2 bits {''.join(first[:4])}, not its identifier's")
                skip = 4
            bits = "".join(cells[i + skip + k][0] for k in range(1, 11))
            if not re.fullmatch(r"[01]{10}", bits) or [cells[i + skip + k][0] for k in range(11, 21)] != ["*"] * 10:
                raise ValueError(f"cell {i}: the row {text} does not have 10 bits and 10 '*'")
            parts, i = name_at(i + skip + 21)
            note = (f"the group's header gives GENRE2 {group.genre2}, not the rows' {table[1]} (the header of the group before, copied): its rows' identifiers and the document's "
                    "table of remote IDs say K%d-%d" % table) if group.genre2 != table[1] else ""
            names = [(label, kept & group.models) for label, kept in doc_names(parts, models)] if parts else []
            out.append(DocRow("kaseikyo", text, f"K{table[0]}-{table[1]}", group.printed, " | ".join(parts), names, bits, table[0], table[1], value(bits), int(m.group(3)), note))
        else:
            i += 1
    if groups:         # the document's table of remote IDs: its first block (ID 1) has the tag of each group, in the order of the groups
        start = next(j for j, (t, _) in enumerate(cells) if t.startswith("REMOTE ID SETTING"))
        tags = [(int(m.group(1)), int(m.group(2))) for t, _ in cells[start:] if (m := re.fullmatch(r"(?:SHARP&)?K(\d)-(\d)", t))][:len(groups)]
        if tags != [g.table for g in groups]:
            raise ValueError(f"the groups' tables {[g.table for g in groups]} are not the tags of the document's table of remote IDs {tags}")
    return out


def emit_rows(file: str, where: str, rows: list[DocRow], models: tuple[str, ...], authored: dict[tuple[str, str], str], report: Report,
              gate: dict[tuple, str | None], aliases: dict[str, tuple[str, ...]] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """The files of a document's rows: a Sharp and a Kaseikyo file for each model, the keys of the rows (and names) that are for it."""
    out: list[tuple[str, dict[str, Any]]] = []
    report.count(MAKER, f"{file}: key rows read", len(rows))
    report.count(MAKER, f"{file}: rows left unnamed (a code the list leaves free: no key)", sum(1 for r in rows if not r.names))
    for model in models:
        for kind, protocol in (("sharp", SHARP), ("kaseikyo", KASEIKYO)):
            members: list[Member] = []
            again: dict[tuple, list[str]] = {}
            for row in rows:
                if row.kind != kind:
                    continue
                for label, kept in row.names:
                    if model not in kept:
                        continue
                    if kind == "sharp":
                        code, fields = (row.device, row.function), {"device": row.device, "function": row.function}
                        agrees = value(row.bits) == row.number
                        if row.how:
                            cite = f"{where} page {row.page} table row '{row.ident}' ('{row.printed}', {row.how}"
                        else:
                            cite = (f"{where} row '{row.ident}' ('{row.printed}', system address {row.table.split()[0]}, extension bits {row.table.split()[1]}, data {row.bits}): "
                                    f"Denon device {row.device} function {row.function}, each field first bit first, the extension bits above the data")
                    else:
                        code, fields = (row.device, row.subdevice, row.function), {"device": row.device, "subdevice": row.subdevice, "function": row.function}
                        agrees = row.function == row.number
                        if row.how:
                            cite = f"{where} page {row.page} table row '{row.ident}' ('{row.printed}', {row.how}"
                        else:
                            cite = (f"{where} row '{row.ident}' ('{row.printed}', {row.table} : {row.group}, bits 29 to 38 {row.bits}, the ID and parity bits '*'): Denon-K device "
                                    f"{row.device} subdevice {row.subdevice} function {row.function}, GENRE1, GENRE2 and the data each first bit first, the ID bits 0 as for remote ID 1")
                        if row.note:
                            cite += "; " + row.note
                    if (protocol["name"], code) not in gate:
                        gate[(protocol["name"], code)] = form_compiles(IMPORT_ROOT, protocol, "K", {"id": "primary.irp", "type": "irp", **fields, "confidence": TIER, "source": "probe"})
                    if gate[(protocol["name"], code)]:
                        report.unrepresented[(MAKER, file, f"does not compile: {gate[(protocol['name'], code)]}")] += 1
                        continue
                    if row.how and row.group:                                  # a PDF's group title (``ZONE3 (Except AVR991)``): its marks are the models' (§94)
                        from .denon_pdf import pdf_marks
                        table = pdf_marks(row.group, models, {})[0]
                    else:
                        table = re.sub(r"\s*\([A-Z/]+ only\)$", "", marks_of(row.group, models)[0]) if row.group else row.table
                    member = Member(label, code, fields, cite, table)
                    if not agrees:
                        member.doubts.append(f"the row's identifier gives the number {row.number} and its bits the value {value(row.bits) if kind == 'sharp' else row.function}, "
                                             "which are not one function: the bits are read and the number is not")
                        if model == models[0]:
                            report.count(MAKER, "rows whose number and bits disagree")
                    if row.doubt:
                        member.doubts.append(row.doubt)
                        if model == models[0]:
                            report.count(MAKER, "rows with a doubt of their own")
                    again.setdefault((code, label), []).append(row.ident)
                    if len(again[(code, label)]) > 1:
                        first = next(m for m in members if (m.code, m.label) == (code, label))
                        first.cite += f"; the document gives the same name to the same code again in the row '{row.ident}'" + (f" of the group '{row.group}'" if row.group else "")
                        continue
                    members.append(member)
                    report.count(MAKER, f"{kind} rows read")
            if not members:
                continue
            labels_of: dict[tuple, list[str]] = {}
            for m in members:
                labels_of.setdefault(m.code, []).append(m.label)
            seen_labels: dict[str, str] = {}
            final: list[tuple[str, dict[str, Any]]] = []
            for m in members:
                label = m.label
                if label in seen_labels and seen_labels[label] != m.table:
                    label = f"{label} ({m.table})"
                seen_labels.setdefault(label, m.table)
                doubts = list(m.doubts)
                if len(labels_of[m.code]) > 1:
                    doubts.append("the document gives this code to " + " and ".join(f"'{n}'" for n in labels_of[m.code]) + ", and a code is one function")
                final.append((label, {"id": "primary.irp", "type": "irp", **m.fields, "confidence": DOUBTFUL_TIER if doubts else TIER,
                                      "source": m.cite + ("; " + "; ".join(doubts) if doubts else "")}))
            name = model if kind == "sharp" else f"{model} (Kaseikyo)"
            while (BRAND.casefold(), name.casefold()) in authored:
                renamed = f"{name} (Denon table)"
                report.notes.append((MAKER, name, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), name.casefold())]} has the name (R15)"))
                name = renamed
            keys = {n: {"label": f[0], "forms": [f[1]]} for n, f in zip(key_names([f[0] for f in final]), final)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            # a name is claimed by one file of the maker (R15): the model's other names are the Sharp file's aliases (and every file's controls), unless the ledger has the name
            named = [a for a in (aliases or {}).get(model, ()) if kind == "sharp" and (BRAND.casefold(), a.casefold()) not in authored]
            for a in (aliases or {}).get(model, ()):
                if kind == "sharp" and a not in named:
                    report.notes.append((MAKER, a, f"kept as a control of '{name}' and no alias: the ledger's {authored[(BRAND.casefold(), a.casefold())]} has the name (R15)"))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(name)}.json",
                        {"manufacturer": BRAND, "model": name, "aliases": named, "controls": [f"{BRAND} {n}" for n in (model, *(aliases or {}).get(model, ()))],
                         "protocol": dict(protocol), "keys": keys}))
    return out


def build_docs(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for document in DOC_DOCUMENTS:
        rows = parse_doc(snapshot.sheet_of(document.file, "paragraphs"), document.models)
        entry, sha = snapshot.documents[document.file], snapshot.sha(document.file)
        if "snapshot" in entry:
            where = f"{DOC_HOST}/{document.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']})"
        else:                                                                    # a live file of Denon's own host (§95)
            where = f"assets.denon.com IR code documents/{document.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)})"
        out += emit_rows(document.file, where, rows, document.models, authored, report, gate)
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    from . import denon_pdf

    return build_pdf(snapshot, authored, report) + build_master(snapshot, authored, report) + build_docs(snapshot, authored, report) + denon_pdf.build_pdfs(snapshot, authored, report)
