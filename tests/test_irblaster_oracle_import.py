"""``tools/irblaster_oracle_import.py``: the import, end to end, against the app.

The committed fixtures (``tests/fixtures/irblaster/*.json``) are a few dozen of
the app's own signals per DB protocol, 23 protocols in all. Here they become a
synthetic SwiftRemote database (one remote per protocol, one key per code), the
real importer runs over it, and the tool then compares every key, compiled
through the written file, with the app's signal. The full run over the real
database is ``tools/irblaster_oracle_import.py --checkout ... --oracle ...``;
its numbers are in NOTES/import-design.md.

The tool is tested as a tool: each test that breaks the import in one way
checks it is reported, so a green run means something.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest

from remote_ledger.irblaster import hex_japan, hex_misc, hex_nec, hex_philips, hex_sony, hex_unknown
from remote_ledger.irblaster.importer import IMPORT_ROOT, write_import

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "irblaster"
COMMIT = "6aafd15e1c95cf494ac729339b9a4701a4ab8f0a"

_spec = importlib.util.spec_from_file_location(
    "irblaster_oracle_import", ROOT / "tools" / "irblaster_oracle_import.py")
tool = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = tool          # worker processes unpickle process_file by this name
_spec.loader.exec_module(tool)

#: fixture file stem -> the DB protocol name
STEMS = {
    "nec": "NEC", "nec2": "NEC2", "necx1": "NECx1", "necx2": "NECx2",
    "sony12": "SONY12", "sony15": "SONY15", "sony20": "SONY20",
    "rc5": "RC5", "rc6": "RC6", "rca_38": "RCA_38", "thomson7": "Thomson7",
    "pioneer": "Pioneer", "jvc": "JVC", "sharp": "Sharp", "denon": "Denon",
    "samsung36": "Samsung36", "proton": "Proton", "f12_relaxed": "F12_relaxed",
    "recs80": "RECS80", "recs80_l": "RECS80_L",
    "rec80": "REC80", "rcc2026": "RCC2026", "rcc0082": "RCC0082",
}


def fixture_records(stem: str) -> list[dict]:
    doc = json.loads((FIXTURES / f"{stem}.json").read_text())
    rows = doc if isinstance(doc, list) else next(v for v in doc.values() if isinstance(v, list))
    return [{"hex": r["hex"], "freq": r["freq"], "pattern": r["pattern"]} for r in rows]


RECORDS = {STEMS[s]: fixture_records(s) for s in STEMS}


def sql_text() -> str:
    """One remote per DB protocol, id 1001..1023, one key per distinct code."""
    def q(text):
        return "'" + text.replace("'", "''") + "'"

    lines = [
        "PRAGMA foreign_keys=OFF;", "BEGIN TRANSACTION;",
        "CREATE TABLE brands (name TEXT PRIMARY KEY);",
        'CREATE TABLE IF NOT EXISTS "remotes" (id INTEGER PRIMARY KEY);',
        'CREATE TABLE IF NOT EXISTS "keys" (id INTEGER NOT NULL, label TEXT NOT NULL, '
        'hexcode TEXT NOT NULL, protocol TEXT NOT NULL, '
        "PRIMARY KEY (id, label, hexcode, protocol));",
        'CREATE TABLE IF NOT EXISTS "models" (brand TEXT NOT NULL, model TEXT NOT NULL, '
        "id INTEGER NOT NULL, PRIMARY KEY (brand, model, id));",
    ]
    for n, (proto, records) in enumerate(sorted(RECORDS.items())):
        db_id = 1001 + n
        lines.append(f"INSERT INTO remotes VALUES({db_id});")
        lines.append(f"INSERT INTO brands VALUES({q('MAKER ' + proto)});")
        lines.append(f"INSERT INTO models VALUES({q('MAKER ' + proto)},{q('M-' + proto)},{db_id});")
        for i, record in enumerate(sorted(records, key=lambda r: r["hex"])):
            lines.append(f"INSERT INTO keys VALUES({db_id},{q('K%d' % i)},{q(record['hex'])},{q(proto)});")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def write_oracle(directory: Path, mutate=None) -> Path:
    (directory / "by_protocol").mkdir(parents=True)
    for proto, records in RECORDS.items():
        rows = [dict(r, protocol=proto) for r in records]
        if mutate:
            mutate(proto, rows)
        (directory / "by_protocol" / f"{proto}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows))
    return directory


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("e2e")
    checkout = base / "SwiftRemote"
    (checkout / "assets" / "db_src").mkdir(parents=True)
    (checkout / "assets" / "db_src" / "swiftremote.sql").write_text(sql_text(), encoding="utf-8")
    ledger = base / "ledger"
    ledger.mkdir()
    write_import(ledger, checkout, COMMIT)
    oracle = write_oracle(base / "oracle")
    return {"checkout": checkout, "ledger": ledger, "oracle": oracle, "base": base}


@pytest.fixture(scope="module")
def clean(world):
    """The tool's result on the unbroken import, computed once."""
    return tool.run(world["ledger"], world["checkout"], world["oracle"], workers=1)


