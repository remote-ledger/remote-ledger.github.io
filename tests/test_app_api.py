"""The app API v1 (D74 to D80): ``site/app/v1/``, the files SwiftRemote reads
instead of bundling its database.

Three kinds of test, and the mutation checks that show the first two have teeth:

* **a reference model.** A small imported database (``tests/app_corpus.py``,
  written by the importer itself) is built into the API, and the API is compared
  with what a few lines of plain Python say the database holds, in the ASCII
  orders SQLite uses. The reference shares no code with the generator but the
  hex tables it must be a function of;
* **the real tree.** The committed ``remotes/irblaster/`` is read independently
  (plain ``json``), ``parse_citation`` is round-tripped over every one of its
  keys, and the API built from it is checked for the facts the app relies on:
  which protocols are read differently, how a press of each is played, brand
  keys that do not collide, files that are what ``rl build`` wrote;
* **the gate.** ``rl build --check`` and ``rl app --check`` report drift, a
  missing file and an orphan in ``site/app/v1``, and ``rl site --check`` does
  not mistake that directory for its own.

``test_mutations_are_detected`` breaks the generator in a dozen ways and requires
the reference comparison to fail for each.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
import string
import sys
from functools import reduce
from operator import or_
from pathlib import Path

import pytest

from remote_ledger import app_api, cli, generators, paths, parallel, pronto, protocols
from remote_ledger.irblaster.importer import FROM_DB_HEX, FROM_DB_HEX_APP, HOW, MIN_SENDS
from app_corpus import REMOTES, make_import
from real_irblaster import REAL, read_real_tree

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded:DeprecationWarning"
)

#: An independent copy of the protocol order (it is frozen; see app_api.PROTOCOLS).
PROTOCOLS = [
    "Denon", "F12_relaxed", "JVC", "NEC", "NEC2", "NECx1", "NECx2", "Pioneer", "Proton",
    "RC5", "RC6", "RCA_38", "RCC0082", "RCC2026", "REC80", "RECS80", "RECS80_L",
    "Samsung36", "Sharp", "SONY12", "SONY15", "SONY20", "Thomson7",
]
#: The ten the app reads differently, and what one press of each plays
#: (``repeatPasses``, ``helperRepeatPasses``, ``rule``).
SIGNAL_PROTOCOLS = {
    "SONY12": (3, 3, "ledger"), "SONY15": (3, 3, "ledger"), "SONY20": (3, 3, "ledger"),
    "Pioneer": (0, 0, "ledger"), "JVC": (0, 0, "ledger"),
    "Sharp": (1, 0, "full-signal"), "Denon": (1, 0, "full-signal"),
    "Thomson7": (2, 2, "ledger"), "Proton": (1, 1, "ledger"), "RCC2026": (1, 1, "ledger"),
}
#: A key the NEC map refuses: the importer skips it.
REFUSED = {("20DF10EE", "NEC")}


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


_TOOLS: dict = {}


def load_tool(name: str):
    """A tool of ``tools/`` by file name. One that another test module already
    loaded is used as it is: replacing it in ``sys.modules`` would leave that
    module's worker processes unpickling functions of a module that is gone."""
    if name in sys.modules:
        return sys.modules[name]
    if name not in _TOOLS:
        spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _TOOLS[name] = module
    return _TOOLS[name]


# --- the reference model --------------------------------------------------------------------


def ascii_upper(text: str) -> str:
    return "".join(chr(ord(c) - 32) if "a" <= c <= "z" else c for c in text)


def ascii_lower(text: str) -> str:
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def nocase(text: str):
    return (ascii_lower(text).encode("utf-8"), text.encode("utf-8"))


def reference(remotes):
    """What the database holds, once the key the hex map refuses is gone: ``held``
    (id -> {(label, DB protocol, hexcode)}) and ``brands`` (brand -> model -> ids)."""
    held = {}
    for db_id, spec in remotes.items():
        keys = {(label, protocol, hexcode) for label, hexcode, protocol in spec["keys"]
                if (hexcode, protocol) not in REFUSED}
        if keys:
            held[db_id] = keys
    brands: dict[str, dict[str, set[int]]] = {}
    for db_id, spec in remotes.items():
        for brand, model in spec["models"]:
            if db_id in held:
                brands.setdefault(brand, {}).setdefault(model, set()).add(db_id)
    return held, brands


def reference_power(held, dart_rank):
    by: dict = {}
    for db_id, keys in held.items():
        for label, protocol, hexcode in keys:
            rank = dart_rank(label)
            if rank <= 1:
                by.setdefault((PROTOCOLS.index(protocol), hexcode), {}).setdefault(
                    label, (rank, set()))[1].add(db_id)
    rows = []
    for (index, hexcode), labels in by.items():
        users = set().union(*(ids for _rank, ids in labels.values()))
        label = min(labels, key=lambda lab: (labels[lab][0], -len(labels[lab][1]),
                                             ascii_upper(lab), lab))
        rows.append([index, hexcode, label, len(users), labels[label][0]])
    rows.sort(key=lambda r: (-r[3], r[1], r[0], r[2]))
    return rows


