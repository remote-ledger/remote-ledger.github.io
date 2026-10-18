"""Denon's per-model remote code lists of 2003 to 2013, the PDFs (DESIGN section 94).

Denon wrote for each receiver (and some players) a ``Remote Code List`` of its remote's codes in two formats: **Sharp** (15 bits: the ledger's ``Denon``) and
**Kaseikyo** (48 bits: ``Denon-K``), the two of §81's AVR-4311 list and §90's X-series lists. These PDFs differ in layout from document to document and from the
4311's (``denon.parse_sharp`` and ``denon.parse_kaseikyo`` read that one by fixed places), so this reader finds the places in each page. The text of a PDF is
converted to CSV by ``tools/official_sheets_to_csv.py`` (``words``: every word of every page by its place, and the rows of its ruled tables).

**Sharp.** A table has the line ``SYSTEM ADDRESS(C1~C5):01000,EXTENSION BIT(C12,C13):11`` (the same line is often written twice) and rows of two halves, ``No.`` (0 to
63), ``Data (C6~C11)`` (six bits, first bit first) and ``Key Name`` (empty for a code with none; a name that goes on over a second line has it under it). The tables of one
address and extension, which a page splits in two of 32 rows, are one: **all 64 rows are read, and each row's data must be its number's bits**.

**Kaseikyo.** A group has a header of 24 bits (the vendor 0x54 0x32, a 0 parity nibble, ``GENRE1``), then rows of 20 bits (bits 29 to 48: the function and its remote ID,
and the parity byte) and a name, **the first row of a group with 4 more bits first (``GENRE2``)**. A group with the line ``REMOTE ID SET : 1`` and one with no such line
are the codes of the default remote ID; **the groups for the other IDs (``2-4``, ``1-4``) write the ID and the parity as ``*`` and are not read**, as §81's were not. A
row's parity byte is the exclusive or of the 3rd, 4th and 5th bytes, which the reader checks: **a row whose parity does not agree is Untested**.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .denon import VENDOR, DocRow, Group, copied_zone, doc_names, marks_of, nf, value

NAME_LINE = 24                     # the most a name's next line is below the line of its row (the rows are 12 to 19 points apart)
SATELLITE = 7                      # the GENRE1 of the XM and Sirius tuners (``K7-5``)
MIN_TUNER_ROWS = 5                 # the rows of a list that have GENRE1 7 parity before they are read as the satellite tuners (one is chance)
_ADDRESS = re.compile(r"SYSTEM ADDRESS ?\(C1[~〜]C5\) ?: ?([01]{5}) ?, ?EXTENSION BIT ?\(C12, ?C13\) ?: ?([01]{2})")
_BIT = re.compile(r"^[01]$")
_PAGE_NOTE = re.compile(r"\d{4}/\d{1,2}/\d{1,2}|\d{1,3}/\d{1,3}")


def tidy(text: str) -> str:
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", " ".join(text.split())))


def lines_of(words: list[tuple[int, int, str]]) -> list[str]:
    """The text of ``(y, x, word)`` words by line: each line's words left to right, the lines top to bottom."""
    by_line: dict[int, list[tuple[int, str]]] = {}
    for y, x, word in words:
        by_line.setdefault(y, []).append((x, word))
    return [" ".join(w for _, w in sorted(by_line[y])) for y in sorted(by_line)]


@dataclass
class Cell:
    page: int
    number: int
    data: str
    words: list[tuple[int, int, str]] = field(default_factory=list)        # the key name's words, ``(y, x, word)``: a name of two lines has its first above the row's
    printed: str = ""               # the header the document printed over the cell where the reader corrects it (``01000 01``), else ``""``

    @property
    def lines(self) -> list[str]:
        return lines_of(self.words)

    @property
    def name(self) -> str:
        return tidy(" ".join(self.lines))


@dataclass
class SharpTable:
    address: str
    extension: str
    cells: dict[int, Cell]

    @property
    def device(self) -> int:
        return value(self.address)

    def function(self, number: int) -> int:
        return value(self.cells[number].data) + 64 * value(self.extension)


def page_lines(rows: list[list[str]]) -> dict[int, list[tuple[int, list[tuple[int, str]]]]]:
    """``{page: [(y, [(x, word)])]}`` of a ``words`` CSV (the ruled tables' rows ``page, R, y0, y1`` are not lines)."""
    pages: dict[int, list[tuple[int, list[tuple[int, str]]]]] = {}
    for row in rows:
        if row[1] == "R":
            continue
        pages.setdefault(int(row[0]), []).append((int(row[1]), [(int(row[i]), nf(row[i + 1])) for i in range(2, len(row), 2)]))
    return pages


def page_spans(rows: list[list[str]]) -> dict[int, list[tuple[int, int]]]:
    """``{page: [(y0, y1)]}``: the ruled rows of a ``words`` CSV (``page, R, y0, y1``), which say which lines a row's cells hold."""
    spans: dict[int, list[tuple[int, int]]] = {}
    for row in rows:
        if row[1] == "R":
            spans.setdefault(int(row[0]), []).append((int(row[2]), int(row[3])))
    return spans


def owner_line(y: int, row_ys: list[int], spans: list[tuple[int, int]]) -> int | None:
    """The line of a row that a line of words with no row of its own goes on: the nearest row line in the same ruled row (a name's first line is above its row's, the rest
    below), and none for a line outside the ruled rows of a page that has them (a footer: ``DENON AVR-4806 RC CODE``). A page with no ruled rows goes by distance: the row line
    just above, no more than ``NAME_LINE`` points."""
    if spans:
        for y0, y1 in spans:
            if y0 - 2 <= y < y1:
                inside = [r for r in row_ys if y0 - 2 <= r < y1]
                return min(inside, key=lambda r: abs(r - y)) if inside else None
        return None
    above = [r for r in row_ys if 0 <= y - r <= NAME_LINE]
    return above[-1] if above else None


