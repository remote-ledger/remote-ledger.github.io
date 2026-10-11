"""RCA invariants (D18 gate 3, DESIGN section 45).

``RCA`` is ``RCA-38``'s frame at 58 kHz (``rca.py`` shares ``rca38.py``'s encoder), so the proof is two-sided: the frame
decodes with a decoder written from the layout, exhaustively, and **its durations are RCA-38's, field for field**, with
nothing but the carrier different. What anchors the constants outside this repository is the cited evidence
(tests/vectors/CITATIONS.md): IrpTransmogrifier 1.2.14's rendering of two signals and hardware captures of an RCA(Old)
remote and an RCA-38 one, read by ``tools/misc_capture_audit.py``.
"""

import pytest

from remote_ledger import pronto
from remote_ledger.errors import EncodeError
from remote_ledger.protocols import RCA, RCA38

UNIT = RCA.unit_us
CARRIER = 58_000


def _encode(device=15, function=48, **kw):
    return RCA.encode(device=device, subdevice=None, function=function, carrier_hz=CARRIER, **kw)


def _decode(repeat, unit=UNIT):
    """``(D, F)`` or None: an 8,-8 lead-in, 24 bits (a one-unit mark, then two units of space for a 0 and four for a 1),
    a one-unit stop mark and a 16-unit gap, the second twelve bits the complement of the first."""
    if len(repeat) != 2 + 48 + 2:
        return None
    units = [round(d / unit) for d in repeat]
    if units[:2] != [8, 8] or units[-2:] != [1, 16]:
        return None
    word = 0
    for mark, space in zip(units[2:-2:2], units[3:-2:2]):
        if mark != 1 or space not in (2, 4):
            return None
        word = word << 1 | (space == 4)
    d, f = word >> 20, word >> 12 & 0xFF
    return (d, f) if word >> 8 & 0xF == ~d & 0xF and word & 0xFF == ~f & 0xFF else None


def test_registry_metadata():
    assert RCA.name == "RCA" and RCA.unit_us == 460 and RCA.bits == 24
    assert RCA.nominal_carrier_hz == 58_000, "the IRP's 58k; a file's carrierHz is authoritative"
    assert RCA.extent_us is None, "the IRP ends -16, not an extent"
    assert RCA.irp.startswith("{58k,460,msb}") and "~D:4,~F:8" in RCA.irp


def test_every_device_and_function_round_trips():
    """4,096 frames, all of them."""
    for device in range(16):
        for function in range(256):
            assert _decode(_encode(device, function).repeat) == (device, function), (device, function)


def test_the_frame_is_rca_38s_with_nothing_but_the_carrier_different():
    """What the database says (RCA-38 'differs from RCA only in the frequency'), held over every frame."""
    for device in range(16):
        for function in range(0, 256, 3):
            ours = _encode(device, function)
            theirs = RCA38.encode(device=device, subdevice=None, function=function, carrier_hz=38_700)
            assert ours.repeat == theirs.repeat and ours.intro == theirs.intro == ()
            assert ours.carrier_hz == 58_000 and theirs.carrier_hz == 38_700


def test_there_is_no_intro_and_the_frame_is_one_length():
    signal = _encode()
    assert signal.intro == () and pronto.parse_words(pronto.encode(signal))[2] == 0
    assert sum(signal.repeat) == 129 * UNIT == 59_340          # twelve ones in every frame
    assert signal.repeat[-2:] == (UNIT, 16 * UNIT) and signal.repeat[:2] == (8 * UNIT, 8 * UNIT)


def test_bit_order_is_most_significant_first():
    assert _encode(device=8, function=0).repeat[3] == 4 * UNIT
    assert _encode(device=1, function=0).repeat[3] == 2 * UNIT
    assert _encode(device=0, function=0x80).repeat[3 + 2 * 4] == 4 * UNIT


def test_a_broken_complement_is_not_a_frame():
    repeat = list(_encode().repeat)
    repeat[3 + 2 * 14] = 6 * UNIT - repeat[3 + 2 * 14]
    assert _decode(tuple(repeat)) is None


def test_canonical_words():
    words = pronto.parse_words(pronto.encode(_encode()))
    assert f"{words[1]:04X}" == "0047" and words[3] == 0x1A     # 58 kHz, 26 burst pairs