def independent_pronto(protocol: str, hexcode: str) -> str:
    """The wire reading compiled with the registry's encoder: no remote file, no
    form loader, none of the generator's code."""
    ledger, device, subdevice, function = FROM_DB_HEX[protocol](hexcode)
    entry = protocols.REGISTRY[ledger]
    signal = entry.encode(device=device, subdevice=subdevice, function=function,
                          carrier_hz=entry.nominal_carrier_hz, unit_us=None)
    return pronto.encode(signal)


def app_differs(protocol: str, codes) -> bool:
    for hexcode in codes:
        wire = tuple(FROM_DB_HEX[protocol](hexcode))
        try:
            app = tuple(FROM_DB_HEX_APP[protocol](hexcode))
        except (ValueError, KeyError):
            return True
        if app != wire:
            return True
    return False


def check_api(files: dict[str, bytes], remotes) -> None:
    """Every fact the API states about ``remotes``, against the reference model.
    Raises AssertionError at the first one that is wrong."""
    held, brands = reference(remotes)
    tool = load_tool("app_api_vs_sql")

    def j(path):
        return json.loads(files[path])

    manifest = j("manifest.json")
    assert manifest["schemaVersion"] == 1
    assert [p["db"] for p in manifest["protocols"]] == PROTOCOLS
    assert manifest["paths"] == {
        "brands": "brands.json", "brandModels": "b/{key}.m.json",
        "brandKeys": "b/{key}.k.json", "signals": "s/{db}.json", "power": "power.json"}

    names = sorted(brands, key=nocase)
    rows = j("brands.json")
    assert [r[0] for r in rows] == names
    n_models = 0
    for name, key, mask in rows:
        assert key == hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        ids = sorted({i for ids in brands[name].values() for i in ids})
        bit = lambda i: reduce(or_, (1 << PROTOCOLS.index(p) for _l, p, _h in held[i]))
        assert mask == reduce(or_, (bit(i) for i in ids))
        k_bytes = files[f"b/{key}.k.json"]
        k, m = json.loads(k_bytes), j(f"b/{key}.m.json")
        assert k["brand"] == name and m["brand"] == name
        assert m["hash"] == hashlib.sha256(k_bytes).hexdigest()[:6]
        assert [r[0] for r in k["r"]] == ids == [r[0] for r in m["ids"]]
        for (db_id, keys), (_id, id_mask, n_keys) in zip(k["r"], m["ids"]):
            want = sorted(held[db_id], key=lambda t: (
                ascii_upper(t[0]), ascii_upper(t[1]), ascii_upper(t[2]), t))
            assert keys == [[label, PROTOCOLS.index(p), h] for label, p, h in want]
            assert n_keys == len(want) and id_mask == bit(db_id)
        models = sorted(brands[name], key=nocase)
        assert [model for model, _ix in m["models"]] == models
        for model, ix in m["models"]:
            assert [ids[i] for i in ix] == sorted(brands[name][model])
        n_models += len(models)
    assert manifest["counts"] == {
        "brands": len(names), "models": n_models, "remotes": len(held),
        "keys": sum(len(v) for v in held.values())}

    # which protocols are read differently is computed from the two tables
    codes: dict[str, set[str]] = {}
    for keys in held.values():
        for _label, protocol, hexcode in keys:
            codes.setdefault(protocol, set()).add(hexcode)
    differing = {p for p in PROTOCOLS if p in codes and app_differs(p, codes[p])}
    assert {p["db"] for p in manifest["protocols"] if p["appReadingDiffers"]} == differing
    assert {p[2:-5] for p in files if p.startswith("s/")} == differing
    for entry in manifest["protocols"]:
        name = entry["db"]
        if name not in differing:
            assert entry["play"] is None
            continue
        shard = j(f"s/{name}.json")
        assert shard["s"] == {h: independent_pronto(name, h) for h in sorted(codes[name])}
        passes, helper, rule = SIGNAL_PROTOCOLS[name]
        assert entry["play"]["repeatPasses"] == passes == shard["play"]["repeatPasses"]
        assert entry["play"]["helperRepeatPasses"] == helper
        assert entry["play"]["rule"] == rule
        assert entry["minSends"] == shard["minSends"] == MIN_SENDS.get(name, 1)
        assert shard["p"] == name and shard["ledger"] == entry["ledger"]
        # the intro is empty exactly when the first sequence has no pairs
        first = shard["s"][min(shard["s"])].split()
        assert entry["play"]["introEmpty"] == (int(first[2], 16) == 0)

    power = j("power.json")
    assert power == reference_power(held, tool.dart_rank)[:3000]
    assert manifest["power"]["total"] == len(reference_power(held, tool.dart_rank))

    digest = hashlib.sha256()
    for path in sorted(p for p in files if p != "manifest.json"):
        digest.update(path.encode() + b"\0" + hashlib.sha256(files[path]).digest())
    assert manifest["dataVersion"] == digest.hexdigest()[:12]


# --- the synthetic corpus -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    return make_import(tmp_path_factory.mktemp("synth") / "ledger")


@pytest.fixture(scope="module")
def api(synth):
    parallel.configure(None)
    built = app_api.build_app_api(synth)
    assert built.problems == []
    return built


def test_the_api_is_what_the_reference_model_says(api):
    check_api(api.files, REMOTES)


