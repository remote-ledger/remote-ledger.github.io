"""Loading and byte-reproducible emission (D20, D28).

Two contracts, deliberately different:

* **Generated** files use ``sort_keys=True``. One rule, nothing per-artifact to
  remember, and no way for a dict-insertion change to churn the diff.
* **Hand-authored** files in ``remotes/`` use a declared key order, because
  people read them and D19 never byte-compares them.

Arrays are classified per field in the schema by ``x-order``: a ``semantic``
array is never reordered by anything (D7's final tie-break is literally array
position), while a ``set`` array in a generated artifact is canonically
sorted -- byte-stability has to come from somewhere.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .numeric import canon_decimal

#: Placeholder marker. U+0000 cannot appear in any ledger string (the schema's
#: patterns and minLength constraints exclude it), so a collision is not
#: reachable from valid input.
_SENTINEL = "\x00"


def _reject_constant(token: str) -> Any:
    """json.load accepts NaN/Infinity/-Infinity by default (D28).

    Every ``minimum``/``maximum`` comparison against NaN is silently false, so
    an unbounded-by-accident field would sail through. Reject at parse time,
    before any schema or bounds check runs.
    """
    raise ValidationError(
        f"JSON contains the non-finite literal {token!r}; D28 rejects "
        "NaN and Infinity at parse time"
    )


def loads(text: str) -> Any:
    """Parse JSON under D28's rules: Decimal floats, no non-finite literals.

    ``parse_float=Decimal`` matters because ``0.15`` would otherwise arrive as
    a binary float and drag D8's arithmetic back onto binary rounding. The
    Decimal is built from the literal *text*, so it is exactly
    ``Decimal("0.15")``. ``parse_int`` stays default -- Python ints are exact.
    """
    return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)


def load(path: str | Path) -> Any:
    return loads(Path(path).read_text(encoding="utf-8"))


def _substitute_decimals(obj: Any, out: dict[str, str]) -> Any:
    if isinstance(obj, Decimal):
        token = f"{_SENTINEL}D{len(out)}{_SENTINEL}"
        out[token] = canon_decimal(obj)
        return token
    if isinstance(obj, dict):
        return {k: _substitute_decimals(v, out) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_substitute_decimals(v, out) for v in obj]
    return obj


def dumps(obj: Any, *, sort_keys: bool = True) -> str:
    """Serialize per D20: UTF-8 text, LF, exactly one trailing newline.

    Decimals are emitted as canonical unquoted numbers (D28). ``json`` offers
    no hook for a custom *number* representation -- ``default`` quotes whatever
    it returns -- so Decimals are swapped for sentinel strings and substituted
    back afterwards.
    """
    tokens: dict[str, str] = {}
    prepared = _substitute_decimals(obj, tokens)
    text = json.dumps(
        prepared,
        indent=2,
        sort_keys=sort_keys,
        ensure_ascii=False,
        separators=(",", ": "),
    )
    for token, number in tokens.items():
        quoted = json.dumps(token, ensure_ascii=False)
        if quoted not in text:  # pragma: no cover - defensive
            raise AssertionError(f"decimal placeholder {token!r} vanished")
        text = text.replace(quoted, number)
    return text + "\n"


def write_generated(path: Path, obj: Any) -> None:
    """Write a generated artifact. LF endings, no BOM, one trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj), encoding="utf-8", newline="\n")


#: D20: `rl fmt`'s key order for hand-authored files. Any key not named here
#: sorts lexicographically after every key that is -- the fallback that keeps
#: the formatter total when a schema field is added.
SOURCE_KEY_ORDER: dict[str, tuple[str, ...]] = {
    "remote": (
        "manufacturer", "model", "aliases", "controls",
        "protocol", "variants", "keys", "layouts",
    ),
    "protocol": (
        "name", "carrierHz", "unitUs", "minSends",
        "defaultGapUs", "tolerance", "claims",
    ),
    "form": (
        "id", "type", "candidate", "device", "subdevice", "function",
        "intro", "repeat", "truncated", "hex", "confidence",
        "verifiedBy", "derivedFrom", "expandedFrom", "source", "claims",
    ),
    "variant": ("label", "confidence", "source", "override"),
    "layout": ("original", "label", "source", "areas", "printedLabels", "shape"),
}


def order_keys(kind: str, obj: dict[str, Any]) -> dict[str, Any]:
    """Reorder one object's keys per SOURCE_KEY_ORDER, unknown keys last."""
    declared = SOURCE_KEY_ORDER.get(kind, ())
    rank = {k: i for i, k in enumerate(declared)}
    return {
        k: obj[k]
        for k in sorted(obj, key=lambda k: (rank.get(k, len(declared)), k))
    }
