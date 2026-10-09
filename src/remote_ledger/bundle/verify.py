"""``rl bundle --verify DIR``: is this bundle what the tree says it should be (D88).

Slower and wider than ``--check``, which only compares a fresh build with the files.
It reads the bundle the way an app would and holds it against the tree:

1. the files: ``manifest.json`` lists the sizes and SHA-256 of the bundle and the
   notices, and the bundle's pragmas and ``meta`` rows are the manifest's;
2. the SQLite file itself: ``quick_check``, the rows' counts, every reference from one
   table to another, the brands' ranges of models, the grams of ``ngram`` recomputed
   from the models' names, ``dataVersion`` recomputed from the rows;
3. the tree: every remote of the bundle is a file of the tree, with the files folded into
   it (D103), and is that remote (carrier, protocol, play rule, tier, every key's canonical
   id, text, confidence and signal, the folded files' keys after its own); ``remote_refs``
   (D105) is the ref of every file of the profile, each to the remote that carries it and
   where its keys start; the full profile has every file of the tree, and the selected one
   every file of a brand it carries; a deterministic sample of remotes is compiled again,
   from the committed ``build/pronto`` artifact when there is one and from the file when
   not, and its Pronto strings compared with the blobs;
4. every signal: each ``(signal, carrier of a remote that uses it)`` is decoded by the
   ledger's decoder and encoded again, and must come back as the same words.

Nothing is repaired and nothing is written.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .. import paths
from ..errors import LedgerError
from ..keys import load_vocabulary, squash
from ..parallel import ordered_map
from ..pronto import encode
from ..serialize import load
from . import aliases as brand_aliases
from . import catalog, corpus, merge, writer
from .build import BUNDLE_FILE, MANIFEST_FILE, NOTICES_FILE
from .catalog import GRAM, unvarints
from .corpus import RemoteRecord
from .signals import blob_to_pronto, blob_words, decode_blob
from .textnorm import search_norm

#: How many remotes are compiled again from the tree, and how many models are looked up
#: in both directions. Chosen by a hash of the name, so the same bundle is sampled the
#: same way and a different tree is not sampled the same way.
SAMPLE_REMOTES = 150
SAMPLE_MODELS = 300


def ranked(names: list[str], count: int, salt: str) -> list[str]:
    """``count`` of ``names``, by the SHA-256 of ``salt`` and the name: a deterministic
    sample that does not depend on the order the names come in."""
    return sorted(names, key=lambda n: hashlib.sha256(f"{salt}\0{n}".encode()).digest())[:count]


def _decode_chunk(chunk: list[tuple[int, bytes, int]]) -> list[str]:
    """Problems of a chunk of ``(signal id, blob, carrier)``. A worker's unit."""
    out = []
    for signal_id, blob, carrier in chunk:
        try:
            decoded = decode_blob(blob, carrier)
            again = encode(decoded)
        except LedgerError as exc:
            out.append(f"signal {signal_id} at {carrier} Hz: {exc}")
            continue
        if again != blob_to_pronto(blob):
            out.append(f"signal {signal_id} at {carrier} Hz decodes and encodes to other words")
    return out


def _one(conn: sqlite3.Connection, sql: str, *args: Any) -> Any:
    return conn.execute(sql, args).fetchone()[0]


def verify_directory(root: Path, directory: Path, *, sample_remotes: int = SAMPLE_REMOTES,
                     sample_models: int = SAMPLE_MODELS,
                     aliases: tuple[brand_aliases.Alias, ...] | None = None) -> tuple[list[str], dict[str, Any]]:
    """``(problems, facts)`` of the bundle in ``directory`` against the tree under ``root`` and
    the brand aliases (the shipped list unless ``aliases`` is given)."""
    problems: list[str] = []
    facts: dict[str, Any] = {}
    manifest_path = directory / MANIFEST_FILE
    if not manifest_path.is_file():
        return [f"{manifest_path}: missing"], facts
    manifest = json.loads(manifest_path.read_bytes())

    # -- 1. the files ------------------------------------------------------------------
    for part, default in (("bundle", BUNDLE_FILE), ("notices", NOTICES_FILE)):
        entry = manifest.get(part) or {}
        path = directory / entry.get("file", default)
        if not path.is_file():
            return [f"{path}: missing"], facts
        data = path.read_bytes()
        if len(data) != entry.get("bytes"):
            problems.append(f"{path}: {len(data):,} bytes, the manifest says {entry.get('bytes')}")
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            problems.append(f"{path}: its SHA-256 is not the manifest's")
    facts["bytes"] = (directory / BUNDLE_FILE).stat().st_size

    conn = sqlite3.connect(f"file:{(directory / BUNDLE_FILE).as_posix()}?mode=ro", uri=True)
    try:
        problems += _verify_file(conn, manifest, facts)
        if problems:
            return problems, facts
        problems += _verify_against_tree(conn, root, manifest, facts, sample_remotes,
                                         sample_models, aliases)
    finally:
        conn.close()
    return problems, facts


