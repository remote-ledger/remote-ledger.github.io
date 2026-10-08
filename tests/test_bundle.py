"""The catalog bundle (D88 to D92): the format, what goes in it, and that two builds
of one tree are the same bytes.

The reference is not the builder. A small ledger with all four sources
(``tests/bundle_corpus.py``) is read here with plain ``json``, and what a bundle
must hold for it is worked out in the test with the rules the design states
(the search key, the merging of spellings, D78's play numbers, the label rule), so
a wrong table fails against it, not against a second copy of the same code.
"""

from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import struct
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from bundle_corpus import floor_of_the_small_ledgers, make_corpus
from remote_ledger import app_api, cli, generators, parallel, pronto
from remote_ledger.bundle import aliases as brand_aliases
from remote_ledger.bundle import build as bb
from remote_ledger.bundle import catalog, corpus, select, writer
from remote_ledger.bundle.signals import blob_to_pronto, blob_words
from remote_ledger.bundle.textnorm import search_norm
from remote_ledger.cli import compiled_artifact
from remote_ledger.keys import load_vocabulary, squash
from remote_ledger.remote import load_remote

ROOT = Path(__file__).resolve().parent.parent

#: The brand aliases the synthetic ledger is built with: not the shipped list, so that a count here
#: does not move when the list grows. ``Nobody`` is a brand of no tree (D101: reported, not an error).
ALIASES = (
    brand_aliases.make("Sony", "索尼"), brand_aliases.make("SONY", "新力"),
    brand_aliases.make("Topping", "拓品"), brand_aliases.make("Nobody", "无名"),
)


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    return make_corpus(tmp_path_factory.mktemp("bundle") / "ledger")


@pytest.fixture(scope="module")
def full(ledger):
    built = bb.build_bundle(ledger, "full", aliases=ALIASES)
    assert built.problems == []
    return built


@pytest.fixture(scope="module")
def selected(ledger):
    with floor_of_the_small_ledgers():
        built = bb.build_bundle(ledger, "selected", aliases=ALIASES)
    assert built.problems == []
    return built


def db(built: bb.Bundle) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.deserialize(built.files[bb.BUNDLE_FILE])
    return conn


def rows(built: bb.Bundle, sql: str, *args):
    return db(built).execute(sql, args).fetchall()


def tree_files(ledger: Path) -> list[Path]:
    return sorted((ledger / "remotes").glob("**/*.json"))


def ref_of(ledger: Path, path: Path) -> str:
    return path.relative_to(ledger / "remotes").as_posix()[:-len(".json")]


# --- the search key ----------------------------------------------------------------------


@pytest.mark.parametrize("text, key", [
    ("UN50NU6900F", "un50nu6900f"),
    ("un 50 nu-6900 f", "un50nu6900f"),
    ("UN50-NU6900/F", "un50nu6900f"),
    ("Ünï", "uni"),                      # accents are marks after NFKD
    ("Éz", "ez"),
    ("Ｓony ① ²", "sony12"),               # full width and compatibility forms
    ("Ω", "ω"),                          # a letter of another script is a letter
    ("Вниз", "вниз"),
    ("ß", "ß"),                          # lower() and not casefold(): ß stays
    ("?? --", ""),                       # nothing but separators: no key
    ("", ""),
    ("T.V.E.", "tve"),
    ("A_B", "ab"),
])
def test_the_search_key(text, key):
    assert search_norm(text) == key


def test_the_search_key_is_letters_and_digits_only_and_idempotent():
    rng = random.Random(7)
    pool = [chr(c) for c in range(0x20, 0x250)] + list("☃⏩–—‑·∕/\\|[](){}~`'\"")
    for _ in range(400):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(0, 14)))
        key = search_norm(text)
        assert search_norm(key) == key
        assert all(unicodedata.category(c)[0] in "LN" for c in key)
        assert not any(unicodedata.category(c) == "Lu" for c in key)


def test_grams_pad_the_key_and_a_short_key_still_has_one():
    assert catalog.grams("a") == {"^a$"}
    assert catalog.grams("un5") == {"^un", "un5", "n5$"}
    assert catalog.grams("") == set()
    assert catalog.grams("abab") == {"^ab", "aba", "bab", "ab$"}


def test_the_posting_lists_are_leb128_deltas():
    assert catalog.varints([1, 2, 300]) == bytes([0x01, 0x01, 0xAA, 0x02])
    assert catalog.unvarints(bytes([0x01, 0x01, 0xAA, 0x02])) == [1, 2, 300]
    rng = random.Random(3)
    for _ in range(50):
        ids = sorted(rng.sample(range(1, 3_000_000), rng.randint(0, 200)))
        assert catalog.unvarints(catalog.varints(ids)) == ids


# --- what the synthetic ledger holds, worked out here -------------------------------------


