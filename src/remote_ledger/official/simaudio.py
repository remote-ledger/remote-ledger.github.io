"""Simaudio's table of IR codes for its Moon products (DESIGN section 68).

Simaudio's ``Table of IR codes`` (Technical Notice, Rev. 1.1, December 2002) is three pages, one for each kind of product: the
**preamplifiers and integrated amplifiers** ``Moon i-3, i-5, P-3, P-5`` (Philips RC-5, system code 16), the **CD players** ``Moon
Nova and Moon Eclipse`` (RC-5, system code 20) and the **DVD players** ``Moon Stellar and Moon Orion`` (Philips RC-6, mode code 00,
control code 04, "Customer code"; the commands in hexadecimal). Each page lists its commands as bullets, ``- Power : 12``, ``- Shuffle
1C``, ``- 0(make CD input direct access):  0``. A key is the ledger's ``RC5`` (the system code the device, the command the function)
or ``RC6`` (the control code the device, the command the function); one file for each product the page names.

The name before the number is the label as written. A direct-access digit ``0(track direct access)`` is the key ``0`` with the
parenthesis in the citation, and a name that begins with a glyph (``▲ (Cursor Up)``) is the words in its parenthesis. The amplifiers'
page ends with a note that only the P-3 has the eight inputs ``0`` to ``7`` and that the other units "will simply ignore codes that
don't apply to them", which is in the citation of each of those keys, since the document does not say which model has which.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "simaudio"
BRAND = "Simaudio"
FILE = "Simaudio-RC-5-IR-codes-rev1.1.pdf"
PROTOCOLS = {"RC5": {"name": "RC5", "carrierHz": 36000, "minSends": 1}, "RC6": {"name": "RC6", "carrierHz": 36000, "minSends": 1}}
_DIGIT = re.compile(r"^(\d)\((.*)\)$")
_PAGE = re.compile(r"^Page \d+ of \d+$")


@dataclass
class Section:
    page: int
    kind: str = ""                                  # ``Preamplifiers and integrated amplifiers``, ``CD players``...
    models: tuple[str, ...] = ()
    protocol: str = ""                              # ``RC5`` or ``RC6``
    device: int | None = None                       # the system code or the control code
    base: int = 10                                  # the commands' base
    items: list[tuple[str, int, str]] = field(default_factory=list)      # (the item as written, its command, the note of its page)
    note: str = ""


def models_of(text: str) -> tuple[str, ...]:
    """``Moon i-3, i-5, P-3, P-5`` as four models and ``Moon Nova and Moon Eclipse`` as two: a name without the brand word takes the one
    before it."""
    out: list[str] = []
    prefix = ""
    for token in (t.strip() for t in re.split(r",|\band\b", text) if t.strip()):
        m = re.match(r"^(Moon)\s+(.+)$", token)
        if m:
            prefix = m[1]
            out.append(token)
        else:
            out.append(f"{prefix} {token}".strip())
    return tuple(out)


def label_of(name: str) -> tuple[str, str]:
    """``(label, the parenthesis)`` of an item's name: ``0(track direct access)`` is ``0``, ``▲ (Cursor Up)`` is ``Cursor Up``."""
    name = name.strip().rstrip(":").strip()
    if m := _DIGIT.match(name):
        return m[1], m[2].strip()
    if name and not name[0].isalnum() and (m := re.search(r"\(([^()]*)\)", name)):
        return m[1].strip(), name
    return name, ""


def parse(rows: list[list[str]], report: Report) -> list[Section]:
    sections: list[Section] = []
    section: Section | None = None
    item: list[str] | None = None
    note: list[str] | None = None

    def close_item() -> None:
        nonlocal item
        if item is None or section is None:
            item = None
            return
        text = " ".join(item).strip()
        item = None
        parts = text.rsplit(None, 1)
        if len(parts) != 2:
            report.unrepresented[(MAKER, FILE, "a bullet that is not a name and a number")] += 1
            return
        name, value = parts[0].strip(), parts[1]
        try:
            command = int(value, section.base)
        except ValueError:
            report.unrepresented[(MAKER, FILE, f"a bullet whose number is not base {section.base}")] += 1
            return
        section.items.append((text, command, ""))

    for row in rows:
        page, line = int(row[0]), row[1].strip()
        if section is None or section.page != page:
            close_item()
            section = Section(page)
            sections.append(section)
            note = None
        if _PAGE.match(line):
            close_item()
            continue
        if line == "-":
            close_item()
            item = []
            note = None
        elif line.startswith("Æ"):
            section.models = models_of(line.lstrip("Æ").strip())
        elif m := re.match(r"^Protocol\s*:\s*Philips (RC-?\d)$", line):
            section.protocol = m[1].replace("-", "")
        elif m := re.match(r"^System code\s*:\s*(\d+)$", line):
            section.device = int(m[1])
        elif m := re.match(r"^Control code\s*:\s*([0-9A-Fa-f]+)", line):
            section.device = int(m[1], 16)
        elif line.startswith("Command codes"):
            section.base = 16 if "hexadecimal" in line.lower() else 10
        elif line.startswith("Note"):
            close_item()
            note = [line]
        elif note is not None:
            note.append(line)
            section.note = " ".join(note)
        elif item is not None:
            item.append(line)
        elif not section.kind and line not in ("Table of IR codes", "Table of IR codes - Simaudio") and not line.startswith("Rev."):
            section.kind = line
    close_item()
    return [s for s in sections if s.items]


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    sections = parse(snapshot.sheet_of(FILE, "pages"), report)
    sha = snapshot.sha(FILE)
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for section in sections:
        report.count(MAKER, "bullets read", len(section.items))
        if section.protocol not in PROTOCOLS or section.device is None or not section.models:
            report.unrepresented[(MAKER, FILE, f"page {section.page} has no protocol, code or models")] += len(section.items)
            continue
        for model in section.models:
            built: list[tuple[str, dict[str, Any]]] = []
            for text, command, _ in section.items:
                key = (section.protocol, section.device, command)
                if key not in gate:
                    gate[key] = form_compiles(IMPORT_ROOT, PROTOCOLS[section.protocol], "K", {
                        "id": "primary.irp", "type": "irp", "device": section.device, "function": command, "confidence": TIER,
                        "source": "probe"})
                if gate[key]:
                    report.unrepresented[(MAKER, FILE, f"does not compile: {gate[key]}")] += 1
                    continue
                name = text.rsplit(None, 1)[0]
                label, extra = label_of(name)
                where = "system code" if section.protocol == "RC5" else "control code"
                cite = (f"simaudio.com Table of IR codes/{FILE}@{sha} (retrieved {snapshot.retrieved}) page {section.page} '{text}' "
                        f"({section.kind}, {where} {section.device}): {section.protocol} device {section.device} function {command}")
                if extra:
                    cite += f"; the bullet says '{extra}'" if not extra.startswith(("▲", "▼", "►", "◄")) else f"; the bullet is '{extra}'"
                if section.note and _DIGIT.match(name.strip().rstrip(":").strip()):
                    cite += f"; the page's note: {section.note}"
                built.append((label, {"id": "primary.irp", "type": "irp", "device": section.device, "function": command,
                                      "confidence": TIER, "source": cite}))
            if not built:
                continue
            if (BRAND.casefold(), model.casefold()) in authored:
                report.collisions.append((MAKER, model))
                continue
            keys = {n: {"label": b[0], "forms": [b[1]]} for n, b in zip(key_names([b[0] for b in built]), built)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(model)}.json",
                        {"manufacturer": BRAND, "model": model, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(PROTOCOLS[section.protocol]), "keys": keys}))
    return out
