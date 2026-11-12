"""Shinybow's IR codes for its matrix switchers and scalers (DESIGN section 104).

Shinybow USA's site has, for each model, **a text file of Pronto codes** (``IRCODES/HexCodes/SB-5688CK.txt``: the model, a date, then a key's name and its Pronto code, a blank line between
keys) and **a PDF of the same model** (``IRCODES/PDFformat/IR-5688CK.pdf``: the custom code and the data code of each key, as the remote sends them, "NEC Standard"). The folders have no
index; the files are the models that answer.

A key is the ledger's ``NEC1``: the customer code's two bytes as sent the device and the subdevice, the data byte the function. **The Pronto code of each key is decoded** (the decoder of Dune
HD's page, D375) **and held against the model's PDF**: the key's custom code and its data code (four hexadecimal digits each, ``4DB2`` and ``A15E``) must be written in the text of the PDF
when the PDF has the model's codes as text; a key with a code the PDF does not have is Untested, and so is a code two differently named keys share (the two power keys of a model that
has one toggle code are not: that is a toggle). **A data byte that is not the complement of the next is read only when the PDF has the two bytes too** (a code the maker wrote twice), and
then the file is ``NEC1-f16`` (the fourth byte a field of its own, as Yamaha's, D332); otherwise it is not read.

One file for each model, named by the file's name on the site (``SB-5688CK``).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug
from .dune import pronto_bytes

MAKER = "shinybow"
BRAND = "Shinybow"
HOST = "shinybowusa.com IR code files"
NEC1 = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
NEC1_F16 = {"name": "NEC1-f16", "carrierHz": 38000, "minSends": 1}
_CODE = re.compile(r"^[0-9A-F]{4}( [0-9A-F]{4})+$")
_DATE = re.compile(r"^[A-Z]+ \d{1,2} \d{4}$")
_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9A-Fa-fOo]{4}(?![0-9A-Za-z])")
TOGGLE = {"POWER ON", "POWER OFF"}


@dataclass(frozen=True)
class Entry:
    where: str                                    # ``line 12`` of the file (the lines that are not empty)
    label: str
    header: str                                   # the heading line (a name with no code after it) the key is under, or ""
    pronto: str


@dataclass
class Key:
    entry: Entry
    bytes_: list[int]
    note: str = ""                                # what the file's Pronto code is, if it is not the usual NEC frame (a captured one)
    doubts: list[str] = field(default_factory=list)
    shared: bool = False                          # another key of the file has this frame under a name that is not the same


def parse_file(rows: list[list[str]], model: str, document: str) -> tuple[str, list[Entry]]:
    """The file's title and date, and its keys: a name, then a line of Pronto words. **A name with another name after it is a heading** (``IR REMOTE-01``, ``SCN-ADJ:``) of the keys below it."""
    lines = [r[0] for r in rows]
    if len(lines) < 4 or not re.fullmatch(r"SB-\S+", lines[0].strip()) or not _DATE.match(lines[1].strip()):
        raise ValueError(f"{document}: the first lines `{lines[:2]}` are not a model and a date")
    out: list[Entry] = []
    header = label = ""
    for n, line in enumerate(lines[2:], 3):
        line = line.strip()
        if _CODE.match(line):
            if not label:
                raise ValueError(f"{document} line {n}: a Pronto code with no name before it")
            out.append(Entry(f"line {n}", label, header, line))
            label = ""
        else:
            if label:
                header = label.rstrip(":").strip()
            label = line
    if label:
        raise ValueError(f"{document}: `{label}` has no code after it")
    if not out:
        raise ValueError(f"{document}: no key read")
    return lines[0].strip(), out


def learned_bytes(pronto: str) -> tuple[int, list[int]] | str:
    """The four bytes of a captured NEC frame (SB-5645LCM's, which the decoder of D375 does not read): a once-sequence of 33 pairs, the leader and 32 bits, and a repeat sequence of two pairs
    that holds the stop bit and the repeat leader, at a carrier word of the capture's own (``0060``, ``005E``, ``006B``...). The times are the capture's: a leader mark twice its space, a bit's
    space about its mark (a zero) or three times it (a one), and a gap after the last bit; whatever is between is not read."""
    try:
        words = [int(w, 16) for w in pronto.split()]
    except ValueError:
        return "not hexadecimal"
    if len(words) != 4 + 2 * 35 or words[0] != 0 or words[2] != 0x21 or words[3] != 2:
        return "not a once-sequence of 33 pairs and a repeat of 2"
    pairs = list(zip(words[4::2], words[5::2]))
    (lead_mark, lead_space), once, repeat = pairs[0], pairs[1:33], pairs[33:]
    if not 1.8 <= lead_mark / lead_space <= 2.3:
        return "no NEC leader"
    unit = lead_mark / 16
    bits = []
    for mark, space in once:
        if not 0.6 * unit <= mark <= 1.5 * unit:
            return "a bit's mark is not about a sixteenth of the leader's"
        ratio = space / mark
        bits.append(0 if 0.6 <= ratio <= 1.5 else 1 if 2.2 <= ratio <= 4.5 else None)
    if None in bits:
        return "a bit's space is neither a zero's nor a one's"
    if not (repeat[0][1] > 20 * unit and 0.8 * lead_mark <= repeat[1][0] <= 1.2 * lead_mark):
        return "the repeat sequence is not the stop bit and the repeat leader"
    return words[1], [sum(b << i for i, b in enumerate(bits[j * 8:(j + 1) * 8])) for j in range(4)]


