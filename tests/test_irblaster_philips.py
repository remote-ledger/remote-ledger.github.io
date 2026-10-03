"""SwiftRemote's Philips-family database codes, as ledger forms.

Two layers. The *oracle* fixtures are a few dozen of the app's own outputs
per protocol (the full sets are 2,428, 1,237, 60 and 29 distinct codes,
checked by ``tools/irblaster_oracle_philips.py``); each must be the signal
the ledger compiles. The *map* tests check ``irblaster/hex_philips.py`` over
whole code spaces against decoders written from the frame layouts, so they
hold for codes no database contains.

Thomson7 is the exception the oracle exists to find: the ledger's reading of
its hexcodes is the one a hardware capture supports, and the app's is not
(DESIGN D63). The fixtures pin that disagreement and its explanation.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from remote_ledger.irblaster import hex_philips
from remote_ledger.protocols import REGISTRY

from test_rc5 import _decode as decode_rc5
from test_rc6 import _decode as decode_rc6
from test_rca38 import _decode as decode_rca38

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "irblaster"

_spec = importlib.util.spec_from_file_location(
    "irblaster_oracle_philips", ROOT / "tools" / "irblaster_oracle_philips.py")
oracle = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = oracle      # a dataclass resolves its annotations here
_spec.loader.exec_module(oracle)


def _records(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["records"]


def _ledger(hexcode, protocol, toggle=None, carrier=None):
    name, device, subdevice, function = hex_philips.FROM_DB_HEX[protocol](hexcode)
    entry = REGISTRY[name]
    kwargs = {} if toggle is None else {"toggle": toggle}
    return entry.encode(device=device, subdevice=subdevice, function=function,
                        carrier_hz=carrier or entry.nominal_carrier_hz, **kwargs)


# --- the contract ----------------------------------------------------------------

def test_the_module_exports_what_the_importer_loads():
    assert set(hex_philips.FROM_DB_HEX) == {"RC5", "RC6", "RCA_38", "Thomson7"}
    assert set(hex_philips.MIN_SENDS) <= set(hex_philips.FROM_DB_HEX)
    assert hex_philips.MIN_SENDS == {"Thomson7": 2}


@pytest.mark.parametrize("protocol, sample", [
    ("RC5", "81A"), ("RC6", "000C"), ("RCA_38", "F30"), ("Thomson7", "30E")])
def test_a_map_returns_a_registry_name_and_three_fields(protocol, sample):
    name, device, subdevice, function = hex_philips.FROM_DB_HEX[protocol](sample)
    assert name in REGISTRY
    assert subdevice is None
    assert isinstance(device, int) and isinstance(function, int)


def test_refusals_are_stable_text_that_never_names_the_code():
    messages = set()
    for protocol, too_long in [("RC5", "1000"), ("RC6", "10000"), ("RCA_38", "1000"),
                               ("Thomson7", "1000")]:
        for code in (too_long, too_long.replace("1", "2")):
            with pytest.raises(ValueError) as caught:
                hex_philips.FROM_DB_HEX[protocol](code)
            assert code not in str(caught.value)
            messages.add((protocol, str(caught.value)))
    assert len(messages) == 4, "one message per protocol, whatever the code"
    for protocol in hex_philips.FROM_DB_HEX:
        for bad in ("", "xyz", "0x12"):
            with pytest.raises(ValueError, match="not a run of hex digits"):
                hex_philips.FROM_DB_HEX[protocol](bad)


# --- RC5 -------------------------------------------------------------------------

def test_every_rc5_code_maps_to_the_frame_it_spells():
    """All 4,096 codes: hexcode bit 11 is the second start bit, 10..6 the
    address, 5..0 the low six command bits, and the start bit is the complement
    of command bit 6."""
    for value in range(0x1000):
        signal = _ledger(f"{value:03X}", "RC5")
        s1, s2, _, device, function = decode_rc5(signal.repeat)
        assert s1 == 1
        assert s2 == (value >> 11) & 1
        assert device == (value >> 6) & 0x1F
        assert function & 0x3F == value & 0x3F
        assert (function >> 6) == 1 - s2


def test_rc5_power_of_a_philips_set_is_address_0_command_12():
    assert hex_philips.FROM_DB_HEX["RC5"]("80C") == ("RC5", 0, None, 12)
    assert hex_philips.FROM_DB_HEX["RC5"]("000") == ("RC5", 0, None, 64)


def test_the_published_raw_rc5_sequence_is_reproduced_exactly():
    """IrpTransmogrifier's BiphaseDecoderNGTest.java L22 @ c945e76, asserted to
    decode to {A=1,B=1,C=1,D=12,E=3}: device 12, command 3, toggle 1. A
    published raw sequence for an already registered protocol, which the
    app's code 0xB03 produces apart from the toggle."""
    raw = [889, 889, 889, 889, 1778, 1778, 889, 889, 1778, 889, 889, 889, 889,
           889, 889, 889, 889, 889, 889, 1778, 889, 889, 889, 89997]
    assert list(REGISTRY["RC5"].encode(device=12, subdevice=None, function=3,
                                       carrier_hz=36_000, toggle=1).repeat) == raw
    assert hex_philips.FROM_DB_HEX["RC5"]("B03") == ("RC5", 12, None, 3)


