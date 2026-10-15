"""InFocus's IR code PDFs for its projectors and displays (DESIGN section 75).

InFocus (the projector maker, now Sanyo's and Epson's neighbour in the Wayback Machine only) published for each product family a short PDF of its
remote's key codes: ``The Work Big IN72, IN74, IN76 uses the NEC IR protocol. The customer code portion of the IR packet should use the
hexadecimal value 0x4E87``, then a table of key names and the data code of each (``Menu 0x02``). **A key is the ledger's ``NEC1``: the
customer code ``0x4E87`` is the device 0x87 and the subdevice 0x4E (the low byte is sent first, as the other sources' InFocus remotes
have it), the data code the function.** The tables come in seven layouts, each a reader here: ``name_code`` (``Menu  0x02``), ``name_desc_code``
(``Menu  Toggles menu state  02h``), ``code_name`` (``0x02  Menu toggle``), ``hex16`` (``POWER  0xE817``: the code and its bit reverse, which
the reader checks), ``keyno`` (the IN514x's table with the customer code in each row), ``dec_hex_bin`` (the decimal, the hex and the binary of a
code, which must agree) and ``numbered`` (the IN5310's rows, a number, a name, a description and a code).

**The customer code is read from the document's own sentence** (0x4E87 in most, 0x83F4 in the IN100's and 0x0080 in the Mondopad's). **A
document that states none (the LP650's, the SP4800's and SP4805's, the X2's and X3's, the IN2100's, and the hex16 ones) is read with 0x4E87
and every key of it is Untested**, which the citation says: its codes are those of the same tables of the same years, and 0x4E87 is the one
code every InFocus document that states a customer code gives but two.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "infocus"
BRAND = "InFocus"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
DEFAULT_CUSTOMER = 0x4E87


@dataclass(frozen=True)
class Document:
    file: str
    models: tuple[str, ...]
    layout: str
    sheet: str = "lines"           # the CSV the reader takes: ``lines`` (cells at a place) or ``pages`` (one line of text a row)
    unnamed: tuple[str, ...] = ()  # models the document does not name (it names a family, or none): they are the product pages that serve it
    doubt: str = ""                # why every key of the document is doubtful, when the document alone does not say
    name_max: int = 150            # the name column of a table read by ``banded`` is the cells with ``x`` under this
    text_sheet: str = ""           # the sheet that has the customer code's sentence when it is not the one of the rows (a table that is an image)
    names_from: str = ""           # the document whose key names a table without names takes, by key number (the IN3118HD's from the IN5310's)
    high_first: bool = False       # the customer code is written in the order it is sent: its high byte is the device (the SP8604's and IN100's)
    default_customer: int = DEFAULT_CUSTOMER   # the customer code a document that states none is read with
    default_why: str = ""          # why that one, when it is not the one every other document gives
    skip_names: tuple[str, ...] = ()           # the names of rows that are no key (the specification's ``N/A``: a reserved code)


DOCUMENTS: tuple[Document, ...] = (
    Document("540-640-IR_codes.pdf", ("LP540", "LP640"), "hex16"),
    Document("IN2x_IR_Codes.pdf", ("IN72", "IN74", "IN76"), "name_code"),
    Document("IN7x_IR_Codes.pdf", ("IN72", "IN74", "IN76"), "name_code"),
    Document("IN5140_IRCodes.pdf", ("IN5140",), "keyno", unnamed=("IN5140",)),
    Document("IN5148HD-IR-Codes.pdf", ("IN5148HD",), "dec_hex_bin", name_max=90),
    Document("InFocus-IN100-IRCodes.pdf", ("IN102", "IN104"), "name_desc_code", high_first=True,
             doubt="its customer code 0x83F4 is read first byte first (device 0x83, subdevice 0xF4), as the IR Blaster database's InFocus remote that has these "
                   "13 functions has it and as the SP8604's own 0x31CE is sent (D301), though the other documents' 0x4E87 is the device 0x87 and the "
                   "subdevice 0x4E, low byte first: InFocus's notation is not one, and nothing in this document says which this is"),
    Document("InFocus-IN1100-IRCodes.pdf", ("IN1100", "IN1102", "M20", "M22", "LP85", "LP85W", "M11", "M11W"), "name_desc_code"),
    Document("InFocus-IN20-IRCodes.pdf", ("IN24", "IN24+", "IN24+EP", "IN26", "IN26EP", "IN26+"), "name_desc_code"),
    Document("InFocus-IN2100-IRCodes.pdf", ("IN2100",), "dec_hex_bin", unnamed=("IN2100",)),
    Document("InFocus-IN3100-IRCodes.pdf", ("IN3102", "IN3104", "IN3106", "A3100", "A3200", "A3300", "IN3182", "IN3184", "IN3186", "A3180", "A3280", "A3380"),
             "name_desc_code"),
    Document("InFocus-IN32-IRcodes.pdf", ("IN32", "IN34"), "name_code", unnamed=("IN32", "IN34")),
    Document("InFocus-IN5300-IRCodes.pdf", ("IN5302", "IN5304"), "name_desc_code"),
    Document("InFocus-IN5310-IRCodes.pdf", ("IN5310", "IN5310HD"), "numbered", sheet="pages", unnamed=("IN5310", "IN5310HD")),
    # the four whose table is an image (D298), read from the transcription; the customer code's sentence is in the text layer
    Document("InFocus-IN110-IRCodes.pdf", ("IN112", "IN114", "IN116"), "transcribed", sheet="transcribed", text_sheet="lines"),
    Document("IN3114_IN3116_IRCodes.pdf", ("IN2112", "IN2114", "IN2116", "IN3114", "IN3116"), "transcribed", sheet="transcribed", text_sheet="lines"),
    Document("InFocus-IN3910-IRCodes.pdf", ("IN3914", "IN3916"), "transcribed", sheet="transcribed", text_sheet="lines"),
    Document("InFocus-IN3118HD-IRCodes.pdf", ("IN3118HD",), "keyed_image", sheet="transcribed", text_sheet="lines", names_from="InFocus-IN5310-IRCodes.pdf"),
    Document("InFocus-IN3900LB-IRCodes.pdf", ("IN3900LB",), "dec_hex_bin", unnamed=("IN3900LB",), skip_names=("N/A",)),
    Document("SP8604_IRCodes.pdf", ("SP8600", "SP8604"), "keycodes_image", sheet="transcribed", high_first=True, unnamed=("SP8604",)),
    Document("IN5550-IR-Codes.pdf", ("IN5550",), "ids0", unnamed=("IN5550",), high_first=True, default_customer=0x31CE,
             default_why="the SP8604's, whose document has these table's codes for 14 functions (`MENU` 0x87, `ENTER` 0xC5, `FREEZE` 0x8E, `INPUT` 0x83, `AUTO SYNC` 0x86, `ON` 0x90...), "
                         "but another InFocus remote of the IR Blaster database that shares only `ENTER` is at 0x32CD, so the family has more than one address: a guess"),
    Document("IN5550-IR-Codes.pdf", ("IN5550 (table 1)",), "ids1", unnamed=("IN5550 (table 1)",), high_first=True, default_customer=0x31CE,
             default_why="the SP8604's, whose document has these table's codes for 14 functions (`MENU` 0x87, `ENTER` 0xC5, `FREEZE` 0x8E, `INPUT` 0x83, `AUTO SYNC` 0x86, `ON` 0x90...), "
                         "but another InFocus remote of the IR Blaster database that shares only `ENTER` is at 0x32CD, so the family has more than one address: a guess"),
    Document("InFocus-IN5500-IRCodes.pdf", ("IN5502", "IN5502L", "IN5504", "IN5504L", "IN5532", "IN5532L", "IN5534", "IN5534L"), "name_desc_code"),
    Document("InFocus-LP120-IRCodes.pdf", ("LP120",), "name_code"),
    Document("InFocus-LP240-LP250-IRCodes.pdf", ("LP240", "LP250"), "hex16"),
    Document("InFocus-LP600-IRCodes.pdf", ("LP600",), "name_code"),
    Document("InFocus-LP650-IRCodes.pdf", ("LP650",), "name_code", unnamed=("LP650",)),
    Document("InFocus-LP815-IRCodes.pdf", ("LP815", "LP820"), "hex16", unnamed=("LP815", "LP820")),
    Document("InFocus-LP840-IRCodes.pdf", ("LP840", "LP850", "LP860"), "hex16", unnamed=("LP860",)),
    Document("InFocus-LS110-IRCodes.pdf", ("LS110",), "code_name", unnamed=("LS110",)),
    Document("InFocus-SP4800-IRCodes.pdf", ("SP4800",), "name_code"),
    Document("InFocus-SP4805-IRCodes.pdf", ("SP4805",), "name_code"),
    Document("InFocus-SP5000-IRCodes.pdf", ("SP5000",), "hex16"),
    Document("InFocus-SP7200-IRCodes.pdf", ("SP7200",), "name_code"),
    Document("InFocus-SP7205-IRCodes.pdf", ("SP7205",), "name_code"),
    Document("Mondopad_IR_codes.pdf", ("INF5520", "INF5520a", "INF55WIN8"), "name_code"),
    Document("X2_IRCodes.pdf", ("X2",), "name_code"),
    Document("X3_IRCodes.pdf", ("X3",), "name_code"),
)
_ITALIC = re.compile(r"^<i>(.*)</i>$")
_HEX0X = re.compile(r"^0[xX]([0-9A-Fa-f]{2})$")
_HEX16 = re.compile(r"^0[xX]([0-9A-Fa-f]{4})$")
_HEXH = re.compile(r"^([0-9A-Fa-f]{2})[hH]$")
_BINARY = re.compile(r"^([01]{4}) ([01]{4})$")
_CUSTOMER = re.compile(r"(?:customer code|Device ID)[^.]{0,160}?0x([0-9A-Fa-f]{4})\b", re.I | re.S)
_ARROWS = {"^": "Up", "v": "Down", "<": "Left", ">": "Right"}


@dataclass(frozen=True)
class Entry:
    page: int
    name: str                  # as the table writes it
    code: int                  # the data code, the function of the NEC1 form
    written: str               # the row's code cells as written
    agrees: bool = True        # the row's own checks (decimal, hex and binary; a code and its bit reverse) say one code
    note: str = ""             # what the document says of the row, for the citation
    customer: int | None = None  # the customer code the row itself gives (the IN514x's table)
    number: int = 0              # the row's key number in a table that has them (the IN5310's)


def _clean(text: str) -> str:
    return " ".join(text.replace("‐", "-").replace("‑", "-").split())


def cells_of(rows: list[list[str]]) -> list[tuple[int, list[str]]]:
    """``(page, cells)`` of each row of a ``lines`` sheet (``page, y, x, text, x, text``), the italic marker removed."""
    out = []
    for row in rows:
        cells = []
        for text in row[3::2]:
            m = _ITALIC.match(text)
            cells.append(_clean(m[1] if m else text))
        out.append((int(row[0]), cells))
    return out


def customer_of(rows: list[list[str]], sheet: str) -> int | None:
    """The customer code a document states, or None. The sentence may be split over lines."""
    text = " ".join((" ".join(row[1:]) if sheet == "pages" else " ".join(row[3::2])).replace("\xa0", " ") for row in rows)
    found = {int(m[1], 16) for m in _CUSTOMER.finditer(text)}
    if len(found) > 1:
        raise ValueError(f"two customer codes stated: {sorted(f'0x{c:04X}' for c in found)}")
    return found.pop() if found else None


def name_code(rows: list[tuple[int, list[str]]]) -> list[Entry]:
    """``Menu  0x02  Toggles menu state``: a name, a code with the prefix, then anything."""
    return [Entry(page, cells[0], int(m[1], 16), cells[1]) for page, cells in rows
            if len(cells) >= 2 and cells[0] and (m := _HEX0X.match(cells[1]))]


def positioned(rows: list[list[str]]) -> list[tuple[int, int, list[tuple[int, str]]]]:
    """``(page, y, [(x, text)])`` of each row of a ``lines`` sheet, the italic marker removed."""
    out = []
    for row in rows:
        cells = []
        for x, text in zip(row[2::2], row[3::2]):
            m = _ITALIC.match(text)
            cells.append((int(x), _clean(m[1] if m else text)))
        out.append((int(row[0]), int(row[1]), cells))
    return out


def banded(rows: list[list[str]], code: re.Pattern[str], numbers: bool, name_max: int) -> list[tuple[int, str, str, list[str]]]:
    """The rows of a table whose cells of one row are not all on one line: ``(page, name, code, other numbers)`` for each cell of the code
    column (``code``, at ``x`` of 300 or more). A cell is the row's when it is within 3 points of the code's line (a name, a description, a
    decimal, a binary); **a cell of the name column (``x`` under ``name_max``) that is near no code is the continuation of the name above it** (``Discrete
    Resize`` / ``Letterbox``), within 20 points of that code's line. Cells under the table's first code (headings) are no row's."""
    out = []
    placed = positioned(rows)
    for page in sorted({p for p, _, _ in placed}):
        lines = [(y, cells) for p, y, cells in placed if p == page]
        codes = [(y, text) for y, cells in lines for x, text in cells if x >= 300 and code.fullmatch(text)]
        names: list[list[str]] = [[] for _ in codes]
        extra: list[list[str]] = [[] for _ in codes]
        for y, cells in lines:
            for x, text in cells:
                if x >= 300 and code.fullmatch(text):
                    continue
                near = [i for i, (cy, _) in enumerate(codes) if abs(cy - y) <= 3]
                if x < name_max:
                    if near:
                        names[near[0]].append(text)
                    elif codes and y > codes[0][0]:         # between two codes, or after the last: the nearer one's, the one above if as near
                        i = min(range(len(codes)), key=lambda i: (abs(codes[i][0] - y), i))
                        if abs(codes[i][0] - y) <= 20:
                            names[i].append(text)
                elif numbers and x >= 300 and near and (re.fullmatch(r"\d{1,3}", text) or _BINARY.match(text)):
                    extra[near[0]].append(text)
        out.extend((page, " ".join(n), c[1], e) for n, c, e in zip(names, codes, extra) if n)
    return out


