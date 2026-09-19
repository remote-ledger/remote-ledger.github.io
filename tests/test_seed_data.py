"""The committed corpus, and the generated tree that must match it."""

import json
from pathlib import Path

import pytest

from remote_ledger.generators import PIPELINE, owned_paths, registered
from remote_ledger.remote import load_remote
from remote_ledger.validate import corpus_files, validate_file

ROOT = Path(__file__).resolve().parents[1]
CORPUS = corpus_files(ROOT)


def test_the_corpus_is_not_empty():
    assert CORPUS, "Phase 3 authors seed data under remotes/"


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.name)
def test_every_seed_file_validates(path):
    assert [str(p) for p in validate_file(path)] == []


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.name)
def test_every_seed_form_carries_a_citation(path):
    """R5/R18 on real data, not only in the schema."""
    remote = load_remote(path)
    for key, forms in remote.keys.items():
        for form in forms:
            if form.is_derived:
                assert form.derived_from, f"{key}:{form.id}"
            else:
                assert form.source, f"{key}:{form.id} has no citation"


def test_unresolved_records_what_was_checked_and_not_found():
    """R20: a device checked and not found must read differently from one
    nobody has looked at (D12)."""
    entries = json.loads((ROOT / "unresolved.json").read_text())
    assert entries
    devices = {e["device"] for e in entries}
    assert "Samsung UN50NU6900F" in devices
    for entry in entries:
        assert entry["searched"] and entry["note"]


def test_compile_and_check_are_registered_in_phase_three():
    names = {g.name for g in registered()}
    assert names == {"check", "compile"}
    assert set(owned_paths()) == {"build/warnings.json", "build/pronto"}


def test_index_and_site_are_still_pending():
    pending = {g.name: g.phase for g in PIPELINE if not g.registered}
    assert pending == {"index": 5, "site": 6}


def test_generated_tree_holds_no_absolute_paths():
    """D20: an absolute path is a non-reproducible value -- it differs
    between a laptop and CI, so the artifact would drift on every machine
    and `rl build --check` would fail for no reason."""
    for owned in owned_paths():
        target = ROOT / owned
        files = [target] if target.is_file() else list(target.rglob("*"))
        for file in files:
            if not file.is_file():
                continue
            text = file.read_text(encoding="utf-8")
            assert str(ROOT) not in text, f"{file}: absolute path"
            assert "/home/" not in text and "/Users/" not in text


def test_generated_tree_holds_no_timestamps():
    for owned in owned_paths():
        target = ROOT / owned
        files = [target] if target.is_file() else list(target.rglob("*"))
        for file in files:
            if file.is_file():
                text = file.read_text(encoding="utf-8")
                assert "generated" not in text.lower()
