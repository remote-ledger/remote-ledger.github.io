"""The ``rl`` command line.

Exit codes (D32): 0 when there are no errors, 1 when there is at least one.
Warnings never change the exit code -- they are recorded in
``build/warnings.json`` so a new one shows up as a committed-tree diff that
``rl build --check`` fails until acknowledged. ``--strict`` promotes them.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__, protocols
from .errors import LedgerError, ValidationError
from .generators import PIPELINE, diff_tree, owned_paths, registered
from .check import check_remote
from .fmt import format_document
from .pronto import encode as pronto_encode
from .remote import load_remote
from .serialize import dumps
from .validate import corpus_files, validate_file

EXIT_OK, EXIT_ERROR, EXIT_UNAVAILABLE = 0, 1, 2


def _repo_root() -> Path:
    return Path.cwd()


def _parse_number(text: str) -> int:
    """Accept 0x-prefixed hex or decimal, raising a LedgerError on anything else.

    A bare int() ValueError would escape main()'s handler and print a
    traceback instead of the ERROR line every other failure path produces.
    """
    stripped = text.strip()
    try:
        base = 16 if stripped.lower().lstrip("+-").startswith("0x") else 10
        return int(stripped, base)
    except ValueError:
        raise ValidationError(
            f"{text!r} is not a number; write a decimal (17) or a "
            "0x-prefixed hex value (0x11)"
        ) from None


def _resolve_targets(raw: str | None) -> list[Path]:
    """Resolve a path argument to remote files, or say why it cannot be.

    A path that is neither a file nor a directory containing ``remotes/``
    used to fall through to the corpus glob and report "0 file(s) checked,
    0 error(s)" with exit 0 -- so a typo'd or renamed path in a hook or CI
    step validated nothing and reported green.
    """
    if raw is None:
        return corpus_files(_repo_root())
    target = Path(raw)
    if target.is_file():
        return [target]
    if not target.exists():
        raise ValidationError(f"{raw}: no such file or directory")
    if not target.is_dir():
        raise ValidationError(f"{raw}: not a file or directory")
    if (target / "remotes").is_dir():
        found = corpus_files(target)
    else:
        found = sorted(target.glob("**/*.json"))
    if not found:
        raise ValidationError(
            f"{raw}: contains no .json files to validate. Pass a remote file, "
            "a directory of them, or a repo root containing remotes/"
        )
    return found


def cmd_validate(args: argparse.Namespace) -> int:
    targets = _resolve_targets(args.path)
    problems = [p for t in targets for p in validate_file(t)]
    for p in problems:
        print(f"ERROR {p}", file=sys.stderr)
    print(f"{len(targets)} file(s) checked, {len(problems)} error(s)")
    return EXIT_ERROR if problems else EXIT_OK


def cmd_encode(args: argparse.Namespace) -> int:
    proto = protocols.get(args.protocol)
    signal = proto.encode(
        device=_parse_number(args.device),
        subdevice=_parse_number(args.subdevice) if args.subdevice else None,
        function=_parse_number(args.function),
        carrier_hz=args.carrier,
        unit_us=args.unit,
    )
    print(pronto_encode(signal))
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    """R13 cross-check plus D9's derived-form regeneration."""
    targets = _resolve_targets(args.path)
    problems, warnings = [], []
    for target in targets:
        schema_errors = validate_file(target)
        if schema_errors:
            problems += [str(p) for p in schema_errors]
            continue
        file_problems, file_warnings = check_remote(load_remote(target))
        problems += [f"{target.as_posix()}: {p}" for p in file_problems]
        warnings += file_warnings

    for warning in warnings:
        print(warning, file=sys.stderr)
    for problem in problems:
        print(f"ERROR {problem}", file=sys.stderr)

    # D19: corpus-wide artifacts are written only by a corpus-wide run, and
    # `check` owns build/warnings.json -- which registers in Phase 3.
    print(
        f"{len(targets)} file(s) cross-checked, {len(problems)} error(s), "
        f"{len(warnings)} warning(s)"
    )
    if problems:
        return EXIT_ERROR
    return EXIT_ERROR if (warnings and args.strict) else EXIT_OK


def compiled_artifact(remote) -> dict:
    """D20's ``build/pronto/<mfr>/<model>.json`` shape.

    No timestamp, no tool version, no path: D20's rule that a generated file
    holds no non-reproducible value is what makes ``--check`` viable at all.
    ``minSends`` sits once at the protocol level, not per key -- it is a fact
    about the hardware, and a consumer repeating the Pronto repeat sequence
    needs it exactly once (D3a).
    """
    protocol = {
        "carrierHz": remote.protocol.carrier_hz,
        "minSends": remote.protocol.min_sends,
    }
    if remote.protocol.name:
        protocol["name"] = remote.protocol.name

    keys: dict[str, dict] = {}
    for key in sorted(remote.keys):
        candidates: dict[str, dict] = {}
        for name, group in remote.groups(key).items():
            from .forms import select
            chosen = select(group)
            entry = {
                "prontoHex": remote.compile_group(key, name),
                "confidence": chosen.confidence,
            }
            if chosen.source:
                entry["source"] = chosen.source
            if name != "primary":
                entry["label"] = remote.variants[name].label
            candidates[name] = entry
        keys[key] = {"candidates": candidates}

    return {
        "schemaVersion": 1,
        "manufacturer": remote.manufacturer,
        "model": remote.model,
        "protocol": protocol,
        "keys": keys,
    }


