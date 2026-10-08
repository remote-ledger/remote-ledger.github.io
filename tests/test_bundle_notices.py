"""The notices of the bundle (D94): where the data came from, under what licence, and what
each source contributed. Every fact is the repository's own, so these tests hold the notices
against the files they were read from, and the quoted sentences against the READMEs that say
them. Where the repository says a licence applies by inheritance only, so does the notice."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from bundle_corpus import make_corpus
from remote_ledger import parallel, paths
from remote_ledger.bundle import build as bb
from remote_ledger.bundle import corpus, notices
from remote_ledger.errors import ValidationError

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    return make_corpus(tmp_path_factory.mktemp("notices") / "ledger")


@pytest.fixture(scope="module")
def built(ledger):
    result = bb.build_bundle(ledger, "full")
    assert result.problems == []
    return result


@pytest.fixture(scope="module")
def doc(built):
    return json.loads(built.files[bb.NOTICES_FILE])


def by_key(doc):
    return {s["key"]: s for s in doc["sources"]}


def test_there_is_one_notice_per_source_in_the_order_the_ids_say(doc):
    assert [s["key"] for s in doc["sources"]] == ["authored", "lirc", "smartir", "irblaster", "hifi-remote", "jp1"]
    assert [s["id"] for s in doc["sources"]] == [1, 2, 3, 4, 5, 6]
    assert doc["schemaVersion"] == 1 and list(corpus.SOURCES) == [s["key"] for s in doc["sources"]]


def test_the_licence_ids_and_texts_are_the_repositorys(doc):
    sources = by_key(doc)
    for source, root in (("lirc", "remotes/lirc/"), ("smartir", "remotes/smartir/"),
                         ("irblaster", "remotes/irblaster/")):
        licence = ROOT / root / notices.FACTS[source]["licence_file"]
        assert sources[source]["spdx"] == paths.IMPORTS[root]["licence"]
        assert sources[source]["name"] == paths.IMPORTS[root]["name"]
        assert sources[source]["licenceText"] == licence.read_text(encoding="utf-8")
    assert [sources[s]["spdx"] for s in ("lirc", "smartir", "irblaster")] == [
        "GPL-2.0-or-later", "MIT", "GPL-3.0-only"]
    assert "Copyright (c) 2019 Vassilis Panos" in sources["smartir"]["licenceText"]
    assert sources["lirc"]["licenceText"].lstrip().startswith("GNU GENERAL PUBLIC LICENSE")
    assert "Version 2, June 1991" in sources["lirc"]["licenceText"]
    assert "Version 3, 29 June 2007" in sources["irblaster"]["licenceText"]


def test_how_firmly_each_licence_holds_is_said_in_the_repositorys_words(doc):
    sources = by_key(doc)
    assert {k: sources[k]["licenceKind"] for k in sources} == {
        "authored": "none", "lirc": "reading", "smartir": "stated", "irblaster": "inherited",
        "hifi-remote": "none", "jp1": "none"}
    # IR Blaster: by inheritance and nothing more, verbatim
    notes = " ".join(sources["irblaster"]["licenceNotes"])
    assert "by inheritance and nothing more" in notes
    assert "nobody in the chain states a licence for the data itself" in notes
    assert "it is not a reading of one, as LIRC's is" in notes
    assert "This is a reading of the licence, not a grant." in sources["lirc"]["licenceNotes"]
    assert any("states its licence outright" in n for n in sources["smartir"]["licenceNotes"])


def test_the_hifi_remote_notice_records_no_licence_and_names_where_the_pages_are(doc):
    """An import of reference codes (DESIGN D110): no SPDX id, no text, a note that says the
    repository records none, and a link the import's own README carries."""
    notice = by_key(doc)["hifi-remote"]
    assert notice["spdx"] is None and notice["licenceText"] is None
    assert notice["licenceKind"] == "none" and notice["upstreamCommit"] is None
    assert "records no licence" in " ".join(notice["licenceNotes"])
    assert notice["name"] == "hifi-remote.com Sony code pages"
    readme = notices.collapse((ROOT / "remotes" / "hifi-remote" / "README.md").read_text(encoding="utf-8"))
    assert notice["upstreamUrl"] in readme and notice["upstreamUrl"] == notices.FACTS["hifi-remote"]["upstream_url"]