# --- RC6 -------------------------------------------------------------------------

def test_rc6_hexcodes_are_address_then_command():
    """A sample covering every address and every command (the full 65,536 are
    arithmetic, and the encoder is exhaustively tested in test_rc6)."""
    for k in range(256):
        for value in (k << 8 | (k * 7 + 3) % 256, ((k * 5 + 1) % 256) << 8 | k):
            signal = _ledger(f"{value:04X}", "RC6", toggle=0)
            assert decode_rc6(signal.repeat) == (1, 0, 0, value >> 8, value & 0xFF)


def test_philips_tv_rc6_codes_are_the_published_command_set():
    """Girr's philips_tv_cmdset_rc6.girr (bengtmartensson/Girr @ 5ca171e,
    src/test/girr/) lists a Philips TV at RC6 device 0, power 12, volume 16/17,
    mute 13, digits 0-9. The database spells them 000C, 0010, 0011, 000D."""
    for code, function in (("000C", 12), ("0010", 16), ("0011", 17), ("000D", 13),
                           ("0005", 5)):
        assert hex_philips.FROM_DB_HEX["RC6"](code) == ("RC6", 0, None, function)


# --- RCA-38 ----------------------------------------------------------------------

def test_every_rca_38_code_maps_to_the_frame_it_spells():
    for value in range(0x1000):
        signal = _ledger(f"{value:03X}", "RCA_38")
        assert decode_rca38(signal.repeat) == (value >> 8, value & 0xFF)


def test_the_capture_s_aspect_key_is_the_databases_f90():
    """RCA-38.exp (IrpTransmogrifier @ c945e76) decodes Aspect to D=15, F=144;
    the database holds F90 for the same two numbers (and F00 style codes for the
    keys the capture also has: Menu F=8 is F08, Mute F=63 is F3F)."""
    for code, function in (("F90", 144), ("F08", 8), ("F3F", 63), ("F2A", 42)):
        assert hex_philips.FROM_DB_HEX["RCA_38"](code) == ("RCA-38", 15, None, function)


# --- Thomson7 --------------------------------------------------------------------

def _thomson_bits_in_transmission_order(signal):
    """The twelve bits as sent, first bit first (so the IRP's D0 comes first)."""
    unit = REGISTRY["Thomson7"].unit_us
    return [0 if space == 4 * unit else 1 for space in signal.repeat[1:24:2]]


def test_every_thomson7_hexcode_is_its_frame_in_transmission_order():
    """All 4,096 codes. Written left to right, the twelve hexcode bits are the
    frame in the order it is sent -- the first bit sent is hexcode bit 11 --
    with bit 7 the toggle's place, which a form cannot carry."""
    for value in range(0x1000):
        signal = _ledger(f"{value:03X}", "Thomson7", toggle=0)
        bits = _thomson_bits_in_transmission_order(signal)
        assert int("".join(map(str, bits)), 2) == value & 0xF7F, f"{value:03X}"


def test_the_five_keys_a_real_thomson_remote_shares_with_the_database():
    """IrpTransmogrifier's Thomson-0625.exp @ c945e76 decodes a captured
    Thomson remote's Vol+, Vol-, Mute, Up and Down to Thomson7 D=12 and F=74,
    42, 80, 104, 88. SwiftRemote's Thomson7 remote (id 800296) has VOL+, VOL-,
    MUTE, J UP and J DOWN as 329, 32A, 305, 30B, 30D. Read as above, they are
    those five frames. tools/philips_capture_audit.py repeats this against the
    capture itself."""
    for code, function in (("329", 74), ("32A", 42), ("305", 80), ("30B", 104),
                           ("30D", 88)):
        assert hex_philips.FROM_DB_HEX["Thomson7"](code) == ("Thomson7", 12, None, function)


