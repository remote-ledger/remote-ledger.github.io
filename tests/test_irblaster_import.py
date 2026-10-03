"""``rl import irblaster`` (SPEC R19, decisions D46 to D56).

Everything here runs on a tiny synthetic SQL dump written for the test, with
the real schema's four tables: the importer is tested on what its rules say,
not on SwiftRemote's data. (The real dump is run and measured by
``tools/irblaster_oracle_import.py``.) Hexcodes are ones an independent source
decodes (``tests/test_irblaster_unknown.py`` ``ANCHORS``, the NEC family's
``20DF10EF``, Sony's TV power ``A90``).
"""

from __future__ import annotations

import json
import random
import re
import subprocess
from pathlib import Path

import pytest

from remote_ledger.check import check_remote
from remote_ledger.cli import main
from remote_ledger.irblaster import importer as I
from remote_ledger.irblaster.importer import (
    IMPORT_ROOT, REPORT, dir_slug, fold_label, key_names, pick_manufacturer, write_import,
)
from remote_ledger.paths import IMPORTS, imported_from
from remote_ledger.remote import load_remote
from remote_ledger.validate import corpus_files, validate_file

COMMIT = "6aafd15e1c95cf494ac729339b9a4701a4ab8f0a"

def schema(primary_key: bool) -> str:
    """The real dump's four tables; without the primary keys when a test needs
    rows the real schema forbids (exact duplicates)."""
    keys_pk = ", PRIMARY KEY (id, label, hexcode, protocol)" if primary_key else ""
    models_pk = ", PRIMARY KEY (brand, model, id)" if primary_key else ""
    return (
        "PRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\n"
        "CREATE TABLE brands (name TEXT PRIMARY KEY);\n"
        'CREATE TABLE IF NOT EXISTS "remotes" (id INTEGER PRIMARY KEY);\n'
        'CREATE TABLE IF NOT EXISTS "keys" (id INTEGER NOT NULL, label TEXT NOT NULL, '
        f'hexcode TEXT NOT NULL, protocol TEXT NOT NULL{keys_pk}, '
        'FOREIGN KEY (id) REFERENCES "remotes"(id) ON DELETE CASCADE);\n'
        'CREATE TABLE IF NOT EXISTS "models" (brand TEXT NOT NULL, model TEXT NOT NULL, '
        f"id INTEGER NOT NULL{models_pk});\n"
    )


#: One of each case the decisions name. Keys are (label, hexcode, DB protocol).
REMOTES = {
    # single brand, three NEC keys and one the NEC1 encoder cannot hold
    1: {"models": [("ACME", "TV-1"), ("ACME", "TV-2")],
        "keys": [("POWER", "20DF10EF", "NEC"), ("VOL+", "20DF40BF", "NEC"),
                 ("VOL-", "20DFC03F", "NEC"), ("BROKEN", "20DF10EE", "NEC")]},
    # several brands: ACME has the most models; Sony12 power
    2: {"models": [("ZED", "Z1"), ("ACME", "A1"), ("ACME", "A2")],
        "keys": [("POWER", "A90", "SONY12")]},
    # two DB protocols, two ledger protocols
    # (also two models rows that spell the same "<BRAND> <MODEL>")
    3: {"models": [("BRAND", "M"), ("BRAND", "N"), ("A B", "C"), ("A", "B C")],
        "keys": [("POWER", "20DF10EF", "NEC"), ("1", "801", "RC5")]},
    # REC80 is a bag of vendors: Panasonic and Denon-K split, one bad Panasonic
    4: {"models": [("PANA", "P")],
        "keys": [("AUDIO", "40040D00CCC1", "REC80"), ("UP", "2A4C028D800F", "REC80"),
                 ("BADP", "400405100104", "REC80")]},
    # the same folded name on several codes, "??", and a same-code collision
    5: {"models": [("DUP", "D")],
        "keys": [("VOL+", "20DF40BF", "NEC"), ("VOL+", "20DFC03F", "NEC"),
                 ("VOL +", "20DF807F", "NEC"), ("POWER", "20DF10EF", "NEC"),
                 ("??", "20DF08F7", "NEC"), ("??", "20DF8877", "NEC"),
                 ("OK", "20DF22DD", "NEC"), ("ok", "20DF22DD", "NEC"),
                 ("A-B", "20DF48B7", "NEC"), ("A B", "20DFC837", "NEC")]},
    # nothing representable: a code the map refuses, a protocol with no map
    6: {"models": [("BAD", "B")],
        "keys": [("X", "20DF10EE", "NEC"), ("Y", "ABC", "FOO")]},
    # Samsung36 carries a unitUs claim
    7: {"models": [("SAM", "S")], "keys": [("UP", "0400E18", "Samsung36")]},
    # a brand that is no directory name
    8: {"models": [("A/B C.", "M")], "keys": [("POWER", "20DF10EF", "NECx2")]},
}


