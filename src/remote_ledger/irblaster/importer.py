"""``rl import irblaster``: the IR Blaster database, as shipped in SwiftRemote,
imported under SPEC R19.

DESIGN section 17 (decisions D46 to D56, with D56a and D56b) is the
contract; each decision is cited where it is implemented. In outline, the
database holds ``remotes(id)``, ``models(brand, model, id)`` and
``keys(id, label, hexcode, protocol)``. It has no remote model and no key names
beyond a free-text label, so (D47, D48):

* **one ledger remote per (database id, ledger protocol)**: a file holds one
  protocol (R3, D23), a database id may use several, and one database protocol
  (``REC80``) lands on six ledger protocols;
* ``manufacturer`` is the id's most common brand, ``model`` a synthetic
  ``IR Blaster DB <id> (<ledger protocol>)``, and ``controls`` every ``models``
  row of the id as ``<BRAND> | <MODEL>``: a space, a pipe, a space. **For this
  tree ``controls`` entries are brand-and-model pairs, not free text** (D56b).
  584 brands are several words (``ACCESS HD``, ``A TREND``), so a plain
  ``<BRAND> <MODEL>`` cannot be taken apart again, and a consumer that needs the
  two (SwiftRemote is to query the ledger online) must not have to guess where
  the brand ends. The database has no pipe in any brand or model, so
  ``entry.split(" | ", 1)`` returns the brand and the model exactly;
  ``controls_entry`` refuses a pipe in either, and every row is checked before
  the first file is written, so a later checkout that breaks the rule stops the
  import instead of producing entries that cannot be split. Search ignores
  spaces and punctuation (``lookup.normalise``), so ``sony kd 49x8088`` still
  finds ``SONY | KD - 49 X 8088``;
* every key carries the database label verbatim as its ``label`` (D56a): the
  text SwiftRemote shows for the key, with its case, spacing and symbols, ``??``
  included. The key *name* is that label folded mechanically (D49) and is the
  identifier; the label is for display, search and ranking, never an
  identifier;
* every key holds one ``plausible`` ``irp`` form (D50), whose parameters come
  from the *wire reading* of the hexcode (``hex_*.FROM_DB_HEX``): what the data
  means, not what SwiftRemote's own encoders do with it (D50). The places where
  the app differs are counted in ``IMPORT.md`` (D55).

A code no hex map can hold is skipped and listed with its reason, never
dropped silently (R19.5, D54). Output is a pure function of the SQL dump, the
commit and this code, so re-running reproduces it byte for byte (D56).
"""

from __future__ import annotations

import re
import sqlite3
import string
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Any, Callable, Iterator

from ..errors import ValidationError
from ..fmt import format_document
from ..import_common import authored_names as _authored_names
from ..import_common import form_compiles
from ..protocols import REGISTRY
from ..serialize import dumps
from . import hex_japan, hex_misc, hex_nec, hex_philips, hex_sony, hex_unknown

UPSTREAM = "irblaster-db"
UPSTREAM_NAME = "IR Blaster database, as shipped in SwiftRemote"
UPSTREAM_URL = "github.com/remote-ledger/SwiftRemote"
#: The input, relative to the checkout (``tools/build_ir_db.py`` builds the
#: committed ``.sqlite`` from the same file).
INPUT = "assets/db_src/swiftremote.sql"
IMPORT_ROOT = "remotes/irblaster"
#: Between brand and model in a ``controls`` entry (D56b). Neither may contain a
#: pipe, so the first occurrence is always this separator.
CONTROLS_SEP = " | "
REPORT = "IMPORT.md"
TIER = "plausible"
MODEL_PREFIX = "IR Blaster DB"

# --- the hex maps ------------------------------------------------------------------

_MODULES = (hex_nec, hex_sony, hex_philips, hex_japan, hex_misc, hex_unknown)


