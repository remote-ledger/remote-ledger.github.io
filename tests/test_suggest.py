"""``suggest``: what a person who is still typing is offered (D100), and the vectors a port is held to.

``suggest`` was a Kotlin function first; the Python is a port held to answers made from the
Kotlin over the real bundles (D100 has the numbers, and ``tests/vectors/suggest_vectors.json``
is what is left of them). These tests hold the Python to its own rules without the Kotlin:

* each rule of the module's docstring (S1 to S5), on the small catalog of the vectors, and each
  tie-break, on catalogs written here for it;
* an independent reference: what is offered inside a brand is worked out again from the list of
  entries with plain Python, and compared with the index's answer for hundreds of queries;
* properties that must hold whatever the catalog is: a smaller limit is the start of a larger one,
  the same query gives the same answer on a cold and a warm index, what is typed is never lost;
* the committed vectors, held to the code exactly, and to the notes that describe them.

The hint (``prefer``, S6, D106) and ``warm`` (D107) are in ``test_suggest_prefer.py``; what is here is what
``suggest`` gave before them, which they must leave as it was.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from remote_ledger import cli, matching
from remote_ledger.bundle import build, matching_vectors, suggest_vectors
from remote_ledger.bundle.textnorm import search_norm
from remote_ledger.matching import BrandSuggestion, MatchIndex, ModelSuggestion, Suggestions
from remote_ledger.serialize import dumps

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "vectors" / "suggest_vectors.json"


@pytest.fixture(scope="module")
def index():
    """The small catalog of the suggest vectors, with its brand aliases."""
    return MatchIndex.from_entries(suggest_vectors.CATALOG, suggest_vectors.ALIASES)


@pytest.fixture(scope="module")
def plain():
    """The catalog of the matching vectors with no alias: what the Kotlin was tested on."""
    return MatchIndex.from_entries(matching_vectors.CATALOG)


def names(found):
    return [b.name for b in found]


def models(found):
    return [m.model for m in found]


def brand_id(index, name):
    return next(i for i, (n, *_rest) in index.brands.items() if n == name)


# --- the types --------------------------------------------------------------------------------------------


def test_the_answer_is_brands_and_models_with_what_the_kotlin_types_hold(plain):
    got = plain.suggest("sony kd 49", 5)
    assert isinstance(got, Suggestions) and not got.is_empty
    brand, = got.brands
    assert brand == BrandSuggestion(brand.brand_id, "SONY", 6) and plain.brands[brand.brand_id][0] == "SONY"
    first = got.models[0]
    assert first == ModelSuggestion(first.model_id, brand.brand_id, "SONY", "KD - 49 X 8088", (201, 202), False)
    part = plain.suggest("rm-ed011").models[0]
    assert part.part_number is True and part.remote_ids == (207,)
    assert isinstance(got.brands, tuple) and isinstance(got.models, tuple)
    assert matching.suggest(plain, "sony kd 49", 5) == got                   # the function form
    assert Suggestions((), ()).is_empty


def test_suggest_brands_is_the_brands_of_suggest_and_suggest_models_the_models_inside_a_brand(index):
    rng = random.Random(4)
    pool = ["s", "sa", "sony", "sony kd", "samsung un50", "kd 49 sony", "phillips", "lg 42", "tvco tv1", "x", "",
            "三星 un5", "海信55e7", "uni", "bigco"]
    for _ in range(300):
        text = " ".join(rng.choice(pool) for _ in range(rng.randint(1, 3)))
        limit = rng.choice([1, 2, 3, 8, 20])
        whole = index.suggest(text, limit)
        assert tuple(index.suggest_brands(text, limit)) == whole.brands, text
    sony = brand_id(index, "SONY")
    assert [m.model for m in index.suggest_models(sony, "kd")] == models(index.suggest("sony kd").models)


# --- S0: nothing asked ----------------------------------------------------------------------------------------


def test_the_empty_query_the_query_with_no_letter_and_a_limit_of_nothing_offer_nothing(plain):
    assert plain.suggest("").is_empty and plain.suggest("  -- ?? ").is_empty
    assert plain.suggest("sony", 0).is_empty and plain.suggest("sony", -1).is_empty
    for query in ("s", "sony", "sony samsung", "samsung un50", "un50nu6900f", "lg"):
        for limit in (0, -1, -2, -100):
            assert plain.suggest(query, limit).is_empty, (query, limit)
            assert plain.suggest_brands(query, limit) == []
    assert plain.suggest_brands("", 5) == [] and plain.suggest_brands("sony", 0) == []
    assert plain.suggest_models(brand_id(plain, "SONY"), "kd", 0) == []
    assert plain.suggest_models(brand_id(plain, "SONY"), "kd", -4) == []


# --- S1: the brands ----------------------------------------------------------------------------------------


def test_brands_that_start_with_what_was_typed_come_with_the_most_models_first_then_by_name(plain):
    # SAMSUNG and SONY have six models each, SAT has one
    assert names(plain.suggest_brands("s")) == ["SAMSUNG", "SONY", "SAT"]
    assert names(plain.suggest_brands("sa")) == ["SAMSUNG", "SAT"]
    assert names(plain.suggest_brands("sams")) == ["SAMSUNG"]
    assert names(plain.suggest_brands("samsun")) == ["SAMSUNG"]


def test_the_brand_whose_key_is_the_query_comes_first_however_few_models_it_has(plain):
    assert names(plain.suggest_brands("sat"))[0] == "SAT"
    assert names(plain.suggest_brands("lg")) == ["LG"]
    assert names(plain.suggest_brands("harman-kardon")) == ["HARMAN KARDON"]     # separators do not matter
    assert names(plain.suggest_brands("Harman K")) == ["HARMAN KARDON"]
    # a brand with one model before brands with more: the key is exactly the query
    small = MatchIndex.from_entries([
        {"brand": "AB", "model": "M 1", "remotes": [1]},
        {"brand": "ABC", "model": "M 1", "remotes": [2]}, {"brand": "ABC", "model": "M 2", "remotes": [3]},
        {"brand": "ABD", "model": "M 1", "remotes": [4]}, {"brand": "ABD", "model": "M 2", "remotes": [5]},
        {"brand": "ABD", "model": "M 3", "remotes": [6]}])
    assert names(small.suggest_brands("ab")) == ["AB", "ABD", "ABC"]
    assert names(small.suggest_brands("a")) == ["ABD", "ABC", "AB"]


def test_brands_with_as_many_models_go_by_name(plain):
    both = [b for b in plain.brands.values() if b[3] == 6]
    assert sorted(b[0] for b in both) == ["SAMSUNG", "SONY"]
    assert names(plain.suggest_brands("s"))[:2] == ["SAMSUNG", "SONY"]
    # and by the name as it is written, in code point order: a capital before a small letter
    mixed = MatchIndex.from_entries([{"brand": "Zeta", "model": "A 1", "remotes": [1]},
                                     {"brand": "ZETA2", "model": "A 1", "remotes": [2]},
                                     {"brand": "zeta3", "model": "A 1", "remotes": [3]}])
    assert names(mixed.suggest_brands("zeta")) == ["Zeta", "ZETA2", "zeta3"]
    assert names(mixed.suggest_brands("z")) == ["ZETA2", "Zeta", "zeta3"]


def test_a_brand_is_offered_when_the_query_only_reads_as_it_through_the_matchers_similarity(plain):
    assert names(plain.suggest_brands("Samsung Electronics")) == ["SAMSUNG"]
    assert names(plain.suggest_brands("Phillips")) == ["PHILIPS"]
    assert names(plain.suggest_brands("Uni")) == ["Ünï"]
    assert plain.suggest_brands("zzzz") == [] and plain.suggest_brands("zzzzzz") == []


def test_the_groups_come_in_order_exact_key_then_starts_then_named_by_a_run_then_the_matchers_reading(index):
    # SONY is the exact key; SONNY (one insertion away: 800) is the matcher's reading and comes after
    assert names(index.suggest_brands("sony")) == ["SONY", "SONNY"]
    assert names(index.suggest_brands("sonny")) == ["SONNY", "SONY"]
    # with room for one the matcher's reading is not asked for
    assert names(index.suggest_brands("sony", 1)) == ["SONY"]
    # named by a run, then the matcher's reading of the whole query: kd 49 sony names SONY
    assert names(index.suggest_brands("kd 49 sony")) == ["SONY"]
    # named by a run although no key starts with the whole query
    assert names(index.suggest_brands("sa samsung")) == ["SAMSUNG"]


def test_a_brand_named_by_a_word_of_the_query_is_offered_longest_run_first_then_by_id(plain):
    assert names(plain.suggest_brands("sony kd 49")) == ["SONY"]
    assert names(plain.suggest_brands("sony samsung")) == ["SAMSUNG", "SONY"]        # 7 before 4
    assert names(plain.suggest_brands("sony roku")) == ["ROKU", "SONY"]              # 4 and 4: ROKU has the lower id
    assert names(plain.suggest_brands("lg sony philips x")) == ["PHILIPS", "SONY", "LG"]
    assert names(plain.suggest_brands("sony sony")) == ["SONY"]
    assert brand_id(plain, "ROKU") < brand_id(plain, "SONY")        # brands are numbered in the order of their keys


def test_a_run_of_adjacent_words_names_a_brand_and_only_the_first_twelve_words_are_looked_at(plain):
    assert names(plain.suggest_brands("harman kardon avr")) == ["HARMAN KARDON"]
    eleven = "a b c d e f g h i j k"
    assert names(plain.suggest_brands(f"{eleven} samsung")) == ["SAMSUNG"]            # the twelfth word
    assert plain.suggest_brands(f"{eleven} l samsung") == []                          # the thirteenth
    assert plain.suggest(f"{eleven} l samsung").is_empty


def test_one_character_gives_the_brands_that_start_with_it_and_nothing_more(plain):
    assert names(plain.suggest_brands("s")) == ["SAMSUNG", "SONY", "SAT"]
    assert plain.suggest_brands("x") == [] and plain.suggest_brands("5") == []
    assert plain.suggest("s").models == ()
    # a key of two characters is looked up as a brand and read as one: 'lg' is a brand, 'sn' no brand's start
    assert names(plain.suggest_brands("lg")) == ["LG"] and plain.suggest_brands("sn") == []


def test_the_limit_cuts_every_list_after_its_order_and_a_smaller_limit_is_the_start_of_a_larger(index):
    assert names(index.suggest_brands("s", 2)) == ["SAMSUNG", "SONY"]
    assert names(index.suggest_brands("s", 1)) == ["SAMSUNG"]
    pool = ["s", "sa", "so", "sony", "sony samsung", "samsung", "bigco", "tvco", "lg 42", "t", "h", "kd 49", "un50nu69",
            "sonyy", "海", "创维", "索尼 三星", "a", "o", "tvco alpha", "cpco", "ünï", "sony kd", "roku"]
    for query in pool:
        biggest = index.suggest(query, 30)
        for limit in (1, 2, 3, 5, 8, 12):
            got = index.suggest(query, limit)
            assert got.brands == biggest.brands[:limit], (query, limit)
            assert got.models == biggest.models[:limit], (query, limit)


# --- S2 and S3: a query that names a brand lists its models ------------------------------------------------


def test_a_query_that_names_a_brand_lists_that_brands_models_for_the_rest_of_the_query(plain):
    got = plain.suggest("samsung un50", 5)
    assert names(got.brands) == ["SAMSUNG"]
    assert models(got.models) == ["UN50NU6800F", "UN50NU6900F"]                # one remote each: by name
    assert [m.brand for m in got.models] == ["SAMSUNG", "SAMSUNG"]
    assert models(plain.suggest("un50 samsung", 5).models) == ["UN50NU6800F", "UN50NU6900F"]     # the brand after
    assert models(plain.suggest("samsung", 20).models) == models(plain.suggest_models(brand_id(plain, "SAMSUNG"), ""))
    assert models(plain.suggest("samsung un5 0").models) == ["UN50NU6800F", "UN50NU6900F"]       # words are joined


def test_the_models_of_a_brand_that_go_on_from_what_was_typed_come_most_remotes_first_then_by_name(plain):
    sony = brand_id(plain, "SONY")
    # KD - 49 X 8088 has two remotes, KD - 49 X 7055 and KDL-40EX720 one each; ' ' sorts before 'L'
    assert models(plain.suggest_models(sony, "kd")) == ["KD - 49 X 8088", "KD - 49 X 7055", "KDL-40EX720"]
    assert models(plain.suggest_models(sony, "kd49")) == ["KD - 49 X 8088", "KD - 49 X 7055"]
    assert models(plain.suggest_models(sony, "KD - 49 X 80")) == ["KD - 49 X 8088"]
    assert models(plain.suggest_models(sony, "kd49", 2)) == ["KD - 49 X 8088", "KD - 49 X 7055"]
    assert models(plain.suggest_models(sony, "kd49", 1)) == ["KD - 49 X 8088"]
    assert plain.suggest_models(sony, "kd50") == []                              # a typo is not forgiven here
    assert plain.suggest_models(sony, "UN50") == []                              # another brand's models do not count


def test_the_model_whose_key_is_what_was_typed_comes_first_even_with_fewer_remotes(index):
    acme = brand_id(index, "TVCO")
    assert models(index.suggest_models(acme, "tv1")) == ["TV 1", "TV 10", "TV 100", "TV 1000"]
    assert models(index.suggest_models(acme, "tv10")) == ["TV 10", "TV 100", "TV 1000"]
    assert models(index.suggest_models(acme, "tv100")) == ["TV 100", "TV 1000"]
    # an empty query does not look at the keys: most remotes first, then by name
    assert models(index.suggest_models(acme, ""))[:3] == ["TV 10", "TV 100", "TV 1000"]
    # a limit keeps the exact one
    assert models(index.suggest_models(acme, "tv1", 1)) == ["TV 1"]
    assert models(index.suggest_models(acme, "tv1", 2)) == ["TV 1", "TV 10"]


def test_models_that_tie_in_remotes_go_by_name_in_code_point_order(index):
    tv = brand_id(index, "TVCO")
    assert models(index.suggest_models(tv, "alpha")) == ["ALPHA 3", "Alpha 1", "alpha 2"]
    cp = brand_id(index, "CPCO")
    # U+FF46 is before U+20000 in code points, and after it in UTF-16 units
    assert models(index.suggest_models(cp, "")) == ["ｆ 1", "\U00020000 1"]
    assert sorted(["\U00020000 1", "ｆ 1"], key=lambda s: s.encode("utf-16-be")) == ["\U00020000 1", "ｆ 1"]


def test_an_empty_query_gives_the_brands_models_most_remotes_first_then_by_name(plain):
    sony = brand_id(plain, "SONY")
    assert models(plain.suggest_models(sony, "")) == [
        "BDP - S 360", "KD - 49 X 8088", "BDP - S 300", "KD - 49 X 7055", "KDL-40EX720", "RM-ED011"]
    assert models(plain.suggest_models(sony, "", 2)) == ["BDP - S 360", "KD - 49 X 8088"]
    assert plain.suggest_models(9999, "kd") == []                                # no such brand
    assert models(plain.suggest_models(brand_id(plain, "SONY"), "kd", 1)) == ["KD - 49 X 8088"]


def test_a_suggested_model_says_whose_it_is_which_remotes_control_it_and_whether_it_is_a_part_number(plain):
    sony = brand_id(plain, "SONY")
    part = plain.suggest_models(sony, "rm-ed011")[0]
    assert part == ModelSuggestion(part.model_id, sony, "SONY", "RM-ED011", (207,), True)
    device = plain.suggest_models(sony, "kdl")[0]
    assert device.part_number is False and device.remote_ids == (208,)
    assert plain.suggest_models(sony, "bdps360")[0].remote_ids == (204, 205, 206)


def test_two_brands_named_give_the_first_ones_models_first_up_to_the_limit(index):
    both = index.suggest("sony samsung", 8)
    assert names(both.brands) == ["SAMSUNG", "SONY"]
    assert [m.brand for m in both.models] == ["SAMSUNG"] * 6 + ["SONY"] * 2
    assert [m.brand for m in index.suggest("sony samsung", 4).models] == ["SAMSUNG"] * 4
    assert [m.brand for m in index.suggest("sony samsung", 7).models] == ["SAMSUNG"] * 6 + ["SONY"]
    # a rest that only one of them has
    only = index.suggest("sony samsung un5")
    assert {m.brand for m in only.models} == {"SAMSUNG"} and names(only.brands) == ["SAMSUNG", "SONY"]


def test_a_brand_typed_twice_loses_only_its_first_word_to_the_brand_and_the_second_stays_in_the_rest(plain):
    """A run's key counts once, at its first place (``runs``), so the second ``lg`` is not a brand
    word: the rest is ``lg42``, which no model starts with. A quirk of the Kotlin, kept (D100)."""
    assert models(plain.suggest("lg 42").models) == ["42LB5700", "42LB5800"]
    assert plain.suggest("lg lg 42").models == () and names(plain.suggest("lg lg 42").brands) == ["LG"]
    assert models(plain.suggest("lg lg").models) == []                   # the rest is "lg"


def test_the_rest_takes_every_word_that_is_not_a_brands_not_only_the_first_twelve(index):
    # ZED and eleven letters: the rest is the whole key of a model, which is offered; a twelfth letter makes
    # the rest longer than any key, though the first twelve words alone would still give the model
    assert models(index.suggest("zed a b c d e f g h i j k").models) == ["A B C D E F G H I J K"]
    assert index.suggest("zed a b c d e f g h i j k l").models == ()
    assert names(index.suggest("zed a b c d e f g h i j k l").brands) == ["ZED"]
    words = "a b c d e f g h i j k l m n"
    assert index.suggest(f"samsung {words} un50").models == ()
    assert index.suggest("samsung un50").models != ()


def test_a_brand_of_one_character_is_offered_as_a_start_but_never_named(index):
    assert names(index.suggest_brands("q")) == ["Q", "QR", "QRSTU"]
    assert index.suggest("q").models == ()                 # a run needs two characters to name a brand
    # the model is found by the matcher as a model of its own (S5), not through the brand
    assert models(index.suggest("q 100").models) == ["Q 100"] and index.suggest("q 100").brands == ()
    assert [m.model for m in index.suggest("q 10").models] == []
    assert models(index.suggest("qr").models) == ["QR 200"]
    assert index.match(None, None, ["q"]) == []


def test_the_brand_a_query_names_comes_before_the_one_the_matcher_reads_the_whole_query_as(index):
    # QR is named by its word; QRSTU is one edit from the key 'qrstv'
    assert names(index.suggest_brands("qr stv")) == ["QR", "QRSTU"]
    assert names(index.suggest_brands("qr stv", 1)) == ["QR"]
    assert names(index.suggest_brands("qrstv")) == ["QRSTU"]                   # no word names QR here


# --- S5: no brand named --------------------------------------------------------------------------------------


def test_a_query_that_names_no_brand_gives_the_models_the_matcher_finds_brand_only_answers_left_out(plain):
    got = plain.suggest("un50nu69", 5)
    assert got.brands == ()
    assert models(got.models) == [c.model for c in plain.match(None, None, ["un50nu69"]) if c.model is not None]
    assert got.models[0].model == "UN50NU6900F" and got.models[0].remote_ids == (101,)
    assert plain.suggest("un50", 5).models == ()                         # four characters: the matcher wants five
    assert plain.suggest("zzzz", 5).models == ()
    assert len(plain.suggest("un50nu69", 1).models) == 1


def test_that_answer_is_the_matchers_for_the_query_as_one_text_for_every_query_of_the_vectors(plain):
    for text in ("un50nu6900f", "un50nu690f", "UN50NU69OOF", "kd49x8088", "bn59-01199f", "bdp-s360", "s 360", "42lb580",
                 "CI 500 TWN(SAT 1)"):
        want = [c for c in plain.match(None, None, [text], limit=8) if c.model is not None]
        got = plain.suggest(text, 8)
        if got.brands:
            continue                                                        # a brand is named: S3 applies
        assert [(m.brand, m.model, m.remote_ids) for m in got.models] == [
            (c.brand, c.model, c.remote_ids) for c in want], text


# --- an independent reference ---------------------------------------------------------------------------


def reference_inside(entries, brand, query):
    """The models of ``brand`` to offer for ``query``, from the entries and nothing else: the models
    whose search key starts with the query's, the one whose key it is first, then by number of remotes
    (most first) and name. Written without the index, from the rule in the docstring."""
    key = search_norm(query)
    by_key: dict[str, tuple[str, set[int]]] = {}
    for entry in entries:
        if search_norm(entry["brand"]) != search_norm(brand):
            continue
        name, remotes = by_key.setdefault(search_norm(entry["model"]), (entry["model"], set()))
        remotes.update(entry["remotes"])
    found = [(model_key, name, len(remotes)) for model_key, (name, remotes) in by_key.items()
             if model_key.startswith(key)]
    found.sort(key=lambda t: (t[0] != key if key else False, -t[2], t[1]))
    return [name for _, name, _ in found]


def test_what_is_offered_inside_a_brand_is_the_reference_for_hundreds_of_queries(index):
    entries = suggest_vectors.CATALOG
    rng = random.Random(12)
    brand_names = sorted({e["brand"] for e in entries})
    checked = 0
    for brand in brand_names:
        keys = sorted({search_norm(e["model"]) for e in entries if e["brand"] == brand})
        queries = {""}
        for key in keys:
            queries.update(key[:n] for n in range(1, len(key) + 1))
        queries.update(rng.choice(keys)[:rng.randint(1, 4)] + rng.choice("xz9") for _ in range(4))
        owner = brand_id(index, brand)
        for query in sorted(queries):
            for limit in (3, 8, 100):
                want = reference_inside(entries, brand, query)[:limit]
                assert models(index.suggest_models(owner, query, limit)) == want, (brand, query, limit)
                checked += 1
    assert checked > 600


def test_the_brands_that_start_with_a_query_are_the_reference_for_every_start_of_every_brand(index):
    entries = suggest_vectors.CATALOG
    count: dict[str, int] = {}
    seen: dict[str, set[str]] = {}
    for e in entries:
        key = search_norm(e["brand"])
        seen.setdefault(key, set()).add(search_norm(e["model"]))
        count[key] = len(seen[key])
    spelling = {search_norm(e["brand"]): e["brand"] for e in entries}
    for key in sorted(count):
        for n in range(1, len(key) + 1):
            typed = key[:n]
            starting = [k for k in count if k.startswith(typed)]
            want = sorted(starting, key=lambda k: (k != typed, -count[k], spelling[k]))
            got = [b.name for b in index.suggest_brands(typed, 100)][:len(want)]
            assert got == [spelling[k] for k in want], typed


# --- properties ----------------------------------------------------------------------------------------------


def test_typing_a_model_letter_by_letter_never_offers_one_that_does_not_go_on_and_never_loses_the_one_typed(plain):
    for target, brand in (("KD - 49 X 8088", "SONY"), ("UN50NU6900F", "SAMSUNG"), ("BDP - S 360", "SONY")):
        typed = search_norm(target)
        owner = brand_id(plain, brand)
        for n in range(1, len(typed) + 1):
            offered = models(plain.suggest_models(owner, typed[:n], 8))
            assert all(search_norm(m).startswith(typed[:n]) for m in offered), (typed[:n], offered)
            assert target in offered, (typed[:n], offered)
            # and through suggest, with the brand typed first
            assert target in models(plain.suggest(f"{brand.lower()} {typed[:n]}", 8).models), typed[:n]


def test_the_same_query_gives_the_same_list_whatever_was_asked_before(index):
    queries = ["s", "sa", "sony", "sony kd", "sony kd 49", "samsung", "samsung un50", "un50nu69", "lg", "roku ultra", "x5",
               "harman", "ph", "42pf", "海信 55e7", "tvco tv1", "bigco", "sony samsung"]
    first = [index.suggest(q, 6) for q in queries]
    backwards = [index.suggest(q, 6) for q in reversed(queries)][::-1]
    fresh = MatchIndex.from_entries(suggest_vectors.CATALOG, suggest_vectors.ALIASES)
    assert first == backwards == [fresh.suggest(q, 6) for q in queries]


def test_a_query_is_answered_the_same_with_the_caches_empty_and_full(index):
    other = MatchIndex.from_entries(suggest_vectors.CATALOG, suggest_vectors.ALIASES)
    for _ in range(3):
        for q in ("samsung un50", "tvco", "sony kd", "un50nu6900f"):
            assert other.suggest(q) == index.suggest(q)
    other._brand_models.cache_clear()
    other._remotes.cache_clear()
    assert other.suggest("sony kd") == index.suggest("sony kd")


def test_every_offered_model_belongs_to_the_brand_it_says_and_to_the_catalog(index):
    entries = {(search_norm(e["brand"]), search_norm(e["model"])) for e in suggest_vectors.CATALOG}
    texts = ["s", "sa", "so", "sony kd", "samsung un", "un50nu69", "tvco tv", "bigco bg", "创维", "海信55e7", "lg 4",
             "uni", "harman kardon", "cpco", "t"]
    for text in texts:
        for limit in (1, 8, 40):
            got = index.suggest(text, limit)
            assert len(got.brands) <= limit and len(got.models) <= limit
            assert len({b.brand_id for b in got.brands}) == len(got.brands)
            assert len({m.model_id for m in got.models}) == len(got.models)
            for m in got.models:
                assert (search_norm(m.brand), search_norm(m.model)) in entries
                assert index.brands[m.brand_id][0] == m.brand


# --- the real bundle -------------------------------------------------------------------------------------------


def test_suggestions_over_the_real_selected_bundle(real_selected):
    _, directory = real_selected
    real = MatchIndex.open(directory / build.BUNDLE_FILE)
    got = real.suggest("samsung un50", 8)
    assert names(got.brands) == ["SAMSUNG"] and got.models
    assert all(search_norm(m.model).startswith("un50") and m.brand == "SAMSUNG" for m in got.models)
    # every brand is carried since D117, so a prefix has more than one answer; the first is the biggest
    assert names(real.suggest_brands("sam"))[0] == "SAMSUNG"
    top = real.suggest("sony", 8)
    assert names(top.brands)[0] == "SONY" and len(top.models) == 8     # SONYSAT and SHONY follow (D117)
    assert real.suggest("zq9x7wv5kk3").is_empty
    # Chinese names of the brands the bundle carries, in both scripts (D101)
    assert names(real.suggest("三星").brands) == ["SAMSUNG"]
    assert names(real.suggest("索尼").brands) == names(real.suggest("新力").brands) == ["SONY"]
    assert names(real.suggest("飞利浦").brands) == names(real.suggest("飛利浦").brands) == ["PHILIPS"]
    assert models(real.suggest("三星 un50", 8).models) == models(got.models)


# --- the vectors ---------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vectors():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_the_committed_vectors_are_what_the_generator_gives():
    """The file does not depend on the repository's data, so it is held to the code exactly."""
    assert VECTORS.read_text(encoding="utf-8") == dumps(suggest_vectors.build())


