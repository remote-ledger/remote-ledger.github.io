"""JVC's D-ILA projector IR emulation guide, long hex format, 2012 version 3.0 (DESIGN section 63).

A 37-page PDF for 22 DLA-HD, X and RS projectors: an entry is a function's name (usually with the models it applies to, in
parentheses), ``Device Code: 115 (0x73) Function: 214 (0xD6)`` and the Pronto hex of the code. **The Pronto hex is checked
against the stated code**: it is JVC's frame (a lead-in sent once, then 16 bits and a stop mark, the device byte and the
function byte, least significant bit first), so each entry's hex must decode to the device and function the entry states; the
code is read as the ledger's ``JVC`` ``irp`` form (device 115, the function), and an entry whose hex does not decode to it
(the guide's text has three that are damaged) is Untested and says so, as Oppo's rows are. The guide's last page says it
"may be distributed freely, as long as the author and copyright information remain intact": the document is pinned whole and
the notices say who wrote it.

**The LX-NZ3 and LX-UH1 (DESIGN section 91)** are JVC's home laser projectors, and their IR code sheets are not the guide's: two PDFs of one table each
(``RCU Code of LX-NZ3`` and ``LX-UH1 Remote Control Codes``: a key's name, the custom code and its second byte, the data code and its complement), on SnapAV's
protocols-and-drivers folder for projection screens, and a third PDF of the LX-NZ3's codes as Pronto hex (``LX-NZ3 long hex commands``). **A key is the ledger's NEC1**
(the frame's four bytes: the custom code ``00`` the device, its second byte ``6A`` the subdevice, which is not the device's complement, the data code the function and
its complement), a file for each of the two products, **at 38.4 kHz: the long hex's frequency word is 0x006C, and the UH1's sheet gives none (read as the NZ3's)**. The long hex is
decoded and must say the table's code (JVC's own check, as the guide's is); a data code whose complement is not the table's makes the key Untested.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug
from .emotiva import decode_frame

MAKER = "jvc"
BRAND = "JVC"
DOCUMENT = "DILA_Remote_Code_Emulation_Long_Hex.pdf"
SHEET = "pages"
#: The ledger's JVC block (carrier and minimum sends as the JP1 and IR Blaster imports give it).
PROTOCOL = {"name": "JVC", "carrierHz": 37900, "minSends": 1}
#: The lists write X70 and X90 for the cover's DLA-X70R and DLA-X90R.
_RENAMED = {"X70": "X70R", "X90": "X90R"}
_COVER_LINE = re.compile(r"^DLA-[A-Z0-9]+(?: / DLA-[A-Z0-9]+)*$")
_DEVICE = re.compile(r"^Device Code:\s*(\d+)\s*\(0x([0-9A-Fa-f]+)\)\s*Function:\s*(\d+)\s*\(0x([0-9A-Fa-f]+)\)")
_FOOTER = re.compile(r"^Page \d+ of \d+$")
_HEXISH = re.compile(r"^(?=.*\d)[0-9A-Fa-f]+(?: [0-9A-Fa-f]+)*$")
_WORD = re.compile(r"^[0-9A-Fa-f]{4}$")
_TOKEN = re.compile(r"^([A-Za-z]*)(\d+)([A-Za-z]?)$")
_LEAD = (0x0141, 0x00A0)


@dataclass(frozen=True)
class Entry:
    page: int
    title: str
    device: int
    function: int
    stated: str             # the entry's line of numbers as written, for the citation
    consistent: bool        # the decimal and the hex it states are one number each
    pronto: str


@dataclass(frozen=True)
class Group:
    models: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class Alternative:
    """A name and the groups of models it applies to; no groups is every model."""
    name: str
    groups: tuple[Group, ...]


@dataclass(frozen=True)
class Guide:
    models: tuple[str, ...]
    entries: tuple[Entry, ...]
    notice: str


def parse_pages(rows: list[list[str]]) -> Guide:
    """The guide from its page-numbered lines: the cover's models (page 1) and an entry for every ``Device Code`` line.

    The lines after a ``Device Code`` line that look like hex are its Pronto text, however damaged; the lines before the next
    one are the next entry's title."""
    models: list[str] = []
    entries: list[Entry] = []
    title: list[str] = []
    notice = ""
    last: int | None = None
    pending: tuple[int, int, int, str, bool] | None = None
    pronto: list[str] = []

    def close() -> None:
        nonlocal pending, pronto
        if pending is not None:
            page, device, function, stated, consistent = pending
            entries.append(Entry(page, entry_title, device, function, stated, consistent, " ".join(pronto)))
        pending, pronto = None, []

    entry_title = ""
    for row in rows:
        page, line = int(row[0]), row[1].strip()
        if _FOOTER.match(line):
            continue
        if page == 1:
            if _COVER_LINE.match(line):
                models += [m.strip() for m in line.split("/")]
            continue
        if "may be distributed freely" in line:
            notice = line
        m = _DEVICE.match(line)
        if m:
            close()
            entry_title = " ".join(" ".join(title).split())
            title = []
            pending = (page, int(m[1]), int(m[3]), line, int(m[1]) == int(m[2], 16) and int(m[3]) == int(m[4], 16))
        elif pending is not None and not title and _HEXISH.match(line):
            pronto.append(line)
        else:
            title.append(line)
    close()
    return Guide(tuple(models), tuple(entries), notice)


