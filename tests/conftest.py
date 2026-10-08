"""Fixtures shared by the tests that read the real ledger.

Reading the tree takes about five seconds and building the selected bundle about eight more, so
each is done once for the whole run and the tests of the bundle (``test_bundle_real.py``) and of
the search over it (``test_matching.py``, ``test_search_eval.py``) share them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def real_records():
    """Every remote of the committed tree, as ``bundle.corpus`` reads them."""
    from remote_ledger.bundle import corpus

    records, problems = corpus.read_corpus(ROOT)
    assert problems == []
    return records


@pytest.fixture(scope="session")
def real_selected(real_records, tmp_path_factory):
    """The selected bundle of the real ledger: the ``Bundle`` ``build_bundle`` made, and the
    directory it was written to (``catalog.sqlite``, ``notices.json``, ``manifest.json``)."""
    from remote_ledger.bundle import build

    built = build.build_bundle(ROOT, "selected", records=real_records)
    assert built.problems == []
    directory = tmp_path_factory.mktemp("real-selected")
    build.write_bundle(built, directory, ROOT)
    return built, directory
