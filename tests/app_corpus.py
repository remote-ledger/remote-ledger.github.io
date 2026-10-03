"""A small imported database, written by the importer itself, for the tests of
the app API (D74 to D80).

Nothing here is hand-written in the importer's output format: a SQL dump with
the real schema's four tables goes through ``write_import`` (the command's own
code), so every file the API generator reads has the shape ``rl import
irblaster`` gives it, citations and ``controls`` included. The data is chosen
to hold each case the API's rules name: brands that differ in case, in
Unicode, by a trailing dot and by the characters that sort between the capitals
and the small letters; one id under many brands and models; one id with two
ledger protocols; a protocol the app reads differently and several that it does
not; duplicate labels; ``??`` labels; labels of every power rank; a key the
hex maps refuse and an id that has nothing left.
"""

from __future__ import annotations

from pathlib import Path

from remote_ledger.irblaster.importer import write_import

COMMIT = "6aafd15e1c95cf494ac729339b9a4701a4ab8f0a"

#: db id -> {"models": [(brand, model)], "keys": [(label, hexcode, DB protocol)]}
REMOTES: dict[int, dict] = {
    # One id under many brands and models, with the labels that make trouble:
    # `??` twice on two codes, one label in two cases on one code, a label with
    # a quote and a tab, a power label of each rank, a label that is no power.
    1: {"models": [
            ("ACME", "TV-1"), ("ACME", "tv-1"), ("ACME", "TV_2"), ("ACME", "TV[3]"),
            ("Acme", "A1"), ("ZED", "Z1"), ("ZED", "Z2"), ("Ünï", "Ω"), ("Üno", "a"),
            ("T.V.E.", "X"), ("B_C", "m"), ("B[C", "m"), ("BAC", "m"), (" LEAD", "m"),
            # two brands that str.casefold() would put in the other order
            ("Éz", "m"), ("éa", "m"),
        ],
        "keys": [("POWER", "00FF609F", "NEC"), ("power", "00FF609F", "NEC"),
                 ("VOL+", "00FFE01F", "NEC"), ("??", "00FFA05F", "NEC"),
                 ("??", "00FF50AF", "NEC"), ("OK", "00FF609F", "NEC"),
                 ("O'K\t", "00FFE01F", "NEC"), ("TV POWER", "00FF50AF", "NEC"),
                 ("1", "00FFA05F", "NEC"), ("STANDBY", "00FF30CF", "NEC"),
                 # ASCII-only UPPER: a capital S, then `[` and `_`, which sort between
                 # the capitals and the small letters, and two letters that
                 # str.upper() would move (ß becomes SS)
                 ("sa", "20DFC03F", "NEC"), ("Sa", "20DF22DD", "NEC"),
                 ("SZ", "20DF807F", "NEC"), ("S[", "20DF8877", "NEC"),
                 ("S_", "20DF08F7", "NEC"), ("ß", "20DF10EF", "NEC"),
                 ("é", "20DF40BF", "NEC")]},
    # Two DB protocols that land on two ledger protocols: two files, one id
    2: {"models": [("ZED", "Z1"), ("ACME", "A1")],
        "keys": [("POWER", "00FF609F", "NEC"), ("POWER", "811", "RC5"),
                 ("1", "810", "RC5"), ("VOL+", "E0E0807F", "NECx2")]},
    # The protocols the app reads differently
    3: {"models": [("SONY", "KD-1"), ("Sony", "KD-1")],
        "keys": [("POWER", "A50", "SONY12"), ("VOL+", "5D0", "SONY12"),
                 ("POWER", "2818", "SONY15"), ("MUTE", "0CB9C", "SONY20"),
                 ("POWER", "55085508", "Pioneer"), ("MUTE", "55885588", "Pioneer"),
                 ("POWER", "8644", "Sharp"), ("POWER", "4518", "Denon"),
                 ("POWER", "C018", "JVC")]},
    4: {"models": [("SONY", "KD-2")],
        "keys": [("POWER", "A50", "SONY12"), ("PWR", "2888", "Proton"),
                 ("POWER", "300", "Thomson7"), ("POWER", "38863BFA05C", "RCC2026"),
                 ("POWER", "00FF609F", "NEC")]},
    5: {"models": [("SONY", "KD-3"), ("SONY", "KD-1")],
        "keys": [("TV POWER", "A50", "SONY12"), ("Standby", "301", "Thomson7"),
                 ("1", "090", "SONY12")]},
    # Where the app reads as the ledger does: no signal shard
    6: {"models": [("PHIL", "RC-1")],
        "keys": [("POWER", "CA0", "F12_relaxed"), ("VOL+", "ED8", "RECS80"),
                 ("ON", "EE0", "RECS80"), ("Power Off", "EA0", "RECS80"),
                 ("POWER", "0C00079", "Samsung36"), ("POWER", "0038", "RC6")]},
    # Duplicate labels: one label on two codes, two on one
    7: {"models": [("DUP", "D")],
        "keys": [("OK", "00FF609F", "NEC"), ("OK", "00FFE01F", "NEC"),
                 ("ok", "00FFE01F", "NEC"), ("Ok", "00FF609F", "NEC"),
                 ("VOL +", "00FFA05F", "NEC"), ("VOL+", "00FF50AF", "NEC")]},
    # A key the NEC map refuses (byte 4 is not the complement of byte 3) beside
    # three it holds: the id stays, the key goes
    8: {"models": [("HALF", "H")],
        "keys": [("POWER", "00FF609F", "NEC"), ("BROKEN", "20DF10EE", "NEC"),
                 ("VOL+", "00FFE01F", "NEC")]},
    # Nothing representable: no file, and the brand it alone lists is not in the API
    9: {"models": [("VANISH", "V1"), ("ACME", "LOST")],
        "keys": [("BROKEN", "20DF10EE", "NEC")]},
}


