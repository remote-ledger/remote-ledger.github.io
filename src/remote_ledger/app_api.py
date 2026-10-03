"""The app API, version 1 (D74 to D80): static files the SwiftRemote app reads.

SwiftRemote is to stop bundling its 51 MB sqlite and read the imported IR
Blaster database from the ledger instead. This module turns the committed
``remotes/irblaster/`` tree into the files it asks for, under ``site/app/v1/``,
the one directory of the repository that GitHub Pages publishes:

``manifest.json``
    schema version, a digest of the data, counts, the protocol table (the index
    of a protocol in it is its bit in every ``protoMask``), the paths of the
    rest, and the rule of ``power.json``;
``brands.json``
    ``[[name, key, protoMask], ...]``, ASCII-NOCASE order;
``b/<key>.m.json`` and ``b/<key>.k.json``
    one brand's models and its keys (``key`` is the SHA-1 of the brand's UTF-8
    name, ten hex digits: names carry case, reserved names and trailing dots);
``s/<DB protocol>.json``
    for the protocols whose reading differs between SwiftRemote and the wire
    only, one Pronto string per code present, with how to play one press;
``power.json``
    the codes that rank as a power key, most used first.

**A pure function of the committed tree, plus the code.** The inputs are the
files under ``remotes/irblaster/`` and the hex modules' two tables. Nothing is
read from the SwiftRemote checkout, the SQL dump or ``build/``. Every key's
database id, protocol and hexcode are read back from its citation through the
importer's own parser (``importer.parse_citation``), every brand and model from
``controls`` through ``importer.split_controls_entry``. The bytes do not depend
on the worker count or on the order a worker finishes in (R12).

The ledger's per-key facts are those the importer wrote, so a key the importer
could not represent (2,066 of the database's 413,331) is not in the tree and
not in the API; ``rl app`` prints the count from ``IMPORT.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import string
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache, partial
from pathlib import Path
from typing import Any

from . import paths
from .errors import LedgerError, ValidationError
from .forms import PRIMARY
from .irblaster import importer
from .irblaster.importer import (
    FROM_DB_HEX, FROM_DB_HEX_APP, app_reading_differs, citation_commit, parse_citation,
    split_controls_entry,
)
from .parallel import ordered_map
from .remote import remote_from_doc
from .serialize import load
from .validate import corpus_files

SCHEMA_VERSION = 1

#: Every database protocol, in the order that fixes its bit in every
#: ``protoMask`` and its index in the manifest, ``.k`` rows and ``power.json``.
#: It is ASCII-NOCASE order today, and it is a literal so that **it never
#: moves**: a protocol the database gains is appended, and a test fails until it
#: is, because a reordering would make every shard a client has cached wrong.
PROTOCOLS: tuple[str, ...] = (
    "Denon", "F12_relaxed", "JVC", "NEC", "NEC2", "NECx1", "NECx2", "Pioneer",
    "Proton", "RC5", "RC6", "RCA_38", "RCC0082", "RCC2026", "REC80", "RECS80",
    "RECS80_L", "Samsung36", "Sharp", "SONY12", "SONY15", "SONY20", "Thomson7",
)
PROTOCOL_INDEX = {name: i for i, name in enumerate(PROTOCOLS)}

#: Database protocols on which one press plays **the whole signal**, the intro
#: once and the repeat once, however few sends the file asks for: the IRP puts
#: the complement frame of Sharp, and the second half of Denon's frame, in the
#: repeat sequence, and the file's ``minSends`` of 1 would send the first frame
#: alone (D64). This is the framing allowance of
#: ``tools/irblaster_oracle_import.py``, which imports this very constant, so
#: the app's playback rule and the oracle's cannot differ.
FULL_SIGNAL_PROTOCOLS = frozenset({"Sharp", "Denon"})

#: The rows ``power.json`` is limited to.
POWER_LIMIT = 3000
#: A label ranks as power at this rank or below (``powerLabelRank`` 0 or 1, the
#: app's default depth of 2).
POWER_MAX_RANK = 1

PLAY_RULES = {
    "ledger": (
        "remotes_io.dart _remoteLedgerSends, the rule that plays a ledger remote "
        "today: the intro once, then the repeat sequence minSends - 1 times, or "
        "minSends times when the intro is empty"
    ),
    "full-signal": (
        "tools/irblaster_oracle_import.py FULL_SIGNAL: the intro once and the "
        "repeat once, or more where the ledger rule asks for more"
    ),
}
POWER_RULE = (
    "SwiftRemote lib/universal_power/power_code.dart powerLabelRank at 6aafd15, "
    "rank 0 or 1 (default depth 2): the label folded to ASCII upper-case with "
    "every other run of characters one underscore; exactly POWER, PWR, OFF, ON, "
    "POWER_OFF, POWER_ON, PWR_OFF or PWR_ON is rank 0; a label holding POWER or "
    "PWR and also OFF or ON is rank 0, one holding POWER or PWR alone is rank 1, "
    "and exactly STANDBY or SLEEP is rank 1. One row per distinct (protocol, "
    "hexcode), counting the distinct ids that use it under such a label, "
    "ordered by that count (most first), then hexcode, protocol index and label"
)

# --- the orders --------------------------------------------------------------------

_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)


def nocase_key(text: str) -> tuple[str, str]:
    """SQLite's ``COLLATE NOCASE``: fold ASCII letters to lower case, compare
    the UTF-8 bytes (so a non-ASCII letter is untouched and code point order is
    byte order), a shorter string first when one is a prefix of the other. The
    exact text breaks a tie, which SQLite leaves to chance."""
    return text.translate(_LOWER), text


def upper_key(text: str) -> str:
    """SQLite's ``UPPER``: ASCII letters only; non-ASCII are left as they are."""
    return text.translate(_UPPER)


