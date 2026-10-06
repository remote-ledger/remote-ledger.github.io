"""Cross-language test vectors for the signal table (D91).

``tests/vectors/bundle_vectors.json`` holds, for a sample of keys of the bundle
that covers every protocol the ledger has, what a reader of the blob has to get:

``blobHex``
    the ``signals.words`` blob, as hex: a big-endian ``uint16`` count of words and the
    words (D91);
``frequencyWord`` and ``frequencyHz``
    word 1 of the blob, and the carrier it says (``1,000,000 / (word x 0.241246)``
    rounded half up);
``catalogCarrierHz``
    the carrier the catalog (``remotes.carrier_hz``) holds for the remote. It is what
    the file declared, ``38000`` where the word says ``38029``; which of the two an
    output uses is its own decision, and both are here;
``introUs`` and ``repeatUs``
    the two sequences in microseconds, from **the ledger's own decoder run on the blob**
    (``pronto.decode``, D25: cycles x period, rounded half up);
``play``
    ``repeatPasses``, ``helperRepeatPasses``, ``introEmpty``, ``rule`` of the remote
    (D78), and ``pressUs``, the intro once and then the repeat sequence ``repeatPasses``
    times: what one press transmits.

A Kotlin (or any) reader of the bundle is right when, from ``blobHex`` and
``catalogCarrierHz``, it produces every one of these. The vectors are
self-contained: each carries its blob, so they stay valid when the catalog changes and
need regenerating only when the decoder or the blob format does
(``rl bundle vectors``).
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .corpus import SOURCES
from .signals import blob_words, decode_blob, frequency_hz, press

FORMAT = 1
#: Keys taken for each (source, protocol) of the bundle, and the longest blob a
#: sampled key may have (the extremes below are exempt, up to ``EXTREME_WORDS``).
PER_GROUP = 4
MAX_WORDS = 400
EXTREME_WORDS = 1200


def _rank(ref: str, n: int) -> bytes:
    return hashlib.sha256(f"vector\0{ref}\0{n}".encode()).digest()


def build_vectors(bundle: Path) -> dict[str, Any]:
    """The vectors of the bundle file ``bundle``."""
    conn = sqlite3.connect(f"file:{bundle.as_posix()}?mode=ro", uri=True)
    try:
        keys_of = defaultdict(list)
        canon = {i: k for i, k in conn.execute("SELECT id, key FROM vocab_keys")}
        remotes = {}
        for row in conn.execute(
                "SELECT id, ref, source, protocol, carrier_hz, repeat_passes, helper_repeat_passes, "
                "intro_empty, rule FROM remotes ORDER BY id"):
            remotes[row[0]] = row
        for remote_id, n, canon_id, label, signal_id, confidence, blob in conn.execute(
                "SELECT k.remote_id, k.n, k.canon, k.label, k.signal_id, k.confidence, s.words "
                "FROM keys k JOIN signals s ON s.id = k.signal_id ORDER BY k.remote_id, k.n"):
            n_words = (len(blob) - 2) // 2
            row = remotes[remote_id]
            group = (SOURCES[row[2] - 1], row[3] or "(unnamed)")
            keys_of[group].append((_rank(row[1], n), remote_id, n, canon_id, label, confidence,
                                   blob, n_words))
        chosen: dict[tuple[int, int], tuple] = {}
        for group in sorted(keys_of):
            usable = [k for k in sorted(keys_of[group]) if k[7] <= MAX_WORDS]
            for entry in usable[:PER_GROUP]:
                chosen[(entry[1], entry[2])] = entry
        everything = [k for ks in keys_of.values() for k in ks]
        # the extremes: the shortest and the longest signal, the largest play numbers,
        # a remote of each rule and of each intro
        extremes = [min(everything, key=lambda k: (k[7], k[0])),
                    max((k for k in everything if k[7] <= EXTREME_WORDS), key=lambda k: (k[7], k[0]))]
        for flag in (0, 1):
            extremes += [min((k for k in everything if remotes[k[1]][7] == flag), key=lambda k: k[0])]
        for rule in ("ledger", "full-signal"):
            members = [k for k in everything if remotes[k[1]][8] == rule and k[7] <= MAX_WORDS]
            if members:
                extremes.append(min(members, key=lambda k: k[0]))
        extremes.append(max((k for k in everything if k[7] <= MAX_WORDS),
                            key=lambda k: (remotes[k[1]][5], k[0])))
        for entry in extremes:
            chosen.setdefault((entry[1], entry[2]), entry)

        vectors = []
        for (remote_id, n), (_, _, _, canon_id, label, confidence, blob, n_words) in sorted(
                chosen.items()):
            ref, source, protocol, carrier, repeat, helper, empty, rule = (
                remotes[remote_id][1], remotes[remote_id][2], remotes[remote_id][3],
                remotes[remote_id][4], remotes[remote_id][5], remotes[remote_id][6],
                remotes[remote_id][7], remotes[remote_id][8])
            signal = decode_blob(blob, carrier)
            vectors.append({
                "remote": ref, "n": n, "source": SOURCES[source - 1],
                "protocol": protocol, "canon": canon.get(canon_id), "label": label,
                "confidence": confidence,
                "blobHex": blob.hex(),
                "frequencyWord": blob_words(blob)[1],
                "frequencyHz": frequency_hz(blob),
                "catalogCarrierHz": carrier,
                "introUs": list(signal.intro), "repeatUs": list(signal.repeat),
                "play": {
                    "repeatPasses": repeat, "helperRepeatPasses": helper,
                    "introEmpty": bool(empty), "rule": rule,
                    "pressUs": press(signal.intro, signal.repeat, repeat),
                },
            })
        return {
            "format": FORMAT,
            "about": "Cross-language vectors of the signal table (DESIGN.md D91): from blobHex "
                     "and catalogCarrierHz a reader must produce frequencyWord, frequencyHz, "
                     "introUs, repeatUs and play.pressUs.",
            "protocols": sorted({v["protocol"] or "(unnamed)" for v in vectors}),
            "vectors": vectors,
        }
    finally:
        conn.close()
