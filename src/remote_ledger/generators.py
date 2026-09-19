"""The generator registry and the whole-tree gate (D19, D20, D32).

``build/`` and ``site/`` are wholly owned by the generators. Each declares
the path it owns; ``--check`` diffs the union of the paths whose generators
are *registered*, which is what lets the gate turn on in Phase 3 and widen by
itself in Phases 5 and 6 with no staging logic.

**The order is fixed; membership follows this registry.** ``rl build`` runs
``validate`` then each registered generator in order -- it never invokes a
stage that does not exist yet. An unowned path under ``build/`` or ``site/``
is an orphan by definition, and the whole-tree diff catches it for free.
"""

from __future__ import annotations

import filecmp
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .check import check_remote
from .errors import LedgerError
from .remote import load_remote
from .serialize import dumps
from .validate import corpus_files
from .warnings import sorted_warnings


@dataclass(frozen=True)
class Generator:
    #: Pipeline stage name, in execution order.
    name: str
    #: Repo-relative path or directory this generator wholly owns.
    owns: str
    #: Phase that registers it, for the message an unregistered stage prints.
    phase: int
    #: Writes its artifact under ``out_root``. None until the phase lands.
    run: Callable[[Path, Path], list[str]] | None = None

    @property
    def registered(self) -> bool:
        return self.run is not None


def _artifact_path(out_root: Path, remote) -> Path:
    return (
        out_root / "build" / "pronto"
        / remote.manufacturer.lower().replace(" ", "-")
        / f"{remote.model}.json"
    )


def run_check(root: Path, out_root: Path) -> list[str]:
    """Cross-check the corpus and write ``build/warnings.json`` (D32).

    A warning never changes the exit code, but this artifact is committed --
    so a new one appears as a tree diff that ``rl build --check`` fails until
    it is acknowledged. That is why CI does not need ``--strict``.
    """
    problems: list[str] = []
    warnings = []
    for path in corpus_files(root):
        try:
            file_problems, file_warnings = check_remote(load_remote(path))
        except LedgerError as exc:
            problems.append(f"{path.as_posix()}: {exc}")
            continue
        problems += [f"{path.as_posix()}: {p}" for p in file_problems]
        warnings += file_warnings

    target = out_root / "build" / "warnings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        dumps({
            "schemaVersion": 1,
            "warnings": [w.to_json() for w in sorted_warnings(warnings)],
        }),
        encoding="utf-8", newline="\n",
    )
    return problems


def run_compile(root: Path, out_root: Path) -> list[str]:
    """Render each candidate group's trusted form (R12, D20)."""
    from .cli import compiled_artifact

    for path in corpus_files(root):
        remote = load_remote(path)
        target = _artifact_path(out_root, remote)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dumps(compiled_artifact(remote)), encoding="utf-8",
                          newline="\n")
    return []


#: Declared in pipeline order. `index` registers in Phase 5, `site` in Phase 6
#: (DESIGN.md section 8).
PIPELINE: tuple[Generator, ...] = (
    Generator(name="check", owns="build/warnings.json", phase=3, run=run_check),
    Generator(name="compile", owns="build/pronto", phase=3, run=run_compile),
    Generator(name="index", owns="build/index.json", phase=5),
    Generator(name="site", owns="site", phase=6),
)


def registered() -> tuple[Generator, ...]:
    return tuple(g for g in PIPELINE if g.registered)


def owned_paths() -> tuple[str, ...]:
    return tuple(g.owns for g in registered())


def _files_under(root: Path, owned: str) -> set[str]:
    target = root / owned
    if target.is_file():
        return {owned}
    if not target.is_dir():
        return set()
    return {
        p.relative_to(root).as_posix() for p in target.rglob("*") if p.is_file()
    }


def diff_tree(root: Path, fresh_root: Path) -> list[str]:
    """Diff the committed tree against a freshly generated one (D19).

    File set *and* contents. A file present in the committed tree but absent
    from the fresh one fails as an **orphan** -- rename a remote and its old
    compiled artifact would otherwise linger forever, a stale code with no
    source, which is precisely the failure OD4 accepted committed artifacts
    in exchange for avoiding.
    """
    problems: list[str] = []
    for owned in owned_paths():
        committed = _files_under(root, owned)
        generated = _files_under(fresh_root, owned)
        for orphan in sorted(committed - generated):
            problems.append(f"{orphan}: orphaned -- no generator produces it")
        for missing in sorted(generated - committed):
            problems.append(f"{missing}: missing from the committed tree")
        for shared in sorted(committed & generated):
            if not filecmp.cmp(root / shared, fresh_root / shared, shallow=False):
                problems.append(f"{shared}: drifted from freshly generated output")
    return problems