def test_the_jp1_notice_records_no_licence_and_names_the_repository(doc):
    """The forum's JP1 upgrades are tables of reference codes (DESIGN D110): no SPDX id and no text, a
    note that says the repository records none, and the link the import's README carries."""
    notice = by_key(doc)["jp1"]
    assert notice["spdx"] is None and notice["licenceText"] is None and notice["licenceKind"] == "none"
    assert "records no licence" in " ".join(notice["licenceNotes"])
    readme = notices.collapse((ROOT / "remotes" / "jp1" / "README.md").read_text(encoding="utf-8"))
    assert notice["upstreamUrl"] in readme and notice["upstreamUrl"] == notices.FACTS["jp1"]["upstream_url"]


@pytest.mark.parametrize("source", ["lirc", "smartir", "irblaster"])
def test_every_quoted_sentence_and_link_is_in_the_import_directorys_readme(source):
    readme = notices.collapse((ROOT / "remotes" / source / "README.md").read_text(encoding="utf-8"))
    facts = notices.FACTS[source]
    assert facts["readme_sentences"]
    for sentence in facts["readme_sentences"]:
        assert notices.collapse(sentence) in readme, sentence
    assert facts["upstream_url"] in readme
    for step in facts.get("lineage", []):
        assert step["url"] in readme
        if "holder" in step:
            assert step["holder"] in readme


def test_the_commit_is_the_one_import_md_names(doc):
    for source, root in (("lirc", "remotes/lirc"), ("smartir", "remotes/smartir")):
        # the synthetic ledger's reports carry the real commits (tests/bundle_corpus.py)
        assert by_key(doc)[source]["upstreamCommit"] == {
            "lirc": "291b40f436a4a17cc72b24c8eb411f6213ee88e5",
            "smartir": "e4df2957ad915536f41ffb39daa96886d7cfe040"}[source]
    # the IR Blaster report is the importer's own, whose first lines name its commit
    report = (ROOT / "remotes/irblaster/IMPORT.md").read_text(encoding="utf-8")
    assert f"@ `{by_key(doc)['irblaster']['upstreamCommit']}`" in report
    real = notices._commit(ROOT / "remotes/lirc/IMPORT.md")
    assert real == "291b40f436a4a17cc72b24c8eb411f6213ee88e5"
    assert notices._commit(ROOT / "remotes/smartir/IMPORT.md") == "e4df2957ad915536f41ffb39daa96886d7cfe040"
    assert notices._commit(ROOT / "remotes/irblaster/IMPORT.md") == "6aafd15e1c95cf494ac729339b9a4701a4ab8f0a"


def test_the_upstream_links_and_the_lineage(doc):
    sources = by_key(doc)
    assert sources["lirc"]["upstreamUrl"] == "https://sourceforge.net/p/lirc-remotes/code/"
    assert sources["smartir"]["upstreamUrl"] == "https://github.com/smartHomeHub/SmartIR"
    assert sources["irblaster"]["upstreamUrl"] == "https://github.com/remote-ledger/SwiftRemote"
    assert [step["name"] for step in sources["irblaster"]["lineage"]] == [
        "SwiftRemote", "IR Blaster", "osram-remote"]
    assert "lineage" not in sources["lirc"]


def test_the_authored_remotes_have_no_licence_and_the_notice_says_there_is_none(doc):
    authored = by_key(doc)["authored"]
    assert authored["spdx"] is None and authored["licenceText"] is None
    assert authored["upstreamUrl"] is None and authored["upstreamCommit"] is None
    assert authored["licenceKind"] == "none"
    assert "records no licence" in authored["licenceNotes"][0]


def test_no_licence_file_is_at_the_root_of_the_repository():
    """If one is ever added, the authored remotes' notice stops being true: this fails
    until ``notices.FACTS['authored']`` says what the file says."""
    names = {p.name.upper() for p in ROOT.iterdir() if p.is_file()}
    assert not {n for n in names if n.startswith(("LICENSE", "LICENCE", "COPYING"))}


