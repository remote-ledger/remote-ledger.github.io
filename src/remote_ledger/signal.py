"""``IrSignal`` -- the one intermediate representation (D1).

Every form type renders into this, which is why cross-checking a stored Pronto
string against a parametric form is the same code path as checking two
parametric forms: there is exactly one comparison function, not one per
form-type pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ValidationError
from .numeric import (
    CARRIER_HZ_MAX,
    CARRIER_HZ_MIN,
    MAX_DURATIONS_PER_SEQUENCE,
    RAW_DURATION_US_MIN,
    SIGNAL_DURATION_US_MAX,
    check_bounds,
)


@dataclass(frozen=True)
class IrSignal:
    """A carrier plus up to three sequences of alternating mark/space durations.

    Durations are integer microseconds. Every sequence starts with a mark and
    has even length -- a sequence ends on a space, because the trailing gap is
    part of the signal, not an afterthought.

    ``intro`` is sent once when a key goes down and ``repeat`` again while it is
    held. ``ending`` is sent once when it is released (D217): the final frame
    some remotes send, such as XMP's. Pronto Hex holds only the first two, so
    the ending travels beside it (``pronto.encode_ending``).
    """

    carrier_hz: int
    intro: tuple[int, ...] = ()
    repeat: tuple[int, ...] = ()
    #: Sent once when the key is released (D217). Empty for every protocol
    #: but the ones whose remotes send a final frame.
    ending: tuple[int, ...] = field(default=())

    def __post_init__(self) -> None:
        # Coerce first. A list survives `frozen=True` as a mutable reference,
        # makes the signal unhashable, and makes two signals that encode to
        # the identical Pronto string compare unequal -- which would break
        # D8's cross-check, since a raw form's durations arrive from
        # json.loads as lists.
        for name in ("intro", "repeat", "ending"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                object.__setattr__(self, name, tuple(value))

        # D1a: strictly positive. v0.1's `carrier_hz = 0` was a representable
        # state with no defined encoding -- D6 divides by the carrier and
        # defines only the modulated `0000` output.
        check_bounds("carrierHz", self.carrier_hz, CARRIER_HZ_MIN, CARRIER_HZ_MAX)

        for name in ("intro", "repeat", "ending"):
            self._check_sequence(name, getattr(self, name))

        if not self.intro and not self.repeat:
            raise ValidationError(
                "IrSignal has neither an intro nor a repeat sequence; "
                "D28 requires n1 + n2 >= 1"
            )

    @staticmethod
    def _check_sequence(name: str, seq: tuple[int, ...]) -> None:
        if len(seq) % 2:
            raise ValidationError(
                f"IrSignal.{name} has {len(seq)} durations; a sequence must "
                "have even length so it ends on a space (D1)"
            )
        if len(seq) > MAX_DURATIONS_PER_SEQUENCE:
            raise ValidationError(
                f"IrSignal.{name} has {len(seq)} durations; D28 caps a "
                f"sequence at {MAX_DURATIONS_PER_SEQUENCE}"
            )
        for i, d in enumerate(seq):
            # The common case -- a plain int inside the bounds -- is settled
            # without building the error text for a check that passes; every
            # other value takes the original checks, with their messages.
            if d.__class__ is int and RAW_DURATION_US_MIN <= d <= SIGNAL_DURATION_US_MAX:
                continue
            if not isinstance(d, int) or isinstance(d, bool):
                raise ValidationError(
                    f"IrSignal.{name}[{i}] is {d!r}; durations are integer "
                    "microseconds (D1)"
                )
            check_bounds(
                f"{name}[{i}]", d, RAW_DURATION_US_MIN, SIGNAL_DURATION_US_MAX
            )

    @property
    def sequences(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """(intro, repeat) -- the two sequences Pronto can carry."""
        return (self.intro, self.repeat)
