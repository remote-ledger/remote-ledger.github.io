"""The SwiftRemote DB hex map for the NEC family, and its oracle comparison.

Three independent checks sit here, because the map's one non-obvious fact --
each hex byte is the bit reversal of the NEC byte it carries -- cannot be
established by the comparison with the app alone (the app and the map could
both be wrong the same way):

1. hand-derived codes, worked from the IRP's LSB-first byte order;
2. the ledger's own Samsung remote (remotes/samsung/BN59-01199F.json, D/S/F
   from IRDB), whose eight keys are present in the DB under the hex the map
   inverts to;
3. a committed fixture of the app's own output for 140 codes
   (tests/fixtures/irblaster/), compared by tools/irblaster_oracle_nec.py.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from remote_ledger.irblaster import hex_nec
from remote_ledger.irblaster.hex_nec import FROM_DB_HEX, MIN_SENDS
from remote_ledger.protocols import REGISTRY

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "irblaster"
DB_PROTOCOLS = ("NEC", "NEC2", "NECx1", "NECx2")


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "irblaster_oracle_nec", ROOT / "tools" / "irblaster_oracle_nec.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _load_tool()


def _rows(protocol):
    return json.loads((FIXTURES / f"{protocol.lower()}.json").read_text())


def _complement_holds(hexcode):
    """Byte 4 is the complement of byte 3 -- written without the map, and
    invariant under per-byte bit reversal, which is why it needs no knowledge
    of the bit order."""
    raw = bytes.fromhex(hexcode)
    return raw[2] ^ raw[3] == 0xFF


# -- the contract ----------------------------------------------------------


def test_exports_exactly_what_the_importer_loads():
    assert set(FROM_DB_HEX) == set(DB_PROTOCOLS)
    assert MIN_SENDS == {"NECx2": 2}
    name, device, subdevice, function = FROM_DB_HEX["NEC"]("20DF10EF")
    assert (name, device, subdevice, function) == ("NEC1", 0x04, 0xFB, 0x08)
    assert isinstance(name, str) and name in REGISTRY


@pytest.mark.parametrize(
    "db,ledger", [("NEC", "NEC1"), ("NEC2", "NEC2"), ("NECx1", "NECx1"), ("NECx2", "NECx2")]
)
def test_each_db_protocol_lands_on_the_protocol_the_app_sends(db, ledger):
    assert FROM_DB_HEX[db]("000008F7")[0] == ledger
    assert ledger in REGISTRY


# -- 1. hand-derived -------------------------------------------------------


@pytest.mark.parametrize(
    "hexcode,expected",
    [
        # 0x08 = 0000 1000 sent MSB first; as an LSB-first byte that is 0x10.
        ("000008F7", (0x00, 0x00, 0x10)),
        # 0x20 = 0010 0000 -> 0x04; 0xDF -> 0xFB; 0x10 -> 0x08; 0xEF -> 0xF7.
        ("20DF10EF", (0x04, 0xFB, 0x08)),
        # D and S are free in extended NEC: 0x01 -> 0x80, 0x02 -> 0x40.
        ("0102B748", (0x80, 0x40, 0xED)),
        # All ones, and all zeros with the complement all ones.
        ("FFFFFF00", (0xFF, 0xFF, 0xFF)),
        ("000000FF", (0x00, 0x00, 0x00)),
        # A palindromic byte reads the same either way round: 0x81, 0x18.
        ("81C318E7", (0x81, 0xC3, 0x18)),
    ],
)
def test_bytes_are_bit_reversed(hexcode, expected):
    assert FROM_DB_HEX["NEC"](hexcode)[1:] == expected


def test_a_bit_order_bug_would_be_visible():
    """0x20DF10EF is not a palindrome under reversal, so MSB-first-as-bytes
    and bit-reversed give different answers -- the distinction the oracle has
    to be able to draw."""
    assert FROM_DB_HEX["NEC"]("20DF10EF")[1] != 0x20


@pytest.mark.parametrize("db", DB_PROTOCOLS)
def test_the_same_hex_means_the_same_bytes_in_every_db_protocol(db):
    assert FROM_DB_HEX[db]("E0E040BF")[1:] == (0x07, 0x07, 0x02)


# -- 2. the ledger's own Samsung remote -------------------------------------

#: The eight DB hexcodes that swiftremote.sqlite holds for the keys of
#: remotes/samsung/BN59-01199F.json (device 7, subdevice 7, from IRDB, which
#: files it under NECx2), found by inverting this map: 74 to 128 keys each under
#: NECx2, and the same code under NEC for 4-8 keys.
SAMSUNG_DB_HEX = {
    "KEY_EXIT": "E0E0B44B",
    "KEY_MENU": "E0E058A7",
    "KEY_MUTE": "E0E0F00F",
    "KEY_OK": "E0E016E9",
    "KEY_POWER": "E0E040BF",
    "KEY_SOURCE": "E0E0807F",
    "KEY_VOLUMEDOWN": "E0E0D02F",
    "KEY_VOLUMEUP": "E0E0E01F",
}


def test_the_map_agrees_with_the_ledgers_own_samsung_remote():
    remote = json.loads((ROOT / "remotes" / "samsung" / "BN59-01199F.json").read_text())
    irp_keys = {
        name: form
        for name, key in remote["keys"].items()
        for form in key["forms"]
        if form["type"] == "irp"
    }
    assert set(irp_keys) == set(SAMSUNG_DB_HEX)
    for name, form in irp_keys.items():
        expected = (
            "NECx2",
            int(form["device"], 16),
            int(form["subdevice"], 16),
            int(form["function"], 16),
        )
        assert FROM_DB_HEX["NECx2"](SAMSUNG_DB_HEX[name]) == expected, name


# -- stable refusals --------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "1234567", "123456789", "807F42BD0", "0xE0E040B", "GGGGGGGG", "E0E040B "])
def test_malformed_hexcodes_are_refused_with_one_reason(bad):
    """807F42BD0 is the nine-digit code SwiftRemote commit b78cb5d repaired;
    the app would have sent its low 32 bits, 07F42BD0."""
    for fn in FROM_DB_HEX.values():
        with pytest.raises(ValueError, match="not exactly 8 hex digits"):
            fn(bad)


def _bad_byte4(f, e):
    """A hexcode whose wire bytes are D=0, S=0, F=f, E=e."""
    rev = lambda b: int(f"{b:08b}"[::-1], 2)
    return f"0000{rev(f):02X}{rev(e):02X}"


@pytest.mark.parametrize(
    "f,e,fragment",
    [
        (0x00, 0x7F, "complements bits 0-6 only (Yamaha style y1)"),
        (0x00, 0xFE, "complements bits 1-7 only (Yamaha style y2)"),
        (0x00, 0x7E, "complements bits 1-6 only (Yamaha style y3)"),
        (0x12, 0xDE, "complement with the nibbles swapped (style rnc)"),
        (0x12, 0x12, "unrelated to byte 3"),
        (0x00, 0x00, "unrelated to byte 3"),
    ],
)
def test_each_variant_of_byte_4_gets_its_own_stable_reason(f, e, fragment):
    hexcode = _bad_byte4(f, e)
    for db, name in (("NEC", "NEC1"), ("NEC2", "NEC2"), ("NECx1", "NECx1"), ("NECx2", "NECx2")):
        with pytest.raises(ValueError) as caught:
            FROM_DB_HEX[db](hexcode)
        text = str(caught.value)
        assert fragment in text
        assert f"unregistered {name}-f16" in text
        assert hexcode not in text, "a reason must not carry the hexcode, or it cannot be grouped"


def test_the_refusal_text_is_the_same_for_every_code_of_a_kind():
    seen = set()
    for f in (0x00, 0x33, 0x44):
        with pytest.raises(ValueError) as caught:
            FROM_DB_HEX["NEC"](_bad_byte4(f, f))
        seen.add(str(caught.value))
    assert len(seen) == 1


def test_a_correct_complement_is_never_refused():
    for f in range(256):
        hexcode = _bad_byte4(f, f ^ 0xFF)
        assert FROM_DB_HEX["NEC"](hexcode)[3] == f


# -- 3. the app's own output -------------------------------------------------


@pytest.mark.parametrize("db", DB_PROTOCOLS)
def test_fixture_rows_are_what_the_app_produced(db):
    """The fixture is the oracle's rows, unedited: protocol, mode and
    duration count are what the app's code gives, per lib/utils/ir.dart and
    lib/ir/protocols/*.dart."""
    rows = _rows(db)
    assert len(rows) >= 30
    expected = {
        "NEC": ("legacy_nec_default", 38_000, 67),
        "NEC2": ("protocol:nec2", 38_222, 68),
        "NECx1": ("protocol:necx1", 38_400, 68),
        "NECx2": ("protocol:necx2", 38_400, 136),
    }[db]
    for row in rows:
        assert row["protocol"] == db
        assert (row["mode"], row["freq"], len(row["pattern"])) == expected
        assert len(row["hex"]) == 8


@pytest.mark.parametrize("db", DB_PROTOCOLS)
def test_every_code_with_a_complement_matches_the_apps_waveform(db):
    rows = _rows(db)
    matched = 0
    for row in rows:
        verdict, info = TOOL.compare(row, db)
        if _complement_holds(row["hex"]):
            assert verdict == "matched", f"{row['hex']}: {info}"
            matched += 1
        else:
            assert verdict == "unrepresentable", f"{row['hex']}: {info}"
    assert matched >= 25


def test_the_fixtures_include_codes_the_ledger_cannot_hold():
    """NEC and NEC2 are the two with bad fourth bytes in the real DB."""
    for db in ("NEC", "NEC2"):
        refused = [r for r in _rows(db) if not _complement_holds(r["hex"])]
        assert len(refused) >= 5
    for db in ("NECx1", "NECx2"):
        assert all(_complement_holds(r["hex"]) for r in _rows(db))


def test_the_comparison_can_fail(monkeypatch):
    """A map that forgets the bit reversal is caught by the oracle rows:
    otherwise 'matched' would only mean 'the tool ran'."""
    forgetful = lambda hexcode: ("NEC1", *bytes.fromhex(hexcode)[:3])
    monkeypatch.setitem(FROM_DB_HEX, "NEC", forgetful)
    verdicts = {TOOL.compare(row, "NEC")[0] for row in _rows("NEC") if _complement_holds(row["hex"])}
    assert "mismatched" in verdicts


def test_the_comparison_can_fail_on_the_wrong_lead_in(monkeypatch):
    """The comparison is not insensitive to NECx2's lead-in: a NEC1 signal
    (16-unit lead-in) for a NECx2 code is a mismatch."""
    swapped = lambda hexcode: ("NEC1", *hex_nec.FROM_DB_HEX["NEC"](hexcode)[1:])
    monkeypatch.setitem(FROM_DB_HEX, "NECx2", swapped)
    verdicts = {TOOL.compare(row, "NECx2")[0] for row in _rows("NECx2")}
    assert verdicts == {"mismatched"}


def test_necx2_needs_two_sends_to_match():
    """The app sends the frame twice; one send of the ledger's NECx2 is half
    the app's 136 durations."""
    row = _rows("NECx2")[0]
    assert TOOL.compare(row, "NECx2")[0] == "matched"
    saved = dict(hex_nec.MIN_SENDS)
    try:
        hex_nec.MIN_SENDS.clear()
        verdict, why = TOOL.compare(row, "NECx2")
    finally:
        hex_nec.MIN_SENDS.update(saved)
    assert verdict == "mismatched"
    assert "68 durations against the app's 136" in why


def test_the_apps_nec_path_has_no_lead_out_and_the_ledgers_does():
    """The one documented framing difference: 67 durations against 68."""
    row = next(r for r in _rows("NEC") if _complement_holds(r["hex"]))
    assert len(row["pattern"]) == 67
    name, device, subdevice, function = FROM_DB_HEX["NEC"](row["hex"])
    signal = REGISTRY[name].encode(
        device=device, subdevice=subdevice, function=function, carrier_hz=38_000
    )
    assert len(signal.intro) == 68
    assert signal.intro[-1] > 30_000  # the ^108m gap the app never sends


@pytest.mark.parametrize("db", ("NEC2", "NECx1", "NECx2"))
def test_the_apps_frame_totals_107904_us_not_the_108800_its_comments_claim(db):
    """nec2.dart, necx1.dart and necx2.dart say 'pad to 108800us' and write
    the constant as 0x1A580, which is 107,904. Every frame the oracle holds
    sums to 107,904, 96 us short of the IRP's ^108m -- closer to the IRP than
    the app's own comments say."""
    for row in _rows(db):
        frame = row["pattern"][:68]
        assert sum(frame) == 107_904 == 0x1A580


def test_a_changed_duration_in_the_apps_pattern_is_a_mismatch():
    row = dict(next(r for r in _rows("NEC2") if _complement_holds(r["hex"])))
    assert TOOL.compare(row, "NEC2")[0] == "matched"
    pattern = list(row["pattern"])
    pattern[3] = 1687 if pattern[3] < 1000 else 562  # flip one bit's space
    row["pattern"] = pattern
    verdict, why = TOOL.compare(row, "NEC2")
    assert verdict == "mismatched"
    assert why.startswith("duration 3:")


def test_a_carrier_more_than_5_percent_off_is_a_mismatch():
    row = dict(next(r for r in _rows("NEC2") if _complement_holds(r["hex"])))
    row["freq"] = 36_000
    verdict, why = TOOL.compare(row, "NEC2")
    assert verdict == "mismatched"
    assert why.startswith("carrier")


def test_the_tool_exits_nonzero_on_a_mismatch_and_zero_otherwise(monkeypatch):
    sources = {"NEC": _rows("NEC")}
    assert TOOL.run(sources, 0, None) == 0
    forgetful = lambda hexcode: ("NEC1", *bytes.fromhex(hexcode)[:3])
    monkeypatch.setitem(FROM_DB_HEX, "NEC", forgetful)
    assert TOOL.run(sources, 0, None) == 1
