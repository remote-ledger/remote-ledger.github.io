"""The index's shards (D69): what index.json keeps, what moves out of it, and
what must not change when it does.

The point of the whole mechanism is a promise to a reader this repository
cannot see: the SwiftRemote app in the field downloads ``index.json`` on every
search and reads only ``schemaVersion``, ``remotes`` and ``unresolved``. So
these tests are mostly about what ``index.json`` does *not* gain, and about R20
holding across the files it no longer holds.
"""

import json
import shutil

import pytest

from remote_ledger import cli, generators, paths
from remote_ledger.index import (
    build_all, build_index, inputs_record, load_committed, merge, shard_entries,
    shard_files, shard_key,
)
from remote_ledger.serialize import dumps
from shard_corpus import corpus, generate, write_remote  # noqa: F401 (a fixture)

INDEX_STAGE = tuple(g for g in generators.PIPELINE if g.name == "index")


# --- what moves, and what does not -----------------------------------------

@pytest.mark.parametrize("name,key", [
    ("Zenith", "z"), ("akai", "a"), ("3B TECH", "0"), ("  \"Quoted\"", "q"),
    ("#1 Remotes", "0"), ("Élan", "_"), ("日本電気", "_"), ("---", "_"), ("", "_"),
])
def test_the_part_is_the_first_letter_or_digit_of_the_manufacturer(name, key):
    """ASCII only: str.casefold moves with the interpreter's Unicode tables,
    and a regenerated part must not (D49's reason)."""
    assert shard_key(name) == key


def test_a_corpus_without_a_shard_indexes_exactly_as_before(tmp_path):
    """The master corpus has no imported database, and index.json must stay
    what it was, byte for byte: no `shards` key, not even an empty one."""
    write_remote(tmp_path, "remotes/topping/RC-15A.json", "Topping", "RC-15A")
    write_remote(tmp_path, "remotes/lirc/sony/RM-1.json", "sony", "RM-1")
    built = build_all(tmp_path)
    assert sorted(built.index) == ["remotes", "schemaVersion", "unresolved"]
    assert built.shards == ()
    assert shard_files(built.shards) == {}


def test_the_imported_database_leaves_index_json(corpus):
    built = build_all(corpus)
    files = {r["file"] for r in built.index["remotes"]}
    assert files == {"remotes/topping/RC-15A.json", "remotes/lirc/sony/RM-1.json"}
    (shard,) = built.shards
    assert {e["file"] for e in shard_entries(built.shards)} == {
        "remotes/irblaster/ZENITH/1-NEC1.json", "remotes/irblaster/AKAI/2-NEC1.json",
        "remotes/irblaster/3B_TECH/3-NEC1.json",
    }
    assert list(shard.parts) == ["0", "a", "z"]


def test_index_json_gains_one_small_additive_field_and_nothing_else(corpus):
    """What an old reader sees: the entries it always saw, and `shards`, which
    it never reads. Its path is relative to index.json, so it holds in both
    build/ and site/."""
    index = build_index(corpus)[0]
    assert sorted(index) == ["remotes", "schemaVersion", "shards", "unresolved"]
    assert index["schemaVersion"] == 1
    assert index["shards"] == [{
        "name": "irblaster",
        "path": "index/irblaster/manifest.json",
        "remotes": 3,
        "root": "remotes/irblaster/",
    }]
    # The advertisement is the whole growth: a few hundred bytes, whatever the
    # shard holds.
    plain = {k: v for k, v in index.items() if k != "shards"}
    assert len(dumps(index)) - len(dumps(plain)) < 250


def test_sharding_changes_no_entry_and_no_order(corpus, monkeypatch):
    """The entries are index.json's own, and merged they are the index an
    unsharded ledger would have written -- which is why `rl lookup` prints
    the same either way."""
    built = build_all(corpus)
    monkeypatch.setattr(paths, "SHARDED", {})
    unsharded = build_all(corpus)
    assert unsharded.shards == ()
    merged = merge(built.index, shard_entries(built.shards))
    assert merged == unsharded.index
    assert [r["file"] for r in merged["remotes"]] == \
        [r["file"] for r in unsharded.index["remotes"]]


