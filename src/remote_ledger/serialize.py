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
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .numeric import canon_decimal

#: Placeholder prefix. A per-call uuid4 makes the token unguessable, and
#: `dumps` additionally requires each token to occur exactly once in the
#: serialized text. The earlier fixed "\x00D<n>\x00" marker was corruptible:
#: U+0000 is *not* excluded by the schema (`model`, `citation`, `reason` and
#: `printedLabels` values carry no pattern), so a string equal to a token was
#: silently replaced by a bare number.
_SENTINEL = "\x00"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse duplicate object keys rather than silently keeping the last.

    A file with an accidentally duplicated ``carrierHz`` would otherwise
    validate and compile against a value the author cannot see in their own
    diff, and ``rl fmt`` would rewrite the file with the losing key erased.
    The same argument D28 makes for ``parse_constant`` applies: catch it at
    parse time, before any schema or bounds check runs.
    """
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValidationError(
                f"duplicate JSON key {key!r}; one of the two values would be "
                "silently discarded"
            )
        seen[key] = value
    return seen


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
    return json.loads(
        text,
        parse_float=Decimal,
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )


def load(path: str | Path) -> Any:
    return loads(Path(path).read_text(encoding="utf-8"))


def _is_int_array(obj: Any) -> bool:
    return (
        isinstance(obj, (list, tuple)) and len(obj) > 0
        and all(isinstance(v, int) and not isinstance(v, bool) for v in obj)
    )


def _substitute_decimals(obj: Any, out: dict[str, str], nonce: str) -> Any:
    """Swap what ``json`` cannot format our way for placeholder tokens.

    Two things qualify. A Decimal, which ``json`` would quote (D28). And,
    since D40, a non-empty array of integers -- a ``raw`` sequence -- which
    ``indent=2`` would spread one number per line, roughly doubling an
    imported remote's size for no reader's benefit. It is emitted on one
    line instead.
    """
    if isinstance(obj, Decimal):
        token = f"{_SENTINEL}{nonce}.{len(out)}{_SENTINEL}"
        out[token] = canon_decimal(obj)
        return token
    if _is_int_array(obj):
        token = f"{_SENTINEL}{nonce}.{len(out)}{_SENTINEL}"
        out[token] = "[" + ", ".join(str(v) for v in obj) + "]"
        return token
    if isinstance(obj, dict):
        return {k: _substitute_decimals(v, out, nonce) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_substitute_decimals(v, out, nonce) for v in obj]
    return obj


def dumps(obj: Any, *, sort_keys: bool = True) -> str:
    """Serialize per D20: UTF-8 text, LF, exactly one trailing newline.

    Decimals are emitted as canonical unquoted numbers (D28). ``json`` offers
    no hook for a custom *number* representation -- ``default`` quotes whatever
    it returns -- so Decimals are swapped for sentinel strings and substituted
    back afterwards.
    """
    tokens: dict[str, str] = {}
    prepared = _substitute_decimals(obj, tokens, uuid.uuid4().hex)
    text = json.dumps(
        prepared,
        indent=2,
        sort_keys=sort_keys,
        ensure_ascii=False,
        separators=(",", ": "),
        # Symmetrical with loads' parse-time rejection: writing a NaN or
        # Infinity literal would produce an artifact this module's own loads
        # refuses, and the failure would surface in a different command.
        allow_nan=False,
    )
    for token, number in tokens.items():
        quoted = json.dumps(token, ensure_ascii=False)
        occurrences = text.count(quoted)
        if occurrences != 1:
            raise ValidationError(
                f"placeholder appeared {occurrences} times instead of once; a "
                "string value collided with it, and substituting would "
                "corrupt the document"
            )
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
