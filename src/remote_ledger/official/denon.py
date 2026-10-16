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


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
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
                            member.doubts.append(f"the document's parity byte {row.bits[12:20]} (0x{row.parity:02X}) is not 0x{row.computed_parity():02X}, which its data "
                                                 "bits give (the exclusive or of the 3rd, 4th and 5th bytes): the data bits are read and the parity byte is not")
                            report.count(MAKER, "parity bytes that disagree")
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