def decode(pronto: str) -> tuple[int, int] | str:
    """``(device, function)`` the guide's Pronto hex sends, or why it is not JVC's frame as the guide writes it.

    ``0000 006D 0001 0011``: a Pronto raw code of one pair sent once (the 8.4 ms and 4.2 ms lead-in) and 17 repeated (sixteen
    bits, each a 0x14 mark and a 0x14 space for a 0 or a 0x3C one for a 1, and a stop mark); the final space varies with the
    bits, the frame being padded to a period."""
    words = pronto.split()
    if not words:
        return "the entry has no Pronto hex"
    bad = next((w for w in words if not _WORD.match(w)), None)
    if bad is not None:
        return f"the guide's Pronto text is damaged ('{bad}' is not four hex digits)"
    numbers = [int(w, 16) for w in words]
    if numbers[:4] != [0, 0x6D, 1, 17] or len(numbers) != 4 + 2 * 18:
        return "the Pronto hex is not the guide's one pair and seventeen"
    pairs = numbers[4:]
    if tuple(pairs[:2]) != _LEAD:
        return "the lead-in is not 0141 00A0"
    bits = []
    for at in range(2, 34, 2):
        mark, space = pairs[at], pairs[at + 1]
        if mark != 0x14 or space not in (0x14, 0x3C):
            return "a bit is not 0014 0014 or 0014 003C"
        bits.append(1 if space == 0x3C else 0)
    if pairs[34] != 0x14:
        return "the stop mark is not 0014"

    def byte(chunk: list[int]) -> int:
        return sum(bit << n for n, bit in enumerate(chunk))

    return byte(bits[:8]), byte(bits[8:])


def expand(text: str) -> tuple[str, ...] | None:
    """``HD350/750/RS10/20`` as the models ``HD350, HD750, RS10, RS20`` (a number takes the letters before it), or None where
    the text is not a list of models."""
    out: list[str] = []
    prefix = ""
    for token in (t.strip() for t in text.split("/")):
        m = _TOKEN.match(token)
        if not m:
            return None
        prefix = m[1] or prefix
        name = f"{prefix}{m[2]}{m[3]}"
        out.append(_RENAMED.get(name, name))
    return tuple(out)


def model_group(inner: str) -> Group | None:
    """A parenthesis that lists models, with the note after a dash (``X7/X9 - Film Mode Only``) or before an ``on``
    (``PC input on HD750/X7``), else None."""
    on = re.match(r"^(.+?)\s+on\s+([A-Za-z0-9/]+)$", inner)
    if on and (models := expand(on[2])) is not None:
        return Group(models, on[1].strip())
    parts = re.split(r"\s+[–-]\s+", inner, maxsplit=1)
    models = expand(parts[0])
    return None if models is None else Group(models, parts[1].strip() if len(parts) > 1 else "")