def sql_text(remotes=REMOTES, *, shuffle: int | None = None, primary_key: bool = True,
             extra_keys=(), keyless_ids=()) -> str:
    def q(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    statements = []
    for db_id, spec in remotes.items():
        statements.append(f"INSERT INTO remotes VALUES({db_id});")
        for brand in sorted({b for b, _ in spec["models"]}):
            statements.append(f"INSERT OR IGNORE INTO brands VALUES({q(brand)});")
        for brand, model in spec["models"]:
            statements.append(f"INSERT INTO models VALUES({q(brand)},{q(model)},{db_id});")
        for label, hexcode, protocol in spec["keys"]:
            statements.append(
                f"INSERT INTO keys VALUES({db_id},{q(label)},{q(hexcode)},{q(protocol)});")
    for db_id in keyless_ids:
        statements.append(f"INSERT INTO remotes VALUES({db_id});")
    for db_id, label, hexcode, protocol in extra_keys:
        statements.append(
            f"INSERT INTO keys VALUES({db_id},{q(label)},{q(hexcode)},{q(protocol)});")
    if shuffle is not None:
        random.Random(shuffle).shuffle(statements)     # foreign keys are off
    return schema(primary_key) + "\n".join(statements) + "\nCOMMIT;\n"


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "SwiftRemote"
    (root / "assets" / "db_src").mkdir(parents=True)
    (root / "assets" / "db_src" / "swiftremote.sql").write_text(sql_text(), encoding="utf-8")
    return root


@pytest.fixture
def ledger(tmp_path):
    root = tmp_path / "ledger"
    root.mkdir()
    return root


def tree(ledger: Path) -> dict[str, bytes]:
    return {p.relative_to(ledger).as_posix(): p.read_bytes()
            for p in sorted((ledger / IMPORT_ROOT).rglob("*")) if p.is_file()}


def load(ledger: Path, rel: str):
    return json.loads((ledger / IMPORT_ROOT / rel).read_text(encoding="utf-8"))


@pytest.fixture
def imported(ledger, checkout):
    report = write_import(ledger, checkout, COMMIT)
    return ledger, report


# --- the registration ------------------------------------------------------------------


def test_the_import_root_is_registered_with_its_licence():
    entry = IMPORTS["remotes/irblaster/"]
    assert entry["licence"] == "GPL-3.0-only"
    assert entry["readme"] == "remotes/irblaster/README.md"
    assert entry["name"] == "IR Blaster database (as shipped in SwiftRemote)"
    assert imported_from("remotes/irblaster/ACME/1-NEC1.json") == "remotes/irblaster/"


def test_the_readme_says_plainly_what_the_licence_rests_on():
    base = Path(__file__).resolve().parents[1] / "remotes" / "irblaster"
    readme = " ".join((base / "README.md").read_text().split())
    for must in ("GPL-3.0-only, by inheritance and nothing more",
                 "No project in it names the data's source",
                 "https://github.com/remote-ledger/SwiftRemote",
                 "https://github.com/iodn/android-ir-blaster", "KaijinLab Inc.",
                 "https://github.com/TalkingPanda0/osram-remote",
                 "SwiftRemote is a fork of"):
        assert must in readme, must
    licence = (base / "LICENSE").read_text()
    assert "GNU GENERAL PUBLIC LICENSE" in licence and "Version 3, 29 June 2007" in licence


def test_every_db_protocol_has_a_hex_map_a_reading_and_a_citation_phrase():
    assert len(I.FROM_DB_HEX) == 23
    assert set(I.FROM_DB_HEX) == set(I.FROM_DB_HEX_APP) == set(I.HOW)
    assert set(I.MIN_SENDS) <= set(I.FROM_DB_HEX)


# --- names (D47, D48, D49) ----------------------------------------------------------------


@pytest.mark.parametrize("label, expected", [
    ("POWER", "KEY_POWER"),
    ("power", "KEY_POWER"),
    ("VOL+", "KEY_VOL_PLUS"),
    ("VOL +", "KEY_VOL_PLUS"),
    ("CH-", "KEY_CH_MINUS"),
    ("-", "KEY_MINUS"),
    ("+", "KEY_PLUS"),
    ("A/B", "KEY_A_SLASH_B"),
    ("*", "KEY_STAR"),
    ("#", "KEY_HASH"),
    ("??", "KEY_UNLABELED"),
    ("?", "KEY_"),
    ("A-B", "KEY_A_MINUS_B"),
    ("A B", "KEY_A_B"),
    ("  TV / AV  ", "KEY_TV_SLASH_AV"),
    ("10+", "KEY_10_PLUS"),
    ("1 \t\t\t", "KEY_1"),
    ("INPUT (HDMI)", "KEY_INPUT_HDMI"),
    ("P◄-►P", "KEY_P_MINUS_P"),
    ("►", "KEY_"),
    ("⏩", "KEY_"),
    ("Ünïcode", "KEY_N_CODE"),
    ("ß", "KEY_"),
])
def test_a_label_folds_to_a_key_name_mechanically(label, expected):
    assert I.key_base(label) == expected
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expected)