def reference(ledger: Path):
    """Counts and sets from the files with plain json and the design's rules."""
    out = {"keys": 0, "remotes": 0, "brands": defaultdict(Counter), "models": defaultdict(set)}
    for path in tree_files(ledger):
        doc = json.loads(path.read_text(encoding="utf-8"))
        out["remotes"] += 1
        out["keys"] += len(doc["keys"])
        where = path.relative_to(ledger).as_posix()
        irblaster = where.startswith("remotes/irblaster/")
        synthetic = irblaster or where.startswith("remotes/smartir/")
        out["brands"][search_norm(doc["manufacturer"])][doc["manufacturer"]] += 1
        for entry in doc.get("controls", []):
            brand, model = entry.split(" | ", 1) if irblaster else (doc["manufacturer"], entry)
            out["brands"][search_norm(brand)][brand] += 1
            out["models"][search_norm(brand)].add(search_norm(model) or "\0" + model)
        if not synthetic:
            for name in [doc["model"], *doc.get("aliases", [])]:
                out["models"][search_norm(doc["manufacturer"])].add(search_norm(name))
    return out


def test_the_counts_are_the_trees(ledger, full):
    ref = reference(ledger)
    stats = full.stats
    assert (stats["remotes"], stats["keys"]) == (ref["remotes"], ref["keys"]) == (28, 61)
    assert stats["brands"] == len(ref["brands"]) == 15
    assert stats["models"] == sum(len(m) for m in ref["models"].values()) == 29
    assert stats["excludedBrands"] == 0
    assert rows(full, "SELECT COUNT(*) FROM remotes") == [(28,)]
    assert rows(full, "SELECT COUNT(*) FROM keys") == [(61,)]
    manifest = json.loads(full.files[bb.MANIFEST_FILE])
    assert manifest["counts"] == {"brands": 15, "brandAliases": 3, "models": 29, "remotes": 28,
                                  "remoteRefs": 28, "keys": 61, "signals": stats["signals"]}


def test_signals_are_shared_between_keys_and_remotes(ledger, full):
    """The dedupe: far fewer signals than keys, and the number is the number of
    distinct Pronto strings the compile stage gives for the same files."""
    strings = set()
    keys = 0
    for path in tree_files(ledger):
        artifact = compiled_artifact(load_remote(path))
        for spec in artifact["keys"].values():
            strings.add(spec["candidates"]["primary"]["prontoHex"])
            keys += 1
    assert keys == 61
    assert full.stats["signals"] == len(strings) == 42
    assert rows(full, "SELECT COUNT(DISTINCT signal_id) FROM keys") == [(42,)]
    assert {blob_to_pronto(b) for (b,) in rows(full, "SELECT words FROM signals")} == strings


def test_a_signal_is_a_length_prefixed_big_endian_word_blob_in_sorted_order(full):
    blobs = rows(full, "SELECT id, words FROM signals ORDER BY id")
    assert [i for i, _ in blobs] == list(range(1, len(blobs) + 1))
    assert [b for _, b in blobs] == sorted(b for _, b in blobs)
    for _, blob in blobs:
        (count,) = struct.unpack(">H", blob[:2])
        assert len(blob) == 2 + 2 * count
        words = struct.unpack(f">{count}H", blob[2:])
        assert words[0] == 0                      # the format word of a modulated code
        assert count == 4 + 2 * (words[2] + words[3])
        assert blob_words(blob) == list(words)


def test_the_blob_is_the_pronto_string_word_for_word(ledger, full):
    """Through the ledger's own decoder: blob -> string -> signal -> string."""
    for (blob, carrier) in rows(full, "SELECT s.words, r.carrier_hz FROM keys k "
                                      "JOIN signals s ON s.id = k.signal_id "
                                      "JOIN remotes r ON r.id = k.remote_id"):
        text = blob_to_pronto(blob)
        assert pronto.encode(pronto.decode(text, carrier_hz=carrier)) == text


def test_a_remote_has_one_id_for_the_whole_tree_in_path_order(ledger, full, selected):
    files = tree_files(ledger)
    expected = {ref_of(ledger, p): i + 1 for i, p in enumerate(files)}
    assert dict((r, i) for i, r in rows(full, "SELECT id, ref FROM remotes")) == expected
    for ref, rid in ((r, i) for i, r in rows(selected, "SELECT id, ref FROM remotes")):
        assert expected[ref] == rid


# D78's numbers, as the app API pins them (tests/test_app_api.py) and for the ledger names
PLAY = {
    "Sony12": (3, 3, 1, "ledger"), "Sony15": (3, 3, 1, "ledger"), "Sony20": (3, 3, 1, "ledger"),
    "Thomson7": (2, 2, 1, "ledger"), "Proton": (1, 1, 1, "ledger"), "Aiwa": (1, 1, 0, "ledger"),
    "Pioneer-2Part": (0, 0, 0, "ledger"), "JVC": (0, 0, 0, "ledger"),
    "Sharp": (1, 0, 0, "full-signal"), "Denon": (1, 0, 0, "full-signal"),
}


