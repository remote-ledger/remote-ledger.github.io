"""RC5x invariants (D18 gate 3, DESIGN section 57).

The decoder is written from the frame layout, not from the encoder: twenty biphase bits at one unit a half, most significant
bit first, with four units of silence between the eighth and the ninth: S1 (a 1), ``~S:1:6``, T, D:5, the silence, S:6, F:6.
It restores the idle half-bit S1 opens with, which no recording keeps. Marantz's charts (D124) give a second, independent
source: a row is a system, a command and an *extension*, and 395 of its rows also carry the maker's own Pronto hex.
"""

import csv
import random
import statistics
from collections import Counter
from pathlib import Path

import pytest

from remote_ledger import pronto
from remote_ledger.errors import BoundsError, EncodeError
from remote_ledger.protocols import RC5X, REGISTRY

ROOT = Path(__file__).resolve().parent.parent
UNIT = RC5X.unit_us
CARRIER = 36_000
CHARTS = ("marantz-master-ir", "marantz-2014-ir-command-sheet", "marantz_fy18_av_sr_nr_ir_code_v02-02072018")


def _encode(device=16, subdevice=12, function=1, **kw):
    return RC5X.encode(device=device, subdevice=subdevice, function=function, carrier_hz=CARRIER, **kw)


def decode(frame, unit=UNIT):
    """``(D, S, F, T)`` of one frame of mark/space durations that ends on its gap, or None where it is not RC-5x."""
    levels = [0]                                             # S1's idle half-bit
    for i, duration in enumerate(frame[:-1]):
        levels += [1 - i % 2] * max(1, round(duration / unit))
    if len(levels) < 43 or len(levels) > 44:
        return None
    levels += [0] * (44 - len(levels))                       # the last bit's space may be inside the gap
    if levels[16:20] != [0, 0, 0, 0]:
        return None
    halves = levels[:16] + levels[20:]
    bits = []
    for first, second in zip(halves[0::2], halves[1::2]):
        if first == second:
            return None
        bits.append(int(second == 1))                        # a 1 is a space then a mark
    if bits[0] != 1:
        return None

    def field(lo, count):
        value = 0
        for b in bits[lo:lo + count]:
            value = value << 1 | b
        return value

    return field(3, 5), (1 - bits[1]) << 6 | field(8, 6), field(14, 6), bits[2]


# ------------------------------------------------------------------------------------------------ the frame

def test_the_frame_has_no_intro_one_repeat_and_the_ledger_extent():
    signal = _encode()
    assert signal.intro == () and signal.ending == ()
    assert len(signal.repeat) % 2 == 0
    assert sum(signal.repeat) == RC5X.extent_us == 114_000


def test_the_first_duration_is_a_mark_because_s1s_idle_half_bit_is_dropped():
    # S1 is a 1: a space then a mark. A frame that opened on that space would have a leading space no recording keeps.
    assert _encode().repeat[0] == UNIT * round(_encode().repeat[0] / UNIT)


def test_it_is_not_rc5_the_two_do_not_decode_each_other():
    rc5 = REGISTRY["RC5"].encode(device=16, subdevice=None, function=12, carrier_hz=CARRIER)
    assert decode(list(rc5.repeat)) is None
    assert decode(list(_encode().repeat)) == (16, 12, 1, 0)


@pytest.mark.parametrize("device,subdevice,function", [
    (0, 0, 0), (31, 127, 63), (16, 12, 1), (16, 1, 3), (22, 126, 0), (1, 64, 32), (30, 63, 21), (5, 65, 42),
])
def test_every_field_survives_the_frame(device, subdevice, function):
    for toggle in (0, 1):
        got = decode(list(_encode(device, subdevice, function, toggle=toggle).repeat))
        assert got == (device, subdevice, function, toggle)


def test_a_random_sample_of_the_whole_space_round_trips():
    rng = random.Random(5)
    for _ in range(3000):
        d, s, f, t = rng.randrange(32), rng.randrange(128), rng.randrange(64), rng.randrange(2)
        assert decode(list(_encode(d, s, f, toggle=t).repeat)) == (d, s, f, t)


def test_the_toggle_changes_one_bit_and_nothing_else():
    zero, one = _encode(toggle=0), _encode(toggle=1)
    assert decode(list(zero.repeat)) == (16, 12, 1, 0) and decode(list(one.repeat)) == (16, 12, 1, 1)
    assert sum(zero.repeat) == sum(one.repeat) == 114_000


def test_the_four_units_of_silence_sit_between_the_eighth_and_the_ninth_bit():
    # D=0 makes the eighth bit end on a space, so the silence merges into one space of five units or more
    spaces = [d for i, d in enumerate(_encode(device=0, subdevice=0, function=0).repeat[:-1]) if i % 2 == 1]
    assert max(spaces) >= 5 * UNIT
    # D=1 ends the eighth bit on a mark: the silence stands alone, four units
    spaces = [d for i, d in enumerate(_encode(device=1, subdevice=0, function=0).repeat[:-1]) if i % 2 == 1]
    assert 4 * UNIT in spaces