def test_the_vectors_are_the_catalog_answered(vectors):
    index = MatchIndex.from_entries(vectors["catalog"], vectors["aliases"])
    assert vectors["catalog"] == suggest_vectors.CATALOG and vectors["aliases"] == suggest_vectors.ALIASES
    assert len(vectors["suggest"]) >= 200 and len(vectors["suggestModels"]) >= 20
    for case in vectors["suggest"]:
        got = index.suggest(case["query"], case["limit"])
        assert [b["name"] for b in case["brands"]] == names(got.brands), case["note"]
        assert [(b["brandId"], b["modelCount"]) for b in case["brands"]] == [
            (b.brand_id, b.model_count) for b in got.brands], case["note"]
        assert case["models"] == [
            {"modelId": m.model_id, "brandId": m.brand_id, "brand": m.brand, "model": m.model,
             "remoteIds": list(m.remote_ids), "partNumber": m.part_number} for m in got.models], case["note"]
        assert len(case["brands"]) <= max(case["limit"], 0) and len(case["models"]) <= max(case["limit"], 0)
    for case in vectors["suggestModels"]:
        got = index.suggest_models(case["brandId"], case["query"], case["limit"])
        assert index.brands[case["brandId"]][0] == case["brand"]
        assert [m["model"] for m in case["models"]] == models(got), case["note"]


