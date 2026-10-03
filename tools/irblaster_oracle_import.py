#!/usr/bin/env python3
"""End to end: every database key, through the imported file and the ledger's
own compile path, against what SwiftRemote transmits.

The family tools (``tools/irblaster_oracle_<family>.py``) prove the encoders:
they map a hexcode and call the registry's encoder. This tool proves the
*import*: it starts from the SQL dump's key rows and the files
``rl import irblaster`` wrote, so a wrong file name, key name, parameter,
carrier, unit or ``minSends`` in the output cannot hide behind a correct
encoder.

For every ``keys`` row of the dump (``assets/db_src/swiftremote.sql``), it

1. finds the imported key by the row in the form's citation
   (``irblaster-db@<sha> remote <id>, '<label>' <hexcode> <DB protocol>: ...``),
   or finds the row in ``IMPORT.md``'s skipped lists, or fails (a row that is
   neither was dropped silently, or invented);
2. loads the file with ``remote_ledger.remote.load_remote``, compiles the key
   with ``Remote.compile_group`` (a Pronto string, R12), decodes that string
   back with ``pronto.decode``, and plays it the way the file's ``minSends``
   says;
3. checks the key's ``label`` is the database's, verbatim: it must equal the
   label of the row the citation names, and that row must be a row of the dump,
   so a label that differs from the database's, or a key without one, is a
   problem (D56a);
4. compares it with the signal the app transmits for the same
   ``(DB protocol, hexcode)`` (``<oracle>/by_protocol/<PROTOCOL>.jsonl``, the
   app's own ``buildButtonFromDbRow`` then ``previewIRButton``) under the family
   tools' rule: the carrier within 5 %, the same number of durations, every
   duration within 12 % of the app's or 150 us, whichever is larger.

**The only forgiven differences are the documented framing ones**, per DB
protocol, in ``FRAMING`` below, each argued in the family notes: NEC's frame
without a lead-out; RC6's lead-out; the lead-out of Samsung36, Fujitsu, Teac-K
and SharpDVD; idle gaps of JVC, Pioneer, Sharp and Denon; Sharp's and Denon's
three frames; a toggle bit the file cannot carry (D3b); Blaupunkt's closing
sync, which a signal cannot carry (D1). Each is counted in its own note, never
folded into ``matched``.

**Where the wire reading differs from SwiftRemote's** (the codes
``FROM_DB_HEX_APP`` reads another way), the imported signal is expected to
differ from the app's. Those rows are classified ``differs by reading``, and
only when this tool can show the app's signal is exactly the one
``FROM_DB_HEX_APP``'s fields compile to (``masked_fields`` for the Sony15
codes the app masks, the RCC2026 port for the codes the app reads as no frame
at all). That confirms the classification; it is not a statement that either
reading is right. The set of such rows is recomputed here and must equal the
counts in ``IMPORT.md``.

The classes, per DB protocol (distinct codes and keys): ``matched``,
``differs by reading``, ``unrepresentable`` (skipped by the importer, listed
in ``IMPORT.md``, and refused by the hex map), ``UNEXPLAINED`` (anything else,
including a skipped row the report does not list and a report row that was not
skipped). Exit status is 1 on any unexplained row, on a report that disagrees
with the files, or on a structural fault (manufacturer, controls, model, path,
a key's label).

Usage::

    python tools/irblaster_oracle_import.py --checkout ~/srcs/SwiftRemote \\
        --oracle ORACLE [--ledger DIR] [--workers N] [--json FILE]
    python tools/irblaster_oracle_import.py ... --list-differences

``--ledger`` is the repository root holding ``remotes/irblaster/`` (default:
this repository). ``--list-differences`` prints every distinct code on which
SwiftRemote's reading and the ledger's differ, for the app owner.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from remote_ledger import pronto  # noqa: E402
from remote_ledger.irblaster import (  # noqa: E402
    hex_japan, hex_misc, hex_nec, hex_philips, hex_sony, hex_unknown,
)
from remote_ledger.protocols import REGISTRY  # noqa: E402
from remote_ledger.remote import load_remote  # noqa: E402

import irblaster_oracle_sony as sony_tool  # noqa: E402
import irblaster_oracle_unknown as unknown_tool  # noqa: E402

IMPORT_ROOT = "remotes/irblaster"
INPUT = "assets/db_src/swiftremote.sql"

CARRIER_TOLERANCE = 0.05
REL_TOLERANCE = 0.12
ABS_TOLERANCE_US = 150
#: A space this long is the idle gap between frames, not part of a bit.
GAP_US = 8_000

# --- the readings, merged here independently of the importer ----------------------

MODULES = (hex_nec, hex_sony, hex_philips, hex_japan, hex_misc, hex_unknown)
WIRE: dict = {}
APP: dict = {}
MIN_SENDS: dict = {}
for _m in MODULES:
    WIRE.update(_m.FROM_DB_HEX)
    APP.update(_m.FROM_DB_HEX_APP)
    MIN_SENDS.update(_m.MIN_SENDS)

# --- the documented framing differences -------------------------------------------
#
# Nothing else about a signal is forgiven. Each entry cites the note that argues it.

#: The app's three Sharp/Denon frames are the IRP's intro plus one pass of its
#: repeat (DESIGN D64); the file's minSends of 1 plays the intro only.
FULL_SIGNAL = frozenset({"Sharp", "Denon"})
#: The app's legacy NEC path stops at the last mark (DESIGN D61, disagreement 3).
DROP_FINAL = frozenset({"NEC"})
#: The app uses one constant idle gap per protocol where the IRP pads to an
#: extent; a gap is compared on its own and counted (DESIGN D64).
GAPS_FREE = frozenset({"JVC", "Pioneer", "Sharp", "Denon"})
#: Ledger protocols whose final gap the app sets to its own constant: Samsung36
#: (DESIGN D65) and the REC80 vendors whose IRP gap is not Panasonic's
#: 173 units (DESIGN D66, disagreement 2).
LEADOUT_FREE = frozenset({"Samsung36", "Fujitsu", "Teac-K", "SharpDVD"})
#: RC6's idle after a frame is the rest of 107 ms, the app's the standard's six
#: units: the ledger's final space must be at least the app's (DESIGN D63).
FINAL_AT_LEAST = frozenset({"RC6"})
#: Protocols with a toggle bit the file cannot carry (D3b): the file compiles
#: T=0, the app's preview shows T=1; either may match.
TOGGLES = frozenset({"RC5", "RC6", "Thomson7", "RECS80", "RECS80_L"})
#: Blaupunkt's closing sync is the opening sync, which IrSignal cannot carry (D1).
CLOSING_SYNC = frozenset({"Blaupunkt"})

CITATION = re.compile(
    r"^irblaster-db@([0-9a-f]{7}) remote (\d+), '(.*)' ([0-9A-Za-z]+) ([A-Za-z0-9_]+): "
    r"(.*) as ([A-Za-z0-9_-]+)$", re.S)

MATCHED = "matched"
DIFFERS = "differs by reading"
UNREPRESENTABLE = "unrepresentable"
UNEXPLAINED = "UNEXPLAINED"
CLASSES = (MATCHED, DIFFERS, UNREPRESENTABLE, UNEXPLAINED)


# --- comparing two signals -----------------------------------------------------------


def within(ours: int, app: int) -> bool:
    return abs(ours - app) <= max(ABS_TOLERANCE_US, REL_TOLERANCE * max(ours, app))


def burst(db_protocol: str, ledger: str, signal, min_sends: int) -> list[int]:
    """What one press plays: the intro once and the repeat ``minSends - 1``
    times (the repeat ``minSends`` times when there is no intro); Sharp and
    Denon play their whole signal; Blaupunkt adds its closing sync."""
    if db_protocol in FULL_SIGNAL:
        out = list(signal.intro) + list(signal.repeat)
    elif signal.intro:
        out = list(signal.intro) + list(signal.repeat) * (min_sends - 1)
    else:
        out = list(signal.repeat) * min_sends
    if ledger in CLOSING_SYNC:
        out += list(signal.intro[: len(signal.intro) - len(signal.repeat)])
    return out


def compare(db_protocol: str, ledger: str, ours: list[int], app: list[int]):
    """``(None, notes)`` when ``ours`` is the app's signal under the documented
    framing, else ``(why, [])``. ``notes`` names every allowance used."""
    notes: list[str] = []
    ours = list(ours)
    if db_protocol in DROP_FINAL:
        ours = ours[:-1]
        notes.append("app stops at the last mark")
    if len(ours) != len(app):
        return f"{len(ours)} durations against the app's {len(app)}", []
    exact = True
    for i, (a, b) in enumerate(zip(ours, app)):
        last = i == len(app) - 1
        gap = i % 2 == 1 and (a >= GAP_US or b >= GAP_US)
        if a == b:
            continue
        exact = False
        if within(a, b):
            continue
        if last and ledger in LEADOUT_FREE:
            notes.append("lead-out differs")
        elif last and db_protocol in FINAL_AT_LEAST and a >= b:
            notes.append("lead-out longer")
        elif gap and db_protocol in GAPS_FREE and a >= GAP_US and b >= GAP_US:
            notes.append("gaps differ")
        else:
            return f"duration {i} is {a} us against the app's {b} us", []
    if db_protocol in FULL_SIGNAL:
        notes.append("three frames")
    if exact:
        notes.append("exact")
    return None, sorted(set(notes))


# --- one imported key ------------------------------------------------------------------

_CONFIG: dict = {}


def init_worker(oracle: dict) -> None:
    _CONFIG["oracle"] = oracle
    _CONFIG["decoded"] = {}


def decode(text: str, carrier_hz: int):
    cache = _CONFIG["decoded"]
    key = (text, carrier_hz)
    if key not in cache:
        if len(cache) > 20_000:
            cache.clear()
        cache[key] = pronto.decode(text, carrier_hz=carrier_hz)
    return cache[key]


def _signal(ledger: str, fields, remote, toggle, quantise: bool = True):
    """The registry's encoding of ``fields``, with the file's carrier and unit,
    quantised through Pronto as a compiled key is (or as the encoder gives it)."""
    _name, device, subdevice, function = fields
    entry = REGISTRY[ledger]
    kwargs = {} if toggle is None else {"toggle": toggle}
    signal = entry.encode(device=device, subdevice=subdevice, function=function,
                          carrier_hz=remote.protocol.carrier_hz,
                          unit_us=remote.protocol.unit_us, **kwargs)
    if not quantise:
        return signal
    return pronto.decode(pronto.encode(signal), carrier_hz=remote.protocol.carrier_hz)


def _match(db_protocol: str, ledger: str, signal, remote, app: list[int]):
    why, notes = compare(db_protocol, ledger, burst(db_protocol, ledger, signal, remote.protocol.min_sends), app)
    if why is None and ledger in CLOSING_SYNC:
        notes = sorted({*notes, "closing sync added back (D1)"})
    return why, notes


def _app_difference_confirmed(db_protocol, hexcode, app_fields, remote, app):
    """Is the app's signal what its own reading compiles to? ``(None, notes)``
    when it is, else ``(why not, [])``."""
    if app_fields is not None:
        app_ledger = app_fields[0]
        last = "no toggle state matches"
        for toggle in ((0, 1) if db_protocol in TOGGLES else (None,)):
            signal = _signal(app_ledger, app_fields, remote, toggle)
            why, notes = _match(db_protocol, app_ledger, signal, remote, app)
            if why is None:
                notes = notes + (["T=1 only (D3b)"] if toggle == 1 else [])
                # and, before Pronto's rounding, is it the app's signal to the microsecond?
                raw = _signal(app_ledger, app_fields, remote, toggle, quantise=False)
                if burst(db_protocol, app_ledger, raw, remote.protocol.min_sends) == app:
                    notes.append("app signal is its reading's encoding exactly")
                return None, notes
            last = why
        return f"the app's reading does not compile to the app's signal: {last}", []
    # the app's reading is no frame at all: only a port of what it does instead can say
    if db_protocol == "SONY15":
        fields = sony_tool.masked_fields("SONY15", hexcode)
        signal = _signal("Sony15", fields, remote, None)
        why, notes = _match(db_protocol, "Sony15", signal, remote, app)
        if why is None:
            return None, notes + ["the app masks the code to 15 bits"]
        return f"masked Sony15 code does not compile to the app's signal: {why}", []
    if db_protocol == "RCC2026":
        if unknown_tool.app_rcc2026(hexcode, "last") == app:
            return None, ["a port of the app's stale encoder reproduces its signal exactly"]
        return "the port of the stale RCC2026 encoder differs from the app's signal", []
    return "the app's reading refuses the code and nothing explains what it sends", []


def process_file(path: str):
    """One imported file -> ``[(row, class, notes, differs, problems)]``.

    ``row`` is ``(id, label, hexcode, DB protocol)`` as the citation spells it.
    """
    oracle = _CONFIG["oracle"]
    remote = load_remote(Path(path))
    out = []
    for key in sorted(remote.keys):
        forms = remote.keys[key]
        form = forms[0]
        problems: list[str] = []
        parsed = CITATION.match(form.source or "")
        if not parsed or len(forms) != 1:
            out.append((None, UNEXPLAINED, [], False, [f"{path}: {key}: no one-form irblaster citation"]))
            continue
        _sha, db_id, label, hexcode, db_protocol, _how, cited_ledger = parsed.groups()
        row = (int(db_id), label, hexcode, db_protocol)
        ledger = remote.protocol.name
        if cited_ledger != ledger:
            problems.append(f"citation says {cited_ledger}, the file {ledger}")
        fields = (ledger, form.device, form.subdevice, form.function)
        # D56a: the key's label is the database's, verbatim. The citation names the
        # row, and `run` requires that row to be in the dump, so equal to the
        # citation's label is equal to the dump's.
        if key not in remote.labels:
            problems.append("the key has no label")
        elif remote.labels[key] != label:
            problems.append(f"label {remote.labels[key]!r} is not the database's {label!r}")
        # the file says what the wire reading of the hexcode says
        try:
            expected = WIRE[db_protocol](hexcode)
        except (ValueError, KeyError) as exc:
            out.append((row, UNEXPLAINED, [], False, [f"imported, but the wire reading refuses it: {exc}"]))
            continue
        if tuple(expected) != fields:
            problems.append(f"file holds {fields}, the wire reading gives {tuple(expected)}")
        if remote.protocol.min_sends != MIN_SENDS.get(db_protocol, 1):
            problems.append(f"minSends {remote.protocol.min_sends}, expected {MIN_SENDS.get(db_protocol, 1)}")
        if remote.protocol.carrier_hz != REGISTRY[ledger].nominal_carrier_hz:
            problems.append("carrierHz is not the registry's nominal")
        if (remote.protocol.unit_us is not None) != (ledger == "Samsung36"):
            problems.append("unitUs is set on a protocol it should not be, or missing from Samsung36")

        record = oracle.get((db_protocol, hexcode))
        if record is None:
            out.append((row, UNEXPLAINED, [], False, problems + ["the oracle has no signal for this code"]))
            continue
        app = record["pattern"]
        text = remote.compile_group(key, "primary")
        signal = decode(text, remote.protocol.carrier_hz)
        if abs(signal.carrier_hz - record["freq"]) > CARRIER_TOLERANCE * record["freq"]:
            problems.append(f"carrier {signal.carrier_hz} against the app's {record['freq']}")

        try:
            app_fields = tuple(APP[db_protocol](hexcode))
            differs = app_fields != fields
        except ValueError:
            app_fields, differs = None, True

        why, notes = _match(db_protocol, ledger, signal, remote, app)
        if why is not None and db_protocol in TOGGLES:
            other = _signal(ledger, fields, remote, 1)
            why1, notes1 = _match(db_protocol, ledger, other, remote, app)
            if why1 is None:
                why, notes = None, notes1 + ["T=1 only (D3b)"]
        elif why is None and db_protocol in TOGGLES:
            notes = notes + ["T=0"]

        if problems:
            out.append((row, UNEXPLAINED, notes, differs, problems))
        elif differs:
            if why is None:
                # the readings differ and the signals still agree: say so, never fold it away
                out.append((row, MATCHED, notes + ["reading differs, signal agrees"], True, []))
            else:
                bad, how = _app_difference_confirmed(db_protocol, hexcode, app_fields, remote, app)
                out.append((row, DIFFERS if bad is None else UNEXPLAINED, how, True,
                            [] if bad is None else [bad]))
        elif why is None:
            out.append((row, MATCHED, notes, False, []))
        else:
            out.append((row, UNEXPLAINED, [], False, [why]))
    return out


# --- reading the inputs -----------------------------------------------------------------


def read_database(checkout: Path):
    path = checkout / INPUT
    con = sqlite3.connect(":memory:")
    con.executescript(path.read_text(encoding="utf-8"))
    rows = [(i, label, hexcode, protocol)
            for i, label, hexcode, protocol in con.execute("SELECT id, label, hexcode, protocol FROM keys")]
    models: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for brand, model, i in con.execute("SELECT brand, model, id FROM models"):
        models[i].append((brand, model))
    ids = [r[0] for r in con.execute("SELECT id FROM remotes")]
    return rows, models, ids


def read_oracle(oracle: Path) -> dict:
    out = {}
    for path in sorted((oracle / "by_protocol").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                out[(record["protocol"], record["hex"])] = {
                    "freq": record["freq"], "pattern": record["pattern"]}
    return out


def parse_report(text: str) -> dict:
    """What ``IMPORT.md`` says, in the parts this tool checks."""
    def number(label: str) -> int | None:
        m = re.search(rf"^\| {re.escape(label)} \| ([\d,]+) \|$", text, re.M)
        return int(m.group(1).replace(",", "")) if m else None

    skipped: Counter = Counter()
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line
        elif section and section.startswith("## Keys skipped, by reason") and line.startswith("| "):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
            if len(cells) == 4 and cells[0].isdigit():
                skipped[(int(cells[0]), cells[1], cells[2])] += 1
    differs: dict[str, tuple[int, int, int, int, int]] = {}
    in_differs = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_differs = line.startswith("## Where SwiftRemote's own reading differs")
        elif in_differs and line.startswith("| ") and not line.startswith("| DB protocol") \
                and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 6 and all(c.replace(",", "").isdigit() for c in cells[1:]):
                differs[cells[0]] = tuple(int(c.replace(",", "")) for c in cells[1:])
    return {
        "files": number("Files written"), "ids": number("Remote ids in the database"),
        "rows": number("Key rows in the database"), "imported": number("Keys imported"),
        "skipped": number("Keys skipped"), "skipped_rows": skipped, "differs": differs,
    }


def _app_refuses(proto: str, hexcode: str) -> bool:
    try:
        APP[proto](hexcode)
    except ValueError:
        return True
    return False


def expected_manufacturer(models: list[tuple[str, str]]) -> str:
    counts = Counter(b for b, _ in models)
    top = max(counts.values())
    return min((b for b, c in counts.items() if c == top), key=lambda b: (b.casefold(), b))


def expected_dir(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._+-]", "_", name) or "_"
    if not out.strip("."):
        return "_"
    lead, trail = len(out) - len(out.lstrip(".")), len(out) - len(out.rstrip("."))
    return "_" * lead + out[lead:len(out) - trail] + "_" * trail


def structure_problems(ledger_root: Path, models, files: list[Path]) -> list[str]:
    """Manufacturer, model, controls and path of every file, re-derived from
    the dump's ``models`` table."""
    problems = []
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        m = re.fullmatch(r"(\d+)-(.+)\.json", path.name)
        if not m:
            problems.append(f"{path}: not named <id>-<protocol>.json")
            continue
        db_id, ledger = int(m.group(1)), m.group(2)
        rel = path.relative_to(ledger_root).as_posix()
        rows = models.get(db_id)
        if not rows:
            problems.append(f"{rel}: id {db_id} has no models rows")
            continue
        maker = expected_manufacturer(rows)
        if doc["manufacturer"] != maker:
            problems.append(f"{rel}: manufacturer {doc['manufacturer']!r}, expected {maker!r}")
        if path.parent.name != expected_dir(maker):
            problems.append(f"{rel}: directory is not {expected_dir(maker)!r}")
        if doc["model"] != f"IR Blaster DB {db_id} ({ledger})":
            problems.append(f"{rel}: model {doc['model']!r}")
        if doc["protocol"].get("name") != ledger:
            problems.append(f"{rel}: protocol {doc['protocol'].get('name')!r} is not the file name's")
        if doc.get("aliases") != []:
            problems.append(f"{rel}: aliases are not empty")
        # D56b: "<BRAND> | <MODEL>", a space, a pipe, a space
        controls = sorted({f"{b} | {mo}" for b, mo in rows})
        if doc.get("controls") != controls:
            problems.append(f"{rel}: controls are not the id's models rows as '<BRAND> | <MODEL>'")
        else:
            # and the entries split back into exactly the rows (needs no pipe in a brand)
            if {tuple(e.split(" | ", 1)) for e in controls} != set(rows):
                problems.append(f"{rel}: controls do not split back into the id's models rows")
    return problems


