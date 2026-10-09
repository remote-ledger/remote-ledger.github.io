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

from . import __version__, parallel, protocols
from .errors import LedgerError, ValidationError
from .generators import PIPELINE, diff_tree, owned_paths, registered
from .check import check_remote
from .fmt import format_document
from .keys import vocabulary_problems
from .pronto import encode as pronto_encode
from .remote import load_remote
from .serialize import dumps
from .validate import corpus_files, validate_file, validate_files

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
    problems = validate_files(targets)
    # The canonical key vocabulary (D83) is part of what a corpus-wide run checks;
    # a run on one path is about that path's remotes.
    if args.path is None:
        problems += vocabulary_problems()
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


def _check_target(target: Path) -> tuple[list[str], list]:
    """One file's share of ``rl check``: (problems, warnings), as text lines."""
    schema_errors = validate_file(target)
    if schema_errors:
        return [str(p) for p in schema_errors], []
    file_problems, file_warnings = check_remote(load_remote(target))
    return [f"{target.as_posix()}: {p}" for p in file_problems], file_warnings


def cmd_check(args: argparse.Namespace) -> int:
    """R13 cross-check plus D9's derived-form regeneration."""
    targets = _resolve_targets(args.path)
    problems, warnings = [], []
    for target_problems, target_warnings in parallel.ordered_map(_check_target, targets):
        problems += target_problems
        warnings += target_warnings

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
    needs it exactly once (D3a). A key's optional display ``label`` (what the
    source shows for it) rides beside its candidates, only when it has one.
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
        # Only a key that has one: a file without labels compiles to the very
        # bytes it did before the field existed.
        if key in remote.labels:
            keys[key]["label"] = remote.labels[key]

    return {
        "schemaVersion": 1,
        "manufacturer": remote.manufacturer,
        "model": remote.model,
        "protocol": protocol,
        "keys": keys,
    }


def _artifact_path(root: Path, target: Path) -> Path:
    """The same rule the compile generator uses (D40): mirror the source."""
    from . import paths

    return root / paths.artifact(paths.rel(root, target))


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
        path = _artifact_path(root, target)
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

    problems = [str(p) for p in validate_files(corpus_files(root))]
    problems += [str(p) for p in vocabulary_problems()]   # D83: ledger data, no stage owns it
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


def cmd_index(args: argparse.Namespace) -> int:
    """R14/D13. Corpus-wide, so a path-scoped run may not write it (D19)."""
    from .generators import run_index

    root = _repo_root()
    if args.check:
        import shutil, tempfile
        out = Path(tempfile.mkdtemp(prefix="rl-index-"))
        try:
            problems = run_index(root, out)
            # Only what the index stage owns: against a tree holding nothing
            # else, every other owned path would read as an orphan.
            drift = (
                diff_tree(root, out, tuple(g for g in PIPELINE if g.name == "index"))
                if not problems else []
            )
            for message in problems + drift:
                print(f"ERROR {message}", file=sys.stderr)
            return EXIT_ERROR if (problems or drift) else EXIT_OK
        finally:
            shutil.rmtree(out, ignore_errors=True)
    problems = run_index(root, root)
    for message in problems:
        print(f"ERROR {message}", file=sys.stderr)
    return EXIT_ERROR if problems else EXIT_OK


def cmd_site(args: argparse.Namespace) -> int:
    """R17. Corpus-wide, so a path-scoped run may not write it (D19)."""
    from .generators import run_site

    root = _repo_root()
    if args.check:
        import shutil, tempfile
        out = Path(tempfile.mkdtemp(prefix="rl-site-"))
        try:
            problems = run_site(root, out) + diff_tree(
                root, out, tuple(g for g in PIPELINE if g.name == "site")
            )
            for message in problems:
                print(f"ERROR {message}", file=sys.stderr)
            return EXIT_ERROR if problems else EXIT_OK
        finally:
            shutil.rmtree(out, ignore_errors=True)
    run_site(root, root)
    print("site/index.html")
    return EXIT_OK


