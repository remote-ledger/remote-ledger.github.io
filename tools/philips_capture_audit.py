#!/usr/bin/env python3
"""Check the RCA-38 and Thomson7 encoders against two hardware captures.

IrpTransmogrifier's test data holds ``irscope`` captures of real remotes with
the decode IrpTransmogrifier is expected to give them
(``src/test/teaserfiles/<name>.ict`` and ``.exp``). Two of them speak
protocols registered here:

* ``RCA-38.ict`` -- 31 keys of an RCA remote, expected ``RCA-38: {D=15,F=..}``;
* ``Thomson-0625.ict`` -- 7 keys of a Thomson TV remote, expected
  ``Thomson7: {D=12,F=..[,T=1]}`` (despite the file name).

Neither file is copied into this repository (they are GPL-3.0 test data of
another project); point ``--irpt`` at a checkout or extracted tarball of
``bengtmartensson/IrpTransmogrifier`` at ``c945e76``. Usage::

    python tools/philips_capture_audit.py --irpt DIR [--db swiftremote.sqlite]

For every key, on the first frame of its capture:

1. decode the bits from the spaces and check them against the ``.exp``
   fields (this checks bit order, field order and, for RCA-38, the
   complement half of the frame);
2. encode the same fields with the ledger and compare every duration within
   12 % or 150 us -- except the final gap, which is reported instead;
3. report how far the capture's durations sit from the IRP's, as a ratio.

A capture carries instrument bias and one remote's tolerances, so this
verifies layout and proportions, not absolute durations (D18 gate 2a).
For Thomson7 it also takes the five keys whose names SwiftRemote's database
shares (VOL+, VOL-, MUTE, J UP, J DOWN) and checks that the database hexcode,
mapped by ``irblaster/hex_philips.py``, is exactly the (device, function) the
capture decodes to. ``--db`` reads the hexcodes from the SwiftRemote database;
without it the five baked in below are used. Exit status is 0 when everything
holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.irblaster import hex_philips  # noqa: E402
from remote_ledger.protocols import REGISTRY  # noqa: E402

RELATIVE_TOLERANCE = 0.12
ABSOLUTE_TOLERANCE_US = 150

#: capture key -> label of the same key in SwiftRemote's Thomson7 remote
#: (id 800296), and the hexcode the database holds for it.
THOMSON_DB_KEYS = {
    "Vol+": ("VOL+", "329"),
    "Vol-": ("VOL-", "32A"),
    "Mute": ("MUTE", "305"),
    "Up": ("J UP", "30B"),
    "Down": ("J DOWN", "30D"),
}


def parse_ict(path: Path) -> tuple[int, dict[str, list[int]]]:
    """``(carrier_hz, {key: [mark, space, ...]})`` from an irscope file."""
    carrier = 0
    keys: dict[str, list[int]] = {}
    current: list[int] | None = None
    for line in path.read_text().splitlines():
        if line.startswith("carrier_frequency"):
            carrier = int(line.split()[1])
        elif line.startswith("note="):
            current = keys.setdefault(line[5:], [])
        elif current is not None and line[:1] in "+-":
            current.append(int(line[1:].split(",")[0]))
    return carrier, keys


def parse_exp(path: Path) -> dict[str, dict[str, int]]:
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"(.*?):\s+(\S+): \{(.*?)\}", line)
        if m:
            out[m.group(1)] = {k: int(v) for k, v in
                               (kv.split("=") for kv in m.group(3).split(","))}
    return out


def first_frame(durations: list[int], gap_us: int) -> list[int]:
    """Durations up to and including the first space of at least ``gap_us``."""
    frame = []
    for i, d in enumerate(durations):
        frame.append(d)
        if i % 2 == 1 and d >= gap_us:
            return frame
    return frame


def tolerance(reference: float) -> float:
    return max(RELATIVE_TOLERANCE * reference, ABSOLUTE_TOLERANCE_US)


def audit_rca38(irpt: Path) -> tuple[int, list[str]]:
    _, keys = parse_ict(irpt / "src/test/teaserfiles/RCA-38.ict")
    expected = parse_exp(irpt / "src/test/teaserfiles/RCA-38.exp")
    problems, ratios_mark, ratios_space = [], [], []
    for key, durations in keys.items():
        frame = first_frame(durations, 6000)
        ours = list(REGISTRY["RCA-38"].encode(
            device=expected[key]["D"], subdevice=None,
            function=expected[key]["F"], carrier_hz=38_700).repeat)
        # lead-in, 24 bits, stop mark, gap
        spaces = frame[3:50:2]
        bits = [0 if s < 1500 else 1 for s in spaces]
        assert len(bits) == 24, key
        value = int("".join(map(str, bits)), 2)
        d, f, nd, nf = value >> 20, (value >> 12) & 0xFF, (value >> 8) & 0xF, value & 0xFF
        if (d, f) != (expected[key]["D"], expected[key]["F"]) or nd != ~d & 0xF or nf != ~f & 0xFF:
            problems.append(f"RCA-38 {key}: decoded D={d} F={f} ~D={nd} ~F={nf}, "
                            f"expected {expected[key]}")
        if len(frame) != len(ours):
            problems.append(f"RCA-38 {key}: {len(frame)} durations against ours {len(ours)}")
            continue
        for i, (c, o) in enumerate(zip(frame, ours)):
            if abs(c - o) > tolerance(o):
                problems.append(f"RCA-38 {key}: duration {i} is {c} us, ours {o} us")
            if i < len(frame) - 1:
                (ratios_mark if i % 2 == 0 else ratios_space).append(c / o)
    print(f"RCA-38: {len(keys)} keys; capture/IRP duration ratio, marks (incl. "
          f"lead-in) {mean(ratios_mark):.3f}, spaces {mean(ratios_space):.3f}")
    return len(keys), problems


def audit_thomson7(irpt: Path, db: Path | None) -> tuple[int, list[str]]:
    carrier, keys = parse_ict(irpt / "src/test/teaserfiles/Thomson-0625.ict")
    expected = parse_exp(irpt / "src/test/teaserfiles/Thomson-0625.exp")
    problems, ratios_mark, ratios_space, periods = [], [], [], []
    for key, durations in keys.items():
        frame = first_frame(durations, 10_000)
        want = expected[key]
        toggle = want.get("T", 0)
        ours = list(REGISTRY["Thomson7"].encode(
            device=want["D"], subdevice=None, function=want["F"],
            carrier_hz=33_000, toggle=toggle).repeat)
        spaces = frame[1:24:2]
        bits = [0 if s < 3300 else 1 for s in spaces]       # LSB first
        d = sum(b << i for i, b in enumerate(bits[:4]))
        t = bits[4]
        f = sum(b << i for i, b in enumerate(bits[5:12]))
        if (d, f, t) != (want["D"], want["F"], toggle):
            problems.append(f"Thomson7 {key}: decoded D={d} F={f} T={t}, expected {want}")
        if len(frame) != len(ours):
            problems.append(f"Thomson7 {key}: {len(frame)} durations against ours {len(ours)}")
            continue
        for i, (c, o) in enumerate(zip(frame[:-1], ours[:-1])):
            if abs(c - o) > tolerance(o):
                problems.append(f"Thomson7 {key}: duration {i} is {c} us, ours {o} us")
            (ratios_mark if i % 2 == 0 else ratios_space).append(c / o)
        periods.append(sum(frame))
    print(f"Thomson7: {len(keys)} keys at {carrier} Hz; capture/IRP ratio, marks "
          f"{mean(ratios_mark):.3f}, spaces {mean(ratios_space):.3f}; first-frame "
          f"period {min(periods)}-{max(periods)} us against the IRP's 80,000")

    # The database's hexcodes against the capture.
    hexes = {k: v[1] for k, v in THOMSON_DB_KEYS.items()}
    if db is not None:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        labels = dict(conn.execute(
            "select label, hexcode from keys where protocol = 'Thomson7' and id = 800296"))
        for key, (label, baked) in THOMSON_DB_KEYS.items():
            if labels.get(label) != baked:
                problems.append(f"database {label!r} is {labels.get(label)}, not {baked}")
            hexes[key] = labels.get(label, baked)
    for key, hexcode in hexes.items():
        _, device, _, function = hex_philips.thomson7(hexcode)
        want = expected[key]
        ok = (device, function) == (want["D"], want["F"])
        print(f"    {key:5s} database {hexcode} -> D={device} F={function}; "
              f"capture decodes to D={want['D']} F={want['F']}: {'ok' if ok else 'DIFFERENT'}")
        if not ok:
            problems.append(f"database {key} {hexcode} maps to ({device}, {function}), "
                            f"the capture decodes to ({want['D']}, {want['F']})")
    return len(keys), problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--irpt", required=True, type=Path,
                        help="a checkout of bengtmartensson/IrpTransmogrifier at c945e76")
    parser.add_argument("--db", type=Path, help="SwiftRemote's swiftremote.sqlite")
    args = parser.parse_args()

    problems: list[str] = []
    for audit in (lambda: audit_rca38(args.irpt), lambda: audit_thomson7(args.irpt, args.db)):
        _, found = audit()
        problems += found
    for p in problems:
        print("PROBLEM", p)
    print("OK" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
