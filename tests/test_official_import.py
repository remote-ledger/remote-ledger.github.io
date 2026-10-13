"""``rl import official`` (DESIGN D123 to D127): Marantz's command charts and Anthem's IR hex sheet,
their snapshots, the readers, and the committed tree."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.official import anthem, arcam, cambridge, hegel, jvc, kef, linn, marantz, oppo
from remote_ledger.official import importer as oi
from remote_ledger.official.common import MANIFEST, Report, load_snapshot
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / oi.SNAPSHOT


# --- the snapshot ------------------------------------------------------------------------------


def test_every_document_and_csv_is_the_one_the_manifest_pins():
    for maker, retrieved in (("marantz", "2026-10-08"), ("anthem", "2026-10-08"), ("oppo", "2026-10-09"), ("kef", "2026-10-13"),
                             ("jvc", "2026-10-13"), ("arcam", "2026-10-13"), ("cambridge", "2026-10-13"), ("hegel", "2026-10-13"), ("linn", "2026-10-13")):
        snap = load_snapshot(SNAPSHOT / maker)
        assert snap.maker == maker and snap.retrieved == retrieved
        assert snap.documents and snap.sheets
        for name, entry in snap.documents.items():
            assert entry["url"].startswith("https://") and entry["sha256"] and entry["bytes"] > 1000, name


def test_a_changed_document_or_csv_stops_the_import(tmp_path):
    directory = tmp_path / "marantz"
    (directory / "csv").mkdir(parents=True)
    doc, sheet = b"document", b"a,b\n1,2\n"
    (directory / "d.xls").write_bytes(doc)
    (directory / "csv" / "s.csv").write_bytes(sheet)
    manifest = {"maker": "marantz", "retrieved": "2026-10-08",
                "documents": {"d.xls": {"url": "https://x/", "sha256": hashlib.sha256(doc).hexdigest(), "bytes": 8}},
                "sheets": {"s.csv": {"document": "d.xls", "sheet": "S", "sha256": hashlib.sha256(sheet).hexdigest()}}}
    (directory / MANIFEST).write_text(json.dumps(manifest))
    assert load_snapshot(directory).sheet_of("d.xls", "S") == [["a", "b"], ["1", "2"]]
    (directory / "csv" / "s.csv").write_bytes(b"a,b\n1,3\n")
    with pytest.raises(ValidationError, match="not the file the manifest pins"):
        load_snapshot(directory)
    (directory / "csv" / "s.csv").write_bytes(sheet)
    (directory / "d.xls").unlink()
    with pytest.raises(ValidationError, match="unreadable"):
        load_snapshot(directory)
    with pytest.raises(ValidationError, match="cannot read the snapshot manifest"):
        load_snapshot(tmp_path / "nowhere")


# --- Marantz ---------------------------------------------------------------------------------------

PRONTO = "0000 0071 0000 0003 0020 0020 0040 0040 0020 0CCC"
CHART = [
    ["Marantz Remote Command Chart"] + [""] * 9,
    [""] * 10,
    [""] * 10,
    [""] * 10,
    ["", "", "", "Remote Code:", "", "", "For search", "AV Receiver", "Slim Line\nAV Receiver", ""],
    ["Zone", "", "Command Name", "System", "Command", "Extension", "", "SR1000", "NR2000\nNR2001", "FUNCTION"],
    ["Main", "Power", "POWER ON/OFF", "16", "12", "---", "16 12", "X", "X", "Power toggle"],
    ["", "", "POWER ON", "16", "12", "01", "16 12 01", "X", "---", "Power on"],
    ["", "", "VOL +", "16", "16", "---", "16 16", "X (*3)", "@", "Volume up"],
    ["", "", "ATT", "", "", "", "16 13", "X", "X", ""],
    ["", "", "", "16", "37", "43", "16 37 43", "X", "---", ""],
    ["", "", "Input 9", "99", "1", "---", "", "X", "X", ""],
    ["", "", "", "", "", "", "", "", "", ""],
    ["9/27/2012", "Added AV8801"] + [""] * 8,
]


def chart_with_hex() -> list[list[str]]:
    rows = [list(r) + [""] for r in CHART]
    rows[5][-1] = "Hex Code (Basic Commands Only)"
    rows[7][-1] = PRONTO
    return rows


def test_a_chart_is_its_models_by_column_and_its_commands_by_row():
    models, commands = marantz.parse_sheet(CHART)
    assert [(m.name, m.aliases, m.category) for m in models] == [
        ("SR1000", (), "AV Receiver"), ("NR2000", ("NR2001",), "Slim Line AV Receiver")]
    assert [(c.row, c.name, c.system, c.command, c.extension) for c in commands] == [
        (7, "POWER ON/OFF", "16", "12", "---"), (8, "POWER ON", "16", "12", "01"), (9, "VOL +", "16", "16", "---"),
        (11, "ATT", "16", "37", "43"),          # a command written on two lines takes the name of the first
        (12, "Input 9", "99", "1", "---")]
    assert commands[0].marks == {7: "X", 8: "X"} and commands[2].marks == {7: "X (*3)", 8: "@"}


def test_the_chart_gives_a_model_the_commands_it_marks_and_says_how_it_marked_them(tmp_path):
    from remote_ledger.official.common import Snapshot

    class Fake(Snapshot):
        def sheet_of(self, document, sheet):
            return chart_with_hex()

    snap = Fake("marantz", "2026-10-08", tmp_path, {d: {"sha256": "ab" * 32} for d in marantz.DOCUMENTS}, {})
    report = Report()
    files = dict(marantz.build(snap, {}, report))
    # two plain RC-5 commands outnumber the one Pronto string, so the RC-5 file has the bare model name
    plain = files["remotes/official/marantz/SR1000.json"]
    assert plain["protocol"] == {"name": "RC5", "carrierHz": 36000, "minSends": 1}
    assert list(plain["keys"]) == ["KEY_POWER_ON_SLASH_OFF", "KEY_VOL_PLUS"]
    vol = plain["keys"]["KEY_VOL_PLUS"]["forms"][0]
    assert (vol["type"], vol["device"], vol["function"], vol["confidence"]) == ("irp", 16, 16, "plausible")
    assert "the chart marks it 'X (*3)'" in vol["source"] and "read as RC5 device 16 function 16" in vol["source"]
    # the chart's own Pronto hex is a pronto form, carried verbatim, in a file of its own carrier
    own = files["remotes/official/marantz/SR1000__36.7_kHz.json"]
    assert own["model"] == "SR1000 [36.7 kHz]" and own["aliases"] == []
    assert own["protocol"] == {"carrierHz": 36683, "minSends": 1}
    on = own["keys"]["KEY_POWER_ON"]["forms"][0]
    assert (on["type"], on["hex"], on["confidence"]) == ("pronto", PRONTO, "plausible")
    assert "sheet 'AVR Commands' row 8 'POWER ON' (RC-5 16 12 01), the column of SR1000: the chart's own Pronto hex" in on["source"]
    assert on["source"].endswith("the chart's description: 'Power on'")
    # the shared column's second name is an alias, and a model gets only the commands its column marks
    sibling = files["remotes/official/marantz/NR2000.json"]
    assert sibling["aliases"] == ["NR2001"] and sibling["controls"] == ["Marantz NR2000", "Marantz NR2001"]
    assert list(sibling["keys"]) == ["KEY_POWER_ON_SLASH_OFF", "KEY_VOL_PLUS"]
    assert "the chart marks it '@'" in sibling["keys"]["KEY_VOL_PLUS"]["forms"][0]["source"]
    # an extension command with no hex is RC-5x (D226): the system is D, the command S and the extension F, in a file of its own
    rc5x = files["remotes/official/marantz/SR1000__RC-5x.json"]
    assert rc5x["model"] == "SR1000 [RC-5x]" and rc5x["protocol"] == {"name": "RC5x", "carrierHz": 36000, "minSends": 1}
    att = rc5x["keys"]["KEY_ATT"]["forms"][0]
    assert (att["type"], att["device"], att["subdevice"], att["function"], att["confidence"]) == ("irp", 16, 37, 43, "plausible")
    assert "(RC-5 16 37 43), the column of SR1000: RC-5x system 16 command 37 extension 43, read as RC5x device 16 subdevice 37 function 43" in att["source"]
    assert "NR2000__RC-5x" not in " ".join(files)             # NR2000's column does not mark it
    # a command with no RC-5 system is counted, once however many models list it
    assert dict(report.unrepresented) == {
        ("marantz", marantz.DOCUMENTS[-1], "no RC-5 system and command the ledger can send"): 1}


def test_an_rc5x_file_has_the_models_own_name_only_when_the_model_has_nothing_else_and_a_range_is_not_a_key(tmp_path):
    from remote_ledger.official.common import Snapshot

    chart = [list(r) for r in CHART[:6]] + [
        ["", "", "ATT", "16", "37", "43", "", "X", "X", ""],
        ["", "", "DIRECT VOLUME", "16", "111", "00-63", "", "X", "---", ""],
        ["", "", "TOO BIG", "16", "5", "64", "", "X", "X", ""],
        ["", "", "POWER", "16", "12", "---", "", "---", "X", ""],
    ]

    class Fake(Snapshot):
        def sheet_of(self, document, sheet):
            return chart

    snap = Fake("marantz", "2026-10-08", tmp_path, {d: {"sha256": "ab" * 32} for d in marantz.DOCUMENTS}, {})
    report = Report()
    files = dict(marantz.build(snap, {}, report))
    # SR1000 has only an RC-5x key, so that file is the bare model's; NR2000 has a plain RC-5 key too, so its RC-5x file says what it is
    only = files["remotes/official/marantz/SR1000.json"]
    assert only["protocol"]["name"] == "RC5x" and list(only["keys"]) == ["KEY_ATT"]
    assert "remotes/official/marantz/SR1000__RC-5x.json" not in files
    assert files["remotes/official/marantz/NR2000.json"]["protocol"]["name"] == "RC5"
    assert list(files["remotes/official/marantz/NR2000__RC-5x.json"]["keys"]) == ["KEY_ATT"]
    # a range of extensions is a command for each value, not a key; one past F:6 cannot be sent
    assert dict(report.unrepresented) == {
        ("marantz", marantz.DOCUMENTS[-1], "an RC-5x extension that is a range of values, a command for each"): 1,
        ("marantz", marantz.DOCUMENTS[-1], "no RC-5x extension the ledger can send"): 1}


def test_the_newest_workbook_that_lists_a_model_gives_its_file():
    # the 2013 chart lists AV8801 and so does the 2014 one; the committed file is the 2014 chart's
    doc = load(ROOT / "remotes" / "official" / "marantz" / "AV8801.json")
    sources = {f["source"].split("@")[0] for v in doc["keys"].values() for f in v["forms"]}
    assert sources == {"marantz.com archive-downloads/marantz-2014-ir-command-sheet.xls"}
    sr6013 = load(ROOT / "remotes" / "official" / "marantz" / "SR6013.json")
    assert {f["source"].split("@")[0] for v in sr6013["keys"].values() for f in v["forms"]} == {
        "marantz.com archive-downloads/marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls"}


def test_the_two_names_of_a_shared_column_are_one_model_and_its_alias():
    doc = load(ROOT / "remotes" / "official" / "marantz" / "SR8500.json")
    assert doc["aliases"] == ["SR7500"] and doc["controls"] == ["Marantz SR8500", "Marantz SR7500"]
    other = ROOT / "remotes" / "official" / "marantz" / "SR8500__RC-5.json"
    assert load(other)["aliases"] == []        # two files may not claim one alias (R15)


def rc5_of(hex_string: str) -> tuple[int, int] | None:
    """The system and command of the first RC-5 frame of a Pronto string whose half-bit is 0x20 cycles:
    the half-bit levels, then 14 Manchester bits (a 1 is space then mark), the field bit giving command bit 6."""
    words = [int(w, 16) for w in hex_string.split()]
    levels = [0]            # the frame's first bit is a space the string leaves out
    for mark, space in zip(words[4::2], words[5::2]):
        levels += [1] * round(mark / 0x20) + [0] * round(space / 0x20)
    bits = []
    for i in range(0, 28, 2):
        pair = tuple(levels[i:i + 2])
        if pair not in ((0, 1), (1, 0)):
            return None
        bits.append(1 if pair == (0, 1) else 0)
    system = int("".join(map(str, bits[3:8])), 2)
    command = int("".join(map(str, bits[8:14])), 2) | ((1 - bits[1]) << 6)
    return system, command


def test_marantzs_rc5_pronto_hex_decodes_to_the_system_and_command_beside_it():
    """The chart gives a command's RC-5 system and command in two columns and, for the basic commands,
    its Pronto hex in a third. They are two statements of one signal: decoding every 36 kHz RC-5 string
    of the tree from its bits finds the system and command the citation names. The strings with an
    extension byte (RC-5x) are a longer frame, carried verbatim and not decoded here."""
    matched, extension, wrong = 0, 0, []
    for path in sorted((ROOT / oi.IMPORT_ROOT / "marantz").glob("*.json")):
        for key, spec in load(path)["keys"].items():
            form = spec["forms"][0]
            if form["type"] != "pronto" or int(form["hex"].split()[1], 16) != marantz.RC5_WORD:
                continue
            cited = re.search(r"\(RC-5 (\d+) (\d+)( \d+)?\)", form["source"])
            if cited[3]:
                extension += 1
            elif rc5_of(form["hex"]) == (int(cited[1]), int(cited[2])):
                matched += 1
            else:
                wrong.append(f"{path.name}:{key}")
    assert wrong == [] and (matched, extension) == (1_640, 31)


DIRECT = [
    ["Marantz AV/Stereo Receiver: Direct Volume Commands"] + [""] * 6,
    [""] * 7,
    [""] * 7,
    ["Zone", "Volume", "Command", "", "", "SR1000", "NR2000\nNR2001"],
    ["MAIN", "0 dB", "16", "111", "00", "X", "X"],
    ["ZONE", "-1 dB", "16", "111", "01", "X", "---"],
    ["", "-\u221e dB", "16", "111", "59-63", "X(-\u221e)", "-"],
    ["MULTI", "0 dB", "16", "112", "00", "X", "X"],
    ["ZONE A", "1", "17", "64", "01", "S", "X/S"],
    ["", "2", "17", "64", "02", "", "@"],
    [""] * 7,
    ["Notes: Specifications subject to change without prior notice."] + [""] * 6,
]


def test_a_direct_sheet_is_its_models_by_column_and_a_row_a_value_in_a_zone_written_down_the_first_column():
    models, commands = marantz.parse_direct_sheet(DIRECT, "Direct Vol.")
    assert [(m.name, m.aliases) for m in models] == [("SR1000", ()), ("NR2000", ("NR2001",))]
    # the zone is the group of cells that last started, the first row of it included, and the notes row is no command
    assert [(c.row, c.name, c.triple) for c in commands] == [
        (5, "Direct volume MAIN ZONE 0 dB", "16 111 00"), (6, "Direct volume MAIN ZONE -1 dB", "16 111 01"),
        (7, "Direct volume MAIN ZONE -\u221e dB", "16 111 59-63"), (8, "Direct volume MULTI ZONE A 0 dB", "16 112 00"),
        (9, "Direct volume MULTI ZONE A 1", "17 64 01"), (10, "Direct volume MULTI ZONE A 2", "17 64 02")]
    assert commands[2].marks == {5: "X(-\u221e)", 6: "-"} and commands[4].marks == {5: "S", 6: "X/S"}


def test_the_direct_rows_a_column_marks_are_a_file_of_its_own_with_the_rc5x_triple_and_the_rows_that_are_ranges_are_counted(tmp_path):
    from remote_ledger.official.common import Snapshot

    document = marantz.DOCUMENTS[-1]

    class Fake(Snapshot):
        def sheet_of(self, doc, sheet):
            return DIRECT if sheet == "Direct Vol." else chart_with_hex()

    sheets = {"direct.csv": {"document": document, "sheet": "Direct Vol."}}
    snap = Fake("marantz", "2026-10-08", tmp_path, {d: {"sha256": "ab" * 32} for d in marantz.DOCUMENTS}, sheets)
    report = Report()
    files = dict(marantz.build(snap, {}, report))
    direct = files["remotes/official/marantz/SR1000__direct.json"]
    assert direct["model"] == "SR1000 [direct]" and direct["aliases"] == [] and direct["controls"] == ["Marantz SR1000"]
    assert direct["protocol"] == {"name": "RC5x", "carrierHz": 36000, "minSends": 1}
    # SR1000's column marks X, X, X(-inf), X, S, and not a blank: a mark that starts with X or S is a command the model has
    assert [(f["device"], f["subdevice"], f["function"]) for f in (v["forms"][0] for v in direct["keys"].values())] == [
        (16, 111, 0), (16, 111, 1), (16, 112, 0), (17, 64, 1)]
    assert list(direct["keys"])[:2] == ["KEY_DIRECT_VOLUME_MAIN_ZONE_0_DB", "KEY_DIRECT_VOLUME_MAIN_ZONE_MINUS_1_DB"]
    cited = direct["keys"]["KEY_DIRECT_VOLUME_MAIN_ZONE_0_DB"]["forms"][0]["source"]
    assert ("sheet 'Direct Vol.' row 5 'Direct volume MAIN ZONE 0 dB' (RC-5 16 111 00), the column of SR1000: RC-5x system 16 command 111 "
            "extension 0, read as RC5x device 16 subdevice 111 function 0") in cited
    assert "the chart marks it 'S'" in direct["keys"]["KEY_DIRECT_VOLUME_MULTI_ZONE_A_1"]["forms"][0]["source"]
    # NR2000 (and its alias) marks the rows 1 to 3 and the last two, but `---` and `-` are no
    other = files["remotes/official/marantz/NR2000__direct.json"]
    assert other["aliases"] == [] and other["controls"] == ["Marantz NR2000", "Marantz NR2001"]
    assert [(f["device"], f["subdevice"], f["function"]) for f in (v["forms"][0] for v in other["keys"].values())] == [
        (16, 111, 0), (16, 112, 0), (17, 64, 1), (17, 64, 2)]
    # the main chart's files are what they were, and the -inf row, whose extension is five values, is counted
    assert files["remotes/official/marantz/SR1000.json"]["protocol"]["name"] == "RC5"
    assert report.unrepresented[("marantz", document, "an RC-5x extension that is a range of values, a command for each")] == 1


def test_a_model_takes_each_direct_sheet_from_the_newest_workbook_that_lists_it_and_the_one_column_of_the_2015_workbook_is_all_of_its_models(tmp_path):
    from remote_ledger.official.common import Snapshot

    old, new = marantz.DOCUMENTS[0], marantz.DOCUMENTS[-1]
    older = [list(r) for r in DIRECT]
    older[5][2:5] = ["16", "99", "01"]          # the older workbook says something else for the second row

    class Fake(Snapshot):
        def sheet_of(self, doc, sheet):
            if sheet == "Direct Vol.":
                return older if doc == old else DIRECT
            return chart_with_hex()

    sheets = {"a.csv": {"document": old, "sheet": "Direct Vol."}, "b.csv": {"document": new, "sheet": "Direct Vol."}}
    snap = Fake("marantz", "2026-10-08", tmp_path, {d: {"sha256": "ab" * 32} for d in marantz.DOCUMENTS}, sheets)
    files = dict(marantz.build(snap, {}, Report()))
    forms = [v["forms"][0] for v in files["remotes/official/marantz/SR1000__direct.json"]["keys"].values()]
    assert (16, 99, 1) not in [(f["device"], f["subdevice"], f["function"]) for f in forms]
    assert all(f["source"].startswith("marantz.com archive-downloads/" + new) for f in forms)

    # `FY18AVR` is every model of the workbook it is in
    fy18 = [list(r) for r in DIRECT]
    fy18[3][5:] = ["FY18AVR"]
    fy18 = [r[:6] for r in fy18]

    class Fake2(Fake):
        def sheet_of(self, doc, sheet):
            return fy18 if sheet == "Direct Vol." else chart_with_hex()

    only = {"b.csv": {"document": new, "sheet": "Direct Vol."}}
    snap = Fake2("marantz", "2026-10-08", tmp_path, {d: {"sha256": "ab" * 32} for d in marantz.DOCUMENTS}, only)
    files = dict(marantz.build(snap, {}, Report()))
    assert {"remotes/official/marantz/SR1000__direct.json", "remotes/official/marantz/NR2000__direct.json"} <= set(files)


def test_the_committed_direct_files_cite_the_row_they_read_and_one_jp1_upgrades_key_is_the_charts_first_row():
    """The 44 files of §60: RC5x, one irp form a key, whose citation names the row's system, command and extension and whose form
    carries them as D, S and F. And an independent corroboration the ledger happens to hold: the Marantz NR1402 upgrade of the JP1
    forum has a key it calls `Direct VOLUME`, and its RC-5x signal is exactly the chart's first row of `Direct Vol.`, system 16,
    command 111, extension 0 (the one JP1 key of the 1,567 distinct triples of the direct files)."""
    files = keys = 0
    for path in sorted((ROOT / oi.IMPORT_ROOT / "marantz").glob("*__direct.json")):
        doc = load(path)
        files += 1
        assert doc["protocol"] == {"name": "RC5x", "carrierHz": 36000, "minSends": 1} and doc["model"].endswith(" [direct]"), path.name
        for key, spec in doc["keys"].items():
            keys += 1
            (form,) = spec["forms"]
            cited = re.search(r"\(RC-5 (\d+) (\d+) (\d+)\)", form["source"])
            assert (form["type"], form["confidence"]) == ("irp", "plausible"), (path.name, key)
            fields = tuple(int(str(form[name]), 0) for name in ("device", "subdevice", "function"))
            assert fields == tuple(int(g) for g in cited.groups()), (path.name, key)
            assert fields[0] <= 31 and fields[1] <= 127 and fields[2] <= 63
    assert (files, keys) == (44, 15_995)
    jp1 = load(ROOT / "remotes" / "jp1" / "Marantz" / "Audio-Marantz_NR1402-rc5x.json")
    (form,) = [v["forms"][0] for v in jp1["keys"].values() if v["label"] == "Direct VOLUME"]
    assert (int(form["device"], 0), int(form["subdevice"], 0), int(form["function"], 0)) == (16, 111, 0)


def test_every_rc5x_key_of_the_tree_says_the_chart_row_it_reads_and_the_forms_are_the_rows_fields():
    """The RC-5x keys are the chart's rows that have an extension and no hex (D226): a file of the protocol ``RC5x``, a key per
    row, whose citation names the row's system, command and extension and whose form carries them as D, S and F. 75 models, 5,589
    keys, from 1,013 of the 1,034 rows (the 21 others are 18 ranges and 3 with no system number, below)."""
    files = keys = 0
    for path in sorted((ROOT / oi.IMPORT_ROOT / "marantz").glob("*.json")):
        doc = load(path)
        if path.name.endswith("__direct.json"):
            continue                         # the direct-access files are RC5x too, and have a test of their own below
        if doc["protocol"].get("name") != "RC5x":
            assert all("subdevice" not in f for v in doc["keys"].values() for f in v["forms"]), path.name
            continue
        files += 1
        assert doc["protocol"] == {"name": "RC5x", "carrierHz": 36000, "minSends": 1}, path.name
        for key, spec in doc["keys"].items():
            keys += 1
            (form,) = spec["forms"]
            cited = re.search(r"\(RC-5 (\d+) (\d+) (\d+)\)", form["source"])
            assert (form["type"], form["confidence"]) == ("irp", "plausible"), (path.name, key)
            fields = tuple(int(str(form[name]), 0) for name in ("device", "subdevice", "function"))   # a file writes them as 0x..
            assert fields == tuple(int(g) for g in cited.groups()), (path.name, key)
            assert fields[0] <= 31 and fields[1] <= 127 and fields[2] <= 63
    assert (files, keys) == (75, 5_589)


def test_the_power_toggle_has_the_signal_marantz_lists_for_system_16_command_12():
    doc = load(ROOT / "remotes" / "official" / "marantz" / "SR5004__RC-5.json")
    form = doc["keys"]["KEY_POWER_ON_SLASH_OFF"]["forms"][0]
    assert form["type"] == "pronto" and rc5_of(form["hex"]) == (16, 12)
    assert "(RC-5 16 12)" in form["source"] and "Power the unit On / Standby (Toggle)" in form["source"]


def test_the_one_pronto_that_is_a_zero_duration_is_reported_and_not_imported():
    report_text = (ROOT / "remotes" / "official" / "IMPORT.md").read_text(encoding="utf-8")
    assert "SURROUND MODE (Back)" in report_text and "repeat[70] is 0" in report_text


# --- Anthem ----------------------------------------------------------------------------------------


def nec_pronto(d: int, s: int, f: int) -> str:
    """A Pronto classic NEC frame like the sheet's own: 0x155/0xAA lead, 0x15 marks, 0x15 or 0x40 spaces."""
    bits = [(b >> i) & 1 for b in (d, s, f, ~f & 0xFF) for i in range(8)]
    words = ["0000", "006D", "0022", "0002", "0155", "00AA"]
    for bit in bits:
        words += ["0015", "0040" if bit else "0015"]
    words += ["0015", "05ED", "0155", "0055", "0015", "0E47"]
    return " ".join(words)


