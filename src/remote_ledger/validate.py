"""Schema conformance (R15).

Phase 1 delivers schema validation plus the two semantic checks the schema
cannot express on its own: that ``protocol.name`` names a registry protocol,
and D24's rule that an unidentified protocol forbids ``irp`` forms. The
remaining semantic checks -- candidate groups (D16), form identity (D21),
cross-check (D8) -- land in Phase 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator

from . import protocols
from .serialize import load

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


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
    if name is None:
        # D24: nothing can dispatch an encoder without a protocol, so a file
        # with an unidentified protocol is raw/pronto only.
        for key, spec in (doc.get("keys") or {}).items():
            for i, form in enumerate(spec.get("forms") or []):
                if isinstance(form, dict) and form.get("type") == "irp":
                    yield Problem(
                        f"{where}['keys'][{key!r}]['forms'][{i}]",
                        "an irp form needs protocol.name, which this file "
                        "omits; an unidentified protocol is raw/pronto only "
                        "(D24)",
                    )


def validate_file(path: Path) -> list[Problem]:
    where = path.as_posix()
    try:
        doc = load(path)
    except Exception as exc:
        return [Problem(where, f"could not be parsed: {exc}")]
    problems = list(schema_problems(doc, "remote.schema.json", where))
    if not problems:
        problems += list(semantic_problems(doc, where))
    return problems


def corpus_files(root: Path) -> list[Path]:
    """Every remote file, in a deterministic order (D20).

    ``remotes/**/*.json`` is uniformly one remote -- no reserved names, which
    is why ``unresolved.json`` sits at the repo root instead (D12).
    """
    return sorted((root / "remotes").glob("**/*.json"))