def test_only_ascii_letters_are_upper_cased():
    """``str.upper`` would turn some non-ASCII letters into ASCII ones, and its
    tables move between Python versions; the name must not depend on either."""
    assert "ß".upper() == "SS" and fold_label("ß") == ""
    assert "ſ".upper() == "S" and fold_label("ſ") == ""


@pytest.mark.parametrize("text, expected", [
    ("ACME", "ACME"), ("A/B C.", "A_B_C_"), ("C.P.", "C.P_"), (".", "_"), ("..", "_"),
    (".x", "_x"), ("...", "_"), ("", "_"), ("a b&c", "a_b_c"), ("1&1", "1_1"),
])
def test_a_directory_named_after_a_brand_cannot_escape_or_end_in_a_dot(text, expected):
    assert dir_slug(text) == expected


def test_key_names_do_not_depend_on_row_order():
    members = [("VOL+", "B", "NEC"), ("VOL+", "A", "NEC"), ("POWER", "C", "NEC")]
    a = key_names(members)
    b = key_names(list(reversed(members)))
    assert a == b
    assert a[("POWER", "C", "NEC")] == "KEY_POWER"
    assert a[("VOL+", "A", "NEC")] == "KEY_VOL_PLUS_A"
    assert a[("VOL+", "B", "NEC")] == "KEY_VOL_PLUS_B"


def test_a_folded_name_on_one_code_alone_keeps_its_plain_name():
    assert key_names([("POWER", "A", "NEC")]) == {("POWER", "A", "NEC"): "KEY_POWER"}
    # the same (label, hexcode) under two DB protocols is one distinct pair
    names = key_names([("POWER", "A", "NEC"), ("POWER", "A", "NEC2")])
    assert set(names.values()) == {"KEY_POWER", "KEY_POWER_2"}


def test_labels_that_fold_alike_on_one_code_get_numbered_in_sorted_order():
    names = key_names([("ok", "A", "NEC"), ("OK", "A", "NEC")])
    assert names[("OK", "A", "NEC")] == "KEY_OK_A"
    assert names[("ok", "A", "NEC")] == "KEY_OK_A_2"


def test_three_labels_that_fold_alike_on_one_code_are_numbered_in_sorted_order():
    names = key_names([("ok", "A", "NEC"), ("OK", "A", "NEC"), ("Ok", "A", "NEC")])
    assert [names[m] for m in sorted(names)] == ["KEY_OK_A", "KEY_OK_A_2", "KEY_OK_A_3"]


def test_a_numbered_name_that_another_key_already_has_is_numbered_again():
    members = [("OK", "A", "NEC"), ("ok", "A", "NEC"), ("OK A 2", "B", "NEC")]
    names = key_names(members)
    assert names[("OK", "A", "NEC")] == "KEY_OK_A"
    assert names[("ok", "A", "NEC")] == "KEY_OK_A_2"
    assert names[("OK A 2", "B", "NEC")] == "KEY_OK_A_2_2"
    assert len(set(names.values())) == 3


def test_the_manufacturer_is_the_brand_with_most_models_and_keeps_its_casing():
    assert pick_manufacturer([("ZED", "1"), ("Acme", "1"), ("Acme", "2")]) == "Acme"
    # a tie goes to the casefolded alphabetical first, then to the exact string
    assert pick_manufacturer([("beta", "1"), ("ALPHA", "1")]) == "ALPHA"
    assert pick_manufacturer([("BETA", "1"), ("alpha", "1")]) == "alpha"    # not ASCII order
    assert pick_manufacturer([("acme", "1"), ("ACME", "1")]) == "ACME"
    assert pick_manufacturer([("Bb", "1"), ("aA", "1"), ("AA", "9"), ("aA", "2")]) == "aA"


