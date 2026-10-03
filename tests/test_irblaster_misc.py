"""The misc family's DB-hex maps, against the app's own signals.

``tests/fixtures/irblaster/*.json`` are samples of what the SwiftRemote app
transmits today, taken from the full Dart oracle run (``buildButtonFromDbRow``
then ``previewIRButton``) by ``tools/irblaster_oracle_misc.py
--write-fixtures``. The full run, 2,812 codes, is not committed; run the tool
against it for the counts in NOTES/misc.md.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from remote_ledger.irblaster import hex_misc
from remote_ledger.irblaster.hex_misc import (
    FROM_DB_HEX, MIN_SENDS, proton_wire_order,
)
from remote_ledger.protocols import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "irblaster"

_spec = importlib.util.spec_from_file_location(
    "irblaster_oracle_misc", ROOT / "tools" / "irblaster_oracle_misc.py")
oracle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oracle)

STEMS = {"Samsung36": "samsung36", "Proton": "proton", "F12_relaxed": "f12_relaxed",
         "RECS80": "recs80", "RECS80_L": "recs80_l"}


def _fixture(db_protocol):
    doc = json.loads((FIXTURES / f"{STEMS[db_protocol]}.json").read_text())
    assert doc["dbProtocol"] == db_protocol
    return [{"hex": c["hex"], "freq": c["freq"], "pattern": c["pattern"]}
            for c in doc["codes"]]


# --- the contract ------------------------------------------------------------

def test_the_module_exports_the_agreed_names():
    assert set(FROM_DB_HEX) == set(STEMS)
    assert set(MIN_SENDS) <= set(FROM_DB_HEX)
    assert all(1 <= n <= 10 for n in MIN_SENDS.values())


def test_every_mapping_lands_on_a_registered_protocol():
    samples = {"Samsung36": "0400E18", "Proton": "2880", "F12_relaxed": "A84",
               "RECS80": "AA8", "RECS80_L": "F30"}
    for db_protocol, hexcode in samples.items():
        name, device, subdevice, function = FROM_DB_HEX[db_protocol](hexcode)
        assert name in REGISTRY
        assert isinstance(device, int) and isinstance(function, int)
        assert subdevice is None or isinstance(subdevice, int)
        REGISTRY[name].encode(device=device, subdevice=subdevice, function=function,
                              carrier_hz=REGISTRY[name].nominal_carrier_hz)


def test_recs80_l_is_the_0068_definition_not_a_carrier_variant():
    """33.3 kHz is only the carrier. The unit (180 us against 158 us) and the
    ending (a 138 ms extent against a plain 45 ms gap) are different too."""
    assert FROM_DB_HEX["RECS80"]("AA8")[0] == "RECS80"
    assert FROM_DB_HEX["RECS80_L"]("AA8")[0] == "RECS80-0068"
    assert REGISTRY["RECS80"].unit_us != REGISTRY["RECS80-0068"].unit_us
    assert REGISTRY["RECS80"].extent_us is None
    assert REGISTRY["RECS80-0068"].extent_us == 138_000


# --- the app's signals -------------------------------------------------------

@pytest.mark.parametrize("db_protocol", sorted(STEMS))
def test_the_ledger_compiles_the_signal_the_app_sends(db_protocol):
    rows = _fixture(db_protocol)
    assert len(rows) >= 30
    for row in rows:
        verdict, detail = oracle.compare(db_protocol, row)
        if db_protocol == "Samsung36":
            # The one explained disagreement: the lead-out. Everything else
            # matched, at the registry's own 560 us unit.
            assert verdict == "matched-leadout", (row["hex"], detail)
        else:
            assert verdict == "matched", (row["hex"], detail)


def test_samsung36_at_the_registry_unit_is_exactly_at_the_tolerance():
    """Not a comfortable pass: the IRP's 560 us against the app's 500 us is
    12.0 % on every bit mark, and the 1,680 us one-space against 1,500 us and
    the 5,040 us divider against 4,500 us are 12.0 % too. If the registry's
    unit ever moved one microsecond further, this would be a mismatch."""
    worst = 0.0
    for row in _fixture("Samsung36"):
        _, detail = oracle.compare("Samsung36", row)
        worst = max(worst, detail["worst"])
    assert worst == pytest.approx(0.12)


def test_samsung36_with_a_500us_unit_is_the_apps_signal_but_for_the_leadout():
    """The remote file's ``unitUs`` (D24) is how the ledger says what the app
    and the captures measure. Then every duration but the lead-out is equal."""
    for row in _fixture("Samsung36"):
        name, device, subdevice, function = FROM_DB_HEX["Samsung36"](row["hex"])
        signal = REGISTRY[name].encode(device=device, subdevice=subdevice,
                                       function=function, carrier_hz=38_000,
                                       unit_us=500)
        assert list(signal.repeat[:-1]) == row["pattern"][:-1], row["hex"]


def test_samsung36_leadout_is_the_only_disagreement_and_it_is_the_irps_extent():
    for row in _fixture("Samsung36"):
        _, durations, _ = oracle.compile_ledger("Samsung36", row["hex"], None)
        assert row["pattern"][-1] == 59_000
        assert sum(durations) == REGISTRY["Samsung36"].extent_us == 108_000
        assert durations[-1] != 59_000


@pytest.mark.parametrize("db_protocol", ["RECS80", "RECS80_L"])
def test_the_ledgers_toggle_zero_differs_from_the_apps_preview_only_in_the_toggle(db_protocol):
    """The app's preview is T=1 (a static that starts False and flips before
    use); a remote file carries T=0 (D3b). The two signals are the same but
    for the toggle bit's space, 47 units against 31 -- and, for RECS80_L, the
    lead-out, which an extent makes absorb that difference so the frame still
    totals 138 ms. The app computes its lead-out the same way (``138000 -
    sum``), so its own T=0 frame would be the ledger's."""
    for row in _fixture(db_protocol):
        name, device, subdevice, function = FROM_DB_HEX[db_protocol](row["hex"])
        entry = REGISTRY[name]
        t0 = list(entry.encode(device=device, subdevice=subdevice, function=function,
                               carrier_hz=entry.nominal_carrier_hz).repeat)
        app = row["pattern"]
        unit = entry.unit_us
        assert app[3] == 47 * unit
        assert t0[:3] == app[:3] and t0[4:-1] == app[4:-1], row["hex"]
        assert t0[3] == 31 * unit
        if entry.extent_us is None:
            assert t0[-1] == app[-1]                      # a plain 45 ms gap
        else:
            assert t0[-1] == app[-1] + 16 * unit          # the extent absorbs it
            assert sum(t0) == sum(app) == entry.extent_us