def test_nec_bytes_are_read_from_a_pronto_hex_bit_by_bit():
    assert anthem.pronto_bytes(nec_pronto(0x85, 0x6A, 0x97)) == (0x85, 0x6A, 0x97, 0x68)
    assert anthem.pronto_bytes("0000 006D 0000 0001 0155 00AA") is None
    assert anthem.pronto_bytes("not hex") is None


def test_the_groups_of_the_layout_sheet():
    rows = [["MRX x40:", "AVM 70, AVM 90, MRX 1140"], ["MRX x10:", "MRX 710, MRX 310"], ["", ""]]
    assert anthem.groups_of(rows) == {"x40": ["AVM 70", "AVM 90", "MRX 1140"], "x10": ["MRX 710", "MRX 310"]}


def test_an_anthem_row_the_pronto_hex_contradicts_is_untested_and_says_so():
    doc = load(ROOT / "remotes" / "official" / "anthem" / "MRX_720.json")
    good, bad = doc["keys"]["KEY_ON"]["forms"][0], doc["keys"]["KEY_INPUT_21"]["forms"][0]
    assert good["confidence"] == "plausible" and "decodes to the same device, sub device and function" in good["source"]
    assert bad["confidence"] == "untested" and "the sheet contradicts itself here" in bad["source"]
    assert (good["device"], good["subdevice"], good["function"]) == ("0x85", "0x6A", "0x97")
    # the data column wins: 0xC6, where the sheet's Pronto repeats input 11's 0xA1
    assert bad["function"] == "0xC6"