def name_desc_code(rows: list[list[str]], name_max: int = 150) -> list[Entry]:
    """``Menu  Toggles menu state  02h``: a name, a description, the code last with an ``h``; the cells of a row may be on lines of
    slightly different heights (see ``banded``)."""
    return [Entry(page, name, int(_HEXH.match(code)[1], 16), code) for page, name, code, _ in banded(rows, _HEXH, False, name_max)]


def dec_hex_bin(rows: list[list[str]], name_max: int = 150) -> list[Entry]:
    """``Menu  Toggles menu state  2  02h  0000 0010``: the decimal, the hex and the binary of a code, which must agree. A decimal or a binary the
    row lacks is not checked, and the row says so."""
    out = []
    for page, name, code, extra in banded(rows, _HEXH, True, name_max):
        hexa = int(_HEXH.match(code)[1], 16)
        decimal = next((int(x) for x in extra if re.fullmatch(r"\d{1,3}", x)), None)
        binary = next((x for x in extra if _BINARY.match(x)), None)
        value = int(binary.replace(" ", ""), 2) if binary else None
        agrees = decimal in (None, hexa) and value in (None, hexa)
        written = " / ".join([str(decimal)] * (decimal is not None) + [code] + [binary] * (binary is not None))
        note = "" if agrees else f"its decimal {decimal}, hex 0x{hexa:02X} and binary {binary} (0x{value:02X}) are not one code" if value is not None else \
            f"its decimal {decimal} and hex 0x{hexa:02X} are not one code"
        if agrees and (decimal is None or binary is None):
            note = "the table's " + ("decimal" if decimal is None else "binary") + " of this row was not read"
        out.append(Entry(page, name, hexa, written, agrees, note))
    return out


