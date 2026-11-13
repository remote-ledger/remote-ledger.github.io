"""AWE Europe's discrete IR code documents of four display makers (DESIGN section 107).

AWE Europe, a British distributor, kept the makers' control documents in its ``documents/`` folder (the folder sections 100 and 105 read Yamaha's from). **Four of them are IR code
tables of televisions and displays, each in a form of its own**, and all four are read here (the Wayback Machine's copies):

* **Philips's discrete codes** (a workbook of 11 names and Pronto codes: eight RC-5 codes in Pronto's short ``5000`` form and the learned timings of four more);
* **Samsung's discrete commands** (a Word list of 14 Pronto codes of an NEC frame with a 4.5 ms leader);
* **the Europe LCD-TV code table** (a ruled table of 80 rows of a 15-bit frame written as its bits: the document does not name the maker, its protocol, its ``OPC`` and ``TruD`` keys and
  its ``GD7E`` remarks are Sharp's);
* **Panasonic's plasma TV remote codes** (7 pages of Pronto codes of a 48-bit frame).

**A key is the ledger's protocol of the frame the code holds**: ``RC5`` (a system and a command), ``NECx2`` (Samsung's device 7, subdevice 7 and a function), ``Sharp`` (a device and a
function) and ``Panasonic`` (a device, a subdevice and a function). **Each Pronto code is decoded** (:func:`rc5`, :func:`necx2` and :func:`kaseikyo`: the leader, the bits as the marks and
spaces give them, the stop bit, and, for Panasonic's, the check byte) and **each key is held against what else its document says**: a code two differently named keys share is Untested for
both, as is a Philips input on another system than the sheet's other inputs and a Samsung code that is not of device 7, subdevice 7. A Pronto code that is not a whole frame (Samsung's
``POWER OFF`` has 30 pairs where a frame has 34) is not read, and the report says so.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "awe"
HOST = "awe-europe.com control documents"

PHILIPS = "Philips_discrete_ir_codes.xls"
SAMSUNG = "Samsung_Discrete_IR_Commands_tested.doc"
SHARP = "RC_IR-codes_Europe_full.pdf"
PANASONIC = "Panasonic_plasma_Discrete-remote-control-code.pdf"

RC5 = {"name": "RC5", "carrierHz": 36000, "minSends": 1}
NECX2 = {"name": "NECx2", "carrierHz": 38000, "minSends": 1}
SHARP_PROTOCOL = {"name": "Sharp", "carrierHz": 38000, "minSends": 1}
PANASONIC_PROTOCOL = {"name": "Panasonic", "carrierHz": 37000, "minSends": 1}
#: the Pronto frequency word of each protocol's carrier: a code with another word has it in its citation
NOMINAL_WORD = {"RC5": 0x73, "NECx2": 0x6D, "Panasonic": 0x70}

SAMSUNG_TITLE = "Samsung Discrete IR Commands"
SAMSUNG_ADDRESS = (7, 7)                      # Samsung's TV address: the device and the subdevice of every NECx2 code of the list

_HEX4 = re.compile(r"^[0-9A-Fa-f]{4}$")
_CODE_LINE = re.compile(r"^(?:[0-9A-F]{4} ?)+$")


def khz(word: int) -> str:
    return f"{1_000_000 / (word * 0.241246) / 1000:.1f} kHz"


# --- the decoders ---------------------------------------------------------------------------------------------------------------------------------------------------------------------


def _words(pronto: str) -> list[int] | str:
    try:
        return [int(w, 16) for w in pronto.split()]
    except ValueError:
        return "not hexadecimal"


def _learned(words: list[int]) -> tuple[int, list[tuple[int, int]]] | str:
    """The carrier word and the burst pairs (the once-sequence and the repeat-sequence one after the other) of a learned Pronto code."""
    if len(words) < 6 or words[0] != 0:
        return "not a learned Pronto code"
    once, rep = words[2], words[3]
    body = words[4:]
    if len(body) != 2 * (once + rep):
        return f"its header says {once + rep} pairs and it has {len(body)} numbers"
    return words[1], list(zip(body[0::2], body[1::2]))


def rc5(pronto: str) -> tuple[int, int, int | None, str, int | None] | str:
    """A Philips RC-5 code: ``(system, command, toggle, shape, carrier word)``. Pronto's short form is ``5000 0000 0000 0001 system command``; a learned code is the biphase timings of a
    frame (a half-bit is one unit, the first half-bit of the frame, a space, is not in the code, and the frame's last space is in its gap): two start bits (the second the complement of
    command bit 6), the toggle bit, five system bits and six command bits, most significant first."""
    words = _words(pronto)
    if isinstance(words, str):
        return words
    if words[:1] == [0x5000]:
        if len(words) != 6 or words[1:4] != [0, 0, 1]:
            return "a 5000 code of another shape than `5000 0000 0000 0001 system command`"
        if words[4] > 31 or words[5] > 127:
            return f"system {words[4]} or command {words[5]} out of RC-5's range"
        return words[4], words[5], None, "Pronto's short RC-5 form", None
    learned = _learned(words)
    if isinstance(learned, str):
        return learned
    word, pairs = learned
    durations = [d for pair in pairs for d in pair][:-1]            # the last number is the gap
    unit = min(durations)
    if not 0x18 <= unit <= 0x28:
        return f"a half-bit of {unit:#06x} is not an RC-5 unit"
    levels = [0]                                                    # the frame's first half-bit, a space, is not in the code
    for i, d in enumerate(durations):
        n, remainder = divmod(d, unit)
        if n not in (1, 2) or remainder > unit // 4:
            return f"a duration of {d:#06x} is not one or two half-bits of {unit:#06x}"
        levels += [1 - i % 2] * n
    if not 26 <= len(levels) <= 28:
        return f"{len(levels)} half-bits, a frame has 28"
    levels += [0] * (28 - len(levels))
    bits = []
    for a, b in zip(levels[0::2], levels[1::2]):
        if (a, b) not in ((0, 1), (1, 0)):
            return "two equal half-bits are not a biphase bit"
        bits.append(b)
    value = lambda chunk: int("".join(map(str, chunk)), 2)          # noqa: E731
    if bits[0] != 1:
        return "the first start bit is not a one"
    return value(bits[3:8]), value(bits[8:14]) + (0 if bits[1] else 64), bits[2], "learned RC-5 timings", word


def _pairs_frame(words: list[int], leader: tuple[range, range], bits: int, split: int) -> tuple[int, list[int]] | str:
    learned = _learned(words)
    if isinstance(learned, str):
        return learned
    word, pairs = learned
    if len(pairs) != bits + 2:
        return f"{len(pairs)} pairs, a frame has {bits + 2} (a leader, {bits} bits and the stop bit)"
    if pairs[0][0] not in leader[0] or pairs[0][1] not in leader[1]:
        return f"a leader of {pairs[0][0]:#06x} and {pairs[0][1]:#06x} is not this protocol's"
    values = [1 if space > split else 0 for _, space in pairs[1:bits + 1]]
    return word, [sum(b << i for i, b in enumerate(values[j * 8:(j + 1) * 8])) for j in range(bits // 8)]


def necx2(pronto: str) -> tuple[int, list[int], str] | str:
    """A Samsung code (NECx2: a 4.5 ms mark and a 4.5 ms space, 32 bits, a stop bit): ``(carrier word, [device, subdevice, function, its complement], shape)``."""
    words = _words(pronto)
    if isinstance(words, str):
        return words
    got = _pairs_frame(words, (range(0xA0, 0xB9), range(0xA0, 0xB9)), 32, 0x2A)
    if isinstance(got, str):
        return got
    word, b = got
    if b[2] ^ b[3] != 0xFF:
        return f"the fourth byte {b[3]:02X} is not the complement of the third {b[2]:02X}"
    return word, b, "an NECx2 frame (a 4.5 ms leader, 32 bits)"


def kaseikyo(pronto: str) -> tuple[int, list[int], str] | str:
    """A Panasonic code (a 3.5 ms mark and a 1.7 ms space, 48 bits, a stop bit): ``(carrier word, [2, 32, device, subdevice, function, check], shape)``; the frame begins ``2, 32`` and
    ends with the exclusive or of the device, subdevice and function."""
    words = _words(pronto)
    if isinstance(words, str):
        return words
    got = _pairs_frame(words, (range(0x7C, 0x88), range(0x3C, 0x46)), 48, 0x20)
    if isinstance(got, str):
        return got
    word, b = got
    if b[:2] != [2, 32]:
        return f"the frame begins {b[0]:02X} {b[1]:02X}, not 02 20"
    if b[5] != b[2] ^ b[3] ^ b[4]:
        return f"the check byte {b[5]:02X} is not {b[2] ^ b[3] ^ b[4]:02X}, the exclusive or of the device, subdevice and function"
    return word, b, "a 48-bit frame (a 3.5 ms leader)"


# --- the documents --------------------------------------------------------------------------------------------------------------------------------------------------------------------


@dataclass
class Row:
    where: str                                    # ``sheet 'Sheet1' row 3``, ``key 2 'POWER OFF'``, ``page 1 table 1 row 'POWER'``, ``page 1 row 'POWER ON'``
    label: str
    pronto: str = ""
    fields: tuple[int, ...] = ()                  # a code the table gives as bits (Sharp's): the device and the function
    remark: str = ""
    model: str = ""                               # the file a row is in, where a document has more than one


def parse_philips(rows: list[list[str]]) -> list[Row]:
    """The sheet: a row of ``name:`` and a Pronto code for each key, and blank rows between."""
    out: list[Row] = []
    for n, row in enumerate(rows, 1):
        cells = [c.strip() for c in row]
        if not any(cells):
            continue
        if len(cells) < 2 or not cells[0].endswith(":") or not cells[1] or any(cells[2:]):
            raise ValueError(f"{PHILIPS} row {n}: not a `name:` and a code: {cells}")
        out.append(Row(f"sheet 'Sheet1' row {n}", cells[0][:-1].strip(), cells[1]))
    if not out:
        raise ValueError(f"{PHILIPS}: no key read")
    return out


def parse_samsung(rows: list[list[str]]) -> list[Row]:
    """The list: a title, then a name (with a colon, or without one, as ``HDMI1``) and its Pronto code, one after the other, as the paragraphs run (a paragraph may hold several)."""
    texts = [r[0] for r in rows if r and r[0].strip()]
    if not texts or texts[0] != SAMSUNG_TITLE:
        raise ValueError(f"{SAMSUNG}: it does not begin with the title {SAMSUNG_TITLE!r}")
    out: list[Row] = []
    name: list[str] = []
    code: list[str] = []

    def close() -> None:
        if code:
            label = " ".join(name).rstrip(":").strip()
            if not label:
                raise ValueError(f"{SAMSUNG}: a code with no name")
            out.append(Row(f"key {len(out) + 1} '{label}'", label, " ".join(code)))

    for token in " ".join(texts[1:]).split():
        if _HEX4.match(token) and (code or token == "0000"):
            code.append(token.upper())
        else:
            if code:
                close()
                name, code = [], []
            name.append(token)
    close()
    if not out or name and not code:
        raise ValueError(f"{SAMSUNG}: a name with no code, or no key")
    return out


_BITS = {5: re.compile(r"^[01]{5}$"), 6: re.compile(r"^[01]{6}$"), 2: re.compile(r"^[01]{2}$")}


def parse_sharp(rows: list[list[str]]) -> list[Row]:
    """The ruled table's rows (``page, table, key name, function, system, data, define, ext, remarks``): the three heading rows, then a key a row, its control code the bits
    ``C1`` to ``C15`` first bit first (the system five, the data six, the define two, the ext two); a row whose bits are not these is refused. A key's ``fields`` are the Sharp device
    (the system bits) and function (the data bits, then the define bits above them)."""
    head = [["KeyName", "Function", "Control Code"], ["SYSTEM", "DATA", "DEFINE", "EXT"], ["C1-C5", "C6-C11", "C12-C13", "C14-C15"]]
    cells = [[(c or "").strip() for c in r[2:]] for r in rows]
    if len(cells) < 3 or [c[:3] for c in cells[:1]] != [head[0]] or cells[1][2:6] != head[1] or cells[2][2:6] != head[2]:
        raise ValueError(f"{SHARP}: the heading rows are not KeyName/Function/Control Code, SYSTEM/DATA/DEFINE/EXT, C1-C5/C6-C11/C12-C13/C14-C15")
    out: list[Row] = []
    for r, c in zip(rows[3:], cells[3:]):
        if not any(c):
            continue
        if len(c) < 7 or not (_BITS[5].match(c[2]) and _BITS[6].match(c[3]) and _BITS[2].match(c[4]) and _BITS[2].match(c[5])) or not c[0]:
            raise ValueError(f"{SHARP} page {r[0]} table {r[1]}: a row that is not a key and its 15 bits: {c}")
        if c[5] != "10":
            raise ValueError(f"{SHARP} page {r[0]} table {r[1]} row '{c[0]}': ext bits {c[5]} are not `10`, the trailer of a Sharp frame")
        label = " ".join(c[0].split())
        device = int(c[2][::-1], 2)
        function = int(c[3][::-1], 2) | int(c[4][::-1], 2) << 6
        out.append(Row(f"page {r[0]} table {r[1]} row '{label}'", label, "", (device, function), c[6]))
    if not out:
        raise ValueError(f"{SHARP}: no key read")
    return out


def parse_panasonic(rows: list[list[str]]) -> list[Row]:
    """The pages (``page, y, x, word, x, word, ...`` for each line): **a code is the lines of hex words in the right column (the x from 150) from one that begins ``0000 xxxx 0000`` to
    the line before the next, and a key's name is the words of the left column (the x under 150) on the code's own height**, as the pages show them (a name is not before its code in
    the text, and a short name shares a line with a line of the code, so neither the order of the text nor the lines are used). The first page's names end with ``(Discrete)``, which is
    the page's kind and not a name."""
    pages: dict[int, list[tuple[float, list[str], list[str]]]] = {}
    for r in rows:
        if r[1] == "R":
            continue
        cells = [(float(r[i]), r[i + 1]) for i in range(2, len(r) - 1, 2)]
        pages.setdefault(int(r[0]), []).append((float(r[1]), [w for x, w in cells if x < 150], [w for x, w in cells if x >= 150]))
    out: list[Row] = []
    for page, lines in sorted(pages.items()):
        blocks: list[list[tuple[float, list[str]]]] = []
        for y, left, right in lines:
            if right and all(_HEX4.match(w) for w in right):
                if right[0] == "0000" and len(right) > 2 and right[2] == "0000":
                    blocks.append([])
                if not blocks:
                    raise ValueError(f"{PANASONIC} page {page}: a line of code before the first code's start")
                blocks[-1].append((y, right))
        for k, block in enumerate(blocks):
            top, bottom = block[0][0] - 4, block[-1][0] + 12
            names = [" ".join(left) for y, left, right in lines if top <= y <= bottom and left]
            label = " ".join(" ".join(names).replace("(Discrete)", "").split())
            if not label:
                raise ValueError(f"{PANASONIC} page {page}: the code {k + 1} has no name beside it")
            out.append(Row(f"page {page} row '{label}'", label, " ".join(w for y, right in block for w in right), (), "", "discrete" if page == 1 else "remote"))
    if not out:
        raise ValueError(f"{PANASONIC}: no key read")
    return out