@pytest.fixture
def broken(world, tmp_path):
    """A private copy of the written ledger, to damage."""
    copy = tmp_path / "ledger"
    shutil.copytree(world["ledger"], copy)
    return copy


def run(world, ledger=None, oracle=None):
    return tool.run(ledger or world["ledger"], world["checkout"], oracle or world["oracle"], workers=1)


def first_file(ledger: Path, name: str) -> Path:
    return sorted((ledger / IMPORT_ROOT).rglob(name))[0]


# --- the independent classification of every fixture code -----------------------------


def expected_class(proto: str, hexcode: str) -> str:
    wire = {**{}, **hex_nec.FROM_DB_HEX, **hex_sony.FROM_DB_HEX, **hex_philips.FROM_DB_HEX,
            **hex_japan.FROM_DB_HEX, **hex_misc.FROM_DB_HEX, **hex_unknown.FROM_DB_HEX}
    app = {**hex_nec.FROM_DB_HEX_APP, **hex_sony.FROM_DB_HEX_APP, **hex_philips.FROM_DB_HEX_APP,
           **hex_japan.FROM_DB_HEX_APP, **hex_misc.FROM_DB_HEX_APP, **hex_unknown.FROM_DB_HEX_APP}
    try:
        w = wire[proto](hexcode)
    except ValueError:
        return tool.UNREPRESENTABLE
    try:
        a = app[proto](hexcode)
    except ValueError:
        return tool.DIFFERS
    return tool.DIFFERS if tuple(a) != tuple(w) else tool.MATCHED


def test_the_fixtures_cover_all_23_db_protocols_and_every_class():
    assert len(RECORDS) == 23 and sum(len(r) for r in RECORDS.values()) > 800
    classes = Counter(expected_class(p, r["hex"]) for p, rs in RECORDS.items() for r in rs)
    assert set(classes) == {tool.MATCHED, tool.DIFFERS, tool.UNREPRESENTABLE}


def test_the_whole_fixture_import_is_explained(clean):
    result = clean
    assert result["problems"] == []
    for proto, entry in result["by_protocol"].items():
        assert entry["keys"][tool.UNEXPLAINED] == 0, proto


def test_every_code_lands_in_the_class_the_reading_tables_predict(world):
    """The set the tool calls ``differs by reading`` is recomputed from the two
    reading tables alone, per protocol, and must be the same set."""
    result = run(world)
    for proto, records in RECORDS.items():
        codes = result["by_protocol"][proto]["codes"]
        for cls in (tool.MATCHED, tool.DIFFERS, tool.UNREPRESENTABLE):
            predicted = {r["hex"] for r in records if expected_class(proto, r["hex"]) == cls}
            assert codes[cls] == predicted, (proto, cls)


@pytest.mark.parametrize("proto", ["SONY12", "SONY20", "Pioneer", "Thomson7", "Proton", "RCC2026"])
def test_the_protocols_the_app_reads_differently_differ(clean, proto):
    keys = clean["by_protocol"][proto]["keys"]
    assert keys[tool.DIFFERS] > keys[tool.MATCHED]


