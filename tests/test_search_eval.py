"""The search test set and its harness (D98): ``rl bundle search-eval``.

The generator is held to its rules class by class on single devices; the harness to what it
reports on the real selected bundle: at least 200 queries, every class, the same report twice,
the honesty caveat, and a place for real queries that is read and scored. The rates themselves
are not asserted as targets: the floors below are regression guards for what a lookup must always
do (an exact name finds itself), and the numbers are the report's to state."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from remote_ledger import cli, matching
from remote_ledger.bundle import build, search_eval
from remote_ledger.bundle.search_eval import CLASSES, Query, variant
from remote_ledger.bundle.textnorm import search_norm
from remote_ledger.matching import MatchIndex

ROOT = Path(__file__).resolve().parent.parent


def osa(a: str, b: str) -> int:
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


# --- the classes, on one device -------------------------------------------------------------------


def test_exact_case_model_only_and_reversed():
    assert variant("exact", "SAMSUNG", "UN50NU6900F", 0) == "SAMSUNG UN50NU6900F"
    assert [variant("case", "SAMSUNG", "UN50NU6900F", p) for p in (0, 1, 2)] == [
        "samsung un50nu6900f", "SAMSUNG UN50NU6900F", "Samsung Un50Nu6900F"]
    assert variant("model-only", "SAMSUNG", "UN50NU6900F", 0) == "UN50NU6900F"
    assert variant("reversed", "SAMSUNG", "UN50NU6900F", 0) == "UN50NU6900F SAMSUNG"


def test_punctuation_changes_only_separators_and_never_the_key():
    for name in ("UN50NU6900F", "KD - 49 X 8088", "BDP-S360", "RM-ED011"):
        key = search_norm(name)
        for pick in range(3):
            text = variant("punctuation", "SONY", name, pick)
            if text is not None:
                assert search_norm(text) == "sony" + key and text != f"SONY {name}", (name, pick)
    assert variant("punctuation", "SONY", "UN50NU6900F", 0) is None        # it has no separator to strip
    assert variant("punctuation", "SONY", "UN50NU6900F", 1) == "SONY UN 50 NU 6900 F"
    assert variant("punctuation", "SONY", "UN50NU6900F", 2) == "SONY UN-50NU6900F"
    assert variant("punctuation", "SONY", "KD-49X8088", 0) == "SONY KD49X8088"
    assert variant("punctuation", "SONY", "KD49X8088", 0) is None          # nothing to change


def test_dropped_suffix_cuts_the_trailing_letters_of_a_model_that_has_them():
    assert variant("dropped-suffix", "SAMSUNG", "UN50NU6900F", 0) == "SAMSUNG UN50NU6900"
    assert variant("dropped-suffix", "SAMSUNG", "UN50NU6900FA", 0) == "SAMSUNG UN50NU6900"
    assert variant("dropped-suffix", "SAMSUNG", "UN50NU6900", 0) is None   # no suffix
    assert variant("dropped-suffix", "SAMSUNG", "AB1C", 0) is None         # what is left is too short
    assert variant("dropped-suffix", "SAMSUNG", "UN50NU6900FXZA", 0) is None   # more than two letters


def test_a_typo_is_one_edit_and_never_the_first_character():
    for name in ("UN50NU6900F", "KD - 49 X 8088", "OLED55C9PUA", "BDP-S360", "42LB5800"):
        for pick in range(40):
            text = variant("typo", "LG", name, pick)
            assert text.startswith("LG ")
            typed, real = search_norm(text[3:]), search_norm(name)
            assert osa(typed, real) == 1, (name, pick, text)
            assert typed[0] == real[0] and typed != real
    assert variant("typo", "LG", "AB12", 0) is None                          # too short to be a typo test
    kinds = {len(search_norm(variant("typo", "LG", "OLED55C9PUA", p)[3:])) for p in range(40)}
    assert kinds == {10, 11}                                                 # a drop (10) and a swap (11)


def test_brand_partial_is_the_brand_and_about_sixty_percent_of_the_model():
    text = variant("brand-partial", "SAMSUNG", "UN50NU6900F", 0)
    assert text == "SAMSUNG UN50NU"                       # 60% of eleven characters is six
    for name in ("UN50NU6900F", "KD - 49 X 8088", "OLED55C9PUA"):
        key = search_norm(name)
        partial = search_norm(variant("brand-partial", "X", name, 0)[2:])
        assert key.startswith(partial) and 5 <= len(partial) < len(key)
        assert len(partial) == max(5, len(key) * 6 // 10)
    assert variant("brand-partial", "X", "AB12CD", 0) is None               # under eight characters


# --- the generator ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def conn(real_selected):
    return MatchIndex.open(real_selected[1] / build.BUNDLE_FILE).conn


@pytest.fixture(scope="module")
def queries(conn):
    return search_eval.generate(conn)


def test_at_least_200_queries_in_every_class_each_with_its_expected_remotes(queries, conn):
    assert len(queries) >= 200
    for cls in CLASSES:
        assert sum(1 for q in queries if q.cls == cls) == search_eval.PER_CLASS, cls
    assert {q.cls for q in queries} == set(CLASSES)
    for q in queries:
        assert q.expect and list(q.expect) == sorted(q.expect)
        assert q.expect == tuple(r for (r,) in conn.execute(
            "SELECT c.remote_id FROM controls c JOIN models m ON m.id = c.model_id "
            "JOIN brands b ON b.id = m.brand_id WHERE b.name = ? AND m.name = ? ORDER BY c.remote_id",
            (q.brand, q.model)))


def test_a_class_has_at_most_two_devices_of_a_brand_and_the_queries_are_devices_of_the_bundle(queries, conn):
    for cls in CLASSES:
        brands = [q.brand for q in queries if q.cls == cls]
        assert max(brands.count(b) for b in set(brands)) <= search_eval.PER_BRAND, cls
        assert len(set(brands)) >= 10, cls
    for q in queries:
        key = search_norm(q.model)
        assert len(key) >= search_eval.MIN_KEY and any(c.isdigit() for c in key)
        assert len(q.model) <= search_eval.MAX_NAME
    for q in queries:
        assert conn.execute("SELECT 1 FROM models m JOIN brands b ON b.id = m.brand_id "
                            "WHERE b.name = ? AND m.name = ? AND m.kind = 0", (q.brand, q.model)).fetchone()


def test_the_generator_is_deterministic_and_the_seed_changes_it(conn):
    first = search_eval.generate(conn, seed=1, per_class=10)
    assert first == search_eval.generate(conn, seed=1, per_class=10)
    other = search_eval.generate(conn, seed=2, per_class=10)
    assert [q.text for q in first] != [q.text for q in other]
    assert len(search_eval.generate(conn, per_class=5)) == 5 * len(CLASSES)


# --- scoring ------------------------------------------------------------------------------------------


def cand(model, remotes, brand="B"):
    return matching.Candidate(brand, model, tuple(remotes), 0.9, ())


def test_a_hit_is_a_model_whose_remotes_contain_all_the_expected_ones():
    q = Query("x", "exact", (1, 2))
    assert search_eval.hits(q, [cand("m", [1, 2])]) == (True, True)
    assert search_eval.hits(q, [cand("m", [1, 2, 3])]) == (True, True)       # offers a superset
    assert search_eval.hits(q, [cand("m", [1])]) == (False, False)           # only part of them
    assert search_eval.hits(q, [cand("a", [9]), cand("m", [1, 2])]) == (False, True)
    assert search_eval.hits(q, [cand("a", [9])] * 5 + [cand("m", [1, 2])]) == (False, False)   # sixth
    assert search_eval.hits(q, [cand(None, [1, 2])]) == (False, False)       # a brand is not a device
    assert search_eval.hits(q, []) == (False, False)


def test_a_query_that_must_find_nothing_is_right_when_no_model_comes_back():
    q = Query("zzz", "real", None)
    assert search_eval.hits(q, []) == (True, True)
    assert search_eval.hits(q, [cand(None, [])]) == (True, True)
    assert search_eval.hits(q, [cand("m", [1])]) == (False, False)


# --- the report ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def text(real_selected):
    return search_eval.report(real_selected[1])


def test_the_report_is_a_markdown_table_per_class_and_overall(text):
    rows = [line for line in text.splitlines() if line.startswith("|")]
    assert rows[0] == "| class | queries | top-1 | top-5 |" and rows[1] == "|---|---|---|---|"
    names = [r.split("|")[1].strip() for r in rows[2:2 + len(CLASSES) + 1]]
    assert names == [*CLASSES, "**all**"]
    for row in rows[2:2 + len(CLASSES)]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[1] == "40"
        assert re.fullmatch(r"\d+ \(\d+\.\d%\)", cells[2]) and re.fullmatch(r"\d+ \(\d+\.\d%\)", cells[3])
    assert rows[2 + len(CLASSES)].split("|")[2].strip() == str(search_eval.PER_CLASS * len(CLASSES))


def test_the_report_says_the_queries_are_friendlier_than_real_ones(text):
    assert "upper bound" in text
    assert "never misspell a brand" in text and "Real people type worse" in text
    assert "real_queries.json" in text and "None yet" in text


def test_the_numbers_of_the_table_add_up(text):
    rows = [line for line in text.splitlines() if line.startswith("|")][2:3 + len(CLASSES)]
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    one = [int(c[2].split()[0]) for c in cells]
    five = [int(c[3].split()[0]) for c in cells]
    assert sum(one[:-1]) == one[-1] and sum(five[:-1]) == five[-1]
    assert all(o <= f <= 40 for o, f in zip(one[:-1], five[:-1]))


def test_the_report_is_the_same_twice_and_changes_with_the_seed(real_selected):
    directory = real_selected[1]
    first = search_eval.report(directory, per_class=6)
    assert search_eval.report(directory, per_class=6) == first
    assert search_eval.report(directory, per_class=6, seed=7) != first
    assert "Timing" not in first and "Timing" in search_eval.report(directory, per_class=3, timing=True)


def test_regression_floors_for_what_a_lookup_must_always_do(real_selected, queries):
    """Not targets: the report is where the rates are stated. These are classes in which the
    query is the catalog's own name up to separators and case, so a miss is a bug."""
    index = MatchIndex.open(real_selected[1] / build.BUNDLE_FILE)
    results, timings = search_eval.evaluate(index, queries)
    by_class = {c: [r for r in results if r.query.cls == c] for c in CLASSES}
    for cls in ("exact", "punctuation", "model-only", "reversed"):
        assert sum(r.top5 for r in by_class[cls]) / len(by_class[cls]) >= 0.95, cls
    assert sum(r.top5 for r in results) / len(results) >= 0.9
    assert max(timings) < 1.0


