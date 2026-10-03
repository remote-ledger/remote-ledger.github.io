#!/usr/bin/env python3
"""Compare the ledger with what SwiftRemote transmits, for the Philips family.

The oracle is the app's own code. Every distinct database code of the four
protocols here (``RC5``, ``RC6``, ``RCA_38``, ``Thomson7``) was run through
``buildButtonFromDbRow`` and then ``previewIRButton``; the results are
``ORACLE/by_protocol/<DBPROTOCOL>.jsonl``, one object per distinct hexcode::

    {protocol, hex, appProtocol, params, code, freq, mode, pattern}

For each code this tool maps the hexcode with
:mod:`remote_ledger.irblaster.hex_philips`, encodes it with the registry's
protocol at the registry's nominal carrier, and compares the two signals:

* the carrier within 5 %;
* the same number of durations, unless the difference is a framing choice
  named in ``FRAMING`` below;
* every duration within 12 % or 150 us, whichever is larger.

**The toggle bit is not compared as a value.** The ledger compiles ``T=0``
(DESIGN D3b) and the app alternates it on every press; its *preview* shows the
first-press state. So each code is encoded with each toggle state the
protocol has, and must match for one of them. The toggle the app showed is
counted, and so is how many codes the ledger's own ``T=0`` would not match
bit-for-bit -- those are not mismatches, they are the D3b gap.

Three outcomes per code: ``matched``, ``mismatched`` and ``unrepresentable``
(the hex map refused the code, with a stable reason). A mismatch is
*explained* when ``explain`` can show the app's signal is the one a stated
defect in the app produces; the exit status is 1 only for an unexplained one.
Nothing is loosened to make a mismatch pass: the explained ones are still
counted as mismatches.

**Which reading.** The importer's table is ``FROM_DB_HEX`` (the wire reading);
SwiftRemote's own is ``FROM_DB_HEX_APP``. They are the same function for RC5,
RC6 and RCA_38, so those are compared directly. For Thomson7 they differ, and
the comparison above fails for all 29 codes; ``explain`` is then the proof that
the ledger's encoder reproduces what the app transmits: the app's pattern must
be exactly the ledger encoding of ``FROM_DB_HEX_APP``'s fields, sent twice.

Usage::

    python tools/irblaster_oracle_philips.py --oracle ORACLE [--protocol RC5] [-v]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.irblaster import hex_philips  # noqa: E402
from remote_ledger.protocols import REGISTRY  # noqa: E402

CARRIER_TOLERANCE = 0.05
RELATIVE_TOLERANCE = 0.12
ABSOLUTE_TOLERANCE_US = 150

#: The toggle states each DB protocol's ledger encoder takes (None: no toggle).
TOGGLES = {"RC5": (0, 1), "RC6": (0, 1), "Thomson7": (0, 1), "RCA_38": (None,)}
#: The toggle value the ledger compiles: D3b, the IRP's own default.
LEDGER_TOGGLE = 0

#: The framing differences that are choices rather than disagreements. Each is
#: argued in DESIGN D63 and is the *only* licence to differ in shape.
FRAMING = {
    # The app ends a frame after RC-6's six-unit signal-free time; the IRP's
    # ^107m makes the idle the rest of the 107 ms frame period. Same marks and
    # spaces, a longer final space.
    "RC6": "lead-out",
    # The app repeats the whole frame inside one pattern; the ledger emits it
    # once and records the second send as minSends (D3a).
    "Thomson7": "doubled-frame",
}


@dataclass
class Result:
    status: str                      # matched / mismatched / unrepresentable
    reason: str = ""                 # for mismatched and unrepresentable
    notes: list[str] = field(default_factory=list)
    explained: bool = False          # a mismatch that is a known app defect


def _tolerance(reference: int) -> float:
    return max(RELATIVE_TOLERANCE * reference, ABSOLUTE_TOLERANCE_US)


def _within(ours: list[int], theirs: list[int]) -> str:
    """'' when every duration is within tolerance, else why not."""
    if len(ours) != len(theirs):
        return f"{len(ours)} durations against the app's {len(theirs)}"
    for i, (a, b) in enumerate(zip(ours, theirs)):
        if abs(a - b) > _tolerance(b):
            return f"duration {i} is {a} us against the app's {b} us"
    return ""


def _signal(name: str, device: int, subdevice, function: int,
            toggle) -> tuple[int, list[int]]:
    entry = REGISTRY[name]
    kwargs = dict(device=device, subdevice=subdevice, function=function,
                  carrier_hz=entry.nominal_carrier_hz)
    if toggle is not None:
        kwargs["toggle"] = toggle
    signal = entry.encode(**kwargs)
    assert not signal.intro, "every protocol here is a single repeat sequence"
    return signal.carrier_hz, list(signal.repeat)


def _compare_frames(protocol: str, ledger: list[int], app: list[int]) -> tuple[str, str]:
    """``(why-not, framing-note)``; why-not is '' on a match."""
    framing = FRAMING.get(protocol)
    if framing == "doubled-frame":
        half = len(app) // 2
        if len(app) % 2 or app[:half] != app[half:]:
            return "the app's pattern is not one frame sent twice", ""
        why = _within(ledger, app[:half])
        return why, "" if why else "doubled-frame"
    if framing == "lead-out":
        why = _within(ledger[:-1], app[:-1])
        if why:
            return why, ""
        if len(ledger) != len(app):
            return f"{len(ledger)} durations against the app's {len(app)}", ""
        # The final space: the ledger's idle must be at least the app's, since
        # the app's is the standard's minimum.
        if ledger[-1] < app[-1]:
            return (f"final space is {ledger[-1]} us, shorter than the app's "
                    f"{app[-1]} us"), ""
        return "", "" if ledger[-1] == app[-1] else "lead-out"
    return _within(ledger, app), ""


def explain(record: dict) -> str:
    """The app defect that accounts for a mismatch, or '' if none does.

    Only Thomson7 has one. The app builds its frame from the hexcode as
    ``last4 + toggle + first7`` where the hexcode is the frame in order,
    ``first4 + toggle + last7`` (DESIGN D63). The claim is checkable:
    the app's pattern must be exactly the ledger encoding of the device and
    function that wrong order produces, for one toggle state, sent twice.
    """
    if record["protocol"] != "Thomson7":
        return ""
    _, device, _, function = hex_philips.FROM_DB_HEX_APP["Thomson7"](record["hex"])
    for toggle in (0, 1):
        _, ledger = _signal("Thomson7", device, None, function, toggle)
        why, _ = _compare_frames("Thomson7", ledger, record["pattern"])
        if not why:
            return (
                "the app sends Thomson7 bits 3..0, toggle, bits 11..5 of the "
                "hexcode, where the code is bits 11..8, toggle, bits 6..0"
            )
    return ""


def compare(record: dict) -> Result:
    protocol = record["protocol"]
    try:
        name, device, subdevice, function = hex_philips.FROM_DB_HEX[protocol](record["hex"])
    except ValueError as exc:
        return Result("unrepresentable", str(exc))

    app = list(record["pattern"])
    last = ""
    default_matches = False
    matched_toggles = []
    for toggle in TOGGLES[protocol]:
        carrier, ledger = _signal(name, device, subdevice, function, toggle)
        if abs(carrier - record["freq"]) > CARRIER_TOLERANCE * record["freq"]:
            return Result(
                "mismatched",
                f"carrier {carrier} Hz against the app's {record['freq']} Hz",
            )
        why, framing = _compare_frames(protocol, ledger, app)
        if not why:
            matched_toggles.append((toggle, framing))
        last = why
        if toggle in (LEDGER_TOGGLE, None):
            default_matches = not why

    if not matched_toggles:
        explanation = explain(record)
        if explanation:
            return Result("mismatched", explanation, explained=True)
        return Result("mismatched", last or "no toggle state matches")

    notes = []
    toggle, framing = matched_toggles[0]
    if len(matched_toggles) > 1:
        # Both toggle states within tolerance would mean the toggle bit is
        # invisible at this tolerance. Say so rather than pick one.
        notes.append("toggle indistinguishable")
    if toggle is not None:
        notes.append(f"app toggle={toggle}")
        if not default_matches:
            notes.append("ledger T=0 differs from the app's frame (D3b)")
    if framing:
        notes.append(f"framing: {framing}")
    if _signal(name, device, subdevice, function, toggle)[1] == app:
        notes.append("exact")
    return Result("matched", notes=notes)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(records: list[dict], *, verbose: bool = False, out=print) -> dict[str, Counter]:
    """Print the summary and return per-protocol counts of ``matched``,
    ``mismatched``, ``explained`` (a subset of ``mismatched``) and
    ``unrepresentable``."""
    counts: dict[str, Counter] = defaultdict(Counter)
    reasons: dict[str, Counter] = defaultdict(Counter)
    notes: dict[str, Counter] = defaultdict(Counter)
    shown: Counter = Counter()
    for record in records:
        result = compare(record)
        protocol = record["protocol"]
        counts[protocol][result.status] += 1
        if result.explained:
            counts[protocol]["explained"] += 1
        if result.status == "matched":
            for note in result.notes:
                notes[protocol][note] += 1
            continue
        reasons[protocol][(result.status, result.reason)] += 1
        if result.status == "mismatched" and not result.explained and (
                verbose or shown[protocol] < 5):
            shown[protocol] += 1
            out(f"MISMATCH {protocol} {record['hex']}: {result.reason}")
    for protocol in sorted(counts):
        c = counts[protocol]
        total = c["matched"] + c["mismatched"] + c["unrepresentable"]
        out(f"{protocol}: {total} codes, {c['matched']} matched, "
            f"{c['mismatched']} mismatched ({c['explained']} explained, "
            f"{c['mismatched'] - c['explained']} unexplained), "
            f"{c['unrepresentable']} unrepresentable")
        for note, n in sorted(notes[protocol].items()):
            out(f"    matched, {note}: {n}")
        for (status, reason), n in sorted(reasons[protocol].items()):
            out(f"    {status}: {n} -- {reason}")
    return counts


def unexplained(counts: dict[str, Counter]) -> int:
    return sum(c["mismatched"] - c["explained"] for c in counts.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--oracle", required=True,
                        help="the ORACLE directory (holding by_protocol/) or by_protocol itself")
    parser.add_argument("--protocol", action="append", choices=sorted(hex_philips.FROM_DB_HEX),
                        help="restrict to this DB protocol (repeatable)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every unexplained mismatch, not the first five per protocol")
    args = parser.parse_args()

    root = Path(args.oracle)
    if (root / "by_protocol").is_dir():
        root = root / "by_protocol"
    records: list[dict] = []
    for protocol in args.protocol or sorted(hex_philips.FROM_DB_HEX):
        path = root / f"{protocol}.jsonl"
        if not path.is_file():
            print(f"ERROR: {path} not found", file=sys.stderr)
            return 2
        records += load(path)

    return 1 if unexplained(run(records, verbose=args.verbose)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