@pytest.mark.parametrize("proto", ["NEC", "NEC2", "NECx1", "NECx2", "RC5", "RC6", "RCA_38",
                                   "Samsung36", "F12_relaxed", "RECS80", "RECS80_L", "RCC0082"])
def test_the_protocols_the_app_reads_the_same_way_match(clean, proto):
    keys = clean["by_protocol"][proto]["keys"]
    assert keys[tool.MATCHED] > 0 and keys[tool.DIFFERS] == 0


def test_the_documented_framing_is_named_not_folded_in(clean):
    notes = {p: dict(e["notes"]) for p, e in clean["by_protocol"].items()}
    assert notes["NEC"] == {"app stops at the last mark": notes["NEC"]["app stops at the last mark"]}
    assert "T=1 only (D3b)" in notes["RC5"] and "T=1 only (D3b)" in notes["RECS80"]
    assert "lead-out longer" in notes["RC6"]
    assert "lead-out differs" in notes["Samsung36"]
    assert "lead-out differs" in notes["REC80"]
    assert "three frames" in notes["Sharp"] and "three frames" in notes["Denon"]
    assert "app signal is its reading's encoding exactly" in notes["SONY12"]
    assert "a port of the app's stale encoder reproduces its signal exactly" in notes["RCC2026"]
    assert "the app masks the code to 15 bits" in notes["SONY15"]


def test_the_tool_exits_zero_on_a_clean_run_and_prints_the_table(world, capsys):
    status = tool.main(["--checkout", str(world["checkout"]), "--oracle", str(world["oracle"]),
                        "--ledger", str(world["ledger"]), "--workers", "1"])
    out = capsys.readouterr().out
    assert status == 0 and "problems: 0" in out
    assert "differs by reading" in out and "unrepresentable" in out and "UNEXPLAINED" in out
    assert re.search(r"^SONY12\s+codes\s+\d+", out, re.M)


def test_it_also_runs_with_workers(world):
    assert tool.run(world["ledger"], world["checkout"], world["oracle"], workers=2)["problems"] == []


def test_list_differences_prints_every_distinct_code_the_readings_disagree_on(world, capsys):
    assert tool.main(["--checkout", str(world["checkout"]), "--list-differences"]) == 0
    lines = capsys.readouterr().out.splitlines()
    expected = sum(1 for p, rs in RECORDS.items() for r in rs
                   if expected_class(p, r["hex"]) == tool.DIFFERS)
    assert len(lines) == expected
    assert any(line.startswith("SONY12\tA90\t") for line in lines) or \
        any(line.startswith("SONY12\t") for line in lines)


# --- the tool must see a broken import ---------------------------------------------------


def _edit(path: Path, change):
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc, indent=2) + "\n")


def _problems(world, ledger, oracle=None):
    return run(world, ledger, oracle)["problems"]


def test_a_wrong_device_in_a_file_is_reported(world, broken):
    path = first_file(broken, "*-NEC1.json")
    def change(doc):
        form = next(iter(doc["keys"].values()))["forms"][0]
        form["device"] = (int(form["device"], 16) + 1) % 256
    _edit(path, change)
    assert any("the wire reading gives" in p for p in _problems(world, broken))


def test_a_wrong_carrier_is_reported(world, broken):
    path = first_file(broken, "*-NEC1.json")
    _edit(path, lambda doc: doc["protocol"].update(carrierHz=45000))
    problems = _problems(world, broken)
    assert any("carrierHz is not the registry's nominal" in p for p in problems)
    assert any("carrier" in p and "against the app's" in p for p in problems)


def test_a_wrong_min_sends_is_reported(world, broken):
    path = first_file(broken, "*-NECx2.json")
    _edit(path, lambda doc: doc["protocol"].update(minSends=1))
    assert any("minSends 1, expected 2" in p for p in _problems(world, broken))


def test_a_unit_override_that_should_not_be_there_is_reported(world, broken):
    path = first_file(broken, "*-Samsung36.json")
    def change(doc):
        doc["protocol"].pop("unitUs")
        doc["protocol"].pop("claims")
    _edit(path, change)
    assert any("unitUs" in p for p in _problems(world, broken))


