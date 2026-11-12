"""Denon's short remote code sheets of 2006 to 2012 (DESIGN section 98): the zone 2 and zone 3 sheets of eleven receivers, the CD changers DCM-390/290/27, the Blu-ray and DVD
players DVD-3800BDCI and DVD-2500BTCI, the three S-series systems and the PMA/DCD pair's remote RC-1179.

They are on ``assets.denon.com`` like §94's lists and have no notice. Each is a table of bits, one layout each, and each is read by the layout's rule with every check the document
itself gives:

* **a zone sheet** is rows of 48 bits (the whole Kaseikyo frame) and a ``Function`` name; its header says the GENRE1 and GENRE2, and its rows' parity byte is a check of both;
* **the RC-1033** (CD changers) is a Sharp table whose row labels ``C<n>`` are the data's value;
* **the RC-1179** is a Sharp table of 15 bits per key for two products (the amplifier's keys and the CD player's) and a Kaseikyo table of four rows;
* **the DVD-3800BDCI and DVD-2500BTCI** have a Kaseikyo table whose identifier names the key and the data (``16`` and ``010``) and a Sharp sub code table;
* **the S-series** have the Kaseikyo bytes written out in full, with the GENRE1, GENRE2, data code and parity byte also given in hexadecimal, and the S-81 a Sharp table with the CH
  number, the extension bits, the data and the address written the most significant bit first.

A line that looks like a row (a row label, or many bit columns) and cannot be read is a note of the import's report, never dropped silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .denon import VENDOR, DocRow, nf, value
from .denon_pdf import PDF_HOST, page_lines

_ONE = re.compile(r"^[01]$")
_BYTE = re.compile(r"^[01]{8}$")
KASEIKYO_VENDOR_BITS = VENDOR            # 0x54 0x32 as 16 bits, first bit first
GENRES = {("00010", "10"): "CD", ("00010", "01"): "VIDEO CD", ("00110", "10"): "RCVR", ("01000", "11"): "AV AMP", ("01000", "10"): "PRE AMP"}


def parity_of(genre1: int, genre2: int, data: int, ident: int = 0) -> int:
    """The parity byte of a Kaseikyo frame of Denon: the XOR of the bytes 3, 4 and 5 (GENRE1 and the vendor's parity nibble, GENRE2 and the data's low bits, the data's high bits and the ID)."""
    byte3, byte4, byte5 = genre1 << 4, genre2 | (data & 0x0F) << 4, data >> 4 | ident << 6
    return byte3 ^ byte4 ^ byte5


@dataclass(frozen=True)
class SheetDocument:
    file: str
    layout: str                                    # ``zone``, ``dcm``, ``rc1179``, ``dvd``, ``bytes`` (the S-32 and S-52) or ``s81``
    models: tuple[str, ...]                        # the files: a Sharp and a Kaseikyo file for each that has rows of the kind
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = ()
    title: str = ""                                # the model the title names, as a zone sheet's check (``AVR-1508``)
    group: str = ""                                # a zone sheet's zone, as the file says


SHEETS: tuple[SheetDocument, ...] = (
    *[SheetDocument(f"avr-{m.lower()}_zone2-ircodes.pdf", "zone", (f"AVR-{m} Zone 2",), ((f"AVR-{m} Zone 2", (f"AVR-{m}",)),), f"AVR-{m}", "ZONE2")
      for m in ("588", "688", "788", "888", "988", "1508", "1708", "1908", "2308CI", "2808CI")],
    SheetDocument("avr-2808ci_zone3-ircodes.pdf", "zone", ("AVR-2808CI Zone 3",), (("AVR-2808CI Zone 3", ("AVR-2808CI",)),), "AVR-2808CI", "ZONE3"),
    SheetDocument("dcm-390_290_27-ircodes.pdf", "dcm", ("DCM-390", "DCM-290", "DCM-27")),
    SheetDocument("dcd1650re_2020ae_pma2000re_2020ae_ir_code_v01.pdf", "rc1179", ("PMA-2000RE", "PMA-2020AE", "DCD-1650RE", "DCD-2020AE")),
    SheetDocument("dvd-3800bdci_dvd-2500btci_ircodes.pdf", "dvd", ("DVD-3800BDCI", "DVD-2500BTCI")),
    SheetDocument("s-32-ircodes_v01.pdf", "bytes", ("S-32",)),
    SheetDocument("s-52-ircodes_v01.pdf", "bytes", ("S-52",)),
    SheetDocument("s-81-ircodes_v01.pdf", "s81", ("S-81",)),
)


def squash(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def clean(words: list[str]) -> str:
    return " ".join(" ".join(words).replace("　", " ").split())


def kaseikyo_row(ident: str, page: int, name: str, printed: str, bits48: str, models: frozenset[str], group: str, header_genre2: int | None = None) -> DocRow:
    """One Kaseikyo row from its 48 bits (first bit first): the vendor and its parity nibble are checked, the parity byte is a check of GENRE2, and the ID bits go above the data as
    in §94. ``header_genre2`` is what the sheet's header says; where the bits say another and the parity byte agrees with the header, the header is read and the key is Untested."""
    if bits48[:16] != KASEIKYO_VENDOR_BITS:
        raise ValueError(f"page {page}: the row {ident!r} has the vendor bits {bits48[:16]}, not Denon's {KASEIKYO_VENDOR_BITS}")
    if value(bits48[16:20]) != 0:
        raise ValueError(f"page {page}: the row {ident!r} has the vendor parity {bits48[16:20]}, not 0000")
    genre1, genre2, data, ident_bits, parity = value(bits48[20:24]), value(bits48[24:28]), value(bits48[28:38]), value(bits48[38:40]), value(bits48[40:48])
    doubt = ""
    if parity_of(genre1, genre2, data, ident_bits) == parity:
        subdevice = genre2
        if header_genre2 is not None and header_genre2 != genre2:
            doubt = f"the sheet's header says GENRE2 {header_genre2} and the row's bits {genre2}"
    elif header_genre2 is not None and parity_of(genre1, header_genre2, data, ident_bits) == parity:
        subdevice = header_genre2
        doubt = f"the row's bits say GENRE2 {genre2}, the sheet's header and the parity byte say {header_genre2}: read as the header, which the parity byte supports"
    else:
        subdevice = genre2
        doubt = f"the parity byte {parity:#04x} is not that of GENRE1 {genre1}, GENRE2 {genre2} and the data {data:#x}"
    function = data | ident_bits << 10
    how = (f"bits 1 to 48 {bits48}): Denon-K device {genre1} subdevice {subdevice} function {function}, GENRE1, GENRE2 and the data each first bit first, the ID bits "
           f"{bits48[38:40]}")
    return DocRow("kaseikyo", ident, f"K{genre1}-{subdevice}", group, printed, [(name, models)] if name else [], bits48[28:38], genre1, subdevice, function, function,
                  page=page, how=how, doubt=doubt)


def sharp_row(ident: str, page: int, name: str, printed: str, address: str, extension: str, data: str, models: frozenset[str], number: int | None, extra: str = "") -> DocRow:
    """One Sharp row: the address (C1 to C5), the extension bits (C12 and C13) and the data (C6 to C11), each first bit first; ``number`` is the row's own number (the data's value) where
    the sheet has one."""
    device, function = value(address), value(data) + 64 * value(extension)
    genre = GENRES.get((address, extension))
    how = (f"{'genre ' + genre + ', ' if genre else ''}system address {address}, extension bits {extension}, data {data}{extra}): Denon device {device} function {function}, "
           "each field first bit first, the extension bits above the data")
    return DocRow("sharp", ident, f"{address} {extension}", "", printed, [(name, models)] if name else [], data, device, None, function, value(data) if number is None else number,
                  page=page, how=how)


def unread(notes: list[str], page: int, y: float, why: str) -> None:
    notes.append(f"page {page}, the line at y {round(y)}: {why}")


# --- the zone sheets -----------------------------------------------------------------------------------------------------------------------


def zone_rows(rows: list[list[str]], document: SheetDocument, notes: list[str]) -> list[DocRow]:
    """The rows of a zone sheet: 48 bit columns (the 48th is just left of the ``Function`` column) and the function's name."""
    lines = page_lines(rows)[1]
    title = " ".join(w for _, ws in lines[:1] for _, w in ws)
    if document.group not in nf(title).upper().replace(" ", "") or squash(document.title) not in squash(title):
        raise ValueError(f"{document.file}: the title {title!r} is not the sheet of {document.group} of the {document.title}")
    header = next((ws for _, ws in lines if any(w == "Data" for _, w in ws) and sum(1 for _, w in ws if w.isdigit()) >= 7), None)
    if header is None:
        raise ValueError(f"{document.file}: no header line of nibbles")
    nibbles = [int(w) for x, w in header if w.isdigit() and x < 420]
    if len(nibbles) != 7 or nibbles[:5] != [4, 5, 2, 3, 0]:
        raise ValueError(f"{document.file}: the header's maker code and parity {nibbles[:5]} are not 4 5 2 3 0")
    header_genre1, header_genre2 = nibbles[5], nibbles[6]
    out: list[DocRow] = []
    for y, words in lines:
        bits = "".join(w for x, w in words if _ONE.match(w) and x < 657)
        if len(bits) != 48:
            if len(bits) > 8:
                unread(notes, 1, y, f"{len(bits)} bit columns, not 48")
            continue
        name = clean([w for x, w in words if x >= 657])
        row = kaseikyo_row(f"row {len(out) + 1}", 1, name, name, bits, frozenset(document.models), document.group, header_genre2)
        if row.device != header_genre1:
            raise ValueError(f"{document.file}: row {len(out) + 1} has GENRE1 {row.device}, the header {header_genre1}")
        out.append(row)
    if len(out) != 33:
        raise ValueError(f"{document.file}: {len(out)} rows of 48 bits, not the 33 of the other zone sheets")
    return out


# --- the RC-1033 ----------------------------------------------------------------------------------------------------------------------------

_ADDRESS = re.compile(r"System Address ?\(C1-C5\) ?: ?([01]{5}).*Expanded Bits ?\(C12, ?C13\) ?: ?([01]{2})", re.I)


def dcm_rows(rows: list[list[str]], document: SheetDocument, notes: list[str]) -> list[DocRow]:
    """The RC-1033's table: ``C<n>``, six data bits whose value is n (a row that says otherwise is Untested), a button's name and what is printed on it (the picture of the remote at
    the right is left out)."""
    lines = page_lines(rows)[1]
    text = " ".join(w for _, ws in lines for _, w in ws)
    m = _ADDRESS.search(text.replace("：", ":").replace("（", "(").replace("）", ")"))
    if not m:
        raise ValueError(f"{document.file}: no 'System Address(C1-C5): ..., Expanded Bits(C12,C13): ..' line")
    address, extension = m[1], m[2]
    models = frozenset(document.models)
    out = []
    for y, words in lines:
        left = [(x, w) for x, w in words if x < 500]
        if not left or not re.fullmatch(r"C\d+", left[0][1]) or left[0][0] > 60:
            continue
        bits = "".join(w for x, w in left[1:] if _ONE.match(w) and x < 235)
        if len(bits) != 6:
            unread(notes, 1, y, f"the row {left[0][1]} has {len(bits)} data bits, not 6")
            continue
        button = clean([w for x, w in left if 235 <= x < 360])
        printing = clean([w for x, w in left if x >= 360])
        out.append(sharp_row(left[0][1], 1, button, f"{button} | {printing}" if printing else button, address, extension, bits, models, int(left[0][1][1:])))
    return out


# --- the RC-1179 ----------------------------------------------------------------------------------------------------------------------------


def rc1179_rows(rows: list[list[str]], document: SheetDocument, notes: list[str]) -> list[DocRow]:
    """The RC-1179's Sharp table (page 2): ``K<n>``, the key's name, the code's product (``pre-main`` for the PMA amplifiers, ``cd`` for the DCD players) and 15 bits, C1 to C15. The
    Kaseikyo table (page 3) is read where its rows have text: four rows."""
    pages = page_lines(rows)
    amplifier = frozenset(m for m in document.models if m.startswith("PMA"))
    player = frozenset(m for m in document.models if m.startswith("DCD"))
    out: list[DocRow] = []
    for y, words in pages[2]:
        if not words or not re.fullmatch(r"K\d+", words[0][1]):
            continue
        bits = [w for x, w in words if _ONE.match(w) and x >= 395]
        key = words[0][1]
        if len(bits) != 15:
            if bits:
                name = clean([w for x, w in words if 100 <= x < 395])
                unread(notes, 2, y, f"the row {key} ({name!r}) has {len(bits)} bit columns, not 15: its first bit is under the key's name in the text layer")
            continue
        product = next((w for x, w in words if 225 <= x < 275), "")
        models = amplifier if product.startswith("pre-main") else player if product.startswith("cd") else None
        if models is None:
            raise ValueError(f"{document.file}: the row {key} is for {product!r}, not a pre-main amplifier or a cd player")
        name = clean([w for x, w in words if 100 <= x < 225])
        remote = clean([w for x, w in words if 225 <= x < 395]).removeprefix("pre-main").removeprefix("cd").strip()
        if bits[13] != "0" or bits[14] != "0":
            raise ValueError(f"{document.file}: the row {key} has C14 and C15 {bits[13]}{bits[14]}, not the 00 of a Sharp code that is not inverted")
        out.append(sharp_row(key, 2, name, f"{name} | {product} | {remote}", "".join(bits[0:5]), "".join(bits[11:13]), "".join(bits[5:11]), models, None))
    for y, words in pages[3]:
        if not words or words[0][1] != "KEY":
            continue
        bytes_ = [w for x, w in words if _BYTE.match(w)]
        if not bytes_:
            continue                                                       # a row the document leaves empty
        if len(bytes_) != 4:
            unread(notes, 3, y, f"{len(bytes_)} byte columns, not 4")
            continue
        number = next(w for x, w in words if x < 100 and w.isdigit())
        name = clean([w for x, w in words if 100 <= x < 250])
        out.append(_byte_row(f"KEY {number}", 3, name, name, bytes_, " ".join(w for _, w in words), player, ""))
    return out


# --- the Kaseikyo rows written as bytes ------------------------------------------------------------------------------------------------------

_PAREN = re.compile(r"\(([0-9A-F])H\)")
_DATA = re.compile(r"\(([0-9A-F]{3})(?:H|\))")


def _byte_row(ident: str, page: int, name: str, printed: str, bytes_: list[str], text: str, models: frozenset[str], group: str) -> DocRow:
    """A row of bytes 3 to 6 written as four 8-bit strings (first bit first), with the GENRE1, GENRE2, data code and parity byte of its own line in hexadecimal, which are the checks."""
    bits48 = KASEIKYO_VENDOR_BITS + "".join(bytes_)
    row = kaseikyo_row(ident, page, name, printed, bits48, models, group)
    genres = list(_PAREN.finditer(text))
    data = _DATA.search(text, genres[-1].end() if genres else 0)
    rest = text[data.end():] if data else text
    parity = re.search(r"([0-9A-F]{2})H", rest)
    said: list[str] = []
    if genres and int(genres[0][1], 16) != row.device:
        said.append(f"the line says GENRE1 {genres[0][1]}, the bits {row.device}")
    if len(genres) >= 2 and int(genres[1][1], 16) != row.subdevice:
        said.append(f"the line says GENRE2 {genres[1][1]}, the bits {row.subdevice}")
    bits_data = value(bits48[28:38])
    if data and int(data[1], 16) != bits_data:
        said.append(f"the line says the data {data[1]}H, the bits {bits_data:#x}")
    if parity and int(parity[1], 16) != value(bits48[40:48]):
        said.append(f"the line says the parity byte {parity[1]}H, the bits {value(bits48[40:48]):#04x}")
    if said:
        row.doubt = "; ".join(filter(None, [row.doubt, *said]))
    return row


def bytes_rows(rows: list[list[str]], document: SheetDocument, notes: list[str]) -> list[DocRow]:
    """The S-32's and S-52's table (and the S-81's Kaseikyo rows): ``KEY <n>``, a key's name, GENRE1 and GENRE2 as the document names them, the function's name, its data code in
    hexadecimal and the parity byte, then bytes 3 to 6 as bit strings."""
    out: list[DocRow] = []
    models = frozenset(document.models)
    for page, lines in sorted(page_lines(rows).items()):
        for y, words in lines:
            bytes_ = [w for x, w in words if _BYTE.match(w)]
            if len(words) < 6 or words[0][1] != "KEY" or not bytes_:
                continue
            if len(bytes_) != 4:
                unread(notes, page, y, f"{len(bytes_)} byte columns, not 4")
                continue
            number = next((w for x, w in words if 60 < x < 88 and w.isdigit()), None)
            tokens = [w for x, w in words]
            # the function's name is between the second genre's ``(nH)`` and the data code ``(nnnH)``; the key's own name is the words before the genres' names
            genre_at = [i for i, w in enumerate(tokens) if _PAREN.fullmatch(w)]
            data_at = next((i for i, w in enumerate(tokens) if i > (genre_at[1] if len(genre_at) > 1 else 0) and _DATA.match(w)), None)
            if number is None or len(genre_at) < 2 or data_at is None:
                unread(notes, page, y, "its key number, its genres' codes or its data code are not where the rows have them")
                continue
            name = clean(tokens[genre_at[1] + 1:data_at])
            key = clean([w for x, w in words if 88 <= x < 195])
            out.append(_byte_row(f"KEY {number}", page, name, f"{key} | {name}", bytes_, " ".join(tokens), models, ""))
    return out


# --- the DVD-3800BDCI and DVD-2500BTCI -----------------------------------------------------------------------------------------------------


def dvd_rows(rows: list[list[str]], document: SheetDocument, notes: list[str]) -> list[DocRow]:
    """Page 1: the main code, Kaseikyo, rows of an identifier (the key's number and the data in three hexadecimal digits), the bits 29 to 48 and the function's name. Page 2: the sub code,
    a Sharp table of 15 bits (C1 to C15) and the function's name."""
    pages = page_lines(rows)
    models = frozenset(document.models)
    genre_line = next(ws for _, ws in pages[1] if [w for _, w in ws if w.isdigit()] == ["4", "5", "2", "3", "0", "2", "1"])
    genre1, genre2 = [int(w) for _, w in genre_line if w.isdigit()][5:7]
    out: list[DocRow] = []
    for y, words in pages[1]:
        bits = [w for x, w in words if _ONE.match(w) and 130 <= x < 380]
        label = next((w for x, w in words if x < 100 and re.fullmatch(r"[0-9A-F]{4,6}", w)), None)
        if label is None:
            continue
        if len(bits) != 20:
            if bits:
                unread(notes, 1, y, f"the identifier {label} has {len(bits)} bit columns, not 20")
            continue
        name = clean([w for x, w in words if x >= 440])
        data = value("".join(bits[:10]))
        bits48 = KASEIKYO_VENDOR_BITS + "0000" + format(genre1, "04b")[::-1] + format(genre2, "04b")[::-1] + "".join(bits)
        row = kaseikyo_row(f"ID {label}", 1, name, name, bits48, models, "MAIN CODE")
        # the identifier is the key's number in decimal and the data in three hexadecimal digits (``16`` and ``010``)
        if not (label.endswith(f"{data:03X}") and label[:-3].isdigit()):
            row.doubt = "; ".join(filter(None, [row.doubt, f"the identifier {label} is not a key's number and the data {data:#05x} in three hexadecimal digits"]))
        out.append(row)
    for y, words in pages[2]:
        bits = [w for x, w in words if _ONE.match(w) and x >= 230]
        if len(bits) != 15:
            if len(bits) > 8:
                unread(notes, 2, y, f"{len(bits)} bit columns, not 15")
            continue
        name = clean([w for x, w in words if x < 225])
        if bits[13] != "0" or bits[14] != "0":
            raise ValueError(f"{document.file}: the sub code row {name!r} has C14 and C15 {bits[13]}{bits[14]}")
        out.append(sharp_row(f"sub code {name}", 2, name, name, "".join(bits[0:5]), "".join(bits[11:13]), "".join(bits[5:11]), models, None))
    return out


# --- the S-81's Sharp rows ----------------------------------------------------------------------------------------------------------------


def s81_rows(rows: list[list[str]], document: SheetDocument, notes: list[str]) -> list[DocRow]:
    """Page 1: ``KEY <n>``, the key's name, the function, the category (the genre), the CH number, the extension bits (C13, C12), the data (C11 to C6) and the address (C5 to C1), the
    last three written with the most significant bit first."""
    models = frozenset(document.models)
    out: list[DocRow] = []
    for page, lines in sorted(page_lines(rows).items()):
        for y, words in lines:
            if len(words) < 6 or words[0][1] != "KEY":
                continue
            ext = next((w for x, w in words if 470 <= x < 540 and re.fullmatch(r"[01]{2}", w)), None)
            data = next((w for x, w in words if 540 <= x < 620 and re.fullmatch(r"[01]{6}", w)), None)
            address = next((w for x, w in words if 620 <= x and re.fullmatch(r"[01]{5}", w)), None)
            if ext is None and data is None and address is None:
                continue                                                   # an empty row, or a Kaseikyo one (read by ``bytes_rows``)
            number = next((w for x, w in words if 85 <= x < 110 and w.isdigit()), None)
            if ext is None or data is None or address is None or number is None:
                unread(notes, page, y, "its extension, data, address or number column is missing")
                continue
            ch = next((int(w) for x, w in words if 395 <= x < 430 and w.isdigit()), None)
            key = clean([w for x, w in words if 108 <= x < 200])
            function = clean([w for x, w in words if 200 <= x < 300])
            category = clean([w for x, w in words if 300 <= x < 395])
            # the three columns are written the most significant bit first: C13 C12, C11..C6, C5..C1
            row = sharp_row(f"KEY {number}", page, function or key, f"{key} | {function} | {category}", address[::-1], ext[::-1], data[::-1], models, ch,
                            extra=f", the table's columns C13,C12 {ext}, C11-C6 {data} and C5-C1 {address}")
            out.append(row)
    return out


def sheet_rows(rows: list[list[str]], document: SheetDocument) -> tuple[list[DocRow], list[str]]:
    """``(the document's rows, what it has that was not read)``."""
    notes: list[str] = []
    if document.layout == "zone":
        found = zone_rows(rows, document, notes)
    elif document.layout == "dcm":
        found = dcm_rows(rows, document, notes)
    elif document.layout == "rc1179":
        found = rc1179_rows(rows, document, notes)
    elif document.layout == "dvd":
        found = dvd_rows(rows, document, notes)
    elif document.layout == "bytes":
        found = bytes_rows(rows, document, notes)
    elif document.layout == "s81":
        found = s81_rows(rows, document, notes) + bytes_rows(rows, document, notes)
    else:
        raise ValueError(f"{document.file}: the layout {document.layout!r} is not read")
    return found, notes


def build_sheets(snapshot, authored, report):
    """The files of every sheet of ``SHEETS``, as ``denon.emit_rows`` makes them."""
    from .denon import MAKER, emit_rows

    gate: dict[tuple, str | None] = {}
    out = []
    for document in SHEETS:
        rows, notes = sheet_rows(snapshot.sheet_of(document.file, "words"), document)
        for note in notes:
            report.notes.append((MAKER, document.file, f"not read: {note}"))
        entry, sha = snapshot.documents[document.file], snapshot.sha(document.file)
        where = f"{PDF_HOST}/{document.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)})"
        out += emit_rows(document.file, where, rows, document.models, authored, report, gate, {m: a for m, a in document.aliases})
    return out
