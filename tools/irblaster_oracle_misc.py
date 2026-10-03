#!/usr/bin/env python3
"""Compare the ledger's ``misc``-family signals with what the SwiftRemote app sends.

The oracle is the app itself. Every distinct hexcode of each DB protocol was run
through ``buildButtonFromDbRow`` and ``previewIRButton`` (``lib/utils/``), and
the result is one JSON object per line in ``<ORACLE>/by_protocol/<DB>.jsonl``::

    {protocol, hex, appProtocol, params, code, freq, mode, pattern}

where ``pattern`` is the app's mark/space durations in microseconds and
``freq`` the carrier in Hz. For each code this tool maps the hexcode with
``remote_ledger.irblaster.hex_misc.FROM_DB_HEX``, compiles it with the
registered encoder at the registry's nominal carrier, and requires:

* the carrier within 5 % of the app's;
* the same number of durations;
* every duration within 12 % **of the app's value** or 150 us, whichever is
  larger (integer arithmetic, so a duration exactly 12 % out is within);
* the **lead-out** -- the last duration, the gap after the stop mark -- judged
  separately, because it is the one place a framing choice, not a bit, can
  differ. A lead-out outside the tolerance is a *mismatch* unless the protocol
  appears in ``EXPLAINED_LEADOUT`` below with the reason; it is then counted on
  its own line ("matched, lead-out differs") and its ranges are printed. It is
  never folded into "matched".

Codes the mapping refuses (``ValueError``) are *unrepresentable* and counted by
reason. An encoder error is a mismatch.

Two things are applied to the ledger's side because the app's preview does
them, and are listed rather than hidden:

* ``RECS80`` and ``RECS80_L`` carry a toggle bit the hexcode does not. The app
  flips it per press from a static starting at ``False``, so a preview shows
  ``T=1``; the ledger's own default is ``T=0`` (D3b). This tool encodes with
  ``toggle=1`` to compare like with like. ``tests/test_irblaster_misc.py``
  shows the ``T=0`` signal differs from the app's in that one duration only.
* ``Samsung36`` is run a second time with ``unitUs`` 500, which the remote
  file's ``protocol`` block can set (D24). See NOTES/misc.md.

Usage::

    python tools/irblaster_oracle_misc.py --oracle DIR
    python tools/irblaster_oracle_misc.py --oracle DIR --write-fixtures tests/fixtures/irblaster

``--oracle`` (or ``$IRBLASTER_ORACLE``) is the directory holding ``by_protocol/``.
Exit status is 0 when nothing is mismatched and every disagreement is
explained, 1 otherwise.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.errors import EncodeError  # noqa: E402
from remote_ledger.irblaster.hex_misc import FROM_DB_HEX  # noqa: E402
from remote_ledger.protocols import REGISTRY  # noqa: E402

CARRIER_TOLERANCE_PCT = 5
DURATION_TOLERANCE_PCT = 12
DURATION_TOLERANCE_FLOOR_US = 150

#: What the app's preview sets that a hexcode does not carry.
EXTRA_ENCODE_ARGS: dict[str, dict] = {
    "RECS80": {"toggle": 1},
    "RECS80_L": {"toggle": 1},
}

#: Protocols whose *lead-out* may differ from the app's, and why. Nothing else
#: about a signal is excused.
EXPLAINED_LEADOUT: dict[str, str] = {
    "Samsung36": (
        "the IRP pads the frame to a 108 ms extent (^108m); the app ends it "
        "with a fixed 59 ms gap. A real Samsung36 remote's frame period is "
        "~122 ms (tools/misc_capture_audit.py), which the app is nearer to"
    ),
}

#: (DB protocol, label, unitUs override or None)
RUNS: tuple[tuple[str, str, int | None], ...] = (
    ("Samsung36", "Samsung36", None),
    ("Samsung36", "Samsung36 @unitUs=500", 500),
    ("Proton", "Proton", None),
    ("F12_relaxed", "F12_relaxed", None),
    ("RECS80", "RECS80", None),
    ("RECS80_L", "RECS80_L", None),
)


def within(ours: int, app: int) -> bool:
    """12 % of the app's duration, or 150 us, whichever is larger."""
    return abs(ours - app) * 100 <= max(DURATION_TOLERANCE_PCT * app,
                                        DURATION_TOLERANCE_FLOOR_US * 100)


