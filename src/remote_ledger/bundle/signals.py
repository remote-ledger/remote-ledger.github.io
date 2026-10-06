"""Reading a signal back out of its blob (D91).

A blob is what ``signals.words`` holds: a big-endian ``uint16`` count of words,
then that many words, each a big-endian ``uint16``. The words are exactly those
of the Pronto Hex string the ledger compiled the key to (``pronto.encode``):
word 0 is ``0000``, word 1 the frequency word, words 2 and 3 the numbers of burst
pairs of the intro and of the repeat sequence, then the durations in carrier
cycles, a mark then a space, first the intro and then the repeat.

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
    """The Pronto Hex string a blob stores: upper case, four digits a word, one space."""
    return " ".join(f"{w:04X}" for w in blob_words(blob))


def decode_blob(blob: bytes, carrier_hz: int) -> IrSignal:
    """The signal of a blob under the ledger's decoder (D25). ``carrier_hz`` is the
    catalog's carrier of the remote, which the decoder checks the frequency word
    against, as it does for any Pronto string."""
    return decode(blob_to_pronto(blob), carrier_hz=carrier_hz)


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
