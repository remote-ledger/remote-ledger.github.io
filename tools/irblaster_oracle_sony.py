#!/usr/bin/env python3
"""Compare the ledger's Sony encoders with what the SwiftRemote app transmits.

The oracle is the app's own code: for every distinct hexcode of the DB
protocols ``SONY12``, ``SONY15`` and ``SONY20``, ``buildButtonFromDbRow`` then
``previewIRButton`` were run and the result recorded as one JSON object per
code, ``{protocol, hex, appProtocol, params, code, freq, mode, pattern}``,
``pattern`` being the mark/space durations in microseconds and ``freq`` the
carrier in Hz. This tool reads those, maps each hexcode to ledger parameters
with :mod:`remote_ledger.irblaster.hex_sony`, encodes it with the registered
protocol, plays the result as ``minSends`` sends, and compares.

**It makes two comparisons per code, because the app and the database
disagree about what a Sony hexcode means** (``hex_sony``'s docstring
explains and gives the evidence):

* the **app's reading** (``FROM_DB_HEX_APP``). This is the check that the
  ledger's encoder and the app's agree on the *waveform* for the same bits,
  and that this tool models the app exactly. It must match every code the
  reading can represent; a code it cannot represent is classified, and for
  the one class the app handles by masking, the signal the app *did* send is
  checked against the masked code.
* the **database's reading** (``FROM_DB_HEX``), which is what the importer
  uses. It differs from the app for nearly every code, because the app reads
  the hex in the other bit order. Each difference is verified to be exactly
  that: the app's pattern is the ledger's encoding of the app's reading.

The tolerance is the brief's and is not widened: carrier within 5%, the same
number of durations, each within 12% or 150 us, whichever is larger. The
report also says how many durations were *exactly* equal, so nothing rests on
the tolerance.

Usage::

    python tools/irblaster_oracle_sony.py --oracle DIR        # DIR/by_protocol/SONY*.jsonl
    python tools/irblaster_oracle_sony.py FILE...             # .jsonl rows, or .json {"rows": [...]}
    python tools/irblaster_oracle_sony.py --oracle DIR --strict
    python tools/irblaster_oracle_sony.py --db assets/db/swiftremote.sqlite   # which spelling the DB uses

Exit status is 1 on an unexplained mismatch: a code the app's reading cannot
reproduce, or a difference between the readings that is not the bit order. A
code the importer will refuse is listed, not an error. ``--strict`` also exits
1 when the database's reading differs from the app for any code.
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger import protocols  # noqa: E402
from remote_ledger.irblaster import hex_sony  # noqa: E402

DB_PROTOCOLS = ("SONY12", "SONY15", "SONY20")

#: The brief's tolerances.
CARRIER_TOLERANCE = 0.05
RELATIVE_TOLERANCE = 0.12
ABSOLUTE_TOLERANCE_US = 150

#: Where a frame's address field sits in the app's params, per DB protocol,
#: and how many bits it has.
ADDRESS_BITS = {"SONY12": 5, "SONY15": 8, "SONY20": 13}


def play(protocol: str, device: int, subdevice: int | None, function: int,
         min_sends: int, carrier_hz: int = 40_000) -> tuple[int, list[int]]:
    """The ledger's signal as played: the intro once, the repeat after it.

    A signal with no intro (every Sony frame) is its repeat sent
    ``min_sends`` times, which is what the app sends.
    """
    signal = protocols.REGISTRY[protocol].encode(
        device=device, subdevice=subdevice, function=function, carrier_hz=carrier_hz)
    if signal.intro:
        durations = list(signal.intro) + list(signal.repeat) * (min_sends - 1)
    else:
        durations = list(signal.repeat) * min_sends
    return signal.carrier_hz, durations


def compare(ours: tuple[int, list[int]], freq: int, pattern: list[int]) -> tuple[str | None, int]:
    """(problem or None, count of exactly equal durations)."""
    carrier, durations = ours
    if abs(carrier - freq) > CARRIER_TOLERANCE * freq:
        return f"carrier {carrier} Hz against the app's {freq} Hz", 0
    if len(durations) != len(pattern):
        return f"{len(durations)} durations against the app's {len(pattern)}", 0
    exact = 0
    for i, (a, b) in enumerate(zip(durations, pattern)):
        if a == b:
            exact += 1
        elif abs(a - b) > max(RELATIVE_TOLERANCE * b, ABSOLUTE_TOLERANCE_US):
            return f"duration {i} is {a} us against the app's {b} us", exact
    return None, exact


def frames_identical(pattern: list[int], sends: int) -> bool:
    """The app's pattern is one frame sent ``sends`` times, byte for byte."""
    if len(pattern) % sends:
        return False
    n = len(pattern) // sends
    return all(pattern[i * n:(i + 1) * n] == pattern[:n] for i in range(sends))