# --- the files (D47) ---------------------------------------------------------------------


def test_one_file_per_database_id_and_ledger_protocol(imported):
    ledger, report = imported
    written = sorted(p.relative_to(ledger / IMPORT_ROOT).as_posix()
                     for p in (ledger / IMPORT_ROOT).rglob("*.json"))
    assert written == [
        "ACME/1-NEC1.json",
        "ACME/2-Sony12.json",
        "A_B_C_/8-NECx2.json",
        "BRAND/3-NEC1.json",
        "BRAND/3-RC5.json",
        "DUP/5-NEC1.json",
        "PANA/4-Denon-K.json",
        "PANA/4-Panasonic.json",
        "SAM/7-Samsung36.json",
    ]
    assert report.files == 9 and report.remotes["imported"] == 9
    assert report.ids == 8 and report.ids_split == 2 and report.ids_without_file == 1


def test_a_file_holds_one_protocol(imported):
    ledger, _ = imported
    for path in (ledger / IMPORT_ROOT).rglob("*.json"):
        doc = json.loads(path.read_text())
        assert doc["protocol"]["name"] in path.name
        assert "variants" not in doc and "layouts" not in doc


def test_a_ledger_protocol_split_of_one_database_protocol(imported):
    ledger, _ = imported
    pana = load(ledger, "PANA/4-Panasonic.json")
    denon = load(ledger, "PANA/4-Denon-K.json")
    assert pana["model"] == "IR Blaster DB 4 (Panasonic)"
    assert denon["model"] == "IR Blaster DB 4 (Denon-K)"
    assert pana["manufacturer"] == denon["manufacturer"] == "PANA"
    assert list(pana["keys"]) == ["KEY_AUDIO"]       # BADP is not representable
    assert list(denon["keys"]) == ["KEY_UP"]


def test_manufacturer_model_controls_and_aliases(imported):
    ledger, _ = imported
    one = load(ledger, "ACME/1-NEC1.json")
    assert one["manufacturer"] == "ACME"
    assert one["model"] == "IR Blaster DB 1 (NEC1)"
    assert one["aliases"] == []
    # every models row, "<BRAND> <MODEL>", sorted and de-duplicated
    assert one["controls"] == ["ACME TV-1", "ACME TV-2"]
    two = load(ledger, "ACME/2-Sony12.json")
    assert two["manufacturer"] == "ACME"
    assert two["controls"] == ["ACME A1", "ACME A2", "ZED Z1"]
    three = load(ledger, "BRAND/3-NEC1.json")
    assert three["manufacturer"] == "BRAND"
    assert three["controls"] == ["A B C", "BRAND M", "BRAND N"]     # one "A B C"
    assert three["model"] == "IR Blaster DB 3 (NEC1)"
    assert load(ledger, "BRAND/3-RC5.json")["model"] == "IR Blaster DB 3 (RC5)"


def test_the_brand_that_is_no_directory_name_is_a_safe_one(imported):
    ledger, _ = imported
    doc = load(ledger, "A_B_C_/8-NECx2.json")
    assert doc["manufacturer"] == "A/B C."            # the database's own spelling


# --- keys and forms (D49, D50, D51) -----------------------------------------------------------


def test_each_key_holds_one_plausible_irp_form_from_the_wire_reading(imported):
    ledger, _ = imported
    one = load(ledger, "ACME/1-NEC1.json")
    assert sorted(one["keys"]) == ["KEY_POWER", "KEY_VOL_MINUS", "KEY_VOL_PLUS"]
    form, = one["keys"]["KEY_POWER"]["forms"]
    assert form["type"] == "irp" and form["confidence"] == "plausible"
    assert (form["device"], form["subdevice"], form["function"]) == ("0x04", "0xFB", "0x08")
    assert form["id"] == "primary.irp"
    assert "candidate" not in form and "verifiedBy" not in form
    for doc_path in (ledger / IMPORT_ROOT).rglob("*.json"):
        for spec in json.loads(doc_path.read_text())["keys"].values():
            assert len(spec["forms"]) == 1 and spec["forms"][0]["type"] == "irp"
            assert spec["forms"][0]["confidence"] == "plausible"