def test_play_numbers_are_d78s(full):
    seen = set()
    for protocol, repeat, helper, empty, rule in rows(
            full, "SELECT protocol, repeat_passes, helper_repeat_passes, intro_empty, rule "
                  "FROM remotes WHERE protocol IS NOT NULL"):
        if protocol in PLAY:
            assert (repeat, helper, empty, rule) == PLAY[protocol], protocol
            seen.add(protocol)
    assert seen == set(PLAY)


def test_play_numbers_of_the_other_sources_follow_the_ledger_rule(full):
    """minSends 2 with an intro: the intro once, then the repeat once; minSends 3 with no
    intro: the repeat three times; neither is a whole-signal protocol."""
    got = {ref: row for ref, *row in rows(
        full, "SELECT ref, repeat_passes, helper_repeat_passes, intro_empty, rule FROM remotes "
              "WHERE source != 4")}
    assert got["lirc/acme/TV-1"] == [1, 1, 0, "ledger"]
    assert got["lirc/zed/Z-9"] == [3, 3, 1, "ledger"]
    assert got["smartir/Acme/media_player_7"] == [1, 1, 1, "ledger"]
    assert got["topping/RC-15A"] == [0, 0, 0, "ledger"]


def test_a_remote_of_the_import_has_no_model_and_the_others_keep_theirs(full):
    got = dict(rows(full, "SELECT ref, model FROM remotes"))
    assert all(m is None for ref, m in got.items() if ref.startswith(("irblaster/", "smartir/")))
    assert got["lirc/acme/TV-1"] == "TV-1" and got["topping/RC-15A"] == "RC-15A"


def test_remote_facts_come_from_the_file(ledger, full):
    got = {ref: row for ref, *row in rows(
        full, "SELECT ref, source, tier, key_count, protocol, carrier_hz FROM remotes")}
    assert got["topping/RC-15A"] == [1, 1, 1, "NEC1", 38000]     # authored, verified
    assert got["lirc/acme/TV-1"] == [2, 2, 4, None, 38000]      # lirc, plausible, unnamed
    assert got["smartir/Acme/media_player_7"] == [3, 2, 2, None, 38000]
    for path in tree_files(ledger):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert got[ref_of(ledger, path)][2] == len(doc["keys"])
        assert got[ref_of(ledger, path)][4] == doc["protocol"]["carrierHz"]


def test_spellings_of_a_brand_are_one_brand_named_by_the_most_used(full):
    names = dict(rows(full, "SELECT norm, name FROM brands"))
    assert names["acme"] == "ACME"                  # ACME on the import, Acme and acme once each
    assert names["sony"] == "SONY"
    assert names["uni"] == "Ünï" and names["ez"] == "Éz"
    assert len(names) == len(set(names))


def test_models_of_a_brand_merge_on_the_search_key_and_kind_is_the_best(full):
    models = {name: kind for name, kind in rows(
        full, "SELECT m.name, m.kind FROM models m JOIN brands b ON b.id = m.brand_id "
              "WHERE b.norm = 'acme'")}
    # ACME | TV-1 and ACME | tv-1 of the import and the LIRC remote TV-1 are one model,
    # which a person owns: kind 0 wins over the remote's part number
    assert models["TV-1"] == 0
    assert "tv-1" not in models
    assert models["TV1 old"] == 1                  # an alias is a part number
    assert "SmartIR media_player 7" not in models  # a placeholder is not a model
    assert not any(n.startswith("IR Blaster DB") for n in models)
    assert {"TV_2", "TV[3]"} <= set(models)        # different keys, so different models


def test_a_brands_models_are_a_contiguous_range_of_rows(full):
    for brand_id, first, count in rows(full, "SELECT id, first_model, model_count FROM brands"):
        ids = [i for (i,) in rows(full, "SELECT id FROM models WHERE brand_id = ?", brand_id)]
        assert ids == list(range(first, first + count))


def test_controls_link_a_model_to_the_remotes_of_every_source_that_lists_it(full):
    got = rows(full, "SELECT r.ref FROM controls c JOIN models m ON m.id = c.model_id "
                     "JOIN brands b ON b.id = m.brand_id JOIN remotes r ON r.id = c.remote_id "
                     "WHERE b.norm = 'acme' AND m.name = 'TV-1' ORDER BY r.ref")
    assert [r for (r,) in got] == ["irblaster/ACME/1-NEC1", "lirc/acme/TV-1"]
    smartir = rows(full, "SELECT r.ref FROM controls c JOIN models m ON m.id = c.model_id "
                         "JOIN remotes r ON r.id = c.remote_id WHERE m.name = 'Acme Stream 1'")
    assert smartir == [("smartir/Acme/media_player_7",)]