# --- the keys -------------------------------------------------------------------------------------------------------------------------------------------------------------------------


@dataclass
class Key:
    row: Row
    fields: tuple[int, ...]                       # the numbers the form has: (device, function) or (device, subdevice, function)
    shape: str
    word: int | None
    doubts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Spec:
    document: str
    brand: str
    protocol: dict[str, Any]
    names: dict[str, str]                         # the row's model -> the file's model ("" for every row)


def _decode(spec: Spec, rows: list[Row], report: Report) -> list[Key]:
    keys: list[Key] = []
    name = spec.protocol["name"]
    for row in rows:
        if "Not implemented" in row.label:
            report.unrepresented[(MAKER, spec.document, "the document says the code is not implemented")] += 1
            continue
        if row.pronto:
            decoder = {"RC5": rc5, "NECx2": necx2, "Panasonic": kaseikyo}[name]
            got = decoder(row.pronto)
            if isinstance(got, str):
                report.unrepresented[(MAKER, spec.document, f"not read: {got}")] += 1
                report.notes.append((MAKER, spec.document, f"not read: {row.where}: {got}"))
                continue
            if name == "RC5":
                system, command, _toggle, shape, word = got
                keys.append(Key(row, (system, command), shape, word))
            elif name == "NECx2":
                word, b, shape = got
                keys.append(Key(row, (b[0], b[1], b[2]), shape, word))
            else:
                word, b, shape = got
                keys.append(Key(row, (b[2], b[3], b[4]), shape, word))
        else:
            keys.append(Key(row, row.fields, "a table of bits (the system, data and define bits, first bit first)", None))
    report.count(MAKER, f"{spec.document}: rows read", len(rows))
    return keys