def test_the_files_of_the_corpus(api):
    held, brands = reference(REMOTES)
    blocks = [name for name in api.files if name.startswith("b/")]
    assert len(blocks) == 2 * len(brands)
    assert sorted(api.files) == sorted(
        ["manifest.json", "brands.json", "power.json", *blocks,
         *(f"s/{p}.json" for p in SIGNAL_PROTOCOLS)])
    assert api.stats["files"] == len(api.files)
    assert api.stats["signalProtocols"] == sorted(SIGNAL_PROTOCOLS, key=PROTOCOLS.index)


def test_brands_are_in_ascii_nocase_order_and_the_vanished_brand_is_absent(api):
    names = [row[0] for row in json.loads(api.files["brands.json"])]
    # fold to lower: `[` and `_` sort before the letters, a tie falls to the bytes,
    # non-ASCII letters come after everything ASCII
    assert names == [" LEAD", "ACME", "Acme", "B[C", "B_C", "BAC", "DUP", "HALF", "PHIL",
                     "SONY", "Sony", "T.V.E.", "ZED", "Éz", "Üno", "Ünï", "éa"]
    assert "VANISH" not in names


def test_models_are_in_ascii_nocase_order_and_a_lost_pair_is_absent(api):
    key = app_api.brand_key("ACME")
    m = json.loads(api.files[f"b/{key}.m.json"])
    assert [model for model, _ix in m["models"]] == ["A1", "TV-1", "tv-1", "TV[3]", "TV_2"]
    assert "LOST" not in [model for model, _ix in m["models"]]
    assert [(row[0], row[2]) for row in m["ids"]] == [
        (1, len(REMOTES[1]["keys"])), (2, len(REMOTES[2]["keys"]))]


def test_keys_of_an_id_are_ordered_by_ascii_upper_label_protocol_hex(api):
    key = app_api.brand_key("DUP")
    k = json.loads(api.files[f"b/{key}.k.json"])
    # OK/Ok/ok on two codes: UPPER ties break by UPPER hex, then by the text
    assert k["r"] == [[7, [
        ["OK", 3, "00FF609F"], ["Ok", 3, "00FF609F"], ["OK", 3, "00FFE01F"],
        ["ok", 3, "00FFE01F"], ["VOL +", 3, "00FFA05F"], ["VOL+", 3, "00FF50AF"],
    ]]]
    one = json.loads(api.files[f"b/{app_api.brand_key('ZED')}.k.json"])
    labels = [row[0] for row in dict(one["r"])[1]]
    # `S[` and `S_` sort after `SZ` (the capitals), not before `Sa`: UPPER, not lower;
    # `Sa` before `sa` because their hexcodes order so; `STANDBY` between
    start = labels.index("Sa")
    assert labels[start:start + 6] == ["Sa", "sa", "STANDBY", "SZ", "S[", "S_"]
    # non-ASCII letters are left alone, so they sort by their bytes after every ASCII one
    assert labels[-2:] == ["ß", "é"]


def test_one_id_under_many_brands_is_the_same_rows_in_each(api):
    rows = None
    for brand in ("ACME", "Acme", "ZED", "Ünï", "T.V.E.", "B_C"):
        k = json.loads(api.files[f"b/{app_api.brand_key(brand)}.k.json"])
        mine = dict(k["r"])[1]
        rows = rows or mine
        assert mine == rows


def test_an_id_with_two_ledger_protocols_is_one_id_with_both_protocols(api):
    k = json.loads(api.files[f"b/{app_api.brand_key('ZED')}.k.json"])
    two = dict(k["r"])[2]
    assert {row[1] for row in two} == {PROTOCOLS.index(p) for p in ("NEC", "NECx2", "RC5")}


def test_that_id_is_two_files_of_the_tree(synth):
    names = sorted(p.name for p in (synth / "remotes/irblaster").rglob("2-*.json"))
    assert names == ["2-NEC1.json", "2-NECx2.json", "2-RC5.json"]


def test_the_key_the_hex_map_refuses_is_not_in_the_api(api):
    everything = b"".join(api.files.values())
    assert b"20DF10EE" not in everything and b"BROKEN" not in everything
    # id 8 stays with the keys that were representable; id 9 and its brand are gone
    assert api.stats["remotes"] == 8
    assert b'"VANISH"' not in api.files["brands.json"]


def test_the_manifest_computes_which_protocols_the_app_reads_differently(api):
    manifest = json.loads(api.files["manifest.json"])
    by = {p["db"]: p for p in manifest["protocols"]}
    assert {n for n, p in by.items() if p["appReadingDiffers"]} == set(SIGNAL_PROTOCOLS)
    assert by["RC5"]["play"] is None and by["NEC"]["appReadingDiffers"] is False
    # a protocol with no key in the corpus is listed, empty and not different
    assert by["REC80"] == {
        "db": "REC80", "ledger": [], "minSends": None, "carrierHz": None,
        "carrierHzByLedger": {}, "appReadingDiffers": False, "play": None}
    assert by["SONY12"]["play"] == {
        "repeatPasses": 3, "helperRepeatPasses": 3, "introEmpty": True, "rule": "ledger"}
    assert by["Sharp"]["play"] == {
        "repeatPasses": 1, "helperRepeatPasses": 0, "introEmpty": False, "rule": "full-signal"}