def sharp_tables(rows: list[list[str]], fixes: tuple[tuple[int, str, str, str, str], ...] = ()) -> list[SharpTable]:
    """The Sharp tables of a document, one for each address and extension: the rows of every block that has that header, by number. A block is the header line and the
    rows after it; a row is a number and six data bits and a name in the words up to the next number and data; a line of words with no number and data goes on the name of
    the cell it is under. ``fixes`` are the headers a document misprints, ``(page, address, extension, address it means, extension it means)``: the cells under one say so."""
    tables: dict[tuple[str, str], SharpTable] = {}
    corrected = {(page, a, e): (ra, re_) for page, a, e, ra, re_ in fixes}
    spans = page_spans(rows)
    printed = ""
    table: SharpTable | None = None                                       # a table goes on over the page's end: its last rows are the next page's first lines
    block = 0                                                             # which header's rows a line is of: a line is never a name of the rows of another table's
    for page, lines in sorted(page_lines(rows).items()):
        row_lines: list[tuple[int, int, list[tuple[int, Cell]]]] = []     # (y, block, [(x of the number, cell)])
        loose: list[tuple[int, int, list[tuple[int, str]]]] = []          # the lines of words that are no row
        for y, words in lines:
            m = _ADDRESS.search(re.sub(r"\s+", " ", " ".join(w for _, w in words)))
            if m:
                key = (m.group(1), m.group(2))
                printed = ""
                if (page, *key) in corrected:
                    printed, key = f"{key[0]} {key[1]}", corrected[(page, *key)]
                table = tables.setdefault(key, SharpTable(key[0], key[1], {}))
                block += 1
                continue
            if table is None:
                continue
            tokens = sorted(words)
            starts = [i for i in range(len(tokens) - 1) if re.fullmatch(r"\d{1,2}", tokens[i][1]) and re.fullmatch(r"[01]{6}", tokens[i + 1][1])]
            if not starts:
                loose.append((y, block, tokens))
                continue
            cells: list[tuple[int, Cell]] = []
            for k, i in enumerate(starts):
                end = starts[k + 1] if k + 1 < len(starts) else len(tokens)
                number, data = int(tokens[i][1]), tokens[i + 1][1]
                if value(data) != number:
                    raise ValueError(f"page {page}: the row {number} has data {data}, whose value is not its number")
                if number in table.cells:
                    raise ValueError(f"page {page}: the row {number} of the table of address {table.address} and extension {table.extension} is there twice")
                cell = Cell(page, number, data, [(y, x, w) for x, w in tokens[i + 2:end]], printed)
                table.cells[number] = cell
                cells.append((tokens[i][0], cell))
            row_lines.append((y, block, cells))
        for y, b, tokens in loose:                                      # the rest of a name: with the cell its place is in, of the row it is in
            if tokens[0][1].startswith("\u203b"):                          # a footnote (``\u203b1:Except AVR990``) is no name
                continue
            same = [r for r in row_lines if r[1] == b]
            owner = owner_line(y, [r[0] for r in same], spans.get(page, []))
            if owner is None:
                continue
            cells = next(r[2] for r in same if r[0] == owner)
            for x, word in tokens:
                under = [cell for cx, cell in cells if x >= cx - 2]
                if under and not re.fullmatch(r"\d+", word):
                    under[-1].words.append((y, x, word))
    return list(tables.values())


@dataclass
class KRow:
    page: int
    group: str                      # the group's title (``MAIN ZONE``)
    table: str                      # ``K4-1``
    device: int
    subdevice: int
    bits: str                       # bits 29 to 48
    names: list[str] = field(default_factory=list)
    number: int | None = None       # the row's ``No.`` where the table has that column (the value of its ten data bits)

    @property
    def function(self) -> int:
        return value(self.bits[:12])

    @property
    def parity(self) -> int:
        return value(self.bits[12:20])

    def computed_parity(self, subdevice: int | None = None, device: int | None = None) -> int:
        f = self.function
        return ((self.device if device is None else device) << 4) ^ (self.subdevice if subdevice is None else subdevice) ^ ((f << 4) & 0xFF) ^ ((f >> 4) & 0xFF)

    @property
    def parity_says(self) -> str:
        return self.says()

    def says(self, tuner: bool = True) -> str:
        """``agrees``; ``main``: the document's parity byte is the one another zone's GENRE2 (1, 3, 5 or 7) gives, which the zones' tables copy; ``tuner``: it is the one
        GENRE1 7 (the satellite tuners') gives for this GENRE2, in a table that is under GENRE1 4 (the AVR-2807's XM channel inputs; ``tuner=False`` where a list has too few
        such rows, which is chance: 1 in 256 of the rows that agree with nothing); ``disagrees``."""
        if self.parity == self.computed_parity():
            return "agrees"
        if copied_zone(self.device, self.subdevice, self.function, self.parity) is not None:
            return "main"
        if tuner and self.device != SATELLITE and self.parity == self.computed_parity(device=SATELLITE):
            return "tuner"
        return "disagrees"

    @property
    def name(self) -> str:
        return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", " ".join(" ".join(self.names).split())))


def _title_of(lines: list[tuple[int, list[tuple[int, str]]]], at: int) -> str:
    """The title of a group whose header (``DENON CODE``) is the line ``at``: the nearest line above it that is not numbers."""
    for j in range(at - 1, max(at - 4, -1), -1):
        text = " ".join(w for _, w in lines[j][1])
        if text and not re.fullmatch(r"[\d /:~.-]+", text) and not text.startswith(("DENON", "ＤＥＮＯＮ", "FORMAT", "DATA CONSTRUCTION")) and not any(w in ("0", "1") for _, w in lines[j][1]):
            return text
    return ""


_HEADER_25 = "".join(str(n) for n in range(25, 49))
_HEADER_29 = "".join(str(n) for n in range(29, 49))


