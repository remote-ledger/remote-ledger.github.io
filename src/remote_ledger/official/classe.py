"""Classé's IR code lists and HEX code PDFs (DESIGN section 83).

Classé published, for each of its amplifiers, preamplifiers, processors and CD players, a one to three page PDF of the codes its remote sends,
under ``IR_CODES``: **an IR code list** (``The Classé SSP-300 / SSP-600 uses RC6 protocol for infrared communications... The Classé Remote
Control uses a frequency of 56.0 kHz``: a table of ``Sys code``, ``IR code`` and ``Action``, a row for each key) and, for the newer products, a
**HEX code PDF** (``RC6 MODE 0, SYSTEM CODE 016``, then for each code ``IR CODE 2 TOGGLE 0``, its name and a Pronto hex, and again with
``TOGGLE 1``). The seven PDFs of the current support site are Classé's own server's; the six of the Delta series (the CAP-2100, CDP-100, CDP-102
and its family, CP-500, CP-700 and SSP-300 and 600), which that site no longer has, are the Wayback Machine's copies of its old downloads page's.

**A key is the ledger's ``RC6``** (mode 0: the system code the device and the IR code the function, at 56 kHz, which is what the documents say and what
the Pronto hex of every HEX code PDF has) **or, for the SSP-25's, its ``RC5``** (system 25, 36 kHz). **The Pronto hex of the HEX code PDFs is decoded and
must be the system, the IR code and the toggle bit the heading and the line beside it say**; where a string does not decode or says another code, the key
is Untested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "classe"
BRAND = "Classé"
HOST = "classeaudio.com IR code documents"
RC6 = {"name": "RC6", "carrierHz": 56000, "minSends": 1}
RC5 = {"name": "RC5", "carrierHz": 36000, "minSends": 1}
RC5_UNIT = 0x22                       # 889 microseconds at 36 kHz: the half-bit of the SSP-25's Pronto hex
RC6_UNIT = 0x19                       # 444 microseconds at 56 kHz, in the Pronto hex of every HEX code PDF
_HEXLINE = re.compile(r"^(?:[0-9A-Fa-f]{4}\s?)+$")
_SECTION = re.compile(r"^(RC[56])(?: MODE (\d))?, SYSTEM CODE (\d+)$")
_ITEM = re.compile(r"^IR CODE (\d+) TOGGLE ([01])$")


@dataclass(frozen=True)
class Document:
    file: str
    kind: str                            # list, commands or hex
    models: tuple[str, ...]              # a file for each


DOCUMENTS: tuple[Document, ...] = (
    Document("CAP-2100_IR_codes.pdf", "list", ("CAP-2100",)),
    Document("CDP-100_IR_codes_v1-20.pdf", "list", ("CDP-100",)),
    Document("CDP-102_202_300_500_and_CDT-300_IR_codes_v1-19.pdf", "list", ("CDP-102", "CDP-202", "CDP-300", "CDP-502", "CDT-300")),
    Document("CP-500_IR_codes.pdf", "list", ("CP-500",)),
    Document("CP-700_IR_codes.pdf", "list", ("CP-700",)),
    Document("SSP-300_600_IR_codes_v1-20.pdf", "list", ("SSP-300", "SSP-600")),
    Document("CLASSE_SSP-800_IR_codes_v1-24.pdf", "list", ("SSP-800",)),
    Document("CLASSE_Delta_1_2_Amplifier_IR_Commands.pdf", "commands", ("Delta amplifiers",)),
    Document("CLASSE_CP-800_HEX.pdf", "hex", ("CP-800 (HEX codes)",)),
    Document("CLASSE_Delta_1_2_Amplifiers_HEX.pdf", "hex", ("Delta and CT amplifiers (HEX codes)",)),
    Document("CLASSE_SSP-800_CT-SSP_HEX.pdf", "hex", ("SSP-800 (HEX codes)", "CT-SSP (HEX codes)")),
    Document("CLASSE_SSP-25_HEX.pdf", "hex", ("SSP-25",)),
)


@dataclass
class Entry:
    page: int
    label: str
    protocol: str                        # RC6 or RC5
    system: int
    code: int
    where: str = ""                      # what the citation says of the place
    doubts: list[str] | None = None


def tidy(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def cells_of(row: list[str]) -> list[tuple[int, str]]:
    return [(int(row[i]), tidy(row[i + 1])) for i in range(2, len(row), 2)]


def read_list(rows: list[list[str]]) -> list[Entry]:
    """The rows of ``Sys code, IR code, Action``: a row has a three-digit system code, an IR code and the action; a line with only the
    action's column is the rest of the action above it (`(low/medium/high)`)."""
    out: list[Entry] = []
    action_x: int | None = None
    previous: tuple[int, int, Entry] | None = None
    for row in rows:
        page, y = int(row[0]), int(row[1])
        cells = cells_of(row)
        if any(t.lower() == "sys code" for _, t in cells):
            action_x = next(x for x, t in cells if t == "Action")
            previous = None
            continue
        if action_x is None:
            continue
        if len(cells) >= 3 and re.fullmatch(r"\d{3}", cells[0][1]) and cells[1][1].isdigit():
            entry = Entry(page, " ".join(t for _, t in cells[2:]), "RC6", int(cells[0][1]), int(cells[1][1]))
            out.append(entry)
            previous = (page, y, entry)
        elif len(cells) == 1 and previous and previous[0] == page and abs(cells[0][0] - action_x) <= 6 and y - previous[1] <= 20:
            previous[2].label += " " + cells[0][1]
            previous = (page, y, previous[2])
    return out


