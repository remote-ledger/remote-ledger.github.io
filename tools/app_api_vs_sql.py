#!/usr/bin/env python3
"""The app API against the database it replaces: what the old SQL queries
returned, rebuilt from ``site/app/v1/`` and compared (D80).

SwiftRemote asks its bundled sqlite seven questions (``lib/ir_finder/
irblaster_db.dart``). The API must answer each from static files with what the
database answered, for every row the ledger holds. This tool loads the SQL dump
(``assets/db_src/swiftremote.sql``) into memory, runs the app's own queries on
it, restricts the answers to the keys the ledger holds, and compares them with
what the API's files give. It reads the committed API and nothing else of the
ledger but ``remotes/irblaster/IMPORT.md``, whose skipped lists say which codes
the importer refused (a key is held unless its ``(DB protocol, hexcode)`` is in
them), so the generator's own parser is not what decides what is expected.

What is compared, per brand of the dump:

* the brand list, in ``ORDER BY name COLLATE NOCASE`` order (``listBrands``);
* the models (``listModelsDistinct``: ``ORDER BY model COLLATE NOCASE``), with
  and without a protocol filter;
* the protocols of the brand and of every (brand, model)
  (``listProtocolsForBrand``, ``listProtocolsFor``), as the API's masks say;
* for every (brand, model, protocol), the key rows as a set, de-duplicated by
  ``(id, label, hexcode, protocol)`` (the old query repeated each key once per
  model: the API does not);
* for every brand, the app's literal ``fetchCandidateKeys`` query with
  ``quickWinsFirst`` true and false and no model, in its ``ORDER BY``, against
  the API's rows sorted by the same rule (rank, ``UPPER`` label, ``UPPER``
  protocol, ``UPPER`` hexcode, id); and the same for a seeded sample of
  (brand, model, protocol) triples;
* ``power.json``, the signal shards' code sets, the manifest's counts, every
  ``protoMask`` and every id's key count against the rows.

**The expected differences**, each counted and checked against a cause: keys the
importer could not represent (dropped from the rows before comparing), and what
follows from them: the ids that have no key left, the (brand, model) pairs only
such ids list, and the brands only they list. Anything else is reported as
UNEXPLAINED and the exit status is 1.

Usage::

    python tools/app_api_vs_sql.py --sql ~/srcs/SwiftRemote/assets/db_src/swiftremote.sql \\
        [--api site/app/v1] [--ledger .] [--sample 3000] [--seed 1]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from remote_ledger import app_api  # noqa: E402
from remote_ledger.irblaster import importer  # noqa: E402

#: The app's ``ORDER BY`` for ``quickWinsFirst``, verbatim from
#: ``lib/ir_finder/irblaster_db.dart`` (``fetchCandidateKeys``, commit 6aafd15).
QUICK_WINS = """ CASE
 WHEN UPPER(k.label) LIKE '%POWER%' OR UPPER(k.label) IN ('PWR','POWER','ON','OFF') THEN 0
 WHEN UPPER(k.label) LIKE '%MUTE%' OR UPPER(k.label) = 'MUTE' THEN 1
 WHEN UPPER(k.label) LIKE 'VOL%' OR UPPER(k.label) LIKE '%VOLUME%' THEN 2
 WHEN UPPER(k.label) LIKE 'CH%' OR UPPER(k.label) LIKE '%CHANNEL%' THEN 3
 WHEN UPPER(k.label) IN ('OK','ENTER','MENU','HOME','BACK','UP','DOWN','LEFT','RIGHT') THEN 4
 ELSE 9
 END ASC,
 UPPER(k.label) ASC,
 UPPER(k.protocol) ASC,
 UPPER(k.hexcode) ASC,
 k.id ASC
"""
PLAIN = """ UPPER(k.label) ASC,
 UPPER(k.protocol) ASC,
 UPPER(k.hexcode) ASC,
 k.id ASC
