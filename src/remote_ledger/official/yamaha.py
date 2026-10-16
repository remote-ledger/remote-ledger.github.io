"""Yamaha's standard and extended IR code tables for its receivers and amplifiers (DESIGN section 87).

Yamaha wrote for its custom installers one table of IR codes for each family of receivers (``Standard IR codes for RX-V385-585``, ``Yamaha Extended IR codes for
RX-V685, RX-A780 - RX-A3080 and CX-A5200``, ``... for WXA-50, WXC-50``, ``... for XDA-QS5400RK and XDA-AMP5400RK``): a row for each function of a group (``POWER``,
``INPUT``, ``VOLUME``, ``MENU``...) and each zone it is addressed to (``MAIN``, ``ZONE2``, ``ZONE3``, ``ZONE4``, ``ALL``), **two codes, ``Code (ID1)`` and ``Code (ID2)``**,
and a ``YES`` or ``NO`` for each model of the family. The standard tables are the codes of the remote's keys, the extended ones the discrete functions (``POWER ON`` and
``STANDBY OFF``, each input, each scene, each sound program). The PDFs are on the support-documents folder of SnapAV, an American distributor of Yamaha's, and are read from
the CSV of each table's rows (``grid``, D332) pinned by SHA-256 (D123).

**A code is Yamaha's own notation, which Yamaha's IR Hex Converter (``yamaha.com/ypab/irhex_converter.asp``, in the Wayback Machine) writes down: ``7E-2A``, ``7A-453A``,
``7F01-6C13``, ``7E-2AD4``**: the first group is the custom code, the second the data, **a group of two digits is a byte and its complement** (``7E`` is ``7E 81``) and a group
of four is the two bytes as written (``7F01`` is ``7F 01``, ``2AD4`` is ``2A D4``), the four bytes sent in the order written. **A key is the ledger's ``NEC1-f16``**: NEC1's frame
whose fourth byte is a field of its own (a ``function`` of ``F:E`` sixteen bits), the device the first byte and the subdevice the second. **The ID2 code of a row is its ID1 code
with the low bit of its last byte flipped, in every one of the 1,316 rows**: the ID is a setting of the receiver and its remote that tells two of them apart.
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
IDS = (("ID1", "Code (ID1)", 0), ("ID2", "Code (ID2)", 1))
_CODE = re.compile(r"^([0-9A-F]{2}|[0-9A-F]{4})-([0-9A-F]{2}|[0-9A-F]{4})$")


@dataclass(frozen=True)
class Document:
    file: str
    columns: tuple[tuple[str, tuple[str, ...]], ...]       # the heading of each model column and the models it is for


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


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    entries: dict[tuple[str, str], list[Entry]] = {}              # (model, id) -> the entries of every document that names the model
    for document in DOCUMENTS:
        sha = snapshot.sha(document.file)
        rows = parse(snapshot.sheet_of(document.file, "grid"), document)
        report.count(MAKER, f"{document.file}: rows read", len(rows))
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
                where.setdefault(twin, []).append(f"{e.document} page {e.row.page}")
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
            cite = (f"{HOST}/{e.document}@{e.sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']}) "
                    f"page {e.row.page} row '{e.row.group} / {e.row.name}' ({e.row.device or 'no device'} {e.row.zone}, Code ({id_name}) '{e.cell}'): "
                    f"NEC1-f16 device {c0} subdevice {c1} function {function} (bytes {c0:02X} {c1:02X} {d0:02X} {d1:02X})")
            if e.row.extra:
                cite += f"; the row's own name cells say `{e.row.extra}`"
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
    return out