def _verify_file(conn: sqlite3.Connection, manifest: dict[str, Any],
                 facts: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if _one(conn, "PRAGMA application_id") != writer.APPLICATION_ID:
        problems.append("application_id is not the bundle's")
    if _one(conn, "PRAGMA user_version") != writer.SCHEMA_VERSION:
        problems.append("user_version is not the schema version")
    if _one(conn, "PRAGMA page_size") != writer.PAGE_SIZE:
        problems.append(f"page_size is not {writer.PAGE_SIZE}")
    quick = _one(conn, "PRAGMA quick_check")
    if quick != "ok":
        return problems + [f"quick_check: {quick}"]
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    for key, value in (("schemaVersion", manifest["schemaVersion"]),
                       ("dataVersion", manifest["dataVersion"]),
                       ("vocabularyVersion", manifest["vocabularyVersion"]),
                       ("profile", manifest["profile"])):
        if meta.get(key) != str(value):
            problems.append(f"meta {key} is {meta.get(key)!r}, the manifest says {value!r}")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'brand_aliases'").fetchone():
        return problems + ["the bundle has no brand_aliases table: it was written before the brand "
                           "aliases of D101; build it again"]
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'remote_refs'").fetchone():
        return problems + ["the bundle has no remote_refs table: it was written before the fragments "
                           "were folded (D103, D105); build it again"]
    tables = {"brands": "brands", "brandAliases": "brand_aliases", "models": "models",
              "controls": "controls", "remotes": "remotes", "remoteRefs": "remote_refs",
              "keys": "keys", "signals": "signals", "excludedBrands": "excluded_brands"}
    for name, table in tables.items():
        n = _one(conn, f"SELECT COUNT(*) FROM {table}")
        if str(n) != meta.get(f"count.{name}"):
            problems.append(f"{table} has {n:,} rows, meta count.{name} says {meta.get(f'count.{name}')}")
        if name in manifest["counts"] and manifest["counts"][name] != n:
            problems.append(f"{table} has {n:,} rows, the manifest says {manifest['counts'][name]:,}")
    if manifest["brands"] != {"included": _one(conn, "SELECT COUNT(*) FROM brands"),
                              "excluded": _one(conn, "SELECT COUNT(*) FROM excluded_brands")}:
        problems.append("the manifest's brand counts are not the tables'")
    digest = writer.content_digest(conn)
    if digest != meta.get("dataVersion"):
        problems.append(f"the rows give the dataVersion {digest}, meta says {meta.get('dataVersion')}")
    facts["dataVersion"] = digest

    dangling = {
        "keys.remote_id": "SELECT COUNT(*) FROM keys WHERE remote_id NOT IN (SELECT id FROM remotes)",
        "keys.signal_id": "SELECT COUNT(*) FROM keys WHERE signal_id NOT IN (SELECT id FROM signals)",
        "keys.canon": "SELECT COUNT(*) FROM keys WHERE canon IS NOT NULL AND "
                      "canon NOT IN (SELECT id FROM vocab_keys)",
        "models.brand_id": "SELECT COUNT(*) FROM models WHERE brand_id NOT IN (SELECT id FROM brands)",
        "controls.model_id": "SELECT COUNT(*) FROM controls WHERE model_id NOT IN (SELECT id FROM models)",
        "controls.remote_id": "SELECT COUNT(*) FROM controls WHERE remote_id NOT IN (SELECT id FROM remotes)",
        "remotes.brand_id": "SELECT COUNT(*) FROM remotes WHERE brand_id IS NOT NULL AND "
                            "brand_id NOT IN (SELECT id FROM brands)",
        "remotes.source": "SELECT COUNT(*) FROM remotes WHERE source NOT IN (SELECT id FROM sources)",
        "unused signals": "SELECT COUNT(*) FROM signals WHERE id NOT IN (SELECT signal_id FROM keys)",
        "remotes.key_count": "SELECT COUNT(*) FROM remotes r WHERE key_count != "
                             "(SELECT COUNT(*) FROM keys k WHERE k.remote_id = r.id)",
        "excluded brands that are in": "SELECT COUNT(*) FROM excluded_brands e JOIN brands b "
                                       "ON b.norm = e.norm AND e.norm != ''",
        "brand_aliases.brand_id": "SELECT COUNT(*) FROM brand_aliases WHERE brand_id NOT IN "
                                  "(SELECT id FROM brands)",
        "aliases that are a brand's own name": "SELECT COUNT(*) FROM brand_aliases a JOIN brands b "
                                               "ON b.norm = a.norm",
        "remote_refs.remote_id": "SELECT COUNT(*) FROM remote_refs WHERE remote_id NOT IN "
                                 "(SELECT id FROM remotes)",
        "remotes whose own ref is not theirs in remote_refs": (
            "SELECT COUNT(*) FROM remotes r WHERE NOT EXISTS (SELECT 1 FROM remote_refs f WHERE "
            "f.ref = r.ref AND f.remote_id = r.id AND f.first_n = 0)"),
    }
    for what, sql in dangling.items():
        n = _one(conn, sql)
        if n:
            problems.append(f"{n:,} rows of {what} are wrong or dangling")

    # the refs of one remote cut its keys into runs, one after the other from 0, with no gap and
    # nothing left over (D105)
    runs: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for ref, remote_id, first, count in conn.execute(
            "SELECT ref, remote_id, first_n, key_count FROM remote_refs"):
        runs[remote_id].append((first, count, ref))
    for remote_id, key_count in conn.execute("SELECT id, key_count FROM remotes"):
        at = 0
        for first, count, ref in sorted(runs.get(remote_id, [])):
            if first != at or count < 1:
                problems.append(f"remote_refs: {ref} holds the keys {first} to {first + count - 1} of "
                                f"remote {remote_id}, the run before it ends at {at - 1}")
                break
            at += count
        else:
            if at != key_count:
                problems.append(f"remote_refs: the refs of remote {remote_id} hold {at} keys, it has {key_count}")

    # a brand's models are the rows first_model .. first_model + model_count - 1
    members: dict[int, list[int]] = defaultdict(list)
    for model_id, brand_id in conn.execute("SELECT id, brand_id FROM models ORDER BY id"):
        members[brand_id].append(model_id)
    for brand_id, first, count in conn.execute("SELECT id, first_model, model_count FROM brands"):
        if members.get(brand_id, []) != list(range(first, first + count)):
            problems.append(f"brand {brand_id}: its models are not the rows {first} to "
                            f"{first + count - 1}")

    # the grams of the models' names, recomputed
    expected: dict[str, list[int]] = defaultdict(list)
    for model_id, name in conn.execute("SELECT id, name FROM models ORDER BY id"):
        for gram in catalog.grams(search_norm(name)):
            expected[gram].append(model_id)
    stored = {g: unvarints(b) for g, b in conn.execute("SELECT gram, ids FROM ngram")}
    if stored != expected:
        problems.append("ngram is not the grams of the models' names "
                        f"({len(stored):,} grams stored, {len(expected):,} expected)")
    for brand_id, name, norm in conn.execute("SELECT id, name, norm FROM brands"):
        if norm != search_norm(name):
            problems.append(f"brand {brand_id} {name!r} has the search key {norm!r}")
    for alias, norm in conn.execute("SELECT alias, norm FROM brand_aliases"):
        if norm != search_norm(alias):
            problems.append(f"the alias {alias!r} has the search key {norm!r}")
    facts["gramLength"] = GRAM
    return problems


def _verify_against_tree(conn: sqlite3.Connection, root: Path, manifest: dict[str, Any],
                         facts: dict[str, Any], sample_remotes: int, sample_models: int,
                         aliases: tuple[brand_aliases.Alias, ...] | None) -> list[str]:
    problems: list[str] = []
    records, read_problems = corpus.read_corpus(root)
    if read_problems:
        return [f"the tree cannot be read: {p}" for p in read_problems[:5]]
    by_ref = {merge.ref_of(r): (i + 1, r) for i, r in enumerate(records)}
    folded = merge.fold(records)
    vocab = load_vocabulary()
    canon_key = {i: k for i, k in conn.execute("SELECT id, key FROM vocab_keys")}
    spellings = {k.id: {squash(k.id), squash(k.name)} for k in vocab.keys}

    signals = {i: b for i, b in conn.execute("SELECT id, words FROM signals")}
    rows = defaultdict(list)
    for remote_id, n, canon, label, signal_id, confidence in conn.execute(
            "SELECT remote_id, n, canon, label, signal_id, confidence FROM keys "
            "ORDER BY remote_id, n"):
        rows[remote_id].append((n, canon, label, signal_id, confidence))

    seen_refs: set[str] = set()
    pairs_used: list[tuple[int, str]] = []
    carriers: dict[tuple[int, int], int] = {}
    for (rid, ref, model, source, tier, key_count, protocol, carrier, repeat, helper, empty,
         rule) in conn.execute(
            "SELECT id, ref, model, source, tier, key_count, protocol, carrier_hz, repeat_passes, "
            "helper_repeat_passes, intro_empty, rule FROM remotes ORDER BY id"):
        found = by_ref.get(ref)
        if found is None:
            problems.append(f"remote {rid} {ref}: no such file in the tree")
            continue
        expected_id, record = found
        if rid != expected_id:
            problems.append(f"{ref}: id {rid}, the tree's order gives {expected_id}")
        if folded.carrier[expected_id - 1] != expected_id - 1:
            problems.append(f"{ref}: a remote of its own, but the rule of D103 folds it into "
                            f"{merge.ref_of(records[folded.carrier[expected_id - 1]])}")
            continue
        seen_refs.add(ref)
        # the remote is the file with the files folded into it: their keys follow its own
        record = folded.remote(records, expected_id - 1)
        want = corpus.play(record)
        if (repeat, helper, empty, rule) != want:
            problems.append(f"{ref}: plays as {(repeat, helper, empty, rule)}, the tree says {want}")
        if (corpus.sources()[source - 1], tier, protocol, carrier, key_count) != (
                record.source, record.tier, record.protocol, record.carrier_hz, len(record.keys)):
            problems.append(f"{ref}: source, tier, protocol, carrier or key count differ from the file")
        if (model is None) != corpus.is_synthetic_model(record.source) or (
                model is not None and model != record.model):
            problems.append(f"{ref}: model {model!r} is not the file's")
        got = rows.get(rid, [])
        if len(got) != len(record.keys):
            problems.append(f"{ref}: {len(got)} keys, the file has {len(record.keys)}")
            continue
        for (n, canon, label, signal_id, confidence), (name, text_label, want_canon, want_conf,
                                                      blob) in zip(got, record.keys):
            text = text_label if text_label is not None else name
            stored = text if catalog.label_needed(text, want_canon, spellings) else None
            if (canon_key.get(canon), label, confidence, signals[signal_id]) != (
                    want_canon, stored, want_conf, blob):
                problems.append(f"{ref}: key {n} ({name}) is not the file's")
                break
            carriers[(signal_id, carrier)] = carrier
            if (blob_words(blob)[2] == 0) != bool(empty):
                problems.append(f"{ref}: key {n} has {'an' if empty else 'no'} intro against the remote's flag")
                break

    # completeness: a file is in the bundle when the remote that carries it is
    profile = manifest["profile"]
    included_brands = {norm for (norm,) in conn.execute("SELECT norm FROM brands")}
    collected = catalog.collect(records)
    refs = [merge.ref_of(r) for r in records]
    present = [refs[folded.carrier[i]] in seen_refs for i in range(len(records))]
    if profile == "full":
        missing = [refs[i] for i, here in enumerate(present) if not here]
        if missing:
            problems.append(f"the full bundle lacks {len(missing):,} remotes of the tree, "
                            f"such as {sorted(missing)[0]}")
        profile_files = list(range(len(records)))
    else:
        profile_files = []
        for i, (maker, pair_rows) in enumerate(collected.pairs):
            touches = maker in included_brands or any(r[0] in included_brands for r in pair_rows)
            if touches:
                profile_files.append(i)
            if touches != present[i]:
                problems.append(f"{refs[i]}: {'in' if present[i] else 'not in'} the bundle although "
                                f"{'no brand' if not touches else 'a brand'} of it is carried")
                break
        every = {b.norm for b in collected.brands.values()}
        excluded = {n for (n,) in conn.execute("SELECT norm FROM excluded_brands")}
        if every - included_brands - excluded - {n for n in every if n.startswith("\0")}:
            problems.append("some brands of the tree are neither carried nor in excluded_brands")

    # remote_refs (D105): the ref of every file of the profile, the remote that carries it and
    # where its keys start in that remote
    stored_refs = list(conn.execute("SELECT ref, remote_id, first_n, key_count FROM remote_refs ORDER BY ref"))
    wanted_refs = sorted((refs[i], folded.carrier[i] + 1, folded.first_key(records, i), len(records[i].keys))
                         for i in profile_files)
    if stored_refs != wanted_refs:
        differ = sorted(set(stored_refs) ^ set(wanted_refs))
        problems.append(f"remote_refs has {len(stored_refs):,} rows and the tree gives {len(wanted_refs):,}; "
                        f"{len(differ):,} differ, such as {differ[0]}")
    file_refs = {ref for ref, *_ in stored_refs}

    # the brand aliases: the list that ships, for the brands this bundle carries
    brand_ids = {norm: i for i, norm in conn.execute("SELECT id, norm FROM brands")}
    wanted = brand_aliases.rows_for(brand_aliases.shipped_aliases() if aliases is None else aliases, brand_ids)
    stored = list(conn.execute("SELECT alias, norm, brand_id FROM brand_aliases ORDER BY norm"))
    if stored != wanted:
        problems.append(f"brand_aliases has {len(stored):,} rows and is not the list that ships "
                        f"for these brands ({len(wanted):,} rows)")
    facts["brandAliases"] = len(stored)

    # the sample, compiled again by the compile stage's own code or its committed output
    for ref in ranked(sorted(seen_refs & set(by_ref)), sample_remotes, "remotes"):
        rid = by_ref[ref][0]
        problems += _recompile_one(root, ref, rid, [records[j] for j in (rid - 1, *folded.parts.get(rid - 1, ()))],
                                   signals, rows)

    # models and controls, a sample both ways
    model_names = {i: (b, n) for i, b, n in conn.execute("SELECT id, brand_id, name FROM models")}
    brand_norm = {i: n for i, n in conn.execute("SELECT id, norm FROM brands")}
    ids = [str(i) for i in model_names]
    for text in ranked(ids, sample_models, "models"):
        mid = int(text)
        brand_id, name = model_names[mid]
        wanted = (brand_norm[brand_id], search_norm(name))
        for (rid,) in conn.execute("SELECT remote_id FROM controls WHERE model_id = ?", (mid,)):
            ref = _one(conn, "SELECT ref FROM remotes WHERE id = ?", rid)
            record = by_ref[ref][1]
            if not any((search_norm(b), search_norm(m)) == wanted
                       for b, m, _ in catalog.pairs_of(record)):
                problems.append(f"model {mid} {name!r} is linked to {ref}, which does not list it")
    controls = {(m, r) for m, r in conn.execute("SELECT model_id, remote_id FROM controls")}
    norm_ids = {(brand_norm[b], search_norm(n)): i for i, (b, n) in model_names.items()}
    for ref in ranked(sorted(file_refs & set(by_ref)), 100, "controls"):
        record = by_ref[ref][1]
        rid = folded.carrier[by_ref[ref][0] - 1] + 1          # the remote that carries the file
        for brand, model, _ in catalog.pairs_of(record):
            key = (search_norm(brand), search_norm(model))
            if key[0] in included_brands and key[1] and (norm_ids.get(key), rid) not in controls:
                problems.append(f"{ref} lists {brand!r} / {model!r}, which is not linked to it")

    # every signal decodes and encodes back
    todo = sorted((sid, signals[sid], carrier) for (sid, carrier) in carriers)
    chunks = [todo[i:i + 200] for i in range(0, len(todo), 200)]
    decoded = 0
    for chunk_problems in ordered_map(_decode_chunk, chunks):
        problems += chunk_problems
    decoded = len(todo)
    facts.update(remotes=len(seen_refs), remoteRefs=len(file_refs), decodedSignals=decoded,
                 sampledRemotes=min(sample_remotes, len(seen_refs)))
    return problems


def _recompile_one(root: Path, ref: str, rid: int, files: list[RemoteRecord],
                   signals: dict[int, bytes], rows: dict) -> list[str]:
    """One remote's Pronto strings from the compile stage's own artifact, against the blobs. The
    remote is ``files[0]`` with the files folded into it after it (D103): each file's keys, in its
    own order, one after the other."""
    keys: list[tuple[str, str]] = []
    for record in files:
        artifact = root / paths.artifact(record.where)
        try:
            if artifact.is_file():
                compiled = load(artifact)["keys"]
            else:
                from ..cli import compiled_artifact
                from ..remote import load_remote

                compiled = compiled_artifact(load_remote(paths.locate(root, record.where)))["keys"]
        except (LedgerError, OSError, ValueError) as exc:
            return [f"{ref}: cannot be compiled again: {exc}"]
        keys += [(name, compiled[name]["candidates"]["primary"]["prontoHex"]) for name in sorted(compiled)]
    got = rows.get(rid, [])
    if len(keys) != len(got):
        return [f"{ref}: {len(got)} keys in the bundle, {len(keys)} in its compiled artifact"]
    out = []
    for (name, text), (n, _canon, _label, signal_id, _conf) in zip(keys, got):
        if blob_to_pronto(signals[signal_id]) != text:
            out.append(f"{ref}: key {n} ({name}) is not the Pronto string the compile stage gives")
    return out
