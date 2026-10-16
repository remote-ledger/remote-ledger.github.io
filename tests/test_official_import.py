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
from remote_ledger.official import anthem, arcam, cambridge, cary, classe, cyrus, denon, emotiva, hegel, hitachi, infocus, jvc, kef, lyngdorf, marantz, nad, oppo, rotel, simaudio
from remote_ledger.official import importer as oi
from remote_ledger.official.common import MANIFEST, Report, load_snapshot
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / oi.SNAPSHOT


# --- the snapshot ------------------------------------------------------------------------------


def test_every_document_and_csv_is_the_one_the_manifest_pins():
    for maker, retrieved in (("marantz", "2026-10-08"), ("anthem", "2026-10-08"), ("oppo", "2026-10-09"), ("kef", "2026-10-13"),
                             ("jvc", "2026-10-13"), ("arcam", "2026-10-13"), ("cambridge", "2026-10-13"), ("hegel", "2026-10-13"), ("cary", "2026-10-13"), ("infocus", "2026-10-15"), ("lyngdorf", "2026-10-16"), ("emotiva", "2026-10-16"), ("denon", "2026-10-16"), ("hitachi", "2026-10-16"), ("classe", "2026-10-16"), ("cyrus", "2026-10-16"), ("nad", "2026-10-14"), ("simaudio", "2026-10-13"), ("rotel", "2026-10-13")):
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
    from remote_ledger.import_common import authored_names

    root = tmp_path / "repo"
    (root / "remotes").mkdir(parents=True)
    oi.write_import(root, SNAPSHOT, authored_names(ROOT, oi.IMPORT_ROOT))      # the other sources' names, as in the tree
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
    for maker in ("marantz", "anthem", "oppo", "kef", "jvc", "arcam", "cambridge", "hegel", "cary", "infocus", "lyngdorf", "emotiva", "denon", "hitachi", "classe", "cyrus", "nad", "simaudio", "rotel"):
        manifest = json.loads((SNAPSHOT / maker / MANIFEST).read_text(encoding="utf-8"))
        for name, entry in manifest["documents"].items():
            hashes[name], dates[name], snapshots[name] = entry["sha256"], entry.get("retrieved", manifest["retrieved"]), entry.get("snapshot")
    shape = re.compile(r"^(?:marantz\.com archive-downloads|anthemav\.com \(storage\.googleapis\.com/sandbox1-anthemav/an\)"
                       r"|download\.oppodigital\.com/\w+|kef\.com IR code sheets|jvc\.com D-ILA remote emulation guide|arcam\.co\.uk custom installation notes|cambridgeaudio\.com IR code documents|support\.hegel\.com RC-5 code tables|caryaudio\.com DMS IR Remote Codes|infocus\.com IR code PDFs|lyngdorf\.com IR code PDF|emotiva\.com IR code PDFs|denon\.co\.uk RC code list|denon\.com Master IR Hex|hitachi-america\.us IR code documents|classeaudio\.com IR code documents|cyrusaudio\.com remote code tables|nadelectronics\.com IR command tables|nadelectronics\.com Crestron IR files|simaudio\.com Table of IR codes|rotel\.com IR hex codes)/(\S+)@([0-9a-f]{8}) \(retrieved (\d{4}-\d{2}-\d{2})"
                       r"(?:, from the Wayback Machine snapshot (\d{14}))?\) (?:sheet '[^']+' row \d+|page \d+ row \d+|page \d+ '|page \d+ table \d+ row '|page \d+ row '|key \d+ ')")
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


FIRST_ARCAM = ("AV40", "AV41", "AVR10", "AVR11", "AVR20", "AVR21", "AVR30", "AVR31", "AVR380", "AVR450", "AVR5", "AVR750", "CDS50", "SA10",
               "SA20", "SA30", "Solo", "Solo_Mini", "UDP411")           # the files of DESIGN section 64


