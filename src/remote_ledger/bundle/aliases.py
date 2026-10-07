"""Brand aliases: the other names a person types for a brand (D101).

The catalog spells every brand in Latin letters (``HISENSE``, ``Skyworth``), so a person who
types 海信 or 創維 finds nothing. ``data/brand_aliases.json`` lists, for a brand of the catalog,
the names it is also typed as, **each written out as it is typed**: ``创维`` and ``創維`` are two
entries, ``索尼`` and ``新力`` two more. Nothing converts one script to another when a person
searches, so a name that is missing from the list is not found, and a mistake in it is a mistake
in one entry and nowhere else.

The file is ledger data like the key vocabulary (``keys.py``): a schema, a validator
(:func:`semantic_problems` over a document that conforms, :func:`catalog_problems` against the
brands of the ledger) and a loader that refuses a list that breaks a rule. Its shape is
``{"header": {...}, "brands": [{"brand": ..., "aliases": [{"name": ...}, ...]}, ...]}``; the
exporter reads ``brand``, ``aliases``, ``name`` and the header's ``status`` and
``intentional_shared_aliases``, and **any other field is information for the person who reviews
the list and is ignored** (``script``, ``region`` and ``confidence`` of a name, a brand's ``models``
and ``remotes``, the header's ``method`` and ``date``). The exporter writes the table
``brand_aliases`` of the bundle from it (``catalog.assemble``) and the matcher reads it
(``matching.MatchIndex``). The rules of the data, each a message of the validator:

1. an alias has a **search key** (``textnorm.search_norm``) of at least ``BRAND_MIN_KEY``
   characters: the matcher never looks a key of one character up, so a one-character alias would
   be dead data;
2. an alias key holds **no letter or digit of ASCII**. This is what keeps a search typed in Latin
   letters and digits exactly what it was before aliases existed: no such search can equal an
   alias's key, or start it, so none is read through one (D101). A name that mixes the two
   (``TCL电视``) is not an alias in this version;
3. no alias key is listed twice under one brand;
4. **an alias key under two brands is an error unless the header declares it**: its
   ``intentional_shared_aliases`` maps the alias to the brands that share it (one company that the
   catalog spells two ways, ``WESTERN DIGITAL`` and ``wd``), and must name exactly the brands the
   file lists it under; a declaration of an alias that is not shared is an error too. A shared
   alias names every one of its brands;
5. no alias key is the key of a brand of the catalog (checked when a bundle is built, against
   every brand of the ledger, not only those the bundle carries): a name that is already a
   brand's is that brand's, and the same key under two brands would be a guess;
6. a brand is listed once, by its search key.

A brand the list names and the catalog does not have is **reported, not an error** (the list is
written ahead of the catalog, and the owner extends it): its aliases are left out of the bundle.
A brand the catalog has and a profile does not carry has its aliases left out of that profile
silently, as its models are.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..errors import ValidationError
from ..serialize import load
from ..validate import Problem, schema_problems
from .textnorm import search_norm

ALIASES_FILE = Path(__file__).resolve().parent / "data" / "brand_aliases.json"
SCHEMA = "brand_aliases.schema.json"
#: Where the file is, for a message.
REPO_PATH = "src/remote_ledger/bundle/data/brand_aliases.json"


@dataclass(frozen=True)
class Alias:
    #: The brand as the list spells it, and its search key.
    brand: str
    brand_key: str
    alias: str
    #: The alias's search key.
    key: str
    #: Information for a reviewer (``script``, ``region``, ``confidence``): never read by a search.
    script: str | None = None
    region: str | None = None
    confidence: str | None = None


def make(brand: str, alias: str, script: str | None = None, region: str | None = None,
         confidence: str | None = None) -> Alias:
    """An alias with its keys made: what the loader does for an entry of the file."""
    return Alias(brand, search_norm(brand), alias, search_norm(alias), script, region, confidence)


def _ascii_alnum(key: str) -> bool:
    return any(c.isascii() and c.isalnum() for c in key)


def _declared(doc: Any) -> dict[str, tuple[str, dict[str, str]]]:
    """The header's shared aliases: an alias key to ``(the alias as written, brand key -> the brand as
    the header spells it)``."""
    out: dict[str, tuple[str, dict[str, str]]] = {}
    for alias, brands in (doc["header"].get("intentional_shared_aliases") or {}).items():
        key = search_norm(alias)
        spelled = {search_norm(b): b for b in brands}
        out[key] = (out[key][0], {**out[key][1], **spelled}) if key in out else (alias, spelled)
    return out


def semantic_problems(doc: Any, where: str = REPO_PATH) -> Iterator[Problem]:
    """The relations a schema cannot say, over a document that conforms to it (rules 1 to 4 and 6)."""
    from ..matching import BRAND_MIN_KEY      # not at the top: the matcher imports the bundle's catalog
    brands: dict[str, str] = {}                      # a brand's search key -> the spelling that has it
    owners: dict[str, dict[str, tuple[str, str]]] = {}   # an alias key -> brand key -> (brand, alias)
    for number, entry in enumerate(doc["brands"]):
        brand = entry["brand"]
        at = f"{where}['brands'][{number}]"
        brand_key = search_norm(brand)
        if not brand_key:
            yield Problem(at, f"{brand!r} has no letter or digit, so it names no brand")
            continue
        if brand_key in brands:
            yield Problem(at, f"{brand!r} is {brands[brand_key]!r} again (the same search key "
                              f"{brand_key!r}); list a brand once")
            continue
        brands[brand_key] = brand
        for i, item in enumerate(entry["aliases"]):
            alias = item["name"]
            here = f"{at}['aliases'][{i}]"
            key = search_norm(alias)
            if not key:
                yield Problem(here, f"{alias!r} has no letter or digit, so it names nothing")
            elif len(key) < BRAND_MIN_KEY:
                yield Problem(here, f"{alias!r} has a search key shorter than {BRAND_MIN_KEY} characters; "
                                    "a search never looks up a name that short, so it could never be found")
            elif _ascii_alnum(key):
                yield Problem(here, f"{alias!r} holds a letter or digit of ASCII; an alias is a name "
                                    "in another script, so that a search typed in Latin letters is "
                                    "never read through one (D101)")
            elif key == brand_key:
                yield Problem(here, f"{alias!r} is the brand's own name")
            elif brand_key in owners.get(key, {}):
                yield Problem(here, f"{alias!r} has the search key {key!r}, which {brand!r} already has "
                                    f"as {owners[key][brand_key][1]!r}; it is listed twice")
            else:
                owners.setdefault(key, {})[brand_key] = (brand, alias)
    declared = _declared(doc)
    shared_at = f"{where}['header']['intentional_shared_aliases']"
    for key in sorted(owners):
        holders = owners[key]
        if len(holders) > 1 and key not in declared:
            names = " and ".join(repr(brands[b]) for b in holders)
            yield Problem(f"{where}['brands']", f"{next(iter(holders.values()))[1]!r} is listed under {names}: "
                          "an alias names one brand unless the header's intentional_shared_aliases "
                          "says that brands share it on purpose")
        elif key in declared and set(holders) != set(declared[key][1]):
            said = " and ".join(sorted(repr(b) for b in declared[key][1].values()))
            has = " and ".join(sorted(repr(brands[b]) for b in holders))
            yield Problem(shared_at, f"{declared[key][0]!r} is declared shared by {said} but the list has it "
                                     f"under {has}")
    for key in sorted(declared):
        if key not in owners:
            yield Problem(shared_at, f"{declared[key][0]!r} is declared shared but no brand of the list has it")


def catalog_problems(aliases: Iterable[Alias], brand_keys: Iterable[str]) -> list[Problem]:
    """Rule 5: an alias that is the search key of a brand of the catalog (``brand_keys`` are the
    keys of every brand of the ledger)."""
    keys = set(brand_keys)
    return [
        Problem(f"{REPO_PATH}['brands'][{a.brand!r}]",
                f"{a.alias!r} is the name of a brand of the catalog (the search key {a.key!r}); "
                "a brand's own name is already that brand's, and an alias must not decide between two")
        for a in aliases if a.key in keys
    ]


def aliases_from(doc: Any) -> tuple[Alias, ...]:
    """The aliases of a document that passed the validator, in the file's order."""
    return tuple(make(entry["brand"], item["name"], item.get("script"), item.get("region"), item.get("confidence"))
                 for entry in doc["brands"] for item in entry["aliases"])


