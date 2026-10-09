"""``rl import jp1`` (DESIGN D118 to D122): the reader of an upgrade, the decoding rules, the
master index, and the import over a small checkout and over the committed tree."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.jp1 import importer as jp
from remote_ledger.jp1 import rmdu
from remote_ledger.remote import load_remote
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "87b0ac05bc3b73eb5d392b9f468ae24a875ff83e"


def upgrade(name: str, parms: str, *functions: tuple[str | None, str | None], fixed: str = "") -> rmdu.Upgrade:
    lines = [f"Protocol.name={name}", f"ProtocolParms={parms}"]
    if fixed:
        lines.append(f"FixedData={fixed}")
    for i, (fname, hx) in enumerate(functions):
        if fname is not None:
            lines.append(f"Function.{i}.name={fname}")
        if hx is not None:
            lines.append(f"Function.{i}.hex={hx}")
    return rmdu.parse("\r\n".join(lines) + "\r\n")


def signal(up: rmdu.Upgrade, n: int = 0):
    return rmdu.signal_of(up, up.functions[n])


# --- the reader --------------------------------------------------------------------------------


def test_an_upgrade_is_its_fields_and_functions_and_a_wrapped_note_is_ignored():
    text = ("Description=Maximum XO-506C\r\nProtocol.name=NEC1\r\nProtocolParms=6 null null\r\n"
            "Notes=a long note that\r\nwraps onto a line with no key\r\n"
            "Function.1.name=num_0\r\nFunction.1.hex=FF\r\nFunction.0.name=Power\r\nFunction.0.hex=AF\r\n"
            "Function.0.notes=hold it\r\nExtFunction.0.name=TV AV\r\nExtFunction.0.hex=7F\r\n"
            "ExtFunction.1.name=TV Component\r\n")
    up = rmdu.parse(text)
    assert up.protocol_name == "NEC1" and up.parms == ["6", "null", "null"]
    assert up.fields["Description"] == "Maximum XO-506C" and up.ext_functions == 2
    assert [(f.index, f.name, f.hex, f.notes) for f in up.functions] == [
        (0, "Power", "AF", "hold it"), (1, "num_0", "FF", None)]


def test_windows_1252_is_read_where_the_file_is_not_utf8():
    assert rmdu.decode_text("Description=Café".encode("utf-8")) == "Description=Café"
    assert rmdu.decode_text("Description=Caf\xe9".encode("cp1252")) == "Description=Café"


@pytest.mark.parametrize("hx,expected", [("AF", 0xAF), ("0f", 0x0F), ("FF BF", None), ("", None),
                                         ("G1", None), (None, None), ("A", None)])
def test_an_obc_is_exactly_one_hex_byte(hx, expected):
    assert rmdu.obc_byte(hx) == expected


def test_a_device_parameter_is_a_small_number_or_nothing():
    parms = "7 null 3000 x 12".split()
    assert [rmdu.parm(parms, i) for i in range(6)] == [7, None, None, None, 12, None]


# --- the decoding rules, anchored to codes the ledger holds from other sources ---------------------


def test_nec_is_the_complemented_reversed_obc_and_a_null_sub_device_is_the_complement():
    # the Samsung TV of the ledger's authored BN59-01199F: NECx2 7,7 and Power 2, Source 1, key 1 is 4
    up = upgrade("NECx2", "7 7 null", ("POWER", "BF"), ("SOURCE", "7F"), ("1", "DF"))
    assert [signal(up, i) for i in range(3)] == [
        rmdu.Signal("NECx2", 7, 7, 0x02), rmdu.Signal("NECx2", 7, 7, 0x01), rmdu.Signal("NECx2", 7, 7, 0x04)]
    # NEC1 with one device number: the sub device is its complement, as the Topping RC-15A's pair is
    one = upgrade("NEC1", "6 null null", ("Power", "AF"))
    assert signal(one) == rmdu.Signal("NEC1", 6, 0xF9, 0x0A)
    assert signal(upgrade("NEC1", "136 119 null", ("Power", "E7"))) == rmdu.Signal("NEC1", 0x88, 0x77, 0x18)


def test_no_repeats_and_nec2_are_the_ledgers_nec1_and_nec2_and_nec_x_needs_a_sub_device():
    assert signal(upgrade("NEC1 (No Repeats)", "204 2", ("0", "FF"))).protocol == "NEC1"
    assert signal(upgrade("NEC2", "128 null null", ("Power", "A7"))).protocol == "NEC2"
    assert signal(upgrade("NECx1", "5 null null", ("0", "77"))) == "the sub device parameter is missing"


def test_a_null_device_number_is_read_from_the_second_byte_of_fixed_data():
    # `null 223` with FixedData 20 FF 04 is the common NEC address 0x00DF
    up = upgrade("NEC1", "null 223 null", ("Power", "AF"), fixed="20 FF 04")
    assert signal(up) == rmdu.Signal("NEC1", 0, 223, 0x0A)
    assert signal(upgrade("NEC1", "null 223 null", ("Power", "AF"))) == "the device parameter is missing"


def test_sony_12_15_takes_the_device_from_the_lowest_bit_and_the_frame_from_its_size():
    # the Sony TV of the ledger's hifi-remote import: device 1 is 12-bit, device 151 15-bit; key 1 is
    # command 0, key 2 command 1, key 3 command 2 on both
    up = upgrade("Sony 12/15", "1 0 151 0", ("1", "00"), ("2", "80"), ("3", "40"), ("1b", "01"), ("2b", "81"))
    assert [signal(up, i) for i in range(5)] == [
        rmdu.Signal("Sony12", 1, None, 0), rmdu.Signal("Sony12", 1, None, 1), rmdu.Signal("Sony12", 1, None, 2),
        rmdu.Signal("Sony15", 151, None, 0), rmdu.Signal("Sony15", 151, None, 1)]
    assert signal(upgrade("Sony 12/15", "17 0 null 0", ("x", "01"))) == "the device the function selects is not set"


def test_sony_20_is_device_sub_device_and_the_reversed_command():
    # Sony Blu-ray 26.226: Power is 21 (OBC A8 reversed), 1 is 0, 2 is 1
    up = upgrade("Sony20", "26 226 null", ("Power", "A8"), ("1", "00"), ("2", "80"), ("bad", "01"))
    assert [signal(up, i) for i in range(3)] == [
        rmdu.Signal("Sony20", 26, 226, 0x15), rmdu.Signal("Sony20", 26, 226, 0), rmdu.Signal("Sony20", 26, 226, 1)]
    assert signal(up, 3) == "the command does not fit Sony's seven bits"


def test_rc5_is_a_device_slot_a_complemented_command_and_a_flag_for_the_seventh_bit():
    """``Rc5Translator``: the OBC's low two bits pick one of three device slots, its top six are the command
    complemented, and the slot's OBC>63 flag adds the seventh bit. Arcam AVR100's parameters, `16 0 16 1 17 0`:
    the second slot is device 16 again with the flag, which is how it sends 16-124 and 16-123."""
    up = upgrade("RC-5", "16 0 16 1 17 0", ("Power ON/OFF", "CC"), ("Power OFF", "0D"), ("Power ON", "11"),
                 ("Tuner", "F2"), ("a slot past the third is the first", "03"))
    assert [signal(up, i) for i in range(5)] == [
        rmdu.Signal("RC5", 16, None, 12), rmdu.Signal("RC5", 16, None, 124), rmdu.Signal("RC5", 16, None, 123),
        rmdu.Signal("RC5", 17, None, 3), rmdu.Signal("RC5", 16, None, 63)]


def test_a_slot_with_no_device_is_the_nearest_earlier_one_and_no_device_at_all_is_said_so():
    # slot 2 (OBC low bits 10) has no device: it is slot 1, device 16, whose flag is 1: 63 - 0x30 = 15, with 64
    assert signal(upgrade("RC-5", "8 0 16 1 null 0", ("x", "C2")), 0) == rmdu.Signal("RC5", 16, None, 63 - 0x30 + 64)
    # slot 1 has no device and slot 0 has: slot 0, no flag
    assert signal(upgrade("RC-5", "8 0 null 0 null 0", ("x", "C1")), 0) == rmdu.Signal("RC5", 8, None, 63 - 0x30)
    assert signal(upgrade("RC-5", "null 0 16 0 null 0", ("x", "C0")), 0) == "the device the function selects is not set"
    assert signal(upgrade("RC-5", "40 0 null 0 null 0", ("x", "C0")), 0) == "the RC-5 device is not five bits"


def test_an_executor_not_read_and_a_function_with_no_single_byte_are_said_so():
    assert "not read by this import" in signal(upgrade("MCE", "28 0 null", ("power", "C4")))
    assert signal(upgrade("NEC1", "6 null null", ("Power", "FF BF"))) == "the function has no single OBC byte"
    assert signal(upgrade("NEC1", "6 null null", ("Guide", None))) == "the function has no single OBC byte"


def test_the_decoded_samsung_agrees_with_the_authored_bn59_and_the_sony_with_the_authored_sony():
    """The import is checked against the ledger's own authored data, not only against a rule."""
    bn59 = load_remote(ROOT / "remotes" / "samsung" / "BN59-01199F.json")
    power = next(f for f in bn59.keys["KEY_POWER"])
    assert (power.device, power.subdevice, power.function) == (7, 7, 0x02)
    b118 = load_remote(ROOT / "remotes" / "sony" / "RMT-B118P.json")
    play = next(f for f in b118.keys["KEY_PLAY"])
    assert (play.device, play.subdevice, play.function) == (26, 226, 0x1A)
    assert signal(upgrade("Sony20", "26 226 null", ("Play", "58"))) == rmdu.Signal("Sony20", 26, 226, 0x1A)