# --- the DB's own contents agree with real hardware --------------------------

#: IrpTransmogrifier @c945e76 src/test/teaserfiles/Samsung36.exp: a Samsung
#: Blu-ray remote, every key Samsung36 {D=32,S=0,E=7}, with this F. Paired with
#: the SwiftRemote DB's hexcode for the same-named key on its Samsung BD remotes
#: (remote ids 159, 2665, 5249: AK59-00179A, UBD-K8500 ...).
SAMSUNG_BD = {
    "0400E18": ("UP", 24),
    "0400E98": ("DOWN", 25),
    "0400ED8": ("LEFT", 27),
    "0400E58": ("RIGHT", 26),
    "0400E38": ("OK", 28),
    "0400E28": ("PLAY", 20),
    "0400E48": ("REW", 18),
}


@pytest.mark.parametrize("hexcode", sorted(SAMSUNG_BD))
def test_samsung36_hexcodes_decode_to_the_published_capture(hexcode):
    """Bit order, the S byte and the E nibble, checked against a capture that
    is not ours: ``E=7`` is nibble ``E``, ``D=32`` is byte ``04``."""
    _, f = SAMSUNG_BD[hexcode]
    assert FROM_DB_HEX["Samsung36"](hexcode) == ("Samsung36", 32, 0, (7 << 8) | f)


#: IrpTransmogrifier @c945e76 src/test/teaserfiles/Proton.exp: a Proton TV
#: remote, every key Proton {D=20}, with this F. Paired with SwiftRemote DB
#: remote 18's hexcode for the key it is named after (digits; P+ P- VOL- VOL+
#: and NORMAL/OK for up, down, left, right, OK).
PROTON_TV = {
    "2800": ("0", 0), "2880": ("1", 1), "2810": ("8", 8), "2890": ("9", 9),
    "28E8": ("P+", 23), "2818": ("P-", 24), "2828": ("VOL-", 20),
    "28C8": ("VOL+", 19), "28E4": ("NORMAL / OK", 39),
}


