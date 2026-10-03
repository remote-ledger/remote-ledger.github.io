"""The japan family's DB hex maps, and the oracle comparison against SwiftRemote.

``tests/fixtures/irblaster/<protocol>.json`` holds a few dozen codes per
protocol with what the app's own code transmits for each (the oracle:
``buildButtonFromDbRow`` then ``previewIRButton``). The full comparison over
every distinct code is ``tools/irblaster_oracle_japan.py``; the numbers are in
NOTES/japan.md.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from remote_ledger.irblaster import hex_japan as H
from remote_ledger.protocols import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "irblaster"
NAMES = ("Pioneer", "JVC", "Sharp", "Denon")

_spec = importlib.util.spec_from_file_location(
    "irblaster_oracle_japan", ROOT / "tools" / "irblaster_oracle_japan.py"
)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _fixture(name):
    return json.loads((FIXTURES / f"{name.lower()}.json").read_text())["codes"]


def _results(name, table=None):
    table = table or H.FROM_DB_HEX
    for row in _fixture(name):
        proto_name, device, subdevice, function = table[name](row["hex"])
        signal = REGISTRY[proto_name].encode(
            device=device, subdevice=subdevice, function=function,
            carrier_hz=tool.CARRIER_HZ[proto_name],
        )
        yield row, tool.compare(row["pattern"], signal)


# --- the contract ---------------------------------------------------------------


def test_the_module_exports_what_the_importer_loads():
    assert set(H.FROM_DB_HEX) == set(NAMES)
    assert set(H.FROM_DB_HEX_WIRE) == set(NAMES)
    assert H.MIN_SENDS == {n: 1 for n in NAMES}
    for name in NAMES:
        proto, device, subdevice, function = H.FROM_DB_HEX[name](
            {"Pioneer": "552BF504", "JVC": "C03F", "Sharp": "8344", "Denon": "1408"}[name]
        )
        assert proto in REGISTRY
        assert subdevice is None and isinstance(device, int) and isinstance(function, int)


# --- the app's reading (FROM_DB_HEX) -------------------------------------------


@pytest.mark.parametrize(
    "name, hexcode, expected",
    [
        # address, command, secondary address, secondary command; the pairs pack
        ("Pioneer", "552BF504", ("Pioneer-2Part", 0x55F5, None, 0x2B04)),
        ("Pioneer", "00080008", ("Pioneer-2Part", 0x0000, None, 0x0808)),
        # D then F, taken as they are
        ("JVC", "C03F", ("JVC", 0xC0, None, 0x3F)),
        # packed & 0x1FFF: address bits 12-8, command bits 7-0; the top 3 bits go
        ("Sharp", "8344", ("Sharp", 3, None, 0x44)),
        ("Sharp", "FFFF", ("Sharp", 0x1F, None, 0xFF)),
        # twelve bits from the first three nibbles, the 13th from hex bit 0
        ("Denon", "1408", ("Denon", 8, None, 1)),
        ("Denon", "1409", ("Denon", 8, None, 129)),
    ],
)
def test_the_apps_reading_of_a_code(name, hexcode, expected):
    assert H.FROM_DB_HEX[name](hexcode) == expected


def test_the_app_cleans_a_hexcode_before_reading_it():
    """``_cleanHex``: trimmed, upper-cased, everything but 0-9A-F dropped."""
    assert H.FROM_DB_HEX["JVC"](" c0-3f ") == H.FROM_DB_HEX["JVC"]("C03F")


def test_jvc_and_denon_keep_the_last_four_digits_of_a_longer_code():
    """The app's 4-digit field truncates from the left; Sharp's does not."""
    assert H.FROM_DB_HEX["JVC"]("FFC03F") == H.FROM_DB_HEX["JVC"]("C03F")
    assert H.FROM_DB_HEX["Denon"]("991408") == H.FROM_DB_HEX["Denon"]("1408")
    with pytest.raises(ValueError, match="Sharp hexcode is not 4 hex digits"):
        H.FROM_DB_HEX["Sharp"]("018344")


@pytest.mark.parametrize("table", ["FROM_DB_HEX", "FROM_DB_HEX_WIRE"])
@pytest.mark.parametrize(
    "name, hexcode, message",
    [
        ("Pioneer", "552BF5", "Pioneer hexcode is not 8 hex digits"),
        ("Pioneer", "552BF50412", "Pioneer hexcode is not 8 hex digits"),
        ("JVC", "C03", "JVC hexcode is not 4 hex digits"),
        ("Sharp", "834", "Sharp hexcode is not 4 hex digits"),
        ("Denon", "140", "Denon hexcode is not 4 hex digits"),
        ("JVC", "", "JVC hexcode is empty or has no hex digits"),
        ("Denon", "zz", "Denon hexcode is empty or has no hex digits"),
    ],
)
def test_a_code_the_app_cannot_send_is_refused_with_a_stable_reason(table, name, hexcode, message):
    with pytest.raises(ValueError) as caught:
        getattr(H, table)[name](hexcode)
    assert str(caught.value) == message
    if hexcode:
        assert hexcode not in message


# --- the reading the evidence supports (FROM_DB_HEX_WIRE) -----------------------


@pytest.mark.parametrize(
    "name, hexcode, published",
    [
        # IrpTransmogrifier's own decodes of real captures (teaser files @c945e76),
        # whose DB codes are the bit strings in wire order
        ("Pioneer", "55DAF524", ("Pioneer-2Part", (170 << 8) | 175, None, (91 << 8) | 36)),
        ("Pioneer", "55DAF544", ("Pioneer-2Part", (170 << 8) | 175, None, (91 << 8) | 34)),
        ("Sharp", "8344", ("Sharp", 1, None, 22)),          # 'Power'
        ("Sharp", "8644", ("Sharp", 1, None, 19)),          # 'Input Cycle'
        ("Denon", "17A8", ("Denon", 8, None, 175)),         # 'left'
        ("Denon", "1408", ("Denon", 8, None, 129)),         # '0'
        ("Denon", "16E8", ("Denon", 8, None, 187)),         # 'OK'
    ],
)
def test_the_wire_reading_gives_the_published_decode_and_the_apps_reading_does_not(
    name, hexcode, published
):
    """Each of these codes is in the database. Read the wire way it is exactly
    what IrpTransmogrifier decodes from a real remote; read the app's way it is
    something else."""
    assert H.FROM_DB_HEX_WIRE[name](hexcode) == published
    assert H.FROM_DB_HEX[name](hexcode) != published


def test_jvc_wire_reading_reverses_each_byte():
    assert H.FROM_DB_HEX_WIRE["JVC"]("C03F") == ("JVC", 0x03, None, 0xFC)


def test_sharp_wire_reading_of_a_complement_frame_inverts_the_function():
    """A trailer of ``2:2`` marks a code recorded from the inverted half; the
    function is then the complement of the bits."""
    assert H.FROM_DB_HEX_WIRE["Sharp"]("8344") == ("Sharp", 1, None, 22)
    assert H.FROM_DB_HEX_WIRE["Sharp"]("8342") == ("Sharp", 1, None, 22 ^ 0xFF)


@pytest.mark.parametrize(
    "hexcode, message",
    [
        ("8345", "Sharp hexcode has its pad bit set"),
        ("8347", "Sharp hexcode has its pad bit set"),
        ("8340", "Sharp hexcode trailer bits are neither 1:2 nor 2:2"),
        ("8346", "Sharp hexcode trailer bits are neither 1:2 nor 2:2"),
    ],
)
def test_sharp_wire_reading_refuses_a_code_without_a_known_trailer(hexcode, message):
    with pytest.raises(ValueError) as caught:
        H.FROM_DB_HEX_WIRE["Sharp"](hexcode)
    assert str(caught.value) == message


def test_denon_wire_reading_refuses_a_set_last_bit():
    with pytest.raises(ValueError, match="Denon hexcode has its last bit set"):
        H.FROM_DB_HEX_WIRE["Denon"]("1409")


# --- the oracle: the app's reading against what the app transmits -----------------


@pytest.mark.parametrize("name", NAMES)
def test_the_apps_reading_reproduces_the_apps_signal_on_the_fixture_codes(name):
    kinds = [r["kind"] for _, r in _results(name)]
    assert kinds and tool.MISMATCHED not in kinds
    assert set(kinds) <= {tool.MATCHED, tool.MATCHED_GAPS}


def test_pioneer_differs_from_the_app_only_in_the_gaps_and_by_the_repeat():
    """Every code: the app's marks and spaces are within 11.3 % of the IRP's
    (500 against 564 us, 1500 against 1692), its 26 ms gap 19.5 % above the
    IRP's 21,756 us, and it sends the intro only."""
    results = [r for _, r in _results("Pioneer")]
    assert {r["kind"] for r in results} == {tool.MATCHED_GAPS}
    assert all(r["omits_repeat"] for r in results)
    assert max(r["worst_relative"] for r in results) == pytest.approx(0.1135, abs=1e-3)
    assert {round(g, 3) for r in results for g in r["gap_ratios"]} == {1.195}