# --- the names ----------------------------------------------------------------------------------


def test_names_collide_into_function_numbers_and_unnamed_functions_have_one_too():
    names = jp.key_names([(0, "Power"), (1, "vol +"), (2, "vol -"), (3, "Up"), (4, "UP"), (5, None), (6, "??")])
    assert names[0] == "KEY_POWER" and names[1] == "KEY_VOL_PLUS" and names[2] == "KEY_VOL_MINUS"
    assert names[3] == "KEY_UP_3" and names[4] == "KEY_UP_4"
    assert names[5] == "KEY_FUNCTION_5" and names[6] == "KEY_UNLABELED"


@pytest.mark.parametrize("brand,description,model", [
    ("Maximum", "Maximum XO-506C", "XO-506C"), ("Daewoo", "Daewoo AC", "AC"), ("Sony", "Sony - kdl46", "kdl46"),
    ("Samsung", "TV SAMSUNG - UN40H5203AFXZC", "TV SAMSUNG - UN40H5203AFXZC"), ("Acme", "Acme", "Acme")])
def test_the_model_is_the_description_without_the_brand_it_begins_with(brand, description, model):
    assert jp.split_model(brand, description) == model


# --- the master index and a small checkout, imported ------------------------------------------------

ROWS = [
    ("TV", "SONY", "Sony TV", "Sony TV.rmdu", "rmdu", "00 CA", "Sony 12/15"),
    ("TV", "Sony", "Sony Bravia", "Sony Bravia.rmdu", "rmdu", "00 CA", "Sony 12/15"),
    ("Audio", "Sony", "Sony Amp", "Sony_Amp.rmdu", "rmdu", "00 DE", "Sony20"),
    ("TV", "Samsung", "Samsung UN40", "Samsung UN40.rmdu", "rmdu", "00 5A", "NECx2"),
    ("DVD", "Samsung", "Samsung UN40", "Samsung UN40.rmdu", "rmdu", "00 5A", "NECx2"),
    ("Misc", "Acme", "Acme Box", "Acme Box.rmdu", "rmdu", "00 5A", "NEC1"),
]
FILES = {
    "TV/Sony TV.rmdu": ("Description=Sony TV\r\nProtocol.name=Sony 12/15\r\nProtocolParms=1 0 151 0\r\n"
                        "Function.0.name=1\r\nFunction.0.hex=00\r\nFunction.1.name=2\r\nFunction.1.hex=81\r\n"),
    "Audio/Sony Amp.rmdu": ("Description=Sony Amp\r\nProtocol.name=Sony20\r\nProtocolParms=26 12 null\r\n"
                            "Function.0.name=Power\r\nFunction.0.hex=A8\r\nFunction.0.notes=toggles\r\n"),
    "TV/Samsung UN40.rmdu": ("Description=Samsung UN40\r\nProtocol.name=NECx2\r\nProtocolParms=7 7 null\r\n"
                             "Function.0.name=POWER\r\nFunction.0.hex=BF\r\nFunction.1.name=Guide\r\n"),
    "DVD/Samsung UN40.rmdu": ("Description=Samsung UN40 DVD\r\nProtocol.name=NECx2\r\nProtocolParms=7 7 null\r\n"
                              "Function.0.name=POWER\r\nFunction.0.hex=BF\r\n"),
    "Misc/Acme Box.rmdu": ("Description=Learned Signal Upgrade\r\nProtocol.name=NEC1\r\nProtocolParms=6 null null\r\n"
                           "Function.0.name=Power\r\nFunction.0.hex=AF\r\n"),
    "Misc/Nobody Box.rmdu": ("Description=Nobody Box\r\nProtocol.name=NEC1\r\nProtocolParms=6 null null\r\n"
                             "Function.0.name=Power\r\nFunction.0.hex=AF\r\n"),
    "Audio/Mce Box.rmdu": ("Description=Acme MCE\r\nProtocol.name=MCE\r\nProtocolParms=10 0\r\n"
                           "Function.0.name=power\r\nFunction.0.hex=CC\r\n"),
    "TV/Keymap Master.txt": "not read\r\n",
    "Audio/Empty.rmdu": "Description=Acme Empty\r\nProtocol.name=NEC1\r\nProtocolParms=6 null null\r\n",
}