def code_name(rows: list[tuple[int, list[str]]]) -> list[Entry]:
    """``0x02  Menu toggle``: the code, then the name."""
    return [Entry(page, cells[1], int(m[1], 16), cells[0]) for page, cells in rows
            if len(cells) >= 2 and cells[1] and (m := _HEX0X.match(cells[0]))]


def hex16(rows: list[tuple[int, list[str]]]) -> list[Entry]:
    """``POWER  0xE817``: the data code is the low byte and the high byte is its bit reverse; a row where it is not is read and marked."""
    out = []
    for page, cells in rows:
        if len(cells) >= 2 and cells[0] and (m := _HEX16.match(cells[1])):
            value = int(m[1], 16)
            low, high = value & 0xFF, value >> 8
            reverse = int(f"{low:08b}"[::-1], 2)
            out.append(Entry(page, cells[0], low, cells[1], reverse == high,
                             "" if reverse == high else f"the high byte 0x{high:02X} is not the bit reverse of the low byte 0x{low:02X}"))
    return out


def keyno(rows: list[tuple[int, list[str]]]) -> list[Entry]:
    """``K01  STANDBY  NEC  4E87  39``: a key number, a name, the format, the customer code and the data code."""
    return [Entry(page, cells[1], int(cells[4], 16), f"{cells[2]} {cells[3]} {cells[4]}", True, "", int(cells[3], 16)) for page, cells in rows
            if len(cells) >= 5 and re.fullmatch(r"K\d+", cells[0]) and cells[2] == "NEC" and re.fullmatch(r"[0-9A-Fa-f]{4}", cells[3])
            and re.fullmatch(r"[0-9A-Fa-f]{2}", cells[4])]


