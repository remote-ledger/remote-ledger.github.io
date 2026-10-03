"""The `unknown` family's DB hexcodes: REC80, RCC2026 and RCC0082.

Two kinds of test. The mapping tests pin the hex -> (protocol, D, S, F)
function against codes whose meaning an independent source states. The
fixture tests replay a few dozen oracle records per DB protocol (the app's own
output, copied to tests/fixtures/irblaster/) through the same comparison the
tool ``tools/irblaster_oracle_unknown.py`` runs over all 4,254 distinct codes.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from remote_ledger.irblaster.hex_unknown import FROM_DB_HEX, FROM_DB_HEX_SWIFTREMOTE, MIN_SENDS
from remote_ledger.protocols import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "irblaster"

_spec = importlib.util.spec_from_file_location(
    "irblaster_oracle_unknown", ROOT / "tools" / "irblaster_oracle_unknown.py"
)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["records"]


def _app(hexcode, proto):
    return FROM_DB_HEX[proto](hexcode)


# --- the interface the importer loads ---------------------------------------------------


def test_the_module_exports_what_the_importer_expects():
    assert set(FROM_DB_HEX) == {"REC80", "RCC2026", "RCC0082"}
    assert MIN_SENDS == {"RCC2026": 2}


def test_every_mapped_protocol_is_in_the_registry():
    for proto in FROM_DB_HEX:
        for record in _fixture(proto.lower()):
            try:
                name = _app(record["hex"], proto)[0]
            except ValueError:
                continue
            assert name in REGISTRY


# --- independent anchors -------------------------------------------------------------------
#
# Each row is a DB code whose label and (D, S, F) an independent source states:
# the decode IrpTransmogrifier's own teaser test asserts for a real remote
# (src/test/teaserfiles/*.exp @c945e76), or a probonopd/irdb row for a real
# remote. The DB's labels are the app's, the parameters are the source's.

ANCHORS = [
    # REC80 -> Panasonic: Panasonic.exp, D=176 S=0: Audio F=51, Angle F=144
    ("REC80", "40040D00CCC1", ("Panasonic", 176, 0, 51), "AUDIO; Panasonic.exp 'Audio'"),
    ("REC80", "40040D000904", ("Panasonic", 176, 0, 144), "ANGLE; Panasonic.exp 'Angle'"),
    # REC80 -> Fujitsu: Fujitsu_pronto.exp, D=132: Power 0, Vol+ 32, Vol- 33, Menu 64
    ("REC80", "28C600212100", ("Fujitsu", 132, 132, 0), "POWER; Fujitsu_pronto.exp 'Power'"),
    ("REC80", "28C600212104", ("Fujitsu", 132, 132, 32), "VOL+; Fujitsu_pronto.exp 'Vol+'"),
    ("REC80", "28C600212184", ("Fujitsu", 132, 132, 33), "VOL-; Fujitsu_pronto.exp 'Vol-'"),
    ("REC80", "28C600212102", ("Fujitsu", 132, 132, 64), "MENU; Fujitsu_pronto.exp 'Menu'"),
    # REC80 -> Denon-K: Denon-K_Denon.exp, D=4 S=1: Up 27, Down 28, Left 29, Right 30
    ("REC80", "2A4C028D800F", ("Denon-K", 4, 1, 27), "UP; Denon-K_Denon.exp 'DirectionUp'"),
    ("REC80", "2A4C02838001", ("Denon-K", 4, 1, 28), "DOWN; Denon-K_Denon.exp 'DirectionDown'"),
    ("REC80", "2A4C028B8009", ("Denon-K", 4, 1, 29), "LEFT; Denon-K_Denon.exp 'DirectionLeft'"),
    ("REC80", "2A4C02878005", ("Denon-K", 4, 1, 30), "RIGHT; Denon-K_Denon.exp 'DirectionRight'"),
    # REC80 -> Denon-K: probonopd/irdb codes/Denon/Blu-Ray/2,1.csv, keys 2 and 3
    ("REC80", "2A4C048C0088", ("Denon-K", 2, 1, 3), "2; irdb Denon-K 2,1 key '2' = 3"),
    ("REC80", "2A4C04820086", ("Denon-K", 2, 1, 4), "3; irdb Denon-K 2,1 key '3' = 4"),
    # REC80 -> Teac-K: Teac_0_4.exp, D=0 S=4: Power 0, Vol+ 32, Vol- 48
    ("REC80", "C2CA80200020", ("Teac-K", 0, 4, 0), "POWER; Teac_0_4.exp 'Power'"),
    ("REC80", "C2CA80200460", ("Teac-K", 0, 4, 32), "VOL+; Teac_0_4.exp 'Vol+'"),
    ("REC80", "C2CA80200CE0", ("Teac-K", 0, 4, 48), "VOL-; Teac_0_4.exp 'Vol-'"),
    # REC80 -> SharpDVD: probonopd/irdb codes/Sharp/Unknown_RRMCGA030WJSA/8,48.csv
    ("REC80", "555AF10C808D", ("SharpDVD", 8, 48, 1), "1; irdb KEY_1 = 1"),
    ("REC80", "555AF10C4081", ("SharpDVD", 8, 48, 2), "2; irdb KEY_2 = 2"),
    ("REC80", "555AF10C5080", ("SharpDVD", 8, 48, 10), "0; irdb KEY_0 = 10"),
    # RCC2026 -> Aiwa: Aiwa_left.exp, a real Aiwa remote's 'left' = D=8 S=0 F=21;
    # and probonopd/irdb codes/Aiwa/Mini System/110,0.csv, POWER 0 and key 1 = 1
    ("RCC2026", "10077FEA15C", ("Aiwa", 8, 0, 21), "LEFT; Aiwa_left.exp 'left'"),
    ("RCC2026", "76044FC03FC", ("Aiwa", 110, 0, 0), "POWER; irdb Aiwa 110,0 POWER = 0"),
    ("RCC2026", "76044FE01FC", ("Aiwa", 110, 0, 1), "1; irdb Aiwa 110,0 key 1 = 1"),
    # RCC0082 -> Blaupunkt: Blaupunkt.exp, D=2: Play 10, Pause 11, Stop 12, Ch- 20, Ch+ 21
    ("RCC0082", "574", ("Blaupunkt", 2, None, 10), "PLAY; Blaupunkt.exp 'Play'"),
    ("RCC0082", "174", ("Blaupunkt", 2, None, 11), "PAUSE; Blaupunkt.exp 'Pause'"),
    ("RCC0082", "674", ("Blaupunkt", 2, None, 12), "STOP; Blaupunkt.exp 'Stop'"),
    ("RCC0082", "6B4", ("Blaupunkt", 2, None, 20), "P-; Blaupunkt.exp 'Ch-'"),
    ("RCC0082", "2B4", ("Blaupunkt", 2, None, 21), "P+; Blaupunkt.exp 'Ch+'"),
]


@pytest.mark.parametrize("proto, hexcode, expected, why", ANCHORS, ids=[a[3] for a in ANCHORS])
def test_hex_maps_to_what_an_independent_source_says(proto, hexcode, expected, why):
    assert FROM_DB_HEX[proto](hexcode) == expected


def test_rcc2026_reads_the_first_42_bits_not_the_last():
    """The case that matters. Upstream's app test (iodn/android-ir-blaster
    test/rcc2026_protocol_test.dart @3bb60e3178) pins ``38863BD42BC`` as a
    NEC42-layout D=284 (13 bits), F=10; D13 = D + 256 * S. SwiftRemote's copy
    of the encoder reads the last 42 bits and sends something else."""
    assert FROM_DB_HEX["RCC2026"]("38863BD42BC") == ("Aiwa", 28, 1, 10)
    assert 28 + 256 * 1 == 284
    # the same bits read the stale way are not an Aiwa frame at all
    n = int("38863BD42BC", 16)
    stale = f"{n:044b}"[-42:]
    d = int(stale[0:8][::-1], 2)
    nd = int(stale[13:21][::-1], 2)
    s = int(stale[8:13][::-1], 2)
    ns = int(stale[21:26][::-1], 2)
    assert not (nd == d ^ 0xFF and ns == s ^ 0x1F)


@pytest.mark.parametrize("hexcode", ["0087FBC03FC", "00FFF8043BC", "0087FBCA35C"])
def test_the_swiftremote_reading_reproduces_the_app_wherever_it_maps(hexcode):
    """``FROM_DB_HEX_SWIFTREMOTE`` is the stale reading. Where it maps at all
    (71 of 1,231 codes) the ledger's burst is exactly what the app sends, which
    is what makes it the app's reading and not a guess at it. For these codes
    the two readings give different parameters: ``0087FBC03FC``, the app's own
    Universal Power default, is Aiwa D=0 S=1 F=0 -- F=0 is POWER in irdb's Aiwa
    tables (codes/Aiwa/Mini System/110,0.csv) -- and F=192 the stale way."""
    name, d, s, f = FROM_DB_HEX_SWIFTREMOTE["RCC2026"](hexcode)
    burst, _ = tool.ledger_burst(name, d, s, f, 38222, MIN_SENDS["RCC2026"])
    assert burst == tool.app_rcc2026(hexcode, "last")
    assert FROM_DB_HEX["RCC2026"](hexcode)[1:] != (d, s, f)
    if hexcode == "0087FBC03FC":
        assert FROM_DB_HEX["RCC2026"](hexcode) == ("Aiwa", 0, 1, 0)
        assert (d, s, f) == (64, 24, 192)


def test_most_rcc2026_codes_are_not_an_aiwa_frame_the_way_swiftremote_reads_them():
    with pytest.raises(ValueError, match="not an Aiwa frame"):
        FROM_DB_HEX_SWIFTREMOTE["RCC2026"]("38863BD42BC")


# --- unrepresentable codes: stable, hexcode-free reasons ------------------------------------

REASONS = [
    ("REC80", "400405100104", "REC80 Panasonic frame: check byte is not D^S^F"),
    ("REC80", "C2CA80200080", "REC80 Teac-K frame: check byte T is not D+S+F nibble sum"),
    ("REC80", "28C601210004", "REC80 Fujitsu frame: E is not 0, which a ledger form cannot name"),
    ("REC80", "555A0D000085", "REC80 SharpDVD frame: the fixed 15:4 nibble is not 15"),
    ("RCC2026", "6604CF5F20C", "RCC2026 bits are not an Aiwa frame: ~S is not the complement"),
    ("RCC2026", "6604CF5F22C", "RCC2026 bits are not an Aiwa frame: ~S, ~F is not the complement"),
]


@pytest.mark.parametrize("proto, hexcode, reason", REASONS)
def test_unrepresentable_codes_say_why_without_the_hexcode(proto, hexcode, reason):
    with pytest.raises(ValueError) as err:
        FROM_DB_HEX[proto](hexcode)
    assert str(err.value) == reason
    assert hexcode not in str(err.value)


@pytest.mark.parametrize(
    "proto, hexcode",
    [("REC80", "28C60021210"), ("REC80", "28C6002121000"), ("REC80", "28C60021210G"),
     ("RCC2026", "38863BD42B"), ("RCC0082", "7B"), ("RCC0082", "7BCC"), ("RCC0082", "7BZ")],
)
def test_malformed_hex_is_refused_as_the_app_refuses_it(proto, hexcode):
    with pytest.raises(ValueError, match="hexadecimal digits"):
        FROM_DB_HEX[proto](hexcode)


def test_rcc0082_unused_bits_must_be_zero():
    """The app drops the top bit of the first digit and the low two bits of the
    last; a code with them set is two different hexcodes for one signal."""
    with pytest.raises(ValueError, match="unused bits"):
        FROM_DB_HEX["RCC0082"]("F7C")
    with pytest.raises(ValueError, match="unused bits"):
        FROM_DB_HEX["RCC0082"]("7BD")


def test_rcc2026_padding_bits_must_be_zero():
    with pytest.raises(ValueError, match="padding bits"):
        FROM_DB_HEX["RCC2026"]("38863BD42BD")


# --- the oracle comparison, on the committed fixtures ---------------------------------------


def _classes(name):
    return [(r, tool.classify({"protocol": name.upper(), **r})) for r in _fixture(name)]


def test_rec80_fixture_matches_the_app_except_for_three_documented_lead_outs():
    results = _classes("rec80")
    by_ledger = {}
    for record, res in results:
        by_ledger.setdefault(res.get("ledger"), set()).add(res["class"])
    assert "UNEXPLAINED" not in {c for cs in by_ledger.values() for c in cs}
    # exact match, including the final gap
    for name in ("Panasonic", "JVC-48", "Denon-K"):
        assert by_ledger[name] == {"matched"}, name
    # the IRP's gap is 110, 100 and 48 units; the app sends 173 for all of them
    for name in ("Fujitsu", "Teac-K", "SharpDVD"):
        assert by_ledger[name] == {"matched, lead-out differs"}, name
    assert by_ledger[None] == {"unrepresentable"}


def test_rec80_lead_out_differences_are_the_final_gap_alone():
    """Not a widened tolerance: everything but the last duration is within
    12 % / 150 us, and the last is the protocol's own gap."""
    seen = 0
    for record, res in _classes("rec80"):
        if res["class"] != "matched, lead-out differs":
            continue
        seen += 1
        assert tool._off(res["burst"], record["pattern"]) == [len(record["pattern"]) - 1]
        assert record["pattern"][-1] == 74736          # the app: 173 units of 432
        assert res["burst"][-1] in (110 * 432, 100 * 432, 48 * 400)
    assert seen >= 12


