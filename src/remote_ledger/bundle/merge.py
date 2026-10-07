"""Folding the protocol fragments of a device into one remote (D103, D104).

The IR Blaster import writes one file per protocol (D56): a database id with keys in
Sony12 and in Sony15 is ``irblaster/AIWA/2312-Sony12`` and ``irblaster/AIWA/2312-Sony15``.
They are **fragments** of one device (the physical remote has both sets of keys), and a
fragment can have no key a person could press to ask "did your device respond?" (no
Power, Volume up or Mute) while the fragment beside it has one, so a list of the remotes
of a device shows a row that cannot be tried.

The exporter folds such a fragment into a sibling. The rule is conservative on purpose,
and every part of it is a line of :func:`fold` that can be widened alone:

1. A **device** is the files of the IR Blaster import that share their path without the
   protocol (``irblaster/AIWA/2312``) *and* the same maker and the same list of products.
   A file of any other source is a device of its own.
2. A fragment is **testable** when it has a key of :data:`TEST_KEY_ORDER`. Only a fragment
   that is not testable is folded, and **only into a testable sibling**: two testable
   fragments may be two encodings of one key (a Power in Sony12 and another in Sony15),
   and a device may answer to only one of them, so both stay a row a person can try.
3. The sibling must have the **same carrier and the same play rule** (``carrier_hz``,
   ``repeat_passes``, ``helper_repeat_passes``, ``intro_empty``, ``rule``), so that the
   keys that move are played by the remote they move to exactly as they were played by
   their own.
4. Where several siblings qualify, the one whose test key is **best in the test key
   order**, then the **lowest remote id** (the first in path order).

The merged remote is the carrier, with its own ref, carrier, play fields and protocol; its
keys are its own and then the folded fragments' keys, each in its own order, the fragments
in path order; its tier is the weakest of all of them. A folded fragment's test key does
not exist, so the carrier's test key is the merged remote's test key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .corpus import RemoteRecord, play

#: The canonical ids a remote's test key is chosen from, best first: the key a service
#: asks a person to send. The first of these that a remote has is its test key, and the
#: lowest key number among keys that share that id (D85).
TEST_KEY_ORDER: tuple[str, ...] = ("POWER", "POWER_OFF", "POWER_ON", "VOLUME_UP", "MUTE")

#: ``2312-Sony12``: the importer's name of a file, the database id and the protocol.
_FRAGMENT = re.compile(r"^(?P<number>\d+)-(?P<protocol>.+)$")


def ref_of(record: RemoteRecord) -> str:
    """The remote's ref: its path under ``remotes/`` without ``.json`` (D89)."""
    return record.where[len("remotes/"):-len(".json")]


def device_of(record: RemoteRecord) -> tuple | None:
    """What makes two files fragments of one device, or ``None`` for a file that has no
    fragment: its path without the protocol, its maker and the products it controls.
    Only the IR Blaster import writes a file per protocol, ``<id>-<protocol>``."""
    if record.source != "irblaster" or record.control_pairs is None:
        return None
    head, _, name = ref_of(record).rpartition("/")
    found = _FRAGMENT.match(name)
    if found is None:
        return None
    return (f"{head}/{found['number']}", record.manufacturer,
            tuple(sorted(record.control_pairs)), tuple(sorted(record.aliases)))


def key_to_try(record: RemoteRecord) -> tuple[int, int] | None:
    """``(position of its canonical id in TEST_KEY_ORDER, key number n)`` of the remote's test
    key, or ``None`` when it has none. Every key of the ledger has a signal."""
    for position, canonical in enumerate(TEST_KEY_ORDER):
        numbers = [n for n, key in enumerate(record.keys) if key[2] == canonical]
        if numbers:
            return position, min(numbers)
    return None


def play_of(record: RemoteRecord) -> tuple:
    """What a press of a key does, as the bundle stores it: the carrier and D78's four
    fields. Two remotes with the same value play a signal alike."""
    return (record.carrier_hz, *play(record))


@dataclass(frozen=True)
class Folded:
    """Which file's remote carries each file's keys.

    ``carrier[i]`` is the index (in path order, the remote id minus one) of the file whose
    remote row carries the keys of file ``i``: ``i`` itself for a file that is not folded,
    and for a folded fragment the file it was folded into. ``parts[c]`` lists, in path
    order, the files folded into the carrier ``c``."""

    carrier: tuple[int, ...]
    parts: dict[int, tuple[int, ...]] = field(default_factory=dict)

    @property
    def folded(self) -> int:
        """How many files are folded into another."""
        return sum(len(p) for p in self.parts.values())

    def remote(self, records: list[RemoteRecord], i: int) -> RemoteRecord:
        """The record of the remote carried by file ``i``: the file's own, or with the
        keys of the files folded into it after its own and the weakest tier of all."""
        record = records[i]
        extra = self.parts.get(i, ())
        if not extra:
            return record
        members = [record, *(records[j] for j in extra)]
        return replace(
            record,
            keys=tuple(key for member in members for key in member.keys),
            tier=max(member.tier for member in members),
            other_candidates=sum(member.other_candidates for member in members),
        )

    def first_key(self, records: list[RemoteRecord], i: int) -> int:
        """The number ``n`` of file ``i``'s first key inside the remote that carries it."""
        c = self.carrier[i]
        if c == i:
            return 0
        before = [c, *self.parts[c]]
        return sum(len(records[j].keys) for j in before[:before.index(i)])


def fold(records: list[RemoteRecord]) -> Folded:
    """Decide which fragments fold into which (the rules in the module's docstring)."""
    carrier = list(range(len(records)))
    devices: dict[tuple, list[int]] = {}
    for i, record in enumerate(records):
        key = device_of(record)
        if key is not None:
            devices.setdefault(key, []).append(i)
    parts: dict[int, list[int]] = {}
    for members in devices.values():
        if len(members) < 2:
            continue
        tests = {i: key_to_try(records[i]) for i in members}
        testable = [i for i in members if tests[i] is not None]
        for i in members:
            if tests[i] is not None:
                continue
            same = [t for t in testable if play_of(records[t]) == play_of(records[i])]
            if not same:
                continue
            target = min(same, key=lambda t: (tests[t][0], t))  # type: ignore[index]
            carrier[i] = target
            parts.setdefault(target, []).append(i)
    return Folded(tuple(carrier), {c: tuple(sorted(p)) for c, p in parts.items()})