def make_checkout(tmp_path: Path, commit: str = COMMIT, files: dict[str, str] | None = None):
    repo = tmp_path / "repo"
    for rel, text in (files or FILES).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    (repo / jp.INPUT).write_bytes(b"workbook")
    root = tmp_path / "ledger"
    master = root / jp.MASTER_DIR
    master.mkdir(parents=True)
    with (master / jp.MASTER_CSV).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Category", "Brand", "File Description (from web)", "File Name", "Type", "PID", "Protocol"])
        writer.writerows(ROWS)
    (master / jp.MASTER_META).write_text(json.dumps(
        {"commit": commit, "xlsSha256": hashlib.sha256(b"workbook").hexdigest()}), encoding="utf-8")
    (root / "remotes").mkdir()
    return root, repo


@pytest.fixture()
def small(tmp_path):
    root, repo = make_checkout(tmp_path)
    return jp.import_tree(root, repo, COMMIT, {})


def test_a_checkout_becomes_one_file_per_protocol_and_every_function_a_key(small):
    docs, report = small
    assert sorted(docs) == [
        "remotes/jp1/Acme/Misc-Acme_Box.json", "remotes/jp1/Samsung/DVD-Samsung_UN40.json",
        "remotes/jp1/Samsung/TV-Samsung_UN40.json", "remotes/jp1/Sony/Audio-Sony_Amp.json",
        "remotes/jp1/Sony/TV-Sony_TV-sony12.json", "remotes/jp1/Sony/TV-Sony_TV-sony15.json"]
    sony12 = docs["remotes/jp1/Sony/TV-Sony_TV-sony12.json"]
    assert sony12["model"] == "TV [Sony12]" and sony12["manufacturer"] == "Sony"
    assert sony12["protocol"] == {"name": "Sony12", "carrierHz": 40000, "minSends": 3}
    assert docs["remotes/jp1/Sony/TV-Sony_TV-sony15.json"]["keys"]["KEY_2"]["forms"][0]["function"] == 1
    samsung = docs["remotes/jp1/Samsung/TV-Samsung_UN40.json"]
    assert samsung["protocol"]["name"] == "NECx2" and samsung["protocol"]["minSends"] == 1
    assert set(samsung["keys"]) == {"KEY_POWER"}      # `Guide` has no OBC byte
    assert (report.files["upgrades"], report.remotes["imported"]) == (8, 6)
    assert report.unread == {"MCE": 1} and report.files["not read: KeymapMaster .txt"] == 1
    assert dict(report.protocols) == {"Sony12": 1, "Sony15": 1, "Sony20": 1, "NECx2": 2, "NEC1": 1}