def arcam_files(only: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Every Arcam file by its stem, or the ones named."""
    return {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "arcam").glob("*.json")) if only is None or p.stem in only}


def test_the_19_arcam_files_are_the_models_the_documents_title_and_the_restrictions_leave_out_what_they_say():
    """One file for each model a document's title lists, 1,816 keys, 1,815 Plausible because every row's decimal and hex agree and
    one Untested (the Solo's ``Number 3``, which the table gives the command of ``Number 2``); 272 distinct codes, systems 16, 17,
    20, 23 and 25. The AVR5 and AVR10 lack the 21 Zone 2 rows the AVR5-41 document says they do not have, the AVR380 the two
    ``AVR450/750 only`` DAB rows."""
    files = arcam_files(FIRST_ARCAM)
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
    JP1 upgrades) hold 401 keys, and **337 of them (84.0%) are a code some table of the first 19 files lists**, **369 (92.0%) with the
    36 more files of sections 74 and 78**, the meaning agreeing where it is plain (volume plus and minus 16 and 17, mute 13, power 12, the
    standby on and off 124 and 123, the digits). The rest are other products' and other generations' codes: system 16 command 53 is
    Select FM on a Solo and Play on an AVR. The 55 files hold 469 distinct codes."""
    ours: dict[tuple[int, int], set[str]] = {}
    for doc in arcam_files(FIRST_ARCAM).values():
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            ours.setdefault((int(form["device"], 0), int(form["function"], 0)), set()).add(spec["label"])
    every = {(int(spec["forms"][0]["device"], 0), int(spec["forms"][0]["function"], 0))
             for doc in arcam_files().values() for spec in doc["keys"].values()}          # the files of section 74 as well
    remotes = total = hit = hit_all = 0
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
        hit_all += sum(1 for c in codes if c in every)
    assert (remotes, total, hit, hit_all) == (8, 401, 337, 369)
    assert len(every) == 469
    for code, wanted in (((16, 16), "Increase volume (+)"), ((16, 17), "Decrease volume (-)"), ((16, 13), "Mute"), ((16, 12), "Standby"),
                         ((16, 124), "Standby On"), ((16, 123), "Standby Off"), ((17, 5), "Tuner Number 5")):
        assert any(label.startswith(wanted.split(" (")[0]) for label in ours[code]), (code, wanted)


def codes_of(doc: dict) -> dict[str, tuple[str, str]]:
    return {spec["label"]: (spec["forms"][0]["device"], spec["forms"][0]["function"]) for spec in doc["keys"].values()}


def test_the_older_arcam_sheets_columns_are_read_by_where_they_are_with_the_sentences_that_apply_to_a_table():
    """A row is a name and its code in the cell after it, a table at the left and one at the right (the left first); the sheet's
    ``The commands are accepted by the A90 and P38 Power amplifier`` makes the table under it those two models'; ``Track number 'n'`` is
    the digits 1 to 9; the CD sheet says what its italics mean, which is in each citation; a code two names share and a system code
    other than the one the sheet states are doubtful."""
    amps = arcam.column_rows([
        ["2", "86", "113", "The system code is 16 ."], ["2", "104", "119", "Power commands", "318", "Menu navigation commands"],
        ["2", "118", "119", "Command", "204", "Decimal Code", "318", "Command", "403", "Decimal Code"],
        ["2", "129", "119", "Power toggle", "204", "16–12", "318", "Up", "403", "16–32"],
        ["2", "140", "119", "Power-on", "204", "16–123", "318", "Down", "403", "16–33"],
        ["2", "323", "113", "The commands are accepted by the A90 and P38 Power amplifier."],
        ["2", "366", "119", "Speaker 1 toggle", "204", "16–35"],
        ["2", "460", "113", "Note that Arcam amplifers also responds to code 20–53 (the Play command for an Arcam CD player)."]])
    assert [(r.function, r.system, r.command, r.only) for r in amps] == [
        ("Power toggle", 16, 12, ()), ("Power-on", 16, 123, ()), ("Speaker 1 toggle", 16, 35, ("A90", "P38")), ("Up", 16, 32, ()),
        ("Down", 16, 33, ()), ("Play (the CD player's command)", 20, 53, ())]
    assert "accepted by the A90 and P38 Power amplifier" in amps[2].note and not any(r.doubt for r in amps)
    cd = arcam.column_rows([
        ["2", "83", "113", "The following tables give the IR-commands accepted by Arcam CD players. The system code is 20 ."],
        ["2", "95", "113", "Items shown in italics reference the buttons on the supplied CR-314 remote control. The items not in"],
        ["2", "104", "113", "italics are additional commands accepted by the CD player."],
        ["2", "158", "119", "<i>Power toggle</i>", "236", "<i>20–12</i>"],
        ["2", "226", "119", "<i>Track number ‘n’, (e.g., 1)</i>", "236", "<i>20–n (e.g., 20–1)</i>"],
        ["2", "237", "119", "Remain", "236", "20–11"], ["2", "300", "119", "Scan 30 seconds", "236", "20–90"],
        ["2", "310", "119", "Scan off", "236", "20–90"], ["2", "320", "119", "Odd", "236", "16–9"]])
    assert [r.function for r in cd] == ["Power toggle"] + [f"Track {n}" for n in range(1, 10)] + ["Remain", "Scan 30 seconds", "Scan off", "Odd"]
    assert [r.command for r in cd[1:10]] == list(range(1, 10)) and all(r.system == 20 for r in cd[:-1])
    assert "set in italics: a button of the supplied CR-314 remote control" in cd[0].note and "gives no n of 0" in cd[5].note
    assert cd[10].note.startswith("not in italics: an additional command the unit accepts")
    assert "gives this code to 'Scan 30 seconds' and 'Scan off'" in cd[11].doubt and cd[11].doubt == cd[12].doubt
    assert cd[13].doubt == "the sheet says the system code is 20 and this row's is 16"


def test_the_solo_bars_page_is_read_as_two_tables_with_a_decimal_and_a_hex_without_the_prefix():
    rows = arcam.bar_rows([
        ["1", "180", "50", "Commands on the Solo bar remote control"], ["1", "195", "50", "Function", "298", "Decimal", "349", "Hex", "390", "Comments"],
        ["1", "210", "50", "Standby", "298", "19-12", "349", "13-0C", "390", "Toggle standby ON/OFF"],
        ["1", "300", "50", "Additional commands (not on the Solo bar remote)"],
        ["1", "320", "50", "Power On", "298", "19-123", "349", "13-7B", "390", "If the soundbar is OFF, turn it ON"],
        ["1", "330", "50", "Select HDMI1", "298", "19-00", "349", "13-01", "390", "Switch directly to the HDMI1 input"]])
    assert [(r.function, r.system, r.command, r.agrees) for r in rows] == [
        ("Standby", 19, 12, True), ("Power On", 19, 123, True), ("Select HDMI1", 19, 0, False)]
    assert rows[0].note == "the sheet lists it under 'Commands on the Solo bar remote control'; the sheet's comment: 'Toggle standby ON/OFF'"
    assert rows[1].note.startswith("the sheet lists it under 'Additional commands (not on the Solo bar remote)'")


def test_the_older_arcam_sheets_are_16_files_for_the_models_on_their_covers_and_the_speaker_keys_are_the_a90s_and_p38s():
    """The amplifier sheet's cover lists ten models (the DiVA A65 Plus, A70, A80 and A90, the FMJ A18, A28, A32, A38, C30 and C31) and the
    CD sheet's five (the DiVA CD73 and CD192, the FMJ CD17, CD36 and CD37): 467 keys. **The six speaker keys are for the A90 and the P38**,
    which the sheet names under its speaker table and not on its cover (26 keys the A90, 6 keys the P38, 20 each of the other nine); the
    CD files have 51 keys, 49 Plausible and the two that share the code 20-90 (``Scan 30 seconds`` and ``Scan off``) Untested. Of the
    CD keys 18 are set in italics, the buttons of the supplied CR-314 remote, and 33 are additional commands."""
    amps = ("A65_Plus", "A70", "A80", "A90", "A18", "A28", "A32", "A38", "C30", "C31", "P38")
    cds = ("CD73", "CD192", "CD17", "CD36", "CD37")
    files = arcam_files(amps + cds)
    assert {m: len(d["keys"]) for m, d in files.items()} == {**{m: 20 for m in amps}, "A90": 26, "P38": 6, **{m: 51 for m in cds}}
    assert sum(len(d["keys"]) for d in files.values()) == 467
    speaker = {"Speaker 1 toggle": 0x23, "Speaker 2 toggle": 0x27, "Speaker 1 on": 0x2B, "Speaker 1 off": 0x2C, "Speaker 2 on": 0x2D, "Speaker 2 off": 0x2E}
    for m in ("A90", "P38"):
        assert {label: int(code[1], 16) for label, code in codes_of(files[m]).items() if "Speaker" in label} == speaker
    assert not any("Speaker" in label for m in amps if m not in ("A90", "P38") for label in codes_of(files[m]))
    assert len(codes_of(files["P38"])) == 6 and {c[0] for c in codes_of(files["P38"]).values()} == {"0x10"}
    base = {k: v for k, v in codes_of(files["A18"]).items()}
    assert all(codes_of(files[m]) == base for m in amps if m not in ("A90", "P38"))
    assert codes_of(files["A90"]) == {**base, **{label: ("0x10", f"0x{code:02X}") for label, code in speaker.items()}}
    assert base["Power toggle"] == ("0x10", "0x0C") and base["Power-on"] == ("0x10", "0x7B") and base["Power-off"] == ("0x10", "0x7C")
    assert [base[f"{n} select"] for n in ("PHONO", "AV", "Tuner", "DVD", "tape", "VCR", "CD", "Aux")] == [("0x10", f"0x{i:02X}") for i in range(1, 9)]
    assert base["Play (the CD player's command)"] == ("0x14", "0x35") and base["Enter"] == ("0x10", "0x57") and len(base) == 20
    cd17 = codes_of(files["CD17"])
    assert all(codes_of(files[m]) == cd17 for m in cds) and {c[0] for c in cd17.values()} == {"0x14"}
    assert [cd17[f"Track {n}"] for n in range(1, 10)] == [("0x14", f"0x{n:02X}") for n in range(1, 10)] and cd17["Track selection 10+"] == ("0x14", "0x0A")
    assert cd17["Scan 30 seconds"] == cd17["Scan off"] == ("0x14", "0x5A") and cd17["Repeat all"] == ("0x14", "0x7F")
    for m in cds:
        keys = files[m]["keys"].values()
        assert sorted(spec["label"] for spec in keys if spec["forms"][0]["confidence"] == "untested") == ["Scan 30 seconds", "Scan off"]
        assert sum("set in italics" in spec["forms"][0]["source"] for spec in keys) == 18
        assert sum("not in italics" in spec["forms"][0]["source"] for spec in keys) == 33
    assert all(spec["forms"][0]["confidence"] == "plausible" for m in amps for spec in files[m]["keys"].values())
    assert all("retrieved 2026-10-15" in spec["forms"][0]["source"] for m in amps + cds for spec in files[m]["keys"].values())


def test_the_older_products_serial_notes_end_with_the_same_table_as_the_newer_ones_and_a_title_with_models_is_a_file_each():
    """Nine documents of Arcam's custom installation notes, eight with the ``Function | RC5 code | RC5 code (Data1 - Data2)`` table of the
    ones of section 64 (17 files, 1,695 keys) and the Solo bar's one page (24 keys, system 19). Of the 1,695 keys three are Untested:
    ``Zone 3 Vol+``, the row of the AVR500, AVR600 and AV888 whose decimal says 23-20 and whose hex 0x17 - 0x17. Two codes of the
    AVR390 family are two rows (``HOME`` and ``Yellow``, ``Cycle between VFD information panels`` and ``Blue``), as the BDP's ``ZOOM`` and
    ``Zoom`` are."""
    sizes = {"AVR500": 171, "AVR600": 171, "AV888": 171, "AVR400": 153, "AVR390": 116, "AVR550": 116, "AVR850": 116, "AV860": 116, "SR250": 116,
             "BDP100": 51, "BDP300": 51, "CDS27": 36, "D33": 19, "ST60": 43, "Solo_Movie": 83, "Solo_Movie_2.1": 83, "Solo_Music_2": 83}
    files = arcam_files(tuple(sizes))
    assert {m: len(d["keys"]) for m, d in files.items()} == sizes and sum(sizes.values()) == 1695
    for group in (("AVR500", "AVR600", "AV888"), ("AVR390", "AVR550", "AVR850", "AV860", "SR250"), ("BDP100", "BDP300"),
                  ("Solo_Movie", "Solo_Movie_2.1", "Solo_Music_2")):
        assert all(codes_of(files[m]) == codes_of(files[group[0]]) for m in group)
    untested = sorted((m, spec["label"], spec["forms"][0]["device"], spec["forms"][0]["function"])
                      for m, d in files.items() for spec in d["keys"].values() if spec["forms"][0]["confidence"] == "untested")
    assert untested == [(m, "Zone 3 Vol+", "0x17", "0x14") for m in ("AV888", "AVR500", "AVR600")]
    assert "do not say the same code" in files["AVR600"]["keys"]["KEY_ZONE_3_VOL_PLUS"]["forms"][0]["source"]
    systems = {m: {spec["forms"][0]["device"] for spec in d["keys"].values()} for m, d in files.items()}
    assert systems["AVR600"] == systems["AVR400"] == {"0x10", "0x11", "0x17"} and systems["AV860"] == {"0x10", "0x17"}
    assert systems["BDP100"] == {"0x19"} and systems["CDS27"] == {"0x14"} and systems["D33"] == {"0x1B"} and systems["ST60"] == {"0x15"}
    assert systems["Solo_Movie"] == {"0x10"}
    codes = {m: {} for m in ("AV860", "BDP100")}
    for m in codes:
        for spec in files[m]["keys"].values():
            codes[m].setdefault((spec["forms"][0]["device"], spec["forms"][0]["function"]), []).append(spec["label"])
    assert {k: v for k, v in codes["AV860"].items() if len(v) > 1} == {
        ("0x10", "0x37"): ["Cycle between VFD information panels", "Blue"], ("0x10", "0x2B"): ["HOME", "Yellow"]}
    assert {k: v for k, v in codes["BDP100"].items() if len(v) > 1} == {("0x19", "0x44"): ["ZOOM", "Zoom"]}
    bar = arcam_files(("Solo_bar",))["Solo_bar"]
    assert len(bar["keys"]) == 24 and {c[0] for c in codes_of(bar).values()} == {"0x13"}
    assert all(spec["forms"][0]["confidence"] == "plausible" for spec in bar["keys"].values())
    on_remote = [spec["label"] for spec in bar["keys"].values() if "under 'Commands on the Solo bar remote control'" in spec["forms"][0]["source"]]
    assert len(on_remote) == 14 and on_remote[0] == "Standby" and on_remote[-1] == "Enter system menu"
    assert [codes_of(bar)[f"Select HDMI{n}"][1] for n in (1, 2, 3, 4)] == ["0x00", "0x01", "0x02", "0x04"] and codes_of(bar)["Select BT"] == ("0x13", "0x0B")


# --- InFocus -----------------------------------------------------------------------------------------------------


def test_infocus_customer_code_is_the_documents_own_sentence_and_two_of_them_are_an_error():
    """``The customer code portion of the IR packet should use the hexadecimal value 0x4E87``, which may be split over two lines, or a
    ``Device ID:0x4E87`` with non-breaking spaces (the IN5310's text); a document with none has none (the importer then uses 0x4E87 and
    marks every key); two different ones in one document stop the import."""
    assert infocus.customer_of([["1", "132", "139", "The customer code portion of the IR packet should use the hexadecimal value 0x4E87."]], "lines") == 0x4E87
    assert infocus.customer_of([["1", "105", "90", "The Screen Play 110 remote uses the NEC protocol. The customer code"],
                                ["1", "121", "90", "portion of the IR packet should use the hexadecimal value 0x4E87."]], "lines") == 0x4E87
    assert infocus.customer_of([["1", "Standard\xa0consumer\xa0IR\xa0(NEC\xa0framing)\xa0Device\xa0ID:0x4E87"]], "pages") == 0x4E87
    assert infocus.customer_of([["1", "448", "90", "The customer code portion should use the hexadecimal value: 0x83F4"]], "lines") == 0x83F4
    assert infocus.customer_of([["1", "79", "471", "Customer Code: 0x0080"]], "lines") == 0x0080
    assert infocus.customer_of([["1", "5", "5", "a table of 0x4E87 and nothing else"]], "lines") is None
    with pytest.raises(ValueError, match="two customer codes"):
        infocus.customer_of([["1", "1", "5", "The customer code is 0x4E87"], ["1", "2", "5", "Customer Code: 0x0080"]], "lines")


def test_the_infocus_table_layouts_are_read_as_a_name_and_a_data_code_with_each_layouts_own_checks():
    """``name_code``, ``code_name``, ``hex16`` (the code is the low byte and the high byte is its bit reverse: 0xE817 is 0x17), ``keyno``
    (the customer code in each row) and the layouts whose rows are not on one line (``banded``): a code and its name a point or two
    apart, a name that goes on in the next line (``Discrete Resize`` / ``Letterbox``), a decimal and a binary that must agree with the hex."""
    named = infocus.name_code(infocus.cells_of([["1", "161", "99", "Key Name", "225", "Code", "270", "Description"],
                                                ["1", "178", "99", "Menu", "225", "0x02", "270", "Toggles menu state up or down"],
                                                ["1", "322", "99", "Power", "225", "0x17"]]))
    assert [(e.name, e.code) for e in named] == [("Menu", 2), ("Power", 0x17)]
    coded = infocus.code_name(infocus.cells_of([["1", "186", "113", "Keycode Hexadecimal Value", "392", "Function"], ["1", "202", "90", "0x02", "311", "Menu toggle"]]))
    assert [(e.name, e.code) for e in coded] == [("Menu toggle", 2)]
    sixteen = infocus.hex16(infocus.cells_of([["1", "136", "193", "POWER", "338", "0xE817"], ["1", "153", "193", "VOLUME_UP", "338", "0x1008"],
                                              ["1", "169", "193", "MUTE", "338", "0x9429"], ["1", "185", "193", "WRONG", "338", "0xE818"]]))
    assert [(e.name, e.code, e.agrees) for e in sixteen] == [("POWER", 0x17, True), ("VOLUME_UP", 0x08, True), ("MUTE", 0x29, True), ("WRONG", 0x18, False)]
    assert "bit reverse" in sixteen[-1].note
    numbered_keys = infocus.keyno(infocus.cells_of([["1", "101", "77", "K01", "132", "STANDBY", "200", "NEC", "261", "4E87", "326", "39"],
                                                    ["1", "112", "77", "K02", "139", "ON", "200", "NEC", "261", "4E87", "326", "3A"]]))
    assert [(e.name, e.code, e.customer) for e in numbered_keys] == [("STANDBY", 0x39, 0x4E87), ("ON", 0x3A, 0x4E87)]
    banded = infocus.name_desc_code([
        ["2", "122", "88", "Key Name", "263", "Description", "392", "Hexadecimal"],
        ["3", "78", "412", "39h"], ["3", "80", "36", "Discrete Power Off", "194", "Commands the system to power down"],
        ["3", "117", "36", "Discrete Resize", "477", "3Eh"], ["3", "129", "36", "Letterbox", "156", "Selects the Letterbox aspect ratio"],
        ["3", "141", "36", "Discrete Resize Natural", "477", "3Fh"], ["3", "152", "36", "Wide", "156", "Selects the Natural Wide aspect ratio"],
        ["3", "300", "36", "A footer far from any code"]])
    assert [(e.page, e.name, e.code) for e in banded] == [(3, "Discrete Power Off", 0x39), (3, "Discrete Resize Letterbox", 0x3E), (3, "Discrete Resize Natural Wide", 0x3F)]
    nearer = infocus.name_desc_code([["3", "55", "45", "Discrete Resize Letterbox", "198", "Selects the Letterbox aspect ratio", "436", "3Eh"],
                                     ["3", "72", "45", "Discrete Resize Natural", "198", "Selects the Natural Wide/Panorama aspect"],
                                     ["3", "86", "45", "Wide/Panorama", "198", "ratio", "436", "3Fh"]])
    assert [(e.name, e.code) for e in nearer] == [("Discrete Resize Letterbox", 0x3E), ("Discrete Resize Natural Wide/Panorama", 0x3F)]     # begins above its code
    checked = infocus.dec_hex_bin([
        ["2", "114", "41", "Power On", "108", "Turns on projector power", "366", "58", "437", "3Ah", "513", "0011 1010"],
        ["2", "200", "41", "Computer", "108", "Computer2", "366", "25", "438", "19h", "512", "1000 1001"],
        ["2", "300", "41", "Discrete Resize Native", "411", "3Bh", "459", "0011 1011"]], name_max=90)
    assert [(e.name, e.code, e.agrees) for e in checked] == [("Power On", 0x3A, True), ("Computer", 0x19, False), ("Discrete Resize Native", 0x3B, True)]
    assert "decimal 25, hex 0x19 and binary 1000 1001 (0x89) are not one code" in checked[1].note and checked[2].note == "the table's decimal of this row was not read"


def test_the_infocus_numbered_rows_are_read_in_order_with_the_arrow_keys_named_and_a_row_without_a_code_left_out():
    rows = [["1", line] for line in (
        "Remote IR Information", "Standard consumer IR (NEC framing) Device ID:0x4E87", "Description", "Data Code",
        "1 Power On", "Turns the projector on.", "3Ah", "2", "^", "When the OSD menu is on, acts as an up arrow key. When the OSD",
        "menu is off, adjusts keystone +.", "13h", "3 Volume\u2010", "Decreases volume.", "04h", "4 Light", "Turns on remote backlight", "n/a")]
    entries, no_code = infocus.numbered(rows)
    assert [(e.name, e.code) for e in entries] == [("Power On", 0x3A), ("Up", 0x13), ("Volume-", 4)]
    assert entries[1].note == "the table writes the key's name as '^'" and no_code == ["'Light' (row 4): the table's code is n/a, so it sends none"]


def test_the_infocus_tables_that_are_images_are_read_from_a_transcription_with_the_same_checks_and_the_ones_that_have_no_names_take_a_twins():
    """A transcription row is ``page, name, decimal, hex, binary`` and a misreading fails the three columns' agreement; the IN3118HD's table
    (a key number, a description, a data code) is the IN5310's, takes its names by number, and a code that is not the twin's stops the import."""
    entries = infocus.transcribed([["2", "Menu", "2", "02h", "0000 0010"], ["2", "Power", "23", "17h", "0001 0110"]])
    assert [(e.name, e.code, e.agrees) for e in entries] == [("Menu", 2, True), ("Power", 0x17, False)]
    assert "binary 0001 0110 (0x16)" in entries[1].note
    with pytest.raises(ValueError, match="not a decimal"):
        infocus.transcribed([["2", "Menu", "two", "02h", "0000 0010"]])
    twin = {1: infocus.Entry(1, "Power On", 0x3A, "3Ah", True, "", None, 1), 2: infocus.Entry(1, "Power Off", 0x39, "39h", True, "", None, 2)}
    keyed, no_code = infocus.keyed_image([["1", "1", "Turns the projector on.", "3Ah"], ["1", "2", "Turns the projector off.", "39h"],
                                          ["1", "3", "LIGHT", ""]], twin)
    assert [(e.name, e.code, e.number) for e in keyed] == [("Power On", 0x3A, 1), ("Power Off", 0x39, 2)] and len(no_code) == 1
    with pytest.raises(ValueError, match="not the IN5310's row"):
        infocus.keyed_image([["1", "1", "Turns the projector on.", "3Bh"]], twin)


def test_the_three_infocus_image_tables_are_one_table_whose_every_name_is_in_the_text_tables_of_the_family():
    """The IN110's, IN3114's and IN3910's images are the same 48 rows (the three transcriptions are one file), the 48 codes are all codes of the
    text tables of the IN1100, IN20, IN3100, IN5300 and IN5500 (59, 59, 59, 62 and 62 rows), and **each name is that table's name for the
    code** (case and punctuation aside): a misreading of a name would show; the IN3118HD's 29 codes are the IN5310's."""
    folder = SNAPSHOT / "infocus" / "csv"
    names = ("InFocus-IN110-IRCodes", "IN3114_IN3116_IRCodes", "InFocus-IN3910-IRCodes")
    texts = [(folder / f"{n}__transcribed.csv").read_text(encoding="utf-8") for n in names]
    assert texts[0] == texts[1] == texts[2] and texts[0].count("\n") == 48
    files = infocus_files()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
    table = {s["forms"][0]["function"]: s["label"] for s in files["IN112"]["keys"].values()}
    assert len(table) == 48
    for model in ("IN1100", "IN24", "IN3102"):          # the three text tables that have all 48 rows, with the same names
        text = {s["forms"][0]["function"]: s["label"] for s in files[model]["keys"].values()}
        assert all(code in text and squash(text[code]) == squash(label) for code, label in table.items()), model
    for model in ("IN5302", "IN5502"):                  # the IN5300's and IN5500's: no `Light` and `Reset Bulb Counter` (not transmitted), and three names
        text = {s["forms"][0]["function"]: s["label"] for s in files[model]["keys"].values()}      # that the later tables write otherwise
        assert {table[c] for c in table if c not in text} == {"Light", "Reset Bulb Counter"}
        assert {(table[c], text[c]) for c in table if c in text and squash(text[c]) != squash(table[c])} == {
            ("New Mute", "Mute"), ("Effect", "Custom"), ("Discrete Resize Natural Wide", "Discrete Resize Natural Wide/Panorama")}
    twin = {s["forms"][0]["function"]: s["label"] for s in files["IN5310"]["keys"].values()}
    assert {s["forms"][0]["function"]: s["label"] for s in files["IN3118HD"]["keys"].values()} == twin and len(twin) == 29
    assert "read from it by eye" in files["IN112"]["keys"]["KEY_MENU"]["forms"][0]["source"]
    assert "the table has no code" not in files["IN3118HD"]["keys"]["KEY_POWER_ON"]["forms"][0]["source"]


def test_the_sp8604_in5550_and_in3900lb_tables_are_read_by_eye_by_a_second_table_and_from_a_rotated_page():
    """The SP8604's table (a picture: a transcription ``page, number, function, description, keycode`` after a customer-code row), the
    IN5550's two tables (a ``Table 0`` and a ``Table 1`` side by side, the ID-set special codes under them) and the IN3900LB's landscape
    specification (rows of ``N/A`` are reserved codes, no keys)."""
    entries, customer = infocus.keycodes_image([["1", "customer", "0x31CE", "Device ID:Ox31CE"], ["1", "1", "Power ON", "Power on toggle", "0x90"],
                                                ["1", "2", "Power OFF", "Power off toggle", "0x91"]])
    assert customer == 0x31CE and [(e.name, e.code, e.number) for e in entries] == [("Power ON", 0x90, 1), ("Power OFF", 0x91, 2)]
    with pytest.raises(ValueError, match="transcription row"):
        infocus.keycodes_image([["1", "customer", "0x31CE", ""], ["1", "2", "Power ON", "x", "0x90"]])
    rows = [["1", "85", "57", "1", "98", "K22", "157", "0x90", "219", "ON", "280", "0x90", "346", "ON", "455", "ON"],
            ["1", "141", "57", "5", "98", "K19", "157", "0xB8", "203", "TEST PATERN", "280", "0xB8", "330", "TEST PATERN"],
            ["1", "350", "55", "20", "98", "K21", "157", "0x86", "206", "AUTO SYNC", "280", "0x61", "349", "1"],
            ["1", "279", "55", "15", "103", "-", "164", "-", "223", "-", "287", "-", "350", "-", "446", "AUTO SYNC"],
            ["1", "433", "55", "26", "98", "K30", "157", "0xBE", "223", "-", "280", "0x67", "349", "7"],
            ["1", "545", "72", "ID_SET + MENU", "157", "0x6C", "214", "Table0", "280", "0x6C", "341", "Table0"]]
    assert [(e.name, e.code) for e in infocus.ids(rows, 0)] == [("ON", 0x90), ("TEST PATERN", 0xB8), ("AUTO SYNC", 0x86), ("Table0", 0x6C)]
    assert [(e.name, e.code) for e in infocus.ids(rows, 1)] == [("ON", 0x90), ("TEST PATERN", 0xB8), ("1", 0x61), ("7", 0x67), ("Table0", 0x6C)]


def test_the_sp8604_is_the_sp8600_remote_lircs_raw_decodes_confirm_and_its_customer_code_is_sent_first_byte_first():
    """**An independent check of the byte order and of the transcription.** LIRC's ``SP8600`` remote is raw timings; decoded as NEC (the bits
    of each byte least significant first) every key is ``0x31 0xCE`` and a function with its complement: **the first byte sent is 0x31, the
    document's ``0x31CE`` is the order sent, and 9 of its 15 functions are keys of the SP8604's table (0x87 `MENU`, 0xC1 to 0xC5 the arrows
    and `ENTER`, 0x8E `FREEZE`, 0x86 `AUTO`, 0x83 `SOURCE`)**, the digits of the transcription that a misreading would have changed. The
    IN5550's table has 14 of the SP8604's functions on the same codes, and the IR Blaster database's InFocus remote at 0x32CD shares only
    `ENTER` (0xC5): the family has more than one address, and the IN5550's is a guess (Untested)."""
    import json

    raw = json.loads((ROOT / "remotes" / "lirc" / "infocus" / "SP8600.json").read_text(encoding="utf-8"))["keys"]
    files = infocus_files()
    table = {int(s["forms"][0]["function"], 16): s["label"] for s in files["SP8604"]["keys"].values()}
    hits = 0
    for spec in raw.values():
        intro = spec["forms"][0]["intro"][2:]
        bits = [1 if intro[i + 1] > 1000 else 0 for i in range(0, 64, 2)]
        first, second, function, complement = ([sum(bits[8 * b + k] << k for k in range(8))] for b in range(4))
        assert (first[0], second[0]) == (0x31, 0xCE) and complement[0] == ~function[0] & 0xFF
        hits += function[0] in table
    assert hits == 9 and len(raw) == 15
    assert {"0x87": "MENU", "0xC1": "Up arrow", "0xC5": "ENTER", "0x8E": "FREEZE", "0x86": "AUTO", "0x83": "SOURCE"} == {
        hex(c).upper().replace("0X", "0x"): table[c] for c in (0x87, 0xC1, 0xC5, 0x8E, 0x86, 0x83)}
    sp, i0 = {s["forms"][0]["function"]: s["label"] for s in files["SP8604"]["keys"].values()}, {s["forms"][0]["function"]: s["label"] for s in files["IN5550"]["keys"].values()}
    shared = [c for c in sp if c in i0]
    assert len(shared) == 18
    assert {c for c in shared if c in ("0x8B", "0x9A", "0x9B", "0x9C")} == {"0x8B", "0x9A", "0x9B", "0x9C"}          # four codes the tables give other functions
    assert {sp[c]: i0[c] for c in shared if c not in ("0x8B", "0x9A", "0x9B", "0x9C")} == {      # and 14 the same one under shorter names
        "Power ON": "ON", "Power OFF": "OFF", "Up arrow": "UP", "Left arrow": "LEFT", "ENTER": "ENTER", "Right arrow": "RIGHT", "Down arrow": "DOWN",
        "MENU": "MENU", "ASPECT RATIO": "ASPECT", "PICTURE MODE": "PICTURE", "SOURCE": "INPUT", "FREEZE": "FREEZE", "LIGHT": "LIGHT", "AUTO": "AUTO SYNC"}
    assert files["SP8600__InFocus_table"]["model"] == "SP8600 (InFocus table)"                  # LIRC has an SP8600
    assert files["IN5550"]["keys"]["KEY_ENTER"]["forms"][0]["confidence"] == "untested" and "a guess" in files["IN5550"]["keys"]["KEY_ENTER"]["forms"][0]["source"]
    spec = files["IN3900LB"]
    assert len(spec["keys"]) == 62 and "N/A" not in {s["label"] for s in spec["keys"].values()}
    assert spec["keys"]["KEY_SHIFT_DOWN"]["forms"][0]["function"] == "0x4F" and spec["keys"]["KEY_HELP"]["forms"][0]["function"] == "0x0A"


def infocus_files() -> dict[str, dict]:
    return {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "infocus").glob("*.json"))}


def infocus_doc_of(doc: dict) -> str:
    return next(iter(doc["keys"].values()))["forms"][0]["source"].split("/")[1].split("@")[0]


