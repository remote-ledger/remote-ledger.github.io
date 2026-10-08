"""``rl import official`` (DESIGN D123 to D127): Marantz's command charts and Anthem's IR hex sheet,
their snapshots, the readers, and the committed tree."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.official import anthem, marantz
from remote_ledger.official import importer as oi
from remote_ledger.official.common import MANIFEST, Report, load_snapshot
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / oi.SNAPSHOT


# --- the snapshot ------------------------------------------------------------------------------


def test_every_document_and_csv_is_the_one_the_manifest_pins():
    for maker in ("marantz", "anthem"):
        snap = load_snapshot(SNAPSHOT / maker)
        assert snap.maker == maker and snap.retrieved == "2026-10-08"
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
    hashes = {}
    for maker in ("marantz", "anthem"):
        manifest = json.loads((SNAPSHOT / maker / MANIFEST).read_text(encoding="utf-8"))
        hashes.update({n: e["sha256"] for n, e in manifest["documents"].items()})
        retrieved = manifest["retrieved"]
    shape = re.compile(r"^(?:marantz\.com archive-downloads|anthemav\.com \(storage\.googleapis\.com/sandbox1-anthemav/an\))/"
                       r"(\S+)@([0-9a-f]{8}) \(retrieved (\d{4}-\d{2}-\d{2})\) sheet '[^']+' row (\d+) ")
    files = sorted((ROOT / oi.IMPORT_ROOT).rglob("*.json"))
    assert len(files) > 150
    bad = []
    for path in files:
        for key, spec in load(path)["keys"].items():
            if len(spec["forms"]) != 1:
                bad.append(f"{path.name}:{key}")
            for form in spec["forms"]:
                m = shape.match(form.get("source", ""))
                ok = (form["confidence"] in ("plausible", "untested") and "verifiedBy" not in form
                      and form["type"] in ("pronto", "irp") and m is not None
                      and hashes.get(m[1], "").startswith(m[2]) and m[3] == retrieved)
                if not ok:
                    bad.append(f"{path.name}:{key}")
    assert bad == []
