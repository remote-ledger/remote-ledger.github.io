"""KEF's IR code sheets for its wireless speakers and the XIO soundbar (DESIGN section 62).

Each product has a one-page PDF, ``IR Codes for Universal Remote Control``, that says ``Code Format: NEC`` and ``Factory Code:
0x01`` and lists the function codes a universal remote must send, 8 to 21 of them, one table to a sheet (the LS50 Wireless's
has two, one for the speakers made before a serial number and one for after). A page is read as the text a PDF library gives
it, converted to CSV by ``tools/official_sheets_to_csv.py`` and pinned by SHA-256 like a spreadsheet (D123): a number line
starts a row, the lines before the first code are its function, and one or two ``0x..`` lines are its codes. **The sheet's
values are the bytes in the other bit order than the ledger's NEC1**: its factory code ``0x01`` is the device ``0x80``, and
its function code ``0x40`` the function ``0x02``, as the IR Blaster's KEF remote holds them (D244). The subdevice is the
device's complement, which is what NEC1 sends.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "kef"
BRAND = "KEF"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
#: document -> (model, the pages read). The pages are one table each; of a document with two, the longer is the model's
#: file and the other, the table for the speakers made before a serial number, is checked against it.
DOCUMENTS: tuple[tuple[str, str, tuple[int, ...]], ...] = (
    ("LS50Wireless-IRcode-INT.pdf", "LS50 Wireless", (1, 2)),
    ("LS50WII_IR-Codes-for-Universal-Remote-Control.pdf", "LS50 Wireless II", (1,)),
    ("LS60W_IR_Codes_for_Universal_Remote_Control.pdf", "LS60 Wireless", (1,)),
    ("LSX-IRcode-INT.pdf", "LSX", (1,)),
    ("LSX-II_IR-Codes-for-Universal-Remote-Control.pdf", "LSX II", (1,)),
    ("XIO_IR-Command-Codes-for-Universal-Remote-Control.pdf", "XIO", (1,)),
)
_NUMBER = re.compile(r"^\d{1,3}$")
_CODE = re.compile(r"^0x[0-9A-Fa-f]{2}$")


@dataclass(frozen=True)
class Entry:
    """One row: the number the sheet gives it, the place it has in the table, its function and its code or codes."""
    number: int
    position: int
    name: str
    codes: tuple[str, ...]


@dataclass(frozen=True)
class Table:
    page: int
    product: str            # what the sheet's first sentence calls the product
    caption: str            # the line(s) of text before ``Code Format``, where there is a ``Table N:`` one
    code_format: str
    factory: str
    entries: tuple[Entry, ...]


def rev8(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


def parse_page(page: int, rows: list[list[str]]) -> Table:
    """The table of one page's text lines (one cell to a row)."""
    lines = [r[0].strip() for r in rows if r and r[0].strip()]

    def after(label: str) -> str:
        if label not in lines or lines.index(label) + 1 >= len(lines):
            raise ValueError(f"page {page} has no {label!r} line")
        return lines[lines.index(label) + 1]

    code_format, factory = after("Code Format:"), after("Factory Code:")
    head = lines[:lines.index("Code Format:")]
    product = next((re.split(r"\s+works?\s+with\b", line)[0].strip() for line in head if re.search(r"\sworks?\s+with\b", line)), "")
    caption_at = next((i for i, line in enumerate(head) if line.startswith("Table ")), None)
    caption = " ".join(head[caption_at:]) if caption_at is not None else ""
    if "Function codes" not in lines:
        raise ValueError(f"page {page} has no 'Function codes' header")
    entries: list[Entry] = []
    number: int | None = None
    name: list[str] = []
    codes: list[str] = []

    def close() -> None:
        if number is not None:
            entries.append(Entry(number, len(entries) + 1, " ".join(name), tuple(codes)))

    for line in lines[lines.index("Function codes") + 1:]:
        if _NUMBER.match(line) and (not name or codes):
            close()
            number, name, codes = int(line), [], []
        elif _CODE.match(line):
            if number is None or not name:
                raise ValueError(f"page {page}: the code {line} has no function above it")
            codes.append(line.lower())
        elif number is not None and codes:
            break                       # the text after the table (a footnote)
        elif number is not None:
            name.append(line)
    close()
    return Table(page, product, caption, code_format, factory.lower(), tuple(entries))