@pytest.mark.parametrize("hexcode", sorted(PROTON_TV))
def test_proton_read_high_byte_first_is_the_published_capture(hexcode):
    """All nine keys of the capture are in one DB remote and the DB reads them
    in wire order: high byte (rev8(20) = 0x28) first. This is the reading the
    capture's own frame has."""
    _, f = PROTON_TV[hexcode]
    assert proton_wire_order(hexcode) == ("Proton", 20, None, f)


@pytest.mark.parametrize("hexcode", sorted(PROTON_TV))
def test_proton_as_the_app_sends_it_is_the_other_way_round(hexcode):
    """The finding, pinned (NOTES/misc.md): the app sends the low byte first,
    so ``FROM_DB_HEX['Proton']`` -- which must reproduce the app's signal --
    puts the address in F and the key in D, which is not what the real remote
    sends."""
    _, f = PROTON_TV[hexcode]
    name, device, subdevice, function = FROM_DB_HEX["Proton"](hexcode)
    assert (device, function) == (hex_misc._rev(int(hexcode[2:], 16), 8), 20)
    assert (device, function) != (20, f) or f == 20


# --- unrepresentable codes -----------------------------------------------------

@pytest.mark.parametrize(
    "db_protocol, hexcode",
    [("Samsung36", "0400E1"), ("Samsung36", "0400E18A"), ("Samsung36", "0400E1G"),
     ("Proton", "288"), ("Proton", "28800"), ("Proton", "28G0"),
     ("F12_relaxed", ""), ("F12_relaxed", "A840"), ("F12_relaxed", "A8G"),
     ("RECS80", "AA"), ("RECS80", "AA80"), ("RECS80", "AAG"),
     ("RECS80_L", "F3"), ("RECS80_L", "F300")],
)
def test_a_malformed_hexcode_is_refused_with_a_stable_reason(db_protocol, hexcode):
    with pytest.raises(ValueError) as exc:
        FROM_DB_HEX[db_protocol](hexcode)
    assert db_protocol in str(exc.value)
    assert hexcode not in str(exc.value) or hexcode == ""   # no hexcode in the text


@pytest.mark.parametrize("db_protocol", ["RECS80", "RECS80_L"])
def test_recs80_bits_below_the_nine_it_carries_are_unrepresentable(db_protocol):
    """The app drops them silently, so two hexcodes would give one signal. None
    of the DB's 555 RECS80 and RECS80_L codes has any (NOTES/misc.md)."""
    for hexcode in ("AA9", "AAA", "AAC", "AAF", "001"):
        with pytest.raises(ValueError, match="top nine"):
            FROM_DB_HEX[db_protocol](hexcode)
    reasons = set()
    for hexcode in ("AA9", "AAF"):
        try:
            FROM_DB_HEX[db_protocol](hexcode)
        except ValueError as exc:
            reasons.add(str(exc))
    assert len(reasons) == 1                    # one reason, so it groups


def test_every_fixture_code_is_representable():
    for db_protocol in STEMS:
        for row in _fixture(db_protocol):
            FROM_DB_HEX[db_protocol](row["hex"])


def test_f12_relaxed_accepts_short_hexcodes_as_numbers():
    """The app parses the hex as an integer and left-pads to 12 bits, so the
    DB's 11 two-digit codes and one one-digit code are numbers, not 12-bit
    strings with missing digits."""
    assert FROM_DB_HEX["F12_relaxed"]("A8") == FROM_DB_HEX["F12_relaxed"]("0A8")
    assert FROM_DB_HEX["F12_relaxed"]("5") == FROM_DB_HEX["F12_relaxed"]("005")


def test_the_fixtures_cover_what_matters():
    e_nibbles = {c["hex"][4] for c in _fixture("Samsung36")}
    assert e_nibbles == set("018FE4B37")           # every E nibble the DB uses
    assert {c["hex"][2] for c in _fixture("RECS80")} == {"0", "8"}