def design_section() -> str:
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    return design[design.index("## 24. Finding a device in the catalog"):]


def test_design_quotes_the_numbers_of_the_selected_report(text):
    """DESIGN section 24 (D99) states the table of the selected bundle; a change of the matcher,
    the generator or the data that moves a number must move the text too."""
    section = design_section()
    rows = [line for line in text.splitlines() if line.startswith("|")][2:3 + len(CLASSES)]
    for row in rows[:-1]:
        cls, count, one, five = [c.strip() for c in row.strip("|").split("|")]
        assert f"| {cls} | {one} | {five} | " in section, cls
    _, count, one, five = [c.strip() for c in rows[-1].strip("|").split("|")]
    assert f"| **all ({count})** | **{one}** | **{five}** | " in section


# --- the hand-written queries -----------------------------------------------------------------------------


def write_real(path: Path, entries) -> Path:
    path.write_text(json.dumps({"format": 1, "queries": entries}), encoding="utf-8")
    return path


def test_the_shipped_file_documents_its_format_and_holds_no_query_yet():
    document = json.loads(search_eval.REAL_QUERIES.read_text(encoding="utf-8"))
    assert document["format"] == 1 and document["queries"] == []
    assert document["about"] == search_eval.REAL_ABOUT
    assert set(document["example"]) == {"query", "expect", "note"}
    for word in ("query", "expect", "brand", "model", "note", "null"):
        assert word in document["about"]


