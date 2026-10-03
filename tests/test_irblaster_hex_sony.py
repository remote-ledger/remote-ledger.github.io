"""The SwiftRemote database's Sony hexcodes (``irblaster/hex_sony.py``).

Two readings exist and they disagree (the module docstring says
why). These tests pin both, and pin the one the importer uses to the
ledger's own hardware-verified Sony remote and to the independent frame
reader, so the claim "the database writes a frame in transmission order" is
tested against something other than the mapping itself.
"""

import json
from pathlib import Path

import pytest

from remote_ledger.irblaster.hex_sony import FROM_DB_HEX, FROM_DB_HEX_APP, MIN_SENDS
from remote_ledger.protocols import REGISTRY
from sirc_reference import read_bits

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "irblaster"

BITS = {"SONY12": 12, "SONY15": 15, "SONY20": 20}
DIGITS = {"SONY12": 3, "SONY15": 4, "SONY20": 5}


def _db_hex(bits: list[int], digits: int) -> str:
    """How the database writes a frame: first-sent bit most significant,
    right-padded with zeros to whole digits. Written here from that
    description, not from hex_sony."""
    padded = bits + [0] * (digits * 4 - len(bits))
    return f"{int(''.join(map(str, padded)), 2):0{digits}X}"


def _frame_bits(protocol, **fields):
    return read_bits(REGISTRY[protocol].encode(carrier_hz=40_000, **fields).repeat)


def test_the_contract():
    assert set(FROM_DB_HEX) == set(FROM_DB_HEX_APP) == set(MIN_SENDS) == {"SONY12", "SONY15", "SONY20"}
    assert MIN_SENDS == {"SONY12": 3, "SONY15": 3, "SONY20": 3}
    for name, convert in FROM_DB_HEX.items():
        protocol, *_ = convert("0" * DIGITS[name])
        assert protocol in REGISTRY and protocol.lower() == name.lower()


@pytest.mark.parametrize("name", sorted(FROM_DB_HEX))
def test_min_sends_is_the_registered_sirc_repeat_count(name):
    """sony.py's docstring: SIRC wants three frames."""
    assert MIN_SENDS[name] == 3


# --- the database's reading (what the importer uses) ---------------------------

def test_tv_power_is_a90():
    """Sony's best-known 12-bit code: device 1, function 21. Also the hint
    text the app itself shows in its SONY12 field."""
    assert FROM_DB_HEX["SONY12"]("A90") == ("Sony12", 1, None, 21)
    assert FROM_DB_HEX["SONY12"]("a90") == ("Sony12", 1, None, 21)


def test_the_projector_codes_of_a_published_protocol_manual_read_as_documented():
    """Girr's sony_vlp_hw50es.girr (from the VPL-VW95ES protocol manual) gives
    the HW50ES power-on as Sony15 D=84 F=46; written as the database writes it
    and read back, it must give the same."""
    bits = _frame_bits("Sony15", device=84, subdevice=None, function=46)
    assert FROM_DB_HEX["SONY15"](_db_hex(bits, 4)) == ("Sony15", 84, None, 46)


def test_the_sony15_pad_bit_is_the_sixteenth():
    """15 bits in four digits: the last bit of the hex is padding."""
    bits = _frame_bits("Sony15", device=0xFF, subdevice=None, function=0x7F)
    assert _db_hex(bits, 4) == "FFFE"
    assert FROM_DB_HEX["SONY15"]("FFFE") == ("Sony15", 255, None, 127)
    with pytest.raises(ValueError, match="pad bit"):
        FROM_DB_HEX["SONY15"]("FFFF")


def test_sony20_splits_the_thirteen_address_bits_device_first():
    """F:7,D:5,S:8: the first five address bits sent are D, the next eight S."""
    bits = _frame_bits("Sony20", device=26, subdevice=226, function=21)
    assert FROM_DB_HEX["SONY20"](_db_hex(bits, 5)) == ("Sony20", 26, 226, 21)


def test_every_sony12_frame_round_trips_through_the_databases_spelling():
    for device in range(32):
        for function in range(128):
            bits = _frame_bits("Sony12", device=device, subdevice=None, function=function)
            assert FROM_DB_HEX["SONY12"](_db_hex(bits, 3)) == ("Sony12", device, None, function)


def test_every_sony15_frame_round_trips_through_the_databases_spelling():
    for device in range(256):
        for function in range(128):
            bits = _frame_bits("Sony15", device=device, subdevice=None, function=function)
            assert FROM_DB_HEX["SONY15"](_db_hex(bits, 4)) == ("Sony15", device, None, function)