def numbered(rows: list[list[str]]) -> tuple[list[Entry], list[str]]:
    """The IN5310's rows as lines of text: ``1 Power On``, a description, the code ``3Ah``; the arrow keys' names are one line of their own
    (``10``, ``^``). A row whose code is ``n/a`` is no key (the second value says so). The row numbers run from 1 and each must be the next."""
    lines = [(int(r[0]), _clean(r[1])) for r in rows]
    out: list[Entry] = []
    no_code: list[str] = []
    seen = 0
    i = 0
    while i < len(lines):
        m = re.fullmatch(r"(\d{1,2})(?: (\S.*))?", lines[i][1])
        if not m or int(m[1]) != seen + 1:
            i += 1
            continue
        seen += 1
        page, name, j = lines[i][0], m[2], i + 1
        if name is None:
            name, j = lines[j][1], j + 1
        while j < len(lines) and not re.fullmatch(r"[0-9A-Fa-f]{2}[hH]|n/a", lines[j][1]):
            j += 1
        if j >= len(lines):
            raise ValueError(f"row {seen} '{name}' has no code")
        code = lines[j][1]
        label = _ARROWS.get(name, name)
        if code == "n/a":
            no_code.append(f"'{label}' (row {seen}): the table's code is n/a, so it sends none")
        else:
            out.append(Entry(page, label, int(code[:2], 16), code, True, f"the table writes the key's name as '{name}'" if name in _ARROWS else "", None, seen))
        i = j + 1
    return out, no_code