def parse_title(text: str) -> tuple[Alternative, ...]:
    """The names an entry gives its function and the models of each. A parenthesis that is not a list of models
    (``(PC)``, ``(Color Transient Improvement)``) is part of the name; a text after a list of models starts another name
    (``Anamorphic - Off (X3/X7) Vertical Stretch - Off (HD350)``: two names of one code)."""
    out: list[Alternative] = []
    name = ""
    groups: list[Group] = []
    for piece in re.split(r"(\([^()]*\))", text):
        if piece.startswith("(") and piece.endswith(")"):
            group = model_group(piece[1:-1])
            if group is None:
                name += piece
            else:
                groups.append(group)
        else:
            if groups and piece.strip():
                out.append(Alternative(" ".join(name.split()), tuple(groups)))
                name, groups = "", []
            name += piece
    out.append(Alternative(" ".join(name.split()), tuple(groups)))
    return tuple(out)


def tidy(name: str) -> str:
    """The label a key shows for a name as the guide writes it (the citation keeps the title as written).

    A name is a function and often a description (``Menu (On/Off toggle)``), uses a spaced dash as a separator (``Power - On``)
    and, for the cursor keys, a triangle. The description at the end, the triangle and the sentence after ``Lens Aperture - If``
    are dropped, and a dash between words becomes a colon (``Power: On``); a dash at the end is a minus and stays
    (``Brightness -``). So ``Power: On``, ``OK`` and ``Cursor Up`` are what the key vocabulary (D84) knows them as."""
    sentence = re.match(r"^(.*?[–+-])\s+If\b", name)
    if sentence:
        name = sentence[1]
    name = re.sub(r"\s*[▲▼◄►]", "", name)
    if not re.fullmatch(r"\([^()]*\)", name.strip()):
        name = re.sub(r"\s*\([^()]*\)\s*$", "", name)
    return re.sub(r"\s+[–-]\s+(?=\S)", ": ", name).strip()


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build_guide(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    guide = parse_pages(snapshot.sheet_of(DOCUMENT, SHEET))
    sha = snapshot.sha(DOCUMENT)
    where = f"jvc.com D-ILA remote emulation guide/{DOCUMENT}"
    cover = {m.removeprefix("DLA-"): m for m in guide.models}
    if guide.notice:
        report.notes.append((MAKER, DOCUMENT, guide.notice))
    gate: dict[tuple, str | None] = {}
    members: dict[str, list[tuple[str, int, dict[str, Any]]]] = {m: [] for m in guide.models}
    taken: set[tuple[str, int]] = set()
    for entry in guide.entries:
        report.count(MAKER, "entries read")
        if not entry.consistent:
            report.notes.append((MAKER, f"{DOCUMENT} page {entry.page}", f"'{entry.stated}': the decimal and the hex it states differ"))
            report.unrepresented[(MAKER, DOCUMENT, "the entry's decimal and hex differ")] += 1
            continue
        key = (entry.device, entry.function)
        if key not in gate:
            gate[key] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                "id": "primary.irp", "type": "irp", "device": entry.device, "function": entry.function,
                "confidence": TIER, "source": "probe"})
        if gate[key]:
            report.unrepresented[(MAKER, DOCUMENT, f"does not compile: {gate[key]}")] += 1
            continue
        said = decode(entry.pronto)
        agrees = said == (entry.device, entry.function)
        if not agrees:
            report.count(MAKER, "entries whose Pronto hex does not say the stated code")
            report.notes.append((MAKER, f"{DOCUMENT} page {entry.page}",
                                 f"'{entry.title}' (function {entry.function}): "
                                 + (said if isinstance(said, str) else f"its Pronto hex says device {said[0]}, function {said[1]}")))
        for alt in parse_title(entry.title):
            named: dict[str, list[str]] = {}
            if not alt.groups:
                named = {m: [] for m in guide.models}
            for group in alt.groups:
                for token in group.models:
                    model = cover.get(token)
                    if model is None:
                        report.unrepresented[(MAKER, DOCUMENT, f"a model the cover does not list: {token}")] += 1
                        continue
                    named.setdefault(model, [])
                    if group.note:
                        named[model].append(group.note)
            for model, notes in named.items():
                if (model, entry.function) in taken:
                    report.count(MAKER, "functions the guide lists twice for a model")
                    continue
                taken.add((model, entry.function))
                cite = (f"{where}@{sha} (retrieved {snapshot.retrieved}) page {entry.page} '{entry.title}' "
                        f"({entry.stated.rstrip()}): JVC device {entry.device} function {entry.function}")
                cite += ("; the guide's Pronto hex decodes to the same" if agrees else
                         "; the guide's Pronto hex does not say it here (" + (said if isinstance(said, str)
                                                                                else f"it says device {said[0]}, function {said[1]}")
                         + "), so the key rests on the stated code alone")
                if not alt.groups:
                    cite += "; the guide names no models for it, read as every model on its cover"
                if notes:
                    cite += "; the guide says of these models: " + "; ".join(notes)
                members[model].append((tidy(alt.name), entry.function, {
                    "id": "primary.irp", "type": "irp", "device": entry.device, "function": entry.function,
                    "confidence": TIER if agrees else DOUBTFUL_TIER, "source": cite}))
    out: list[tuple[str, dict[str, Any]]] = []
    for model in guide.models:
        rows = members[model]
        if not rows:
            continue
        if (BRAND.casefold(), model.casefold()) in authored:
            report.collisions.append((MAKER, model))
            continue
        names = key_names([r[0] for r in rows])
        keys = {n: {"label": r[0], "forms": [r[2]]} for n, r in zip(names, rows)}
        report.count(MAKER, "keys", len(keys))
        report.count(MAKER, "files")
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(model)}.json",
                    {"manufacturer": BRAND, "model": model, "aliases": [], "controls": [f"{BRAND} {model}"],
                     "protocol": dict(PROTOCOL), "keys": keys}))
    return out


