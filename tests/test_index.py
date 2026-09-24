"""The generated index (R14, R15, D13, OD4)."""

import json
from pathlib import Path

import pytest

from remote_ledger.index import (
    alias_conflicts, build_index, load_unresolved, rolled_up_confidence, summarise,
)
from remote_ledger.remote import load_remote

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def corpus(tmp_path):
    """A throwaway repo root with a remotes/ tree."""
    def _build(files, unresolved=None):
        for name, doc in files.items():
            path = tmp_path / "remotes" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, indent=2))
        if unresolved is not None:
            (tmp_path / "unresolved.json").write_text(json.dumps(unresolved))
        return tmp_path
    return _build


def _remote(model="RC-15A", manufacturer="Topping", **extra):
    doc = json.loads((ROOT / "remotes" / "topping" / "RC-15A.json").read_text())
    doc["manufacturer"] = manufacturer
    doc["model"] = model
    doc.update(extra)
    return doc


def test_index_is_computed_from_the_files(corpus):
    root = corpus({"t/a.json": _remote()})
    index, problems = build_index(root)
    assert problems == []
    assert [r["model"] for r in index["remotes"]] == ["RC-15A"]
    assert index["remotes"][0]["protocol"] == "NEC1"


def test_remotes_are_ordered_deterministically(corpus):
    root = corpus({
        "z/z.json": _remote(manufacturer="Zenith", model="Z1"),
        "a/a.json": _remote(manufacturer="Aiwa", model="A1"),
        "m/m.json": _remote(manufacturer="aiwa", model="A2"),
    })
    index, _ = build_index(root)
    assert [r["model"] for r in index["remotes"]] == ["A1", "A2", "Z1"]


def test_rolled_up_confidence_is_the_weakest_not_the_best(corpus):
    """A file is only as trustworthy as the button you happen to press --
    rolling up the best tier would let one verified Power key vouch for
    forty untested ones."""
    doc = _remote()
    weak = json.loads(json.dumps(doc["keys"]["KEY_POWER"]))
    weak["forms"][0]["id"] = "primary.irp"
    weak["forms"][0]["confidence"] = "untested"
    weak["forms"][0]["function"] = "0x20"
    doc["keys"]["KEY_HOME"] = weak
    root = corpus({"t/a.json": doc})
    index, _ = build_index(root)
    assert index["remotes"][0]["confidence"] == "untested"


def test_rolled_up_confidence_of_a_single_verified_key(corpus):
    root = corpus({"t/a.json": _remote()})
    assert build_index(root)[0]["remotes"][0]["confidence"] == "verified"


def test_untested_alternates_are_counted(corpus):
    """D32's risk note: make open questions countable rather than letting
    untested alternates accumulate silently."""
    doc = _remote()
    doc["variants"] = {"mode2": {"confidence": "untested", "source": "manual",
                                 "override": {"subdevice": "0xEA"}}}
    root = corpus({"t/a.json": doc})
    summary = build_index(root)[0]["remotes"][0]
    # One open alternate per key the variant expanded into, not one per file.
    assert summary["unresolvedAlternates"] == len(doc["keys"]) > 1
    assert summary["keys"]["KEY_POWER"]["candidates"]["mode2"]["label"] == "mode2"


def test_alias_conflicts_are_surfaced(corpus):
    """R15: two files claiming the same alias is surfaced, not allowed."""
    root = corpus({
        "a/a.json": _remote(model="RM-1", aliases=["RM-9"]),
        "b/b.json": _remote(model="RM-2", aliases=["RM-9"]),
    })
    _, problems = build_index(root)
    assert any("rm-9" in p.lower() for p in problems)


def test_a_model_colliding_with_another_files_alias_is_a_conflict(corpus):
    root = corpus({
        "a/a.json": _remote(model="RM-1", aliases=[]),
        "b/b.json": _remote(model="RM-2", aliases=["RM-1"]),
    })
    _, problems = build_index(root)
    assert problems


def test_no_conflict_for_distinct_names(corpus):
    root = corpus({
        "a/a.json": _remote(model="RM-1", aliases=["RM-1a"]),
        "b/b.json": _remote(model="RM-2", aliases=["RM-2a"]),
    })
    assert build_index(root)[1] == []


def test_unresolved_is_folded_in_and_sorted(corpus):
    root = corpus(
        {"t/a.json": _remote()},
        unresolved=[
            {"device": "Zeta", "checked": "2026-01-01", "searched": ["x"]},
            {"device": "Alpha", "checked": "2026-01-01", "searched": ["x"]},
        ],
    )
    index, _ = build_index(root)
    assert [e["device"] for e in index["unresolved"]] == ["Alpha", "Zeta"]


def test_a_missing_unresolved_file_is_fine(tmp_path):
    assert load_unresolved(tmp_path) == []


def test_the_committed_index_matches_the_corpus():
    """OD4 commits it; R14 says nothing hand-maintained can go stale. Both
    hold only because this is checked."""
    committed = json.loads((ROOT / "build" / "index.json").read_text())
    fresh, problems = build_index(ROOT)
    assert problems == []
    assert committed == fresh


def test_the_real_corpus_has_no_alias_conflicts():
    assert build_index(ROOT)[1] == []