def transcribed(rows: list[list[str]]) -> list[Entry]:
    """The tables that are images (the IN110's, IN3114's and IN3910's), written down by eye as ``page, name, decimal, hex, binary``: the same
    checks as ``dec_hex_bin``, which are what a misreading would fail."""
    out = []
    for row in rows:
        page, name, decimal, hexa, binary = int(row[0]), _clean(row[1]), row[2], row[3], row[4]
        if not (re.fullmatch(r"\d{1,3}", decimal) and _HEXH.match(hexa) and _BINARY.match(binary)):
            raise ValueError(f"transcription row {row}: not a decimal, a hex with h and a binary of two nibbles")
        code, value = int(_HEXH.match(hexa)[1], 16), int(binary.replace(" ", ""), 2)
        agrees = int(decimal) == code == value
        out.append(Entry(page, name, code, f"{decimal} / {hexa} / {binary}", agrees,
                         "" if agrees else f"its decimal {decimal}, hex 0x{code:02X} and binary {binary} (0x{value:02X}) are not one code"))
    return out


def keycodes_image(rows: list[list[str]]) -> tuple[list[Entry], int]:
    """The SP8604's table (a picture, written down by eye: ``page, key number, function, description, keycode``, after a first row
    ``page, customer, 0x31CE, the sentence``): the function is the label, the keycode the function of the key. The document writes
    ``Ox31CE`` and ``Ox90`` with the letter O."""
    if rows[0][1] != "customer" or not _HEX16.match(rows[0][2]):
        raise ValueError(f"the transcription's first row {rows[0]} is not the customer code")
    out = []
    for position, (page, number, function, description, code) in enumerate(((int(r[0]), r[1], _clean(r[2]), _clean(r[3]), r[4]) for r in rows[1:]), 1):
        if int(number) != position or not _HEX0X.match(code):
            raise ValueError(f"transcription row {position}: {number} {function} {code}")
        out.append(Entry(page, function, int(code[2:], 16), f"{number} '{function}' ({description}) / {code}", True, "", None, position))
    return out, int(rows[0][2], 16)