def test_the_master_index_gives_brand_and_category_and_a_prefix_gives_the_rest(small):
    docs, report = small
    # `SONY` and `Sony` are one brand, spelled as the index mostly spells it
    assert {Path(p).parts[2] for p in docs} == {"Acme", "Samsung", "Sony"}
    assert docs["remotes/jp1/Acme/Misc-Acme_Box.json"]["model"] == "Box"      # the generic description is not used
    assert docs["remotes/jp1/Acme/Misc-Acme_Box.json"]["controls"] == ["Acme Box"]
    # two rows share a file name: the category the repository files it under chooses
    assert docs["remotes/jp1/Samsung/DVD-Samsung_UN40.json"]["controls"] == ["Samsung UN40 DVD"]
    # `Nobody Box` has neither a row nor a brand, `Empty` no function
    assert report.files["skipped: no brand"] == 1 and report.files["skipped: no functions"] == 1
    assert dict(report.brands_from) == {"the master index": 5}


def test_a_citation_names_the_commit_the_file_the_function_the_obc_and_the_reading(small):
    docs, _ = small
    form = docs["remotes/jp1/Sony/Audio-Sony_Amp.json"]["keys"]["KEY_POWER"]["forms"][0]
    assert form["source"] == (
        "jp1-device-upgrades@87b0ac0 Audio/Sony Amp.rmdu function 0 'Power' (OBC A8; Sony20, parms 26 12 null): "
        "Sony20 device 26 subdevice 12 function 21; the upgrade's note: 'toggles'")
    assert form["confidence"] == "plausible" and form["type"] == "irp"
    assert (form["device"], form["subdevice"], form["function"]) == (26, 12, 21)


