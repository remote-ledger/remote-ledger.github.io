"""The generator registry (D19).

``build/`` and ``site/`` are wholly owned by the generators. Each declares the
path it owns; ``--check`` diffs the union of the paths whose generators are
registered, which is what lets the gate turn on in Phase 3 and widen by itself
in Phases 5 and 6 with no staging logic.

**The order is fixed; membership follows this registry.** ``rl build`` runs
``validate`` then each *registered* generator in order -- it never invokes a
stage that does not exist yet. An unowned path under ``build/`` or ``site/``
is an orphan by definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Generator:
    #: Pipeline stage name, in execution order.
    name: str
    #: Repo-relative path or directory this generator wholly owns.
    owns: str
    #: Phase that registers it, for the message an unregistered stage prints.
    phase: int
    #: Writes its artifact into ``root``. None until the phase lands.
    run: Callable[[Path, Path], None] | None = None

    @property
    def registered(self) -> bool:
        return self.run is not None


#: Declared in pipeline order. `compile` and `check` register in Phase 3,
#: `index` in Phase 5, `site` in Phase 6 (DESIGN.md section 8).
PIPELINE: tuple[Generator, ...] = (
    Generator(name="check", owns="build/warnings.json", phase=3),
    Generator(name="compile", owns="build/pronto", phase=3),
    Generator(name="index", owns="build/index.json", phase=5),
    Generator(name="site", owns="site", phase=6),
)


def registered() -> tuple[Generator, ...]:
    return tuple(g for g in PIPELINE if g.registered)


def owned_paths() -> tuple[str, ...]:
    return tuple(g.owns for g in registered())
