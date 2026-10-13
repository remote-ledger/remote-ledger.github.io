"""Rotel's IR hex code PDFs (DESIGN section 69).

Rotel's IR Codes page lists 59 models, each with a link to a PDF of its **IR hex codes**: ``ROTEL RA-1572 HEX CODES``, then sets of
codes under ``NEC 8311, MAIN IR CODES`` (and ``NEC 8314, BLUETOOTH/PC-USB CODES``, ``NEC 8321, ALTERNATIVE MAIN IR CODES``, ``NEC
8322, ZONE 2 DISCRETE CODES``...), each code a line ``IR CODE 01``, its name (``SOURCE CD``) and its **Pronto hex**. 52 PDFs serve the
59 models (a PDF may be for two models: ``A12-A14``). The page is kept as the evidence of which PDF is for which model.

A key is the ledger's ``NEC1``: the heading's ``NEC 8311`` is the address and the subaddress (0x83 and 0x11, Rotel's NEC has its own
second byte), ``IR CODE 01`` the function; the fourth byte is its complement, which NEC1 sends. **The hex is decoded and must agree
with the heading and the code** (the same 32 bits); where it does not the key is Untested and says so. **A set whose heading says
``ALTERNATIVE`` or ``ALTERNATE`` is the units' second IR code set (a unit can be set to code 1 or 2 against clashes with another Rotel remote), a file
of its own** (``RA-1572 (alternative IR codes)``); every other set is the model's file, a name that another set of the file already
has taking its set's words in parentheses. A name's ``*`` (``requires X5 Main Software V1.05``) is dropped and the set's note
is in the citation. The S14 and RAS-5000's PDF has codes of three hex digits with a 31.8 kHz carrier that is not NEC and is not read.
"""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug
from .hegel import decode_rc5

MAKER = "rotel"
BRAND = "Rotel"
PAGE = "Rotel_IR_Codes.html"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
RC5_PROTOCOL = {"name": "RC5", "carrierHz": 36000, "minSends": 1}
#: documents that are not NEC: the codes of three hex digits and a carrier of 31.8 kHz
UNREAD = {"S14_RAS-5000_HEX.pdf": "its codes have three hex digits and its Pronto carrier word is 0x82 (31.8 kHz): it is not NEC, and no protocol of the ledger's was found for it"}
_HEXLINE = re.compile(r"^(?:[0-9A-Fa-f]{4}\s?)+$")
_HEADING = re.compile(r"^NEC ([0-9A-F]{2})([0-9A-F]{2})(?:,\s*(.+))?$")
_CODE = re.compile(r"^I([RF]) CODE ([0-9A-Fa-f]{2})$")             # ``IF CODE 0F`` is a typo that four documents have
_RC5 = re.compile(r"^RC5, SYSTEM CODE (\d+), (.+)$")
_LINK = re.compile(r"\{([^}]+)\}")


@dataclass
class Item:
    page: int
    code: int | None                            # None where the text has no ``IR CODE`` line for the item
    name: str
    hex: str
    typo: bool = False                          # the line says ``IF CODE``


@dataclass
class Section:
    address: int                                # NEC: the first byte; RC-5: the system code
    sub: int                                    # NEC: the second byte
    kind: str                                   # the words after ``NEC 8311,`` or empty
    note: str = ""
    protocol: str = "NEC1"                      # or ``RC5`` for a set ``RC5, SYSTEM CODE 17, ...`` (its codes are decimal)
    items: list[Item] = field(default_factory=list)

    @property
    def alternative(self) -> bool:
        return "ALTERNAT" in self.kind                 # ``ALTERNATIVE MAIN IR CODES``, ``ALTERNATE MAIN ZONE CODES``


def tidy(line: str) -> str:
    return " ".join(line.replace("\xa0", " ").replace("‐", "-").replace("‑", "-").split())


