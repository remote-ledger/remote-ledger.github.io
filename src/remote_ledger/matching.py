"""Matching what a person or a provider says to the catalog (D96).

A query is a ``brand``, a ``model`` and some visible ``texts`` (any may be empty): a
search box gives one text, a service that reads a photo gives what a provider read off it.
The answer is up to five **candidates**, ``Candidate(brand, model, remote_ids, score,
evidence)``, best first. It runs over the bundle's own search structures (D90), so the
same rules are the reference for a search on a phone and for a service that turns a
provider's output into catalog entries.

**Everything is integers.** A score is an integer of thousandths, so a port to another
language gets the same numbers and the same order, not the same numbers to the last bit of
a float. ``Candidate.score`` is that integer over 1000.

**The rules, in the order they apply**

1. *Keys and tokens.* The **key** of a text is the bundle's search key (``search_norm``,
   D90: NFKD, lower case, letters and digits only). Its **tokens** are the runs of letters
   and digits of the same NFKD lower-case text, combining marks dropped, so the key of a
   text is its tokens joined. ``UN50-NU 6900/F`` has the key ``un50nu6900f`` and the tokens
   ``un``, ``50``, ``nu``, ``6900``, ``f``.
2. *Similarity of two keys* (``similarity``), in thousandths, between a query key ``q`` and
   a catalog key ``m``:

   * equal: 1000;
   * **edit**: ``1000 - 1000 x d / max(len q, len m)``, rounded down, where ``d`` is the
     optimal-string-alignment distance (insert, delete, substitute and swap two neighbours
     each cost 1) except that a substitution between the look-alikes ``o``/``0``,
     ``i``/``1``, ``l``/``1``, ``s``/``5``, ``b``/``8`` and ``z``/``2`` costs a half. A
     look-alike is cheaper, never free, so two different keys are never equal and a brand
     is never read through it (it applies to models only);
   * **prefix**: when the shorter key has at least 5 characters and is the start of the
     longer, ``800 + 200 x len(shorter) / len(longer)``, rounded down. This is the boost for
     a dropped suffix (``un50nu6900`` for ``un50nu6900f``) and for a label that goes on
     past the catalog's entry (``un50nu6900fxza``);
   * the larger of the two. A pair counts only at **800** or more, which is 20% of the
     longer key: a key of 4 characters must be exact, 5 to 9 may differ by one, 10 to 14 by
     two.
3. *The brand.* ``brand`` is looked up by its key: an exact brand scores 1000, otherwise
   brands that share three-character grams with it and score 800 or more by the rule above
   (``Samsung Electronics`` is ``samsung`` at 878). Its runs of tokens (rule 4) that are
   exactly a brand's key score 900. A run of any text that is exactly a brand's key, of 2
   characters or more, scores 800. The brands found are the **named brands**; those that
   ``brand`` gave are the **given** ones, and a brand named only by a text is a hint, because a
   text holds incidental words (``DVD`` is a brand).
4. *The model keys.* A **run** is one to eight adjacent tokens of one line, its key the tokens
   joined. The key of all of ``model`` weighs 1000, and so does each of its runs (a model
   written with spaces, or after its brand). Of every text the runs weigh 900, but only
   those of 4 to 24 characters, and of those only the ones that contain a digit (a model
   number has one; ``model`` does not) are tried among all the models; one without a digit
   is tried only among the models of the named brands, and never when it is itself a brand's
   key (so ``roku ultra`` finds Roku's Ultra, and ``samsung`` is not a model). The first 60
   keys are kept, in the order of the lines and then of the runs, and tried **longest first**;
   a run that lies inside a longer run that gave a candidate by the exact or the edit rule at a
   similarity of 900 or more (rule 6) is not tried, so ``bdp-s360`` is the model ``bdps360`` and
   not also the other brand's ``s360``, while a poor match of the long run, or a short entry that
   is only the start of it, does not hide an exact match of a part of it.
5. *Candidates.* A model key's grams (D90) give the models that share at least 40% of them,
   the 40 sharing the most (ties to the lower id). If a brand is named, the same is done
   inside each named brand's models, so a brand's models that a global top 40 would miss are
   still found.
6. *Score.* A candidate's ``similarity`` is its best over the model keys, and counts only at
   800 or more; times the key's weight; times a **brand factor**: 1000 if its brand is a
   named one, 600 if a brand was *given* and it is not that one, 950 otherwise (no brand
   named, or only hinted at by a text). A candidate that scores under 650 is dropped. A catalog entry that is a remote's own part
   number is a candidate like any other (``kind:remote``).
7. *The answer.* Sorted by score, then similarity, then the longer catalog key (the more
   specific), then brand name, model name and id; five at most. **A model string counts only if it matches an entry** (rule 6), so an
   invented model number gives nothing, and **when no model matches but a brand is named**
   the answer is the brand: ``Candidate(brand, None, (), score, evidence, models)`` where
   ``models`` are its models with most remotes first (50 at most), and the score the brand's.

The evidence is a few words: ``model:exact``, ``model:prefix`` or ``model:edit``;
``via:model`` or ``via:text``; ``brand:given``, ``brand:text``, ``brand:none`` or
``brand:other`` (the factor 600); ``kind:remote`` for a part number; ``brand`` alone for a brand-only answer.

``matching_vectors.json`` (D97) holds the normalisation, the similarity and a small catalog
with queries and their answers, for a port.
"""