def test_a_protocol_the_two_readings_agree_on_has_no_shard(synth, monkeypatch):
    """Computed over the codes present, not read from a table: a second function
    object that returns the same fields leaves the reading as it was."""
    sony12 = FROM_DB_HEX["SONY12"]
    monkeypatch.setitem(FROM_DB_HEX_APP, "SONY12", lambda hexcode: sony12(hexcode))
    built = app_api.build_app_api(synth)
    manifest = json.loads(built.files["manifest.json"])
    assert not {p["db"]: p for p in manifest["protocols"]}["SONY12"]["appReadingDiffers"]
    assert "s/SONY12.json" not in built.files and "s/SONY15.json" in built.files
    # and one code that differs makes the protocol differ, whatever the rest do
    monkeypatch.setitem(
        FROM_DB_HEX_APP, "SONY12",
        lambda hexcode: sony12(hexcode) if hexcode != "5D0" else ("Sony12", 9, None, 9))
    built = app_api.build_app_api(synth)
    assert "s/SONY12.json" in built.files


def test_the_full_signal_rule_is_the_one_the_oracle_plays():
    oracle = load_tool("irblaster_oracle_import")
    assert oracle.FULL_SIGNAL is app_api.FULL_SIGNAL_PROTOCOLS
    assert app_api.FULL_SIGNAL_PROTOCOLS == {"Sharp", "Denon"}


@pytest.mark.parametrize("protocol, minsends, intro_empty, expected", [
    ("SONY12", 3, True, (3, 3, "ledger")),
    ("JVC", 1, False, (0, 0, "ledger")),
    ("RCC2026", 2, False, (1, 1, "ledger")),
    ("Proton", 1, True, (1, 1, "ledger")),
    ("Sharp", 1, False, (1, 0, "full-signal")),
    ("Denon", 1, False, (1, 0, "full-signal")),
    ("Denon", 3, False, (2, 2, "full-signal")),     # the file may ask for more than the oracle
    ("Sharp", 1, True, (1, 1, "full-signal")),
])
def test_play_is_the_apps_helper_raised_for_the_whole_signal_protocols(
        protocol, minsends, intro_empty, expected):
    play = app_api._play(protocol, minsends, intro_empty)
    assert (play["repeatPasses"], play["helperRepeatPasses"], play["rule"]) == expected
    assert play["introEmpty"] is intro_empty


def test_a_protocol_whose_signals_disagree_on_the_intro_cannot_state_repeat_passes(
        synth, monkeypatch):
    calls = []

    def flip(pronto_text):
        calls.append(pronto_text)
        return len(calls) % 2 == 0
    monkeypatch.setattr(app_api, "_intro_empty", flip)
    built = app_api.build_app_api(synth)
    assert any("one number of repeat passes cannot be stated" in p for p in built.problems)
    assert built.files == {}


# --- determinism (R12) --------------------------------------------------------------------------------


def test_the_bytes_do_not_depend_on_the_worker_count_or_the_run(synth):
    parallel.configure(1)
    serial = app_api.build_app_api(synth).files
    again = app_api.build_app_api(synth).files
    parallel.configure(3)
    many = app_api.build_app_api(synth).files
    parallel.configure(2)
    two = app_api.build_app_api(synth).files
    assert serial == again == many == two
    assert list(serial) == list(many)


def test_the_files_are_compact_utf8_with_sorted_keys_and_one_newline(api):
    for name, content in api.files.items():
        text = content.decode("utf-8")
        assert text.endswith("]\n") or text.endswith("}\n"), name
        assert text.count("\n") == 1, name
        assert json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n" == text
    assert "Ünï" in api.files[f"b/{app_api.brand_key('Ünï')}.m.json"].decode("utf-8")


def test_the_data_version_covers_every_other_file(synth, tmp_path):
    base = app_api.build_app_api(synth)
    data_version = json.loads(base.files["manifest.json"])["dataVersion"]
    assert data_version == base.stats["dataVersion"] and len(data_version) == 12
    changed = {k: dict(v) for k, v in REMOTES.items()}
    changed[4] = {"models": REMOTES[4]["models"],
                  "keys": [k for k in REMOTES[4]["keys"] if k[0] != "PWR"]}
    other = app_api.build_app_api(make_import(tmp_path / "other", changed))
    assert json.loads(other.files["manifest.json"])["dataVersion"] != data_version


def test_an_empty_corpus_writes_nothing(tmp_path):
    (tmp_path / "remotes").mkdir()
    built = app_api.build_app_api(tmp_path)
    assert built.files == {} and built.problems == [] and built.stats == {}
    assert app_api.write_app_api(tmp_path, tmp_path) == []
    assert not (tmp_path / "site").exists()


# --- a tree the generator cannot read is an error, not a guess ----------------------------------------------


def _damaged(synth, tmp_path, edit, prefix="3-"):
    root = tmp_path / "damaged"
    shutil.copytree(synth, root)
    target = next(p for p in sorted((root / "remotes/irblaster").rglob("*.json"))
                  if p.name.startswith(prefix))
    doc = json.loads(target.read_text(encoding="utf-8"))
    edit(doc)
    target.write_text(json.dumps(doc), encoding="utf-8")
    return app_api.build_app_api(root)