@pytest.mark.parametrize("kwargs, match", [
    (dict(device=16), "RCA device is 16; D:4"), (dict(device=-1), "D:4"),
    (dict(function=256), "RCA function is 256; F:8"), (dict(function=-1), "F:8"),
])
def test_out_of_range_fields_are_refused_in_its_own_name(kwargs, match):
    with pytest.raises(EncodeError, match=match):
        _encode(**kwargs)


def test_a_subdevice_is_refused_not_ignored():
    with pytest.raises(EncodeError, match="RCA has no subdevice"):
        RCA.encode(device=15, subdevice=3, function=48, carrier_hz=CARRIER)


# --- the LIRC project's remotes, from another source ----------------------------------------------------------------

#: The files of ``remotes/lirc`` whose raw keys are RCA frames by the layout decoder below (878 keys). They are confs
#: expanded by lircd, not hardware captures, and the claim is only that the frame has the layout.
LIRC_RCA = (
    "lirc/ge/UNIVERSAL.json", "lirc/ge/VKFS0938.json", "lirc/microsoft/Xbox_MCE.json",
    "lirc/one_for_all/URC-4220.One4All_URC-4220_pvr.json", "lirc/one_for_all/URC-4220.One4All_URC-4220_tv.json",
    "lirc/one_for_all/URC-8910.json", "lirc/panasonic/RCR_195_DC1.json", "lirc/rca/CRK76CA2.json",
    "lirc/rca/DSS_RG_RH.json", "lirc/rca/DTA800B1.RCA_DTA800B1.json", "lirc/rca/R130A1.json", "lirc/rca/RCA312W.json",
    "lirc/rca/RCR311TCM1.json", "lirc/rca/RCR504BR.json", "lirc/rca/RCR815.json", "lirc/rca/RCU600RS.json",
    "lirc/rca/RS2300.json", "lirc/rca/VR637HF.json", "lirc/scientific_atlanta/3250.SA_3250_TV_VCR.json",
    "lirc/scientific_atlanta/E940.json", "lirc/scientific_atlanta/Explorer_2000.json",
    "lirc/thomson/ROC85.THOMSON_ROC85_CAB.json",
)


def decode_lirc(durations):
    """``(D, F)`` of a raw frame that has RCA's layout at the unit its own marks have, or None: a lead-in of about
    eight units' mark and eight of space, 24 bits (a space of two units for a 0 and four for a 1, nothing else), and
    the second twelve the complement of the first."""
    pairs = list(zip(durations[0::2], durations[1::2]))
    if len(pairs) < 26:
        return None
    lead, body = pairs[0], pairs[1:25]
    unit = sorted(m for m, _ in body)[len(body) // 2]
    if not (lead[0] > 6 * unit and 6 < lead[1] / unit < 10):
        return None
    ratios = [s / unit for _, s in body]
    if not all(1.5 < r < 2.7 or 3.3 < r < 4.7 for r in ratios):
        return None
    word = 0
    for r in ratios:
        word = word << 1 | (r > 3)
    d, f = word >> 20, word >> 12 & 0xFF
    return (d, f) if word >> 8 & 0xF == ~d & 0xF and word & 0xFF == ~f & 0xFF else None


def lirc_codes() -> dict[tuple[int, int], set[str]]:
    """``{(D, F): {files}}`` of the LIRC remotes' RCA frames (the first frame of each raw key)."""
    from pathlib import Path

    from remote_ledger.remote import load_remote

    root = Path(__file__).resolve().parent.parent / "remotes"
    found: dict[tuple[int, int], set[str]] = {}
    keys = 0
    for name in LIRC_RCA:
        for forms in load_remote(root / name).keys.values():
            form = forms[0]
            for sequence in (form.intro, form.repeat):
                got = decode_lirc(list(sequence)) if form.type == "raw" and sequence else None
                if got is not None:
                    found.setdefault(got, set()).add(name)
                    keys += 1
    assert keys == 878
    return found


def test_the_lirc_remotes_of_rca_devices_decode_to_frames_the_encoder_builds():
    """878 keys of 22 LIRC remotes (devices 5, 7, 8, 9, 11, 12, 13, 14 and 15) decode by the layout decoder at the unit
    each has, and the encoder builds the same frame at that unit."""
    codes = lirc_codes()
    assert len(codes) == 290 and sorted({d for d, _ in codes}) == [5, 7, 8, 9, 11, 12, 13, 14, 15]
    for (device, function) in codes:
        assert _decode(_encode(device, function).repeat) == (device, function)
