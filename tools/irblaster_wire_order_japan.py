#!/usr/bin/env python3
"""Which reading of a SwiftRemote Pioneer/JVC/Sharp/Denon code matches real remotes?

``remote_ledger.irblaster.hex_japan`` has two readings of the database's
hexcodes: ``FROM_DB_HEX``, which is what SwiftRemote's code does with them,
and ``FROM_DB_HEX_WIRE``, which treats each code as the bit string that goes on
the wire, first bit most significant. This tool tests both against an
independent source of real waveforms.

The source is this repository's own LIRC import (``remotes/lirc``), whose raw
forms are what lircd sends for a conf contributed from a real remote. The
database and the import share **models**: ``Denon RC-129A``, ``JVC RM-C360``,
``Sharp G1071SA``, ``Pioneer CU-XR015`` and so on, matched here by brand and by
model name with case and punctuation ignored. For every database key of such a
model it asks whether the first frame's data bits appear among the frames of
that model's LIRC conf, under

* the app's reading: the data bits of the app's own pattern (``--oracle``);
* the wire reading: the data bits of the ledger's signal for the code.

A match is the whole data field (16 bits for JVC, 32 for Pioneer, 15 for Sharp
and Denon), not a prefix. A key counts as ``neither`` when its model's conf has
no such frame, which is mostly a key the conf does not have, so the figure to
read is how the two readings split the keys that *do* match.

Usage::

    python tools/irblaster_wire_order_japan.py --db assets/db/swiftremote.sqlite \\
        --oracle DIR [--corpus remotes/lirc]

Run over the 4 families, models shared with remotes/lirc @ the repo's import
(lirc-remotes @ 291b40f), the result in NOTES/japan.md.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from remote_ledger.irblaster import hex_japan  # noqa: E402
from remote_ledger.protocols import REGISTRY  # noqa: E402

GAP_US = 8_000
#: DB protocol -> (LIRC brand directory, space threshold between a 0 and a 1,
#: lead-in pairs before the data, data bits)
FAMILY = {
    "Pioneer": ("pioneer", 1000, 1, 32),
    "JVC": ("jvc", 1000, 1, 16),
    "Sharp": ("sharp", 1200, 0, 15),
    "Denon": ("denon", 1200, 0, 15),
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _frames(durations: list[int]) -> list[list[tuple[int, int]]]:
    pairs = list(zip(durations[0::2], durations[1::2]))
    frames, current = [], []
    for mark, space in pairs:
        current.append((mark, space))
        if space >= GAP_US:
            frames.append(current)
            current = []
    return frames + ([current] if current else [])


def _data_bits(frame, threshold: int, lead: int, count: int) -> str | None:
    """The data field of one frame: after ``lead`` pairs, before the stop pair."""
    body = frame[lead:-1]
    if len(body) != count:
        return None
    return "".join("1" if space > threshold else "0" for _, space in body)


def _lirc_frames(path: Path, threshold: int, lead: int, count: int) -> set[str]:
    out: set[str] = set()
    for spec in json.loads(path.read_text())["keys"].values():
        for form in spec["forms"]:
            if form["type"] != "raw":
                continue
            for sequence in ("intro", "repeat"):
                for frame in _frames(form.get(sequence) or []):
                    bits = _data_bits(frame, threshold, lead, count)
                    if bits:
                        out.add(bits)
    return out


def study(db: sqlite3.Connection, oracle: Path, corpus: Path, protocol: str) -> dict:
    brand, threshold, lead, count = FAMILY[protocol]
    app_rows = {}
    for line in (oracle / "by_protocol" / f"{protocol}.jsonl").read_text().splitlines():
        row = json.loads(line)
        app_rows[row["hex"]] = row
    confs = {_norm(p.stem): p for p in (corpus / brand).glob("*.json")}
    seen: set[tuple[str, int]] = set()
    tally: collections.Counter = collections.Counter()
    for model, remote_id in db.execute(
        "SELECT DISTINCT m.model, m.id FROM keys k JOIN models m ON m.id = k.id "
        "WHERE k.protocol = ? AND lower(m.brand) = ?", (protocol, brand),
    ):
        key = _norm(model)
        if key not in confs or (key, remote_id) in seen:
            continue
        seen.add((key, remote_id))
        real = _lirc_frames(confs[key], threshold, lead, count)
        for (hexcode,) in db.execute(
            "SELECT hexcode FROM keys WHERE id = ? AND protocol = ?", (remote_id, protocol)
        ):
            app_frame = _frames(app_rows[hexcode]["pattern"])[0]
            name, device, subdevice, function = hex_japan.FROM_DB_HEX_WIRE[protocol](hexcode)
            signal = REGISTRY[name].encode(
                device=device, subdevice=subdevice, function=function, carrier_hz=38_000
            )
            wire_frame = _frames(list(signal.intro))[0]
            in_app = _data_bits(app_frame, threshold, lead, count) in real
            in_wire = _data_bits(wire_frame, threshold, lead, count) in real
            tally[("app" if in_app else "-") + "+" + ("wire" if in_wire else "-")] += 1
    return {
        "models": len(seen), "keys": sum(tally.values()),
        **{k: tally[k] for k in ("app+-", "-+wire", "app+wire", "-+-")},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", required=True, help="swiftremote.sqlite")
    parser.add_argument("--oracle", required=True, help="directory holding by_protocol/")
    parser.add_argument("--corpus", default=str(ROOT / "remotes" / "lirc"))
    args = parser.parse_args(argv)
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    print(f"{'DB protocol':<10} {'models':>6} {'keys':>6} {'app only':>9} {'wire only':>10} "
          f"{'both':>6} {'neither':>8}")
    for protocol in FAMILY:
        r = study(db, Path(args.oracle), Path(args.corpus), protocol)
        print(f"{protocol:<10} {r['models']:>6} {r['keys']:>6} {r['app+-']:>9} {r['-+wire']:>10} "
              f"{r['app+wire']:>6} {r['-+-']:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
