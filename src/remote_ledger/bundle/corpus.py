"""Reading the ledger for the bundle: one record per remote file (D88, D91).

A pure function of the committed ``remotes/`` tree and of the code. Nothing is
read from ``build/`` or ``site/``: each remote is loaded and compiled by the
very functions the ``compile`` stage uses (``Remote.compile_group`` after
``load_remote``), so a signal in the bundle is the ledger's own **wire reading**
of the key, never the app API's second reading of a hexcode (D55, D77), and a
bundle cannot be stale against the files it was built from.

A worker (``read_remote``) turns one file into a :class:`RemoteRecord` of plain
tuples; the parent never sees a Pronto string, only its blob.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .. import paths
from ..app_api import FULL_SIGNAL_PROTOCOLS
from ..errors import LedgerError
from ..forms import CONFIDENCE_RANK, PRIMARY, select
from ..irblaster.importer import parse_citation, split_controls_entry
from ..keys import canonical_id, load_vocabulary
from ..parallel import ordered_map
from ..remote import load_remote
from ..validate import corpus_files

#: The sources, in the order that fixes each one's id in ``sources`` and in
#: ``remotes.source``. A literal that only grows, like ``app_api.PROTOCOLS``.
SOURCES: tuple[str, ...] = ("authored", "lirc", "smartir", "irblaster", "hifi-remote", "jp1", "official")
SOURCE_ID = {name: i + 1 for i, name in enumerate(SOURCES)}


def sources() -> tuple[str, ...]:
    """``SOURCES`` and then the sources of the extra roots (D128), whose ids follow the repository's."""
    return SOURCES + paths.extra_source_names()


def source_ids() -> dict[str, int]:
    return {name: i + 1 for i, name in enumerate(sources())}

#: ``remotes.tier`` and ``keys.confidence``: ``forms.CONFIDENCE_RANK`` itself, so
#: 0 is the best tier. Written out in the meta table too.
TIERS = tuple(sorted(CONFIDENCE_RANK, key=CONFIDENCE_RANK.__getitem__))

#: ``rule`` values of D78, as a small integer in ``remotes.rule``.
RULES = ("ledger", "full-signal")


def source_of(where: str) -> str:
    """``remotes/lirc/sony/839.json`` -> ``lirc``; an authored remote -> ``authored``."""
    root = paths.imported_from(where)
    return root.split("/")[1] if root else "authored"


def is_synthetic_model(source: str) -> bool:
    """True where the file's own ``model`` is a placeholder and not a name a
    person would type: the IR Blaster import's ``IR Blaster DB <id> (<protocol>)``
    and SmartIR's ``SmartIR <category> <id>`` (D43, D46). The products those
    remotes control are in ``controls``."""
    return source in ("irblaster", "smartir")


@dataclass(frozen=True, slots=True)
class RemoteRecord:
    """Everything the bundle takes from one remote file. Plain data: it is
    pickled from a worker."""

    where: str
    source: str
    manufacturer: str
    model: str
    aliases: tuple[str, ...]
    controls: tuple[str, ...]
    #: ``(brand, model)`` pairs of ``controls`` where the source keeps the two
    #: apart (the IR Blaster import, D56b); ``None`` for free-text ``controls``.
    control_pairs: tuple[tuple[str, str], ...] | None
    protocol: str | None
    carrier_hz: int
    min_sends: int
    intro_empty: bool
    #: Every key of the file is of a database protocol that D78 plays as the
    #: whole signal (Sharp, Denon). Only an IR Blaster file can be.
    full_signal: bool
    #: The weakest tier of the primary groups (``index.rolled_up_confidence``).
    tier: int
    #: ``(key name, label or None, canonical id or None, confidence rank, blob)``,
    #: in the order of the key names.
    keys: tuple[tuple[str, str | None, str | None, int, bytes], ...]
    #: Candidate groups other than ``primary`` that the bundle does not carry.
    other_candidates: int


