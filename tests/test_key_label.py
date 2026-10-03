"""A key's optional display ``label``: the text the source shows for it.

It is display only, never an identifier (the key's name is). It travels with the
key everywhere the key does: the schema, the loader, ``rl fmt``, the compiled
artifact, the site page and ``rl lookup``. A remote without labels must generate
exactly the files it did before the field existed, which is what most of these
tests pin.
"""

import json
from pathlib import Path

import pytest

from page_driver import HAVE_NODE, run_page
from remote_ledger import paths
from remote_ledger.cli import compiled_artifact
from remote_ledger.fmt import format_document
from remote_ledger.generators import run_compile
from remote_ledger.index import build_index
from remote_ledger.lookup import keys_for, render, search
from remote_ledger.remote import load_remote
from remote_ledger.serialize import SOURCE_KEY_ORDER, loads, order_keys
from remote_ledger.site import (
    LABEL_STYLE, SCRIPT, STYLE, build_site, page_parts, payload, render_html,
)
from remote_ledger.validate import schema_problems, validate_file

ROOT = Path(__file__).resolve().parents[1]
TOPPING = ROOT / "remotes" / "topping" / "RC-15A.json"


def _doc(labels=None):
    doc = json.loads(TOPPING.read_text())
    for name, label in (labels or {}).items():
        doc["keys"][name] = {"label": label, "forms": doc["keys"][name]["forms"]}
    return doc


def _write(root: Path, doc, rel="t/a.json") -> Path:
    path = root / "remotes" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


LABELS = {"KEY_POWER": "POWER", "KEY_VOLUMEUP": "Vol +"}


def errors(doc):
    return [str(p) for p in schema_problems(doc, "remote.schema.json", "t")]


# --- the schema ----------------------------------------------------------------


def test_a_key_may_carry_a_label():
    assert errors(_doc(LABELS)) == []
    assert errors(_doc()) == []                      # and need not


@pytest.mark.parametrize("label", ["", None, 5, ["POWER"], {"text": "POWER"}])
def test_a_label_must_be_a_non_empty_string(label):
    doc = _doc()
    doc["keys"]["KEY_POWER"]["label"] = label
    assert errors(doc)


def test_a_label_is_free_text_and_never_a_name():
    """Anything the source shows: symbols, spaces, a tab, lower case, '??'."""
    for text in ("??", "VOL+", " I 6", "1 \t\t\t", "STEREO / LANG. ", "⏩", "[ | ]", "ok"):
        assert errors(_doc({"KEY_POWER": text})) == [], text


def test_other_properties_of_a_key_are_still_refused():
    doc = _doc()
    doc["keys"]["KEY_POWER"]["lable"] = "POWER"
    assert errors(doc)


# --- the loader ----------------------------------------------------------------


def test_the_loader_collects_labels_by_key_name(tmp_path):
    remote = load_remote(_write(tmp_path, _doc(LABELS)))
    assert remote.labels == LABELS
    assert set(remote.labels) <= set(remote.keys)


def test_a_remote_without_labels_has_none(tmp_path):
    assert load_remote(_write(tmp_path, _doc())).labels == {}
    assert load_remote(TOPPING).labels == {}


def test_a_label_changes_no_form_and_no_signal(tmp_path):
    plain = load_remote(_write(tmp_path, _doc(), "t/plain.json"))
    labelled = load_remote(_write(tmp_path, _doc(LABELS), "t/labelled.json"))
    for key in plain.keys:
        assert plain.compile_group(key, "primary") == labelled.compile_group(key, "primary")


# --- rl fmt --------------------------------------------------------------------


def test_the_declared_order_of_a_key_is_label_then_forms():
    assert SOURCE_KEY_ORDER["key"] == ("label", "forms")
    assert list(order_keys("key", {"forms": [], "label": "x"})) == ["label", "forms"]


def test_fmt_writes_label_before_forms_and_is_idempotent(tmp_path):
    doc = _doc()
    # forms first, as a person might type it; rl fmt puts the label first
    doc["keys"]["KEY_POWER"] = {"forms": doc["keys"]["KEY_POWER"]["forms"], "label": "POWER"}
    path = _write(tmp_path, doc)
    once = format_document(path)
    assert list(loads(once)["keys"]["KEY_POWER"]) == ["label", "forms"]
    assert loads(once)["keys"]["KEY_POWER"]["label"] == "POWER"
    path.write_text(once, encoding="utf-8")
    assert format_document(path) == once


def test_fmt_leaves_a_file_without_labels_exactly_as_it_was():
    """The committed corpus is canonical; formatting must not move a byte."""
    authored = sorted((ROOT / "remotes").glob("*/*.json"))     # not the imports' subdirectories
    assert authored
    for path in authored:
        assert path.read_text(encoding="utf-8") == format_document(path), path


# --- the compiled artifact -----------------------------------------------------


def test_the_artifact_has_a_label_only_on_a_key_that_has_one(tmp_path):
    artifact = compiled_artifact(load_remote(_write(tmp_path, _doc(LABELS))))
    for key, entry in artifact["keys"].items():
        assert set(entry) == ({"candidates", "label"} if key in LABELS else {"candidates"})
        if key in LABELS:
            assert entry["label"] == LABELS[key]


