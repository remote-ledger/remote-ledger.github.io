"""``rl keys report``: how far the canonical vocabulary reaches into the corpus (D86).

A key the vocabulary cannot name is shown by a client in its "More" group under
its own label, so what matters is not how many keys are named but **how many
remotes are left with a mostly empty layout**: the report counts, per source,
the remotes with at least 90, 75 and 50 percent of their keys mapped, beside the
share of keys.

The report is a function of the files under ``remotes/`` and of the vocabulary and
nothing else: no clock, no path outside the repository, every count and every
tie ordered. The same tree gives the same bytes at any worker count (R12).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from functools import partial
from pathlib import Path
from typing import Any

from . import paths
from .keys import Vocabulary, load_vocabulary, squash
from .parallel import ordered_map
from .serialize import dumps
from .validate import corpus_files

#: Sources in the order the report lists them. ``authored`` is everything outside
#: an import root (``paths.IMPORTS``).
SOURCES = ("irblaster", "lirc", "smartir", "authored")
#: The shares of mapped keys a remote is counted at (percent).
THRESHOLDS = (90, 75, 50)
#: A remote with fewer keys than this says little about coverage: one key mapped
#: is a remote of 100 percent.
MIN_KEYS = 10
UNMAPPED_LIMIT = 50
WORST_LIMIT = 20


@dataclass(frozen=True)
class RemoteKeys:
    """What the report needs of one remote file."""

    where: str
    source: str
    #: ``(key name, label or None)`` in file order.
    keys: tuple[tuple[str, str | None], ...]


def source_of(where: str) -> str:
    """``irblaster``, ``lirc``, ``smartir``, or ``authored``, from a repo-relative path."""
    root = paths.imported_from(where)
    return root.split("/")[1] if root else "authored"


def read_remote(root: Path, path: Path) -> RemoteKeys:
    """One remote's keys, read from its JSON (a worker's unit).

    The file is parsed, not loaded as a remote: the report needs two fields of each
    key, and loading would encode the signals of ten thousand files for nothing.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    where = paths.rel(root, path)
    return RemoteKeys(
        where, source_of(where),
        tuple((name, spec.get("label")) for name, spec in doc["keys"].items()),
    )


def read_corpus(root: Path) -> list[RemoteKeys]:
    """Every remote under ``root/remotes``, in the corpus's sorted order."""
    return list(ordered_map(partial(read_remote, root), corpus_files(root)))


def spelling(name: str, label: str | None) -> str:
    """The text of a key as the vocabulary reads it: its label, else its name."""
    return label if label is not None and label.strip() else name