def test_the_38_infocus_documents_are_92_files_and_4053_keys_and_618_of_them_are_untested_for_the_reason_each_says():
    """Files, keys per file and Untested keys per file of each document (the models are the title's: the IN1100's are eight, the IN3100's
    twelve; the tables of the IN110's, IN3114's, IN3910's, IN3118HD's and SP8604's are images, read by eye: D298, D301). **Untested are the 618 keys of 16 documents' files**: the IN100's 13 (the customer code's byte order is in doubt, below), and
    the keys of the 12 documents that state no customer code (the IN2100's, LP650's, SP4800's, SP4805's, X2's and X3's, and the hex16 ones),
    which are read with 0x4E87, and the IN5148HD's `Computer` row, whose binary is not its decimal's."""
    per: dict[str, list] = {}
    for stem, doc in infocus_files().items():
        row = per.setdefault(infocus_doc_of(doc), [0, 0, 0])      # files, and the most keys and Untested keys a file of the document has
        row[0] += 1
        row[1] = max(row[1], len(doc["keys"]))
        row[2] = max(row[2], sum(s["forms"][0]["confidence"] == "untested" for s in doc["keys"].values()))
    assert {k: tuple(v) for k, v in per.items()} == {
        "InFocus-IN3100-IRCodes.pdf": (12, 59, 0), "InFocus-IN100-IRCodes.pdf": (2, 13, 13), "InFocus-IN1100-IRCodes.pdf": (8, 59, 0),
        "InFocus-IN2100-IRCodes.pdf": (1, 55, 55), "InFocus-IN20-IRCodes.pdf": (6, 59, 0), "InFocus-IN32-IRcodes.pdf": (2, 41, 0),
        "IN5140_IRCodes.pdf": (1, 50, 0), "IN5148HD-IR-Codes.pdf": (1, 44, 1), "InFocus-IN5300-IRCodes.pdf": (2, 62, 0),
        "InFocus-IN5310-IRCodes.pdf": (2, 29, 0), "IN5312a_IRCodes.pdf": (1, 29, 0), "IN5316HDa_IRCodes.pdf": (1, 29, 0), "InFocus-IN5500-IRCodes.pdf": (8, 62, 0), "IN7x_IR_Codes.pdf": (3, 32, 0),
        "IN2x_IR_Codes.pdf": (3, 41, 0), "Mondopad_IR_codes.pdf": (3, 22, 0), "InFocus-LP120-IRCodes.pdf": (1, 29, 0),
        "InFocus-LP240-LP250-IRCodes.pdf": (2, 20, 20), "540-640-IR_codes.pdf": (2, 20, 20), "InFocus-LP600-IRCodes.pdf": (1, 29, 0),
        "InFocus-LP650-IRCodes.pdf": (1, 46, 46), "InFocus-LP815-IRCodes.pdf": (2, 20, 20), "InFocus-LP840-IRCodes.pdf": (3, 19, 19),
        "InFocus-LS110-IRCodes.pdf": (1, 20, 0), "InFocus-SP4800-IRCodes.pdf": (1, 41, 41), "InFocus-SP4805-IRCodes.pdf": (1, 41, 41),
        "InFocus-SP5000-IRCodes.pdf": (1, 20, 20), "InFocus-SP7200-IRCodes.pdf": (1, 29, 0), "InFocus-SP7205-IRCodes.pdf": (1, 29, 0),
        "X2_IRCodes.pdf": (1, 41, 41), "X3_IRCodes.pdf": (1, 41, 41),
        "InFocus-IN110-IRCodes.pdf": (3, 48, 0), "IN3114_IN3116_IRCodes.pdf": (5, 48, 0), "InFocus-IN3910-IRCodes.pdf": (2, 48, 0),
        "InFocus-IN3118HD-IRCodes.pdf": (1, 29, 0),
        "InFocus-IN3900LB-IRCodes.pdf": (1, 62, 62), "SP8604_IRCodes.pdf": (2, 30, 0), "IN5550-IR-Codes.pdf": (2, 34, 34)}
    files = infocus_files()
    assert len(files) == 92 and sum(len(d["keys"]) for d in files.values()) == 4_053
    tiers = Counter(s["forms"][0]["confidence"] for d in files.values() for s in d["keys"].values())
    assert tiers == {"plausible": 3_435, "untested": 618}
    assert {d["protocol"]["name"] for d in files.values()} == {"NEC1"} and all(d["manufacturer"] == "InFocus" for d in files.values())
    # the two documents of the IN72, IN74 and IN76, and the names other sources already have (R15)
    assert {"IN72 (InFocus table)", "IN74", "IN76", "IN72 (IN7x_IR_Codes.pdf)", "IN74 (IN7x_IR_Codes.pdf)", "IN76 (IN7x_IR_Codes.pdf)",
            "SP4805 (InFocus table)"} <= {d["model"] for d in files.values()}
    assert files["IN72__InFocus_table"]["controls"] == ["InFocus IN72"]
    # a model the document does not name says so
    assert "the document does not name IN32, and the PDF is served on the page of IN32" in files["IN32"]["keys"]["KEY_MENU"]["forms"][0]["source"]
    assert "the document does not name IN34, and the PDF is served on the page of IN34" in files["IN34"]["keys"]["KEY_MENU"]["forms"][0]["source"]
    assert "does not name" not in files["LP120"]["keys"]["KEY_MENU"]["forms"][0]["source"]
    # the three customer codes: 0x4E87 (device 0x87, subdevice 0x4E, low byte first), the IN100's 0x83F4 and the Mondopad's 0x0080
    for model, expected in (("IN1100", ("0x87", "0x4E")), ("IN102", ("0x83", "0xF4")), ("SP8604", ("0x31", "0xCE")), ("IN5550", ("0x31", "0xCE")), ("INF5520", ("0x80", "0x00")), ("LP120", ("0x87", "0x4E"))):
        assert {(s["forms"][0]["device"], s["forms"][0]["subdevice"]) for s in files[model]["keys"].values()} == {expected}, model
    assert files["LP650"]["keys"]["KEY_POWER"]["forms"][0]["source"].count("states no customer code") == 1
    assert files["IN1100"]["keys"]["KEY_POWER"]["forms"][0]["source"].endswith("customer code 0x4E87, as the document states")
    # the IN5148HD's table has a row whose binary is not its decimal's; the LP840's gives one code to two functions
    computer = files["IN5148HD"]["keys"]["KEY_COMPUTER"]["forms"][0]
    assert computer["confidence"] == "untested" and "binary 1000 1001 (0x89) are not one code" in computer["source"]
    assert files["LP840"]["keys"]["KEY_EFFECT"]["forms"][0]["function"] == files["LP840"]["keys"]["KEY_SOURCE_1"]["forms"][0]["function"] == "0x2B"
    assert "the table gives this code to 'Effect' and 'Source 1'" in files["LP840"]["keys"]["KEY_EFFECT"]["forms"][0]["source"]
    # the "a" series' two documents (served by InFocus's image host: no Wayback snapshot to name) are the IN5310's table, key for key, and each says whose file it is
    twin = {n: (s["label"], s["forms"][0]["function"]) for n, s in files["IN5310"]["keys"].items()}
    for model in ("IN5312a", "IN5316HDa"):
        assert {n: (s["label"], s["forms"][0]["function"]) for n, s in files[model]["keys"].items()} == twin
        source = files[model]["keys"]["KEY_POWER_ON"]["forms"][0]["source"]
        assert f"(retrieved 2026-10-16) page 1 row 'Power On' (3Ah): NEC1 device 135 subdevice 78 function 58, customer code 0x4E87, as the document states; " \
               f"the document names no model: the file is `{model}_IRCodes.pdf` on InFocus's image host" in source and "Wayback" not in source
    # the IN5310's arrow keys are named and its Light key sends no code
    assert {"Up", "Down", "Left", "Right"} <= {s["label"] for s in files["IN5310"]["keys"].values()} and "Light" not in {s["label"] for s in files["IN5310"]["keys"].values()}


def test_every_code_like_cell_of_an_infocus_document_is_a_row_of_it():
    """The count that found the rows the first reading missed (12 `Discrete ...` rows of the IN1100's and of the IN3100's, 2 of the IN20's,
    5 of the IN2100's): in each pinned document the cells that look like a data code (`0x02`, `02h`, a four-digit `0xE817`, a `K01` row's
    code) are as many as the entries read from it, and the IN5310's one row with no code is the one `n/a`."""
    snapshot = load_snapshot(SNAPSHOT / "infocus")
    assert len({d.file for d in infocus.DOCUMENTS}) == len(snapshot.documents) == 38
    read: dict[str, list] = {}
    for document in infocus.DOCUMENTS:
        rows = snapshot.sheet_of(document.file, document.sheet)
        entries, no_code = infocus.read(document, rows, {e.number: e for e in read.get(document.names_from, [])})
        read[document.file] = entries
        if document.layout in ("ids0", "ids1"):             # the IN5550's two tables in one: 33 keys in the first, 34 in the second
            assert len(entries) == {"ids0": 33, "ids1": 34}[document.layout]
            continue
        if document.layout == "keycodes_image":              # the SP8604's: the first row is the customer code
            assert len(entries) == len(rows) - 1 == 30
            continue
        if document.sheet == "transcribed":         # the image tables: a row of the transcription is an entry or the one row with no code
            assert len(entries) + len(no_code) == len(rows), document.file
            assert len(no_code) == (1 if document.layout == "keyed_image" else 0)
            continue
        if document.sheet == "pages":
            cells = [infocus._clean(r[1]) for r in rows]
        else:
            cells = [infocus._clean(c) for _, cs in infocus.cells_of(rows) for c in cs]
        if document.layout == "keyno":
            like = [c for c in cells if re.fullmatch(r"K\d+", c)]
        else:
            like = [c for c in cells if re.fullmatch(r"0[xX][0-9A-Fa-f]{2,4}|[0-9A-Fa-f]{2}[hH]", c) and not c.startswith("0x4E87")]
        assert len(like) == len(entries), (document.file, len(like), len(entries))
        assert len(no_code) == (1 if document.layout == "numbered" else 0)


def test_other_sources_infocus_remotes_send_the_codes_the_tables_list_and_the_documents_disagree_where_they_do():
    """An independent check: the ledger's nine InFocus remotes at 0x4E87 (device 0x87, subdevice 0x4E: six from the IR Blaster database and
    three JP1 upgrades) hold 219 keys, **215 of them (98.2%) a code some table lists**. **The IN100's 13 functions are all in the IR
    Blaster database's one remote at 0x83/0xF4 (NEC2)**: the IN100's keys are read at that address (its 0x83F4 first byte first, D301)
    and stay Untested, since the document's notation is not the 0x4E87 documents'. **The IN5140's table has `PAGE UP` at 0x25 and `PAGE DOWN` at 0x24, and the IN5148HD's has them the other way**
    (`Page Up` 0x24, `Page Down` 0x25), as the LP650's `Next Slide` 0x24 and `Previous Slide` 0x25 send PageUp and PageDn: the IN5140's
    two names are swapped against every other table, and its keys say so."""
    ours = {(f["device"], f["subdevice"], f["function"]) for d in infocus_files().values() for s in d["keys"].values() for f in s["forms"]}
    remotes = total = hit = 0
    for path in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in path.parts or "infocus" not in path.parent.name.lower().replace(" ", ""):
            continue
        doc = load(path)
        codes = [(f["device"], f.get("subdevice"), f["function"]) for spec in doc["keys"].values() for f in spec["forms"]
                 if f.get("type") == "irp" and (f["device"], f.get("subdevice")) == ("0x87", "0x4E")]
        if codes:
            remotes += 1
            total += len(codes)
            hit += sum(1 for c in codes if c in ours)
    assert (remotes, total, hit) == (9, 219, 215)
    other = load(ROOT / "remotes" / "irblaster" / "INFOCUS" / "1739-NEC2.json")
    theirs = {(f["device"], f["subdevice"], f["function"]) for spec in other["keys"].values() for f in spec["forms"]}
    mine = {(f["device"], f["subdevice"], f["function"]) for spec in infocus_files()["IN102"]["keys"].values() for f in spec["forms"]}
    assert len(mine) == 13 and {f for _, _, f in mine} == {f for _, _, f in theirs}      # the remote has Menu and Exit on one code (0x0F)
    assert {(d, s) for d, s, _ in mine} == {("0x83", "0xF4")} == {(d, s) for d, s, _ in theirs}       # read first byte first, as the remote has it (D301)
    page = {m: {s["label"]: s["forms"][0]["function"] for s in d["keys"].values()} for m, d in infocus_files().items() if m in ("IN5140", "IN5148HD", "LP650")}
    assert (page["IN5140"]["PAGE UP"], page["IN5140"]["PAGE DOWN"]) == ("0x25", "0x24")
    assert (page["IN5148HD"]["Page Up"], page["IN5148HD"]["Page Down"]) == ("0x24", "0x25")
    assert (page["LP650"]["Next Slide"], page["LP650"]["Previous Slide"]) == ("0x24", "0x25")


def test_the_solo_movie_notes_ir_tables_are_two_tables_side_by_side_of_a_code_and_a_command_and_a_name_may_go_on_in_the_next_line():
    """``16-12  Standby`` for each row, the AMP, TUN, AUX and DVD tables under ``System code 16``, ``17``, ``21`` and ``25``; the right table's
    ``HDMI Resolution SD`` goes on in an indented line (``Interlaced``) and ``(DV137/9 only)`` stays in the label; a code whose system is none the
    page heads is doubtful, and the text before the ``IR remote commands`` page is not read."""
    rows = arcam.code_first_rows([
        ["2", "50", "59", "16-1", "120", "Not an IR row"],
        ["15", "23", "28", "IR remote commands"], ["15", "92", "50", "AMP commands", "298", "TUN commands"],
        ["15", "108", "50", "System code 16", "298", "System code 17"], ["15", "123", "59", "Decimal Code", "120", "Command", "307", "Decimal Code", "371", "Command"],
        ["15", "134", "59", "16-0", "120", "Source SAT", "307", "17-0", "371", "Tuner Number 0"],
        ["15", "145", "59", "16-12", "120", "Standby", "307", "17-5", "371", "Tuner Number 5"],
        ["16", "439", "50", "System code 25", "329", "25-90", "425", "HDMI Resolution SD"],
        ["16", "448", "439", "Interlaced"], ["16", "459", "329", "25-91", "425", "HDMI Resolution SD"],
        ["16", "467", "59", "25-0", "154", "DVD Number 0", "439", "Progressive"],
        ["16", "500", "329", "25-94", "425", "HDMI Resolution 1080p"], ["16", "510", "59", "25-4", "154", "DVD Number 4", "439", "(DV137/9 only)"],
        ["16", "520", "59", "30-1", "154", "Odd one"]])
    assert [(r.function, r.system, r.command) for r in rows] == [
        ("Source SAT", 16, 0), ("Tuner Number 0", 17, 0), ("Standby", 16, 12), ("Tuner Number 5", 17, 5),
        ("HDMI Resolution SD Interlaced", 25, 90), ("HDMI Resolution SD Progressive", 25, 91), ("DVD Number 0", 25, 0),
        ("HDMI Resolution 1080p (DV137/9 only)", 25, 94), ("DVD Number 4", 25, 4), ("Odd one", 30, 1)]
    assert [bool(r.doubt) for r in rows] == [False] * 9 + [True]
    with pytest.raises(ValueError, match="no page headed"):
        arcam.code_first_rows([["2", "50", "59", "16-1", "120", "Not an IR row"]])


def test_the_solo_movie_2_1_and_5_1_notes_of_2008_are_two_files_of_232_keys_with_the_typo_left_out_and_the_second_2_1_kept_under_another_name():
    """SH194 issue 3 (the Solo Movie 2.1 and 5.1: its cover lists both): the AMP codes (system 16, 59 keys), the tuner's (17, 59), the iPod's (21, 30)
    and the DVD's (25, 84), 232 keys each, all Plausible. **The `25-611` of its `Slow Backward x1/4` is a typo (the order says 25-61) and is no code**:
    the importer reports it for each file. The SH284 notes (section 74) already have a `Solo Movie 2.1`, so this one is `Solo Movie 2.1 (SH194)`."""
    files = arcam_files(("Solo_Movie_2.1__SH194", "Solo_Movie_5.1", "Solo_Movie_2.1"))
    assert sorted(d["model"] for d in files.values()) == ["Solo Movie 2.1", "Solo Movie 2.1 (SH194)", "Solo Movie 5.1"]
    a, b = files["Solo_Movie_2.1__SH194"], files["Solo_Movie_5.1"]
    assert len(a["keys"]) == len(b["keys"]) == 232 and codes_of(a) == codes_of(b)
    assert Counter(int(s["forms"][0]["device"], 0) for s in b["keys"].values()) == {16: 59, 17: 59, 21: 30, 25: 84}
    assert all(s["forms"][0]["confidence"] == "plausible" for s in b["keys"].values())
    labels = [s["label"] for s in b["keys"].values()]
    assert "HDMI Resolution SD Interlaced" in labels and "HDMI Resolution 1080p (DV137/9 only)" in labels and "Slow Backward x1/4" not in labels
    assert codes_of(b)["Source SAT"] == ("0x10", "0x00") and b["keys"]["KEY_STANDBY_2"]["forms"][0]["device"] == "0x11"
    assert "@2c160215 (retrieved 2026-10-15) page 15 row 'Source SAT' (16-0)" in b["keys"]["KEY_SOURCE_SAT"]["forms"][0]["source"]
    assert len(files["Solo_Movie_2.1"]["keys"]) == 83                                               # the SH284 notes' file is as it was


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


# --- Cary Audio ----------------------------------------------------------------------------------------------------


HEADER6 = ["Key Printing", "Function", "System Code A", "System Code B", "Key Command", "Notes"]


def test_a_cary_table_is_a_header_of_six_columns_and_rows_of_a_printing_a_function_two_system_codes_and_a_command():
    rows = [["1", "1", *HEADER6], ["1", "1", "POWER", "POWER ON / OFF", "0x10", "0x09", "0x0C", ""],
            ["1", "1", "-  VOLUME", "VOLUME DOWN", "0x10", "0x09", "0x11", "a note"]]
    assert cary.parse(rows) == [(2, "POWER", "POWER ON / OFF", 16, 9, 12, ""), (3, "- VOLUME", "VOLUME DOWN", 16, 9, 17, "a note")]
    for bad in ([["1", "1", "Key", "Function"]], [["1", "1", *HEADER6], ["1", "1", "POWER", "POWER ON / OFF", "0x10", "0x09"]],
                [["1", "1", *HEADER6], ["1", "1", "POWER", "POWER ON / OFF", "0x10", "0x09", "zz", ""]]):
        with pytest.raises(ValueError):
            cary.parse(bad)


def test_a_cary_label_is_the_key_printing_unless_it_is_a_sign_beside_a_word():
    assert cary.label_of("POWER", "POWER ON / OFF") == "POWER" and cary.label_of("1", "NUMBER KEY PAD 1") == "1"
    assert cary.label_of("- VOLUME", "VOLUME DOWN") == "VOLUME DOWN" and cary.label_of("VOLUME +", "VOLUME UP") == "VOLUME UP"
    assert cary.label_of("*.-/", "NUMBER KEY PAD *.-/") == "*.-/" and cary.label_of("USB / SD CARD", "USB/ SD CARD INPUT") == "USB / SD CARD"


def cary_files() -> dict[str, dict]:
    return {load(p)["model"]: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "cary").glob("*.json"))}


def test_the_two_cary_files_are_the_two_system_codes_of_one_table_of_38_keys():
    """``DMS`` (system code A, 0x10) and ``DMS (system code B)`` (0x09): the same 38 commands under each, 76 keys, all Plausible."""
    files = cary_files()
    assert sorted(files) == ["DMS", "DMS (system code B)"]
    for doc in files.values():
        assert doc["manufacturer"] == "Cary Audio" and doc["controls"] == ["Cary Audio DMS"] and len(doc["keys"]) == 38
        assert doc["protocol"] == {"name": "RC5", "carrierHz": 36000, "minSends": 1}
        assert {s["forms"][0]["confidence"] for s in doc["keys"].values()} == {"plausible"}
    a, b = files["DMS"]["keys"], files["DMS (system code B)"]["keys"]
    assert {s["forms"][0]["device"] for s in a.values()} == {"0x10"} and {s["forms"][0]["device"] for s in b.values()} == {"0x09"}
    assert list(a) == list(b) and [s["forms"][0]["function"] for s in a.values()] == [s["forms"][0]["function"] for s in b.values()]
    assert a["KEY_POWER"]["forms"][0]["function"] == "0x0C" and a["KEY_VOLUME_UP"]["forms"][0]["function"] == "0x10"
    assert a["KEY_VOLUME_DOWN"]["label"] == "VOLUME DOWN" and "'- VOLUME'" in a["KEY_VOLUME_DOWN"]["forms"][0]["source"]
    assert a["KEY_LIFT"]["forms"][0]["function"] == "0x1F" and "function 'NAVIGATION LIFT'" in a["KEY_LIFT"]["forms"][0]["source"]
    assert a["KEY_NETWORK"]["forms"][0]["function"] == "0x22" and "function 'NETWORD INPUT'" in a["KEY_NETWORK"]["forms"][0]["source"]
    assert "the table's system code B" in b["KEY_POWER"]["forms"][0]["source"] and "RC5 system 9 command 12" in b["KEY_POWER"]["forms"][0]["source"]
    report = (ROOT / oi.IMPORT_ROOT / oi.REPORT).read_text(encoding="utf-8")
    figure = lambda what: int(re.search(rf"\| cary: {what} \| ([\d,]+) \|", report)[1].replace(",", ""))      # noqa: E731
    assert [figure(w) for w in ("rows read", "files", "keys")] == [38, 2, 76]


def test_other_sources_hold_most_of_the_cary_codes_and_agree_on_three_names():
    """The independent check (D280): of the table's 76 codes **67 are held by other sources' RC-5 remotes at the same system code and
    command** (all 38 of system 16, 29 of the 38 of system 9), **and in only 3 does another source's label name the same function: ``POWER``
    (0x0C) and ``VOLUME UP`` and ``DOWN`` (0x10, 0x11) at system 16.** The other 35 keys of system 16 are the DMS's own assignment (its keys
    ``5``, ``6``, ``7``, ``9`` and ``0`` send what the standard sends for ``1`` to ``5``), and the keys of system 9 are nothing like
    what the other remotes use there: 0 of 58 names agree."""
    from remote_ledger.keys import load_vocabulary

    vocabulary = load_vocabulary()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())      # noqa: E731
    ours: dict[tuple[int, int], set[str]] = {}
    for doc in cary_files().values():
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            ours.setdefault((int(form["device"], 0), int(form["function"], 0)), set()).add(spec["label"])
    seen: dict[tuple[int, int], set[str]] = {}
    for path in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in path.parts:
            continue
        doc = load(path)
        if doc.get("protocol", {}).get("name") != "RC5":
            continue
        for spec in doc["keys"].values():
            for form in spec["forms"]:
                if form.get("type") == "irp" and form.get("device") is not None:
                    key = (int(form["device"], 0), int(form["function"], 0))
                    if key in ours:
                        seen.setdefault(key, set()).add(spec["label"])
    held = [k for k in ours if k in seen]
    same = [k for k in held if any(squash(a) == squash(b) for a in ours[k] for b in seen[k])
            or ({vocabulary.resolve(x) for x in ours[k]} - {None}) & ({vocabulary.resolve(x) for x in seen[k]} - {None})]
    assert (len(ours), len(held), len([k for k in held if k[0] == 16]), len([k for k in held if k[0] == 9])) == (76, 67, 38, 29)
    assert sorted(same) == [(16, 0x0C), (16, 0x10), (16, 0x11)]