def bit_header(words: list[tuple[int, str]]) -> tuple[int, int, int | None] | None:
    """``(cut, data_x, number_x)`` where a line is the numbers of the bit columns, ``25 26 ... 48`` (the GENRE2 column and the rows' 20 bits) or ``29 30 ... 48`` (the 20
    bits alone), after an optional ``No.``: the place right of the last column (where the names begin), the place left of which a bit is a GENRE2 bit, and the place left of
    which a number is the row's ``No.``. Some lists print the numbers of several columns as one word (``2930``)."""
    ws = list(words)
    if ws and ws[0][1] == "No.":
        ws = ws[1:]
    digits = "".join(w for _, w in ws)
    if not ws or ws[-1][1] != "48" or digits not in (_HEADER_25, _HEADER_29):
        return None
    first = ws[0][0]
    data_x = next(x for x, w in ws if w.startswith("29")) - 8 if digits == _HEADER_25 else first - 8
    return ws[-1][0] + 8, data_x, first - 10 if words[0][1] == "No." else None


def kaseikyo_rows(rows: list[list[str]]) -> tuple[list[KRow], list[str]]:
    """``(the rows of the groups for the default remote ID, what is not read)``. A group starts at its header (the line ``DENON CODE`` and 24 bits at the left of the page),
    may have ``REMOTE ID SET : n`` and ``K4-1 : MAIN ZONE`` lines, and goes on over the pages until the next header or ID line; its rows are lines whose words left of the
    bits' last column (the place of the ``48`` of the line ``25 26 ... 48``) are bits and whose words right of it are the name."""
    out: list[KRow] = []
    skipped: list[str] = []
    state: dict = {"table": None}          # the group the rows are being read for: persists over the pages
    building: list | None = None
    pending: list[str] = []

    def close_row(table: dict, tokens: list[tuple[int, str]], names: list[str], page: int, with_genre2: bool, number: int | None) -> None:
        bits = "".join(t for _, t in sorted(tokens))
        if with_genre2:                  # a row of a column of rows that has its own GENRE2 (a cell that is not merged with the one above)
            table["genre2"] = value(bits[:4])
            bits = bits[4:]
        if table["genre2"] is None and table["code"]:             # a list whose rows have no GENRE2 column: the group's code (``K4-1``) says it
            table["genre2"] = int(table["code"].split("-")[1], 16)
        if table["genre2"] is None:
            raise ValueError(f"page {page}: the first row of a group has no GENRE2")
        if table["code"] and table["code"] != f"K{table['genre1']:X}-{table['genre2']:X}":
            raise ValueError(f"page {page}: the group {table['code']} has the GENRE1 {table['genre1']} and the GENRE2 {table['genre2']}")
        out.append(KRow(page, table["title"], table["code"] or f"K{table['genre1']}-{table['genre2']}", table["genre1"], table["genre2"], bits, names, number))

    cut = data_x = no_cut = None           # the columns of the last header: a page that goes on from the one before has no header of its own
    for page, lines in sorted(page_lines(rows).items()):
        for _, words in lines:
            if (found := bit_header(words)) is not None:
                cut, data_x, no_cut = found
        bit_rows = [k for k, (_, words) in enumerate(lines) if cut is not None and any(_BIT.match(w) for x, w in words if x < cut)]
        for k, (y, words) in enumerate(lines):
            if y > 795:
                continue
            text = " ".join(w for _, w in words)
            all_bits = [w for _, w in words if _BIT.match(w)]
            if len(all_bits) == 24 and len(words) == 24 and words[-1][0] > 440 and words[0][0] < 110:           # the 24 bits of a header
                header = "".join(all_bits)
                if header[:16] != VENDOR or header[16:20] != "0000":
                    raise ValueError(f"page {page}: a group's header is not of the vendor 0x54 0x32 and a 0 parity nibble")
                title = ""
                for j in range(k - 1, -1, -1):
                    if lines[j][1] and " ".join(w for _, w in lines[j][1]).startswith("DENON CODE"):
                        title = _title_of(lines, j)
                        break
                state["table"] = {"title": title, "genre1": value(header[20:24]), "genre2": None, "code": None, "id": None}
                building, pending = None, []
                continue
            if (m := re.match(r"REMOTE ID SET ?: ?(?:ID ?)?(.+)$", text)) and state["table"]:
                state["table"]["id"] = m.group(1).strip()
                continue
            if (m := re.match(r"(K\d-\w+) ?: ?(.+)$", text)) and state["table"]:
                state["table"]["code"] = m.group(1)
                if not state["table"]["title"]:
                    state["table"]["title"] = m.group(2).strip()
                continue
            table = state["table"]
            if table is None or cut is None:
                continue
            if table["id"] not in (None, "1"):
                if table["code"] and not table.get("said"):
                    skipped.append(f"page {page} {table['code']} : {table['title']} (REMOTE ID SET : {table['id']})")
                    table["said"] = True
                continue
            if text.startswith(("GENRE2", "DENON CODE", "Parity", "Psrity", "Data", "25 26", "Key")) or not words:
                continue
            left = [(x, w) for x, w in words if x < cut]
            number = None
            if no_cut is not None:                                    # a ``No.`` column: the number of the row is left of the bits
                nums = [w for x, w in left if x < no_cut]
                left = [(x, w) for x, w in left if x >= no_cut]
                if len(nums) == 1 and nums[0].isdigit() and left:
                    number = int(nums[0])
            right = " ".join(w for x, w in words if x >= cut)
            if _PAGE_NOTE.fullmatch(text):                            # the date at a page's head or its number (``2007/07/06``, ``1/28``)
                continue
            if left and any(not _BIT.match(w) and w != "*" for _, w in left):
                continue                                          # a heading or a note, not a row
            tokens = [(x, w) for x, w in left]
            if not tokens:
                near = [abs(lines[j][0] - y) for j in bit_rows if lines[j][0] != y]
                if not near or min(near) > 20:
                    continue                                      # a line far from every row: a page's header or a note
                before = [j for j in bit_rows if lines[j][0] < y]
                after = [j for j in bit_rows if lines[j][0] > y]
                to_next = bool(after) and (not before or lines[after[0]][0] - y < y - lines[before[-1]][0])
                if re.search(r"\((?:e|E)xcept [^)]*\)$", right):
                    to_next = True
                if re.match(r"^/", right):
                    to_next = False
                if to_next:
                    pending.append(right)
                elif building is not None:
                    building[1].append(right)
                elif out and out[-1].table == table["code"]:
                    out[-1].names.append(right)
                continue
            if any(w == "*" for _, w in tokens):
                if building is None and any(not (_BIT.match(w) or w == "*") for x, w in words if x >= cut):      # a row with no name is the page's legend of the columns (``1 0 0 0 * * *``)
                    skipped.append(f"page {page} y {y}: a row with '*' bits in a table for remote ID 1 or none")
                continue
            if building is None:
                building = [[], pending, page, min(x for x, _ in tokens) < data_x, number]
                pending = []
            building[0].extend(tokens)
            if right:
                building[1].append(right)
            need = 24 if building[3] else 20
            if len(building[0]) < need:
                continue
            if len(building[0]) != need:
                raise ValueError(f"page {page} y {y}: a row has {len(building[0])} bits, not {need}")
            close_row(table, building[0], building[1], building[2], building[3], building[4])
            building = None
    return out, skipped