def _merged(attribute: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for module in _MODULES:
        for name, value in getattr(module, attribute).items():
            if name in out:
                raise RuntimeError(f"{name} is in {attribute} of two hex modules")
            out[name] = value
    return out


#: DB protocol name -> function(hexcode) -> (ledger protocol, D, S, F): the
#: wire reading, what the import writes.
FROM_DB_HEX: dict[str, Callable[[str], tuple]] = _merged("FROM_DB_HEX")
#: The same keys, what SwiftRemote transmits today. Only for the report (D55).
FROM_DB_HEX_APP: dict[str, Callable[[str], tuple]] = _merged("FROM_DB_HEX_APP")
#: ``protocol.minSends`` per DB protocol; a missing name means 1.
MIN_SENDS: dict[str, int] = _merged("MIN_SENDS")
DEFAULT_MIN_SENDS = 1

#: What each DB protocol's hexcode is, in a few words, for the citation (D51).
#: Fixed per DB protocol, never per code: the form carries the parameters.
HOW = {
    "NEC": "32 wire bits, bytes bit-reversed",
    "NEC2": "32 wire bits, bytes bit-reversed",
    "NECx1": "32 wire bits, bytes bit-reversed",
    "NECx2": "32 wire bits, bytes bit-reversed",
    "SONY12": "wire-order bits, first is MSB",
    "SONY15": "wire-order bits, first is MSB, pad bit 0",
    "SONY20": "wire-order bits, first is MSB",
    "RC5": "12 bits, start bit inverted into F",
    "RC6": "16 bits, mode 0",
    "RCA_38": "12 bits, address nibble then command",
    "Thomson7": "12 wire bits, toggle ignored",
    "Pioneer": "wire-order bytes, bit-reversed",
    "JVC": "wire-order bytes, bit-reversed",
    "Sharp": "15 wire bits, pad bit 0",
    "Denon": "13 wire bits, 3 unused",
    "Samsung36": "wire-order bits, fields bit-reversed",
    "Proton": "wire-order bytes, bit-reversed",
    "F12_relaxed": "12-bit number, fields bit-reversed",
    "RECS80": "9 bits, MSB first, 3 unused",
    "RECS80_L": "9 bits, MSB first, 3 unused",
    "REC80": "48 wire bits, bytes bit-reversed",
    "RCC2026": "42 wire bits left-aligned in 44",
    "RCC0082": "9 biphase bits",
}

#: Per ledger protocol: what a remote file's ``protocol`` block carries beyond
#: name, carrierHz and minSends (D52).
SAMSUNG36_CLAIM = {
    "reason": (
        "the IRP says 560 us; SwiftRemote's own encoder, eight real captures and "
        "IRremoteESP8266 all say about 500 us (marks 512, spaces 490/1468), so the "
        "compiled signal follows them and not the IRP's unit"
    ),
    "source": (
        "https://github.com/bengtmartensson/IrpTransmogrifier/tree/c945e76/src/test/"
        "teaserfiles Samsung36.ict (eight keys of a Samsung Blu-ray remote, median "
        "unit about 496 us; tools/misc_capture_audit.py); "
        "https://github.com/crankyoldgit/IRremoteESP8266/blob/1e2f0f3/src/ir_Samsung.cpp"
        "#L59-L63 and #L175-L190 (sendSamsung36, 'Works on real devices'); "
        "tests/vectors/CITATIONS.md, section 'The misc family'"
    ),
}
PROTOCOL_EXTRAS: dict[str, dict[str, Any]] = {
    "Samsung36": {"unitUs": 500, "claims": {"unitUs": SAMSUNG36_CLAIM}},
}

# --- names (D47, D48, D49) ---------------------------------------------------------

#: Only ASCII letters are upper-cased: ``str.upper`` also changes some non-ASCII
#: letters (and its tables move between Unicode versions), and a regenerated
#: import must not depend on the interpreter (D56).
_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)
_SYMBOLS = (
    ("??", " UNLABELED "), ("+", " PLUS "), ("-", " MINUS "),
    ("/", " SLASH "), ("*", " STAR "), ("#", " HASH "),
)
_ILLEGAL = re.compile(r"[^A-Z0-9]+")
_SLUG = re.compile(r"[^A-Za-z0-9._+-]")


def slug(text: str) -> str:
    """A file-name part: anything outside ``[A-Za-z0-9._+-]`` becomes ``_``."""
    return _SLUG.sub("_", text) or "_"


def dir_slug(text: str) -> str:
    """:func:`slug` for a directory named after data: never ``.`` or ``..``, and
    no leading or trailing dot, which Windows will not check out (13 brands end
    in one). Such a dot becomes ``_``."""
    out = slug(text)
    if not out.strip("."):
        return "_"
    lead = len(out) - len(out.lstrip("."))
    trail = len(out) - len(out.rstrip("."))
    return "_" * lead + out[lead:len(out) - trail] + "_" * trail


def fold_label(label: str) -> str:
    """The label as the tail of a key name: ASCII upper-case, the six symbols
    spelled out, every other run of non-alphanumerics one ``_``, trimmed (D49).
    Empty when nothing alphanumeric is left (``►``)."""
    text = label.translate(_UPPER)
    for old, new in _SYMBOLS:
        text = text.replace(old, new)
    return _ILLEGAL.sub("_", text).strip("_")