# --- NAD ---------------------------------------------------------------------------------------------------------


def nad_sheet(model: str, rows: list[tuple[int, str, str, str, str, str]], comment: str = "") -> list[list[str]]:
    """A page's cells as ``tools/official_sheets_to_csv.py`` writes them: the model, the header rows, then ``(y, function, address
    hex, data hex, address dec, data dec)`` rows."""
    out = [["1", "39", "40", f"Model {model}"],
           ["1", "64", "40", "Function", "181", "Format", "253", "Address", "303", "Data", "353", "Address", "404", "Data", "454", "Comment"],
           ["1", "78", "253", "Hex", "303", "Hex", "353", "Dec", "404", "Dec"]]
    for y, function, ah, dh, ad, dd in rows:
        out.append(["1", str(y), "40", function, "181", "NEC", "253", ah, "304", dh, "355", ad, "403", dd] + (["454", comment] if comment else []))
    out.append(["1", "738", "40", "April-27-12", "261", "IR Commands by Model", "516", "Page 52 of 127"])
    return out


def test_a_nad_page_is_a_model_a_header_with_columns_at_places_and_rows_of_cells_at_those_places():
    sheet = nad_sheet("T187", [(99, "A/V Preset 1", "877C", "3A", "34684", "58"), (112, "Zone Tune Up", "877C", "B5", "34684", "181")], "a note")
    model, rows = nad.parse(sheet)
    assert model == "T187" and rows == [(1, "A/V Preset 1", "NEC", 0x877C, 0x3A, "a note"), (1, "Zone Tune Up", "NEC", 0x877C, 0xB5, "a note")]
    assert nad.columns([nad.cells_of(r) for r in sheet]) == {"function": 40, "format": 181, "address_hex": 253, "data_hex": 303,
                                                             "address_dec": 353, "data_dec": 404, "comment": 454}
    assert nad.parse(nad_sheet("T187", [(99, "Mute", "877C", "94", "34684", "148")]))[1][0][5] == ""


def test_a_nad_row_whose_hex_and_decimal_are_not_one_number_or_that_has_no_column_is_an_error():
    for bad in ([(99, "Mute", "877C", "94", "34684", "149")], [(99, "Mute", "877C", "94", "34685", "148")]):
        with pytest.raises(ValueError, match="not one number"):
            nad.parse(nad_sheet("T187", bad))
    off = nad_sheet("T187", [(99, "Mute", "877C", "94", "34684", "148")])
    off[3][12] = "999"
    with pytest.raises(ValueError, match="in no column"):
        nad.parse(off)
    with pytest.raises(ValueError, match="no header row"):
        nad.parse(nad_sheet("T187", [])[:1] + [["1", "64", "40", "Function"]])
    two = nad_sheet("T187", []) + [["2", "39", "40", "Model T777"]]
    with pytest.raises(ValueError, match="2 models"):
        nad.parse(two)


NAD_TABLES = ("T175", "T765", "T775", "T775 (February 2008 table)", "T785", "T187", "T777", "T787")


def nad_all_files() -> dict[str, dict]:
    return {load(p)["model"]: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "nad").glob("*.json"))}


def nad_files() -> dict[str, dict]:
    """The eight tables of the T-series PDFs (D282 to D284)."""
    return {m: d for m, d in nad_all_files().items() if m in NAD_TABLES}


def test_the_eight_nad_files_are_the_seven_models_and_the_t775s_older_table():
    """Seven models and eight tables (the T775 has two, of February and March 2008): 797 keys, all Plausible, NEC1 device 0x87 subdevice 0x7C
    (the address 877C). The T187, T777, T785 and T787 and the T775 of March have the same 100 codes; the T175 lacks ``Speaker A`` and
    ``Speaker B`` and the T765 ``Audyssey``; the T775 of February has the same 100 codes under abbreviated names (``Vol +``)."""
    files = nad_files()
    assert {m: len(d["keys"]) for m, d in files.items()} == {
        "T175": 98, "T765": 99, "T775": 100, "T775 (February 2008 table)": 100, "T785": 100, "T187": 100, "T777": 100, "T787": 100}
    for doc in files.values():
        assert doc["manufacturer"] == "NAD" and doc["protocol"] == {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
        assert doc["controls"] == [f"NAD {doc['model'].split(' ')[0]}"]
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            assert (form["device"], form["subdevice"], form["confidence"]) == ("0x87", "0x7C", "plausible")
    codes = {m: {s["forms"][0]["function"]: s["label"] for s in d["keys"].values()} for m, d in files.items()}
    assert len({tuple(sorted(c)) for m, c in codes.items() if m not in ("T175", "T765")}) == 1
    t187 = codes["T187"]
    assert set(t187) - set(codes["T175"]) == {"0xCE", "0xCF"} and {t187[c] for c in set(t187) - set(codes["T175"])} == {"Speaker A", "Speaker B"}
    assert {t187[c] for c in set(t187) - set(codes["T765"])} == {"Audyssey"}
    assert codes["T777"] == codes["T787"] == codes["T785"] == codes["T775"] == t187
    old = codes["T775 (February 2008 table)"]
    assert set(old) == set(t187) and {c for c in old if old[c] != t187[c]} and old["0x88"] == "Vol +" and t187["0x88"] == "Volume Up"
    power = files["T187"]["keys"]["KEY_POWER_TOGGLE"]["forms"][0]
    assert power["function"] == "0x80" and "page 1 row 'Power Toggle' (NEC, address 877C, data 128 = 0x80)" in power["source"]
    assert files["T187"]["keys"]["KEY_ZONE_TUNE_UP"]["forms"][0]["function"] == "0xB5"
    report = (ROOT / oi.IMPORT_ROOT / oi.REPORT).read_text(encoding="utf-8")
    figure = lambda what: int(re.search(rf"\| nad: {what} \| ([\d,]+) \|", report)[1].replace(",", ""))      # noqa: E731
    assert [figure(w) for w in ("files", "keys")] == [42, 2_296]          # the eight tables' 797 keys and the Crestron files' 1,499


def test_the_nad_documents_are_members_of_three_zips_and_the_manifest_says_which():
    snapshot = load_snapshot(SNAPSHOT / "nad")
    assert sorted(snapshot.documents) == sorted([*nad.DOCUMENTS, nad.README, *(f"crestron_{s}.ir" for s in nad.CRESTRON)])
    for name, entry in snapshot.documents.items():
        assert entry["url"].startswith("https://cdn.shopify.com/s/files/1/") and entry["url"].endswith(
            ("NAD_TXX5_Control_Docs.zip", "NAD_TXX7_Control_Docs.zip", "NAD-CrestronIR.zip")), name
        assert entry["member"].endswith(("IR Commands.pdf", "IR_Commands.pdf", ".ir")) or entry["member"] == "readme.pdf"
    assert snapshot.documents["T775_IR_Commands_2008-02.pdf"]["member"] == "T775 IR Commands.pdf"
    assert snapshot.documents["crestron_receiver2_1.ir"]["member"] == "receiver2_1.ir" and snapshot.documents[nad.README]["member"] == "readme.pdf"


def test_other_sources_nad_remotes_hold_58_of_the_100_codes_and_name_43_alike():
    """The independent check (D284): the 100 distinct codes (NEC1 device 0x87 subdevice 0x7C) **are held by other sources' NEC1 remotes in
    58 cases** (15 remotes, the IR Blaster database's NAD ones), **and in 43 of them some other source's label is the same function by the
    vocabulary or the spelling**; the 15 that the comparison does not count are mostly the same function in other words (``Tuner 1`` is
    the remote's ``1``, ``A/V Preset 1`` its ``AV PRESET``). **42 codes are nobody else's.**"""
    from remote_ledger.keys import load_vocabulary

    vocabulary = load_vocabulary()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())      # noqa: E731
    ours: dict[tuple[int, int, int], set[str]] = {}
    for doc in nad_files().values():
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            ours.setdefault((int(form["device"], 0), int(form["subdevice"], 0), int(form["function"], 0)), set()).add(spec["label"])
    seen: dict[tuple[int, int, int], set[str]] = {}
    remotes: set[str] = set()
    for path in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in path.parts:
            continue
        doc = load(path)
        if doc.get("protocol", {}).get("name") != "NEC1":
            continue
        for spec in doc["keys"].values():
            for form in spec["forms"]:
                if form.get("type") == "irp" and form.get("device") is not None and form.get("subdevice") is not None:
                    key = (int(form["device"], 0), int(form["subdevice"], 0), int(form["function"], 0))
                    if key in ours:
                        seen.setdefault(key, set()).add(spec.get("label", ""))
                        remotes.add(path.name)
    held = [k for k in ours if k in seen]
    same = [k for k in held if any(squash(a) == squash(b) for a in ours[k] for b in seen[k])
            or ({vocabulary.resolve(x) for x in ours[k]} - {None}) & ({vocabulary.resolve(x) for x in seen[k]} - {None})]
    assert (len(ours), len(held), len(same), len(remotes)) == (100, 58, 43, 15)


def ir_frame(protocol: str, bits: str) -> bytes:
    head, tail = nad.FRAMES[protocol]
    return head + bytes(int(b) for b in bits) + tail


def lsb_first(*values: int) -> str:
    return "".join(str((v >> k) & 1) for v in values for k in range(8))


def test_a_crestron_key_is_a_head_32_bits_least_significant_first_and_a_tail_and_the_two_heads_are_the_two_protocols():
    """NEC's 9 ms lead (the timing table 21, 64, 86, 171, 343) and ``NECx1``'s 4.5 ms lead (21, 64, 171, 1692, 3707): the bits are the bytes
    0x87 0x7C 0x80 0x7F, the first bit the lowest."""
    for protocol in ("NEC1", "NECx1"):
        assert nad.decode_frame(ir_frame(protocol, lsb_first(0x87, 0x7C, 0x80, 0x7F))) == (protocol, (0x87, 0x7C, 0x80, 0x7F))
    good = ir_frame("NEC1", lsb_first(0x87, 0x7C, 0x80, 0x7F))
    assert nad.decode_frame(good[:-1]) is None and nad.decode_frame(good + b"\x00") is None
    assert nad.decode_frame(good.replace(b"\x05\x42\x06", b"\x05\x42\x07")) is None
    assert nad.decode_frame(ir_frame("NEC1", lsb_first(0x87, 0x7C, 0x80, 0x7F)).replace(bytes([0, 0, 0, 0, 0, 0, 0, 1]), bytes([0, 0, 0, 0, 0, 0, 0, 2]), 1)) is None


def test_a_crestron_file_is_records_a_header_names_an_ff_record_and_keys_and_the_readme_says_which_models():
    def row(i: int, tag: int, body: bytes) -> list[str]:
        return [str(i), f"{tag:02x}", body.hex()]
    rows = [row(0, 0xF0, b"IR"), row(1, 0xF4, b"5170, 5240"), row(2, 0xF7, b"CD"), row(3, 1, b"POWER"), row(4, 2, b"PLAY"), row(5, 0xFF, b""),
            row(6, 1, b"\x01"), row(7, 2, b"\x02")]
    header, names, keys = nad.parse_ir(rows)
    assert header == {0xF0: "IR", 0xF4: "5170, 5240", 0xF7: "CD"} and names == {1: "POWER", 2: "PLAY"} and keys == [(1, 6, b"\x01"), (2, 7, b"\x02")]
    readme = [["1", "Receivers/Preamps/Integrated Amps"], ["1", "receiver1.ir"], ["1", "118"], ["1", "receiver2_1.ir All Others"],
              ["1", "DVD Players"], ["1", "cd2.ir 5170, 5240, 5340"], ["1", "tv2.ir MR20, MR26"]]
    assert nad.readme_models(readme) == {"receiver1.ir": "118", "receiver2_1.ir": "All Others", "cd2.ir": "5170, 5240, 5340", "tv2.ir": "MR20, MR26"}
    head = {0xF4: "Receiver, Amplifier"}
    assert nad.crestron_models("r", "All Others", head) == ["Receiver, Amplifier (all others)"]
    assert nad.crestron_models("r", "Second Zone Commands", head) == ["Receiver, Amplifier"]
    assert nad.crestron_models("c", "5170, 5240", {0xF4: "5170, 5240"}) == ["5170", "5240"]
    with pytest.raises(ValueError, match="the readme says"):
        nad.crestron_models("c", "5170, 5240", {0xF4: "5170"})


def test_the_ir_records_the_sheet_tool_writes_are_length_tag_body_and_a_file_that_does_not_fit_is_an_error():
    import importlib.util

    spec = importlib.util.spec_from_file_location("official_sheets_to_csv", ROOT / "tools" / "official_sheets_to_csv.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    data = bytes([4, 0xF0, 0x49, 0x52, 5, 1, 0x41, 0x42, 0x43, 2, 0xFF, 0])
    assert tool.ir_records(data) == [["0", "f0", "4952"], ["1", "01", "414243"], ["2", "ff", ""]]
    for bad in (data[:-1], data + b"\x00", bytes([9, 0xF0, 0x49]) + b"\x00"):
        with pytest.raises(ValueError):
            tool.ir_records(bad)


def crestron_files() -> dict[str, dict]:
    """The 34 files of the Crestron IR files (D286 to D288)."""
    return {m: d for m, d in nad_all_files().items() if m not in NAD_TABLES}


def test_the_crestron_files_are_34_models_of_the_16_files_and_every_key_is_one_of_the_two_frames():
    """16 files, 645 keys, 34 models (a file for each model the readme lists, and four that say ``All Others`` or ``Second Zone``), 1,499
    keys: every key decodes, with the fourth byte the complement of the third. Fourteen files are NEC and two (the C660 and the ten T5xx
    DVD players) ``NECx1``."""
    files = crestron_files()
    assert len(files) == 34 and sum(len(d["keys"]) for d in files.values()) == 1_499
    assert {m: (d["protocol"]["name"], len(d["keys"])) for m, d in files.items() if m in ("5060", "5170", "5240", "5340", "5325", "C660", "T512", "T572",
                                                                                      "118", "L75", "L76", "L70", "L73", "L53", "L55", "MR13", "MR26")} == {
        "5060": ("NEC1", 18), "5170": ("NEC1", 34), "5240": ("NEC1", 34), "5340": ("NEC1", 34), "5325": ("NEC1", 20), "C660": ("NECx1", 50),
        "T512": ("NECx1", 47), "T572": ("NECx1", 47), "118": ("NEC1", 32), "L75": ("NEC1", 43), "L76": ("NEC1", 43), "L70": ("NEC1", 76),
        "L73": ("NEC1", 76), "L53": ("NEC1", 76), "L55": ("NEC1", 50), "MR13": ("NEC1", 28), "MR26": ("NEC1", 18)}
    assert {"Receiver, Amplifier (all others)", "Receiver, Amplifier (Second Zone)", "CD Player (all others)", "Tuner (all others)",
            "Tape Deck (all others)"} <= set(files)
    addresses = {m: {(s["forms"][0]["device"], s["forms"][0]["subdevice"]) for s in d["keys"].values()} for m, d in files.items()}
    assert addresses["5060"] == {("0x85", "0x6F")} and addresses["5170"] == {("0x2A", "0xD5")} and addresses["5325"] == {("0x87", "0x7E")}
    assert addresses["C660"] == {("0x2F", "0x2F")} and addresses["T532"] == {("0x2D", "0x2D")} and addresses["118"] == {("0x86", "0x0F")}
    assert addresses["MR13"] == {("0x40", "0xBF")} and addresses["MR20"] == {("0x04", "0xFB")}
    assert addresses["L70"] == addresses["L73"] == {("0x85", "0x6A"), ("0x85", "0x9A")} and addresses["L75"] == {("0x85", "0x6A"), ("0x85", "0x9A")}
    assert addresses["Tuner (all others)"] == addresses["Tape Deck (all others)"] == addresses["CD Player (all others)"] == {("0x87", "0x7C")}
    receiver = files["Receiver, Amplifier (all others)"]["keys"]
    assert receiver["KEY_POWER"]["forms"][0]["function"] == "0x80" and receiver["KEY_MUTE"]["forms"][0]["function"] == "0x94"
    assert receiver["KEY_MONO"]["forms"][0]["function"] == "0x83" and receiver["KEY_MONO_2"]["forms"][0]["function"] == "0x37"
    assert receiver["KEY_MONO"]["label"] == receiver["KEY_MONO_2"]["label"] == "MONO"
    cite = receiver["KEY_POWER"]["forms"][0]["source"]
    assert "crestron_receiver2_1.ir@" in cite and "key " in cite and "frame 87 7C 80 7F" in cite and "lists the file for All Others" in cite
    assert files["L75"]["keys"]["KEY_0"]["forms"][0]["source"].endswith("lists the file for L75, L76")
    assert [(k, s["forms"][0]["device"], s["forms"][0]["subdevice"]) for k, s in files["L75"]["keys"].items() if s["forms"][0]["subdevice"] != "0x6A"] == [
        ("KEY_DVD", "0x85", "0x9A")]
    report = (ROOT / oi.IMPORT_ROOT / oi.REPORT).read_text(encoding="utf-8")
    keys_read = sum(int(m[1].replace(",", "")) for m in re.finditer(r"\| nad: crestron_\w+\.ir: keys read \| ([\d,]+) \|", report))
    assert keys_read == 645 and "no name" not in report and "neither of the two frames" not in report


def test_the_generic_crestron_receiver_files_hold_the_t_series_codes_under_older_names():
    """The independent check (D288): the five files at 0x87/0x7C (the receivers, the second zone, the CD players, the tuner and the tape
    deck) have 196 distinct codes, **98 of them in the T187's table, and 40 of those 98 are named alike by the spelling or the vocabulary**;
    the other 58 are the same functions in older words (``CENTR-`` is ``Center Volume Down``, ``NEO6_CINEMA`` is ``DTS NEO:6 - Cinema``,
    ``PSET1`` is ``A/V Preset 1``) except three whose function was renamed between 2005 and 2008: ``HALL`` (0xC9) is the T187's ``Audyssey``,
    ``CD`` (0x85) its ``Front`` and ``PHONO`` (0x89) its ``MP``."""
    from remote_ledger.keys import load_vocabulary

    vocabulary = load_vocabulary()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())      # noqa: E731
    t187 = {(int(s["forms"][0]["device"], 0), int(s["forms"][0]["subdevice"], 0), int(s["forms"][0]["function"], 0)): s["label"]
            for s in nad_files()["T187"]["keys"].values()}
    files = crestron_files()
    ours: dict[tuple[int, int, int], set[str]] = {}
    for model in ("CD Player (all others)", "Receiver, Amplifier (all others)", "Receiver, Amplifier (Second Zone)", "Tape Deck (all others)", "Tuner (all others)"):
        for spec in files[model]["keys"].values():
            form = spec["forms"][0]
            ours.setdefault((int(form["device"], 0), int(form["subdevice"], 0), int(form["function"], 0)), set()).add(spec["label"])
    both = [k for k in ours if k in t187]
    same = [k for k in both if squash(t187[k]) in {squash(x) for x in ours[k]} or (vocabulary.resolve(t187[k]) and vocabulary.resolve(t187[k]) in {vocabulary.resolve(x) for x in ours[k]})]
    assert (len(ours), len(both), len(same)) == (196, 98, 40)
    assert ours[(0x87, 0x7C, 0xC9)] == {"HALL"} and t187[(0x87, 0x7C, 0xC9)] == "Audyssey" and ours[(0x87, 0x7C, 0x85)] == {"CD"} and ours[(0x87, 0x7C, 0x89)] == {"PHONO"}


