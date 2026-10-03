"""The site with a sharded import (D69): what the page no longer embeds, and
the files it loads instead."""

import json
import re

import pytest

from remote_ledger import generators, paths, site
from remote_ledger.index import build_all, shard_files
from shard_corpus import corpus, generate, make_corpus, write_remote  # noqa: F401 (a fixture)

ISLAND = re.compile(r'<script type="application/json" id="ledger">(.*?)</script>', re.S)


def _island(html):
    return json.loads(ISLAND.search(html).group(1).replace("<\\/", "</"))


def _script_args(text):
    assert text.startswith("ledgerShard(...") and text.endswith(");\n")
    return json.loads(text[len("ledgerShard(..."):-len(");\n")])


@pytest.fixture
def built(corpus):
    """The build/ and site/ trees of the corpus, written in place."""
    generate(corpus)
    assert site.build_site(corpus, corpus) == []
    return corpus


def test_the_page_embeds_the_core_index_and_describes_the_shards(built):
    html = (built / "site/index.html").read_text()
    island = _island(html)
    assert {r["file"] for r in island["remotes"]} == {
        "remotes/topping/RC-15A.json", "remotes/lirc/sony/RM-1.json"}
    assert island["shards"] == [{
        "name": "irblaster",
        "parts": [
            {"key": "0", "remotes": 1, "script": "index/irblaster/0.js"},
            {"key": "a", "remotes": 1, "script": "index/irblaster/a.js"},
            {"key": "z", "remotes": 1, "script": "index/irblaster/z.js"},
        ],
        "remotes": 3,
        "root": "remotes/irblaster/",
    }]


def test_the_imported_remotes_are_not_in_the_page(built):
    """The whole point: the page no longer carries the imported database."""
    html = (built / "site/index.html").read_text()
    assert "IR Blaster DB" not in html
    assert "AK 77" not in html and "ZENITH" not in html


def test_the_site_holds_the_shard_for_clients_and_for_the_page(built):
    """JSON, byte for byte what build/ holds, for clients; a script per part
    for the page, which loads from file:// where fetch does not."""
    for rel in shard_files(build_all(built).shards):
        assert (built / "site" / rel).read_bytes() == (built / "build" / rel).read_bytes(), rel
    assert (built / "site/index.json").read_bytes() == (built / "build/index.json").read_bytes()
    assert sorted(p.name for p in (built / "site/index/irblaster").iterdir()) == sorted(
        [f"{k}.{ext}" for k in "0az" for ext in ("js", "json")] + ["manifest.json"])


def test_a_part_script_carries_exactly_the_parts_entries(built):
    for key in "0az":
        name, k, entries = _script_args((built / f"site/index/irblaster/{key}.js").read_text())
        assert (name, k) == ("irblaster", key)
        assert entries == json.loads((built / f"build/index/irblaster/{key}.json").read_text())["remotes"]


def test_a_part_script_is_json_arguments_only():
    """D29: data enters as data. U+2028 would end a string literal in older
    engines, so the payload is ASCII-escaped."""
    text = site.shard_script("irblaster", "x", [{"controls": ["a b </script>"]}])
    assert " " not in text and "\\u2028" in text
    assert _script_args(text) == ["irblaster", "x", [{"controls": ["a b </script>"]}]]


def test_every_script_the_page_names_exists_and_every_remote_still_opens(built):
    for part in _island((built / "site/index.html").read_text())["shards"][0]["parts"]:
        assert (built / "site" / part["script"]).is_file()
    for entry in build_all(built).shards[0].parts["a"]:
        assert (built / "site" / paths.site_script(entry["file"])).is_file()


def test_a_part_that_is_gone_leaves_the_site(built):
    (built / "remotes/irblaster/AKAI/2-NEC1.json").unlink()
    assert site.build_site(built, built) == []
    assert not (built / "site/index/irblaster/a.js").exists()
    assert not (built / "site/index/irblaster/a.json").exists()


def test_without_a_shard_the_site_is_what_it_was(tmp_path):
    write_remote(tmp_path, "remotes/topping/RC-15A.json", "Topping", "RC-15A")
    assert site.build_site(tmp_path, tmp_path) == []
    assert sorted(p.name for p in (tmp_path / "site").iterdir()) == ["index.html", "index.json", "r"]
    assert _island((tmp_path / "site/index.html").read_text())["shards"] == []
    assert "shards" not in json.loads((tmp_path / "site/index.json").read_text())


def test_the_pages_script_loads_parts_as_scripts_and_never_by_fetch(built):
    """D15/D40: no fetch, so the page works from a file."""
    html = (built / "site/index.html").read_text()
    assert "window.ledgerShard" in html and "s.src = p.script" in html
    code = [line for line in html.splitlines() if not line.lstrip().startswith("//")]
    assert not any("fetch(" in line for line in code)
    assert "<script src=" not in ISLAND.sub("", html)


def test_the_page_is_deterministic_with_shards(corpus):
    index = build_all(corpus)
    assert site.render_html(index.index, index.shards) == site.render_html(index.index, index.shards)


def test_the_tree_check_covers_the_site_shards(corpus, tmp_path_factory):
    """`rl build --check` diffs `site/` whole, so the shard copies are
    covered by the owner table that already holds `site`."""
    committed = tmp_path_factory.mktemp("committed")
    make_corpus(committed)
    generate(committed)
    assert site.build_site(committed, committed) == []
    fresh = tmp_path_factory.mktemp("fresh")
    assert site.build_site(committed, fresh) == []
    stage = tuple(g for g in generators.PIPELINE if g.name == "site")
    assert generators.diff_tree(committed, fresh, stage) == []
    (committed / "site/index/irblaster/a.js").write_text("ledgerShard();\n")
    (committed / "site/index/irblaster/q.js").write_text("\n")
    assert sorted(generators.diff_tree(committed, fresh, stage)) == [
        "site/index/irblaster/a.js: drifted from freshly generated output",
        "site/index/irblaster/q.js: orphaned -- no generator produces it",
    ]