def key_base(label: str) -> str:
    return "KEY_" + fold_label(label)


def hex_suffix(hexcode: str) -> str:
    return _ILLEGAL.sub("_", hexcode.translate(_UPPER))


def key_names(members: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], str]:
    """``{(label, hexcode, DB protocol): key name}`` for one file (D49).

    A folded name that more than one distinct ``(label, hexcode)`` of this file
    folds to gets ``_<HEXCODE>`` on *every* member, so a name does not depend on
    the order the rows arrive in. A collision still left (two labels folding
    alike on one code) gets ``_2``, ``_3`` in sorted order.
    """
    base = {m: key_base(m[0]) for m in members}
    carriers: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for (label, hexcode, _db), name in base.items():
        carriers[name].add((label, hexcode))
    provisional = {
        m: (f"{name}_{hex_suffix(m[1])}" if len(carriers[name]) > 1 else name)
        for m, name in base.items()
    }
    taken: set[str] = set()
    out: dict[tuple[str, str, str], str] = {}
    for m in sorted(members, key=lambda m: (provisional[m], m)):
        name, n = provisional[m], 2
        while name in taken:
            name, n = f"{provisional[m]}_{n}", n + 1
        taken.add(name)
        out[m] = name
    return out


def pick_manufacturer(models: list[tuple[str, str]]) -> str:
    """The brand with the most ``models`` rows for an id; ties go to the
    casefolded alphabetically first, then to the exact string. The database's
    casing is kept (D43)."""
    counts = Counter(brand for brand, _ in models)
    return min(counts, key=lambda b: (-counts[b], b.casefold(), b))


def controls_entry(brand: str, model: str) -> str:
    """One ``controls`` entry: ``<BRAND> | <MODEL>`` (D56b).

    The pipe is the separator, so it must not occur in either part: with none in
    the brand, the first `` | `` of an entry is the separator whatever the model
    holds, and ``entry.split(" | ", 1)`` is the inverse of this function. The
    database has no pipe in any brand or model; a checkout that has one is
    refused rather than imported into entries that cannot be split.
    """
    if "|" in brand or "|" in model:
        raise ValidationError(
            f"brand {brand!r} / model {model!r} contains a pipe, so a controls entry "
            f"'<BRAND>{CONTROLS_SEP}<MODEL>' could not be split back into the two "
            "(D56b); choose another separator before importing this database"
        )
    return f"{brand}{CONTROLS_SEP}{model}"


def split_controls_entry(entry: str) -> tuple[str, str]:
    """``<BRAND> | <MODEL>`` back into ``(brand, model)``: the exact inverse of
    :func:`controls_entry` (D56b), and the one place the format is read.

    Raises ``ValueError`` for an entry with no separator, which an imported
    file never has."""
    brand, sep, model = entry.partition(CONTROLS_SEP)
    if not sep:
        raise ValueError(f"{entry!r} is not '<BRAND>{CONTROLS_SEP}<MODEL>'")
    return brand, model


# --- the citation (D51) ---------------------------------------------------------------

#: What a key's citation looks like, and the only place that says so. The label
#: is free text (quotes, spaces, tabs, ``??``), so it is matched greedily up to
#: the *last* ``'``: everything after it is a hexcode, a DB protocol name, the
#: ``HOW`` phrase and a ledger protocol name, none of which can hold a quote
#: (``[^']*`` for the phrase makes that a rule, not a habit), so the last quote
#: is always the one the importer wrote and a label cannot move the split.
_CITATION = re.compile(
    r"irblaster-db@(?P<sha>[0-9a-f]{7}) remote (?P<id>[0-9]+), "
    r"'(?P<label>.*)' (?P<hex>[0-9A-Za-z]+) (?P<protocol>[A-Za-z0-9_]+): "
    r"(?P<how>[^']*) as (?P<ledger>[A-Za-z0-9_-]+)",
    re.DOTALL,
)


def format_citation(sha: str, db_id: int, label: str, hexcode: str,
                    protocol: str, ledger: str) -> str:
    """A key's ``source`` (D51): the only place the shape is written."""
    return (f"{UPSTREAM}@{sha} remote {db_id}, '{label}' "
            f"{hexcode} {protocol}: {HOW[protocol]} as {ledger}")