def test_sony_is_read_in_wire_order_not_the_apps_way(imported):
    """TV power is D=1 F=21 and goes out as ``A90``; SwiftRemote reads ``A90``
    as command 0x10 on address 0x15 (hex_sony)."""
    ledger, report = imported
    form, = load(ledger, "ACME/2-Sony12.json")["keys"]["KEY_POWER"]["forms"]
    assert (form["device"], form["function"]) == ("0x01", "0x15")
    assert "subdevice" not in form
    assert report.app_differs["SONY12"]["A90"] == (
        ("Sony12", 1, None, 21), ("Sony12", 21, None, 16))


def test_subdevice_is_omitted_when_the_protocol_has_none(imported):
    ledger, _ = imported
    form, = load(ledger, "BRAND/3-RC5.json")["keys"]["KEY_1"]["forms"]
    assert "subdevice" not in form and (form["device"], form["function"]) == ("0x00", "0x01")


def test_a_label_that_folds_alike_on_several_codes_names_every_member_by_its_code(imported):
    ledger, _ = imported
    keys = load(ledger, "DUP/5-NEC1.json")["keys"]
    assert sorted(keys) == [
        "KEY_A_B",
        "KEY_A_MINUS_B",
        "KEY_OK_20DF22DD",
        "KEY_OK_20DF22DD_2",
        "KEY_POWER",
        "KEY_UNLABELED_20DF08F7",
        "KEY_UNLABELED_20DF8877",
        "KEY_VOL_PLUS_20DF40BF",
        "KEY_VOL_PLUS_20DF807F",
        "KEY_VOL_PLUS_20DFC03F",
    ]
    # the original label, with its spacing, is in the citation
    assert "'VOL +' 20DF807F NEC" in keys["KEY_VOL_PLUS_20DF807F"]["forms"][0]["source"]
    assert "'ok' 20DF22DD NEC" in keys["KEY_OK_20DF22DD_2"]["forms"][0]["source"]


CITATION = re.compile(
    r"^irblaster-db@[0-9a-f]{7} remote \d+, '.*' [0-9A-F]+ [A-Za-z0-9_]+: .+ as [A-Za-z0-9_-]+$"
)


def test_every_citation_has_the_fixed_shape_and_names_the_commit(imported):
    ledger, _ = imported
    for path in (ledger / IMPORT_ROOT).rglob("*.json"):
        for spec in json.loads(path.read_text())["keys"].values():
            source = spec["forms"][0]["source"]
            assert CITATION.match(source), source
            assert source.startswith(f"irblaster-db@{COMMIT[:7]} ")
            assert len(source) < 120


def test_the_citation_names_remote_label_code_protocol_and_how(imported):
    ledger, _ = imported
    form, = load(ledger, "ACME/1-NEC1.json")["keys"]["KEY_VOL_PLUS"]["forms"]
    assert form["source"] == ("irblaster-db@6aafd15 remote 1, 'VOL+' 20DF40BF NEC: "
                              "32 wire bits, bytes bit-reversed as NEC1")
    form, = load(ledger, "PANA/4-Denon-K.json")["keys"]["KEY_UP"]["forms"]
    assert form["source"] == ("irblaster-db@6aafd15 remote 4, 'UP' 2A4C028D800F REC80: "
                              "48 wire bits, bytes bit-reversed as Denon-K")


def test_a_key_the_app_reads_the_same_way_is_not_in_the_difference_report(imported):
    _, report = imported
    assert "NEC" not in report.app_differs and "REC80" not in report.app_differs


# --- the protocol block (D52) ----------------------------------------------------------------


@pytest.mark.parametrize("rel, name, carrier, min_sends", [
    ("ACME/1-NEC1.json", "NEC1", 38400, 1),
    ("ACME/2-Sony12.json", "Sony12", 40000, 3),
    ("BRAND/3-RC5.json", "RC5", 36000, 1),
    ("PANA/4-Panasonic.json", "Panasonic", 37000, 1),
    ("A_B_C_/8-NECx2.json", "NECx2", 38000, 2),
])
def test_the_protocol_block_carries_the_registrys_carrier_and_the_min_sends(
        imported, rel, name, carrier, min_sends):
    ledger, _ = imported
    block = load(ledger, rel)["protocol"]
    assert block == {"name": name, "carrierHz": carrier, "minSends": min_sends}


def test_every_carrier_is_the_registrys_so_no_carrier_warning_appears(imported):
    ledger, _ = imported
    for path in corpus_files(ledger):
        _, warnings = check_remote(load_remote(path))
        assert warnings == [], [str(w) for w in warnings]


