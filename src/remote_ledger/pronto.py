"""Pronto Hex emission (D6) and parsing (D25).

The emission rules are fixed to the byte, because R12's "same JSON input
always produces byte-identical Pronto Hex" is otherwise untestable. Two rules
are worth reading twice:

* Rounding is ``ROUND_HALF_UP`` on ``Decimal``, never Python's ``round()``.
* Durations come from **IRP units multiplied out exactly**, never from rounded
  nominal values: ``16 x 564 = 9024`` microseconds, not "9 ms". That choice
  alone is the difference between a lead-in word of ``0157`` and ``0156``.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .errors import ProntoParseError
from .numeric import (
    BURST_CYCLES_MAX,
    BURST_CYCLES_MIN,
    CARRIER_HZ_MAX,
    CARRIER_HZ_MIN,
    FREQ_WORD_MAX,
    FREQ_WORD_MIN,
    PAIR_COUNT_MAX,
    PRONTO_CLOCK_US,
    check_bounds,
    decimal_context,
    round_half_up,
)
from .signal import IrSignal

#: Word 0 of a learned, carrier-modulated code -- the only format v1 emits.
FORMAT_RAW_MODULATED = 0x0000
#: Learned *unmodulated*. Deferred with D1a: no v1 protocol emits one, so
#: under D10's gate there would be no cited vector to prove the encoder right.
FORMAT_RAW_UNMODULATED = 0x0100
#: References into a Pronto-internal protocol table, not waveforms.
FORMAT_PREDEFINED = (0x5000, 0x5001)

_WORD_RE = re.compile(r"\A[0-9A-Fa-f]{4}\Z")


def frequency_word(carrier_hz: int) -> int:
    """D6 rule 2: the header's frequency word for a carrier in hertz."""
    # Checked here rather than only in IrSignal: `decode` needs the word
    # before it can build a signal, so a zero carrier would otherwise escape
    # as decimal.DivisionByZero -- which is not a LedgerError, so the CLI
    # would print a traceback instead of an error line.
    check_bounds("carrierHz", carrier_hz, CARRIER_HZ_MIN, CARRIER_HZ_MAX)
    with decimal_context():
        word = round_half_up(Decimal(1_000_000) / (Decimal(carrier_hz) * PRONTO_CLOCK_US))
    check_bounds("frequency word", word, FREQ_WORD_MIN, FREQ_WORD_MAX)
    return word


def period_us(freq_word: int) -> Decimal:
    """D6 rule 3: one carrier period, in microseconds."""
    with decimal_context():
        return Decimal(freq_word) * PRONTO_CLOCK_US


def us_to_cycles(duration_us: int, period: Decimal) -> int:
    """D6 rule 4: a duration in microseconds as a count of carrier cycles."""
    with decimal_context():
        return round_half_up(Decimal(duration_us) / period)


def cycles_to_us(cycles: int, period: Decimal) -> int:
    """D25: the inverse of :func:`us_to_cycles`, and lossy in this direction.

    ``decode(encode(s))`` recovers ``s`` only to within one cycle, because
    ``encode`` already quantized. This is why the round-trip property and D8
    both compare in *cycles*, and why D9 diffs the canonical string rather
    than decoded signals.
    """
    with decimal_context():
        return round_half_up(Decimal(cycles) * period)