def parse_citation(source: str) -> tuple[int, str, str, str]:
    """``(db_id, label, hexcode, db_protocol)`` of a key's citation (D51): the
    inverse of :func:`format_citation`, and the one parser of the format.

    Raises ``ValueError`` for text that is not one. Everything the app API
    knows about a key beyond its label comes through here, so
    ``tests/test_app_api.py`` runs it over every key of the committed tree and
    requires the label to equal the key's own ``label`` and the hexcode and the
    protocol to be the ones the form's fields read back from.
    """
    m = _CITATION.fullmatch(source)
    if m is None:
        raise ValueError(f"{source!r} is not an irblaster-db citation (D51)")
    return int(m["id"]), m["label"], m["hex"], m["protocol"]


def citation_commit(source: str) -> str:
    """The seven-character commit a citation names."""
    m = _CITATION.fullmatch(source)
    if m is None:
        raise ValueError(f"{source!r} is not an irblaster-db citation (D51)")
    return m["sha"]


def model_name(db_id: int, ledger: str) -> str:
    return f"{MODEL_PREFIX} {db_id} ({ledger})"


def target_path(maker: str, db_id: int, ledger: str) -> str:
    return f"{IMPORT_ROOT}/{dir_slug(maker)}/{db_id}-{slug(ledger)}.json"


# --- the report (D54, D55) ----------------------------------------------------------


@dataclass
class ProtocolStats:
    """Per DB protocol, for the report's table."""
    codes: set = field(default_factory=set)
    imported_codes: set = field(default_factory=set)
    keys: int = 0
    imported: int = 0
    skipped: int = 0
    ledger: set = field(default_factory=set)


