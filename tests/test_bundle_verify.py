"""``rl bundle --verify`` (D88): it passes on a bundle of the tree and reports each way a
bundle can be wrong, which is shown by breaking a good one in a dozen ways.

A mutation edits the rows of a written bundle and then brings ``meta.dataVersion`` and the
manifest's size and hash up to date, so what is left to find the damage is the check under
test and not the digest or the hash that would catch anything.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from bundle_corpus import make_corpus
from remote_ledger import app_api, parallel
from remote_ledger.bundle import aliases as brand_aliases
from remote_ledger.bundle import build as bb
from remote_ledger.bundle import writer
from remote_ledger.bundle.verify import ranked, verify_directory

#: The aliases the good bundles are built and verified with (D101).
ALIASES = (brand_aliases.make("Sony", "索尼"), brand_aliases.make("Sony", "新力"),
           brand_aliases.make("Sony", "共享名"), brand_aliases.make("Topping", "共享名"),     # one alias, two brands
           brand_aliases.make("Nobody", "无名"))


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    return make_corpus(tmp_path_factory.mktemp("verify") / "ledger")


@pytest.fixture(scope="module")
def written(ledger, tmp_path_factory):
    """One good bundle of each profile, written once."""
    out = {}
    for profile in ("full", "selected"):
        built = bb.build_bundle(ledger, profile, aliases=ALIASES)
        assert built.problems == []
        directory = tmp_path_factory.mktemp(f"good-{profile}")
        bb.write_bundle(built, directory, ledger)
        out[profile] = directory
    return out


def refresh(directory: Path) -> None:
    """Bring ``meta`` (its counts and ``dataVersion``) and the manifest up to date with the
    rows."""
    path = directory / bb.BUNDLE_FILE
    conn = sqlite3.connect(path, isolation_level=None)
    tables = {"brands": "brands", "brandAliases": "brand_aliases", "models": "models",
              "controls": "controls", "remotes": "remotes", "remoteRefs": "remote_refs", "keys": "keys",
              "signals": "signals", "excludedBrands": "excluded_brands"}
    counts = {name: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for name, table in tables.items()}
    for name, n in counts.items():
        conn.execute("UPDATE meta SET value = ? WHERE key = ?", (str(n), f"count.{name}"))
    version = writer.content_digest(conn)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'dataVersion'", (version,))
    conn.close()
    manifest = json.loads((directory / bb.MANIFEST_FILE).read_bytes())
    data = path.read_bytes()
    manifest["dataVersion"] = version
    manifest["bundle"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    manifest["counts"] = {n: counts[n] for n in ("brands", "brandAliases", "models", "remotes", "remoteRefs",
                                                 "keys", "signals")}
    manifest["brands"] = {"included": counts["brands"], "excluded": counts["excludedBrands"]}
    (directory / bb.MANIFEST_FILE).write_bytes(app_api.compact(manifest))


def damaged(written, tmp_path, profile, *statements, update=True):
    directory = tmp_path / "bundle"
    shutil.copytree(written[profile], directory)
    conn = sqlite3.connect(directory / bb.BUNDLE_FILE, isolation_level=None)
    for sql in statements:
        conn.execute(sql)
    conn.close()
    if update:
        refresh(directory)
    return directory


def problems_of(ledger, directory) -> list[str]:
    return verify_directory(ledger, directory, aliases=ALIASES)[0]


def test_a_good_bundle_of_either_profile_verifies(ledger, written):
    for profile, remotes in (("full", 28), ("selected", 14)):
        problems, facts = verify_directory(ledger, written[profile], aliases=ALIASES)
        assert problems == []
        assert facts["remotes"] == remotes and facts["decodedSignals"] >= remotes
        assert facts["sampledRemotes"] == remotes             # fewer than the sample size
        assert facts["bytes"] == (written[profile] / "catalog.sqlite").stat().st_size


def test_the_sample_is_deterministic_and_does_not_depend_on_the_order():
    names = [f"r{i}" for i in range(50)]
    assert ranked(names, 5, "x") == ranked(list(reversed(names)), 5, "x")
    assert ranked(names, 5, "x") != ranked(names, 5, "y")
    assert len(set(ranked(names, 5, "x"))) == 5


# -- the files ------------------------------------------------------------------------------------


def test_a_changed_bundle_file_is_not_the_one_the_manifest_lists(ledger, written, tmp_path):
    directory = tmp_path / "b"
    shutil.copytree(written["full"], directory)
    data = bytearray((directory / "catalog.sqlite").read_bytes())
    data[-1] ^= 1
    (directory / "catalog.sqlite").write_bytes(bytes(data))
    got = problems_of(ledger, directory)
    assert any("its SHA-256 is not the manifest's" in p for p in got)


def test_a_wrong_size_in_the_manifest_is_reported(ledger, written, tmp_path):
    directory = tmp_path / "b"
    shutil.copytree(written["full"], directory)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["notices"]["bytes"] += 1
    (directory / "manifest.json").write_bytes(app_api.compact(manifest))
    assert any("notices.json: " in p and "the manifest says" in p for p in problems_of(ledger, directory))


def test_a_missing_manifest_or_file_is_reported(ledger, written, tmp_path):
    directory = tmp_path / "b"
    shutil.copytree(written["full"], directory)
    (directory / "notices.json").unlink()
    assert problems_of(ledger, directory) == [f"{directory / 'notices.json'}: missing"]
    (directory / "manifest.json").unlink()
    assert problems_of(ledger, directory) == [f"{directory / 'manifest.json'}: missing"]


def test_a_file_that_is_not_a_database_does_not_pass(ledger, written, tmp_path):
    directory = tmp_path / "b"
    shutil.copytree(written["full"], directory)
    (directory / "catalog.sqlite").write_bytes(b"not sqlite" * 500)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    data = (directory / "catalog.sqlite").read_bytes()
    manifest["bundle"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    (directory / "manifest.json").write_bytes(app_api.compact(manifest))
    with pytest.raises(sqlite3.DatabaseError):
        verify_directory(ledger, directory)


@pytest.mark.parametrize("statement, message", [
    ("PRAGMA user_version = 2", "user_version is not the schema version"),
    ("PRAGMA application_id = 1", "application_id is not the bundle's"),
])
def test_the_pragmas_are_checked(ledger, written, tmp_path, statement, message):
    directory = damaged(written, tmp_path, "full", statement)
    assert message in problems_of(ledger, directory)


# -- the rows ------------------------------------------------------------------------------------


def test_the_manifest_and_meta_must_agree(ledger, written, tmp_path):
    directory = damaged(written, tmp_path, "full", "UPDATE meta SET value = 'x' WHERE key = 'profile'",
                        update=False)
    assert any("meta profile is 'x', the manifest says 'full'" in p for p in problems_of(ledger, directory))
    directory = damaged(written, tmp_path / "2", "full",
                        "UPDATE meta SET value = '99' WHERE key = 'count.keys'", update=False)
    assert any("keys has 61 rows, meta count.keys says 99" in p for p in problems_of(ledger, directory))


def test_a_changed_row_is_another_data_version(ledger, written, tmp_path):
    directory = damaged(written, tmp_path, "full",
                        "UPDATE keys SET confidence = 3 WHERE remote_id = 1 AND n = 0", update=False)
    got = problems_of(ledger, directory)
    assert any("the rows give the dataVersion" in p for p in got)


MUTATIONS = [
    ("a byte of a signal",
     "UPDATE signals SET words = CAST(substr(words, 1, 10) || x'7f' || substr(words, 12) AS BLOB) "
     "WHERE id = (SELECT signal_id FROM keys WHERE remote_id = 25 AND n = 0)",
     "full", ["key 0 (KEY_AGAIN) is not the file's", "key 0 (KEY_AGAIN) is not the Pronto string"]),
    ("a key's signal swapped for another",
     "UPDATE keys SET signal_id = (SELECT MAX(id) FROM signals) WHERE remote_id = 25 AND n = 1",
     "full", ["is not the file's"]),
    ("a key's canonical id",
     "UPDATE keys SET canon = 2 WHERE remote_id = 25 AND n = 1", "full", ["is not the file's"]),
    ("a key's stored text",
     "UPDATE keys SET label = 'POWER' WHERE remote_id = 25 AND n = 0", "full", ["is not the file's"]),
    ("a key's tier", "UPDATE keys SET confidence = 3 WHERE remote_id = 28 AND n = 0", "full",
     ["is not the file's"]),
    ("a key missing",
     "DELETE FROM keys WHERE remote_id = 25 AND n = 3", "full",
     ["4 keys in the file", "rows of remotes.key_count are wrong"]),
    ("a carrier", "UPDATE remotes SET carrier_hz = 38400 WHERE ref = 'lirc/acme/TV-1'", "full",
     ["source, tier, protocol, carrier or key count differ from the file", "Hz: "]),
    ("a play number", "UPDATE remotes SET repeat_passes = 9 WHERE ref = 'lirc/acme/TV-1'", "full",
     ["plays as"]),
    ("a rule", "UPDATE remotes SET rule = 'full-signal' WHERE ref = 'lirc/acme/TV-1'", "full",
     ["plays as"]),
    ("a tier", "UPDATE remotes SET tier = 0 WHERE ref = 'lirc/acme/TV-1'", "full",
     ["source, tier, protocol, carrier or key count differ from the file"]),
    ("a model on a placeholder remote",
     "UPDATE remotes SET model = 'IR Blaster DB 1' WHERE ref = 'irblaster/ACME/1-NEC1'", "full",
     ["model 'IR Blaster DB 1' is not the file's"]),
    ("an id out of path order",
     "UPDATE remotes SET id = 100 WHERE ref = 'topping/RC-15A'; UPDATE keys SET remote_id = 100 "
     "WHERE remote_id = 28; UPDATE controls SET remote_id = 100 WHERE remote_id = 28; "
     "UPDATE remote_refs SET remote_id = 100 WHERE remote_id = 28",
     "full", ["id 100, the tree's order gives 28"]),
    ("a remote the tree does not have",
     "INSERT INTO remotes VALUES (99, 'lirc/ghost/none', NULL, NULL, 2, 2, 1, NULL, 38000, 0, 0, 0, 'ledger'); "
     "INSERT INTO keys VALUES (99, 0, NULL, 'x', 1, 2); INSERT INTO remote_refs VALUES ('lirc/ghost/none', 99, 0, 1)",
     "full", ["remote 99 lirc/ghost/none: no such file in the tree"]),
    ("a remote of the full profile missing",
     "DELETE FROM controls WHERE remote_id = 28", "full", ["lists 'Topping' / 'DX3 Pro', which is not linked to it"]),
    ("a model linked to a remote that does not list it",
     "INSERT INTO controls VALUES (1, 28)", "full", ["is linked to topping/RC-15A, which does not list it"]),
    ("a grams table that is not the models'",
     "UPDATE ngram SET ids = x'01' WHERE gram = '^tv'", "full", ["ngram is not the grams of the models' names"]),
    ("a brand's range of models",
     "UPDATE brands SET first_model = first_model + 1 WHERE norm = 'acme'", "full",
     ["its models are not the rows"]),
    ("a brand's search key", "UPDATE brands SET norm = 'zzz' WHERE norm = 'acme'", "full",
     ["has the search key 'zzz'"]),
    ("a signal no key uses",
     "INSERT INTO signals VALUES (9999, x'00010000')", "full", ["rows of unused signals"]),
    ("a key of no remote",
     "INSERT INTO keys VALUES (777, 0, NULL, 'x', 1, 2)", "full", ["rows of keys.remote_id"]),
    ("a canonical id that is not the vocabulary's",
     "UPDATE keys SET canon = 999 WHERE remote_id = 25 AND n = 1", "full", ["rows of keys.canon"]),
    ("an alias of a brand the bundle does not have",
     "INSERT INTO brand_aliases VALUES ('无名', '无名', 999)", "full", ["rows of brand_aliases.brand_id"]),
    ("an alias that is a brand's own name",
     "INSERT INTO brand_aliases VALUES ('SONY', 'sony', 10)", "full", ["rows of aliases that are a brand's own name"]),
    ("an alias whose key is not its text's",
     "UPDATE brand_aliases SET norm = 'zzz' WHERE norm = '索尼'", "full", ["the alias '索尼' has the search key 'zzz'"]),
    ("an alias missing", "DELETE FROM brand_aliases WHERE norm = '新力'", "full",
     ["brand_aliases has 3 rows and is not the list that ships for these brands (4 rows)"]),
    ("a shared alias with one of its brands missing",
     "DELETE FROM brand_aliases WHERE norm = '共享名' AND brand_id = (SELECT id FROM brands WHERE norm = 'topping')",
     "full", ["is not the list that ships"]),
    ("an alias of another brand", "UPDATE brand_aliases SET brand_id = 1 WHERE norm = '索尼'", "full",
     ["is not the list that ships"]),
    ("an alias of a brand the profile leaves out",
     "INSERT INTO brand_aliases VALUES ('拓品', '拓品', 1)", "selected", ["is not the list that ships"]),
    ("a brand both carried and left out",
     "INSERT INTO excluded_brands VALUES ('SONY', 'sony', NULL)", "selected",
     ["rows of excluded brands that are in"]),
    ("a remote the selection should carry, missing",
     "DELETE FROM keys WHERE remote_id = 15; DELETE FROM remotes WHERE id = 15; "
     "DELETE FROM controls WHERE remote_id = 15; DELETE FROM remote_refs WHERE remote_id = 15; "
     "DELETE FROM signals WHERE id NOT IN (SELECT signal_id FROM keys)",
     "selected", ["not in the bundle although a brand of it is carried"]),
]


@pytest.mark.parametrize("what, sql, profile, messages", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_way_a_bundle_can_be_wrong_is_found(ledger, written, tmp_path, what, sql, profile, messages):
    statements = [part for part in sql.split("; ")]
    directory = damaged(written, tmp_path, profile, *statements)
    got = "\n".join(problems_of(ledger, directory))
    assert got, f"{what}: nothing was reported"
    assert any(m in got for m in messages), f"{what}: {messages} not in\n{got}"


def test_a_bundle_written_before_the_aliases_is_told_to_be_built_again(ledger, written, tmp_path):
    directory = damaged(written, tmp_path, "full", "DROP TABLE brand_aliases", update=False)
    got = problems_of(ledger, directory)
    assert any("the bundle has no brand_aliases table: it was written before the brand aliases of D101; "
               "build it again" in p for p in got)


def test_the_count_of_aliases_must_agree_in_the_table_meta_and_the_manifest(ledger, written, tmp_path):
    directory = damaged(written, tmp_path, "full", "UPDATE meta SET value = '99' WHERE key = 'count.brandAliases'",
                        update=False)
    assert any("brand_aliases has 4 rows, meta count.brandAliases says 99" in p for p in problems_of(ledger, directory))
    directory = tmp_path / "2" / "b"
    shutil.copytree(written["full"], directory)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["counts"]["brandAliases"] = 7
    (directory / "manifest.json").write_bytes(app_api.compact(manifest))
    assert any("brand_aliases has 4 rows, the manifest says 7" in p for p in problems_of(ledger, directory))


def test_the_aliases_are_checked_against_the_list_the_bundle_was_built_with(ledger, written):
    assert problems_of(ledger, written["full"]) == []
    other = (brand_aliases.make("Sony", "索尼"),)
    got = verify_directory(ledger, written["full"], aliases=other)[0]
    assert any("brand_aliases has 4 rows and is not the list that ships for these brands (1 rows)" in p for p in got)
    assert verify_directory(ledger, written["full"], aliases=ALIASES)[1]["brandAliases"] == 4


def test_a_remote_of_a_left_out_brand_in_the_selected_bundle_is_found(ledger, written, tmp_path):
    """Put a remote of ACME, which the selected bundle does not carry, into it."""
    directory = tmp_path / "b"
    shutil.copytree(written["selected"], directory)
    full = sqlite3.connect(written["full"] / "catalog.sqlite")
    row = full.execute("SELECT * FROM remotes WHERE ref = 'lirc/zed/Z-9'").fetchone()
    keys = full.execute("SELECT * FROM keys WHERE remote_id = ?", (row[0],)).fetchall()
    signals = full.execute("SELECT * FROM signals WHERE id IN (SELECT signal_id FROM keys WHERE remote_id = ?)",
                           (row[0],)).fetchall()
    full.close()
    conn = sqlite3.connect(directory / "catalog.sqlite", isolation_level=None)
    top = conn.execute("SELECT COALESCE(MAX(id), 0) FROM signals").fetchone()[0]
    mapped = {}
    for sid, blob in signals:
        top += 1
        mapped[sid] = top
        conn.execute("INSERT INTO signals VALUES (?, ?)", (top, blob))
    conn.execute("INSERT INTO remotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (*row[:2], None, *row[3:]))
    conn.execute("INSERT INTO remote_refs VALUES (?, ?, 0, ?)", (row[1], row[0], len(keys)))
    for remote_id, n, canon, label, signal_id, confidence in keys:
        conn.execute("INSERT INTO keys VALUES (?,?,?,?,?,?)",
                     (remote_id, n, canon, label, mapped[signal_id], confidence))
    conn.close()
    refresh(directory)
    got = problems_of(ledger, directory)
    assert any("in the bundle although no brand of it is carried" in p for p in got)


def test_a_stale_bundle_is_found_when_the_tree_moves_on(ledger, written, tmp_path):
    """The bundle was built before a raw duration of the LIRC remote was corrected."""
    moved = tmp_path / "ledger"
    shutil.copytree(ledger, moved)
    path = moved / "remotes/lirc/acme/TV-1.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["keys"]["KEY_POWER"]["forms"][0]["repeat"][0] = 9100
    path.write_text(json.dumps(doc), encoding="utf-8")
    got = problems_of(moved, written["full"])
    assert any("lirc/acme/TV-1: key 0 (KEY_AGAIN)" in p or "lirc/acme/TV-1: key" in p for p in got)
    assert any("is not the Pronto string the compile stage gives" in p for p in got)


def test_a_remote_added_to_the_tree_after_the_build_is_found_in_the_full_profile(ledger, written, tmp_path):
    moved = tmp_path / "ledger"
    shutil.copytree(ledger, moved)
    extra = moved / "remotes/lirc/acme/TV-2.json"
    extra.write_text((moved / "remotes/lirc/acme/TV-1.json").read_text(encoding="utf-8"))
    got = problems_of(moved, written["full"])
    assert any("the full bundle lacks 1 remotes of the tree, such as lirc/acme/TV-2" in p for p in got)
    # and the ids of every later remote have moved
    assert any("the tree's order gives" in p for p in got)


def test_the_committed_compile_artifact_is_what_the_sample_is_compared_with(ledger, written, tmp_path):
    """With a ``build/pronto`` file present it is the one read; a wrong one is reported."""
    moved = tmp_path / "ledger"
    shutil.copytree(ledger, moved)
    from remote_ledger import paths
    from remote_ledger.cli import compiled_artifact
    from remote_ledger.remote import load_remote
    from remote_ledger.serialize import dumps

    where = "remotes/topping/RC-15A.json"
    artifact = compiled_artifact(load_remote(moved / where))
    target = moved / paths.artifact(where)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(artifact), encoding="utf-8")
    assert problems_of(moved, written["full"]) == []
    artifact["keys"]["KEY_POWER"]["candidates"]["primary"]["prontoHex"] = "0000 006D 0000 0001 0001 0001"
    target.write_text(dumps(artifact), encoding="utf-8")
    got = problems_of(moved, written["full"])
    assert any("topping/RC-15A: key 0 (KEY_POWER) is not the Pronto string the compile stage gives" in p
               for p in got)