def decode(pronto: str) -> tuple[list[int], str] | str:
    """The four bytes as sent and a note (``""`` for a code of D375's form), or why the Pronto code is not an NEC frame."""
    strict = pronto_bytes(pronto)
    if not isinstance(strict, str):
        return strict[1], ""
    lenient = learned_bytes(pronto)
    if isinstance(lenient, str):
        return f"{strict}; as a captured frame: {lenient}"
    return lenient[1], f"the Pronto code is a captured one (33 pairs, carrier word {lenient[0]:04X}, the stop bit in the repeat sequence)"


def pdf_tokens(rows: list[list[str]]) -> set[str]:
    """The four-digit hexadecimal words in the text of the PDF (``4DB2``, ``A15E``; an ``O`` for a zero, as the PDF of the SB-5654 writes ``O5FA``, is read as one; a word of four letters
    that make no digit, ``CODE``, is not)."""
    text = " ".join(r[1] for r in rows)
    return {t.upper().replace("O", "0") for t in _TOKEN.findall(text) if re.search(r"[0-9]", t)}


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple[str, int, int, int], str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for document in sorted(snapshot.documents):
        if not document.endswith(".txt"):
            continue
        model = document.removesuffix(".txt")
        title, entries = parse_file(snapshot.sheet_of(document, "text"), model, document)
        pdf_name = f"IR-{model.removeprefix('SB-')}.pdf"
        tokens = pdf_tokens(snapshot.sheet_of(pdf_name, "pages")) if pdf_name in snapshot.documents else None
        keys: list[Key] = []
        for e in entries:
            decoded = decode(e.pronto)
            if isinstance(decoded, str):
                report.notes.append((MAKER, document, f"not read: {e.where} '{e.label}': {decoded}"))
                continue
            keys.append(Key(e, decoded[0], decoded[1]))
        report.count(MAKER, "Pronto codes", len(entries))
        # the PDF holds the model's codes as text when it has one of the custom codes of the file
        checked = tokens is not None and any(f"{k.bytes_[0]:02X}{k.bytes_[1]:02X}" in tokens for k in keys)
        by_frame: dict[tuple[int, ...], list[Key]] = {}
        for k in keys:
            by_frame.setdefault(tuple(k.bytes_), []).append(k)
        for group in by_frame.values():
            names = {k.entry.label for k in group}
            if len(names) > 1 and names != TOGGLE:
                for k in group:
                    k.shared = True
                    k.doubts.append("the file gives this code to " + " and ".join(repr(n) for n in sorted(names)))
        kept: list[Key] = []
        f16 = False
        for k in keys:
            c, d = f"{k.bytes_[0]:02X}{k.bytes_[1]:02X}", f"{k.bytes_[2]:02X}{k.bytes_[3]:02X}"
            if checked:
                assert tokens is not None
                missing = [x for x in (c, d) if x not in tokens]
                if missing:
                    k.doubts.append(f"the PDF {pdf_name} does not have {' or '.join(f'`{x}`' for x in missing)}")
            if k.bytes_[2] ^ k.bytes_[3] != 0xFF:
                if not (checked and d in (tokens or set())):
                    report.notes.append((MAKER, document, f"not read: {k.entry.where} '{k.entry.label}': `{c} {d}` has a fourth byte that is not the complement of the third, "
                                                         f"and no PDF of the maker's lists it"))
                    continue
                f16 = True
            kept.append(k)
        protocol = NEC1_F16 if f16 else NEC1
        counts = Counter(k.entry.label for k in kept)
        entry, sha = snapshot.documents[document], snapshot.sha(document)
        pdf_cite = ""
        if pdf_name in snapshot.documents and checked:
            pdf_cite = f"; held against the maker's PDF {pdf_name}@{snapshot.sha(pdf_name)} (retrieved {snapshot.documents[pdf_name].get('retrieved', snapshot.retrieved)})"
        built: list[tuple[str, dict[str, Any]]] = []
        for k in kept:
            e = k.entry
            device, subdevice = k.bytes_[0], k.bytes_[1]
            function = k.bytes_[2] if not f16 else k.bytes_[2] << 8 | k.bytes_[3]
            gkey = (protocol["name"], device, subdevice, function)
            if gkey not in gate:
                gate[gkey] = form_compiles(IMPORT_ROOT, protocol, "K", {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                                                                       "confidence": TIER, "source": "probe"})
            if gate[gkey]:
                report.unrepresented[(MAKER, document, f"does not compile: {gate[gkey]}")] += 1
                continue
            label = e.label if counts[e.label] == 1 or not e.header else f"{e.label} ({e.header})"
            cite = (f"{HOST}/{document}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}) {e.where} '{e.label}'"
                    + (f" under '{e.header}'" if e.header else "")
                    + (f" (the file's title line reads `{title}`)" if title != model else "")
                    + f": {protocol['name']} device {device} subdevice {subdevice} function {function} (bytes {' '.join(f'{b:02X}' for b in k.bytes_)})"
                    + pdf_cite + (f"; {k.note}" if k.note else "") + "".join(f"; {x}" for x in k.doubts))
            built.append((label, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": function,
                                  "confidence": DOUBTFUL_TIER if k.doubts else TIER, "source": cite}))
        if not built:
            report.notes.append((MAKER, document, "no key kept"))
            continue
        final = model
        while (BRAND.casefold(), final.casefold()) in authored:
            renamed = f"{final} (Shinybow table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        seen: Counter = Counter()
        body: dict[str, Any] = {}
        for label, form in built:
            base = "KEY_" + (fold_label(label) or "COMMAND")
            seen[base] += 1
            body[base if seen[base] == 1 else f"{base}_{seen[base]}"] = {"label": label, "forms": [form]}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(body))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": [f"{BRAND} {model}"], "protocol": dict(protocol), "keys": body}))
    return out
