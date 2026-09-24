#!/usr/bin/env python3
"""Compare the lircd port (``remote_ledger.lirc``) against lircd itself.

The oracle is ``irsimsend`` from the lirc 0.10.2 release tarball (sha256
``3d44ec8274881cf262f160805641f0827ffcc20ade0d85e7e6f3b90e0d3d222a``), built
in place, with one local patch to ``plugins/file.c``: after the final
``space <min_remaining_gap>`` line of each send, ``send_func`` also writes
``# end``. See ``tests/vectors/lirc/README.md`` for the build.

For every conf file this script

1. runs ``irsimsend -U <build>/plugins/.libs -c <count> <file>`` from an
   empty scratch directory, with a small ``LD_PRELOAD`` shim (``SHIM_C``,
   compiled here with ``cc``) that wraps ``puts`` -- which irsimsend uses to print each
   button name before sending it -- so that a ``#code <name>`` marker lands
   in ``simsend.out`` ahead of that button's output. That changes no output;
   it only makes the file splittable per button, including buttons whose
   send fails and writes nothing. The shim has two opt-in switches, each
   mirrored on the port's side: it can ignore the ``SIGUSR1`` the file
   driver raises on a ``LIRC_EOF`` value (on by default here, so every
   button is compared; ``--exit-on-eof`` turns it off), and it can restore
   each remote's parsed ``min_repeat`` before every send
   (``--keep-min-repeat``), reaching lircd's ``repeat_countdown`` path that
   irsimsend's zeroing otherwise hides;
2. parses the file with :func:`remote_ledger.lirc.parse_file` and replays
   the same session with :class:`remote_ledger.lirc.IrSimSend`;
3. compares the two button by button, line by line.

Usage::

    python tools/lirc_oracle_compare.py --lirc-build DIR --corpus DIR
    python tools/lirc_oracle_compare.py --lirc-build DIR a.conf b.conf

``--lirc-build`` (or ``$LIRC_BUILD``) is the built ``lirc-0.10.2`` tree;
``--corpus`` (or ``$LIRC_CORPUS``) a checkout of
``git.code.sf.net/p/lirc-remotes/code``, whose ``remotes/**/*.conf`` are
compared when no files are named. ``--json FILE`` writes the full report.
Exit status is 0 when there are no mismatches, 1 otherwise.

The report counts: files, and how each ended (compared; rejected by both
parsers; ...); buttons compared, split by what lircd emitted (timings,
``code N`` scancode lines, nothing because the send failed); exact matches;
mismatches with the first differing line and both values; over the remote
blocks both sides accepted, how many use each feature; and how often each
non-obvious send-path rule fired (the port's ``Lircd.trace``).

Last full run, over lirc-remotes @ 291b40f436a4a17cc72b24c8eb411f6213ee88e5
(2,815 ``*.conf`` files), ``-c 2``: 2,795 files compared, 19 with no remote
block (lircmd configs) and 1 rejected by both (a JPEG named ``.conf``);
124,498 buttons compared, 124,498 exact matches, 0 mismatches -- identically
with ``--keep-min-repeat``; with ``--exit-on-eof`` 119,912 compared and
matched, the other 4,586 never sent because irsimsend died first.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from remote_ledger.lirc import (  # noqa: E402
    IrSimSend,
    LircConfigError,
    LircUndefinedBehaviour,
    parse_file,
)
from remote_ledger.lirc import conf as C  # noqa: E402

MARK = b"#code "

SHIM_C = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "ir_remote_types.h" /* <build>/lib: struct ir_remote */

/* file.c raises SIGUSR1 after writing any value with the LIRC_EOF bit, which
 * kills irsimsend. With LIRC_ORACLE_IGNORE_SIGUSR1 set it is ignored instead,
 * so the buttons after it are still sent -- and the port is told the same. */
__attribute__((constructor)) static void maybe_ignore_sigusr1(void)
{
	if (getenv("LIRC_ORACLE_IGNORE_SIGUSR1"))
		signal(SIGUSR1, SIG_IGN);
}

/* irsimsend prints each button name with printf("%s\n"), which gcc emits
 * as puts(). With LIRC_ORACLE_MARK set, write a marker to simsend.out first;
 * file.c opens it O_APPEND, so the marker lands exactly before that button's
 * output. */
int puts(const char *s)
{
	static int (*real)(const char *);
	int fd;

	if (!real)
		real = (int (*)(const char *))dlsym(RTLD_NEXT, "puts");
	if (!getenv("LIRC_ORACLE_MARK"))
		return real(s);
	fd = open("simsend.out", O_WRONLY | O_APPEND | O_CREAT, 0666);
	if (fd >= 0) {
		(void)!write(fd, "#code ", 6);
		(void)!write(fd, s, strlen(s));
		(void)!write(fd, "\n", 1);
		close(fd);
	}
	return real(s);
}

/* irsimsend zeroes every remote's min_repeat before sending (irsimsend.cpp:
 * 168,194), so lircd's repeat_countdown path never runs under it. With
 * LIRC_ORACLE_KEEP_MIN_REPEAT set, each send_ir_ncode() call first restores
 * the value read_config() parsed, which is what lircd itself sends with. */
#define MAX_REMOTES 8192
static struct {
	const struct ir_remote *r;
	int min_repeat;
} saved[MAX_REMOTES];
static int nsaved;

struct ir_remote *read_config(FILE *f, const char *name)
{
	static struct ir_remote *(*real)(FILE *, const char *);
	struct ir_remote *head, *r;

	if (!real)
		real = (struct ir_remote *(*)(FILE *, const char *))
			dlsym(RTLD_NEXT, "read_config");
	head = real(f, name);
	for (r = head; r && r != (void *)-1 && nsaved < MAX_REMOTES; r = r->next) {
		saved[nsaved].r = r;
		saved[nsaved].min_repeat = r->min_repeat;
		nsaved++;
	}
	return head;
}

int send_ir_ncode(struct ir_remote *remote, struct ir_ncode *code, int delay)
{
	static int (*real)(struct ir_remote *, struct ir_ncode *, int);
	int i;

	if (!real)
		real = (int (*)(struct ir_remote *, struct ir_ncode *, int))
			dlsym(RTLD_NEXT, "send_ir_ncode");
	if (getenv("LIRC_ORACLE_KEEP_MIN_REPEAT"))
		for (i = 0; i < nsaved; i++)
			if (saved[i].r == remote)
				remote->min_repeat = saved[i].min_repeat;
	return real(remote, code, delay);
}
"""


