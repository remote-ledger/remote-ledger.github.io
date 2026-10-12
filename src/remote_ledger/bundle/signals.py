"""Reading a signal back out of its blob (D91).

A blob is what ``signals.words`` holds: a big-endian ``uint16`` count of words,
then that many words, each a big-endian ``uint16``. The words are exactly those
of the Pronto Hex string the ledger compiled the key to (``pronto.encode``):
word 0 is ``0000``, word 1 the frequency word, words 2 and 3 the numbers of burst
pairs of the intro and of the repeat sequence, then the durations in carrier
cycles, a mark then a space, first the intro and then the repeat.

A signal with an **ending** (D217), the final frame sent when the key is released,
has more words after those: the number of burst pairs of the ending and then its
durations in carrier cycles, at the frequency word of word 1. A blob is then
``4 + 2 (n1 + n2)`` words and ``1 + 2 n3`` more; any other length is not a blob.

:func:`decode_blob` is the ledger's own decoder run on the blob, which is how the
test vectors (``tests/vectors/bundle_vectors.json``) are made, so a reader in any
other language can be checked from blob to microseconds.
"""

from __future__ import annotations

import struct
from decimal import Decimal

from ..errors import ValidationError
from ..numeric import decimal_context, round_half_up
from ..pronto import decode, period_us
from ..signal import IrSignal


def _split(words: list[int]) -> tuple[list[int], list[int]]:
    """The Pronto words of a blob and the words of its ending (none for a blob without one)."""
    if len(words) < 4:
        raise ValidationError("a signal blob needs the four words of a Pronto header")
    base = 4 + 2 * (words[2] + words[3])
    if len(words) < base:
        raise ValidationError(f"a signal blob's header says {base} words but it holds {len(words)}")
    tail = words[base:]
    if tail and (tail[0] < 1 or len(tail) != 1 + 2 * tail[0]):
        raise ValidationError(
            f"a signal blob holds {len(tail)} words after its Pronto string; an ending is its burst "
            "pair count and then that many pairs")
    return words[:base], tail


def blob_words(blob: bytes) -> list[int]:
    """The words of a blob, after checking its length against its own prefix."""
    if len(blob) < 2:
        raise ValidationError("a signal blob holds at least its word count")
    (count,) = struct.unpack_from(">H", blob)
    if len(blob) != 2 + 2 * count:
        raise ValidationError(
            f"a signal blob says {count} words but holds {(len(blob) - 2) / 2:g}")
    return list(struct.unpack_from(f">{count}H", blob, 2))


def blob_to_pronto(blob: bytes) -> str:
    """The Pronto Hex string a blob stores: upper case, four digits a word, one space.
    The ending of a signal that has one is not in it (:func:`blob_ending_pronto`)."""
    return " ".join(f"{w:04X}" for w in _split(blob_words(blob))[0])


def blob_ending_pronto(blob: bytes) -> str | None:
    """The ending of a blob as the Pronto string of its own that ``pronto.encode_ending`` writes
    (the same frequency word, the burst pairs as the intro and no repeat), or None."""
    words = blob_words(blob)
    base, tail = _split(words)
    if not tail:
        return None
    return " ".join(f"{w:04X}" for w in (0, base[1], tail[0], 0, *tail[1:]))


def has_ending(blob: bytes) -> bool:
    return bool(_split(blob_words(blob))[1])


def decode_blob(blob: bytes, carrier_hz: int) -> IrSignal:
    """The signal of a blob under the ledger's decoder (D25), its ending too (D217).
    ``carrier_hz`` is the catalog's carrier of the remote, which the decoder checks the
    frequency word against, as it does for any Pronto string."""
    signal = decode(blob_to_pronto(blob), carrier_hz=carrier_hz)
    ending = blob_ending_pronto(blob)
    if ending is None:
        return signal
    return IrSignal(carrier_hz=signal.carrier_hz, intro=signal.intro, repeat=signal.repeat,
                    ending=decode(ending, carrier_hz=carrier_hz).intro)


def frequency_hz(blob: bytes) -> int:
    """The carrier the frequency word says, in whole hertz, rounded half up:
    ``1,000,000 / (word x 0.241246)`` (``006D`` is 38,029, not the catalog's 38,000)."""
    words = blob_words(blob)
    if len(words) < 4 or not words[1]:
        raise ValidationError("a signal blob needs a header with a frequency word")
    with decimal_context():
        return round_half_up(Decimal(1_000_000) / period_us(words[1]))


def press(intro: tuple[int, ...], repeat: tuple[int, ...], repeat_passes: int) -> list[int]:
    """What one press transmits (D78): the intro once, then the repeat sequence
    ``repeat_passes`` times."""
    return [*intro, *repeat * repeat_passes]
