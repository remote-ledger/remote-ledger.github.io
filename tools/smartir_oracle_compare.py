#!/usr/bin/env python3
"""Compare :mod:`remote_ledger.smartir.broadlink` against a reference decoder.

The oracle is ``data_to_pulses`` from the ``broadlink`` PyPI package
(MIT-licensed), the community's own decoder for this format -- not
installed as a project dependency, only used here, once, to check this
module's decode against an independent implementation, the same role
``tools/lirc_oracle_compare.py`` plays for the lircd port.

For every ``Broadlink``/``Base64`` command in a SmartIR checkout's
``codes/media_player/`` and ``codes/fan/`` (the categories this project
imports, DESIGN.md section 15), decodes it with both and reports any
mismatch. Usage::

    pip install broadlink
    python tools/smartir_oracle_compare.py --corpus DIR

Exit status is 0 when there are no mismatches, 1 otherwise.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.smartir.broadlink import BroadlinkDecodeError, decode_packet  # noqa: E402
from remote_ledger.smartir.codes import parse_profile  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="a SmartIR checkout")
    args = parser.parse_args()

    try:
        from broadlink.remote import data_to_pulses
    except ImportError:
        print("ERROR: pip install broadlink first (the reference decoder)", file=sys.stderr)
        return 2

    corpus = Path(args.corpus)
    compared = mismatches = errors = skipped = 0
    for category in ("media_player", "fan"):
        for path in sorted((corpus / "codes" / category).glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                profile = parse_profile(category, path.stem, doc)
            except Exception:
                skipped += 1
                continue
            if profile.controller != "Broadlink" or profile.encoding != "Base64":
                continue
            for command in profile.commands:
                compared += 1
                try:
                    mine = decode_packet(command.code)
                except BroadlinkDecodeError as exc:
                    errors += 1
                    print(f"DECODE ERROR {path}#{'.'.join(command.path)}: {exc}")
                    continue
                ref = data_to_pulses(base64.b64decode(command.code))
                if mine != ref:
                    mismatches += 1
                    print(f"MISMATCH {path}#{'.'.join(command.path)}")
                    print(f"  ours: {mine}")
                    print(f"  ref:  {ref}")

    print(f"\n{compared:,} commands compared, {skipped:,} files skipped "
          f"(parse errors), {errors:,} decode errors, {mismatches:,} mismatches")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