def build_shim(workdir: Path, build: Path) -> Path:
    src = workdir / "oracle_shim.c"
    out = workdir / "oracle_shim.so"
    src.write_text(SHIM_C)
    subprocess.run(["cc", "-shared", "-fPIC", "-O2", f"-I{build / 'lib'}",
                    f"-I{build / 'include'}", "-o", str(out), str(src), "-ldl"],
                   check=True)
    return out


def run_oracle(conf: Path, build: Path, shim: Path, count: int, timeout: int,
               exit_on_eof: bool, keep_min_repeat: bool):
    """Returns (returncode, segments, stderr) with segments [(name, [lines])]."""
    with tempfile.TemporaryDirectory(prefix="lircoracle-") as tmp:
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = str(build / "lib" / ".libs")
        env["LD_PRELOAD"] = str(shim)
        env["LIRC_ORACLE_MARK"] = "1"
        env.pop("LIRC_ORACLE_IGNORE_SIGUSR1", None)
        if not exit_on_eof:
            env["LIRC_ORACLE_IGNORE_SIGUSR1"] = "1"
        env.pop("LIRC_ORACLE_KEEP_MIN_REPEAT", None)
        if keep_min_repeat:
            env["LIRC_ORACLE_KEEP_MIN_REPEAT"] = "1"
        try:
            proc = subprocess.run(
                [str(build / "tools" / ".libs" / "irsimsend"), "-U",
                 str(build / "plugins" / ".libs"), "-c", str(count), str(conf)],
                cwd=tmp, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=timeout)
            rc, err = proc.returncode, proc.stderr
        except subprocess.TimeoutExpired:
            return "timeout", [], b""
        out_path = Path(tmp) / "simsend.out"
        data = out_path.read_bytes() if out_path.exists() else b""
    segments: list[tuple[str, list[str]]] = []
    preamble: list[str] = []
    raw_lines = data.split(b"\n")
    if raw_lines[-1] == b"":
        raw_lines.pop()  # the final newline, or an empty file
    for raw in raw_lines:
        if raw.startswith(MARK):
            segments.append((C._decode(raw[len(MARK):]), []))
        elif segments:
            segments[-1][1].append(raw.decode("ascii", "replace"))
        else:
            preamble.append(raw.decode("ascii", "replace"))
    if preamble:
        segments.insert(0, ("<before first button>", preamble))
    return rc, segments, err.decode("utf-8", "replace")


