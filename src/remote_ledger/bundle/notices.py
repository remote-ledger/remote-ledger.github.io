"""The notices of the bundle: where the data came from and under what licence (D94).

Every fact here is the repository's own statement and nothing is inferred:

* the SPDX id of an imported source is ``paths.IMPORTS[root]["licence"]``;
* the licence text is the file the import directory carries (``COPYING`` or
  ``LICENSE``), copied byte for byte at build time;
* the commit is the one ``IMPORT.md`` names;
* the sentences that say how firmly the licence is established are the README
  of the import directory's own, word for word (``tests/test_bundle_notices.py``
  looks every one of them up in that README, so this file cannot drift from it);
* the upstream links are the README's.

Where the repository says a licence applies **by inheritance only**, the notice
says so in the repository's words and its ``licenceKind`` is ``inherited``. The
authored remotes have no licence file or statement anywhere in the repository,
so their entry has no SPDX id and no text, and says that.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .. import paths
from ..errors import ValidationError
from .corpus import SOURCE_ID, SOURCES, RemoteRecord

#: Per source, the facts the repository does not hold as data. ``readme_sentences``
#: are sentences of the import directory's README, quoted; ``urls`` are links of
#: the README; ``licence_file`` is the licence text the directory carries.
FACTS: dict[str, dict[str, Any]] = {
    "authored": {
        "name": "Authored remotes",
        "kind": "none",
        "note": "The repository records no licence for the remotes authored in it, "
                "and no licence file. Every key cites the sources it was taken from "
                "and checked against.",
    },
    "lirc": {
        "kind": "reading",
        "licence_file": "COPYING",
        "upstream_url": "https://sourceforge.net/p/lirc-remotes/code/",
        "readme_sentences": [
            "The upstream repository states no licence.",
            "Later files come from the same project, and nothing on record says otherwise.",
            "This is a reading of the licence, not a grant.",
        ],
    },
    "smartir": {
        "kind": "stated",
        "licence_file": "LICENSE",
        "upstream_url": "https://github.com/smartHomeHub/SmartIR",
        "readme_sentences": [
            "Unlike LIRC's remotes database, this one states its licence outright, "
            "so there is no reading involved.",
        ],
    },
    "hifi-remote": {
        "kind": "none",
        "upstream_url": "https://www.hifi-remote.com/sony/",
        "note": "hifi-remote.com's Sony code pages are a person's table of reference "
                "remote codes: which command number a Sony device code answers to. "
                "The repository records no licence for them, because it treats a table "
                "of reference codes as facts (DESIGN D110). Every key cites the page, "
                "its checksum and its row.",
    },
    "jp1": {
        "kind": "none",
        "upstream_url": "https://github.com/hifiremote/deviceupgrades",
        "note": "The JP1 device upgrades are what people of the hifi-remote.com forum "
                "programmed into their remotes to send one device's codes: tables of "
                "reference remote codes, which the repository records no licence for "
                "(DESIGN D110); the upstream repository states none either. Every key "
                "cites the repository's commit and the file.",
    },
    "irblaster": {
        "kind": "inherited",
        "licence_file": "LICENSE",
        "upstream_url": "https://github.com/remote-ledger/SwiftRemote",
        "readme_sentences": [
            "Licence: GPL-3.0-only, by inheritance and nothing more.",
            "nobody in the chain states a licence for the data itself, because nobody "
            "in the chain says where the data came from.",
            "This is not a grant from the data's authors, and it is not a reading of "
            "one, as LIRC's is.",
        ],
        "lineage": [
            {"name": "SwiftRemote", "url": "https://github.com/remote-ledger/SwiftRemote",
             "licence": "GPL-3.0"},
            {"name": "IR Blaster", "url": "https://github.com/iodn/android-ir-blaster",
             "licence": "GPL-3.0", "holder": "KaijinLab Inc."},
            {"name": "osram-remote", "url": "https://github.com/TalkingPanda0/osram-remote"},
        ],
    },
}

_COMMIT = re.compile(r"@ `([0-9a-f]{7,40})`")


def collapse(text: str) -> str:
    """Whitespace to single spaces: a README wraps its lines, a notice does not."""
    return " ".join(text.split())


def import_root(source: str) -> str | None:
    """``lirc`` -> ``remotes/lirc/``; ``authored`` -> None."""
    return next((r for r in paths.IMPORTS if r == f"remotes/{source}/"), None)


def _commit(report: Path) -> str | None:
    match = _COMMIT.search(report.read_text(encoding="utf-8"))
    return match[1] if match else None


def build_sources(root: Path, records: list[RemoteRecord]) -> list[dict[str, Any]]:
    """One entry per source, in ``SOURCES`` order, with the ledger's counts.

    Raises ``ValidationError`` when a statement the notice rests on is not in the
    tree (a missing licence file, a README that no longer says it)."""
    ledger_remotes = Counter(r.source for r in records)
    ledger_keys: Counter = Counter()
    for r in records:
        ledger_keys[r.source] += len(r.keys)
    out: list[dict[str, Any]] = []
    for name in SOURCES:
        facts = FACTS[name]
        entry: dict[str, Any] = {
            "id": SOURCE_ID[name], "key": name, "licenceKind": facts["kind"],
            "ledgerRemotes": ledger_remotes.get(name, 0), "ledgerKeys": ledger_keys.get(name, 0),
        }
        base = import_root(name)
        if facts["kind"] == "none":
            # No licence is recorded: the authored remotes, and an import of reference
            # codes (D110). The import's README still has to name where it came from.
            upstream = facts.get("upstream_url")
            readme = root / base / "README.md" if base else None
            if readme is not None and readme.is_file() and upstream and \
                    upstream not in collapse(readme.read_text(encoding="utf-8")):
                raise ValidationError(f"{base}README.md does not name {upstream}, the notice's link")
            entry.update(name=paths.IMPORTS[base]["name"] if base else facts["name"],
                         spdx=None, licenceText=None, licenceNotes=[facts["note"]],
                         upstreamUrl=upstream, upstreamCommit=None)
        else:
            meta = paths.IMPORTS[base]
            directory = root / base
            licence = directory / facts["licence_file"]
            readme = directory / "README.md"
            if not licence.is_file():
                raise ValidationError(f"{base}{facts['licence_file']}: the licence text of the "
                                      "notice is not in the tree")
            readme_text = collapse(readme.read_text(encoding="utf-8")) if readme.is_file() else ""
            for sentence in facts["readme_sentences"]:
                if collapse(sentence) not in readme_text:
                    raise ValidationError(
                        f"{base}README.md no longer says {sentence!r}; the notice quotes it")
            if facts["upstream_url"] not in readme_text:
                raise ValidationError(
                    f"{base}README.md does not name {facts['upstream_url']}, the notice's link")
            report = directory / "IMPORT.md"
            entry.update(
                name=meta["name"], spdx=meta["licence"],
                licenceText=licence.read_text(encoding="utf-8"),
                licenceNotes=list(facts["readme_sentences"]),
                upstreamUrl=facts["upstream_url"],
                upstreamCommit=_commit(report) if report.is_file() else None,
            )
            if "lineage" in facts:
                entry["lineage"] = facts["lineage"]
        out.append(entry)
    return out


def notices_document(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """``notices.json``: what an app shows on a screen of open-source notices."""
    keep = ("id", "key", "name", "spdx", "licenceKind", "licenceNotes", "licenceText",
            "upstreamUrl", "upstreamCommit", "lineage")
    return {
        "schemaVersion": 1,
        "sources": [
            {**{k: s[k] for k in keep if k in s},
             "contributed": {
                 "ledger": {"remotes": s["ledgerRemotes"], "keys": s["ledgerKeys"]},
                 "bundle": {"remotes": s["bundleRemotes"], "keys": s["bundleKeys"]},
             }}
            for s in sources
        ],
    }