def test_every_model_has_a_remote_and_the_brand_row_of_a_remotes_maker_exists(full):
    assert rows(full, "SELECT COUNT(*) FROM models WHERE id NOT IN (SELECT model_id FROM controls)") == [(0,)]
    assert rows(full, "SELECT COUNT(*) FROM remotes WHERE brand_id IS NULL") == [(0,)]


def test_the_grams_are_those_of_the_models_search_keys(full):
    expected = defaultdict(list)
    for mid, name in rows(full, "SELECT id, name FROM models ORDER BY id"):
        for gram in catalog.grams(search_norm(name)):
            expected[gram].append(mid)
    stored = {g: catalog.unvarints(b) for g, b in rows(full, "SELECT gram, ids FROM ngram")}
    assert stored == dict(expected)
    assert [g for (g,) in rows(full, "SELECT gram FROM ngram")] == sorted(stored)


# --- the keys ---------------------------------------------------------------------------------


def test_a_key_stores_its_text_only_when_the_canonical_key_does_not_say_it(full):
    vocab = load_vocabulary()
    spelled = {k.id: {squash(k.id), squash(k.name)} for k in vocab.keys}
    keys = {k.id: i + 1 for i, k in enumerate(vocab.keys)}
    names = {i: k for k, i in keys.items()}
    got = rows(full, "SELECT r.ref, k.n, k.canon, k.label FROM keys k JOIN remotes r ON r.id = k.remote_id")
    by_remote = defaultdict(list)
    for ref, n, canon, label in got:
        by_remote[ref].append((n, names.get(canon), label))
    # the LIRC remote: names only. KEY_AGAIN is not canonical, so its name is its text
    lirc = {(canon, label) for _, canon, label in by_remote["lirc/acme/TV-1"]}
    assert lirc == {("POWER", None), ("VOLUME_UP", None), ("MUTE", None), (None, "KEY_AGAIN")}
    for ref, items in by_remote.items():
        for _, canon, label in items:
            if canon is None:
                assert label is not None, ref
            elif label is not None:
                assert squash(label) not in spelled[canon], (ref, canon, label)


def test_the_import_keeps_the_labels_that_say_more_than_the_canonical_key(full):
    items = rows(full, "SELECT k.canon, k.label FROM keys k JOIN remotes r ON r.id = k.remote_id "
                       "WHERE r.ref = 'irblaster/ACME/1-NEC1' ORDER BY k.n")
    labels = [label for _, label in items if label is not None]
    assert "VOL+" in labels and "??" in labels and "STANDBY" in labels   # not the canonical name
    assert "POWER" not in labels and "OK" not in labels and "1" not in labels  # it says it already
    names = {i + 1: k.id for i, k in enumerate(load_vocabulary().keys)}
    assert {names[c] for c, _ in items if c} >= {"POWER", "VOLUME_UP", "OK", "DIGIT_1"}


def test_the_vocabulary_is_in_the_bundle_with_its_version(full):
    vocab = load_vocabulary()
    got = rows(full, "SELECT key FROM vocab_keys ORDER BY id")
    assert [k for (k,) in got] == [k.id for k in vocab.keys]
    assert len(got) == 156
    assert rows(full, "SELECT value FROM meta WHERE key = 'vocabularyVersion'") == [(str(vocab.version),)]
    assert [g for (g,) in rows(full, "SELECT key FROM vocab_groups ORDER BY id")] == [
        g.id for g in vocab.groups]
    assert rows(full, "SELECT COUNT(*) FROM keys WHERE canon IS NOT NULL AND canon NOT IN "
                      "(SELECT id FROM vocab_keys)") == [(0,)]


def test_a_remote_with_a_second_candidate_group_is_counted_not_hidden(ledger, tmp_path):
    """A candidate group other than primary is not carried (D89); the report counts them."""
    root = tmp_path / "ledger"
    make_corpus(root)
    path = root / "remotes/topping/RC-15A.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["variants"] = {"mode2": {"confidence": "untested", "source": "manual",
                                 "override": {"subdevice": "0xEA"}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    built = bb.build_bundle(root, "full")
    assert built.problems == [] and built.stats["otherCandidates"] == 1
    assert built.stats["keys"] == 61                          # one row per key, whatever it has


# --- the file ------------------------------------------------------------------------------------