# --- the whole run ----------------------------------------------------------------------


def run(ledger_root: Path, checkout: Path, oracle_dir: Path, workers: int, limit: int | None = None):
    rows, models, ids = read_database(checkout)
    oracle = read_oracle(oracle_dir)
    files = sorted((ledger_root / IMPORT_ROOT).rglob("*.json"))
    if limit:
        files = files[:limit]
    report_text = (ledger_root / IMPORT_ROOT / "IMPORT.md").read_text(encoding="utf-8")
    report = parse_report(report_text)

    results = []
    if workers > 1:
        with ProcessPoolExecutor(workers, initializer=init_worker, initargs=(oracle,)) as pool:
            for chunk in pool.map(process_file, [str(f) for f in files], chunksize=8):
                results.extend(chunk)
    else:
        init_worker(oracle)
        for f in files:
            results.extend(process_file(str(f)))

    db_rows = Counter(rows)
    by_protocol: dict[str, dict] = defaultdict(lambda: {
        "keys": Counter(), "codes": defaultdict(set), "notes": Counter(), "worst": 0.0})
    problems: list[str] = []
    seen: Counter = Counter()
    differs_codes: dict[str, set] = defaultdict(set)
    differs_keys: Counter = Counter()
    imported_codes: dict[str, set] = defaultdict(set)
    imported_keys: Counter = Counter()

    def tally(proto: str, hexcode: str, cls: str, notes=()):
        entry = by_protocol[proto]
        entry["keys"][cls] += 1
        entry["codes"][cls].add(hexcode)
        for note in notes:
            entry["notes"][note] += 1

    for row, cls, notes, differs, why in results:
        if row is None:
            problems += why
            continue
        seen[row] += 1
        if row not in db_rows:
            problems.append(f"imported key {row} is not a row of the dump (invented)")
            tally(row[3], row[2], UNEXPLAINED)
            continue
        imported_codes[row[3]].add(row[2])
        imported_keys[row[3]] += 1
        if differs:
            differs_codes[row[3]].add(row[2])
            differs_keys[row[3]] += 1
        tally(row[3], row[2], cls, notes)
        for text in why:
            problems.append(f"{row}: {text}")
    for row, n in seen.items():
        if n != db_rows.get(row, 0) and row in db_rows:
            problems.append(f"{row} is imported {n} times for {db_rows[row]} row(s)")

    # rows not imported: each must be in IMPORT.md and refused by the wire reading
    full = limit is None
    listed = report["skipped_rows"].copy()
    for row, n in db_rows.items():
        missing = n - seen.get(row, 0)
        if missing <= 0 or not full:
            continue
        db_id, label, hexcode, proto = row
        try:
            WIRE[proto](hexcode)
            refused = False
        except KeyError:
            refused = True                      # a protocol with no hex map
        except ValueError:
            refused = True
        key = (db_id, proto, hexcode)
        for _ in range(missing):
            if not refused:
                problems.append(f"{row} is representable but was not imported")
                tally(proto, hexcode, UNEXPLAINED)
            elif listed[key] <= 0:
                problems.append(f"{row} was skipped and IMPORT.md does not list it")
                tally(proto, hexcode, UNEXPLAINED)
            else:
                listed[key] -= 1
                tally(proto, hexcode, UNREPRESENTABLE)
    if full:
        for key, n in listed.items():
            if n > 0:
                problems.append(f"IMPORT.md lists {key} as skipped {n} more time(s) than the dump has it")

    # IMPORT.md's totals and difference table against what the files say
    n_imported = sum(imported_keys.values())
    if full:
        for label, expected in (("Files written", len(files)), ("Keys imported", n_imported),
                                ("Key rows in the database", len(rows)),
                                ("Remote ids in the database", len(set(ids) | {r[0] for r in rows}))):
            said = {"Files written": report["files"], "Keys imported": report["imported"],
                    "Key rows in the database": report["rows"],
                    "Remote ids in the database": report["ids"]}[label]
            if said != expected:
                problems.append(f"IMPORT.md says {label} = {said}, the files and dump say {expected}")
        for proto in sorted(set(differs_codes) | set(report["differs"])):
            said = report["differs"].get(proto)
            refused = sum(1 for h in differs_codes[proto] if _app_refuses(proto, h))
            mine = (len(imported_codes[proto]), len(differs_codes[proto]), refused,
                    imported_keys[proto], differs_keys[proto])
            if said != mine:
                problems.append(f"IMPORT.md's difference table for {proto} is {said}; "
                                f"the files give {mine} (codes, differing, app cannot send, "
                                "keys, differing keys)")
        piped = sorted({(b, mo) for rows_ in models.values() for b, mo in rows_
                        if "|" in b or "|" in mo})
        if piped:
            problems.append(f"{len(piped)} brand/model pair(s) of the dump contain a pipe, so "
                            f"'<BRAND> | <MODEL>' cannot be split back (D56b), e.g. {piped[0]!r}")
        problems += structure_problems(ledger_root, models, files)

    return {
        "by_protocol": by_protocol, "problems": problems, "files": len(files),
        "imported_keys": n_imported, "rows": len(rows), "oracle_codes": len(oracle),
        "differs_codes": differs_codes, "differs_keys": differs_keys,
        "imported_codes": imported_codes, "imported_keys_by_protocol": imported_keys,
        "report": report,
    }