def test_inputs_21_to_30_are_only_for_the_x20_group_as_the_sheets_note_says():
    keys = {m: set(load(ROOT / "remotes" / "official" / "anthem" / f"{m}.json")["keys"])
            for m in ("AVM_60", "MRX_1120", "AVM_70", "MRX_310")}
    assert "KEY_INPUT_30" in keys["AVM_60"] and "KEY_INPUT_30" in keys["MRX_1120"]
    assert "KEY_INPUT_30" not in keys["AVM_70"] and "KEY_INPUT_30" not in keys["MRX_310"]
    assert "KEY_INPUT_20" in keys["AVM_70"] and "KEY_INPUT_20" in keys["MRX_310"]


# --- the committed tree --------------------------------------------------------------------------------


def test_the_committed_import_is_exactly_what_the_pinned_snapshots_yield(tmp_path):
    """SPEC R19.5: re-running the import over the pinned snapshots reproduces every file byte for byte."""
    root = tmp_path / "repo"
    (root / "remotes").mkdir(parents=True)
    oi.write_import(root, SNAPSHOT)
    committed = {p.relative_to(ROOT / oi.IMPORT_ROOT).as_posix(): p.read_bytes()
                 for p in (ROOT / oi.IMPORT_ROOT).rglob("*") if p.is_file() and p.name != "README.md"}
    fresh = {p.relative_to(root / oi.IMPORT_ROOT).as_posix(): p.read_bytes()
             for p in (root / oi.IMPORT_ROOT).rglob("*") if p.is_file()}
    assert fresh == committed


def test_the_committed_official_import_keeps_r19():
    """Conditions 2 and 3 over every form: README and report beside it, one form a key, Plausible
    or Untested, and a citation that names a document the manifest pins, by hash."""
    assert (ROOT / oi.IMPORT_ROOT / "README.md").is_file() and (ROOT / oi.IMPORT_ROOT / oi.REPORT).is_file()
    hashes, dates, snapshots = {}, {}, {}
    for maker in ("marantz", "anthem", "oppo", "kef", "jvc", "arcam", "cambridge", "hegel", "linn"):
        manifest = json.loads((SNAPSHOT / maker / MANIFEST).read_text(encoding="utf-8"))
        for name, entry in manifest["documents"].items():
            hashes[name], dates[name], snapshots[name] = entry["sha256"], manifest["retrieved"], entry.get("snapshot")
    shape = re.compile(r"^(?:marantz\.com archive-downloads|anthemav\.com \(storage\.googleapis\.com/sandbox1-anthemav/an\)"
                       r"|download\.oppodigital\.com/\w+|kef\.com IR code sheets|jvc\.com D-ILA remote emulation guide|arcam\.co\.uk custom installation notes|cambridgeaudio\.com IR code documents|support\.hegel\.com RC-5 code tables|docs\.linn\.co\.uk RC-5 codes)/(\S+)@([0-9a-f]{8}) \(retrieved (\d{4}-\d{2}-\d{2})"
                       r"(?:, from the Wayback Machine snapshot (\d{14}))?\) (?:sheet '[^']+' row \d+|page \d+ row \d+|page \d+ '|page \d+ table \d+ row '|page \d+ row ')")
    files = sorted((ROOT / oi.IMPORT_ROOT).rglob("*.json"))
    assert len(files) > 180
    bad = []
    for path in files:
        for key, spec in load(path)["keys"].items():
            if len(spec["forms"]) != 1:
                bad.append(f"{path.name}:{key}")
            for form in spec["forms"]:
                m = shape.match(form.get("source", ""))
                ok = (form["confidence"] in ("plausible", "untested") and "verifiedBy" not in form
                      and form["type"] in ("pronto", "irp") and m is not None
                      and hashes.get(m[1], "").startswith(m[2]) and m[3] == dates[m[1]] and m[4] == snapshots[m[1]])
                if not ok:
                    bad.append(f"{path.name}:{key}")
    assert bad == []


# --- Oppo --------------------------------------------------------------------------------------------------------


def oppo_rows(device: str = "49B6", keys=(("POWER", "1A", "26.0"), ("Vol +", "13", "19.0"))) -> list[list[str]]:
    rows = [["Product", "BDP-103/105 Remote Code Set 1", "", "", ""], ["Protocol", "NEC or NEC1", "", "", ""], ["", "HEX", "DEC", "", ""],
            ["Custom Code", device[:2], "", "", ""], ["Device", device, "", "", ""], [""] * 5,
            ["Key", "Hex Key Data", "Decimal Key Cmd", "Pronto TSU3000 Code", "Pronto Classic Hex Code"]]
    for name, hex_, dec in keys:
        d, s, f = int(device[:2], 16), int(device[2:], 16), int(hex_, 16)
        rows.append([name, hex_, dec, f"900A 006D 0000 0001 {device} {f:02X}{~f & 0xFF:02X}", nec_pronto(d, s, f)])
    return rows


def test_an_oppo_sheet_is_a_head_and_a_table_of_keys():
    sheet = oppo.parse_sheet("Remote Code 1", oppo_rows())
    assert (sheet.number, sheet.product, sheet.custom, sheet.device) == (1, "BDP-103/105 Remote Code Set 1", "49", "49B6")
    assert [(k.row, k.name, k.hex, k.decimal) for k in sheet.keys] == [(8, "POWER", "1A", "26.0"), (9, "Vol +", "13", "19.0")]
    with pytest.raises(ValueError, match="no Product, Custom Code and Device lines"):
        oppo.parse_sheet("Remote Code 2", [["Key", "Hex Key Data"]])
    assert oppo.key_names(list(oppo.parse_sheet("Remote Code 1", oppo_rows(keys=(("Vol +", "13", "19"), ("VOL+", "14", "20")))).keys)) == {
        8: "KEY_VOL_PLUS", 9: "KEY_VOL_PLUS_2"}


def test_a_row_is_checked_against_every_other_statement_of_the_same_code():
    sheet = oppo.parse_sheet("Remote Code 1", oppo_rows())
    key = sheet.keys[0]
    triple = oppo.check_key(sheet, key)
    assert triple == (0x49, 0xB6, 0x1A) and oppo.disagreements(sheet, key, triple) == ([], [])
    typo = oppo.Key(key.row, key.name, key.hex, key.decimal, key.tsu[:-4] + "1AE4", key.classic)
    assert oppo.disagreements(sheet, typo, triple) == ([], ["its TSU3000 string is 900A 006D 0000 0001 49B6 1AE4, not 900A 006D 0000 0001 49B6 1AE5"])
    wrong = oppo.Key(key.row, key.name, key.hex, "27.0", key.tsu, nec_pronto(0x49, 0xB6, 0x1B))
    signal, other = oppo.disagreements(sheet, wrong, triple)
    assert signal == ["its classic Pronto hex decodes to device 73, sub device 182, function 27"] and other == ["its decimal column says 27.0"]
    assert oppo.check_key(sheet, oppo.Key(1, "X", "ZZ", "", "", "")) == ("the key's hex is not a byte",)


def test_every_signal_oppo_published_says_the_code_of_its_key_and_only_one_derived_string_is_wrong():
    """The classic Pronto hex is the signal. Decoded bit by bit it says the key's code on every row of the three
    workbooks (519 rows), and the one place another column is wrong is a TSU3000 string, in the `Picture Adj.` row of
    seven of the nine sheets."""
    snap = load_snapshot(SNAPSHOT / "oppo")
    rows = other = 0
    wrong = []
    for document, _models in oppo.DOCUMENTS:
        for sheet in oppo.SHEETS:
            code_set = oppo.parse_sheet(sheet, snap.sheet_of(document, sheet))
            for key in code_set.keys:
                triple = oppo.check_key(code_set, key)
                assert isinstance(triple[0], int), (document, sheet, key)
                signal, others = oppo.disagreements(code_set, key, triple)
                rows += 1
                assert signal == [], (document, sheet, key.name)
                if others:
                    other += 1
                    wrong.append((key.name, others[0].split(" is ")[0]))
    assert rows == 519 and other == 7 and {w for w in wrong} == {("Picture Adj.", "its TSU3000 string")}


