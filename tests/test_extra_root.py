"""Extra roots (DESIGN D128 to D130): data that lives in another directory laid out like this repository,
read by the commands that write nothing into ``build/`` or ``site/``."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bundle_corpus import FRAME, FRAME2, make_corpus, raw_key
from remote_ledger import cli, parallel, paths
from remote_ledger.bundle import build as bb
from remote_ledger.bundle import corpus
from remote_ledger.errors import ValidationError
from remote_ledger.validate import corpus_files

UPSTREAM = "https://example.org/codes/"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No extra root and no worker count outlives a test: both are process-wide."""
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    paths.clear_extra_roots()
    yield
    paths.clear_extra_roots()
    parallel.configure(None)


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    return make_corpus(tmp_path_factory.mktemp("extra") / "ledger")


def registry(**over) -> dict:
    entry = {"name": "Demo codes", "licence": "none: unclear", "kind": "none", "upstream_url": UPSTREAM,
             "note": "Demo data of an unclear licence, kept apart."}
    entry.update(over)
    return {"format": 1, "sources": {"demo": entry}}


def remote(model: str = "Q-1", maker: str = "QQ") -> dict:
    return {"manufacturer": maker, "model": model, "aliases": [], "controls": [f"{maker} {model} unit"],
            "protocol": {"carrierHz": 38000, "minSends": 1}, "keys": {"KEY_POWER": raw_key([], FRAME), "KEY_MUTE": raw_key([], FRAME2)}}


def make_extra(directory: Path, doc: dict | None = None, models=("Q-1",)) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / paths.IMPORTS_FILE).write_text(json.dumps(doc if doc is not None else registry()), encoding="utf-8")
    source = directory / "remotes" / "demo"
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.md").write_text(f"Demo data, from <{UPSTREAM}>.\n", encoding="utf-8")
    for model in models:
        (source / "qq").mkdir(exist_ok=True)
        (source / "qq" / f"{model}.json").write_text(json.dumps(remote(model)), encoding="utf-8")
    return directory


# --- the registry ------------------------------------------------------------------------------------------


def test_an_extra_root_adds_its_remotes_after_the_repositorys_own_and_names_their_source(tmp_path, ledger):
    own = corpus_files(ledger)
    extra = make_extra(tmp_path / "extra")
    paths.configure_extra_roots([extra], primary=ledger)
    both = corpus_files(ledger)
    assert both[: len(own)] == own and both[len(own):] == [extra.resolve() / "remotes" / "demo" / "qq" / "Q-1.json"]
    where = paths.rel(ledger, both[-1])
    assert where == "remotes/demo/qq/Q-1.json" and paths.imported_from(where) == "remotes/demo/"
    assert paths.imports()["remotes/demo/"] == {"name": "Demo codes", "licence": "none: unclear", "readme": "remotes/demo/README.md"}
    assert paths.extra_source_names() == ("demo",) and set(paths.IMPORTS) < set(paths.imports())
    paths.clear_extra_roots()
    assert corpus_files(ledger) == own and paths.imported_from(where) is None and "remotes/demo/" not in paths.imports()


@pytest.mark.parametrize("mutate, message", [
    (lambda d: (d / paths.IMPORTS_FILE).unlink(), "names its sources in imports.json"),
    (lambda d: (d / paths.IMPORTS_FILE).write_text("{not json"), "names its sources in imports.json"),
    (lambda d: (d / paths.IMPORTS_FILE).write_text(json.dumps({"format": 2, "sources": {}})), 'expected {"format": 1'),
    (lambda d: (d / paths.IMPORTS_FILE).write_text(json.dumps(registry() | {"sources": {"authored": registry()["sources"]["demo"]}})), "not 'authored'"),
    (lambda d: (d / paths.IMPORTS_FILE).write_text(json.dumps(registry() | {"sources": {"Demo_1": registry()["sources"]["demo"]}})), "lower-case directory name"),
    (lambda d: (d / paths.IMPORTS_FILE).write_text(json.dumps(registry() | {"sources": {"lirc": registry()["sources"]["demo"]}})), "already a source of the repository"),
    (lambda d: (d / paths.IMPORTS_FILE).write_text(json.dumps(registry(name=""))), "name, licence, upstream_url, note are needed"),
    (lambda d: (d / paths.IMPORTS_FILE).write_text(json.dumps(registry(kind="stated"))), "licence_file, readme_sentences are needed"),
    (lambda d: (d / paths.IMPORTS_FILE).write_text(json.dumps(registry(kind="free"))), "kind is one of"),
    (lambda d: (d / "remotes" / "stray").mkdir() or (d / "remotes" / "stray" / "x.json").write_text("{}"), "no source in"),
])
def test_an_extra_root_that_does_not_say_where_its_data_is_from_is_refused(tmp_path, ledger, mutate, message):
    extra = make_extra(tmp_path / "extra")
    mutate(extra)
    with pytest.raises(ValidationError, match=message):
        paths.configure_extra_roots([extra], primary=ledger)
    assert paths.extra_roots() == ()