def test_a_key_without_a_citation_is_reported(synth, tmp_path):
    def edit(doc):
        next(iter(doc["keys"].values()))["forms"][0]["source"] = "somewhere else"
    built = _damaged(synth, tmp_path, edit)
    assert built.files == {}
    assert any("not an irblaster-db citation" in p for p in built.problems), built.problems


def test_a_label_that_disagrees_with_its_citation_is_reported(synth, tmp_path):
    def edit(doc):
        next(iter(doc["keys"].values()))["label"] = "SOMETHING ELSE"
    built = _damaged(synth, tmp_path, edit)
    assert any("its citation" in p for p in built.problems), built.problems


def test_a_controls_entry_without_the_separator_is_reported(synth, tmp_path):
    built = _damaged(synth, tmp_path, lambda doc: doc["controls"].append("NO SEPARATOR"))
    assert any("NO SEPARATOR" in p for p in built.problems), built.problems


def test_an_id_that_no_brand_lists_is_reported(synth, tmp_path):
    built = _damaged(synth, tmp_path, lambda doc: doc.pop("controls"), prefix="8-")
    assert any("remote 8: no file of it has a controls entry" in p for p in built.problems), \
        built.problems
    assert built.files == {}


def test_a_file_whose_name_is_not_an_id_is_reported(synth, tmp_path):
    root = tmp_path / "named"
    shutil.copytree(synth, root)
    stray = root / "remotes/irblaster/ACME/not-an-id.json"
    stray.parent.mkdir(exist_ok=True)
    stray.write_text("{}", encoding="utf-8")
    built = app_api.build_app_api(root)
    assert any("not-an-id.json: not named" in p for p in built.problems), built.problems


# --- the two orders and the two folds, as SQLite has them ----------------------------------------------------------


def test_nocase_key_orders_as_sqlite_does():
    words = ["b_c", "B[C", "bac", "BAC", "Bac", "a", "A", "é", "É", "z", "Z", "_", "[", "",
             " ", "Ünï", "ünï", "ß", "SS", "Σ", "σ", "ab", "aB", "Ab", "AB", "a b", "a_b",
             "a-b", "a1", "A1", "ZZZ", "zzz"]
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (w TEXT)")
    con.executemany("INSERT INTO t VALUES (?)", [(w,) for w in words])
    lowered = [r[0] for r in con.execute("SELECT w FROM t ORDER BY w COLLATE NOCASE ASC")]
    mine = sorted(words, key=app_api.nocase_key)
    # SQLite leaves a tie to chance: compare the folded sequences, then each tie as a set
    assert [app_api.nocase_key(w)[0] for w in lowered] == [app_api.nocase_key(w)[0] for w in mine]
    assert [ascii_lower(w) for w in lowered] == [ascii_lower(w) for w in mine]


def test_upper_key_folds_as_sqlite_upper_does():
    words = ["abc", "ß", "é", "ı", "ǆ", "Straße", "a[_", "sa", "S_", "ünï ► ⏩", "i̇"]
    con = sqlite3.connect(":memory:")
    for word in words:
        assert con.execute("SELECT UPPER(?)", (word,)).fetchone()[0] == app_api.upper_key(word)
        assert app_api.upper_key(word) == ascii_upper(word)


# --- the power rule -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("label, rank", [
    ("POWER", 0), ("power", 0), ("Pwr", 0), ("OFF", 0), ("On", 0), ("POWER OFF", 0),
    ("Power-On", 0), ("PWR_ON", 0), ("PWR.OFF", 0), ("TV POWER", 1), ("POWER TOGGLE", 1),
    ("Power Button", 0),          # BUTTON holds ON: the app's rule, kept
    ("STANDBY", 1), ("sleep", 1), ("Stand by", 3), ("TV OFF", 3), ("TV ON", 3),
    ("ALL OFF", 3), ("1", 3), ("", 3), ("  ", 3), ("??", 3), ("►", 3), ("POWER?", 0), ("Power Saving", 1), ("ON/OFF", 3),
    ("System Power", 1), ("PowerSave", 1), ("Standby/On", 3), ("sleep timer", 3),
])
def test_power_label_rank_is_the_apps(label, rank):
    assert app_api.power_label_rank(label) == rank
    assert load_tool("app_api_vs_sql").dart_rank(label) == rank


def test_the_power_list_counts_ids_and_orders_by_use(api):
    power = json.loads(api.files["power.json"])
    counts = [row[3] for row in power]
    assert counts == sorted(counts, reverse=True)
    sony = next(row for row in power if row[1] == "A50")
    # ids 3, 4 call it POWER and id 5 TV POWER: one row, three ids, the better label
    assert sony[:2] == [PROTOCOLS.index("SONY12"), "A50"] and sony[2:] == ["POWER", 3, 0]
    assert not any(row[2] in ("1", "OK", "VOL+") for row in power)
    manifest = json.loads(api.files["manifest.json"])
    assert manifest["power"]["columns"] == ["protoIdx", "hex", "label", "nIds", "rank"]
    assert manifest["power"]["limit"] == 3000 and manifest["power"]["rows"] == len(power)


# --- the protocol table ----------------------------------------------------------------------------------------------------


