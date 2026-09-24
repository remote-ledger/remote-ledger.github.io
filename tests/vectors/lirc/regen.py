#!/usr/bin/env python3
"""Regenerate the lircd oracle vectors in this directory.

    python tests/vectors/lirc/regen.py --lirc-build /path/to/lirc-0.10.2

For every entry of ``index.json`` this runs, from an empty scratch
directory, exactly::

    LD_LIBRARY_PATH=$B/lib/.libs $B/tools/.libs/irsimsend \\
        -U $B/plugins/.libs -c <count> [-k <keysym>] <conf>

and stores irsimsend's stdout as ``<id>.stdout``, its ``simsend.out`` as
``<id>.out``, and in ``oracle.json`` its exit status and the ``Error:``
messages it logged (``XDG_CACHE_HOME`` points its log into the scratch
directory). An entry with
``"keep_min_repeat": true`` runs under the ``LD_PRELOAD`` shim from
``tools/lirc_oracle_compare.py`` with ``LIRC_ORACLE_KEEP_MIN_REPEAT=1``,
which restores each remote's parsed ``min_repeat`` before every send; no
other entry uses the shim. See README.md for building ``$B``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from lirc_oracle_compare import build_shim  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lirc-build", default=os.environ.get("LIRC_BUILD"), required=False)
    args = ap.parse_args()
    if not args.lirc_build:
        ap.error("--lirc-build (or $LIRC_BUILD) is required")
    build = Path(args.lirc_build).resolve()
    index = json.loads((HERE / "index.json").read_text())
    statuses, errors = {}, {}
    with tempfile.TemporaryDirectory(prefix="lircvec-") as tmp:
        shim = build_shim(Path(tmp), build)
        for vec in index["vectors"]:
            cmd = [str(build / "tools" / ".libs" / "irsimsend"),
                   "-U", str(build / "plugins" / ".libs"), "-c", str(vec["count"])]
            if "keysym" in vec:
                cmd += ["-k", vec["keysym"]]
            cmd.append(str(HERE / vec["conf"]))
            env = {k: v for k, v in os.environ.items() if not k.startswith("LIRC_ORACLE_")}
            env["LD_LIBRARY_PATH"] = str(build / "lib" / ".libs")
            env.pop("LD_PRELOAD", None)
            if vec.get("keep_min_repeat"):
                env["LD_PRELOAD"] = str(shim)
                env["LIRC_ORACLE_KEEP_MIN_REPEAT"] = "1"
            with tempfile.TemporaryDirectory(prefix="run-", dir=tmp) as cwd:
                # lirc_log.c:339: the client log goes to $XDG_CACHE_HOME
                env["XDG_CACHE_HOME"] = cwd
                proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                                      timeout=60)
                out = Path(cwd, "simsend.out")
                (HERE / f"{vec['id']}.out").write_bytes(
                    out.read_bytes() if out.exists() else b"")
                log = Path(cwd, "irsimsend.log")
                log_text = log.read_text(errors="replace") if log.exists() else ""
            (HERE / f"{vec['id']}.stdout").write_bytes(proc.stdout)
            statuses[vec["id"]] = proc.returncode
            errors[vec["id"]] = [  # paths made relative to this directory
                line.split(" irsimsend: Error: ", 1)[1].replace(f"{HERE}/", "")
                for line in log_text.splitlines() if " irsimsend: Error: " in line]
            print(f"{vec['id']}: exit {proc.returncode}")
    (HERE / "oracle.json").write_text(json.dumps(
        {"exit_status": statuses, "log_errors": errors}, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