def problems_of(path: Path = ALIASES_FILE) -> tuple[Any, list[Problem]]:
    """``(document, problems)`` of a list: the schema first and alone, then the relations."""
    where = REPO_PATH if path == ALIASES_FILE else path.as_posix()
    try:
        doc = load(path)
    except OSError as exc:
        return None, [Problem(where, f"could not be read: {exc.strerror or exc}")]
    except Exception as exc:  # a parse error, or one of D28's refusals
        return None, [Problem(where, f"could not be parsed: {exc}")]
    problems = list(schema_problems(doc, SCHEMA, where))
    if not problems:
        problems = list(semantic_problems(doc, where))
    return doc, problems


def load_aliases(path: Path = ALIASES_FILE) -> tuple[Alias, ...]:
    """The aliases of the list at ``path``, or a ValidationError that lists what is wrong."""
    doc, problems = problems_of(path)
    if problems:
        raise ValidationError("the brand aliases are invalid:\n  " + "\n  ".join(map(str, problems)))
    return aliases_from(doc)


@lru_cache(maxsize=None)
def shipped_aliases() -> tuple[Alias, ...]:
    """The list that ships, validated once per process."""
    return load_aliases()


def rows_for(aliases: Iterable[Alias], brand_ids: dict[str, int]) -> list[tuple[str, str, int]]:
    """The rows of the bundle's ``brand_aliases``: ``(alias, key, brand id)`` for each alias whose
    brand the bundle carries (``brand_ids`` is brand key to id), sorted by key and then by brand.
    An alias that two brands share is a row for each."""
    rows: dict[tuple[str, int], str] = {}
    for a in aliases:
        if a.brand_key in brand_ids:
            rows.setdefault((a.key, brand_ids[a.brand_key]), a.alias)
    return [(alias, key, brand) for (key, brand), alias in sorted(rows.items())]
