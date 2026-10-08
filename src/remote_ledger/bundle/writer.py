"""The SQLite file of the bundle, written deterministically (D89, D93).

Two builds of one tree are the same bytes, because every degree of freedom is
fixed: the page size, the text encoding, the auto-vacuum mode, the application
and user version, the order of every insert (each table is written sorted by its
key, in one transaction), and a final ``VACUUM`` that rewrites the file densely
in the order of the schema. Nothing is read from the clock, the machine or the
environment: the temporary directory the file is built in never reaches it.

What is **not** fixed is the SQLite library: its version number is in the
header (offset 96), so two machines with different libraries write different
bytes for one tree. ``dataVersion`` (``content_digest``) is the digest of the
*rows*, so it is the same on both; the SHA-256 of the file is not, and the
manifest says which file it signed.

Only what Android's SQLite can read: the oldest it ships from API 30 is 3.28,
so the schema holds no ``STRICT`` table (3.37), no generated column (3.31), no
``RETURNING`` and no ``FTS`` or any other module.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .catalog import GRAM, Assembled
from .corpus import RULES, TIERS

SCHEMA_VERSION = 1
PAGE_SIZE = 4096
#: ``PRAGMA application_id``: ``RLB`` and the format's version, in ASCII.
APPLICATION_ID = 0x524C4231
#: The oldest SQLite the schema is written for: Android 11 (API 30).
SQLITE_MIN_VERSION = "3.28.0"

SCHEMA = """
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE sources (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,
  spdx           TEXT,
  licence_kind   TEXT NOT NULL,
  licence_notes  TEXT NOT NULL,
  licence_text   TEXT,
  upstream_url   TEXT,
  upstream_commit TEXT,
  remote_count   INTEGER NOT NULL,
  key_count      INTEGER NOT NULL
);

CREATE TABLE vocab_groups (
  id   INTEGER PRIMARY KEY,
  key  TEXT NOT NULL,
  name TEXT NOT NULL,
  ord  INTEGER NOT NULL
);

CREATE TABLE vocab_keys (
  id     INTEGER PRIMARY KEY,
  key    TEXT NOT NULL,
  grp    INTEGER NOT NULL,
  ord    INTEGER NOT NULL,
  name   TEXT NOT NULL,
  icon   TEXT,
  glyph  TEXT,
  color  TEXT,
  repeat INTEGER NOT NULL
);

CREATE TABLE brands (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  norm        TEXT NOT NULL,
  first_model INTEGER NOT NULL,
  model_count INTEGER NOT NULL
);
CREATE INDEX brands_norm ON brands (norm);

CREATE TABLE brand_aliases (
  alias    TEXT NOT NULL,
  norm     TEXT NOT NULL,
  brand_id INTEGER NOT NULL,
  PRIMARY KEY (norm, brand_id)
) WITHOUT ROWID;

CREATE TABLE models (
  id       INTEGER PRIMARY KEY,
  brand_id INTEGER NOT NULL,
  name     TEXT NOT NULL,
  kind     INTEGER NOT NULL
);

CREATE TABLE controls (
  model_id  INTEGER NOT NULL,
  remote_id INTEGER NOT NULL,
  PRIMARY KEY (model_id, remote_id)
) WITHOUT ROWID;

CREATE TABLE remotes (
  id                   INTEGER PRIMARY KEY,
  ref                  TEXT NOT NULL,
  brand_id             INTEGER,
  model                TEXT,
  source               INTEGER NOT NULL,
  tier                 INTEGER NOT NULL,
  key_count            INTEGER NOT NULL,
  protocol             TEXT,
  carrier_hz           INTEGER NOT NULL,
  repeat_passes        INTEGER NOT NULL,
  helper_repeat_passes INTEGER NOT NULL,
  intro_empty          INTEGER NOT NULL,
  rule                 TEXT NOT NULL
);
CREATE UNIQUE INDEX remotes_ref ON remotes (ref);