def test_the_manifest_and_parts_describe_each_other(corpus):
    files = shard_files(build_all(corpus).shards)
    manifest = json.loads(files["index/irblaster/manifest.json"])
    assert manifest["schemaVersion"] == 1
    assert manifest["name"] == "irblaster" and manifest["root"] == "remotes/irblaster/"
    assert manifest["remotes"] == 3
    assert [p["key"] for p in manifest["parts"]] == ["0", "a", "z"]
    total = 0
    for part in manifest["parts"]:
        # Part paths are relative to the manifest, which sits beside them.
        text = files[f"index/irblaster/{part['path']}"]
        doc = json.loads(text)
        assert part["bytes"] == len(text.encode("utf-8"))
        assert part["remotes"] == len(doc["remotes"])
        total += part["remotes"]
    assert total == manifest["remotes"]
    assert sorted(files) == sorted(
        ["index/irblaster/manifest.json"]
        + [f"index/irblaster/{k}.json" for k in "0az"])


def test_a_part_parses_as_an_index_without_unresolved(corpus):
    """One parser serves index.json and its parts: same top-level shape, same
    entries, and the unresolved list stays in index.json alone."""
    files = shard_files(build_all(corpus).shards)
    doc = json.loads(files["index/irblaster/a.json"])
    assert sorted(doc) == ["remotes", "schemaVersion"]
    (entry,) = doc["remotes"]
    assert entry["manufacturer"] == "AKAI" and entry["controls"] == ["AKAI | AK 80"]
    assert entry["importedFrom"] == "remotes/irblaster/"
    own = build_index(corpus)[0]["remotes"][0]
    assert set(entry) == set(own) | {"importedFrom"}


def test_every_part_is_in_index_order(corpus):
    built = build_all(corpus)
    for shard in built.shards:
        for entries in shard.parts.values():
            keys = [(e["manufacturer"].casefold(), e["model"].casefold()) for e in entries]
            assert keys == sorted(keys)


def test_the_shard_files_are_byte_reproducible(corpus):
    assert shard_files(build_all(corpus).shards) == shard_files(build_all(corpus).shards)
    assert inputs_record(corpus) == inputs_record(corpus)


# --- the generator ----------------------------------------------------------

def test_the_index_stage_owns_the_shard_directory():
    stage = next(g for g in generators.PIPELINE if g.name == "index")
    assert stage.paths == ("build/index.json", "build/index")
    assert "build/index" in generators.owned_paths()