def test_real_queries_are_resolved_scored_and_reported_on_their_own(real_selected, tmp_path):
    _, directory = real_selected
    index = MatchIndex.open(directory / build.BUNDLE_FILE)
    entries = [
        {"query": "samsung un50nu6900f", "expect": {"brand": "samsung", "model": "UN 50 NU 6900 F"},
         "note": "typed by a person"},
        {"query": "samsung un50nu69", "expect": {"brand": "SAMSUNG", "model": "UN50NU6900F"}},
        {"query": "sony zzzzzzz123", "expect": None},                       # nothing to find: right
        {"query": "samsung un50nu6900f", "expect": None},                   # a device that exists: wrong
        {"query": "nonexistent 12345", "expect": {"brand": "ZZZ NO SUCH BRAND", "model": "M1"}},
        {"query": "not carried", "expect": {"brand": "GRUNDIG", "model": "ST 70-100"}},
    ]
    real, missing, entries_read = search_eval.read_real(write_real(tmp_path / "r.json", entries),
                                                        index.conn)
    assert entries_read == 6 and missing == 2 and len(real) == 4
    assert [q.expect is None for q in real] == [False, False, True, True]
    assert real[0].expect == tuple(r for (r,) in index.conn.execute(
        "SELECT c.remote_id FROM controls c JOIN models m ON m.id = c.model_id "
        "JOIN brands b ON b.id = m.brand_id WHERE b.name = 'SAMSUNG' AND m.name = 'UN50NU6900F' "
        "ORDER BY c.remote_id"))
    assert real[0].expect and real[1].expect == real[0].expect
    results, _ = search_eval.evaluate(index, real)
    assert [(r.top1, r.top5) for r in results[:1]] == [(True, True)]
    assert [(r.top1, r.top5) for r in results[2:]] == [(True, True), (False, False)]
    text = search_eval.report(directory, queries=tmp_path / "r.json", per_class=2)
    section = text.split("## Hand-written queries of real people")[1].split("## Generated")[0]
    assert "| real | 4 |" in section and "2 of 6 entries name a device this bundle does not have" in section
    assert "missed: `samsung un50nu6900f` gave" in section


def test_an_absent_or_empty_file_of_real_queries_is_none_yet(real_selected, tmp_path):
    index = MatchIndex.open(real_selected[1] / build.BUNDLE_FILE)
    assert search_eval.read_real(tmp_path / "no.json", index.conn) == ([], 0, 0)
    assert search_eval.read_real(write_real(tmp_path / "empty.json", []), index.conn) == ([], 0, 0)


# --- the command ------------------------------------------------------------------------------------------


def test_the_command_prints_the_report_and_writes_it(real_selected, tmp_path, monkeypatch, capsys):
    _, directory = real_selected
    out = tmp_path / "report.md"
    monkeypatch.chdir(ROOT)
    assert cli.main(["bundle", "search-eval", "--bundle", str(directory), "--per-class", "5",
                     "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert printed == out.read_text(encoding="utf-8")
    assert "# Search evaluation of the selected bundle" in printed and "40 devices" not in printed
    assert "5 devices for each of 8 classes" in printed


def test_the_command_asks_for_a_bundle_when_there_is_none(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["bundle", "search-eval"]) == 1
    assert "no bundle there; build one with `rl bundle`" in capsys.readouterr().err