def test_other_sources_hold_the_crestron_codes_of_the_models_they_have_remotes_for():
    """The independent check (D288): by file, the share of its codes (protocol, device, subdevice, function) that another source's remote
    holds: the DVD players' ``NECx1`` 0x2D/0x2D 47 of 47 (59 remotes), the L55's 0x85/0x9A 49 of 50, the L75's 43 of 43 and the L70's 76 of
    76 (0x85/0x6A and 0x85/0x9A), the CD player and the tuner at 0x87/0x7C 38 of 46 and 24 of 28; and **none of the 5060's (0x85/0x6F), the
    5325's (0x87/0x7E), the C660's (0x2F/0x2F) nor the 118's (0x86/0x0F)**."""
    files = crestron_files()
    wanted: dict[str, set[tuple[str, int, int, int]]] = {}
    for model in ("5060", "5325", "C660", "118", "T512", "L55", "L75", "L70", "CD Player (all others)", "Tuner (all others)", "MR13"):
        wanted[model] = {(files[model]["protocol"]["name"], int(s["forms"][0]["device"], 0), int(s["forms"][0]["subdevice"], 0), int(s["forms"][0]["function"], 0))
                         for s in files[model]["keys"].values()}
    every = set().union(*wanted.values())
    held: set[tuple[str, int, int, int]] = set()
    for path in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in path.parts:
            continue
        doc = load(path)
        name = doc.get("protocol", {}).get("name")
        if name not in ("NEC1", "NECx1"):
            continue
        for spec in doc["keys"].values():
            for form in spec["forms"]:
                if form.get("type") == "irp" and form.get("device") is not None and form.get("subdevice") is not None:
                    key = (name, int(form["device"], 0), int(form["subdevice"], 0), int(form["function"], 0))
                    if key in every:
                        held.add(key)
    shares = {m: (len(c & held), len(c)) for m, c in wanted.items()}
    assert shares == {"5060": (0, 18), "5325": (0, 20), "C660": (0, 50), "118": (0, 32), "T512": (47, 47), "L55": (49, 50), "L75": (43, 43), "L70": (76, 76),
                      "CD Player (all others)": (38, 46), "Tuner (all others)": (24, 28), "MR13": (28, 28)}


# --- Simaudio ----------------------------------------------------------------------------------------------------


def test_simaudios_model_lists_label_glyphs_and_digits_are_read_as_written():
    assert simaudio.models_of("Moon i-3, i-5, P-3, P-5") == ("Moon i-3", "Moon i-5", "Moon P-3", "Moon P-5")
    assert simaudio.models_of("Moon Nova and Moon Eclipse") == ("Moon Nova", "Moon Eclipse")
    assert simaudio.models_of("Moon Stellar") == ("Moon Stellar",)
    assert simaudio.label_of("0(make CD input direct access):") == ("0", "make CD input direct access")
    assert simaudio.label_of("▲ (Cursor Up)") == ("Cursor Up", "▲ (Cursor Up)")
    assert simaudio.label_of("OK (Acknowledge)") == ("OK (Acknowledge)", "") and simaudio.label_of("Power :") == ("Power", "")


def test_a_simaudio_page_is_a_heading_models_a_protocol_a_code_and_bullets_of_a_name_and_a_number():
    """Bullets are a ``-`` line and the lines after it: the name and the number may be on two lines (``Power :`` / ``12``) or one
    (``Search+ : 52``, ``Shuffle`` / ``1C``); the page's commands are decimal for RC-5 and hexadecimal where it says so, and a
    page with no bullets is none."""
    report = Report()
    rows = [["1", "Table of IR codes"], ["1", "Rev.: 1.1 - december, 2002"], ["1", "Amplifiers"], ["1", "Æ Moon i-3, P-5"],
            ["1", "Protocol :  Philips RC-5"], ["1", "System code : 16"], ["1", "Command codes :"],
            ["1", "-"], ["1", "Power :"], ["1", "12"], ["1", "-"], ["1", "Search+ : 52"], ["1", "-"], ["1", "0(make CD input direct access):  0"],
            ["1", "Note : only the P3 has those"], ["1", "inputs."], ["1", "Page 1 of 2"],
            ["2", "Table of IR codes - Simaudio"], ["2", "DVD players"], ["2", "Æ Moon Stellar and Moon Orion"], ["2", "Protocol :  Philips RC-6"],
            ["2", "Mode code : 00"], ["2", "Control code: 04    (Customer code)"], ["2", "Command codes : (hexadecimal)"],
            ["2", "-"], ["2", "Standby :"], ["2", "C"], ["2", "-"], ["2", "Shuffle"], ["2", "1C"], ["2", "-"], ["2", "Broken"], ["2", "Page 2 of 2"]]
    amplifiers, dvd = simaudio.parse(rows, report)
    assert (amplifiers.kind, amplifiers.models, amplifiers.protocol, amplifiers.device, amplifiers.base) == (
        "Amplifiers", ("Moon i-3", "Moon P-5"), "RC5", 16, 10)
    assert [(t, c) for t, c, _ in amplifiers.items] == [("Power : 12", 12), ("Search+ : 52", 52), ("0(make CD input direct access):  0", 0)]
    assert amplifiers.note == "Note : only the P3 has those inputs."
    assert (dvd.kind, dvd.models, dvd.protocol, dvd.device, dvd.base) == ("DVD players", ("Moon Stellar", "Moon Orion"), "RC6", 4, 16)
    assert [(t, c) for t, c, _ in dvd.items] == [("Standby : C", 12), ("Shuffle 1C", 28)]
    assert report.unrepresented[(simaudio.MAKER, simaudio.FILE, "a bullet that is not a name and a number")] == 1


def simaudio_files() -> dict[str, dict]:
    return {load(p)["model"]: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "simaudio").glob("*.json"))}


def test_the_eight_simaudio_files_are_the_moon_products_the_three_pages_name():
    """The four amplifiers have 15 keys (system 16: power, input, mute, volume, balance and the eight inputs), the two CD players 20
    (system 20) and the two DVD players 35 (RC-6, control code 4): 70 bullets, 170 keys, all Plausible."""
    files = simaudio_files()
    assert {m: (d["protocol"]["name"], len(d["keys"])) for m, d in files.items()} == {
        "Moon i-3": ("RC5", 15), "Moon i-5": ("RC5", 15), "Moon P-3": ("RC5", 15), "Moon P-5": ("RC5", 15),
        "Moon Nova": ("RC5", 20), "Moon Eclipse": ("RC5", 20), "Moon Stellar": ("RC6", 35), "Moon Orion": ("RC6", 35)}
    for doc in files.values():
        assert doc["manufacturer"] == "Simaudio" and doc["controls"] == [f"Simaudio {doc['model']}"]
        assert {s["forms"][0]["confidence"] for s in doc["keys"].values()} == {"plausible"}
    devices = {m: {s["forms"][0]["device"] for s in d["keys"].values()} for m, d in files.items()}
    assert devices["Moon i-5"] == {"0x10"} and devices["Moon Eclipse"] == {"0x14"} and devices["Moon Orion"] == {"0x04"}
    stellar = files["Moon Stellar"]["keys"]
    assert stellar["KEY_STANDBY"]["forms"][0]["function"] == "0x0C" and stellar["KEY_MENU"]["forms"][0]["function"] == "0xD1"
    assert stellar["KEY_CURSOR_UP"]["forms"][0]["function"] == "0x58" and "'▲ (Cursor Up)'" in stellar["KEY_CURSOR_UP"]["forms"][0]["source"]
    nova = files["Moon Nova"]["keys"]
    assert nova["KEY_EJECT"]["forms"][0]["function"] == "0x2D" and nova["KEY_0"]["forms"][0]["function"] == "0x00"
    assert "only the P3 has those eight inputs" in files["Moon i-3"]["keys"]["KEY_3"]["forms"][0]["source"]
    assert "page's note" not in files["Moon i-3"]["keys"]["KEY_POWER"]["forms"][0]["source"]
    report = (ROOT / oi.IMPORT_ROOT / oi.REPORT).read_text(encoding="utf-8")
    figure = lambda what: int(re.search(rf"\| simaudio: {what} \| ([\d,]+) \|", report)[1].replace(",", ""))
    assert [figure(w) for w in ("bullets read", "files", "keys")] == [70, 8, 170]


def test_other_sources_hold_the_simaudio_codes_and_name_them_alike():
    """The independent check (D269): the table's 70 distinct codes (35 RC-5 and 35 RC-6) **are held by other sources' remotes of the same
    protocol and device in 69 cases** (RC-5 system 16 and 20; RC-6 device 4), and **in 64 of them some other source's label is the
    same function by the vocabulary or the spelling**. The five that the comparison does not count are the same functions in other
    words (``Search+`` is ``FORWARD`` or ``DVD FWD``, ``OK (Acknowledge)`` is ``OK``, ``Bal R`` and ``Bal L`` are ``Balance Right`` and
    ``Left``) except ``A/B Repeat`` (RC-6 0x3D), which other remotes use for ``AV/SOURCE`` and ``EJECT``. **One code is nobody else's:
    ``Search-``, RC-6 0x27.**"""
    from remote_ledger.keys import load_vocabulary

    vocabulary = load_vocabulary()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())      # noqa: E731
    ours: dict[tuple[str, int, int], set[str]] = {}
    for doc in simaudio_files().values():
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            ours.setdefault((doc["protocol"]["name"], int(form["device"], 0), int(form["function"], 0)), set()).add(spec["label"])
    seen: dict[tuple[str, int, int], set[str]] = {}
    for path in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in path.parts:
            continue
        doc = load(path)
        protocol = doc.get("protocol", {}).get("name")
        if protocol not in ("RC5", "RC6"):
            continue
        for spec in doc["keys"].values():
            for form in spec["forms"]:
                if form.get("type") == "irp" and form.get("device") is not None:
                    key = (protocol, int(form["device"], 0), int(form["function"], 0))
                    if key in ours:
                        seen.setdefault(key, set()).add(spec["label"])
    held = [k for k in ours if k in seen]
    same = [k for k in held if any(squash(a) == squash(b) for a in ours[k] for b in seen[k])
            or ({vocabulary.resolve(x) for x in ours[k]} - {None}) & ({vocabulary.resolve(x) for x in seen[k]} - {None})]
    assert (len(ours), len(held), len(same)) == (70, 69, 64)
    assert [k for k in ours if k not in seen] == [("RC6", 4, 0x27)]


# --- Rotel -------------------------------------------------------------------------------------------------------


def rows_of_lines(*lines: str, page: int = 1) -> list[list[str]]:
    return [[str(page), line] for line in lines]


def test_a_rotel_title_lists_models_and_a_number_alone_takes_the_letters_before_it():
    assert rotel.title_models("ROTEL RSX-1550/1560 HEX CODES") == {"RSX1550", "RSX1560"}
    assert rotel.title_models("ROTEL RA-11/12 HEX CODES") == {"RA11", "RA12"}
    assert rotel.title_models("ROTEL P5/P5S2/X5/X5 Series 2 HEX CODES") == {"P5", "P5S2", "X5", "X5S2"}
    assert rotel.title_models("ROTEL CD14 and CD14MKII HEX CODES") == {"CD14", "CD14MKII"}
    assert rotel.documents([["1", "Model Number", "IR Hex Codes"],
                            ["1", "A12", "A12 IR Hex Codes {https://www.rotel.com/sites/default/files/product/ir/A12-A14%20HEX.pdf}"],
                            ["1", "A14", "A14 IR Hex Codes {https://www.rotel.com/sites/default/files/product/ir/A12-A14%20HEX.pdf}"],
                            ["1", "CD14MKII", "CD14MKII IR Hex Codes {https://www.rotel.com/sites/default/files/product/ir/CD14%26CD14MKII%20HEX.pdf}"],
                            ["2", "A12", "A12 IR Hex Codes {https://www.rotel.com/sites/default/files/product/ir/A12-A14%20HEX.pdf}"]]) == {
        "A12-A14_HEX.pdf": ["A12", "A14"], "CD14andCD14MKII_HEX.pdf": ["CD14MKII"]}


def test_an_nec_frame_in_pronto_is_read_as_its_four_bytes():
    """Rotel's RA-1572 ``SOURCE CD`` (NEC 8311, IR CODE 01) is the bytes 0x83, 0x11, 0x01 and the complement of 0x01; a frame with a
    space that is neither a mark nor three of them, or of another length, is none."""
    snapshot = oi.load_snapshot(SNAPSHOT / "rotel")
    _, sections = rotel.parse(snapshot.sheet_of("RA1572_HEX.pdf", "pages"))
    item = sections[0].items[0]
    assert (item.name, item.code) == ("SOURCE CD", 1)
    assert rotel.decode_nec(item.hex) == (0x83, 0x11, 0x01, 0xFE)
    assert rotel.decode_nec(item.hex.replace("0015 0041", "0015 0024", 1)) is None
    assert rotel.decode_nec(item.hex.replace("0022 0002", "0021 0002")) is None and rotel.decode_nec("nope") is None


def test_a_rotel_document_is_sets_of_codes_with_names_and_hex_and_its_oddities_are_read_as_the_documents_have_them():
    """A page's first line is its number (a name that is a digit is a name); ``IF CODE`` is read as ``IR CODE``; a set's lines before
    its first code are its note; a name may take two lines; a hex with no ``IR CODE`` line before it is an item with no code, to which a
    code line put after it gives its code; ``RC5, SYSTEM CODE 17`` opens a set of decimal commands."""
    h = "0000 006D 0022 0002 0157 00AC 0015 0016"
    title, sections = rotel.parse(rows_of_lines(
        "1", "ROTEL X9 HEX CODES", "NEC 8311, MAIN IR CODES", "Note commands marked with * require V1.", "IR CODE 01", "SOURCE CD*", h,
        "IR CODE 09", "1", h, "IR CODE 0A", "RECORD /", "ZONE SELECT", h, "NEC 8314, BLUETOOTH CODES", "IF CODE 0F", "STOP", h, page=1)
        + rows_of_lines("2", "NEC 8312", "SOURCE TUNER*", h, "IR CODE 02", page=2)
        + rows_of_lines("3", "RC5, SYSTEM CODE 17, ALTERNATE RC5 POWER & VOLUME CODES", "IR CODE 12", "POWER TOGGLE", h, page=3))
    assert title == "ROTEL X9 HEX CODES"
    assert [(f"{s.protocol} {s.address:02X}{s.sub:02X}", s.kind, [(i.code, i.name, i.typo) for i in s.items]) for s in sections] == [
        ("NEC1 8311", "MAIN IR CODES", [(1, "SOURCE CD*", False), (9, "1", False), (10, "RECORD / ZONE SELECT", False)]),
        ("NEC1 8314", "BLUETOOTH CODES", [(15, "STOP", True)]),
        ("NEC1 8312", "", [(2, "SOURCE TUNER*", False)]),
        ("RC5 1100", "ALTERNATE RC5 POWER & VOLUME CODES", [(12, "POWER TOGGLE", False)])]
    assert sections[0].note == "Note commands marked with * require V1."


def rotel_files() -> dict[str, dict]:
    return {load(p)["model"]: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "rotel").glob("*.json"))}


def test_the_rotel_files_are_the_59_models_less_two_their_alternative_sets_and_the_questionable_ones():
    """93 files and 3,598 keys: 57 model files (2,528 keys), 26 files of the units' alternative IR code set (942), 5 of the processors'
    and receivers' RC-5 power and volume codes (20) and 5 questionable ones (108 keys, Untested). The S14 and RAS-5000 are not read."""
    files = rotel_files()
    kinds: dict[str, list[int]] = {"model": [], "alternative": [], "RC-5": [], "questionable": []}
    for model, doc in files.items():
        assert doc["manufacturer"] == "Rotel" and doc["controls"][0].startswith("Rotel ")
        kind = ("questionable" if model.endswith("_questionable") else "alternative" if "alternative" in model else "RC-5" if "RC-5" in model else "model")
        kinds[kind].append(len(doc["keys"]))
        tiers = {s["forms"][0]["confidence"] for s in doc["keys"].values()}
        assert tiers == ({"untested"} if kind == "questionable" else {"plausible"}), model
        assert doc["protocol"]["name"] == ("RC5" if kind == "RC-5" else "NEC1")
    assert {k: (len(v), sum(v)) for k, v in kinds.items()} == {"model": (57, 2528), "alternative": (26, 942), "RC-5": (5, 20), "questionable": (5, 108)}
    report = (ROOT / oi.IMPORT_ROOT / oi.REPORT).read_text(encoding="utf-8")
    figure = lambda what: int(re.search(rf"\| rotel: {what} \| ([\d,]+) \|", report)[1].replace(",", ""))
    assert [figure(w) for w in ("models on the page", "files", "keys", "Pronto hex that decodes to its heading and code",
                                "Pronto hex that decodes to another code", "codes whose line says IF CODE")] == [59, 93, 3598, 3052, 67, 24]
    assert "S14" not in " ".join(files) and "RAS-5000" not in " ".join(files)


def test_a_rotel_set_is_nec_with_the_headings_two_bytes_and_a_set_whose_hex_says_another_address_is_questionable():
    files = rotel_files()
    ra = files["RA-1572"]["keys"]
    form = ra["KEY_SOURCE_CD"]["forms"][0]
    assert (form["device"], form["subdevice"], form["function"]) == ("0x83", "0x11", "0x01")
    assert "NEC 8311, MAIN IR CODES, IR CODE 01): NEC1 device 131 subdevice 17 function 1; its Pronto hex decodes" in form["source"]
    assert {s["forms"][0]["subdevice"] for s in ra.values()} == {"0x11", "0x14"}                 # the main set and the Bluetooth/USB set
    alt = files["RA-1572 (alternative IR codes)"]["keys"]
    assert {s["forms"][0]["subdevice"] for s in alt.values()} == {"0x21"} and len(alt) > 20
    # the RSP-1570's ZONE 4 set is headed NEC 8324 and its hex decodes to NEC 8323 (the Zone 3's): kept as written, apart
    zone4 = files["RSP-1570_questionable"]["keys"]
    assert len(zone4) == 14 and {s["forms"][0]["subdevice"] for s in zone4.values()} == {"0x24"}
    assert "its Pronto hex decodes to NEC 8323 function 00, FF after it and not to NEC 8324 function 00" in zone4["KEY_ZONE_4_POWER"]["forms"][0]["source"]
    assert "ZONE 4 POWER" not in {s["label"] for s in files["RSP-1570"]["keys"].values()}
    # the RSP-1576's two sets have their headings the other way round from their hex
    assert len(files["RSP-1576_questionable"]["keys"]) == len(files["RSP-1576 (alternative IR codes)_questionable"]["keys"]) == 6
    # the NEC 24DB set is standard NEC (address 0x24, complement 0xDB) and the RC-5 power and volume codes are decimal system 17
    rc5 = files["RSX-1562 (RC-5 codes)"]
    assert rc5["protocol"]["name"] == "RC5" and {s["forms"][0]["device"] for s in rc5["keys"].values()} == {"0x11"}
    assert rc5["keys"]["KEY_POWER_TOGGLE"]["forms"][0]["function"] == "0x0C" and rc5["keys"]["KEY_VOLUME_UP"]["forms"][0]["function"] == "0x10"
    assert files["RSX-1562"]["keys"]["KEY_AMP_MODE"]["forms"][0]["device"] == "0x24"
    # R15: the JP1 files named A11 and CD11 have the names, so these are kept under another
    assert "A11 (Rotel hex codes)" in files and "CD11 (Rotel hex codes)" in files and "A11" not in files and "CD11" not in files
    # the RC-1550's PDF is titled RC-1580 (Rotel's typo): the page's link decides, and the report says so
    assert "the title 'ROTEL RC-1580 HEX CODES' does not name the RC-1550" in (ROOT / oi.IMPORT_ROOT / oi.REPORT).read_text(encoding="utf-8")