def normalised(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


# --- the lists whose rows have an identifier (2012 and 2013) ----------------------------------------------------------------

_ID_SHARP = re.compile(r"^RCSHP([0-9A-F]{2})(\d)(\d{4})$")
_ID_KAS = re.compile(r"^RCKSK0(\d)(\d)(\d{4})$")
_SHORT_ID = re.compile(r"^RCKSK0\d\d\d{1,3}$")                 # an identifier of which a digit wrapped to the line below (the AVR-3312's zone 3 table)
_NOT_TEXT = re.compile(r"^(?:[\d*]+|No\.|Data|Key|Name|ID|parity|\(C6|C11\))$")
_TABLE_LINE = re.compile(r"^K\d-\w+ ?: ?\S")


def unwrap_ids(lines: list[tuple[int, list[tuple[int, str]]]]) -> list[tuple[int, list[tuple[int, str]]]]:
    """The lines of a page with the rows whose identifier wrapped made whole: a cell too narrow for ``RCKSK0450005`` has ``RCKSK045000`` on one line and the last digit under it
    (the AVR-3312's zone 3 table), and the row's bits and name are on the lines between, in two or three pieces. **Everything between that identifier and the next
    one's line is the row's**: the digit completes the identifier and the rest, by place, is the row."""
    out = [(y, list(words)) for y, words in lines]
    gone: set[int] = set()
    for i, (y, words) in enumerate(out):
        if i in gone:
            continue
        for x, word in list(words):
            if len(word) >= 12 or not _SHORT_ID.match(word):
                continue
            j = i + 1
            while (j < len(out) and not any(w.startswith("RCKSK") and abs(xx - x) <= 10 for xx, w in out[j][1]) and out[j][0] - y <= 40
                   and not (len(out[j][1]) == 1 and re.fullmatch(r"\d{2,3}", out[j][1][0][1]))):             # (the page number ends it)
                j += 1
            band = range(i + 1, j)
            digit = next(((k, n) for k in band for n, (xx, w) in enumerate(out[k][1]) if x + 15 <= xx <= x + 60 and re.fullmatch(r"\d", w)), None)
            if digit is None:
                continue
            k, n = digit
            _, digit_word = out[k][1].pop(n)
            words.remove((x, word))
            words.append((x, word + digit_word))
            for b in band:
                words.extend(out[b][1])
                out[b] = (out[b][0], [])
                gone.add(b)
    return [(y, words) for (y, words) in out if words]


def id_rows(rows: list[list[str]], models: tuple[str, ...], notes: dict[str, str] | None = None) -> tuple[list[DocRow], list[str]]:
    """``(the key rows, what is not read)`` of a list whose rows have an identifier (``RCSHP0230016``: the Sharp table of address 02 and extension bits 3, row 16;
    ``RCKSK0410001``: the Kaseikyo table K4-1, function 1), §90's lists' layout as the PDFs of 2011 and 2012 have it (the pages are turned 90 degrees, which the ``words``
    CSV undoes). **A Sharp row is its identifier, its six data bits where the PDF has them (which must be the number's) and a name**; **a Kaseikyo row is its identifier, ten
    data bits (and in a group's first row four more before them, its ``GENRE2``, which must be its identifier's), ten ``*`` (the ID and the parity) and a name**. A group has
    a title (the line above its ``REMOTE ID SET`` line) and a header of 24 bits (the vendor, a 0 parity nibble, ``GENRE1``: the lists of 2011) or 28 (and ``GENRE2``: the
    lists of 2012), whose ``GENRE2`` is the rows' table's, or the row says it is not (§90's ``ZONE3`` header that copies the ``ZONE2``'s). **The groups of ``REMOTE ID SET:
    1`` and ``1-4`` are read, with the ID bits 0 as the remote ID 1's; the groups of ``2-4``, whose line is followed by the table's own (``K4-2 : MAIN ZONE``), are not read**
    (their tables are the other IDs', which write the ID and the parity as ``*`` and have another ``GENRE2``) and are listed. A ``2-4`` line that is a column's heading
    inside a group (the lists of 2012) is not a group."""
    records: list[dict] = []
    skipped: list[str] = []
    reading = False                                                            # whether the Kaseikyo rows now are of a group for remote ID 1
    other, other_y = "", -100
    group_y = -100
    group: Group | None = None
    notes = footnotes(rows) if notes is None else notes
    spans = page_spans(rows)
    block = 0                                                                  # which group's rows a line is of: a line is never a name of the rows of another group's
    for page, lines in sorted(page_lines(rows).items()):
        lines = unwrap_ids(lines)
        id_lines: list[tuple[int, int, list[tuple[int, dict]]]] = []       # (y, block, [(x of the identifier, record)]): where a line of words that has none goes on
        loose: list[tuple[int, int, list[tuple[int, str]]]] = []
        before = ""
        for y, words in lines:
            tokens = sorted(words)
            text = " ".join(w for _, w in tokens)
            if y > 560 and re.fullmatch(r"\d+", text):
                continue                                              # the page number
            if re.fullmatch(r"REMOTE ID SET ?: ?(?:1|1-4)", text):
                label, kept = pdf_marks(before, models, notes)
                group = Group(before, label, kept, 0, -1)
                block += 1
                reading = True
                group_y = y
                continue
            if text.startswith("REMOTE ID SET"):
                other, other_y = text, y
                continue
            if _TABLE_LINE.match(text) and other and 0 < y - other_y <= 20 and not other.endswith(": 1"):
                reading = False                                            # the table of a group for the other remote IDs
                skipped.append(f"page {page} {text} ({other})")
                other = ""
                block += 1
                continue
            if _TABLE_LINE.match(text) and group is not None and reading and 0 < y - group_y <= 20:
                group.printed = text.split(":", 1)[1].strip()               # the table's own name (``K2-3 : iPod``), shorter than the page's heading
                continue
            bits = [w for _, w in tokens if _BIT.match(w)]
            if len(bits) in (24, 28) and len(tokens) == len(bits) and group is not None and group.genre1 == 0 and group.table is None:
                header = "".join(bits)
                if header[:16] != VENDOR or header[16:20] != "0000":
                    raise ValueError(f"page {page}: the group {group.printed!r} has no header of the vendor 0x54 0x32 and a 0 parity nibble")
                group.genre1 = value(header[20:24])
                group.genre2 = value(header[24:28]) if len(bits) == 28 else -1
                continue
            ids = [i for i, (_, w) in enumerate(tokens) if _ID_SHARP.match(w) or _ID_KAS.match(w)]
            if not ids:
                if tokens and not text.startswith(("DENON CODE", "No.")) and not all(_NOT_TEXT.match(w) for _, w in tokens):
                    before = text
                loose.append((y, block, tokens))
                continue
            cells: list[tuple[int, dict]] = []
            id_lines.append((y, block, cells))
            for k, i in enumerate(ids):
                end = ids[k + 1] if k + 1 < len(ids) else len(tokens)
                ident, rest = tokens[i][1], tokens[i + 1:end]
                record: dict = {"ident": ident, "page": page, "group": group, "words": []}
                if m := _ID_SHARP.match(ident):
                    number = int(m.group(3)) % 64
                    data = next((w for _, w in rest if re.fullmatch(r"[01]{6}", w)), None)
                    if data is not None and value(data) != number:
                        raise ValueError(f"page {page}: the row {ident} has data {data}, whose value is not its number")
                    record.update(kind="sharp", address=int(m.group(1), 16), extension=int(m.group(2)), number=number, data=f"{number:06b}"[::-1])
                    record["words"] = [(y, x, w) for x, w in rest if not re.fullmatch(r"[01]{6}", w)]
                else:
                    m = _ID_KAS.match(ident)
                    if group is None:
                        raise ValueError(f"page {page}: the row {ident} is in no group")
                    if not reading:
                        continue
                    name_x = min([x for x, w in rest if w != "*" and not _BIT.match(w)] + [10 ** 6])       # the name starts at its first word that is no bit (it may end in a ``1``)
                    limit = min([x for x, w in rest if w == "*"] + [name_x])                              # and the data bits are left of the stars (where the ID and parity are)
                    data = [w for x, w in rest if _BIT.match(w) and x < limit]
                    if len(data) in (14, 24):                                   # a group's first row has its GENRE2 before the ten data bits
                        if value("".join(data[:4])) != int(m.group(2)):
                            raise ValueError(f"page {page}: the row {ident} has the GENRE2 bits {''.join(data[:4])}, not its identifier's")
                        data = data[4:]
                    if len(data) not in (0, 10, 20):
                        raise ValueError(f"page {page}: the row {ident} has {len(data)} data bits")
                    number = int(m.group(3))
                    record.update(kind="kaseikyo", table=(int(m.group(1)), int(m.group(2))), data="".join(data[:10]) if data else f"{number:010b}"[::-1], number=number,
                                  unread=not data,         # a row whose bits are not in the text (v02's Spotify rows): its function is the identifier's
                                  tail="".join(data[10:]))   # the ID bits and the parity byte where the group writes them (the 2011 lists' tables for ID 1) and not ``*``
                    record["words"] = [(y, x, w) for x, w in rest if x >= name_x]
                records.append(record)
                cells.append((tokens[i][0], record))
        for y, b, tokens in loose:                                      # the rest of a name: with the cell its place is in, of the row it is in
            if tokens[0][1].startswith("\u203b"):                          # a footnote (``\u203b1:Except AVR990``) is no name
                continue
            same = [r for r in id_lines if r[1] == b]
            owner = owner_line(y, [r[0] for r in same], spans.get(page, []))
            if owner is None:
                continue
            cells = next(r[2] for r in same if r[0] == owner)
            for x, word in tokens:
                under = [c for c in cells if x >= c[0] - 2]
                if under and not _NOT_TEXT.match(word):
                    under[-1][1]["words"].append((y, x, word))
    out: list[DocRow] = []
    for r in records:
        parts = lines_of(r["words"])
        printed = tidy(" ".join(parts))
        if r["kind"] == "sharp":
            address, extension = r["address"], r["extension"]
            out.append(DocRow("sharp", r["ident"], f"{address:05b}"[::-1] + " " + f"{extension:02b}"[::-1], "", printed, pdf_names(parts, models, notes) if parts else [], r["data"], address, None,
                              value(r["data"]) + 64 * extension, r["number"]))
        else:
            g: Group = r["group"]
            table = r["table"]
            note = (f"the group's header gives GENRE2 {g.genre2}, not the rows' {table[1]}: its rows' identifiers say K{table[0]}-{table[1]}") if g.genre2 not in (-1, table[1]) else ""
            if r.get("unread"):
                note += ("; " if note else "") + "the row's data bits are not in the PDF's text, so its function is its identifier's alone"
            names = [(label, kept & g.models) for label, kept in pdf_names(parts, models, notes)] if parts else []
            if not r["tail"]:
                out.append(DocRow("kaseikyo", r["ident"], f"K{table[0]}-{table[1]}", g.printed, printed, names, r["data"], table[0], table[1], value(r["data"]), r["number"], note))
                continue
            if r["tail"][:2] != "00":
                raise ValueError(f"page {r['page']}: the row {r['ident']} has the ID bits {r['tail'][:2]}, not those of remote ID 1")
            k = KRow(r["page"], g.printed, f"K{table[0]}-{table[1]}", table[0], table[1], r["data"] + r["tail"], [])
            device, code, how, doubt = parity_reading(k, False)                    # (no list of 2011 has XM rows under another table)
            out.append(DocRow("kaseikyo", r["ident"], code, g.printed, printed, names, r["data"], device, table[1], k.function, r["number"], note, page=r["page"], doubt=doubt,
                              how=f"{k.table} : {g.printed or 'no title'}, bits 29 to 48 {k.bits}): Denon-K device {device} subdevice {table[1]} function {k.function}, GENRE1, GENRE2 and the "
                                  f"data each first bit first{how}"))
    return out, skipped


# --- the marks of names ------------------------------------------------------------------------------------------------------

REGIONS = ("NA", "EU", "JP", "CHI", "China", "Asia", "AUS", "UK", "US", "Others")
_PAREN = re.compile(r"\(\s*([^()]*?)\s*\)")
_NOTE_MARK = re.compile(r"\u203b\s*(\d)")
_MARK_ONLY = re.compile(r"\s*\u203b\s*([A-Za-z][^\u203b()]*?\s+only)(?![A-Za-z])")


def squash(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text).upper()


def footnotes(rows: list[list[str]]) -> dict[str, str]:
    """The notes a list's names refer to by a mark (``\u203b1:Except AVR990``, ``\u203b2:EU only``): the mark's number -> its text."""
    notes: dict[str, str] = {}
    for _, lines in sorted(page_lines(rows).items()):
        for _, words in lines:
            if m := re.match(r"^\u203b(\d) ?: ?(.+)$", " ".join(w for _, w in sorted(words))):
                notes[m.group(1)] = " ".join(m.group(2).split())
    return notes


def pdf_marks(text: str, models: tuple[str, ...], notes: dict[str, str]) -> tuple[str, frozenset[str]]:
    """``(the label, the models it is for)`` of one printed name of a list of 2003 to 2013. **A parenthesis that says ``(AVR3311CI only)`` or ``(except AVR3311CI)`` names
    models, and restricts the key**; a mark ``\u203b1`` is the page's note of that number, read as a parenthesis. **A region (``NA only``, ``except JP,China``) stays in the
    label**, as does a parenthesis that names nothing of the document's models (``(ASD-51/ASD-3 only)``: a device)."""
    kept = set(models)
    text = _NOTE_MARK.sub(lambda m: f" ({notes[m.group(1)]})" if m.group(1) in notes else m.group(0), text)
    text = _MARK_ONLY.sub(lambda m: f" ({m.group(1)})", text)                  # ``PTY \u203bEU only``: the 2012 lists write the footnote's words at the mark
    by_squash = {squash(m): m for m in models}

    def one(found: re.Match) -> str:
        nonlocal kept
        inside = found.group(1)
        m = re.match(r"(?i)^(except)\s*(.+)$", inside) or re.match(r"(?i)^(.+?)\s+(only)$", inside)
        if m is None:
            return found.group(0)
        mode, body = (m.group(1).lower(), m.group(2)) if m.group(1).lower() == "except" else (m.group(2).lower(), m.group(1))
        tokens = [t.strip() for t in re.split(r"[,/&]|\band\b", body) if t.strip()]
        named = {by_squash[squash(t)] for t in tokens if squash(t) in by_squash}
        others = [t for t in tokens if squash(t) not in by_squash]
        variants = {m for t in others for sq, m in by_squash.items() if squash(t).startswith(sq)}      # ``AVR1911 E3``: a version of the AVR-1911
        if not named and not variants:
            return found.group(0)                                    # a region or a device: stays in the label
        if mode == "only":
            kept &= named | variants                                 # (a version's key is for the model: it has that version)
        else:
            kept -= named                                            # (the model's other versions have the key)
        return "" if not others else found.group(0)

    label = _PAREN.sub(one, text)
    label = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", " ".join(label.split())))
    return label, frozenset(kept)


def pdf_names(text: str | list[str], models: tuple[str, ...], notes: dict[str, str]) -> list[tuple[str, frozenset[str]]]:
    """The names of a row's cell, as its lines or one text, and the models each is for. **A line that begins with ``(`` goes on the line before, a line that begins with ``/``
    is the rest of a name unless the name before it is qualified (``TUNER(except AVR4311CI)`` and then ``/HD RADIO(AVR4311CI only)`` are two), and a name that is two,
    ``TUNER(except AVR3311CI)/HD RADIO(AVR3311 only)``, is each side of the slash after a mark** (§81's rules, which read the AVR-4311's lines the same, and a parenthesis that
    is not closed goes on in the next line); a name with no mark
    of models is for the models the others are not for (§90's rule)."""
    lines = [text] if isinstance(text, str) else text
    joined: list[str] = []
    for line in lines:
        qualified = bool(joined) and bool(re.search(r"(?i)(except|only)\b[^()]*\)$", joined[-1]))
        if joined and (line.startswith("(") or joined[-1].count("(") > joined[-1].count(")")):          # a parenthesis goes on over the line's end
            joined[-1] += " " + line
        elif joined and line.startswith("/") and not qualified:
            joined[-1] += line
        else:
            joined.append(line[1:] if line.startswith("/") else line)
    parts: list[str] = []
    for line in joined:
        parts.extend(re.split(r"(?<=\))\s*/\s*(?=[A-Za-z])", line) if re.search(r"(?i)(except|only)[^()]*\)\s*/", line) else [line])
    names = [pdf_marks(part.strip(), models, notes) for part in parts if part.strip()]
    if len(names) > 1:
        taken = frozenset().union(*(kept for _, kept in names if len(kept) < len(models)))
        names = [(label, kept if len(kept) < len(models) or not taken or set(models) == taken else frozenset(models) - taken) for label, kept in names]
    return names


# --- the documents -----------------------------------------------------------------------------------------------------------

PDF_HOST = "assets.denon.com IR code documents"


def parity_reading(r: KRow, tuner: bool = True) -> tuple[int, str, str, str]:
    """``(the device, the table's code, what the citation adds, why the key is doubted)`` of a row whose bits include the parity byte: **a byte that is its own table's agrees; a
    byte that is another zone's is a table copied (the data bits are the original's); a byte that is the satellite tuners' (GENRE1 7) is a row of those under another table;
    a byte that is nobody's is doubted** (D349)."""
    says = r.says(tuner)
    if says == "tuner":
        return SATELLITE, f"K{SATELLITE}-{r.subdevice:X}", (f"; the document's parity byte is the one GENRE1 {SATELLITE} gives, not this table's GENRE1 {r.device}: the row is read as "
                                                           f"K{SATELLITE}-{r.subdevice:X} (the satellite tuners')"), ""
    if says == "main":
        return r.device, r.table, f"; the document's parity byte is the one GENRE2 {copied_zone(r.device, r.subdevice, r.function, r.parity)} gives, not this table's (a table copied from another's)", ""
    if says == "disagrees":
        return r.device, r.table, "", (f"the document's parity byte {r.bits[12:20]} (0x{r.parity:02X}) is not 0x{r.computed_parity():02X}, which its data bits give (the exclusive or of the 3rd, "
                                       "4th and 5th bytes), nor any zone's: the data bits are read and the parity byte is not")
    return r.device, r.table, "", ""


def legacy_rows(rows: list[list[str]], models: tuple[str, ...], notes: dict[str, str] | None = None, fixes: tuple[tuple[int, str, str, str, str], ...] = (),
                name_fixes: tuple[tuple[str, str], ...] = ()) -> tuple[list[DocRow], list[str]]:
    """``(the key rows, what is not read)`` of a list whose rows have no identifier: its Sharp tables and the Kaseikyo groups for the default remote ID, as ``DocRow`` rows
    whose ``how`` is the citation from the row's bits on. **A Kaseikyo row's parity byte is checked**: where it is the one the GENRE2 of another zone gives (the zones' tables
    are copied from the main zone's) the row says so and stays, and where it is not any zone's, the row is Untested. ``name_fixes`` are the model names a document misprints in
    its marks (``AVR3311`` in the AVR-2311's list, a copy of the 3311's), ``(printed, meant)``: a row whose name has one says so."""
    out: list[DocRow] = []
    notes = footnotes(rows) if notes is None else notes

    def mended(*texts: str) -> tuple[list[str], str]:
        fixed = []
        for text in texts:
            for printed, meant in name_fixes:
                text = text.replace(printed, meant)
            fixed.append(text)
        said = sorted({f"'{a}' is read as '{b}'" for t in texts for a, b in name_fixes if a in t})
        return fixed, ("; the document's mark names the model wrongly (" + ", ".join(said) + ": the title's model)") if said else ""

    for table in sharp_tables(rows, fixes):
        for n, cell in sorted(table.cells.items()):
            name = cell.name
            lines, said = mended(*cell.lines)
            function = table.function(n)
            doubt = (f"the page prints the header {cell.printed.replace(' ', ' / extension ')} over this cell, and the half of the table above it on the page has the header "
                     f"{table.address} / extension {table.extension}: the cell is read as that table's") if cell.printed else ""
            out.append(DocRow("sharp", f"No. {n}", f"{table.address} {table.extension}", "", name, pdf_names(lines, models, notes) if name else [], cell.data, table.device, None, function, n,
                              page=cell.page, doubt=doubt,
                              how=(f"system address {table.address}, extension bits {table.extension}, data {cell.data}): Denon device {table.device} function {function}, "
                                   f"each field first bit first, the extension bits above the data{said}")))
    kas, skipped = kaseikyo_rows(rows)
    tuner = sum(1 for r in kas if r.says() == "tuner") >= MIN_TUNER_ROWS          # a table of XM rows has several such rows; one is chance
    counted: dict[str, int] = {}
    for r in kas:
        counted[r.table] = counted.get(r.table, 0) + 1
        (group, *lines), said = mended(r.group, *r.names)
        kept = pdf_marks(group, models, notes)[1] if r.group else frozenset(models)
        names = [(label, k & kept) for label, k in pdf_names(lines, models, notes)] if r.name else []
        device, table, said_of_parity, doubt = parity_reading(r, tuner)
        how = (f"{r.table} : {r.group or 'no title'}, bits 29 to 48 {r.bits}): Denon-K device {device} subdevice {r.subdevice} function {r.function}, GENRE1, GENRE2 and the data "
               f"each first bit first{said_of_parity}")
        how += said
        if r.number is not None and r.number != r.function & 0x3FF:
            how += f"; the row's No. is {r.number}"
            doubt += ("; " if doubt else "") + f"the row's No. {r.number} is not the value {r.function & 0x3FF} of its data bits, which are read and the number is not"
        out.append(DocRow("kaseikyo", f"{r.table} #{counted[r.table]}", table, r.group, r.name, names, r.bits, device, r.subdevice, r.function, r.function, page=r.page, how=how, doubt=doubt))
    return out, skipped


@dataclass(frozen=True)
class PdfDocument:
    file: str
    style: str                                  # ``legacy`` (rows of bits, no identifier) or ``id`` (identifiers, the 2012 and 2013 lists)
    models: tuple[str, ...]                     # the products the document's title names (or its file name): a Sharp and a Kaseikyo file for each
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = ()
    header_fixes: tuple[tuple[int, str, str, str, str], ...] = ()       # a Sharp header the document misprints: (page, address, extension, the address and extension it means)
    name_fixes: tuple[tuple[str, str], ...] = ()                         # a model name the document misprints in its marks: (printed, meant)


PDF_DOCUMENTS: tuple[PdfDocument, ...] = (
    PdfDocument("avr-2106-ircodes.pdf", "legacy", ("AVR-2106",)),
    PdfDocument("avr-2805-ircodes.pdf", "legacy", ("AVR-2805",)),
    PdfDocument("avr-2807-ircodes.pdf", "legacy", ("AVR-2807",)),
    PdfDocument("avr-2808_ir_code.pdf", "legacy", ("AVR-2808CI", "AVR-2808", "AVR-988", "AVC-2808")),
    PdfDocument("avr-3803-ircodes.pdf", "legacy", ("AVR-3803",)),
    PdfDocument("avr-3808ci-ircodes.pdf", "legacy", ("AVR-3808CI",), (("AVR-3808CI", ("AVR-3808",)),)),                     # the title says AVR3808, the file's name 3808ci
    PdfDocument("avr-4308ci_ircodes.pdf", "legacy", ("AVR-4308CI",), (("AVR-4308CI", ("AVR-4308",)),)),
    PdfDocument("avr-4806-ircodes.pdf", "legacy", ("AVR-4806",)),
    PdfDocument("avr-5805-ircodes.pdf", "legacy", ("AVR-5805",)),
    PdfDocument("avr-5805ci_5805up_ir_codes.pdf", "legacy", ("AVR-5805CI", "AVR-5805 Upgraded")),
    PdfDocument("avr987-ircodes.pdf", "legacy", ("AVR-987",)),
    PdfDocument("417_avr3310_ir_code.pdf", "legacy", ("AVR-3310CI", "AVR-990", "AVC-3310")),
    PdfDocument("457_avr3311ci_avr3311_991_ir_code_v01.pdf", "legacy", ("AVR-3311CI", "AVR-991", "AVR-3311")),
    PdfDocument("avr-1911_791_ir_code_v01.pdf", "legacy", ("AVR-1911", "AVR-791"), header_fixes=((7, "01000", "01", "00100", "01"),)),
    PdfDocument("avr-2311ci_avr2311_891_ir_code_v01.pdf", "legacy", ("AVR-2311CI", "AVR-891", "AVR-2311"), name_fixes=(("AVR3311", "AVR2311"),)),     # a copy of the 3311's list
    PdfDocument("avr-4310_ir_code_v01.pdf", "legacy", ("AVR-4310CI", "AVC-4310")),
    PdfDocument("avr2809_ir_code_v01.pdf", "legacy", ("AVR-2809",), (("AVR-2809", ("AVR-2809CI",)),)),
    PdfDocument("avr2309_ir_code_v01.pdf", "legacy", ("AVR-2309",), (("AVR-2309", ("AVR-2309CI",)),)),
    PdfDocument("avr2113_1913_ir_code_v02.pdf", "id", ("AVR-2113", "AVR-1913"), (("AVR-2113", ("AVR-2113CI",)),)),
    PdfDocument("avr2313_ir_code_v02.pdf", "id", ("AVR-2313",), (("AVR-2313", ("AVR-2313CI",)),)),
    PdfDocument("avr3313_ir_code_v02.pdf", "id", ("AVR-3313",), (("AVR-3313", ("AVR-3313CI",)),)),
    PdfDocument("avr4520_ir_code_v02.pdf", "id", ("AVR-4520",), (("AVR-4520", ("AVR-4520CI",)),)),
    PdfDocument("avr3312_ir_code_v01.pdf", "id", ("AVR-3312",), (("AVR-3312", ("AVR-3312CI",)),)),
    PdfDocument("avr2312_ir_code_v01.pdf", "id", ("AVR-2312",), (("AVR-2312", ("AVR-2312CI",)),)),
)


def build_pdfs(snapshot, authored, report):
    """The files of every PDF in ``PDF_DOCUMENTS``: its rows (``legacy_rows`` or ``id_rows``) as ``denon.emit_rows`` makes them into files."""
    from .common import IMPORT_ROOT
    from .denon import MAKER, emit_rows

    gate: dict[tuple, str | None] = {}
    out = []
    for document in PDF_DOCUMENTS:
        words = snapshot.sheet_of(document.file, "words")
        entry, sha = snapshot.documents[document.file], snapshot.sha(document.file)
        where = f"{PDF_HOST}/{document.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)})"
        if document.style == "legacy":
            rows, skipped = legacy_rows(words, document.models, None, document.header_fixes, document.name_fixes)
        else:
            rows, skipped = id_rows(words, document.models)
        for note in skipped:
            report.notes.append((MAKER, document.file, f"not read: {note}: its rows write the ID and parity bits as '*', or the table is for other remote IDs"))
        out += emit_rows(document.file, where, rows, document.models, authored, report, gate, {m: a for m, a in document.aliases})
    return out
