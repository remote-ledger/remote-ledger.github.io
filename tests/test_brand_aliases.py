"""Brand aliases (D101, D102): the data, its validator, the table of the bundle and the matcher.

* The **data** is the seed list the owner reviews: it conforms to its schema, passes every rule of
  the validator, names the brands and the aliases the owner listed, in both scripts.
* The **validator** refuses what it says it refuses. Each rule has a test that breaks exactly that
  rule in a copy of the file and asks for the message.
* The **exporter** writes the table, leaves a brand the catalog lacks out with a note and stops on a
  name that is a brand's own; the table is part of ``dataVersion``; ``schemaVersion`` stays 1.
* The **matcher** reads an alias as it reads a brand's name: given, typed alone, with a model
  number, in either script, with or without the space; and **a search typed in Latin letters is
  what it was**, proved against the same index without the table, over the vectors' queries and
  over generated ones on the real selected bundle.
"""

from __future__ import annotations

import copy
import json
import random
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from bundle_corpus import make_corpus
from remote_ledger import matching, validate
from remote_ledger.bundle import aliases as brand_aliases
from remote_ledger.bundle import build, matching_vectors, search_eval, suggest_vectors, writer
from remote_ledger.bundle.aliases import (
    ALIASES_FILE, Alias, aliases_from, catalog_problems, load_aliases, make, problems_of, rows_for,
    semantic_problems)
from remote_ledger.bundle.textnorm import search_norm
from remote_ledger.errors import ValidationError
from remote_ledger.matching import MatchIndex

ROOT = Path(__file__).resolve().parent.parent
DOC = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))


def messages(doc) -> list[str]:
    return [str(p) for p in semantic_problems(doc)]


def listing(**brands):
    """A document: the shipped header and ``brands`` (name -> aliases, a name or an entry), no shared alias."""
    doc = copy.deepcopy(DOC)
    doc["header"]["intentional_shared_aliases"] = {}
    doc["brands"] = [{"brand": name, "aliases": [{"name": a} if isinstance(a, str) else a for a in entries]}
                     for name, entries in brands.items()]
    return doc


def names_of(doc):
    return {e["brand"]: [a["name"] for a in e["aliases"]] for e in doc["brands"]}


# --- the data ---------------------------------------------------------------------------------------------


def test_the_shipped_list_is_valid_and_says_it_is_a_seed():
    doc, problems = problems_of()
    assert [str(p) for p in problems] == []
    assert sorted(doc) == ["brands", "header"] and doc["header"]["status"] == "seed"
    assert "SEED" in doc["header"]["about"] and "reviewed" in doc["header"]["about"]
    assert doc["header"]["intentional_shared_aliases"] == {}
    assert (validate.SCHEMA_DIR / brand_aliases.SCHEMA).is_file()


def test_the_seed_names_the_brands_and_the_aliases_the_owner_listed_in_both_scripts():
    assert names_of(DOC) == {
        "Hisense": ["海信"], "Xiaomi": ["小米"], "Skyworth": ["创维", "創維"], "Changhong": ["长虹", "長虹"],
        "Haier": ["海尔", "海爾"], "Midea": ["美的"], "Gree": ["格力"], "Konka": ["康佳"], "Samsung": ["三星"],
        "Sony": ["索尼", "新力"], "Panasonic": ["松下", "國際牌"], "Sharp": ["夏普"], "Toshiba": ["东芝", "東芝"],
        "Philips": ["飞利浦", "飛利浦"], "Apple": ["苹果", "蘋果"], "Huawei": ["华为", "華為"],
        "Yamaha": ["雅马哈", "雅馬哈"], "Pioneer": ["先锋", "先鋒"], "Denon": ["天龙", "天龍"], "Onkyo": ["安桥", "安橋"]}
    tags = {a["name"]: (a["script"], a["region"]) for e in DOC["brands"] for a in e["aliases"]}
    assert tags["创维"] == ("hans", "CN") and tags["創維"] == ("hant", "any") and tags["海信"] == ("both", "any")
    assert tags["新力"] == ("both", "TW") and tags["國際牌"] == ("hant", "TW")


def test_every_alias_is_a_key_of_two_characters_or_more_with_no_ascii_letter_or_digit():
    loaded = load_aliases()
    assert len(loaded) == 33
    for a in loaded:
        assert len(a.key) >= matching.BRAND_MIN_KEY, a
        assert not any(c.isascii() and c.isalnum() for c in a.key), a
        assert a.key == search_norm(a.alias) and a.brand_key == search_norm(a.brand)
    assert len({a.key for a in loaded}) == len(loaded)
    assert {a.alias for a in loaded} >= {"海信", "创维", "創維", "索尼", "新力", "松下", "國際牌", "苹果", "蘋果"}


def test_loading_gives_the_aliases_in_the_files_order_and_the_loader_is_cached():
    loaded = load_aliases()
    assert aliases_from(DOC) == loaded
    assert loaded[0] == Alias("Hisense", "hisense", "海信", "海信", "both", "any", "certain")
    assert [a.alias for a in loaded if a.brand == "Sony"] == ["索尼", "新力"]
    assert brand_aliases.shipped_aliases() is brand_aliases.shipped_aliases()
    assert make("Sony", "索尼", "both") == Alias("Sony", "sony", "索尼", "索尼", "both", None, None)