# --- the LX-NZ3 and LX-UH1 sheets ---------------------------------------------------------------------------------------

LX_HOST = "snapav.com JVC LX projector code sheets"
LX_PROTOCOL = {"name": "NEC1", "carrierHz": 38400, "minSends": 1}          # the long hex's frequency word is 0x006C, which is NEC1's own 38.4 kHz


@dataclass(frozen=True)
class LxDocument:
    file: str
    model: str
    long_hex: str | None            # the document of the same codes as Pronto hex, where there is one
    pages: bool = False             # the sheet's text is read too (the footnote of an asterisk)


LX_DOCUMENTS: tuple[LxDocument, ...] = (
    LxDocument("RCU Code of LX-NZ3_20191114.pdf", "LX-NZ3", "LX-NZ3 long hex commands.pdf", pages=True),
    LxDocument("LX-UH1 Remote Control Codes.pdf", "LX-UH1", None),
)
_LX_HEX = re.compile(r"^[0-9A-Fa-f]{2}$")
_LX_LONG = re.compile(r"^(.+?): NEC1 Device: (\d+)\.(\d+) Function: (\d+) S=(\d+)$")


@dataclass(frozen=True)
class LxRow:
    page: int
    printed: str                    # the key's name as the sheet writes it
    label: str                      # without the asterisk and the notes
    notes: tuple[str, ...]          # what the name says besides the key: ``Remote Code only``, ``Direct Input Sel.``
    custom: int | None              # None: the row has no code (the sheet's dashes)
    custom2: int | None
    data: int | None
    data2: int | None

    @property
    def complement(self) -> bool:
        return self.data is not None and self.data2 == self.data ^ 0xFF


