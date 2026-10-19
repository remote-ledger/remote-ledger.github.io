#!/usr/bin/env python3
"""Pin the CC0 files of Flipper-IRDB into ``sources/flipper-irdb/`` (DESIGN section 96, D353).

``Lucaslhm/Flipper-IRDB`` is CC0-1.0 **from the commit that added its LICENSE and the README's "by submitting you agree to
license your work under CC0"** (2025-08-07, PR #960); a file that was submitted before it has no licence of its own, and SPEC
section 4 excludes it. This tool reads a full-history checkout and keeps the files that are the contribution of a later
commit:

* the commit that added the path is after the licence commit (not one of its ancestors) **and its author date is not
  before it**: a pull request written before the licence and merged after is not covered;
* the file's content, a git blob, is **in no commit up to the licence commit or authored before it**: a file that was moved or
  copied from an older one is that older file.

It copies each file as the checkout's ``HEAD`` has it, and the ``LICENSE``, and writes ``MANIFEST.json``: the upstream URL and commit, the licence
commit, and for every file its SHA-256, the commit that added it and that commit's date; and the files it did not keep, with
the reason. ``rl import flipper-irdb sources/flipper-irdb`` reads the result and nothing else.

    python3 tools/fetch_flipper.py <checkout of Lucaslhm/Flipper-IRDB with its history> [--out sources/flipper-irdb]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

UPSTREAM = "https://github.com/Lucaslhm/Flipper-IRDB"
LICENCE_NAME = "CC0-1.0"
LICENCE_FILE = "LICENSE"
LICENCE_PR = re.compile(r"\(#(\d+)\)\s*$")


def git(checkout: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True).stdout


def licence_commit(checkout: Path) -> tuple[str, int, str, str]:
    """``(commit, author time, author date, subject)`` of the commit that first added the LICENSE."""
    rows = [r for r in git(checkout, "log", "--diff-filter=A", "--format=%H\t%at\t%aI\t%s", "--", LICENCE_FILE).splitlines() if r]
    if not rows:
        raise SystemExit(f"{checkout} has no history of a {LICENCE_FILE} file: is it a full clone?")
    sha, at, date, subject = rows[-1].split("\t", 3)
    return sha, int(at), date, subject


def earlier_blobs(checkout: Path, licence: str, licence_at: int) -> set[str]:
    """Every blob a ``.ir`` file had in a commit that is up to the licence commit **or was authored before it** (a pull request written
    earlier and merged later holds files of the time before the licence: a copy of one of them is not a later contribution)."""
    out = git(checkout, "log", "HEAD", "--format=C\t%H\t%at", "--raw", "--no-abbrev", "--no-renames", "--diff-filter=AM", "--", "*.ir")
    ancestors = set(git(checkout, "rev-list", licence).split())
    blobs: set[str] = set()
    keep = False
    for line in out.splitlines():
        if line.startswith("C\t"):
            _, sha, at = line.split("\t")
            keep = sha in ancestors or int(at) < licence_at
        elif line.startswith(":") and keep:
            blobs.add(line.split()[3])
    return blobs


def additions(checkout: Path, licence: str) -> tuple[dict[str, dict], dict[str, str]]:
    """``({path: {commit, at, date, blob}}, {new path: old path})``: the first commit after ``licence`` that added each ``.ir`` path, and the
    renames (git's detection, 50% similar or more) of those commits: a renamed file is the file it was."""
    out = git(checkout, "log", f"{licence}..HEAD", "--format=C\t%H\t%at\t%aI", "--raw", "--no-abbrev", "-M", "-l0", "--diff-filter=AR", "--", "*.ir")
    found: dict[str, dict] = {}
    renames: dict[str, str] = {}
    commit: tuple[str, int, str] | None = None
    for line in out.splitlines():
        if line.startswith("C\t"):
            _, sha, at, date = line.split("\t")
            commit = (sha, int(at), date)
        elif line.startswith(":") and commit is not None:
            meta, *paths = line.split("\t")
            if meta.split()[4][0] == "R":
                renames[paths[1]] = paths[0]
            else:
                # git lists newest first: the last one seen is the first to add the path
                found[paths[0]] = {"commit": commit[0], "at": commit[1], "date": commit[2], "blob": meta.split()[3]}
    return found, renames


def origin(path: str, renames: dict[str, str]) -> str:
    seen = {path}
    while path in renames and renames[path] not in seen:
        path = renames[path]
        seen.add(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("checkout")
    parser.add_argument("--out", default="sources/flipper-irdb")
    args = parser.parse_args(argv)
    checkout, out = Path(args.checkout), Path(args.out)
    head = git(checkout, "rev-parse", "HEAD").strip()
    if git(checkout, "status", "--porcelain").strip():
        raise SystemExit(f"{checkout} has changes; the pin is HEAD's files")
    licence, licence_at, licence_date, subject = licence_commit(checkout)
    pr = LICENCE_PR.search(subject)
    if not pr:
        raise SystemExit(f"the commit that added the {LICENCE_FILE} says {subject!r}, which names no pull request")
    old = earlier_blobs(checkout, licence, licence_at)
    at_head = {line.split("\t", 1)[1]: line.split()[2] for line in git(checkout, "ls-tree", "-r", "HEAD").splitlines()
               if line.endswith(".ir")}
    kept: dict[str, dict] = {}
    excluded: list[dict] = []
    added, renames = additions(checkout, licence)
    for path in sorted(p for p in at_head if p in added or p in renames):
        root = origin(path, renames)
        add = added.get(root)
        why = None
        if add is None:
            why = "a rename of a file of the time before the licence commit"
        elif add["at"] < licence_at:
            why = f"written {add['date'][:10]} (commit {add['commit'][:7]}), before the licence commit {licence[:7]} of {licence_date[:10]}, and merged after it"
        elif add["blob"] in old or at_head[path] in old:
            why = "its content is that of a file of the time before the licence commit (moved or copied)"
        if why:
            excluded.append({"path": path, "reason": why})
            continue
        kept[path] = add
    if out.exists():
        shutil.rmtree(out)
    files: dict[str, dict] = {}
    for path, add in kept.items():
        data = subprocess.run(["git", "-C", str(checkout), "show", f"HEAD:{path}"], check=True, capture_output=True).stdout
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[path] = {"sha256": hashlib.sha256(data).hexdigest(), "added": add["commit"], "date": add["date"][:10]}
    licence_text = (checkout / LICENCE_FILE).read_bytes()
    (out / LICENCE_FILE).write_bytes(licence_text)
    manifest = {
        "upstream": UPSTREAM,
        "commit": head,
        "licence": {
            "name": LICENCE_NAME,
            "pullRequest": int(pr.group(1)),
            "commit": licence,
            "date": licence_date[:10],
            "fileSha256": hashlib.sha256(licence_text).hexdigest(),
            "statement": "the repository's LICENSE (CC0 1.0 Universal) and the README's note that a contributor agrees to license their work under CC0, both added by that commit",
        },
        "rule": ("a file is kept when the commit that added its path is after the licence commit and not authored before it, and its content "
                 "is in no commit up to the licence commit or authored before it"),
        "files": files,
        "excluded": excluded,
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(files)} files kept, {len(excluded)} not (HEAD {head[:7]}, licence {licence[:7]} of {licence_date[:10]})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