def test_the_pragmas_are_fixed(full):
    data = full.files[bb.BUNDLE_FILE]
    conn = db(full)
    one = lambda pragma: conn.execute(f"PRAGMA {pragma}").fetchone()[0]  # noqa: E731
    assert one("page_size") == 4096 and one("encoding") == "UTF-8"
    assert one("auto_vacuum") == 0 and one("freelist_count") == 0
    assert one("application_id") == 0x524C4231 and one("user_version") == 1
    assert one("integrity_check") == "ok"
    assert data[:16] == b"SQLite format 3\x00"
    assert struct.unpack(">H", data[16:18])[0] == 4096
    assert data[18:20] == b"\x01\x01"                      # rollback journal: no WAL on a read-only asset
    assert len(data) == 4096 * one("page_count")
    assert data[20] == 0                                   # no reserved bytes
    assert json.loads(full.files[bb.MANIFEST_FILE])["bundle"]["bytes"] == len(data)


FORBIDDEN = re.compile(r"\b(STRICT|GENERATED|RETURNING|VIRTUAL|FTS\d|RTREE|USING|TRIGGER|VIEW)\b", re.I)


def test_the_schema_needs_nothing_newer_than_sqlite_3_28(full):
    """Android 11 ships SQLite 3.28: no STRICT tables (3.37), generated columns (3.31),
    RETURNING (3.35) and no module, trigger or view at all."""
    schema = rows(full, "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL")
    assert {t for t, _, _ in schema} == {"table", "index"}
    for _, name, sql in schema:
        assert not FORBIDDEN.search(sql), (name, sql)
    assert writer.SQLITE_MIN_VERSION == "3.28.0"
    assert sorted(n for t, n, _ in schema if t == "table") == sorted([
        "meta", "sources", "vocab_groups", "vocab_keys", "brands", "brand_aliases", "models",
        "controls", "remotes", "remote_refs", "keys", "signals", "ngram", "excluded_brands"])
    assert {n for t, n, _ in schema if t == "index"} == {"brands_norm", "remotes_ref"}


def test_two_builds_of_one_tree_are_the_same_bytes_whatever_the_worker_count(ledger, full):
    for jobs in (1, 3):
        parallel.configure(jobs)
        again = bb.build_bundle(ledger, "full", aliases=ALIASES)
        assert again.files == full.files, f"differs with {jobs} workers"


def test_the_bytes_do_not_depend_on_the_hash_seed(ledger, tmp_path):
    outputs = []
    for seed in ("1", "2", "31337"):
        out = tmp_path / f"out-{seed}"
        done = subprocess.run(
            [sys.executable, "-m", "remote_ledger.cli", "bundle", "--profile", "selected",
             "--out", str(out), "-j", "2"],
            cwd=ledger, env={**os.environ, "PYTHONHASHSEED": seed,
                             "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        outputs.append({p.name: p.read_bytes() for p in out.iterdir()})
    assert outputs[0] == outputs[1] == outputs[2]
    assert set(outputs[0]) == {"catalog.sqlite", "notices.json", "manifest.json"}


def test_data_version_is_the_digest_of_the_rows_and_not_of_the_pages(full):
    conn = db(full)
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    assert writer.content_digest(conn) == meta["dataVersion"]
    assert re.fullmatch(r"[0-9a-f]{12}", meta["dataVersion"])
    # the same rows in a file with another page size and another insert order of the
    # meta table are the same version
    other = sqlite3.connect(":memory:")
    other.execute("PRAGMA page_size = 16384")
    other.executescript(writer.SCHEMA)
    for table, _ in writer.DIGEST_TABLES:
        cursor = conn.execute(f"SELECT * FROM {table}")
        width = len(cursor.description)
        other.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * width)})",
                          cursor.fetchall())
    assert other.execute("PRAGMA page_size").fetchone()[0] == 16384
    assert writer.content_digest(other) == meta["dataVersion"]
    # and a changed row is another version
    other.execute("UPDATE keys SET confidence = 3 WHERE remote_id = 1 AND n = 0")
    assert writer.content_digest(other) != meta["dataVersion"]


def test_the_manifest_lists_the_files_by_size_and_hash(full):
    import gzip
    import hashlib

    manifest = json.loads(full.files[bb.MANIFEST_FILE])
    data, notices = full.files[bb.BUNDLE_FILE], full.files[bb.NOTICES_FILE]
    assert manifest["schemaVersion"] == 1 and manifest["vocabularyVersion"] == 1
    assert manifest["profile"] == "full"
    assert manifest["bundle"] == {
        "file": "catalog.sqlite", "bytes": len(data),
        "gzipBytes": len(gzip.compress(data, 9, mtime=0)),
        "sha256": hashlib.sha256(data).hexdigest()}
    assert manifest["notices"] == {"file": "notices.json", "bytes": len(notices),
                                   "sha256": hashlib.sha256(notices).hexdigest()}
    assert manifest["brands"] == {"included": 15, "excluded": 0}
    assert manifest["dataVersion"] == dict(rows(full, "SELECT key, value FROM meta"))["dataVersion"]
    assert "signature" not in manifest and set(manifest) == {
        "schemaVersion", "dataVersion", "vocabularyVersion", "profile", "bundle", "notices",
        "counts", "brands"}
    assert full.files[bb.MANIFEST_FILE].endswith(b"\n")