def features(cfg) -> collections.Counter:
    """Feature counts over one file's remote blocks."""
    f = collections.Counter()
    for r in cfg.remotes:
        f["remote blocks"] += 1
        proto = {C.RAW_CODES: "RAW_CODES", C.RC5: "RC5", C.RC6: "RC6",
                 C.RCMM: "RCMM", C.SPACE_ENC: "SPACE_ENC",
                 C.SPACE_FIRST: "SPACE_FIRST", C.GRUNDIG: "GRUNDIG",
                 C.BO: "BO", C.SERIAL: "SERIAL", C.XMP: "XMP",
                 0: "no protocol flag"}.get(r.protocol, f"mixed protocol bits 0x{r.protocol:x}")
        f[f"protocol {proto}"] += 1
        if r.rc6_mask and r.protocol != C.RC6:
            f["rc6_mask without RC6 flag"] += 1
        if r.rc6_mask:
            f["rc6_mask set (after defaults)"] += 1
        scancode_only = r.pzero == 0 and r.szero == 0 and not r.flags & C.RAW_CODES
        if scancode_only:
            f["no timings: scancode-only (file driver writes `code N`)"] += 1
        for bit, name in ((C.CONST_LENGTH, "CONST_LENGTH"), (C.NO_HEAD_REP, "NO_HEAD_REP"),
                          (C.NO_FOOT_REP, "NO_FOOT_REP"), (C.REPEAT_HEADER, "REPEAT_HEADER"),
                          (C.COMPAT_REVERSE, "REVERSE")):
            if r.flags & bit:
                f[f"flag {name}"] += 1
        checks = {
            "toggle_bit_mask": r.has_toggle_bit_mask(),
            "toggle_bit_mask with >1 bit": bin(r.toggle_bit_mask).count("1") > 1,
            "toggle_mask": r.has_toggle_mask(),
            "repeat_mask": r.has_repeat_mask(),
            "repeat (ditto) timings": r.has_repeat(),
            "repeat_gap": r.has_repeat_gap(),
            "gap2": r.gap2 != 0,
            "header": r.has_header(),
            "foot": r.has_foot(),
            "plead": r.plead != 0,
            "ptrail": r.ptrail != 0,
            "pre_data": r.has_pre(),
            "post_data": r.has_post(),
            "pre pulse/space": r.pre_p > 0 and r.pre_s > 0,
            "post pulse/space": r.post_p > 0 and r.post_s > 0,
            "min_repeat > 0 (zeroed by irsimsend)": r.min_repeat > 0,
            "gap < 10 ms": 0 < r.min_gap() < 10000,
            "manual_sort": bool(r.manual_sort),
        }
        for k, v in checks.items():
            if v:
                f[k] += 1
        multi = sum(1 for c in r.codes or () if c.next)
        if multi:
            f["blocks with multi-code buttons"] += 1
            f["multi-code buttons"] += multi
        f["buttons"] += len(r.codes or ())
    return f