def test_the_models_of_a_workbook_are_the_ones_its_name_gives_and_the_newest_workbook_has_a_shared_model():
    oppo_dir = ROOT / oi.IMPORT_ROOT / "oppo"
    sources = {p.stem: load(p)["keys"]["KEY_POWER"]["forms"][0]["source"] for p in oppo_dir.glob("*.json")}
    assert sorted(sources) == sorted(f"{m}{s}" for m in ("BDP-103", "BDP-103D", "BDP-105", "UDP-203", "UDP-205") for s in ("", "__code_set_2", "__code_set_3"))
    assert "BDP-103_BDP-103D_Remote_Code_v1.2.xls" in sources["BDP-103"] and "BDP-103_BDP-105_Remote_Code_v1.1.xls" in sources["BDP-105"]
    assert "UDP203/UDP-203_Remote_Code_v1.2.xls@" in sources["UDP-205"] and "snapshot 20251211233638" in sources["UDP-205"]
    for suffix, device in (("", (0x49, 0xB6)), ("__code_set_2", (0x61, 0x9E)), ("__code_set_3", (0x43, 0xBC))):
        form = load(oppo_dir / f"UDP-203{suffix}.json")["keys"]["KEY_POWER"]["forms"][0]
        assert tuple(int(form[k], 16) for k in ("device", "subdevice", "function")) == (*device, 0x1A) and form["confidence"] == "plausible"
    doc = load(oppo_dir / "UDP-203__code_set_2.json")
    assert (doc["model"], doc["controls"], doc["protocol"]) == ("UDP-203 [code set 2]", ["Oppo UDP-203"], {"name": "NEC1", "carrierHz": 38000, "minSends": 1})


# --- KEF -------------------------------------------------------------------------------------------------------


KEF_HEAD = ["IR Codes for Universal Remote Control", "LS50 Wireless II work with Universal programmable remote control provided that remote",
            "control manufacturers have implemented KEF IR command codes in their remote controls.",
            "The following tables list the command codes required.", "Code Format:", "NEC", "Factory Code:", "0x01", "#", "Functions",
            "Function codes"]


def kef_rows(*lines: str) -> list[list[str]]:
    return [[line] for line in (*KEF_HEAD, *lines)]


def test_a_kef_page_is_a_head_and_a_numbered_table_of_rows_with_one_or_two_codes():
    """A number line starts a row; what is above its first code is its function (a long name may wrap onto two lines); a row
    may have two codes (the sheets' HDMI); the table ends at the first line after a code that is not a number (a footnote); and
    a row numbered out of order (the XIO's 18th is printed 28) is kept in its place."""
    table = kef.parse_page(1, kef_rows(
        "1", "Power On/Off Toggle ", "0x40", "2", "Source: HDMI ", "0x88", "0x8A", "28", "Favourite (customized in", "KEF Connect app)",
        "0x99", "*Older firmware versions do not incorporate these function codes", "9", "not a row", "0x11"))
    assert (table.product, table.code_format, table.factory, table.caption) == ("LS50 Wireless II", "NEC", "0x01", "")
    assert [(e.number, e.position, e.name, e.codes) for e in table.entries] == [
        (1, 1, "Power On/Off Toggle", ("0x40",)), (2, 2, "Source: HDMI", ("0x88", "0x8a")),
        (28, 3, "Favourite (customized in KEF Connect app)", ("0x99",))]
    two = kef.parse_page(2, [["Table 2: speakers made after LS50W06256 use the following 19"], ["codes to control 19 functions.*"],
                             ["Code Format:"], ["NEC"], ["Factory Code:"], ["0x01"], ["#"], ["Functions"], ["Function codes"],
                             ["1"], ["Power"], ["0x40"]])
    assert two.caption == "Table 2: speakers made after LS50W06256 use the following 19 codes to control 19 functions.*"
    with pytest.raises(ValueError, match="no 'Code Format:' line"):
        kef.parse_page(1, [["1"], ["Power"], ["0x40"]])
    with pytest.raises(ValueError, match="has no function above it"):
        kef.parse_page(1, kef_rows("1", "0x40"))


def test_kefs_bytes_are_the_ledgers_nec1_in_the_other_bit_order():
    """The sheet's factory code 0x01 is the device 0x80 and its complement 0x7F, and a function code is the function reversed:
    0x40 is 0x02, 0xA0 is 0x05, 0x58 is 0x1A (the IR Blaster's KEF remote has exactly these, D244)."""
    table = kef.parse_page(1, kef_rows("1", "Power", "0x40"))
    assert [kef.read_triple(table, c) for c in ("0x40", "0xa0", "0x58", "0x99")] == [
        (0x80, 0x7F, 0x02), (0x80, 0x7F, 0x05), (0x80, 0x7F, 0x1A), (0x80, 0x7F, 0x99)]
    assert kef.rev8(0xD2) == 0x4B and kef.rev8(kef.rev8(0x2A)) == 0x2A
    other = kef.Table(1, "X", "", "RC5", "0x01", ())
    assert kef.read_triple(other, "0x40") == "the code format is RC5, not NEC"
    assert kef.key_names(["Mute", "Mute", "Source: HDMI"]) == ["KEY_MUTE", "KEY_MUTE_2", "KEY_SOURCE_HDMI"]


def kef_files() -> dict[str, dict]:
    return {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "kef").glob("*.json"))}


def test_the_six_kef_files_are_the_six_sheets_tables_and_every_key_is_the_nec1_frame_of_one_code():
    """One file to a product, 124 keys in all: the LS50 Wireless's and the LSX's 19, the LS50 Wireless II's, the LSX II's and the
    LS60 Wireless's 22 (21 rows, the HDMI row having two codes) and the XIO's 20. Every key is NEC1 with device 0x80 and sub device
    0x7F; the rows the older sheets call Next, Source toggle and Previous are the newer ones' Forward, Next source and Backward."""
    files = kef_files()
    assert {m: len(d["keys"]) for m, d in files.items()} == {
        "LS50_Wireless": 19, "LS50_Wireless_II": 22, "LS60_Wireless": 22, "LSX": 19, "LSX_II": 22, "XIO": 20}
    for doc in files.values():
        assert doc["manufacturer"] == "KEF" and doc["protocol"]["name"] == "NEC1" and doc["aliases"] == []
        for spec in doc["keys"].values():
            (form,) = spec["forms"]
            assert (form["type"], form["confidence"], form["device"], form["subdevice"]) == ("irp", "plausible", "0x80", "0x7F")
    xio = {s["label"]: s["forms"][0]["function"] for s in files["XIO"]["keys"].values()}
    assert xio["Favourite (customized in KEF Connect app)"] == "0x99" and xio["EQ1 (customized in KEF Connect app)"] == "0xEE"
    assert xio["EQ2 (customized in KEF Connect app)"] == "0xAA" and "Source: AUX" not in xio
    hdmi = [(s["label"], s["forms"][0]["function"]) for s in files["LSX_II"]["keys"].values() if "HDMI" in s["label"]]
    assert hdmi == [("Source: HDMI", "0x11"), ("Source: HDMI (second code)", "0x51")]


def test_the_kef_sheets_agree_with_each_other_and_a_code_means_one_function_in_all_of_them():
    """Six documents written over four years say the same thing: grouping every key by its function code, the labels differ only
    in case and spacing, except the three pairs the older sheets (LS50 Wireless, LSX) and the newer ones name differently."""
    names: dict[str, dict[str, set[str]]] = {}
    for model, doc in kef_files().items():
        for spec in doc["keys"].values():
            label = re.sub(r"[^a-z0-9]", "", spec["label"].lower())
            names.setdefault(spec["forms"][0]["function"], {}).setdefault(label, set()).add(model)
    differing = {code: sorted(labels) for code, labels in names.items() if len(labels) > 1}
    assert differing == {"0x1A": ["nextsource", "sourcetoggle"], "0x4A": ["forward", "next"], "0x4B": ["backward", "previous"]}
    assert len(names) == 26


def test_the_ir_blasters_kef_remote_has_eight_codes_and_all_eight_are_in_every_kef_sheet():
    """An independent source: the IR Blaster database's KEF remote (a SwiftRemote capture or dump) holds eight keys of NEC1 device
    0x80, and **each is a code of each of the six sheets**, read with the bit order this import uses (0x40 is 0x02): the bit order
    is what this checks, since the sheets' 0x40 and the IR Blaster's 0x02 are the same byte."""
    doc = load(ROOT / "remotes" / "irblaster" / "KEF" / "6889-NEC1.json")
    codes = {(f["device"], f["subdevice"], f["function"]) for spec in doc["keys"].values() for f in spec["forms"]}
    assert len(codes) == 8 and {c[0] for c in codes} == {"0x80"}
    for model, kef_doc in kef_files().items():
        ours = {(f["device"], f["subdevice"], f["function"]) for spec in kef_doc["keys"].values() for f in spec["forms"]}
        assert codes <= ours, model


# --- JVC -------------------------------------------------------------------------------------------------------


def jvc_hex(device: int, function: int, gap: int = 0x02BD) -> str:
    """The guide's long hex for a code, written from the frame (a lead-in once, then sixteen bits least significant first and
    a stop mark) and not from the reader."""
    words = [0, 0x6D, 1, 17, 0x141, 0xA0]
    for byte in (device, function):
        for bit in range(8):
            words += [0x14, 0x3C if byte >> bit & 1 else 0x14]
    return " ".join(f"{w:04X}" for w in words + [0x14, gap])


JVC_3D_FORMAT = ("0000 006D 0001 0011 0141 00A0 0014 003C 0014 003C 0014 0014 0014 0014 0014 003C 0014 003C "
                 "0014 003C 0014 0014 0014 0014 0014 003C 0014 003C 0014 0014 0014 003C 0014 0014 0014 003C "
                 "0014 003C 0014 02BD")


def test_the_jvc_hex_is_a_lead_in_and_sixteen_bits_and_must_say_the_code_the_entry_states():
    """The guide's first entry, ``3D Format``, says device 115 and function 214 and its hex is JVC's frame, the device byte and
    the function byte least significant bit first; the final space varies with the bits (the frame is padded to a period)."""
    assert jvc.decode(JVC_3D_FORMAT) == (115, 214)
    assert JVC_3D_FORMAT == jvc_hex(115, 214)
    assert jvc.decode(jvc_hex(115, 24, gap=0x0336)) == (115, 24)
    assert jvc.decode("") == "the entry has no Pronto hex"
    assert jvc.decode(JVC_3D_FORMAT.replace("0014 003C 0014 0014", "0014 003C 011014 0014", 1)) == \
        "the guide's Pronto text is damaged ('011014' is not four hex digits)"
    assert jvc.decode(JVC_3D_FORMAT.replace("0000 006D", "0000 0071")) == "the Pronto hex is not the guide's one pair and seventeen"
    assert jvc.decode(JVC_3D_FORMAT.replace("0141 00A0", "0141 00A1")) == "the lead-in is not 0141 00A0"
    assert jvc.decode(JVC_3D_FORMAT.replace("0014 003C 0014 003C 0014 0014", "0014 003C 0014 003D 0014 0014", 1)) == \
        "a bit is not 0014 0014 or 0014 003C"
    assert jvc.decode(JVC_3D_FORMAT[:-9] + "0015 02BD") == "the stop mark is not 0014"


