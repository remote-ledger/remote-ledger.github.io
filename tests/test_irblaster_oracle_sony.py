"""The Sony oracle comparison, on committed fixtures.

``tests/fixtures/irblaster/sony{12,15,20}.json`` hold a few dozen rows each of
what the SwiftRemote app itself transmits for distinct codes of its Sony DB
protocols (the full run, 2,858 codes, is ``tools/irblaster_oracle_sony.py
--oracle DIR``). The tool is loaded from its path; nothing else imports it.
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from remote_ledger.irblaster import hex_sony

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "irblaster"

_spec = importlib.util.spec_from_file_location(
    "irblaster_oracle_sony", ROOT / "tools" / "irblaster_oracle_sony.py")
oracle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oracle)

NAMES = ("SONY12", "SONY15", "SONY20")


def _rows(name):
    return json.loads((FIXTURES / f"{name.lower()}.json").read_text())["rows"]


@pytest.fixture(scope="module")
def tallies():
    return oracle.run([row for name in NAMES for row in _rows(name)])


@pytest.mark.parametrize("name", NAMES)
def test_fixture_is_a_few_dozen_distinct_codes(name):
    rows = _rows(name)
    assert 30 <= len(rows) <= 60
    assert len({r["hex"] for r in rows}) == len(rows)
    assert all(r["protocol"] == name and r["freq"] == 40_000 for r in rows)


@pytest.mark.parametrize("name", NAMES)
def test_the_app_sends_three_identical_frames_at_forty_kilohertz(name):
    for row in _rows(name):
        assert oracle.frames_identical(row["pattern"], hex_sony.MIN_SENDS[name])


@pytest.mark.parametrize("name", NAMES)
def test_the_ledger_sends_what_the_app_sends_for_every_code_the_apps_reading_can_hold(name, tallies):
    """The waveform check: same bits, same signal, to the microsecond."""
    t = tallies[name]
    assert t.app_mismatched == []
    assert t.model_errors == []
    assert t.app_matched > 0
    assert t.exact_durations == t.app_matched * len(_rows(name)[0]["pattern"])


def test_the_app_masks_sony15_codes_with_a_sixteenth_bit_and_the_tool_proves_it(tallies):
    t = tallies["SONY15"]
    assert sum(t.app_unrepresentable.values()) == t.app_masked_verified > 0
    assert set(t.app_unrepresentable) == {
        "SONY15 hexcode has bits above the 15-bit frame; the app masks them away "
        "and transmits a different code"}
    for name in ("SONY12", "SONY20"):
        assert not tallies[name].app_unrepresentable


@pytest.mark.parametrize("name", NAMES)
def test_the_databases_reading_represents_every_code(name, tallies):
    assert not tallies[name].db_unrepresentable


@pytest.mark.parametrize("name", NAMES)
def test_every_difference_between_the_readings_is_the_hex_bit_order(name, tallies):
    t = tallies[name]
    assert t.db_unexplained == []
    assert t.db_same + t.db_differs_explained == t.codes
    # They really do disagree: this is not a vacuous pass.
    assert t.db_differs_explained > t.codes // 2


def test_the_sony20_readings_never_agree_on_a_signal(tallies):
    """No Sony20 code in the fixture, and none of the 1,213 in the database,
    is the same frame under both readings."""
    assert tallies["SONY20"].db_same == 0


# --- the tool must be able to fail ---------------------------------------------

def _first_row(name):
    return copy.deepcopy(_rows(name)[0])


@pytest.mark.parametrize("name", NAMES)
def test_a_changed_duration_is_reported_as_a_mismatch(name):
    row = _first_row(name)
    row["pattern"][4] += 400          # a bit mark 400 us long: beyond 12% and 150 us
    t = oracle.run([row])[name]
    assert t.app_matched == 0 and t.app_mismatched


def test_a_change_inside_the_tolerance_is_tolerated_but_not_counted_exact():
    row = _first_row("SONY12")
    row["pattern"][4] += 50
    t = oracle.run([row])["SONY12"]
    assert t.app_matched == 1 and not t.app_mismatched
    assert t.exact_durations == len(row["pattern"]) - 1


def test_a_wrong_carrier_is_reported():
    row = _first_row("SONY12")
    row["freq"] = 36_000
    assert oracle.run([row])["SONY12"].app_mismatched


def test_a_missing_frame_is_reported():
    row = _first_row("SONY20")
    row["pattern"] = row["pattern"][: len(row["pattern"]) // 3 * 2]
    t = oracle.run([row])["SONY20"]
    assert t.app_mismatched or t.model_errors


def test_wrong_app_field_text_is_reported():
    row = _first_row("SONY20")
    row["params"] = {"address": "1FFF", "command": "7F"}
    assert oracle.run([row])["SONY20"].app_mismatched


def test_the_report_and_exit_codes(tallies, tmp_path, capsys):
    text, unexplained, disagrees = oracle.report(tallies)
    assert not unexplained and disagrees
    assert "SONY20: 40 distinct codes" in text
    paths = [str(FIXTURES / f"{n.lower()}.json") for n in NAMES]
    assert oracle.main.__module__ == "irblaster_oracle_sony"
    import sys
    argv, sys.argv = sys.argv, ["tool", *paths]
    try:
        assert oracle.main() == 0
        sys.argv = ["tool", "--strict", *paths]
        assert oracle.main() == 1
    finally:
        sys.argv = argv
    capsys.readouterr()


# --- which spelling does the database use? (--db) ------------------------------

def _db(tmp_path, spell):
    import sqlite3
    path = tmp_path / "keys.sqlite"
    con = sqlite3.connect(path)
    con.execute("create table keys (id integer, label text, hexcode text, protocol text)")
    for _label, db_name, protocol, frames in oracle.anchor_sets():
        for frame in frames:
            con.execute("insert into keys values (1, 'k', ?, ?)", (spell(protocol, *frame), db_name))
    con.commit()
    con.close()
    return path


def _frame_count(line):
    return int(line.split(": ")[1].split(" frames")[0])


def test_the_anchor_sets_are_the_cited_ones():
    sizes = sorted(len(frames) for _label, _db_name, _protocol, frames in oracle.anchor_sets())
    assert sizes == [22, 25, 30, 38]


def test_a_database_that_spells_frames_in_transmission_order_scores_all_of_them(tmp_path):
    lines = oracle.anchor_evidence(_db(tmp_path, oracle.tx_hex))
    assert len(lines) == 4
    for line in lines:
        assert f"transmission-order hex {_frame_count(line)}," in line


def test_a_database_that_spells_them_as_the_app_does_scores_the_other_way(tmp_path):
    for line in oracle.anchor_evidence(_db(tmp_path, oracle.app_hex)):
        assert line.endswith(f"as the app's packing {_frame_count(line)}")
        assert f"transmission-order hex {_frame_count(line)}," not in line


def test_tx_hex_is_what_the_mapping_reads_back():
    for _label, db_name, protocol, frames in oracle.anchor_sets():
        for frame in frames:
            hexcode = oracle.tx_hex(protocol, *frame)
            assert hex_sony.FROM_DB_HEX[db_name](hexcode) == (protocol, *frame)
