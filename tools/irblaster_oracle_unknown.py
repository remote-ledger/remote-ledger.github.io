#!/usr/bin/env python3
"""Compare REC80, RCC2026 and RCC0082 against what SwiftRemote transmits.

SwiftRemote's database has three protocol names that no published protocol
list contains. ``remote_ledger.irblaster.hex_unknown`` maps each DB hexcode
to a registered protocol (Aiwa, Blaupunkt, and the Kaseikyo family) and its
parameters; this tool checks that the ledger's compiled signal is the signal
the app sends today.

The oracle is the app's own code: every distinct DB hexcode was run through
``buildButtonFromDbRow`` and ``previewIRButton`` (SwiftRemote
``lib/utils/db_button_import.dart``, ``lib/ir/protocols/*.dart``), and the
result is one JSON object per code in ``<oracle>/by_protocol/<NAME>.jsonl``::

    {protocol, hex, appProtocol, params, code, freq, mode, pattern}

Usage::

    python tools/irblaster_oracle_unknown.py --oracle DIR [--db swiftremote.sqlite]
    python tools/irblaster_oracle_unknown.py --oracle DIR --write-fixtures tests/fixtures/irblaster

For every code the tool reports exactly one of:

``matched``
    Same carrier (within 5 %), the same number of durations, every duration
    within 12 % or 150 us, whichever is larger.
``matched, lead-out differs``
    All of the above except the final gap, which is the protocol's own
    inter-frame gap and not part of the code. The app ends every REC80 frame
    with 173 units (Panasonic's); Fujitsu, Teac-K and SharpDVD define 110,
    100 and 48. Reported, and allowed only for those three.
``mismatched, explained``
    RCC2026 only: SwiftRemote's copy of the encoder reads the LAST 42 of the
    44 bits, upstream's current one reads the FIRST 42 (commit 3bb60e3178,
    "fix: preserve all 42 RCC2026 database payload bits"). The ledger follows
    the left-aligned reading. A code is explained when this tool's port of the
    stale reading reproduces the oracle exactly, its port of the fixed reading
    reproduces the ledger's signal, and the two differ. The report then also
    maps the same codes the way SwiftRemote reads them
    (``FROM_DB_HEX_SWIFTREMOTE``), which must match the oracle wherever it
    maps at all, and counts the rest as unrepresentable.
``unrepresentable``
    ``hex_unknown`` raised ValueError; counted by its (stable) reason.
``UNEXPLAINED``
    Anything else. Makes the exit status 1.

The tolerance is not widened to hide anything: the largest deviation inside
the tolerance is printed per protocol, with the value pairs behind it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.irblaster.hex_unknown import FROM_DB_HEX, FROM_DB_HEX_SWIFTREMOTE, MIN_SENDS  # noqa: E402
from remote_ledger.protocols import REGISTRY  # noqa: E402

CARRIER_TOL = 0.05
REL_TOL = 0.12
ABS_TOL_US = 150

DB_PROTOCOLS = ("REC80", "RCC2026", "RCC0082")

#: Ledger protocols whose own lead-out differs from the app's 173 units.
#: Everything else must match its final gap too.
LEADOUT_DOCUMENTED = {
    "Fujitsu": "IRP gap is 110 units; the app sends 173",
    "Teac-K": "IRP gap is 100 units; the app sends 173",
    "SharpDVD": "IRP gap is 48 units at a 400 us unit; the app sends 173 at 432",
}


# --- ports of the app's encoders (Dart -> Python), used only to check the oracle ----


def app_rec80(hexcode: str) -> list[int]:
    """``rec80.dart``: header, 48 bits MSB-first, tail."""
    bits = f"{int(hexcode, 16):048b}"
    out = [3456, 1728]
    for b in bits:
        out += [432, 1296 if b == "1" else 432]
    return out + [432, 74736]


def app_rcc2026(hexcode: str, aligned: str) -> list[int]:
    """``rcc2026.dart``; ``aligned`` is "last" (SwiftRemote) or "first" (upstream)."""
    bin44 = f"{int(hexcode, 16):044b}"
    bits = bin44[-42:] if aligned == "last" else bin44[:42]
    out = [8800, 4400]
    for b in bits:
        out += [550, 550 if b == "0" else 1650]
    return out + [550, 23100, 8800, 4400, 550, 90750]


def app_rcc0082(hexcode: str) -> list[int]:
    """``rcc0082.dart``: a transition coder over a 10-character string."""
    bit, gap, end = 528, 2640, 21120
    prefix = [bit] * 22
    prefix[1] = gap
    prefix[21] = end
    out = list(prefix) + [bit, gap, bit, bit]
    n = [int(c, 16) & 15 for c in hexcode]
    bits = "0" + f"{n[0]:04b}"[1:] + f"{n[1]:04b}" + f"{n[2]:04b}"[:2]
    for i in range(1, len(bits)):
        if bits[i] == bits[i - 1]:
            out += [bit, bit]
        else:
            out[-1] += bit
            out.append(bit)
    if len(out) % 2 == 0:
        out[-1] = 111408
    else:
        out.append(110880)
    return out + prefix


# --- the ledger's side --------------------------------------------------------------


def ledger_burst(name: str, device: int, sub, function: int, carrier_hz: int, sends: int):
    """What one press sends: the intro once, then the repeat ``sends - 1`` times
    (or ``sends`` times when there is no intro) -- D3a / D38's ``minSends``.

    Returns ``(burst, ending)``. ``ending`` is the IRP's closing sequence when
    the signal cannot carry it (``IrSignal.ending`` is reserved, D1): Blaupunkt's
    is its opening sync, which is its intro less the repeated frame.
    """
    signal = REGISTRY[name].encode(
        device=device, subdevice=sub, function=function, carrier_hz=carrier_hz
    )
    burst = list(signal.intro)
    burst += list(signal.repeat) * (sends - (1 if signal.intro else 0))
    ending: list[int] = []
    if name == "Blaupunkt":
        ending = list(signal.intro[: len(signal.intro) - len(signal.repeat)])
    return burst, ending


def _off(ledger: list[int], app: list[int]) -> list[int]:
    return [
        i
        for i, (a, b) in enumerate(zip(ledger, app))
        if abs(a - b) > max(REL_TOL * b, ABS_TOL_US)
    ]


def classify(record: dict, mapping=None) -> dict:
    """One oracle record -> ``{"class": ..., ...}``. Pure; no file access."""
    proto = record["protocol"]
    hexcode = record["hex"]
    app = record["pattern"]
    try:
        name, device, sub, function = (mapping or FROM_DB_HEX[proto])(hexcode)
    except ValueError as err:
        return {"class": "unrepresentable", "reason": str(err)}

    sends = MIN_SENDS.get(proto, 1)
    burst, ending = ledger_burst(name, device, sub, function, record["freq"], sends)
    ledger = burst + ending
    out = {"ledger": name, "params": (device, sub, function), "burst": ledger}

    # The remote file's carrierHz is authoritative (D3) and will be the app's;
    # what can disagree is the registry's IRP carrier, which this checks.
    nominal = REGISTRY[name].nominal_carrier_hz
    if abs(nominal - record["freq"]) > CARRIER_TOL * record["freq"]:
        return {**out, "class": "UNEXPLAINED",
                "reason": f"IRP carrier {nominal} against the app's {record['freq']}"}
    if len(ledger) != len(app):
        return {**out, "class": "UNEXPLAINED", "reason": f"{len(ledger)} durations against the app's {len(app)}"}

    off = _off(ledger, app)
    if not off:
        return {**out, "class": "matched"}

    if off == [len(app) - 1] and name in LEADOUT_DOCUMENTED:
        return {**out, "class": "matched, lead-out differs", "reason": LEADOUT_DOCUMENTED[name]}

    if proto == "RCC2026":
        stale = app_rcc2026(hexcode, "last")
        fixed = app_rcc2026(hexcode, "first")
        if stale == app and not _off(ledger, fixed) and _off(fixed, stale):
            return {**out, "class": "mismatched, explained",
                    "reason": "SwiftRemote's RCC2026 copy reads the last 42 bits (upstream 3bb60e3178 reads the first 42)"}

    return {**out, "class": "UNEXPLAINED", "reason": f"durations {off[:6]} outside tolerance"}


def check_port(record: dict) -> str | None:
    """Does this file's Python port of the app's encoder reproduce the oracle?"""
    proto = record["protocol"]
    hexcode = record["hex"]
    ports = {
        "REC80": lambda: app_rec80(hexcode),
        "RCC2026": lambda: app_rcc2026(hexcode, "last"),
        "RCC0082": lambda: app_rcc0082(hexcode),
    }
    return None if ports[proto]() == record["pattern"] else "port differs from the oracle"


# --- reporting ------------------------------------------------------------------------


def load(oracle: Path, proto: str) -> list[dict]:
    path = oracle / "by_protocol" / f"{proto}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def key_counts(db: Path | None) -> dict[tuple[str, str], int]:
    if db is None:
        return {}
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    return {
        (p, h): n
        for p, h, n in con.execute(
            "select protocol, hexcode, count(*) from keys where protocol in (?,?,?) group by 1,2",
            DB_PROTOCOLS,
        )
    }


def deviation_pairs(records: list[dict], results: list[dict]) -> Counter:
    """(ledger, app) pairs that differ at all, over the non-final durations."""
    pairs: Counter = Counter()
    for record, result in zip(records, results):
        if "burst" not in result or len(result["burst"]) != len(record["pattern"]):
            continue
        if result["class"] in ("mismatched, explained", "UNEXPLAINED"):
            continue
        for a, b in list(zip(result["burst"], record["pattern"]))[:-1]:
            if a != b:
                pairs[(a, b)] += 1
    return pairs


def report(oracle: Path, db: Path | None, out=sys.stdout) -> int:
    keys = key_counts(db)
    unexplained = 0
    for proto in DB_PROTOCOLS:
        records = load(oracle, proto)
        results = [classify(r) for r in records]
        port_bad = sum(1 for r in records if check_port(r))
        classes = Counter(r["class"] for r in results)
        by_key = defaultdict(int)
        for r, res in zip(records, results):
            by_key[res["class"]] += keys.get((proto, r["hex"]), 0)

        print(f"\n== {proto}: {len(records)} distinct codes ==", file=out)
        for cls in ("matched", "matched, lead-out differs", "mismatched, explained",
                    "unrepresentable", "UNEXPLAINED"):
            suffix = f"  ({by_key[cls]} DB keys)" if keys else ""
            print(f"  {cls:28s} {classes.get(cls, 0):5d}{suffix}", file=out)
        print(f"  (this tool's port of the app's encoder differs from the oracle in {port_bad} codes)", file=out)

        by_ledger = Counter(r["ledger"] for r in results if "ledger" in r)
        print("  by ledger protocol: " + ", ".join(f"{k} {v}" for k, v in sorted(by_ledger.items())), file=out)

        reasons = Counter(r["reason"] for r in results if r["class"] in ("unrepresentable", "UNEXPLAINED", "matched, lead-out differs", "mismatched, explained"))
        for reason, n in reasons.most_common():
            kinds = sorted({r["class"] for r in results if r.get("reason") == reason})
            print(f"    {n:5d}  [{'/'.join(kinds)}] {reason}", file=out)

        pairs = deviation_pairs(records, results)
        if pairs:
            worst = max(pairs, key=lambda p: abs(p[0] - p[1]) / p[1])
            print(f"  durations that differ inside the tolerance (ledger, app): "
                  f"{len(pairs)} distinct pairs; largest relative deviation "
                  f"{abs(worst[0] - worst[1]) / worst[1]:.1%} ({worst[0]} vs {worst[1]})", file=out)
            for (a, b), n in sorted(pairs.items(), key=lambda kv: -abs(kv[0][0] - kv[0][1]) / kv[0][1])[:8]:
                print(f"      {a:7d} vs {b:7d}  {abs(a - b) / b:6.1%}  x{n}", file=out)
        if proto in FROM_DB_HEX_SWIFTREMOTE:
            # The alternative reading: what the app sends today. It must match
            # the oracle wherever it maps at all, which is what makes the
            # explanation above a measurement and not an assertion.
            stale = [classify(r, FROM_DB_HEX_SWIFTREMOTE[proto]) for r in records]
            sc = Counter(r["class"] for r in stale)
            by_key_stale = defaultdict(int)
            for r, res in zip(records, stale):
                by_key_stale[res["class"]] += keys.get((proto, r["hex"]), 0)
            note = ", ".join(
                f"{cls} {n}" + (f" ({by_key_stale[cls]} keys)" if keys else "")
                for cls, n in sorted(sc.items())
            )
            print(f"  read the way SwiftRemote reads it (FROM_DB_HEX_SWIFTREMOTE): {note}", file=out)
            unexplained += sc.get("UNEXPLAINED", 0) + sc.get("mismatched, explained", 0)
        unexplained += classes.get("UNEXPLAINED", 0) + port_bad
    print(f"\nunexplained: {unexplained}", file=out)
    return 1 if unexplained else 0


# --- fixtures -------------------------------------------------------------------------


def _sample(items: list, n: int) -> list:
    """First, last and evenly spaced in between; deterministic."""
    if len(items) <= n:
        return list(items)
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


#: Records kept per (ledger protocol, class, reason) group. RCC2026 and RCC0082
#: have one or two big groups, so they get more per group to stay at a few dozen.
PER_GROUP = {"REC80": 4, "RCC2026": 20, "RCC0082": 30}


def write_fixtures(oracle: Path, dest: Path) -> None:
    """The committed pytest fixtures: a deterministic few dozen codes per DB
    protocol, copied from the oracle, one record per line."""
    dest.mkdir(parents=True, exist_ok=True)
    for proto in DB_PROTOCOLS:
        per_group = PER_GROUP[proto]
        records = sorted(load(oracle, proto), key=lambda r: r["hex"])
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for r in records:
            res = classify(r)
            groups[(res.get("ledger", "-"), res["class"], res.get("reason", ""))].append(r)
        chosen: dict[str, dict] = {}
        for _, items in sorted(groups.items()):
            for r in _sample(items, per_group):
                chosen[r["hex"]] = r
        header = {
            "protocol": proto,
            "source": f"SwiftRemote ORACLE/by_protocol/{proto}.jsonl: the app's own encoder output "
                      "(buildButtonFromDbRow, previewIRButton) for each distinct DB hexcode",
            "selection": "per (ledger protocol, class, reason) group: first, last and evenly spaced, "
                         f"at most {per_group}; regenerate with tools/irblaster_oracle_unknown.py --write-fixtures",
        }
        lines = [
            json.dumps({"hex": r["hex"], "freq": r["freq"], "pattern": r["pattern"]}, separators=(", ", ": "))
            for r in sorted(chosen.values(), key=lambda r: r["hex"])
        ]
        text = "{\n" + "".join(f" {json.dumps(k)}: {json.dumps(v)},\n" for k, v in header.items())
        text += ' "records": [\n  ' + ",\n  ".join(lines) + "\n ]\n}\n"
        path = dest / f"{proto.lower()}.json"
        path.write_text(text, encoding="utf-8")
        print(f"{path}: {len(lines)} records", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--oracle", required=True, type=Path, help="directory holding by_protocol/*.jsonl")
    parser.add_argument("--db", type=Path, help="swiftremote.sqlite, to weight the counts by DB keys")
    parser.add_argument("--write-fixtures", type=Path, metavar="DIR", help="write the committed pytest fixtures and exit")
    args = parser.parse_args()
    if args.write_fixtures:
        write_fixtures(args.oracle, args.write_fixtures)
        return 0
    return report(args.oracle, args.db)


if __name__ == "__main__":
    sys.exit(main())