def test_samsung36_alone_overrides_the_unit_and_says_why(imported):
    ledger, _ = imported
    block = load(ledger, "SAM/7-Samsung36.json")["protocol"]
    assert block["unitUs"] == 500
    claim = block["claims"]["unitUs"]
    assert claim["reason"] and "560" in claim["reason"] and "500" in claim["reason"]
    assert "IrpTransmogrifier" in claim["source"] and "IRremoteESP8266" in claim["source"]
    for path in (ledger / IMPORT_ROOT).rglob("*.json"):
        if path.name != "7-Samsung36.json":
            assert "unitUs" not in json.loads(path.read_text())["protocol"]
    form, = load(ledger, "SAM/7-Samsung36.json")["keys"]["KEY_UP"]["forms"]
    assert (form["device"], form["subdevice"], form["function"]) == ("0x20", "0x00", "0x718")


def test_samsung36_compiles_at_a_500us_unit(imported):
    from remote_ledger import pronto
    ledger, _ = imported
    remote = load_remote(ledger / IMPORT_ROOT / "SAM/7-Samsung36.json")
    signal = pronto.decode(remote.compile_group("KEY_UP", "primary"), carrier_hz=37900)
    marks = signal.repeat[0::2]
    assert 4400 <= marks[0] <= 4600 and 480 <= marks[1] <= 540     # header 4500, bit mark ~500


# --- what is skipped, and where it says so (D54) ----------------------------------------------


def test_a_code_no_map_can_hold_is_skipped_with_its_reason(imported):
    ledger, report = imported
    assert "KEY_BROKEN" not in load(ledger, "ACME/1-NEC1.json")["keys"]
    reasons = {row[0] for row in report.skipped_keys}
    assert any(r.startswith("byte 4 is not the complement of byte 3") for r in reasons)
    assert "REC80 Panasonic frame: check byte is not D^S^F" in reasons
    assert "no hex map for the DB protocol FOO" in reasons
    assert len(report.skipped_keys) == 4        # BROKEN, BADP, X, Y


def test_a_remote_with_no_representable_key_writes_no_file(imported):
    ledger, report = imported
    assert not (ledger / IMPORT_ROOT / "BAD").exists()
    assert report.skipped_remotes == [(6, "BAD", 2, "FOO, NEC")]
    assert report.remotes["skipped: no key of the id is representable"] == 1


def test_the_report_says_all_of_it(imported):
    ledger, report = imported
    text = (ledger / IMPORT_ROOT / REPORT).read_text()
    assert text == report.render()
    assert f"@ `{COMMIT}`" in text
    assert "IR Blaster database, as shipped in SwiftRemote; original source unknown upstream" in text
    assert "github.com/remote-ledger/SwiftRemote" in text
    for heading in ("## Totals", "## By ledger protocol", "## By database protocol",
                    "## Keys skipped, by reason (4 keys)",
                    "## Remotes skipped: no key of the id is representable (1)",
                    "## Where SwiftRemote's own reading differs from the ledger's"):
        assert heading in text, heading
    assert "| Remote ids in the database | 8 |" in text
    assert "| Files written | 9 |" in text
    assert "| Key rows in the database | 24 |" in text
    assert "| Keys imported | 20 |" in text and "| Keys skipped | 4 |" in text
    # one row per skipped key, with the remote, the DB protocol, the code, the label
    assert "| 4 | REC80 | 400405100104 | `BADP` |" in text
    assert "| 6 | FOO | ABC | `Y` |" in text
    assert "| 6 | ACME" not in text
    assert "| 6 | `BAD` | 2 | FOO, NEC |" in text


def test_every_key_row_is_accounted_for(imported):
    _, report = imported
    imported_keys = sum(n for k, n in report.keys.items() if k.startswith("imported"))
    skipped = sum(n for k, n in report.keys.items() if k.startswith("skipped"))
    assert report.key_rows == imported_keys + skipped + report.duplicates == 24


def test_the_per_protocol_table_counts_codes_and_keys(imported):
    _, report = imported
    nec = report.protocols["NEC"]
    assert len(nec.codes) == 10 and nec.imported == 14 and nec.skipped == 2
    rec = report.protocols["REC80"]
    assert (rec.keys, rec.imported, rec.skipped) == (3, 2, 1)
    assert rec.ledger == {"Panasonic", "Denon-K"}
    assert report.protocols["FOO"].skipped == 1 and not report.protocols["FOO"].ledger


def test_the_difference_report_counts_codes_and_keys(imported):
    ledger, report = imported
    text = (ledger / IMPORT_ROOT / REPORT).read_text()
    assert set(report.app_differs) == {"SONY12"}
    assert report.app_keys["SONY12"] == 1
    assert "| SONY12 | 1 | 1 | 0 | 1 | 1 |" in text
    assert "| SONY12 | A90 | Sony12 D=1 S=- F=21 | Sony12 D=21 S=- F=16 |" in text