def lx_name(printed: str, footnote: str | None) -> tuple[str, tuple[str, ...]]:
    """``(the label, the notes)`` of a key's name: the asterisk is the sheet's footnote (``*Remote Code only``) and a parenthesis is a note (``(for Remote only)``,
    ``(Direct Input Sel.)``)."""
    notes: list[str] = []
    name = printed
    if "*" in name:
        if footnote is None:
            raise ValueError(f"'{printed}': an asterisk and no footnote on the page")
        notes.append(footnote)
        name = name.replace("*", "")
    for note in re.findall(r"\(([^()]*)\)", name):
        notes.append(note)
    name = re.sub(r"\s*\([^()]*\)", "", name)
    return " ".join(name.split()), tuple(notes)


def parse_lx(table: list[list[str]], pages: list[list[str]] | None) -> list[LxRow]:
    """The rows of a sheet's ``tables`` (``page, table, cells``): the header ``KEY NAME`` and a row for each key, whose custom code, its second byte, data code
    and complement are two hex digits (a row of dashes is a key with no code). The footnote is the page's line ``*Remote Code only``, which a name with an asterisk needs."""
    footnote = next((line[1][1:].strip() for line in (pages or []) if len(line) > 1 and line[1].startswith("*") and "only" in line[1]), None)
    out: list[LxRow] = []
    started = False
    for row in table:
        cells = [c.strip() for c in row[2:]]
        if cells and cells[0] == "KEY NAME":
            started = True
            continue
        if not started or not cells or not cells[0]:
            continue
        codes = cells[1:5]
        label, notes = lx_name(cells[0], footnote)
        if all(c in ("-", "") for c in codes):
            out.append(LxRow(int(row[0]), cells[0], label, notes, None, None, None, None))
            continue
        if not all(_LX_HEX.match(c) for c in codes):
            raise ValueError(f"page {row[0]}: the row '{cells[0]}' has codes {codes}, which are not four hex bytes")
        out.append(LxRow(int(row[0]), cells[0], label, notes, *(int(c, 16) for c in codes)))
    if not out:
        raise ValueError("no rows after the header 'KEY NAME'")
    return out


@dataclass(frozen=True)
class LongHex:
    page: int
    name: str
    stated: str                     # ``NEC1 Device: 0.106 Function: 94 S=106``
    device: int
    subdevice: int
    function: int
    pronto: str


def parse_long_hex(rows: list[list[str]]) -> list[LongHex]:
    """The entries of the long hex document's lines (``page, line``): ``INPUT: NEC1 Device: 0.106 Function: 94 S=106`` and the Pronto hex on the lines after it."""
    out: list[LongHex] = []
    pending: tuple[int, str, str, int, int, int] | None = None
    text: list[str] = []

    def close() -> None:
        if pending is not None:
            out.append(LongHex(pending[0], pending[1], pending[2], pending[3], pending[4], pending[5], " ".join(text)))

    for row in rows:
        page, line = int(row[0]), " ".join(row[1].split())
        if m := _LX_LONG.match(line):
            close()
            text = []
            pending = (page, m.group(1), line[len(m.group(1)) + 2:], int(m.group(2)), int(m.group(3)), int(m.group(4)))
            if int(m.group(5)) != int(m.group(3)):
                raise ValueError(f"page {page}: '{line}' says S={m.group(5)} and a subdevice of {m.group(3)}")
        elif pending is not None and re.fullmatch(r"(?:[0-9A-Fa-f]{4}\s?)+", line):
            text.append(line)
    close()
    return out


