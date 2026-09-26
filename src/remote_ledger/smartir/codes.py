"""Parse one SmartIR ``codes/<category>/<id>.json`` profile.

SmartIR has no notion of "remote" at all (see DESIGN.md section 15, D43):
a profile names a manufacturer, the device model(s) it controls, and a
``commands`` tree keyed by button name -- flat for most buttons
(``on``, ``off``, ``volumeUp``), one level deeper for a named group
(``media_player``'s ``sources`` map, ``fan``'s per-speed ``forward``/
``reverse``). A command's value is ordinarily one upstream capture; where
it is a list, upstream is recording several redundant captures of the same
button, and only the first is imported (noted in the citation).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import ValidationError


class SmartIrParseError(ValidationError):
    """A profile is missing a field the importer needs, or is malformed."""


@dataclass(frozen=True)
class Command:
    path: tuple[str, ...]
    code: str
    alternates: int


@dataclass(frozen=True)
class Profile:
    category: str
    profile_id: str
    manufacturer: str
    models: tuple[str, ...]
    controller: str | None
    encoding: str | None
    commands: tuple[Command, ...]


def _flatten(node: dict, prefix: tuple[str, ...]) -> list[Command]:
    out: list[Command] = []
    for name, value in node.items():
        path = prefix + (name,)
        if isinstance(value, dict):
            out.extend(_flatten(value, path))
        elif isinstance(value, list):
            if not value:
                continue
            out.append(Command(path, value[0], len(value)))
        else:
            out.append(Command(path, value, 1))
    return out


def parse_profile(category: str, profile_id: str, doc: dict) -> Profile:
    """Normalize one already-JSON-parsed upstream document.

    Raises :class:`SmartIrParseError` if a required field is absent or the
    wrong shape -- callers report this as a skipped file (R19 condition 5),
    never guess a value SPEC doesn't have.
    """
    if not isinstance(doc, dict):
        raise SmartIrParseError("the document is not a JSON object")
    manufacturer = doc.get("manufacturer")
    if not isinstance(manufacturer, str) or not manufacturer.strip():
        raise SmartIrParseError("no (non-empty) manufacturer field")
    models_raw = doc.get("supportedModels") or []
    if not isinstance(models_raw, list) or not all(isinstance(m, str) for m in models_raw):
        raise SmartIrParseError("supportedModels is not a list of strings")
    commands_raw = doc.get("commands")
    if not isinstance(commands_raw, dict):
        raise SmartIrParseError("commands is not a JSON object")
    commands = tuple(_flatten(commands_raw, ()))
    return Profile(
        category=category,
        profile_id=profile_id,
        manufacturer=manufacturer.strip(),
        models=tuple(models_raw),
        controller=doc.get("supportedController"),
        encoding=doc.get("commandsEncoding"),
        commands=commands,
    )
