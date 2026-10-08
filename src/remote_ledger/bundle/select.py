"""Which brands a profile carries (D92).

Two profiles:

* ``full``: every brand, every remote. It is what a backend serves, and what the
  other profile is cut from.
* ``selected``: the profile an app ships as an asset. Since D117 it has no
  budget and no floor, so it carries every brand, and the rule below only says in
  which order they are taken; a budget (:data:`BUDGET_BYTES`) and a floor
  (:data:`MIN_MODELS`, :data:`MIN_MAPPED_SHARE`) are still honoured when set, which
  is how the tests and a future owner narrow it again (D92 to D95 describe the
  profile as it was: at most 20 MB, 36 brands). Every number in the rule is a
  constant of this file, and the brands it prefers are a plain-text file the owner
  edits (``data/selected_brands.txt``). The rule, in order:

  1. **The curated list**, in the file's order, which is the priority. Each brand
     is carried whole (all its models, remotes and signals) if what it adds still
     fits the budget :data:`BUDGET_BYTES` (none, now); one that does not fit is
     skipped and the next is tried (the report names every skipped line).
  2. **The proxy fill.** The ledger has no popularity data, so the brands that are
     not on the list are ranked by the best proxy it does have: the number of
     **models** a brand is listed with, per kilobyte the brand costs (a brand with
     thousands of keys for a few models is a maker of generic replacement remotes,
     not a popular one). A brand needs at least :data:`MIN_MODELS` models and
     :data:`MIN_MAPPED_SHARE` of its keys mapped to a canonical key (a remote most
     of whose keys fall into a "More" group is a poor first screen) to be
     considered, and is added, in that order, while it fits what is left of the
     budget.

The "share of keys with signals" the owner suggested as a proxy is the same for
every brand here: each key the ledger holds has a compiled signal. The share of
keys that map to a canonical key stands in for it as the data-quality proxy.

A remote is carried when **any** of its brands is chosen; a model is carried when
its brand is. A remote that is in because of one brand keeps all its keys and its
signals; the models it controls under brands that were not chosen are not in the
bundle, and those brands are in ``excluded_brands``.

Everything is a function of the catalog, the two files and these numbers: the
same tree gives the same brands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .catalog import BrandStats, Collected
from .textnorm import search_norm

PROFILES = ("selected", "full")

#: The most bytes the ``selected`` profile may be: ``None``, no limit (D117). The
#: owner asked for all the data and for the size not to be a factor, so a build
#: does not fail for its size. It was 20,000,000 (the app's whole install near 30 MB,
#: D92), then 20,250,000 (D116). ``rl bundle --max-bytes N`` still sets one.
SELECTED_MAX_BYTES: int | None = None
#: What the estimate below may add up to: ``None``, no budget (D117), so every brand
#: of the list fits and the proxy fill takes every other brand. It was 19,000,000, then
#: 19,609,000 (D116). The estimate leaves out the small tables (the notices' licence
#: texts, the vocabulary, the list of brands left out: about 0.3 MB) and is within 3%
#: of the file, not exact. A budget makes the rule greedy: it skips what does not fit,
#: and a bigger one is not worth what it adds (D116 has the scan).
BUDGET_BYTES: int | None = None
#: The floor a brand outside the list needs to be taken by the proxy fill: at least
#: this many models, and this share of its keys mapped to a canonical key. 40 and 0.60
#: until D117, which took the floor away, because it left out 4,427 brands, 2,460
#: remotes and 91,992 keys (17%), among them Topping.
MIN_MODELS = 0
MIN_MAPPED_SHARE = 0.0

#: What a row costs, in bytes of the SQLite file, measured on the full bundle
#: (D95): a key row, a remote row with its ``ref``, and a model with its
#: ``controls`` rows, its place in the brand index and its grams. A signal costs
#: its blob and eight.
KEY_BYTES = 21
REMOTE_BYTES = 110
MODEL_BYTES = 56
SIGNAL_ROW_BYTES = 8

CURATED_FILE = Path(__file__).resolve().parent / "data" / "selected_brands.txt"


@dataclass
class Selection:
    profile: str
    #: The search keys of the brands chosen; ``None`` means every brand.
    chosen: frozenset[str] | None
    #: Brand names (as the catalog spells them), in the order they were taken.
    curated: list[str] = field(default_factory=list)
    proxy_added: list[str] = field(default_factory=list)
    #: Lines of the list that match no brand of the catalog, and the brands of the
    #: list that did not fit the budget.
    unresolved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    estimated_bytes: int = 0
    rule: str = "every brand"


def read_curated(text: str) -> list[str]:
    """The names of a curated file: one per line, a ``#`` starting a comment, blank
    lines ignored."""
    names = []
    for line in text.splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return names


def choose(collected: Collected, profile: str, curated_text: str | None = None) -> Selection:
    """The brands of ``profile`` over ``collected`` (every record of the ledger)."""
    if profile == "full":
        return Selection("full", None)
    if profile != "selected":
        raise ValueError(f"unknown profile {profile!r}; choose one of {PROFILES}")
    if curated_text is None:
        curated_text = CURATED_FILE.read_text(encoding="utf-8")

    brands = collected.brands
    by_brand: dict[str, list[int]] = {}
    for i, (maker, rows) in enumerate(collected.pairs):
        for norm in {maker, *(r[0] for r in rows)}:
            by_brand.setdefault(norm, []).append(i)

    included: set[int] = set()
    signals: set[bytes] = set()
    used = 0

    def marginal(norm: str, remotes: set[int], blobs: set[bytes]) -> tuple[int, set[int], set[bytes]]:
        """What adding a brand costs, given the remotes and signals already in, and
        the remotes and signals it would add."""
        cost = MODEL_BYTES * len(brands[norm].models)
        new_remotes: set[int] = set()
        new_blobs: set[bytes] = set()
        for i in by_brand.get(norm, ()):
            if i in remotes:
                continue
            new_remotes.add(i)
            record = collected.records[i]
            cost += REMOTE_BYTES + KEY_BYTES * len(record.keys)
            for key in record.keys:
                blob = key[4]
                if blob not in blobs and blob not in new_blobs:
                    new_blobs.add(blob)
                    cost += len(blob) + SIGNAL_ROW_BYTES
        return cost, new_remotes, new_blobs

    chosen: set[str] = set()
    skipped: set[str] = set()
    selection = Selection("selected", None)

    def take(norm: str) -> bool:
        nonlocal used, included, signals
        cost, remotes, blobs = marginal(norm, included, signals)
        if BUDGET_BYTES is not None and used + cost > BUDGET_BYTES:
            return False
        used += cost
        included |= remotes
        signals |= blobs
        chosen.add(norm)
        return True

    for name in read_curated(curated_text):
        norm = search_norm(name) or f"\0{name}"
        if norm not in brands:
            selection.unresolved.append(name)
        elif norm in chosen or norm in skipped:
            continue
        elif take(norm):
            selection.curated.append(brands[norm].name)
        else:
            skipped.add(norm)
            selection.skipped.append(brands[norm].name)

    def eligible(stats: BrandStats) -> bool:
        return (stats.norm not in chosen and stats.norm not in skipped
                and len(stats.models) >= MIN_MODELS and stats.keys > 0
                and stats.mapped_keys / stats.keys >= MIN_MAPPED_SHARE)

    ranked = []
    for stats in brands.values():
        if eligible(stats):
            alone = marginal(stats.norm, set(), set())[0]
            ranked.append((-len(stats.models) / alone, stats.norm))
    for _, norm in sorted(ranked):
        if take(norm):
            selection.proxy_added.append(brands[norm].name)

    selection.chosen = frozenset(chosen)
    selection.estimated_bytes = used
    budget = "no budget" if BUDGET_BYTES is None else f"{BUDGET_BYTES:,} bytes"
    selection.rule = (
        f"{len(selection.curated)} brands of the curated list, then {len(selection.proxy_added)} "
        f"by models per byte (at least {MIN_MODELS} models, {MIN_MAPPED_SHARE:.0%} of keys mapped), "
        f"estimated {used:,} bytes of {budget}"
    )
    return selection
