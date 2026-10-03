#!/usr/bin/env python3
"""Audit the misc-family encoders against IrpTransmogrifier's hardware captures.

IrpTransmogrifier's test data (``src/test/teaserfiles/``, "used with
permission of the author", from the hifi-remote.com forum thread cited in its
README) holds real IrScope captures of remotes for three of the protocols
registered here, each with the decode IrpTransmogrifier's own test expects:

    Samsung36.ict / .exp   a Samsung Blu-ray remote, 8 keys
    Proton.ict    / .exp   a Proton-protocol remote, 9 keys
    F12.ict       / .exp   a strict-F12 remote, 11 keys

They are **cited, not vendored**: the repository is GPL-3.0 and the files are
used there by permission of a third party. Check it out at the pinned commit
and point this script at it::

    git clone https://github.com/bengtmartensson/IrpTransmogrifier
    git -C IrpTransmogrifier checkout c945e7638355ca5697f458d33338ee1bfd4d1640
    python tools/misc_capture_audit.py --irpt IrpTransmogrifier

For every key it (1) reads the first frame's bits straight off the capture,
with a threshold decoder written here from the frame layout and not from the
encoder, (2) checks the fields it reads equal the ``.exp`` file's, (3) asks
the registered encoder for that signal and checks its bits are the capture's
bits, and (4) reports how far each duration is from the capture's, as a ratio.

A capture carries instrument bias, so (4) is information, not a pass mark: it
is the evidence for the unit and extent findings in NOTES/misc.md. (1) to (3)
are the structural claim, and the exit status is 1 if any of them fails.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.protocols import REGISTRY  # noqa: E402

# A gap this long ends a frame in all three files (the shortest in-frame space
# is Samsung36's 5 ms divider).
FRAME_GAP_US = 20_000


def load_ict(path: Path) -> dict[str, tuple[int, list[int]]]:
    """``{note: (carrier_hz, signed durations in us)}`` from an IrScope file."""
    out: dict[str, tuple[int, list[int]]] = {}
    current = None
    carrier = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("carrier_frequency"):
            carrier = int(line.split()[1])
        elif line.startswith("note="):
            current = line[5:]
            out[current] = (carrier, [])
        elif current and line[:1] in "+-" and line[1:2].isdigit():
            out[current][1].append(int(line.split(",")[0]))
    return out


def first_frame(signed: list[int]) -> list[int]:
    """Unsigned durations of the first frame, ending on its lead-out gap."""
    frame: list[int] = []
    for d in signed:
        frame.append(abs(d))
        if d < 0 and abs(d) >= FRAME_GAP_US:
            break
    return frame


def read_expected(path: Path) -> dict[str, dict[str, int]]:
    """``{key: {D:.., S:.., F:.., E:..}}`` from the ``.exp`` file."""
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"(.+?):\s+\w+: \{(.*?)\}", line)
        if m:
            out[m.group(1)] = {
                k: int(v) for k, v in (kv.split("=") for kv in m.group(2).split(","))
            }
    return out


def _bit(mark: int, space: int, *, wide_mark: bool) -> int:
    """Classify one bit: the long half is about three times the short one,
    so "more than twice" separates them whatever the capture's bias."""
    return int((mark > 2 * space) if wide_mark else (space > 2 * mark))


def _lsb(bits: list[int]) -> int:
    return sum(b << i for i, b in enumerate(bits))


def decode_samsung36(frame: list[int]):
    """16 bits, a divider, 20 bits; D, S, then E, F and a checked ~F."""
    pairs = list(zip(frame[0::2], frame[1::2]))
    header, body = pairs[0], pairs[1:]
    bits = [_bit(m, s, wide_mark=False) for m, s in body[:16]]
    divider = body[16]
    tail = [_bit(m, s, wide_mark=False) for m, s in body[17:37]]
    assert header[0] > 4000 and header[1] > 4000
    assert divider[1] > 3000, "no divider where the layout puts one"
    e, f, nf = _lsb(tail[:4]), _lsb(tail[4:12]), _lsb(tail[12:20])
    assert nf == (~f & 0xFF), "~F is not the complement of F"
    return {"D": _lsb(bits[:8]), "S": _lsb(bits[8:16]), "F": f, "E": e}