def read_commands(rows: list[list[str]]) -> list[Entry]:
    """The rows of ``command, action, Description`` of a sheet whose line says ``RC6 Coded Mode 0 system 201``."""
    system: int | None = None
    out: list[Entry] = []
    for row in rows:
        cells = cells_of(row)
        text = " ".join(t for _, t in cells)
        if m := re.search(r"RC6 Coded Mode 0 system (\d+)", text):
            system = int(m.group(1))
            continue
        if system is not None and len(cells) >= 2 and cells[0][1].isdigit():
            label = cells[1][1]
            out.append(Entry(int(row[0]), label, "RC6", system, int(cells[0][1]),
                             f"the description '{cells[2][1]}'" if len(cells) >= 3 else ""))
    return out


def prontos(text: str) -> list[str]:
    """The Pronto strings of a hex that may be two (a header of four words says how many pairs follow)."""
    words = text.split()
    out = []
    while words:
        try:
            n = 4 + 2 * (int(words[2], 16) + int(words[3], 16))
        except (ValueError, IndexError):
            return out + [" ".join(words)]
        out.append(" ".join(words[:n]))
        words = words[n:]
    return out


def decode_rc6(pronto: str) -> tuple[int, int, int, int] | str:
    """``(mode, toggle, system, command)`` of an RC6 frame in Pronto form (a leader of 6 and 2 units, a start bit, 3 mode bits, a trailer bit of
    two units a half, 8 bits of system and 8 of command, each a mark and a space or the other way), or why it is not one."""
    try:
        w = [int(x, 16) for x in pronto.split()]
    except ValueError:
        return "not hex"
    if len(w) < 6 or w[0] != 0 or len(w) != 4 + 2 * (w[2] + w[3]):
        return f"the Pronto header says {w[2] + w[3] if len(w) > 3 else '?'} pairs and the string has {(len(w) - 4) / 2:g}"
    body, unit = w[4:], RC6_UNIT
    levels: list[int] = []
    for i in range(0, len(body), 2):
        levels += [1] * round(body[i] / unit)
        if i + 1 < len(body) - 1:
            levels += [0] * round(body[i + 1] / unit)
    if levels[:8] != [1] * 6 + [0] * 2:
        return "the leader is not 6 units and 2"
    levels = levels[8:] + [0] * max(0, 44 - len(levels[8:]))
    if len(levels) != 44:
        return f"{len(levels)} half-bits, not 44"

    def bit(a: int, b: int) -> int | None:
        return 1 if (a, b) == (1, 0) else 0 if (a, b) == (0, 1) else None

    start = bit(levels[0], levels[1])
    mode = [bit(levels[2 + 2 * i], levels[3 + 2 * i]) for i in range(3)]
    trailer = tuple(levels[8:12])
    toggle = 1 if trailer == (1, 1, 0, 0) else 0 if trailer == (0, 0, 1, 1) else None
    data = [bit(levels[12 + 2 * i], levels[13 + 2 * i]) for i in range(16)]
    if start != 1 or None in mode or toggle is None or None in data:
        return "a bit is not a mark and a space"
    value = lambda bits: int("".join(map(str, bits)), 2)       # noqa: E731
    return value(mode), toggle, value(data[:8]), value(data[8:])