def documents(page_rows: list[list[str]]) -> dict[str, list[str]]:
    """``{file: [the models the page links it for]}`` from the page's tables, in the page's order: a row is ``table, model, link text
    {address}`` and the file is the address's last part, as ``tools/fetch_official.py`` names it."""
    out: dict[str, list[str]] = {}
    for row in page_rows:
        if len(row) < 3 or row[1] == "Model Number":
            continue
        m = _LINK.search(row[2])
        if not m:
            continue
        name = urllib.parse.unquote(m[1]).split("/ir/")[-1].replace(" ", "_").replace("&", "and")
        if row[1] not in out.setdefault(name, []):
            out[name].append(row[1])
    return out


def title_models(title: str) -> set[str]:
    """The squashed model names a title lists: ``ROTEL RSX-1550/1560 HEX CODES`` is ``RSX1550`` and ``RSX1560`` (a number alone takes the
    letters of the name before it), ``P5/P5S2/X5/X5S2`` four, and ``Series 2`` is ``S2``."""
    words = re.sub(r"^ROTEL\s+|\s+HEX CODES$", "", title.upper()).replace("SERIES 2", "S2").replace(" S2", "S2").replace(" AND ", "/")
    out: set[str] = set()
    prefix = ""
    for token in (t.strip() for t in re.split(r"[/,]", words) if t.strip()):
        squashed = re.sub(r"[^A-Z0-9]", "", token)
        if squashed.isdigit() and prefix:
            squashed = prefix + squashed
        else:
            prefix = re.match(r"^[A-Z]*", squashed)[0]
        out.add(squashed)
    return out


def decode_nec(pronto: str) -> tuple[int, int, int, int] | None:
    """``(address, subaddress, function, the byte after it)`` of a NEC frame in Pronto form (a leader, 32 bits of a mark and a space,
    the space three marks for a 1 and one for a 0, each byte least significant bit first, a stop mark), or None."""
    try:
        words = [int(w, 16) for w in pronto.split()]
    except ValueError:
        return None
    if len(words) < 8 or words[0] != 0 or len(words) != 4 + 2 * (words[2] + words[3]):
        return None
    pairs = [(words[i], words[i + 1]) for i in range(4, 4 + 2 * words[2], 2)]
    if len(pairs) != 34:
        return None
    bits = []
    for mark, space in pairs[1:33]:
        if space > 2 * mark:
            bits.append(1)
        elif space < 1.5 * mark:
            bits.append(0)
        else:
            return None
    value = [sum(bit << i for i, bit in enumerate(bits[8 * b:8 * b + 8])) for b in range(4)]
    return value[0], value[1], value[2], value[3]