def cmd_app(args: argparse.Namespace) -> int:
    """D74. The app API under ``site/app/v1/``, from ``remotes/irblaster/``.
    Corpus-wide, so a path-scoped run may not write it (D19)."""
    import shutil
    import tempfile

    from .app_api import build_app_api, write_result

    root = _repo_root()
    built = build_app_api(root)
    if built.problems:
        for message in built.problems:
            print(f"ERROR {message}", file=sys.stderr)
        return EXIT_ERROR
    stats = built.stats
    if stats:
        skipped = stats["unrepresentedKeys"]
        print(
            f"{stats['files']:,} files, {stats['bytes']:,} bytes: {stats['brands']:,} brands, "
            f"{stats['models']:,} models, {stats['remotes']:,} remotes, {stats['keys']:,} keys; "
            f"signals for {', '.join(stats['signalProtocols']) or 'no protocol'}; "
            f"{stats['power']:,} power codes; keys the import could not represent and the API "
            f"therefore lacks: {'unknown (no IMPORT.md)' if skipped is None else f'{skipped:,}'}; "
            f"dataVersion {stats['dataVersion']}"
        )
    else:
        print("no imported remote under remotes/irblaster/, so there is no API")
    if args.check:
        out = Path(tempfile.mkdtemp(prefix="rl-app-"))
        try:
            write_result(built, out)
            drift = diff_tree(root, out, tuple(g for g in PIPELINE if g.name == "app"))
            for message in drift:
                print(f"ERROR {message}", file=sys.stderr)
            return EXIT_ERROR if drift else EXIT_OK
        finally:
            shutil.rmtree(out, ignore_errors=True)
    write_result(built, root)
    return EXIT_OK


def cmd_bundle(args: argparse.Namespace) -> int:
    """D88. The catalog bundle: a build artifact an app ships, written under ``--out``
    and never under ``build/`` or ``site/``. ``rl bundle sign``, ``verify-signature``
    and ``vectors`` are its other commands."""
    import time

    from .bundle import build as bb

    root = _repo_root()
    sub = getattr(args, "bundle_command", None)
    if sub == "sign":
        from .bundle.sign import sign_directory

        identifier = sign_directory(Path(args.directory), Path(args.key))
        print(f"signed {args.directory}/{bb.MANIFEST_FILE} with the key {identifier}: "
              f"{args.directory}/{bb.SIGNATURE_FILE}")
        return EXIT_OK
    if sub == "verify-signature":
        from .bundle.sign import verify_signature

        problems = verify_signature(Path(args.directory), Path(args.pub))
        for message in problems:
            print(f"ERROR {message}", file=sys.stderr)
        if not problems:
            print(f"{args.directory}: the signature is valid and the bundle and notices are "
                  "the bytes the manifest lists")
        return EXIT_ERROR if problems else EXIT_OK
    if sub == "vectors":
        return _bundle_vectors(root, args)
    if sub == "matching-vectors":
        return _matching_vectors(args)
    if sub == "suggest-vectors":
        return _matching_vectors(args, suggest=True)
    if sub == "search-eval":
        return _search_eval(root, args)

    if args.verify:
        from .bundle.verify import verify_directory

        started = time.perf_counter()
        problems, facts = verify_directory(root, Path(args.verify))
        for message in problems[:50]:
            print(f"ERROR {message}", file=sys.stderr)
        if len(problems) > 50:
            print(f"ERROR ... and {len(problems) - 50} more", file=sys.stderr)
        if not problems:
            print(f"{args.verify}: verified against the tree: {facts['remotes']:,} remotes "
                  f"({facts['remoteRefs']:,} refs), "
                  f"{facts['decodedSignals']:,} signals decoded and encoded back, "
                  f"{facts['sampledRemotes']} remotes compiled again, "
                  f"dataVersion {facts['dataVersion']} "
                  f"({time.perf_counter() - started:.1f} s)")
        return EXIT_ERROR if problems else EXIT_OK

    started = time.perf_counter()
    built = bb.build_bundle(root, args.profile, max_bytes=args.max_bytes)
    if built.problems:
        for message in built.problems:
            print(f"ERROR {message}", file=sys.stderr)
        return EXIT_ERROR
    stats, selection = built.stats, built.selection
    for message in built.notes:
        print(f"note: {message}", file=sys.stderr)
    if selection is not None and selection.profile == "selected":
        for name in selection.unresolved:
            print(f"note: {name!r} is on selected_brands.txt and matches no brand of the "
                  "catalog", file=sys.stderr)
    print(
        f"{stats['profile']}: {stats['bytes']:,} bytes: {stats['brands']:,} brands, "
        f"{stats['models']:,} models, {stats['remotes']:,} remotes, {stats['keys']:,} keys, "
        f"{stats['signals']:,} signals; dataVersion {stats['dataVersion']} "
        f"({time.perf_counter() - started:.1f} s)"
    )
    print(
        f"fragments: {stats['foldedFragments']:,} protocol fragments with no test key are folded into "
        f"{stats['mergedRemotes']:,} remotes; remote_refs: {stats['remoteRefs']:,} refs"
    )
    print(
        f"brand aliases: {stats['brandAliases']:,} of the {stats['aliasesListed']:,} listed; not in "
        f"it: {stats['aliasesLeftOut']:,} of brands this profile leaves out, "
        f"{stats['aliasesMissing']:,} of brands the catalog lacks"
    )
    print(
        f"left out: {stats['excludedBrands']:,} brands ({stats['unreachableBrands']:,} of them "
        f"in no shard of the app API), {stats['leftOutRemotes']:,} remotes and "
        f"{stats['leftOutKeys']:,} keys ({stats['leftOutNotInApi']:,} remotes of a source the "
        "app API does not serve)"
    )
    if selection is not None and selection.chosen is not None:
        print(f"selection: {selection.rule}")
        if selection.skipped:
            print(f"on the list, not carried (over the budget): {len(selection.skipped)} brands: "
                  + ", ".join(selection.skipped[:40]) + (" ..." if len(selection.skipped) > 40 else ""))
    out = _bundle_out(root, args)
    if args.check:
        drift = bb.check_bundle(built, out)
        for message in drift:
            print(f"ERROR {message}", file=sys.stderr)
        return EXIT_ERROR if drift else EXIT_OK
    bb.write_bundle(built, out, root)
    print(f"written to {out}")
    return EXIT_OK