CREATE TABLE remote_refs (
  ref       TEXT PRIMARY KEY,
  remote_id INTEGER NOT NULL,
  first_n   INTEGER NOT NULL,
  key_count INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE keys (
  remote_id  INTEGER NOT NULL,
  n          INTEGER NOT NULL,
  canon      INTEGER,
  label      TEXT,
  signal_id  INTEGER NOT NULL,
  confidence INTEGER NOT NULL,
  PRIMARY KEY (remote_id, n)
) WITHOUT ROWID;

CREATE TABLE signals (
  id    INTEGER PRIMARY KEY,
  words BLOB NOT NULL
);

CREATE TABLE ngram (
  gram TEXT PRIMARY KEY,
  ids  BLOB NOT NULL
) WITHOUT ROWID;

CREATE TABLE excluded_brands (
  name    TEXT PRIMARY KEY,
  norm    TEXT NOT NULL,
  api_key TEXT
) WITHOUT ROWID;
"""

#: Every table but ``meta``, with the order its rows are read in for the digest:
#: its primary key, the order they were written in.
DIGEST_TABLES: tuple[tuple[str, str], ...] = (
    ("sources", "id"), ("vocab_groups", "id"), ("vocab_keys", "id"), ("brands", "id"),
    ("brand_aliases", "norm, brand_id"), ("models", "id"), ("controls", "model_id, remote_id"), ("remotes", "id"),
    ("remote_refs", "ref"), ("keys", "remote_id, n"), ("signals", "id"), ("ngram", "gram"),
    ("excluded_brands", "name"),
)

PLAY_RULE_TEXT = {
    "ledger": ("press = intro once, then the repeat sequence helperRepeatPasses times "
               "(which is repeatPasses)"),
    "full-signal": ("press = intro once, then the repeat sequence repeatPasses times, "
                    "at least once: the protocol's second frame is in the repeat sequence"),
}


MERGE_RULE_TEXT = (
    "a protocol fragment of a device with no test key (POWER, POWER_OFF, POWER_ON, VOLUME_UP, "
    "MUTE) is folded into a sibling fragment that has one and the same carrier and play rule; "
    "its keys follow the sibling's, and remote_refs maps every ledger ref to the remote that "
    "carries its keys")


def _hexed(row: tuple) -> list[Any]:
    return [v.hex() if isinstance(v, bytes) else v for v in row]


def content_digest(conn: sqlite3.Connection) -> str:
    """``dataVersion``: twelve hex digits of the SHA-256 of every row of every table
    but ``meta``, each table in key order, as JSON with ASCII escapes and blobs as
    hex. It is the version of the *content*, which the file's own bytes cannot be (a
    file cannot hold a hash of itself), and it does not depend on the SQLite library
    that wrote the pages."""
    digest = hashlib.sha256()
    for table, order in DIGEST_TABLES:
        digest.update(f"\n{table}\n".encode("ascii"))
        cursor = conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
        while True:
            rows = cursor.fetchmany(20_000)
            if not rows:
                break
            digest.update(json.dumps([_hexed(r) for r in rows], separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii"))
    return digest.hexdigest()[:12]


def _meta(assembled: Assembled, vocabulary_version: int, selection: str) -> dict[str, str]:
    rows = {
        "schemaVersion": str(SCHEMA_VERSION),
        "vocabularyVersion": str(vocabulary_version),
        "profile": assembled.profile,
        "selection": selection,
        "tiers": ",".join(TIERS),
        "gramLength": str(GRAM),
        "normalisation": "NFKD, lower case, keep letters and digits",
        "sqliteMinVersion": SQLITE_MIN_VERSION,
        "count.brands": str(len(assembled.brands)),
        "count.brandAliases": str(len(assembled.brand_aliases)),
        "count.models": str(len(assembled.models)),
        "count.controls": str(len(assembled.controls)),
        "count.remotes": str(len(assembled.remotes)),
        "count.remoteRefs": str(len(assembled.remote_refs)),
        "count.keys": str(len(assembled.keys)),
        "count.signals": str(len(assembled.signals)),
        "count.excludedBrands": str(len(assembled.excluded)),
    }
    for rule in RULES:
        rows[f"playRule.{rule}"] = PLAY_RULE_TEXT[rule]
    rows["mergeRule"] = MERGE_RULE_TEXT
    return rows


def write_database(assembled: Assembled, vocabulary_version: int, selection: str) -> tuple[bytes, str]:
    """The bundle's bytes and its ``dataVersion``."""
    with tempfile.TemporaryDirectory(prefix="rl-bundle-") as tmp:
        path = Path(tmp) / "bundle.sqlite"
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute(f"PRAGMA page_size = {PAGE_SIZE}")
            conn.execute("PRAGMA encoding = 'UTF-8'")
            conn.execute("PRAGMA auto_vacuum = NONE")
            conn.execute("PRAGMA journal_mode = OFF")
            conn.execute("PRAGMA synchronous = OFF")
            conn.executescript(SCHEMA)
            _insert(conn, assembled)
            data_version = content_digest(conn)
            meta = _meta(assembled, vocabulary_version, selection)
            meta["dataVersion"] = data_version
            conn.execute("BEGIN")
            conn.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))
            conn.execute("COMMIT")
            conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("VACUUM")
        finally:
            conn.close()
        return path.read_bytes(), data_version


def _insert(conn: sqlite3.Connection, a: Assembled) -> None:
    def put(table: str, rows: list[tuple], width: int) -> None:
        conn.execute("BEGIN")
        conn.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * width)})", rows)
        conn.execute("COMMIT")

    put("sources", [
        (s["id"], s["name"], s["spdx"], s["licenceKind"], "\n".join(s["licenceNotes"]),
         s["licenceText"],
         s["upstreamUrl"], s["upstreamCommit"], s["bundleRemotes"], s["bundleKeys"])
        for s in a.sources
    ], 10)
    put("vocab_groups", a.vocab_groups, 4)
    put("vocab_keys", a.vocab_keys, 9)
    put("brands", a.brands, 5)
    put("brand_aliases", a.brand_aliases, 3)
    put("models", a.models, 4)
    put("controls", a.controls, 2)
    put("remotes", a.remotes, 13)
    put("remote_refs", a.remote_refs, 4)
    put("keys", a.keys, 6)
    put("signals", a.signals, 2)
    put("ngram", a.ngram, 2)
    put("excluded_brands", [(name, norm if not norm.startswith("\0") else "", api)
                            for norm, name, api in a.excluded], 3)
