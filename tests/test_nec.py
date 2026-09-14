"""NEC1 invariants (D18 gate 3) and the committed regression snapshot.

These are the checks that catch broken *framing*. They cannot catch a
consistently wrong unit -- only a cited golden vector does that (D10), and
NEC1's is still PENDING; see tests/vectors/CITATIONS.md.
"""

from pathlib import Path

import pytest

from remote_ledger.errors import EncodeError
from remote_ledger.protocols import NEC1
from remote_ledger.pronto import encode, parse_words

VECTORS = Path(__file__).parent / "vectors"

# Topping RC-15A, Power. D and S are complements here (0x11 ^ 0xEE == 0xFF),
# which is the "nec-complement-check" SPEC section 1 cites for this remote.
TOPPING = dict(device=0x11, subdevice=0xEE, function=0x18, carrier_hz=38_000)


@pytest.fixture
def signal():
    return NEC1.encode(**TOPPING)


def test_registry_metadata_is_machine_readable():
    """D3: validation reads fields, never prose."""
    assert NEC1.unit_us == 564
    assert NEC1.extent_us == 108_000
    assert NEC1.bits == 32
    assert "D:8,S:8,F:8,~F:8" in NEC1.irp


def test_intro_pads_to_its_own_extent(signal):
    """D31: the extent sits inside a sequence, so each pads independently."""
    assert sum(signal.intro) == NEC1.extent_us


def test_repeat_pads_to_its_own_extent(signal):
    assert sum(signal.repeat) == NEC1.extent_us
    assert sum(signal.intro) + sum(signal.repeat) == 2 * NEC1.extent_us


def test_pair_counts(signal):
    """1 lead-in + 32 bits + 1 stop-and-gap = 34 pairs; repeat is 2."""
    words = parse_words(encode(signal))
    assert words[2] == 34
    assert words[3] == 2


def test_lead_in_is_units_multiplied_out(signal):
    assert signal.intro[0] == 16 * NEC1.unit_us == 9024
    assert signal.intro[1] == 8 * NEC1.unit_us == 4512


def test_repeat_shape(signal):
    assert signal.repeat[:3] == (16 * 564, 4 * 564, 564)


def _recover_bytes(signal):
    """Read the four bytes back out of the emitted burst words."""
    words = parse_words(encode(signal))
    body = words[4 : 4 + 68]
    bits = [1 if body[i + 1] == 0x40 else 0 for i in range(2, 66, 2)]
    return [
        sum(b << i for i, b in enumerate(bits[off : off + 8]))
        for off in (0, 8, 16, 24)
    ]


def test_field_layout_and_lsb_first_bit_order(signal):
    device, subdevice, function, complement = _recover_bytes(signal)
    assert device == 0x11
    assert subdevice == 0xEE
    assert function == 0x18
    assert complement == 0xE7


def test_function_complement_relation(signal):
    _, _, function, complement = _recover_bytes(signal)
    assert function ^ complement == 0xFF


@pytest.mark.parametrize(
    "params,match",
    [
        (dict(device=0x100, subdevice=0, function=0), "D:8 and F:8"),
        (dict(device=0, subdevice=0, function=0x100), "D:8 and F:8"),
        (dict(device=0, subdevice=0x100, function=0), "S:8 holds"),
        (dict(device=0, subdevice=None, function=0), "explicit subdevice"),
    ],
)
def test_parameter_bounds(params, match):
    with pytest.raises(EncodeError, match=match):
        NEC1.encode(carrier_hz=38_000, **params)


def test_absent_subdevice_names_the_backlogged_variant():
    """The `NEC` variant (S = ~D) is backlogged in D18, not silently assumed."""
    with pytest.raises(EncodeError, match="backlogged in D18"):
        NEC1.encode(device=0x11, subdevice=None, function=0x18, carrier_hz=38_000)


def test_oversized_unit_cannot_exceed_the_extent():
    """D31: an over-extent frame is an error, never a clamp."""
    with pytest.raises(EncodeError, match="Clamping would fabricate"):
        NEC1.encode(**{**TOPPING, "unit_us": 900})


def test_carrier_is_the_files_not_the_registrys(signal):
    """D3: nominal_carrier_hz is informational; the file's value is used."""
    assert NEC1.nominal_carrier_hz == 38_000
    at_40k = NEC1.encode(**{**TOPPING, "carrier_hz": 40_000})
    assert at_40k.carrier_hz == 40_000
    assert encode(at_40k).split()[1] == "0068"


def test_regression_snapshot(signal):
    """Self-derived: proves stability, not correctness (see CITATIONS.md)."""
    expected = (VECTORS / "nec1_topping_power.pronto").read_text().strip()
    assert encode(signal) == expected