def test_the_protocol_order_is_frozen_and_complete():
    assert list(app_api.PROTOCOLS) == PROTOCOLS
    assert set(PROTOCOLS) == set(FROM_DB_HEX) == set(HOW)
    # ASCII-NOCASE order today; a new protocol is appended, never inserted
    assert PROTOCOLS == sorted(PROTOCOLS, key=nocase)
    assert PROTOCOLS == sorted(PROTOCOLS, key=ascii_upper)


def test_brand_keys_are_ten_hex_digits_of_the_sha1_of_the_utf8_name():
    assert app_api.brand_key("ACME") == hashlib.sha1(b"ACME").hexdigest()[:10]
    assert app_api.brand_key("Ünï") == hashlib.sha1("Ünï".encode()).hexdigest()[:10]
    assert len(app_api.brand_key("")) == 10
    assert set(app_api.brand_key("x")) <= set(string.hexdigits.lower())
    assert app_api.brand_key("SONY") != app_api.brand_key("Sony") != app_api.brand_key("Sony ")


def test_two_brands_with_one_key_stop_the_build(synth, monkeypatch):
    monkeypatch.setattr(app_api, "brand_key", lambda name: "0123456789")
    built = app_api.build_app_api(synth)
    assert any("share the key 0123456789" in p for p in built.problems)
    assert built.files == {}


# --- the stage, the owner table and the gate ----------------------------------------------------------------------------------------


def test_the_app_stage_is_registered_and_owns_its_directory_alone():
    stages = {g.name: g for g in generators.PIPELINE}
    assert stages["app"].paths == ("site/app/v1",) == (paths.APP_API,)
    assert stages["app"].registered and list(stages) == ["check", "compile", "index", "site", "app"]
    assert stages["site"].owns == "site" and stages["site"].excludes == ("site/app/v1",)
    assert "site/app/v1" in generators.owned_paths()


def test_the_site_stage_does_not_see_the_app_directory(tmp_path):
    (tmp_path / "site/app/v1/b").mkdir(parents=True)
    (tmp_path / "site/app/v1/b/x.json").write_text("{}")
    (tmp_path / "site/index.json").write_text("{}")
    fresh = tmp_path.parent / (tmp_path.name + "-fresh")
    (fresh / "site").mkdir(parents=True)
    (fresh / "site/index.json").write_text("{}")
    site = tuple(g for g in generators.PIPELINE if g.name == "site")
    app = tuple(g for g in generators.PIPELINE if g.name == "app")
    assert generators.diff_tree(tmp_path, fresh, site) == []
    assert generators.diff_tree(tmp_path, fresh, app) == [
        "site/app/v1/b/x.json: orphaned -- no generator produces it"]
    # the whole gate reports it once, not once per owner
    assert generators.diff_tree(tmp_path, fresh) == [
        "site/app/v1/b/x.json: orphaned -- no generator produces it"]


def test_the_dirty_path_guard_covers_the_app_directory(monkeypatch, tmp_path):
    seen = []

    class Done:
        stdout = ""

    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or Done())
    cli._dirty_owned_paths(tmp_path)
    assert "site/app/v1" in seen[0] and "site" in seen[0]


@pytest.mark.parametrize("path", [
    "site/app/v1/manifest.json", "site/app/v1/b/0123456789.k.json", "site/app/v1/s/SONY12.json",
])
def test_the_app_files_are_not_gitignored(path):
    import subprocess
    ignored = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0
    assert not ignored


@pytest.fixture
def repo(synth, tmp_path):
    """A writable corpus with the import in it, the index's unresolved list, and
    nothing generated yet."""
    root = tmp_path / "repo"
    shutil.copytree(synth, root)
    (root / "unresolved.json").write_text(
        json.dumps([{"device": "Sony BDP-BX510", "checked": "2026-09-14"}]), encoding="utf-8")
    return root


def run(monkeypatch, root, *argv):
    monkeypatch.chdir(root)
    return cli.main(list(argv))


def test_build_writes_the_app_api_and_check_passes(repo, monkeypatch, capsys):
    assert run(monkeypatch, repo, "build") == 0
    out = capsys.readouterr()
    assert "site -> app: written" in out.out
    assert (repo / "site/app/v1/manifest.json").is_file()
    assert run(monkeypatch, repo, "build", "--check") == 0
    assert "0 difference(s)" in capsys.readouterr().out


def test_check_catches_drift_a_missing_file_and_an_orphan(repo, monkeypatch, capsys):
    assert run(monkeypatch, repo, "build") == 0
    capsys.readouterr()
    api = repo / "site/app/v1"
    k = api / "b" / f"{app_api.brand_key('ZED')}.k.json"
    m = api / "b" / f"{app_api.brand_key('PHIL')}.m.json"
    k.write_text(k.read_text(encoding="utf-8").replace("POWER", "PWR"), encoding="utf-8")
    m.unlink()
    (api / "b/ffffffffff.m.json").write_text("{}\n", encoding="utf-8")
    (api / "s/Stray.json").write_text("{}\n", encoding="utf-8")
    (api / "power.json").write_text("[]\n", encoding="utf-8")
    assert run(monkeypatch, repo, "build", "--check") == 1
    err = capsys.readouterr().err
    assert f"site/app/v1/b/{k.name}: drifted from freshly generated output" in err
    assert f"site/app/v1/b/{m.name}: missing from the committed tree" in err
    assert "site/app/v1/b/ffffffffff.m.json: orphaned -- no generator produces it" in err
    assert "site/app/v1/s/Stray.json: orphaned -- no generator produces it" in err
    assert "site/app/v1/power.json: drifted from freshly generated output" in err
    assert run(monkeypatch, repo, "app", "--check") == 1
    err = capsys.readouterr().err
    assert err.count("ERROR") == 5
    # `rl app` repairs it, and removes the orphans (the directory is the stage's)
    assert run(monkeypatch, repo, "app") == 0
    assert not (api / "s/Stray.json").exists()
    assert run(monkeypatch, repo, "app", "--check") == 0
    assert run(monkeypatch, repo, "build", "--check") == 0


