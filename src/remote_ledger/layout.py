"""Layouts: parsing ``grid-template-areas`` by splitting on whitespace (D14).

R9's whole design is that a layout's ``areas`` array drops into a real CSS
``grid-template-areas`` rule untouched. That means the constraints are CSS's
own, not ours:

* every row has the same number of cells;
* each named area forms a **single rectangle**.

Enforcing those is matching CSS, not inventing rules -- a
``grid-template-areas`` string that violates either is simply invalid CSS,
and a browser would drop the whole declaration.

The known limit, named rather than glossed over: a single key cannot have an
L-shaped or disconnected footprint, and two keys can never overlap. Every
remote encountered so far decomposes cleanly into rows and columns anyway --
even a D-pad, which reads as a plus but is five ordinary cells with the
corners left empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ValidationError

#: D29. A legal CSS identifier, which also excludes `.` (a gap) and
#: whitespace (a cell separator), so a name can never be mistaken for grid
#: syntax.
KEY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GAP = "."


@dataclass(frozen=True)
class Grid:
    """A parsed ``areas`` array."""

    rows: tuple[tuple[str, ...], ...]

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    @property
    def names(self) -> set[str]:
        return {c for row in self.rows for c in row if c != GAP}

    def cells(self, name: str) -> list[tuple[int, int]]:
        return [
            (r, c)
            for r, row in enumerate(self.rows)
            for c, cell in enumerate(row)
            if cell == name
        ]

    def to_css(self) -> str:
        """Re-emit as a CSS declaration, which is the point of R9."""
        body = "\n".join(f'    "{" ".join(row)}"' for row in self.rows)
        return f"grid-template-areas:\n{body};"


def parse(areas: list[str], *, where: str = "layout") -> Grid:
    """Parse an ``areas`` array, enforcing CSS grid's own constraints."""
    if not areas:
        raise ValidationError(f"{where}: `areas` holds no rows")

    rows = tuple(tuple(row.split()) for row in areas)
    for i, row in enumerate(rows):
        if not row:
            raise ValidationError(f"{where}: row {i} is empty")

    width = len(rows[0])
    for i, row in enumerate(rows):
        if len(row) != width:
            raise ValidationError(
                f"{where}: row {i} has {len(row)} cells against row 0's "
                f"{width}. Every row of a grid-template-areas string has the "
                "same cell count -- CSS drops the whole declaration otherwise"
            )

    grid = Grid(rows=rows)
    for name in sorted(grid.names):
        if not KEY_NAME.match(name):
            raise ValidationError(
                f"{where}: area name {name!r} is not a legal CSS identifier "
                f"(must match {KEY_NAME.pattern}). R11 leans on CSS grid for "
                "collision-checking, which only holds while every name is one "
                "(D29)"
            )
        _check_rectangle(grid, name, where)
    return grid


def _check_rectangle(grid: Grid, name: str, where: str) -> None:
    """CSS requires a named area's cells to form one rectangle."""
    cells = grid.cells(name)
    rows = [r for r, _ in cells]
    cols = [c for _, c in cells]
    top, bottom, left, right = min(rows), max(rows), min(cols), max(cols)
    expected = (bottom - top + 1) * (right - left + 1)
    if len(cells) != expected:
        raise ValidationError(
            f"{where}: area {name!r} occupies {len(cells)} cells but spans a "
            f"{bottom - top + 1}x{right - left + 1} box, so it is not a single "
            "rectangle. CSS grid requires one; an L-shaped or split footprint "
            "is a known limit of R9, not an oversight (D14)"
        )


def layout_problems(name: str, layout: dict, keys: set[str], where: str):
    """Every ledger-specific check for one layout (D14, R8, R10)."""
    at = f"{where}:{name}"
    try:
        grid = parse(layout.get("areas") or [], where=at)
    except ValidationError as exc:
        yield str(exc)
        return

    for area in sorted(grid.names - keys):
        yield f"{at}: area {area!r} does not name a key in `keys` (R9)"

    # R10: an orphan in a sibling map renders nothing and reports nothing.
    for field in ("printedLabels", "shape"):
        for orphan in sorted(set(layout.get(field) or {}) - keys):
            yield (
                f"{at}: `{field}` has an entry for {orphan!r}, which is not a "
                "key in `keys`. These maps track the grid without being part "
                "of it, so an orphan here is invisible at render time (R10)"
            )


def layouts_problems(layouts: dict | None, keys: set[str], where: str):
    """Check every layout, plus R8's at-most-one-original rule."""
    if not layouts:
        return
    originals = sorted(n for n, l in layouts.items() if l.get("original"))
    if len(originals) > 1:
        yield (
            f"{where}: layouts {', '.join(repr(n) for n in originals)} all set "
            "`original: true`. Exactly one layout may be the factory "
            "arrangement -- two is a contradiction about a physical fact, and "
            "whichever a renderer picked would be arbitrary (R8)"
        )
    for name in sorted(layouts):
        yield from layout_problems(name, layouts[name], keys, where)