def percent(part: int, whole: int) -> Decimal:
    """``part / whole`` in percent, rounded *down* to one decimal.

    Down, so that 89.97 is never printed as 90.0: a remote is at 90 percent when
    it is, and the report must not say it at a number that is not so.
    """
    return (Decimal(part * 1000 // whole) / 10 if whole else Decimal(0)).quantize(Decimal("0.1"))


def _share_row(remotes: list[tuple[int, int]]) -> dict[str, int]:
    """Counts over ``(keys, mapped)`` pairs, one per remote."""
    row = {
        "remotes": len(remotes),
        "keys": sum(n for n, _ in remotes),
        "mapped": sum(m for _, m in remotes),
    }
    for threshold in THRESHOLDS:
        row[f"atLeast{threshold}"] = sum(1 for n, m in remotes if m * 100 >= n * threshold)
    return row


def build_report(remotes: list[RemoteKeys], vocabulary: Vocabulary | None = None) -> dict[str, Any]:
    """The whole report, as plain data."""
    vocabulary = vocabulary or load_vocabulary()
    per_remote: list[tuple[RemoteKeys, int]] = []
    unmapped_keys = Counter()           # fold -> keys
    unmapped_remotes = Counter()        # fold -> remotes holding it
    unmapped_texts: dict[str, Counter] = {}
    for remote in remotes:
        mapped = 0
        seen: set[str] = set()
        for name, label in remote.keys:
            if vocabulary.canonical_id(name, label) is not None:
                mapped += 1
                continue
            text = spelling(name, label)
            fold = squash(text, vocabulary.tokens)
            unmapped_keys[fold] += 1
            unmapped_texts.setdefault(fold, Counter())[text] += 1
            seen.add(fold)
        for fold in seen:
            unmapped_remotes[fold] += 1
        per_remote.append((remote, mapped))

    report: dict[str, Any] = {
        "vocabulary": {
            "version": vocabulary.version,
            "keys": len(vocabulary.keys),
            "groups": len(vocabulary.groups),
            "aliases": sum(len(v) for v in vocabulary.aliases.values()),
        },
        "minKeys": MIN_KEYS,
        "sources": {},
        "sourcesWithMinKeys": {},
    }
    for tag in (*SOURCES, "all"):
        chosen = [(len(r.keys), m) for r, m in per_remote if tag == "all" or r.source == tag]
        if not chosen:
            continue
        report["sources"][tag] = _share_row(chosen)
        big = [pair for pair in chosen if pair[0] >= MIN_KEYS]
        report["sourcesWithMinKeys"][tag] = _share_row(big)

    ranked = sorted(unmapped_keys.items(), key=lambda item: (-item[1], item[0]))
    report["unmapped"] = []
    for fold, count in ranked[:UNMAPPED_LIMIT]:
        texts = unmapped_texts[fold]
        shown = min(texts, key=lambda t: (-texts[t], t))
        report["unmapped"].append({
            "text": shown, "fold": fold, "keys": count,
            "remotes": unmapped_remotes[fold], "spellings": len(texts),
        })
    report["unmappedTotal"] = {"keys": sum(unmapped_keys.values()), "names": len(unmapped_keys)}

    big_remotes = [(r, m) for r, m in per_remote if len(r.keys) >= MIN_KEYS]
    big_remotes.sort(key=lambda rm: (Fraction(rm[1], len(rm[0].keys)), -len(rm[0].keys), rm[0].where))
    report["worst"] = [
        {"remote": r.where, "source": r.source, "keys": len(r.keys), "mapped": m}
        for r, m in big_remotes[:WORST_LIMIT]
    ]
    return report


# --- rendering --------------------------------------------------------------------


def _cell(count: int, whole: int) -> str:
    return f"{percent(count, whole)}% ({count:,})"


def _table(rows: dict[str, dict[str, int]]) -> list[str]:
    head = ["source", "remotes", "keys", "keys mapped"] + [f">={t}% of keys" for t in THRESHOLDS]
    lines = [head]
    for tag, row in rows.items():
        lines.append([
            tag, f"{row['remotes']:,}", f"{row['keys']:,}", _cell(row["mapped"], row["keys"]),
            *(_cell(row[f"atLeast{t}"], row["remotes"]) for t in THRESHOLDS),
        ])
    widths = [max(len(line[i]) for line in lines) for i in range(len(head))]
    out = []
    for n, line in enumerate(lines):
        out.append("  ".join(
            cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
            for i, cell in enumerate(line)
        ).rstrip())
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    return out


def render_summary(report: dict[str, Any]) -> list[str]:
    """The two tables, as the lines DESIGN.md quotes (a test compares them)."""
    return [
        "All remotes:", "", *_table(report["sources"]), "",
        f"Remotes with at least {report['minKeys']} keys:", "", *_table(report["sourcesWithMinKeys"]),
    ]


def render_text(report: dict[str, Any]) -> str:
    v = report["vocabulary"]
    lines = [
        f"Canonical key vocabulary v{v['version']}: {v['keys']} keys in {v['groups']} groups, "
        f"{v['aliases']} aliases.", "",
        *render_summary(report), "",
        f"The {len(report['unmapped'])} most frequent unmapped names, of "
        f"{report['unmappedTotal']['names']:,} ({report['unmappedTotal']['keys']:,} keys):", "",
    ]
    for rank, row in enumerate(report["unmapped"], 1):
        more = f" (+{row['spellings'] - 1} spellings)" if row["spellings"] > 1 else ""
        lines.append(
            f"{rank:3}  {row['keys']:>7,} keys  {row['remotes']:>6,} remotes  "
            f"{json.dumps(row['text'], ensure_ascii=False)}{more}"
        )
    lines += ["", f"The {len(report['worst'])} remotes with the lowest coverage, "
              f"among those with at least {report['minKeys']} keys:", ""]
    for row in report["worst"]:
        lines.append(
            f"{percent(row['mapped'], row['keys']):>5}%  {row['mapped']:>4}/{row['keys']:<4} "
            f"{row['source']:<9} {row['remote']}"
        )
    return "\n".join(lines) + "\n"


def render_json(report: dict[str, Any]) -> str:
    """Sorted keys, two-space indent, one trailing newline (D20); percentages as decimals."""
    out = json.loads(json.dumps(report))
    for table in ("sources", "sourcesWithMinKeys"):
        for row in out[table].values():
            row["percentMapped"] = percent(row["mapped"], row["keys"])
            for t in THRESHOLDS:
                row[f"percentAtLeast{t}"] = percent(row[f"atLeast{t}"], row["remotes"])
    return dumps(out)