def test_roots_may_not_nest_repeat_or_share_a_source_or_a_path(tmp_path, ledger):
    first = make_extra(tmp_path / "one")
    with pytest.raises(ValidationError, match="given twice"):
        paths.configure_extra_roots([first, first], primary=ledger)
    with pytest.raises(ValidationError, match="already a source"):
        paths.configure_extra_roots([first, make_extra(tmp_path / "two")], primary=ledger)
    inside = make_extra(ledger / "inside")
    try:
        with pytest.raises(ValidationError, match="cannot contain the repository or be inside it"):
            paths.configure_extra_roots([inside], primary=ledger)
    finally:
        shutil.rmtree(inside)
    # a path the repository itself already holds is one remote to every reader that keys on the path
    clash = ledger / "remotes" / "demo" / "qq"
    clash.mkdir(parents=True)
    (clash / "Q-1.json").write_text(json.dumps(remote()), encoding="utf-8")
    try:
        paths.configure_extra_roots([first], primary=ledger)
        with pytest.raises(ValidationError, match="one path, two files"):
            corpus_files(ledger)
    finally:
        shutil.rmtree(ledger / "remotes" / "demo")


# --- the commands ------------------------------------------------------------------------------------------


def test_validate_reads_the_extra_root_and_leaves_no_state_behind(tmp_path, ledger, monkeypatch, capsys):
    monkeypatch.chdir(ledger)
    assert cli.main(["validate"]) == cli.EXIT_OK
    own = int(capsys.readouterr().out.split()[0])
    extra = make_extra(tmp_path / "extra", models=("Q-1", "Q-2"))
    assert cli.main(["validate", "--extra-root", str(extra)]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith(f"{own + 2} file(s) checked, 0 error(s)")
    assert paths.extra_roots() == ()                     # the state belongs to the call
    (extra / "remotes" / "demo" / "qq" / "Q-2.json").write_text(json.dumps({**remote("Q-2"), "protocol": {"carrierHz": 5}}), encoding="utf-8")
    assert cli.main(["validate", "--extra-root", str(extra)]) == cli.EXIT_ERROR
    assert "remotes/demo/qq/Q-2.json" in capsys.readouterr().err


def test_a_name_the_repository_has_is_refused_in_an_extra_root_too(tmp_path, ledger, monkeypatch, capsys):
    """R15 across the roots: the corpus is one corpus, so two files may not share a manufacturer and model."""
    monkeypatch.chdir(ledger)
    extra = make_extra(tmp_path / "extra", models=("Q-1",))
    doc = remote("TV-1", "acme")                              # tests/bundle_corpus.py's LIRC remote is acme TV-1
    (extra / "remotes" / "demo" / "qq" / "Q-1.json").write_text(json.dumps(doc), encoding="utf-8")
    assert cli.main(["validate", "--extra-root", str(extra)]) == cli.EXIT_ERROR
    assert "TV-1" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["build", "index", "site", "app", "compile", "lookup", "fmt", "import"])
def test_the_commands_that_write_the_committed_trees_do_not_take_an_extra_root(command, tmp_path):
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args([command, "--extra-root", str(tmp_path)])
    assert raised.value.code == 2


@pytest.mark.parametrize("command", [["validate"], ["check"], ["bundle"], ["keys", "report"]])
def test_the_commands_that_read_the_corpus_and_write_nothing_into_it_do_take_one(command, tmp_path):
    assert cli.build_parser().parse_args([*command, "--extra-root", str(tmp_path)]).extra_root == [str(tmp_path)]