def _bundle_out(root: Path, args: argparse.Namespace) -> Path:
    from .bundle.build import DEFAULT_OUT

    return Path(args.out) if args.out else root / DEFAULT_OUT / args.profile


def _matching_vectors(args: argparse.Namespace, suggest: bool = False) -> int:
    """``rl bundle matching-vectors``: the matcher's cross-language vectors (D97); with
    ``suggest``, ``rl bundle suggest-vectors``: those of ``suggest`` (D100, with a hint D106)."""
    if suggest:
        from .bundle.suggest_vectors import build
    else:
        from .bundle.matching_vectors import build

    text = dumps(build())
    target = Path(args.file)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current != text:
            print(f"ERROR {target} {'differs from' if current else 'is missing; expected'} "
                  "the vectors the matcher gives", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"{target}: written")
    return EXIT_OK


def _search_eval(root: Path, args: argparse.Namespace) -> int:
    """``rl bundle search-eval``: hit rates of the matcher over a bundle (D98)."""
    from .bundle import build as bb
    from .bundle.search_eval import report

    directory = Path(args.bundle) if args.bundle else root / bb.DEFAULT_OUT / "selected"
    if not (directory / bb.BUNDLE_FILE).is_file():
        print(f"ERROR {directory / bb.BUNDLE_FILE}: no bundle there; build one with `rl bundle`",
              file=sys.stderr)
        return EXIT_ERROR
    text = report(directory, queries=Path(args.queries) if args.queries else None,
                  seed=args.seed, per_class=args.per_class, timing=args.timing)
    sys.stdout.write(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    return EXIT_OK


def _bundle_vectors(root: Path, args: argparse.Namespace) -> int:
    """``rl bundle vectors``: the cross-language vectors of a bundle (D91)."""
    import tempfile

    from .bundle import build as bb
    from .bundle.vectors import build_vectors

    if args.source:
        document = build_vectors(Path(args.source) / bb.BUNDLE_FILE)
    else:
        built = bb.build_bundle(root, "full")
        if built.problems:
            for message in built.problems:
                print(f"ERROR {message}", file=sys.stderr)
            return EXIT_ERROR
        with tempfile.TemporaryDirectory(prefix="rl-vectors-") as tmp:
            (Path(tmp) / bb.BUNDLE_FILE).write_bytes(built.files[bb.BUNDLE_FILE])
            document = build_vectors(Path(tmp) / bb.BUNDLE_FILE)
    text = dumps(document)
    target = Path(args.file)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current != text:
            print(f"ERROR {target} {'differs from' if current else 'is missing; expected'} "
                  "the vectors this tree gives", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"{target}: {len(document['vectors'])} vectors over {len(document['protocols'])} protocols")
    return EXIT_OK


def cmd_keys_report(args: argparse.Namespace) -> int:
    """D86: how much of the corpus the canonical key vocabulary reaches.

    Read-only and corpus-wide: it writes nothing, so it cannot disturb D19's tree.
    """
    from .keys_report import build_report, read_corpus, render_json, render_text

    report = build_report(read_corpus(_repo_root()))
    sys.stdout.write((render_json if args.json else render_text)(report))
    return EXIT_OK


def cmd_lookup(args: argparse.Namespace) -> int:
    """R16: find a remote by device, model, alias or manufacturer.

    Reads the committed index and every shard of it when they were generated
    from exactly the files on disk (D69), which is what keeps a lookup under a
    second however much is imported; otherwise rebuilds them from the files,
    as it always did, and says so on stderr.
    """
    from .index import build_all, load_committed, merge, shard_entries
    from .lookup import keys_for, render, search

    root = _repo_root()
    index, why = load_committed(root)
    if index is None:
        print(f"note: {why}; rebuilding the index from the files "
              "(`rl build` refreshes it)", file=sys.stderr)
        built = build_all(root)
        index = merge(built.index, shard_entries(built.shards))
    query = " ".join(args.query)
    matches = search(index, query)
    print(render(matches, query, keys_for(root, matches)))
    return EXIT_OK


def cmd_import(args: argparse.Namespace) -> int:
    """SPEC R19 / DESIGN sections 14, 15 and 17: rewrite remotes/<source>/ from a
    checkout.

    The commit is read from the checkout itself, and must match ``--commit``
    when one is given, so every citation names the tree it was built from.
    """
    from .hifiremote import importer as hifiremote_importer
    from .irblaster import importer as irblaster_importer
    from .jp1 import importer as jp1_importer
    from .lirc import importer as lirc_importer
    from .smartir import importer as smartir_importer

    if args.source == "remotecentral":
        # A site of learned codes, pinned as a snapshot of what its pages say (D128).
        from .remotecentral import importer as remotecentral_importer

        report = remotecentral_importer.write_import(_repo_root(), Path(args.checkout))
        print(f"{remotecentral_importer.IMPORT_ROOT}/: {report.remotes:,} remotes, {report.keys:,} keys; "
              f"see {remotecentral_importer.IMPORT_ROOT}/{remotecentral_importer.REPORT}")
        return EXIT_OK

    if args.source == "official":
        # Manufacturers' documents are pinned as snapshots, not as a repository (D123).
        from .official import importer as official_importer

        report = official_importer.write_import(_repo_root(), Path(args.checkout))
        print(f"{official_importer.IMPORT_ROOT}/: {report.remotes:,} remotes, {report.keys:,} keys; "
              f"see {official_importer.IMPORT_ROOT}/{official_importer.REPORT}")
        return EXIT_OK

    if args.source == "hifi-remote":
        # Not a git repository: the pin is a snapshot directory (D111).
        report = hifiremote_importer.write_import(_repo_root(), Path(args.checkout))
        imported = sum(n for k, n in report.keys.items() if k.startswith("imported"))
        print(f"{hifiremote_importer.IMPORT_ROOT}/: {report.remotes['imported']:,} remotes, "
              f"{imported:,} keys; see {hifiremote_importer.IMPORT_ROOT}/{hifiremote_importer.REPORT}")
        return EXIT_OK

    module = {
        "lirc": lirc_importer, "smartir": smartir_importer,
        "irblaster": irblaster_importer, "jp1": jp1_importer,
    }[args.source]

    checkout = Path(args.checkout)
    try:
        head = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValidationError(f"{checkout} is not a git checkout: {exc}") from exc
    if args.commit and not head.startswith(args.commit):
        raise ValidationError(
            f"{checkout} is at {head}, not the requested {args.commit}"
        )
    # An importer that reads one named file must read the commit's version of
    # it, or every citation would name a tree the data did not come from.
    source = getattr(module, "INPUT", None)
    if source is not None:
        dirty = subprocess.run(
            ["git", "-C", str(checkout), "status", "--porcelain", "--", source],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if dirty:
            raise ValidationError(
                f"{checkout}/{source} differs from the commit {head[:7]}; "
                "commit or restore it so the pinned commit names the data"
            )
    report = module.write_import(_repo_root(), checkout, head)
    imported = sum(n for k, n in report.keys.items() if k.startswith("imported"))
    print(f"{module.IMPORT_ROOT}/: {report.remotes['imported']:,} remotes, "
          f"{imported:,} keys; see {module.IMPORT_ROOT}/{module.REPORT}")
    return EXIT_OK


def _unavailable(name: str, phase: int) -> int:
    print(
        f"`rl {name}` is not implemented until Phase {phase} "
        f"(see DESIGN.md section 8).",
        file=sys.stderr,
    )
    return EXIT_UNAVAILABLE


def _jobs_argument(text: str) -> int:
    try:
        return parallel.parse_jobs(text, source="--jobs")
    except ValidationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


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
    common.add_argument(
        "-j", "--jobs", type=_jobs_argument, default=argparse.SUPPRESS, metavar="N",
        help="worker processes for the per-remote loops; 1 runs everything in "
             f"this process. Default: ${parallel.ENV_JOBS}, else the usable "
             f"CPUs up to {parallel.MAX_AUTO_JOBS}. Output never depends on it",
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

    ix = sub.add_parser(
        "index", help="regenerate build/index.json and build/index/ (R14, D69)",
        parents=[common]
    )
    ix.add_argument("--check", action="store_true", help="diff instead of write")
    ix.set_defaults(func=cmd_index)

    lu = sub.add_parser(
        "lookup", help="find a remote by device or model (R16)", parents=[common]
    )
    lu.add_argument("query", nargs="+")
    lu.set_defaults(func=cmd_lookup)

    im = sub.add_parser(
        "import", help="import an upstream database under SPEC R19", parents=[common]
    )
    im.add_argument("source", choices=["lirc", "smartir", "irblaster", "hifi-remote", "jp1", "official", "remotecentral"],
                     help="a source meeting SPEC R19's five conditions")
    im.add_argument("checkout", help="a git checkout of the upstream source "
                                     "(for hifi-remote: its snapshot, sources/hifi-remote; for official: "
                                     "sources/official; for remotecentral: sources/remotecentral)")
    im.add_argument("--commit", help="refuse unless the checkout is at this commit")
    im.set_defaults(func=cmd_import)

    st = sub.add_parser("site", help="generate site/ (R17)", parents=[common])
    st.add_argument("--check", action="store_true", help="diff instead of write")
    st.set_defaults(func=cmd_site)

    ap = sub.add_parser(
        "app", help="generate site/app/v1/, the SwiftRemote app's API (D74)",
        parents=[common])
    ap.add_argument("--check", action="store_true", help="diff instead of write")
    ap.set_defaults(func=cmd_app)

    bu = sub.add_parser(
        "bundle", parents=[common],
        help="build the catalog bundle an app ships: a SQLite file, notices and a manifest (D88)")
    bu.add_argument("--profile", choices=["selected", "full"], default="selected",
                    help="selected: what an app ships, every brand since D117, no size limit; "
                         "full: every brand (D92)")
    bu.add_argument("--out", metavar="DIR",
                    help="where to write (default: bundle-out/<profile>, which is ignored by "
                         "version control); never inside build/ or site/")
    bu.add_argument("--check", action="store_true",
                    help="compare a fresh build with the files in --out instead of writing")
    bu.add_argument("--verify", metavar="DIR",
                    help="check the bundle in DIR against the tree and decode every signal")
    bu.add_argument("--max-bytes", type=int, metavar="N",
                    help="fail when the bundle is larger (no limit by default, D117)")
    bu.set_defaults(func=cmd_bundle)
    bu_sub = bu.add_subparsers(dest="bundle_command")
    bs = bu_sub.add_parser("sign", parents=[common],
                           help="sign manifest.json of DIR with an ECDSA P-256 key (D93)")
    bs.add_argument("--key", required=True, metavar="KEY.pem",
                    help="the private key (never committed)")
    bs.add_argument("directory", metavar="DIR")
    bs.set_defaults(func=cmd_bundle)
    bv = bu_sub.add_parser("verify-signature", parents=[common],
                           help="check manifest.sig of DIR and the files the manifest lists")
    bv.add_argument("--pub", required=True, metavar="PUB.pem", help="the public key")
    bv.add_argument("directory", metavar="DIR")
    bv.set_defaults(func=cmd_bundle)
    bx = bu_sub.add_parser("vectors", parents=[common],
                           help="write the cross-language vectors of the signal table (D91)")
    bx.add_argument("--from", dest="source", metavar="DIR",
                    help="a bundle directory to take them from (default: build the full bundle)")
    bx.add_argument("--file", required=True, metavar="FILE", help="where to write them")
    bx.add_argument("--check", action="store_true", help="compare instead of writing")
    bx.set_defaults(func=cmd_bundle)
    bm = bu_sub.add_parser("matching-vectors", parents=[common],
                           help="write the cross-language vectors of the matcher (D97)")
    bm.add_argument("--file", required=True, metavar="FILE", help="where to write them")
    bm.add_argument("--check", action="store_true", help="compare instead of writing")
    bm.set_defaults(func=cmd_bundle)
    bg = bu_sub.add_parser("suggest-vectors", parents=[common],
                           help="write the cross-language vectors of suggest (D100, D106)")
    bg.add_argument("--file", required=True, metavar="FILE", help="where to write them")
    bg.add_argument("--check", action="store_true", help="compare instead of writing")
    bg.set_defaults(func=cmd_bundle)
    be = bu_sub.add_parser(
        "search-eval", parents=[common],
        help="score the matcher on generated and hand-written queries over a bundle (D98)")
    be.add_argument("--bundle", metavar="DIR",
                    help="a bundle directory (default: bundle-out/selected)")
    be.add_argument("--queries", metavar="FILE",
                    help="hand-written real queries (default: bundle/data/real_queries.json)")
    be.add_argument("--seed", type=int, default=1, help="seed of the generated queries")
    be.add_argument("--per-class", type=int, default=40, metavar="N",
                    help="devices for each of the eight classes of generated query")
    be.add_argument("--out", metavar="FILE", help="also write the report here")
    be.add_argument("--timing", action="store_true", help="add the time each query took")
    be.set_defaults(func=cmd_bundle)

    ky = sub.add_parser(
        "keys", help="the canonical key vocabulary (D83)", parents=[common]
    )
    ky_sub = ky.add_subparsers(dest="keys_command", required=True)
    kr = ky_sub.add_parser(
        "report", parents=[common],
        help="how much of the corpus the vocabulary maps, per source (D86)",
    )
    kr.add_argument("--json", action="store_true", help="print the report as JSON")
    kr.set_defaults(func=cmd_keys_report)

    return p


#: Flags accepted on either side of the subcommand (see build_parser).
GLOBAL_FLAGS = ("strict", "allow_dirty")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for flag in GLOBAL_FLAGS:
        if not hasattr(args, flag):
            setattr(args, flag, False)
    # Set for this call only, so a count given to one call cannot leak into the
    # next one made in the same process (the test suite makes many).
    parallel.configure(getattr(args, "jobs", None))
    try:
        return args.func(args)
    except LedgerError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        parallel.configure(None)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