# --- the validator ----------------------------------------------------------------------------------------------


def test_the_seed_passes_the_relations_and_each_relation_is_one_of_the_documented_rules():
    assert messages(DOC) == []


@pytest.mark.parametrize("alias, fragment", [
    ("--", "has no letter or digit, so it names nothing"),
    ("索", "has a search key shorter than 2 characters"),
    ("Ｘ", "has a search key shorter than 2 characters"),            # full width, one character
    ("ab", "holds a letter or digit of ASCII"),
    ("TCL电视", "holds a letter or digit of ASCII"),
    ("海信2", "holds a letter or digit of ASCII"),
    ("Ｓｏｎｙ", "holds a letter or digit of ASCII"),                    # NFKD makes it Latin
])
def test_an_alias_that_cannot_be_looked_up_or_would_disturb_a_latin_search_is_refused(alias, fragment):
    got = messages(listing(Sony=[alias]))
    assert any(fragment in m and repr(alias) in m for m in got), got


def test_an_alias_is_refused_when_it_is_listed_twice_under_a_brand():
    twice = messages(listing(Sony=["索尼", "索 尼"]))
    assert len(twice) == 1 and twice[0].endswith("'索 尼' has the search key '索尼', which 'Sony' already has as '索尼'; it is listed twice")
    assert messages(listing(Sony=["索尼"], Samsung=["三星"])) == []


def test_an_alias_under_two_brands_is_refused_unless_the_header_says_they_share_it():
    doc = listing(Sony=["索尼"], Samsung=["索尼"])
    got = messages(doc)
    assert len(got) == 1 and "'索尼' is listed under 'Sony' and 'Samsung': an alias names one brand unless the header's " \
        "intentional_shared_aliases says that brands share it on purpose" in got[0]
    doc["header"]["intentional_shared_aliases"] = {"索尼": ["Sony", "Samsung"]}
    assert messages(doc) == []
    # by search key: the header may spell the alias and the brands another way
    doc["header"]["intentional_shared_aliases"] = {"索 尼": ["SAMSUNG", "sony"]}
    assert messages(doc) == []


def test_a_shared_alias_is_declared_for_exactly_the_brands_that_have_it():
    doc = listing(Sony=["索尼"], Samsung=["索尼"], Sharp=["索尼"])
    doc["header"]["intentional_shared_aliases"] = {"索尼": ["Sony", "Samsung"]}
    got = messages(doc)
    assert len(got) == 1 and "'索尼' is declared shared by 'Samsung' and 'Sony' but the list has it under 'Samsung' and " \
        "'Sharp' and 'Sony'" in got[0]
    one = listing(Sony=["索尼"], Samsung=["三星"])
    one["header"]["intentional_shared_aliases"] = {"索尼": ["Sony", "Samsung"]}
    got = messages(one)
    assert len(got) == 1 and "'索尼' is declared shared by 'Samsung' and 'Sony' but the list has it under 'Sony'" in got[0]
    nobody = listing(Sony=["索尼"])
    nobody["header"]["intentional_shared_aliases"] = {"新力": ["Sony", "Samsung"]}
    got = messages(nobody)
    assert len(got) == 1 and "'新力' is declared shared but no brand of the list has it" in got[0]


def test_a_brand_listed_twice_by_its_search_key_is_refused():
    doc = listing(Sony=["索尼"])
    doc["brands"].append({"brand": "SONY", "aliases": [{"name": "新力"}]})
    got = messages(doc)
    assert any("'SONY' is 'Sony' again (the same search key 'sony'); list a brand once" in m for m in got)
    assert messages(listing(**{"--": ["索尼"]}))[0].endswith("has no letter or digit, so it names no brand")


def test_an_alias_that_is_the_brands_own_name_is_refused():
    got = messages(listing(**{"Ünï": ["ünï"], "ЭРА": ["эра"], "海信": ["海信"]}))
    # a Latin name is caught by the rule about ASCII; a name of another script by this one
    assert len(got) == 3
    assert "holds a letter or digit of ASCII" in got[0] and "'ünï'" in got[0]
    assert got[1].endswith("'эра' is the brand's own name") and got[2].endswith("'海信' is the brand's own name")


def test_an_alias_that_is_the_name_of_a_brand_of_the_catalog_is_refused_when_a_bundle_is_built():
    loaded = (make("Sony", "索尼"), make("Samsung", "三星"))
    assert catalog_problems(loaded, ["sony", "samsung"]) == []
    got = catalog_problems(loaded, ["sony", "索尼", "x"])
    assert len(got) == 1 and "'索尼' is the name of a brand of the catalog (the search key '索尼')" in str(got[0])
    assert "['brands']['Sony']" in str(got[0])


# --- the schema and the file ---------------------------------------------------------------------------------------