@pytest.mark.parametrize("kw,message", [
    ({"device": 32}, "D:5"),
    ({"device": -1}, "D:5"),
    ({"subdevice": 128}, "S is 0-127"),
    ({"subdevice": -1}, "S is 0-127"),
    ({"function": 64}, "F:6"),
    ({"function": -1}, "F:6"),
    ({"toggle": 2}, "T:1"),
    ({"subdevice": None}, "needs a subdevice"),
])
def test_a_field_out_of_range_is_refused(kw, message):
    with pytest.raises(EncodeError, match=message):
        _encode(**kw)


def test_the_unit_is_bounded_and_a_frame_that_overruns_its_extent_is_refused():
    with pytest.raises(BoundsError):
        _encode(unit_us=0)
    with pytest.raises(EncodeError, match="extent"):
        _encode(unit_us=3_000)                              # 43 units are 129 ms, past the 114 ms extent
    assert sum(_encode(unit_us=800).repeat) == 114_000


# ------------------------------------------------------------------------------------------------ the vectors

def test_the_golden_vectors_are_what_the_encoder_makes_but_for_the_lead_out_word():
    """The 1.2.14 release's own render of three frames. Ours differs only in the last word, the lead-out, by three cycles: the
    release rounds the extent another way (the declared divergence of the index)."""
    for name, (d, s, f) in {"D16_S12_F1": (16, 12, 1), "D16_S1_F3": (16, 1, 3), "D22_S126_F0": (22, 126, 0)}.items():
        gold = (ROOT / "tests" / "vectors" / f"irpt_rc5x_{name}.pronto").read_text().split()
        ours = pronto.encode(_encode(d, s, f)).split()
        assert len(ours) == len(gold)
        assert [i for i, (a, b) in enumerate(zip(ours, gold)) if a != b] == [len(gold) - 1]
        assert abs(int(ours[-1], 16) - int(gold[-1], 16)) == 3


# ------------------------------------------------------------------------------------------------ Marantz's charts

def _chart_rows():
    """The distinct (system, command, extension, hex) rows of Marantz's three charts that have an extension and the maker's
    Pronto hex. The extension is decimal in the chart (``12``, ``13``), as the command and the system are."""
    seen, rows = set(), []
    for stem in CHARTS:
        path = ROOT / "sources" / "official" / "marantz" / "csv" / f"{stem}__AVR_Commands.csv"
        table = list(csv.reader(path.open(encoding="utf-8")))
        header = table[5]
        hex_column = next(j for j, c in enumerate(header) if "hex" in c.lower())
        for row in table[6:]:
            if len(row) <= hex_column:
                continue
            system, command, extension, text = (row[3].strip(), row[4].strip(), row[5].strip(), row[hex_column].strip())
            if not (system.isdigit() and command.isdigit() and extension.isdigit() and text):
                continue
            if (system, command, extension, text) not in seen:
                seen.add((system, command, extension, text))
                rows.append((int(system), int(command), int(extension), text))
    return rows


def _first_frame(text):
    """The durations of the first frame of a Pronto string at its own frequency word, ending on its first gap of 20 ms."""
    words = text.split()
    period = int(words[1], 16) * 0.241246
    frame = []
    for i, word in enumerate(words[4:]):
        duration = round(int(word, 16) * period)
        frame.append(duration)
        if i % 2 == 1 and duration >= 20_000:
            break
    return frame


def test_marantzs_own_pronto_reads_as_the_charts_fields_in_nine_rows_of_ten():
    """395 distinct rows. 367 read exactly as the chart's system, command and extension. The 28 others are the chart disagreeing
    with itself: 19 read as another row's fields (a hex pasted under the wrong row), 7 differ in the command by one (a typo) or
    in the system, and 2 are not a frame of this shape. None is a decoder that cannot read the layout."""
    rows = _chart_rows()
    assert len(rows) == 395
    chart = {(s, c, e) for s, c, e, _ in rows}
    tally = Counter()
    for system, command, extension, text in rows:
        got = decode(_first_frame(text), unit=872)
        if got is None:
            tally["not a frame"] += 1
        elif got[:3] == (system, command, extension):
            tally["agrees"] += 1
        elif got[:3] in chart:
            tally["another row's fields"] += 1
        else:
            tally["off"] += 1
    assert tally == {"agrees": 367, "another row's fields": 19, "off": 7, "not a frame": 2}


def test_the_encoder_makes_the_frame_marantz_wrote_down_to_a_few_percent():
    """For the rows that agree, the encoder's durations against Marantz's own: Marantz's unit is 32 cycles at 36.7 kHz, 872 us,
    against 889 us, so the ratio is a little under one, and its frame is a unit or so shorter at the end."""
    ratios = []
    for system, command, extension, text in _chart_rows():
        frame = _first_frame(text)
        got = decode(frame, unit=872)
        if got is None or got[:3] != (system, command, extension):
            continue
        ours = list(_encode(system, command, extension, toggle=got[3]).repeat)
        assert len(ours) == len(frame) or len(ours) == len(frame) + 1 or len(ours) + 1 == len(frame)
        ratios += [b / a for a, b in zip(ours[:-1], frame[:-1])]
    assert 0.96 < statistics.median(ratios) < 1.0
    assert min(ratios) > 0.9 and max(ratios) < 1.1
