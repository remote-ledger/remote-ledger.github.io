#!/usr/bin/env python3
"""Re-run the evidence behind ``remotes/meridian/MSR.json`` (DESIGN section 16).

Three upstream sources are compared on decoded fields only -- address and
command -- and none of their data is copied into this repository:

* the LIRC conf for the MSR (the pinned remotes database),
* IRDB's Meridian tables (``codes/Meridian/*/19,-1.csv``),
* a Meridian 562+565 code set in Flipper-IRDB.

For every key the authored file claims a confidence tier. This script
recomputes how many sources agree and fails if a tier overstates (or
understates) the evidence. It then checks the RC5 encoder against every frame
of the Flipper set, using the address, command and toggle bit each frame
decodes to, and shows how the conf's 13-bit frames compare with those frames
once the first start bit is restored.

Usage::

    python tools/rc5_capture_audit.py --lirc DIR --irdb DIR --flipper DIR

``--lirc`` is a checkout of ``git.code.sf.net/p/lirc-remotes/code``, ``--irdb``
of ``probonopd/irdb`` and ``--flipper`` of ``Lucaslhm/Flipper-IRDB``. Exit
status is 0 when the file matches the evidence, 1 otherwise.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from remote_ledger.protocols import RC5

REMOTE = Path("remotes/meridian/MSR.json")
UNIT = 888.5          # the Flipper set's half-bit
TOLERANCE_US = 4
EXTENT_SLACK_US = 1500
DEVICE = 19


def half_bits(durations: list[int]) -> list[int]:
    """Mark/space durations (gap last) to half-bit levels, with the idle
    half-bit S1 begins with restored."""
    levels = [0]
    for i, d in enumerate(durations[:-1]):
        levels += [1 if i % 2 == 0 else 0] * round(d / UNIT)
    return levels


def bits_of(levels: list[int]) -> list[int | None]:
    if len(levels) % 2:
        levels = levels + [0]   # a last bit of 0 ends on a space the gap swallowed
    out: list[int | None] = []
    for j in range(0, len(levels), 2):
        out.append({(0, 1): 1, (1, 0): 0}.get((levels[j], levels[j + 1])))
    return out


def decode(durations: list[int]):
    """(device, function, toggle) of a valid 14-bit RC-5 frame, else None."""
    b = bits_of(half_bits(durations))
    if len(b) != 14 or None in b or b[0] != 1:
        return None
    device = int("".join(map(str, b[3:8])), 2)
    function = int("".join(map(str, b[8:14])), 2) + (0 if b[1] else 64)
    return device, function, b[2]


def read_conf(lirc: Path) -> dict[str, dict]:
    path = lirc / "remotes" / "meridian" / "MSR.lircd.conf"
    codes: dict[str, dict] = {}
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        m = re.match(r"^\s+(\S+)\s+0x([0-9A-Fa-f]+)", line)
        if m and not line.lstrip().startswith("begin"):
            code = int(m.group(2), 16)
            s2 = (code >> 12) & 1
            codes[m.group(1)] = dict(
                line=number, code=code, s2=s2, toggle=(code >> 11) & 1,
                device=(code >> 6) & 0x1F,
                function=(code & 0x3F) + (0 if s2 else 64))
    return codes


def read_irdb(irdb: Path) -> set[int]:
    functions: set[int] = set()
    for sub in ("Surround Processor", "System Remote"):
        with open(irdb / "codes" / "Meridian" / sub / "19,-1.csv") as f:
            for row in csv.DictReader(f):
                if row["protocol"] == "RC5" and row["device"] == str(DEVICE):
                    functions.add(int(row["function"]))
    return functions


def read_flipper(flipper: Path) -> dict[int, tuple[str, list[int], int]]:
    """function -> (name, durations, toggle) for each valid address-19 frame."""
    text = (flipper / "_Converted_" / "Pronto" / "M" / "Meridian" / "562+565.ir").read_text()
    frames: dict[int, tuple[str, list[int], int]] = {}
    for m in re.finditer(r"name: (.*?)\n.*?data: ([\d ]+)", text, re.S):
        durations = [int(x) for x in m.group(2).split()]
        got = decode(durations)
        if got and got[0] == DEVICE:
            frames[got[1]] = (m.group(1), durations, got[2])
    return frames


def conf_frame(info: dict) -> list[int]:
    """The 13 bits lircd sends for a conf code, as durations (no gap)."""
    halves = []
    for bit in format(info["code"], "013b"):
        halves += [0, 1] if bit == "1" else [1, 0]
    if halves[0] == 0:
        halves = halves[1:]          # the leading idle half-bit
    out, level, run = [], 1, 0
    for h in halves:
        if h == level:
            run += 889
        else:
            out.append(run)
            level, run = h, 889
    out.append(run)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for name in ("lirc", "irdb", "flipper"):
        ap.add_argument(f"--{name}", type=Path, required=True)
    args = ap.parse_args()

    conf = read_conf(args.lirc)
    irdb = read_irdb(args.irdb)
    flip = read_flipper(args.flipper)
    authored = json.loads(REMOTE.read_text())["keys"]
    problems: list[str] = []

    # 1. per-key evidence against the claimed tier
    both = one = none = 0
    by_function = {info["function"]: name for name, info in conf.items()}
    for name, spec in authored.items():
        form = spec["forms"][0]
        function = int(str(form["function"]), 0)
        if int(str(form["device"]), 0) != DEVICE or function not in by_function:
            problems.append(f"{name}: device/function {form['device']}/{form['function']} "
                            "is not a code in the LIRC conf")
            continue
        sources = int(function in irdb) + int(function in flip)
        both += sources == 2
        one += sources == 1
        none += sources == 0
        expected = "verified" if sources else "plausible"
        if form["confidence"] != expected:
            problems.append(f"{name}: claims {form['confidence']} with {sources} "
                            f"corroborating source(s); the evidence says {expected}")
    print(f"{len(authored)} keys, all address {DEVICE}: corroborated by IRDB and Flipper "
          f"{both}, by one of them {one}, by neither {none}")
    unknown = {c["function"] for c in conf.values()} - {
        int(str(s["forms"][0]["function"]), 0) for s in authored.values()}
    if unknown:
        problems.append(f"conf codes missing from the authored file: {sorted(unknown)}")

    # 2. the encoder against every frame in the Flipper set
    reproduced = 0
    for function, (name, durations, toggle) in sorted(flip.items()):
        signal = RC5.encode(device=DEVICE, subdevice=None, function=function,
                            carrier_hz=36_000, toggle=toggle)
        ours = list(signal.repeat)
        same = (len(ours[:-1]) == len(durations[:-1])
                and all(abs(a - b) <= TOLERANCE_US for a, b in zip(ours[:-1], durations[:-1]))
                and abs(sum(ours) - sum(durations)) < EXTENT_SLACK_US)
        reproduced += same
        if not same:
            problems.append(f"encoder disagrees with the Flipper frame {name!r} "
                            f"(function {function}, toggle {toggle})")
    print(f"encoder reproduces {reproduced} of {len(flip)} Flipper frames "
          "(address, command and toggle taken from each frame)")

    # 3. the conf's 13-bit frames against the same frames, S1 restored
    identical = toggle_only = absent = 0
    for name, info in conf.items():
        if info["function"] not in flip:
            absent += 1
            continue
        _, durations, flip_toggle = flip[info["function"]]
        frame = conf_frame(info)
        # S1 is a lone mark; with S2 = 1 it is followed by S2's own space,
        # with S2 = 0 it merges into S2's mark.
        real = [889, 889, *frame] if info["s2"] else [frame[0] + 889, *frame[1:]]
        # A capture ends in its gap. A frame that ends on a space has that
        # space folded into the gap, so it is one element longer than the
        # capture without its gap.
        n = len(durations) - 1
        ok = len(real) in (n, n + 1) and all(
            abs(a - b) <= TOLERANCE_US for a, b in zip(real[:n], durations[:n]))
        if ok:
            identical += 1
        elif info["toggle"] != flip_toggle:
            toggle_only += 1
        else:
            problems.append(f"{name}: conf frame plus S1 differs from the Flipper frame "
                            "for a reason other than the toggle bit")
    print(f"conf frames plus a restored S1: identical to the Flipper set {identical}, "
          f"differing only in the toggle bit {toggle_only}, not in it {absent}")

    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