def test_a_key_dropped_from_a_file_is_reported(world, broken):
    path = first_file(broken, "*-RC5.json")
    _edit(path, lambda doc: doc["keys"].pop(next(iter(doc["keys"]))))
    problems = _problems(world, broken)
    assert any("is representable but was not imported" in p for p in problems)


def test_a_key_that_is_not_in_the_dump_is_reported_as_invented(world, broken):
    path = first_file(broken, "*-RC5.json")
    def change(doc):
        form = json.loads(json.dumps(next(iter(doc["keys"].values()))["forms"][0]))
        form["source"] = form["source"].replace("remote 1", "remote 9")
        doc["keys"]["KEY_INVENTED"] = {"forms": [form]}
    _edit(path, change)
    assert any("is not a row of the dump" in p for p in _problems(world, broken))


def test_a_changed_label_in_a_citation_is_reported(world, broken):
    path = first_file(broken, "*-Sony12.json")
    def change(doc):
        form = next(iter(doc["keys"].values()))["forms"][0]
        form["source"] = re.sub(r"'K\d+'", "'WRONG'", form["source"])
    _edit(path, change)
    assert any("is not a row of the dump" in p for p in _problems(world, broken))


def test_a_changed_signal_in_the_oracle_is_reported(world, tmp_path):
    def corrupt(proto, rows):
        if proto == "Proton":
            for row in rows:
                row["pattern"][3] *= 3
    oracle = write_oracle(tmp_path / "oracle", corrupt)
    problems = _problems(world, world["ledger"], oracle)
    # a code the app reads differently: its own reading no longer explains the signal
    assert any("the app's reading does not compile to the app's signal" in p for p in problems)
    # a code the app reads the same way: the imported signal is not the app's
    assert any("duration 3 is" in p and "against the app's" in p for p in problems)

    def corrupt_match(proto, rows):
        if proto == "RC6":
            for row in rows:
                row["pattern"][5] *= 3
    oracle = write_oracle(tmp_path / "oracle2", corrupt_match)
    assert any("against the app's" in p for p in _problems(world, world["ledger"], oracle))


def test_a_wrong_manufacturer_controls_or_model_is_reported(world, broken):
    path = first_file(broken, "*-RC6.json")
    _edit(path, lambda doc: doc.update(manufacturer="WRONG"))
    assert any("manufacturer 'WRONG'" in p for p in _problems(world, broken))
    path = first_file(broken, "*-RC6.json")
    _edit(path, lambda doc: doc.update(manufacturer="MAKER RC6", controls=["x"], model="IR Blaster DB 1"))
    problems = _problems(world, broken)
    assert any("controls are not the id's models rows" in p for p in problems)
    assert any("model 'IR Blaster DB 1'" in p for p in problems)


def test_a_key_label_that_is_not_the_databases_is_reported(world, broken):
    """D56a: the label must be the database's, verbatim, case included."""
    path = first_file(broken, "*-RC5.json")
    def change(doc):
        spec = next(iter(doc["keys"].values()))
        spec["label"] = spec["label"].lower()                 # 'K0' written as 'k0'
    _edit(path, change)
    problems = _problems(world, broken)
    assert any("label 'k0' is not the database's 'K0'" in p for p in problems)


def test_a_key_without_a_label_is_reported(world, broken):
    path = first_file(broken, "*-RC5.json")
    _edit(path, lambda doc: next(iter(doc["keys"].values())).pop("label"))
    assert any("the key has no label" in p for p in _problems(world, broken))


def test_controls_in_the_old_space_format_are_reported(world, broken):
    """D56b: "<BRAND> | <MODEL>"; the plain "<BRAND> <MODEL>" cannot be split."""
    path = first_file(broken, "*-RC6.json")
    _edit(path, lambda doc: doc.update(controls=["MAKER RC6 M-RC6"]))
    assert any("as '<BRAND> | <MODEL>'" in p for p in _problems(world, broken))


def test_controls_in_the_pipe_format_pass(world, clean):
    path = first_file(world["ledger"], "*-RC6.json")
    assert json.loads(path.read_text())["controls"] == ["MAKER RC6 | M-RC6"]
    assert clean["problems"] == []


