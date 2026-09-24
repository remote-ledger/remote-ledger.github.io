"""Two other tools' microsecond-to-Pronto rules, reimplemented for gate 2b.

Pronto Hex records durations in carrier cycles, and the tools that emit it do
not agree on how to get from microseconds to cycles:

- **D6 rule 4 (ours):** each duration on its own, against the period the
  frequency word implies -- ``round(t / (word * 0.241246))``.
- **IrpTransmogrifier:** each duration on its own, against the *nominal*
  carrier -- ``round(t * f / 1e6)``.
- **MakeHex:** against the word's period like ours, but each mark+space pair
  cumulatively, so a pair's rounding error lands on its space.

So "byte-identical to someone else's Pronto" is not one target, and a
published vector differing from our bytes proves nothing on its own. What a
vector *can* prove is that our microsecond timings are exactly the tool's:
quantize our signal by the tool's own rule and compare every word. These
functions are that rule, transcribed from each tool's source (cited on each
function), so the comparison checks our timings rather than our rounding.
"""

from decimal import ROUND_HALF_UP, Decimal

from remote_ledger.signal import IrSignal

_ONE = Decimal(1)


def _half_up(x: Decimal) -> int:
    # Java's Math.round and C's floor(x + 0.5) agree with ROUND_HALF_UP for
    # the non-negative values a duration can take.
    return int(x.quantize(_ONE, rounding=ROUND_HALF_UP))


def _words(freq_word: int, intro: list[int], repeat: list[int]) -> str:
    words = [0, freq_word, len(intro) // 2, len(repeat) // 2, *intro, *repeat]
    return " ".join(f"{w:04X}" for w in words)


def irpt(signal: IrSignal) -> str:
    """IrpTransmogrifier's rule, from ``ircore/Pronto.java`` @ c945e76:
    L88 ``Math.round(1000000d / (frequency * FREQUENCY_CONSTANT))`` for the
    word, and L128 ``Math.round(time * actualFrequency)`` per duration, where
    ``actualFrequency`` is the nominal carrier (L322-331)."""
    f = Decimal(signal.carrier_hz)
    word = _half_up(Decimal(1_000_000) / (f * Decimal("0.241246")))
    cycles = lambda seq: [_half_up(Decimal(t) * f / Decimal(1_000_000)) for t in seq]
    intro, repeat = signal.sequences
    return _words(word, cycles(intro), cycles(repeat))


def makehex(signal: IrSignal) -> str:
    """MakeHex's rule, from ``IRP.cpp`` @ probonopd/MakeHex 1373d90:

    - L447 ``unit = floor(4145146. / m_frequency + 0.5)`` for the word;
    - L464 ``v1 = floor( m_hex[nIndex]*4.145146/unit+0.5 )`` for a mark;
    - L469 ``v2 = floor( (m_hex[nIndex]+m_hex[nIndex+1])*4.145146/unit+0.5 )
      - v1`` for the space after it.

    The constants are MakeHex's own, not 1/0.241246."""
    f = Decimal(signal.carrier_hz)
    word = _half_up(Decimal(4_145_146) / f)
    per_us = Decimal("4.145146") / word

    def pairs(seq):
        out = []
        for mark, space in zip(seq[::2], seq[1::2]):
            v1 = _half_up(Decimal(mark) * per_us)
            out += [v1, _half_up(Decimal(mark + space) * per_us) - v1]
        return out

    intro, repeat = signal.sequences
    return _words(word, pairs(intro), pairs(repeat))


RULES = {"irpt-nominal-carrier": irpt, "makehex-cumulative-pairs": makehex}
