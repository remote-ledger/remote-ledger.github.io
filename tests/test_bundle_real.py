"""The bundle over the real ledger (D90 to D92, D95): the numbers the design quotes, and that
the selected profile is within the size the owner asked for.

One reading of the tree and one build of the selected bundle (``tests/conftest.py``) serve every
test here and the tests of the search over it, about 15 seconds in all. What ``rl bundle --verify``
checks against a written bundle takes longer and is run by hand; D95 has its numbers."""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from remote_ledger import parallel
from remote_ledger.bundle import build, catalog, merge, select
from remote_ledger.bundle.textnorm import search_norm

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


@pytest.fixture(scope="module")
def real(real_records):
    return real_records, catalog.collect(real_records)


@pytest.fixture(scope="module")
def selection(real_selected):
    return real_selected[0].selection


@pytest.fixture(scope="module")
def selected(real_selected):
    built = real_selected[0]
    return built.assembled, built.files[build.BUNDLE_FILE]


def test_the_dedupe_numbers_the_design_quotes(real):
    """411,265 keys of the IR Blaster import share 57,709 compiled signals; the whole ledger's
    525,096 keys share 155,976."""
    records, _ = real
    keys: Counter = Counter()
    distinct: dict[str, set[bytes]] = {}
    for record in records:
        for key in record.keys:
            keys[record.source] += 1
            distinct.setdefault(record.source, set()).add(key[4])
    assert (keys["irblaster"], len(distinct["irblaster"])) == (411_265, 57_709)
    assert (keys["lirc"], keys["smartir"], keys["authored"]) == (112_789, 914, 128)
    assert sum(keys.values()) == 525_096 and len(records) == 13_218
    assert len(set().union(*distinct.values())) == 155_976
    # the IR Blaster signals as blobs, with their two-byte counts: 8.8 MB of binary words
    assert sum(len(b) for b in distinct["irblaster"]) == 8_793_390


def test_no_remote_has_a_second_candidate_group(real):
    assert sum(r.other_candidates for r in real[0]) == 0


def test_every_name_of_the_curated_list_is_a_brand_of_the_catalog(real, selection):
    names = select.read_curated(select.CURATED_FILE.read_text(encoding="utf-8"))
    assert selection.unresolved == []
    assert all(search_norm(n) in real[1].brands for n in names)
    assert len(selection.curated) + len(selection.skipped) == len(names)


def test_the_selection_uses_its_budget_and_starts_with_the_head_of_the_list(selection):
    assert 0.97 * select.BUDGET_BYTES < selection.estimated_bytes <= select.BUDGET_BYTES
    assert selection.curated[:10] == ["SAMSUNG", "LG", "SONY", "PANASONIC", "PHILIPS", "SHARP",
                                      "TOSHIBA", "HISENSE", "TCL", "VIZIO"]
    assert set(selection.curated).isdisjoint(selection.skipped)
    assert selection.skipped, "the list is longer than the budget: some brands are over it (D95)"


def test_the_selected_bundle_is_within_the_size_an_app_can_ship(real, selected, selection):
    assembled, data = selected
    assert len(data) <= select.SELECTED_MAX_BYTES == 20_000_000
    # the estimate the rule works by is within a few percent of the file it predicts
    assert abs(len(data) - selection.estimated_bytes) / len(data) < 0.04
    assert len(assembled.brands) == len(selection.curated) + len(selection.proxy_added)
    assert len(assembled.brands) + len(assembled.excluded) == len(real[1].brands)


def test_the_selected_bundle_has_every_model_of_the_brands_it_carries_and_no_other(real, selected):
    records, collected = real
    assembled, data = selected
    carried = {row[2] for row in assembled.brands}
    assert len(assembled.models) == sum(len(collected.brands[n].models) for n in carried)
    conn = sqlite3.connect(":memory:")
    conn.deserialize(data)
    assert conn.execute("SELECT COUNT(*) FROM models WHERE brand_id NOT IN (SELECT id FROM brands)"
                        ).fetchone() == (0,)
    # a remote file is carried when any of its brands is (by the remote that carries it, when it is
    # folded into a sibling's, D103), and keeps all its keys
    kept = {row[0] for row in assembled.remote_refs}
    for i, record in enumerate(records):
        maker, rows = collected.pairs[i]
        touches = maker in carried or any(r[0] in carried for r in rows)
        assert touches == (merge.ref_of(record) in kept), record.where
    assert sum(len(r.keys) for r in records if merge.ref_of(r) in kept) == len(assembled.keys)


def test_what_the_selected_bundle_leaves_out_is_recorded_with_where_to_find_it(real, selected):
    _, collected = real
    assembled, _ = selected
    carried = {row[2] for row in assembled.brands}
    assert {e[0] for e in assembled.excluded} == set(collected.brands) - carried
    for norm, _, api in assembled.excluded:
        # a brand has a shard of the app API exactly when the IR Blaster import files it
        assert (api is not None) == bool(collected.brands[norm].api_names)


def test_design_quotes_the_numbers_of_the_real_build(real, selected, selection):
    """DESIGN section 23 states these; they are checked here so the text cannot go stale
    (as D86's does). Byte sizes are not asserted: a different SQLite library may lay pages
    out differently, which is why D89 does not promise the same bytes across libraries."""
    text = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    section = text[text.index("## 23. The catalog bundle"):]
    records, collected = real
    assembled, data = selected
    keys = sum(len(r.keys) for r in records)
    models = sum(len(b.models) for b in collected.brands.values())
    kept = {row[0] for row in assembled.remote_refs}
    left_out = [r for r in records if merge.ref_of(r) not in kept]
    quoted = [
        f"{len(collected.brands):,} | {len(assembled.brands)}",            # the table's brands row
        f"{models:,} | {len(assembled.models):,}",
        f"{len(records):,} | {len(kept):,}",                                  # the remote files
        f"{len(records) - collected.folded.folded:,} | {len(assembled.remotes):,}",   # the remotes, D103
        f"{keys:,} | {len(assembled.keys):,}",
        f"155,976 | {len(assembled.signals):,}",
        f"{len(assembled.excluded):,} brands, {len(left_out):,} remotes, "
        f"{sum(len(r.keys) for r in left_out):,} keys",
        f"{len(selection.curated)} brands of the list and one by the proxy",
        f"{len(selection.skipped)} brands of the list are left out for lack of room",
        f"{sum(1 for r in left_out if r.source != 'irblaster'):,} remotes of LIRC and SmartIR",
        f"{len(select.read_curated(select.CURATED_FILE.read_text(encoding='utf-8')))} well-known brands",
        "411,265 keys but only 57,709 distinct compiled signals",
    ]
    for fact in quoted:
        assert fact in section, f"DESIGN section 23 no longer says {fact!r}"
    assert len(selection.proxy_added) == 1
