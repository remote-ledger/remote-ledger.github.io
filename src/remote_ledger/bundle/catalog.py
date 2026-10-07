"""The catalog of the bundle: brands, models, remotes, signals, keys (D89 to D92).

A pure function of the records ``corpus.read_corpus`` reads. It has two passes:

1. :func:`collect` looks at **every** remote and gathers what a selection rule
   may use (models, remotes and keys per brand) and what the excluded-brands
   table needs (every brand's spellings);
2. :func:`assemble` takes the brands a profile chose and builds the rows of every
   table for them and nothing else.

Identity. A **remote** has one id for the whole ledger (its position in path
order, from 1), so the full bundle and the selected one number a remote alike
and an id the backend's matcher returns means the same remote in either; ``ref``
(its path under ``remotes/`` without ``.json``) is its name across ledger
versions. Brand, model, signal and vocabulary ids are numbered inside one
bundle, from 1, in the order the rows are inserted, which is a sort.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from ..app_api import brand_key
from ..keys import load_vocabulary, squash
from .aliases import Alias, rows_for
from .corpus import SOURCE_ID, RemoteRecord, is_synthetic_model, play
from .textnorm import search_norm

#: ``models.kind``: a product a person owns, and a remote's own part number.
KIND_DEVICE, KIND_REMOTE = 0, 1

#: The length of a gram of ``ngram`` (D90).
GRAM = 3


def grams(norm: str) -> set[str]:
    """The grams of a search key: every ``GRAM`` characters of ``^`` + key + ``$``.

    The two marks cannot occur in a key (it holds letters and digits only), so a
    gram that starts with ``^`` is the start of a key and one that ends in ``$``
    is its end; a key of one or two characters still has a gram. An empty key
    has none."""
    if not norm:
        return set()
    padded = f"^{norm}$"
    return {padded[i:i + GRAM] for i in range(len(padded) - GRAM + 1)}


def varints(ids: list[int]) -> bytes:
    """Ascending ids as the differences between neighbours (the first from 0),
    each an unsigned LEB128 varint: seven bits a byte, low group first, the high
    bit set on every byte but the last."""
    out = bytearray()
    previous = 0
    for value in ids:
        delta = value - previous
        previous = value
        while delta > 0x7F:
            out.append((delta & 0x7F) | 0x80)
            delta >>= 7
        out.append(delta)
    return bytes(out)


def unvarints(blob: bytes) -> list[int]:
    """The inverse of :func:`varints`."""
    ids: list[int] = []
    value = shift = total = 0
    for byte in blob:
        value |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            continue
        total += value
        ids.append(total)
        value = shift = 0
    return ids


def pairs_of(record: RemoteRecord) -> list[tuple[str, str, int]]:
    """The ``(brand, model, kind)`` rows a remote adds to the catalog.

    * the IR Blaster import files each remote under a list of products, brand and
      model kept apart (D56b): those, as devices;
    * for every other source ``controls`` is free text: each entry is a device of
      the remote's manufacturer;
    * the remote's own ``model`` and aliases are its part numbers (``KIND_REMOTE``),
      except where the model is a placeholder the importer made up
      (``corpus.is_synthetic_model``).
    """
    out: list[tuple[str, str, int]] = []
    if record.control_pairs is not None:
        out += [(b, m, KIND_DEVICE) for b, m in record.control_pairs]
    else:
        out += [(record.manufacturer, c, KIND_DEVICE) for c in record.controls]
    if not is_synthetic_model(record.source):
        out.append((record.manufacturer, record.model, KIND_REMOTE))
        out += [(record.manufacturer, a, KIND_REMOTE) for a in record.aliases]
    return out


def model_key(brand_norm: str, model: str) -> tuple[str, str]:
    """What makes two models one: the brand and the search key of the name. A name
    with no letter or digit has no key, and stands for itself."""
    return brand_norm, search_norm(model) or f"\0{model}"


@dataclass
class BrandStats:
    """What a selection rule may look at for one brand (all of the ledger)."""

    norm: str
    #: Spelling -> number of times it is written (remotes and products).
    spellings: Counter = field(default_factory=Counter)
    #: The exact names the IR Blaster import writes for it (the app API's brands).
    api_names: set[str] = field(default_factory=set)
    models: set[tuple[str, str]] = field(default_factory=set)
    remotes: int = 0
    keys: int = 0
    mapped_keys: int = 0

    @property
    def name(self) -> str:
        """The spelling most used, the first in code point order among equals: a
        deterministic pick that follows the data (``ORION`` over ``orion``)."""
        return min(self.spellings, key=lambda s: (-self.spellings[s], s))


@dataclass
class Collected:
    records: list[RemoteRecord]
    #: ``(brand norm of the manufacturer, [(brand norm, brand, model, kind)])`` per record.
    pairs: list[tuple[str, list[tuple[str, str, str, int]]]]
    brands: dict[str, BrandStats]


def collect(records: list[RemoteRecord]) -> Collected:
    brands: dict[str, BrandStats] = {}

    def stats(name: str) -> BrandStats:
        norm = search_norm(name) or f"\0{name}"
        entry = brands.get(norm)
        if entry is None:
            entry = brands[norm] = BrandStats(norm)
        entry.spellings[name] += 1
        return entry

    pairs: list[tuple[str, list[tuple[str, str, str, int]]]] = []
    for record in records:
        maker = stats(record.manufacturer)
        rows = []
        touched = {maker.norm: maker}
        for brand, model, kind in pairs_of(record):
            entry = stats(brand)
            touched[entry.norm] = entry
            entry.models.add(model_key(entry.norm, model))
            if record.source == "irblaster":
                entry.api_names.add(brand)
            rows.append((entry.norm, brand, model, kind))
        pairs.append((maker.norm, rows))
        mapped = sum(1 for k in record.keys if k[2] is not None)
        for entry in touched.values():
            entry.remotes += 1
            entry.keys += len(record.keys)
            entry.mapped_keys += mapped
    return Collected(records, pairs, brands)


@dataclass
class Assembled:
    """The rows of every table of one bundle, in insertion order."""

    profile: str
    sources: list[dict]
    vocab_groups: list[tuple]
    vocab_keys: list[tuple]
    brands: list[tuple]            # (id, name, norm, first_model, model_count)
    models: list[tuple]            # (id, brand_id, name, kind)
    controls: list[tuple]          # (model_id, remote_id)
    remotes: list[tuple]
    keys: list[tuple]              # (remote_id, n, canon, label, signal_id, confidence)
    signals: list[tuple]           # (id, blob)
    ngram: list[tuple]             # (gram, blob)
    excluded: list[tuple]          # (norm, name, api_key)
    stats: dict = field(default_factory=dict)
    brand_aliases: list[tuple] = field(default_factory=list)   # (alias, norm, brand_id) (D101)


def label_needed(text: str, canon: str | None, spellings: dict[str, set[str]]) -> bool:
    """A key's text is stored when it says something the canonical key does not: the
    key has no canonical id, or the text is not a spelling of the canonical key's id
    or display name (D89)."""
    if canon is None:
        return True
    return squash(text) not in spellings[canon]


def assemble(collected: Collected, chosen: frozenset[str] | None, profile: str,
             sources: list[dict], aliases: Iterable[Alias] = ()) -> Assembled:
    """The rows for the brands in ``chosen`` (every brand when ``None``). ``aliases`` are the
    brand aliases (D101): those of a brand the bundle carries become rows of ``brand_aliases``."""
    aliases = tuple(aliases)
    records = collected.records
    vocab = load_vocabulary()

    # -- the vocabulary: ids in reading order (D83), so the id is a position ---------
    group_ids = {g.id: i + 1 for i, g in enumerate(vocab.groups)}
    vocab_groups = [(group_ids[g.id], g.id, g.name, g.order) for g in vocab.groups]
    vocab_keys = []
    canon_ids: dict[str, int] = {}
    for key in vocab.keys:
        canon_ids[key.id] = len(canon_ids) + 1
        vocab_keys.append((canon_ids[key.id], key.id, group_ids[key.group], key.order,
                           key.name, key.icon, key.glyph, key.color, int(key.repeat)))
    spellings = {k.id: {squash(k.id), squash(k.name)} for k in vocab.keys}

    # -- which remotes -----------------------------------------------------------------
    included: list[int] = []
    for i, (maker, rows) in enumerate(collected.pairs):
        if chosen is None or maker in chosen or any(r[0] in chosen for r in rows):
            included.append(i)

    # -- brands and models -------------------------------------------------------------
    brand_spell: dict[str, Counter] = defaultdict(Counter)
    model_spell: dict[tuple[str, str], Counter] = defaultdict(Counter)
    model_kind: dict[tuple[str, str], int] = {}
    model_remotes: dict[tuple[str, str], set[int]] = defaultdict(set)
    maker_brands: set[str] = set()
    for i in included:
        maker, rows = collected.pairs[i]
        if chosen is None or maker in chosen:
            maker_brands.add(maker)
        for norm, brand, model, kind in rows:
            if chosen is not None and norm not in chosen:
                continue
            key = model_key(norm, model)
            brand_spell[norm][brand] += 1
            model_spell[key][model] += 1
            model_kind[key] = min(kind, model_kind.get(key, kind))
            model_remotes[key].add(i + 1)
    for norm in maker_brands:
        brand_spell.setdefault(norm, Counter({collected.brands[norm].name: 1}))

    brand_norms = sorted(brand_spell)
    brand_id = {norm: n + 1 for n, norm in enumerate(brand_norms)}
    model_keys = sorted(model_spell, key=lambda k: (brand_id[k[0]], k[1]))
    models, controls, model_id = [], [], {}
    first_model: dict[str, int] = {}
    model_count: Counter = Counter()
    gram_ids: dict[str, list[int]] = defaultdict(list)
    for key in model_keys:
        mid = model_id[key] = len(model_id) + 1
        first_model.setdefault(key[0], mid)
        model_count[key[0]] += 1
        counts = model_spell[key]
        name = min(counts, key=lambda s: (-counts[s], s))
        models.append((mid, brand_id[key[0]], name, model_kind[key]))
        for rid in sorted(model_remotes[key]):
            controls.append((mid, rid))
        if not key[1].startswith("\0"):
            for gram in grams(key[1]):
                gram_ids[gram].append(mid)
    ngram = [(gram, varints(ids)) for gram, ids in sorted(gram_ids.items())]
    brands = []
    for norm in brand_norms:
        # the name is the one the whole ledger uses most, not the one the subset does;
        # a brand's models are the rows first_model .. first_model + model_count - 1
        brands.append((brand_id[norm], collected.brands[norm].name,
                       "" if norm.startswith("\0") else norm,
                       first_model.get(norm, len(model_id) + 1), model_count[norm]))

    # -- signals and keys ---------------------------------------------------------------
    distinct: set[bytes] = set()
    for i in included:
        for key in records[i].keys:
            distinct.add(key[4])
    ordered = sorted(distinct)
    signal_id = {blob: n + 1 for n, blob in enumerate(ordered)}
    signals = [(n + 1, blob) for n, blob in enumerate(ordered)]

    remotes, keys = [], []
    per_source: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for i in included:
        record = records[i]
        rid = i + 1
        repeat, helper, empty, rule = play(record)
        maker = collected.pairs[i][0]
        remotes.append((
            rid, record.where[len("remotes/"):-len(".json")],
            brand_id.get(maker),
            None if is_synthetic_model(record.source) else record.model,
            SOURCE_ID[record.source], record.tier, len(record.keys), record.protocol,
            record.carrier_hz, repeat, helper, empty, rule,
        ))
        per_source[record.source][0] += 1
        per_source[record.source][1] += len(record.keys)
        for n, (name, label, canon, conf, blob) in enumerate(record.keys):
            text = label if label is not None else name
            stored = text if label_needed(text, canon, spellings) else None
            keys.append((rid, n, canon_ids[canon] if canon else None, stored,
                         signal_id[blob], conf))

    # -- the brands left out ---------------------------------------------------------------
    excluded = []
    for norm in sorted(collected.brands):
        if norm in brand_spell:
            continue
        entry = collected.brands[norm]
        api = ",".join(sorted(brand_key(n) for n in entry.api_names)) or None
        excluded.append((norm, entry.name, api))

    # -- the brand aliases: those of a brand this bundle carries ------------------------------
    alias_rows = rows_for(aliases, brand_id)
    for source in sources:
        count = per_source.get(source["key"], [0, 0])
        source["bundleRemotes"], source["bundleKeys"] = count
    return Assembled(
        profile=profile, sources=sources, vocab_groups=vocab_groups, vocab_keys=vocab_keys,
        brands=brands, models=models, controls=controls, remotes=remotes, keys=keys,
        signals=signals, ngram=ngram, excluded=excluded,
        stats={"ledgerRemotes": len(records), "ledgerBrands": len(collected.brands),
               "aliasesListed": len(aliases),
               # a brand the list names that the whole ledger does not have, and one it has and
               # this bundle does not carry
               "aliasBrandsMissing": sorted({a.brand for a in aliases if a.brand_key not in collected.brands}),
               "aliasesLeftOut": sum(1 for a in aliases
                                     if a.brand_key in collected.brands and a.brand_key not in brand_id)},
        brand_aliases=alias_rows,
    )
