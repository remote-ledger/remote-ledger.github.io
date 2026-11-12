"""Dune HD's IR codes for its media players and remotes (DESIGN section 102).

Dune HD's support page on IR (``dune-hd.com/support/ir/``) lists **the NEC codes of the remotes of its non-ATV media players**: one table, ``label  00 BF 10 EF`` (the customer
code's two bytes, the data byte and its complement), with a note after a key that only some remotes have (``* legacy big v1 only, discontinued``, ``* RCU models with MOUSE
button``), and says that **the latest remotes use the customer code ``CF CF`` instead of ``00 BF`` for the same codes**, and that **two discrete commands** (``DISCRETE-POWER-ON``
and ``DISCRETE-POWER-OFF``, which no Dune remote sends, for programmable remotes) have the customer code ``00 BF``. A second page, ``dunehd_google_ir_codes.html``, lists **the
Google IR codes of the Google-certified remotes**: a key, its NEC code as a 32-bit number written most significant byte first (``DE217788`` is the bytes ``88 77 21 DE`` as sent)
and its Pronto code, which this reader decodes and holds against the NEC code.

A key is the ledger's ``NEC1``: the customer code's two bytes as sent the device and the subdevice, the data byte the function. **Three files**: ``IR codes (00 BF)`` (every key of the
table, the discrete commands, the models the page names as shipped with a ``00 BF`` remote), ``IR codes (CF CF)`` (the keys of the table that are not marked discontinued: the page says
the latest remotes use ``CF CF`` for the same codes, and the discontinued remotes are ``00 BF`` ones) and ``Google IR codes``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "dune"
BRAND = "Dune HD"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
HOST = "dune-hd.com IR code pages"
IR_PAGE = "ir.html"
GOOGLE_PAGE = "dunehd_google_ir_codes.html"
_ROW = re.compile(r"^\s{2,}(\S.*?)\s{2,}([0-9A-F]{2}) ([0-9A-F]{2}) ([0-9A-F]{2}) ([0-9A-F]{2})(?:\s+\*\s*(.*?))?\s*$")

#: the models the page names as shipped with a ``00 BF`` remote (the Premium IR Remote and the discontinued ones), and which support that customer code in all firmware
MODELS_00BF = ("Dune HD Duo 4K", "Dune HD TV-102", "Dune HD TV-101", "Dune HD TV-301", "Dune HD Smart D1", "Dune HD Smart H1", "Dune HD Smart B1", "Dune HD Max", "Dune HD Duo",
               "Dune HD Lite 53D", "Dune HD Base 2.0", "Dune HD Prime 3.0", "Dune HD Base 3.0", "Dune BD Prime", "Dune HD Center", "Dune HD Base", "Dune HD Mini", "Dune HD Ultra")
REMOTES_CFCF = ("Dune HD IR Remote", "Dune HD Slim IR Remote", "Dune HD Simple IR Remote")
MODELS_GOOGLE = ("Dune HD BOXY", "Dune HD Homatics Box 4K Plus")


@dataclass(frozen=True)
class Row:
    where: str                                    # ``line 12`` of the page's ``<pre>`` blocks, or ``row 3`` of the table
    label: str
    device: int
    subdevice: int
    function: int
    written: str                                  # the code as the page writes it
    note: str = ""                                # what the page says after the key (``legacy big v1 only, discontinued``)
    doubt: str = ""


def parse_ir(rows: list[list[str]], notes: list[str]) -> list[Row]:
    """The lines of the page's ``<pre>`` blocks: a key is ``label  XX XX XX XX`` (+ ``* note``), the customer code ``00 BF`` and the data byte and its complement; **a line that is
    not a key is a note of the import (the page's own instructions), and a key whose bytes are not what the page says is not read**."""
    out: list[Row] = []
    for n, row in enumerate(rows, 1):
        line = row[0]
        m = _ROW.match(line)
        if not m:
            notes.append(f"{IR_PAGE} line {n}: {line.strip()[:90]}")
            continue
        label, bytes_, note = " ".join(m[1].split()), [int(m[i], 16) for i in range(2, 6)], " ".join((m[6] or "").split())
        if bytes_[0] != 0x00 or bytes_[1] != 0xBF or bytes_[2] ^ bytes_[3] != 0xFF:
            notes.append(f"{IR_PAGE} line {n} '{label}': `{' '.join(m[i] for i in range(2, 6))}` is not the customer code 00 BF and a data byte and its complement")
            continue
        out.append(Row(f"line {n}", label, bytes_[0], bytes_[1], bytes_[2], " ".join(m[i] for i in range(2, 6)), note))
    if not out:
        raise ValueError(f"{IR_PAGE}: no key read")
    return out


def pronto_bytes(pronto: str) -> tuple[int, list[int]] | str:
    """The carrier word and the four bytes (as sent) of a Pronto code of an NEC frame: a leader of about 9 ms and 4.5 ms and 32 bits, a space of about three marks being a one."""
    try:
        words = [int(w, 16) for w in pronto.split()]
    except ValueError:
        return "not hexadecimal"
    if len(words) < 6 or words[0] != 0 or words[2] != 0x22 or words[3] != 0:
        return "not a Pronto code of one burst of 34 pairs"
    pairs = list(zip(words[4::2], words[5::2]))[:34]
    if len(pairs) != 34 or not (0x140 <= pairs[0][0] <= 0x170 and 0x90 <= pairs[0][1] <= 0xB0):
        return "no NEC leader and 33 pairs after it"
    bits = [1 if space > 0x25 else 0 for _, space in pairs[1:33]]
    return words[1], [sum(b << i for i, b in enumerate(bits[j * 8:(j + 1) * 8])) for j in range(4)]


def parse_google(rows: list[list[str]], notes: list[str]) -> list[Row]:
    """The table: ``table number, key, NEC IR code, Pronto``. **The NEC code is the frame as a 32-bit number, most significant byte first** (``DE217788``: ``88 77 21 DE`` as sent), and
    the Pronto code is decoded and must give the same four bytes at 38 kHz (``006D``) or the row is Untested, and a code whose two byte pairs are not complements is not read."""
    if rows[0][1:] != ["Key", "NEC IR code", "Pronto HEX code"]:
        raise ValueError(f"{GOOGLE_PAGE}: the heading {rows[0][1:]} is not the table's")
    out: list[Row] = []
    for n, row in enumerate(rows[1:], 2):
        key, nec, pronto = (" ".join(c.split()) for c in row[1:])
        if not re.fullmatch(r"[0-9A-F]{8}", nec):
            notes.append(f"{GOOGLE_PAGE} row {n} '{key}': `{nec}` is not a 32-bit NEC code")
            continue
        sent = [int(nec[i:i + 2], 16) for i in range(6, -1, -2)]
        if sent[0] ^ sent[1] != 0xFF or sent[2] ^ sent[3] != 0xFF:
            notes.append(f"{GOOGLE_PAGE} row {n} '{key}': `{nec}` ({' '.join(f'{b:02X}' for b in sent)} as sent) has a byte pair that is not a byte and its complement")
            continue
        decoded = pronto_bytes(pronto)
        doubt = ""
        if isinstance(decoded, str) or decoded != (0x6D, sent):
            doubt = f"the page's Pronto code does not give the NEC code ({decoded if isinstance(decoded, str) else f'carrier {decoded[0]:04X}, bytes ' + ' '.join(f'{b:02X}' for b in decoded[1])})"
        out.append(Row(f"row {n}", key, sent[0], sent[1], sent[2], nec, "", doubt))
    if not out:
        raise ValueError(f"{GOOGLE_PAGE}: no key read")
    return out


def key_names(labels: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for label in labels:
        base = "KEY_" + (fold_label(label) or "COMMAND")
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


@dataclass(frozen=True)
class File:
    model: str
    controls: tuple[str, ...]
    page: str
    customer: tuple[int, int] | None             # the customer code a key of the IR page is sent with in this file (None: the Google table's own)
    skip_discontinued: bool = False


FILES = (File("IR codes (00 BF)", MODELS_00BF, IR_PAGE, (0x00, 0xBF)), File("IR codes (CF CF)", REMOTES_CFCF, IR_PAGE, (0xCF, 0xCF), True),
         File("Google IR codes", MODELS_GOOGLE, GOOGLE_PAGE, None))


def discontinued(row: Row) -> bool:
    return "discontinued" in row.note or "legacy" in row.note


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    notes: list[str] = []
    pages = {IR_PAGE: parse_ir(snapshot.sheet_of(IR_PAGE, "pre"), notes), GOOGLE_PAGE: parse_google(snapshot.sheet_of(GOOGLE_PAGE, "tables"), notes)}
    for note in notes:
        report.notes.append((MAKER, IR_PAGE if note.startswith(IR_PAGE) else GOOGLE_PAGE, f"not read: {note}"))
    gate: dict[tuple[int, int, int], str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for spec in FILES:
        rows = [r for r in pages[spec.page] if not (spec.skip_discontinued and discontinued(r))]
        if spec.model == "IR codes (CF CF)":                              # the discrete commands are `00 BF` ones, by the page
            rows = [r for r in rows if not r.label.startswith("DISCRETE-")]
        entry, sha = snapshot.documents[spec.page], snapshot.sha(spec.page)
        report.count(MAKER, f"{spec.page}: keys read for {spec.model}", len(rows))
        by_code: dict[int, list[Row]] = {}                               # **a code two keys share is explained where each says which remotes it is for (`PLAY` and `PLAY/PAUSE`)**
        for r in rows:
            by_code.setdefault(r.function, []).append(r)
        dup = Counter(r.label for r in rows)
        built: list[tuple[str, dict[str, Any]]] = []
        for r in rows:
            device, subdevice = spec.customer if spec.customer else (r.device, r.subdevice)
            triple = (device, subdevice, r.function)
            if triple not in gate:
                gate[triple] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": r.function,
                                                                          "confidence": TIER, "source": "probe"})
            if gate[triple]:
                report.unrepresented[(MAKER, spec.page, f"does not compile: {gate[triple]}")] += 1
                continue
            sharing = by_code[r.function]
            unexplained = len(sharing) > 1 and not all(s.note for s in sharing)
            label = f"{r.label} ({r.written.split()[2] if ' ' in r.written else r.written})" if dup[r.label] > 1 else r.label
            cite = (f"{HOST}/{spec.page}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}) {r.where} '{r.label}' (code {r.written}"
                    + (f", `* {r.note}`" if r.note else "") + f"): NEC1 device {device} subdevice {subdevice} function {r.function}"
                    + (f"; the page says the latest remotes use `CF CF` instead of `00 BF` for these codes" if spec.customer == (0xCF, 0xCF) else "")
                    + (f"; the page gives this code to {' and '.join(repr(s.label) for s in sharing)}, which no note tells apart" if unexplained else "")
                    + (f"; {r.doubt}" if r.doubt else ""))
            built.append((label, {"id": "primary.irp", "type": "irp", "device": device, "subdevice": subdevice, "function": r.function,
                                  "confidence": DOUBTFUL_TIER if unexplained or r.doubt else TIER, "source": cite}))
        final = spec.model
        while (BRAND.casefold(), final.casefold()) in authored:
            renamed = f"{final} (Dune HD table)"
            report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(BRAND.casefold(), final.casefold())]} has the name (R15)"))
            final = renamed
        body = {n: {"label": lab, "forms": [b_[1]]} for n, lab, b_ in zip(key_names([b_[0] for b_ in built]), [b_[0] for b_ in built], built)}
        report.count(MAKER, "files")
        report.count(MAKER, "keys", len(body))
        out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(final)}.json",
                    {"manufacturer": BRAND, "model": final, "aliases": [], "controls": list(spec.controls), "protocol": dict(PROTOCOL), "keys": body}))
    return out