def test_a_whole_build_without_the_import_has_no_app_directory(tmp_path, monkeypatch):
    from shard_corpus import write_remote
    write_remote(tmp_path, "remotes/topping/RC-15A.json", "Topping", "RC-15A", ["DX3 Pro"])
    (tmp_path / "unresolved.json").write_text("[]", encoding="utf-8")
    assert run(monkeypatch, tmp_path, "build") == 0
    assert not (tmp_path / "site/app").exists()
    assert run(monkeypatch, tmp_path, "build", "--check") == 0


def test_the_other_stages_do_not_take_the_app_directory_for_theirs(repo, monkeypatch, capsys):
    assert run(monkeypatch, repo, "build") == 0
    api = repo / "site/app/v1"
    (api / "power.json").write_text("[]\n", encoding="utf-8")
    (api / "b/ffffffffff.m.json").write_text("{}\n", encoding="utf-8")
    capsys.readouterr()
    assert run(monkeypatch, repo, "site", "--check") == 0        # not the site's
    assert run(monkeypatch, repo, "index", "--check") == 0
    assert run(monkeypatch, repo, "app", "--check") == 1         # the app stage's


def test_rl_app_reports_what_it_made(repo, monkeypatch, capsys):
    assert run(monkeypatch, repo, "app") == 0
    line = capsys.readouterr().out
    assert "17 brands" in line and "8 remotes" in line
    assert "keys the import could not represent and the API therefore lacks:" in line
    assert "unknown (no IMPORT.md)" not in line          # the importer wrote one


def test_a_problem_stops_the_stage_before_it_writes(repo, monkeypatch, capsys):
    stray = repo / "remotes/irblaster/ACME/not-an-id.json"
    stray.write_text("{}", encoding="utf-8")
    assert run(monkeypatch, repo, "app") == 1
    assert "not named <database id>-<protocol>.json" in capsys.readouterr().err
    assert not (repo / "site/app").exists()


# --- mutations: the reference comparison must fail for each of these ---------------------------------------------------------------------------


_REAL_RANK = app_api.power_label_rank.__wrapped__

MUTATIONS = {
    "NOCASE folds to upper case": ("nocase_key", lambda t: (t.translate(
        str.maketrans(string.ascii_lowercase, string.ascii_uppercase)), t)),
    "NOCASE is case-sensitive": ("nocase_key", lambda t: (t, t)),
    "NOCASE folds Unicode": ("nocase_key", lambda t: (t.casefold(), t)),
    "UPPER is Unicode": ("upper_key", str.upper),
    "UPPER is the identity": ("upper_key", lambda t: t),
    "UPPER is lower": ("upper_key", str.lower),
    "brand key is eight digits": ("brand_key", lambda n: hashlib.sha1(n.encode()).hexdigest()[:8]),
    "brand key hashes the folded name": (
        "brand_key", lambda n: hashlib.sha1(n.lower().encode()).hexdigest()[:10]),
    "the app's reading is taken as never different": ("app_reading_differs", lambda p, h: False),
    "the full-signal protocols are forgotten": ("FULL_SIGNAL_PROTOCOLS", frozenset()),
    "every protocol plays the whole signal": (
        "FULL_SIGNAL_PROTOCOLS", frozenset(SIGNAL_PROTOCOLS)),
    "the protocol order is reversed": (
        "PROTOCOL_INDEX", {n: len(PROTOCOLS) - 1 - i for i, n in enumerate(PROTOCOLS)}),
    "the power list is cut short": ("POWER_LIMIT", 2),
    "only the best power rank counts": ("POWER_MAX_RANK", 0),
    "a label that is no power counts": ("POWER_MAX_RANK", 3),
    "ON is not a power word": ("power_label_rank", lambda label: 3 if label == "ON" else
                               _REAL_RANK(label)),
    "the intro is always empty": ("_intro_empty", lambda pronto_text: True),
    "a key is lost from every id": (
        "_read_file", None),     # replaced below: drops the last key of each file
}


def _drop_last_key(real):
    def read(root, path):
        fact = real(root, path)
        if fact.get("keys"):
            fact["keys"] = fact["keys"][:-1]
        return fact
    return read


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutations_are_detected(synth, monkeypatch, name):
    parallel.configure(1)
    target, replacement = MUTATIONS[name]
    if name == "a key is lost from every id":
        replacement = _drop_last_key(app_api._read_file)
    monkeypatch.setattr(app_api, target, replacement)
    built = app_api.build_app_api(synth)
    if built.problems:           # a mutation the generator itself refuses is detected too
        return
    with pytest.raises(AssertionError):
        check_api(built.files, REMOTES)