def test_everything_it_writes_loads_compiles_and_a_second_run_changes_nothing(tmp_path):
    root, repo = make_checkout(tmp_path)
    report = jp.write_import(root, repo, COMMIT)
    assert report.remotes["imported"] == 6
    for path in (root / jp.IMPORT_ROOT).rglob("*.json"):
        remote = load_remote(path)
        for key in remote.keys:
            remote.compile_group(key, "primary")
    before = {p: p.read_bytes() for p in (root / jp.IMPORT_ROOT).rglob("*") if p.is_file()}
    jp.write_import(root, repo, COMMIT)
    assert before == {p: p.read_bytes() for p in (root / jp.IMPORT_ROOT).rglob("*") if p.is_file()}
    assert (root / jp.IMPORT_ROOT / jp.REPORT).read_text().startswith("# JP1 device upgrades import report")


def test_an_authored_remote_of_the_same_name_wins(tmp_path):
    root, repo = make_checkout(tmp_path)
    docs, report = jp.import_tree(root, repo, COMMIT, {("acme", "box"): "remotes/acme/box.json"})
    assert "remotes/jp1/Acme/Misc-Acme_Box.json" not in docs
    assert report.collisions == [("Misc/Acme Box.rmdu", "remotes/jp1/Acme/Misc-Acme_Box.json")]


def test_two_files_whose_names_slug_alike_are_numbered_in_path_order(tmp_path):
    files = {"TV/Sony TV.rmdu": FILES["TV/Sony TV.rmdu"], "TV/Sony_TV.rmdu": FILES["TV/Sony TV.rmdu"]}
    root, repo = make_checkout(tmp_path, files=files)
    docs, _ = jp.import_tree(root, repo, COMMIT, {})
    assert {p for p in docs if "sony12" in p} == {"remotes/jp1/Sony/TV-Sony_TV-sony12.json",
                                                   "remotes/jp1/Sony/TV-Sony_TV-sony12-2.json"}
    assert len({d["model"] for d in docs.values()}) == len(docs)


def test_the_master_index_must_be_the_one_of_this_commit_and_workbook(tmp_path):
    root, repo = make_checkout(tmp_path)
    with pytest.raises(ValidationError, match="was made from commit"):
        jp.load_master(root, repo, "1234567" + "0" * 33)
    (repo / jp.INPUT).write_bytes(b"another workbook")
    with pytest.raises(ValidationError, match="not the workbook"):
        jp.load_master(root, repo, COMMIT)
    with pytest.raises(ValidationError, match="cannot read the master index"):
        jp.load_master(tmp_path / "nowhere", repo, COMMIT)


def test_brand_spellings_and_the_order_of_brands_do_not_depend_on_the_process(tmp_path):
    root, repo = make_checkout(tmp_path)
    master = jp.load_master(root, repo, COMMIT)
    assert master.brand("sony") == "Sony" and master.brand("SONY") == "Sony" and master.brand("Unknown") == "Unknown"
    assert master.brands == sorted(master.brands, key=lambda b: (-len(b), b))
    assert jp.brand_by_prefix(master, "sony tv model") == "Sony" and jp.brand_by_prefix(master, "sonyx") is None


# --- the committed tree ---------------------------------------------------------------------------