# --- the profiles --------------------------------------------------------------------------


def test_read_curated_takes_names_and_drops_comments_and_blanks():
    text = "# a header\n\nSamsung  # tv\n  LG\nHarman Kardon # avr\n   # indented comment\n"
    assert select.read_curated(text) == ["Samsung", "LG", "Harman Kardon"]


def test_the_shipped_list_has_no_brand_twice():
    keys = [search_norm(n) for n in select.read_curated(select.CURATED_FILE.read_text("utf-8"))]
    assert len(keys) > 100 and len(keys) == len(set(keys))


def collected_of(ledger):
    records, problems = corpus.read_corpus(ledger)
    assert problems == []
    return records, catalog.collect(records)


def test_a_brand_that_does_not_fit_is_skipped_and_the_ones_behind_it_are_still_tried(ledger, monkeypatch):
    records, collected = collected_of(ledger)
    everything = select.choose(collected, "selected", "Sony\nZED\nAcme\nNoSuchBrand\n")
    assert everything.curated == ["SONY", "ZED", "ACME"] and everything.skipped == []
    assert everything.unresolved == ["NoSuchBrand"] and everything.estimated_bytes == 8874
    # room for SONY and ZED but not for the three: ACME, which comes between them, does not
    # fit after SONY (3,983 more), ZED behind it still does (3,353 more)
    monkeypatch.setattr(select, "BUDGET_BYTES", 7840)
    squeezed = select.choose(collected, "selected", "Sony\nAcme\nZED\n")
    assert squeezed.curated == ["SONY", "ZED"] and squeezed.skipped == ["ACME"]
    assert squeezed.estimated_bytes == 7840


def test_the_order_of_the_list_is_the_priority(ledger, monkeypatch):
    records, collected = collected_of(ledger)
    monkeypatch.setattr(select, "BUDGET_BYTES", 7840)
    sony_first = select.choose(collected, "selected", "Sony\nZED\nAcme\n")
    acme_first = select.choose(collected, "selected", "Acme\nZED\nSony\n")
    assert (sony_first.curated, sony_first.skipped) == (["SONY", "ZED"], ["ACME"])
    assert (acme_first.curated, acme_first.skipped) == (["ACME", "ZED"], ["SONY"])


def test_a_brand_is_found_by_its_search_key(ledger):
    records, collected = collected_of(ledger)
    found = select.choose(collected, "selected", "t.v.e.\nÜNI\nzed # a comment\n")
    assert sorted(found.curated) == ["T.V.E.", "ZED", "Ünï"] and found.unresolved == []


def test_the_proxy_orders_the_rest_by_models_per_byte_and_needs_models_and_mapped_keys(ledger, monkeypatch):
    records, collected = collected_of(ledger)
    monkeypatch.setattr(select, "MIN_MODELS", 1)
    monkeypatch.setattr(select, "MIN_MAPPED_SHARE", 0.0)
    got = select.choose(collected, "selected", "")
    assert got.curated == [] and got.proxy_added           # nothing on the list: the proxy fills
    stats = collected.brands
    density = [len(stats[search_norm(n)].models) for n in got.proxy_added]
    assert set(got.proxy_added) <= {b.name for b in stats.values()}
    assert len(density) == len(stats)                      # all fit a tiny corpus
    monkeypatch.setattr(select, "MIN_MODELS", 3)
    fewer = select.choose(collected, "selected", "")
    assert set(fewer.proxy_added) == {b.name for b in stats.values() if len(b.models) >= 3}
    monkeypatch.setattr(select, "MIN_MODELS", 1)
    monkeypatch.setattr(select, "MIN_MAPPED_SHARE", 0.99)
    strict = select.choose(collected, "selected", "")
    assert len(strict.proxy_added) < len(got.proxy_added)