def print_report(result: dict, out=None) -> None:
    out = out or sys.stdout
    print(f"{result['files']:,} files, {result['imported_keys']:,} imported keys, "
          f"{result['rows']:,} dump rows, {result['oracle_codes']:,} oracle codes", file=out)
    print(f"{'DB protocol':<12}{'':>2}{'codes':>8}"
          + "".join(f"{c:>20}" for c in CLASSES), file=out)
    totals = Counter()
    code_totals = Counter()
    for proto in sorted(result["by_protocol"]):
        entry = result["by_protocol"][proto]
        keys = entry["keys"]
        codes = {c: len(entry["codes"][c]) for c in CLASSES}
        # a code is counted once, in the class of its keys (they agree, or one is unexplained)
        all_codes = set().union(*entry["codes"].values()) if entry["codes"] else set()
        print(f"{proto:<12}{'codes':>2}{len(all_codes):>8,}"
              + "".join(f"{codes[c]:>20,}" for c in CLASSES), file=out)
        print(f"{'':<12}{'keys':>2}{sum(keys.values()):>8,}"
              + "".join(f"{keys[c]:>20,}" for c in CLASSES), file=out)
        totals.update(keys)
        code_totals.update(codes)
    print(f"{'total':<12}{'codes':>2}{sum(code_totals.values()):>8,}"
          + "".join(f"{code_totals[c]:>20,}" for c in CLASSES), file=out)
    print(f"{'':<12}{'keys':>2}{sum(totals.values()):>8,}"
          + "".join(f"{totals[c]:>20,}" for c in CLASSES), file=out)
    print("\nnotes (matched keys: the documented framing used; differs by reading: how the "
          "app's own reading accounts for its signal):", file=out)
    for proto in sorted(result["by_protocol"]):
        notes = result["by_protocol"][proto]["notes"]
        if notes:
            print(f"  {proto}: " + ", ".join(f"{n} x{c:,}" for n, c in sorted(notes.items())), file=out)
    print("\nwhere the wire reading differs from SwiftRemote's "
          "(distinct imported codes, imported keys):", file=out)
    for proto in sorted(result["differs_codes"]):
        print(f"  {proto}: {len(result['differs_codes'][proto]):,} of "
              f"{len(result['imported_codes'][proto]):,} codes, "
              f"{result['differs_keys'][proto]:,} of {result['imported_keys_by_protocol'][proto]:,} keys",
              file=out)
    print(f"  total: {sum(len(v) for v in result['differs_codes'].values()):,} codes, "
          f"{sum(result['differs_keys'].values()):,} keys", file=out)
    problems = result["problems"]
    print(f"\nproblems: {len(problems)}", file=out)
    for text in problems[:40]:
        print(f"  {text}", file=out)
    if len(problems) > 40:
        print(f"  ... and {len(problems) - 40} more", file=out)