def test_the_committed_jp1_import_keeps_r19():
    """SPEC R19 conditions 2 and 3 over every form: README, report and the index beside it, one
    Plausible `irp` form a key, a citation of the fixed shape that names the commit the index was made
    from, and a reading that is the form's own. A cheap scan of the JSON, not a load."""
    meta = json.loads((ROOT / jp.MASTER_DIR / jp.MASTER_META).read_text(encoding="utf-8"))
    assert (ROOT / jp.IMPORT_ROOT / "README.md").is_file() and (ROOT / jp.IMPORT_ROOT / jp.REPORT).is_file()
    assert f"@ `{meta['commit']}`" in (ROOT / jp.IMPORT_ROOT / jp.REPORT).read_text(encoding="utf-8")
    shape = re.compile(
        rf"^jp1-device-upgrades@{meta['commit'][:7]} \S.* function (\d+) '.*' \(OBC ([0-9A-F]{{2}}); [^;]+, parms [^)]*\): "
        r"(NEC1|NEC2|NECx1|NECx2|Sony12|Sony15|Sony20|RC5) device (\d+)(?: subdevice (\d+))? function (\d+)", re.S)

    def num(value):
        return int(value, 0) if isinstance(value, str) else value

    files = sorted((ROOT / jp.IMPORT_ROOT).rglob("*.json"))
    assert len(files) > 1_500
    bad = []
    for path in files:
        doc = load(path)
        for key, spec in doc["keys"].items():
            forms = spec["forms"]
            if len(forms) != 1:
                bad.append(f"{path.relative_to(ROOT)}:{key}")
            for form in forms:
                m = shape.match(form.get("source", ""))
                ok = (form["type"] == "irp" and form["confidence"] == "plausible" and "verifiedBy" not in form
                      and m is not None and m[3] == doc["protocol"]["name"]
                      and (int(m[4]), None if m[5] is None else int(m[5]), int(m[6]))
                      == (num(form["device"]), None if "subdevice" not in form else num(form["subdevice"]),
                          num(form["function"])))
                if not ok:
                    bad.append(f"{path.relative_to(ROOT)}:{key}")
    assert bad == []


def test_the_committed_samsung_upgrade_agrees_with_the_authored_bn59_on_every_key_they_share():
    """Two independent sources for one TV family: a forum member's JP1 upgrade for the UN40H5203AFXZC
    and the ledger's authored BN59-01199F. Where both name a key, they send the same signal."""
    path = next((ROOT / jp.IMPORT_ROOT / "Samsung").glob("TV-TV_SAMSUNG_-_UN40H5203AFXZC*.json"))
    upgrade_keys = load_remote(path).keys
    authored = load_remote(ROOT / "remotes" / "samsung" / "BN59-01199F.json").keys
    shared = set(upgrade_keys) & set(authored)
    assert {"KEY_POWER"} <= shared
    for key in shared:
        a, b = authored[key][0], upgrade_keys[key][0]
        assert (a.device, a.subdevice, a.function) == (b.device, b.subdevice, b.function), key


def test_the_committed_arcam_upgrades_send_the_codes_arcam_publishes():
    """An independent check of the RC-5 rule, from another source. Arcam's own table of its amplifiers'
    IR codes (arcam.co.uk/ugc/tor/a18/IR Codes/amps_rc.pdf, system code 16) lists PHONO select 16-1, AV select
    16-2, Tuner select 16-3, tape select 16-5, CD select 16-7, Mute 16-13, Volume up 16-16, Volume down 16-17,
    Power-on 16-123 and Power-off 16-124. Two forum upgrades of Arcam equipment, decoded through the RC-5
    executor's translator, send exactly those (the AVR100's `Power OFF` and `Power ON` through the OBC>63
    flag of its second device slot)."""
    def codes(name: str) -> dict[str, tuple[int, int]]:
        doc = load(ROOT / jp.IMPORT_ROOT / "Arcam" / name)
        assert doc["protocol"]["name"] == "RC5"
        return {key: (int(spec["forms"][0]["device"], 16), int(spec["forms"][0]["function"], 16))
                for key, spec in doc["keys"].items()}

    amp = codes("Audio-Arcam_Amp-Tuner-CD.json")
    assert {k: amp[k] for k in ("KEY_PHONO", "KEY_AV", "KEY_TUNER", "KEY_TAPE", "KEY_CD", "KEY_MUTE", "KEY_VOL_PLUS", "KEY_VOL_MINUS")} == {
        "KEY_PHONO": (16, 1), "KEY_AV": (16, 2), "KEY_TUNER": (16, 3), "KEY_TAPE": (16, 5), "KEY_CD": (16, 7),
        "KEY_MUTE": (16, 13), "KEY_VOL_PLUS": (16, 16), "KEY_VOL_MINUS": (16, 17)}
    receiver = codes("Audio-Arcam_Receiver_AVR100.json")
    assert (receiver["KEY_POWER_ON"], receiver["KEY_POWER_OFF"]) == ((16, 123), (16, 124))
