#!/usr/bin/env python3
"""Check the Flipper mapping (``remote_ledger.flipper.mapping``) against the firmware's own captures (DESIGN D354).

``flipperdevices/flipperzero-firmware`` keeps, for each protocol its decoders read, real captures and the messages its decoder
gave for them (``applications/debug/unit_tests/resources/unit_tests/infrared/test_*.irtest``: ``decoder_input<N>`` is a raw
capture, ``decoder_expected<N>`` the parsed messages). For every distinct message this tool maps it to the ledger's protocol,
compiles it and compares the compiled frame with the frames of the capture: every duration within 30% (and at least 250 us)
of the capture's, the gap that ends a frame left out (a gap of 8 ms or more ends a frame: SIRC's is 10 ms). RC-5 and RC-6 captures carry a
toggle bit the ledger's files leave at 0 (D3b), so either state of it may match. It prints what matched and what did not, and exits 1 on a mismatch.
The firmware is GPL-3.0 and is not part of this repository: give it a checkout.

    python3 tools/flipper_capture_audit.py <flipperzero-firmware checkout>
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.flipper import ir, mapping  # noqa: E402
from remote_ledger.flipper.importer import block  # noqa: E402
from remote_ledger.protocols import rc5, rc6  # noqa: E402
from remote_ledger.remote import remote_from_doc  # noqa: E402
from remote_ledger.forms import select  # noqa: E402

TESTS = "applications/debug/unit_tests/resources/unit_tests/infrared"
#: a gap that ends a frame in a capture
FRAME_GAP_US = 8_000


def read_tests(text: str) -> dict[str, dict]:
    """``{section name: {"type": ..., "data": [...], "messages": [{protocol, address, command, repeat}]}}``."""
    sections: dict[str, dict] = {}
    current: dict | None = None
    message: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line == "#":
            message = None
            continue
        if line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "name":
            current = sections.setdefault(value, {"messages": []})
            message = None
        elif current is None:
            continue
        elif key == "protocol":
            message = {"protocol": value}
            current["messages"].append(message)
        elif key in ("address", "command", "repeat") and message is not None:
            message[key] = value
        elif key == "data":
            current["data"] = [int(v) for v in value.split()]
        else:
            current[key] = value
    return sections


def frames(durations: list[int]) -> list[list[int]]:
    """A capture's frames: the durations between gaps of FRAME_GAP_US or more, the first number (a gap) dropped."""
    out: list[list[int]] = []
    cur: list[int] = []
    for i, d in enumerate(durations[1:], 1):
        if i % 2 == 0 and d >= FRAME_GAP_US:  # a space
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(d)
    if cur:
        out.append(cur)
    return out


def close(a: int, b: int) -> bool:
    return abs(a - b) <= max(250, 0.3 * b)


def matches(compiled: tuple[int, ...], frame: list[int]) -> bool:
    """Does the compiled signal's first frame (without its closing gap) agree with the capture's frame?"""
    want = list(compiled)
    while len(want) % 2 == 0 and want and want[-1] >= FRAME_GAP_US:
        want = want[:-1]
    got = list(frame)
    if len(got) % 2 == 0:  # a capture that ends on a space
        got = got[:-1]
    return len(got) == len(want) and all(close(a, b) for a, b in zip(got, want))


def first_frame(signal) -> tuple[int, ...]:
    """The signal's first frame: the intro's, or the repeat's when there is none, up to its first gap."""
    seq = list(signal.intro or signal.repeat)
    out: list[int] = []
    for i, d in enumerate(seq):
        if i % 2 == 1 and d >= FRAME_GAP_US:
            out.append(d)
            break
        out.append(d)
    # Pioneer-2Part sends two frames: the first only
    return tuple(out)


def compile_message(message: dict):
    """``(mapped, [signals])``: the ledger's reading and its compiled signal, with the toggled one too for RC-5 and RC-6."""
    entry = ir.Entry("t", "parsed", protocol=message["protocol"],
                     address=tuple(int(x, 16) for x in message["address"].split()),
                     command=tuple(int(x, 16) for x in message["command"].split()))
    mapped = mapping.signal_of(entry)
    if isinstance(mapped, str):
        return mapped, []
    form = {"id": "primary.irp", "type": "irp", "device": mapped.device, "function": mapped.function, "confidence": "plausible", "source": "audit"}
    if mapped.subdevice is not None:
        form["subdevice"] = mapped.subdevice
    doc = {"manufacturer": "audit", "model": "audit", "protocol": block(mapped.protocol), "keys": {"K": {"forms": [form]}}}
    remote = remote_from_doc(doc, Path("audit.json"))
    signals = [remote.render(select(remote.groups("K")["primary"]))]
    if mapped.protocol in ("RC5", "RC6"):
        module = {"RC5": rc5, "RC6": rc6}[mapped.protocol]
        signals.append(module.encode(device=mapped.device, subdevice=None, function=mapped.function,
                                     carrier_hz=signals[0].carrier_hz, toggle=1))
    return mapped, signals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("firmware")
    args = parser.parse_args(argv)
    root = Path(args.firmware) / TESTS
    totals: Counter = Counter()
    bad = 0
    for path in sorted(root.glob("test_*.irtest")):
        sections = read_tests(path.read_text(encoding="utf-8"))
        for name, section in sorted(sections.items()):
            if not name.startswith("decoder_input") or "data" not in section:
                continue
            expected = sections.get(name.replace("input", "expected"), {}).get("messages", [])
            caps = frames(section["data"])
            seen: set[tuple] = set()
            for message in expected:
                if message.get("repeat") == "true":
                    continue
                key = (message["protocol"], message["address"], message["command"])
                if key in seen:
                    continue
                seen.add(key)
                mapped, signals = compile_message(message)
                if not signals:
                    totals[f"{message['protocol']}: not mapped ({mapped})"] += 1
                    continue
                ok = any(matches(first_frame(signal), f) for signal in signals for f in caps)
                totals[f"{message['protocol']} -> {mapped.protocol}: {'matches' if ok else 'DOES NOT MATCH'}"] += 1
                if not ok:
                    bad += 1
                    print(f"{path.name} {name}: {key} -> {mapped.protocol} {mapped.device} {mapped.subdevice} {mapped.function}; "
                          f"compiled {list(first_frame(signals[0]))[:12]}... capture frames {[f[:12] for f in caps[:2]]}", file=sys.stderr)
    for line, n in sorted(totals.items()):
        print(f"{n:4d}  {line}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