def read_triple(table: Table, code: str) -> tuple[int, int, int] | str:
    """``(device, subdevice, function)`` of one code of a table, or why it is not readable."""
    if table.code_format != "NEC":
        return f"the code format is {table.code_format}, not NEC"
    if not _CODE.match(table.factory):
        return "the factory code is not a byte"
    device = rev8(int(table.factory, 16))
    return device, ~device & 0xFF, rev8(int(code, 16))


def key_names(labels: list[str]) -> list[str]:
    """``KEY_`` and D49's fold of each label, a later key of one name getting ``_2``."""
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for document, model, pages in DOCUMENTS:
        tables = [parse_page(p, snapshot.sheet_of(document, f"page {p}")) for p in pages]
        main = max(tables, key=lambda t: len(t.entries))
        others = [t for t in tables if t is not main]
        where = f"kef.com IR code sheets/{document}"       # the manifest has the URL, and the file's hash is in the citation
        sha = snapshot.sha(document)
        members: list[tuple[str, str, tuple[int, int, int], str]] = []    # (label, code, triple, the row's citation)
        for row in main.entries:
            report.count(MAKER, f"{document}: rows read")
            if row.number != row.position:
                report.notes.append((MAKER, f"{document} page {main.page}",
                                     f"the row numbered {row.number} is the {row.position}th: the sheet's numbering has a typo"))
            for index, code in enumerate(row.codes):
                triple = read_triple(main, code)
                if isinstance(triple, str):
                    report.unrepresented[(MAKER, document, triple)] += 1
                    continue
                if triple not in gate:
                    gate[triple] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                        "id": "primary.irp", "type": "irp", "device": triple[0], "subdevice": triple[1], "function": triple[2],
                        "confidence": TIER, "source": "probe"})
                if gate[triple]:
                    report.unrepresented[(MAKER, document, f"does not compile: {gate[triple]}")] += 1
                    continue
                label = row.name if index == 0 else f"{row.name} (second code)"
                cite = (f"{where}@{sha} (retrieved {snapshot.retrieved}) page {main.page} row {row.number} '{row.name}' "
                        f"({main.code_format}, factory code {main.factory}, function code {code}): NEC1 device {triple[0]} "
                        f"subdevice {triple[1]} function {triple[2]}, the sheet's bytes in the other bit order")
                if len(row.codes) > 1:
                    cite += f"; the row has {len(row.codes)} codes ({', '.join(row.codes)}) and the sheet does not say how they differ"
                for other in others:
                    twin = next((r for r in other.entries if r.codes == row.codes and r.name == row.name), None)
                    cite += (f"; the sheet's page {other.page} ({other.caption or 'another table'}) has the same row" if twin else
                             f"; not in the sheet's page {other.page}")
                if main.caption:
                    cite += f"; table: {main.caption}"
                if main.product and main.product != model:
                    cite += f"; the sheet calls the product '{main.product}'"
                members.append((label, code, triple, cite))
        if not members:
            continue
        if (BRAND.casefold(), model.casefold()) in authored:
            report.collisions.append((MAKER, model))
            continue
        names = key_names([m[0] for m in members])
        keys: dict[str, dict[str, Any]] = {}
        for key, (label, code, (device, subdevice, function), cite) in zip(names, members):
            keys[key] = {"label": label, "forms": [{
                "id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                "confidence": TIER, "source": cite}]}
            report.count(MAKER, "keys")
        report.count(MAKER, "files")
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(model)}.json",
                    {"manufacturer": BRAND, "model": model, "aliases": [], "controls": [f"{BRAND} {model}"],
                     "protocol": dict(PROTOCOL), "keys": keys}))
    return out