def decode_proton(frame: list[int]):
    pairs = list(zip(frame[0::2], frame[1::2]))
    body = pairs[1:]
    d = [_bit(m, s, wide_mark=False) for m, s in body[:8]]
    divider = body[8]
    f = [_bit(m, s, wide_mark=False) for m, s in body[9:17]]
    assert divider[1] > 3000, "no divider where the layout puts one"
    return {"D": _lsb(d), "F": _lsb(f)}


def decode_f12(frame: list[int]):
    """Twelve bits, a zero a narrow mark and a one a wide one. The last bit's
    space is swallowed by the lead-out, so classify every bit by its *mark*
    against the narrowest mark in the frame."""
    pairs = list(zip(frame[0::2], frame[1::2]))[:12]
    narrow = min(m for m, _ in pairs)
    bits = [int(m > 2 * narrow) for m, _ in pairs]
    return {"D": _lsb(bits[:3]), "S": bits[3], "F": _lsb(bits[4:12])}


def encode_like(proto: str, p: dict[str, int], carrier: int):
    entry = REGISTRY[proto]
    if proto == "Samsung36":
        return entry.encode(device=p["D"], subdevice=p["S"],
                            function=(p["E"] << 8) | p["F"], carrier_hz=carrier)
    if proto == "Proton":
        return entry.encode(device=p["D"], subdevice=None, function=p["F"],
                            carrier_hz=carrier)
    return entry.encode(device=p["D"], subdevice=p["S"], function=p["F"],
                        carrier_hz=carrier)


CASES = (
    ("Samsung36", "Samsung36", decode_samsung36),
    ("Proton", "Proton", decode_proton),
    ("F12", "F12_relaxed", decode_f12),
)


def audit(root: Path) -> int:
    data = root / "src" / "test" / "teaserfiles"
    failures = 0
    for stem, proto, decode in CASES:
        captures = load_ict(data / f"{stem}.ict")
        expected = read_expected(data / f"{stem}.exp")
        marks, spaces, gaps, ratios, carriers = [], [], [], [], []
        ok = 0
        for key, (carrier, signed) in captures.items():
            frame = first_frame(signed)
            fields = decode(frame)
            want = {k: v for k, v in expected[key].items() if k in fields}
            if fields != want:
                failures += 1
                print(f"  FAIL {stem}/{key}: capture reads {fields}, .exp says {want}")
                continue
            ours = encode_like(proto, fields, carrier).repeat
            if len(ours) != len(frame):
                failures += 1
                print(f"  FAIL {stem}/{key}: encoder has {len(ours)} durations, "
                      f"capture {len(frame)}")
                continue
            ok += 1
            carriers.append(carrier)
            # Compare every duration but the lead-out, which a capture
            # truncates or pads at will.
            for i, (a, b) in enumerate(zip(ours[:-1], frame[:-1])):
                ratios.append(b / a)
            marks += [b for i, b in enumerate(frame[:-1]) if i % 2 == 0]
            spaces += [b for i, b in enumerate(frame[:-1]) if i % 2 == 1]
            if frame[-1] < 200_000:       # not the capture's last, truncated frame
                gaps.append((sum(ours), sum(frame)))
        print(f"{stem} ({proto}): {ok}/{len(captures)} keys decode to the .exp "
              f"fields and re-encode to the same bit pattern")
        if ratios:
            print(f"  capture / encoder, every duration but the lead-out: "
                  f"median {statistics.median(ratios):.3f}, "
                  f"min {min(ratios):.3f}, max {max(ratios):.3f}")
            print(f"  carrier measured by the capture tool: "
                  f"{min(carriers)}-{max(carriers)} Hz, "
                  f"registry nominal {REGISTRY[proto].nominal_carrier_hz} Hz")
            if gaps:
                enc = sorted({a for a, _ in gaps})
                cap = sorted({b for _, b in gaps})
                print(f"  whole-frame period, lead-out included: encoder "
                      f"{enc[0]}-{enc[-1]} us; capture {cap[0]}-{cap[-1]} us "
                      f"({cap[0] / enc[-1]:.3f}-{cap[-1] / enc[0]:.3f}x)")
    print("structural audit:", "FAILED" if failures else "ok")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--irpt", required=True,
                    help="a checkout of bengtmartensson/IrpTransmogrifier @c945e76")
    args = ap.parse_args()
    return audit(Path(args.irpt))


if __name__ == "__main__":
    raise SystemExit(main())