def app_params(db_name: str, fields: tuple[str, int, int | None, int]) -> dict[str, str]:
    """What the app's reading derives, as the app spells its fields."""
    _name, device, subdevice, function = fields
    address = device if subdevice is None else device | (subdevice << 5)
    digits = (ADDRESS_BITS[db_name] + 3) // 4
    return {"address": f"{address:0{digits}X}", "command": f"{function:02X}"}


def masked_fields(db_name: str, hexcode: str) -> tuple[str, int, int | None, int]:
    """What the app transmits for a hex wider than its frame: the integer
    masked to the frame's width, the high bits dropped without a word."""
    bits = hex_sony.SHAPES[db_name][1]
    return hex_sony.fields_from_packed(db_name, int(hexcode, 16) & ((1 << bits) - 1))


# --- which spelling of a hexcode does the database use? ----------------------

ROOT = Path(__file__).resolve().parent.parent
DIGITS = {"Sony12": 3, "Sony15": 4, "Sony20": 5}


def tx_hex(protocol: str, device: int, subdevice: int | None, function: int) -> str:
    """The frame in transmission order, first bit most significant, padded on
    the right to whole digits: read off the ledger's own encoding."""
    frame = protocols.REGISTRY[protocol].encode(
        device=device, subdevice=subdevice, function=function, carrier_hz=40_000).repeat
    bits = [1 if mark > 900 else 0 for mark in frame[2::2]]
    digits = DIGITS[protocol]
    bits += [0] * (digits * 4 - len(bits))
    return f"{int(''.join(map(str, bits)), 2):0{digits}X}"


def app_hex(protocol: str, device: int, subdevice: int | None, function: int) -> str:
    """The same frame as the app's packed integer, ``function | address << 7``."""
    address = device if subdevice is None else device | (subdevice << 5)
    return f"{function | (address << 7):0{DIGITS[protocol]}X}"


def anchor_sets() -> list[tuple[str, str, str, list[tuple[int, int | None, int]]]]:
    """Frames with parameters from a source that is not the database:
    (label, DB protocol, ledger protocol, [(device, subdevice, function)])."""
    structural = json.loads((ROOT / "tests" / "vectors" / "sirc-structural.json").read_text())
    remote = json.loads((ROOT / "remotes" / "sony" / "RMT-B118P.json").read_text())
    b118p = []
    for key in remote["keys"].values():
        form = key["forms"][0]
        b118p.append(tuple(int(form[k], 16) for k in ("device", "subdevice", "function")))
    girr = structural["girrSony12"]
    hw = structural["girrHw50es"]
    return [
        ("ledger remotes/sony/RMT-B118P.json, hardware-verified, D=26 S=226", "SONY20", "Sony20", b118p),
        ("Girr commandset_sony.girr, D=1", "SONY12", "Sony12",
         [(girr["device"], None, c["function"]) for c in girr["commands"]]),
        ("VPL-VW95ES protocol manual via Girr sony_vlp_hw50es.girr, Sony15 D=84", "SONY15", "Sony15",
         [(hw["sony15"]["device"], None, f) for f in hw["sony15"]["functions"]]),
        ("VPL-VW95ES protocol manual via Girr sony_vlp_hw50es.girr, Sony20 D=26 S=42", "SONY20", "Sony20",
         [(hw["sony20"]["device"], hw["sony20"]["subdevice"], f) for f in hw["sony20"]["functions"]]),
    ]