def q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def sql_dump(remotes: dict[int, dict] = REMOTES) -> str:
    """The real dump's four tables, with the rows of ``remotes``."""
    out = [
        "PRAGMA foreign_keys=OFF;", "BEGIN TRANSACTION;",
        "CREATE TABLE brands (name TEXT PRIMARY KEY);",
        'CREATE TABLE IF NOT EXISTS "remotes" (id INTEGER PRIMARY KEY);',
        'CREATE TABLE IF NOT EXISTS "keys" (id INTEGER NOT NULL, label TEXT NOT NULL, '
        "hexcode TEXT NOT NULL, protocol TEXT NOT NULL, "
        "PRIMARY KEY (id, label, hexcode, protocol));",
        'CREATE TABLE IF NOT EXISTS "models" (brand TEXT NOT NULL, model TEXT NOT NULL, '
        "id INTEGER NOT NULL, PRIMARY KEY (brand, model, id));",
    ]
    for db_id, spec in remotes.items():
        out.append(f"INSERT INTO remotes VALUES({db_id});")
        for brand in sorted({b for b, _ in spec["models"]}):
            out.append(f"INSERT OR IGNORE INTO brands VALUES({q(brand)});")
        for brand, model in spec["models"]:
            out.append(f"INSERT INTO models VALUES({q(brand)},{q(model)},{db_id});")
        for label, hexcode, protocol in spec["keys"]:
            out.append(f"INSERT INTO keys VALUES({db_id},{q(label)},{q(hexcode)},{q(protocol)});")
    out += ["CREATE INDEX idx_keys_id ON keys(id);", "CREATE INDEX idx_models_brand ON models(brand);",
            "COMMIT;"]
    return "\n".join(out) + "\n"


def make_import(root: Path, remotes: dict[int, dict] = REMOTES) -> Path:
    """Import ``remotes`` into ``root/remotes/irblaster/`` through the importer
    and return ``root``. The SQL dump is left in ``root/../checkout``."""
    checkout = root.parent / f"checkout-{root.name}"
    (checkout / "assets" / "db_src").mkdir(parents=True, exist_ok=True)
    (checkout / "assets" / "db_src" / "swiftremote.sql").write_text(
        sql_dump(remotes), encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    write_import(root, checkout, COMMIT)
    return root