def test_run_index_writes_the_shards_and_the_stamp_beside_index_json(corpus, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    generate(corpus, out)
    build = out / "build"
    assert sorted(p.relative_to(build).as_posix() for p in build.rglob("*") if p.is_file()) == [
        "index.json", "index/inputs.json", "index/irblaster/0.json",
        "index/irblaster/a.json", "index/irblaster/manifest.json",
        "index/irblaster/z.json",
    ]
    assert json.loads((build / "index/inputs.json").read_text()) == inputs_record(corpus)
    assert (build / "index.json").read_text() == dumps(build_index(corpus)[0])


def test_run_index_removes_a_part_that_no_longer_exists(corpus):
    """The directory is the generator's alone: a brand whose last remote is
    gone must not leave its part behind to be reported as an orphan."""
    generate(corpus)
    (corpus / "remotes/irblaster/AKAI/2-NEC1.json").unlink()
    generate(corpus)
    assert not (corpus / "build/index/irblaster/a.json").exists()
    manifest = json.loads((corpus / "build/index/irblaster/manifest.json").read_text())
    assert [p["key"] for p in manifest["parts"]] == ["0", "z"]


def test_no_shard_directory_content_without_a_shard(tmp_path):
    write_remote(tmp_path, "remotes/topping/RC-15A.json", "Topping", "RC-15A")
    generate(tmp_path)
    build = tmp_path / "build"
    assert sorted(p.relative_to(build).as_posix() for p in build.rglob("*") if p.is_file()) == [
        "index.json", "index/inputs.json"]
    assert "shards" not in json.loads((build / "index.json").read_text())


# --- rl build --check sees the shards ----------------------------------------

def test_the_tree_check_sees_drift_orphans_and_missing_parts(corpus, tmp_path_factory):
    """D19 over the shard directory: nothing special is needed, which is the
    point of giving the directory to a generator."""
    committed = tmp_path_factory.mktemp("committed")
    generate(corpus, committed)
    fresh = tmp_path_factory.mktemp("fresh")
    generate(corpus, fresh)
    assert generators.diff_tree(committed, fresh, INDEX_STAGE) == []

    shard = committed / "build/index/irblaster"
    (shard / "a.json").write_text("[]\n")                 # drift
    (shard / "q.json").write_text("{}\n")                 # orphan
    (shard / "z.json").unlink()                           # missing
    (committed / "build/index/inputs.json").write_text("{}\n")
    problems = generators.diff_tree(committed, fresh, INDEX_STAGE)
    assert sorted(problems) == sorted([
        "build/index/irblaster/a.json: drifted from freshly generated output",
        "build/index/irblaster/q.json: orphaned -- no generator produces it",
        "build/index/irblaster/z.json: missing from the committed tree",
        "build/index/inputs.json: drifted from freshly generated output",
    ])


def test_rl_index_check_compares_only_what_the_index_stage_owns(corpus, monkeypatch, capsys):
    """`rl index --check` regenerates the index alone, so every other owned
    path used to read as an orphan and the command could never pass."""
    generate(corpus)
    (corpus / "build").joinpath("warnings.json").write_text("{}\n")
    monkeypatch.setattr(cli, "_repo_root", lambda: corpus)
    assert cli.main(["index", "--check"]) == 0
    (corpus / "build/index/irblaster/a.json").write_text("{}\n")
    assert cli.main(["index", "--check"]) == 1
    assert "build/index/irblaster/a.json: drifted" in capsys.readouterr().err


# --- freshness ----------------------------------------------------------------

def test_a_fresh_committed_index_loads_merged(corpus):
    generate(corpus)
    index, why = load_committed(corpus)
    assert why is None
    built = build_all(corpus)
    assert index == merge(built.index, shard_entries(built.shards))
    assert len(index["remotes"]) == 5 and "shards" not in index


@pytest.mark.parametrize("break_it,why", [
    (lambda r: (r / "remotes/lirc/sony/RM-1.json").write_text(
        (r / "remotes/lirc/sony/RM-1.json").read_text().replace("SONY KDL 40", "SONY KDL 41")),
     "differ from what build/ was generated from"),
    (lambda r: write_remote(r, "remotes/irblaster/AKAI/9-NEC1.json", "AKAI", "IR Blaster DB 9 (NEC1)"),
     "differ from what build/ was generated from"),
    (lambda r: (r / "remotes/irblaster/AKAI/2-NEC1.json").unlink(),
     "differ from what build/ was generated from"),
    (lambda r: (r / "unresolved.json").write_text("[]"),
     "differ from what build/ was generated from"),
    (lambda r: (r / "build/index/inputs.json").unlink(), "inputs.json is missing"),
    (lambda r: (r / "build/index/irblaster/a.json").unlink(), "unreadable"),
    (lambda r: (r / "build/index/irblaster/a.json").write_text('{"remotes": []}'),
     "holds 0 remotes, its manifest says 1"),
    (lambda r: (r / "build/index.json").write_text("{"), "unreadable"),
])
def test_a_stale_or_broken_committed_index_says_why_and_is_not_used(corpus, break_it, why):
    """The digest is of content, so an edit anywhere in the remotes -- one
    character of one `controls` entry -- is seen, and nothing is trusted
    that does not describe the files on disk."""
    generate(corpus)
    break_it(corpus)
    index, reason = load_committed(corpus)
    assert index is None and why in reason


def test_the_digest_is_of_content_not_of_mtime(corpus):
    generate(corpus)
    path = corpus / "remotes/lirc/sony/RM-1.json"
    text = path.read_text()
    path.write_text(text)           # a new mtime, the same bytes
    shutil.copystat(corpus / "remotes/topping/RC-15A.json", path)
    assert load_committed(corpus)[1] is None