def test_a_pipe_in_the_dump_is_reported(world, tmp_path):
    """The format is only parseable while no brand or model holds a pipe."""
    checkout = tmp_path / "SwiftRemote"
    (checkout / "assets" / "db_src").mkdir(parents=True)
    sql = (world["checkout"] / "assets" / "db_src" / "swiftremote.sql").read_text()
    assert "'M-RC6'" in sql
    (checkout / "assets" / "db_src" / "swiftremote.sql").write_text(
        sql.replace("'M-RC6'", "'M | RC6'"), encoding="utf-8")
    problems = tool.run(world["ledger"], checkout, world["oracle"], workers=1)["problems"]
    assert any("contain a pipe" in p for p in problems)


def test_a_skipped_row_the_report_does_not_list_is_reported(world, broken):
    report = broken / IMPORT_ROOT / "IMPORT.md"
    text = report.read_text()
    row = re.search(r"^\| \d+ \| NEC \| [0-9A-F]+ \| .*\|$", text, re.M).group(0)
    report.write_text(text.replace(row + "\n", "", 1))
    assert any("was skipped and IMPORT.md does not list it" in p for p in _problems(world, broken))


def test_a_report_row_for_a_key_that_was_imported_is_reported(world, broken):
    report = broken / IMPORT_ROOT / "IMPORT.md"
    text = report.read_text()
    report.write_text(text.replace("| 1001 | NEC |", "| 1001 | NEC | 20DF10EF | `X` |\n| 1001 | NEC |", 1)
                      if "| 1001 | NEC |" in text else text + "\n| 1001 | NEC | 20DF10EF | `X` |\n")
    # the row sits under the last section, so the parser sees it only if it is in the skipped one
    text = report.read_text()
    section = "## Keys skipped, by reason"
    head, _, tail = text.partition(section)
    report.write_text(head + section + tail.replace(
        "| Remote | DB protocol | Hexcode | Label |\n|---|---|---|---|\n",
        "| Remote | DB protocol | Hexcode | Label |\n|---|---|---|---|\n| 1002 | NEC2 | FFFFFFFF | `X` |\n", 1))
    assert any("more time(s) than the dump has it" in p for p in _problems(world, broken))


@pytest.mark.parametrize("label", ["Keys imported", "Files written", "Key rows in the database"])
def test_a_report_total_that_disagrees_with_the_files_is_reported(world, broken, label):
    report = broken / IMPORT_ROOT / "IMPORT.md"
    text = report.read_text()
    report.write_text(re.sub(rf"^(\| {label} \| )([\d,]+)( \|)$",
                             lambda m: f"{m.group(1)}9{m.group(2)}{m.group(3)}", text, count=1, flags=re.M))
    assert any(f"IMPORT.md says {label}" in p for p in _problems(world, broken))


def test_a_difference_table_that_disagrees_with_the_files_is_reported(world, broken):
    report = broken / IMPORT_ROOT / "IMPORT.md"
    text = report.read_text()
    report.write_text(re.sub(r"^(\| SONY12 \| \d+ \| )(\d+)", lambda m: m.group(1) + "9" + m.group(2),
                             text, count=1, flags=re.M))
    assert any("difference table for SONY12" in p for p in _problems(world, broken))


def test_a_file_without_an_irblaster_citation_is_reported(world, broken):
    path = first_file(broken, "*-RC5.json")
    def change(doc):
        next(iter(doc["keys"].values()))["forms"][0]["source"] = "somewhere else"
    _edit(path, change)
    assert any("no one-form irblaster citation" in p for p in _problems(world, broken))


def test_an_unexplained_mismatch_makes_the_exit_status_one(world, tmp_path, capsys):
    def corrupt(proto, rows):
        if proto == "NECx1":
            for row in rows:
                row["pattern"][4] = 9999
    oracle = write_oracle(tmp_path / "oracle", corrupt)
    status = tool.main(["--checkout", str(world["checkout"]), "--oracle", str(oracle),
                        "--ledger", str(world["ledger"]), "--workers", "1"])
    assert status == 1 and "problems:" in capsys.readouterr().out