def pronto_blob(text: str) -> bytes:
    """A Pronto string as the bundle stores it (D91): a big-endian ``uint16``
    count of words, then the words, each a big-endian ``uint16``. The string is
    the ledger's own canonical output (``pronto.encode``: upper case, four hex
    digits a word, one space between), so the words are its hex digits as bytes."""
    body = bytes.fromhex(text.replace(" ", ""))
    return struct.pack(">H", len(body) // 2) + body


def read_remote(root: Path, path: Path) -> RemoteRecord | str:
    """One file as a record, or the problem that stops the export. A worker's unit."""
    where = paths.rel(root, path)
    try:
        return _read(root, path, where)
    except (LedgerError, ValueError, KeyError, TypeError) as exc:
        return f"{where}: {type(exc).__name__}: {exc}"


def _read(root: Path, path: Path, where: str) -> RemoteRecord | str:
    remote = load_remote(path)
    source = source_of(where)
    keys: list[tuple[str, str | None, str | None, int, bytes]] = []
    tiers: list[int] = []
    db_protocols: set[str] = set()
    empties: set[bool] = set()
    other = 0
    for name in sorted(remote.keys):
        groups = remote.groups(name)
        if PRIMARY not in groups:
            return f"{where}: key {name} has no primary candidate group"
        other += len(groups) - 1
        chosen = select(groups[PRIMARY])
        rank = CONFIDENCE_RANK[chosen.confidence]
        tiers.append(rank)
        text = remote.compile_group(name, PRIMARY)
        blob = pronto_blob(text)
        empties.add(int(text.split(" ", 3)[2], 16) == 0)
        label = remote.labels.get(name)
        keys.append((name, label, canonical_id(name, label), rank, blob))
        if source == "irblaster":
            db_protocols.add(parse_citation(chosen.source or "")[3])
    if not keys:
        return f"{where}: no key"
    if len(empties) != 1:
        return (f"{where}: some of its signals have an intro and some do not, so one "
                "play rule cannot be stated for the remote (D78)")

    full = bool(db_protocols) and db_protocols <= FULL_SIGNAL_PROTOCOLS
    if db_protocols & FULL_SIGNAL_PROTOCOLS and not full:
        return f"{where}: keys of database protocols {sorted(db_protocols)} are played by two rules"

    controls = tuple(remote.raw.get("controls") or ())
    pairs = None
    if source == "irblaster":
        pairs = tuple(split_controls_entry(c) for c in controls)
    return RemoteRecord(
        where=where, source=source, manufacturer=remote.manufacturer, model=remote.model,
        aliases=tuple(remote.raw.get("aliases") or ()), controls=controls,
        control_pairs=pairs,
        protocol=remote.protocol.name, carrier_hz=remote.protocol.carrier_hz,
        min_sends=remote.protocol.min_sends, intro_empty=empties.pop(), full_signal=full,
        tier=max(tiers), keys=tuple(keys), other_candidates=other,
    )


def play(record: RemoteRecord) -> tuple[int, int, int, str]:
    """``(repeatPasses, helperRepeatPasses, introEmpty, rule)`` of D78 for a remote.

    ``helperRepeatPasses`` is what ``_remoteLedgerSends`` plays for a ledger
    remote today: the repeat sequence ``minSends`` times when the intro is empty,
    ``minSends - 1`` times after it otherwise. ``repeatPasses`` is that, raised
    to one for a remote whose protocol is played as the whole signal. It is
    ``app_api._play`` over one remote instead of one database protocol."""
    helper = record.min_sends if record.intro_empty else record.min_sends - 1
    repeat = max(helper, 1) if record.full_signal else helper
    return repeat, helper, int(record.intro_empty), RULES[int(record.full_signal)]


def read_corpus(root: Path) -> tuple[list[RemoteRecord], list[str]]:
    """Every remote of the tree, in path order, and the problems found."""
    load_vocabulary()  # once, in the parent: the workers fork with it loaded
    records: list[RemoteRecord] = []
    problems: list[str] = []
    for item in ordered_map(partial(read_remote, root), corpus_files(root)):
        if isinstance(item, str):
            problems.append(item)
        else:
            records.append(item)
    return records, problems
