"""The search test set and its harness: ``rl bundle search-eval`` (D98).

It asks, of a written bundle: if a person types a device the way people type, does the
matcher (``matching.py``, D96) over the bundle's own search structures offer the remote
that controls it, as the first answer or among the first five?

**The queries** are made from the bundle by a deterministic generator, and there are eight
**classes**, each a mechanical change to a device's own catalog name (a device is a
``kind`` 0 model of at least six characters of search key with a digit, a name of at most
40 characters, and at most two devices of one brand in a class, so that no big brand is the
test set):

``exact``
    the brand and the model as the catalog spells them: ``SAMSUNG UN50NU6900F``;
``case``
    the same in lower case, upper case or title case;
``punctuation``
    the model without its separators, or with spaces at every letter-digit boundary, or with a
    hyphen at the first: ``UN50NU6900F``, ``UN 50 NU 6900 F``, ``UN-50NU6900F``;
``dropped-suffix``
    the model without its trailing letters (``UN50NU6900``), for a model that has them;
``typo``
    the model with one character dropped, or two neighbours swapped, not at the start;
``brand-partial``
    the brand and the first 60% of the model's characters (at least five);
``model-only``
    the model alone;
``reversed``
    the model and then the brand.

**A hit.** A query expects the remote ids that the bundle's ``controls`` give its device; an
answer hits when it is a model (not only a brand) whose remote ids **contain all** of them.
Top-1 is the first answer; top-5 is any of the first five (the matcher returns five at most).

**What this is not.** The queries are the catalog's own names changed by a rule. They are
friendlier than what people type: they never misspell a brand, never use a name the catalog
does not hold, never write a nickname or a model from memory, and the exact class is a lookup.
**The rates it prints are a measure of the matcher on these changes and an upper bound on its
quality in use, not an estimate of it.** The report says so, and that is why hand-written
queries of real people are scored too: ``bundle/data/real_queries.json`` (format in D98) is
read by default, ``--queries FILE`` reads another, and they are reported in a table of their
own.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ..matching import MatchIndex
from .textnorm import search_norm

CLASSES = ("exact", "case", "punctuation", "dropped-suffix", "typo", "brand-partial",
           "model-only", "reversed")
#: Devices per class, and of one brand in a class.
PER_CLASS = 40
PER_BRAND = 2
MIN_KEY, MAX_NAME = 6, 40
SEED = 1
REAL_QUERIES = Path(__file__).resolve().parent / "data" / "real_queries.json"

REAL_ABOUT = (
    "Hand-written queries of real people, scored by `rl bundle search-eval` next to the "
    "generated ones (DESIGN.md D98). Each entry is {\"query\": what was typed, \"expect\": "
    "{\"brand\": the catalog's brand, \"model\": the catalog's model} or null, \"note\": who "
    "and where}. `expect` names a device the way the catalog spells it (case, spaces and "
    "punctuation do not matter): the remotes that control it are what the answer must "
    "contain. null means the query names nothing the catalog has, and is right when the "
    "answer holds no model. A device that is not in the bundle being scored is counted as "
    "not in it and left out of the rates.")


@dataclass(frozen=True)
class Query:
    text: str
    cls: str
    #: The remote ids the answer must contain; None for a query that must find no model.
    expect: tuple[int, ...] | None
    brand: str = ""
    model: str = ""


def _rank(seed: int, *parts: str) -> bytes:
    return hashlib.sha256(("\0".join((str(seed), *parts))).encode()).digest()


def devices(conn: sqlite3.Connection) -> list[tuple[int, str, str, str, tuple[int, ...]]]:
    """``(model id, brand name, brand key, model name, remote ids)`` of every device of the
    bundle that the generator may use."""
    remotes: dict[int, list[int]] = {}
    for model_id, remote_id in conn.execute("SELECT model_id, remote_id FROM controls ORDER BY remote_id"):
        remotes.setdefault(model_id, []).append(remote_id)
    out = []
    for model_id, brand, brand_key, name in conn.execute(
            "SELECT m.id, b.name, b.norm, m.name FROM models m JOIN brands b ON b.id = m.brand_id "
            "WHERE m.kind = 0 ORDER BY m.id"):
        key = search_norm(name)
        if (len(key) >= MIN_KEY and len(name) <= MAX_NAME and any(c.isdigit() for c in key)
                and brand_key and model_id in remotes):
            out.append((model_id, brand, brand_key, name, tuple(remotes[model_id])))
    return out


_SUFFIX = re.compile(r"^(.*[0-9])([A-Za-z]{1,2})$")


def variant(cls: str, brand: str, name: str, pick: int) -> str | None:
    """The text of one class for a device, or None when the device has no such variant.
    ``pick`` is a number from a hash that chooses among a class's styles."""
    key = search_norm(name)
    if cls == "exact":
        return f"{brand} {name}"
    if cls == "case":
        text = f"{brand} {name}"
        return (text.lower(), text.upper(), text.title())[pick % 3]
    if cls == "punctuation":
        bare = re.sub(r"[\W_]+", "", name)
        styles = [
            f"{brand} {bare}",
            f"{brand} {re.sub(r'(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])', ' ', bare)}",
            f"{brand} {re.sub(r'(?<=[A-Za-z])(?=[0-9])', '-', bare, count=1)}",
        ]
        text = styles[pick % 3]
        return text if text != f"{brand} {name}" else None
    if cls == "dropped-suffix":
        match = _SUFFIX.match(name)
        return f"{brand} {match.group(1)}" if match and len(search_norm(match.group(1))) >= 6 else None
    if cls == "typo":
        if len(key) < 7:
            return None
        inner = [i for i, c in enumerate(name) if c.isalnum()][1:]           # not the first character
        swaps = [i for i in inner if i + 1 < len(name) and name[i + 1].isalnum()
                 and name[i] != name[i + 1]]
        which = pick // 2
        if pick % 2 and swaps:
            j = swaps[which % len(swaps)]
            return f"{brand} {name[:j]}{name[j + 1]}{name[j]}{name[j + 2:]}"
        i = inner[which % len(inner)]
        return f"{brand} {name[:i]}{name[i + 1:]}"
    if cls == "brand-partial":
        if len(key) < 8:
            return None
        want = max(5, (len(key) * 6) // 10)
        seen = 0
        for i, c in enumerate(name):
            seen += c.isalnum()
            if seen == want:
                return f"{brand} {name[:i + 1]}"
        return None
    if cls == "model-only":
        return name
    if cls == "reversed":
        return f"{name} {brand}"
    raise ValueError(cls)


def generate(conn: sqlite3.Connection, *, seed: int = SEED, per_class: int = PER_CLASS) -> list[Query]:
    """The synthetic queries of a bundle: ``per_class`` for each class, chosen by a hash
    of the seed and the device (a different order for each class: the hash is xor-ed with one
    of the seed and the class), at most ``PER_BRAND`` devices of a brand in a class."""
    pool = devices(conn)
    base = [int.from_bytes(_rank(seed, d[2], str(d[0]))[:8], "big") for d in pool]
    out: list[Query] = []
    for cls in CLASSES:
        mask = int.from_bytes(_rank(seed, cls)[:8], "big")
        keys = [b ^ mask for b in base]
        order = sorted(range(len(pool)), key=keys.__getitem__)       # ties keep the order of ids
        used: dict[str, int] = {}
        taken = 0
        for i in order:
            model_id, brand, brand_key, name, remotes = pool[i]
            if taken == per_class:
                break
            if used.get(brand_key, 0) >= PER_BRAND:
                continue
            pick = int.from_bytes(_rank(seed, cls, "pick", str(model_id))[:4], "big")
            text = variant(cls, brand, name, pick)
            if text is None:
                continue
            used[brand_key] = used.get(brand_key, 0) + 1
            taken += 1
            out.append(Query(text, cls, remotes, brand, name))
    return out


# --- the hand-written queries ------------------------------------------------------------------


def read_real(path: Path, conn: sqlite3.Connection) -> tuple[list[Query], int, int]:
    """``(queries, not in this bundle, entries read)`` of a file of real queries (the
    format of ``REAL_ABOUT``). A device the bundle does not have is counted and left out."""
    if not path.is_file():
        return [], 0, 0
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("queries", [])
    queries: list[Query] = []
    missing = 0
    for entry in entries:
        expect = entry.get("expect")
        if expect is None:
            queries.append(Query(entry["query"], "real", None))
            continue
        row = conn.execute(
            "SELECT m.id, m.name, b.name FROM brands b JOIN models m "
            "ON m.id BETWEEN b.first_model AND b.first_model + b.model_count - 1 "
            "WHERE b.norm = ? ORDER BY m.id", (search_norm(expect["brand"]),)).fetchall()
        ids = [i for i, name, _ in row if search_norm(name) == search_norm(expect["model"])]
        remotes = tuple(r for (r,) in conn.execute(
            "SELECT remote_id FROM controls WHERE model_id = ? ORDER BY remote_id", (ids[0],))) if ids else ()
        if not remotes:
            missing += 1
            continue
        queries.append(Query(entry["query"], "real", remotes, expect["brand"], expect["model"]))
    return queries, missing, len(entries)


# --- scoring --------------------------------------------------------------------------------------


def hits(query: Query, answers: list) -> tuple[bool, bool]:
    """``(top-1, top-5)``: whether the first answer, and any of the first five, is a model
    whose remote ids contain the query's; for a query that must find no model, whether the
    answer holds no model."""
    if query.expect is None:
        nothing = not any(c.model is not None for c in answers)
        return nothing, nothing
    wanted = set(query.expect)

    def good(candidate) -> bool:
        return candidate.model is not None and wanted <= set(candidate.remote_ids)

    return bool(answers) and good(answers[0]), any(good(c) for c in answers[:5])


@dataclass
class Result:
    query: Query
    top1: bool
    top5: bool
    answer: tuple[str, ...]


def evaluate(index: MatchIndex, queries: list[Query]) -> tuple[list[Result], list[float]]:
    """Run every query as a typed text; the results and the seconds each took."""
    results, timings = [], []
    for query in queries:
        started = time.perf_counter()
        answers = index.match(None, None, [query.text])
        timings.append(time.perf_counter() - started)
        top1, top5 = hits(query, answers)
        results.append(Result(query, top1, top5, tuple(
            f"{c.brand} | {c.model}" if c.model else f"{c.brand} (brand)" for c in answers[:5])))
    return results, timings


def table(results: list[Result], classes: tuple[str, ...]) -> list[str]:
    def row(label: str, subset: list[Result]) -> str:
        n = len(subset)
        one = sum(r.top1 for r in subset)
        five = sum(r.top5 for r in subset)
        pct = lambda k: f"{100 * k / n:.1f}%" if n else "-"          # noqa: E731
        return f"| {label} | {n} | {one} ({pct(one)}) | {five} ({pct(five)}) |"

    lines = ["| class | queries | top-1 | top-5 |", "|---|---|---|---|"]
    lines += [row(c, [r for r in results if r.query.cls == c]) for c in classes]
    lines.append(row("**all**", results))
    return lines


CAVEAT = (
    "**Read these rates as an upper bound.** The generated queries are the catalog's own names "
    "changed by eight mechanical rules. They never misspell a brand, never name a device the "
    "catalog does not have, never use a nickname or a model written from memory, and the exact "
    "class is a lookup. Real people type worse, so the real rate is lower by an amount this "
    "table does not measure. The table below it scores hand-written queries of real people "
    "(`src/remote_ledger/bundle/data/real_queries.json`, format in DESIGN.md D98); it is only "
    "as good as the queries somebody has put there."
)


def report(bundle: Path, *, queries: Path | None = None, seed: int = SEED,
           per_class: int = PER_CLASS, timing: bool = False) -> str:
    """The Markdown report for the bundle in ``bundle`` (a directory with ``catalog.sqlite``
    and ``manifest.json``)."""
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    index = MatchIndex.open(bundle / "catalog.sqlite")
    conn = index.conn
    synthetic = generate(conn, seed=seed, per_class=per_class)
    results, timings = evaluate(index, synthetic)
    out = [
        f"# Search evaluation of the {manifest['profile']} bundle, data version {manifest['dataVersion']}",
        "",
        f"{len(synthetic)} generated queries, seed {seed}, {per_class} devices for each of "
        f"{len(CLASSES)} classes, from {manifest['counts']['models']:,} models of "
        f"{manifest['counts']['brands']:,} brands. A query is typed text; it hits when an answer "
        "is a model whose remote ids contain all the remotes the catalog gives the device.",
        "",
        CAVEAT,
        "",
        *table(results, CLASSES),
        "",
    ]
    real, missing, entries = read_real(queries or REAL_QUERIES, conn)
    out.append("## Hand-written queries of real people")
    out.append("")
    if real:
        real_results, real_timings = evaluate(index, real)
        timings += real_timings
        out += table(real_results, ("real",))
        out.append("")
        out.append(f"{len(real)} scored, {missing} of {entries} entries name a device this bundle does "
                   "not have and are left out of the rates.")
        for r in real_results:
            if not r.top5:
                out.append(f"- missed: `{r.query.text}` gave {', '.join(r.answer) or 'nothing'}")
    else:
        out.append("None yet" + (f" ({entries} entries, all for devices this bundle does not have)"
                                 if entries else "")
                   + ". Add queries to `src/remote_ledger/bundle/data/real_queries.json`; "
                   "DESIGN.md D98 has the format.")
    out.append("")
    misses = [r for r in results if not r.top5]
    out.append(f"## Generated queries with no hit in the first five ({len(misses)})")
    out.append("")
    for r in misses[:25]:
        out.append(f"- `{r.query.text}` ({r.query.cls}): {', '.join(r.answer) or 'nothing'}")
    if len(misses) > 25:
        out.append(f"- and {len(misses) - 25} more")
    if timing and timings:
        ordered = sorted(timings)
        out += ["", "## Timing (not part of the deterministic report)", "",
                f"{len(ordered)} queries on the bundle, one thread: median "
                f"{1000 * ordered[len(ordered) // 2]:.1f} ms, 95th percentile "
                f"{1000 * ordered[int(len(ordered) * 0.95)]:.1f} ms, slowest {1000 * ordered[-1]:.1f} ms."]
    return "\n".join(out) + "\n"
