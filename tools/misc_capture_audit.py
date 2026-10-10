#!/usr/bin/env python3
"""Audit the misc-family and NEC-f16 encoders against IrpTransmogrifier's hardware captures.

IrpTransmogrifier's test data (``src/test/teaserfiles/``, "used with
permission of the author", from the hifi-remote.com forum thread cited in its
README) holds real IrScope captures of remotes for protocols registered here,
each with the decode IrpTransmogrifier's own test expects:

    Samsung36.ict / .exp   a Samsung Blu-ray remote, 8 keys
    Proton.ict    / .exp   a Proton-protocol remote, 9 keys
    F12.ict       / .exp   a strict-F12 remote, 11 keys
    NEC-f16.ict   / .exp   an NEC1-f16 remote, whose fourth byte is its own field
    NEC2-f16.ict  / .exp   an NEC2-f16 remote (and four plain NEC2 keys of it)
    NEC1-y1_nofinalA / _B  two Yamaha receivers' NEC1-Yamaha, style Y1
    NEC2-rnc.ict  / .exp   an NEC1-rnc remote, NEC2-f16 with E the nibble-swapped complement
    Nokia32.ict   / .exp   a Nokia receiver's remote, whose symbols are pulse positions
    Panasonic_Old.ict / .exp   an older Panasonic remote
    Panasonic_Old_Pronto.txt / .exp   sixteen Pronto strings of a cable box's remote
    RCA_Old.ict   / .exp   an RCA(Old) remote: RCA's bits, a longer first lead-in and a double stop mark
    RCA-38.ict    / .exp   an RCA-38 remote: RCA's frame at another carrier
    TDC-38_6_10.ict / TDC-56_14_10.ict   two IPTV remotes whose bits are biphase
    GI_Cable.ict  / .exp   a General Instrument cable box's remote: an intro and a short repeat

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
is the evidence for the unit and extent findings in DESIGN D65. (1) to (3)
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


def first_frame(signed: list[int], gap_us: int = FRAME_GAP_US) -> list[int]:
    """Unsigned durations of the first frame, ending on its lead-out gap."""
    frame: list[int] = []
    for d in signed:
        frame.append(abs(d))
        if d < 0 and abs(d) >= gap_us:
            break
    return frame


def read_expected(path: Path) -> dict[str, dict[str, int]]:
    """``{key: {D:.., S:.., F:.., E:..}}`` from the ``.exp`` file; a ``_protocol`` entry names the first
    decode's protocol (``NEC1-rnc``, ``NEC1-Yamaha``), whose own parameters are not ``E``'s."""
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"(.+?):\s+([^\s:]+): \{(.*?)\}", line)
        if m:
            out[m.group(1)] = {
                k: int(v) for k, v in (kv.split("=") for kv in m.group(3).split(","))
            }
            out[m.group(1)]["_protocol"] = m.group(2)
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


def yamaha_e(f: int, y: int) -> int:
    """The fourth byte of ``NEC1-Yamaha``'s IRP, ``E0=(~Y:1:1)^(F:1)``, ``~F:6:1``, ``E7=(~Y:1)^(F:1:7)``."""
    e0 = (~(y >> 1) & 1) ^ (f & 1)
    e7 = (~y & 1) ^ ((f >> 7) & 1)
    return e0 | (~f & 0x7E) | (e7 << 7)


def rnc_e(f: int) -> int:
    """``NEC1-rnc``'s fourth byte, ``~F:4:4,~F:4``: the complement with its nibbles swapped."""
    return ((~f & 0x0F) << 4) | ((~f >> 4) & 0x0F)


def nec_expected(raw: dict[str, int]) -> dict[str, int]:
    """The four bytes an ``.exp`` line says: ``S`` defaults to ``255-D`` and ``E`` to ``255-F`` unless the line is
    a Yamaha or an rnc decode, which say how ``E`` follows ``F``."""
    d, f = raw["D"], raw["F"]
    kind = raw["_protocol"]
    if "Y" in raw:
        e = yamaha_e(f, raw["Y"])
    elif kind.endswith("rnc"):
        e = rnc_e(f)
    else:
        e = raw.get("E", 255 - f)
    return {"D": d, "S": raw.get("S", 255 - d), "F": f, "E": e}