def brand_key(name: str) -> str:
    """A brand's file name: ten hex digits of the SHA-1 of its UTF-8 name."""
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


def compact(obj: Any) -> bytes:
    """One JSON document as the API writes it: UTF-8, no spaces, keys sorted,
    one trailing newline (D20)."""
    text = json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        return (text + "\n").encode("utf-8")
    except UnicodeEncodeError as exc:  # a lone surrogate, from a hand-edited file
        raise ValidationError(f"a label or brand is not text UTF-8 can hold: {exc}") from None


# --- the power rule ---------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_POWER_EXACT = frozenset(
    {"POWER", "PWR", "OFF", "ON", "POWER_OFF", "POWER_ON", "PWR_OFF", "PWR_ON"})
_POWER_ALIASES = frozenset({"STANDBY", "SLEEP"})
#: ``secondary`` in the app: every name in it holds POWER or PWR, so the app's
#: own code returns rank 1 for it before it gets here. Kept for the rank.
_POWER_SECONDARY = frozenset({
    "TV_POWER", "SYSTEM_POWER", "MAIN_POWER", "ALL_POWER", "POWER_TOGGLE", "PWR_TOGGLE"})


@lru_cache(maxsize=None)
def power_label_rank(label: str) -> int:
    """``powerLabelRank`` of SwiftRemote's ``power_code.dart``, ported line by
    line (0 and 1 are power, 2 and 3 are not)."""
    norm = _NON_ALNUM.sub("_", label.strip()).translate(_UPPER).strip("_")
    if not norm:
        return 3
    if norm in _POWER_EXACT:
        return 0
    has_power = "POWER" in norm or "PWR" in norm
    has_off_on = "OFF" in norm or "ON" in norm
    if has_power and has_off_on:
        return 0
    if has_power:
        return 1
    if norm in _POWER_ALIASES:
        return 1
    if norm in _POWER_SECONDARY:
        return 2
    return 3


# --- reading one remote file ---------------------------------------------------------------

_FILE_NAME = re.compile(r"([0-9]+)-.+\.json")

def candidates() -> frozenset[str]:
    """DB protocols on which the two readings are different functions at all
    (D53): the only ones whose codes can differ, so the only ones whose signals
    are compiled. Whether they differ *over the codes present* is decided
    afterwards."""
    return frozenset(p for p in FROM_DB_HEX if FROM_DB_HEX_APP.get(p) is not FROM_DB_HEX[p])


