"""R13's cross-check: one comparison, entirely in carrier cycles (D8).

The algorithm is stated once, because "quantize, then apply a microsecond
tolerance" was two units in one rule:

0. Exclude ``derived`` forms -- they never reach this (D5a).
1. Fix the comparison carrier, and compare frequency **words**, not hertz.
2. Compute the period exactly as D6 does.
3. Check structure: sequence count and lengths must match exactly.
4. Quantize both with D6's own function.
5. Compare cycle counts; nothing is compared in microseconds.
6. Aggregate as a star against the selected form, never all-pairs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .forms import Form, select
from .numeric import decimal_context
from .pronto import frequency_word, period_us, quantize
from .remote import Remote
from .signal import IrSignal
from .warnings import Warning_, RAW_CARRIER_WORD_DRIFT

#: SPEC R13's defaults. `relative` past 0.5 stops discriminating (D28).
DEFAULT_TOLERANCE = {"absUs": 100, "relative": Decimal("0.15"), "gapUs": 150}


@dataclass(frozen=True)
class Mismatch:
    key: str
    candidate: str
    form_id: str
    against: str
    detail: str

    def __str__(self) -> str:
        return (
            f"{self.key}:{self.candidate}: form {self.form_id!r} disagrees "
            f"with the trusted form {self.against!r} -- {self.detail}"
        )


def _tolerance(remote: Remote) -> dict:
    merged = dict(DEFAULT_TOLERANCE)
    merged.update(remote.protocol.tolerance or {})
    return merged


def _allow_cycles(us_a: int, us_b: int, period: Decimal, tol: dict) -> int:
    """D8 step 5's ``allow(a, b)``, converted to cycles once, with ceil.

    ``min`` rather than a bare ``a``: the predicate has to be **symmetric**,
    or the verdict depends on which form came first in the array and R12's
    determinism quietly fails for every raw comparison.
    """
    with decimal_context():
        span = Decimal(tol["absUs"])
        relative = Decimal(str(tol["relative"])) * Decimal(min(us_a, us_b))
        micros = max(span, relative)
        return math.ceil(micros / period)


def _gap_allow(period: Decimal, tol: dict) -> int:
    with decimal_context():
        return math.ceil(Decimal(tol["gapUs"]) / period)


def compare(
    *,
    trusted: Form,
    trusted_signal: IrSignal,
    other: Form,
    other_signal: IrSignal,
    remote: Remote,
) -> str | None:
    """Compare one form against the trusted one. Returns a detail, or None."""
    tol = _tolerance(remote)
    freq = frequency_word(remote.protocol.carrier_hz)
    period = period_us(freq)

    a_seqs = quantize(trusted_signal, freq_word=freq)
    b_seqs = quantize(other_signal, freq_word=freq)

    tolerant = "raw" in (trusted.type, other.type)
    skip_gap = trusted.truncated or other.truncated

    for name, a, b in zip(("intro", "repeat"), a_seqs, b_seqs):
        # Step 3. A differing burst COUNT is a structural disagreement,
        # never rounding, and always fails.
        if len(a) != len(b):
            return (
                f"{name} has {len(b)} bursts against {len(a)}; a differing "
                "burst count is structural, never rounding"
            )
        for i, (x, y) in enumerate(zip(a, b)):
            terminal = i == len(a) - 1
            if terminal and skip_gap:
                continue
            if terminal:
                allow = _gap_allow(period, tol)
            elif tolerant:
                allow = _allow_cycles(
                    trusted_signal.sequences[0 if name == "intro" else 1][i],
                    other_signal.sequences[0 if name == "intro" else 1][i],
                    period,
                    tol,
                )
            else:
                allow = 0
            if abs(x - y) > allow:
                return (
                    f"{name}[{i}] is {y} cycles against {x}"
                    + (f" (allowed drift {allow})" if allow else " (exact match required)")
                )
    return None


def check_key(remote: Remote, key: str) -> tuple[list[Mismatch], list[Warning_]]:
    """Cross-check every candidate group of one key."""
    mismatches: list[Mismatch] = []
    warnings: list[Warning_] = []
    expected_word = frequency_word(remote.protocol.carrier_hz)

    for candidate, group in remote.groups(key).items():
        # Step 0: derived forms are excluded entirely (D5a).
        eligible = [f for f in group if not f.is_derived]
        trusted = select(group)
        trusted_signal = remote.render(trusted)

        for form in eligible:
            if form is trusted:
                continue
            # Step 1, the raw half: a capture's carrier is a measurement with
            # instrument error, and the quantization is coarse enough to
            # absorb it -- so a word off by one warns rather than fails.
            if form.type == "raw" and form.carrier_hz:
                word = frequency_word(form.carrier_hz)
                if word != expected_word:
                    drift = abs(word - expected_word)
                    detail = (
                        f"declares carrierHz {form.carrier_hz} (word "
                        f"{word:04X}); the file declares "
                        f"{remote.protocol.carrier_hz} (word {expected_word:04X})"
                    )
                    if drift > 1:
                        mismatches.append(
                            Mismatch(key, candidate, form.id, trusted.id, detail)
                        )
                        continue
                    warnings.append(
                        Warning_(
                            code=RAW_CARRIER_WORD_DRIFT,
                            file=remote.where,
                            key=key,
                            candidate=candidate,
                            form=form.id,
                            message=detail + " -- one word apart, within "
                            "instrument error, so the comparison is unaffected",
                        )
                    )
            detail = compare(
                trusted=trusted,
                trusted_signal=trusted_signal,
                other=form,
                other_signal=remote.render(form),
                remote=remote,
            )
            if detail:
                mismatches.append(Mismatch(key, candidate, form.id, trusted.id, detail))

    return mismatches, warnings