def decode_nec_family(frame: list[int]):
    """The 16-unit lead-in, then thirty-two bits, a zero a short space and a one a long one: D, S, F and E."""
    pairs = list(zip(frame[0::2], frame[1::2]))
    header, body = pairs[0], pairs[1:]
    assert header[0] > 7000 and header[1] > 3500, "not a 16-unit NEC lead-in"
    bits = [_bit(m, s, wide_mark=False) for m, s in body[:32]]
    return {"D": _lsb(bits[:8]), "S": _lsb(bits[8:16]), "F": _lsb(bits[16:24]), "E": _lsb(bits[24:32])}


def decode_nokia32(frame: list[int]):
    """The lead-in, sixteen symbols (a 6-period mark and a space of 10, 16, 22 or 28 periods) and the stop mark:
    D, S, T, X and F. The period is the mean of the symbols' marks over 6."""
    pairs = list(zip(frame[0::2], frame[1::2]))
    assert len(pairs) >= 18, "too short for a Nokia32 frame"
    lead, symbols = pairs[0], pairs[1:17]
    period = statistics.mean(m for m, _ in symbols) / 6
    assert 13 < lead[0] / period < 17 and 8 < lead[1] / period < 12, "not a 15,10 lead-in"
    word = 0
    for _, space in symbols:
        steps = round((space / period - 10) / 6)
        assert 0 <= steps <= 3, "a symbol with no place"
        word = word << 2 | steps
    return {"D": word >> 24, "S": word >> 16 & 0xFF, "T": word >> 15 & 1, "X": word >> 8 & 0x7F, "F": word & 0xFF}


def decode_panasonic_old(frame: list[int]):
    """A 4,4 lead-in and 22 bits (D:5, F:6, ~D:5, ~F:6), least significant first; the complements are checked."""
    pairs = list(zip(frame[0::2], frame[1::2]))
    assert len(pairs) >= 24, "too short for a Panasonic_Old frame"
    lead, body = pairs[0], pairs[1:23]
    assert 0.8 < lead[0] / lead[1] < 1.25 and lead[0] > 3 * min(m for m, _ in body), "no 4,4 lead-in"
    bits = [_bit(m, s, wide_mark=False) for m, s in body]
    d, f, nd, nf = _lsb(bits[:5]), _lsb(bits[5:11]), _lsb(bits[11:16]), _lsb(bits[16:22])
    assert nd == (~d & 0x1F) and nf == (~f & 0x3F), "the complements are not complements"
    return {"D": d, "F": f}


def decode_rca(frame: list[int]):
    """A long lead-in (RCA's 8,8 units, or RCA(Old)'s longer first one), 24 bits most significant first (D:4, F:8, ~D:4,
    ~F:8, a one-unit mark then a space of two units for a 0 and four for a 1) and a stop mark. The complements are
    checked."""
    pairs = list(zip(frame[0::2], frame[1::2]))
    assert len(pairs) >= 26, "too short for an RCA frame"
    lead, body = pairs[0], pairs[1:25]
    unit = statistics.median(m for m, _ in body)
    assert lead[0] > 6 * unit and 6 < lead[1] / unit < 10, "no long lead-in"
    bits = [int(s > 3 * unit) for _, s in body]               # 2 and 4 units: 3 is the midpoint
    word = 0
    for b in bits:
        word = word << 1 | b
    d, f, nd, nf = word >> 20, word >> 12 & 0xFF, word >> 8 & 0xF, word & 0xFF
    assert nd == (~d & 0xF) and nf == (~f & 0xFF), "the complements are not complements"
    return {"D": d, "F": f}