def test_other_sources_rotel_nec_remotes_send_the_codes_the_pdfs_list():
    """The independent check (D274): the ledger's nineteen other Rotel NEC remotes (IR Blaster, JP1, LIRC) hold 550 keys at the address
    0x83 and **528 of them (96.0%) are a code some Rotel PDF lists**; where the vocabulary or the spelling compares the names 306 agree and
    12 do not (``OK`` and ``ENTER``, ``PRESET NEXT`` and ``TUNE UP``: the same keys in other words)."""
    from remote_ledger.keys import load_vocabulary

    vocabulary = load_vocabulary()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())      # noqa: E731
    ours: dict[tuple[int, int, int], set[str]] = {}
    for doc in rotel_files().values():
        if doc["protocol"]["name"] == "NEC1":
            for spec in doc["keys"].values():
                f = spec["forms"][0]
                ours.setdefault((int(f["device"], 0), int(f["subdevice"], 0), int(f["function"], 0)), set()).add(spec["label"])
    remotes = total = hit = agree = disagree = 0
    for path in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in path.parts:
            continue
        doc = load(path)
        if not (doc.get("manufacturer") or "").lower().startswith("rotel") or not doc["protocol"]["name"].startswith("NEC"):
            continue
        remotes += 1
        for name, spec in doc["keys"].items():
            label = spec.get("label") or name
            for f in spec["forms"]:
                if f.get("type") != "irp" or f.get("device") is None or f.get("subdevice") is None:
                    continue
                try:
                    key = (int(f["device"], 0), int(f["subdevice"], 0), int(f["function"], 0))
                except (TypeError, ValueError):
                    continue
                if key[0] != 0x83:
                    continue
                total += 1
                if key in ours:
                    hit += 1
                    mine, theirs = {vocabulary.resolve(x) for x in ours[key]} - {None}, vocabulary.resolve(label)
                    if squash(label) in {squash(x) for x in ours[key]} or (theirs and theirs in mine):
                        agree += 1
                    elif theirs is not None and mine:
                        disagree += 1
    assert (remotes, total, hit, agree, disagree) == (19, 550, 528, 306, 12)



# --- Lyngdorf ------------------------------------------------------------------------------------------------------


def test_a_lyngdorf_cell_is_the_device_and_the_byte_after_it_then_the_function_and_its_complement_and_a_cell_that_lacks_a_digit_is_doubtful():
    assert lyngdorf.read_cell("0x10EF 0x0FF0", 0x10EF) == (0x10, 0xEF, 0x0F, "")
    assert lyngdorf.read_cell("0x10E0 0x30CF", 0x10E0) == (0x10, 0xE0, 0x30, "")
    assert lyngdorf.read_cell("-", 0x10E1) is None
    device, subdevice, function, doubt = lyngdorf.read_cell("0x10E 0x16E9", 0x10E1)
    assert (device, subdevice, function) == (0x10, 0xE1, 0x16) and "lacks its last digit" in doubt
    assert "not a function and its complement" in lyngdorf.read_cell("0x10EF 0x16E8", 0x10EF)[3]
    with pytest.raises(ValueError, match="does not complete it"):
        lyngdorf.read_cell("0x10E 0x16E9", 0x10F0)                  # the column's own word does not begin with them
    with pytest.raises(ValueError, match="cannot read"):
        lyngdorf.read_cell("0x10EF", 0x10EF)
    names, rows = lyngdorf.cells_of([
        ["1", "121", "153", "TDAI 2200"], ["1", "153", "60", "Button", "153", "SDAI 2175", "246", "RP-1", "339", "CD-1"],
        ["1", "165", "60", "Stand By", "153", "0x10EF 0x0FF0", "246", "0x10E0 0x0FF0", "339", "0x10E1 0x0FF0"],
        ["1", "562", "60", "Video", "153", "0x10EF 0x09F6", "246", "-", "339", "-"],
        ["1", "646", "57", "Note: Mute and Volume commands are the same for all products."]])
    assert names[153] == ["TDAI 2200", "SDAI 2175"] and names[246] == ["RP-1"]
    assert [r[0] for r in rows] == ["Stand By", "Video"] and rows[1][1][246] == "-"


def test_lyngdorfs_one_page_is_six_files_and_218_keys_and_the_17_untested_are_the_cd1s_cells_that_lack_a_digit():
    files = {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "lyngdorf").glob("*.json"))}
    assert sorted(files) == ["CD-1", "DPA-1", "Millennium_Mk._III+IV", "RP-1", "SDAI_2175", "TDAI_2200"]
    assert {n: len(f["keys"]) for n, f in files.items()} == {"CD-1": 33, "DPA-1": 38, "Millennium_Mk._III+IV": 38, "RP-1": 33, "SDAI_2175": 38, "TDAI_2200": 38}
    triples = {n: {k: (f["forms"][0]["device"], f["forms"][0]["subdevice"], f["forms"][0]["function"]) for k, f in doc["keys"].items()}
               for n, doc in files.items()}
    # the four products of the first column send the same codes; the RP-1 and CD-1 differ in the subdevice, but for Mute and the two Volumes
    assert triples["DPA-1"] == triples["TDAI_2200"] == triples["SDAI_2175"] == triples["Millennium_Mk._III+IV"]
    assert triples["TDAI_2200"]["KEY_STAND_BY"] == ("0x10", "0xEF", "0x0F") and triples["RP-1"]["KEY_STAND_BY"] == ("0x10", "0xE0", "0x0F")
    assert triples["CD-1"]["KEY_STAND_BY"] == ("0x10", "0xE1", "0x0F") and triples["CD-1"]["KEY_MENU"] == ("0x10", "0xE1", "0x16")
    for name in ("KEY_MUTE", "KEY_VOLUME_PLUS", "KEY_VOLUME_MINUS"):
        assert {triples[n][name][1] for n in triples} == {"0xEF"}, name
    untested = {n: sorted(k for k, s in doc["keys"].items() if s["forms"][0]["confidence"] == "untested") for n, doc in files.items()}
    assert [n for n, u in untested.items() if u] == ["CD-1"] and len(untested["CD-1"]) == 17
    assert "KEY_MUTE" not in untested["CD-1"] and "KEY_STAND_BY" not in untested["CD-1"]
    for doc in files.values():
        for spec in doc["keys"].values():
            for form in spec["forms"]:
                assert "from the Wayback Machine snapshot 20190426003950" in form["source"] and "Lyngdorf-IR-Codes.pdf@4746d773" in form["source"]
    # every second word of the page is a function and its complement: nothing is doubtful for that
    assert not any("not a function and its complement" in s["forms"][0]["source"] for doc in files.values() for s in doc["keys"].values())


def test_the_ledgers_jp1_tdai_1120_sends_21_of_the_tdai_2200s_codes_under_the_same_functions():
    jp = load(ROOT / "remotes" / "jp1" / "Lyngdorf" / "Audio-Lyngdorf_TDAI-1120_amplifier.json")
    theirs = {}
    for spec in jp["keys"].values():
        f = spec["forms"][0]
        theirs[(int(f["device"], 0), int(f["subdevice"], 0), int(f["function"], 0))] = spec["label"]
    ours = load(ROOT / oi.IMPORT_ROOT / "lyngdorf" / "TDAI_2200.json")
    same = {}
    for spec in ours["keys"].values():
        f = spec["forms"][0]
        key = (int(f["device"], 0), int(f["subdevice"], 0), int(f["function"], 0))
        if key in theirs:
            same[spec["label"]] = theirs[key]
    assert len(theirs) == 48 and len(same) == 21
    assert same["Stand By"] == "Standby" and same["Volume +"] == "Volume Up" and same["Channel -"] == "SRC Down" and same["Random"] == "Shuffle"
    assert all(v.startswith("Digit ") for k, v in same.items() if k.isdigit()) and sum(k.isdigit() for k in same) == 10


# --- Emotiva -------------------------------------------------------------------------------------------------------


def test_an_emotiva_key_is_the_line_before_its_hex_and_every_other_line_is_no_key():
    items = emotiva.parse([
        ["1", "Emotiva UMC-1 Metal Remote Codes \u2013 rev 1/25/11"], ["1", "Here are the current complimentary IR codes."],
        ["1", "Source\xa02 \u2013 UMC Source 1 - DVD"], ["1", "0000 006D 0022 0002"], ["1", "0157 00AB 0016 0015"],
        ["1", "TUNER\xa0CH\u2010"], ["1", "0000 006C 0000 0026"],
        ["2", "Emotiva UMC-1 Metal Remote Codes"], ["2", "Back Sur+"], ["2", "0000 006D 0022 0002"]])
    assert [(i.page, i.name, i.hex) for i in items] == [
        (1, "Source 2 \u2013 UMC Source 1 - DVD", "0000 006D 0022 0002 0157 00AB 0016 0015"), (1, "TUNER CH-", "0000 006C 0000 0026"),
        (2, "Back Sur+", "0000 006D 0022 0002")]
    with pytest.raises(ValueError, match="no key name before"):
        emotiva.parse([["1", "0000 006D 0022 0002"]])


def test_an_emotiva_pronto_hex_is_a_nec_frame_with_the_bytes_least_significant_bit_first_and_what_is_not_one_says_why():
    frame = emotiva.decode_frame(nec_pronto(0x20, 0x5F, 0x0F))
    assert frame == emotiva.Frame((0x20, 0x5F, 0x0F, 0xF0), 0x6D, False)
    # the repeat sequence only (the 0 pairs of the once sequence, as the UMC-1's MUTE is): the frame is its first 34 pairs
    words = nec_pronto(0x02, 0xFD, 0x5C).split()
    repeat_only = " ".join(["0000", "006C", "0000", "0025", *words[4:4 + 68], "0016", "05F5", "0159", "0056", "0016", "05F5"])
    assert emotiva.decode_frame(repeat_only) == emotiva.Frame((0x02, 0xFD, 0x5C, 0xA3), 0x6C, True)
    assert "not hex" in emotiva.decode_frame("0000 006D 00G2 0002")
    assert "first word" in emotiva.decode_frame("0100 006D 0022 0002")
    assert "string has" in emotiva.decode_frame("0000 006D 0022 0002 0155 00AA")
    assert "neither sequence" in emotiva.decode_frame(" ".join(["0000", "006D", "0010", "0000", *["0015"] * 32]))
    assert "not a NEC leader" in emotiva.decode_frame(nec_pronto(1, 2, 3).replace("0155 00AA", "0155 0155", 1))
    assert "neither a 0 nor a 1" in emotiva.decode_frame(nec_pronto(1, 2, 3).replace("0015 0040", "0015 0025", 1))


def test_emotivas_six_documents_are_seven_files_and_239_keys_and_the_16_untested_are_a_code_two_keys_have():
    files = {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "emotiva").glob("*.json"))}
    assert sorted(files) == ["ERC-1", "ERC-2", "ERC-3", "UMC-1_Metal__Emotiva_table", "UMC-1__Emotiva_table", "UMC-200", "USP"]
    assert {n: len(f["keys"]) for n, f in files.items()} == {
        "ERC-1": 24, "ERC-2": 24, "ERC-3": 24, "UMC-1_Metal__Emotiva_table": 52, "UMC-1__Emotiva_table": 51, "UMC-200": 51, "USP": 13}
    assert files["UMC-1__Emotiva_table"]["model"] == "UMC-1 (Emotiva table)" and files["UMC-1_Metal__Emotiva_table"]["model"] == "UMC-1 Metal (Emotiva table)"
    untested = {n: sorted(k for k, s in f["keys"].items() if s["forms"][0]["confidence"] == "untested") for n, f in files.items()}
    assert {n: len(u) for n, u in untested.items() if u} == {"ERC-1": 2, "ERC-2": 2, "ERC-3": 2, "UMC-1_Metal__Emotiva_table": 2, "UMC-1__Emotiva_table": 8}
    assert untested["UMC-1_Metal__Emotiva_table"] == ["KEY_BACK_SUR_PLUS", "KEY_STANDBACK_SUR_MINUS"]
    assert untested["ERC-1"] == ["KEY_PAUSE", "KEY_PLAY"] and untested["ERC-1"] == untested["ERC-2"]
    for name, doc in files.items():
        triples = [tuple(int(doc["keys"][k]["forms"][0][x], 0) for x in ("device", "subdevice", "function")) for k in doc["keys"]]
        for key, spec in doc["keys"].items():
            form = spec["forms"][0]
            assert form["type"] == "irp" and "from the Wayback Machine snapshot" in form["source"] and "frequency word 0x006" in form["source"], (name, key)
            twins = triples.count(tuple(int(form[x], 0) for x in ("device", "subdevice", "function")))
            assert (twins > 1) == (form["confidence"] == "untested" and "gives this code to" in form["source"]) or twins == 1, (name, key)
    # the one document that names no model is pinned for the two it is served on, with the same keys
    assert {k: s["forms"][0]["function"] for k, s in files["ERC-1"]["keys"].items()} == {k: s["forms"][0]["function"] for k, s in files["ERC-2"]["keys"].items()}
    assert "names no model" in files["ERC-2"]["keys"]["KEY_STOP"]["forms"][0]["source"] and "names no model" not in files["ERC-3"]["keys"]["KEY_STOP"]["forms"][0]["source"]
    assert "stray 'stand'" in files["UMC-1_Metal__Emotiva_table"]["keys"]["KEY_STANDBACK_SUR_MINUS"]["forms"][0]["source"]
    # the UMC-200 table's group (`VOL + (VOLUME GROUP)`) is dropped from the label and cited, so its keys are named as the other tables' are
    umc200 = files["UMC-200"]["keys"]
    assert [umc200[k]["label"] for k in ("KEY_VOL_PLUS", "KEY_ARROW_UP", "KEY_STANDBY", "KEY_TRACK_ADVANCE", "KEY_VOL_MINUS")] == ["VOL +", "ARROW UP", "STANDBY", "TRACK ADVANCE", "VOL \u2013"]
    assert "row 'VOL + (VOLUME GROUP)'" in umc200["KEY_VOL_PLUS"]["forms"][0]["source"] and "without its group 'VOLUME GROUP'" in umc200["KEY_VOL_PLUS"]["forms"][0]["source"]
    assert "group" not in umc200["KEY_STANDBY"]["forms"][0]["source"] and not any("GROUP" in s["label"] for s in umc200.values())
    # the three ERC documents are one remote: ERC-3's 24 codes are ERC-1's under the other names of the keys
    erc1 = {s["forms"][0]["function"] for s in files["ERC-1"]["keys"].values()}
    assert {s["forms"][0]["function"] for s in files["ERC-3"]["keys"].values()} == erc1


def test_other_sources_emotiva_remotes_are_these_documents_codes_and_the_documents_disagree_where_they_do():
    def triples(path):
        out = {}
        for key, spec in load(ROOT / path)["keys"].items():
            f = spec["forms"][0]
            out.setdefault(tuple(int(f[x], 0) for x in ("device", "subdevice", "function")), []).append(spec["label"])
        return out
    ours = {n: triples(f"remotes/official/emotiva/{n}.json") for n in ("UMC-1__Emotiva_table", "UMC-1_Metal__Emotiva_table", "UMC-200", "USP")}
    receiver = triples("remotes/jp1/Emotiva/Audio-Emotiva_UMC_a-v_receiver.json")
    metal = triples("remotes/jp1/Emotiva/Audio-Emotiva_UMC-1_Metal.json")
    blaster = triples("remotes/irblaster/EMOTIVA/7001-NEC1.json")
    umc1 = triples("remotes/jp1/Emotiva/Audio-Emotiva_UMC-1.json")
    # each other source's remote that is a table's holds every code of it, and the JP1 receiver file has the 2010 document's own labels
    assert (len(ours["UMC-1__Emotiva_table"]), sum(k in receiver for k in ours["UMC-1__Emotiva_table"])) == (47, 47)
    # 37 of them have the same label (two more the same two in the other order); the other 8 are the source keys, which the receiver file names as the 2011 document does
    other = [k for k in ours["UMC-1__Emotiva_table"] if sorted(ours["UMC-1__Emotiva_table"][k]) != sorted(receiver[k])]
    assert len(other) == 8 and all(receiver[k] == ours["UMC-1_Metal__Emotiva_table"][k] for k in other)
    assert (len(ours["UMC-1_Metal__Emotiva_table"]), sum(k in metal for k in ours["UMC-1_Metal__Emotiva_table"])) == (51, 51)
    assert (len(ours["UMC-200"]), sum(k in blaster for k in ours["UMC-200"])) == (51, 41) and len(blaster) == 41
    # where they differ: the 2011 document's `Back Sur+` is the row above it (0x58), where both JP1 files have 0x54, and the JP1 Metal file has the two Channel keys the other way round
    assert ours["UMC-1_Metal__Emotiva_table"][(0x20, 0x5F, 0x58)] == ["standBack Sur-", "Back Sur+"]
    assert umc1[(0x20, 0x5F, 0x54)] == ["VolBackSurrUp"] and metal[(0x20, 0x5F, 0x54)] == ["back sur+"] and metal[(0x20, 0x5F, 0x58)] == ["back sur-"]
    assert ours["UMC-1_Metal__Emotiva_table"][(0x20, 0x5F, 0x55)] == ["Channel Down"] and umc1[(0x20, 0x5F, 0x55)] == ["TuneDown"] and metal[(0x20, 0x5F, 0x55)] == ["channel up"]
    # the USP shares the receiver's address and its codes mean other keys there: only the power and volume keys are the same
    receiver_names = {k: v[0].casefold() for k, v in receiver.items()}
    same = [n for k, n in ((k, v[0]) for k, v in ours["USP"].items()) if k in receiver_names and receiver_names[k].replace("–", "").replace(" ", "") in (n.casefold().replace(" ", "").replace("down", "").replace("up", ""), "")]
    assert sum(k in receiver for k in ours["USP"]) == 13 and len(same) <= 4


# --- Denon ---------------------------------------------------------------------------------------------------------


def denon_cells(*cells):
    return [v for x, t in cells for v in (str(x), t)]


def test_a_denon_name_says_which_models_it_is_for_and_a_cell_of_two_lines_is_two_names():
    ci, avr = frozenset({"AVR4311CI"}), frozenset({"AVR4311"})
    a = denon.read_name("TUNER(except AVR4311CI)")
    assert (a.label, a.models) == ("TUNER", avr)
    b = denon.read_name("HD RADIO(AVR4311CI only)")
    assert (b.label, b.models) == ("HD RADIO", ci)
    c = denon.read_name("TUNER SHIFT A \u203b1")
    assert (c.label, c.models, c.marks) == ("TUNER SHIFT A", avr, ("1",))
    d = denon.read_name("DIRECT SEARCH\u203b1 (RDS SEARCH \u203b2)")
    assert (d.label, d.models, d.marks) == ("DIRECT SEARCH (RDS SEARCH)", avr, ("1", "2"))
    assert denon.read_name("ZONE2 HD RADIO (AVR4311 only)").models == avr and denon.read_name("POWER").models == denon.ALL
    # a line that begins with `(` goes on the line before it; `/HD RADIO...` after a qualified name is its other name, and after a plain one the rest of it
    two = denon.alternatives(["ZONE2 TUNER", "(except AVR4311CI)", "ZONE2 HD RADIO", "(AVR4311 only)"])
    assert [(a.label, a.models) for a in two] == [("ZONE2 TUNER", avr), ("ZONE2 HD RADIO", avr)]
    slash = denon.alternatives(["TUNER(except AVR4311CI)", "/HD RADIO(AVR4311CI only)"])
    assert [(a.label, a.models) for a in slash] == [("TUNER", avr), ("HD RADIO", ci)]
    rest = denon.alternatives(["MAIN ZONE TUNING MODE AUTO/MANU.", "/Fast Forward"])
    assert [a.label for a in rest] == ["MAIN ZONE TUNING MODE AUTO/MANU./Fast Forward"]