def compile_ledger(db_protocol: str, hexcode: str, unit_us: int | None):
    """``(ledger name, durations, carrier)`` or raises ValueError (unrepresentable)
    or EncodeError."""
    name, device, subdevice, function = FROM_DB_HEX[db_protocol](hexcode)
    entry = REGISTRY[name]
    signal = entry.encode(
        device=device, subdevice=subdevice, function=function,
        carrier_hz=entry.nominal_carrier_hz, unit_us=unit_us,
        **EXTRA_ENCODE_ARGS.get(db_protocol, {}),
    )
    return name, list(signal.intro) + list(signal.repeat), signal.carrier_hz


def compare(db_protocol: str, row: dict, unit_us: int | None = None) -> tuple[str, dict]:
    """Classify one oracle row.

    Returns ``(verdict, detail)`` where verdict is one of ``matched``,
    ``matched-leadout`` (every duration but the lead-out matches and the
    protocol's lead-out difference is explained), ``mismatch`` or
    ``unrepresentable``.
    """
    try:
        name, ours, carrier = compile_ledger(db_protocol, row["hex"], unit_us)
    except ValueError as exc:
        return "unrepresentable", {"reason": str(exc)}
    except EncodeError as exc:
        return "mismatch", {"why": f"encoder refused: {exc}"}

    app = row["pattern"]
    detail: dict = {"ledger": name}
    if abs(carrier - row["freq"]) * 100 > CARRIER_TOLERANCE_PCT * row["freq"]:
        return "mismatch", {**detail, "why": f"carrier {carrier} vs app {row['freq']}"}
    if len(ours) != len(app):
        return "mismatch", {**detail,
                            "why": f"{len(ours)} durations vs the app's {len(app)}"}
    worst = 0.0
    for i, (a, b) in enumerate(zip(ours[:-1], app[:-1])):
        worst = max(worst, abs(a - b) / b)
        if not within(a, b):
            return "mismatch", {**detail,
                                "why": f"duration {i}: ledger {a} us vs app {b} us"}
    detail["worst"] = worst
    detail["leadout"] = (ours[-1], app[-1])
    if within(ours[-1], app[-1]):
        return "matched", detail
    if db_protocol in EXPLAINED_LEADOUT:
        return "matched-leadout", detail
    return "mismatch", {**detail,
                        "why": f"lead-out: ledger {ours[-1]} us vs app {app[-1]} us"}


def load_rows(oracle: Path, db_protocol: str) -> list[dict]:
    path = oracle / "by_protocol" / f"{db_protocol}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def report(oracle: Path) -> int:
    failed = False
    print(f"carrier within {CARRIER_TOLERANCE_PCT}%; durations within "
          f"{DURATION_TOLERANCE_PCT}% of the app's or {DURATION_TOLERANCE_FLOOR_US} us; "
          "lead-out judged separately\n")
    for db_protocol, label, unit_us in RUNS:
        rows = load_rows(oracle, db_protocol)
        verdicts: collections.Counter[str] = collections.Counter()
        reasons: collections.Counter[str] = collections.Counter()
        mismatches: list[tuple[str, str]] = []
        worst = 0.0
        leadouts: list[tuple[int, int]] = []
        for row in rows:
            verdict, detail = compare(db_protocol, row, unit_us)
            verdicts[verdict] += 1
            if verdict == "unrepresentable":
                reasons[detail["reason"]] += 1
            elif verdict == "mismatch":
                mismatches.append((row["hex"], detail["why"]))
            else:
                worst = max(worst, detail["worst"])
                if verdict == "matched-leadout":
                    leadouts.append(detail["leadout"])
        print(f"{label}: {len(rows)} codes")
        print(f"  matched {verdicts['matched']}, "
              f"matched with lead-out differing {verdicts['matched-leadout']}, "
              f"mismatched {verdicts['mismatch']}, "
              f"unrepresentable {verdicts['unrepresentable']}")
        if verdicts["matched"] or verdicts["matched-leadout"]:
            print(f"  largest non-lead-out duration deviation from the app: "
                  f"{worst * 100:.1f}% (limit {DURATION_TOLERANCE_PCT}%)")
        if leadouts:
            ours = sorted({a for a, _ in leadouts})
            theirs = sorted({b for _, b in leadouts})
            print(f"  lead-out, ledger {ours[0]}-{ours[-1]} us vs app "
                  f"{theirs[0]}-{theirs[-1]} us")
            print(f"  explained: {EXPLAINED_LEADOUT[db_protocol]}")
        for reason, n in reasons.most_common():
            print(f"  unrepresentable x{n}: {reason}")
        for hexcode, why in mismatches[:10]:
            print(f"  MISMATCH {hexcode}: {why}")
        if len(mismatches) > 10:
            print(f"  ... and {len(mismatches) - 10} more")
        if verdicts["mismatch"]:
            failed = True
        print()
    # Samsung36 at the registry's own unit is expected to differ in the
    # lead-out; any *other* disagreement above already set `failed`.
    return 1 if failed else 0