@dataclass
class Report:
    commit: str
    #: ids in the database / ids that wrote at least one file.
    ids: int = 0
    ids_without_file: int = 0
    ids_split: int = 0
    files: int = 0
    files_by_ledger: Counter = field(default_factory=Counter)
    key_rows: int = 0
    duplicates: int = 0
    remotes: Counter = field(default_factory=Counter)
    keys: Counter = field(default_factory=Counter)
    protocols: dict[str, ProtocolStats] = field(default_factory=lambda: defaultdict(ProtocolStats))
    #: (reason, id, DB protocol, hexcode, label) for every key that yields nothing.
    skipped_keys: list[tuple[str, int, str, str, str]] = field(default_factory=list)
    #: (id, manufacturer, key rows, DB protocols) for an id that writes no file.
    skipped_remotes: list[tuple[int, str, int, str]] = field(default_factory=list)
    #: (id, target path, would-be model) for authored-data collisions.
    collisions: list[tuple[int, str, str]] = field(default_factory=list)
    #: DB protocol -> distinct imported codes whose APP reading differs, with
    #: (wire fields, app fields or None) each; and the keys that carry them.
    app_differs: dict[str, dict[str, tuple]] = field(default_factory=lambda: defaultdict(dict))
    app_keys: Counter = field(default_factory=Counter)
    #: Numbers behind the "by reason" tables.
    reason_codes: dict[str, set] = field(default_factory=lambda: defaultdict(set))

    # -- rendering ------------------------------------------------------------

    def render(self) -> str:
        imported = sum(n for k, n in self.keys.items() if k.startswith("imported"))
        skipped = sum(n for k, n in self.keys.items() if k.startswith("skipped"))
        out = [
            "# IR Blaster database import report",
            "",
            "Generated by `rl import irblaster` (DESIGN.md D46 to D56); never",
            "hand-edited.",
            f"Upstream: {UPSTREAM_NAME}; original source unknown upstream.",
            f"`{UPSTREAM_URL}` @ `{self.commit}`, file `{INPUT}`.",
            "",
            "Everything the database holds that the import could not represent is",
            "listed here with its reason (SPEC R19, condition 5). Every key row of",
            "the database is either imported, skipped with a reason below, or",
            "counted as an exact duplicate.",
            "",
            "## Totals",
            "",
            "| | Count |",
            "|---|---|",
            f"| Remote ids in the database | {self.ids:,} |",
            f"| Files written | {self.files:,} |",
            f"| Ids split over several files (several ledger protocols) | {self.ids_split:,} |",
            f"| Ids with no file | {self.ids_without_file:,} |",
            f"| Key rows in the database | {self.key_rows:,} |",
            f"| Keys imported | {imported:,} |",
            f"| Keys skipped | {skipped:,} |",
            f"| Key rows dropped as exact duplicates | {self.duplicates:,} |",
        ]

        out += ["", "## By ledger protocol", "",
                "| Ledger protocol | Files | Keys imported |", "|---|---|---|"]
        for name in sorted(self.files_by_ledger):
            out.append(f"| {name} | {self.files_by_ledger[name]:,} | "
                       f"{self.keys['imported: ' + name]:,} |")

        out += [
            "", "## By database protocol", "",
            "| DB protocol | Ledger protocol | Distinct codes | Codes imported | "
            "Codes skipped | Keys imported | Keys skipped |",
            "|---|---|---|---|---|---|---|",
        ]
        for name in sorted(self.protocols):
            s = self.protocols[name]
            out.append(
                f"| {name} | {', '.join(sorted(s.ledger)) or 'none'} | {len(s.codes):,} | "
                f"{len(s.imported_codes):,} | {len(s.codes) - len(s.imported_codes):,} | "
                f"{s.imported:,} | {s.skipped:,} |"
            )

        by_reason: dict[str, list[tuple]] = defaultdict(list)
        for row in self.skipped_keys:
            by_reason[row[0]].append(row)
        out += ["", f"## Keys skipped, by reason ({len(self.skipped_keys):,} keys)", ""]
        if not by_reason:
            out.append("None.")
        else:
            out += ["| Reason | Keys | Distinct codes |", "|---|---|---|"]
            for reason in sorted(by_reason):
                out.append(f"| {_cell(reason)} | {len(by_reason[reason]):,} | "
                           f"{len(self.reason_codes[reason]):,} |")
        for reason in sorted(by_reason):
            rows = sorted(by_reason[reason], key=lambda r: (r[2], r[1], r[3], r[4]))
            out += ["", f"### {reason} ({len(rows):,} keys)", "",
                    "| Remote | DB protocol | Hexcode | Label |", "|---|---|---|---|"]
            for _reason, db_id, protocol, hexcode, label in rows:
                out.append(f"| {db_id} | {protocol} | {hexcode} | {_cell(label)} |")

        out += ["", f"## Remotes skipped: no key of the id is representable "
                f"({len(self.skipped_remotes):,})", ""]
        if not self.skipped_remotes:
            out.append("None.")
        else:
            out += ["| Remote | Manufacturer | Keys | DB protocols |", "|---|---|---|---|"]
            for db_id, maker, n, protocols in sorted(self.skipped_remotes):
                out.append(f"| {db_id} | {_cell(maker)} | {n:,} | {protocols} |")

        out += ["", "## Files skipped because an authored remote wins "
                f"(R19, condition 4) ({len(self.collisions):,})", ""]
        if not self.collisions:
            out.append("None.")
        else:
            out += ["| Remote | Would have been | Model |", "|---|---|---|"]
            for db_id, path, model in sorted(self.collisions):
                out.append(f"| {db_id} | {path} | {_cell(model)} |")

        out += self._render_app_differences()
        return "\n".join(out) + "\n"

    def _render_app_differences(self) -> list[str]:
        total_codes = sum(len(v) for v in self.app_differs.values())
        total_keys = sum(self.app_keys.values())
        out = [
            "", "## Where SwiftRemote's own reading differs from the ledger's", "",
            "The database stores every hexcode in wire order. The ledger holds that",
            "reading (D50). SwiftRemote's own code reads the codes of the protocols",
            "below differently, so the signal it transmits for them is not the one the",
            "imported key compiles to. A code is counted here when the ledger protocol,",
            "device, subdevice or function SwiftRemote's reading gives is not what the",
            "wire reading gives, or SwiftRemote's reading cannot send the code at all.",
            "Every other imported protocol reads identically in both.",
            "",
            f"{total_codes:,} distinct codes, {total_keys:,} imported keys.",
            "",
        ]
        if not self.app_differs:
            return out + ["None."]
        out += ["| DB protocol | Imported codes | Codes that differ | "
                "of which the app cannot send as a frame | Imported keys | Keys that differ |",
                "|---|---|---|---|---|---|"]
        for name in sorted(self.app_differs):
            differ = self.app_differs[name]
            refused = sum(1 for _wire, app in differ.values() if app is None)
            out.append(
                f"| {name} | {len(self.protocols[name].imported_codes):,} | {len(differ):,} | "
                f"{refused:,} | {self.protocols[name].imported:,} | {self.app_keys[name]:,} |"
            )
        out += ["", "The first three codes of each, in hexcode order "
                "(ledger protocol, device, subdevice, function):", "",
                "| DB protocol | Hexcode | Ledger reading | SwiftRemote's reading |",
                "|---|---|---|---|"]
        for name in sorted(self.app_differs):
            for hexcode in sorted(self.app_differs[name])[:3]:
                wire, app = self.app_differs[name][hexcode]
                out.append(f"| {name} | {hexcode} | {_fields(wire)} | "
                           f"{_fields(app) if app else 'cannot be sent'} |")
        return out