def test_jvc_differs_from_the_app_in_the_repeat_and_sometimes_the_gap():
    """The app sends the intro and no repeat frame. Its fixed 21 ms gap is the
    data-independent stand-in for the IRP's fixed 59.08 ms extent, so it
    matches when the frame has about half its bits set and not otherwise."""
    results = [r for _, r in _results("JVC")]
    assert all(r["omits_repeat"] for r in results)
    assert {r["kind"] for r in results} == {tool.MATCHED, tool.MATCHED_GAPS}
    assert max(r["worst_relative"] for r in results) < 0.01


@pytest.mark.parametrize("name", ["Sharp", "Denon"])
def test_sharp_and_denon_send_the_whole_signal(name):
    results = [r for _, r in _results(name)]
    assert not any(r["omits_repeat"] for r in results)
    assert max(r["worst_relative"] for r in results) < 0.08


@pytest.mark.parametrize("name", ["Pioneer", "JVC", "Sharp"])
def test_the_wire_reading_differs_from_the_app_on_most_codes(name):
    """The point of the alternative reading: it is *not* what the app sends."""
    kinds = [r["kind"] for _, r in _results(name, H.FROM_DB_HEX_WIRE)]
    assert kinds.count(tool.MISMATCHED) > len(kinds) * 0.9