from __future__ import annotations

import heapq
import re
import sqlite3
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

from .bundle.catalog import GRAM, grams, unvarints, varints  # noqa: F401 (GRAM is stated in the vectors)
from .bundle.textnorm import search_norm

# --- the constants, all of them -----------------------------------------------------------

#: A pair of keys counts at this similarity (thousandths), for a model and for a brand.
THRESHOLD = 800
#: A candidate scores under this and is dropped.
MIN_SCORE = 650
#: The shorter key of a prefix match has at least this many characters; and the floor and
#: the span of what a prefix scores: ``PREFIX_BASE + PREFIX_SPAN x short / long``.
PREFIX_MIN = 5
PREFIX_BASE, PREFIX_SPAN = 800, 200
#: What swapping a look-alike costs, in halves of an edit (a plain edit is 2).
LOOKALIKE_HALVES = 1
LOOKALIKES = frozenset(
    pair for a, b in ("o0", "i1", "l1", "s5", "b8", "z2") for pair in ((a, b), (b, a)))
#: How a query key weighs: the model as given, and a text of the query.
WEIGHT_MODEL, WEIGHT_TEXT = 1000, 900
#: The weight of a brand found: as given, a token of the given brand, a token of a text.
BRAND_GIVEN, BRAND_GIVEN_PART, BRAND_TEXT = 1000, 900, 800
BRAND_MIN_KEY = 2
#: The factor a candidate's brand gives it.
FACTOR_NAMED, FACTOR_NONE, FACTOR_OTHER = 1000, 950, 600
#: Runs of adjacent tokens: at most this many; and the lengths a run of a text may have.
MAX_RUN = 8
TEXT_KEY_MIN, TEXT_KEY_MAX = 4, 24
#: A run inside a longer run that gave a candidate at this similarity is not tried.
COVER_MIN = 900
#: At most this many model keys of one query, and tokens of one line looked at.
MAX_KEYS = 60
MAX_TOKENS = 12
#: Candidates of one key: the most that share at least this share (percent) of its grams.
PER_KEY = 40
MIN_OVERLAP_PERCENT = 40
#: Candidates of a fuzzy brand search, and models listed for a brand-only answer.
BRAND_CANDIDATES = 5
BRAND_MODELS = 50
LIMIT = 5

_TOKEN = re.compile(r"[^\W_]+")


# --- normalisation -------------------------------------------------------------------------


def tokens(text: str) -> list[str]:
    """The runs of letters and digits of ``text``: NFKD, lower case, combining marks
    dropped. ``"".join(tokens(t)) == search_norm(t)`` for every text."""
    decomposed = unicodedata.normalize("NFKD", text).lower()
    plain = "".join(c for c in decomposed if not unicodedata.category(c).startswith("M"))
    return _TOKEN.findall(plain)


@dataclass(frozen=True)
class Run:
    """Adjacent tokens ``start`` to ``end`` (exclusive) of line number ``line``, and their key."""

    key: str
    line: int
    start: int
    end: int