def jvc_rows(*lines: tuple[int, str]) -> list[list[str]]:
    return [[str(page), text] for page, text in lines]


def test_a_jvc_entry_is_a_title_a_device_code_line_and_the_hex_after_it_however_damaged():
    """The cover's models are page 1's lines; an entry's title is the lines before its ``Device Code`` line (a title may wrap),
    and the lines after it that look like hex are its Pronto text, damaged or not, so a damaged line is not the next
    entry's title; page footers are not text, and the last page's notice is kept."""
    rows = jvc_rows(
        (1, "DLA-HD350 / DLA-HD750"), (1, "DLA-X70R / DLA-X90R"), (1, "Infrared Remote Control Command Emulation"),
        (2, "Page 2 of 37"), (2, "Aspect – 16:9 (HD350/750)"), (2, "Device Code: 115 (0x73) Function: 38 (0x26)"),
        (2, "0000 006D 0001 0011 0141 00A0"), (2, "0014 003C 00010 006D"),             # a damaged second line
        (2, "Clear Motion Drive - Cycles through: Off/ Mode1"), (2, "(X70/X90)"),
        (2, "Device Code: 115 (0x73) Function: 138 (0x8A)"), (2, "0000 006D 0001 0011"),
        (3, "Page 3 of 37"), (3, "Brightness –"), (3, "Device Code: 115 (0x73) Function: 123 (0x7B)"),
        (3, "This document may be distributed freely, as long as the author and copyright information remain intact."))
    guide = jvc.parse_pages(rows)
    assert guide.models == ("DLA-HD350", "DLA-HD750", "DLA-X70R", "DLA-X90R")
    assert [(e.page, e.title, e.function, e.pronto, e.consistent) for e in guide.entries] == [
        (2, "Aspect – 16:9 (HD350/750)", 38, "0000 006D 0001 0011 0141 00A0 0014 003C 00010 006D", True),
        (2, "Clear Motion Drive - Cycles through: Off/ Mode1 (X70/X90)", 138, "0000 006D 0001 0011", True),
        (3, "Brightness –", 123, "", True)]
    assert guide.notice.startswith("This document may be distributed freely")
    same = jvc.parse_pages(jvc_rows((2, "Gamma"), (2, "Device Code: 115 (0x73) Function: 100 (0x64)")))
    assert same.entries[0].consistent
    off = jvc.parse_pages(jvc_rows((2, "Gamma"), (2, "Device Code: 115 (0x73) Function: 100 (0x65)")))
    assert not off.entries[0].consistent


def test_a_jvc_title_is_names_with_the_models_each_applies_to_and_a_note_per_group():
    """``HD350/750/RS10/20`` is four models (a number takes the letters before it), ``X70`` and ``X90`` are the cover's ``X70R`` and
    ``X90R``; a parenthesis that is not a list of models (``(PC)``) is part of the name; a text after a list starts another name
    of the same code; a dash or an ``on`` in a parenthesis gives a note; no list is every model."""
    assert jvc.expand("HD350/750/RS10/20") == ("HD350", "HD750", "RS10", "RS20")
    assert jvc.expand("X7/X9/X70/X90/RS40/50") == ("X7", "X9", "X70R", "X90R", "RS40", "RS50")
    assert jvc.expand("PC") is None and jvc.expand("toggles display of RNR/MNR") is None
    assert jvc.parse_title("Aspect (PC) – Auto (X3/X7/RS40)") == (
        jvc.Alternative("Aspect (PC) – Auto", (jvc.Group(("X3", "X7", "RS40"), ""),)),)
    two = jvc.parse_title("Anamorphic – Off (X3/X7) Vertical Stretch – Off (HD350/750/RS10)")
    assert [(a.name, a.groups[0].models) for a in two] == [
        ("Anamorphic – Off", ("X3", "X7")), ("Vertical Stretch – Off", ("HD350", "HD750", "RS10"))]
    notes = jvc.parse_title("Picture Tone Blue – (X7/X9/RS50/60 – Film Mode Only) (X70/X90/RS55/65 - All Modes)")
    assert len(notes) == 1 and notes[0].name == "Picture Tone Blue –"
    assert [(g.models, g.note) for g in notes[0].groups] == [
        (("X7", "X9", "RS50", "RS60"), "Film Mode Only"), (("X70R", "X90R", "RS55", "RS65"), "All Modes")]
    pc = jvc.parse_title("Auto Align (PC input on HD750/950/X7)")
    assert pc == (jvc.Alternative("Auto Align", (jvc.Group(("HD750", "HD950", "X7"), "PC input"),)),)
    assert jvc.parse_title("Contrast –") == (jvc.Alternative("Contrast –", ()),)
    # the label a key shows: a trailing description, a triangle and the sentence after `If` are dropped, a dash between words
    # is a colon, a dash at the end is a minus
    assert [jvc.tidy(n) for n in (
        "Power – On", "Power – Off (send twice with short delay between to switch off)", "Menu (On/Off toggle)", "Cursor Up ▲",
        "Brightness –", "Aspect (PC) – Auto", "Lens Aperture – If Lens Aperture Gauge is not displayed – displays gauge.",
        "Lens Aperture + If Lens Aperture Gauge is not displayed", "Input – HDMI-1", "(PC)")] == [
        "Power: On", "Power: Off", "Menu", "Cursor Up", "Brightness –", "Aspect (PC): Auto", "Lens Aperture –", "Lens Aperture +",
        "Input: HDMI-1", "(PC)"]
    assert jvc.key_names(["Gamma", "Gamma", "Aspect – 16:9"]) == ["KEY_GAMMA", "KEY_GAMMA_2", "KEY_ASPECT_16_9"]


def jvc_files() -> dict[str, dict]:
    return {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "jvc").glob("*.json"))}


def test_the_22_jvc_files_are_the_covers_models_and_the_three_damaged_entries_are_untested():
    """One file for each model on the cover, 3,047 keys; each is the ledger's ``JVC`` with device 0x73. The guide's 211 entries
    are 209 codes (functions 35 and 36 are listed twice, once for each of two groups of models), and the three whose Pronto
    text is damaged (``Color Management - Custom 3``, ``Picture Mode - Cinema 3`` and ``Stage``, functions 99, 102 and 103) are
    Untested in every model that has them, 54 keys, and say so; every other key is Plausible."""
    from remote_ledger.jp1 import importer as jp

    files = jvc_files()
    assert {m: len(d["keys"]) for m, d in files.items()} == {
        "DLA-HD350": 91, "DLA-HD550": 101, "DLA-HD750": 98, "DLA-HD950": 105, "DLA-HD990": 107, "DLA-RS10": 85, "DLA-RS15": 94,
        "DLA-RS20": 93, "DLA-RS25": 104, "DLA-RS35": 104, "DLA-RS40": 148, "DLA-RS45": 161, "DLA-RS50": 175, "DLA-RS55": 187,
        "DLA-RS60": 175, "DLA-RS65": 187, "DLA-X3": 149, "DLA-X30": 160, "DLA-X7": 175, "DLA-X70R": 186, "DLA-X9": 175,
        "DLA-X90R": 187}
    untested: Counter = Counter()
    codes = set()
    for doc in files.values():
        assert doc["manufacturer"] == "JVC" and doc["protocol"] == jp.Importer.block("JVC")
        for spec in doc["keys"].values():
            (form,) = spec["forms"]
            assert form["type"] == "irp" and form["device"] == "0x73" and "subdevice" not in form
            codes.add(form["function"])
            if form["confidence"] == "untested":
                untested[int(form["function"], 0)] += 1
                assert "damaged" in form["source"] and "rests on the stated code alone" in form["source"]
            else:
                assert form["confidence"] == "plausible" and "decodes to the same" in form["source"]
    assert len(codes) == 209 and untested == {99: 14, 102: 18, 103: 22}


def test_the_jvc_guides_codes_are_the_ones_other_sources_projector_remotes_send_under_the_same_names():
    """An independent check from another source. Nine remotes of the ledger send the ``JVC`` protocol with device 115 (the IR
    Blaster database's JVC projector remotes and the JP1 forum's ``DLA-RS2`` and ``X590R`` upgrades): **300 of their 317 keys
    are a function the guide lists (94.6%)**, and where one has a name it agrees (power on is 5 and off 6, the cursor keys 1, 2,
    54 and 52, the picture modes 104 to 111, the inputs 112 and 113)."""
    guide: dict[int, set[str]] = {}
    for doc in jvc_files().values():
        for spec in doc["keys"].values():
            guide.setdefault(int(spec["forms"][0]["function"], 0), set()).add(spec["label"])
    remotes = total = hit = 0
    for path in sorted((ROOT / "remotes").rglob("*.json")):
        if "official" in path.parts or path.name in ("IMPORT.md",):
            continue
        doc = load(path)
        if not isinstance(doc, dict) or doc.get("protocol", {}).get("name") != "JVC":
            continue
        functions = [int(f["function"], 0) for spec in doc["keys"].values() for f in spec["forms"]
                     if f.get("type") == "irp" and int(f["device"], 0) == 115]
        if functions:
            remotes += 1
            total += len(functions)
            hit += sum(1 for fn in functions if fn in guide)
    assert (remotes, total, hit) == (9, 317, 300)
    for function, start in ((5, "Power: On"), (6, "Power: Off"), (1, "Cursor Up"), (2, "Cursor Down"), (54, "Cursor Left"),
                            (52, "Cursor Right"), (47, "OK"), (3, "Back"), (29, "Hide"), (46, "Menu"), (104, "Picture Mode: Cinema 2"),
                            (105, "Picture Mode: Cinema 1"), (111, "Picture Mode: THX"), (112, "Input: HDMI-1"),
                            (113, "Input: HDMI-2"), (77, "Input: Component")):
        assert any(label.startswith(start) for label in guide[function]), (function, start)


def test_the_ledgers_jvc_signal_is_the_guides_hex_to_within_two_cycles_of_the_final_space():
    """The guide's hex sends the lead-in once and then the sixteen bits, a stop mark and a space, repeated; the ledger's ``JVC``
    sends the lead-in and the first frame as its intro and the frame again as its repeat. **For each of the 206 distinct codes
    whose hex can be read, the ledger's compiled lead-in, sixteen bits and stop mark are the guide's, pair for pair**; only the
    final space of the repeat differs, by one carrier cycle (143 codes) or two (63): the guide pads a frame to the period the
    ledger's JVC does, to within 0.1 ms."""
    from remote_ledger.import_common import probe

    snap = load_snapshot(SNAPSHOT / "jvc")
    guide = jvc.parse_pages(snap.sheet_of(jvc.DOCUMENT, jvc.SHEET))
    seen: set[int] = set()
    differences: Counter = Counter()
    for entry in guide.entries:
        if jvc.decode(entry.pronto) != (entry.device, entry.function) or entry.function in seen:
            continue
        seen.add(entry.function)
        form = {"id": "primary.irp", "type": "irp", "device": entry.device, "function": entry.function,
                "confidence": "plausible", "source": "probe"}
        remote, _ = probe("remotes/official", dict(jvc.PROTOCOL), "K", [form])
        ledger = [int(w, 16) for w in remote.compile_group("K", "primary").split()]
        theirs = [int(w, 16) for w in entry.pronto.split()]
        assert ledger[:4] == [0, 0x6D, 18, 17], entry.function
        pairs, intro = ledger[4:], 2 * ledger[2]
        repeat, lead_and_frame = pairs[intro:], theirs[4:]
        assert pairs[:2] == lead_and_frame[:2] and repeat[:-1] == lead_and_frame[2:-1], entry.function
        differences[repeat[-1] - lead_and_frame[-1]] += 1
    assert len(seen) == 206 and dict(differences) == {1: 143, 2: 63}


