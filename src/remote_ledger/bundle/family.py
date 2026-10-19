"""Completing a thin remote from the remotes that share its address (D357, D358).

A device is linked to the remotes that name it, so a device whose only remote is a stub
(a handful of keys written to prove a protocol, or one capture of a few buttons) is offered
that stub and nothing more, while the ledger holds a dozen full tables for the very address
the stub speaks. The exporter completes such a remote from them. Nothing is guessed from a
name, a brand or a model: two keys are the same key when they send the same signal, and the
signals a remote has in common with another say whether it speaks that remote's address.

The rule is conservative on purpose, every number below is a constant of this file that can
be changed alone, and the parts are the lines of :func:`complete`:

1. A remote is **thin** when it has fewer than :data:`THIN_BELOW` keys, and **completable**
   when it has at least :data:`MIN_SIGNALS` distinct signals to be recognised by. Only a
   thin remote is completed; a remote that is not thin is carried as it is.
2. Another remote **corroborates** it when it plays alike (the same carrier and play rule, so
   a key that is added is played as it was in its own remote), shares at least three quarters
   of its signals (and at least :data:`MIN_SHARED`), and **names the shared keys alike**: of the
   shared signals that both remotes give a canonical key, at least three quarters the same one
   and at least :data:`MIN_AGREEING` of them. Two remotes can send one signal for two
   functions (a code shared between a TV and a clone's player); naming is what tells them apart.
3. A remote needs at least :data:`MIN_CORROBORATORS` corroborators, or it is left as it is.
4. A signal the remote lacks is **supported** by the corroborators that have it, and is added
   when two fifths of them do (and at least two): the family's core, not the keys that one
   generation of one model has. Only keys of the tier plausible or better vote: a guess is
   not evidence.
5. It is added as the canonical key most of its supporters call it, if at least
   :data:`MIN_NAME_VOTES` do and they are at least seven tenths of the supporters that name it
   at all. A key with no such name is not added: nothing is carried as an unnamed key that
   no source agrees on. A canonical key the remote already has is not added again, and
   each canonical key is added once, to its best supported signal.
6. The most supported keys come first, and the remote grows only to :data:`TARGET_KEYS`
   keys, so a remote is made as large as the remotes of its family usually are and no larger.
7. **A remote keeps its test key and its alternative key.** A service asks a person to send
   one key to try a remote (Power, else Power off, Power on, Volume up, Mute: ``merge.
   TEST_KEY_ORDER``) and offers another when Power is no good (Volume up, else Mute), and the
   tier of that key is what the person's answer is about. A key added to a remote that has
   one of its own is never better in either order than the key it already has, so the key
   a service asks about, and the tier it tells, are what they were. A remote with no such key
   may be given one: it becomes a remote that can be tried, by an untested key.

The added keys are **untested**, the tier of "a reasoned candidate, offered but not
confirmed" (SPEC section 5), and follow the remote's own keys (and the folded fragments',
D103) in the order of the canonical vocabulary. The remote's tier is the weakest of its own
keys, not of these. Completion reads the remotes as they are in the ledger, never one that
another completion has added to, so nothing chains. The remote files are not changed: the
completion exists only in the bundle, in the table ``remote_completions``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from ..forms import CONFIDENCE_RANK
from .corpus import RemoteRecord
from .merge import TEST_KEY_ORDER, Folded, play_of

#: A remote with fewer keys than this is thin: the smallest quarter of the full bundle's remotes
#: (their quartiles are 29, 41 and 51 keys). It can be raised alone if this proves too narrow.
THIN_BELOW = 30
#: A remote is completed up to this many keys, no further: only a tenth of the remotes have more
#: (the 90th percentile is 61).
TARGET_KEYS = 60
#: Distinct signals a remote needs to be recognised by.
MIN_SIGNALS = 3
#: A corroborator shares this many of the remote's signals, and at least ``MIN_SHARED``.
SHARED = (3, 4)
MIN_SHARED = 3
#: Of the shared signals both name, this share is named alike, and at least this many are named.
AGREEING = (3, 4)
MIN_AGREEING = 2
#: Corroborators a remote needs.
MIN_CORROBORATORS = 3
#: A signal is added when this share of the corroborators (at least ``MIN_SUPPORT``) have it.
SUPPORT = (2, 5)
MIN_SUPPORT = 2
#: The name of an added key: votes, and their share of the supporters that name the signal.
NAME_SHARE = (7, 10)
MIN_NAME_VOTES = 2

#: The canonical ids a service offers when Power is no good to try a remote, best first (the
#: backend's ``TestKeyRule``, the other half of ``TEST_KEY_ORDER``).
ALTERNATIVE_ORDER: tuple[str, ...] = ("VOLUME_UP", "MUTE")

#: The tier of an added key, and the best tier that counts as evidence.
UNTESTED = CONFIDENCE_RANK["untested"]
VOTES_FROM = CONFIDENCE_RANK["plausible"]


@dataclass(frozen=True)
class Completion:
    """The keys added to one remote, as the records hold keys: ``(name, label, canonical id,
    tier, blob)``, in the order of the vocabulary, and how many remotes corroborated them."""

    keys: tuple[tuple[str, None, str, int, bytes], ...]
    corroborators: int


def _ceil_share(count: int, share: tuple[int, int]) -> int:
    """``ceil(count * share)`` in integers, so the same tree gives the same bundle on every machine."""
    return -(-count * share[0] // share[1])


def _signals_of(keys: tuple, best: int | None = None) -> dict[bytes, set[str]]:
    """The signals of some keys and, for each, the canonical keys it is called there. Keys of a
    tier worse than ``best`` are left out when it is given."""
    out: dict[bytes, set[str]] = {}
    for _name, _label, canon, rank, blob in keys:
        if best is not None and rank > best:
            continue
        names = out.setdefault(blob, set())
        if canon is not None:
            names.add(canon)
    return out


def _signals(record: RemoteRecord, best: int | None = None) -> dict[bytes, set[str]]:
    return _signals_of(record.keys, best)


def _outranks(canon: str, held: set[str]) -> bool:
    """Whether ``canon`` would come before a key ``held`` in the order a service picks its test key
    or its alternative from: such a key is not added to a remote that has that one (rule 7)."""
    for ranking in (TEST_KEY_ORDER, ALTERNATIVE_ORDER):
        if canon in ranking:
            have = [ranking.index(name) for name in held if name in ranking]
            if have and ranking.index(canon) < min(have):
                return True
    return False


class Family:
    """The signals of every remote of a tree, ready to ask what a remote's family holds. Built once
    per tree: the remotes are read as the ledger has them, never as a completion has changed
    them, so nothing chains."""

    def __init__(self, records: list[RemoteRecord], folded: Folded, order: dict[str, int]) -> None:
        self.order = order
        self.carriers = [i for i in range(len(records)) if folded.carrier[i] == i]
        self.remotes = {i: folded.remote(records, i) for i in self.carriers}
        self.plays = {i: play_of(remote) for i, remote in self.remotes.items()}
        self._mine = {i: _signals(remote) for i, remote in self.remotes.items()}
        self._voters = {i: _signals(remote, VOTES_FROM) for i, remote in self.remotes.items()}
        self._holders: dict[bytes, list[int]] = defaultdict(list)
        for i in self.carriers:
            for blob in self._mine[i]:
                self._holders[blob].append(i)

    def complete_remote(self, keys: tuple, play: tuple, exclude: frozenset[int] = frozenset(),
                        tally: Counter[str] | None = None) -> Completion | None:
        """The completion of a remote with ``keys`` that plays as ``play``, from the remotes of the
        tree but those of ``exclude`` (the remote itself, when it is one of them), or ``None`` when
        the rule leaves it as it is. ``tally`` counts where a remote falls out (DESIGN D358)."""
        tally = tally if tally is not None else Counter()
        own = _signals_of(keys)
        if len(own) < MIN_SIGNALS:
            tally["fewer than MIN_SIGNALS signals"] += 1
            return None
        shared: Counter[int] = Counter()
        for blob in own:
            for j in self._holders.get(blob, ()):
                if j not in exclude and self.plays[j] == play:
                    shared[j] += 1
        need = max(MIN_SHARED, _ceil_share(len(own), SHARED))
        corroborators = []
        for j in sorted(j for j, n in shared.items() if n >= need):
            both = alike = 0
            for blob, names in own.items():
                other = self._mine[j].get(blob)
                if names and other:
                    both += 1
                    alike += bool(names & other)
            if both >= MIN_AGREEING and AGREEING[1] * alike >= AGREEING[0] * both:
                corroborators.append(j)
        enough = len(corroborators) >= MIN_CORROBORATORS
        tally[f"corroborators: {min(len(corroborators), MIN_CORROBORATORS)}{'+' if enough else ''}"] += 1
        if not enough:
            return None

        support: Counter[bytes] = Counter()
        named: dict[bytes, Counter[str]] = defaultdict(Counter)
        for j in corroborators:
            for blob, names in self._voters[j].items():
                if blob in own:
                    continue
                support[blob] += 1
                named[blob].update(names)
        floor = max(MIN_SUPPORT, _ceil_share(len(corroborators), SUPPORT))
        held = {canon for names in own.values() for canon in names}
        taken = set(held)
        added: list[tuple[bytes, str]] = []
        for blob in sorted(support, key=lambda b: (-support[b], b)):
            if support[blob] < floor or len(keys) + len(added) >= TARGET_KEYS:
                break
            votes = named[blob]
            if not votes:
                continue
            canon, count = min(votes.items(), key=lambda item: (-item[1], self.order[item[0]]))
            if count < MIN_NAME_VOTES or NAME_SHARE[1] * count < NAME_SHARE[0] * sum(votes.values()):
                continue
            if canon in taken or _outranks(canon, held):
                continue
            taken.add(canon)
            added.append((blob, canon))
        tally["completed" if added else "corroborated, nothing to add"] += 1
        if not added:
            return None
        added.sort(key=lambda item: self.order[item[1]])
        return Completion(tuple((canon, None, canon, UNTESTED, blob) for blob, canon in added), len(corroborators))


def complete(records: list[RemoteRecord], folded: Folded, order: dict[str, int],
             funnel: Counter[str] | None = None) -> dict[int, Completion]:
    """The completion of every thin remote that has one, by the index of the file that carries
    the remote. ``order`` is the position of each canonical id in the vocabulary. ``funnel``,
    when given, counts where the thin remotes that are left as they are fall out."""
    tally = funnel if funnel is not None else Counter()
    family = Family(records, folded, order)
    done: dict[int, Completion] = {}
    for i in family.carriers:
        remote = family.remotes[i]
        if len(remote.keys) >= THIN_BELOW:
            continue
        tally["thin"] += 1
        found = family.complete_remote(remote.keys, family.plays[i], frozenset((i,)), tally)
        if found is not None:
            done[i] = found
    return done
