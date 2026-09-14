"""Schema conformance and the semantic rules JSON Schema cannot express (R15).

The division is deliberate. The schema carries every rule expressible as
structure -- required fields, enums, bounds, the `derived`/`derivedFrom`
pairing. Everything needing to *relate* one part of the file to another lives
here: form identity across a key (D21), candidate declaration (D26), cache
premises (D33), and the citation sidecar for fields that widen what passes
(D27).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator

from . import protocols
from .errors import ValidationError
from .serialize import load

#: Package data, not a repo-relative path: a non-editable ``pip install .``
#: has no repo checkout, and `rl validate` would die with an uncaught
#: FileNotFoundError. Tests import this rather than recomputing it, so the
#: suite exercises the same resolution the installed command uses.
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


@dataclass(frozen=True)
class Problem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_problems(doc: Any, schema_name: str, where: str) -> Iterator[Problem]:
    for err in sorted(_validator(schema_name).iter_errors(doc), key=str):
        loc = "".join(f"[{p!r}]" for p in err.absolute_path)
        yield Problem(f"{where}{loc}", err.message)


def semantic_problems(doc: Any, where: str) -> Iterator[Problem]:
    if not isinstance(doc, dict):
        return
    proto = doc.get("protocol")
    if not isinstance(proto, dict):
        return
    name = proto.get("name")
    if name is not None and name not in protocols.REGISTRY:
        known = ", ".join(sorted(protocols.REGISTRY))
        yield Problem(
            f"{where}['protocol']['name']",
            f"{name!r} is not in the v1 registry ({known}); adding a protocol "
            "needs an IRP string with its source, an independently cited "
            "golden vector, and an invariant test (D18)",
        )
    for key, spec in (doc.get("keys") or {}).items():
        if not isinstance(spec, dict):
            continue
        for i, form in enumerate(spec.get("forms") or []):
            if not isinstance(form, dict):
                continue
            at = f"{where}['keys'][{key!r}]['forms'][{i}]"
            # D24: nothing can dispatch an encoder without a protocol, so a
            # file with an unidentified protocol is raw/pronto only.
            if name is None and form.get("type") == "irp":
                yield Problem(
                    at,
                    "an irp form needs protocol.name, which this file omits; "
                    "an unidentified protocol is raw/pronto only (D24)",
                )
            # D4a: even length unless truncated. JSON Schema cannot express
            # modulo, so this is the semantic half of the rule the schema
            # states in prose.
            if form.get("type") == "raw" and not form.get("truncated"):
                for seq in ("intro", "repeat"):
                    values = form.get(seq)
                    if isinstance(values, list) and len(values) % 2:
                        yield Problem(
                            f"{at}[{seq!r}]",
                            f"has {len(values)} durations; a sequence must "
                            "have even length so it ends on a space. Only a "
                            "form declared `truncated` may end on a mark (D4a)",
                        )


#: D27: fields that override a default or widen what passes. The rule matters
#: more than the list -- anything added later that widens joins it.
CLAIMED_PROTOCOL_FIELDS = ("unitUs", "defaultGapUs", "tolerance")


def claims_problems(doc: Any, where: str) -> Iterator[Problem]:
    """D27: a reason justifies; only a source lets someone else re-derive."""
    proto = doc.get("protocol")
    if not isinstance(proto, dict):
        return
    claims = proto.get("claims") or {}
    for name in CLAIMED_PROTOCOL_FIELDS:
        if name not in proto:
            continue
        entry = claims.get(name)
        if not isinstance(entry, dict) or not entry.get("reason") or not entry.get("source"):
            yield Problem(
                f"{where}['protocol'][{name!r}]",
                f"overrides a default, so it needs a `claims.{name}` entry "
                "with both a reason and an independently checkable source. "
                "Presence is checked here; whether the source says what the "
                "claim says is a human judgement (D27, R18)",
            )


def structural_problems(path: Path, where: str) -> Iterator[Problem]:
    """Checks that need the whole document related together (D16/D21/D26/D33).

    Loading performs them: form ids, candidate declaration, cache cardinality
    and premises are all conditions on a well-formed document, so the loader
    refuses to build one that violates them rather than handing back
    something subtly wrong.
    """
    from .remote import load_remote
    from .variants import check_cache_cardinality, check_cache_premises, check_declared

    try:
        remote = load_remote(path, expand_variants=False)
    except ValidationError as exc:
        yield Problem(where, str(exc))
        return
    except Exception as exc:  # pragma: no cover - defensive
        yield Problem(where, f"could not be loaded: {exc}")
        return

    for key, forms in remote.keys.items():
        for check in (check_declared, check_cache_cardinality, check_cache_premises):
            try:
                if check is check_cache_cardinality:
                    check(key, forms)
                else:
                    check(key, forms, remote.variants)
            except ValidationError as exc:
                yield Problem(where, str(exc))


def validate_file(path: Path) -> list[Problem]:
    where = path.as_posix()
    try:
        doc = load(path)
    except Exception as exc:
        return [Problem(where, f"could not be parsed: {exc}")]
    problems = list(schema_problems(doc, "remote.schema.json", where))
    if problems:
        return problems
    problems += list(semantic_problems(doc, where))
    problems += list(claims_problems(doc, where))
    problems += list(structural_problems(path, where))
    return problems


def corpus_files(root: Path) -> list[Path]:
    """Every remote file, in a deterministic order (D20).

    ``remotes/**/*.json`` is uniformly one remote -- no reserved names, which
    is why ``unresolved.json`` sits at the repo root instead (D12).
    """
    return sorted((root / "remotes").glob("**/*.json"))