def parse(rows: list[list[str]]) -> tuple[str, list[Section]]:
    """``(the title, the sets)`` of a document's lines: a heading opens a set, a ``IR CODE`` line an item whose name is the lines up to
    its hex, and the lines of a set before its first code are its note. A page's first line is its number. A hex with no ``IR CODE``
    line before it is an item with no code: its name is the last line before it."""
    title = ""
    sections: list[Section] = []
    item: Item | None = None
    pending: list[str] = []                     # the lines since the last heading, code or hex
    reading = "name"
    last_page = 0
    for row in rows:
        page, line = int(row[0]), tidy(row[1])
        first = page != last_page
        last_page = page
        if not line or (first and re.match(r"^\d{1,3}$", line)):
            continue
        if _HEXLINE.match(line):
            if item is None and sections and pending:
                item = Item(page, None, pending.pop(), "")
                sections[-1].items.append(item)
                sections[-1].note = " ".join([sections[-1].note, *pending]).strip()
                pending = []
            if item is not None:
                item.hex = (item.hex + " " + line).strip()
                reading = "hex"
            continue
        if (m := _HEADING.match(line)):
            sections.append(Section(int(m[1], 16), int(m[2], 16), (m[3] or "").strip()))
            item, pending = None, []
            continue
        if (m := _RC5.match(line)):
            sections.append(Section(int(m[1]), 0, m[2].strip(), protocol="RC5"))
            item, pending = None, []
            continue
        if (m := _CODE.match(line)) and sections:
            base = 10 if sections[-1].protocol == "RC5" else 16
            item = Item(page, int(m[2], base), "", "", m[1] == "F")
            sections[-1].items.append(item)
            sections[-1].note = " ".join([sections[-1].note, *pending]).strip()
            pending = []
            reading = "name"
            continue
        if re.match(r"^ROTEL .* HEX CODES$", line):
            title = line
        elif item is not None and reading == "name":
            item.name = (item.name + " " + line).strip()
        elif item is not None and reading == "hex":
            item = None                                 # text after the last hex: it may be the name of a code with no ``IR CODE`` line
            pending = [line]
        elif sections:
            pending.append(line)
    for section in sections:                    # a code line the text puts after its item (X3-X3S2's first): the one item with no code
        orphans = [i for i in section.items if not i.name and not i.hex and i.code is not None]
        nameless = [i for i in section.items if i.code is None]
        if len(orphans) == len(nameless) == 1:
            nameless[0].code = orphans[0].code
            section.items.remove(orphans[0])
    return title, sections


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    files = documents(snapshot.sheet_of(PAGE, "tables"))
    report.count(MAKER, "models on the page", sum(len(v) for v in files.values()))
    gate: dict[tuple, str | None] = {}
    #: file name -> (the model it controls, protocol block, [(label, form)]), for the model files of every document
    out: dict[str, tuple[str, dict[str, Any], list[tuple[str, dict[str, Any]]]]] = {}
    agreed = disagreed = unchecked = typos = 0
    for document, models in files.items():
        if document in UNREAD:
            report.unrepresented[(MAKER, document, UNREAD[document])] += len(models)
            continue
        title, sections = parse(snapshot.sheet_of(document, "pages"))
        sha = snapshot.sha(document)
        listed = title_models(title)
        for model in models:
            if title and re.sub(r"[^A-Z0-9]", "", model.upper().replace("SERIES 2", "S2")) not in listed:
                report.notes.append((MAKER, document, f"the title '{title}' does not name the {model} the page links it for"))
        report.count(MAKER, f"{document}: codes read", sum(len(s.items) for s in sections))
        checked: dict[int, tuple[int | None, str, str]] = {}       # id(item) -> (its code, what the hex says, why it is doubtful)
        for section in sections:
            for item in section.items:
                code, said, doubt = item.code, "", ""
                if item.typo:
                    typos += 1
                    report.notes.append((MAKER, f"{document} page {item.page}", f"'{item.name}': the line says 'IF CODE {item.code:02X}', read as IR CODE"))
                if not item.hex:
                    unchecked += 1
                elif section.protocol == "NEC1":
                    decoded = decode_nec(item.hex)
                    if code is None and decoded and (decoded[0], decoded[1]) == (section.address, section.sub) and decoded[3] == decoded[2] ^ 0xFF:
                        code = decoded[2]
                        said = "the text has no IR CODE line for it: its function is its Pronto hex's"
                    elif decoded and code is not None and decoded == (section.address, section.sub, code, code ^ 0xFF):
                        said = "its Pronto hex decodes to the same address, subaddress and function"
                    if said and code is not None and "hex decodes" in said or "its function is" in said:
                        agreed += 1
                    else:
                        disagreed += 1
                        got = (f"{decoded[0]:02X}{decoded[1]:02X} function {decoded[2]:02X}, {decoded[3]:02X} after it" if decoded
                               else "no NEC frame")
                        doubt = f"its Pronto hex decodes to NEC {got} and not to NEC {section.address:02X}{section.sub:02X} function " + (
                            f"{code:02X}" if code is not None else "(no code)")
                else:
                    decoded5 = decode_rc5(item.hex)
                    if decoded5 and code is not None and decoded5[:2] == (section.address, code):
                        said = f"its Pronto hex decodes to the same RC-5 system code and command (toggle bit {decoded5[2]})"
                        agreed += 1
                    else:
                        disagreed += 1
                        doubt = ("its Pronto hex decodes to " + (f"RC-5 system {decoded5[0]} command {decoded5[1]}" if decoded5 else "no RC-5 frame")
                                 + f" and not to system {section.address} command {code}")
                if doubt:
                    report.notes.append((MAKER, f"{document} page {item.page}", f"'{item.name}': {doubt}"))
                checked[id(item)] = (code, said, doubt)
        for model in models:
            for section in sections:
                for item in section.items:
                    code, said, doubt = checked[id(item)]
                    if code is None or not item.name:
                        report.unrepresented[(MAKER, document, "a code with no name or no code")] += 1
                        continue
                    nec = section.protocol == "NEC1"
                    key = (section.protocol, section.address, section.sub, code)
                    if key not in gate:
                        form = {"id": "primary.irp", "type": "irp", "device": section.address, "function": code, "confidence": TIER,
                                "source": "probe"}
                        if nec:
                            form["subdevice"] = section.sub
                        gate[key] = form_compiles(IMPORT_ROOT, PROTOCOL if nec else RC5_PROTOCOL, "K", form)
                    if gate[key]:
                        report.unrepresented[(MAKER, document, f"does not compile: {gate[key]}")] += 1
                        continue
                    name = model + (" (alternative IR codes)" if section.alternative and nec else "") + ("" if nec else " (RC-5 codes)")
                    if doubt:
                        name += "_questionable"
                    heading = (f"NEC {section.address:02X}{section.sub:02X}" if nec else f"RC5 system code {section.address}")
                    cite = (f"rotel.com IR hex codes/{document}@{sha} (retrieved {snapshot.retrieved}) page {item.page} '{item.name}' "
                            f"({heading}{', ' + section.kind if section.kind else ''}, IR CODE "
                            f"{code:02X}" + ("" if nec else f" (decimal {code})") + "): "
                            + (f"NEC1 device {section.address} subdevice {section.sub} function {code}" if nec
                               else f"RC5 device {section.address} function {code}"))
                    if said:
                        cite += "; " + said
                    if doubt:
                        cite += "; QUESTIONABLE, kept as the document writes it: " + doubt
                    if item.name.endswith("*") and section.note:
                        cite += f"; the set's note: {section.note}"
                    form = {"id": "primary.irp", "type": "irp", "device": section.address, "function": code,
                            "confidence": DOUBTFUL_TIER if doubt else TIER, "source": cite}
                    if nec:
                        form["subdevice"] = section.sub
                    entry = out.setdefault(name, (model, dict(PROTOCOL if nec else RC5_PROTOCOL), []))
                    label = item.name.rstrip("*").strip()
                    taken = {lab for lab, _ in entry[2]}
                    if label in taken and section.kind:
                        label = f"{label} ({section.kind.title()})"
                    while label in taken:
                        label += "'"
                    entry[2].append((label, form))
    files_out: list[tuple[str, dict[str, Any]]] = []
    for name, (model, protocol, built) in sorted(out.items()):
        final = name
        while (BRAND.casefold(), final.casefold()) in authored:         # R15: another source has the name; the file is kept under another
            renamed = final.replace("_questionable", "") + " (Rotel hex codes)" + ("_questionable" if final.endswith("_questionable") else "")
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        keys = {n: {"label": b[0], "forms": [b[1]]} for n, b in zip(key_names([b[0] for b in built]), built)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(keys))
        files_out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                          {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"],
                           "protocol": protocol, "keys": keys}))
    report.count(MAKER, "Pronto hex that decodes to its heading and code", agreed)
    report.count(MAKER, "Pronto hex that decodes to another code", disagreed)
    report.count(MAKER, "codes with no Pronto hex", unchecked)
    report.count(MAKER, "codes whose line says IF CODE", typos)
    return files_out