def test_an_artifact_without_labels_is_what_it_was(tmp_path):
    """The generated tree stays byte for byte: the label is added to nothing
    that has none. Taking the labels away from a labelled artifact gives the
    unlabelled one."""
    plain = compiled_artifact(load_remote(_write(tmp_path, _doc(), "t/plain.json")))
    labelled = compiled_artifact(load_remote(_write(tmp_path, _doc(LABELS), "t/labelled.json")))
    for entry in labelled["keys"].values():
        entry.pop("label", None)
    assert labelled == plain
    assert all(set(entry) == {"candidates"} for entry in plain["keys"].values())


def test_the_compile_generator_writes_the_label(tmp_path):
    _write(tmp_path, _doc(LABELS))
    assert run_compile(tmp_path, tmp_path) == []
    artifact = json.loads((tmp_path / paths.artifact("remotes/t/a.json")).read_text())
    assert artifact["keys"]["KEY_VOLUMEUP"]["label"] == "Vol +"
    assert "label" not in artifact["keys"]["KEY_MUTE"]


def test_the_index_carries_no_per_key_data_so_no_label(tmp_path):
    """D40: the index is identity and roll-ups; a label lives with its key."""
    _write(tmp_path, _doc(LABELS))
    index, problems = build_index(tmp_path)
    assert problems == [] and "Vol +" not in json.dumps(index)


# --- the site ------------------------------------------------------------------


def test_the_site_data_has_labels_beside_the_keys_for_a_remote_that_has_them(tmp_path):
    other = _doc()
    other["model"] = "B"
    _write(tmp_path, _doc(LABELS))
    _write(tmp_path, other, "t/b.json")
    _, extra = payload(tmp_path)
    labelled, plain = extra["remotes/t/a.json"], extra["remotes/t/b.json"]
    assert labelled["labels"] == LABELS
    assert "labels" not in plain                      # its script is what it was
    assert labelled["keys"] == plain["keys"]          # candidates are not touched


def test_the_page_only_carries_label_code_when_the_ledger_has_labels():
    plain_style, plain_script = page_parts(False)
    style, script = page_parts(True)
    assert (plain_style, plain_script) == (STYLE, SCRIPT)
    assert "klabel" not in plain_style + plain_script and "labels" not in plain_script
    assert style == STYLE + LABEL_STYLE and "klabel" in script and "extra.labels" in script
    assert script.count("<h3>") == 1                  # the one line was replaced, not duplicated
    assert "klabel" not in render_html({"remotes": [], "unresolved": []})
    assert "klabel" in render_html({"remotes": [], "unresolved": []}, labelled=True)


def test_build_site_chooses_the_page_by_the_data(tmp_path):
    plain, labelled = tmp_path / "plain", tmp_path / "labelled"
    _write(plain, _doc())
    _write(labelled, _doc(LABELS))
    build_site(plain, plain)
    build_site(labelled, labelled)
    assert "klabel" not in (plain / "site" / "index.html").read_text()
    assert "klabel" in (labelled / "site" / "index.html").read_text()
    script = (labelled / "site" / "r" / "t" / "a.js").read_text()
    assert '"labels":{"KEY_POWER":"POWER","KEY_VOLUMEUP":"Vol +"}' in script
    assert '"labels":' not in (plain / "site" / "r" / "t" / "a.js").read_text()


@pytest.mark.skipif(not HAVE_NODE, reason="node is not installed")
def test_the_page_shows_the_label_next_to_the_key_name_and_only_when_there_is_one(tmp_path):
    """Runs the page's own JavaScript, so a typo in the labelled line fails here
    and not in a browser. A label is page text, escaped like any other (D29)."""
    root = tmp_path / "ledger"
    _write(root, _doc({**LABELS, "KEY_MUTE": '<b>"M"</b> & co'}))
    build_site(root, root)
    detail = run_page(root, tmp_path)["detail"]
    assert '<h3>KEY_POWER <span class="klabel">POWER</span></h3>' in detail
    assert '<h3>KEY_VOLUMEUP <span class="klabel">Vol +</span></h3>' in detail
    assert ('<h3>KEY_MUTE <span class="klabel">&lt;b&gt;&quot;M&quot;&lt;/b&gt; &amp; co</span></h3>'
            in detail)
    assert "<h3>KEY_GAIN</h3>" in detail                        # no label, no span
    assert "<b>" not in detail

    plain = tmp_path / "plain"
    _write(plain, _doc())
    build_site(plain, plain)
    detail = run_page(plain, tmp_path)["detail"]
    assert "klabel" not in detail and "<h3>KEY_POWER</h3>" in detail


# --- rl lookup -----------------------------------------------------------------


def test_lookup_shows_the_label_beside_the_key_name(tmp_path):
    _write(tmp_path, _doc({"KEY_POWER": "POWER", "KEY_MUTE": "ok\t"}))
    index, _ = build_index(tmp_path)
    matches = search(index, "DX3 Pro")
    out = render(matches, "DX3 Pro", keys_for(tmp_path, matches))
    lines = out.splitlines()
    assert '  KEY_POWER  "POWER"' in lines
    assert '  KEY_MUTE  "ok\\t"' in lines                 # control characters are shown, not sent
    assert "  KEY_OK" in lines                             # a key without a label is as it was


# --- the labelled remote still validates, cross-checks and builds ----------------


def test_a_labelled_file_passes_validate(tmp_path):
    path = _write(tmp_path, _doc(LABELS))
    assert [str(p) for p in validate_file(path)] == []
