"""`rl lookup` over an index with a shard (D57), and R20 across it.

R20 says a device in the ledger reads as in the ledger. With the imported
database in files of its own, "the ledger" is the index *and* every shard, and
a lookup that read only index.json would say a device nobody has looked up
about one that is in the ledger -- the one answer R20 exists to prevent.
"""

import shutil

from remote_ledger import cli, paths
from remote_ledger import index as index_mod
from remote_ledger.index import load_committed
from remote_ledger.lookup import keys_for, render, search
from shard_corpus import corpus, generate  # noqa: F401 (a fixture)


def test_a_device_only_in_the_shard_reads_as_in_the_ledger(corpus):
    """SPEC R20's first row. AK 77 is named only in a `controls` entry of a
    remote that is listed in a shard, under a different maker."""
    generate(corpus)
    index, why = load_committed(corpus)
    assert why is None
    matches = search(index, "AK 77")
    out = render(matches, "AK 77", keys_for(corpus, matches))
    assert "ZENITH IR Blaster DB 1 (NEC1)" in out
    assert "AKAI | AK 77" in out and "KEY_POWER" in out
    assert "nobody has looked" not in out and "checked, nothing found" not in out
    # The shard's remote is found by its own name and by a combined query too.
    assert search(index, "zenith z100")
    assert search(index, "akai ak80")


def test_the_other_two_rows_are_unchanged_by_the_shard(corpus):
    generate(corpus)
    index, _ = load_committed(corpus)
    middle = search(index, "Sony BDP-BX510")
    assert [m.kind for m in middle] == ["unresolved"]
    assert "[checked, nothing found]" in render(middle, "Sony BDP-BX510")
    assert "nobody has looked" in render(search(index, "Nokia 3310"), "Nokia 3310")


def test_lookup_reads_the_committed_index_and_does_not_rebuild(corpus, monkeypatch, capsys):
    generate(corpus)
    monkeypatch.setattr(cli, "_repo_root", lambda: corpus)

    def forbidden(root):
        raise AssertionError("rl lookup rebuilt an index that was fresh")

    monkeypatch.setattr(index_mod, "build_all", forbidden)
    assert cli.main(["lookup", "AK", "77"]) == 0
    captured = capsys.readouterr()
    assert "ZENITH IR Blaster DB 1 (NEC1)" in captured.out
    assert captured.err == ""


def test_lookup_rebuilds_and_says_so_when_the_files_changed(corpus, monkeypatch, capsys):
    """Edited since the last `rl build`: slower, correct, and it says why on
    stderr, where it cannot be mistaken for part of the answer."""
    generate(corpus)
    monkeypatch.setattr(cli, "_repo_root", lambda: corpus)
    assert cli.main(["lookup", "AK", "77"]) == 0
    assert "ZENITH IR Blaster DB 1 (NEC1)" in capsys.readouterr().out

    path = corpus / "remotes/irblaster/AKAI/2-NEC1.json"
    path.write_text(path.read_text().replace("AKAI | AK 80", "AKAI | AK 81"))
    assert cli.main(["lookup", "AK", "81"]) == 0
    captured = capsys.readouterr()
    assert "note: the remotes or unresolved.json differ" in captured.err
    assert "AKAI | AK 81" in captured.out


def test_lookup_works_in_a_tree_nothing_was_built_in(corpus, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_repo_root", lambda: corpus)
    assert cli.main(["lookup", "AK 77"]) == 0
    captured = capsys.readouterr()
    assert "inputs.json is missing" in captured.err
    assert "ZENITH IR Blaster DB 1 (NEC1)" in captured.out


def test_a_lookup_prints_what_it_printed_before_the_shard(corpus, monkeypatch, capsys):
    """Same index entries, same order, so the same text -- with the shard in
    its files or in the index."""
    monkeypatch.setattr(cli, "_repo_root", lambda: corpus)
    generate(corpus)
    assert cli.main(["lookup", "AKAI"]) == 0
    sharded = capsys.readouterr().out
    monkeypatch.setattr(paths, "SHARDED", {})
    shutil.rmtree(corpus / "build")
    assert cli.main(["lookup", "AKAI"]) == 0
    assert capsys.readouterr().out == sharded