def _read_file(root: Path, path: Path) -> dict[str, Any]:
    """Everything the API takes from one remote file. A worker's unit.

    Returns ``problems`` (strings), and otherwise the file's database id, its
    ledger protocol, carrier and ``minSends``, its ``(brand, model)`` pairs, its
    keys as ``(label, DB protocol, hexcode)``, the commit its citations name,
    and the Pronto string of every key of a candidate protocol.
    """
    where = paths.rel(root, path)
    out: dict[str, Any] = {"where": where, "problems": [], "keys": [], "signals": {}}
    try:
        _read_into(out, root, path, where)
    except (LedgerError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        out["problems"].append(f"{where}: {type(exc).__name__}: {exc}")
    return out


def _read_into(out: dict[str, Any], root: Path, path: Path, where: str) -> None:
    named = _FILE_NAME.fullmatch(path.name)
    if named is None:
        out["problems"].append(f"{where}: not named <database id>-<protocol>.json")
        return
    doc = load(path)
    block = doc["protocol"]
    out["ledger"] = block["name"]
    out["carrierHz"] = block["carrierHz"]
    out["minSends"] = block["minSends"]
    out["pairs"] = [split_controls_entry(c) for c in doc.get("controls") or []]
    db_id = int(named[1])
    out["id"] = db_id
    remote = None
    commits: set[str] = set()
    compiled = candidates()
    for name, spec in doc["keys"].items():
        forms = spec["forms"]
        if len(forms) != 1:
            out["problems"].append(f"{where}: key {name} has {len(forms)} forms, not the one "
                                   "the importer writes")
            continue
        source = forms[0]["source"]
        key_id, label, hexcode, protocol = parse_citation(source)
        commits.add(citation_commit(source))
        if key_id != db_id:
            out["problems"].append(f"{where}: key {name} cites remote {key_id}, the file is "
                                   f"remote {db_id}")
        if spec.get("label") != label:
            out["problems"].append(f"{where}: key {name} has label {spec.get('label')!r}, "
                                   f"its citation {label!r}")
        if protocol not in PROTOCOL_INDEX or protocol not in FROM_DB_HEX:
            out["problems"].append(f"{where}: key {name} has the DB protocol {protocol!r}, "
                                   "which the API does not know")
            continue
        out["keys"].append((label, protocol, hexcode))
        if protocol in compiled:
            if remote is None:
                remote = remote_from_doc(doc, path)
            pronto = remote.compile_group(name, PRIMARY)
            code = (protocol, hexcode)
            if out["signals"].setdefault(code, pronto) != pronto:
                out["problems"].append(
                    f"{where}: {protocol} {hexcode} compiles to two different Pronto strings")
    out["commits"] = sorted(commits)


# --- assembling ----------------------------------------------------------------------------


@dataclass
class AppApi:
    """What :func:`build_app_api` made: the files (path relative to
    ``site/app/v1/``, content), what stopped it, and numbers for ``rl app``."""

    files: dict[str, bytes] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _render_brand(item: tuple) -> tuple[str, bytes, bytes]:
    """One brand's ``.k`` and ``.m`` documents. A worker's unit: the item holds
    everything it needs, so nothing is shared with the parent."""
    name, key, rows, models = item
    k_bytes = compact({"brand": name, "r": [[db_id, keys] for db_id, _mask, keys in rows]})
    m_bytes = compact({
        "brand": name,
        "hash": hashlib.sha256(k_bytes).hexdigest()[:6],
        "ids": [[db_id, mask, len(keys)] for db_id, mask, keys in rows],
        "models": [[model, indexes] for model, indexes in models],
    })
    return key, k_bytes, m_bytes


def _play(protocol: str, min_sends: int, intro_empty: bool) -> dict[str, Any]:
    """How one press of a signal shard's Pronto string is played.

    ``helperRepeatPasses`` is what ``_remoteLedgerSends`` (SwiftRemote
    ``lib/utils/remotes_io.dart``) plays for a ledger remote today: the repeat
    sequence ``minSends`` times when the intro is empty, ``minSends - 1`` times
    after the intro otherwise. ``repeatPasses`` is that, raised to one for the
    protocols the oracle plays as a whole signal (``FULL_SIGNAL_PROTOCOLS``).
    """
    helper = min_sends if intro_empty else min_sends - 1
    full = protocol in FULL_SIGNAL_PROTOCOLS
    return {
        "repeatPasses": max(helper, 1) if full else helper,
        "helperRepeatPasses": helper,
        "introEmpty": intro_empty,
        "rule": "full-signal" if full else "ledger",
    }


def _intro_empty(pronto: str) -> bool:
    """Pronto word 2 is the number of burst pairs of the first sequence."""
    return int(pronto.split()[2], 16) == 0


def _unrepresented(root: Path) -> int | None:
    """Keys the importer could not represent, from its report; None if there is
    no report (a corpus that was not imported)."""
    report = root / importer.IMPORT_ROOT / importer.REPORT
    if not report.is_file():
        return None
    return importer.report_totals(report.read_text(encoding="utf-8")).get("Keys skipped")


def source_files(root: Path) -> list[Path]:
    """The remote files the API is a function of: everything under the
    imported database's directory."""
    return [p for p in corpus_files(root)
            if paths.rel(root, p).startswith(paths.APP_API_SOURCE)]


def build_app_api(root: Path) -> AppApi:
    """The whole API for the tree under ``root``, in memory. With no imported
    remote there is nothing to publish and the result holds no file (as the
    index writes no shard then, D69)."""
    result = AppApi()
    files = source_files(root)
    if not files:
        return result

    facts = list(ordered_map(partial(_read_file, root), files))
    for fact in facts:
        result.problems += fact["problems"]
    if result.problems:
        return result

    # -- merge the files into ids -------------------------------------------------
    id_keys: dict[int, set[tuple[str, str, str]]] = defaultdict(set)
    id_pairs: dict[int, set[tuple[str, str]]] = defaultdict(set)
    ledgers: dict[str, set[str]] = defaultdict(set)
    carriers: dict[tuple[str, str], int] = {}
    min_sends: dict[str, set[int]] = defaultdict(set)
    commits: set[str] = set()
    signals: dict[tuple[str, str], tuple[str, str]] = {}
    codes: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        db_id = fact["id"]
        id_keys[db_id].update(fact["keys"])
        id_pairs[db_id].update(fact["pairs"])
        commits.update(fact["commits"])
        for _label, protocol, hexcode in fact["keys"]:
            ledgers[protocol].add(fact["ledger"])
            carriers.setdefault((protocol, fact["ledger"]), fact["carrierHz"])
            if carriers[(protocol, fact["ledger"])] != fact["carrierHz"]:
                result.problems.append(
                    f"{fact['where']}: {protocol} as {fact['ledger']} has carrierHz "
                    f"{fact['carrierHz']}, another file {carriers[(protocol, fact['ledger'])]}")
            min_sends[protocol].add(fact["minSends"])
            codes[protocol].add(hexcode)
        for code, pronto in fact["signals"].items():
            seen = signals.setdefault(code, (pronto, fact["where"]))
            if seen[0] != pronto:
                result.problems.append(
                    f"{code[0]} {code[1]} compiles to different Pronto strings in "
                    f"{seen[1]} and {fact['where']}")
    for protocol, values in sorted(min_sends.items()):
        if len(values) > 1:
            result.problems.append(f"{protocol}: files disagree on minSends: {sorted(values)}")
    if len(commits) > 1:
        result.problems.append(f"the tree cites several commits: {sorted(commits)}")
    if result.problems:
        return result

    # -- which protocols the app reads differently (a computed fact) -------------
    differs: dict[str, bool] = {}
    compiled = candidates()
    for protocol in PROTOCOLS:
        try:
            differs[protocol] = (
                protocol in compiled
                and any(app_reading_differs(protocol, hexcode) for hexcode in codes[protocol]))
        except ValueError as exc:
            result.problems.append(f"{protocol}: a code in the tree is not readable: {exc}")
    if result.problems:
        return result

    # -- the shards of signals ----------------------------------------------------
    protocol_rows: list[dict[str, Any]] = []
    signal_files: dict[str, bytes] = {}
    for protocol in PROTOCOLS:
        names = sorted(ledgers.get(protocol, ()))
        by_ledger = {name: carriers[(protocol, name)] for name in names}
        distinct = sorted(set(by_ledger.values()))
        sends = sorted(min_sends.get(protocol, ()))
        row: dict[str, Any] = {
            "db": protocol,
            "ledger": names,
            "minSends": sends[0] if sends else None,
            "carrierHz": distinct[0] if len(distinct) == 1 else None,
            "carrierHzByLedger": by_ledger,
            "appReadingDiffers": differs[protocol],
            "play": None,
        }
        protocol_rows.append(row)
        if not differs[protocol]:
            continue
        table = {hexcode: signals[(protocol, hexcode)][0] for hexcode in sorted(codes[protocol])}
        empties = {_intro_empty(pronto) for pronto in table.values()}
        if len(empties) != 1:
            result.problems.append(
                f"{protocol}: some signals have an intro and some do not, so one number of "
                "repeat passes cannot be stated for the protocol")
            continue
        row["play"] = _play(protocol, sends[0], empties.pop())
        signal_files[f"s/{protocol}.json"] = compact({
            "p": protocol, "ledger": names, "carrierHz": row["carrierHz"],
            "carrierHzByLedger": by_ledger, "minSends": row["minSends"],
            "play": row["play"], "s": table,
        })
    if result.problems:
        return result

    # -- brands, models and keys -----------------------------------------------------
    sorted_keys: dict[int, list[list[Any]]] = {}
    id_mask: dict[int, int] = {}
    for db_id, keys in id_keys.items():
        if not keys:
            continue
        ordered = sorted(keys, key=lambda k: (upper_key(k[0]), upper_key(k[1]), upper_key(k[2]), k))
        sorted_keys[db_id] = [[label, PROTOCOL_INDEX[protocol], hexcode]
                              for label, protocol, hexcode in ordered]
        id_mask[db_id] = sum(1 << i for i in {PROTOCOL_INDEX[k[1]] for k in keys})

    brand_ids: dict[str, set[int]] = defaultdict(set)
    brand_models: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for db_id, pairs in id_pairs.items():
        if db_id not in sorted_keys:
            continue
        for brand, model in pairs:
            brand_ids[brand].add(db_id)
            brand_models[brand][model].add(db_id)

    brand_names = sorted(brand_ids, key=nocase_key)
    keys_of: dict[str, str] = {}
    for name in brand_names:
        key = brand_key(name)
        if key in keys_of:
            result.problems.append(f"brands {keys_of[key]!r} and {name!r} share the key {key}")
        keys_of[key] = name
    if result.problems:
        return result

    items = []
    brand_rows = []
    n_models = 0
    for name in brand_names:
        ids = sorted(brand_ids[name])
        position = {db_id: i for i, db_id in enumerate(ids)}
        models = [
            (model, sorted(position[i] for i in brand_models[name][model]))
            for model in sorted(brand_models[name], key=nocase_key)
        ]
        n_models += len(models)
        rows = [(db_id, id_mask[db_id], sorted_keys[db_id]) for db_id in ids]
        key = brand_key(name)
        items.append((name, key, rows, models))
        mask = 0
        for db_id in ids:
            mask |= id_mask[db_id]
        brand_rows.append([name, key, mask])

    shards: dict[str, bytes] = {}
    for key, k_bytes, m_bytes in ordered_map(_render_brand, items):
        shards[f"b/{key}.k.json"] = k_bytes
        shards[f"b/{key}.m.json"] = m_bytes

    # -- the power list ---------------------------------------------------------------
    by_code: dict[tuple[int, str], dict[str, tuple[int, set[int]]]] = defaultdict(dict)
    for db_id, rows in sorted_keys.items():
        for label, index, hexcode in rows:
            rank = power_label_rank(label)
            if rank <= POWER_MAX_RANK:
                by_code[(index, hexcode)].setdefault(label, (rank, set()))[1].add(db_id)
    power = []
    for (index, hexcode), labels in by_code.items():
        users = set().union(*(ids for _rank, ids in labels.values()))
        label = min(labels, key=lambda lab: (
            labels[lab][0], -len(labels[lab][1]), upper_key(lab), lab))
        power.append([index, hexcode, label, len(users), labels[label][0]])
    power.sort(key=lambda r: (-r[3], r[1], r[0], r[2]))
    power_total = len(power)
    power = power[:POWER_LIMIT]

    # -- the files ---------------------------------------------------------------------------
    n_keys = sum(len(k) for k in sorted_keys.values())
    body = {
        "brands.json": compact(brand_rows),
        "power.json": compact(power),
        **shards,
        **signal_files,
    }
    digest = hashlib.sha256()
    for name in sorted(body):
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(body[name]).digest())
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "dataVersion": digest.hexdigest()[:12],
        "source": {"name": importer.UPSTREAM, "commit": next(iter(commits))},
        "counts": {
            "brands": len(brand_names), "models": n_models,
            "remotes": len(sorted_keys), "keys": n_keys,
        },
        "protocols": protocol_rows,
        "paths": {
            "brands": "brands.json", "brandModels": "b/{key}.m.json",
            "brandKeys": "b/{key}.k.json", "signals": "s/{db}.json", "power": "power.json",
        },
        "playRules": PLAY_RULES,
        "power": {
            "columns": ["protoIdx", "hex", "label", "nIds", "rank"],
            "limit": POWER_LIMIT, "rows": len(power), "total": power_total,
            "rule": POWER_RULE,
        },
    }
    result.files = {"manifest.json": compact(manifest), **dict(sorted(body.items()))}
    result.stats = {
        "files": len(result.files), "bytes": sum(len(b) for b in result.files.values()),
        "brands": len(brand_names), "models": n_models, "remotes": len(sorted_keys),
        "keys": n_keys, "signalProtocols": [p for p in PROTOCOLS if differs[p]],
        "power": power_total, "unrepresentedKeys": _unrepresented(root),
        "dataVersion": manifest["dataVersion"],
    }
    return result


def write_app_api(root: Path, out_root: Path) -> list[str]:
    """The ``app`` stage: write ``site/app/v1/`` under ``out_root``. The
    directory is wholly this stage's, so a file the data no longer yields is
    removed rather than left to be reported as an orphan."""
    return write_result(build_app_api(root), out_root)


def write_result(result: AppApi, out_root: Path) -> list[str]:
    if result.problems:
        return result.problems
    target = out_root / paths.APP_API
    shutil.rmtree(target, ignore_errors=True)
    for rel, content in result.files.items():
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
    return []