def _artifact_path(root: Path, remote) -> Path:
    return (
        root / "build" / "pronto"
        / remote.manufacturer.lower().replace(" ", "-")
        / f"{remote.model}.json"
    )


def cmd_compile(args: argparse.Namespace) -> int:
    """R12: render each candidate group's trusted form, and write it.

    Per-file artifacts may be written by a path-scoped run -- their scope is
    exactly the input's -- unlike the corpus-wide artifacts D19 reserves for
    a corpus-wide run.
    """
    root = _repo_root()
    targets = _resolve_targets(args.path)
    written = drift = 0
    for target in targets:
        errors = validate_file(target)
        if errors:
            for problem in errors:
                print(f"ERROR {problem}", file=sys.stderr)
            return EXIT_ERROR
        remote = load_remote(target)
        path = _artifact_path(root, remote)
        text = dumps(compiled_artifact(remote))
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != text:
                drift += 1
                print(
                    f"ERROR {path.relative_to(root).as_posix()} "
                    f"{'differs from' if current else 'is missing; expected'} "
                    "freshly compiled output",
                    file=sys.stderr,
                )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            written += 1
            print(path.relative_to(root).as_posix())

    if args.check:
        print(f"{len(targets)} file(s) checked, {drift} drifted")
        return EXIT_ERROR if drift else EXIT_OK
    print(f"{written} artifact(s) written")
    return EXIT_OK


def cmd_fmt(args: argparse.Namespace) -> int:
    """D9/D17/D20: canonicalise hand-authored files."""
    targets = _resolve_targets(args.path)
    changed = 0
    for target in targets:
        before = target.read_text(encoding="utf-8")
        after = format_document(
            target, refresh=args.refresh, expand_variants=args.expand, sort=args.sort
        )
        if before != after:
            changed += 1
            if args.check:
                print(f"ERROR {target.as_posix()} is not canonically formatted",
                      file=sys.stderr)
            else:
                target.write_text(after, encoding="utf-8", newline="\n")
    print(f"{len(targets)} file(s), {changed} "
          f"{'would change' if args.check else 'rewritten'}")
    return EXIT_ERROR if (changed and args.check) else EXIT_OK


def _dirty_owned_paths(root: Path) -> list[str]:
    """Generator-owned paths with uncommitted modifications (D11).

    ``--check`` refuses to run when any exist, because that is exactly the
    case where the tree comparison would be vacuous. Skipped outside a git
    repo.
    """
    paths = owned_paths()
    if not paths:
        return []
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", *paths],
            cwd=root, capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line[3:] for line in out.splitlines() if line.strip()]


def cmd_build(args: argparse.Namespace) -> int:
    """The whole pipeline, whole-tree (D19).

    Order is fixed; membership follows the registry, so this never invokes a
    stage that does not exist yet.
    """
    import shutil
    import tempfile

    root = _repo_root()
    stages = registered()

    if args.check and not args.allow_dirty:
        dirty = _dirty_owned_paths(root)
        if dirty:
            print(
                "ERROR --check cannot run with uncommitted changes under a "
                "generator-owned path, or the comparison is vacuous (D11):\n  "
                + "\n  ".join(dirty)
                + "\n  Commit them, or pass --allow-dirty.",
                file=sys.stderr,
            )
            return EXIT_ERROR

    problems = [str(p) for t in corpus_files(root) for p in validate_file(t)]
    if problems:
        for problem in problems:
            print(f"ERROR {problem}", file=sys.stderr)
        return EXIT_ERROR

    if not stages:
        pending = ", ".join(f"{g.name} (phase {g.phase})" for g in PIPELINE)
        print(
            "validate: OK. No generators registered yet, so there is nothing "
            f"to {'compare' if args.check else 'generate'}.\n"
            "  Pipeline order is fixed; membership follows the registry (D19).\n"
            f"  Pending: {pending}."
        )
        return EXIT_OK

    out_root = Path(tempfile.mkdtemp(prefix="rl-build-")) if args.check else root
    try:
        for stage in stages:
            stage_problems = stage.run(root, out_root)
            if stage_problems:
                for problem in stage_problems:
                    print(f"ERROR {problem}", file=sys.stderr)
                return EXIT_ERROR

        if args.check:
            drift = diff_tree(root, out_root)
            for problem in drift:
                print(f"ERROR {problem}", file=sys.stderr)
            print(
                f"validate -> {' -> '.join(g.name for g in stages)}: "
                f"{len(drift)} difference(s) against the committed tree"
            )
            return EXIT_ERROR if drift else EXIT_OK
    finally:
        if args.check:
            shutil.rmtree(out_root, ignore_errors=True)

    print(f"validate -> {' -> '.join(g.name for g in stages)}: written")
    return EXIT_OK