def ids(rows: list[list[str]], table: int) -> list[Entry]:
    """The IN5550's two tables (a ``Table 0 (default)`` and a ``Table 1``, the remote's ID-set modes): a row is ``number, key, code, function``
    for each table, and the last four rows are the special codes the ID-set sequences send (``ID_SET + MENU  0x6C  Table0``). The cells at the
    right (``x`` of 400 or more) are the labels of the remote's picture. A row with ``-`` for the function in a table has none there."""
    out: list[Entry] = []
    for row in rows:
        page = int(row[0])
        cells = [_clean(text) for x, text in zip(row[2::2], row[3::2]) if int(x) < 400]
        if len(cells) == 6 and re.fullmatch(r"\d{1,2}", cells[0]) and (_HEX0X.match(cells[2]) or cells[2] == "-"):
            code, function = (cells[2], cells[3]) if table == 0 else (cells[4], cells[5])
            if _HEX0X.match(code) and function != "-":
                out.append(Entry(page, function, int(code[2:], 16), f"{cells[0]} {cells[1]} / {code}", True, "", None, int(cells[0])))
        elif len(cells) == 5 and cells[0].startswith("ID_SET") and _HEX0X.match(cells[1]):
            code, function = (cells[1], cells[2]) if table == 0 else (cells[3], cells[4])
            out.append(Entry(page, function, int(code[2:], 16), f"{cells[0]} / {code}", True, f"the special code of {cells[0]}"))
    return out


def keyed_image(rows: list[list[str]], names: dict[int, Entry]) -> tuple[list[Entry], list[str]]:
    """The IN3118HD's table (an image: ``page, key number, description, data code``) has no key names, only a description of each key; it is the
    IN5310's table (the same ``Code table (InFocus RC)``), so each row takes the IN5310's name for the key of that number, **and the two codes
    must be the same** (they are in all 29 rows). A row with no code (the 30th, ``LIGHT``) is no key."""
    out: list[Entry] = []
    no_code: list[str] = []
    for page, number, description, code in ((int(r[0]), int(r[1]), _clean(r[2]), r[3]) for r in rows):
        if not code:
            no_code.append(f"'{description}' (row {number}): the table has no code, so it sends none")
            continue
        twin = names.get(number)
        if twin is None or not _HEXH.match(code) or twin.code != int(code[:2], 16):
            raise ValueError(f"row {number} '{description}' ({code}) is not the IN5310's row of that number ({twin and twin.written})")
        out.append(Entry(page, twin.name, twin.code, f"{number} '{description}' / {code}", True,
                         f"the label is the IN5310 document's name for the key of this number and code, the same code table; this document gives only "
                         f"the description '{description}'", None, number))
    return out, no_code