def _fields(fields: tuple) -> str:
    name, device, subdevice, function = fields
    sub = "-" if subdevice is None else str(subdevice)
    return f"{name} D={device} S={sub} F={function}"


def report_totals(text: str) -> dict[str, int]:
    """The ``## Totals`` table of ``IMPORT.md`` as ``{row label: count}``: what
    :meth:`Report.render` wrote, read back (the app API reports the keys the
    import could not represent from here, since the tree holds no trace of a
    key it refused). Empty for text with no such table."""
    out: dict[str, int] = {}
    in_totals = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_totals = line == "## Totals"
        elif in_totals and (m := re.fullmatch(r"\| (.+?) \| ([0-9,]+) \|", line)):
            out[m[1]] = int(m[2].replace(",", ""))
    return out


def _cell(value: Any) -> str:
    """A table cell, in a code span when that is safe."""
    text = " ".join(str(value).split()) or " "
    text = text.replace("|", "\\|")
    return text if "`" in text or not text.strip() else f"`{text}`"


def app_reading_of(protocol: str, hexcode: str, wire: tuple):
    """``None`` when SwiftRemote reads a code as the ledger does (``wire`` is the
    ledger's ``(ledger protocol, D, S, F)``), else ``(wire, app fields or None)``;
    ``None`` as the second member is a code the app's reading cannot send as a
    frame at all (D55)."""
    if FROM_DB_HEX_APP.get(protocol) is FROM_DB_HEX[protocol]:
        return None
    try:
        app = tuple(FROM_DB_HEX_APP[protocol](hexcode))
    except (ValueError, KeyError):
        return wire, None
    return None if app == wire else (wire, app)


def app_reading_differs(protocol: str, hexcode: str) -> bool:
    """Whether SwiftRemote's own reading of one code is not the wire reading the
    ledger holds, computed from the two tables and nothing else. The app API
    states it per DB protocol (``appReadingDiffers``) as "true when it is true
    of any code present". Raises ``ValueError`` for a code the wire reading
    itself refuses, which no committed key has."""
    return app_reading_of(protocol, hexcode, tuple(FROM_DB_HEX[protocol](hexcode))) is not None


# --- one id ------------------------------------------------------------------------


@dataclass(frozen=True)
class Mapped:
    ledger: str
    device: int
    subdevice: int | None
    function: int