# --- Arcam -----------------------------------------------------------------------------------------------------


def test_an_arcam_function_name_may_say_which_models_it_is_for():
    """``(not AVR5, AVR10)`` is every model but those, ``(AVR450/750 only)`` is those two (a number takes the letters before it);
    a footnote asterisk is dropped and any other parenthesis stays in the name."""
    assert arcam.expand("AVR450/750") == ("AVR450", "AVR750") and arcam.expand("AVR5, AVR10") == ("AVR5", "AVR10")
    assert arcam.expand("Speaker trim") is None
    assert arcam.restriction("Zone 2 Mute (not AVR5, AVR10)") == ("Zone 2 Mute", (), ("AVR5", "AVR10"))
    assert arcam.restriction("Zone 2 Vol-(not AVR5, AVR10)") == ("Zone 2 Vol-", (), ("AVR5", "AVR10"))
    assert arcam.restriction("DAB (AVR450/750 only)") == ("DAB", ("AVR450", "AVR750"), ())
    assert arcam.restriction("Eject (Speaker trim)*") == ("Eject (Speaker trim)", (), ())
    assert arcam.restriction("Auro Native (not AVR5)") == ("Auro Native", (), ("AVR5",))


def test_an_arcam_rows_decimal_and_hex_must_say_one_code_and_the_tables_own_typos_are_tolerated():
    """``16-12`` and ``0x10 - 0x0C`` are one code; the tables write ``0x10 - 4C`` (no prefix on the second) and ``0X17`` (a capital
    X) once each, which still say the code; a decimal that is not ``system-command`` is not a row."""
    assert arcam.read_code("16-12*", "0x10 - 0x0C*") == (16, 12, True, "16-12* / 0x10 - 0x0C*")
    assert arcam.read_code("16-76", "0x10 - 4C")[:3] == (16, 76, True)
    assert arcam.read_code("23-123", "0X17 - 0x7B")[:3] == (23, 123, True)
    assert arcam.read_code("16 - 48", "0x10 - 0x30")[:3] == (16, 48, True)
    assert arcam.read_code("16-12", "0x10 - 0x0D")[:3] == (16, 12, False)
    assert arcam.read_code("16-12", "")[:3] == (16, 12, False)
    assert arcam.read_code("Decimal", "Hexadecimal") is None


def test_the_three_layouts_of_arcams_tables_are_read_as_rows_with_a_system_code_and_a_command():
    """The function tables (the CSV row is the page, the table's number and the cells), the Solo's ``System Code`` table whose
    system code is written on the first row of a group, and the Solo Mini's lines after ``IR remote commands``."""
    header = ["1", "1", "Function", "RC5 code [system- command]", "RC5 code (Data1 - Data2)"]
    rows = arcam.function_rows([
        ["1", "9", "Function", "something else", "x"], ["1", "9", "Standby", "16-12", "0x10 - 0x0C"],          # not an RC-5 table
        header, ["1", "1", "", "Decimal", "Hexadecimal"], ["1", "1", "Standby", "16-12*", "0x10 - 0x0C*"],
        ["1", "1", "DAB (AVR450/750 only)", "16-72", "0x10 - 0x48"], ["1", "1", "Bad", "16-1", "0x10 - 0x02"],
        ["1", "2", "Other table", "16-2", "0x10 - 0x02"]])
    assert [(r.function, r.system, r.command, r.agrees, r.only) for r in rows] == [
        ("Standby", 16, 12, True, ()), ("DAB", 16, 72, True, ("AVR450", "AVR750")), ("Bad", 16, 1, False, ())]
    assert arcam.digit_doubt("Number 3", 3) == arcam.digit_doubt("5", 5) == arcam.digit_doubt("Standby", 12) == ""
    assert "the digit 3 and its command is 2" in arcam.digit_doubt("Number 3", 2)
    assert "the digit 7" in arcam.digit_doubt("CD Number 7", 6) and "the digit 4" in arcam.digit_doubt("Tuner Number 4", 0)
    solo = arcam.system_rows([
        ["1", "1", "System Code", "Command", "Command Code", "Comments"], ["1", "1", "16", "Select TV", "0", ""],
        ["1", "1", "", "Select AV", "2", ""], ["2", "1", "System Code", "Command", "Command Code", "Comments"],
        ["2", "1", "17", "Number 0", "0", "For Tuner & CD"], ["2", "1", "", "Number 1", "1", ""]])
    assert [(r.function, r.system, r.command) for r in solo] == [
        ("Select TV", 16, 0), ("Select AV", 16, 2), ("Number 0", 17, 0), ("Number 1", 17, 1)]
    mini = arcam.line_rows([["13", "16-0 not yet"], ["14", "IR remote commands"], ["14", "System code 16"], ["14", "16-0 Source TV"],
                            ["14", "Decimal Code Command"], ["15", "17-52 Source DAB/AM"]])
    assert [(r.function, r.system, r.command) for r in mini] == [("Source TV", 16, 0), ("Source DAB/AM", 17, 52)]


def arcam_files() -> dict[str, dict]:
    return {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "arcam").glob("*.json"))}


def test_the_19_arcam_files_are_the_models_the_documents_title_and_the_restrictions_leave_out_what_they_say():
    """One file for each model a document's title lists, 1,816 keys, 1,815 Plausible because every row's decimal and hex agree and
    one Untested (the Solo's ``Number 3``, which the table gives the command of ``Number 2``); 272 distinct codes, systems 16, 17,
    20, 23 and 25. The AVR5 and AVR10 lack the 21 Zone 2 rows the AVR5-41 document says they do not have, the AVR380 the two
    ``AVR450/750 only`` DAB rows."""
    files = arcam_files()
    assert {m: len(d["keys"]) for m, d in files.items()} == {
        "AV40": 120, "AV41": 120, "AVR10": 99, "AVR11": 120, "AVR20": 120, "AVR21": 120, "AVR30": 120, "AVR31": 120, "AVR380": 125,
        "AVR450": 127, "AVR5": 99, "AVR750": 127, "CDS50": 37, "SA10": 30, "SA20": 30, "SA30": 59, "Solo": 80, "Solo_Mini": 112,
        "UDP411": 51}
    codes = set()
    for doc in files.values():
        assert doc["manufacturer"] == "Arcam" and doc["protocol"]["name"] == "RC5"
        for spec in doc["keys"].values():
            (form,) = spec["forms"]
            assert form["type"] == "irp" and "subdevice" not in form
            assert form["confidence"] == (
                "untested" if (doc["model"], spec["label"], form["device"], form["function"]) == ("Solo", "Number 3", "0x14", "0x02")
                else "plausible")
            codes.add((form["device"], form["function"]))
    assert len(codes) == 272 and {c[0] for c in codes} == {"0x10", "0x11", "0x14", "0x17", "0x19"}
    labels = {m: [s["label"] for s in d["keys"].values()] for m, d in files.items()}
    assert not any("Zone 2 Power" in label for label in labels["AVR5"] + labels["AVR10"])
    assert "Zone 2 Power On" in labels["AV41"] and "DAB" in labels["AVR450"] and "DAB" not in labels["AVR380"]
    assert files["AVR450"]["keys"]["KEY_DAB"]["forms"][0]["source"].endswith("the table says it is for AVR450, AVR750 only")


def test_the_other_sources_arcam_rc5_remotes_send_the_codes_arcams_tables_list():
    """An independent check from other sources: eight Arcam RC-5 remotes of the ledger (five from the IR Blaster database, three
    JP1 upgrades) hold 401 keys, and **337 of them (84.0%) are a code some table of Arcam's lists**, with the meaning agreeing
    where it is plain (volume plus and minus 16 and 17, mute 13, power 12, the standby on and off 124 and 123, the digits). The
    rest are other products' and other generations' codes: system 16 command 53 is Select FM on a Solo and Play on an AVR."""
    ours: dict[tuple[int, int], set[str]] = {}
    for doc in arcam_files().values():
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            ours.setdefault((int(form["device"], 0), int(form["function"], 0)), set()).add(spec["label"])
    remotes = total = hit = 0
    for path in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in path.parts or "arcam" not in path.parent.name.lower():
            continue
        doc = load(path)
        if doc.get("protocol", {}).get("name") != "RC5":
            continue
        codes = [(int(f["device"], 0), int(f["function"], 0)) for spec in doc["keys"].values() for f in spec["forms"]
                 if f.get("type") == "irp"]
        remotes += 1
        total += len(codes)
        hit += sum(1 for c in codes if c in ours)
    assert (remotes, total, hit) == (8, 401, 337)
    for code, wanted in (((16, 16), "Increase volume (+)"), ((16, 17), "Decrease volume (-)"), ((16, 13), "Mute"), ((16, 12), "Standby"),
                         ((16, 124), "Standby On"), ((16, 123), "Standby Off"), ((17, 5), "Tuner Number 5")):
        assert any(label.startswith(wanted.split(" (")[0]) for label in ours[code]), (code, wanted)


# --- Cambridge Audio ---------------------------------------------------------------------------------------------


def test_cambridges_two_layouts_are_read_as_four_bytes_or_as_a_function_code_with_a_name_for_each_product():
    """The AXR85's table gives a label and four bytes (the address, its complement, the function, its complement); the remotes list's
    header says ``NEC: 49 B6`` and a row is a function code and what each product calls the button, ``N/A`` where it has none. A
    row whose bytes do not complement is read and marked, not guessed at."""
    rows = cambridge.byte_rows([["1", "1", "Remote Button", "IR Code"], ["1", "1", "standby", "C0 3F 06 F9"],
                                ["1", "1", "Mute", "C0 3F 00 FE"], ["1", "1", "", "C0 3F 01 FE"]])
    assert [(r.label, r.device, r.subdevice, r.function, r.complements) for r in rows] == [
        ("standby", 0xC0, 0x3F, 0x06, True), ("Mute", 0xC0, 0x3F, 0x00, False)]
    columns, notes = cambridge.column_rows([
        ["1", "1", "NEC: 49 B6", "752BD", "CXU", "CXUHD"], ["1", "1", "Backlight", "N/A", "Yes", "Yes"],
        ["1", "1", "20 DF", "N/A", "Mute", "N/A"], ["1", "1", "4C B3", "Volume Up", "Volume Up", "N/A"],
        ["2", "1", "NEC: 49 B6", "752BD", "CXU", "CXUHD"], ["2", "1", "28 D7", "N/A", "Darbee", "HDR"],
        ["2", "1", "22 DC", "3D", "N/A", "N/A"]])
    assert [(r.product, r.label, r.function, r.complements, r.page) for r in columns] == [
        ("CXU", "Mute", 0x20, True, 1), ("752BD", "Volume Up", 0x4C, True, 1), ("CXU", "Volume Up", 0x4C, True, 1),
        ("CXU", "Darbee", 0x28, True, 2), ("CXUHD", "HDR", 0x28, True, 2), ("752BD", "3D", 0x22, False, 2)]
    assert len(notes) == 1 and "Backlight" in notes[0] and "752BD N/A, CXU Yes, CXUHD Yes" in notes[0]