def decode_tdc(frame: list[int]):
    """Biphase (a 1 is a mark then a space, a 0 a space then a mark), a one-unit mark and space to start, then D:5,
    S:5 and F:7 most significant first. The unit is the mean of the durations that are one unit (the shortest, within
    40 %), a duration is that many half-bits, and the frame's last space is the gap, which swallows the last bit's
    space when that bit is a 1."""
    body = frame[:-1]
    shortest = min(body)
    unit = statistics.mean(d for d in body if d < 1.4 * shortest)
    halves: list[int] = []
    for i, d in enumerate(body):
        run = round(d / unit)
        assert run in (1, 2), "a duration that is neither one half-bit nor two"
        halves += [1 - i % 2] * run                             # even index: a mark (1), odd: a space (0)
    if len(halves) == 35:                                       # the last bit's space is in the gap
        halves.append(0)
    assert len(halves) == 36, f"{len(halves)} half-bits, not the start and 17 bits"
    assert halves[:2] == [1, 0], "no one-unit mark and space to start"
    word = 0
    for hi, lo in zip(halves[2::2], halves[3::2]):
        assert (hi, lo) in ((1, 0), (0, 1)), "a bit that is not biphase"
        word = word << 1 | hi
    return {"D": word >> 12, "S": word >> 7 & 0x1F, "F": word & 0x7F}


def decode_gicable(frame: list[int]):
    """An 18-unit mark and a 9-unit space, then 16 bits least significant first (F:8, D:4, C:4; a one-unit mark and a
    space of 4.5 units for a 0 and 9 for a 1), the stop mark and the gap. The check nibble is -(D + F:4 + F:4:4)."""
    pairs = list(zip(frame[0::2], frame[1::2]))
    assert len(pairs) >= 18, "too short for a G.I.Cable intro"
    lead, body = pairs[0], pairs[1:17]
    unit = statistics.median(m for m, _ in body)
    assert 15 < lead[0] / unit < 21 and 7 < lead[1] / unit < 11, "no 18,9 lead-in"
    bits = [int(s > 6.75 * unit) for _, s in body]            # 4.5 and 9 units: 6.75 is the midpoint
    f, d, c = _lsb(bits[:8]), _lsb(bits[8:12]), _lsb(bits[12:16])
    assert c == (-(d + (f & 15) + (f >> 4))) & 15, "the check nibble is not -(D + F:4 + F:4:4)"
    return {"D": d, "F": f}


def encode_like(proto: str, p: dict[str, int], carrier: int):
    entry = REGISTRY[proto]
    if proto == "Nokia32":
        return entry.encode(device=p["D"], subdevice=p["S"], function=(p["X"] << 8) | p["F"], carrier_hz=carrier,
                            toggle=p["T"])
    if proto in ("Panasonic_Old", "RCA", "GI_Cable"):
        return entry.encode(device=p["D"], subdevice=None, function=p["F"], carrier_hz=carrier)
    if proto in ("NEC1-f16", "NEC2-f16"):
        return entry.encode(device=p["D"], subdevice=p["S"], function=(p["F"] << 8) | p["E"],
                            carrier_hz=carrier)
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
    ("NEC-f16", "NEC1-f16", decode_nec_family),
    ("NEC1-y1_nofinalA", "NEC1-f16", decode_nec_family),
    ("NEC1-y1_nofinal_B", "NEC1-f16", decode_nec_family),
    ("NEC2-f16", "NEC2-f16", decode_nec_family),
    ("NEC2-rnc", "NEC2-f16", decode_nec_family),
    ("Nokia32", "Nokia32", decode_nokia32),
    ("Panasonic_Old", "Panasonic_Old", decode_panasonic_old),
    ("RCA_Old", "RCA", decode_rca),
    ("RCA-38", "RCA", decode_rca),
    ("TDC-38_6_10", "TDC-38", decode_tdc),
    ("TDC-56_14_10", "TDC-56", decode_tdc),
    ("GI_Cable", "GI_Cable", decode_gicable),
)

#: The gap that ends a frame where the default would run through the repeats (RCA's is 8 ms).
FRAME_GAPS = {"RCA_Old": 6_000, "RCA-38": 6_000}
#: The durations compared with the encoder's where the capture's frame is not the registered one's all through: RCA(Old)'s
#: lead-in and stop mark are longer, so only its 48 durations of bits (the two of the lead-in and one pair of the stop
#: mark left out) are.
SPANS = {"RCA_Old": (2, 50)}


def the_signal_frame(proto: str, signal) -> tuple[int, ...]:
    """The sequence that holds the first frame: NEC1-f16 and GI_Cable send it as the intro and the repeaters
    as the repeat."""
    return signal.intro if proto in ("NEC1-f16", "GI_Cable") else signal.repeat