def test_exact_duplicate_rows_are_dropped_and_counted(tmp_path, ledger):
    root = tmp_path / "dups"
    (root / "assets/db_src").mkdir(parents=True)
    keys = REMOTES[1]["keys"][:2]
    (root / "assets/db_src/swiftremote.sql").write_text(
        sql_text({1: {"models": REMOTES[1]["models"], "keys": keys + keys}}, primary_key=False),
        encoding="utf-8")
    report = write_import(ledger, root, COMMIT)
    assert report.duplicates == 2 and report.key_rows == 4
    assert sorted(load(ledger, "ACME/1-NEC1.json")["keys"]) == ["KEY_POWER", "KEY_VOL_PLUS"]


def test_an_id_with_no_models_is_skipped_not_filed_under_a_made_up_brand(tmp_path, ledger):
    root = tmp_path / "nomodels"
    (root / "assets/db_src").mkdir(parents=True)
    (root / "assets/db_src/swiftremote.sql").write_text(
        sql_text({1: {"models": [], "keys": [("POWER", "20DF10EF", "NEC")]}}), encoding="utf-8")
    report = write_import(ledger, root, COMMIT)
    assert report.files == 0
    assert report.skipped_remotes == [(1, "", 1, "NEC")]
    assert "the id has no models row" in report.skipped_keys[0][0]


def test_a_remote_without_keys_is_reported_and_writes_nothing(tmp_path, ledger):
    root = tmp_path / "nokeys"
    (root / "assets/db_src").mkdir(parents=True)
    (root / "assets/db_src/swiftremote.sql").write_text(
        sql_text({1: REMOTES[1]}, keyless_ids=(2,)), encoding="utf-8")
    report = write_import(ledger, root, COMMIT)
    assert report.ids == 2 and report.ids_without_file == 1 and report.files == 1


def test_a_key_of_an_id_missing_from_remotes_is_imported_not_dropped(tmp_path, ledger):
    root = tmp_path / "orphan"
    (root / "assets/db_src").mkdir(parents=True)
    (root / "assets/db_src/swiftremote.sql").write_text(
        sql_text({1: REMOTES[1]}, extra_keys=[(99, "POWER", "20DF10EF", "NEC")]),
        encoding="utf-8")
    report = write_import(ledger, root, COMMIT)
    assert report.ids == 2 and report.key_rows == 5
    assert report.skipped_keys[-1][0] == ("the id has no models row, so there is no "
                                          "manufacturer to file it under")


# --- authored data wins (D56, R19.4) -------------------------------------------------------------


def test_authored_data_wins_and_the_report_says_so(checkout, ledger):
    authored = ledger / "remotes" / "acme"
    authored.mkdir(parents=True)
    (authored / "curated.json").write_text(json.dumps({
        "manufacturer": "Acme", "model": "ir blaster db 1 (nec1)",
        "aliases": [], "protocol": {"carrierHz": 38000, "minSends": 1}, "keys": {},
    }))
    report = write_import(ledger, checkout, COMMIT)
    assert not (ledger / IMPORT_ROOT / "ACME" / "1-NEC1.json").exists()
    assert (ledger / IMPORT_ROOT / "ACME" / "2-Sony12.json").exists()      # only the collision
    assert report.collisions == [(1, "remotes/irblaster/ACME/1-NEC1.json", "IR Blaster DB 1 (NEC1)")]
    assert report.keys["skipped: an authored remote wins"] == 3
    text = (ledger / IMPORT_ROOT / REPORT).read_text()
    assert "remotes/irblaster/ACME/1-NEC1.json" in text
    imported_keys = sum(n for k, n in report.keys.items() if k.startswith("imported"))
    skipped = sum(n for k, n in report.keys.items() if k.startswith("skipped"))
    assert report.key_rows == imported_keys + skipped


def test_an_authored_remote_inside_the_import_root_is_not_an_authored_collision(checkout, ledger):
    """The import's own previous output must not shadow itself."""
    write_import(ledger, checkout, COMMIT)
    first = tree(ledger)
    write_import(ledger, checkout, COMMIT)
    assert tree(ledger) == first


# --- regenerable byte for byte (D56, R19.5) -------------------------------------------------------