def build_lx(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for document in LX_DOCUMENTS:
        rows = parse_lx(snapshot.sheet_of(document.file, "tables"), snapshot.sheet_of(document.file, "pages") if document.pages else None)
        entry, sha = snapshot.documents[document.file], snapshot.sha(document.file)
        prefix = f"{LX_HOST}/{document.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']})"
        long_hex: dict[int, LongHex] = {}
        long_prefix = ""
        if document.long_hex:
            for item in parse_long_hex(snapshot.sheet_of(document.long_hex, "pages")):
                long_hex[item.function] = item
            long_entry = snapshot.documents[document.long_hex]
            long_prefix = (f"{LX_HOST}/{document.long_hex}@{snapshot.sha(document.long_hex)} (retrieved {long_entry.get('retrieved', snapshot.retrieved)}, "
                           f"from the Wayback Machine snapshot {long_entry['snapshot']})")
            report.count(MAKER, f"{document.long_hex}: entries read", len(long_hex))
        members: list[tuple[str, dict[str, Any]]] = []
        for row in rows:
            report.count(MAKER, f"{document.file}: rows read")
            if row.custom is None:
                report.unrepresented[(MAKER, document.file, "a key with no code (the sheet's dashes)")] += 1
                continue
            code = (row.custom, row.custom2, row.data)
            if code not in gate:
                gate[code] = form_compiles(IMPORT_ROOT, LX_PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": row.custom, "subdevice": row.custom2, "function": row.data,
                                                                         "confidence": TIER, "source": "probe"})
            if gate[code]:
                report.unrepresented[(MAKER, document.file, f"does not compile: {gate[code]}")] += 1
                continue
            cite = (f"{prefix} page {row.page} table row '{row.printed}' (custom code {row.custom:02X} {row.custom2:02X}, data code {row.data:02X} {row.data2:02X}): "
                    f"NEC1 device {row.custom} subdevice {row.custom2} function {row.data}")
            if row.notes:
                cite += "; the sheet says of it: " + "; ".join(row.notes)
            doubts: list[str] = []
            if not row.complement:
                doubts.append(f"its data code {row.data:02X} and the code beside it {row.data2:02X} are not complements, and NEC1 sends the complement")
            if document.long_hex:
                item = long_hex.get(row.data)
                if item is None:
                    doubts.append("the long hex document has no entry of this function")
                else:
                    said = decode_frame(item.pronto)
                    where = f"{long_prefix} page {item.page} '{item.name}: {item.stated}'"
                    if isinstance(said, str):
                        doubts.append(f"the long hex ({where}) is not a NEC frame: {said}")
                    elif said.bytes != (row.custom, row.custom2, row.data, row.data ^ 0xFF) or (item.device, item.subdevice, item.function) != (row.custom, row.custom2, row.data):
                        doubts.append(f"the long hex ({where}) says device {item.device} subdevice {item.subdevice} function {item.function} and decodes to {said.bytes}")
                    else:
                        cite += f"; the long hex document says the same ({where}: its Pronto hex, frequency word 0x{said.carrier:04X}, decodes to these four bytes)"
                        if lx_name(item.name, "")[0] != row.label:
                            cite += f", under the name '{item.name}'"
            for d in doubts:
                note = (MAKER, f"{document.file} page {row.page}", f"'{row.printed}': {d}")
                if note not in report.notes:
                    report.notes.append(note)
            if doubts:
                cite += "; " + "; ".join(doubts)
            members.append((row.label, {"id": "primary.irp", "type": "irp", "device": row.custom, "subdevice": row.custom2, "function": row.data,
                                        "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
        if document.long_hex and (extra := sorted(set(long_hex) - {row.data for row in rows if row.data is not None})):
            for function in extra:
                report.notes.append((MAKER, document.long_hex, f"'{long_hex[function].name}' (function {function}) is in the long hex document and not in the sheet's table"))
        name = document.model
        while (BRAND.casefold(), name.casefold()) in authored:
            renamed = f"{name} (JVC sheet)"
            report.notes.append((MAKER, name, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), name.casefold())]} has the name (R15)"))
            name = renamed
        keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(name)}.json",
                    {"manufacturer": BRAND, "model": name, "aliases": [], "controls": [f"{BRAND} {document.model}"], "protocol": dict(LX_PROTOCOL), "keys": keys}))
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    return build_guide(snapshot, authored, report) + build_lx(snapshot, authored, report)