def runs(words: Sequence[str], line: int = 0) -> list[Run]:
    """Every run of one to ``MAX_RUN`` adjacent tokens, in order of start and then of
    length; a key that comes twice is kept once, at its first place."""
    out: list[Run] = []
    seen: set[str] = set()
    for start in range(len(words)):
        key = ""
        for end in range(start, min(start + MAX_RUN, len(words))):
            key += words[end]
            if key not in seen:
                seen.add(key)
                out.append(Run(key, line, start, end + 1))
    return out


# --- similarity ------------------------------------------------------------------------------


def halves(a: str, b: str, limit: int | None = None, lookalikes: bool = True) -> int:
    """The distance of ``a`` and ``b`` in halves of an edit: optimal string alignment, an
    insertion, a deletion, a substitution and a swap of two neighbours cost 2 and a
    look-alike substitution ``LOOKALIKE_HALVES`` (unless ``lookalikes`` is False). With a ``limit`` it may stop as soon as the
    distance is certainly above it and return a number above it (an optimisation of the matcher,
    which never needs an exact distance beyond what could reach the threshold)."""
    n, m = len(a), len(b)
    if not n or not m:
        return 2 * (n or m)
    if limit is not None and 2 * abs(n - m) > limit:
        return limit + 1
    before: list[int] | None = None
    row = [2 * j for j in range(m + 1)]
    low = 0
    for i in range(1, n + 1):
        current = [2 * i] + [0] * m
        ca = a[i - 1]
        for j in range(1, m + 1):
            cb = b[j - 1]
            sub = 0 if ca == cb else (LOOKALIKE_HALVES if lookalikes and (ca, cb) in LOOKALIKES else 2)
            best = min(row[j] + 2, current[j - 1] + 2, row[j - 1] + sub)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb and before is not None:
                best = min(best, before[j - 2] + 2)
            current[j] = best
        if limit is not None:
            lowest = min(current)
            if lowest > limit and low > limit:
                return limit + 1
            low = lowest
        before, row = row, current
    return row[m]


def similarity(query: str, entry: str, *, cut: bool = False, lookalikes: bool = True) -> int:
    """The similarity of a query key and a catalog key, in thousandths (rule 2). With ``cut``
    a pair that cannot reach ``THRESHOLD`` may get any number below it, which is all the matcher
    asks of it and spares most of the distance. A brand is compared with ``lookalikes=False``."""
    if not query or not entry:
        return 0
    if query == entry:
        return 1000
    longest = max(len(query), len(entry))
    # the largest distance, in halves, that still leaves 1000 - ceil(500 h / L) >= THRESHOLD
    limit = (1000 - THRESHOLD) * longest // 500 if cut else None
    edit = 1000 - (500 * halves(query, entry, limit, lookalikes) + longest - 1) // longest
    short, long_ = (query, entry) if len(query) <= len(entry) else (entry, query)
    prefix = 0
    if len(short) >= PREFIX_MIN and long_.startswith(short):
        prefix = PREFIX_BASE + PREFIX_SPAN * len(short) // len(long_)
    return max(edit, prefix, 0)


# --- the answer ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One answer: a catalog entry and the remotes that control it."""

    brand: str
    #: None when only the brand matched; ``models`` then lists its models.
    model: str | None
    remote_ids: tuple[int, ...]
    #: Thousandths over 1000: ``round(score * 1000)`` is the exact integer.
    score: float
    evidence: tuple[str, ...]
    models: tuple[str, ...] = ()

    @property
    def permille(self) -> int:
        return round(self.score * 1000)


