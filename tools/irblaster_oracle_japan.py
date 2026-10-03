#!/usr/bin/env python3
"""Compare the ledger's Pioneer, JVC, Sharp and Denon signals with what SwiftRemote transmits.

The oracle is the app's own code. Every distinct database code was run through
``buildButtonFromDbRow`` and then ``previewIRButton``, and the result written
one object per code to ``<oracle>/by_protocol/<DB protocol>.jsonl``::

    {protocol, hex, appProtocol, params, code, freq, mode, pattern}

where ``pattern`` is the app's mark/space durations in microseconds and
``freq`` its carrier in hertz. This tool maps each code with
``remote_ledger.irblaster.hex_japan``, encodes it with the registry protocol,
and compares the two signals.

Usage::

    python tools/irblaster_oracle_japan.py --oracle DIR [--reading app|wire]
                                           [--strict-gaps] [--json FILE]

``DIR`` (or ``$IRBLASTER_ORACLE``) holds ``by_protocol/``. ``--reading app``
(the default) is ``FROM_DB_HEX_APP``, the reading SwiftRemote applies to a
code, and **this is the run that proves the ledger's encoders**: they compile
the signal the app transmits, so it must pass (exit 0). ``--reading wire`` is
``FROM_DB_HEX``, the reading the importer uses, which follows the evidence in
DESIGN D64; there a disagreement with the app is the point rather than
a failure, so the exit status stays 0 and the tool counts the codes on which
the two readings differ.

**The comparison**, per code:

* the carrier: the registry protocol's nominal carrier within 5 % of the app's
  (the carrier the ledger remote should declare is the app's, which is what the
  signal is encoded with);
* the signal: the ledger's ``intro`` then ``repeat``, against the app's pattern.
  The app may stop early, on the end of a frame -- JVC and Pioneer-2Part: the
  app sends the intro and omits the repeat. That is the one *framing* difference
  allowed in the length, and it is counted (``app omits the repeat``). A pattern
  that is longer, or ends mid-frame, is a mismatch;
* every duration within 12 % of the larger, or 150 us, whichever is more --
  the brief's tolerance, relative to the larger of the two so it is symmetric;
* **idle gaps** (a space of 8 ms or more) are compared on their own. The IRPs
  pad each frame to a fixed *extent*, so the gap depends on the data; the app
  uses one fixed gap per protocol. A code whose marks and spaces all agree but
  whose gaps fall outside the tolerance is ``matched, gaps differ`` -- counted
  separately with the range of the ratio, never folded into ``matched``.
  ``--strict-gaps`` makes it a failure.

Exit status is 1 if any code is a mismatch (or, with ``--strict-gaps``, differs
in a gap), and 0 otherwise.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from remote_ledger.irblaster import hex_japan  # noqa: E402
from remote_ledger.protocols import REGISTRY  # noqa: E402

DB_PROTOCOLS = ("Pioneer", "JVC", "Sharp", "Denon")
#: The carrier the ledger remote should declare, per ledger protocol: the one
#: the app transmits (see DESIGN D64). JVC differs from the registry's
#: 37.9k by 0.26 %, and both give the same Pronto frequency word.
CARRIER_HZ = {"Pioneer-2Part": 40_000, "JVC": 38_000, "Sharp": 38_000, "Denon": 38_000}
GAP_US = 8_000
CARRIER_TOLERANCE = 0.05

MATCHED = "matched"
MATCHED_GAPS = "matched, gaps differ"
MISMATCHED = "mismatched"
UNREPRESENTABLE = "unrepresentable"
READING_DIFFERS = "codes whose reading differs from the app's"


def within(a: float, b: float) -> bool:
    """12 % of the larger, or 150 us, whichever is more."""
    return abs(a - b) <= max(150.0, 0.12 * max(a, b))


def compare(app: list[int], signal) -> dict:
    """Compare one app pattern with one ledger signal.

    Returns ``{"kind": MATCHED | MATCHED_GAPS | MISMATCHED, ...}`` with the
    detail needed to report it: ``why`` for a mismatch, ``omits_repeat`` and the
    worst deviations otherwise.
    """
    ours = list(signal.intro) + list(signal.repeat)
    n = len(app)
    if n > len(ours):
        return {"kind": MISMATCHED, "why": "the app sends more durations than the ledger signal has"}
    if ours[n - 1] < GAP_US:
        return {"kind": MISMATCHED, "why": "the app's pattern ends inside a ledger frame"}

    worst = 0.0                 # largest relative difference of a non-gap duration
    gap_ratios: list[float] = []
    gap_differs = False
    for i in range(n):
        a, b = app[i], ours[i]
        a_gap, b_gap = i % 2 == 1 and a >= GAP_US, i % 2 == 1 and b >= GAP_US
        if a_gap != b_gap:
            return {"kind": MISMATCHED, "why": f"a gap in only one signal at duration {i}"}
        if a_gap:
            gap_ratios.append(a / b)
            if not within(a, b):
                gap_differs = True
        else:
            if not within(a, b):
                return {"kind": MISMATCHED, "why": f"duration {i} is {a} against {b}"}
            worst = max(worst, abs(a - b) / max(a, b))
    return {
        "kind": MATCHED_GAPS if gap_differs else MATCHED,
        "omits_repeat": n < len(ours),
        "worst_relative": worst,
        "gap_ratios": gap_ratios,
    }


def run(oracle: Path, reading: str, strict_gaps: bool) -> tuple[dict, int]:
    table = hex_japan.FROM_DB_HEX_APP if reading == "app" else hex_japan.FROM_DB_HEX
    app_table = hex_japan.FROM_DB_HEX_APP
    report: dict = {}
    failures = 0
    for db_name in DB_PROTOCOLS:
        path = oracle / "by_protocol" / f"{db_name}.jsonl"
        counts: collections.Counter = collections.Counter()
        reasons: collections.Counter = collections.Counter()
        mismatches: list[dict] = []
        worst = 0.0
        gap_lo, gap_hi = float("inf"), 0.0
        omits = 0
        reading_differs = 0
        carriers: collections.Counter = collections.Counter()
        total = 0
        for line in path.read_text().splitlines():
            row = json.loads(line)
            total += 1
            try:
                name, device, subdevice, function = table[db_name](row["hex"])
            except ValueError as exc:
                counts[UNREPRESENTABLE] += 1
                reasons[str(exc)] += 1
                continue
            if reading == "wire":
                try:
                    if table[db_name](row["hex"]) != app_table[db_name](row["hex"]):
                        reading_differs += 1
                except ValueError:
                    reading_differs += 1
            proto = REGISTRY[name]
            nominal_ok = abs(proto.nominal_carrier_hz - row["freq"]) <= CARRIER_TOLERANCE * row["freq"]
            carriers[(proto.nominal_carrier_hz, row["freq"], nominal_ok)] += 1
            signal = proto.encode(
                device=device, subdevice=subdevice, function=function,
                carrier_hz=CARRIER_HZ[name],
            )
            result = compare(row["pattern"], signal)
            kind = result["kind"]
            if not nominal_ok:
                kind = MISMATCHED
                result = {"kind": kind, "why": "carrier more than 5 % from the app's"}
            counts[kind] += 1
            if kind == MISMATCHED:
                if len(mismatches) < 10:
                    mismatches.append({"hex": row["hex"], "why": result["why"]})
                continue
            worst = max(worst, result["worst_relative"])
            omits += result["omits_repeat"]
            if result["gap_ratios"]:
                gap_lo = min(gap_lo, *result["gap_ratios"])
                gap_hi = max(gap_hi, *result["gap_ratios"])
        report[db_name] = {
            "codes": total,
            MATCHED: counts[MATCHED],
            MATCHED_GAPS: counts[MATCHED_GAPS],
            MISMATCHED: counts[MISMATCHED],
            UNREPRESENTABLE: counts[UNREPRESENTABLE],
            "unrepresentable reasons": dict(reasons),
            "app omits the repeat": omits,
            "worst non-gap deviation": round(worst, 4),
            "app gap / ledger gap": (
                [round(gap_lo, 3), round(gap_hi, 3)] if gap_hi else None
            ),
            "carriers (registry nominal, app, within 5%)": [
                [list(k), v] for k, v in sorted(carriers.items())
            ],
            "first mismatches": mismatches,
        }
        if reading == "wire":
            report[db_name][READING_DIFFERS] = reading_differs
        failures += counts[MISMATCHED]
        if strict_gaps:
            failures += counts[MATCHED_GAPS]
    return report, (0 if reading == "wire" else (1 if failures else 0))


def print_report(report: dict, reading: str) -> None:
    print(f"reading: {reading}")
    header = f"{'DB protocol':<10} {'codes':>6} {'matched':>8} {'gaps differ':>12} {'mismatched':>11} {'unrepresentable':>16}"
    print(header)
    for name, r in report.items():
        print(f"{name:<10} {r['codes']:>6} {r[MATCHED]:>8} {r[MATCHED_GAPS]:>12} "
              f"{r[MISMATCHED]:>11} {r[UNREPRESENTABLE]:>16}")
    print()
    for name, r in report.items():
        print(f"{name}:")
        print(f"  worst non-gap deviation (relative to the larger): {r['worst non-gap deviation']:.1%}")
        print(f"  app gap / ledger gap, over every gap compared: {r['app gap / ledger gap']}")
        print(f"  codes where the app omits the repeat sequence: {r['app omits the repeat']}")
        if reading == "wire":
            print(f"  {READING_DIFFERS}: {r[READING_DIFFERS]}")
        for reason, count in r["unrepresentable reasons"].items():
            print(f"  unrepresentable x{count}: {reason}")
        for m in r["first mismatches"]:
            print(f"  MISMATCH {m['hex']}: {m['why']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--oracle", default=os.environ.get("IRBLASTER_ORACLE"),
                        help="directory holding by_protocol/ (or $IRBLASTER_ORACLE)")
    parser.add_argument("--reading", choices=("app", "wire"), default="app")
    parser.add_argument("--strict-gaps", action="store_true",
                        help="count a code whose only difference is its gaps as a failure")
    parser.add_argument("--json", help="write the full report here")
    args = parser.parse_args(argv)
    if not args.oracle:
        parser.error("--oracle DIR (or $IRBLASTER_ORACLE) is required")
    report, status = run(Path(args.oracle), args.reading, args.strict_gaps)
    print_report(report, args.reading)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
