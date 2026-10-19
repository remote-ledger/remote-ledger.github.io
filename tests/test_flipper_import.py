"""The Flipper-IRDB import (DESIGN section 96, D353 to D356): the ``.ir`` format, the mapping of Flipper's protocols to the ledger's, the pinned snapshot
of the CC0 files, and the import over a small snapshot and over the committed one."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.flipper import importer as fi
from remote_ledger.flipper import ir, mapping
from remote_ledger.forms import select
from remote_ledger.import_common import authored_names
from remote_ledger.remote import remote_from_doc
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / fi.SNAPSHOT
COMMIT = "d126fb1b6f1e114c52b4a8c19839ea65e3a9c24d"
LICENCE_COMMIT = "2319685f2cbf0cd3f809609622cade14d24fb819"


# --- the format -----------------------------------------------------------------------------------

SAMPLE = """# written by someone
Filetype: IR signals file
Version: 1
#
# A test device
#
name: Power
type: parsed
protocol: NEC
address: 04 00 00 00
command: 08 00 00 00
#
name: Vol_up
type: parsed
protocol: SIRC20
address: 10 04 00 00
command: 17 00 00 00
#
name: Odd
type: parsed
protocol: SIRC15
address: 9A 0E 00 00
command: 2F 00 00 00
#
name: On
type: raw
frequency: 38000
duty_cycle: 0.330000
data: 4391 4402 552 1599 553 524 552
"""


def test_a_file_is_a_header_comments_and_entries_of_parsed_bytes_or_raw_durations():
    parsed = ir.parse(SAMPLE)
    assert parsed.comments == ("written by someone", "A test device")
    assert [(e.name, e.kind) for e in parsed.entries] == [("Power", "parsed"), ("Vol_up", "parsed"), ("Odd", "parsed"), ("On", "raw")]
    power, _, _, on = parsed.entries
    assert (power.protocol, power.address, power.command, power.line) == ("NEC", (4, 0, 0, 0), (8, 0, 0, 0), 7)
    assert (on.frequency, on.duty_cycle, on.data) == (38000, 0.33, (4391, 4402, 552, 1599, 553, 524, 552))


def test_another_firmwares_filetype_is_the_same_layout_and_other_headers_are_refused():
    assert ir.parse(SAMPLE.replace("IR signals file", "Bruce IR File")).entries
    for bad, why in ((SAMPLE.replace("Version: 1", "Version: 2"), "the header"), (SAMPLE.replace("IR signals file", "Something"), "the header"),
                     (SAMPLE.replace("04 00 00 00", "04 00 00"), "four hexadecimal bytes"), (SAMPLE.replace("type: raw", "type: other"), "has type"),
                     (SAMPLE.replace("protocol: NEC\n", ""), "has no protocol"), (SAMPLE.replace("data: 4391", "data: x4391"), "raw entry"),
                     (SAMPLE.replace("Filetype: IR signals file\nVersion: 1\n", "Filetype: IR signals file\nVersion: 1\nnonsense\n"), "not 'key: value'")):
        with pytest.raises(ir.FlipperParseError, match=why):
            ir.parse(bad)


# --- the mapping, checked by decoding what the ledger compiles with the firmware's own rules -----------------------------


def _signal(entry: ir.Entry):
    mapped = mapping.signal_of(entry)
    assert not isinstance(mapped, str), mapped
    form = {"id": "primary.irp", "type": "irp", "device": mapped.device, "function": mapped.function, "confidence": "plausible", "source": "t"}
    if mapped.subdevice is not None:
        form["subdevice"] = mapped.subdevice
    remote = remote_from_doc({"manufacturer": "t", "model": "t", "protocol": fi.block(mapped.protocol), "keys": {"K": {"forms": [form]}}}, Path("t.json"))
    return mapped, remote.render(select(remote.groups("K")["primary"]))


def _bytes(bits):
    return [sum(bit << i for i, bit in enumerate(bits[k:k + 8])) for k in range(0, len(bits), 8)]


def _wire(signal, bits: int) -> list[int]:
    """The bytes of a pulse-distance frame (a header of two durations, then a mark and a space for each bit, first bit least significant)."""
    d = list(signal.intro or signal.repeat)
    return _bytes([1 if d[3 + 2 * i] > 1.5 * d[2 + 2 * i] else 0 for i in range(bits)])


def _sirc(signal, bits: int) -> int:
    d = list(signal.repeat or signal.intro)
    return sum((1 if d[2 + 2 * i] > 900 else 0) << i for i in range(bits))


def _halves(signal, unit: int) -> list[int]:
    out: list[int] = []
    for i, d in enumerate(signal.repeat or signal.intro):
        out += [1 if i % 2 == 0 else 0] * round(d / unit)
    return out


def _flipper(protocol: str, address: tuple, command: tuple) -> tuple:
    """Flipper's own reading (``infrared_decoder_*.c``) of what the ledger's compiled signal sends, as ``(protocol, address, command)``."""
    entry = ir.Entry("t", "parsed", protocol=protocol, address=address, command=command)
    mapped, signal = _signal(entry)
    if mapped.protocol in ("NEC1", "NEC1-f16"):
        a0, a1, c0, c1 = _wire(signal, 32)
        if c0 == 0xFF - c1 and a0 == 0xFF - a1:
            return "NEC", (a0, 0, 0, 0), (c0, 0, 0, 0)
        return "NECext", (a0, a1, 0, 0), (c0, c1, 0, 0)
    if mapped.protocol == "NECx2":
        a0, a1, c0, c1 = _wire(signal, 32)
        assert a0 == a1 and c1 == 0xFF - c0
        return "Samsung32", (a0, 0, 0, 0), (c0, 0, 0, 0)
    if mapped.protocol == "Pioneer-2Part":
        a0, a1, c0, c1 = _wire(signal, 32)
        assert a1 == 0xFF - a0 and c1 == 0xFF - c0
        return "Pioneer", (a0, 0, 0, 0), (c0, 0, 0, 0)
    if mapped.protocol in ("Panasonic", "JVC-48", "Denon-K"):
        w = _wire(signal, 48)
        vendor, parity = w[1] << 8 | w[0], w[5]
        assert parity == w[2] ^ w[3] ^ w[4] and w[2] & 0xF == ((w[0] ^ w[1]) & 0xF) ^ ((w[0] ^ w[1]) >> 4)
        address_word = (w[4] >> 6) << 24 | vendor << 8 | (w[2] >> 4) << 4 | (w[3] & 0xF)
        data = (w[3] >> 4) | (w[4] & 0x3F) << 4
        return "Kaseikyo", tuple(address_word.to_bytes(4, "little")), tuple(data.to_bytes(4, "little"))
    if mapped.protocol in ("Sony12", "Sony15", "Sony20"):
        bits = {"Sony12": 12, "Sony15": 15, "Sony20": 20}[mapped.protocol]
        value = _sirc(signal, bits)
        name = {"Sony12": "SIRC", "Sony15": "SIRC15", "Sony20": "SIRC20"}[mapped.protocol]
        return name, tuple((value >> 7).to_bytes(4, "little")), (value & 0x7F, 0, 0, 0)
    if mapped.protocol == "RC5":
        h = [0] + _halves(signal, 889)
        bit = [1 if (h[2 * i], h[2 * i + 1]) == (0, 1) else 0 for i in range(14)]
        assert bit[0] == 1
        address = int("".join(map(str, bit[3:8])), 2)
        command = int("".join(map(str, bit[8:14])), 2)
        return ("RC5" if bit[1] else "RC5X"), (address, 0, 0, 0), (command, 0, 0, 0)
    if mapped.protocol == "RC6":
        h = _halves(signal, 444)
        assert h[:8] == [1] * 6 + [0, 0]
        pos = 8
        bits: list[int] = []
        for width in (1, 1, 1, 1, 2) + (1,) * 16:    # the start bit, three mode bits, the toggle (double width), the address and the command
            bits.append(1 if (h[pos], h[pos + width]) == (1, 0) else 0)
            pos += 2 * width
        assert bits[0] == 1 and bits[1:4] == [0, 0, 0]
        return "RC6", (int("".join(map(str, bits[5:13])), 2), 0, 0, 0), (int("".join(map(str, bits[13:21])), 2), 0, 0, 0)
    raise AssertionError(mapped.protocol)


def _vendor_parity(vendor: int) -> int:
    x = (vendor & 0xFF) ^ (vendor >> 8)
    return (x & 0xF) ^ (x >> 4)


MESSAGES = [
    ("NEC", (0x04, 0, 0, 0), (0x08, 0, 0, 0), "NEC1", 0x04, 0xFB, 0x08),
    ("NEC", (0x00, 0, 0, 0), (0xFF, 0, 0, 0), "NEC1", 0x00, 0xFF, 0xFF),
    ("NECext", (0x87, 0x7C, 0, 0), (0x4D, 0xB2, 0, 0), "NEC1", 0x87, 0x7C, 0x4D),
    ("NECext", (0x84, 0x79, 0, 0), (0x12, 0xED, 0, 0), "NEC1", 0x84, 0x79, 0x12),
    ("NECext", (0x11, 0x22, 0, 0), (0x33, 0x44, 0, 0), "NEC1-f16", 0x11, 0x22, 0x3344),
    ("Samsung32", (0x07, 0, 0, 0), (0x02, 0, 0, 0), "NECx2", 0x07, 0x07, 0x02),
    ("SIRC", (0x01, 0, 0, 0), (0x15, 0, 0, 0), "Sony12", 0x01, None, 0x15),
    ("SIRC", (0x1F, 0, 0, 0), (0x7F, 0, 0, 0), "Sony12", 0x1F, None, 0x7F),
    ("SIRC15", (0xFD, 0, 0, 0), (0x13, 0, 0, 0), "Sony15", 0xFD, None, 0x13),
    ("SIRC20", (0x10, 0x04, 0, 0), (0x17, 0, 0, 0), "Sony20", 0x10, 0x20, 0x17),
    ("SIRC20", (0x3A, 0x07, 0, 0), (0x7C, 0, 0, 0), "Sony20", 0x1A, 0x39, 0x7C),
    ("RC5", (0x05, 0, 0, 0), (0x10, 0, 0, 0), "RC5", 0x05, None, 0x10),
    ("RC5", (0x1F, 0, 0, 0), (0x3F, 0, 0, 0), "RC5", 0x1F, None, 0x3F),
    ("RC5X", (0x14, 0, 0, 0), (0x3F, 0, 0, 0), "RC5", 0x14, None, 0x7F),
    ("RC6", (0x93, 0, 0, 0), (0xA1, 0, 0, 0), "RC6", 0x93, None, 0xA1),
    ("RC6", (0x00, 0, 0, 0), (0x00, 0, 0, 0), "RC6", 0x00, None, 0x00),
    ("Pioneer", (0xAF, 0, 0, 0), (0x36, 0, 0, 0), "Pioneer-2Part", 0xAFAF, None, 0x3636),
    # Kaseikyo: the address is ID << 24 | vendor << 8 | GENRE1 << 4 | GENRE2, least significant byte first
    ("Kaseikyo", (0x80, 0x02, 0x20, 0x00), (0xD0, 0x03, 0, 0), "Panasonic", 0x80, 0x00, 0x3D),
    ("Kaseikyo", (0x89, 0x02, 0x20, 0x02), (0x70, 0x00, 0, 0), "Panasonic", 0x80, 0x09, 0x87),
    ("Kaseikyo", (0x20, 0x03, 0x01, 0x00), (0x01, 0x01, 0, 0), "JVC-48", 0x22, 0x10, 0x10),
    ("Kaseikyo", (0x41, 0x54, 0x32, 0x01), (0x2C, 0x01, 0, 0), "Denon-K", 4, 1, 0x52C),
]


@pytest.mark.parametrize("protocol,address,command,ledger,device,subdevice,function", MESSAGES)
def test_a_flipper_message_is_the_ledgers_code_and_what_the_ledger_compiles_decodes_to_the_message(protocol, address, command, ledger, device, subdevice, function):
    mapped = mapping.signal_of(ir.Entry("t", "parsed", protocol=protocol, address=address, command=command))
    assert (mapped.protocol, mapped.device, mapped.subdevice, mapped.function) == (ledger, device, subdevice, function)
    assert _flipper(protocol, address, command) == (protocol, address, command)


def test_panasonics_power_key_is_the_frame_every_panasonic_remote_sends_and_the_vendor_parity_is_the_nibbles_xor():
    """Panasonic's power key, the one whose frame every Panasonic TV's remote sends: 02 20 80 00 3D BD."""
    mapped = mapping.signal_of(ir.Entry("t", "parsed", protocol="Kaseikyo", address=(0x80, 0x02, 0x20, 0), command=(0xD0, 0x03, 0, 0)))
    assert (mapped.protocol, mapped.device, mapped.subdevice, mapped.function) == ("Panasonic", 0x80, 0x00, 0x3D) and 0x80 ^ 0x00 ^ 0x3D == 0xBD
    assert (_vendor_parity(0x2002), _vendor_parity(0x3254), _vendor_parity(0x0103)) == (0, 0, 2)


@pytest.mark.parametrize("protocol,address,command,why", [
    ("NEC", (4, 1, 0, 0), (8, 0, 0, 0), "NEC takes an 8-bit address"),
    ("NEC", (4, 0, 0, 0), (8, 1, 0, 0), "NEC takes an 8-bit address"),
    ("NECext", (4, 1, 1, 0), (8, 0, 0, 0), "NECext takes a 16-bit address"),
    ("Samsung32", (4, 1, 0, 0), (8, 0, 0, 0), "Samsung32 takes"),
    ("SIRC", (0x20, 0, 0, 0), (1, 0, 0, 0), "SIRC takes a 5-bit address"),
    ("SIRC15", (0x9A, 0x0E, 0, 0), (0x2F, 0, 0, 0), "SIRC15 takes a 8-bit address"),
    ("SIRC15", (0x01, 0, 0, 0), (0x80, 0, 0, 0), "7-bit command"),
    ("SIRC20", (0xFF, 0xFF, 0, 0), (1, 0, 0, 0), "SIRC20 takes a 13-bit address"),
    ("RC5", (0x20, 0, 0, 0), (1, 0, 0, 0), "RC5 takes a 5-bit address"),
    ("RC5X", (0x01, 0, 0, 0), (0x40, 0, 0, 0), "RC5X takes a 5-bit address and a 6-bit command"),
    ("RC6", (1, 1, 0, 0), (1, 0, 0, 0), "RC6 takes"),
    ("Pioneer", (1, 0, 0, 1), (1, 0, 0, 0), "Pioneer takes"),
    ("Kaseikyo", (0x80, 0x02, 0x20, 0x04), (1, 0, 0, 0), "more than two bits"),
    ("Kaseikyo", (0x80, 0x02, 0x20, 0x00), (0x00, 0x04, 0, 0), "more than ten bits"),
    ("Kaseikyo", (0x80, 0x4C, 0x2A, 0x00), (1, 0, 0, 0), "vendor 0x2a4c is not a registered protocol"),
    ("NEC42", (1, 0, 0, 0), (1, 0, 0, 0), "NEC42 is not read"),
    ("RCA", (1, 0, 0, 0), (1, 0, 0, 0), "RCA is not read"),
])
def test_a_message_that_does_not_fit_its_protocol_or_has_none_here_is_refused_with_the_reason(protocol, address, command, why):
    got = mapping.signal_of(ir.Entry("t", "parsed", protocol=protocol, address=address, command=command))
    assert isinstance(got, str) and why in got


# --- names and one file ----------------------------------------------------------------------------------------------

CATEGORIES = {"ACs", "TVs", "DVD_Players", "Car_Multimedia", "Projectors", "MiniDisc", "Toys"}


@pytest.mark.parametrize("path,brand,model", [
    ("TVs/Hotels/Hilton_Connected_Room_Remote.ir", "Hotels", "Hilton Connected Room Remote"),
    ("ACs/Panasonic/Panasonic_CS-UE12RKE.ir", "Panasonic", "CS-UE12RKE"),
    ("ACs/Ferroli/Ferroli.ir", "Ferroli", "Ferroli"),
    ("Car_Multimedia/DVD_Players/Lexus/Lexus_GX470_DVD_2008.ir", "Lexus", "GX470 DVD 2008"),
    ("MiniDisc/Pioneer_CU-MJO11.ir", "Pioneer", "CU-MJO11"),
    ("Projectors/Sony/Sony RM-PJ30/Sony RM-PJ30 ID-1.ir", "Sony", "RM-PJ30 ID-1"),
    ("Toys/WowWee-RoboSapien/Roboraptor/Roboraptor.ir", "WowWee-RoboSapien", "Roboraptor"),
])
def test_the_brand_is_the_directory_below_the_category_and_the_model_the_file_without_the_brand(path, brand, model):
    assert fi.names_of(path, CATEGORIES) == (brand, model)


def test_key_names_are_the_folded_label_and_a_name_two_entries_fold_to_gets_a_number():
    entries = [ir.Entry(n, "raw") for n in ("Power", "Vol_up", "Vol up", "VOL UP", "Vol-up", "▲", "Mute")]
    assert fi.key_names(entries) == ["KEY_POWER", "KEY_VOL_UP_1", "KEY_VOL_UP_2", "KEY_VOL_UP_3", "KEY_VOL_MINUS_UP", "KEY_FUNCTION_1", "KEY_MUTE"]


def _snapshot(files: dict[str, str]) -> fi.Snapshot:
    manifest = {"commit": COMMIT, "licence": {"name": "CC0-1.0", "date": "2025-08-07", "pullRequest": 960, "commit": LICENCE_COMMIT},
                "files": {p: {"added": "0b2d88eabcdef", "date": "2025-11-09"} for p in files}}
    return fi.Snapshot(manifest, files)


def test_a_file_is_a_remote_for_each_protocol_and_a_raw_signal_is_truncated_with_the_firmwares_gap():
    docs, report = fi.import_tree(_snapshot({"TVs/Acme/Acme_X1.ir": SAMPLE}), {})
    assert sorted(docs) == ["remotes/flipper-irdb/Acme/TVs-Acme_X1-nec1.json", "remotes/flipper-irdb/Acme/TVs-Acme_X1-raw_38000_hz.json",
                            "remotes/flipper-irdb/Acme/TVs-Acme_X1-sony20.json"]
    nec = docs["remotes/flipper-irdb/Acme/TVs-Acme_X1-nec1.json"]
    assert (nec["manufacturer"], nec["model"], nec["controls"], nec["protocol"]) == ("Acme", "X1 [NEC1]", ["Acme X1 [NEC1]"], fi.block("NEC1"))
    form = nec["keys"]["KEY_POWER"]["forms"][0]
    assert (form["device"], form["subdevice"], form["function"], form["confidence"]) == (4, 251, 8, "plausible")
    assert form["source"].startswith("flipper-irdb@d126fb1 TVs/Acme/Acme_X1.ir:7 name 'Power' (added in 0b2d88e on 2025-11-09; parsed NEC, address 04 00 00 00, command 08 00 00 00): ")
    raw = docs["remotes/flipper-irdb/Acme/TVs-Acme_X1-raw_38000_hz.json"]
    assert raw["protocol"]["carrierHz"] == 38000 and raw["protocol"]["defaultGapUs"] == 180000 and raw["protocol"]["unitUs"] == 524
    assert set(raw["protocol"]["claims"]) == {"unitUs", "defaultGapUs"} and "name" not in raw["protocol"]
    rform = raw["keys"]["KEY_ON"]["forms"][0]
    assert rform["truncated"] is True and rform["repeat"] == [4391, 4402, 552, 1599, 553, 524, 552] and "truncated" in rform["claims"]
    assert "180 ms" in rform["source"]
    assert report.skipped_keys == [("TVs/Acme/Acme_X1.ir", "Odd", "SIRC15 takes a 8-bit address and a 7-bit command and a bit above them is not 0")]
    assert (report.remotes["imported"], report.keys["imported: NEC1"], report.keys["imported: Sony20"], report.keys["imported: raw 38000 Hz"]) == (3, 1, 1, 1)


def test_a_raw_remote_compiles_to_its_durations_and_a_closing_gap_of_180_ms():
    docs, _ = fi.import_tree(_snapshot({"TVs/Acme/Acme_X1.ir": SAMPLE}), {})
    remote = remote_from_doc(docs["remotes/flipper-irdb/Acme/TVs-Acme_X1-raw_38000_hz.json"], Path("t.json"))
    signal = remote.render(select(remote.groups("KEY_ON")["primary"]))
    assert signal.repeat == (4391, 4402, 552, 1599, 553, 524, 552, 180000) and signal.carrier_hz == 38000


def test_a_name_the_ledger_has_keeps_the_file_under_a_second_name_and_a_repeat_is_kept_once():
    text = SAMPLE.split("#\nname: Odd")[0] + "#\nname: Power\ntype: parsed\nprotocol: NEC\naddress: 04 00 00 00\ncommand: 08 00 00 00\n"
    snap = _snapshot({"TVs/Acme/Acme_X1.ir": text, "TVs/Acme/Acme_X2.ir": SAMPLE})
    docs, report = fi.import_tree(snap, {("acme", "x2 [nec1]"): "remotes/other/x2.json"})
    assert "Acme/TVs-Acme_X1-nec1.json" in " ".join(docs)
    nec2 = [d for t, d in docs.items() if "X2-nec1" in t][0]
    assert nec2["model"] == "X2 [NEC1] (Flipper-IRDB)" and ("TVs/Acme/Acme_X2.ir", "X2 [NEC1] (Flipper-IRDB)", "remotes/other/x2.json") in report.renamed
    assert report.repeated == [("TVs/Acme/Acme_X1.ir", "Power")]
    assert list([d for t, d in docs.items() if "X1-nec1" in t][0]["keys"]) == ["KEY_POWER"]


def test_a_file_that_is_not_readable_or_yields_nothing_is_reported_not_dropped():
    docs, report = fi.import_tree(_snapshot({"TVs/A/a.ir": "nonsense", "TVs/A/b.ir": "Filetype: IR signals file\nVersion: 1\n#\n",
                                             "TVs/A/c.ir": SAMPLE.split("#\nname: Power")[0] + "#\nname: Odd\ntype: parsed\nprotocol: SIRC15\n"
                                                                                                "address: 9A 0E 00 00\ncommand: 2F 00 00 00\n"}), {})
    assert docs == {} and report.files == Counter({"files": 3, "skipped: not a readable .ir file": 1, "skipped: no entries": 1, "skipped: no entry yields a key": 1})
    assert [p for p, _ in report.skipped_files] == ["TVs/A/a.ir", "TVs/A/b.ir", "TVs/A/c.ir"]


# --- the pinned snapshot ----------------------------------------------------------------------------------------------


def manifest() -> dict:
    return json.loads((SNAPSHOT / fi.MANIFEST).read_text(encoding="utf-8"))


def test_the_snapshot_is_90_files_of_the_commits_after_the_licence_and_names_the_53_it_left_out():
    m = manifest()
    assert (m["upstream"], m["commit"]) == ("https://github.com/Lucaslhm/Flipper-IRDB", COMMIT)
    assert m["licence"]["commit"] == LICENCE_COMMIT and (m["licence"]["name"], m["licence"]["pullRequest"], m["licence"]["date"]) == ("CC0-1.0", 960, "2025-08-07")
    assert len(m["files"]) == 90 and len(m["excluded"]) == 53
    assert all(entry["date"] > m["licence"]["date"] for entry in m["files"].values())            # not one added on the day of the licence: none is a close call
    assert not set(m["files"]) & {e["path"] for e in m["excluded"]}
    kinds = Counter("a rename" if e["reason"].startswith("a rename of") else "written before" if e["reason"].startswith("written ") else "content" for e in m["excluded"])
    assert kinds == Counter({"a rename": 20, "written before": 30, "content": 3})


def test_the_snapshot_files_are_the_ones_the_manifest_pins_and_the_loader_refuses_a_changed_one(tmp_path):
    snapshot = fi.load_snapshot(SNAPSHOT)
    assert len(snapshot.files) == 90 and snapshot.commit == COMMIT
    for path, entry in manifest()["files"].items():
        assert hashlib.sha256((SNAPSHOT / path).read_bytes()).hexdigest() == entry["sha256"]
    assert hashlib.sha256((SNAPSHOT / "LICENSE").read_bytes()).hexdigest() == manifest()["licence"]["fileSha256"]
    assert (ROOT / fi.IMPORT_ROOT / "LICENSE").read_bytes() == (SNAPSHOT / "LICENSE").read_bytes()
    import shutil

    copy = tmp_path / "snap"
    shutil.copytree(SNAPSHOT, copy)
    victim = copy / sorted(manifest()["files"])[0]
    victim.write_text(victim.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="is not the file MANIFEST.json pins"):
        fi.load_snapshot(copy)
    victim.write_bytes((SNAPSHOT / sorted(manifest()["files"])[0]).read_bytes())
    (copy / "Extra.ir").write_text("x", encoding="utf-8")
    with pytest.raises(ValidationError, match="in one of the directory"):
        fi.load_snapshot(copy)


def test_every_file_of_the_snapshot_parses_and_has_entries():
    for path, text in fi.load_snapshot(SNAPSHOT).files.items():
        assert ir.parse(text).entries, path


# --- the import ----------------------------------------------------------------------------------------------------------


def committed() -> dict[str, dict]:
    return {p.relative_to(ROOT).as_posix(): load(p) for p in sorted((ROOT / fi.IMPORT_ROOT).rglob("*.json"))}


def test_the_committed_import_is_exactly_what_the_pinned_snapshot_yields(tmp_path):
    """SPEC R19.5: re-running the import over the pinned snapshot reproduces every file byte for byte (no name collides, so nothing is renamed)."""
    docs, report = fi.import_tree(fi.load_snapshot(SNAPSHOT), authored_names(ROOT, fi.IMPORT_ROOT))
    assert report.renamed == [] and len(docs) == len(committed())
    root = tmp_path / "repo"
    (root / "remotes").mkdir(parents=True)
    fi.write_import(root, SNAPSHOT)
    fresh = {p.relative_to(root / fi.IMPORT_ROOT).as_posix(): p.read_bytes() for p in (root / fi.IMPORT_ROOT).rglob("*") if p.is_file()}
    have = {p.relative_to(ROOT / fi.IMPORT_ROOT).as_posix(): p.read_bytes() for p in (ROOT / fi.IMPORT_ROOT).rglob("*")
            if p.is_file() and p.name not in ("README.md", "LICENSE")}
    assert fresh == have


def test_the_import_is_102_remotes_and_2009_keys_and_lists_every_button_it_did_not_read():
    docs, report = fi.import_tree(fi.load_snapshot(SNAPSHOT), authored_names(ROOT, fi.IMPORT_ROOT))
    assert len(docs) == 102 and sum(len(d["keys"]) for d in docs.values()) == 2009
    assert report.keys["imported: raw 38000 Hz"] + report.keys["imported: raw 40000 Hz"] == 389
    assert Counter(r[2].split()[0] for r in report.skipped_keys) == Counter({"SIRC15": 44, "RC5X": 1, "does": 1})
    assert report.renamed == [] and len(report.repeated) == 6


def test_every_citation_names_a_snapshot_file_and_the_line_of_the_button_it_is_for():
    snapshot = fi.load_snapshot(SNAPSHOT)
    shape = re.compile(r"^flipper-irdb@d126fb1 (?P<path>\S.*?\.ir):(?P<line>\d+) name '(?P<name>.*)' \(added in (?P<commit>[0-9a-f]{7}) on (?P<date>\d{4}-\d\d-\d\d); "
                       r"(parsed \S+, address [0-9A-F ]{11}, command [0-9A-F ]{11}|raw, \d+ Hz, duty cycle [0-9.]+, \d+ durations ending on a mark)\): .+")
    count = 0
    for rel, doc in committed().items():
        for key, spec in doc["keys"].items():
            for form in spec["forms"]:
                m = shape.match(form["source"])
                assert m, form["source"]
                lines = snapshot.files[m["path"]].split("\n")
                assert lines[int(m["line"]) - 1].strip() == f"name: {spec['label']}" == f"name: {m['name']}", (rel, key)
                assert manifest()["files"][m["path"]]["added"].startswith(m["commit"]) and manifest()["files"][m["path"]]["date"] == m["date"]
                count += 1
    assert count == 2009


def test_the_raw_remotes_are_a_unit_a_gap_and_a_claim_for_each_and_every_raw_form_is_truncated():
    raws = {p: d for p, d in committed().items() if "name" not in d["protocol"]}
    assert len(raws) == 27
    for path, doc in raws.items():
        protocol = doc["protocol"]
        assert protocol["defaultGapUs"] == 180000 and protocol["minSends"] == 1 and set(protocol["claims"]) == {"unitUs", "defaultGapUs"}
        shortest = min(d for spec in doc["keys"].values() for form in spec["forms"] for d in form["repeat"])
        assert protocol["unitUs"] == min(shortest, 10_000)
        for spec in doc["keys"].values():
            form = spec["forms"][0]
            assert form["type"] == "raw" and form["truncated"] is True and len(form["repeat"]) % 2 == 1 and set(form["claims"]) == {"truncated"}
            assert "intro" not in form


def test_the_parsed_remotes_have_a_registered_protocol_with_its_nominal_carrier():
    from remote_ledger.protocols import REGISTRY

    for path, doc in committed().items():
        protocol = doc["protocol"]
        if "name" in protocol:
            assert protocol["carrierHz"] == REGISTRY[protocol["name"]].nominal_carrier_hz, path
            assert protocol["minSends"] == (3 if protocol["name"].startswith("Sony") else 1), path


def test_the_notice_names_the_licence_with_its_date_and_the_readme_says_it_in_the_same_words():
    from remote_ledger.bundle import notices
    from remote_ledger import paths

    facts = notices.FACTS["flipper-irdb"]
    assert facts["kind"] == "stated" and facts["upstream_url"] == "https://github.com/Lucaslhm/Flipper-IRDB"
    readme = notices.collapse((ROOT / "remotes" / "flipper-irdb" / "README.md").read_text(encoding="utf-8"))
    assert all(notices.collapse(s) in readme for s in facts["readme_sentences"]) and facts["upstream_url"] in readme
    assert paths.IMPORTS["remotes/flipper-irdb/"]["licence"] == "CC0-1.0"
