"""``rl bundle family-eval``: what the completion does to a remote it has not seen (D354).

The rule of :mod:`family` is only as good as the names it gives, and a remote it completes in the
bundle has no answer to be held against. So the rule is run where there is one, leaving one out:

1. a remote the ledger holds in full (at least 40 keys, at least 25 of them with a canonical
   key) is **cut down to a stub**: the first key of each of the ten basics a stub is written for
   (:data:`STUB`), and nothing else, at least five of them;
2. the remote is **taken out of the ledger**, so that it cannot corroborate itself, and the stub
   is completed by :meth:`family.Family.complete_remote` as a real thin remote is;
3. what was added is held against what the remote has. A key added is **in the remote** when its
   signal is one of the remote's, **named right** when the remote calls that signal by the same
   canonical key (counted where the remote names it at all), and **recovered** when it is both.

The sample is the remotes in the order of a hash of the seed and the file, so the same tree and
seed give the same figures. The figures are an **upper bound on precision and a lower bound on
recall**: a key added that the remote does not hold may well be a key of its family (a table is a
superset of one model's remote), and the remote's keys that the stub's family does not hold are
not ones any rule over the ledger could have found.
"""

from __future__ import annotations

import hashlib
from collections import Counter

from .corpus import RemoteRecord
from .family import UNTESTED, Family
from .merge import Folded

#: The keys a stub is written for.
STUB: tuple[str, ...] = ("POWER", "VOLUME_UP", "VOLUME_DOWN", "MUTE", "OK", "INPUT", "MENU", "EXIT",
                         "CHANNEL_UP", "CHANNEL_DOWN")
MIN_STUB = 5
#: A remote held in full.
RICH_KEYS, RICH_NAMED = 40, 25
SAMPLE = 400
SEED = 7


def _rank(where: str, seed: int) -> bytes:
    return hashlib.sha256(f"family\0{seed}\0{where}".encode()).digest()


def evaluate(records: list[RemoteRecord], folded: Folded, order: dict[str, int], *,
             sample: int = SAMPLE, seed: int = SEED) -> Counter[str]:
    """The counts of the evaluation (see the module's docstring), over ``sample`` remotes."""
    family = Family(records, folded, order)
    rich = [i for i in family.carriers
            if len(family.remotes[i].keys) >= RICH_KEYS
            and sum(1 for k in family.remotes[i].keys if k[2] is not None) >= RICH_NAMED]
    rich.sort(key=lambda i: _rank(records[i].where, seed))
    count: Counter[str] = Counter()
    for i in rich[:sample]:
        remote = family.remotes[i]
        stub: dict[str, tuple] = {}
        for key in remote.keys:
            if key[2] in STUB and key[2] not in stub:
                stub[key[2]] = key
        if len(stub) < MIN_STUB:
            continue
        found = family.complete_remote(tuple(stub.values()), family.plays[i], frozenset((i,)))
        count["stubs"] += 1
        truth = {k[4]: k[2] for k in remote.keys}
        have = {k[4] for k in stub.values()}
        to_find = len({k[2] for k in remote.keys if k[2] is not None} - set(stub))
        count["canonical keys to find"] += to_find
        if found is None:
            continue
        count["completed"] += 1
        count["canonical keys to find in a completed stub"] += to_find
        for name, label, canon, tier, blob in found.keys:
            assert tier == UNTESTED and blob not in have
            count["keys added"] += 1
            if blob in truth:
                count["in the remote"] += 1
                if truth[blob] is not None:
                    count["named by the remote"] += 1
                    if truth[blob] == canon:
                        count["named right"] += 1
    return count


def report(count: Counter[str]) -> str:
    """The figures as text, one per line."""
    def share(part: str, whole: str) -> str:
        return f"{100 * count[part] / max(count[whole], 1):.0f}%"

    return "\n".join([
        f"stubs cut from remotes held in full: {count['stubs']:,}",
        f"completed: {count['completed']:,} ({share('completed', 'stubs')})",
        f"keys added: {count['keys added']:,}, {count['keys added'] / max(count['completed'], 1):.1f} a completed stub",
        f"added keys the remote has (upper bound of precision): {count['in the remote']:,} ({share('in the remote', 'keys added')})",
        f"named right, where the remote names them: {count['named right']:,} of {count['named by the remote']:,} "
        f"({share('named right', 'named by the remote')})",
        f"canonical keys of the remotes that the stub lacks: {count['canonical keys to find']:,}; "
        f"found and named right: {count['named right']:,} ({share('named right', 'canonical keys to find')} of them, a lower bound "
        f"of recall; {share('named right', 'canonical keys to find in a completed stub')} of those of the stubs that were completed)",
    ]) + "\n"