def audit_pronto_panasonic_old(data: Path) -> int:
    """The sixteen Pronto strings of ``Panasonic_Old_Pronto.txt`` (a cable box's remote), each decoded from its words
    at the carrier its own frequency word gives, against the ``.exp`` file's decode (D=27 and a function each)."""
    expected = read_expected(data / "Panasonic_Old_Pronto.exp")
    label, strings = None, {}
    for line in (data / "Panasonic_Old_Pronto.txt").read_text().splitlines():
        line = line.strip()
        if re.fullmatch(r"(?:[0-9A-Fa-f]{4} ?)+", line) and label is not None:
            strings[label] = line.split()
        elif line:
            label = line
    failures = ok = 0
    ratios = []
    for key, words in strings.items():
        period = int(words[1], 16) * 0.241246               # a Pronto cycle, in microseconds
        burst = [int(w, 16) * period for w in words[4:]]
        fields = decode_panasonic_old([round(x) for x in burst])
        want = {k: v for k, v in expected[key].items() if k in fields}
        if fields != want:
            failures += 1
            print(f"  FAIL Panasonic_Old_Pronto/{key}: string reads {fields}, .exp says {want}")
            continue
        ours = encode_like("Panasonic_Old", fields, round(1e6 / period)).repeat
        ok += 1
        ratios += [b / a for a, b in zip(ours[:-1], burst[:-1])]
    print(f"Panasonic_Old_Pronto (Panasonic_Old): {ok}/{len(strings)} strings decode to the .exp fields "
          f"(D {sorted({expected[k]['D'] for k in strings})})")
    if ratios:
        print(f"  string / encoder, every duration but the lead-out: median {statistics.median(ratios):.3f}, "
              f"min {min(ratios):.3f}, max {max(ratios):.3f}")
    return failures


def audit(root: Path) -> int:
    data = root / "src" / "test" / "teaserfiles"
    failures = audit_pronto_panasonic_old(data)
    for stem, proto, decode in CASES:
        captures = load_ict(data / f"{stem}.ict")
        expected = read_expected(data / f"{stem}.exp")
        marks, spaces, gaps, ratios, carriers = [], [], [], [], []
        ok = 0
        skipped = [key for key in captures if key not in expected]
        for key, (carrier, signed) in captures.items():
            if key in skipped:
                # IrpTransmogrifier's own test has no decode for it (a glitched capture: NEC2-f16's `Left`)
                continue
            frame = first_frame(signed, FRAME_GAPS.get(stem, FRAME_GAP_US))
            fields = decode(frame)
            if decode is decode_nec_family:
                want = nec_expected(expected[key])
            elif decode is decode_nokia32:
                want = {"D": 0, "S": 0, "F": 0, "X": 0, "T": 0} | {k: v for k, v in expected[key].items() if k in fields}
            else:
                want = {k: v for k, v in expected[key].items() if k in fields}
            if fields != want:
                failures += 1
                print(f"  FAIL {stem}/{key}: capture reads {fields}, .exp says {want}")
                continue
            ours = the_signal_frame(proto, encode_like(proto, fields, carrier))
            ours = ours[:len(frame)]
            if len(ours) != len(frame):
                failures += 1
                print(f"  FAIL {stem}/{key}: encoder has {len(ours)} durations, "
                      f"capture {len(frame)}")
                continue
            if decode(list(ours)) != fields:
                failures += 1
                print(f"  FAIL {stem}/{key}: the encoder's own frame reads {decode(list(ours))}, not {fields}")
                continue
            ok += 1
            carriers.append(carrier)
            # Compare every duration but the lead-out, which a capture
            # truncates or pads at will.
            lo, hi = SPANS.get(stem, (0, len(frame) - 1))
            for i, (a, b) in enumerate(zip(ours[lo:hi], frame[lo:hi])):
                ratios.append(b / a)
            marks += [b for i, b in enumerate(frame[:-1]) if i % 2 == 0]
            spaces += [b for i, b in enumerate(frame[:-1]) if i % 2 == 1]
            if frame[-1] < 200_000:       # not the capture's last, truncated frame
                gaps.append((sum(ours), sum(frame)))
        print(f"{stem} ({proto}): {ok}/{len(captures) - len(skipped)} keys decode to the .exp "
              f"fields and re-encode to the same bit pattern"
              + (f" ({len(skipped)} capture without a decode left out)" if skipped else ""))
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
