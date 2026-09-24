"""The generated site (R17, D15, D20, D29, D30)."""

import json
import re
from pathlib import Path

import pytest

from remote_ledger import paths
from remote_ledger.site import build_site, payload, remote_script, render_html
from remote_ledger.validate import corpus_files

ROOT = Path(__file__).resolve().parents[1]
ISLAND = re.compile(
    r'<script type="application/json" id="ledger">(.*?)</script>', re.S
)


@pytest.fixture(scope="module")
def html():
    return (ROOT / "site" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def island(html):
    return json.loads(ISLAND.search(html).group(1).replace("<\\/", "</"))


def _script_payload(text):
    """``ledgerRemote(...[file, data]);`` back to (file, data)."""
    assert text.startswith("ledgerRemote(...") and text.endswith(");\n")
    return json.loads(text[len("ledgerRemote(..."):-len(");\n")])


@pytest.fixture(scope="module")
def scripts():
    return {
        p.relative_to(ROOT / "site").as_posix(): _script_payload(p.read_text())
        for p in sorted((ROOT / "site" / "r").rglob("*.js"))
    }


def test_the_site_is_one_page_an_index_and_a_script_per_remote(scripts):
    """D15 and D40: no framework, no build step, no server. Since D40 each
    remote's detail is its own small script, so no file grows with the
    corpus."""
    assert sorted(p.name for p in (ROOT / "site").iterdir()) == \
        ["index.html", "index.json", "r"]
    expected = {paths.site_script(paths.rel(ROOT, p)) for p in corpus_files(ROOT)}
    assert set(scripts) == expected


def test_each_script_is_for_the_remote_its_path_names(scripts):
    for script, (file, _) in scripts.items():
        assert paths.site_script(file) == script


def test_the_client_derives_script_paths_as_the_generator_does(html):
    """The page computes r/<path>.js itself; the rule must be the one
    paths.site_script applies, or opening a remote fetches nothing."""
    assert "'r/' + file.slice('remotes/'.length, -'.json'.length) + '.js'" in html
    assert paths.site_script("remotes/a/b c.json") == "r/a/b c.js"


def test_a_remote_script_is_json_arguments_only():
    """D29: data enters as data. U+2028 would end a string literal in older
    engines, so the payload is ASCII-escaped."""
    text = remote_script("remotes/t/a.json", {"note": "a\u2028b </script>"})
    assert "\u2028" not in text and "\\u2028" in text
    assert _script_payload(text) == ["remotes/t/a.json", {"note": "a\u2028b </script>"}]


def test_site_index_json_is_byte_identical_to_the_build_one():
    """D20. The site needs its own copy because Pages serves only site/."""
    assert (ROOT / "site" / "index.json").read_bytes() == \
        (ROOT / "build" / "index.json").read_bytes()


def test_no_external_resources_are_loaded(html):
    """No framework means none: no CDN, no font host, no fetch."""
    assert "<script src=" not in html
    assert "http://" not in html.split('id="ledger"')[0]
    assert "cdn" not in html.lower()


def test_the_island_parses_and_carries_the_ledger(island):
    assert sorted(island) == ["imports", "remotes", "unresolved"]
    assert island["remotes"] and island["unresolved"]
    assert island["imports"] == paths.IMPORTS


def test_the_island_cannot_close_the_script_tag(html):
    raw = ISLAND.search(html).group(1)
    assert "</script>" not in raw


def test_the_page_is_deterministic():
    """D20: no timestamp, no version, no absolute path. The page embeds only
    the index (D40), so building the index is enough -- the per-remote
    scripts are covered by the reproducibility test in test_seed_data."""
    from remote_ledger.index import build_index

    first = render_html(build_index(ROOT)[0])
    second = render_html(build_index(ROOT)[0])
    assert first == second
    assert str(ROOT) not in first and str(Path.home()) not in first
    for forbidden in ("generated at", "timestamp"):
        assert forbidden not in first.lower()


def test_every_confidence_tier_has_a_badge_style(html):
    for tier in ("confirmed", "verified", "plausible", "untested", "derived"):
        assert f".{tier} {{" in html or f".{tier}," in html


def test_r20_three_states_are_all_representable(html, island):
    """In the ledger, checked-and-not-found, and nobody-has-looked."""
    assert island["remotes"], "state one"
    assert island["unresolved"], "state two"
    assert "nobody has looked up yet" in html, "state three"


def test_the_unresolved_state_is_visually_distinct(html):
    assert "checked, nothing found" in html
    assert ".unresolved" in html


def test_citations_go_through_the_scheme_allowlist(html):
    """The page renders author-supplied `source` text as links, so its own
    client-side cite() must apply D29's allowlist too."""
    assert "const SAFE = /^(https?|mailto):/i" in html
    assert "startsWith('//')" in html


def test_layout_grids_are_emitted_as_real_css(tmp_path):
    """R9's whole design: the areas array drops into a real rule untouched."""
    doc = json.loads((ROOT / "remotes" / "topping" / "RC-15A.json").read_text())
    template = doc["keys"]["KEY_POWER"]["forms"][0]
    for i, key in enumerate(["KEY_UP", "KEY_DOWN"]):
        doc["keys"][key] = {"forms": [
            dict(template, id=f"{key}.irp", function=f"0x{0x30 + i:02X}")
        ]}
    doc["layouts"] = {"original": {
        "original": True, "areas": ["KEY_UP KEY_POWER", "KEY_DOWN KEY_POWER"],
        "printedLabels": {"KEY_POWER": "PWR"},
    }}
    (tmp_path / "remotes" / "t").mkdir(parents=True)
    (tmp_path / "remotes" / "t" / "a.json").write_text(json.dumps(doc))
    build_site(tmp_path, tmp_path)
    _, data = _script_payload((tmp_path / "site" / "r" / "t" / "a.js").read_text())
    out = data["css"]
    assert 'grid-template-areas: "KEY_UP KEY_POWER" "KEY_DOWN KEY_POWER";' in out
    assert "grid-area: KEY_POWER;" in out
    # A repeated name spans without a rowSpan field (R9).
    assert out.count("grid-area: KEY_POWER;") == 1


def test_a_derived_form_carries_its_parents_citation(tmp_path):
    """D30: a derived form has no `source`; its citation is its parent,
    reached in two hops. Otherwise it would be the one displayed form with
    nothing to show."""
    doc = json.loads((ROOT / "remotes" / "topping" / "RC-15A.json").read_text())
    from remote_ledger.pronto import encode
    from remote_ledger.remote import load_remote
    remote = load_remote(ROOT / "remotes" / "topping" / "RC-15A.json")
    good = encode(remote.render(remote.keys["KEY_POWER"][0]))
    doc["keys"]["KEY_POWER"]["forms"].append({
        "id": "primary.pronto", "type": "pronto", "hex": good,
        "confidence": "derived", "derivedFrom": "primary.irp",
    })
    (tmp_path / "remotes" / "t").mkdir(parents=True)
    (tmp_path / "remotes" / "t" / "a.json").write_text(json.dumps(doc))
    _, extra = payload(tmp_path)
    entry = list(extra.values())[0]["keys"]["KEY_POWER"]["primary"]
    assert entry["derivedFrom"]["form"] == "primary.irp"
    assert "audiosciencereview" in entry["derivedFrom"]["source"]


def test_pronto_codes_reach_the_page(scripts):
    assert scripts
    for _, data in scripts.values():
        for candidates in data["keys"].values():
            for entry in candidates.values():
                assert entry["prontoHex"].startswith("0000 ")