def decode_rc5(pronto: str) -> tuple[int, int, int] | str:
    """``(system, command, toggle)`` of an RC-5 frame in Pronto form (a half-bit of one unit, the first mark standing for the start bit's second half)."""
    try:
        w = [int(x, 16) for x in pronto.split()]
    except ValueError:
        return "not hex"
    if len(w) < 6 or w[0] != 0 or w[2] != 0 or len(w) != 4 + 2 * (w[2] + w[3]):
        return "the Pronto header is not that of a learned RC-5 string"
    body, unit = w[4:], RC5_UNIT
    levels: list[int] = []
    for i in range(0, len(body), 2):
        levels += [1] * round(body[i] / unit)
        if i + 1 < len(body) - 1:
            levels += [0] * round(body[i + 1] / unit)
    levels = [0] + levels
    levels += [0] * (28 - len(levels))
    if len(levels) != 28:
        return f"{len(levels)} half-bits, not 28"
    bits = []
    for i in range(0, 28, 2):
        pair = (levels[i], levels[i + 1])
        if pair not in ((0, 1), (1, 0)):
            return "a bit is not a mark and a space"
        bits.append(1 if pair == (0, 1) else 0)
    value = lambda b: int("".join(map(str, b)), 2)             # noqa: E731
    return value(bits[3:8]), value(bits[8:14]) | ((1 - bits[1]) << 6), bits[2]


def read_hex(rows: list[list[str]]) -> tuple[list[Entry], list[str]]:
    """``(the keys, the notes)`` of a HEX code PDF's lines (``page, line``): a section heading gives the protocol and the system code, ``IR CODE n TOGGLE t``
    opens an item, the lines before its hex are its name and the hex goes on while the lines are hex. A code is a key once, and **each string is decoded and
    must be the heading's system, the item's code and a toggle bit**."""
    items: list[dict[str, Any]] = []
    section: tuple[str, int] | None = None
    current: dict[str, Any] | None = None
    for row in rows:
        page, line = int(row[0]), tidy(row[1])
        if not line:
            continue
        if m := _SECTION.match(line):
            section = (m.group(1), int(m.group(3)))
            if m.group(2) not in (None, "0"):
                raise ValueError(f"page {page}: {line!r} is not mode 0")
            current = None
        elif m := _ITEM.match(line):
            if section is None:
                raise ValueError(f"page {page}: an item before a section heading")
            current = {"page": page, "protocol": section[0], "system": section[1], "code": int(m.group(1)), "toggle": int(m.group(2)), "name": [], "hex": ""}
            items.append(current)
        elif current is not None:
            if _HEXLINE.match(line):
                current["hex"] = (current["hex"] + " " + line).strip()
            elif not current["hex"]:
                current["name"].append(line)
    entries: dict[tuple[str, int, int], Entry] = {}
    notes: list[str] = []
    for item in items:
        key = (item["protocol"], item["system"], item["code"])
        label = " ".join(item["name"])
        entry = entries.get(key)
        if entry is None:
            entry = entries[key] = Entry(item["page"], label, item["protocol"], item["system"], item["code"], "", [])
        elif label != entry.label:
            entry.doubts.append(f"the lines of the code's two strings have the names '{entry.label}' and '{label}'")
        strings = prontos(item["hex"])
        for string in strings:
            got = decode_rc6(string) if item["protocol"] == "RC6" else decode_rc5(string)
            if isinstance(got, str):
                entry.doubts.append(f"a Pronto hex of its TOGGLE {item['toggle']} item does not decode: {got}")
                continue
            if item["protocol"] == "RC6":
                mode, toggle, system, command = got
                agrees = (mode, system, command) == (0, item["system"], item["code"])
            else:
                system, command, toggle = got
                agrees = (system, command) == (item["system"], item["code"])
            if not agrees:
                entry.doubts.append(f"a Pronto hex of its TOGGLE {item['toggle']} item decodes to system {system} code {command}")
            elif len(strings) == 1 and toggle != item["toggle"]:
                entry.doubts.append(f"the Pronto hex of its TOGGLE {item['toggle']} item has toggle bit {toggle}")
        if len(strings) > 1:
            notes.append(f"page {item['page']}: the item 'IR CODE {item['code']} TOGGLE {item['toggle']}' ({label}) has {len(strings)} Pronto strings, the second of the toggle bit the heading of the next item lacks")
    return list(entries.values()), notes