def test_every_sony20_device_and_function_round_trips_and_a_subdevice_sweep():
    for device in range(32):
        for function in range(0, 128, 9):
            for subdevice in (0, 1, 2, 0x55, 0x80, 0xAA, 0xFF):
                bits = _frame_bits("Sony20", device=device, subdevice=subdevice, function=function)
                assert FROM_DB_HEX["SONY20"](_db_hex(bits, 5)) == (
                    "Sony20", device, subdevice, function)


def test_the_ledgers_hardware_verified_remote_is_what_the_database_says():
    """remotes/sony/RMT-B118P.json is a Sony20 remote verified against a
    hardware capture and a second source (device 26, subdevice 226). Every one
    of its 38 keys, written as the database writes a frame, reads back to the
    file's own device, subdevice and function."""
    remote = json.loads((ROOT / "remotes" / "sony" / "RMT-B118P.json").read_text())
    assert len(remote["keys"]) == 38
    for name, key in remote["keys"].items():
        form = key["forms"][0]
        device, subdevice, function = (int(form[k], 16) for k in ("device", "subdevice", "function"))
        bits = _frame_bits("Sony20", device=device, subdevice=subdevice, function=function)
        assert FROM_DB_HEX["SONY20"](_db_hex(bits, 5)) == ("Sony20", device, subdevice, function), name


def test_and_the_databases_own_rows_for_that_remote_are_in_the_fixture():
    """The ten anchor rows in the Sony20 fixture are the database's hex for
    keys of the verified remote: under the database's reading they are
    (26, 226, F) and under the app's they are not."""
    rows = json.loads((FIXTURES / "sony20.json").read_text())["rows"]
    anchors = [r for r in rows if r["hex"].endswith("B47")]
    assert len(anchors) >= 10
    for row in anchors:
        assert FROM_DB_HEX["SONY20"](row["hex"])[:3] == ("Sony20", 26, 226)
        assert FROM_DB_HEX_APP["SONY20"](row["hex"])[1:3] != (26, 226)


# --- the app's reading (what it transmits today; not for import) ---------------

def test_the_app_reads_a90_as_another_code():
    """cmd = low seven bits, address above them: 0xA90 is command 0x10 on
    address 0x15, not power."""
    assert FROM_DB_HEX_APP["SONY12"]("A90") == ("Sony12", 0x15, None, 0x10)


def test_the_app_splits_a_thirteen_bit_address_device_low():
    # 0x0002F is the app's own Universal Power default for SONY20.
    assert FROM_DB_HEX_APP["SONY20"]("0002F") == ("Sony20", 0, 0, 0x2F)
    # device 26, subdevice 226, function 21, packed as the app packs them
    assert FROM_DB_HEX_APP["SONY20"]("E2D15") == ("Sony20", 26, 226, 21)


def test_the_app_masks_a_sony15_hex_that_spells_sixteen_bits_and_we_refuse_it():
    with pytest.raises(ValueError, match="masks them away"):
        FROM_DB_HEX_APP["SONY15"]("8014")
    assert FROM_DB_HEX_APP["SONY15"]("0014") == ("Sony15", 0, None, 0x14)


# --- refusals, with text the importer can group by -----------------------------

@pytest.mark.parametrize("name", sorted(FROM_DB_HEX))
@pytest.mark.parametrize("reading", [FROM_DB_HEX, FROM_DB_HEX_APP], ids=["db", "app"])
def test_not_hex_is_refused_without_naming_the_code(name, reading):
    for bad in ("", "  ", "0xA90", "G00", "A9 0"):
        with pytest.raises(ValueError) as caught:
            reading[name](bad)
        assert "hexadecimal" in str(caught.value)
        assert bad.strip() not in str(caught.value) or bad.strip() == ""


@pytest.mark.parametrize("name,hexcode", [("SONY12", "A9"), ("SONY12", "A900"),
                                          ("SONY15", "FFE"), ("SONY15", "FFFE0"),
                                          ("SONY20", "A8B4"), ("SONY20", "A8B470")])
def test_the_database_reading_wants_exactly_the_digits_of_its_frame(name, hexcode):
    with pytest.raises(ValueError, match="digits"):
        FROM_DB_HEX[name](hexcode)


def test_refusal_text_is_stable_across_codes():
    messages = set()
    for bad in ("8014", "9999", "ABCD"):
        with pytest.raises(ValueError) as caught:
            FROM_DB_HEX_APP["SONY15"](bad)
        messages.add(str(caught.value))
    assert len(messages) == 1
    messages = set()
    for bad in ("0001", "FFFF", "1235"):
        with pytest.raises(ValueError) as caught:
            FROM_DB_HEX["SONY15"](bad)
        messages.add(str(caught.value))
    assert len(messages) == 1