def kind(match_similarity: int, query: str, entry: str) -> str:
    """``exact``, ``prefix`` or ``edit``: which rule gave a similarity."""
    if match_similarity == 1000:
        return "exact"
    short, long_ = (query, entry) if len(query) <= len(entry) else (entry, query)
    if len(short) >= PREFIX_MIN and long_.startswith(short) and (
            match_similarity == PREFIX_BASE + PREFIX_SPAN * len(short) // len(long_)):
        return "prefix"
    return "edit"


# --- the index -----------------------------------------------------------------------------------


class MatchIndex:
    """The search structures of a bundle (or of ``from_entries``), read as the rules say.

    Brands are held in memory (a few thousand); models, their remotes and the posting lists
    of grams are read as a query needs them, each from the bundle's own tables, with small
    caches. Nothing is written."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.brands: dict[int, tuple[str, str, int, int]] = {}      # id -> name, norm, first, count
        self.brand_by_key: dict[str, int] = {}
        self.brand_grams: dict[str, list[int]] = {}
        for brand_id, name, norm, first, count in conn.execute(
                "SELECT id, name, norm, first_model, model_count FROM brands ORDER BY id"):
            self.brands[brand_id] = (name, norm, first, count)
            if norm:
                self.brand_by_key.setdefault(norm, brand_id)
                for gram in sorted(grams(norm)):
                    self.brand_grams.setdefault(gram, []).append(brand_id)
        self._postings = lru_cache(maxsize=2048)(self._read_postings)
        self._model = lru_cache(maxsize=4096)(self._read_model)
        self._remotes = lru_cache(maxsize=4096)(self._read_remotes)
        self._listing = lru_cache(maxsize=64)(self._read_listing)

    @classmethod
    def open(cls, bundle: str | Path) -> "MatchIndex":
        """The index of a bundle file, read-only."""
        path = Path(bundle)
        return cls(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                                   check_same_thread=False))

    @classmethod
    def from_entries(cls, entries: Iterable[dict]) -> "MatchIndex":
        """A small index from ``{"brand", "model", "remotes": [ids]}`` entries, built with
        the bundle's own functions for ids and grams (what the vectors of D97 use)."""
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.executescript("""
            CREATE TABLE brands (id INTEGER PRIMARY KEY, name TEXT, norm TEXT,
                                 first_model INTEGER, model_count INTEGER);
            CREATE TABLE models (id INTEGER PRIMARY KEY, brand_id INTEGER, name TEXT, kind INTEGER);
            CREATE TABLE controls (model_id INTEGER, remote_id INTEGER,
                                   PRIMARY KEY (model_id, remote_id)) WITHOUT ROWID;
            CREATE TABLE ngram (gram TEXT PRIMARY KEY, ids BLOB) WITHOUT ROWID;
        """)
        by_brand: dict[str, dict[str, tuple[str, set[int], int]]] = {}
        names: dict[str, str] = {}
        for entry in entries:
            norm = search_norm(entry["brand"])
            names.setdefault(norm, entry["brand"])
            slot = by_brand.setdefault(norm, {})
            key = search_norm(entry["model"])
            name, remotes, flag = slot.get(key, (entry["model"], set(), entry.get("kind", 0)))
            slot[key] = (name, remotes | set(entry["remotes"]), min(flag, entry.get("kind", 0)))
        gram_ids: dict[str, list[int]] = {}
        model_id = 0
        for brand_id, norm in enumerate(sorted(by_brand), 1):
            first = model_id + 1
            for key in sorted(by_brand[norm]):
                model_id += 1
                name, remotes, flag = by_brand[norm][key]
                conn.execute("INSERT INTO models VALUES (?,?,?,?)", (model_id, brand_id, name, flag))
                conn.executemany("INSERT INTO controls VALUES (?,?)",
                                 [(model_id, r) for r in sorted(remotes)])
                for gram in grams(key):
                    gram_ids.setdefault(gram, []).append(model_id)
            conn.execute("INSERT INTO brands VALUES (?,?,?,?,?)",
                         (brand_id, names[norm], norm, first, model_id - first + 1))
        conn.executemany("INSERT INTO ngram VALUES (?,?)",
                         [(g, varints(ids)) for g, ids in sorted(gram_ids.items())])
        return cls(conn)

    # -- reading ----------------------------------------------------------------------------

    def _read_postings(self, gram: str) -> tuple[int, ...]:
        row = self.conn.execute("SELECT ids FROM ngram WHERE gram = ?", (gram,)).fetchone()
        return tuple(unvarints(row[0])) if row else ()

    def _read_model(self, model_id: int) -> tuple[int, str, int]:
        return self.conn.execute(
            "SELECT brand_id, name, kind FROM models WHERE id = ?", (model_id,)).fetchone()

    def _read_remotes(self, model_id: int) -> tuple[int, ...]:
        return tuple(r for (r,) in self.conn.execute(
            "SELECT remote_id FROM controls WHERE model_id = ? ORDER BY remote_id", (model_id,)))

    def _read_listing(self, brand_id: int) -> tuple[str, ...]:
        """A brand's models, most remotes first, then by name."""
        _, _, first, count = self.brands[brand_id]
        if not count:
            return ()
        last = first + count - 1
        remotes = Counter(m for (m,) in self.conn.execute(
            "SELECT model_id FROM controls WHERE model_id BETWEEN ? AND ?", (first, last)))
        names = dict(self.conn.execute(
            "SELECT id, name FROM models WHERE id BETWEEN ? AND ?", (first, last)))
        top = sorted(names, key=lambda i: (-remotes[i], names[i], i))
        return tuple(names[i] for i in top[:BRAND_MODELS])

    # -- brands -------------------------------------------------------------------------------

    def _fuzzy_brands(self, key: str) -> list[tuple[int, int]]:
        """``(brand id, similarity)`` of the brands that share grams with ``key`` and are
        similar to it at ``THRESHOLD``, best first."""
        if len(key) < BRAND_MIN_KEY:
            return []
        shared: Counter = Counter()
        for gram in sorted(grams(key)):
            shared.update(self.brand_grams.get(gram, ()))
        found = []
        for brand_id, _ in sorted(shared.items(), key=lambda kv: (-kv[1], kv[0]))[:BRAND_CANDIDATES * 4]:
            score = similarity(key, self.brands[brand_id][1], cut=True, lookalikes=False)
            if score >= THRESHOLD:
                found.append((brand_id, score))
        found.sort(key=lambda t: (-t[1], t[0]))
        return found[:BRAND_CANDIDATES]

    def named_brands(self, brand: str | None, texts: Sequence[str]) -> dict[int, tuple[int, str]]:
        """The named brands (rule 3): ``{brand id: (weight, evidence)}``."""
        found: dict[int, tuple[int, str]] = {}

        def add(brand_id: int, weight: int, why: str) -> None:
            if brand_id not in found or found[brand_id][0] < weight:
                found[brand_id] = (weight, why)

        if brand and brand.strip():
            key = search_norm(brand)
            if key in self.brand_by_key:
                add(self.brand_by_key[key], BRAND_GIVEN, "brand:given")
            else:
                for brand_id, score in self._fuzzy_brands(key):
                    add(brand_id, min(score, BRAND_GIVEN), "brand:given")
            for part in runs(tokens(brand)[:MAX_RUN]):
                if len(part.key) >= BRAND_MIN_KEY and part.key in self.brand_by_key:
                    add(self.brand_by_key[part.key], BRAND_GIVEN_PART, "brand:given")
        for text in texts:
            for part in runs(tokens(text)[:MAX_TOKENS]):
                if len(part.key) >= BRAND_MIN_KEY and part.key in self.brand_by_key:
                    add(self.brand_by_key[part.key], BRAND_TEXT, "brand:text")
        return found

    # -- models ---------------------------------------------------------------------------------

    def _shared(self, key: str, span: tuple[int, int] | None = None) -> tuple[Counter, int]:
        """How many grams of ``key`` each model shares, and how many a candidate needs
        (``MIN_OVERLAP_PERCENT`` of them); only inside ``span`` (the first and last model id
        of a brand) if given."""
        wanted = sorted(grams(key))
        shared: Counter = Counter()
        for gram in wanted:
            ids = self._postings(gram)
            if span is not None:
                ids = ids[bisect_left(ids, span[0]):bisect_right(ids, span[1])]
            shared.update(ids)
        return shared, max((len(wanted) * MIN_OVERLAP_PERCENT + 99) // 100, 1)

    @staticmethod
    def _best(items: list[tuple[int, int]], span: tuple[int, int] | None = None) -> list[int]:
        """The ``PER_KEY`` models of ``items`` (model id, grams shared) that share the most, ties
        to the lower id; only those inside ``span`` if given."""
        if span is not None:
            items = [t for t in items if span[0] <= t[0] <= span[1]]
        return [i for i, _ in heapq.nsmallest(PER_KEY, items, key=lambda t: (-t[1], t[0]))]

    def model_keys(self, model: str | None, texts: Sequence[str]) -> list[tuple[Run, int, str, bool]]:
        """The model keys of a query (rule 4): ``(run, weight, via, within brands)``, at most
        ``MAX_KEYS``, in the order of the lines and of the runs. The whole of ``model`` is
        line 0. The last is True for a key to be tried only among the models of named brands."""
        out: list[tuple[Run, int, str, bool]] = []
        seen: set[str] = set()

        def add(run: Run, weight: int, via: str, within: bool = False) -> None:
            if run.key and run.key not in seen and len(out) < MAX_KEYS:
                seen.add(run.key)
                out.append((run, weight, via, within))

        if model and model.strip():
            words = tokens(model)[:MAX_TOKENS]
            add(Run("".join(words), 0, 0, len(words)), WEIGHT_MODEL, "via:model")
            for run in runs(words, 0):
                add(run, WEIGHT_MODEL, "via:model")
        for number, text in enumerate(texts, 1):
            for run in runs(tokens(text)[:MAX_TOKENS], number):
                if not TEXT_KEY_MIN <= len(run.key) <= TEXT_KEY_MAX:
                    continue
                if any(c.isdigit() for c in run.key):
                    add(run, WEIGHT_TEXT, "via:text")
                elif run.key not in self.brand_by_key:
                    add(run, WEIGHT_TEXT, "via:text", True)
        return out

    # -- the answer ---------------------------------------------------------------------------

    def match(self, brand: str | None = None, model: str | None = None,
              texts: Sequence[str] = (), limit: int = LIMIT) -> list[Candidate]:
        """The candidates for a query (the rules in the module's docstring)."""
        named = self.named_brands(brand, texts)
        given = any(why == "brand:given" for _, why in named.values())
        keyed = self.model_keys(model, texts)
        order = sorted(range(len(keyed)), key=lambda i: (-len(keyed[i][0].key), i))
        found: dict[int, tuple[int, int, int, str, str, str]] = {}   # id -> score, sim, length, rule, via, why
        covered: list[Run] = []
        for i in order:
            run, weight, via, within = keyed[i]
            if any(c.line == run.line and c.start <= run.start and run.end <= c.end
                   and (c.start, c.end) != (run.start, run.end) for c in covered):
                continue
            spans = [(first, first + count - 1) for brand_id in sorted(named)
                     for _, _, first, count in [self.brands[brand_id]] if count]
            pool: set[int] = set()
            if within:
                for span in spans:
                    shared, floor = self._shared(run.key, span)
                    pool.update(self._best([(i, n) for i, n in shared.items() if n >= floor]))
            else:
                shared, floor = self._shared(run.key)
                eligible = [(i, n) for i, n in shared.items() if n >= floor]
                pool.update(self._best(eligible))
                for span in spans:
                    pool.update(self._best(eligible, span))
            gave = False
            for model_id in sorted(pool):
                brand_id, name, flag = self._model(model_id)
                entry = search_norm(name)
                score = similarity(run.key, entry, cut=True)
                if score < THRESHOLD:
                    continue
                if brand_id in named:
                    factor, why = FACTOR_NAMED, named[brand_id][1]
                elif given:
                    factor, why = FACTOR_OTHER, "brand:other"
                else:
                    factor, why = FACTOR_NONE, "brand:none"
                total = score * weight // 1000 * factor // 1000
                if total < MIN_SCORE:
                    continue
                rule = kind(score, run.key, entry)
                gave = gave or (score >= COVER_MIN and rule != "prefix")
                best = found.get(model_id)
                if best is None or (total, score) > (best[0], best[1]):
                    found[model_id] = (total, score, len(entry), rule, via, why)
            if gave:
                covered.append(run)
        ranked = sorted(found.items(), key=lambda kv: (
            -kv[1][0], -kv[1][1], -kv[1][2], self.brands[self._model(kv[0])[0]][0],
            self._model(kv[0])[1], kv[0]))
        out = []
        for model_id, (total, score, _, rule, via, why) in ranked[:limit]:
            brand_id, name, flag = self._model(model_id)
            evidence = (f"model:{rule}", via, why) + (("kind:remote",) if flag == 1 else ())
            out.append(Candidate(self.brands[brand_id][0], name, self._remotes(model_id),
                                 total / 1000, evidence))
        if out or not named:
            return out
        ordered = sorted(named.items(), key=lambda kv: (-kv[1][0], kv[0]))
        return [Candidate(self.brands[b][0], None, (), weight / 1000, ("brand", why),
                          self._listing(b))
                for b, (weight, why) in ordered[:limit]]


def match(index: MatchIndex, brand: str | None = None, model: str | None = None,
          texts: Sequence[str] = (), limit: int = LIMIT) -> list[Candidate]:
    """``index.match`` as a function: the candidates for a brand, a model and some texts."""
    return index.match(brand, model, texts, limit)
