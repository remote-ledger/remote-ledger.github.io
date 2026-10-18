"""Flipper Zero's ``.ir`` file format (``Filetype: IR signals file`` or ``Filetype: Bruce IR File``, ``Version: 1``).

A file is a header, then entries separated by ``#`` lines (a ``#`` line with text is a comment; the text above the first
entry says what the file is for). An entry is ``name:`` and ``type:`` and then either

* ``type: parsed``: ``protocol:``, ``address:`` and ``command:``, each of the last two four bytes in hexadecimal, **least
  significant byte first** (the firmware's 32-bit word written as bytes), or
* ``type: raw``: ``frequency:``, ``duty_cycle:`` and ``data:``, the microseconds of a mark, a space, a mark...
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``Bruce IR File`` is another firmware's name for the same layout (one file of the repository has it)
FILETYPES = ("Filetype: IR signals file", "Filetype: Bruce IR File")
VERSION = "Version: 1"
_BYTES = re.compile(r"^[0-9A-Fa-f]{2}( [0-9A-Fa-f]{2}){3}$")


class FlipperParseError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str
    #: ``parsed`` only
    protocol: str = ""
    address: tuple[int, ...] = ()
    command: tuple[int, ...] = ()
    #: ``raw`` only
    frequency: int = 0
    duty_cycle: float = 0.0
    data: tuple[int, ...] = ()
    #: the line of the entry's ``name:``
    line: int = 0


@dataclass(frozen=True)
class IrFile:
    comments: tuple[str, ...]
    entries: tuple[Entry, ...]


def _bytes(text: str, field: str, line: int) -> tuple[int, ...]:
    if not _BYTES.match(text):
        raise FlipperParseError(f"line {line}: {field} {text!r} is not four hexadecimal bytes")
    return tuple(int(part, 16) for part in text.split())


def parse(text: str) -> IrFile:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").split("\n")
    comments: list[str] = []
    entries: list[Entry] = []
    # comments and blank lines may come before the header
    start = 0
    head: list[str] = []
    while start < len(lines) and len(head) < 2:
        line = lines[start].strip()
        if line.startswith("#"):
            if line != "#":
                comments.append(line[1:].strip())
        elif line:
            head.append(line)
        start += 1
    if len(head) != 2 or head[0] not in FILETYPES or head[1] != VERSION:
        raise FlipperParseError(f"the header is {head!r}, not one of {list(FILETYPES)!r} and {VERSION!r}")
    fields: dict[str, str] = {}
    at = 0

    def close() -> None:
        nonlocal fields
        if not fields:
            return
        kind = fields.get("type", "")
        name = fields.get("name", "")
        if kind == "parsed":
            missing = [k for k in ("protocol", "address", "command") if k not in fields]
            if missing:
                raise FlipperParseError(f"line {at}: the entry {name!r} has no {', '.join(missing)}")
            entries.append(Entry(name, kind, protocol=fields["protocol"], address=_bytes(fields["address"], "address", at),
                                 command=_bytes(fields["command"], "command", at), line=at))
        elif kind == "raw":
            missing = [k for k in ("frequency", "duty_cycle", "data") if k not in fields]
            if missing:
                raise FlipperParseError(f"line {at}: the entry {name!r} has no {', '.join(missing)}")
            try:
                data = tuple(int(v) for v in fields["data"].split())
                entries.append(Entry(name, kind, frequency=int(fields["frequency"]), duty_cycle=float(fields["duty_cycle"]), data=data, line=at))
            except ValueError as exc:
                raise FlipperParseError(f"line {at}: the raw entry {name!r}: {exc}") from exc
        else:
            raise FlipperParseError(f"line {at}: the entry {name!r} has type {kind!r}")
        fields = {}

    for number, raw in enumerate(lines[start:], start + 1):
        line = raw.strip()
        if not line or line == "#":
            continue
        if line.startswith("#"):
            if not entries and not fields:
                comments.append(line[1:].strip())
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise FlipperParseError(f"line {number}: {line!r} is not 'key: value'")
        key, value = key.strip(), value.strip()
        if key == "name":
            close()
            at = number
        fields[key] = value
    close()
    return IrFile(tuple(comments), tuple(entries))