def compare_file(path: str, build: str, shim: str, count: int, timeout: int,
                 exit_on_eof: bool, keep_min_repeat: bool) -> dict:
    conf = Path(path)
    res: dict = {"file": path, "status": None, "codes": collections.Counter(),
                 "mismatches": [], "skips": [], "features": {}, "rules": {}}
    rc, segs, err = run_oracle(conf, Path(build), Path(shim), count, timeout,
                               exit_on_eof, keep_min_repeat)
    res["oracle_rc"] = rc
    try:
        cfg = parse_file(conf)
    except LircConfigError as e:
        if rc == 1 and not segs and "Cannot parse" in err:
            res["status"] = "rejected by both"
            res["skips"].append({"reason": "lircd rejects the file", "detail": e.reason})
        else:
            res["status"] = "MISMATCH: port rejects, lircd accepts"
            res["mismatches"].append({"code": None, "detail": str(e)})
        return res
    except LircUndefinedBehaviour as e:
        res["status"] = "skipped: undefined behaviour in lircd"
        res["skips"].append({"reason": "lircd undefined behaviour at parse", "detail": str(e),
                             "oracle_rc": rc})
        return res
    if not cfg.remotes:
        if rc == 1 and not segs:
            res["status"] = "no remotes (both)"
            res["skips"].append({"reason": "file defines no remote", "detail": ""})
        else:
            res["status"] = "MISMATCH: port finds no remotes"
            res["mismatches"].append({"code": None, "detail": f"oracle rc={rc}"})
        return res
    if rc == 1 and not segs:
        res["status"] = "MISMATCH: lircd rejects, port accepts"
        res["mismatches"].append({"code": None, "detail": err.strip()[-300:]})
        return res
    res["features"] = dict(features(cfg))

    port: list[tuple[str, list[str], set[str]]] = []
    session = IrSimSend(cfg, count=count, exit_on_eof=exit_on_eof,
                        keep_min_repeat=keep_min_repeat)
    ub = None
    try:
        for r, code, results in session.run():
            lines: list[str] = []
            kinds = set()
            for x in results:
                lines += x.lines(exit_on_eof)
                kinds.add(x.kind if x.kind != "failed" else "failed: " + x.reason.split(":")[0])
                if x.terminated:
                    kinds.add("a LIRC_EOF value" + (" (irsimsend exits)" if exit_on_eof
                                                     else " (SIGUSR1 ignored)"))
            port.append((code.name, lines, kinds))
    except LircUndefinedBehaviour as e:
        ub = str(e)
    res["rules"] = dict(session.lircd.trace)

    n = max(len(port), len(segs))
    for i in range(n):
        o = segs[i] if i < len(segs) else None
        p = port[i] if i < len(port) else None
        if p is None and ub is not None:
            remaining = n - i
            res["codes"]["skipped: undefined behaviour"] += remaining
            res["skips"].append({"reason": "lircd undefined behaviour at send",
                                 "detail": ub, "buttons": remaining,
                                 "at": o[0] if o else None})
            break
        if o is None or p is None or o[0] != p[0]:
            res["mismatches"].append({
                "code": (p or o)[0], "index": i,
                "detail": f"button order differs: oracle {o and o[0]!r}, port {p and p[0]!r}"})
            res["codes"]["mismatch"] += 1
            break
        res["codes"]["compared"] += 1
        if o[1] == p[1]:
            res["codes"]["exact match"] += 1
            for k in p[2]:
                res["codes"][f"match, lircd emitted {k}"] += 1
        else:
            res["codes"]["mismatch"] += 1
            j = next((j for j, (a, b) in enumerate(zip(o[1], p[1])) if a != b),
                     min(len(o[1]), len(p[1])))
            res["mismatches"].append({
                "code": p[0], "index": i, "first_diff_line": j,
                "oracle": o[1][j] if j < len(o[1]) else "<end>",
                "port": p[1][j] if j < len(p[1]) else "<end>"})
    if session.terminated:
        # file.c:206-209: irsimsend died on SIGUSR1; nothing after it ran.
        unsent = sum(len(r.codes or ()) for r in cfg.remotes) - len(port)
        res["codes"]["not sent: irsimsend exited on LIRC_EOF"] += unsent
        res["skips"].append({"reason": "irsimsend exits on a LIRC_EOF value "
                             "(both agree); later buttons never sent",
                             "detail": port[-1][0], "buttons": unsent})
    res["status"] = "compared" if not res["mismatches"] else "MISMATCH"
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", help="conf files (default: the corpus)")
    ap.add_argument("--lirc-build", default=os.environ.get("LIRC_BUILD"),
                    help="built lirc-0.10.2 tree (default $LIRC_BUILD)")
    ap.add_argument("--corpus", default=os.environ.get("LIRC_CORPUS"),
                    help="lirc-remotes checkout (default $LIRC_CORPUS)")
    ap.add_argument("--count", type=int, default=2, help="irsimsend -c (default 2)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--show", type=int, default=20, help="mismatches to print")
    ap.add_argument("--limit", type=int, help="compare only the first N files")
    ap.add_argument("--exit-on-eof", action="store_true",
                    help="let irsimsend die on a LIRC_EOF value, as unmodified "
                         "(default: ignore SIGUSR1 in both, so every button is compared)")
    ap.add_argument("--keep-min-repeat", action="store_true",
                    help="send with each remote's parsed min_repeat, as lircd does, "
                         "instead of irsimsend's zero")
    args = ap.parse_args(argv)
    if not args.lirc_build:
        ap.error("--lirc-build (or $LIRC_BUILD) is required")
    files = args.files
    if not files:
        if not args.corpus:
            ap.error("name files, or give --corpus (or $LIRC_CORPUS)")
        files = sorted(str(p) for p in Path(args.corpus, "remotes").rglob("*.conf")
                       if p.is_file())
    if args.limit is not None:
        files = files[: args.limit]
    # irsimsend runs from a scratch directory, so hand it absolute paths
    # (which also resolves `include` relative to the right file).
    files = [str(Path(f).resolve()) for f in files]

    with tempfile.TemporaryDirectory(prefix="lircshim-") as tmp:
        shim = build_shim(Path(tmp), Path(args.lirc_build))
        with concurrent.futures.ProcessPoolExecutor(args.jobs) as ex:
            n = len(files)
            results = list(ex.map(compare_file, files, [args.lirc_build] * n,
                                  [str(shim)] * n, [args.count] * n, [args.timeout] * n,
                                  [args.exit_on_eof] * n, [args.keep_min_repeat] * n,
                                  chunksize=4))

    status = collections.Counter(r["status"] for r in results)
    codes = collections.Counter()
    feats = collections.Counter()
    rules = collections.Counter()
    skips = collections.Counter()
    for r in results:
        codes.update(r["codes"])
        feats.update(r["features"])
        rules.update(r["rules"])
        for s in r["skips"]:
            skips[s["reason"]] += 1
    mism = [(r["file"], m) for r in results for m in r["mismatches"]]

    print(f"files: {len(results)}")
    for k, v in sorted(status.items()):
        print(f"  {k}: {v}")
    print("buttons:")
    for k, v in sorted(codes.items()):
        print(f"  {k}: {v}")
    print("skips (files or button runs), by reason:")
    for k, v in sorted(skips.items()):
        print(f"  {k}: {v}")
    print("features over remote blocks both sides accepted:")
    for k, v in sorted(feats.items()):
        print(f"  {k}: {v}")
    print("send-path rules exercised (times fired, all sessions):")
    for k, v in sorted(rules.items()):
        print(f"  {k}: {v}")
    print(f"mismatches: {len(mism)}")
    for f, m in mism[: args.show]:
        print(f"  {f}: {m}")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"status": status, "buttons": codes, "skips": skips, "features": feats,
             "rules": rules, "files": results}, indent=1, default=dict))
    return 1 if mism else 0


if __name__ == "__main__":
    sys.exit(main())
