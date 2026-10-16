"""Emotiva's PDFs of Pronto hex codes (DESIGN section 80).

Emotiva (the American maker of the UMC processors, the USP preamplifier and the ERC CD players) published for each product a PDF that
says it is ``IR HEX Codes`` or ``Remote Codes`` "in Pronto format" and lists a key name, alone on a line, and **the key's Pronto hex**
below it. The PDFs are the Wayback Machine's copies (the site no longer has the pages they were on), their text is converted to CSV by
``tools/official_sheets_to_csv.py`` (``pages``: a line to a row) and pinned by SHA-256 as every maker's are (D123).

A key is the ledger's ``NEC1``: **the Pronto hex is decoded, not carried** (a leader of about 9 ms and 4.5 ms, 32 bits whose space is
one mark for a 0 and three for a 1, each byte least significant bit first, the first byte the device and the second the subdevice), as
Rotel's is (section 69). A string is the key's once sequence (a leader, 32 bits and a stop: 34 pairs and the 2 of the repeat) or, where the
document gave it as the repeat sequence only, its first 34 pairs. The fourth byte must be the complement of the third, which NEC1 sends
and which the reader checks; a code that two keys of a document have, or a fourth byte that is not the complement, makes the keys
Untested, and the citation says which. **The carrier is 38 kHz for every key** as the other manufacturers' NEC tables are, where the
Pronto's frequency word is 0x006D (38.0 kHz) or 0x006C (38.4 kHz): the citation gives the word the document has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "emotiva"
BRAND = "Emotiva"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
HOST = "emotiva.com IR code PDFs"
_HEXLINE = re.compile(r"^(?:[0-9A-Fa-f]{4}\s?)+$")
_GROUP = re.compile(r"\s*\((\w+(?: \w+)*) GROUP\)$")          # ``VOL + (VOLUME GROUP)``: the UMC-200 table's own grouping of its keys
FRAME_PAIRS = 34                          # a leader, 32 bits and a stop


@dataclass(frozen=True)
class Document:
    file: str
    models: tuple[str, ...]               # a file for each
    named: bool = True                    # False: the document names no model and the models are the pages it is served on
    also: str = ""                        # where the same bytes are, for the citation
    labels: tuple[tuple[str, str], ...] = ()   # (label as printed, a note on it)


DOCUMENTS: tuple[Document, ...] = (
    Document("UMC_remote_codes.pdf", ("UMC-1",)),
    Document("remoteCodes_1-25-11.pdf", ("UMC-1 Metal",),
             also="the same bytes are at emotiva.com/resources/media/umc1/remote_codes.pdf (snapshot 20140630024223)",
             labels=(("standBack Sur-", "the label is printed 'standBack Sur-', with a stray 'stand' before what is, from the alphabetical order of the list (it comes before 'Back Sur+'), 'Back Sur-'"),)),
    Document("umc200_remote_codes.pdf", ("UMC-200",)),
    Document("usp_remote_codes.pdf", ("USP",)),
    Document("erc1_hex.pdf", ("ERC-1", "ERC-2"), named=False,
             also="the same bytes are at emotiva.com/resources/media/erc2/hexcodes.pdf (snapshot 20140630024328), the ERC-2's page"),
    Document("ERC-3_IR_CODES.pdf", ("ERC-3",)),
)


@dataclass
class Item:
    page: int
    name: str
    hex: str = ""


@dataclass(frozen=True)
class Frame:
    bytes: tuple[int, int, int, int]
    carrier: int                          # the Pronto's frequency word
    repeat_only: bool                     # the string has no once sequence and the frame is the first 34 pairs of the repeat one


def tidy(line: str) -> str:
    return " ".join(line.replace("\xa0", " ").replace("‐", "-").replace("‑", "-").split())


def parse(rows: list[list[str]]) -> list[Item]:
    """The items of a document's lines (``page, line``): a key name is the last line before a hex string, and a string goes on while the
    lines are hex. Every other line (a title, a paragraph, a page header) is no key."""
    items: list[Item] = []
    last: str | None = None
    current: Item | None = None
    for row in rows:
        page, line = int(row[0]), tidy(row[1])
        if not line:
            continue
        if _HEXLINE.match(line):
            if current is None:
                if last is None:
                    raise ValueError(f"page {page}: a Pronto hex with no key name before it")
                current = Item(page, last)
                items.append(current)
            current.hex = (current.hex + " " + line).strip()
        else:
            last, current = line, None
    return items


def decode_frame(text: str) -> Frame | str:
    """The four bytes of the NEC frame of a Pronto hex, or why it is not one."""
    try:
        words = [int(w, 16) for w in text.split()]
    except ValueError:
        return "the Pronto hex has a group that is not hex"
    if len(words) < 4 or words[0] != 0:
        return "not a Pronto code of a learned signal (its first word is not 0000)"
    once, again = words[2], words[3]
    if len(words) != 4 + 2 * (once + again):
        return f"the Pronto header says {once} and {again} pairs and the string has {(len(words) - 4) / 2:g}"
    if once >= FRAME_PAIRS:
        repeat_only = False
    elif once == 0 and again >= FRAME_PAIRS:
        repeat_only = True
    else:
        return f"neither sequence has the {FRAME_PAIRS} pairs of a NEC frame ({once} and {again})"
    pairs = [(words[i], words[i + 1]) for i in range(4, 4 + 2 * FRAME_PAIRS, 2)]
    lead_mark, lead_space = pairs[0]
    if not (1.7 <= lead_mark / max(lead_space, 1) <= 2.3 and lead_mark > 100):
        return f"the leader is {lead_mark} and {lead_space}, not a NEC leader (a mark twice the space)"
    bits = []
    for mark, space in pairs[1:33]:
        if space > 2 * mark:
            bits.append(1)
        elif space < 1.5 * mark:
            bits.append(0)
        else:
            return f"a bit of {mark} and {space} is neither a 0 nor a 1"
    value = tuple(sum(bit << i for i, bit in enumerate(bits[8 * b:8 * b + 8])) for b in range(4))
    return Frame(value, words[1], repeat_only)  # type: ignore[arg-type]


def key_names(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label.replace("\u2013", "-").replace("\u2212", "-")) or "COMMAND")      # an en dash or a minus sign is a hyphen for the name (`VOL \u2013` is KEY_VOL_MINUS)
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
        where = f"{HOST}/{document.file}"
        read: list[tuple[Item, Frame]] = []
        for item in parse(snapshot.sheet_of(document.file, "pages")):
            report.count(MAKER, f"{document.file}: strings")
            frame = decode_frame(item.hex)
            if isinstance(frame, str):
                report.unrepresented[(MAKER, document.file, frame)] += 1
                continue
            read.append((item, frame))
        by_code: dict[tuple[int, int, int], list[Item]] = {}
        for item, frame in read:
            by_code.setdefault(frame.bytes[:3], []).append(item)
        labels = dict(document.labels)
        members: list[tuple[str, dict[str, Any]]] = []
        for item, frame in read:
            device, subdevice, function, check = frame.bytes
            triple = (device, subdevice, function)
            if triple not in gate:
                gate[triple] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                    "id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                    "confidence": TIER, "source": "probe"})
            if gate[triple]:
                report.unrepresented[(MAKER, document.file, f"does not compile: {gate[triple]}")] += 1
                continue
            group = _GROUP.search(item.name)
            label = item.name[:group.start()] if group else item.name
            cite = (f"{where}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}, from the Wayback Machine snapshot {entry['snapshot']}) "
                    f"page {item.page} row '{item.name}' (Pronto hex, frequency word 0x{frame.carrier:04X}): NEC1 device {device} "
                    f"subdevice {subdevice} function {function}, decoded from the hex"
                    + (f"; the label is the table's name without its group '{group.group(1)} GROUP'" if group else "")
                    + (" (the document gives the repeat sequence only, and the frame is its first 34 pairs)" if frame.repeat_only else ""))
            doubts = []
            same = [o.name for o in by_code[frame.bytes[:3]] if o is not item]
            if same:
                doubts.append("the document gives this code to " + " and ".join(f"'{n}'" for n in [item.name, *same]) + ", and a code is one function")
            if check != (~function & 0xFF):
                doubts.append(f"the frame's fourth byte 0x{check:02X} is not the complement of its third, 0x{function:02X}")
            if item.name in labels:
                doubts.append(labels[item.name])
                report.notes.append((MAKER, f"{document.file} page {item.page}", f"'{item.name}': {labels[item.name]}"))
            if doubts:
                cite += "; " + "; ".join(doubts)
            if document.also:
                cite += "; " + document.also
            if not document.named:
                cite += "; the document names no model, and the PDF is served on the page of each model it is pinned for"
            members.append((label, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice,
                                        "function": function, "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
        doubtful = [n for n, f in members if f["confidence"] == DOUBTFUL_TIER]
        if doubtful:
            report.notes.append((MAKER, document.file, f"{len(doubtful)} keys are Untested ({', '.join(doubtful)}): a code that two keys have, "
                                 "a fourth byte that is not a complement, or a label printed with a stray word"))
        if not members:
            continue
        for model in document.models:
            final = model
            if final in taken:
                renamed = f"{final} ({document.file})"
                report.notes.append((MAKER, final, f"kept as '{renamed}': an earlier document of the maker has a file of that name"))
                final = renamed
            while (BRAND.casefold(), final.casefold()) in authored:
                renamed = f"{final} (Emotiva table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            taken.add(model)
            keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                        {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(PROTOCOL), "keys": keys}))
    return out
