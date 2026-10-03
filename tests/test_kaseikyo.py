"""The Kaseikyo-family protocols' invariants (D18 gate 3).

Panasonic, JVC-48, Fujitsu, Teac-K, Denon-K and SharpDVD. What anchors the
constants to the outside world is outside this file: the cited golden vectors
(tests/vectors/CITATIONS.md) and the hardware captures checked below.

The decoder is written from the frame layouts in IrpTransmogrifier's IRP
strings, not from the encoder: it recovers six LSB-first bytes and each test
states what those bytes must be for the protocol under test.
"""

import random

import pytest

from irpt_captures import capture, disagreements
from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import REGISTRY
from remote_ledger.protocols.kaseikyo import (
    DENON_K, FAMILY, FUJITSU, JVC48, PANASONIC, SHARP_DVD, TEAC_K,
)

CARRIER = 37_000


def _bytes(frame, unit, gap_units):
    """A 100-duration Kaseikyo-family frame -> six LSB-first bytes, or None.

    Header ``8,-4``; forty-eight ``1,-1`` / ``1,-3`` bits; a stop mark ``1``;
    then exactly ``gap_units`` of space.
    """
    if len(frame) != 100 or (frame[0], frame[1]) != (8 * unit, 4 * unit):
        return None
    bits = []
    for i in range(2, 98, 2):
        if frame[i] != unit or frame[i + 1] not in (unit, 3 * unit):
            return None
        bits.append(1 if frame[i + 1] == 3 * unit else 0)
    if (frame[98], frame[99]) != (unit, gap_units * unit):
        return None
    return [sum(bits[8 * j + k] << k for k in range(8)) for j in range(6)]


def _sub(name, **kw):
    return REGISTRY[name].encode(carrier_hz=CARRIER, **kw)


# --- the expected bytes, per the IRP strings ---------------------------------------


def panasonic_bytes(d, s, f):
    return [2, 32, d, s, f, d ^ s ^ f]


def jvc48_bytes(d, s, f):
    return [3, 1, d, s, f, d ^ s ^ f]


def fujitsu_bytes(d, s, f, e=0):
    return [20, 99, 0 | (e << 4), d, s, f]


def teack_bytes(d, s, f, x=1):
    t = d + (s & 15) + (s >> 4) + (f & 15) + (f >> 4)
    return [67, 83, x | (d << 4), s, f, t]


def denonk_bytes(d, s, f):
    chk = ((d * 16) ^ s ^ (f * 16) ^ ((f >> 4) & 0xFF)) & 0xFF
    return [84, 50, 0 | (d << 4), s | ((f & 15) << 4), (f >> 4) & 0xFF, chk]


def sharpdvd_bytes(d, s, f, e=1):
    c = d ^ (s & 15) ^ (s >> 4) ^ (f & 15) ^ (f >> 4) ^ e
    return [170, 90, 15 | (d << 4), s, f, e | (c << 4)]


SPEC = {
    # name: (expected bytes, unit, gap units, d range, s range, f range, repeat-only)
    "Panasonic": (panasonic_bytes, 432, 173, 256, 256, 256),
    "JVC-48": (jvc48_bytes, 432, 173, 256, 256, 256),
    "Fujitsu": (fujitsu_bytes, 432, 110, 256, 256, 256),
    "Teac-K": (teack_bytes, 432, 100, 16, 256, 256),
    "Denon-K": (denonk_bytes, 432, 173, 16, 16, 4096),
    "SharpDVD": (sharpdvd_bytes, 400, 48, 16, 256, 256),
}
NAMES = sorted(SPEC)


def _frame_of(signal):
    """The one frame: the intro for Teac-K, the repeat for the rest."""
    return signal.intro if signal.intro else signal.repeat