class Importer:
    """State shared by every id: the caches that make 413k keys cheap, the
    report, the authored names."""

    def __init__(self, commit: str, authored: dict[tuple[str, str], str], report: Report):
        self.sha = commit[:7]
        self.authored = authored
        self.report = report
        #: (DB protocol, hexcode) -> Mapped, or the reason it yields nothing.
        self._resolved: dict[tuple[str, str], Mapped | str] = {}
        #: compile-gate results by the inputs that determine a compiled signal.
        self._compiles: dict[tuple, str | None] = {}

    # -- one code -------------------------------------------------------------

    def protocol_block(self, ledger: str, min_sends: int) -> dict[str, Any]:
        block: dict[str, Any] = {
            "name": ledger,
            "carrierHz": REGISTRY[ledger].nominal_carrier_hz,
            "minSends": min_sends,
        }
        block.update(PROTOCOL_EXTRAS.get(ledger, {}))
        return block

    def _gate(self, mapped: Mapped, min_sends: int) -> str | None:
        block = self.protocol_block(mapped.ledger, min_sends)
        signature = (mapped.ledger, block["carrierHz"], block.get("unitUs"),
                     mapped.device, mapped.subdevice, mapped.function)
        if signature not in self._compiles:
            form: dict[str, Any] = {
                "id": "primary.irp", "type": "irp", "device": mapped.device,
                "function": mapped.function, "confidence": TIER, "source": "probe",
            }
            if mapped.subdevice is not None:
                form["subdevice"] = mapped.subdevice
            self._compiles[signature] = form_compiles(
                IMPORT_ROOT, {k: v for k, v in block.items() if k != "claims"}, "K", form)
        return self._compiles[signature]

    def resolve(self, protocol: str, hexcode: str) -> Mapped | str:
        """What one ``(DB protocol, hexcode)`` becomes, or why it yields nothing
        (D54). Pure, so cached."""
        cached = self._resolved.get((protocol, hexcode))
        if cached is not None:
            return cached
        result: Mapped | str
        mapper = FROM_DB_HEX.get(protocol)
        if mapper is None:
            result = f"no hex map for the DB protocol {protocol}"
        else:
            try:
                mapped = Mapped(*mapper(hexcode))
            except ValueError as exc:
                result = str(exc)
            else:
                why = self._gate(mapped, MIN_SENDS.get(protocol, DEFAULT_MIN_SENDS))
                result = mapped if why is None else f"does not compile to Pronto: {why}"
        self._resolved[(protocol, hexcode)] = result
        return result

    def app_reading(self, protocol: str, hexcode: str, mapped: Mapped):
        """``None`` when SwiftRemote reads the code as the ledger does, else
        ``(wire fields, app fields or None)`` (D55)."""
        return app_reading_of(
            protocol, hexcode, (mapped.ledger, mapped.device, mapped.subdevice, mapped.function))

    # -- one remote id ---------------------------------------------------------

    def import_id(self, db_id: int, models: list[tuple[str, str]],
                  rows: list[tuple[str, str, str]]) -> Iterator[tuple[str, dict[str, Any]]]:
        """``(target path, document)`` for every file one id yields."""
        report = self.report
        maker = pick_manufacturer(models) if models else ""
        controls = sorted({controls_entry(brand, model) for brand, model in models})

        def skip(reason: str, protocol: str, hexcode: str, label: str) -> None:
            report.keys[f"skipped: {reason}"] += 1
            report.skipped_keys.append((reason, db_id, protocol, hexcode, label))
            report.reason_codes[reason].add((protocol, hexcode))
            report.protocols[protocol].skipped += 1

        seen: set[tuple[str, str, str]] = set()
        by_ledger: dict[str, list[tuple[str, str, str, Mapped]]] = defaultdict(list)
        for label, hexcode, protocol in rows:
            report.key_rows += 1
            if (label, hexcode, protocol) in seen:
                report.duplicates += 1
                continue
            seen.add((label, hexcode, protocol))
            stats = report.protocols[protocol]
            stats.keys += 1
            stats.codes.add(hexcode)
            if not models:
                skip("the id has no models row, so there is no manufacturer to file "
                     "it under", protocol, hexcode, label)
                continue
            result = self.resolve(protocol, hexcode)
            if isinstance(result, str):
                skip(result, protocol, hexcode, label)
                continue
            by_ledger[result.ledger].append((label, hexcode, protocol, result))

        if not by_ledger:
            report.ids_without_file += 1
            report.remotes["skipped: no key of the id is representable"] += 1
            report.skipped_remotes.append(
                (db_id, maker, len(rows), ", ".join(sorted({p for _l, _h, p in rows}))))
            return
        written = 0
        for ledger in sorted(by_ledger):
            members = by_ledger[ledger]
            model = model_name(db_id, ledger)
            target = target_path(maker, db_id, ledger)
            if (maker.casefold(), model.casefold()) in self.authored:
                report.remotes["skipped: an authored remote wins"] += 1
                report.collisions.append((db_id, target, model))
                for label, hexcode, protocol, _m in members:
                    report.keys["skipped: an authored remote wins"] += 1
                    report.protocols[protocol].skipped += 1
                continue
            written += 1
            yield target, self.document(db_id, maker, controls, ledger, model, members)
        report.ids_split += written > 1

    def document(self, db_id: int, maker: str, controls: list[str], ledger: str,
                 model: str, members) -> dict[str, Any]:
        report = self.report
        names = key_names([(label, hexcode, protocol) for label, hexcode, protocol, _m in members])
        min_sends = max(MIN_SENDS.get(p, DEFAULT_MIN_SENDS) for _l, _h, p, _m in members)
        keys: dict[str, dict[str, Any]] = {}
        for label, hexcode, protocol, mapped in sorted(
                members, key=lambda m: (names[(m[0], m[1], m[2])], m[0], m[1], m[2])):
            form: dict[str, Any] = {
                "id": "primary.irp", "type": "irp", "device": mapped.device,
            }
            if mapped.subdevice is not None:
                form["subdevice"] = mapped.subdevice
            form["function"] = mapped.function
            form["confidence"] = TIER
            form["source"] = format_citation(self.sha, db_id, label, hexcode, protocol, ledger)
            # D56a: the database's own label, verbatim, beside the folded name.
            keys[names[(label, hexcode, protocol)]] = {"label": label, "forms": [form]}

            stats = report.protocols[protocol]
            stats.imported += 1
            stats.imported_codes.add(hexcode)
            stats.ledger.add(ledger)
            report.keys[f"imported: {ledger}"] += 1
            differs = self.app_reading(protocol, hexcode, mapped)
            if differs is not None:
                report.app_differs[protocol][hexcode] = differs
                report.app_keys[protocol] += 1
        report.remotes["imported"] += 1
        report.files += 1
        report.files_by_ledger[ledger] += 1
        doc: dict[str, Any] = {"manufacturer": maker, "model": model, "aliases": []}
        if controls:
            doc["controls"] = controls
        doc["protocol"] = self.protocol_block(ledger, min_sends)
        doc["keys"] = keys
        return doc