def test_what_each_source_contributed_is_counted_for_the_ledger_and_for_the_bundle(ledger, built, doc):
    sources = by_key(doc)
    records, _ = corpus.read_corpus(ledger)
    for source in corpus.SOURCES:
        mine = [r for r in records if r.source == source]
        assert sources[source]["contributed"]["ledger"] == {
            "remotes": len(mine), "keys": sum(len(r.keys) for r in mine)}
        assert sources[source]["contributed"]["bundle"] == sources[source]["contributed"]["ledger"]
    assert sources["irblaster"]["contributed"]["ledger"]["remotes"] == 24
    assert sum(s["contributed"]["bundle"]["keys"] for s in doc["sources"]) == built.stats["keys"]


def test_a_selected_bundle_counts_what_it_carries_next_to_what_the_ledger_has(ledger):
    chosen = bb.build_bundle(ledger, "selected")
    sources = by_key(json.loads(chosen.files[bb.NOTICES_FILE]))
    assert sources["irblaster"]["contributed"]["ledger"]["remotes"] == 24
    assert sources["irblaster"]["contributed"]["bundle"]["remotes"] == 14
    assert sources["lirc"]["contributed"]["bundle"] == {"remotes": 0, "keys": 0}
    assert sources["lirc"]["contributed"]["ledger"]["remotes"] == 2


def test_the_notices_are_in_the_database_too(built, doc):
    conn = sqlite3.connect(":memory:")
    conn.deserialize(built.files[bb.BUNDLE_FILE])
    rows = {r[0]: r for r in conn.execute(
        "SELECT id, name, spdx, licence_kind, licence_notes, licence_text, upstream_url, "
        "upstream_commit, remote_count, key_count FROM sources")}
    for source in doc["sources"]:
        row = rows[source["id"]]
        assert row[1:4] == (source["name"], source["spdx"], source["licenceKind"])
        assert row[4] == "\n".join(source["licenceNotes"]) and row[5] == source["licenceText"]
        assert row[6:8] == (source["upstreamUrl"], source["upstreamCommit"])
        assert row[8:] == (source["contributed"]["bundle"]["remotes"],
                           source["contributed"]["bundle"]["keys"])


def test_the_file_is_compact_utf8_json_with_sorted_keys_and_one_newline(built):
    raw = built.files[bb.NOTICES_FILE]
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    text = raw.decode("utf-8")
    assert json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n" == text


# -- the facts must still be in the tree -----------------------------------------------------------


def copy_of(ledger, tmp_path):
    root = tmp_path / "ledger"
    shutil.copytree(ledger, root)
    return root


def test_a_missing_licence_text_stops_the_build(ledger, tmp_path):
    root = copy_of(ledger, tmp_path)
    (root / "remotes/lirc/COPYING").unlink()
    got = bb.build_bundle(root, "full")
    assert got.files == {} and "remotes/lirc/COPYING: the licence text of the notice is not in the tree" in got.problems


def test_a_readme_that_no_longer_says_what_the_notice_quotes_stops_the_build(ledger, tmp_path):
    root = copy_of(ledger, tmp_path)
    readme = root / "remotes/irblaster/README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        "by inheritance and nothing more", "by a grant"), encoding="utf-8")
    got = bb.build_bundle(root, "full")
    assert got.files == {} and "no longer says 'Licence: GPL-3.0-only, by inheritance and nothing more.'" in got.problems[0]


def test_a_readme_that_loses_the_upstream_link_stops_the_build(ledger, tmp_path):
    root = copy_of(ledger, tmp_path)
    readme = root / "remotes/smartir/README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        "github.com/smartHomeHub/SmartIR", "example.invalid"), encoding="utf-8")
    got = bb.build_bundle(root, "full")
    assert "does not name https://github.com/smartHomeHub/SmartIR" in got.problems[0]


def test_build_sources_names_the_statement_it_could_not_find(ledger):
    records, _ = corpus.read_corpus(ledger)
    with pytest.raises(ValidationError):
        notices.build_sources(ledger / "remotes", records)
