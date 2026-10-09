"""``rl import official`` (DESIGN D123 to D127): Marantz's command charts and Anthem's IR hex sheet,
their snapshots, the readers, and the committed tree."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.official import anthem, marantz, oppo
from remote_ledger.official import importer as oi
from remote_ledger.official.common import MANIFEST, Report, load_snapshot
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / oi.SNAPSHOT


# --- the snapshot ------------------------------------------------------------------------------


def test_every_document_and_csv_is_the_one_the_manifest_pins():
    for maker, retrieved in (("marantz", "2026-10-08"), ("anthem", "2026-10-08"), ("oppo", "2026-10-09")):
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
    # an extension command with no hex, and a command with no RC-5 system, are counted, once however many models list them
    assert dict(report.unrepresented) == {
        ("marantz", marantz.DOCUMENTS[-1], "an RC-5 extension command with no Pronto hex"): 1,
        ("marantz", marantz.DOCUMENTS[-1], "no RC-5 system and command the ledger can send"): 1}


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
    for maker in ("marantz", "anthem", "oppo"):
        manifest = json.loads((SNAPSHOT / maker / MANIFEST).read_text(encoding="utf-8"))
        for name, entry in manifest["documents"].items():
            hashes[name], dates[name], snapshots[name] = entry["sha256"], manifest["retrieved"], entry.get("snapshot")
    shape = re.compile(r"^(?:marantz\.com archive-downloads|anthemav\.com \(storage\.googleapis\.com/sandbox1-anthemav/an\)"
                       r"|download\.oppodigital\.com/\w+)/(\S+)@([0-9a-f]{8}) \(retrieved (\d{4}-\d{2}-\d{2})"
                       r"(?:, from the Wayback Machine snapshot (\d{14}))?\) sheet '[^']+' row (\d+) ")
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