def cambridge_files() -> dict[str, dict]:
    return {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "cambridge").glob("*.json"))}


def test_the_five_cambridge_files_are_one_for_each_product_the_two_documents_list():
    """AXR85 and AXR100 share a table (23 keys each); the remotes list gives the 752BD 52, the CXU 52 and the CXUHD 48 (an ``N/A``
    is a button the product has not), 198 keys, 197 Plausible and one Untested: the CXUHD column names two codes ``Subtitle``
    (57 A8 and 53 AC, which is ``Repeat`` on the other two products), and the second is the doubtful one."""
    files = cambridge_files()
    assert {m: len(d["keys"]) for m, d in files.items()} == {"752BD": 52, "AXR100": 23, "AXR85": 23, "CXU": 52, "CXUHD": 48}
    for doc in files.values():
        assert doc["manufacturer"] == "Cambridge Audio" and doc["protocol"]["name"] == "NEC1"
        for spec in doc["keys"].values():
            (form,) = spec["forms"]
            assert form["type"] == "irp" and form["device"] in ("0xC0", "0x49") and form["subdevice"] in ("0x3F", "0xB6")
            assert (form["device"], form["subdevice"]) in (("0xC0", "0x3F"), ("0x49", "0xB6"))
    doubtful = [(m, k) for m, d in files.items() for k, s in d["keys"].items() if s["forms"][0]["confidence"] == "untested"]
    assert doubtful == [("CXUHD", "KEY_SUBTITLE_2")]
    assert files["CXUHD"]["keys"]["KEY_SUBTITLE"]["forms"][0]["function"] == "0x57"
    assert files["CXUHD"]["keys"]["KEY_SUBTITLE_2"]["forms"][0]["function"] == "0x53"
    assert files["CXU"]["keys"]["KEY_REPEAT"]["forms"][0]["function"] == "0x53"
    assert "KEY_MUTE" in files["CXU"]["keys"] and "KEY_MUTE" not in files["752BD"]["keys"] and "KEY_3D" in files["752BD"]["keys"]
    assert files["AXR85"]["keys"].keys() == files["AXR100"]["keys"].keys()


def test_every_axr85_code_is_in_the_ir_blasters_axr85_remote_under_the_same_name():
    """The independent check: the IR Blaster database's remote 320 (``CAMBRIDGE AUDIO | AXR85``) holds 46 keys and **all 23 of the
    table's codes**, as the same device, subdevice and function, 15 under the same name (``MENU``, ``PRESET 1/5``, ``D1``) and
    the other 8 in other words for the same key (``POWER`` for ``standby``, ``VOL+`` for ``Volume Up``, the skip arrows, ``AM/FM``
    for ``FM/AM``): no code has a different meaning. The 752BD, CXU and CXUHD share the address 49 B6 with Oppo's players and 43
    of the CXU's 52 function codes are in Oppo's tables, **but under another name in 41 of the 43**: Cambridge's remote is its
    own table, so Oppo's is no evidence for it and is not counted."""
    ours = {}
    for spec in cambridge_files()["AXR85"]["keys"].values():
        form = spec["forms"][0]
        ours[(int(form["device"], 0), int(form["subdevice"], 0), int(form["function"], 0))] = spec["label"]
    theirs = {}
    for spec in load(ROOT / "remotes/irblaster/CAMBRIDGE_AUDIO/320-NEC1.json")["keys"].values():
        for form in spec["forms"]:
            theirs[(int(form["device"], 0), int(form["subdevice"], 0), int(form["function"], 0))] = spec["label"]
    assert len(theirs) == 46 and set(ours) <= set(theirs) and len(ours) == 23
    squash = lambda text: "".join(c for c in text.lower() if c.isalnum())      # noqa: E731
    same = [label for code, label in ours.items() if squash(label) == squash(theirs[code])]
    assert len(same) == 15, same          # the other eight are other words for the same key, read one by one
    oppo = {}
    for path in sorted((ROOT / oi.IMPORT_ROOT / "oppo").glob("*.json")):
        for spec in load(path)["keys"].values():
            for form in spec["forms"]:
                if (form["device"], form["subdevice"]) == ("0x49", "0xB6"):
                    oppo.setdefault(int(form["function"], 0), set()).add(squash(spec["label"]))
    cxu = [(squash(s["label"]), int(s["forms"][0]["function"], 0)) for s in cambridge_files()["CXU"]["keys"].values()]
    shared = [(name, fn) for name, fn in cxu if fn in oppo]
    assert len(shared) == 43 and sum(1 for name, fn in shared if name in oppo[fn]) == 2


# --- Hegel -------------------------------------------------------------------------------------------------------

M = "<merged>"
FRAME = "0000 0073 0000 000C 0020 0020 0040 0040 0040 0020 0020 0020 0020 0020 0020 0020 0020 0020 0020 0020 0020 0020 0020 0020 0020 0040 0020 0CA4"


def test_an_rc5_frame_in_pronto_is_read_as_its_system_command_and_toggle_bit():
    """The first press of Hegel's P20 ``BAL1`` is system 16 command 1 with the toggle bit 0, the ``(alt)`` row of its ``Volume Up``
    is system 16 command 16 with the toggle bit 1; a frame whose durations are not whole half-bits, or that is not a Manchester
    frame, or whose header is not a Pronto one, is none."""
    assert hegel.decode_rc5(FRAME) == (16, 1, 0)
    document = next(d for d in hegel.DOCUMENTS if d.file == "Hegel_P20_P30_IR_codes_v2.pdf")
    table = hegel.parse(oi.load_snapshot(SNAPSHOT / "hegel").sheet_of(document.file, "flow"), document)
    volume = next(r for r in table.rows if r.function == "Volume Up")
    assert hegel.decode_rc5(volume.hex) == (16, 16, 0) and hegel.decode_rc5(volume.alt) == (16, 16, 1)
    assert volume.hex.split()[4:] != volume.alt.split()[4:]
    assert hegel.decode_rc5(FRAME.replace("0040 0040 0040", "0041 0040 0040")) is None
    assert hegel.decode_rc5(FRAME.replace("0020 0020 0040 0040 0040", "0020 0020 0020 0020 0020", 1)) is None       # not Manchester
    assert hegel.decode_rc5("0000 0073 0001 000C") is None and hegel.decode_rc5("nope") is None


def test_hegel_names_that_one_function_is_called_in_two_documents_are_synonyms_and_the_rest_are_contradictions():
    assert hegel.same_name("BAL", "Balanced") and hegel.same_name("XLR (1)", "BAL") and hegel.same_name("HT / An2", "Analog 2")
    assert hegel.same_name("Optical1", "Optical 1") and hegel.same_name("ECO", "Power") and hegel.same_name("Net", "Network")
    assert hegel.same_name("Volume Up", "volume up") and hegel.same_name("Coax1", "Coaxial")
    assert not hegel.same_name("Net", "Analog 2") and not hegel.same_name("USB", "HT / An2") and not hegel.same_name("An1", "XLR 2 (H590)")


def test_a_hegel_page_is_read_as_its_title_its_system_sections_its_rows_with_their_hex_and_their_alt_twins():
    """A table has a ``System 16 (Preamp 1)`` row, a header, and rows ``Function | Command | Descripton``; the hex is on the row's line;
    ``16 (alt)`` is the twin of the row above (in the command cell or, as the tables' typo has it, the function cell); ``N/A`` is no
    command; ``(H590)`` and ``(P30 only)`` limit a row to a model the title names."""
    doc = hegel.Document("d.pdf", ("H390", "H590"))
    rows = [
        ["1", "text", "48", "Hegel H390, H590 RC-5 code table"], ["1", "text", "69", "v1"], ["1", "text", "81", "07.09.2020"],
        ["1", "row", "1", "104", "System 16 (Preamp 1)", M, M, M], ["1", "text", "111", "HEX-Long"],
        ["1", "row", "1", "111", "Function", "Command", "Descripton", "<block>"],
        ["1", "text", "119", FRAME], ["1", "row", "1", "119", "XLR (1)", "1", "Source select", M],
        ["1", "text", "127", FRAME], ["1", "row", "1", "127", "Volume Up", "16", "Volume control", M],
        ["1", "text", "134", FRAME], ["1", "row", "1", "134", "", "16 (alt)", "Volume control (alt)", M],
        ["1", "text", "142", FRAME], ["1", "row", "1", "142", "XLR 2 (H590)", "0", "Source select", M],
        ["1", "row", "1", "150", "Network", "N/A", "Source select", M],
        ["1", "text", "158", FRAME], ["1", "row", "1", "158", "IN -", "70", "x", M], ["1", "text", "166", FRAME], ["1", "row", "1", "166", "70 (alt)", M, "x (alt)", M],
        ["1", "row", "2", "175", "System 19 (Preamp 2)", M, M, M], ["1", "row", "2", "183", "Function", "Command", "Descripton", M],
        ["1", "row", "2", "191", "Display", "15", "Display on/off", M], ["1", "row", "2", "199", "", M, M, M],
    ]
    table = hegel.parse(rows, doc)
    assert (table.title, table.models, table.date, table.blank) == ("H390, H590", ("H390", "H590"), "07.09.2020", 1)
    assert [(r.system, r.function, r.command, r.only, bool(r.hex), r.alt_row, bool(r.alt)) for r in table.rows] == [
        (16, "XLR (1)", 1, (), True, False, False), (16, "Volume Up", 16, (), True, True, True),
        (16, "XLR 2", 0, ("H590",), True, False, False), (16, "Network", None, (), False, False, False),
        (16, "IN -", 70, (), True, True, True), (19, "Display", 15, (), False, False, False)]
    assert table.rows[2].written == "XLR 2 (H590)" and table.rows[0].heading == "System 16 (Preamp 1)"


def hegel_files() -> dict[str, dict]:
    return {load(p)["model"]: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "hegel").glob("*.json"))}


def test_the_hegel_files_are_one_for_each_product_and_the_h590s_older_table_is_kept_apart_as_questionable():
    """15 files, 318 keys: 14 products (295 Plausible keys) and ``H590 (2019 table)_questionable`` (23 Untested): the 2019 table of the
    H590 is the Roest/H190/H360/H90 table, and the 2020 table of the H390 and H590 gives other names to 8 of the 22 codes they share."""
    files = hegel_files()
    assert {m: len(d["keys"]) for m, d in files.items()} == {
        "H120": 27, "H190": 28, "H360": 23, "H390": 26, "H590": 28, "H590 (2019 table)_questionable": 23, "H90": 22, "H95": 27,
        "HD12": 14, "HD25": 15, "HD30": 22, "Mohican": 17, "P20": 11, "P30": 12, "Röst": 23}
    for model, doc in files.items():
        assert doc["manufacturer"] == "Hegel" and doc["protocol"] == {"name": "RC5", "carrierHz": 36000, "minSends": 1}
        tiers = {s["forms"][0]["confidence"] for s in doc["keys"].values()}
        assert tiers == ({"untested"} if model.endswith("_questionable") else {"plausible"}), model
        for spec in doc["keys"].values():
            assert spec["forms"][0]["type"] == "irp" and int(spec["forms"][0]["function"], 0) <= 127
    systems = {m: {int(s["forms"][0]["device"], 0) for s in d["keys"].values()} for m, d in files.items()}
    assert systems["H190"] == {16, 19} and systems["HD30"] == {19} and systems["Mohican"] == {20} and systems["P20"] == {16}
    report = (ROOT / oi.IMPORT_ROOT / oi.REPORT).read_text(encoding="utf-8")
    figure = lambda what: int(re.search(rf"\| hegel: {what} \| ([\d,]+) \|", report)[1].replace(",", ""))
    assert [figure(w) for w in ("files", "keys", "H590: codes Hegel_H590_IR_Codes.pdf shares with the newer document",
                                "H590: of them named differently", "H190: of them named differently")] == [15, 318, 22, 8, 0]
    questionable = files["H590 (2019 table)_questionable"]
    assert all("QUESTIONABLE, kept as the document writes it" in s["forms"][0]["source"] for s in questionable["keys"].values())
    assert questionable["keys"]["KEY_BAL"]["forms"][0]["function"] == "0x01" and files["H590"]["keys"]["KEY_XLR_1"]["forms"][0]["function"] == "0x01"
    assert files["H590"]["keys"]["KEY_ANALOG_2"]["forms"][0]["function"] == "0x03" and questionable["keys"]["KEY_NET"]["forms"][0]["function"] == "0x03"