"""


class Findings:
    """Counts by cause, and the first few examples of each."""

    def __init__(self) -> None:
        self.counts: Counter = Counter()
        self.examples: dict[str, list] = defaultdict(list)

    def add(self, cause: str, example=None, n: int = 1) -> None:
        self.counts[cause] += n
        if example is not None and len(self.examples[cause]) < 5:
            self.examples[cause].append(example)

    @property
    def unexplained(self) -> int:
        return sum(n for cause, n in self.counts.items() if cause.startswith("UNEXPLAINED"))


# --- the inputs ---------------------------------------------------------------------------

_ROW = re.compile(r"\| ([0-9]+) \| ([A-Za-z0-9_]+) \| ([0-9A-Za-z]+) \| .* \|")


def skipped_rows(import_md: Path) -> list[tuple[int, str, str]]:
    """``(id, DB protocol, hexcode)`` of every key ``IMPORT.md`` lists as skipped,
    one entry per row (two keys of an id can share a code and differ in label)."""
    out: list[tuple[int, str, str]] = []
    inside = False
    for line in import_md.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line.startswith("## Keys skipped, by reason")
        elif inside and (m := _ROW.fullmatch(line)):
            out.append((int(m[1]), m[2], m[3]))
    return out


def load_sql(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(path.read_text(encoding="utf-8"))
    return con


class Api:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.brands = json.loads((directory / "brands.json").read_text(encoding="utf-8"))
        self.protocols = [p["db"] for p in self.manifest["protocols"]]

    def names(self, mask: int) -> list[str]:
        return [name for i, name in enumerate(self.protocols) if mask >> i & 1]

    def brand(self, key: str):
        m = json.loads((self.dir / "b" / f"{key}.m.json").read_text(encoding="utf-8"))
        k = json.loads((self.dir / "b" / f"{key}.k.json").read_text(encoding="utf-8"))
        return m, k


# --- the app's own rules, evaluated by SQLite ---------------------------------------------------


class Ranker:
    """``quickWinsFirst``'s CASE, run by SQLite on a label (memoised), so that
    the rank is the app's SQL and not a port of it."""

    def __init__(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.case = QUICK_WINS.split("END ASC")[0] + "END"
        self.memo: dict[str, int] = {}

    def __call__(self, label: str) -> int:
        rank = self.memo.get(label)
        if rank is None:
            rank = self.con.execute(
                f"SELECT {self.case} FROM (SELECT ? AS label) k", (label,)).fetchone()[0]
            self.memo[label] = rank
        return rank


def old_order(rank, quick: bool):
    """The sort key of ``fetchCandidateKeys`` for a row ``(id, label, hexcode,
    protocol)``."""
    def key(row):
        db_id, label, hexcode, protocol = row
        base = (app_api.upper_key(label), app_api.upper_key(protocol),
                app_api.upper_key(hexcode), db_id)
        return (rank(label), *base) if quick else base
    return key


def tie_groups(rows, key):
    """Rows grouped by their full sort key: SQLite leaves the order inside a
    group to chance, so two answers agree when their groups do."""
    groups: list[tuple] = []
    for row in rows:
        k = key(row)
        if groups and groups[-1][0] == k:
            groups[-1][1].add(row)
        else:
            groups.append((k, {row}))
    return groups


# --- the comparison ------------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    ledger = Path(args.ledger)
    api = Api(Path(args.api))
    con = load_sql(Path(args.sql))
    skipped = skipped_rows(ledger / importer.IMPORT_ROOT / importer.REPORT)
    skipped_codes = {(p, h) for _i, p, h in skipped}
    finds = Findings()
    rank = Ranker()
    rng = random.Random(args.seed)

    all_sql_keys = con.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
    sql_brands = [r[0] for r in con.execute(
        "SELECT DISTINCT m.brand FROM models m JOIN keys k ON k.id = m.id "
        "ORDER BY m.brand COLLATE NOCASE ASC")]
    finds.add("info: brands the old listBrands returns", n=len(sql_brands))
    api_names = [b[0] for b in api.brands]
    api_by_name = {b[0]: b for b in api.brands}

    # the skipped list must be rows of the dump, and complete: the dump's rows
    # whose code is skipped are exactly the skipped rows (as a count, and each
    # listed (id, protocol, hexcode) is a row of the dump)
    for db_id, protocol, hexcode in skipped:
        if not con.execute("SELECT 1 FROM keys WHERE id = ? AND protocol = ? AND hexcode = ?",
                           (db_id, protocol, hexcode)).fetchone():
            finds.add("UNEXPLAINED: IMPORT.md lists a key the dump does not have",
                      (db_id, protocol, hexcode))
    dump_rows_of_skipped = 0
    for protocol, hexcode in skipped_codes:
        dump_rows_of_skipped += con.execute(
            "SELECT COUNT(*) FROM keys WHERE protocol = ? AND hexcode = ?",
            (protocol, hexcode)).fetchone()[0]
    finds.add("info: IMPORT.md skipped rows", n=len(skipped))
    finds.add("info: dump rows whose code is skipped", n=dump_rows_of_skipped)
    if dump_rows_of_skipped != len(skipped):
        finds.add("UNEXPLAINED: IMPORT.md's skipped rows are not the dump rows of its skipped codes",
                  (len(skipped), dump_rows_of_skipped))

    dropped_keys: set[tuple[int, str, str, str]] = set()
    held_ids: set[int] = set()
    dropped_pairs: set[tuple[str, str]] = set()
    pairs_total = 0
    vanished_brands: list[str] = []
    expected_brands: list[str] = []
    kept_pairs = 0
    api_pairs = 0
    sample_pool: list[tuple[str, str, str]] = []
    n_brand_checks = 0
    ledger_keys: set[tuple[int, str, str, str]] = set()

    for brand in sql_brands:
        pairs = con.execute("SELECT DISTINCT model, id FROM models WHERE brand = ?", (brand,)).fetchall()
        rows = con.execute(
            "SELECT k.id, k.label, k.hexcode, k.protocol FROM keys k "
            "WHERE k.id IN (SELECT id FROM models WHERE brand = ?)", (brand,)).fetchall()
        held = []
        for row in rows:
            if (row[3], row[2]) in skipped_codes:
                dropped_keys.add(row)
            else:
                held.append(row)
        ledger_keys.update(held)
        by_id: dict[int, list] = defaultdict(list)
        for row in held:
            by_id[row[0]].append(row)
        held_ids.update(by_id)
        models_all = sorted({m for m, _i in pairs}, key=app_api.nocase_key)
        # the old listModelsDistinct (no protocol filter) reads `models` alone
        sql_models_literal = [r[0] for r in con.execute(
            "SELECT DISTINCT model FROM models WHERE brand = ? ORDER BY model COLLATE NOCASE ASC",
            (brand,))]
        if [app_api.nocase_key(m)[0] for m in sql_models_literal] != \
                [app_api.nocase_key(m)[0] for m in models_all]:
            finds.add("UNEXPLAINED: app_api.nocase_key is not SQLite's NOCASE", brand)
        pairs_total += len(models_all)
        model_ids: dict[str, set[int]] = defaultdict(set)
        for model, db_id in pairs:
            model_ids[model].add(db_id)
        kept_models = [m for m in models_all if any(i in by_id for i in model_ids[m])]
        for m in models_all:
            if m not in kept_models:
                dropped_pairs.add((brand, m))
        if not by_id:
            vanished_brands.append(brand)
            if brand in api_by_name:
                finds.add("UNEXPLAINED: a brand with no held key is in the API", brand)
            continue
        expected_brands.append(brand)
        kept_pairs += len(kept_models)

        if brand not in api_by_name:
            finds.add("UNEXPLAINED: a brand with held keys is missing from the API", brand)
            continue
        name, key, mask = api_by_name[brand]
        if key != app_api.brand_key(brand):
            finds.add("UNEXPLAINED: brand key", brand)
        m_doc, k_doc = api.brand(key)
        n_brand_checks += 1
        if m_doc["brand"] != brand or k_doc["brand"] != brand:
            finds.add("UNEXPLAINED: brand name inside its files", brand)

        # --- the API's view of the brand --------------------------------------------------
        ids = [row[0] for row in m_doc["ids"]]
        if ids != sorted(set(ids)):
            finds.add("UNEXPLAINED: ids of a brand are not ascending and distinct", brand)
        if set(ids) != set(by_id):
            finds.add("UNEXPLAINED: the ids of a brand differ from the dump's held ids",
                      (brand, sorted(set(ids) ^ set(by_id))[:5]))
        k_by_id = {db_id: keys for db_id, keys in k_doc["r"]}
        if [r[0] for r in k_doc["r"]] != ids:
            finds.add("UNEXPLAINED: .k and .m list different ids", brand)
        if m_doc["hash"] != hashlib.sha256(
                (api.dir / "b" / f"{key}.k.json").read_bytes()).hexdigest()[:6]:
            finds.add("UNEXPLAINED: .m hash is not the hash of .k", brand)
        api_rows: dict[int, list] = {}
        for db_id, p_mask, n_keys in m_doc["ids"]:
            keys = k_by_id.get(db_id, [])
            rows_of = [(db_id, label, hexcode, api.protocols[idx]) for label, idx, hexcode in keys]
            api_rows[db_id] = rows_of
            if len(rows_of) != n_keys:
                finds.add("UNEXPLAINED: nKeys is not the number of keys", (brand, db_id))
            if p_mask != sum(1 << i for i in {idx for _l, idx, _h in keys}):
                finds.add("UNEXPLAINED: an id's protoMask is not its keys' protocols", (brand, db_id))
            if len(set(rows_of)) != len(rows_of):
                finds.add("UNEXPLAINED: duplicate key rows inside an id", (brand, db_id))
            order = [(app_api.upper_key(l), app_api.upper_key(p), app_api.upper_key(h), (l, p, h))
                     for _i, l, h, p in rows_of]
            if order != sorted(order):
                finds.add("UNEXPLAINED: keys of an id are not in (UPPER label, UPPER protocol, "
                          "UPPER hex) order", (brand, db_id))
            # the dump's held rows of this id, de-duplicated, are the API's
            if set(rows_of) != set(by_id.get(db_id, [])):
                finds.add("UNEXPLAINED: the keys of an id differ from the dump's held rows",
                          (brand, db_id, sorted(set(rows_of) ^ set(by_id.get(db_id, [])))[:3]))
        brand_mask = 0
        for _i, p_mask, _n in m_doc["ids"]:
            brand_mask |= p_mask
        if brand_mask != mask:
            finds.add("UNEXPLAINED: brands.json mask is not the OR of the ids' masks", brand)

        # models, in NOCASE order, with the ids they list
        api_models = [m for m, _ix in m_doc["models"]]
        if [app_api.nocase_key(m)[0] for m in api_models] != \
                [app_api.nocase_key(m)[0] for m in kept_models]:
            finds.add("UNEXPLAINED: the models of a brand differ from the dump's (NOCASE order)",
                      (brand, sorted(set(api_models) ^ set(kept_models))[:5]))
        elif tie_groups(api_models, app_api.nocase_key) != tie_groups(kept_models, app_api.nocase_key):
            finds.add("UNEXPLAINED: model order inside a NOCASE tie", brand)
        api_pairs += len(api_models)
        model_api_ids: dict[str, list[int]] = {}
        for model, ix in m_doc["models"]:
            model_api_ids[model] = [ids[i] for i in ix]
            if ix != sorted(set(ix)):
                finds.add("UNEXPLAINED: a model's id indexes are not ascending and distinct",
                          (brand, model))
            if set(model_api_ids[model]) != {i for i in model_ids[model] if i in by_id}:
                finds.add("UNEXPLAINED: a model's ids differ from the dump's held ids",
                          (brand, model))

        # protocol sets: brand, and each model, with and without the filter
        sql_protocols = sorted({r[3] for r in held}, key=app_api.upper_key)
        if api.names(mask) != sql_protocols:
            finds.add("UNEXPLAINED: protocols of a brand differ", (brand, api.names(mask), sql_protocols))
        for model in kept_models:
            held_of_model = [row for i in model_ids[model] for row in by_id.get(i, [])]
            expected = sorted({r[3] for r in held_of_model}, key=app_api.upper_key)
            got = api.names(sum(1 << i for i in {idx for db_id in model_api_ids.get(model, [])
                                                 for _l, idx, _h in k_by_id[db_id]}))
            if got != expected:
                finds.add("UNEXPLAINED: protocols of a (brand, model) differ", (brand, model))
            grouped: dict[str, set] = defaultdict(set)
            for row in held_of_model:
                grouped[row[3]].add(row)
            api_grouped: dict[str, set] = defaultdict(set)
            for db_id in model_api_ids.get(model, []):
                for row in api_rows[db_id]:
                    api_grouped[row[3]].add(row)
            if grouped != api_grouped:
                finds.add("UNEXPLAINED: the key rows of a (brand, model, protocol) differ",
                          (brand, model))
            for protocol in grouped:
                sample_pool.append((brand, model, protocol))
        # models filtered by a protocol (listModelsDistinct with a protocol)
        for protocol in sql_protocols:
            sql_f = sorted({m for m in kept_models
                            if any(r[3] == protocol for i in model_ids[m] for r in by_id.get(i, []))},
                           key=app_api.nocase_key)
            idx = api.protocols.index(protocol)
            api_f = [m for m, ix in m_doc["models"]
                     if any(m_doc["ids"][i][1] >> idx & 1 for i in ix)]
            if [app_api.nocase_key(m)[0] for m in api_f] != [app_api.nocase_key(m)[0] for m in sql_f]:
                finds.add("UNEXPLAINED: models filtered by protocol differ", (brand, protocol))

        # the literal fetchCandidateKeys, brand only, both orders, de-duplicated
        all_api = [row for db_id in ids for row in api_rows[db_id]]
        for quick, order_sql in ((True, QUICK_WINS), (False, PLAIN)):
            sql_rows = con.execute(
                "SELECT DISTINCT k.id, k.label, k.hexcode, k.protocol FROM models m "
                "JOIN keys k ON k.id = m.id WHERE m.brand = ? ORDER BY " + order_sql,
                (brand,)).fetchall()
            sql_rows = [r for r in sql_rows if (r[3], r[2]) not in skipped_codes]
            key_fn = old_order(rank, quick)
            mine = sorted(all_api, key=key_fn)
            if tie_groups(sql_rows, key_fn) != tie_groups(mine, key_fn):
                finds.add(f"UNEXPLAINED: fetchCandidateKeys order (quickWinsFirst={quick}) differs",
                          brand)

    # --- the sample of model-level literal queries -------------------------------------------------
    sample = rng.sample(sample_pool, min(args.sample, len(sample_pool)))
    for brand, model, protocol in sample:
        _name, key, _mask = api_by_name[brand]
        m_doc, k_doc = api.brand(key)
        ids = [row[0] for row in m_doc["ids"]]
        k_by_id = dict(k_doc["r"])
        ix = dict(m_doc["models"])[model]
        proto_idx = api.protocols.index(protocol)
        for quick, order_sql in ((True, QUICK_WINS), (False, PLAIN)):
            sql_rows = con.execute(
                "SELECT DISTINCT k.id, k.label, k.hexcode, k.protocol FROM models m "
                "JOIN keys k ON k.id = m.id WHERE m.brand = ? AND m.model = ? AND k.protocol = ? "
                "ORDER BY " + order_sql, (brand, model, protocol)).fetchall()
            sql_rows = [r for r in sql_rows if (r[3], r[2]) not in skipped_codes]
            mine = [(ids[i], label, hexcode, protocol) for i in ix
                    for label, idx, hexcode in k_by_id[ids[i]] if idx == proto_idx]
            key_fn = old_order(rank, quick)
            if tie_groups(sql_rows, key_fn) != tie_groups(sorted(mine, key=key_fn), key_fn):
                finds.add(f"UNEXPLAINED: model-level fetchCandidateKeys order differs "
                          f"(quickWinsFirst={quick})", (brand, model, protocol))

    # --- the totals, and every difference accounted for -----------------------------------------------
    finds.add("info: brand-model pairs in the dump", n=pairs_total)
    finds.add("difference: keys the importer did not represent (distinct rows)", n=len(dropped_keys))
    if len(dropped_keys) != len(skipped):
        finds.add("UNEXPLAINED: dropped keys are not IMPORT.md's skipped rows",
                  (len(dropped_keys), len(skipped)))
    all_ids = {r[0] for r in con.execute("SELECT id FROM remotes")}
    ids_with_models = {r[0] for r in con.execute("SELECT DISTINCT id FROM models")}
    ids_with_keys = {r[0] for r in con.execute("SELECT DISTINCT id FROM keys")}
    lost_ids = {i for i in ids_with_models & ids_with_keys if i not in held_ids}
    finds.add("difference: ids with keys and models and no held key", n=len(lost_ids))
    finds.add("info: ids in remotes / with models / with keys",
              n=len(all_ids), example=(len(ids_with_models), len(ids_with_keys)))
    finds.add("difference: brand-model pairs not in the API (only no-held-key ids list them)",
              n=len(dropped_pairs))
    finds.add("difference: brands not in the API (only no-held-key ids list them)",
              n=len(vanished_brands), example=vanished_brands[:3])
    if kept_pairs != api_pairs or kept_pairs + len(dropped_pairs) != pairs_total:
        finds.add("UNEXPLAINED: pair totals", (kept_pairs, api_pairs, len(dropped_pairs), pairs_total))
    if api_names != sorted(api_names, key=app_api.nocase_key):
        finds.add("UNEXPLAINED: brands.json is not in NOCASE order")
    if [app_api.nocase_key(n)[0] for n in api_names] != \
            [app_api.nocase_key(n)[0] for n in expected_brands]:
        finds.add("UNEXPLAINED: brands.json differs from the dump's brands with held keys")
    counts = api.manifest["counts"]
    expect_counts = {"brands": len(expected_brands), "models": kept_pairs,
                     "remotes": len(held_ids), "keys": len(ledger_keys)}
    # a key is counted once per id (an id keeps its keys whatever the brand)
    if counts != expect_counts:
        finds.add("UNEXPLAINED: manifest counts", (counts, expect_counts))
    finds.add("info: keys in the dump (rows)", n=all_sql_keys)

    check_signals_and_power(api, con, skipped_codes, finds, rank)

    for cause in sorted(finds.counts):
        line = f"{finds.counts[cause]:>10,}  {cause}"
        if finds.examples[cause]:
            line += f"   e.g. {finds.examples[cause][:3]}"
        print(line)
    print(f"\n{n_brand_checks:,} brands checked; {len(sample):,} (brand, model, protocol) "
          f"triples through the literal query, {len(sample_pool):,} through the key sets.")
    print(f"unexplained differences: {finds.unexplained}")
    return 1 if finds.unexplained else 0


def dart_rank(label: str) -> int:
    """``powerLabelRank`` of ``lib/universal_power/power_code.dart``, written as
    the Dart is (a loop over the characters), apart from ``app_api``'s port."""
    raw = label.strip()
    if not raw:
        return 3
    out = []
    last_underscore = False
    for ch in raw:
        u = ord(ch)
        if 48 <= u <= 57 or 65 <= u <= 90 or 97 <= u <= 122:
            out.append(chr(u - 32) if 97 <= u <= 122 else ch)
            last_underscore = False
        elif not last_underscore:
            out.append("_")
            last_underscore = True
    norm = "".join(out)
    while norm.startswith("_"):
        norm = norm[1:]
    while norm.endswith("_"):
        norm = norm[:-1]
    if norm in ("POWER", "PWR", "OFF", "ON", "POWER_OFF", "POWER_ON", "PWR_OFF", "PWR_ON"):
        return 0
    has_power = "POWER" in norm or "PWR" in norm
    has_off_on = "OFF" in norm or "ON" in norm
    if has_power and has_off_on:
        return 0
    if has_power:
        return 1
    if norm in ("STANDBY", "SLEEP"):
        return 1
    if norm in ("TV_POWER", "SYSTEM_POWER", "MAIN_POWER", "ALL_POWER", "POWER_TOGGLE", "PWR_TOGGLE"):
        return 2
    return 3


def check_signals_and_power(api: Api, con, skipped_codes, finds: Findings, rank) -> None:
    """``power.json`` and the signal shards against the rows."""
    rows = [r for r in con.execute("SELECT id, label, hexcode, protocol FROM keys")
            if (r[3], r[2]) not in skipped_codes]
    # signal shards: the codes present, per protocol
    present: dict[str, set[str]] = defaultdict(set)
    for _id, _label, hexcode, protocol in rows:
        present[protocol].add(hexcode)
    for entry in api.manifest["protocols"]:
        name = entry["db"]
        shard = api.dir / "s" / f"{name}.json"
        if entry["appReadingDiffers"] != shard.exists():
            finds.add("UNEXPLAINED: a signal shard exists iff the protocol differs", name)
        if entry["appReadingDiffers"] and shard.exists():
            doc = json.loads(shard.read_text(encoding="utf-8"))
            if set(doc["s"]) != present[name]:
                finds.add("UNEXPLAINED: a signal shard's codes are not the codes present", name)
            finds.add(f"info: signals in s/{name}.json", n=len(doc["s"]))
            if doc["play"] != entry["play"] or doc["minSends"] != entry["minSends"]:
                finds.add("UNEXPLAINED: a shard and the manifest disagree", name)
        if not entry["appReadingDiffers"] and entry["play"] is not None:
            finds.add("UNEXPLAINED: play on a protocol that has no shard", name)

    # power.json: from the rows, with the app's rule written out again
    ids_of: dict[tuple[str, str], set[int]] = defaultdict(set)
    labels_of: dict[tuple[str, str], dict[str, tuple[int, set[int]]]] = defaultdict(dict)
    for db_id, label, hexcode, protocol in rows:
        r = dart_rank(label)
        if r <= app_api.POWER_MAX_RANK:
            ids_of[(protocol, hexcode)].add(db_id)
            labels_of[(protocol, hexcode)].setdefault(label, (r, set()))[1].add(db_id)
    api_rows = json.loads((api.dir / "power.json").read_text(encoding="utf-8"))
    expected = {k: len(v) for k, v in ids_of.items()}
    got = {(api.protocols[i], h): n for i, h, _label, n, _rank in api_rows}
    all_rows = con.execute("SELECT id, label, hexcode, protocol FROM keys").fetchall()
    every = {(p, h) for _i, lab, h, p in all_rows if dart_rank(lab) <= app_api.POWER_MAX_RANK}
    finds.add("info: distinct (protocol, hex) ranking as power, rows the ledger holds", n=len(expected))
    finds.add("info: the same over every row of the dump", n=len(every))
    if len(api_rows) != min(len(expected), app_api.POWER_LIMIT) or (
            len(expected) <= app_api.POWER_LIMIT and got != expected):
        finds.add("UNEXPLAINED: power.json differs from the rows", (len(api_rows), len(expected)))
    counts = [r[3] for r in api_rows]
    if counts != sorted(counts, reverse=True):
        finds.add("UNEXPLAINED: power.json is not ordered by use")
    for index, hexcode, label, n_ids, r in api_rows:
        key = (api.protocols[index], hexcode)
        if label not in labels_of[key] or labels_of[key][label][0] != r:
            finds.add("UNEXPLAINED: a power row's label or rank", key)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sql", required=True, help="assets/db_src/swiftremote.sql")
    ap.add_argument("--api", default=str(ROOT / "site" / "app" / "v1"))
    ap.add_argument("--ledger", default=str(ROOT), help="repository root (for IMPORT.md)")
    ap.add_argument("--sample", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    return run(ap.parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