def quantize(
    signal: IrSignal, *, freq_word: int | None = None
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Both sequences as carrier-cycle counts, at the signal's own carrier.

    D8 step 4 uses this so that an ``irp`` vs ``pronto`` comparison is exact
    by construction rather than by luck. ``freq_word`` lets a caller that has
    already computed it avoid doing so twice.
    """
    if freq_word is None:
        freq_word = frequency_word(signal.carrier_hz)
    period = period_us(freq_word)
    out = []
    for name, seq in zip(("intro", "repeat"), signal.sequences):
        cycles = []
        for i, d in enumerate(seq):
            c = us_to_cycles(d, period)
            # D28: the lower bound is the important one. At 38 kHz a cycle is
            # ~26 us, so anything under ~13 us rounds away. No real protocol
            # has such a burst, which is exactly why hitting it means
            # something upstream is wrong -- emitting `0000` would hide it.
            check_bounds(
                f"{name}[{i}] ({d} us) in cycles",
                c,
                BURST_CYCLES_MIN,
                BURST_CYCLES_MAX,
            )
            cycles.append(c)
        out.append(tuple(cycles))
    return out[0], out[1]


def encode(signal: IrSignal) -> str:
    """Render an :class:`IrSignal` as canonical Pronto Hex (D6).

    Uppercase, four hex digits per word, single-space separated, no trailing
    space. A protocol with no intro (every Sony variant) emits ``NNNN = 0000``
    and puts its whole sequence in the repeat slot.
    """
    freq = frequency_word(signal.carrier_hz)
    intro_cycles, repeat_cycles = quantize(signal, freq_word=freq)
    n1, n2 = len(intro_cycles) // 2, len(repeat_cycles) // 2
    check_bounds("n1", n1, 0, PAIR_COUNT_MAX)
    check_bounds("n2", n2, 0, PAIR_COUNT_MAX)
    words = [FORMAT_RAW_MODULATED, freq, n1, n2, *intro_cycles, *repeat_cycles]
    return " ".join(f"{w:04X}" for w in words)


def parse_words(text: str) -> list[int]:
    """Split a Pronto string into words, tolerating whitespace and case (D25)."""
    tokens = text.split()
    if not tokens:
        raise ProntoParseError("empty Pronto string")
    words = []
    for i, tok in enumerate(tokens):
        if not _WORD_RE.match(tok):
            raise ProntoParseError(
                f"word {i} is {tok!r}; D6 requires four hex digits per word"
            )
        words.append(int(tok, 16))
    return words


def decode(text: str, *, carrier_hz: int) -> IrSignal:
    """Parse Pronto Hex back into an :class:`IrSignal`.

    ``carrier_hz`` is the *file's* declared carrier, and the stored frequency
    word is validated against it (D8 step 1). Carrier agreement is checked by
    comparing **words, not hertz**: decoding ``006D`` back to hertz yields
    38 028.9 Hz, so comparing that against a declared ``38000`` would reject
    correctly generated output, while ignoring the header would let a string
    from a 36 kHz remote pass unnoticed. ``frequency_word`` is many-to-one and
    the word *is* the representation.
    """
    words = parse_words(text)
    if len(words) < 4:
        raise ProntoParseError(
            f"{len(words)} words; a Pronto string needs at least a 4-word header"
        )

    fmt, freq, n1, n2 = words[:4]
    # Before anything reads `freq`: a word of 0000 would make the period zero,
    # and the carrier-mismatch message below would raise DivisionByZero while
    # formatting itself.
    check_bounds("frequency word", freq, FREQ_WORD_MIN, FREQ_WORD_MAX)
    if fmt == FORMAT_RAW_UNMODULATED:
        raise ProntoParseError(
            "word 0 is 0100 (learned unmodulated); unmodulated signals are "
            "deferred in v1 (D1a) -- no registry protocol emits one, so there "
            "is no cited vector to prove the encoder right"
        )
    if fmt in FORMAT_PREDEFINED:
        raise ProntoParseError(
            f"word 0 is {fmt:04X}; that is a reference into a Pronto-internal "
            "protocol table, not a waveform, so there is nothing to decode (D25)"
        )
    if fmt != FORMAT_RAW_MODULATED:
        raise ProntoParseError(
            f"word 0 is {fmt:04X}; v1 accepts only 0000 (D25)"
        )

    expected_words = 4 + 2 * (n1 + n2)
    if len(words) != expected_words:
        raise ProntoParseError(
            f"header declares n1={n1}, n2={n2} so the string must hold "
            f"{expected_words} words, but it holds {len(words)} (D25)"
        )
    if n1 + n2 < 1:
        raise ProntoParseError(
            "header declares n1 = n2 = 0; either sequence may be empty, "
            "but not both (D28)"
        )

    expected_freq = frequency_word(carrier_hz)
    if freq != expected_freq:
        raise ProntoParseError(
            f"frequency word is {freq:04X} (~{_word_to_hz(freq)} Hz) but the "
            f"file declares carrierHz {carrier_hz}, whose word is "
            f"{expected_freq:04X} (~{_word_to_hz(expected_freq)} Hz). A Pronto "
            "string is a generated artifact, not a measurement: its word is "
            "either right or the string came from a different remote (D8)"
        )

    period = period_us(freq)
    body = words[4:]
    intro = tuple(cycles_to_us(c, period) for c in body[: 2 * n1])
    repeat = tuple(cycles_to_us(c, period) for c in body[2 * n1 :])
    return IrSignal(carrier_hz=carrier_hz, intro=intro, repeat=repeat)


def _word_to_hz(freq_word: int) -> int:
    """Approximate carrier for a frequency word -- for error messages only.

    Never used for comparison: that would be lossy in exactly the way D25
    describes for cycles -> microseconds.
    """
    with decimal_context():
        return round_half_up(Decimal(1_000_000) / period_us(freq_word))