def test_hegels_restrictions_and_merged_documents_give_each_product_its_own_rows():
    files = hegel_files()
    label = lambda model: {s["label"] for s in files[model]["keys"].values()}
    assert "XLR 2" in label("H590") and "Analog 3" in label("H590") and not {"XLR 2", "Analog 3"} & label("H390")
    assert "ECO" in label("P30") and "ECO" not in label("P20") and len(files["P30"]["keys"]) == len(files["P20"]["keys"]) + 1
    # the H190 has the 2020 table's rows and the 2019 table's DAC+, which the 2020 table has not; its Power is the 2019 table's ECO
    assert "DAC+" in label("H190") and "DAC+" not in label("H95")
    power = files["H190"]["keys"]["KEY_POWER"]["forms"][0]["source"]
    assert "Hegel_H95_H120_H190_IR_codes_v2.pdf" in power and "calls it 'ECO' (Low Power Toggle)" in power
    # two functions of one document with one code are two keys: the HD25's IN + / INPUT and DAC+ are both command 32
    hd25 = files["HD25"]["keys"]
    assert hd25["KEY_IN_PLUS_SLASH_INPUT"]["forms"][0]["function"] == hd25["KEY_DAC_PLUS"]["forms"][0]["function"] == "0x20"


def test_every_hegel_pronto_hex_decodes_to_its_row_and_is_the_ledgers_own_rc5_with_the_toggle_bit_zero():
    """The independent check (D262): 67 rows of the three 2020 tables give their code in Pronto hex; **all 67 decode to the row's system
    and command**, all with the toggle bit 0, **and are the ledger's own compile of the row's code word for word except the last one**,
    the gap after the frame, which is 7 or 8 carrier cycles shorter in Hegel's; the 24 ``(alt)`` rows are the same codes with the toggle bit
    1."""
    from remote_ledger.import_common import probe

    snapshot = oi.load_snapshot(SNAPSHOT / "hegel")
    cache: dict[tuple[int, int], list[str]] = {}
    rows = twins = 0
    gaps = Counter()
    for document in hegel.DOCUMENTS:
        table = hegel.parse(snapshot.sheet_of(document.file, "flow"), document)
        for row in table.rows:
            if row.command is None or not row.hex:
                continue
            assert hegel.decode_rc5(row.hex) == (row.system, row.command, 0), row
            if (row.system, row.command) not in cache:
                form = {"id": "primary.irp", "type": "irp", "device": row.system, "function": row.command, "confidence": "plausible",
                        "source": "probe"}
                cache[(row.system, row.command)] = probe("remotes/official", hegel.PROTOCOL, "K", [form])[0].compile_group("K", "primary").split()
            ours, theirs = cache[(row.system, row.command)], row.hex.split()
            assert ours[:-1] == theirs[:-1]
            gaps[int(ours[-1], 16) - int(theirs[-1], 16)] += 1
            rows += 1
            if row.alt:
                assert hegel.decode_rc5(row.alt) == (row.system, row.command, 1)
                twins += 1
    assert (rows, twins) == (67, 24) and dict(gaps) == {8: 34, 7: 33}


# --- Linn --------------------------------------------------------------------------------------------------------


def test_a_linn_cell_is_a_label_with_notes_and_a_reserved_one_is_the_function_in_brackets():
    """``Zero / (Direct source selection)^1`` is ``Zero`` with a note, ``[Direct Source PHONO] / Linn DSM - 1st Analogue input / ...``
    ``Direct Source PHONO``, ``Reserved (REM019) / [Audio Adjust: Balance +]`` that function reserved for a Linn remote, a stray
    bracket is tolerated, and ``.`` or a reserved cell with no function is no key."""
    assert linn.parse_cell("Zero / (Direct source selection)^1") == ("Zero", "", ["(Direct source selection)"])
    assert linn.parse_cell("[Direct Source PHONO] / Linn DSM - 1st Analogue input / Akurate Kontrol/1 - PHONO") == (
        "Direct Source PHONO", "", ["Linn DSM - 1st Analogue input", "Akurate Kontrol/1 - PHONO"])
    assert linn.parse_cell("Reserved (REM019) / [Audio Adjust: Balance +]") == ("Audio Adjust: Balance +", "REM019", [])
    assert linn.parse_cell("Reserved (REM020) / [Direct Source Programmable 1] / (Held Function: Set Programmable Source 1)") == (
        "Direct Source Programmable 1", "REM020", ["(Held Function: Set Programmable Source 1)"])
    assert linn.parse_cell("Direct Source TUNER] / Linn DS/DSM - RADIO") == ("Direct Source TUNER", "", ["Linn DS/DSM - RADIO"])
    assert linn.parse_cell("Reserved (REM019) / .") is None and linn.parse_cell(".") is None and linn.parse_cell("") is None
    assert linn.parse_cell("Reserved (REM021) / [*]") == ("*", "REM021", [])


def test_a_linn_table_has_device_columns_whose_hex_and_decimal_agree_and_rows_that_are_codes():
    devices, rows = linn.parse_table([["Code", "Amplifier Address / 0x10 (16)", "CD Address / 0x14 (20)"],
                                      ["0x00 (0)", "Zero", "Zero"], ["0x7E (126)", "Power Off", "."]])
    assert devices == [("Amplifier", 16), ("CD", 20)] and rows == [(2, 0, ["Zero", "Zero"]), (3, 126, ["Power Off", "."])]
    for bad in ([["Code", "Amplifier Address / 0x10 (17)"]], [["Code", "Amplifier 0x10"]],
                [["Code", "Amplifier Address / 0x10 (16)"], ["0x7E (127)", "x"]]):
        with pytest.raises(ValueError):
            linn.parse_table(bad)
    pronto = [["1", "text", "12", "Linn DS/DSM Pronto HEX codes"],
              ["1", "row", "1", "25", "0x00 (0)", "Zero (Direct source selection)1", "Device Code: 10 Function: 00 Linn DS/DSM Pronto codes " + FRAME],
              ["1", "row", "1", "41", "0x0C (12)", "Power (Toggle)", FRAME], ["1", "row", "1", "50", "0x0D (13)", "x", "no hex"]]
    assert linn.hex_rows(pronto) == {0: (FRAME, "Zero (Direct source selection)1"), 12: (FRAME, "Power (Toggle)")}


def linn_files() -> dict[str, dict]:
    return {load(p)["model"]: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "linn").glob("*.json"))}


def test_the_three_linn_files_are_the_three_addresses_of_the_table_116_keys_and_one_doubtful():
    """DS/DSM Amplifier (address 16, 51 keys), CD (20, 35) and DVD (25, 30). The one Untested key is the amplifier's ``Power Off``
    (126): the PDF's Pronto hex for it decodes to command 127. 128 rows, 117 cells with something in them, and one of those is
    ``Reserved (REM019)`` and nothing else, which is no key."""
    files = linn_files()
    assert {m: len(d["keys"]) for m, d in files.items()} == {"DS/DSM Amplifier": 51, "DS/DSM CD": 35, "DS/DSM DVD": 30}
    for model, doc in files.items():
        assert doc["manufacturer"] == "Linn" and doc["controls"] == ["Linn DS", "Linn DSM"]
        assert doc["protocol"] == {"name": "RC5", "carrierHz": 36000, "minSends": 1}
    systems = {m: {int(s["forms"][0]["device"], 0) for s in d["keys"].values()} for m, d in files.items()}
    assert systems == {"DS/DSM Amplifier": {16}, "DS/DSM CD": {20}, "DS/DSM DVD": {25}}
    doubtful = [(m, k) for m, d in files.items() for k, s in d["keys"].items() if s["forms"][0]["confidence"] == "untested"]
    assert doubtful == [("DS/DSM Amplifier", "KEY_POWER_OFF")]
    amp = files["DS/DSM Amplifier"]["keys"]
    assert "decodes to address 16 command 127 and not to this code" in amp["KEY_POWER_OFF"]["forms"][0]["source"]
    assert amp["KEY_POWER_ON"]["forms"][0]["function"] == "0x7D" and amp["KEY_VOLUME_PLUS"]["forms"][0]["function"] == "0x10"
    assert "reserved for the Linn remote REM019" in amp["KEY_AUDIO_ADJUST_BALANCE_PLUS"]["forms"][0]["source"]
    assert files["DS/DSM CD"]["keys"]["KEY_SKIP_MINUS"]["forms"][0]["function"] == "0x21"
    assert files["DS/DSM CD"]["keys"]["KEY_SKIP_MINUS_2"]["forms"][0]["function"] == "0x61"
    report = (ROOT / oi.IMPORT_ROOT / oi.REPORT).read_text(encoding="utf-8")
    figure = lambda what: int(re.search(rf"\| linn: {what} \| ([\d,]+) \|", report)[1].replace(",", ""))
    assert [figure(w) for w in ("RC5_Codes.html: rows read", "files", "keys", "Pronto hex that decodes to its row's address and command",
                                "Pronto hex that decodes to another code")] == [128, 3, 116, 50, 1]


def test_the_ir_blasters_linn_rc5_remotes_send_the_codes_the_wiki_table_lists():
    """The independent check: the ledger's three Linn RC-5 remotes (IR Blaster database) hold 125 keys and **64 of them are a code the
    table lists** (the CD player's at address 20 mostly, and a remote's volume, mute, power, balance and source keys at 16). The
    names agree in 30 of the 34 where the vocabulary or the spelling compares them; the 4 that do not are one remote's cursor keys
    (``UP``, ``DOWN``, ``OK``, ``YELLOW``) on the transport codes (``Play``, ``Pause``, ``Stop``): a button named for where it is."""
    from remote_ledger.keys import load_vocabulary

    vocabulary = load_vocabulary()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())      # noqa: E731
    ours: dict[tuple[int, int], set[str]] = {}
    for doc in linn_files().values():
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            ours.setdefault((int(form["device"], 0), int(form["function"], 0)), set()).add(spec["label"])
    remotes = total = hit = agree = disagree = 0
    for path in sorted((ROOT / "remotes/irblaster/LINN").glob("*.json")):
        remotes += 1
        for spec in load(path)["keys"].values():
            form = spec["forms"][0]
            key = (int(form["device"], 0), int(form["function"], 0))
            total += 1
            if key not in ours:
                continue
            hit += 1
            mine, theirs = {vocabulary.resolve(x) for x in ours[key]} - {None}, vocabulary.resolve(spec["label"])
            if squash(spec["label"]) in {squash(x) for x in ours[key]} or (theirs and theirs in mine):
                agree += 1
            elif theirs is not None and mine:
                disagree += 1
    assert (remotes, total, hit, agree, disagree) == (3, 125, 64, 30, 4)

