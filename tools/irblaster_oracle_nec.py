#!/usr/bin/env python3
"""Compare the ledger's NEC-family signals with what SwiftRemote transmits.

The oracle is the app itself: every distinct DB hexcode of the protocols
``NEC``, ``NEC2``, ``NECx1`` and ``NECx2`` was run through the app's
``buildButtonFromDbRow`` and ``previewIRButton``, and the result written to
``<ORACLE>/by_protocol/<DBPROTOCOL>.jsonl``, one object per code::

    {protocol, hex, appProtocol, params, code, freq, mode, pattern}

where ``pattern`` is the app's mark/space durations in microseconds and
``freq`` its carrier in Hz.

For each code this tool maps the hexcode with
:data:`remote_ledger.irblaster.hex_nec.FROM_DB_HEX`, compiles it with the
ledger's own encoder at the carrier named in :data:`CARRIER_HZ`, plays it
``minSends`` times (:data:`~remote_ledger.irblaster.hex_nec.MIN_SENDS`) and
compares the durations with the app's. A code matches when

* the carriers agree within 5 %,
* the number of durations is equal, after the one documented framing
  adjustment below, and
* every duration is within 12 % of the app's, or 150 us, whichever is larger.

Documented framing, the only differences the comparison forgives:

``NEC``
    The app's legacy NEC path (``legacy_nec_default``, ir.dart L390-L398)
    stops at the last mark: 67 durations, no lead-out. The ledger's NEC1
    carries the IRP's ``^108m`` gap, so its final gap is dropped before
    comparing.
``NEC2``, ``NECx1``, ``NECx2``
    The app pads every frame to 0x1A580 = 107,904 us (its comments say
    108,800); the IRP says 108,000. That is not forgiven -- it is inside the
    tolerance and shows up as the "final gap" deviation in the report.
``NECx1``
    The app sends the first frame only. The IRP's short repeat frame
    (``8,-8,~D:1,1,^108m``) is the ledger's *repeat* sequence, which a
    ``minSends`` of 1 does not play, so nothing is dropped; the app's
    ``encodeToggleFrame`` is not used by the app (necx1.dart L43-L61).
``NECx2``
    The app sends the frame twice back to back (necx2.dart L75-L76). The
    ledger holds it once, in the repeat slot, and ``minSends`` is 2.

The app and the wire agree for the NEC family, so ``hex_nec.FROM_DB_HEX_APP`` is
the same table as ``FROM_DB_HEX``; this tool therefore proves the encoders
against what the app transmits and the importer's reading at once.

A code the mapping refuses (``ValueError``) is **unrepresentable**, counted by
its stable reason, and is not a mismatch. A code the mapping accepts whose
signal fails the comparison is a **mismatch**, and the exit status is 1.

Usage::

    python tools/irblaster_oracle_nec.py --oracle DIR            # or $IRBLASTER_ORACLE
    python tools/irblaster_oracle_nec.py --oracle DIR --json out.json
    python tools/irblaster_oracle_nec.py --file tests/fixtures/irblaster/nec.json ...

``--file`` reads a committed fixture (a JSON list of the same objects) rather
than the jsonl.
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

from remote_ledger import pronto  # noqa: E402
from remote_ledger.irblaster.hex_nec import FROM_DB_HEX, MIN_SENDS  # noqa: E402
from remote_ledger.protocols import REGISTRY  # noqa: E402

DB_PROTOCOLS = ("NEC", "NEC2", "NECx1", "NECx2")

#: ``protocol.carrierHz`` the ledger remote should use per DB protocol. The
#: reasons are in DESIGN D61: the app sends NEC at 38000 (legacy path) and
#: the rest at 38222 (NEC2) or 38400; the registry's nominal 38.4k is within
#: 5 % of all of them.
CARRIER_HZ = {"NEC": 38_000, "NEC2": 38_400, "NECx1": 38_400, "NECx2": 38_400}

#: The app's legacy NEC path stops at the last mark.
APP_OMITS_LEAD_OUT = frozenset({"NEC"})

CARRIER_TOLERANCE = 0.05
REL_TOLERANCE = 0.12
ABS_TOLERANCE_US = 150


def played(signal, min_sends: int) -> list[int]:
    """The durations a player sends for ``min_sends`` sends of ``signal``.

    A signal with an intro sends it first and then repeats the repeat
    sequence; one without sends its repeat sequence ``min_sends`` times.
    """
    if signal.intro:
        return [*signal.intro, *(signal.repeat * (min_sends - 1))]
    return list(signal.repeat) * min_sends


def tolerance(app_us: int) -> float:
    return max(ABS_TOLERANCE_US, REL_TOLERANCE * app_us)


def compare(row: dict, db_protocol: str):
    """Returns ``("matched", info)``, ``("unrepresentable", reason)`` or
    ``("mismatched", why)``."""
    try:
        name, device, subdevice, function = FROM_DB_HEX[db_protocol](row["hex"])
    except ValueError as exc:
        return "unrepresentable", str(exc)
    signal = REGISTRY[name].encode(
        device=device, subdevice=subdevice, function=function,
        carrier_hz=CARRIER_HZ[db_protocol],
    )
    pronto.encode(signal)  # the compiled form must exist, not only the signal
    ours = played(signal, MIN_SENDS.get(db_protocol, 1))
    if db_protocol in APP_OMITS_LEAD_OUT:
        ours = ours[:-1]
    app = row["pattern"]
    drift = abs(row["freq"] - signal.carrier_hz) / row["freq"]
    if drift > CARRIER_TOLERANCE:
        return "mismatched", f"carrier {signal.carrier_hz} against the app's {row['freq']}"
    if len(ours) != len(app):
        return "mismatched", f"{len(ours)} durations against the app's {len(app)}"
    worst = 0.0
    for i, (a, o) in enumerate(zip(app, ours)):
        if abs(a - o) > tolerance(a):
            return "mismatched", f"duration {i}: {o} us against the app's {a} us"
        worst = max(worst, abs(a - o) / a)
    return "matched", {"name": name, "drift": drift, "worst": worst, "app": app, "ours": ours}


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def classify_positions(app: list[int], ours: list[int], worst_by_class: dict) -> None:
    """Track the largest relative deviation by what the duration is: the
    lead-in, a bit mark, a zero or one space, the final gap. Frames repeat
    (NECx2), so a position is classed by its place within a frame of 68."""
    frame = 68
    for i, (a, o) in enumerate(zip(app, ours)):
        j = i % frame
        if j == 0 or j == 1:
            kind = "lead-in mark" if j == 0 else "lead-in space"
        elif j == 67:
            kind = "final gap"
        elif j == 66:
            kind = "stop mark"
        elif j % 2 == 0:
            kind = "bit mark"
        else:
            kind = "one space" if a > 1100 else "zero space"
        rel = abs(a - o) / a
        if rel > worst_by_class.get(kind, (0.0, 0, 0))[0]:
            worst_by_class[kind] = (rel, a, o)


def run(sources: dict[str, list[dict]], show: int, out_json: Path | None) -> int:
    report: dict = {}
    failed = False
    for db_protocol in DB_PROTOCOLS:
        rows = sources.get(db_protocol)
        if rows is None:
            continue
        counts = collections.Counter()
        reasons = collections.Counter()
        mismatches: list[tuple[str, str]] = []
        worst_by_class: dict = {}
        by_ledger = collections.Counter()
        for row in rows:
            verdict, info = compare(row, db_protocol)
            counts[verdict] += 1
            if verdict == "unrepresentable":
                reasons[info] += 1
            elif verdict == "mismatched":
                mismatches.append((row["hex"], info))
            else:
                by_ledger[info["name"]] += 1
                classify_positions(info["app"], info["ours"], worst_by_class)
        report[db_protocol] = {
            "codes": len(rows),
            "matched": counts["matched"],
            "mismatched": counts["mismatched"],
            "unrepresentable": counts["unrepresentable"],
            "ledgerProtocols": dict(by_ledger),
            "carrierHz": CARRIER_HZ[db_protocol],
            "minSends": MIN_SENDS.get(db_protocol, 1),
            "unrepresentableReasons": dict(reasons),
            "mismatches": mismatches[:50],
            "worstDeviationByDuration": {
                k: {"relative": round(v[0], 4), "appUs": v[1], "ledgerUs": v[2]}
                for k, v in sorted(worst_by_class.items())
            },
        }
        print(
            f"{db_protocol:6} {len(rows):6} codes: {counts['matched']:6} matched, "
            f"{counts['mismatched']:4} mismatched, "
            f"{counts['unrepresentable']:4} unrepresentable"
            f"  (ledger {dict(by_ledger)}, carrier {CARRIER_HZ[db_protocol]} Hz, "
            f"minSends {MIN_SENDS.get(db_protocol, 1)})"
        )
        for reason, n in reasons.most_common():
            print(f"         {n:5} unrepresentable: {reason}")
        for kind, (rel, a, o) in sorted(worst_by_class.items()):
            print(f"         worst {kind:13} {rel * 100:5.2f} %  (app {a} us, ledger {o} us)")
        for hexcode, why in mismatches[:show]:
            print(f"         MISMATCH {hexcode}: {why}")
        if mismatches:
            failed = True
    if out_json:
        out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--oracle", default=os.environ.get("IRBLASTER_ORACLE"),
                        help="directory holding by_protocol/*.jsonl")
    parser.add_argument("--file", action="append", default=[],
                        help="a fixture or jsonl file; its rows' protocol field names the DB protocol")
    parser.add_argument("--json", type=Path, help="write the full report here")
    parser.add_argument("--show", type=int, default=10, help="mismatches to print per protocol")
    args = parser.parse_args()

    sources: dict[str, list[dict]] = {}
    paths = [Path(f) for f in args.file]
    if args.oracle:
        paths += [Path(args.oracle) / "by_protocol" / f"{p}.jsonl" for p in DB_PROTOCOLS]
    if not paths:
        parser.error("give --oracle DIR (or $IRBLASTER_ORACLE) or --file")
    for path in paths:
        for row in load(path):
            sources.setdefault(row["protocol"], []).append(row)
    return run(sources, args.show, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
