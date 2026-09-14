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
from .generators import PIPELINE, owned_paths, registered
from .pronto import encode as pronto_encode
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

    problems = [p for t in corpus_files(root) for p in validate_file(t)]
    for p in problems:
        print(f"ERROR {p}", file=sys.stderr)
    if problems:
        return EXIT_ERROR

    if not stages:
        pending = ", ".join(f"{g.name} (phase {g.phase})" for g in PIPELINE)
        verb = "would check" if args.check else "would run"
        print(
            f"validate: OK. No generators registered yet, so there is nothing "
            f"to {'compare' if args.check else 'generate'}.\n"
            f"  Pipeline order is fixed; membership follows the registry (D19).\n"
            f"  Pending: {pending}."
        )
        return EXIT_OK

    print(f"stages: validate -> {' -> '.join(g.name for g in stages)}")
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
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--strict", action="store_true",
        help="promote warnings to errors (D32)",
    )
    common.add_argument(
        "--allow-dirty", action="store_true",
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
    for name, fallback_phase, help_text in (
        ("check", 3, "cross-check every candidate group (R13)"),
        ("compile", 3, "write build/pronto/... (R12)"),
        ("fmt", 2, "canonicalize hand-authored files (D9, D17, D20)"),
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LedgerError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