def test_the_vectors_state_the_constants_and_the_numbering(vectors):
    constants = vectors["constants"]
    for name in ("SUGGEST_LIMIT", "BRAND_MIN_KEY", "BRAND_CANDIDATES", "MAX_RUN", "MAX_TOKENS", "BRAND_MODELS"):
        assert constants[name] == getattr(matching, name)
    assert (constants["SUGGEST_LIMIT"], constants["BRAND_MIN_KEY"], constants["MAX_TOKENS"]) == (8, 2, 12)
    ids = {b["brandId"]: b["name"] for case in vectors["suggest"] for b in case["brands"]}
    keys = sorted({search_norm(e["brand"]) for e in vectors["catalog"]})
    spelling = {search_norm(e["brand"]): e["brand"] for e in vectors["catalog"]}
    for number, name in ids.items():
        assert spelling[keys[number - 1]] == name, "brands are numbered from 1 in the order of their search keys"
    assert "from 1" in vectors["about"] and "search keys" in vectors["about"]


def test_the_vectors_cover_each_rule_and_each_tie_break(vectors):
    cases = vectors["suggest"]
    notes = " | ".join(c["note"] for c in cases + vectors["suggestModels"])
    for fragment in ("empty query", "no letter or digit", "limit of 0", "negative limit", "exact key", "by name",
                     "lower id", "longer run", "matcher's similarity", "code point", "typo is not forgiven",
                     "most remotes", "twelfth", "thirteenth", "rest takes every word", "part number", "typing:",
                     "Simplified", "Traditional", "regional", "alias"):
        assert fragment in notes, fragment
    assert any(c["brands"] and not c["models"] for c in cases)               # a brand named, no model goes on
    assert any(c["models"] and not c["brands"] for c in cases)               # S5: models and no brand
    assert any(len(c["brands"]) >= 3 for c in cases)
    assert any(len(c["models"]) == c["limit"] for c in cases)                # the limit cut a list
    assert any(m["partNumber"] for c in cases for m in c["models"])
    assert {c["limit"] for c in cases} >= {-3, 0, 1, 2, 3, 4, 8, 12, 20}
    han = [c for c in cases if any("一" <= ch <= "鿿" for ch in c["query"])]
    assert len(han) >= 40 and {b["name"] for c in han for b in c["brands"]} >= {
        "SONY", "SAMSUNG", "PANASONIC", "PHILIPS", "HISENSE", "SKYWORTH"}


def test_design_quotes_the_size_of_the_suggest_vectors(vectors):
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    section = design[design.index("### D100"):design.index("### D101")]
    for fact in (f"{len(vectors['suggest'])} cases of `suggest`", f"{len(vectors['suggestModels'])} of `suggest_models`",
                 f"a catalog of {len(vectors['catalog'])} entries"):
        assert fact in section, fact


def test_the_command_writes_the_vectors_and_check_compares_them(tmp_path, monkeypatch, capsys):
    target = tmp_path / "s.json"
    monkeypatch.chdir(tmp_path)
    assert cli.main(["bundle", "suggest-vectors", "--file", str(target)]) == 0
    assert target.read_bytes() == VECTORS.read_bytes()
    assert cli.main(["bundle", "suggest-vectors", "--file", str(target), "--check"]) == 0
    target.write_text(target.read_text().replace("modelCount", "modelCnt", 1))
    assert cli.main(["bundle", "suggest-vectors", "--file", str(target), "--check"]) == 1
    assert "differs from the vectors the matcher gives" in capsys.readouterr().err
    assert cli.main(["bundle", "suggest-vectors", "--file", str(tmp_path / "missing.json"), "--check"]) == 1