def test_denon_wire_reading_differs_exactly_where_the_app_drops_a_bit():
    """The app's thirteenth bit is hex bit 0, the data's is hex bit 3, so a code
    differs exactly when hex bit 3 is set (a fourth digit of 8 or E)."""
    for row, result in _results("Denon", H.FROM_DB_HEX_WIRE):
        differs = result["kind"] == tool.MISMATCHED
        assert differs == (row["hex"][3] in "8E"), row["hex"]


# --- the tool ------------------------------------------------------------------


def _oracle_dir(tmp_path, mutate=None):
    (tmp_path / "by_protocol").mkdir()
    for name in NAMES:
        rows = [dict(r, protocol=name) for r in _fixture(name)]
        if mutate:
            mutate(name, rows)
        (tmp_path / "by_protocol" / f"{name}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows)
        )
    return tmp_path


def test_the_tool_exits_zero_when_every_code_matches(tmp_path, capsys):
    status = tool.main(["--oracle", str(_oracle_dir(tmp_path))])
    out = capsys.readouterr().out
    assert status == 0
    assert "mismatched" in out and "Pioneer" in out


def test_the_tool_exits_nonzero_on_a_mismatch(tmp_path, capsys):
    def corrupt(name, rows):
        if name == "JVC":
            rows[0]["pattern"][5] *= 3          # a one-space becomes a zero-space's triple

    status = tool.main(["--oracle", str(_oracle_dir(tmp_path, corrupt))])
    assert status == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_strict_gaps_turns_a_gap_difference_into_a_failure(tmp_path):
    oracle = _oracle_dir(tmp_path)
    assert tool.main(["--oracle", str(oracle)]) == 0
    assert tool.main(["--oracle", str(oracle), "--strict-gaps"]) == 1


def test_the_tool_counts_an_unrepresentable_code_with_its_reason(tmp_path, capsys):
    def bad_hex(name, rows):
        if name == "Sharp":
            rows[0]["hex"] = "83"

    status = tool.main(["--oracle", str(_oracle_dir(tmp_path, bad_hex))])
    out = capsys.readouterr().out
    assert status == 0
    assert "unrepresentable x1: Sharp hexcode is not 4 hex digits" in out


def test_wire_reading_never_fails_the_tool(tmp_path):
    assert tool.main(["--oracle", str(_oracle_dir(tmp_path)), "--reading", "wire"]) == 0