def test_the_keys_report_has_a_row_for_the_extra_source_before_authored(tmp_path, ledger, monkeypatch, capsys):
    monkeypatch.chdir(ledger)
    extra = make_extra(tmp_path / "extra")
    assert cli.main(["keys", "report", "--extra-root", str(extra)]) == cli.EXIT_OK
    table = capsys.readouterr().out.split("Remotes with at least")[0]
    rows = [line.split()[0] for line in table.splitlines() if line and line.split()[0] in {*corpus.SOURCES, "demo", "all", "official"}]
    assert rows[-3:] == ["demo", "authored", "all"] and rows.index("demo") > rows.index("smartir")
    assert cli.main(["keys", "report", "--json", "--extra-root", str(extra)]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["sources"]["demo"]["keys"] == 2


# --- the bundle ---------------------------------------------------------------------------------------------


def test_the_bundle_gives_an_extra_source_the_next_id_and_a_notice_of_its_own(tmp_path, ledger):
    plain = bb.build_bundle(ledger, "full")
    assert plain.problems == [] and len(corpus.sources()) == len(corpus.SOURCES)
    extra = make_extra(tmp_path / "extra")
    paths.configure_extra_roots([extra], primary=ledger)
    built = bb.build_bundle(ledger, "full")
    assert built.problems == []
    notices = json.loads(built.files[bb.NOTICES_FILE])["sources"]
    assert [s["key"] for s in notices] == [*corpus.SOURCES, "demo"] and notices[-1]["id"] == len(corpus.SOURCES) + 1
    demo = notices[-1]
    assert (demo["name"], demo["spdx"], demo["licenceKind"], demo["upstreamUrl"]) == ("Demo codes", None, "none", UPSTREAM)
    assert demo["licenceNotes"] == ["Demo data of an unclear licence, kept apart."]
    assert demo["contributed"]["ledger"] == {"remotes": 1, "keys": 2}
    assert built.stats["remoteRefs"] == plain.stats["remoteRefs"] + 1 and built.stats["keys"] == plain.stats["keys"] + 2
    # the repository's own sources keep the ids they had without the extra root
    assert [s["id"] for s in notices[:-1]] == [s["id"] for s in json.loads(plain.files[bb.NOTICES_FILE])["sources"]]


def test_a_readme_that_does_not_name_the_notices_link_stops_the_bundle(tmp_path, ledger):
    extra = make_extra(tmp_path / "extra")
    (extra / "remotes" / "demo" / "README.md").write_text("Demo data, from nowhere in particular.\n", encoding="utf-8")
    paths.configure_extra_roots([extra], primary=ledger)
    problems = bb.build_bundle(ledger, "full").problems
    assert any("remotes/demo/README.md does not name https://example.org/codes/" in p for p in problems), problems


def test_an_extra_source_with_a_licence_of_its_own_brings_the_text_from_its_own_root(tmp_path, ledger):
    extra = make_extra(tmp_path / "extra", registry(kind="stated", licence="MIT", licence_file="LICENSE",
                                                    readme_sentences=["The maker states the licence outright."]))
    (extra / "remotes" / "demo" / "LICENSE").write_text("MIT License\n\nCopyright (c) Demo\n", encoding="utf-8")
    readme = extra / "remotes" / "demo" / "README.md"
    readme.write_text(readme.read_text() + "The maker states the licence outright.\n", encoding="utf-8")
    paths.configure_extra_roots([extra], primary=ledger)
    demo = json.loads(bb.build_bundle(ledger, "full").files[bb.NOTICES_FILE])["sources"][-1]
    assert (demo["spdx"], demo["licenceKind"], demo["licenceText"]) == ("MIT", "stated", "MIT License\n\nCopyright (c) Demo\n")
    assert demo["licenceNotes"] == ["The maker states the licence outright."]
    (extra / "remotes" / "demo" / "LICENSE").unlink()
    problems = bb.build_bundle(ledger, "full").problems
    assert any("remotes/demo/LICENSE: the licence text of the notice is not in the tree" in p for p in problems), problems


def test_verify_finds_the_files_of_an_extra_root_and_the_bundle_it_checks_is_the_bundle_it_built(tmp_path, ledger):
    from remote_ledger.bundle.verify import verify_directory

    extra = make_extra(tmp_path / "extra", models=("Q-1", "Q-2", "Q-3"))
    paths.configure_extra_roots([extra], primary=ledger)
    built = bb.build_bundle(ledger, "full")
    assert built.problems == []
    directory = tmp_path / "bundle"
    bb.write_bundle(built, directory, ledger)
    # every remote is sampled, so each of the three is recompiled from the extra root's own file
    problems, facts = verify_directory(ledger, directory, sample_remotes=10_000)
    assert problems == [] and facts["sampledRemotes"] == facts["remotes"]
    assert paths.locate(ledger, "remotes/demo/qq/Q-1.json") == extra.resolve() / "remotes" / "demo" / "qq" / "Q-1.json"
    assert paths.locate(ledger, "remotes/nowhere/x.json") == ledger / "remotes" / "nowhere" / "x.json"
