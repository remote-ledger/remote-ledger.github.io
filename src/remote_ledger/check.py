"""``rl check``: cross-validation across a whole remote (R13, D9, D16, D32)."""

from __future__ import annotations

from .crosscheck import Mismatch, check_key
from .errors import ValidationError
from .forms import PRIMARY
from .pronto import encode, frequency_word
from .remote import Remote
from .warnings import (
    CARRIER_OFF_NOMINAL,
    REDUNDANT_CANDIDATE,
    Warning_,
    sorted_warnings,
)


def check_derived(remote: Remote, key: str) -> list[str]:
    """D9: regenerate each derived form and diff the canonical string.

    Comparing strings rather than decoded signals is both stronger and immune
    to D25's lossy cycles -> microseconds direction. Because the parent is
    named by a stable id, "derived from what?" always has one answer -- which
    is what makes this a genuine regression test rather than a restatement.
    """
    problems: list[str] = []
    by_id = {f.id: f for f in remote.keys[key]}
    for form in remote.keys[key]:
        if not form.is_derived:
            continue
        parent = by_id.get(form.derived_from)
        if parent is None:
            problems.append(
                f"{key}: form {form.id!r} is derived from "
                f"{form.derived_from!r}, which does not exist in this key (D21)"
            )
            continue
        if parent.candidate != form.candidate:
            problems.append(
                f"{key}: form {form.id!r} is derived from {parent.id!r} in a "
                f"different candidate group ({parent.candidate!r} vs "
                f"{form.candidate!r}); derivation stays within a group (D21)"
            )
            continue
        if parent.is_derived:
            problems.append(
                f"{key}: form {form.id!r} is derived from {parent.id!r}, "
                "which is itself derived; derivation is one level deep (D21)"
            )
            continue
        expected = encode(remote.render(parent))
        if form.hex.split() != expected.split():
            problems.append(
                f"{key}: derived form {form.id!r} is stale -- regenerating it "
                f"from {parent.id!r} gives different bytes. Run "
                "`rl fmt --refresh` (D9)"
            )
    return problems


def check_distinctness(remote: Remote, key: str) -> list[Warning_]:
    """D16: two groups that would ship the identical artifact -- one is redundant.

    Compared as **compiled Pronto strings**, by exact equality, not with D8's
    tolerances: the claim is "these two emit the same artifact", and the
    artifact *is* that string. Under a 15% raw tolerance two captures of
    genuinely different addresses could match and warn falsely, while two
    groups one cycle apart ship different bytes and are not redundant.
    """
    compiled: dict[str, str] = {}
    for candidate in remote.groups(key):
        try:
            compiled[candidate] = remote.compile_group(key, candidate)
        except ValidationError:
            continue  # reported elsewhere; distinctness is only a warning
    names = sorted(compiled)
    out: list[Warning_] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if compiled[a] == compiled[b]:
                out.append(
                    Warning_(
                        code=REDUNDANT_CANDIDATE,
                        file=remote.where,
                        key=key,
                        candidate=a,
                        peer=b,
                        message=(
                            "both candidate groups compile to the identical "
                            "Pronto string, so one is redundant"
                        ),
                    )
                )
    return out


def check_carrier_nominal(remote: Remote) -> list[Warning_]:
    """D3: a deviation large enough to change the frequency word warns.

    Not a `claims` requirement: carrierHz overrides no default, it *is* the
    value, and demanding a citation for 38000-vs-38400 on most NEC files
    would be boilerplate that devalues `claims` where it genuinely matters.
    """
    entry = remote.protocol.entry
    if entry is None:
        return []
    actual = frequency_word(remote.protocol.carrier_hz)
    nominal = frequency_word(entry.nominal_carrier_hz)
    if actual == nominal:
        return []
    return [
        Warning_(
            code=CARRIER_OFF_NOMINAL,
            file=remote.where,
            message=(
                f"carrierHz {remote.protocol.carrier_hz} maps to word "
                f"{actual:04X}; {entry.name}'s nominal "
                f"{entry.nominal_carrier_hz} maps to {nominal:04X}"
            ),
        )
    ]


def check_remote(remote: Remote) -> tuple[list[str], list[Warning_]]:
    """Every R13/D9/D16 check for one file."""
    problems: list[str] = []
    warnings: list[Warning_] = list(check_carrier_nominal(remote))

    for key in sorted(remote.keys):
        groups = remote.groups(key)
        if PRIMARY not in groups:
            problems.append(
                f"{key}: no `primary` candidate group, so the key has no "
                "default answer (D16)"
            )
        try:
            mismatches, key_warnings = check_key(remote, key)
        except ValidationError as exc:
            problems.append(f"{key}: {exc}")
        else:
            problems.extend(str(m) for m in mismatches)
            warnings.extend(key_warnings)
        problems.extend(check_derived(remote, key))
        warnings.extend(check_distinctness(remote, key))

    return problems, sorted_warnings(warnings)