def key_names(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    taken: set[str] = set()
    for document in DOCUMENTS:
        entry = snapshot.documents[document.file]
        sha = snapshot.sha(document.file)
        when = f"retrieved {entry.get('retrieved', snapshot.retrieved)}" + (f", from the Wayback Machine snapshot {entry['snapshot']}" if "snapshot" in entry else "")
        prefix = f"{HOST}/{document.file}@{sha} ({when})"
        if document.kind == "list":
            entries = read_list(snapshot.sheet_of(document.file, "cells"))
        elif document.kind == "commands":
            entries = read_commands(snapshot.sheet_of(document.file, "cells"))
        else:
            entries, notes = read_hex(snapshot.sheet_of(document.file, "pages"))
            for note in notes:
                report.notes.append((MAKER, document.file, note))
        by_code: dict[tuple[str, int, int], list[str]] = {}
        for e in entries:
            by_code.setdefault((e.protocol, e.system, e.code), []).append(e.label)
        members: list[tuple[str, dict[str, Any]]] = []
        for e in entries:
            report.count(MAKER, f"{document.file}: rows read")
            protocol = RC6 if e.protocol == "RC6" else RC5
            key = (e.protocol, e.system, e.code)
            if key not in gate:
                gate[key] = form_compiles(IMPORT_ROOT, protocol, "K", {"id": "primary.irp", "type": "irp", "device": e.system, "function": e.code,
                                                                     "confidence": TIER, "source": "probe"})
            if gate[key]:
                report.unrepresented[(MAKER, document.file, f"does not compile: {gate[key]}")] += 1
                continue
            doubts = list(e.doubts or [])
            if len(by_code[key]) > 1:
                doubts.append("the document gives this code to " + " and ".join(f"'{n}'" for n in by_code[key]) + ", and a code is one function")
            how = (f"RC6 mode 0 system code {e.system}, IR code {e.code}" if e.protocol == "RC6" else f"RC5 system code {e.system}, IR code {e.code}")
            cite = f"{prefix} page {e.page} row '{e.label}' ({how}" + (f", {e.where}" if e.where else "") + f"): {e.protocol} device {e.system} function {e.code}"
            if document.kind == "hex" and not any("Pronto" in d for d in doubts):
                cite += ", the Pronto hex decoded and agreeing"
            if doubts:
                cite += "; " + "; ".join(doubts)
            members.append((e.label, {"id": "primary.irp", "type": "irp", "device": e.system, "function": e.code,
                                      "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
        if not members:
            continue
        protocol = RC5 if entries[0].protocol == "RC5" else RC6
        for model in document.models:
            final = model
            if final in taken:
                renamed = f"{final} ({document.file})"
                report.notes.append((MAKER, final, f"kept as '{renamed}': an earlier document of the maker has a file of that name"))
                final = renamed
            while (BRAND.casefold(), final.casefold()) in authored:
                renamed = f"{final} (Classé table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            taken.add(model)
            keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                        {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model.split(' (')[0]}"],
                         "protocol": dict(protocol), "keys": keys}))
    return out