def write(tmp_path, doc, name="aliases.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def sony(doc):
    return next(e for e in doc["brands"] if e["brand"] == "Sony")


@pytest.mark.parametrize("change, fragment", [
    (lambda d: sony(d)["aliases"].append({"name": ""}), "should be non-empty|is too short"),
    (lambda d: sony(d)["aliases"].append({"name": "x" * 49}), "is too long"),
    (lambda d: sony(d)["aliases"].append({"name": "新力x", "script": "latin"}), "is not one of"),
    (lambda d: sony(d)["aliases"].append({"name": "新力y", "region": "EU"}), "is not one of"),
    (lambda d: sony(d)["aliases"].append({"name": "新力z", "confidence": "sure"}), "is not one of"),
    (lambda d: sony(d)["aliases"].append({"script": "both"}), "'name' is a required property"),
    (lambda d: sony(d).__setitem__("aliases", []), "should be non-empty|is too short"),
    (lambda d: sony(d).pop("aliases"), "'aliases' is a required property"),
    (lambda d: sony(d).pop("brand"), "'brand' is a required property"),
    (lambda d: sony(d).__setitem__("brand", ""), "should be non-empty|is too short"),
    (lambda d: sony(d).__setitem__("aliases", ["索尼"]), "is not of type 'object'"),
    (lambda d: d["brands"].clear(), "should be non-empty|is too short"),
    (lambda d: d.__setitem__("brands", {"Sony": []}), "is not of type 'array'"),
    (lambda d: d.pop("header"), "'header' is a required property"),
    (lambda d: d["header"].pop("status"), "'status' is a required property"),
    (lambda d: d["header"].__setitem__("status", ""), "should be non-empty|is too short"),
    (lambda d: d["header"].__setitem__("intentional_shared_aliases", {"索尼": ["Sony"]}), "should be non-empty|is too short"),
    (lambda d: d["header"].__setitem__("intentional_shared_aliases", {"索尼": ["Sony", "Sony"]}), "has non-unique elements"),
    (lambda d: d.__setitem__("extra", 1), "Additional properties are not allowed"),
])
def test_the_schema_refuses_a_file_of_the_wrong_shape(tmp_path, change, fragment):
    doc = copy.deepcopy(DOC)
    change(doc)
    _, problems = problems_of(write(tmp_path, doc))
    # jsonschema words an empty string or list differently from one release to another
    assert problems and any(f in p.message for p in problems for f in fragment.split("|")), [str(p) for p in problems]


def test_a_field_the_exporter_does_not_read_is_allowed_and_ignored(tmp_path):
    """The shape of a reviewed list: counts of a brand, a header with a method and a date, tags of a name."""
    doc = copy.deepcopy(DOC)
    doc["header"].update(date="2026-10-07", method="by hand", fields={"remotes": "..."}, counts={"brands": 20})
    for entry in doc["brands"]:
        entry.update(remotes=3, models=9, remotes_made=2, comment="free text")
        for a in entry["aliases"]:
            a.update(note="a note", reviewed_by="nobody")
    path = write(tmp_path, doc)
    assert problems_of(path)[1] == []
    assert load_aliases(path) == load_aliases()


def test_a_list_of_the_shape_of_a_review_draft_loads_with_a_shared_alias(tmp_path):
    """A header with more than a status, brands that carry counts, one company spelled two ways in the
    catalog with three names under both of its brands, declared in the header."""
    shared = ["西部数据", "西部數據", "威騰"]
    doc = {
        "header": {
            "status": "draft for the owner's review", "date": "2026-10-07", "method": "by hand",
            "bundle": {"dataVersion": "0123456789ab", "profile": "full"}, "counts": {"brands": 3, "aliases": 7},
            "intentional_shared_aliases": {name: ["WESTERN DIGITAL", "wd"] for name in shared}},
        "brands": [
            {"brand": "SONY", "remotes": 1061, "models": 12031, "remotes_made": 1025, "aliases": [
                {"name": "索尼", "script": "both", "region": "any", "confidence": "certain"},
                {"name": "新力", "script": "both", "region": "TW", "confidence": "likely"}]},
            {"brand": "WESTERN DIGITAL", "remotes": 10, "models": 50, "remotes_made": 4,
             "aliases": [{"name": n, "script": "hans", "region": "CN", "confidence": "certain"} for n in shared]},
            {"brand": "wd", "remotes": 5, "models": 6, "remotes_made": 5,
             "aliases": [{"name": n, "script": "hans", "region": "CN", "confidence": "certain"} for n in shared]}]}
    loaded = load_aliases(write(tmp_path, doc))
    assert [(a.brand, a.alias) for a in loaded] == [("SONY", "索尼"), ("SONY", "新力")] + [
        (b, n) for b in ("WESTERN DIGITAL", "wd") for n in shared]
    assert loaded[2] == Alias("WESTERN DIGITAL", "westerndigital", "西部数据", "西部数据", "hans", "CN", "certain")
    # the rows: one for each brand that has a shared alias, in the order of key and brand
    ids = {"sony": 3, "westerndigital": 2, "wd": 1}
    rows = rows_for(loaded, ids)
    assert [r for r in rows if r[1] == "西部数据"] == [("西部数据", "西部数据", 1), ("西部数据", "西部数据", 2)]
    assert len(rows) == 2 + 3 * 2
    # and the same list with the declaration left out is refused, once for each shared alias
    del doc["header"]["intentional_shared_aliases"]
    _, problems = problems_of(write(tmp_path, doc, "undeclared.json"))
    assert len(problems) == 3 and all("is listed under 'WESTERN DIGITAL' and 'wd'" in p.message for p in problems)


def test_a_file_that_cannot_be_read_or_parsed_or_repeats_a_key_is_a_problem_not_a_crash(tmp_path):
    assert "could not be read" in problems_of(tmp_path / "missing.json")[1][0].message
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert "could not be parsed" in problems_of(bad)[1][0].message
    dup = tmp_path / "dup.json"
    dup.write_text('{"header": {}, "header": {}}', encoding="utf-8")
    assert "could not be parsed" in problems_of(dup)[1][0].message
    with pytest.raises(ValidationError, match="the brand aliases are invalid"):
        load_aliases(bad)


def test_a_valid_file_elsewhere_loads_and_the_relations_are_checked_after_the_schema(tmp_path):
    doc = listing(Sony=["索尼"], Samsung=["三星", {"name": "三星电子", "script": "hans", "note": "the company"}])
    path = write(tmp_path, doc)
    assert [a.alias for a in load_aliases(path)] == ["索尼", "三星", "三星电子"]
    bad = listing(Sony=["索尼", "ab"])
    problems = problems_of(write(tmp_path, bad, "b.json"))[1]
    assert [p.message for p in problems] == [
        "'ab' holds a letter or digit of ASCII; an alias is a name in another script, so that a search typed in "
        "Latin letters is never read through one (D101)"]
    # a schema problem is reported alone: the relations are only meaningful over a file of the right shape
    both = listing(Sony=["索尼", "ab"])
    both["header"].pop("status")
    assert len(problems_of(write(tmp_path, both, "c.json"))[1]) == 1


# --- the table of the bundle ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    return make_corpus(tmp_path_factory.mktemp("aliases") / "ledger")


ALIASES = (make("Sony", "索尼"), make("SONY", "新力"), make("Topping", "拓品"), make("Nobody", "无名"),
           make("ZED", "泽德"))


@pytest.fixture(scope="module")
def full(ledger):
    built = build.build_bundle(ledger, "full", aliases=ALIASES)
    assert built.problems == []
    return built


def table(built, sql):
    conn = sqlite3.connect(":memory:")
    conn.deserialize(built.files[build.BUNDLE_FILE])
    return conn.execute(sql).fetchall()


def test_the_table_holds_alias_key_and_brand_sorted_by_key_for_the_brands_the_bundle_carries(full):
    ids = {name: i for name, i in table(full, "SELECT name, id FROM brands")}
    got = table(full, "SELECT alias, norm, brand_id FROM brand_aliases")
    assert got == sorted([("索尼", "索尼", ids["SONY"]), ("新力", "新力", ids["SONY"]),
                          ("拓品", "拓品", ids["Topping"]), ("泽德", "泽德", ids["ZED"])], key=lambda r: r[1])
    assert [r[1] for r in got] == sorted(r[1] for r in got)
    assert rows_for(ALIASES, {"sony": 7}) == sorted([("索尼", "索尼", 7), ("新力", "新力", 7)], key=lambda r: r[1])


def test_the_table_is_counted_in_meta_and_in_the_manifest_and_the_format_is_still_version_1(full):
    meta = dict(table(full, "SELECT key, value FROM meta"))
    assert meta["count.brandAliases"] == "4" and meta["schemaVersion"] == "1"
    manifest = json.loads(full.files[build.MANIFEST_FILE])
    assert manifest["counts"]["brandAliases"] == 4 and manifest["schemaVersion"] == 1
    conn = sqlite3.connect(":memory:")
    conn.deserialize(full.files[build.BUNDLE_FILE])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1 == writer.SCHEMA_VERSION
    assert conn.execute("PRAGMA application_id").fetchone()[0] == writer.APPLICATION_ID == 0x524C4231
    assert full.stats["brandAliases"] == 4 and full.stats["aliasesListed"] == 5 and full.stats["aliasesLeftOut"] == 0


def test_a_brand_the_catalog_lacks_is_reported_and_not_an_error(full):
    assert full.problems == []
    assert full.notes == ["the brand 'Nobody' of brand_aliases.json is not in the catalog, so its aliases (1) are "
                          "not in the bundle"]
    assert full.stats["aliasesMissing"] == 1


def test_a_profile_that_leaves_a_brand_out_leaves_its_aliases_out_without_a_note(ledger, full):
    chosen = build.build_bundle(ledger, "selected", aliases=ALIASES)
    assert chosen.problems == []
    in_ledger = {norm for (norm,) in table(full, "SELECT norm FROM brands")}
    carried = {norm for (norm,) in table(chosen, "SELECT norm FROM brands")}
    assert {"sony", "topping", "zed"} <= in_ledger and carried < in_ledger and "sony" in carried
    got = {alias for (alias,) in table(chosen, "SELECT alias FROM brand_aliases")}
    assert got == {a.alias for a in ALIASES if a.brand_key in carried}
    left = [a for a in ALIASES if a.brand_key in in_ledger and a.brand_key not in carried]
    assert left and chosen.stats["aliasesLeftOut"] == len(left)
    assert len(chosen.notes) == 1 and "Nobody" in chosen.notes[0]               # only the brand no tree has is reported


def test_an_alias_that_is_a_brands_name_stops_the_build_and_says_which(ledger):
    clash = (make("Sony", "索尼"), make("Topping", "ünï"))              # the key of the catalog's brand Ünï
    built = build.build_bundle(ledger, "full", aliases=clash)
    assert built.files == {} and len(built.problems) == 1
    assert "'ünï' is the name of a brand of the catalog (the search key 'uni')" in built.problems[0]
    assert built.problems[0].startswith(brand_aliases.REPO_PATH)


def test_an_invalid_shipped_list_stops_the_build_with_its_problems(ledger, monkeypatch):
    def broken():
        raise ValidationError("the brand aliases are invalid:\n  somewhere: wrong")
    monkeypatch.setattr(brand_aliases, "shipped_aliases", broken)
    built = build.build_bundle(ledger, "full")
    assert built.files == {} and built.problems == ["the brand aliases are invalid:\n  somewhere: wrong"]


def test_the_table_is_part_of_the_data_version(ledger, full):
    other = build.build_bundle(ledger, "full", aliases=ALIASES[:3])
    same = build.build_bundle(ledger, "full", aliases=ALIASES)
    version = lambda b: dict(table(b, "SELECT key, value FROM meta"))["dataVersion"]
    assert version(same) == version(full) and same.files == full.files
    assert version(other) != version(full)
    changed = build.build_bundle(ledger, "full", aliases=(make("Sony", "索尼"), make("SONY", "新立"), *ALIASES[2:]))
    assert version(changed) != version(full)
    assert ("brand_aliases", "norm, brand_id") in writer.DIGEST_TABLES


def test_a_key_and_a_brand_are_one_row_of_the_table_and_a_key_may_have_two_brands(full):
    conn = sqlite3.connect(":memory:")
    conn.deserialize(full.files[build.BUNDLE_FILE])
    (sony,) = conn.execute("SELECT id FROM brands WHERE norm = 'sony'").fetchone()
    other = conn.execute("SELECT id FROM brands WHERE id != ? LIMIT 1", (sony,)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO brand_aliases VALUES ('索 尼', '索尼', ?)", (sony,))
    conn.execute("INSERT INTO brand_aliases VALUES ('索尼', '索尼', ?)", (other,))       # a second brand: allowed


SHARED = (make("Topping", "共享名"), make("ZED", "共享名"), make("Sony", "索尼"))


def test_an_alias_that_two_brands_share_is_a_row_for_each_and_a_profile_keeps_those_it_carries(ledger):
    built = build.build_bundle(ledger, "full", aliases=SHARED)
    assert built.problems == [] and built.notes == []
    ids = {name: i for name, i in table(built, "SELECT name, id FROM brands")}
    assert table(built, "SELECT alias, norm, brand_id FROM brand_aliases") == sorted(
        [("共享名", "共享名", ids["Topping"]), ("共享名", "共享名", ids["ZED"]), ("索尼", "索尼", ids["SONY"])],
        key=lambda r: (r[1], r[2]))
    assert built.stats["brandAliases"] == 3
    chosen = build.build_bundle(ledger, "selected", aliases=SHARED)
    by_norm = {norm: i for norm, i in table(chosen, "SELECT norm, id FROM brands")}
    wanted = {by_norm[a.brand_key] for a in SHARED[:2] if a.brand_key in by_norm}
    assert {brand for norm, brand in table(chosen, "SELECT norm, brand_id FROM brand_aliases")
            if norm == "共享名"} == wanted
    assert "sony" in by_norm and len(wanted) < 2          # the profile carries one of the two brands, and its row only


def test_the_command_writes_the_table_and_says_what_it_left_out(ledger, tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "remote_ledger.cli", "bundle", "--profile", "full", "--out", str(tmp_path / "out")],
        cwd=ledger, env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "note: the brand 'Gree' of brand_aliases.json is not in the catalog, so its aliases (1) are not in the bundle" \
        in done.stderr
    assert "brand aliases: 2 of the 33 listed; not in it: 0 of brands this profile leaves out, 31 of brands the catalog lacks" \
        in done.stdout
    conn = sqlite3.connect(tmp_path / "out" / "catalog.sqlite")
    assert conn.execute("SELECT alias FROM brand_aliases ORDER BY norm").fetchall() == [("新力",), ("索尼",)]


# --- the matcher ---------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def index():
    return MatchIndex.from_entries(matching_vectors.CATALOG, matching_vectors.ALIASES)


@pytest.fixture(scope="module")
def without():
    return MatchIndex.from_entries(matching_vectors.CATALOG)


def answer(found):
    return [(c.brand, c.model, c.permille, c.evidence) for c in found]


def test_an_alias_is_a_name_of_its_brand_with_the_score_a_brand_name_of_the_same_kind_gets(index):
    given = index.match("索尼", None, [])
    assert answer(given) == [("SONY", None, 1000, ("brand", "brand:given"))]
    assert given[0].models == index.match("SONY", None, [])[0].models and len(given[0].models) == 6
    typed = index.match(None, None, ["索尼"])
    assert answer(typed) == answer(index.match(None, None, ["sony"])) == [("SONY", None, 800, ("brand", "brand:text"))]
    assert typed[0].models == given[0].models
    assert answer(index.match("新力")) == answer(given)                            # the regional form
    part = index.match("索尼 Samsung")                                              # the runs of a given brand: 900
    assert answer(part) == [("SAMSUNG", None, 900, ("brand", "brand:given")), ("SONY", None, 900, ("brand", "brand:given"))]


def test_an_alias_and_a_model_number_find_the_model_in_either_script_with_or_without_the_space(index):
    want = [("PHILIPS", "42PF9966", 900, ("model:exact", "via:text", "brand:text"))]
    for text in ("飞利浦 42PF9966", "飛利浦 42PF9966", "飞利浦42pf9966", "42PF9966 飛利浦", "飞利浦,42-PF-9966"):
        assert answer(index.match(None, None, [text]))[:1] == want, text
    assert answer(index.match("松下", "NV - FS 200"))[:1] == [
        ("PANASONIC", "NV - FS 200", 1000, ("model:exact", "via:model", "brand:given"))]
    assert answer(index.match(None, None, ["國際牌 NV-FS200"]))[:1] == [
        ("PANASONIC", "NV - FS 200", 900, ("model:exact", "via:text", "brand:text"))]


def test_a_model_of_another_brand_does_not_count_for_the_brand_an_alias_names(index):
    assert answer(index.match("三星", "KD-49X8088")) == [("SAMSUNG", None, 1000, ("brand", "brand:given"))]
    assert answer(index.match("三星", "UN50NU6900F"))[0] == (
        "SAMSUNG", "UN50NU6900F", 1000, ("model:exact", "via:model", "brand:given"))


def test_two_aliases_name_two_brands_and_an_alias_with_a_name_names_both(index):
    got = index.match(None, None, ["三星 索尼"])
    assert [(c.brand, c.permille) for c in got] == [("SAMSUNG", 800), ("SONY", 800)]
    assert [c.brand for c in index.match(None, None, ["三星 sony"])] == ["SAMSUNG", "SONY"]
    assert [c.brand for c in index.match(None, None, ["索尼 新力"])] == ["SONY"]


def test_an_alias_that_is_part_of_a_model_name_does_not_hide_the_model_that_holds_it(index):
    got = index.match(None, None, ["索尼 TV 55"])
    assert answer(got) == [("ACME", "索尼 TV 55", 855, ("model:exact", "via:text", "brand:none"))]
    # SONY is named, the model is another brand's: a named brand's factor is not its own to give
    assert index.suggest("索尼 TV 55").models == ()


def test_an_alias_that_two_brands_share_names_both_and_each_answers_with_its_own_models(index):
    assert index.alias_by_key["西部数据"] == (index.brand_by_key["wd"], index.brand_by_key["westerndigital"])
    assert index.brand_by_key["wd"] < index.brand_by_key["westerndigital"]
    both = index.match(None, None, ["西部数据"])
    assert answer(both) == [("WD", None, 800, ("brand", "brand:text")),
                            ("WESTERN DIGITAL", None, 800, ("brand", "brand:text"))]    # equal: by brand id
    assert [c.models for c in both] == [("WDTV HUB",), ("WDTV LIVE",)]
    given = index.match("西部數據")
    assert answer(given) == [("WD", None, 1000, ("brand", "brand:given")),
                             ("WESTERN DIGITAL", None, 1000, ("brand", "brand:given"))]
    assert answer(index.match(None, None, ["西部数據"])) == []       # a mixed spelling of the two scripts is none listed
    assert index.aliases[index.brand_by_key["wd"]] == ("西部数据", "西部數據")
    assert index.aliases[index.brand_by_key["westerndigital"]] == ("西部数据", "西部數據")


def test_a_shared_alias_with_a_model_finds_the_model_of_the_brand_that_has_it(index):
    got = index.match(None, None, ["西部数据 WDTV LIVE"])
    assert answer(got)[:1] == [("WESTERN DIGITAL", "WDTV LIVE", 900, ("model:exact", "via:text", "brand:text"))]
    assert all(c.brand == "WESTERN DIGITAL" for c in got if c.permille == 900)
    given = index.match("西部数据", "WDTV HUB")
    assert answer(given) == [("WD", "WDTV HUB", 1000, ("model:exact", "via:model", "brand:given"))]
    assert index.suggest("西部数据 wdtv l").models[0].brand == "WESTERN DIGITAL"
    assert [m.brand for m in index.suggest("西部数据").models] == ["WD", "WESTERN DIGITAL"]       # in the order of the ids
    assert [b.name for b in index.suggest("西部数据").brands] == ["WD", "WESTERN DIGITAL"]
    assert [(b.name) for b in index.suggest("西部数据", 1).brands] == ["WD"]
    assert [(m.brand) for m in index.suggest("西部数据", 1).models] == ["WD"]


def test_a_shared_alias_in_a_longer_run_and_a_shared_alias_that_is_not_a_run_name_nothing(index):
    assert index.match(None, None, ["西部数据公司"]) == [] and index.suggest("西部数据公司").is_empty
    assert [b.name for b in index.suggest("西部").brands] == ["WD", "WESTERN DIGITAL"]


def test_a_query_that_matches_no_alias_names_nothing(index):
    for text in ("日本 电视", "三星电视", "索", "电视", "海", "索尼电视", "三", "星三"):
        assert index.match(None, None, [text]) == [], text
    assert index.match("三星电视") == []                                           # no brand is read as it


def test_an_alias_is_not_read_through_a_typo_or_a_look_alike(index):
    assert index.match("三星星") == [] and index.match("三") == []
    assert index.match(None, None, ["三 星"])[0].brand == "SAMSUNG"               # words of one run join as for a brand


def test_a_run_that_is_an_aliass_key_is_not_tried_among_all_models_as_a_brands_is_not():
    entries = [{"brand": "YAMAHA", "model": "RX-V 4A", "remotes": [1]}]
    aliases = [{"brand": "YAMAHA", "alias": "雅马哈公司"}]
    both = MatchIndex.from_entries(entries, aliases)
    alone = MatchIndex.from_entries(entries)
    assert [(r.key, within) for r, _, _, within in alone.model_keys(None, ["雅马哈公司"])] == [("雅马哈公司", True)]
    assert both.model_keys(None, ["雅马哈公司"]) == []
    assert [(r.key, within) for r, _, _, within in both.model_keys(None, ["雅马哈公司电器"])] == [("雅马哈公司电器", True)]


def test_a_key_of_one_character_in_a_text_is_never_a_brand_whether_a_name_or_an_alias_holds_it():
    index = MatchIndex.from_entries([{"brand": "SONY", "model": "KD 49", "remotes": [1]},
                                     {"brand": "A", "model": "B 1", "remotes": [2]}], [{"brand": "SONY", "alias": "索尼"}])
    index.conn.execute("INSERT INTO brand_aliases VALUES ('索', '索', 1)")      # not a row the exporter writes
    again = MatchIndex(index.conn)
    assert "索" in again.alias_by_key and "a" in again.brand_by_key
    assert again.match(None, None, ["索"]) == [] and again.match(None, None, ["a"]) == []
    assert [c.brand for c in again.match(None, None, ["索尼"])] == ["SONY"]


def test_a_brands_own_key_wins_over_an_alias_and_an_alias_of_no_brand_is_ignored():
    base = MatchIndex.from_entries([{"brand": "SONY", "model": "KD 49", "remotes": [1]},
                                    {"brand": "SHARP", "model": "AQ 1", "remotes": [2]}],
                                   [{"brand": "SONY", "alias": "夏普"}])
    base.conn.execute("INSERT INTO brand_aliases VALUES ('索尼', '索尼', 99)")        # a brand the bundle does not have
    base.conn.execute("INSERT INTO brand_aliases VALUES ('SHARP', 'sharp', 1)")      # a brand's own key
    again = MatchIndex(base.conn)
    assert again.alias_by_key == {"夏普": (again.brand_by_key["sony"],)}
    assert again.name_by_key["sharp"] == (again.brand_by_key["sharp"],) != (again.brand_by_key["sony"],)
    assert "索尼" not in again.name_by_key and again.aliases == {again.brand_by_key["sony"]: ("夏普",)}
    assert [c.brand for c in again.match(None, None, ["sharp"])] == ["SHARP"]


def test_a_bundle_with_no_table_works_and_so_does_one_whose_table_is_empty(ledger, tmp_path):
    built = build.build_bundle(ledger, "full", aliases=())
    build.write_bundle(built, tmp_path / "empty", ledger)
    empty = MatchIndex.open(tmp_path / "empty" / build.BUNDLE_FILE)
    assert empty.alias_by_key == {} and empty.aliases == {}
    # an older bundle: the table is not there at all
    old = tmp_path / "old.sqlite"
    old.write_bytes(built.files[build.BUNDLE_FILE])
    conn = sqlite3.connect(old)
    conn.execute("DROP TABLE brand_aliases")
    conn.commit()
    conn.close()
    reader = MatchIndex.open(old)
    assert reader.alias_by_key == {}
    for text in ("sony", "topping dx3 pro", "zed rc 5"):
        assert answer(reader.match(None, None, [text])) == answer(empty.match(None, None, [text]))
        assert reader.suggest(text) == empty.suggest(text)
    assert reader.match(None, None, ["索尼"]) == [] and reader.suggest("索尼").is_empty


def test_a_reader_of_version_1_that_reads_only_the_five_tables_works_on_a_bundle_with_the_table(full):
    """What the Kotlin matcher reads: brands, models, controls, ngram, and nothing else."""
    conn = sqlite3.connect(":memory:")
    conn.deserialize(full.files[build.BUNDLE_FILE])
    for sql in ("SELECT id, name, norm, first_model, model_count FROM brands ORDER BY id",
                "SELECT ids FROM ngram WHERE gram = '^tv'", "SELECT brand_id, name, kind FROM models WHERE id = 1",
                "SELECT remote_id FROM controls WHERE model_id = 1 ORDER BY remote_id"):
        assert conn.execute(sql).fetchall() is not None
    assert [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")] == sorted([
        "meta", "sources", "vocab_groups", "vocab_keys", "brands", "brand_aliases", "models", "controls", "remotes",
        "remote_refs", "keys", "signals", "ngram", "excluded_brands"])
    assert dict(conn.execute("SELECT key, value FROM meta"))["schemaVersion"] == "1"


# --- a search typed in Latin letters is what it was -------------------------------------------------------------


def test_every_latin_query_of_the_matching_vectors_gives_the_same_answer_with_the_table_and_without(index, without):
    queries = json.loads((ROOT / "tests" / "vectors" / "matching_vectors.json").read_text(encoding="utf-8"))["queries"]
    latin = queries[:41]                                    # the queries of D97 before the aliases (next test)
    assert not any("\u3400" <= c <= "\u9fff" for q in latin for c in json.dumps(q, ensure_ascii=False))
    for q in latin:
        a = index.match(q["brand"], q["model"], q["texts"])
        b = without.match(q["brand"], q["model"], q["texts"])
        assert a == b, q["note"]
        assert [(c.brand, c.model, c.permille) for c in a] == [(x["brand"], x["model"], x["permille"]) for x in q["answer"]]


def digest(value) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def test_the_vectors_only_grew_the_queries_and_answers_of_d97_are_the_first_41_untouched():
    """Pinned by digest, from the file as it was before brand aliases existed: the 41 queries with their answers,
    the first 42 entries of the catalog and the similarity of 23 pairs. A change to the rules that moves one
    of them is a decision (D97), and moves the digest on purpose."""
    doc = json.loads((ROOT / "tests" / "vectors" / "matching_vectors.json").read_text(encoding="utf-8"))
    assert digest(doc["queries"][:41]) == "941bb6885c73b8265180e44f60a5218db738b6e54d3b9297089421db74d4d8ea"
    assert digest(doc["catalog"][:42]) == "862c7098e7847a2e867f7554fba037c74221b1461983b5d6ae56d116a652d6a5"
    assert digest(doc["similarity"]) == "7d522fe5cfabceff10660ca63848316a70425a1c9243856941b06c88b8356c0d"
    assert doc["queries"][40]["note"] == "a brand that was given does"
    assert doc["queries"][41]["note"].startswith("an alias typed alone")
    assert len(doc["queries"]) >= 62


def test_random_latin_queries_give_the_same_answers_and_suggestions_with_the_table_and_without(index, without):
    rng = random.Random(77)
    words = ["samsung", "sony", "un50nu6900f", "kd", "49", "x", "8088", "lg", "42lb5800", "roku", "ultra", "phil", "ips",
             "panasonic", "nv", "fs", "200", "s", "bdp", "360", "vizio", "ünï", "ω1000", "harman", "kardon", "avr",
             "161", "dvd", "sat", "1", "zed", "rc", "5", "-", "/", "tv", "so", "sa", "ph", "la", "ni"]
    for _ in range(1500):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 5)))
        assert index.match(None, None, [text]) == without.match(None, None, [text]), text
        brand, model = rng.choice(words), rng.choice(words)
        assert index.match(brand, model, [text]) == without.match(brand, model, [text]), (brand, model, text)
        limit = rng.choice([1, 3, 8, 20])
        assert index.suggest(text, limit) == without.suggest(text, limit), (text, limit)