def _hold(spec: Spec, keys: list[Key]) -> None:
    """The checks: a code two differently named keys share (a doubt on each), and the one rule of each document that says what a key's address should be."""
    by_code: dict[tuple[str, tuple[int, ...]], list[Key]] = {}
    for k in keys:
        by_code.setdefault((k.row.model, k.fields), []).append(k)
    for group in by_code.values():
        names = sorted({k.row.label for k in group})
        if len(names) > 1:
            for k in group:
                k.doubts.append("the document gives this code to " + " and ".join(repr(n) for n in names))
    if spec.document == PHILIPS:
        inputs = [k for k in keys if k.row.label.startswith(("AV", "HDMI", "VGA", "Side", "Front"))]
        on_three = sum(1 for k in inputs if k.fields[0] == 3)
        for k in inputs:
            if k.fields[0] != 3 and on_three > 1:
                k.doubts.append(f"the only input of the sheet that is on RC-5 system {k.fields[0]} (the other {on_three} are on system 3)")
    if spec.document == SAMSUNG:
        for k in keys:
            if k.fields[:2] != SAMSUNG_ADDRESS:
                k.doubts.append(f"device {k.fields[0]} subdevice {k.fields[1]}, not the TV address 7 and 7 of the other codes of the list")


def _citation(spec: Spec, snapshot: Snapshot, k: Key) -> str:
    entry, sha = snapshot.documents[spec.document], snapshot.sha(spec.document)
    archived = f", from the Wayback Machine snapshot {entry['snapshot']}" if "snapshot" in entry else ""
    name = spec.protocol["name"]
    numbers = {2: "system {} command {}" if name == "RC5" else "device {} function {}", 3: "device {} subdevice {} function {}"}[len(k.fields)].format(*k.fields)
    nominal = NOMINAL_WORD.get(name)
    text = (f"{HOST}/{spec.document}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}{archived}) {k.row.where}: {name} {numbers} ({k.shape}"
            + (f"; Pronto carrier word {k.word:04X}" if k.word is not None else "") + ")")
    if k.word is not None and nominal is not None and k.word != nominal:
        text += f"; the sheet's carrier word is {k.word:04X} ({khz(k.word)}), the key is the protocol's {spec.protocol['carrierHz'] // 1000} kHz"
    if k.row.remark:
        text += f"; the table's remark: '{k.row.remark}'"
    return text + "".join(f"; {d}" for d in k.doubts)


