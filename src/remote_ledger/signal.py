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
    RAW_DURATION_US_MAX,
    RAW_DURATION_US_MIN,
    check_bounds,
)


@dataclass(frozen=True)
class IrSignal:
    """A carrier plus up to two sequences of alternating mark/space durations.

    Durations are integer microseconds. Every sequence starts with a mark and
    has even length -- a sequence ends on a space, because the trailing gap is
    part of the signal, not an afterthought.
    """

    carrier_hz: int
    intro: tuple[int, ...] = ()
    repeat: tuple[int, ...] = ()
    #: Reserved. Pronto carries only two sequences, so a non-empty ending
    #: would silently vanish on emission; D1 keeps the field and rejects use.
    ending: tuple[int, ...] = field(default=())

    def __post_init__(self) -> None:
        # D1a: strictly positive. v0.1's `carrier_hz = 0` was a representable
        # state with no defined encoding -- D6 divides by the carrier and
        # defines only the modulated `0000` output.
        check_bounds("carrierHz", self.carrier_hz, CARRIER_HZ_MIN, CARRIER_HZ_MAX)

        for name in ("intro", "repeat", "ending"):
            self._check_sequence(name, getattr(self, name))

        if self.ending:
            raise ValidationError(
                "IrSignal.ending is reserved and must be empty in v1 (D1): "
                "Pronto carries only two sequences, so an ending would be "
                "dropped on emission rather than encoded"
            )
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
            if not isinstance(d, int) or isinstance(d, bool):
                raise ValidationError(
                    f"IrSignal.{name}[{i}] is {d!r}; durations are integer "
                    "microseconds (D1)"
                )
            check_bounds(
                f"{name}[{i}]", d, RAW_DURATION_US_MIN, RAW_DURATION_US_MAX
            )

    @property
    def sequences(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """(intro, repeat) -- the two sequences Pronto can carry."""
        return (self.intro, self.repeat)