# --- the whole database (D56) -----------------------------------------------------


def read_database(checkout: Path) -> sqlite3.Connection:
    """The checkout's SQL dump, executed into an in-memory sqlite (what
    ``tools/build_ir_db.py`` does to a file)."""
    path = checkout / INPUT
    if not path.is_file():
        raise ValidationError(f"{path} is not there; is {checkout} a SwiftRemote checkout?")
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(path.read_text(encoding="utf-8"))
    except sqlite3.Error as exc:
        raise ValidationError(f"{path} is not a SQL dump sqlite can run: {exc}") from exc
    return con


def iter_documents(checkout: Path, commit: str, authored: dict[tuple[str, str], str],
                   report: Report) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every file the database yields, in id order, filling ``report``."""
    con = read_database(checkout)
    models: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for db_id, brand, model in con.execute(
            "SELECT id, brand, model FROM models ORDER BY id, brand, model"):
        models[db_id].append((brand, model))
    # D56b, before any file is written: a brand or model that breaks the
    # controls format stops the import here, not half-way through the tree.
    for rows in models.values():
        for brand, model in rows:
            controls_entry(brand, model)
    importer = Importer(commit, authored, report)
    ids = [r[0] for r in con.execute("SELECT id FROM remotes ORDER BY id")]
    keys = con.execute(
        "SELECT id, label, hexcode, protocol FROM keys ORDER BY id, protocol, hexcode, label")
    by_id = {db_id: [(label, hexcode, protocol) for _id, label, hexcode, protocol in group]
             for db_id, group in groupby(keys, key=lambda r: r[0])}
    for db_id in ids:
        report.ids += 1
        yield from importer.import_id(db_id, models.get(db_id, []), by_id.pop(db_id, []))
    # Keys whose id is not in ``remotes``: the schema's foreign key forbids it,
    # but a dump written with the check off could hold one. Import it, don't drop it.
    for db_id in sorted(by_id):
        report.ids += 1
        yield from importer.import_id(db_id, models.get(db_id, []), by_id[db_id])
    con.close()


def import_tree(checkout: Path, commit: str, authored: dict[tuple[str, str], str]):
    """Every document, as ``{repo path: document}`` plus the report."""
    report = Report(commit=commit)
    out = dict(iter_documents(checkout, commit, authored, report))
    return out, report


def write_import(root: Path, checkout: Path, commit: str) -> Report:
    """Rewrite ``remotes/irblaster/`` wholesale (D56): every ``*.json`` and
    ``IMPORT.md`` are the importer's; anything else there (``README.md``,
    ``LICENSE``) is authored and left alone. Files are written as they are
    produced, so 413k keys are never in memory at once."""
    authored = _authored_names(root, IMPORT_ROOT)
    report = Report(commit=commit)
    target_root = root / IMPORT_ROOT
    written: set[str] = set()
    for rel, doc in iter_documents(checkout, commit, authored, report):
        if rel in written:
            raise RuntimeError(f"two remotes share the path {rel}")
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(doc, sort_keys=False), encoding="utf-8", newline="\n")
        # rl fmt's canonical form (D20), so `rl fmt --check` stays clean.
        path.write_text(format_document(path), encoding="utf-8", newline="\n")
        written.add(rel)
    for stale in sorted(target_root.rglob("*.json")) if target_root.exists() else []:
        if stale.relative_to(root).as_posix() not in written:
            stale.unlink()
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / REPORT).write_text(report.render(), encoding="utf-8", newline="\n")
    for empty in sorted((p for p in target_root.rglob("*") if p.is_dir()), reverse=True):
        if not any(empty.iterdir()):
            empty.rmdir()
    return report