def test_rcc0082_fixture_matches_the_app():
    results = _classes("rcc0082")
    assert {res["class"] for _, res in results} == {"matched"}
    # the constants that differ, none by more than 9.1 %: the IRP's unit is 512
    # and the app's 528; the IRP's gaps are 45 and 236 units against the app's
    # 40 and 210/211.
    pairs = set()
    for record, res in results:
        for a, b in zip(res["burst"], record["pattern"]):
            if a != b:
                pairs.add((a, b))
    assert max(abs(a - b) / b for a, b in pairs) < 0.092
    assert (23040, 21120) in pairs


def test_rcc0082_ending_is_checked_even_though_the_signal_cannot_carry_it():
    """The ledger's burst is the intro plus the IRP's ending (its sync);
    the app sends that and nothing else, every one of its durations."""
    for record, res in _classes("rcc0082"):
        assert len(res["burst"]) == len(record["pattern"]) >= 56


def test_rcc2026_fixture_is_the_stale_copy_not_the_ledger():
    """Every representable RCC2026 code transmits a different frame from
    SwiftRemote today. The cause is pinned, not just counted: this file's port
    of the stale reading reproduces the oracle exactly, and the left-aligned
    reading reproduces the ledger exactly."""
    results = _classes("rcc2026")
    assert {res["class"] for _, res in results} == {"mismatched, explained", "unrepresentable"}
    explained = [(r, res) for r, res in results if res["class"] == "mismatched, explained"]
    assert len(explained) >= 20
    for record, res in explained:
        assert tool.app_rcc2026(record["hex"], "last") == record["pattern"]
        assert tool.app_rcc2026(record["hex"], "first") == res["burst"]
        assert res["burst"] != record["pattern"]


def test_rcc2026_burst_is_the_frame_then_the_tail():
    """Intro plus one repeat, which is what ``MIN_SENDS`` says the app sends."""
    for record, res in _classes("rcc2026"):
        if "burst" in res:
            assert len(res["burst"]) == 92 == len(record["pattern"])
            assert res["burst"][-4:] == [8800, 4400, 550, 90750]


def test_every_fixture_record_is_internally_consistent_with_the_ports():
    """The fixture is the oracle's own output; this file's ports of the three
    Dart encoders must reproduce all of it, or the comparison above would be
    comparing against something other than the app."""
    for proto in ("REC80", "RCC2026", "RCC0082"):
        for record in _fixture(proto.lower()):
            assert tool.check_port({"protocol": proto, **record}) is None, (proto, record["hex"])


@pytest.mark.parametrize("proto", ["rec80", "rcc2026", "rcc0082"])
def test_a_few_dozen_codes_per_protocol_are_committed(proto):
    n = len(_fixture(proto))
    assert 24 <= n <= 60