def test_the_selected_profile_is_a_subset_that_keeps_every_carried_brand_whole(ledger, full, selected):
    carried = {n for (n,) in rows(selected, "SELECT norm FROM brands")}
    assert carried == {"sony"}
    refs = {r for (r,) in rows(selected, "SELECT ref FROM remotes")}
    assert refs < {r for (r,) in rows(full, "SELECT ref FROM remotes")}
    # every remote of the full bundle that has a SONY model or maker is in it, and no other
    sony = {r for (r,) in rows(full, "SELECT DISTINCT r.ref FROM remotes r LEFT JOIN controls c "
                                    "ON c.remote_id = r.id LEFT JOIN models m ON m.id = c.model_id "
                                    "LEFT JOIN brands b ON b.id = m.brand_id OR b.id = r.brand_id "
                                    "WHERE b.norm = 'sony'")}
    assert refs == sony and len(refs) == 14
    # all the models of SONY, none of another brand
    assert [n for (n,) in rows(selected, "SELECT name FROM models ORDER BY name")] == [
        "KD-1", "KD-2", "KD-3"]
    assert rows(selected, "SELECT COUNT(*) FROM controls WHERE remote_id NOT IN (SELECT id FROM remotes)") == [(0,)]
    # keys, signals and the vocabulary are those of the carried remotes
    assert rows(selected, "SELECT COUNT(*) FROM keys") == rows(
        full, "SELECT COUNT(*) FROM keys WHERE remote_id IN (%s)" % ",".join(
            str(i) for (i,) in rows(selected, "SELECT id FROM remotes")))
    assert rows(selected, "SELECT COUNT(*) FROM signals WHERE id NOT IN (SELECT signal_id FROM keys)") == [(0,)]
    assert rows(selected, "SELECT COUNT(*) FROM vocab_keys") == [(156,)]
    meta = dict(rows(selected, "SELECT key, value FROM meta"))
    assert meta["profile"] == "selected" and "curated list" in meta["selection"]


def test_a_remote_filed_under_a_carried_brand_and_a_left_out_one_keeps_only_the_carried_models(full, selected):
    # id 1 of the synthetic import is filed under ACME, ZED, SONY...: the selection is SONY
    # alone here, so a remote carried for SONY may list other brands that are not carried
    carried = {n for (n,) in rows(selected, "SELECT norm FROM brands")}
    assert carried == {"sony"}
    assert rows(selected, "SELECT COUNT(*) FROM remotes WHERE brand_id IS NULL") == [(0,)]
    assert rows(selected, "SELECT COUNT(*) FROM controls") < rows(full, "SELECT COUNT(*) FROM controls")


def test_the_brands_left_out_are_recorded_with_the_shard_that_has_them(ledger, full, selected):
    left = {name: (norm, api) for name, norm, api in rows(
        selected, "SELECT name, norm, api_key FROM excluded_brands")}
    every = {name for (name,) in rows(full, "SELECT name FROM brands")}
    carried = {name for (name,) in rows(selected, "SELECT name FROM brands")}
    assert set(left) | carried == every and not set(left) & carried
    assert len(left) == 14 and rows(full, "SELECT COUNT(*) FROM excluded_brands") == [(0,)]
    # a brand the import files has its shard's name (D76); an authored or LIRC one has none
    # ACME and Acme are two brands of the import (two shards) and one here (D90)
    assert left["ACME"] == ("acme", ",".join(sorted(
        [app_api.brand_key("ACME"), app_api.brand_key("Acme")])))
    assert left["ZED"][1] == app_api.brand_key("ZED")
    assert all(re.fullmatch(r"[0-9a-f]{10}(,[0-9a-f]{10})*", api or "0" * 10) for _, api in left.values())
    assert left["Topping"] == ("topping", None)
    assert left["T.V.E."][1] == app_api.brand_key("T.V.E.")
    assert selected.stats["excludedBrands"] == 14 and selected.stats["unreachableBrands"] == 1
    assert selected.stats["leftOutRemotes"] == 14 and selected.stats["leftOutNotInApi"] == 4
    # the spellings of one brand that the import writes differently each have a shard
    assert left["Üno"][1] == app_api.brand_key("Üno")


def test_an_unknown_profile_and_too_large_a_bundle_stop_the_build(ledger):
    assert "unknown profile 'tiny'" in bb.build_bundle(ledger, "tiny").problems[0]
    big = bb.build_bundle(ledger, "full", max_bytes=1000)
    assert big.files == {} and "over the 1,000 allowed" in big.problems[0]
    assert bb.build_bundle(ledger, "selected", max_bytes=10**9).problems == []


def test_a_ledger_with_no_remote_has_no_bundle(tmp_path):
    (tmp_path / "remotes").mkdir()
    built = bb.build_bundle(tmp_path, "full")
    assert built.files == {} and "nothing to export" in built.problems[0]


def test_a_remote_whose_signals_disagree_on_the_intro_stops_the_build(tmp_path):
    root = make_corpus(tmp_path / "ledger")
    path = root / "remotes/lirc/zed/Z-9.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["keys"]["KEY_2"]["forms"][0]["intro"] = [9000, 4500, 560, 40000]
    path.write_text(json.dumps(doc), encoding="utf-8")
    built = bb.build_bundle(root, "full")
    assert built.files == {} and "some of its signals have an intro" in built.problems[0]
    assert "remotes/lirc/zed/Z-9.json" in built.problems[0]


def test_an_unreadable_remote_is_named_and_stops_the_build(tmp_path):
    root = make_corpus(tmp_path / "ledger")
    (root / "remotes/lirc/zed/Z-9.json").write_text('{"manufacturer": "x"}', encoding="utf-8")
    built = bb.build_bundle(root, "full")
    assert built.files == {} and built.problems[0].startswith("remotes/lirc/zed/Z-9.json:")