def test_a_denon_sharp_row_is_its_number_and_data_and_the_address_and_extension_of_its_table_first_bit_first():
    rows = [["2", "R", "204", "221"], ["2", "R", "221", "238"], ["2", "R", "238", "254"], ["2", "R", "302", "331"], ["2", "R", "331", "347"],
            ["2", "191"] + denon_cells((70, "SYSTEM"), (116, "ADDRESS(C1\uff5eC5)"), (212, ":"), (219, "\uff10\uff11\uff10\uff10\uff10"), (257, ","), (264, "EXTENSION"),
                                       (327, "BIT(C12,C13)"), (394, ":"), (401, "\uff11\uff11")),
            ["2", "208"] + denon_cells((70, "No."), (97, "Data"), (222, "Key"), (244, "Name")),
            ["2", "225"] + denon_cells((75, "0"), (114, "\uff10\uff10\uff10\uff10\uff10\uff10"), (326, "16"), (370, "\uff10\uff10\uff10\uff10\uff11\uff10"), (437, "FR"), (453, "LEVEL"), (486, "DOWN")),
            ["2", "241"] + denon_cells((75, "1"), (114, "\uff11\uff10\uff10\uff10\uff10\uff10"), (181, "POWER"), (326, "17"), (370, "\uff11\uff10\uff10\uff10\uff11\uff10")),
            ["2", "305"] + denon_cells((181, "TUNER(except"), (247, "AVR4311CI)")),
            ["2", "313"] + denon_cells((75, "5"), (114, "\uff11\uff10\uff11\uff10\uff10\uff10"), (326, "21"), (370, "\uff11\uff10\uff11\uff10\uff11\uff10")),
            ["2", "319"] + denon_cells((181, "HD"), (199, "RADIO(AVR4311CI"), (284, "only)")),
            ["2", "335"] + denon_cells((75, "6"), (114, "\uff10\uff11\uff11\uff10\uff10\uff10"), (326, "22"), (370, "\uff10\uff11\uff11\uff10\uff11\uff10"), (437, "SR"), (454, "LEVEL"), (486, "UP"))]
    got = denon.parse_sharp(rows)
    assert [(r.number, [a.label for a in r.names]) for r in got] == [
        (0, []), (16, ["FR LEVEL DOWN"]), (1, ["POWER"]), (17, []), (5, ["TUNER", "HD RADIO"]), (21, []), (6, []), (22, ["SR LEVEL UP"])]
    power, down = got[2], got[1]
    assert (power.device, power.function) == (2, 0xC1) and (down.device, down.function) == (2, 0xD0)      # 01000 is 2, 100000 is 1 and the extension 11 is +192
    assert denon.SharpRow(4, "00110", "01", 3, "110000", []).function == 3 + 128 and denon.SharpRow(4, "00110", "01", 3, "110000", []).device == 12
    bad = [r if r[1] != "241" else ["2", "241"] + denon_cells((75, "1"), (114, "\uff10\uff10\uff10\uff10\uff10\uff11"), (181, "POWER")) for r in rows]
    with pytest.raises(ValueError, match="whose value is not its number"):
        denon.parse_sharp(bad)


def denon_bits(y, bits, name="", first=False):
    """A line of the Kaseikyo table: the bits at their places (the first row's four GENRE2 bits before the 20) and the name at 393."""
    xs = ([80, 93, 106, 119] if first else []) + [133 + 12.6 * i for i in range(20)]
    return ["10", str(y)] + denon_cells(*[(round(x), b) for x, b in zip(xs, bits)], *([(393, name)] if name else []))


def test_a_denon_kaseikyo_row_is_genre1_genre2_and_twelve_function_bits_first_bit_first_and_its_parity_is_the_xor_of_the_bytes_before_it():
    header = "0010101001001100" + "0000" + "0010"            # the vendor 0x54 and 0x32, a 0 parity nibble and GENRE1 4
    rows = [["10", "26"] + denon_cells((70, "MAIN"), (101, "ZONE")),
            ["10", "40"] + denon_cells((172, "DENON"), (227, "CODE"), (382, "Parity"), (447, "GENRE1")),
            ["10", "81"] + denon_cells(*[(79 + 17 * i, b) for i, b in enumerate(header)]),
            ["10", "108"] + denon_cells((70, "REMOTE"), (121, "ID"), (136, "SET"), (153, ":"), (159, "1")),
            ["10", "124"] + denon_cells((276, "K4-1"), (300, ":"), (307, "MAIN"), (335, "ZONE")),
            ["10", "138"] + denon_cells((83, "GENRE2"), (184, "Data"), (268, "ID"), (329, "parity"), (455, "Key"), (475, "Name")),
            denon_bits(167, "1000" + "01000000000010000110", "ALL POWER ON", first=True),
            denon_bits(184, "11000000000010001110", "ALL POWER OFF"),
            # a name of two lines: the first above the bits, the bits 39 and 40 a line above the rest, the second on their line
            ["10", "200"] + denon_cells((393, "TUNER(except"), (462, "AVR4311CI)")),
            ["10", "207"] + denon_cells(*[(round(133 + 12.6 * i), b) for i, b in zip(range(10, 12), "00")]),
            ["10", "214"] + denon_cells(*[(round(133 + 12.6 * i), b) for i, b in zip(list(range(10)) + list(range(12, 20)), "0000100000" + "00000010")], (393, "/HD"), (414, "RADIO(AVR4311CI"), (500, "only)")),
            # the parity byte is not what the data give: reported, and the data read
            denon_bits(230, "00001000000000000000", "KEY1")]
    got, skipped = denon.parse_kaseikyo(rows)
    assert skipped == [] and [(r.group, r.device, r.subdevice, r.function, r.names) for r in got] == [
        ("MAIN ZONE", 4, 1, 2, ["ALL POWER ON"]), ("MAIN ZONE", 4, 1, 3, ["ALL POWER OFF"]),
        ("MAIN ZONE", 4, 1, 16, ["TUNER(except AVR4311CI)", "/HD RADIO(AVR4311CI only)"]), ("MAIN ZONE", 4, 1, 16, ["KEY1"])]
    assert [r.computed_parity() == r.parity for r in got] == [True, True, True, False]
    with pytest.raises(ValueError, match="ID bits"):
        denon.parse_kaseikyo(rows[:-1] + [denon_bits(230, "00001000001100000000", "KEY1")])
    with pytest.raises(ValueError, match="no header"):
        denon.parse_kaseikyo(rows[:2] + rows[3:])


def test_the_four_denon_files_are_the_sharp_and_the_kaseikyo_codes_of_each_model_with_the_parity_that_does_not_agree_untested():
    files = {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "denon").glob("AVR-4311*.json"))}
    assert sorted(files) == ["AVR-4311", "AVR-4311CI", "AVR-4311CI__Kaseikyo", "AVR-4311__Kaseikyo"]
    assert {n: (f["protocol"]["name"], len(f["keys"])) for n, f in files.items()} == {
        "AVR-4311": ("Denon", 155), "AVR-4311CI": ("Denon", 123), "AVR-4311CI__Kaseikyo": ("Denon-K", 369), "AVR-4311__Kaseikyo": ("Denon-K", 377)}
    untested = {n: [k for k, s in f["keys"].items() if s["forms"][0]["confidence"] == "untested"] for n, f in files.items()}
    assert {n: len(u) for n, u in untested.items()} == {"AVR-4311": 4, "AVR-4311CI": 0, "AVR-4311CI__Kaseikyo": 42, "AVR-4311__Kaseikyo": 44}
    for n in ("AVR-4311CI__Kaseikyo", "AVR-4311__Kaseikyo"):          # every Untested Kaseikyo key is a parity byte that does not agree, and every parity that does not is Untested
        assert all("parity byte" in files[n]["keys"][k]["forms"][0]["source"] for k in untested[n])
        assert sum("parity byte" in s["forms"][0]["source"] for s in files[n]["keys"].values()) == len(untested[n])
    assert sorted(files["AVR-4311"]["keys"][k]["label"] for k in untested["AVR-4311"]) == ["ZONE2 HD RADIO", "ZONE2 TUNER", "ZONE3 HD RADIO", "ZONE3 TUNER"]
    sharp, sharp_ci = files["AVR-4311"]["keys"], files["AVR-4311CI"]["keys"]
    power = sharp["KEY_POWER"]["forms"][0]
    assert (power["device"], power["function"], "subdevice" in power) == ("0x02", "0xC1", False)
    assert files["AVR-4311CI"]["keys"]["KEY_HD_RADIO"]["forms"][0]["function"] == sharp["KEY_TUNER"]["forms"][0]["function"] == "0xC5" and "KEY_TUNER" not in sharp_ci and "KEY_HD_RADIO" not in sharp
    assert set(sharp_ci) - {"KEY_HD_RADIO"} <= set(sharp) and len(set(sharp) - set(sharp_ci)) == 33     # the 32 `※1` and `except AVR4311CI` keys and the TUNER the CI calls HD RADIO
    k4 = files["AVR-4311__Kaseikyo"]["keys"]
    on = k4["KEY_ALL_POWER_ON"]["forms"][0]
    assert (on["device"], on["subdevice"], on["function"]) == ("0x04", "0x01", "0x02") and "bits 29 to 48 01000000000010000110" in on["source"]
    assert "KEY_BAND_FM" in k4 and "KEY_BAND_FM" not in files["AVR-4311CI__Kaseikyo"]["keys"]            # the analog tuner's table is `(Except AVR4311CI)`
    assert len(k4) - len(files["AVR-4311CI__Kaseikyo"]["keys"]) == 8
    assert "ZONE2 MODE AUTO/MANU./ZONE2 Fast Forward" in {s["label"] for s in k4.values()}
    assert "denon.co.uk RC code list/AVR4311CI_AVR4311_IR-Codes.pdf@4cc989d3" in power["source"] and "snapshot 20110813053037" in power["source"]


def test_other_sources_denon_remotes_hold_every_sharp_code_of_the_table_and_most_kaseikyo_ones_and_the_parity_that_disagrees_is_not_the_data():
    def triples(path, kind):
        out = {}
        for key, spec in load(ROOT / path)["keys"].items():
            f = spec["forms"][0]
            fields = ("device", "function") if kind == "Denon" else ("device", "subdevice", "function")
            out.setdefault(tuple(int(f[x], 0) for x in fields), []).append(spec["label"])
        return out
    ours = triples("remotes/official/denon/AVR-4311.json", "Denon")
    held = set()
    for p in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in p.parts:
            continue
        doc = load(p)
        if doc["protocol"].get("name") == "Denon":
            for spec in doc["keys"].values():
                for f in spec["forms"]:
                    if f.get("device") is not None and f.get("function") is not None:
                        held.add((int(f["device"], 0), int(f["function"], 0)))
    assert (len(ours), sum(k in held for k in ours)) == (153, 153)
    avr3310 = triples("remotes/jp1/Denon/Audio-Denon_AVR3310.json", "Denon")
    squash = lambda text: re.sub(r"[^a-z0-9]", "", text.lower())
    both = [k for k in ours if k in avr3310]
    assert len(both) == 150 and sum(bool({squash(x) for x in ours[k]} & {squash(x) for x in avr3310[k]}) for k in both) == 138
    kas = triples("remotes/official/denon/AVR-4311__Kaseikyo.json", "Denon-K")
    x4000 = triples("remotes/jp1/Denon/Audio-Denon_AVR-x4000_ALL.json", "Denon-K")
    assert (len(kas), sum(k in x4000 for k in kas)) == (377, 246)
    heldk = set()
    for p in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in p.parts:
            continue
        doc = load(p)
        if doc["protocol"].get("name") == "Denon-K":
            for spec in doc["keys"].values():
                for f in spec["forms"]:
                    if f.get("device") is not None and f.get("subdevice") is not None:
                        heldk.add(tuple(int(f[x], 0) for x in ("device", "subdevice", "function")))
    parity = [tuple(int(s["forms"][0][x], 0) for x in ("device", "subdevice", "function"))
              for s in load(ROOT / "remotes/official/denon/AVR-4311__Kaseikyo.json")["keys"].values() if "parity byte" in s["forms"][0]["source"]]
    assert (len(set(kas) & heldk), len(parity), sum(c in heldk for c in parity)) == (286, 44, 28)


# --- Hitachi America -----------------------------------------------------------------------------------------------


def test_the_hitachi_tables_are_read_by_layout_a_key_with_no_code_is_no_key_and_a_spreadsheets_number_is_not_a_label():
    assert hitachi.tidy("1.0") == "1" and hitachi.tidy("VOL \x81\u00a31") == "VOL \u25b21" and hitachi.tidy("GUIDE\uff0fTV2") == "GUIDE/TV2"
    assert hitachi.tidy("SLEEP  (+100 (VCR MODE))") == "SLEEP (+100 (VCR MODE))" and hitachi.tidy("4:3 ZOOM 2") == "4:3 ZOOM 2"
    entries, modes = hitachi.keys_xls([
        ["BASE MODEL:HL01322", "", "", "", "", "", ""], ["", "No.", "", "TV", "", "New for 2004 models", ""],
        ["", "1.0", "POWER (TOGGLE)", "50AF 17E8", "", "POWER ON", "50AF 3EC1"], ["", "2.0", "(LIGHT)", "MODE KEY", "", "POWER OFF", "50AF 3FC0"],
        ["", "11.0", "1.0", "50AF 0DF2", "", "", ""]])
    assert modes == 1 and [(e.row, e.label, e.code, e.where) for e in entries] == [
        (3, "POWER (TOGGLE)", "50AF 17E8", "No. 1"), (3, "POWER ON", "50AF 3EC1", "No. 1, 'New for 2004 models'"),
        (4, "POWER OFF", "50AF 3FC0", "No. 2, 'New for 2004 models'"), (5, "1", "50AF 0DF2", "No. 11")]
    assert [(e.label, e.code) for e in hitachi.label_code([["", "RC IR CODES", ""], ["", "POWER", "50AF 17E8"], ["VID1", "50AF 21DE"]])] == [("POWER", "50AF 17E8"), ("VID1", "50AF 21DE")]
    pdf = [["1", "114", "90", "#1", "162", "Input1", "270", "50AF 21DE"], ["1", "103", "90", "RC KEY", "162", "FUNCTION", "270", "HEX CODE"],
           ["1", "121", "65", "1", "82", "AV1", "215", "50AF 21DE"]]
    assert [(e.label, e.where) for e in hitachi.function_hex(pdf)] == [("Input1", "the RC key '#1'"), ("AV1", "the item '1'")]
    assert [(e.label, e.code) for e in hitachi.custom_key([["1", "75", "16", "1", "58", "POWER", "191", "50AF", "256", "17E8"], ["1", "65", "16", "No.", "58", "Key Label", "191", "Custom Code", "256", "Key Code"]])] == [("POWER", "50AF 17E8")]
    items = hitachi.pronto_items([["1", "73", "56", "08 Hitachi AVC Discrete Hex codes + Pronto Hex Codes"],
                                  ["1", "100", "56", "Input System", "165", "HDMI2", "262", "56AB 20DF", "346", "0000 006c 0022 0002"], ["1", "111", "346", "0157 00ac"],
                                  ["4", "73", "56", "08 UT Monitor Discrete Codes"], ["4", "100", "56", "Power Control", "165", "Power On", "262", "50AF 3EC1", "346", "0000 006c"]],
                                 "08 Hitachi AVC Discrete Hex codes + Pronto Hex Codes")
    assert [(e.label, e.code, e.pronto) for e in items] == [("HDMI2", "56AB 20DF", "0000 006c 0022 0002 0157 00ac")]


def test_hitachis_six_documents_are_ten_files_and_311_keys_and_the_nine_untested_are_codes_two_keys_have_or_a_pronto_that_disagrees():
    files = {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "hitachi").glob("*.json"))}
    assert {n: len(f["keys"]) for n, f in files.items()} == {
        "2004_TV_additional_codes": 22, "2004_TV_remote": 36, "AVC__2008": 17, "DW3": 22, "HL01322__2003": 48, "HL01322__2004": 63,
        "L19_and_L26": 47, "L32_and_larger": 27, "PDP__2008": 24, "UT_Monitor__2008": 5}
    untested = {n: sorted(s["label"] for s in f["keys"].values() if s["forms"][0]["confidence"] == "untested") for n, f in files.items()}
    assert {n: u for n, u in untested.items() if u} == {
        "2004_TV_additional_codes": ["OFF", "ON", "POWER OFF", "POWER ON"], "HL01322__2004": ["OFF", "ON", "POWER OFF", "POWER ON"], "PDP__2008": ["HDMI1"]}
    assert "decodes to 50 AF 20 DF" in files["PDP__2008"]["keys"]["KEY_HDMI1"]["forms"][0]["source"]
    avc = files["AVC__2008"]["keys"]
    assert all("is decoded and agrees" in s["forms"][0]["source"] for s in avc.values())
    assert (avc["KEY_HDMI2"]["forms"][0]["device"], avc["KEY_HDMI2"]["forms"][0]["subdevice"], avc["KEY_HDMI2"]["forms"][0]["function"]) == ("0x56", "0xAB", "0x20")
    power = files["HL01322__2003"]["keys"]["KEY_POWER_TOGGLE"]["forms"][0]
    assert (power["device"], power["subdevice"], power["function"]) == ("0x50", "0xAF", "0x17") and "sheet 'Sheet2' row 4 'POWER (TOGGLE)'" in power["source"]
    assert {s["label"] for s in files["HL01322__2003"]["keys"].values()} >= {"VOL \u25b21", "VOL \u25bc1", "GUIDE/TV2", "1"}
    assert files["L19_and_L26"]["keys"]["KEY_POWER"]["forms"][0]["function"] == "0x17" and files["DW3"]["keys"]["KEY_POWER_ON"]["forms"][0]["function"] == "0x3E"


def test_other_sources_hitachi_remotes_have_the_codes_of_the_tvs_tables_and_the_avc_ones_are_new_and_the_documents_differ_in_seven_labels():
    held = set()
    for p in sorted((ROOT / "remotes").glob("*/*/*.json")):
        if "official" in p.parts:
            continue
        doc = load(p)
        if (doc.get("manufacturer") or "").lower() != "hitachi" or not str(doc["protocol"].get("name", "")).startswith("NEC"):
            continue
        for spec in doc["keys"].values():
            for f in spec["forms"]:
                if f.get("device") is not None and f.get("subdevice") is not None:
                    held.add(tuple(int(f[x], 0) for x in ("device", "subdevice", "function")))
    counts = {}
    codes: dict[str, dict[str, str]] = {}
    for path in sorted((ROOT / oi.IMPORT_ROOT / "hitachi").glob("*.json")):
        doc = load(path)
        triples = {tuple(int(s["forms"][0][x], 0) for x in ("device", "subdevice", "function")) for s in doc["keys"].values()}
        counts[path.stem] = (len(triples), len(triples & held))
        codes[path.stem] = {s["label"]: s["forms"][0]["function"] for s in doc["keys"].values()}
    assert counts == {"2004_TV_additional_codes": (20, 20), "2004_TV_remote": (36, 36), "AVC__2008": (17, 0), "DW3": (22, 22), "HL01322__2003": (48, 48),
                      "HL01322__2004": (61, 61), "L19_and_L26": (47, 34), "L32_and_larger": (27, 25), "PDP__2008": (24, 22), "UT_Monitor__2008": (5, 5)}
    squash = lambda text: re.sub(r"[^a-z0-9]", "", text.lower())
    by_label: dict[str, dict[str, str]] = {}
    for name, labelled in codes.items():
        for label, function in labelled.items():
            by_label.setdefault(squash(label), {})[name] = function
    repeated = {k: v for k, v in by_label.items() if len(v) > 1}
    assert len(repeated) == 80 and sorted(k for k, v in repeated.items() if len(set(v.values())) > 1) == ["169zoom", "43expanded", "43zoom2", "cc", "expanded", "info", "night"]


# --- Classé --------------------------------------------------------------------------------------------------------


def runs_to_pronto(levels, word, unit, gap=0x1275):
    """A Pronto string of the levels (1 a mark) at ``unit`` words a unit: a mark and a space for each pair, and the gap for the last space."""
    while levels and levels[-1] == 0:
        levels = levels[:-1]
    pairs, i = [], 0
    while i < len(levels):
        mark = 0
        while i < len(levels) and levels[i] == 1:
            mark, i = mark + 1, i + 1
        space = 0
        while i < len(levels) and levels[i] == 0:
            space, i = space + 1, i + 1
        pairs.append((mark * unit, space * unit if i < len(levels) else gap))
    return " ".join(f"{w:04X}" for w in [0, word, 0, len(pairs), *[x for pair in pairs for x in pair]])


