"""``rl keys report``: the coverage of the canonical vocabulary, per source and per remote (D86).

The report is checked on a small corpus whose every number is known, then on the real
tree, where DESIGN.md quotes what it says.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from remote_ledger.cli import main
from remote_ledger.keys import load_vocabulary
from remote_ledger.keys_report import (
    SOURCES, build_report, percent, read_corpus, render_json, render_summary, render_text,
    source_of,
)

ROOT = Path(__file__).resolve().parents[1]

#: Ten labels the vocabulary maps, and three it must not.
GOOD = ["POWER", "VOL+", "VOL-", "MUTE", "MENU", "OK", "UP", "DOWN", "LEFT", "RIGHT"]
BAD = ["??", "P/C", "MODE"]


def _write(root: Path, where: str, labels, *, named: bool = False) -> None:
    """A remote whose keys are ``labels``. ``named`` keys carry no label: the name is the text."""
    keys = {}
    for i, text in enumerate(labels):
        if named:
            keys[text] = {"forms": []}
        else:
            keys[f"KEY_{i}"] = {"label": text, "forms": []}
    path = root / "remotes" / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"manufacturer": "M", "model": "m", "keys": keys}), encoding="utf-8")


def _mix(good: int, bad: int) -> list[str]:
    """``good`` labels the vocabulary maps and ``bad`` it does not, in turn ``??``, ``P/C``, ``MODE``."""
    return GOOD[:good] + [BAD[i % 3] for i in range(bad)]


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "ledger"
    _write(root, "irblaster/A/1-NEC1.json", _mix(10, 0))        # 100%
    _write(root, "irblaster/A/2-NEC1.json", _mix(9, 1))         # 90% exactly
    _write(root, "irblaster/B/3-NEC1.json", _mix(8, 2))         # 80%
    _write(root, "irblaster/B/4-NEC1.json", _mix(7, 3))         # 70%
    _write(root, "irblaster/C/5-NEC1.json", _mix(5, 5))         # 50% exactly
    _write(root, "irblaster/C/6-NEC1.json", _mix(4, 6))         # 40%
    _write(root, "irblaster/D/7-NEC1.json", _mix(3, 1))         # 4 keys: 75%, and fewer than 10
    _write(root, "lirc/x/a.json", ["KEY_POWER", "KEY_MUTE", "KEY_AGAIN"], named=True)
    _write(root, "smartir/y/b.json", ["KEY_VOLUMEUP", "KEY_VOLUMEDOWN"], named=True)
    _write(root, "sony/RMT.json", ["KEY_POWER"], named=True)
    return root


def test_the_source_is_the_import_root_and_everything_else_is_authored():
    assert source_of("remotes/irblaster/A/1-NEC1.json") == "irblaster"
    assert source_of("remotes/lirc/sony/x.json") == "lirc"
    assert source_of("remotes/smartir/Argo/fan.json") == "smartir"
    assert source_of("remotes/sony/RMT-B118P.json") == "authored"
    assert source_of("remotes/lirc_like/x.json") == "authored"


def test_a_percentage_is_rounded_down_so_89_97_is_never_90():
    assert percent(8997, 10000) == Decimal("89.9") and str(percent(8997, 10000)) == "89.9"
    assert str(percent(9, 10)) == "90.0" and str(percent(1, 1)) == "100.0"
    assert str(percent(0, 7)) == "0.0" and str(percent(1, 3)) == "33.3" and str(percent(2, 3)) == "66.6"
    assert str(percent(0, 0)) == "0.0"                       # no remotes: not a division by zero


def test_the_counts_for_a_known_corpus(corpus):
    report = build_report(read_corpus(corpus))
    # 100 and 90 are at 90; with 80 and 75 at 75; with 70 and 50 at 50; only 40 is not
    assert report["sources"]["irblaster"] == {
        "remotes": 7, "keys": 64, "mapped": 46, "atLeast90": 2, "atLeast75": 4, "atLeast50": 6}
    assert report["sources"]["lirc"] == {
        "remotes": 1, "keys": 3, "mapped": 2, "atLeast90": 0, "atLeast75": 0, "atLeast50": 1}
    assert report["sources"]["smartir"] == {
        "remotes": 1, "keys": 2, "mapped": 2, "atLeast90": 1, "atLeast75": 1, "atLeast50": 1}
    assert report["sources"]["authored"]["remotes"] == 1
    # the fixture holds no hifi-remote file, and a source with no remote is not a row
    assert list(report["sources"]) == [*(s for s in SOURCES if s != "hifi-remote"), "all"]
    assert report["sources"]["all"] == {
        "remotes": 10, "keys": 70, "mapped": 51, "atLeast90": 4, "atLeast75": 6, "atLeast50": 9}


def test_the_thresholds_are_inclusive_and_exact(corpus):
    """90 percent of 10 keys is 9 and 9 of 10 counts; 5 of 10 is at 50 and not at 75."""
    big = build_report(read_corpus(corpus))["sourcesWithMinKeys"]
    assert big["irblaster"] == {
        "remotes": 6, "keys": 60, "mapped": 43, "atLeast90": 2, "atLeast75": 3, "atLeast50": 5}


def test_remotes_with_fewer_keys_than_the_minimum_are_left_out_of_the_second_table(corpus):
    report = build_report(read_corpus(corpus))
    assert report["minKeys"] == 10
    for source in ("lirc", "smartir", "authored"):
        assert report["sourcesWithMinKeys"][source]["remotes"] == 0
    assert report["sourcesWithMinKeys"]["all"]["remotes"] == 6


def test_the_unmapped_names_are_ranked_by_keys_and_say_how_many_remotes(corpus):
    report = build_report(read_corpus(corpus))
    assert [(r["text"], r["fold"], r["keys"], r["remotes"], r["spellings"]) for r in report["unmapped"]] == [
        ("??", "??", 8, 6, 1),
        ("P/C", "P/C", 6, 4, 1),
        ("MODE", "MODE", 4, 3, 1),
        ("KEY_AGAIN", "AGAIN", 1, 1, 1),
    ]
    assert report["unmappedTotal"] == {"keys": 19, "names": 4}


def test_unmapped_names_fold_their_spellings_and_count_a_remote_once(tmp_path):
    root = tmp_path / "ledger"
    _write(root, "irblaster/A/1.json", ["mode", "MODE", "Mode", "mode", "POWER"])
    _write(root, "irblaster/A/2.json", ["MODE", "POWER"])
    report = build_report(read_corpus(root))
    (row,) = report["unmapped"]
    assert (row["fold"], row["keys"], row["remotes"], row["spellings"]) == ("MODE", 5, 2, 3)
    assert row["text"] == "MODE"      # "mode" and "MODE" are two keys each: the tie goes to the smaller text


def test_ties_are_ordered_by_the_fold(tmp_path):
    root = tmp_path / "ledger"
    _write(root, "irblaster/A/1.json", ["ZED", "ALPHA", "MIDDLE", "POWER"])
    folds = [r["fold"] for r in build_report(read_corpus(root))["unmapped"]]
    assert folds == ["ALPHA", "MIDDLE", "ZED"]


def test_a_label_that_folds_to_nothing_is_one_name(tmp_path):
    root = tmp_path / "ledger"
    _write(root, "lirc/x/a.json", ["_", "__", "KEY_", "POWER"], named=True)
    report = build_report(read_corpus(root))
    assert [(r["fold"], r["keys"]) for r in report["unmapped"]] == [("", 3)]


def test_the_lists_are_cut_at_fifty_and_twenty(tmp_path):
    root = tmp_path / "ledger"
    for i in range(60):
        _write(root, f"irblaster/A/{i:03}.json",
               [f"WORD{i}"] + GOOD[: i % 10] + [f"X{i}_{j}" for j in range(10)])
    report = build_report(read_corpus(root))
    assert len(report["unmapped"]) == 50 and len(report["worst"]) == 20
    assert report["unmappedTotal"]["names"] == 60 * 11


def test_the_worst_remotes_have_ten_keys_and_are_ordered(tmp_path):
    root = tmp_path / "ledger"
    _write(root, "irblaster/A/small.json", ["??", "??"])                      # 0%, but two keys
    _write(root, "irblaster/A/b.json", _mix(0, 12))                           # 0% of 12
    _write(root, "irblaster/A/a.json", _mix(0, 12))                           # the same, a path earlier
    _write(root, "irblaster/A/big.json", _mix(0, 15))                         # 0% of 15: the worst
    _write(root, "irblaster/A/half.json", _mix(5, 5))                         # 50%
    report = build_report(read_corpus(root))
    assert [(r["remote"], r["source"], r["keys"], r["mapped"]) for r in report["worst"]] == [
        ("remotes/irblaster/A/big.json", "irblaster", 15, 0),
        ("remotes/irblaster/A/a.json", "irblaster", 12, 0),
        ("remotes/irblaster/A/b.json", "irblaster", 12, 0),
        ("remotes/irblaster/A/half.json", "irblaster", 10, 5),
    ]


def test_the_label_is_the_text_when_there_is_one_and_the_name_when_there_is_not(tmp_path):
    root = tmp_path / "ledger"
    path = root / "remotes" / "irblaster" / "A" / "x.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"keys": {
        "KEY_POWER": {"label": "??", "forms": []},       # the label says nothing: not mapped
        "KEY_MUTE": {"forms": []},                       # no label: the name
        "KEY_X": {"label": "OK", "forms": []},
    }}), encoding="utf-8")
    report = build_report(read_corpus(root))
    assert report["sources"]["irblaster"]["mapped"] == 2
    assert [r["text"] for r in report["unmapped"]] == ["??"]


def test_the_text_report_reads_in_order(corpus):
    text = render_text(build_report(read_corpus(corpus)))
    vocabulary = load_vocabulary()
    assert text.startswith(f"Canonical key vocabulary v1: {len(vocabulary.keys)} keys in "
                           f"{len(vocabulary.groups)} groups, ")
    headings = ["All remotes:", "Remotes with at least 10 keys:", "most frequent unmapped names",
                "remotes with the lowest coverage"]
    assert [text.index(h) for h in headings] == sorted(text.index(h) for h in headings)
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "remotes/irblaster/C/6-NEC1.json" in text and "40.0%" in text
    assert "71.8% (46)" in text and "28.5% (2)" in text      # 46 of 64 keys, 2 of 7 remotes: rounded down


def test_the_json_report_is_sorted_and_carries_percentages(corpus):
    text = render_json(build_report(read_corpus(corpus)))
    assert text.endswith("}\n") and "\r" not in text
    parsed = json.loads(text)
    assert list(parsed) == sorted(parsed)
    assert set(parsed) == {"minKeys", "sources", "sourcesWithMinKeys", "unmapped", "unmappedTotal",
                           "vocabulary", "worst"}
    assert parsed["sources"]["irblaster"]["percentMapped"] == 71.8
    assert parsed["sources"]["irblaster"]["percentAtLeast90"] == 28.5
    assert parsed["vocabulary"]["version"] == 1


def test_the_same_corpus_gives_the_same_bytes_at_any_worker_count(corpus, capsys, monkeypatch):
    monkeypatch.chdir(corpus)
    seen: dict[tuple, set] = {(): set(), ("--json",): set()}
    for jobs in ("1", "2", "3"):
        for flag in seen:
            assert main(["keys", "report", *flag, "--jobs", jobs]) == 0
            seen[flag].add(capsys.readouterr().out)
    assert all(len(outputs) == 1 and next(iter(outputs)) for outputs in seen.values())


def test_the_command_prints_to_standard_output_and_writes_nothing(corpus, capsys, monkeypatch):
    monkeypatch.chdir(corpus)
    before = sorted(p.relative_to(corpus).as_posix() for p in corpus.rglob("*"))
    assert main(["keys", "report"]) == 0
    out = capsys.readouterr()
    assert out.out.startswith("Canonical key vocabulary v1:") and out.err == ""
    assert main(["keys", "report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sources"]["all"]["remotes"] == 10
    assert sorted(p.relative_to(corpus).as_posix() for p in corpus.rglob("*")) == before


def test_keys_needs_a_subcommand(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["keys"])
    assert caught.value.code == 2 and "report" in capsys.readouterr().err


# --- the real tree --------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    return build_report(read_corpus(ROOT))


def test_the_real_corpus_is_the_one_the_report_describes(real):
    sources = real["sources"]
    assert list(sources) == [*SOURCES, "all"]
    assert sources["irblaster"]["remotes"] == 10013 and sources["irblaster"]["keys"] == 411265
    assert sources["all"]["remotes"] == sum(sources[s]["remotes"] for s in SOURCES)
    assert sources["all"]["keys"] == sum(sources[s]["keys"] for s in SOURCES)
    for row in sources.values():
        assert row["mapped"] <= row["keys"]
        assert row["atLeast90"] <= row["atLeast75"] <= row["atLeast50"] <= row["remotes"]
    assert len(real["unmapped"]) == 50 and len(real["worst"]) == 20
    assert all(r["keys"] >= 10 for r in real["worst"])


def test_design_states_the_size_of_the_vocabulary(real):
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    size = real["vocabulary"]
    assert f"{size['keys']} keys in all" in design and f"{size['aliases']} in all" in design
    assert size["groups"] == 11 + 3                      # the eleven named in D83 and the three it adds


def test_design_quotes_the_numbers_the_report_prints(real):
    """The coverage in DESIGN.md is asserted, not kept by hand (R14's principle): a change to the
    vocabulary, an alias or the imported data that moves a number fails here until DESIGN.md
    section 22 quotes the new one (`rl keys report` prints it)."""
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    for line in render_summary(real):
        if line.strip():
            assert line in design, f"DESIGN.md does not quote this line of `rl keys report`:\n{line}"