SPECS: tuple[tuple[Spec, Any, str], ...] = (
    (Spec(PHILIPS, "Philips", RC5, {"": "Discrete IR codes"}), parse_philips, "Sheet1"),
    (Spec(SAMSUNG, "Samsung", NECX2, {"": "Discrete IR commands"}), parse_samsung, "paragraphs"),
    (Spec(SHARP, "Sharp", SHARP_PROTOCOL, {"": "LCD-TV for Europe"}), parse_sharp, "tables"),
    (Spec(PANASONIC, "Panasonic", PANASONIC_PROTOCOL, {"discrete": "Plasma TV discrete codes", "remote": "Plasma TV remote codes (2007)"}), parse_panasonic, "words"),
)


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for spec, parse, sheet in SPECS:
        rows = parse(snapshot.sheet_of(spec.document, sheet))
        keys = _decode(spec, rows, report)
        # a Sharp table lists some keys twice (the same name and the same code): kept once, and the report says so
        seen: set[tuple[str, tuple[int, ...]]] = set()
        kept: list[Key] = []
        for k in keys:
            if (k.row.label, k.fields) in seen:
                report.unrepresented[(MAKER, spec.document, "the same name and code as an earlier row of the table: kept once")] += 1
                continue
            seen.add((k.row.label, k.fields))
            kept.append(k)
        _hold(spec, kept)
        for row_model, model in spec.names.items():
            members = [k for k in kept if k.row.model == row_model]
            built: list[tuple[str, dict[str, Any]]] = []
            for k in members:
                numbers = dict(zip(("device", "function") if len(k.fields) == 2 else ("device", "subdevice", "function"), k.fields))
                gate_key = (spec.protocol["name"], *k.fields)
                if gate_key not in gate:
                    gate[gate_key] = form_compiles(IMPORT_ROOT, spec.protocol, "K", {"id": "primary.irp", "type": "irp", **numbers, "confidence": TIER, "source": "probe"})
                if gate[gate_key]:
                    report.unrepresented[(MAKER, spec.document, f"does not compile: {gate[gate_key]}")] += 1
                    continue
                built.append((k.row.label, {"id": "primary.irp", "type": "irp", **numbers, "confidence": DOUBTFUL_TIER if k.doubts else TIER, "source": _citation(spec, snapshot, k)}))
            if not built:
                report.notes.append((MAKER, spec.document, f"no key kept for {model}"))
                continue
            final = model
            while (spec.brand.casefold(), final.casefold()) in authored:
                renamed = f"{final} ({spec.brand} table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(spec.brand.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            counts: Counter = Counter()
            body: dict[str, Any] = {}
            for label, form in built:
                base = "KEY_" + (fold_label(label) or "COMMAND")
                counts[base] += 1
                body[base if counts[base] == 1 else f"{base}_{counts[base]}"] = {"label": label, "forms": [form]}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(body))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(spec.brand + ' ' + final)}.json",
                        {"manufacturer": spec.brand, "model": final, "aliases": [], "controls": [f"{spec.brand} {model}"], "protocol": dict(spec.protocol), "keys": body}))
    return out