def read(document: Document, rows: list[list[str]], names: dict[int, Entry] | None = None) -> tuple[list[Entry], list[str]]:
    """The document's entries and what its rows say that is no key."""
    if document.layout == "numbered":
        return numbered(rows)
    if document.layout == "transcribed":
        return transcribed(rows), []
    if document.layout == "keyed_image":
        return keyed_image(rows, names or {})
    if document.layout == "keycodes_image":
        return keycodes_image(rows)[0], []
    if document.layout in ("ids0", "ids1"):
        return ids(rows, int(document.layout[-1])), []
    if document.layout in ("name_desc_code", "dec_hex_bin"):
        return {"name_desc_code": name_desc_code, "dec_hex_bin": dec_hex_bin}[document.layout](rows, document.name_max), []
    readers = {"name_code": name_code, "code_name": code_name, "hex16": hex16, "keyno": keyno}
    return readers[document.layout](cells_of(rows)), []


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    taken: set[str] = set()
    read_entries: dict[str, list[Entry]] = {}
    for document in DOCUMENTS:
        rows = snapshot.sheet_of(document.file, document.sheet)
        entry = snapshot.documents[document.file]
        sha = snapshot.sha(document.file)
        if document.layout == "keycodes_image":
            stated = keycodes_image(rows)[1]
        else:
            stated = customer_of(snapshot.sheet_of(document.file, document.text_sheet), document.text_sheet) if document.text_sheet else customer_of(rows, document.sheet)
        entries, no_code = read(document, rows, {e.number: e for e in read_entries.get(document.names_from, [])})
        read_entries[document.file] = entries
        reserved = [e for e in entries if e.name in document.skip_names]
        entries = [e for e in entries if e.name not in document.skip_names]
        if reserved:
            report.notes.append((MAKER, document.file, f"{len(reserved)} rows are named `{document.skip_names[0]}` (reserved codes) and are no keys"))
        report.count(MAKER, f"{document.file}{' (table 1)' if document.layout == 'ids1' else ''}: rows read", len(entries))
        for why in no_code:
            report.notes.append((MAKER, document.file, why))
        # a name written twice with one code is one key; two names with one code are both doubtful
        unique: list[Entry] = []
        for e in entries:
            if any(_squash(u.name) == _squash(e.name) and u.code == e.code for u in unique):
                report.notes.append((MAKER, f"{document.file} page {e.page}", f"'{e.name}' ({e.written}) is the table's second row for the same name and code"))
                continue
            unique.append(e)
        by_code: dict[int, list[Entry]] = {}
        for e in unique:
            by_code.setdefault(e.code, []).append(e)
        inferred = stated is None and all(e.customer is None for e in unique)
        if document.doubt:
            report.notes.append((MAKER, document.file, f"every key is Untested: {document.doubt}"))
        if inferred:
            report.notes.append((MAKER, document.file, f"states no customer code: read with 0x{document.default_customer:04X}, " + (document.default_why or "the one the other InFocus documents give")
                                 + f", and each of its {len(unique)} keys is Untested"))
        where = f"infocus.com IR code PDFs/{document.file}"
        for model in document.models:
            members: list[tuple[str, dict[str, Any]]] = []
            for e in unique:
                customer = e.customer if e.customer is not None else (stated if stated is not None else document.default_customer)
                device, subdevice = (customer >> 8, customer & 0xFF) if document.high_first else (customer & 0xFF, customer >> 8)
                key = (device, subdevice, e.code)
                if key not in gate:
                    gate[key] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                        "id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": e.code,
                        "confidence": TIER, "source": "probe"})
                if gate[key]:
                    report.unrepresented[(MAKER, document.file, f"does not compile: {gate[key]}")] += 1
                    continue
                cite = (f"{where}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']}) "
                        f"page {e.page} row '{e.name}' ({e.written}): NEC1 device {device} subdevice {subdevice} function {e.code}, "
                        f"customer code 0x{customer:04X}" + (" written in the order it is sent" if document.high_first else ""))
                doubts = []
                if document.doubt:
                    doubts.append(document.doubt)
                if inferred:
                    doubts.append(f"the document states no customer code, and 0x{document.default_customer:04X} is " + (document.default_why or "the one the other InFocus documents give"))
                elif e.customer is None:
                    cite += ", as the document states"
                if not e.agrees:
                    doubts.append(e.note)
                if len(by_code[e.code]) > 1:
                    doubts.append("the table gives this code to " + " and ".join(f"'{o.name}'" for o in by_code[e.code]) + ", and a code is one function")
                if doubts:
                    cite += "; " + "; ".join(doubts)
                for d in doubts[(1 if inferred else 0) + (1 if document.doubt else 0):]:
                    note = (MAKER, f"{document.file} page {e.page}", f"'{e.name}' ({e.written}): {d}")
                    if note not in report.notes:
                        report.notes.append(note)
                if e.note and e.agrees:
                    cite += "; " + e.note
                if document.sheet == "transcribed":
                    cite += "; the table is an image in the PDF, and this row was read from it by eye (DESIGN D298)"
                if model in document.unnamed:
                    cite += f"; the document does not name {model}, and the PDF is served on the page of {model}"
                members.append((e.name, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": e.code,
                                         "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
            if not members:
                continue
            final = model
            if final in taken:
                renamed = f"{final} ({document.file})"
                report.notes.append((MAKER, final, f"kept as '{renamed}': an earlier document of the maker has a file of that name"))
                final = renamed
            while (BRAND.casefold(), final.casefold()) in authored:
                renamed = f"{final} (InFocus table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            taken.add(model)
            names = key_names([m[0] for m in members])
            keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(names, members)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                        {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(PROTOCOL), "keys": keys}))
    return out