def anchor_evidence(db_path: Path) -> list[str]:
    """For frames whose parameters come from elsewhere, how many are in the
    database as the transmission-order hex, and how many as the app's packing."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        lines = []
        for label, db_name, protocol, frames in anchor_sets():
            present = {row[0] for row in con.execute(
                "select distinct hexcode from keys where protocol = ?", (db_name,))}
            tx = sum(tx_hex(protocol, *f) in present for f in frames)
            app = sum(app_hex(protocol, *f) in present for f in frames)
            lines.append(f"{db_name}: {len(frames)} frames from {label}: "
                         f"in the DB as transmission-order hex {tx}, as the app's packing {app}")
        return lines
    finally:
        con.close()


class Tally:
    def __init__(self) -> None:
        self.codes = 0
        self.exact_durations = 0
        self.total_durations = 0
        # the app's reading
        self.app_matched = 0
        self.app_mismatched: list[str] = []
        self.app_unrepresentable: collections.Counter[str] = collections.Counter()
        self.app_masked_verified = 0
        # the database's reading
        self.db_same = 0
        self.db_differs_explained = 0
        self.db_unexplained: list[str] = []
        self.db_unrepresentable: collections.Counter[str] = collections.Counter()
        self.model_errors: list[str] = []


def _reproduces_app(db_name: str, fields, row: dict, sends: int) -> tuple[str | None, int]:
    """Does the ledger's encoding of ``fields`` equal what the app sent for
    this row, and does the app's own field text agree? (problem, exact)"""
    problem, exact = compare(play(*fields, sends), row["freq"], row["pattern"])
    if problem is None and app_params(db_name, fields) != row["params"]:
        problem = f"fields {app_params(db_name, fields)} against the app's {row['params']}"
    return problem, exact


def check_row(row: dict, tally: Tally) -> None:
    db_name = row["protocol"]
    hexcode = row["hex"]
    sends = hex_sony.MIN_SENDS[db_name]
    tally.codes += 1
    tally.total_durations += len(row["pattern"])

    if not frames_identical(row["pattern"], sends):
        tally.model_errors.append(f"{hexcode}: the app's pattern is not {sends} identical frames")

    # --- the app's reading: does the ledger send what the app sends? -------
    app_fields = None
    try:
        fields = hex_sony.FROM_DB_HEX_APP[db_name](hexcode)
    except ValueError as exc:
        tally.app_unrepresentable[str(exc)] += 1
        if int(hexcode, 16) >> hex_sony.SHAPES[db_name][1]:
            # The app masks the hex to its frame width and sends that code.
            # Refused by the reading, but the signal it did send must still be
            # the ledger's encoding of the masked code, or the model is wrong.
            masked = masked_fields(db_name, hexcode)
            problem, _exact = _reproduces_app(db_name, masked, row, sends)
            if problem is None:
                tally.app_masked_verified += 1
                app_fields = masked
            else:
                tally.model_errors.append(f"{hexcode}: the app's masked code is not what it sent ({problem})")
    else:
        problem, exact = _reproduces_app(db_name, fields, row, sends)
        if problem is None:
            tally.app_matched += 1
            tally.exact_durations += exact
            app_fields = fields
        else:
            tally.app_mismatched.append(f"{hexcode}: {problem}")

    # --- the database's reading: what the importer will use ----------------
    try:
        ours = hex_sony.FROM_DB_HEX[db_name](hexcode)
    except ValueError as exc:
        tally.db_unrepresentable[str(exc)] += 1
        return
    problem, _exact = compare(play(*ours, sends), row["freq"], row["pattern"])
    if problem is None:
        tally.db_same += 1
    elif app_fields is not None:
        # Different from the app, and the app's own signal is exactly the
        # ledger's encoding of the app's reading: the difference is the hex's
        # bit order, and nothing else.
        tally.db_differs_explained += 1
    else:
        tally.db_unexplained.append(f"{hexcode}: {problem}")


def load_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            rows += json.loads(text)["rows"]
        else:
            rows += [json.loads(line) for line in text.splitlines() if line.strip()]
    return rows