def _combos(nd, ns, nf):
    """Every value of each field with the others at fixed awkward values,
    plus a seeded random sample of the whole space."""
    rng = random.Random(48)
    seen = set()
    for fixed in ((0, 0, 0), (nd - 1, ns - 1, nf - 1), (nd // 2, ns // 3, nf // 5)):
        d0, s0, f0 = fixed
        for d in range(nd):
            seen.add((d, s0, f0))
        for s in range(ns):
            seen.add((d0, s, f0))
        for f in range(nf):
            seen.add((d0, s0, f))
    for _ in range(20_000):
        seen.add((rng.randrange(nd), rng.randrange(ns), rng.randrange(nf)))
    return sorted(seen)


# --- metadata ---------------------------------------------------------------------------


def test_the_family_is_exactly_these_six():
    assert sorted(p.name for p in FAMILY) == sorted(NAMES)
    assert all(REGISTRY[p.name] is p for p in FAMILY)


@pytest.mark.parametrize("name", NAMES)
def test_registry_metadata(name):
    proto = REGISTRY[name]
    _, unit, _, *_ = SPEC[name]
    assert proto.unit_us == unit
    assert proto.bits == 48
    # none of the six writes `^`, so there is no extent for D31 to substitute
    assert proto.extent_us is None
    assert "(8,-4," in proto.irp
    assert "<1,-1|1,-3>" in proto.irp
    assert proto.nominal_carrier_hz == (38_000 if name == "SharpDVD" else 37_000)


# --- layout ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_every_field_and_a_random_sample_round_trips(name):
    """Single-field sweeps against awkward fixed values, plus 20,000 seeded
    random frames. The full spaces (16 M for Panasonic, 1 M for Denon-K) are
    too large for pure Python, but every field is swept completely, which is
    where a swapped bit order or a mis-set nibble boundary shows."""
    expected, unit, gap, nd, ns, nf = SPEC[name]
    for d, s, f in _combos(nd, ns, nf):
        frame = _frame_of(_sub(name, device=d, subdevice=s, function=f))
        got = _bytes(frame, unit, gap)
        assert got == expected(d, s, f), (name, d, s, f)


@pytest.mark.parametrize("name", NAMES)
def test_the_frame_is_a_complete_sequence(name):
    _, unit, gap, *_ = SPEC[name]
    signal = _sub(name, device=1, subdevice=2, function=3)
    frame = _frame_of(signal)
    assert len(frame) == 100 and len(frame) % 2 == 0
    # header, then marks of one unit throughout, then stop mark and gap
    assert frame[0] == 8 * unit and frame[1] == 4 * unit
    assert set(frame[2:98:2]) == {unit}
    assert frame[98] == unit and frame[99] == gap * unit


@pytest.mark.parametrize("name", [n for n in NAMES if n != "Teac-K"])
def test_all_but_teac_k_put_the_whole_frame_in_the_repeat(name):
    """Their IRP ends in ``)*``: no intro, D6 rule 6 emits ``NNNN = 0000``."""
    signal = _sub(name, device=1, subdevice=2, function=3)
    assert signal.intro == ()
    assert pronto.parse_words(pronto.encode(signal))[2] == 0


def test_teac_k_sends_the_frame_once_then_a_shorter_repeat():
    """``(8,-4,...,1,-100,(8,-8,1,-100)*)``: an 8-unit mark and 8-unit space
    with no data, a stop mark and the same gap."""
    signal = _sub("Teac-K", device=0, subdevice=4, function=19)
    assert len(signal.intro) == 100
    assert signal.repeat == (8 * 432, 8 * 432, 432, 100 * 432)
    words = pronto.parse_words(pronto.encode(signal))
    assert (words[2], words[3]) == (50, 2)


# --- what is peculiar to each --------------------------------------------------------


def test_panasonic_check_byte_is_the_xor_of_the_three_fields():
    for d, s, f in ((0, 0, 0), (1, 2, 4), (255, 255, 255), (176, 16, 17)):
        frame = _frame_of(_sub("Panasonic", device=d, subdevice=s, function=f))
        assert _bytes(frame, 432, 173)[5] == d ^ s ^ f


def test_jvc48_differs_from_panasonic_only_in_its_vendor_bytes():
    a = _bytes(_frame_of(_sub("Panasonic", device=34, subdevice=33, function=12)), 432, 173)
    b = _bytes(_frame_of(_sub("JVC-48", device=34, subdevice=33, function=12)), 432, 173)
    assert a[:2] == [2, 32] and b[:2] == [3, 1]
    assert a[2:] == b[2:]


def test_fujitsu_has_no_check_byte_and_its_sixth_byte_is_the_function():
    frame = _frame_of(_sub("Fujitsu", device=132, subdevice=132, function=0xA5))
    assert _bytes(frame, 432, 110) == [20, 99, 0, 132, 132, 0xA5]


def test_teac_k_check_byte_sums_four_nibbles_and_the_device():
    """``T=D+S:4:0+S:4:4+F:4:0+F:4:4``, an arithmetic sum, not an XOR: it
    reaches 75, which an eight-bit field holds without truncation."""
    frame = _frame_of(_sub("Teac-K", device=15, subdevice=255, function=255))
    assert _bytes(frame, 432, 100)[5] == 15 + 15 * 4


def test_denon_k_function_straddles_two_bytes():
    """``S:4,F:12``: S fills the low nibble of byte 3, F's low four bits its
    high nibble, and F's other eight bits all of byte 4."""
    frame = _frame_of(_sub("Denon-K", device=4, subdevice=1, function=0xABC))
    got = _bytes(frame, 432, 173)
    assert got[3] == 1 | (0xC << 4)
    assert got[4] == 0xAB


def test_denon_k_check_byte_truncates_the_whole_xor():
    """The IRP's ``(...):8`` truncates after the XOR, so ``F*16``'s bits above
    the eighth do not leak into the check."""
    d, s, f = 15, 15, 0xFFF
    frame = _frame_of(_sub("Denon-K", device=d, subdevice=s, function=f))
    expected = ((d << 4) ^ s ^ ((f << 4) & 0xFF) ^ (f >> 4)) & 0xFF
    assert _bytes(frame, 432, 173)[5] == expected


def test_sharp_dvd_uses_its_own_unit_and_gap():
    signal = _sub("SharpDVD", device=8, subdevice=48, function=1)
    assert signal.repeat[0] == 8 * 400 and signal.repeat[-1] == 48 * 400


# --- refusals -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, kwargs, match",
    [
        ("Panasonic", dict(device=256), "D:8 holds 0-255"),
        ("Panasonic", dict(device=-1), "D:8 holds 0-255"),
        ("Panasonic", dict(subdevice=256), "S:8 holds 0-255"),
        ("Panasonic", dict(function=256), "F:8 holds 0-255"),
        ("JVC-48", dict(function=-1), "F:8 holds 0-255"),
        ("Fujitsu", dict(subdevice=-1), "S:8 holds 0-255"),
        ("Teac-K", dict(device=16), "D:4 holds 0-15"),
        ("Denon-K", dict(device=16), "D:4 holds 0-15"),
        ("Denon-K", dict(subdevice=16), "S:4 holds 0-15"),
        ("Denon-K", dict(function=4096), "F:12 holds 0-4095"),
        ("SharpDVD", dict(device=16), "D:4 holds 0-15"),
        ("SharpDVD", dict(function=256), "F:8 holds 0-255"),
    ],
)
def test_out_of_range_fields_are_refused(name, kwargs, match):
    params = dict(device=1, subdevice=2, function=3) | kwargs
    with pytest.raises(EncodeError, match=match):
        _sub(name, **params)


@pytest.mark.parametrize("name", NAMES)
def test_a_missing_subdevice_is_refused_not_defaulted(name):
    """Fujitsu's IRP says ``S=D``; the ledger states it instead (as NEC1 does)."""
    with pytest.raises(EncodeError, match="requires an explicit subdevice"):
        _sub(name, device=1, subdevice=None, function=3)


@pytest.mark.parametrize("name", NAMES)
def test_unit_override_scales_every_duration(name):
    _, unit, gap, *_ = SPEC[name]
    signal = REGISTRY[name].encode(
        device=1, subdevice=2, function=3, carrier_hz=CARRIER, unit_us=unit + 100
    )
    assert _bytes(_frame_of(signal), unit + 100, gap) is not None


# --- gate 2a: independent evidence ---------------------------------------------------------

IRREMOTE_PANASONIC = {
    # crankyoldgit/IRremoteESP8266 src/ir_Panasonic.cpp @1e2f0f3, L28-L35
    "hdr_mark": 3456, "hdr_space": 1728, "bit_mark": 432,
    "one_space": 1296, "zero_space": 432, "min_gap": 74736,
}


def _irremote_encode_panasonic(manufacturer, device, subdevice, function):
    """``IRsend::encodePanasonic`` (L104-L112) and ``sendGeneric`` MSB-first."""
    c = IRREMOTE_PANASONIC
    checksum = device ^ subdevice ^ function
    data = (manufacturer << 32) | (device << 24) | (subdevice << 16) | (function << 8) | checksum
    out = [c["hdr_mark"], c["hdr_space"]]
    for i in range(47, -1, -1):
        out += [c["bit_mark"], c["one_space"] if (data >> i) & 1 else c["zero_space"]]
    return out + [c["bit_mark"], c["min_gap"]]


def _rev8(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


@pytest.mark.parametrize("d, s, f", [(176, 0, 54), (0, 0, 0), (255, 1, 128), (176, 16, 17)])
def test_panasonic_reproduces_irremoteesp8266_exactly(d, s, f):
    """A published constant table (not a capture, so no bias): 3456/1728, 432,
    1296, and a 74,736 us minimum gap -- which is exactly 173 units.

    **The two name the bytes differently.** IRremoteESP8266 sends its 48-bit
    value most-significant bit first, the IRP sends each field least-
    significant first, so a byte means the bit-reverse of the other's. Their
    manufacturer 0x4004 is our vendor bytes 02 20 spelt MSB-first, which is
    why the app's DB hexcodes for Panasonic all start ``4004``. The waveform
    for one IRP ``D, S, F`` is theirs for ``rev8(D), rev8(S), rev8(F)``."""
    ours = _frame_of(_sub("Panasonic", device=d, subdevice=s, function=f))
    theirs = _irremote_encode_panasonic(0x4004, _rev8(d), _rev8(s), _rev8(f))
    assert list(ours) == theirs
    # and without the reversal they are different signals for D=176 (non-palindromic)
    if _rev8(d) != d:
        assert list(ours) != _irremote_encode_panasonic(0x4004, d, s, f)


@pytest.mark.parametrize("name", ["Panasonic", "Teac-K", "Denon-K", "Fujitsu"])
def test_ours_agrees_with_the_hardware_capture(name):
    """IrpTransmogrifier's teaser captures, at the pinned commit, with the
    decode its own test asserts. Marks run long and spaces short in a real
    capture, so this is tolerance-checked layout (ratios), not exact."""
    cap = capture(name)
    signal = REGISTRY[name].encode(carrier_hz=cap["captureCarrierHz"], **cap["params"])
    assert disagreements(list(signal.intro or signal.repeat), cap.get("intro") or cap["repeat"]) == []
    if signal.intro:
        assert disagreements(list(signal.repeat), cap["repeat"]) == []


def test_the_captures_decode_as_the_parameters_asserted_for_them():
    """The asserted decode names the same D, S, F the test above encodes."""
    for name in ("Panasonic", "Teac-K", "Denon-K", "Fujitsu"):
        cap = capture(name)
        text = cap["expectedDecode"]
        assert text.startswith(name + ": {")
        for key, value in (("D", cap["params"]["device"]), ("F", cap["params"]["function"])):
            assert f"{key}={value}" in text, (name, text)
