"""Yamaha's standard and extended IR code tables for its receivers and amplifiers (DESIGN section 87).

Yamaha wrote for its custom installers one table of IR codes for each family of receivers (``Standard IR codes for RX-V385-585``, ``Yamaha Extended IR codes for
RX-V685, RX-A780 - RX-A3080 and CX-A5200``, ``... for WXA-50, WXC-50``, ``... for XDA-QS5400RK and XDA-AMP5400RK``): a row for each function of a group (``POWER``,
``INPUT``, ``VOLUME``, ``MENU``...) and each zone it is addressed to (``MAIN``, ``ZONE2``, ``ZONE3``, ``ZONE4``, ``ALL``), **two codes, ``Code (ID1)`` and ``Code (ID2)``**,
and a ``YES`` or ``NO`` for each model of the family. The standard tables are the codes of the remote's keys, the extended ones the discrete functions (``POWER ON`` and
``STANDBY OFF``, each input, each scene, each sound program). The PDFs are on the support-documents folder of SnapAV, an American distributor of Yamaha's, and are read from
the CSV of each table's rows (``grid``, D332) pinned by SHA-256 (D123). **DESIGN section 100 adds the tables of 2010 to 2014** from the control documents of AWE Europe, a British
distributor (the Wayback Machine's copies of the zips of its Yamaha folder): three spreadsheets (RX-V671 to RX-V3071 and the RX-A710 to RX-A3010, RX-V675 to RX-V3075 and the RX-A730
to RX-A3030 and the CX-A5000, RX-V677 to RX-V3077 and the RX-A740 to RX-A3040), whose names and devices are written once for the rows they cover (D368: ``blocks``), and the PDFs of the
RX-V375 to RX-V775 (the same layout as D332's) and of the RX-V667 to RX-V3067 (a table with a ``Comment`` column that says which models a row is for, D369).

**A code is Yamaha's own notation, which Yamaha's IR Hex Converter (``yamaha.com/ypab/irhex_converter.asp``, in the Wayback Machine) writes down: ``7E-2A``, ``7A-453A``,
``7F01-6C13``, ``7E-2AD4``**: the first group is the custom code, the second the data, **a group of two digits is a byte and its complement** (``7E`` is ``7E 81``) and a group
of four is the two bytes as written (``7F01`` is ``7F 01``, ``2AD4`` is ``2A D4``), the four bytes sent in the order written. **A key is the ledger's ``NEC1-f16``**: NEC1's frame
whose fourth byte is a field of its own (a ``function`` of ``F:E`` sixteen bits), the device the first byte and the subdevice the second. **The ID2 code of a row is its ID1 code
with the low bit of its last byte flipped, in every one of the 1,316 rows**: the ID is a setting of the receiver and its remote that tells two of them apart.

**DESIGN section 105 adds the BD-S477 and BD-S677 Blu-ray players** from the three one-page PDFs of the same zips of AWE Europe (``BD-S477_IR_code_list.pdf``, ``BD-S677_IR_code_list.pdf``
and ``BD-S477_S677_RC_code_list.pdf``): a custom code written once (``7C``), then a row for each function with its ``data code 0`` and ``data code 1`` for each ID (the list of the
BD-S477 and the BD-S677 of China has ID1 only), read by the words' places. **The remotes' key layout (the third PDF) is held against the two lists**: a key it has under another code is Untested.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "yamaha"
BRAND = "Yamaha"
PROTOCOL = {"name": "NEC1-f16", "carrierHz": 38000, "minSends": 1}
HOST = "snapav.com Yamaha IR code PDFs"
AWE = "awe-europe.com Yamaha control documents"
IDS = (("ID1", "Code (ID1)", 0), ("ID2", "Code (ID2)", 1))
_CODE = re.compile(r"^([0-9A-F]{2}|[0-9A-F]{4})-([0-9A-F]{2}|[0-9A-F]{4})$")


@dataclass(frozen=True)
class Document:
    file: str
    columns: tuple[tuple[str, tuple[str, ...]], ...]       # the heading of each model column and the models it is for
    layout: str = "grid"                                    # ``grid`` (D332), ``blocks`` (D368: a spreadsheet) or ``comments`` (D369: the 2010 PDFs)
    host: str = HOST
    skip: tuple[str, ...] = ()                              # models of a column that a newer document of this module reads (D368)


DOCUMENTS: tuple[Document, ...] = (
    Document("Standard_IR_Codes_RX-V685RX-A780-RX-A3080CX-A5200_4.pdf",
             (("RX-V685 RX-A780", ("RX-V685", "RX-A780")), ("RX-A880", ("RX-A880",)), ("RX-A1080", ("RX-A1080",)), ("RX-A2080", ("RX-A2080",)),
              ("RX-A3080", ("RX-A3080",)), ("CX-A5200", ("CX-A5200",)))),
    Document("Extended_IR_Codes_RX-V685RX-A780-RX-A3080CX-A5200_6.pdf",
             (("RX-V685 RX-A780", ("RX-V685", "RX-A780")), ("RX-A880", ("RX-A880",)), ("RX-A1080", ("RX-A1080",)), ("RX-A2080", ("RX-A2080",)),
              ("RX-A3080", ("RX-A3080",)), ("CX-A5200", ("CX-A5200",)))),
    Document("RX-V385_RX-V485_RX-V585_IR_Codes_2.pdf", (("385", ("RX-V385",)), ("485", ("RX-V485",)), ("585", ("RX-V585",)))),
    Document("WXA-50_WXC-50_Standard_IR_Codes_1.pdf", (("WXA-50 WXC-50", ("WXA-50", "WXC-50")),)),
    Document("WXA-50_WXC-50_Extended_IR_Codes_1.pdf", (("WXA-50 WXC-50", ("WXA-50", "WXC-50")),)),
    Document("XDA-QS5400_XDA-AMP5400_IR_Commands.pdf", (("XDA-QS5400RK", ("XDA-QS5400RK",)), ("XDA-AMP5400RK", ("XDA-AMP5400RK",)))),
    # the receivers of 2010 to 2014 (DESIGN section 100); the RX-V675 and RX-V775 are in two documents, and the newer one (the spreadsheet of 2013-07) is read for them
    Document("IR_RX-V671-3071_rev1.0_Full.xls",
             tuple((m, (m,)) for m in ("RX-A710", "RX-A810", "RX-A1010", "RX-A2010", "RX-A3010", "RX-V671", "RX-V771", "RX-V871", "RX-V1071", "RX-V2071", "RX-V3071", "HTR-6064")),
             layout="blocks", host=AWE),
    Document("IR_Vx75.xls",
             (("RX-V675 / RX-A730", ("RX-V675", "RX-A730")), ("RX-V775", ("RX-V775",)), ("RX-A830", ("RX-A830",)), ("RX-V1075 / RX-A1030", ("RX-V1075", "RX-A1030")),
              ("RX-V2075 / RX-A2030", ("RX-V2075", "RX-A2030")), ("RX-V3075 / RX-A3030", ("RX-V3075", "RX-A3030")), ("CX-A5000", ("CX-A5000",))),
             layout="blocks", host=AWE),
    Document("IR_Vx77.xls",
             (("RX-V677 RX-A740", ("RX-V677", "RX-A740")), ("RX-V777", ("RX-V777",)), ("RX-A840", ("RX-A840",)), ("RX-V1077 RX-A1040", ("RX-V1077", "RX-A1040")),
              ("RX-V2077 RX-A2040", ("RX-V2077", "RX-A2040")), ("RX-V3077 RX-A3040", ("RX-V3077", "RX-A3040"))),
             layout="blocks", host=AWE),
    *(Document(f"IR_RX-Vx75_375-775_{kind}.pdf",
               (("RX-V375 HTR-2866, 3066", ("RX-V375", "HTR-2866", "HTR-3066")), ("RX-V475, V500D HTR-4066", ("RX-V475", "RX-V500D", "HTR-4066")),
                ("RX-V575 HTR-5066", ("RX-V575", "HTR-5066")), ("RX-V675 HTR-6066", ("RX-V675", "HTR-6066")), ("RX-V775", ("RX-V775",))),
               host=AWE, skip=("RX-V675", "RX-V775")) for kind in ("STD", "EXD")),
    *(Document(f"{kind}_IR_RXV667-3067.pdf", tuple((n, (f"RX-V{n}",)) for n in ("667", "767", "867", "1067", "2067", "3067")), layout="comments", host=AWE)
      for kind in ("STD", "EXD")),
)


@dataclass(frozen=True)
class Row:
    page: int
    y: int
    group: str
    name: str                                # the function as the table writes it, with its second line
    device: str
    zone: str
    codes: tuple[str, str]                   # the ID1 and ID2 cells ("" where the cell is blank)
    marks: tuple[str, ...]                   # the YES or NO of each model column
    extra: str = ""                          # what the row's own name cells say besides its function's name (a note: ``If Another input, Swich input to TUNER``)
    where: str = ""                          # a spreadsheet's place (``sheet Standard row 12``); a PDF's is its page and the row's height
    comment: str = ""                        # the 2010 tables' ``Comment`` cell
    short: tuple[str, str] = ("", "")        # the abbreviated codes of the 2011 spreadsheet, which has the full ones too

    @property
    def place(self) -> str:
        return self.where or f"page {self.page}"


def tidy(text: str) -> str:
    """Runs of space and the ideographic space one space, and a hyphen a number follows (``HDMI- 1``) joined to it."""
    return re.sub(r"(?<=[A-Za-z])- (?=\d)", "-", " ".join(text.replace("　", " ").split()))


ZONE_ORDER = {"MAIN": 0, "ZONE2": 1, "ZONE3": 2, "ZONE4": 3, "ALL": 4}


def is_note(text: str) -> bool:
    """A second line of a name that is no name but a remark on the row (``If Another input, Swich input to TUNER``, ``and select band FM``)."""
    return text.startswith(("If ", "and ", "or ")) or text[:1].islower()


@dataclass
class Run:
    """The rows of one function: the name cells' text, the device, and the rows' own remarks."""
    first: list[str]                                 # the first line of the name (``PAUSE``, ``or``, ``BAND`` where it goes on over the zones' rows)
    second: str
    rows: list[int]
    shared: bool = False                             # the name cells are one merged cell over several functions (the zones begin again with the same text)


def parse(rows: list[list[str]], document: Document) -> list[Row]:
    """The rows of a table's grid (``page, y, group, name, name, device, zone, ID1, ID2, a mark for each model, the two names again``) as the functions they are.

    **A function is a run of rows of rising zones** (``MAIN``, ``ZONE2``, ``ZONE3``, ``ZONE4``, ``ALL``, some left out) **under one name**, which the table writes in cells the
    zones' rows share: the text of a cell a merged cell covers is the same in each row; a cell the library finds no merge for is blank, and its row is the function above (a
    page break, a merged cell with no cover). **A function ends where the name changes or the zones begin again.** A name that goes on over the rows (``PAUSE``, ``or``,
    ``BAND``) is one name, and a remark in a row's second cell (``If Another input, Swich input to TUNER``) is that row's ``extra``, which the citation quotes. **Where one merged
    cell holds the names of several functions (Yamaha's ``MUTE``: ``ON (FULL)``, ``ON (-40dB)`` and ``ON (-20dB)`` are one cell of nine rows), the zones begin again with the
    same text, and each function's name is the words on its own rows.**"""
    heading = [h for h, _ in document.columns]
    width = 7 + len(heading)
    seen = False
    first: list[str] = []
    cells_of: list[tuple[int, int, list[str], list[str]]] = []
    for row in rows:
        page, y, cells = int(row[0]), int(row[1]), [tidy(c) for c in row[2:]]
        if cells[0] == "Function":                                  # the heading is two rows: ``Code`` and the first line of a model, then ``(ID1)`` and the second line
            first = cells[:width]
            continue
        if cells[0] == "Group":
            joined = [" ".join(x for x in pair if x) for pair in zip(first[5:], cells[5:width])]
            if joined != ["Code (ID1)", "Code (ID2)"] + heading:
                raise ValueError(f"page {page}: the heading {joined} is not {['Code (ID1)', 'Code (ID2)'] + heading}")
            seen = True
            continue
        if not seen:
            raise ValueError(f"page {page}: a row before the heading")
        if len(cells) != width + 2:
            raise ValueError(f"page {page} row {y}: {len(cells)} cells, not {width + 2}")
        if cells[4] not in ZONE_ORDER:
            raise ValueError(f"page {page} row {y}: the zone `{cells[4]}` is none of {sorted(ZONE_ORDER)}")
        cells_of.append((page, y, cells[:width], cells[width:]))
    runs: list[Run] = []
    extras: dict[int, str] = {}
    last = -1
    for i, (page, y, cells, own) in enumerate(cells_of):
        zone = ZONE_ORDER[cells[4]]
        c1, c2 = cells[1], cells[2]
        run = runs[-1] if runs else None
        if run is None:
            runs.append(Run([c1], c2, [i]))
        elif not c1 and not c2:                                     # no name at all: the function above, or (the zones beginning again) its merged cell's next function
            if zone <= last:
                run.shared = True
                runs.append(Run(list(run.first), run.second, [i], True))
            else:
                run.rows.append(i)
        elif not c1:                                                # a second line of its own under the first line above
            if zone > last and is_note(c2):
                run.rows.append(i)
                extras[i] = c2
            else:
                runs.append(Run(list(run.first), c2, [i]))
        elif zone > last and c1 == " ".join(run.first) and (c2 == run.second or not c2):         # the first line again over the zones' rows
            run.rows.append(i)
        elif zone > last and c1 == " ".join(run.first) and is_note(c2):
            run.rows.append(i)
            extras[i] = c2
        elif zone > last and not run.second and (c1[0].islower() or run.first[-1].lower() in ("or", "and")) and c1 not in run.first:
            run.first.append(c1)                                    # a name that goes on over the rows: ``PAUSE``, ``or``, ``BAND``
            run.rows.append(i)
        elif zone <= last and c1 == " ".join(run.first) and c2 == run.second:
            run.shared = True
            runs.append(Run(list(run.first), run.second, [i], True))
        else:
            runs.append(Run([c1], c2, [i]))
        last = zone
    group = ""
    names: dict[int, tuple[str, str, str]] = {}
    for run in runs:
        second = run.second
        if run.shared:                                              # the words of the second column on this function's own rows
            own_words = []
            for i in run.rows:
                text = cells_of[i][3][1]
                if text and (not own_words or own_words[-1] != text):
                    own_words.append(text)
            second = " ".join(own_words) or second
        name = " ".join(x for x in (" ".join(run.first), second) if x)
        device = next((cells_of[i][2][3] for i in run.rows if cells_of[i][2][3]), "")
        for i in run.rows:
            names[i] = (name, device, cells_of[i][2][0])
    out: list[Row] = []
    for i, (page, y, cells, own) in enumerate(cells_of):
        name, device, g = names[i]
        group = g or group
        out.append(Row(page, y, group, name, device, cells[4], (cells[5], cells[6]), tuple(cells[7:width]), extras.get(i, "")))
    return out


_FULL = re.compile(r"^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$")
_COLUMNS_2011 = ["FunctionGroup", "FunctionName", "", "Zone", "Code(ID1)", "FullCode(ID1)", "Code(ID2)", "FullCode(ID2)"]
_COLUMNS_2013 = ["Group", "Name", "", "Device", "Zone", "FullCode(ID1)", "FullCode(ID2)"]


def parse_blocks(rows: list[list[str]], document: Document, sheet: str) -> list[Row]:
    """The rows of a spreadsheet's sheet (D368: ``row, group, name, name 2, [device,] zone, the codes, a mark for each model``), the cells of a name, a device and a zone as
    the block they are in (the sheet draws a line above and below the rows a name covers, which the tool turns into the block's text on each row).

    **The heading must be the one the document is read with**: the 2011 sheet's is one row (``FunctionGroup``, ``FunctionName``, a second name column, ``Zone``, ``Code(ID1)``,
    ``FullCode(ID1)``, ``Code(ID2)``, ``FullCode(ID2)`` and the model of each column; its ``Zone`` is ``ZONE`` on one sheet: the case is not compared) and has no device; the 2013 and 2014 sheets' are two (the models' names in two lines,
    ``RX-V675 /`` and ``RX-A730``) with a ``Device`` column and the full codes only. **A row is a function at a zone** (a zone may be blank: the tuner's functions have none);
    a row after the heading that is none is an error."""
    heading = [h for h, _ in document.columns]
    n = len(heading)
    start = next((i for i, r in enumerate(rows) if r[1] in ("Function", "FunctionGroup")), None)
    if start is None:
        raise ValueError(f"{document.file} {sheet}: no heading row")
    if rows[start][1] == "FunctionGroup":
        short = True
        if [c.upper() for c in rows[start][1:9]] != [c.upper() for c in _COLUMNS_2011] or rows[start][9:9 + n] != heading or any(rows[start][9 + n:]):
            raise ValueError(f"{document.file} {sheet}: the heading {rows[start][1:]} is not the 2011 sheet's with {heading}")
        first, models = start + 1, 9
    else:
        short = False
        joined = [" ".join(x for x in pair if x) for pair in zip(rows[start][8:8 + n], rows[start + 1][8:8 + n])]
        if [c.upper() for c in rows[start + 1][1:8]] != [c.upper() for c in _COLUMNS_2013] or joined != heading or any(rows[start][8 + n:]) or any(rows[start + 1][8 + n:]):
            raise ValueError(f"{document.file} {sheet}: the heading {rows[start + 1][1:]} is not the 2013 sheet's with {heading} (the models read {joined})")
        first, models = start + 2, 8
    out: list[Row] = []
    group = ""
    for r in rows[first:]:
        if not any(r[1:]):
            continue
        group = r[1] or group
        name = " ".join(x for x in (r[2], r[3]) if x)
        device, zone = ("", r[4]) if short else (r[4], r[5])
        full = (r[6], r[8]) if short else (r[6], r[7])
        marks = tuple(r[models:models + n])
        if not name or zone not in ZONE_ORDER and zone != "" or not all(_FULL.match(c) for c in full) or not all(m.upper().startswith(("YES", "NO")) for m in marks):
            raise ValueError(f"{document.file} {sheet} row {r[0]}: not a function at a zone: {r[1:models + n]}")
        out.append(Row(0, int(r[0]), group, tidy(name), tidy(device), zone, (full[0].upper(), full[1].upper()), tuple(tidy(m) for m in marks),
                       where=f"sheet {sheet} line {r[0]}", short=(r[5].upper(), r[7].upper()) if short else ("", "")))
    return out


_MODEL_NUMBER = re.compile(r"\d{3,4}")


def parse_comments(rows: list[list[str]], document: Document) -> list[Row]:
    """The rows of one of the 2010 tables (D369: ``page, y, device, function group, function name, zone, Code(ID1), Code(ID2), comment``, the names again after them).

    The device, the group and the name are written once for the rows they cover and the library finds no merged cell, so **a blank cell is the one above** (a name over its
    zones' rows, a group over its functions, the device over a page break; a device written in brackets under another (``TUNER``, then ``(HD-Radio)``) is added to it). A zone of ``-`` is none. **The ``Comment`` says which models a row is for: the model numbers in it
    (``2067, 3067``; ``667exceptUC, 767exceptUC, 1067``; ``TUN./CH +(2067U/3067U), TUNING +(2067exceptU/3067exceptU)``); a comment with none (blank, ``U``, ``J``) is for all
    of the document's models.** The comment is in the key's citation."""
    out: list[Row] = []
    device = base = group = name = ""
    seen = False
    models = [m for _, ms in document.columns for m in ms]
    for row in rows:
        page, y, cells = int(row[0]), int(row[1]), [tidy(c) for c in row[2:]]
        if cells[0] == "Device":
            if cells[:7] != ["Device", "Function Group", "Function name", "ZONE", "Code(ID1)", "Code(ID2)", "Comment"]:
                raise ValueError(f"{document.file} page {page}: the heading {cells[:7]} is not the 2010 tables'")
            seen = True
            continue
        if not seen:
            raise ValueError(f"{document.file} page {page}: a row before the heading")
        if len(cells) != 9:
            raise ValueError(f"{document.file} page {page} row {y}: {len(cells)} cells, not 9")
        cell_device, cell_group, cell_name, zone, id1, id2, comment = cells[:7]
        if cell_device.startswith("(") and cell_device.endswith(")") and base:
            device = f"{base} {cell_device[1:-1]}"                    # ``TUNER`` and, on the rows below, ``(HD-Radio)``: the device is both
        elif cell_device:
            device = base = cell_device
        group, name = cell_group or group, cell_name or name
        zone = "" if zone == "-" else zone
        if zone not in ZONE_ORDER and zone != "" or not name:
            raise ValueError(f"{document.file} page {page} row {y}: the zone `{zone}` or the name `{name}` is not one of a function at a zone")
        listed = {f"RX-V{m}" for m in _MODEL_NUMBER.findall(comment)}
        if listed - set(models):
            raise ValueError(f"{document.file} page {page} row {y}: the comment `{comment}` names models the document does not")
        marks = tuple("YES" if not listed or all(m in listed for m in ms) else "NO" for _, ms in document.columns)
        out.append(Row(page, y, group, name, device, zone, (id1, id2), marks, comment=comment))
    return out


def frame_of(code: str) -> tuple[int, int, int, int] | str:
    """The four bytes of Yamaha's notation, in the order sent (``Yamaha IR Hex Converter``): two digits are a byte and its complement, four digits are two bytes."""
    m = _CODE.match(code)
    if not m:
        return f"`{code}` is not a code of the form `7E-2A` or `7F01-6C13`"
    first, second = m[1], m[2]
    c0 = int(first[:2], 16)
    c1 = int(first[2:], 16) if len(first) == 4 else c0 ^ 0xFF
    d0 = int(second[:2], 16)
    d1 = int(second[2:], 16) if len(second) == 4 else d0 ^ 0xFF
    return c0, c1, d0, d1


def mark_of(text: str) -> tuple[bool, str]:
    """Whether a model's cell says the code is its, and what it adds (``YES*J``: the Japan model's; ``YES*No key``: the remote has no key for it)."""
    upper = text.upper()
    if upper.startswith("YES"):
        return True, text[3:].strip()
    return False, ""


def label_of(row: Row) -> str:
    """The function's name as the table writes it, with the device when it is not the receiver (``TUNER DAB``) and the zone when it is not the main one."""
    label = row.name
    if row.device not in ("", "RECEIVER"):
        label += f" ({row.device})"
    if row.zone not in ("MAIN", ""):
        label += f" ({row.zone})"
    return label


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


@dataclass
class Entry:
    document: str
    sha: str
    row: Row
    label: str
    frame: tuple[int, int, int, int]
    cell: str
    note: str


@dataclass(frozen=True)
class BDDocument:
    file: str
    models: tuple[str, ...]              # the models a file is made for (the same table for each: the list's title names them)
    ids: tuple[str, ...]                 # the ID of each pair of data-code columns, left to right (the line ``custom code: 7C ID1 ID2`` says which)


BD_DOCUMENTS: tuple[BDDocument, ...] = (
    BDDocument("BD-S477_IR_code_list.pdf", ("BD-S477", "BD-S677 China"), ("ID1",)),          # ``BD-S477 & BD-S677 for China IR code comparison``, ``BD-S477 has only ID1``
    BDDocument("BD-S677_IR_code_list.pdf", ("BD-S677",), ("ID1", "ID2")),                    # ``BD-S677 (Except for China) IR code comparison``
)
BD_RC = "BD-S477_S677_RC_code_list.pdf"                                                      # the key layout of the three remotes: five columns of codes
BD_RC_COLUMNS = (("BD-S677", "ID1", "USA"), ("BD-S677", "ID2", "USA"), ("BD-S677", "ID1", "other destinations"), ("BD-S677", "ID2", "other destinations"),
                 ("BD-S477", "ID1", "BD-S477 and BD-S677 China"))
BD_RC_HEADINGS = ("For USA only", "Except USA and China", "BD-S477 all desti / BD-S677 China")
_HEX2 = re.compile(r"^[0-9A-F]{2}$")


@dataclass(frozen=True)
class BDRow:
    number: int
    name: str
    group: str                           # ``standard`` or ``Extended IR Code`` (the list's own heading before the extended codes)
    codes: tuple[tuple[int, int] | None, ...]   # the (data code 0, data code 1) of each column, left to right (``None`` where the row has none: only the key layout's rows)
    remark: str


def _lines(rows: list[list[str]]) -> list[tuple[int, list[tuple[float, str]]]]:
    """A sheet's ``words`` rows (``page, y, x, word, x, word...``) as the lines of words they are, the ruled tables' own rows (``page, R, y0, y1``) left out."""
    out = []
    for r in rows:
        if len(r) < 4 or r[1] == "R":
            continue
        out.append((int(r[1]), [(float(r[i]), r[i + 1]) for i in range(2, len(r) - 1, 2)]))
    return out


def _columns(line: list[tuple[float, str]], ids: tuple[str, ...], document: str) -> list[float]:
    """The places of the ``ID1`` and ``ID2`` words of the line that has the custom code: the columns of data codes are the words 40 points about each."""
    labels = [(x, w) for x, w in line if re.fullmatch(r"ID\d", w)]
    if tuple(w for _, w in labels) != ids:
        raise ValueError(f"{document}: the line of the custom code has the IDs {[w for _, w in labels]}, not {list(ids)}")
    return [x for x, _ in labels]


def _split_row(line: list[tuple[float, str]], columns: list[float], document: str) -> tuple[int, str, tuple[tuple[int, int] | None, ...], str]:
    """A row's number, name, pairs of data codes and remark. A word that is a hex pair within 40 points of a column's place is a code (the left word of a column's pair is its data code 0, the right
    one its data code 1); a word before the columns is the number and the name; a word after them the remark. A column with one code and not two, or a code that is not a hex pair, is refused."""
    if not line[0][1].isdigit():
        raise ValueError(f"{document}: the line `{' '.join(w for _, w in line)}` does not begin with a row number")
    number = int(line[0][1])
    left = min(columns) - 40
    name = tidy(" ".join(w for x, w in line[1:] if x < left))
    remark = tidy(" ".join(w for x, w in line[1:] if x >= left and not any(abs(x - c) < 40 for c in columns)))
    pairs: list[tuple[int, int] | None] = []
    for c in columns:
        words = [(x, w) for x, w in line[1:] if abs(x - c) < 40]
        if not words:
            pairs.append(None)
            continue
        if len(words) != 2 or not all(_HEX2.match(w) for _, w in words):
            raise ValueError(f"{document}: row {number} `{name}` has `{[w for _, w in words]}` where a column has two hex digit pairs")
        (x0, d0), (x1, d1) = sorted(words)
        pairs.append((int(d0, 16), int(d1, 16)))
    if not name:
        raise ValueError(f"{document}: row {number} has no name")
    return number, name, tuple(pairs), remark


def parse_bd(rows: list[list[str]], document: BDDocument) -> tuple[int, list[BDRow]]:
    """A BD list's custom code and its rows. The page's first line says ``IR code comparison``, the line ``custom code: 7C ID1 ID2`` the custom code (a byte and, as Yamaha writes it, its complement)
    and the columns, the line ``YAMAHA Name data code 0 data code 1 ...`` begins the table; a row is a number, a name and a pair of data codes in each column that has one; a line ``Extended IR Code``
    begins the extended codes; the numbers run from 1 without a gap. A row with data codes in no column, or one that is none of these, is refused."""
    lines = _lines(rows)
    if not lines or "comparison" not in {w for _, w in lines[0][1]}:
        raise ValueError(f"{document.file}: the first line is not that of an IR code comparison")
    at = next((i for i, (_, line) in enumerate(lines) if [w for _, w in line[:2]] == ["custom", "code:"]), None)
    if at is None:
        raise ValueError(f"{document.file}: no line gives the custom code")
    custom_line = lines[at][1]
    if len(custom_line) < 3 or not _HEX2.match(custom_line[2][1]):
        raise ValueError(f"{document.file}: the custom code `{[w for _, w in custom_line[:3]]}` is not a hex pair")
    custom = int(custom_line[2][1], 16)
    columns = _columns(custom_line, document.ids, document.file)
    head = next((i for i, (_, line) in enumerate(lines) if i > at and [w for _, w in line[:2]] == ["YAMAHA", "Name"]), None)
    if head is None:
        raise ValueError(f"{document.file}: no heading `YAMAHA Name` of the table")
    group = "standard"
    out: list[BDRow] = []
    for _, line in lines[head + 1:]:
        words = [w for _, w in line]
        if words == ["Extended", "IR", "Code"]:
            group = "Extended IR Code"
            continue
        if not words[0].isdigit():
            continue                                          # the footer (``BD-S477 ID1``)
        number, name, pairs, remark = _split_row(line, columns, document.file)
        if number != len(out) + 1:
            raise ValueError(f"{document.file}: row {number} follows row {len(out)}")
        if None in pairs:
            raise ValueError(f"{document.file}: row {number} `{name}` has no data codes in one of its {len(columns)} ID columns")
        out.append(BDRow(number, name, group, pairs, remark))
    if not out:
        raise ValueError(f"{document.file}: no rows")
    return custom, out


def parse_rc(rows: list[list[str]]) -> list[BDRow]:
    """The key layout of the three remotes (``BD-S677/S477 RC code comparison``): the lists' rows again, in **five columns of codes** under three headings (``For USA only``: ID1 and ID2;
    ``Except USA and China``: ID1 and ID2; ``BD-S477 all desti / BD-S677 China``: ID1), a row having a pair of data codes only in the columns of the remotes that have the key (``VUDU``: the
    USA's two; ``SETUP``: not the BD-S477's). A row's ``codes`` are the five columns' pairs, ``None`` where the column has none. The custom code must be the lists' ``7C``."""
    lines = _lines(rows)
    if not lines or "RC" not in {w for _, w in lines[0][1]}:
        raise ValueError(f"{BD_RC}: the first line is not that of an RC code comparison")
    at = next((i for i, (_, line) in enumerate(lines) if [w for _, w in line[:2]] == ["custom", "code:"]), None)
    if at is None:
        raise ValueError(f"{BD_RC}: no line gives the custom code")
    custom_line = lines[at][1]
    if len(custom_line) < 3 or custom_line[2][1] != "7C":
        raise ValueError(f"{BD_RC}: the custom code is `{[w for _, w in custom_line[:3]]}`, not 7C")
    columns = _columns(custom_line, ("ID1", "ID2", "ID1", "ID2", "ID1"), BD_RC)
    words = iter(w for _, line in lines[:at] for _, w in line)       # the three headings, as words, in order, above the columns
    if not all(any(w == e for w in words) for e in " ".join(BD_RC_HEADINGS).split()):
        raise ValueError(f"{BD_RC}: the headings {list(BD_RC_HEADINGS)} are not above the five columns")
    head = next((i for i, (_, line) in enumerate(lines) if i > at and [w for _, w in line[:2]] == ["YAMAHA", "Name"]), None)
    if head is None:
        raise ValueError(f"{BD_RC}: no heading `YAMAHA Name` of the table")
    out: list[BDRow] = []
    for _, line in lines[head + 1:]:
        if not line[0][1].isdigit():
            continue
        number = int(line[0][1])
        if number != len(out) + 1:
            raise ValueError(f"{BD_RC}: row {number} follows row {len(out)}")
        name = tidy(" ".join(w for x, w in line[1:] if x < min(columns) - 40))
        cells: list[tuple[int, int] | None] = []
        for c in columns:
            cell = sorted((x, w) for x, w in line[1:] if abs(x - c) < 40)
            if not cell:
                cells.append(None)
            elif len(cell) == 2 and all(_HEX2.match(w) for _, w in cell):
                cells.append((int(cell[0][1], 16), int(cell[1][1], 16)))
            else:
                raise ValueError(f"{BD_RC}: row {number} `{name}` has `{[w for _, w in cell]}` where a column has two hex digit pairs")
        if not name or not any(cells):
            raise ValueError(f"{BD_RC}: row {number} has no name or no code")
        out.append(BDRow(number, name, "standard", tuple(cells), ""))
    if not out:
        raise ValueError(f"{BD_RC}: no rows")
    return out


def _norm(text: str) -> str:
    """A name without its case and spaces (``SEARCH+`` and ``SEARCH-`` stay two names: the lists have both)."""
    return re.sub(r"\s+", "", text.casefold())


def build_bd(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    """The files of the BD-S477 and BD-S677 lists (DESIGN section 105): a file for each model and ID of a list, a key for each of its rows. **Each key is held against the key layout of the remotes**
    (the third PDF): a key it has as a row of the remote's own keys must have the same two data codes in the columns of the remotes that have it, and a key it does not have is an extended code (the
    layout is of the keys on the remotes). A key whose codes the layout gives otherwise is Untested, and so is a key whose ID1 codes are not a byte and its complement or whose ID2 code is not its ID1
    code with the low bit of the last byte flipped (the rule of all of the 1,316 rows of the receivers' tables), and a code two names share."""
    rc = parse_rc(snapshot.sheet_of(BD_RC, "words"))
    report.count(MAKER, f"{BD_RC}: rows read", len(rc))
    rc_sha = snapshot.sha(BD_RC)
    rc_by_name: dict[str, BDRow] = {}
    for r in rc:
        rc_by_name.setdefault(_norm(r.name), r)
    layout = {("BD-S677", "ID1"): (0, 2), ("BD-S677", "ID2"): (1, 3), ("BD-S477", "ID1"): (4,)}
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for document in BD_DOCUMENTS:
        custom, rows = parse_bd(snapshot.sheet_of(document.file, "words"), document)
        report.count(MAKER, f"{document.file}: rows read", len(rows))
        sha = snapshot.sha(document.file)
        entry = snapshot.documents[document.file]
        c0, c1 = custom, custom ^ 0xFF
        for index, id_name in enumerate(document.ids):
            columns = layout[(document.models[0].split()[0], id_name)]
            by_code: dict[tuple[int, int], list[str]] = {}
            for r in rows:
                by_code.setdefault(r.codes[index], []).append(r.name)
            members: list[tuple[str, dict[str, Any]]] = []
            for r in rows:
                d0, d1 = r.codes[index]
                function = d0 << 8 | d1
                doubts: list[str] = []
                if id_name == "ID1" and d1 != d0 ^ 0xFF:
                    doubts.append(f"the data code 1 `{d1:02X}` is not the complement of the data code 0 `{d0:02X}`, which every ID1 row of the receivers' tables is")
                if id_name == "ID2":
                    first = r.codes[0]
                    if first[0] != d0 or first[1] ^ 1 != d1:
                        doubts.append(f"the ID2 code is not the ID1 code `{first[0]:02X} {first[1]:02X}` with the low bit of its last byte flipped, which every ID2 row of the receivers' tables is")
                cite_more = ""
                row_rc = rc_by_name.get(_norm(r.name))
                if row_rc is None:
                    row_rc = next((x for x in rc if any(x.codes[col] and x.codes[col][0] == d0 for col in columns)), None)
                    if row_rc is not None:
                        cite_more += f"; the key layout names it `{row_rc.name}` (its row {row_rc.number})"
                if row_rc is None:
                    cite_more += "; the remotes' key layout has no key for it (an extended code)" if r.group == "Extended IR Code" else "; the remotes' key layout has no row for it"
                else:
                    seen = [(col, row_rc.codes[col]) for col in columns if row_rc.codes[col]]
                    for col, pair in seen:
                        if pair != (d0, d1):
                            doubts.append(f"the key layout's row {row_rc.number} gives `{pair[0]:02X} {pair[1]:02X}` in its {BD_RC_COLUMNS[col][1]} column of the remote for {BD_RC_COLUMNS[col][2]}")
                    if seen:
                        where = " and ".join(sorted({BD_RC_COLUMNS[col][2] for col, _ in seen}))
                        cite_more += f"; the key layout ({BD_RC}@{rc_sha}) has it as row {row_rc.number}, in the {id_name} column of the remote for {where}"
                    else:
                        cite_more += f"; the key layout's row {row_rc.number} has no code in the remote's {id_name} columns"
                if len(by_code[(d0, d1)]) > 1:
                    doubts.append("the list gives this code to " + " and ".join(f"'{n}'" for n in by_code[(d0, d1)]) + ", and a code is one function")
                if (c0, c1, function) not in gate:
                    gate[(c0, c1, function)] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": c0, "subdevice": c1, "function": function,
                                                                                          "confidence": TIER, "source": "probe"})
                if gate[(c0, c1, function)]:
                    report.unrepresented[(MAKER, document.file, f"does not compile: {gate[(c0, c1, function)]}")] += 1
                    continue
                cite = (f"{AWE}/{document.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']}) "
                        f"page 1 row '{r.number} {r.name}' (custom code {custom:02X}, data code 0 {d0:02X}, data code 1 {d1:02X}, {id_name}"
                        f"{', under the heading `Extended IR Code`' if r.group == 'Extended IR Code' else ''}): "
                        f"NEC1-f16 device {c0} subdevice {c1} function {function} (bytes {c0:02X} {c1:02X} {d0:02X} {d1:02X})")
                if r.remark:
                    cite += f"; the list's remark on the row is `{r.remark}`"
                cite += cite_more
                if doubts:
                    cite += "; " + "; ".join(doubts)
                members.append((r.name, {"id": "primary.irp", "type": "irp", "device": c0, "subdevice": c1, "function": function,
                                         "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
            if not members:
                continue
            for model in document.models:
                final = f"{model} ({id_name})"
                while (BRAND.casefold(), final.casefold()) in authored:
                    renamed = f"{final} (Yamaha table)"
                    report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                    final = renamed
                keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
                report.count(MAKER, "files")
                report.count(MAKER, "keys", len(keys))
                out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                            {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(PROTOCOL), "keys": keys}))
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    entries: dict[tuple[str, str], list[Entry]] = {}              # (model, id) -> the entries of every document that names the model
    for document in DOCUMENTS:
        sha = snapshot.sha(document.file)
        if document.layout == "blocks":
            rows = [row for sheet in ("Standard", "Extended") for row in parse_blocks(snapshot.sheet_of(document.file, f"{sheet}/blocks"), document, sheet)]
        elif document.layout == "comments":
            rows = parse_comments(snapshot.sheet_of(document.file, "grid"), document)
        else:
            rows = parse(snapshot.sheet_of(document.file, "grid"), document)
        report.count(MAKER, f"{document.file}: rows read", len(rows))
        for row in rows:
            for id_name, _, index in IDS:
                if row.short[index] and frame_of(row.short[index]) != frame_of(row.codes[index]):
                    report.notes.append((MAKER, f"{document.file} {row.place}", f"the abbreviated {id_name} code `{row.short[index]}` is not the full code `{row.codes[index]}`: the full one is read"))
        for row in rows:
            for id_name, _, index in IDS:
                cell = row.codes[index]
                frame = frame_of(cell) if cell else "the cell is blank"
                for column, (_, models) in enumerate(document.columns):
                    available, note = mark_of(row.marks[column])
                    if not available:
                        continue
                    if isinstance(frame, str):
                        report.unrepresented[(MAKER, document.file, f"{id_name}: {frame}")] += 1
                        continue
                    for model in models:
                        if model not in document.skip:
                            entries.setdefault((model, id_name), []).append(Entry(document.file, sha, row, label_of(row), frame, cell, note))
    out: list[tuple[str, dict[str, Any]]] = []
    for (model, id_name), listed in sorted(entries.items()):
        # a function the tables list twice with one code (the standard table's and the extended one's both have the tuner's bands) is one key: the first row is its
        # citation and the others say where else it is
        items: list[Entry] = []
        where: dict[int, list[str]] = {}
        for e in listed:
            twin = next((i for i, o in enumerate(items) if (o.label, o.frame) == (e.label, e.frame)), None)
            if twin is None:
                items.append(e)
            else:
                where.setdefault(twin, []).append(f"{e.document} {e.row.place}")
        # a label two keys share with different codes keeps its group, and the code where that is not enough
        counts = Counter(e.label for e in items)
        grouped = Counter(f"{e.label} ({e.row.group})" for e in items if counts[e.label] > 1)
        members: list[tuple[str, dict[str, Any]]] = []
        by_form: dict[tuple[int, int, int, int], list[str]] = {}
        for e in items:
            by_form.setdefault(e.frame, []).append(e.label)
        for i, e in enumerate(items):
            c0, c1, d0, d1 = e.frame
            function = d0 << 8 | d1
            if (c0, c1, function) not in gate:
                gate[(c0, c1, function)] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": c0, "subdevice": c1,
                                                                                      "function": function, "confidence": TIER, "source": "probe"})
            if gate[(c0, c1, function)]:
                report.unrepresented[(MAKER, e.document, f"does not compile: {gate[(c0, c1, function)]}")] += 1
                continue
            entry = snapshot.documents[e.document]
            host = next(d.host for d in DOCUMENTS if d.file == e.document)
            cite = (f"{host}/{e.document}@{e.sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']}) "
                    f"{e.row.place} row '{e.row.group} / {e.row.name}' ({e.row.device or 'no device'} {e.row.zone or 'no zone'}, Code ({id_name}) '{e.cell}'): "
                    f"NEC1-f16 device {c0} subdevice {c1} function {function} (bytes {c0:02X} {c1:02X} {d0:02X} {d1:02X})")
            if e.row.extra:
                cite += f"; the row's own name cells say `{e.row.extra}`"
            if e.row.comment:
                cite += f"; the table's comment on the row is `{e.row.comment}`"
            if i in where:
                cite += "; the tables list it again, with the same code, on " + " and ".join(where[i])
            doubts = []
            if e.note.startswith("*J"):
                doubts.append(f"the table marks it `YES{e.note}`: the Japan model's")
            elif e.note:
                cite += f"; the table marks it `YES{e.note}`"
            if len(by_form[e.frame]) > 1:
                doubts.append("the tables give this code to " + " and ".join(f"'{n}'" for n in by_form[e.frame]) + ", and a code is one function")
            if doubts:
                cite += "; " + "; ".join(doubts)
            label = e.label
            if counts[e.label] > 1:
                label = f"{e.label} ({e.row.group})"
                if grouped[label] > 1:
                    label = f"{e.label} ({e.row.group}, {e.cell})"
            members.append((label, {"id": "primary.irp", "type": "irp", "device": c0, "subdevice": c1, "function": function,
                                    "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
        if not members:
            continue
        final = f"{model} ({id_name})"
        while (BRAND.casefold(), final.casefold()) in authored:
            renamed = f"{final} (Yamaha table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(PROTOCOL), "keys": keys}))
    out.extend(build_bd(snapshot, authored, report))
    return out