def run(rows: list[dict]) -> dict[str, Tally]:
    tallies = {name: Tally() for name in DB_PROTOCOLS}
    for row in rows:
        check_row(row, tallies[row["protocol"]])
    return tallies


def report(tallies: dict[str, Tally]) -> tuple[str, bool, bool]:
    """(text, unexplained, disagrees)."""
    lines: list[str] = []
    unexplained = disagrees = False
    for name in DB_PROTOCOLS:
        t = tallies[name]
        if not t.codes:
            continue
        lines.append(f"{name}: {t.codes} distinct codes, {t.total_durations // t.codes} durations each "
                     f"({hex_sony.MIN_SENDS[name]} frames; {protocols.REGISTRY[hex_sony.SHAPES[name][0]].name})")
        lines.append(
            f"  app's reading (FROM_DB_HEX_APP): matched {t.app_matched}, "
            f"mismatched {len(t.app_mismatched)}, unrepresentable {sum(t.app_unrepresentable.values())}"
            + (f" (of which the app masks and sends another code: {t.app_masked_verified} verified)"
               if t.app_masked_verified else ""))
        for reason, count in t.app_unrepresentable.items():
            lines.append(f"      unrepresentable {count}: {reason}")
        lines.append(
            f"  database's reading (FROM_DB_HEX): same signal as the app {t.db_same}, "
            f"different {t.db_differs_explained + len(t.db_unexplained)} "
            f"(explained as the hex's bit order: {t.db_differs_explained}, unexplained: {len(t.db_unexplained)}), "
            f"unrepresentable {sum(t.db_unrepresentable.values())}")
        for reason, count in t.db_unrepresentable.items():
            lines.append(f"      unrepresentable {count}: {reason}")
        if t.total_durations and t.app_matched:
            lines.append(f"  durations exactly equal to the app's under its reading: "
                         f"{t.exact_durations} of {t.app_matched * (t.total_durations // t.codes)}")
        for problem in t.app_mismatched[:5] + t.db_unexplained[:5] + t.model_errors[:5]:
            lines.append(f"  UNEXPLAINED {problem}")
        if t.app_mismatched or t.db_unexplained or t.model_errors:
            unexplained = True
        if t.db_differs_explained or t.db_unexplained or t.db_unrepresentable:
            disagrees = True
    return "\n".join(lines), unexplained, disagrees


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", type=Path, help="oracle .jsonl files, or fixture .json files")
    parser.add_argument("--oracle", type=Path, help="the oracle directory (holds by_protocol/SONY*.jsonl)")
    parser.add_argument("--strict", action="store_true",
                        help="also exit 1 when the database's reading differs from the app for any code")
    parser.add_argument("--json", type=Path, help="write the counts to this file")
    parser.add_argument("--db", type=Path,
                        help="the SwiftRemote sqlite: count how many frames with parameters from "
                             "other sources are in it spelled each way, and exit")
    args = parser.parse_args()

    if args.db:
        print("\n".join(anchor_evidence(args.db)))
        return 0

    paths = list(args.paths)
    if args.oracle:
        base = args.oracle / "by_protocol" if (args.oracle / "by_protocol").is_dir() else args.oracle
        paths += [base / f"{name}.jsonl" for name in DB_PROTOCOLS]
    if not paths:
        parser.error("name --oracle DIR or some files")

    tallies = run(load_rows(paths))
    text, unexplained, disagrees = report(tallies)
    print(text)
    if args.json:
        args.json.write_text(json.dumps({
            name: {
                "codes": t.codes,
                "app_matched": t.app_matched,
                "app_mismatched": t.app_mismatched,
                "app_unrepresentable": dict(t.app_unrepresentable),
                "db_same": t.db_same,
                "db_differs_explained": t.db_differs_explained,
                "db_unexplained": t.db_unexplained,
                "db_unrepresentable": dict(t.db_unrepresentable),
            } for name, t in tallies.items()}, indent=1) + "\n")
    if unexplained:
        print("\nUNEXPLAINED MISMATCHES: the tool's model of the app, or an encoder, is wrong.")
        return 1
    if disagrees:
        print("\nThe database's reading of these codes differs from what the app transmits today; "
              "see the hex_sony docstring.")
        return 1 if args.strict else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