def rc6_pronto(system, code, toggle):
    levels = [1] * 6 + [0] * 2 + [1, 0] + [0, 1] * 3 + ([1, 1, 0, 0] if toggle else [0, 0, 1, 1])
    for bit in [int(b) for b in f"{system:08b}{code:08b}"]:
        levels += [1, 0] if bit else [0, 1]
    return runs_to_pronto(levels, 0x4A, 0x19)


def rc5_pronto(system, command, toggle):
    bits = [1, 1 - (command >> 6), toggle] + [int(b) for b in f"{system:05b}{command & 0x3F:06b}"]
    levels = []
    for bit in bits:
        levels += [0, 1] if bit else [1, 0]
    return runs_to_pronto(levels[1:], 0x6D, 0x22)


def test_classes_rc6_and_rc5_pronto_hex_decode_to_the_system_the_code_and_the_toggle_bit():
    for system, code, toggle in ((16, 0, 0), (200, 153, 1), (116, 255, 0), (201, 12, 1)):
        assert classe.decode_rc6(rc6_pronto(system, code, toggle)) == (0, toggle, system, code)
    for system, command, toggle in ((25, 12, 0), (25, 62, 1), (25, 127, 0)):
        assert classe.decode_rc5(rc5_pronto(system, command, toggle)) == (system, command, toggle)
    assert "leader" in classe.decode_rc6(rc6_pronto(16, 0, 0).replace("0096", "0050", 1))
    assert "header says" in classe.decode_rc6("0000 004A 0000 0003 0095 0032")
    two = rc6_pronto(200, 153, 0) + " " + rc6_pronto(200, 153, 1)
    assert classe.prontos(two) == [rc6_pronto(200, 153, 0), rc6_pronto(200, 153, 1)] and classe.prontos("not hex") == ["not hex"]


def test_a_classe_list_is_the_rows_of_system_code_ir_code_and_action_and_a_line_in_the_action_column_goes_on_the_action():
    rows = [["1", "86", "90", "The Classé CAP-2100 uses RC6 protocol for infrared communications. The"], ["1", "197", "109", "Sys code", "181", "IR code", "239", "Action"],
            ["1", "211", "125", "016", "199", "1", "239", "Toggle Tape Monitor on/off"], ["1", "354", "125", "016", "196", "15", "239", "Cycle through display intensities"],
            ["1", "368", "239", "(low/medium/high)"], ["1", "382", "125", "200", "196", "16", "239", "Volume up (volume +)"], ["1", "500", "90", "A note that is no row"]]
    got = classe.read_list(rows)
    assert [(e.protocol, e.system, e.code, e.label) for e in got] == [
        ("RC6", 16, 1, "Toggle Tape Monitor on/off"), ("RC6", 16, 15, "Cycle through display intensities (low/medium/high)"), ("RC6", 200, 16, "Volume up (volume +)")]
    commands = classe.read_commands([["1", "100", "77", "RC6 Coded Mode 0 system 201"], ["1", "178", "120", "1", "148", "#1", "261", "Direct Power Toggle AMP 1"],
                                     ["1", "339", "114", "68", "148", "Program Mode Toggle Same as front panel mode button"]])
    assert [(e.system, e.code, e.label, e.where) for e in commands] == [
        (201, 1, "#1", "the description 'Direct Power Toggle AMP 1'"), (201, 68, "Program Mode Toggle Same as front panel mode button", "")]


def test_a_classe_hex_pdf_is_read_by_its_headings_and_every_string_must_be_its_systems_code_and_a_toggle_and_a_second_string_in_an_item_is_a_note():
    hexlines = lambda text: [text[i:i + 90].strip() for i in range(0, len(text), 90)]            # noqa: E731
    rows = [["1", "RC6 MODE 0, SYSTEM CODE 016"], ["1", "IR CODE 0 TOGGLE 0"], ["1", "INPUT 1"],
            *[["1", line] for line in hexlines(rc6_pronto(16, 0, 0))], ["1", "IR CODE 0 TOGGLE 1"], ["1", "INPUT 1"], *[["1", line] for line in hexlines(rc6_pronto(16, 0, 1))],
            ["2", "IR CODE 12 TOGGLE 0"], ["2", "STANDBY/"], ["2", "OPERATE"], *[["2", line] for line in hexlines(rc6_pronto(16, 12, 0))],
            ["2", "IR CODE 13 TOGGLE 0"], ["2", "MUTE"], *[["2", line] for line in hexlines(rc6_pronto(16, 14, 0))],
            ["3", "IR CODE 153 TOGGLE 0"], ["3", "UNMUTE"], *[["3", line] for line in hexlines(rc6_pronto(16, 153, 0) + " " + rc6_pronto(16, 153, 1))]]
    entries, notes = classe.read_hex(rows)
    assert [(e.label, e.system, e.code, e.doubts) for e in entries] == [
        ("INPUT 1", 16, 0, []), ("STANDBY/ OPERATE", 16, 12, []),
        ("MUTE", 16, 13, ["a Pronto hex of its TOGGLE 0 item decodes to system 16 code 14"]), ("UNMUTE", 16, 153, [])]
    assert len(notes) == 1 and "has 2 Pronto strings" in notes[0]
    with pytest.raises(ValueError, match="not mode 0"):
        classe.read_hex([["1", "RC6 MODE 6, SYSTEM CODE 16"]])


def test_classes_twelve_documents_are_eighteen_files_and_1170_keys_all_plausible_and_the_lists_and_the_hex_pdfs_of_a_product_have_the_same_codes():
    files = {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "classe").glob("*.json"))}
    assert {n: len(f["keys"]) for n, f in files.items()} == {
        "CAP-2100": 32, "CDP-100": 47, "CDP-102": 88, "CDP-202": 88, "CDP-300": 88, "CDP-502": 88, "CDT-300": 88, "CP-500": 33, "CP-700": 39,
        "CP-800__HEX_codes": 54, "CT-SSP__HEX_codes": 86, "Delta_amplifiers": 25, "Delta_and_CT_amplifiers__HEX_codes": 22, "SSP-25": 24,
        "SSP-300": 98, "SSP-600": 98, "SSP-800": 86, "SSP-800__HEX_codes": 86}
    assert sum(len(f["keys"]) for f in files.values()) == 1_170
    assert all(s["forms"][0]["confidence"] == "plausible" for f in files.values() for s in f["keys"].values())
    assert {n: f["protocol"] for n, f in files.items() if f["protocol"]["name"] == "RC5"} == {"SSP-25": {"name": "RC5", "carrierHz": 36000, "minSends": 1}}
    assert {f["protocol"]["carrierHz"] for n, f in files.items() if n != "SSP-25"} == {56000}
    assert files["CDP-102"]["keys"] == files["CDT-300"]["keys"] == files["CDP-502"]["keys"] and files["SSP-300"]["keys"] == files["SSP-600"]["keys"]

    def codes(name):
        return {(int(s["forms"][0]["device"], 0), int(s["forms"][0]["function"], 0)): s["label"] for s in files[name]["keys"].values()}
    ssp, ssp_hex = codes("SSP-800"), codes("SSP-800__HEX_codes")
    assert set(ssp) == set(ssp_hex) and codes("SSP-800__HEX_codes") == codes("CT-SSP__HEX_codes")
    squash = lambda text: re.sub(r"[^a-z0-9]", "", text.lower())            # noqa: E731
    assert sum(squash(ssp[k]) == squash(ssp_hex[k]) for k in ssp) == 43
    delta, delta_hex = codes("Delta_amplifiers"), codes("Delta_and_CT_amplifiers__HEX_codes")
    assert set(delta_hex) < set(delta) and sorted(set(delta) - set(delta_hex)) == [(201, 68), (201, 70), (201, 92)]
    assert {k[0] for k in codes("CP-800__HEX_codes")} == {16, 200} and {k[0] for k in codes("CDP-102")} == {4, 200}
    assert codes("SSP-800")[(116, 2)] == "Input01" and codes("SSP-800__HEX_codes")[(116, 2)] == "INPUT 1" and codes("CAP-2100")[(200, 16)] == "Volume up (volume +)"
    source = files["CAP-2100"]["keys"]["KEY_VOLUME_UP_VOLUME_PLUS"]["forms"][0]["source"]
    assert "CAP-2100_IR_codes.pdf@f6bbfefe (retrieved 2026-10-16, from the Wayback Machine snapshot 20101217163929)" in source and "RC6 mode 0 system code 200, IR code 16" in source
    hex_source = files["CP-800__HEX_codes"]["keys"]["KEY_INPUT_1"]["forms"][0]["source"]
    assert "CLASSE_CP-800_HEX.pdf@2e511a04 (retrieved 2026-10-16) page 1" in hex_source and hex_source.endswith("the Pronto hex decoded and agreeing")


def sharp_pronto(device, function, word=0x6D, mark=10):
    pairs = []
    for d, f, e in ((device, function, 0), (device, function ^ 0xFF, 3)):
        bits = [(d >> i) & 1 for i in range(5)] + [(f >> i) & 1 for i in range(8)] + [(e >> i) & 1 for i in range(2)]
        pairs += [(mark, mark * (7 if b else 3)) for b in bits] + [(mark, 0x0D00)]
    return " ".join(f"{x:04X}" for x in [0, word, 0, len(pairs), *[v for pair in pairs for v in pair]])


def kaseikyo_pronto(device, subdevice, function, word=0x70, unit=0x10):
    by = [0x54, 0x32, device << 4, subdevice | ((function & 15) << 4), function >> 4]
    by.append(by[2] ^ by[3] ^ by[4])
    bits = [(b >> i) & 1 for b in by for i in range(8)]
    pairs = [(unit * 8, unit * 4)] + [(unit, unit * (3 if b else 1)) for b in bits] + [(unit, 0x0E5A)]
    return " ".join(f"{x:04X}" for x in [0, word, 0, len(pairs), *[v for pair in pairs for v in pair]])


def test_a_denon_master_pronto_hex_is_a_sharp_or_a_kaseikyo_frame_with_its_second_frame_or_parity_byte_checked():
    assert denon.decode_pronto(sharp_pronto(2, 0xC1)) == denon.Frame("Denon", 2, None, 0xC1, 0x6D)
    assert denon.decode_pronto(sharp_pronto(12, 0x5A, word=0x6B, mark=9)) == denon.Frame("Denon", 12, None, 0x5A, 0x6B)
    assert denon.decode_pronto(kaseikyo_pronto(4, 1, 2)) == denon.Frame("Denon-K", 4, 1, 2, 0x70)
    assert denon.decode_pronto(kaseikyo_pronto(4, 7, 0x2A5, word=0x6C, unit=0x0F)) == denon.Frame("Denon-K", 4, 7, 0x2A5, 0x6C)
    bad_second = sharp_pronto(2, 0xC1).split()
    bad_second[4 + 2 * 17 + 1] = "000A"                                         # the second frame's second space is a 0 where it was a 1
    assert "not a function and its inverse" in denon.decode_pronto(" ".join(bad_second))
    words = kaseikyo_pronto(4, 1, 2).split()
    words[4 + 2 * 25 + 1] = "0010"                                              # a bit of the data flipped: the parity byte no longer agrees
    assert "parity byte" in denon.decode_pronto(" ".join(words))
    other = kaseikyo_pronto(4, 1, 2).split()
    other[4 + 2 * 1 + 1] = "0030"                                               # the first bit of the vendor byte
    assert "vendor" in denon.decode_pronto(" ".join(other))
    assert "header" in denon.decode_pronto("0000 006D 0000 0020 000A") and denon.decode_pronto("zzzz") == "not hex"
    assert "neither" in denon.decode_pronto("0000 006D 0000 0004 000A 001E 000A 0046 000A 001E 000A 0046")


def test_a_denon_master_sheet_is_names_and_pronto_hex_with_the_headings_above_them_and_a_sharp_sheet_has_its_device_code_and_function_before_the_name():
    pronto = sharp_pronto(2, 72)
    rows = [["Denon Main Zone ID#1", "", "", "Legend"], [" ", "Pronto Hex Code", "", ""], ["Power", "", "", ""], ["Main Zone ON", pronto, "", ""], ["", "", "", ""],
            ["Source Input", "", "", ""], ["1.0", "", pronto, ""], ["Device Code", "Function#", "Function Name", "Pronto Hex Code"], ["2", "72", "Zone3 Tuner", pronto]]
    got = denon.parse_master_sheet(rows)
    assert [(r.row, r.name, r.section, r.given) for r in got] == [(4, "Main Zone ON", "Power", ()), (7, "1", "Source Input", ()), (9, "Zone3 Tuner", "Source Input", ("2", "72"))]
    assert denon.SHEETS["Zone 4 ID #2"] == "AVRxx08 Zone 4 ID#2" and denon.SHEETS["Sharp device 4"] == "AVRxx07 and earlier Sharp Device 4" and len(denon.SHEETS) == 52


def test_denons_master_spreadsheet_is_57_files_and_2770_keys_that_decode_and_the_sheets_own_codes_agree_with_the_hex():
    files = {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "denon").glob("*.json")) if not p.name.startswith("AVR-4311")}
    assert len(files) == 57 and sum(len(f["keys"]) for f in files.values()) == 2_770
    sharp = {n: f for n, f in files.items() if f["protocol"]["name"] == "Denon"}
    kaseikyo = {n: f for n, f in files.items() if f["protocol"]["name"] == "Denon-K"}
    assert (len(sharp), len(kaseikyo)) == (10, 47)
    main = files["AVRxx08_Main_Zone_ID_1"]["keys"]
    on = main["KEY_MAIN_ZONE_ON"]["forms"][0]
    assert (on["device"], on["function"], "subdevice" in on) == ("0x02", "0x79", False)               # `MAIN ZONE ON` of the AVR-4311's Sharp table: 57 and the extension 01
    assert "denon.com Master IR Hex/Denon_Master_IR_Hex.xls@133b7db2 (retrieved 2026-10-16, from the Wayback Machine snapshot 20220505222023) sheet 'Main Zone ID#1' row 4 'Main Zone ON' (Pronto hex, frequency word 0x006D, section 'Power')" in on["source"]
    # the Sharp and Kaseikyo sheets give each row's device code and function number beside the hex, and the hex says the same in every row but one
    checked = [s for n, f in files.items() if n.startswith("AVRxx07") for s in f["keys"].values()]
    assert len(checked) == 482 and sum("sheet's own Device Code" in s["forms"][0]["source"] for s in checked) == 1
    assert all(f["keys"] for f in files.values()) and {f["manufacturer"] for f in files.values()} == {"Denon"}


# --- Cyrus ---------------------------------------------------------------------------------------------------------


def test_a_cyrus_table_is_the_codes_and_the_products_names_told_from_the_standards_by_place_and_a_blank_cell_is_no_key():
    document = cyrus.Document("x.pdf", 19, ("AV5",), 153, (320, 408))
    rows = [["1", "73", "56", "CYRUS AV5 REMOTE CODES"], ["1", "121", "64", "Code", "105", "RC5 Amplifier 2 (19)", "201", "Cyrus AV5 & AV8", "320", "Code"],
            ["1", "130", "71", "0", "138", "0", "325", "64"], ["1", "140", "71", "1", "138", "1", "218", "Input 1*", "325", "65"],
            ["1", "241", "69", "12", "127", "Standby", "218", "Standby", "325", "76"], ["1", "250", "69", "13", "118", "Mute/Demute", "223", "Mute", "325", "77"],
            ["1", "287", "69", "16", "125", "Volume +", "325", "80", "472", "Adjust up"], ["1", "731", "56", "NOTE:- Codes marked * are available from version 1.2"]]
    got = cyrus.parse(rows, document)
    assert [(r.code, r.name, r.standard) for r in got] == [(1, "Input 1*", "1"), (12, "Standby", "Standby"), (13, "Mute", "Mute/Demute"), (80, "Adjust up", "")]
    assert [(lo, split, end) for lo, split, end in cyrus.tables(document)] == [(60, 153, 305), (305, 408, 10_000)]
    assert cyrus.tables(cyrus.Document("y.pdf", 16, ("3i",), 181)) == [(60, 181, 10_000)]


def test_cyrus_thirteen_one_page_tables_are_twenty_files_and_435_keys_and_a_second_table_is_the_codes_64_to_127():
    files = {p.stem: load(p) for p in sorted((ROOT / oi.IMPORT_ROOT / "cyrus").glob("*.json"))}
    assert {n: len(f["keys"]) for n, f in files.items()} == {
        "3": 14, "3i": 16, "5": 16, "6": 16, "7": 16, "8": 16, "AV5": 31, "AV8": 31, "AV_Master": 39, "CD_player": 25, "DVD_7": 32, "FM7.5": 21, "FM7": 21,
        "FMX": 21, "Pre": 16, "PreX": 16, "Quattro": 26, "aCA7.5": 16, "dAD1": 26, "original_tuner": 20}
    assert sum(len(f["keys"]) for f in files.values()) == 435 and all(s["forms"][0]["confidence"] == "plausible" for f in files.values() for s in f["keys"].values())
    assert {n: {s["forms"][0]["device"] for s in f["keys"].values()} for n, f in files.items() if n in ("3i", "AV5", "CD_player", "DVD_7", "Quattro", "original_tuner")} == {
        "3i": {"0x10"}, "AV5": {"0x13"}, "CD_player": {"0x14"}, "DVD_7": {"0x19"}, "Quattro": {"0x15"}, "original_tuner": {"0x11"}}
    assert all(f["protocol"] == {"name": "RC5", "carrierHz": 36000, "minSends": 1} for f in files.values())
    assert files["AV5"]["keys"] == files["AV8"]["keys"] and files["FM7"]["keys"] == files["FMX"]["keys"] == files["FM7.5"]["keys"] and files["5"]["keys"] == files["8"]["keys"]
    high = {s["label"]: int(s["forms"][0]["function"], 0) for s in files["AV5"]["keys"].values() if int(s["forms"][0]["function"], 0) >= 64}
    assert high["Adjust up"] == 80 and high["Subwoofer select"] == 118 and len(high) == 12
    source = files["3i"]["keys"]["KEY_STANDBY"]["forms"][0]["source"]
    assert source.startswith("cyrusaudio.com remote code tables/Cyrus3iremotecodes.pdf@8178e865 (retrieved 2026-10-16, from the Wayback Machine snapshot 20070306121238) page 1 row 'Standby' (RC-5 system code 16, code 12, the standard's name for it 'Standby')")
    assert "the document names the Cyrus 5 and Cyrus 7" in files["6"]["keys"]["KEY_STANDBY"]["forms"][0]["source"]


def test_the_ledgers_jp1_cyrus_remotes_have_the_codes_of_the_acA75_and_the_tuner_tables_but_the_power_on_and_the_stores_code():
    def codes(path):
        return {(int(s["forms"][0]["device"], 0), int(s["forms"][0]["function"], 0)): s["label"] for s in load(ROOT / path)["keys"].values()}
    jp1, ours = codes("remotes/jp1/Cyrus/Audio-Cyrus_aCA7.5_Pre.json"), codes("remotes/official/cyrus/aCA7.5.json")
    assert (len(jp1), len(ours), len(set(jp1) & set(ours))) == (16, 16, 15)
    assert jp1[(16, 59)] == "Power on" and ours[(16, 58)] == "Power on" and (16, 59) not in ours             # the table's `Power on` is 58, the 3i's and the JP1's 59
    assert codes("remotes/official/cyrus/3i.json")[(16, 59)] == "Power on"
    tuner, table = codes("remotes/jp1/Cyrus/Audio-Cyrus_AM-FM_Tuner.json"), codes("remotes/official/cyrus/original_tuner.json")
    assert (len(tuner), len(table), len(set(tuner) & set(table))) == (22, 20, 19)
    assert tuner[(17, 10)] == "Store" and table[(17, 17)] == "Store"