def list_differences(checkout: Path, ledger_root: Path, out=None) -> None:
    """Every distinct code whose two readings differ, from the dump alone."""
    out = out or sys.stdout
    rows, _models, _ids = read_database(checkout)
    for proto, hexcode in sorted({(r[3], r[2]) for r in rows}):
        try:
            wire = tuple(WIRE[proto](hexcode))
        except (ValueError, KeyError):
            continue
        try:
            app = tuple(APP[proto](hexcode))
        except ValueError:
            app = None
        if app != wire:
            print(f"{proto}\t{hexcode}\t{wire}\t{app if app else 'cannot be sent'}", file=out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--checkout", type=Path, default=os.environ.get("SWIFTREMOTE"),
                        help="the SwiftRemote checkout (or $SWIFTREMOTE)")
    parser.add_argument("--oracle", type=Path, default=os.environ.get("IRBLASTER_ORACLE"),
                        help="directory holding by_protocol/ (or $IRBLASTER_ORACLE)")
    parser.add_argument("--ledger", type=Path, default=ROOT, help="repository root holding remotes/irblaster/")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--limit-files", type=int, help="only the first N files (a partial run)")
    parser.add_argument("--json", type=Path, help="write the counts here")
    parser.add_argument("--list-differences", action="store_true")
    args = parser.parse_args(argv)
    if not args.checkout:
        parser.error("--checkout DIR (or $SWIFTREMOTE) is required")
    if args.list_differences:
        list_differences(Path(args.checkout), args.ledger)
        return 0
    if not args.oracle:
        parser.error("--oracle DIR (or $IRBLASTER_ORACLE) is required")
    result = run(args.ledger, Path(args.checkout), Path(args.oracle), args.workers, args.limit_files)
    print_report(result)
    if args.json:
        args.json.write_text(json.dumps({
            "files": result["files"], "importedKeys": result["imported_keys"],
            "byProtocol": {p: {"keys": dict(e["keys"]),
                               "codes": {c: len(s) for c, s in e["codes"].items()},
                               "notes": dict(e["notes"])}
                           for p, e in sorted(result["by_protocol"].items())},
            "problems": result["problems"],
        }, indent=2) + "\n", encoding="utf-8")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
