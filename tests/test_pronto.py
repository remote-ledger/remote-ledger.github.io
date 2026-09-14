"""D6 emission and D25 parsing."""

from decimal import Decimal

import pytest

from remote_ledger.errors import BoundsError, ProntoParseError
from remote_ledger.pronto import (
    decode,
    encode,
    frequency_word,
    parse_words,
    period_us,
    quantize,
)
from remote_ledger.signal import IrSignal


@pytest.mark.parametrize(
    "carrier,word",
    [
        (38_000, 0x006D),   # NEC1 as the seed data declares it
        (40_000, 0x0068),   # Sony -- see CITATIONS.md for the 0067 question
        (38_400, 0x006C),   # NEC1's nominal carrier
        (36_000, 0x0073),
    ],
)
def test_frequency_word(carrier, word):
    assert frequency_word(carrier) == word


def test_period_matches_the_worked_example():
    """DESIGN.md section 4: period 26.295814 us at 38 kHz."""
    assert period_us(frequency_word(38_000)) == Decimal("26.295814")


def test_header_shape():
    s = IrSignal(carrier_hz=38_000, intro=(9024, 4512), repeat=(9024, 2256))
    words = encode(s).split()
    assert words[:4] == ["0000", "006D", "0001", "0001"]


def test_words_are_uppercase_four_digits_single_spaced():
    text = encode(IrSignal(carrier_hz=38_000, intro=(9024, 4512)))
    assert text == text.strip()
    assert "  " not in text
    assert all(len(w) == 4 and w.upper() == w for w in text.split())


def test_irp_exact_units_not_rounded_nominals():
    """D6 rule 7. 16 x 564 = 9024 us gives 0157; a nominal "9 ms" gives 0156."""
    exact = encode(IrSignal(carrier_hz=38_000, intro=(9024, 4512))).split()
    nominal = encode(IrSignal(carrier_hz=38_000, intro=(9000, 4500))).split()
    assert exact[4:6] == ["0157", "00AC"]
    assert nominal[4:6] == ["0156", "00AB"]


def test_no_intro_puts_everything_in_the_repeat_slot():
    words = encode(IrSignal(carrier_hz=40_000, repeat=(2400, 600))).split()
    assert words[2:4] == ["0000", "0001"]


def test_zero_cycle_duration_is_rejected_not_emitted():
    """D28: at 38 kHz a cycle is ~26 us, so 13 us rounds away.

    Emitting `0000` for a burst that vanished would hide the upstream fault.
    """
    with pytest.raises(BoundsError, match="in cycles"):
        encode(IrSignal(carrier_hz=38_000, intro=(13, 5000)))


def test_round_trip_holds_in_cycles():
    """D25: cycles -> us is lossy, so equality is asserted in cycles."""
    original = IrSignal(
        carrier_hz=38_000,
        intro=(9024, 4512, 564, 1692, 564, 564),
        repeat=(9024, 2256, 564, 96156),
    )
    decoded = decode(encode(original), carrier_hz=38_000)
    assert quantize(decoded) == quantize(original)
    assert encode(decoded) == encode(original)


def test_round_trip_is_not_exact_in_microseconds():
    """Stated so the lossy direction is a tested fact, not a footnote."""
    original = IrSignal(carrier_hz=38_000, intro=(9024, 4512))
    decoded = decode(encode(original), carrier_hz=38_000)
    assert decoded.intro != original.intro
    assert quantize(decoded) == quantize(original)


def test_decode_tolerates_whitespace_and_case():
    text = encode(IrSignal(carrier_hz=38_000, intro=(9024, 4512)))
    messy = "\n  " + text.lower().replace(" ", "   ") + "\t\n"
    assert decode(messy, carrier_hz=38_000) == decode(text, carrier_hz=38_000)


def test_unmodulated_header_rejected():
    with pytest.raises(ProntoParseError, match="D1a"):
        decode("0100 006D 0001 0000 0157 00AC", carrier_hz=38_000)


@pytest.mark.parametrize("fmt", ["5000", "5001"])
def test_predefined_header_rejected(fmt):
    with pytest.raises(ProntoParseError, match="not a waveform"):
        decode(f"{fmt} 006D 0001 0000 0157 00AC", carrier_hz=38_000)


def test_unknown_header_rejected():
    with pytest.raises(ProntoParseError, match="only 0000"):
        decode("0001 006D 0001 0000 0157 00AC", carrier_hz=38_000)


def test_word_count_must_match_header():
    with pytest.raises(ProntoParseError, match="must hold 8 words"):
        decode("0000 006D 0002 0000 0157 00AC", carrier_hz=38_000)


def test_both_sequences_empty_rejected():
    with pytest.raises(ProntoParseError, match="not both"):
        decode("0000 006D 0000 0000", carrier_hz=38_000)


def test_carrier_compared_as_words_not_hertz():
    """D8 step 1. Decoding 006D gives ~38028.9 Hz, not 38000.

    Comparing decoded hertz against the declared carrier would reject
    correctly generated output; comparing words is exact.
    """
    text = encode(IrSignal(carrier_hz=38_000, intro=(9024, 4512)))
    assert decode(text, carrier_hz=38_000).carrier_hz == 38_000
    # 37_900 quantizes to the same word, so it is accepted...
    assert frequency_word(37_900) == frequency_word(38_000)
    assert decode(text, carrier_hz=37_900).carrier_hz == 37_900
    # ...while a genuinely different carrier is a different word, and fails.
    with pytest.raises(ProntoParseError, match="frequency word"):
        decode(text, carrier_hz=36_000)


@pytest.mark.parametrize("bad", ["", "00", "0000 00G0", "0000 006D 1"])
def test_malformed_words_rejected(bad):
    with pytest.raises(ProntoParseError):
        parse_words(bad) if bad else parse_words("")


def test_short_string_rejected():
    with pytest.raises(ProntoParseError, match="4-word header"):
        decode("0000 006D 0001", carrier_hz=38_000)