def test_every_database_thomson7_code_is_device_12():
    """What the reading above predicts for a remote with one address -- and what
    the app's reading, which takes the device from the low nibble, gives as
    thirteen different devices (and two commands, 12 and 76) for the same
    remote's 29 keys, 20 distinct frames in all."""
    records = _records("thomson7")
    for record in records:
        assert hex_philips.FROM_DB_HEX["Thomson7"](record["hex"])[1] == 12
    app_devices = {hex_philips.thomson7_as_the_app_sends(r["hex"])[0] for r in records}
    assert len(app_devices) == 13
    assert len({hex_philips.thomson7_as_the_app_sends(r["hex"]) for r in records}) == 20


def test_thomson7_bit_7_is_the_toggle_and_is_ignored():
    assert (hex_philips.FROM_DB_HEX["Thomson7"]("3A9")
            == hex_philips.FROM_DB_HEX["Thomson7"]("329"))


def test_the_app_ignores_hexcode_bit_4_and_the_ledger_does_not():
    """Two database keys, '1' (300) and '3' (310), differ only in bit 4. The
    app sends the same Thomson7 frame for both. The ledger sends two."""
    assert (hex_philips.thomson7_as_the_app_sends("300")
            == hex_philips.thomson7_as_the_app_sends("310"))
    assert (hex_philips.FROM_DB_HEX["Thomson7"]("300")
            != hex_philips.FROM_DB_HEX["Thomson7"]("310"))


# --- the oracle fixtures ---------------------------------------------------------

@pytest.mark.parametrize("name", ["rc5", "rc6", "rca_38"])
def test_the_ledger_compiles_the_signal_the_app_sends(name):
    records = _records(name)
    assert len(records) >= 20
    for record in records:
        result = oracle.compare(record)
        assert result.status == "matched", (record["hex"], result.reason)


def test_rc5_and_rca_38_match_the_app_duration_for_duration():
    for name in ("rc5", "rca_38"):
        for record in _records(name):
            assert "exact" in oracle.compare(record).notes, record["hex"]


def test_rc6_differs_from_the_app_only_in_its_final_space():
    """The app idles for RC-6's six-unit signal-free time; the IRP's ^107m makes
    the idle the rest of the frame period. Same marks and spaces up to there."""
    for record in _records("rc6"):
        app = record["pattern"]
        ledger = list(_ledger(record["hex"], "RC6", toggle=1).repeat)
        assert ledger[:-1] == app[:-1] or len(ledger) == len(app)
        assert ledger[:-2] == app[:-2]
        assert sum(ledger) == 107_000
        assert ledger[-1] > app[-1]
        assert app[-1] in (6 * 444, 7 * 444), "the app's idle is 2,664 us after a space"


def test_the_app_shows_toggle_1_for_rc5_and_rc6_and_0_for_thomson7():
    """The app's *preview* of a fresh state. The ledger compiles T=0 everywhere
    (D3b), so for RC5 and RC6 its compiled frame differs from the preview's in
    exactly the toggle bit."""
    for name in ("rc5", "rc6"):
        for record in _records(name):
            notes = oracle.compare(record).notes
            assert "app toggle=1" in notes
            assert "ledger T=0 differs from the app's frame (D3b)" in notes


def test_the_app_sends_the_thomson7_frame_twice_and_the_ledger_once():
    record = _records("thomson7")[0]
    half = len(record["pattern"]) // 2
    assert record["pattern"][:half] == record["pattern"][half:]
    assert len(_ledger(record["hex"], "Thomson7").repeat) == half


def test_thomson7_mismatches_the_app_for_a_stated_reason_on_every_code():
    """29 of 29: the ledger's frame is not the app's, and the app's is exactly
    the frame its own reading of the hexcode produces (for one toggle state,
    sent twice). Not a tolerance: no duration of any frame is compared away."""
    records = _records("thomson7")
    assert len(records) == 29
    for record in records:
        result = oracle.compare(record)
        assert result.status == "mismatched" and result.explained, record["hex"]
        assert "bits 3..0, toggle, bits 11..5" in result.reason


def test_thomson7_app_reading_accounts_for_the_apps_pattern_in_every_bit():
    """The explanation is exact. For the frame the app's own reading of the
    hexcode names, the ledger's encoding agrees with the app's pattern in every
    bit: its marks are 460 us against the IRP's 500, its long space 4,600
    against 4,500, and the short space is the same 2,000."""
    for record in _records("thomson7"):
        device, function = hex_philips.thomson7_as_the_app_sends(record["hex"])
        app = record["pattern"][: len(record["pattern"]) // 2]
        toggle = 0 if app[9] < 3000 else 1       # the app's T bit is the fifth space
        ledger = REGISTRY["Thomson7"].encode(
            device=device, subdevice=None, function=function, carrier_hz=33_000,
            toggle=toggle).repeat
        assert set(app[0:-1:2]) == {460}
        assert [4600 if s == 4500 else s for s in ledger[1:-1:2]] == app[1:-1:2], record["hex"]