def test_the_import_is_byte_reproducible(ledger, checkout):
    write_import(ledger, checkout, COMMIT)
    first = tree(ledger)
    write_import(ledger, checkout, COMMIT)
    assert tree(ledger) == first


def test_the_output_does_not_depend_on_the_order_of_the_dump(tmp_path):
    outputs = []
    for seed in (None, 1, 2, 3):
        root = tmp_path / f"checkout{seed}"
        (root / "assets/db_src").mkdir(parents=True)
        (root / "assets/db_src/swiftremote.sql").write_text(sql_text(shuffle=seed), encoding="utf-8")
        ledger = tmp_path / f"ledger{seed}"
        ledger.mkdir()
        write_import(ledger, root, COMMIT)
        outputs.append(tree(ledger))
    assert outputs[0] == outputs[1] == outputs[2] == outputs[3]


def test_a_rerun_removes_what_the_database_no_longer_holds(ledger, checkout):
    write_import(ledger, checkout, COMMIT)
    assert (ledger / IMPORT_ROOT / "SAM" / "7-Samsung36.json").exists()
    sql = checkout / "assets" / "db_src" / "swiftremote.sql"
    remotes = {k: v for k, v in REMOTES.items() if k != 7}
    sql.write_text(sql_text(remotes), encoding="utf-8")
    write_import(ledger, checkout, COMMIT)
    assert not (ledger / IMPORT_ROOT / "SAM").exists()


def test_the_authored_files_in_the_import_root_survive(ledger, checkout):
    (ledger / IMPORT_ROOT).mkdir(parents=True)
    (ledger / IMPORT_ROOT / "LICENSE").write_text("GPL")
    (ledger / IMPORT_ROOT / "README.md").write_text("hand written")
    write_import(ledger, checkout, COMMIT)
    assert (ledger / IMPORT_ROOT / "LICENSE").read_text() == "GPL"
    assert (ledger / IMPORT_ROOT / "README.md").read_text() == "hand written"


def test_the_written_files_are_what_rl_fmt_would_write(imported):
    from remote_ledger.fmt import format_document
    ledger, _ = imported
    for path in corpus_files(ledger):
        assert path.read_text(encoding="utf-8") == format_document(path)


def test_the_files_validate_and_cross_check_and_compile(imported):
    ledger, _ = imported
    paths = corpus_files(ledger)
    assert len(paths) == 9
    for path in paths:
        assert [str(p) for p in validate_file(path)] == []
        remote = load_remote(path)
        problems, _ = check_remote(remote)
        assert problems == []
        for key in remote.keys:
            assert remote.compile_group(key, "primary").startswith("0000 ")


# --- the command ---------------------------------------------------------------------------------


def _commit(checkout: Path) -> str:
    git = ["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_the_cli_dispatches_to_irblaster_and_pins_the_commit(ledger, checkout, monkeypatch, capsys):
    head = _commit(checkout)
    monkeypatch.chdir(ledger)
    assert main(["import", "irblaster", str(checkout)]) == 0
    out = capsys.readouterr().out
    assert "remotes/irblaster/: 9 remotes, 20 keys" in out and REPORT in out
    assert f"@ `{head}`" in (ledger / IMPORT_ROOT / REPORT).read_text()
    form, = load(ledger, "ACME/1-NEC1.json")["keys"]["KEY_POWER"]["forms"]
    assert form["source"].startswith(f"irblaster-db@{head[:7]} ")


def test_the_cli_refuses_a_checkout_at_another_commit(ledger, checkout, monkeypatch, capsys):
    _commit(checkout)
    monkeypatch.chdir(ledger)
    assert main(["import", "irblaster", str(checkout), "--commit", "deadbee"]) == 1
    assert "not the requested deadbee" in capsys.readouterr().err
    assert not (ledger / IMPORT_ROOT).exists()


def test_the_cli_refuses_a_dump_that_differs_from_the_commit(ledger, checkout, monkeypatch, capsys):
    _commit(checkout)
    sql = checkout / "assets" / "db_src" / "swiftremote.sql"
    sql.write_text(sql.read_text() + "-- edited\n")
    monkeypatch.chdir(ledger)
    assert main(["import", "irblaster", str(checkout)]) == 1
    assert "differs from the commit" in capsys.readouterr().err
    assert not (ledger / IMPORT_ROOT).exists()


def test_the_cli_says_when_the_checkout_has_no_dump(ledger, tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    subprocess.run(["git", "-C", str(empty), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    monkeypatch.chdir(ledger)
    assert main(["import", "irblaster", str(empty)]) == 1
    assert "swiftremote.sql" in capsys.readouterr().err