def _unavailable(name: str, phase: int) -> int:
    print(
        f"`rl {name}` is not implemented until Phase {phase} "
        f"(see DESIGN.md section 8).",
        file=sys.stderr,
    )
    return EXIT_UNAVAILABLE


def build_parser() -> argparse.ArgumentParser:
    # Global flags live on a shared parent so they are accepted *after* the
    # subcommand too. Previously `rl build --check --allow-dirty` was an
    # argparse error, which made the remedy cmd_build prints unusable.
    # `default=argparse.SUPPRESS` is load-bearing. A parent parser attached
    # to both the root and the subparsers sets its defaults twice, and the
    # subparser's pass runs second -- so with an ordinary `default=False`,
    # `rl --strict validate` parses as strict=False, silently discarding the
    # flag. SUPPRESS makes the subparser leave the attribute alone unless the
    # flag actually appeared after the subcommand; main() fills in the
    # default once, afterwards.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--strict", action="store_true", default=argparse.SUPPRESS,
        help="promote warnings to errors (D32)",
    )
    common.add_argument(
        "--allow-dirty", action="store_true", default=argparse.SUPPRESS,
        help="let --check run with uncommitted changes under an owned path (D11)",
    )

    p = argparse.ArgumentParser(
        prog="rl",
        description="Remote Ledger: compile and check IR remote files.",
        parents=[common],
    )
    p.add_argument("--version", action="version", version=f"rl {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser(
        "validate", help="schema and semantic checks (R15)", parents=[common]
    )
    v.add_argument("path", nargs="?")
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser(
        "encode", help="render one irp form to Pronto Hex (D3, D6)",
        parents=[common],
    )
    e.add_argument("--protocol", required=True, choices=sorted(protocols.REGISTRY))
    e.add_argument("--device", required=True)
    e.add_argument("--subdevice")
    e.add_argument("--function", required=True)
    e.add_argument("--carrier", type=int, required=True)
    e.add_argument("--unit", type=int)
    e.set_defaults(func=cmd_encode)

    b = sub.add_parser(
        "build", help="the whole pipeline, whole-tree (D19)", parents=[common]
    )
    b.add_argument("--check", action="store_true", help="diff instead of write")
    b.set_defaults(func=cmd_build)

    # Phases come from generators.PIPELINE where a stage has one, so the
    # binary cannot print two different phase numbers for the same stage.
    generator_phase = {g.name: g.phase for g in PIPELINE}
    c = sub.add_parser(
        "check", help="cross-check every candidate group (R13)", parents=[common]
    )
    c.add_argument("path", nargs="?")
    c.set_defaults(func=cmd_check)

    co = sub.add_parser(
        "compile", help="write build/pronto/... (R12)", parents=[common]
    )
    co.add_argument("path", nargs="?")
    co.add_argument("--check", action="store_true", help="diff instead of write")
    co.set_defaults(func=cmd_compile)

    f = sub.add_parser(
        "fmt", help="canonicalise hand-authored files (D9, D17, D20)",
        parents=[common],
    )
    f.add_argument("path", nargs="?")
    f.add_argument("--refresh", action="store_true",
                   help="regenerate derived forms and variant caches (D9, D33)")
    f.add_argument("--expand", action="store_true",
                   help="write variant expansions longhand (D17)")
    f.add_argument("--sort", action="store_true",
                   help="canonically order set-like arrays only (D20)")
    f.add_argument("--check", action="store_true", help="diff instead of write")
    f.set_defaults(func=cmd_fmt)

    for name, fallback_phase, help_text in (
        ("index", 5, "regenerate build/index.json (R14)"),
        ("lookup", 5, "find a remote by device or model (R16)"),
        ("site", 6, "generate site/ (R17)"),
    ):
        phase = generator_phase.get(name, fallback_phase)
        sp = sub.add_parser(
            name, help=f"{help_text} [phase {phase}]", parents=[common]
        )
        sp.add_argument("rest", nargs="*")
        sp.set_defaults(func=lambda a, n=name, ph=phase: _unavailable(n, ph))

    return p


#: Flags accepted on either side of the subcommand (see build_parser).
GLOBAL_FLAGS = ("strict", "allow_dirty")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for flag in GLOBAL_FLAGS:
        if not hasattr(args, flag):
            setattr(args, flag, False)
    try:
        return args.func(args)
    except LedgerError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