def test_the_unmutated_build_passes_the_same_comparison(synth):
    parallel.configure(1)
    built = app_api.build_app_api(synth)
    assert built.problems == []
    check_api(built.files, REMOTES)


# --- the real tree -------------------------------------------------------------------------------------------------------------------------


needs_data = pytest.mark.skipif(
    not REAL.is_dir(), reason="the imported database is not in this tree")

@pytest.fixture(scope="module")
def real_api():
    if not REAL.is_dir():
        pytest.skip("the imported database is not in this tree")
    parallel.configure(None)
    built = app_api.build_app_api(ROOT)
    parallel.configure(None)
    assert built.problems == []
    return built


@needs_data
def test_the_counts_are_the_trees(real_api):
    facts, _ = read_real_tree()
    counts = json.loads(real_api.files["manifest.json"])["counts"]
    assert counts == {"brands": len(facts["brands"]), "models": len(facts["pairs"]),
                      "remotes": len(facts["ids"]), "keys": facts["keys"]}
    brands = json.loads(real_api.files["brands.json"])
    assert len(brands) == len(facts["brands"])
    assert len({row[1] for row in brands}) == len(brands)           # no key collides
    assert all(row[1] == hashlib.sha1(row[0].encode()).hexdigest()[:10] for row in brands)


@needs_data
def test_exactly_the_ten_protocols_the_hex_modules_say_are_read_differently(real_api):
    manifest = json.loads(real_api.files["manifest.json"])
    differing = {p["db"] for p in manifest["protocols"] if p["appReadingDiffers"]}
    assert differing == set(SIGNAL_PROTOCOLS)
    assert {n[2:-5] for n in real_api.files if n.startswith("s/")} == differing


@needs_data
def test_how_one_press_plays_is_pinned_for_the_ten(real_api):
    manifest = json.loads(real_api.files["manifest.json"])
    seen = {p["db"]: (p["play"]["repeatPasses"], p["play"]["helperRepeatPasses"], p["play"]["rule"])
            for p in manifest["protocols"] if p["appReadingDiffers"]}
    assert seen == SIGNAL_PROTOCOLS
    for name in SIGNAL_PROTOCOLS:
        shard = json.loads(real_api.files[f"s/{name}.json"])
        assert shard["play"] == next(
            p["play"] for p in manifest["protocols"] if p["db"] == name)
        # the shard states what the app reads off any of its Pronto strings
        for text in list(shard["s"].values())[:50]:
            words = text.split()
            assert words[0] == "0000" and len(words) == 4 + 2 * (
                int(words[2], 16) + int(words[3], 16))
            assert (int(words[2], 16) == 0) == shard["play"]["introEmpty"]
        assert shard["minSends"] == MIN_SENDS.get(name, 1)


@needs_data
def test_every_code_of_a_signal_protocol_is_in_its_shard_once(real_api):
    facts, _ = read_real_tree()
    for name in SIGNAL_PROTOCOLS:
        shard = json.loads(real_api.files[f"s/{name}.json"])
        present = {h for _i, _l, p, h in facts["rows"] if p == name}
        assert set(shard["s"]) == present


@needs_data
def test_the_committed_api_is_what_the_generator_makes(real_api):
    base = ROOT / paths.APP_API
    if not base.is_dir():
        pytest.skip("site/app/v1 has not been generated yet")
    on_disk = {p.relative_to(base).as_posix(): p.read_bytes()
               for p in base.rglob("*") if p.is_file()}
    assert sorted(on_disk) == sorted(real_api.files)
    assert [n for n in on_disk if on_disk[n] != real_api.files[n]] == []


@needs_data
def test_the_real_tree_passes_the_reference_checks_that_do_not_need_the_database(real_api):
    """Structure of every brand file: ascending ids, per-id key order, masks and
    counts agreeing, hashes of the right file."""
    tool = load_tool("app_api_vs_sql")
    files = real_api.files
    protocols_ = json.loads(files["manifest.json"])["protocols"]
    for name, key, mask in json.loads(files["brands.json"]):
        k_bytes = files[f"b/{key}.k.json"]
        k, m = json.loads(k_bytes), json.loads(files[f"b/{key}.m.json"])
        assert m["hash"] == hashlib.sha256(k_bytes).hexdigest()[:6]
        ids = [r[0] for r in m["ids"]]
        assert ids == sorted(set(ids)) == [r[0] for r in k["r"]]
        total = 0
        for (db_id, keys), (_i, id_mask, n_keys) in zip(k["r"], m["ids"]):
            assert n_keys == len(keys) and id_mask == reduce(or_, (1 << idx for _l, idx, _h in keys))
            order = [(ascii_upper(label), ascii_upper(protocols_[idx]["db"]), ascii_upper(h),
                      (label, idx, h)) for label, idx, h in keys]
            assert [o[:3] for o in order] == sorted(o[:3] for o in order)
            total |= id_mask
        assert total == mask
        assert [mm[0] for mm in m["models"]] == sorted((mm[0] for mm in m["models"]), key=nocase)
    assert tool.dart_rank("POWER") == 0
