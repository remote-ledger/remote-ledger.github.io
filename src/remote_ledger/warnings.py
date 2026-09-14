"""Warning records and their stable codes (D32).

Exit codes: 0 when there are no errors, 1 when there is at least one.
Warnings never change the exit code -- they are recorded in
``build/warnings.json``, a committed artifact, so a new one shows up as a
tree diff that ``rl build --check`` fails until acknowledged. That is why CI
does not need ``--strict``: promoting the carrier warnings to errors would
defeat the reason they are warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: A file's carrierHz maps to a different frequency word than the registry's
#: nominal figure. 38 kHz against NEC1's nominal 38.4 kHz is the common case.
CARRIER_OFF_NOMINAL = "carrier-off-nominal"
#: A raw capture's own carrier is one word from the file's -- instrument
#: error, which the comparison already absorbs.
RAW_CARRIER_WORD_DRIFT = "raw-carrier-word-drift"
#: Two candidate groups compile to the identical Pronto string, so one is
#: redundant. Carries BOTH group names, ordered, so the pair is reported once
#: and the sort key stays total.
REDUNDANT_CANDIDATE = "redundant-candidate"


@dataclass(frozen=True)
class Warning_:
    code: str
    file: str
    message: str
    key: str | None = None
    candidate: str | None = None
    peer: str | None = None
    form: str | None = None

    @property
    def location(self) -> str:
        """Emitted only to the depth the warning actually has (D32)."""
        parts = [self.file]
        if self.key:
            parts.append(self.key)
        if self.candidate:
            parts.append(
                f"{self.candidate}+{self.peer}" if self.peer else self.candidate
            )
        if self.form:
            parts.append(self.form)
        return ":".join(parts)

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (
            self.file, self.key or "", self.candidate or "",
            self.peer or "", self.form or "", self.code,
        )

    def to_json(self) -> dict[str, Any]:
        """Absent scope fields are omitted, not null (D32)."""
        out: dict[str, Any] = {"code": self.code, "file": self.file}
        for name in ("key", "candidate", "peer", "form"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        out["message"] = self.message
        return out

    def __str__(self) -> str:
        return f"WARN {self.location} {self.code} {self.message}"


def sorted_warnings(items: list[Warning_]) -> list[Warning_]:
    return sorted(items, key=lambda w: w.sort_key)