def test_over_the_real_selected_bundle_a_latin_search_gives_the_same_with_the_table_and_without(real_selected, tmp_path):
    _, directory = real_selected
    with_table = MatchIndex.open(directory / build.BUNDLE_FILE)
    assert with_table.alias_by_key, "the real bundle carries aliases of the brands it has"
    bare = tmp_path / "bare.sqlite"
    bare.write_bytes((directory / build.BUNDLE_FILE).read_bytes())
    conn = sqlite3.connect(bare)
    conn.execute("DROP TABLE brand_aliases")
    conn.commit()
    conn.close()
    plain = MatchIndex.open(bare)
    assert plain.alias_by_key == {}
    queries = search_eval.generate(with_table.conn, per_class=40)
    assert len(queries) >= 300
    for q in queries:
        assert with_table.match(None, None, [q.text]) == plain.match(None, None, [q.text]), q.text
        assert with_table.suggest(q.text) == plain.suggest(q.text), q.text
        for n in range(1, len(q.text) + 1, 3):
            assert with_table.suggest(q.text[:n], 5) == plain.suggest(q.text[:n], 5), q.text[:n]


def test_the_real_bundle_resolves_the_seed_names_of_the_brands_it_carries(real_selected):
    _, directory = real_selected
    real = MatchIndex.open(directory / build.BUNDLE_FILE)
    carried = {search_norm(name) for (name,) in real.conn.execute("SELECT name FROM brands")}
    expected = rows_for(load_aliases(), {k: v for k, v in real.brand_by_key.items()})
    assert [(a, k, b) for a, k, b in real.conn.execute("SELECT alias, norm, brand_id FROM brand_aliases ORDER BY norm")] == expected
    assert len(expected) >= 20
    for alias, key, brand_id in expected:
        top = real.match(None, None, [alias])
        assert top and top[0].brand == real.brands[brand_id][0] and top[0].model is None, alias
        assert top[0].permille == 800 and top[0].models
        assert real.suggest(alias).brands[0].brand_id == brand_id, alias
    assert {"sony", "samsung", "philips", "panasonic"} <= carried
    # a Chinese name and a model number, with the space and without
    for text in ("三星 UN50NU6900F", "三星UN50NU6900F"):
        got = real.match(None, None, [text])
        assert (got[0].brand, got[0].model, got[0].permille) == ("SAMSUNG", "UN50NU6900F", 900), text
    # Skyworth is not in the selected bundle: its names are not either, in either script
    assert real.match(None, None, ["创维"]) == [] and real.match(None, None, ["創維"]) == []


def test_the_vectors_show_chinese_in_both_files():
    for name in ("matching_vectors.json", "suggest_vectors.json"):
        doc = json.loads((ROOT / "tests" / "vectors" / name).read_text(encoding="utf-8"))
        assert doc["aliases"] and all(set(a) == {"brand", "alias"} for a in doc["aliases"])
        text = json.dumps(doc["queries"] if "queries" in doc else doc["suggest"], ensure_ascii=False)
        for fragment in ("索尼", "新力", "飞利浦", "飛利浦", "國際牌"):
            assert fragment in text, (name, fragment)
    assert any(a["alias"] == "創維" for a in suggest_vectors.ALIASES) and any(a["alias"] == "创维" for a in suggest_vectors.ALIASES)