# --- the command, and what it leaves alone ----------------------------------------------------


def run(monkeypatch, root, *argv):
    monkeypatch.chdir(root)
    return cli.main(list(argv))


def test_the_bundle_is_not_a_stage_and_owns_no_path():
    assert [g.name for g in generators.PIPELINE] == ["check", "compile", "index", "site", "app"]
    assert generators.owned_paths() == (
        "build/warnings.json", "build/pronto", "build/index.json", "build/index", "site",
        "site/app/v1")


def test_the_default_place_and_signing_keys_are_ignored_by_version_control():
    ignored = lambda path: subprocess.run(                                  # noqa: E731
        ["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0
    assert ignored("bundle-out/selected/catalog.sqlite")
    assert ignored("bundle-out/full/manifest.sig")
    assert ignored("signing.pem") and ignored("keys/private.pem")
    assert not ignored("keys/release.pub.pem")
    # the generated trees are still tracked, as D19 needs
    assert not ignored("build/index.json") and not ignored("site/app/v1/manifest.json")


def test_rl_bundle_writes_the_three_files_and_check_and_verify_pass(ledger, tmp_path, monkeypatch, capsys):
    out = tmp_path / "bundle"
    assert run(monkeypatch, ledger, "bundle", "--out", str(out)) == 0
    text = capsys.readouterr().out
    assert "selected:" in text and "dataVersion" in text and f"written to {out}" in text
    assert "left out:" in text and "selection:" in text
    assert sorted(p.name for p in out.iterdir()) == ["catalog.sqlite", "manifest.json", "notices.json"]
    assert run(monkeypatch, ledger, "bundle", "--out", str(out), "--check") == 0
    assert run(monkeypatch, ledger, "bundle", "--verify", str(out)) == 0
    assert "verified against the tree" in capsys.readouterr().out


def test_check_finds_a_changed_file_a_missing_one_and_ignores_a_signature(ledger, tmp_path, monkeypatch, capsys):
    out = tmp_path / "bundle"
    assert run(monkeypatch, ledger, "bundle", "--out", str(out)) == 0
    capsys.readouterr()
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["signature"] = {"keyId": "0123456789abcdef"}
    (out / "manifest.json").write_bytes(app_api.compact(manifest))
    assert run(monkeypatch, ledger, "bundle", "--out", str(out), "--check") == 0
    (out / "notices.json").write_text("{}\n")
    (out / "catalog.sqlite").unlink()
    assert run(monkeypatch, ledger, "bundle", "--out", str(out), "--check") == 1
    err = capsys.readouterr().err
    assert "notices.json: differs from a fresh build" in err and "catalog.sqlite: missing" in err


def test_the_bundle_is_never_written_under_a_tree_rl_build_owns(ledger, monkeypatch, capsys):
    for where in ("build/bundle", "site/x/y", "build", "site"):
        assert run(monkeypatch, ledger, "bundle", "--out", where) == 1
        err = capsys.readouterr().err
        assert "is inside" in err and "never written there" in err
        assert not (ledger / where / "catalog.sqlite").exists()


def test_writing_again_replaces_a_signature_that_no_longer_matches(ledger, tmp_path, monkeypatch):
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "manifest.sig").write_bytes(b"old")
    assert run(monkeypatch, ledger, "bundle", "--out", str(out)) == 0
    assert not (out / "manifest.sig").exists()


def test_rl_build_check_is_the_same_before_and_after_a_bundle(tmp_path, monkeypatch, capsys):
    """The bundle touches nothing `rl build` owns: the generated trees are the same bytes
    and `rl build --check` still finds no difference."""
    root = make_corpus(tmp_path / "repo")
    assert run(monkeypatch, root, "build") == 0
    snapshot = lambda: {p.relative_to(root).as_posix(): p.read_bytes()                # noqa: E731
                        for d in ("build", "site") for p in (root / d).rglob("*") if p.is_file()}
    before = snapshot()
    capsys.readouterr()
    assert run(monkeypatch, root, "bundle") == 0
    assert (root / "bundle-out" / "selected" / "catalog.sqlite").is_file()
    assert snapshot() == before
    capsys.readouterr()
    assert run(monkeypatch, root, "build", "--check") == 0
    assert "0 difference(s)" in capsys.readouterr().out


def test_the_options_of_the_command_are_parsed():
    args = cli.build_parser().parse_args(["bundle", "--profile", "full", "--out", "x", "--check"])
    assert (args.profile, args.out, args.check, args.verify) == ("full", "x", True, None)
    assert cli.build_parser().parse_args(["bundle"]).profile == "selected"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["bundle", "--profile", "huge"])