def pick(rows: list[dict], count: int, keep) -> list[dict]:
    """A deterministic sample: the first and last by hexcode, one of each
    ``keep(row)`` class, and an even stride through the rest up to ``count``."""
    ordered = sorted(rows, key=lambda r: r["hex"])
    chosen: dict[str, dict] = {ordered[0]["hex"]: ordered[0],
                               ordered[-1]["hex"]: ordered[-1]}
    seen: set = set()
    for row in ordered:
        k = keep(row)
        if k not in seen:
            seen.add(k)
            chosen[row["hex"]] = row
    stride = max(1, len(ordered) // max(1, count - len(chosen)))
    for row in ordered[::stride]:
        if len(chosen) >= count:
            break
        chosen.setdefault(row["hex"], row)
    return sorted(chosen.values(), key=lambda r: r["hex"])


#: Fixture file name, and the class a sample must cover at least once.
FIXTURES = {
    "Samsung36": ("samsung36", lambda r: r["hex"][4]),                  # each E nibble
    "Proton": ("proton", lambda r: r["hex"][:2]),                       # each high byte
    "F12_relaxed": ("f12_relaxed", lambda r: (int(r["hex"], 16) >> 8, r["pattern"][-3])),
    "RECS80": ("recs80", lambda r: r["hex"][2]),
    "RECS80_L": ("recs80_l", lambda r: r["hex"][2]),
}


def write_fixtures(oracle: Path, out: Path, count: int = 40) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for db_protocol, (stem, keep) in FIXTURES.items():
        rows = load_rows(oracle, db_protocol)
        sample = pick(rows, count, keep)
        lines = ",\n".join(
            "  " + json.dumps({"hex": r["hex"], "freq": r["freq"], "pattern": r["pattern"]},
                              separators=(", ", ": "))
            for r in sample
        )
        note = ("a deterministic sample of the app's own signals for this DB "
                "protocol, from the Dart oracle (buildButtonFromDbRow + "
                "previewIRButton); regenerate with tools/irblaster_oracle_misc.py "
                "--write-fixtures")
        text = (f'{{\n "dbProtocol": {json.dumps(db_protocol)},\n '
                f'"note": {json.dumps(note)},\n "codes": [\n{lines}\n ]\n}}\n')
        json.loads(text)                     # it must stay valid JSON
        (out / f"{stem}.json").write_text(text)
        print(f"{db_protocol}: {len(sample)} of {len(rows)} codes -> {out / (stem + '.json')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oracle", default=os.environ.get("IRBLASTER_ORACLE"),
                    help="the directory holding by_protocol/ (or $IRBLASTER_ORACLE)")
    ap.add_argument("--write-fixtures", metavar="DIR",
                    help="write a small sample per protocol instead of reporting")
    args = ap.parse_args()
    if not args.oracle:
        ap.error("--oracle or $IRBLASTER_ORACLE is required")
    oracle = Path(args.oracle)
    if args.write_fixtures:
        write_fixtures(oracle, Path(args.write_fixtures))
        return 0
    return report(oracle)


if __name__ == "__main__":
    raise SystemExit(main())
