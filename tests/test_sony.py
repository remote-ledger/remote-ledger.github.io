"""Sony20 invariants (D18 gate 3).

Like NEC1's, these catch broken *framing*. Only a cited golden vector
catches a consistently wrong constant, and Sony20's is PENDING -- see
tests/vectors/CITATIONS.md.
"""

import pytest

from remote_ledger.errors import EncodeError
from remote_ledger.protocols import SONY20
from remote_ledger.pronto import encode, parse_words

# Sony BD addressing as SPEC section 1 records it.
BX510 = dict(device=26, subdevice=218, function=21, carrier_hz=40_000)


@pytest.fixture
def signal():
    return SONY20.encode(**BX510)


def test_registry_metadata(signal):
    assert SONY20.unit_us == 600
    assert SONY20.extent_us == 45_000
    assert SONY20.bits == 20
    assert "F:7,D:5,S:8" in SONY20.irp


def test_there_is_no_intro_sequence(signal):
    """Every Sony variant is a bare repeating frame, so D6 rule 6 emits
    NNNN = 0000 and puts everything in the second slot."""
    assert signal.intro == ()
    assert parse_words(encode(signal))[2] == 0


def test_pair_count_is_lead_in_plus_twenty_bits(signal):
    assert parse_words(encode(signal))[3] == 21


def test_frame_pads_to_its_extent(signal):
    assert sum(signal.repeat) == SONY20.extent_us


def test_the_extent_lengthens_the_final_space_rather_than_appending(signal):
    """The frame ends on a space because the last bit contributes one.
    Appending a gap would add a burst pair no Sony decoder expects."""
    assert len(signal.repeat) == 2 * 21
    assert signal.repeat[-1] > 600  # lengthened, not a bare unit


def test_lead_in_is_four_units_then_one(signal):
    assert signal.repeat[0] == 4 * SONY20.unit_us == 2400
    assert signal.repeat[1] == SONY20.unit_us == 600


def test_canonical_sony_words(signal):
    """0060 lead, 0018 unit, 0030 double unit -- the published spellings."""
    words = parse_words(encode(signal))
    assert f"{words[1]:04X}" == "0068"
    assert f"{words[4]:04X}" == "0060"
    assert f"{words[5]:04X}" == "0018"
    assert 0x0030 in words


def _recover(signal):
    body = parse_words(encode(signal))[4:]
    bits = [1 if body[i] == 0x0030 else 0 for i in range(2, len(body), 2)]
    out, off = {}, 0
    for name, width in (("function", 7), ("device", 5), ("subdevice", 8)):
        out[name] = sum(b << i for i, b in enumerate(bits[off:off + width]))
        off += width
    return out


def test_field_layout_is_function_device_subdevice_lsb_first(signal):
    """F:7,D:5,S:8 -- note the function comes FIRST, unlike NEC."""
    recovered = _recover(signal)
    assert recovered == {"function": 21, "device": 26, "subdevice": 218}


@pytest.mark.parametrize("subdevice", [218, 234, 242])
def test_the_bx510_alternate_subdevices_all_encode(subdevice):
    """SPEC section 1's three candidates. They must differ from each other --
    which is exactly why D16 forbids cross-checking candidates."""
    codes = {
        s: encode(SONY20.encode(**{**BX510, "subdevice": s}))
        for s in (218, 234, 242)
    }
    assert len(set(codes.values())) == 3


@pytest.mark.parametrize(
    "params,match",
    [
        (dict(function=128), "F:7 holds"),
        (dict(device=32), "D:5 holds"),
        (dict(subdevice=256), "S:8 holds"),
        (dict(subdevice=None), "Sony12 or Sony15"),
    ],
)
def test_parameter_bounds(params, match):
    with pytest.raises(EncodeError, match=match):
        SONY20.encode(**{**BX510, **params})


def test_device_field_is_five_bits_not_eight():
    """The error has to say so: Sony's eight-bit field is the SUBdevice, and
    passing an eight-bit device is the obvious mistake."""
    with pytest.raises(EncodeError, match="eight-bit field is the subdevice"):
        SONY20.encode(**{**BX510, "device": 200})


# --- Sony20 against the reference frame reader (SwiftRemote DB import) ------
#
# The three cases above recover fields from one frame. These sweep: the 20-bit
# space is 1,048,576 frames, so the address pair is swept exhaustively at one
# command, the command at one address pair, and every single field bit alone.

from sirc_reference import EXTENT, field, read_bits  # noqa: E402


def _bits(device, subdevice, function):
    return read_bits(SONY20.encode(device=device, subdevice=subdevice,
                                   function=function, carrier_hz=40_000).repeat)


def test_every_device_and_subdevice_round_trips_at_one_function():
    """8,192 address pairs; the 13 address bits are D (low five) then S."""
    for device in range(32):
        for subdevice in range(256):
            bits = _bits(device, subdevice, 0x55)
            assert bits is not None and len(bits) == 20, (device, subdevice)
            assert (field(bits, 0, 7), field(bits, 7, 5), field(bits, 12, 8)) == (
                0x55, device, subdevice)


def test_every_function_round_trips_at_the_bx510_address():
    for function in range(128):
        bits = _bits(26, 218, function)
        assert (field(bits, 0, 7), field(bits, 7, 5), field(bits, 12, 8)) == (
            function, 26, 218)


def test_each_field_bit_alone_lands_on_its_own_wire_position():
    for bit in range(7):
        assert _bits(0, 0, 1 << bit) == [int(i == bit) for i in range(20)]
    for bit in range(5):
        assert _bits(1 << bit, 0, 0) == [int(i == 7 + bit) for i in range(20)]
    for bit in range(8):
        assert _bits(0, 1 << bit, 0) == [int(i == 12 + bit) for i in range(20)]


def test_the_thirteen_bit_address_is_device_low_subdevice_high():
    """What the SwiftRemote app calls a 13-bit address, packed above the
    command, is D | S << 5 -- the split tools/irblaster_oracle_sony.py checks
    against the app's own output for all 1,213 Sony20 codes in its database."""
    for address in (0, 1, 0x1F, 0x20, 0x1FFF, 0x0ABC, 0x1A5A):
        bits = _bits(address & 0x1F, address >> 5, 0)
        assert field(bits, 7, 13) == address


def test_every_sony20_frame_pads_to_the_extent():
    for subdevice in range(256):
        assert sum(SONY20.encode(device=31, subdevice=subdevice, function=127,
                                 carrier_hz=40_000).repeat) == EXTENT
