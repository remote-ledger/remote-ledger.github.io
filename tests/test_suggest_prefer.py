"""``suggest`` with a hint (S6, D106) and ``warm`` (D107).

The hint: brand names that go first **where S1 to S5 do not tell two entries apart**, and nowhere else.

* what ``prefer`` is and is not: names, the brands they stand for (``brand_ids``), a set, a string refused;
* the rule on the catalog of ties (``suggest_vectors.TIES``), written here by hand for each row of S6's list:
  the brand whose key is the query, brands that start with it, brands named by runs of equal length and
  their models, brands the matcher only reads the query as, models of equal score, and the limit;
* the properties, for thousands of generated queries and hints on catalogs made of ties: the hinted answer
  is the plain one with each run of tied entries re-ordered and nothing else, checked against an oracle
  written here from the rule and not from the code; a smaller limit is the start of a larger one; a limit
  only trades places inside one run;
* **no hint is today's answer**, exactly: against a copy of ``suggest`` as it was before the hint (D100),
  for random queries, with no ``prefer``, with names that are no brand, and with every brand;
* the committed vectors of the hint, held to the code, to the oracle and to their notes;
* ``warm``: what it keeps, that no traffic over other brands evicts it, and that it changes no answer.
"""

from __future__ import annotations

import itertools
import json
import random
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Callable, Sequence

import pytest

from remote_ledger import matching
from remote_ledger.bundle import suggest_vectors
from remote_ledger.bundle.textnorm import search_norm
from remote_ledger.matching import (
    BRAND_LISTINGS, BRAND_MIN_KEY, MAX_TOKENS, BrandSuggestion, MatchIndex, Suggestions, runs, tokens)

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "vectors" / "suggest_vectors.json"
BIG = 1000          # a limit no list of these catalogs reaches: the whole list in its order


@pytest.fixture(scope="module")
def ties():
    """The catalog of ties of the vectors, with its brand aliases."""
    return MatchIndex.from_entries(suggest_vectors.TIES, suggest_vectors.TIE_ALIASES)


@pytest.fixture(scope="module")
def main():
    """The small catalog of the suggest vectors (79 entries, 14 aliases)."""
    return MatchIndex.from_entries(suggest_vectors.CATALOG, suggest_vectors.ALIASES)


def names_of(found):
    return [b.name for b in found]


def brands(index, query, prefer=(), limit=8):
    return [b.name for b in index.suggest(query, limit, prefer).brands]


def models(index, query, prefer=(), limit=8):
    return [f"{m.brand}/{m.model}" for m in index.suggest(query, limit, prefer).models]


# --- what the hint is ----------------------------------------------------------------------------------------


def test_prefer_is_names_that_stand_for_brands_by_their_search_key_and_an_alias_stands_for_its_brand(ties):
    ids = ties.brand_ids
    assert len(ids(["alps"])) == 1 and ids(["ALPS"]) == ids(["alps"]) == ids([" A-L-P-S "]) == ids(["ａｌｐｓ"])
    assert ids(["南方"]) == ids(["SOUTH"]) and ids(["北方"]) == ids(["NORTH"])
    assert len(ids(["方向"])) == 2 and ids(["方向"]) == ids(["EAST", "WEST"])      # a name two brands share names both
    assert ids([]) == ids(()) == frozenset()
    assert ids(["Nobody", "", "!!!", "ALP", "ALPIN", "Alpsx", "海信", "ALPS NOT"]) == frozenset()   # no similarity, no start
    assert ids(["ALPS", "alps", "Alps", "南方"]) == ids(["南方", "ALPS"])             # a set: a name twice, any order
    assert ids({"ALPS", "ZEBRA"}) == ids(iter(["ZEBRA", "ALPS"])) == ids(("ZEBRA", "ALPS"))   # any iterable of names
    assert all(isinstance(i, int) and i in ties.brands for i in ids(["ALPS", "方向"]))


def test_a_single_string_is_a_mistake_for_a_list_of_names_and_is_refused_not_read_letter_by_letter(ties):
    for call in (lambda: ties.suggest("al", 8, "ALPS"), lambda: ties.suggest_brands("al", 8, "ALPS"),
                 lambda: ties.brand_ids("ALPS"), lambda: ties.suggest("", 8, "ALPS"), lambda: ties.suggest("al", 0, "")):
        with pytest.raises(TypeError, match="not a string"):
            call()


def test_the_hint_is_the_third_argument_of_suggest_and_of_suggest_brands_and_of_the_function_form(ties):
    plain = ties.suggest("al")
    assert ties.suggest("al", 8, ["ALPS"]) == ties.suggest("al", prefer=["ALPS"]) == matching.suggest(ties, "al", 8, ["ALPS"])
    assert ties.suggest("al", 8, ["ALPS"]) != plain
    assert ties.suggest("al", 8) == ties.suggest("al", 8, ()) == ties.suggest("al", 8, []) == plain
    assert matching.suggest(ties, "al") == plain


def test_suggest_brands_is_the_brands_of_suggest_with_the_same_hint(ties, main):
    rng = random.Random(8)
    pool = ["a", "al", "alp", "acid acme", "alpine ac", "korix", "kor", "tx1000", "方向", "北方 南方", "zebra", ""]
    names = ["ALPS", "ALPHA", "ACME", "KOREX", "WEST", "南方", "方向", "Nobody"]
    for _ in range(300):
        query = " ".join(rng.sample(pool, rng.randint(1, 2)))
        hint = rng.sample(names, rng.randint(0, 3))
        limit = rng.choice([0, 1, 2, 3, 8, 20])
        assert tuple(ties.suggest_brands(query, limit, hint)) == ties.suggest(query, limit, hint).brands, (query, hint, limit)
    assert names_of(main.suggest_brands("s", 3, ["SAT"])) == ["SAT", "SAMSUNG", "SONY"]
    assert main.suggest_brands("", 3, ["SAT"]) == [] and main.suggest_brands("s", 0, ["SAT"]) == []


def test_nothing_asked_still_offers_nothing_with_a_hint(ties):
    for query, limit in (("", 8), ("  -- ?? ", 8), ("al", 0), ("al", -1), ("tx1000", -5)):
        assert ties.suggest(query, limit, ["ALPS", "WEST"]).is_empty, (query, limit)
        assert ties.suggest_brands(query, limit, ["ALPS"]) == []


def test_suggest_models_has_no_hint_the_models_of_one_brand_are_never_reordered(ties):
    south = next(i for i, b in ties.brands.items() if b[0] == "SOUTH")
    assert [m.model for m in ties.suggest_models(south, "tx")] == ["TX1000", "TX10001"]
    with pytest.raises(TypeError):
        ties.suggest_models(south, "tx", 8, ["SOUTH"])                      # type: ignore[call-arg]


# --- the rule, by hand, on the catalog of ties --------------------------------------------------------------


def test_the_catalog_of_ties_is_ordered_as_the_rule_assumes_without_a_hint(ties):
    assert brands(ties, "al") == ["AL", "ALPINE", "ALTEC", "ALPHA", "ALPS"]     # the key; most models; name; fewer models
    assert brands(ties, "acid acme") == ["ACID", "ACME"]
    assert brands(ties, "korix") == ["KORAX", "KOREX"]
    assert models(ties, "tx1000") == ["EAST/TX1000", "NORTH/TX1000", "SOUTH/TX1000", "WEST/TX1000", "SOUTH/TX10001"]


def test_a_hinted_brand_comes_first_among_the_brands_that_start_with_the_query(ties):
    assert brands(ties, "alp") == ["ALPINE", "ALPHA", "ALPS"]
    assert brands(ties, "alp", ["ALPS"]) == ["ALPS", "ALPINE", "ALPHA"]
    assert brands(ties, "alp", ["alpha"]) == ["ALPHA", "ALPINE", "ALPS"]


def test_the_hint_goes_before_the_name_that_breaks_a_tie_of_model_counts_and_before_the_count_itself(ties):
    assert brands(ties, "al")[1:3] == ["ALPINE", "ALTEC"]                   # five models each: by name
    assert brands(ties, "al", ["ALTEC"])[1:3] == ["ALTEC", "ALPINE"]
    assert brands(ties, "a")[:2] == ["ALPINE", "ALTEC"]
    assert brands(ties, "a", ["ALPS"])[0] == "ALPS"                         # one model, before brands with five


def test_the_brand_whose_key_is_the_query_stays_first_whatever_is_hinted(ties):
    assert brands(ties, "al", ["ALPS"]) == ["AL", "ALPS", "ALPINE", "ALTEC", "ALPHA"]
    assert brands(ties, "al", ["ALPS", "ALPHA", "ALTEC", "ALPINE"]) == brands(ties, "al")     # all hinted: nothing moves
    assert brands(ties, "al", ["AL"]) == brands(ties, "al")
    assert brands(ties, "ac", ["ACID"]) == ["AC", "ACID", "ACME"]


def test_hinted_brands_keep_the_plain_order_among_themselves_and_the_order_of_the_names_means_nothing(ties):
    assert brands(ties, "al", ["ALPS", "ALPHA"]) == ["AL", "ALPHA", "ALPS", "ALPINE", "ALTEC"]
    assert brands(ties, "al", ["ALPHA", "ALPS"]) == brands(ties, "al", ["ALPS", "ALPHA"])
    assert models(ties, "tx1000", ["WEST", "NORTH"])[:2] == ["NORTH/TX1000", "WEST/TX1000"]
    assert models(ties, "tx1000", ["NORTH", "WEST"]) == models(ties, "tx1000", ["WEST", "NORTH"])
    assert models(ties, "tx1000", ["WEST", "NORTH", "west", "North ", "南方"]) == models(ties, "tx1000", ["NORTH", "WEST", "SOUTH"])


def test_a_brand_the_query_does_not_offer_is_never_added(ties):
    assert brands(ties, "al", ["ZEBRA"]) == brands(ties, "al")
    assert brands(ties, "alp", ["ZEBRA", "EAST", "ACME"]) == brands(ties, "alp")
    assert models(ties, "tx1000", ["ZEBRA"]) == models(ties, "tx1000")
    assert ties.suggest("al", 8, ["ZEBRA"]) == ties.suggest("al")
    assert models(ties, "zebra", ["ALPS"]) == models(ties, "zebra")


def test_a_name_that_is_no_brand_is_ignored_and_the_other_names_still_count(ties):
    for hint in (["Nobody"], ["nobody", "ALPS-NOT"], ["x" * 40], ["ALP"], ["ALPIN"], ["Alpsx"], ["海信"], ["ALPS NOT"], [""], [" "]):
        assert ties.suggest("al", 8, hint) == ties.suggest("al"), hint
        assert ties.suggest("tx1000", 8, hint) == ties.suggest("tx1000"), hint
    assert brands(ties, "al", ["Nobody", "ALPS"]) == ["AL", "ALPS", "ALPINE", "ALTEC", "ALPHA"]


def test_names_are_compared_by_the_search_key(ties):
    expected = brands(ties, "alp", ["ALPS"])
    for spelling in ("alps", "Alps", "aLpS", " ALPS ", "A.L.P.S", "al ps", "A-L P-S", "ａｌｐｓ"):
        assert brands(ties, "alp", [spelling]) == expected, spelling
    assert expected[0] == "ALPS"


def test_a_name_in_chinese_hints_its_brand_and_so_does_a_name_two_brands_share(ties):
    assert models(ties, "tx1000", ["南方"])[0] == "SOUTH/TX1000"
    assert models(ties, "tx1000", ["北方", "南方"])[:2] == ["NORTH/TX1000", "SOUTH/TX1000"]
    assert models(ties, "tx1000", ["方向"])[:2] == ["EAST/TX1000", "WEST/TX1000"]            # both brands of the shared name
    assert models(ties, "tx1000", ["方向", "南方"])[:3] == ["EAST/TX1000", "SOUTH/TX1000", "WEST/TX1000"]


def test_the_limit_cuts_after_the_hinted_order_and_a_hinted_brand_takes_the_place_of_an_equal_one_only(ties):
    assert brands(ties, "al", limit=2) == ["AL", "ALPINE"]
    assert brands(ties, "al", ["ALPS"], limit=2) == ["AL", "ALPS"]               # ALPS takes the place of ALPINE, an equal
    assert brands(ties, "al", ["ALPS"], limit=1) == ["AL"]                      # the first place is the exact key's
    assert brands(ties, "al", ["ALPS"], limit=3) == ["AL", "ALPS", "ALPINE"]
    assert brands(ties, "al", ["ALPS"], limit=5) == ["AL", "ALPS", "ALPINE", "ALTEC", "ALPHA"]
    for hint in ([], ["ALPS"], ["ALPHA", "ALPS"], ["ALTEC"], ["ZEBRA"]):
        whole = brands(ties, "alp", hint, limit=20)
        for limit in range(1, 6):
            assert brands(ties, "alp", hint, limit=limit) == whole[:limit], (hint, limit)


# ---- brands named by runs of the query, and their models

def test_two_brands_named_by_runs_of_the_same_length_go_to_the_hinted_one_first_and_so_do_their_models(ties):
    assert brands(ties, "acid acme") == ["ACID", "ACME"]
    assert brands(ties, "acid acme", ["ACME"]) == ["ACME", "ACID"]
    assert models(ties, "acid acme")[:2] == ["ACID/X101", "ACID/X100"]
    assert models(ties, "acid acme", ["ACME"]) == [
        "ACME/X102", "ACME/X100", "ACME/X103", "ACID/X101", "ACID/X100"]    # each brand's models in their order, the hinted block first


def test_the_limit_of_models_is_filled_from_the_hinted_brand_first(ties):
    assert models(ties, "acid acme", limit=3) == ["ACID/X101", "ACID/X100", "ACME/X102"]
    assert models(ties, "acid acme", ["ACME"], limit=3) == ["ACME/X102", "ACME/X100", "ACME/X103"]
    assert models(ties, "acid acme", ["ACME"], limit=1) == ["ACME/X102"]


def test_a_brand_named_by_a_shorter_run_is_not_lifted_above_one_named_by_a_longer_run(ties):
    assert brands(ties, "alpine ac") == ["ALPINE", "AC"]
    assert brands(ties, "alpine ac", ["AC"]) == ["ALPINE", "AC"]
    assert models(ties, "alpine ac", ["AC"]) == models(ties, "alpine ac")
    assert models(ties, "alpine ac", ["AC"])[0].startswith("ALPINE/")


def test_a_shared_name_offers_both_brands_and_the_hinted_ones_models_first(ties):
    assert brands(ties, "方向") == ["EAST", "WEST"]
    assert brands(ties, "方向", ["WEST"]) == ["WEST", "EAST"]
    assert models(ties, "方向", ["WEST"]) == ["WEST/TX1000", "EAST/TX1000"]
    assert models(ties, "方向", ["WEST"], limit=1) == ["WEST/TX1000"]
    assert models(ties, "方向", limit=1) == ["EAST/TX1000"]
    assert models(ties, "方向 tx", ["WEST"]) == ["WEST/TX1000", "EAST/TX1000"]
    assert models(ties, "北方 南方", ["南方"]) == ["SOUTH/TX1000", "SOUTH/TX10001", "NORTH/TX1000"]


# ---- brands the matcher only reads the query as

def test_a_brand_offered_only_because_the_query_reads_like_it_is_never_moved(ties):
    assert brands(ties, "korix") == ["KORAX", "KOREX"]
    assert brands(ties, "korix", ["KOREX"]) == ["KORAX", "KOREX"]
    assert brands(ties, "korix", ["KOREX", "KORAX"]) == ["KORAX", "KOREX"]
    assert brands(ties, "korix", ["KOREX"], limit=1) == ["KORAX"]                       # not let in by the limit
    assert brands(ties, "kor", ["KOREX"]) == ["KOREX", "KORAX"]                         # as brands that start with the query they move


def test_the_brand_the_query_names_stays_before_the_one_the_matcher_reads_it_as(ties, main):
    assert brands(main, "qr stv") == ["QR", "QRSTU"]
    assert brands(main, "qr stv", ["QRSTU"]) == ["QR", "QRSTU"]
    assert brands(main, "qr stv", ["QRSTU"], limit=1) == ["QR"]
    assert brands(main, "sony", ["SONNY"]) == ["SONY", "SONNY"]                         # and never above the exact key
    assert brands(main, "sonyy", ["SONY"]) == brands(main, "sonyy") == ["SONNY", "SONY"]


# ---- models when no brand is named

def test_a_hinted_brands_model_comes_first_among_models_that_match_equally_well(ties):
    assert models(ties, "tx1000", ["WEST"]) == [
        "WEST/TX1000", "EAST/TX1000", "NORTH/TX1000", "SOUTH/TX1000", "SOUTH/TX10001"]
    assert models(ties, "tx1000", ["SOUTH"])[:4] == ["SOUTH/TX1000", "EAST/TX1000", "NORTH/TX1000", "WEST/TX1000"]


def test_a_hinted_brands_worse_match_stays_below_every_better_one(ties):
    for hint in (["SOUTH"], ["SOUTH", "WEST"], ["SOUTH", "WEST", "EAST", "NORTH"]):
        assert models(ties, "tx1000", hint)[-1] == "SOUTH/TX10001", hint
    assert models(ties, "tx1000", ["SOUTH"], limit=4) == ["SOUTH/TX1000", "EAST/TX1000", "NORTH/TX1000", "WEST/TX1000"]
    assert models(ties, "tx1000", ["SOUTH"], limit=5)[4] == "SOUTH/TX10001"


def test_the_limit_trades_a_hinted_models_place_for_an_equal_ones_only(ties):
    assert models(ties, "tx1000", limit=1) == ["EAST/TX1000"]
    assert models(ties, "tx1000", ["SOUTH"], limit=1) == ["SOUTH/TX1000"]
    assert models(ties, "tx1000", ["WEST"], limit=3) == ["WEST/TX1000", "EAST/TX1000", "NORTH/TX1000"]
    for hint in ([], ["SOUTH"], ["WEST"], ["SOUTH", "EAST"]):
        assert "SOUTH/TX10001" not in models(ties, "tx1000", hint, limit=4), hint     # the worse one is not let in


def test_the_models_of_one_brand_are_never_reordered_by_a_hint(ties):
    for hint in (["ZEBRA"], ["ALPS"], ["ALPINE", "ZEBRA"]):
        assert models(ties, "zebra", hint) == models(ties, "zebra")
        assert models(ties, "alpine p", hint) == models(ties, "alpine p")
    assert [m.split("/")[1] for m in models(ties, "alpine", ["ALPINE"])] == ["P2", "P4", "P1", "P3", "P5"]


def test_a_hint_changes_nothing_but_the_order(ties, main):
    for index, texts in ((ties, ["al", "alp", "acid acme", "tx1000", "方向", "korix", "zebra", "a"]),
                         (main, ["s", "sony roku", "ci500twnsat1", "t", "西部数据", "q"])):
        for text in texts:
            plain = index.suggest(text, BIG)
            for hint in (["ALPS"], ["SAT", "TELESTAR"], ["WEST", "SOUTH"], ["EAST", "ACME", "ALTEC", "NORTH", "KOREX", "WESTERN DIGITAL"]):
                hinted = index.suggest(text, BIG, hint)
                assert sorted(map(repr, hinted.brands)) == sorted(map(repr, plain.brands)), (text, hint)
                assert sorted(map(repr, hinted.models)) == sorted(map(repr, plain.models)), (text, hint)


def test_a_hint_is_used_for_its_call_only_and_leaves_the_index_as_it_was(ties):
    plain = ties.suggest("al")
    before = {name: getattr(ties, name) for name in ("conn", "brands", "name_by_key", "_keys", "_kept")}
    hinted = ties.suggest("al", 8, ["ALPS"])
    assert hinted != plain and ties.suggest("al") == plain and ties.suggest("al", 8, ["ALPS"]) == hinted
    assert {name: getattr(ties, name) for name in before} == before
    assert not hasattr(ties, "_preferred") and not hasattr(ties, "prefer")


# --- the properties ---------------------------------------------------------------------------------------------


def synthetic(seed: int) -> tuple[list[dict], list[dict]]:
    """A catalog made of ties: brands that start alike, models that several brands have, aliases two brands share."""
    rng = random.Random(seed)
    brand_names = [
        "AL", "ALFA", "ALFAX", "ALFIN", "ALTO", "ALTUS", "A", "NORD", "NORDA", "NORDIC", "SUD", "SUDO", "TXCO", "TXC",
        "DELTA", "DELTAX", "KORAX", "KOREX", "KORIX", "ZED", "ZEDD", "PIONEER", "PIONEERX", "Q", "QQ", "LG", "LGE", "LG ELECTRONICS",
        "Ünï", "UNI", "SAMSUNG", "SAMSUNG ELECTRONICS", "SAM", "SAMSON",
    ]
    models_pool = [
        "TX100", "TX1000", "TX10001", "TX1002", "RC5", "RC55", "RC-5A", "KD49", "KD-490", "KD 49 X", "BG01", "BG02", "BG03",
        "UN50NU6900", "UN50NU6900F", "UN55NU6900F", "ALFA 3", "DELTA 1", "AL 77", "PIONEER 9", "X", "XY", "XYZ12", "XYZ123",
    ]
    entries: list[dict] = []
    for brand in brand_names:
        for model in rng.sample(models_pool, rng.randint(1, 9)):
            remotes = list(range(len(entries) * 3, len(entries) * 3 + rng.randint(1, 3)))
            entries.append({"brand": brand, "model": model, "remotes": remotes, **({"kind": 1} if rng.random() < 0.05 else {})})
    aliases = [
        {"brand": "ALFA", "alias": "阿尔法"}, {"brand": "ALFAX", "alias": "阿尔法"}, {"brand": "ALFIN", "alias": "阿尔法"},
        {"brand": "NORD", "alias": "北方"}, {"brand": "SUD", "alias": "南方"}, {"brand": "SUDO", "alias": "南方"},
        {"brand": "LG", "alias": "乐金"}, {"brand": "LGE", "alias": "乐金"}, {"brand": "LG ELECTRONICS", "alias": "乐金"},
        {"brand": "SAMSUNG", "alias": "三星"}, {"brand": "SAMSON", "alias": "三星电子"}, {"brand": "SAMSUNG", "alias": "參星"},
    ]
    return entries, aliases


def queries_of(entries: list[dict], aliases: list[dict], seed: int, count: int) -> list[str]:
    """Typed text: every start of some brands and models, a brand and the start of a model, aliases with models, and words."""
    rng = random.Random(seed)
    brand_names = sorted({e["brand"] for e in entries})
    model_names = sorted({e["model"] for e in entries})
    alias_names = sorted({a["alias"] for a in aliases})
    out: list[str] = []
    for brand in rng.sample(brand_names, min(len(brand_names), 14)):
        out += [brand[:n] for n in range(1, len(brand) + 1)]
    for model in rng.sample(model_names, min(len(model_names), 12)):
        out += [model[:n] for n in range(1, len(model) + 1)]
    for _ in range(count):
        kind = rng.randrange(6)
        brand, model, alias = rng.choice(brand_names), rng.choice(model_names), rng.choice(alias_names)
        if kind == 0:
            out.append(f"{brand} {model[: rng.randint(1, len(model))]}")
        elif kind == 1:
            out.append(f"{model[: rng.randint(1, len(model))]} {brand}")
        elif kind == 2:
            out.append(f"{alias}{model[: rng.randint(1, len(model))]}")
        elif kind == 3:
            out.append(" ".join(rng.choice(brand_names + alias_names) for _ in range(rng.randint(2, 4))))
        elif kind == 4:
            out.append(f"{brand[: rng.randint(1, len(brand))]}{rng.choice(['', 'x', 'z', '1'])}")
        else:
            out.append(rng.choice([model, model.lower(), model.replace(" ", ""), alias, brand.lower()]))
    return out


def hints_for(index, names: Sequence[str], query: str, seed: int, count: int = 3) -> list[tuple[str, ...]]:
    """Hints for a query: mostly brands that are in its answer (a hint for a brand that is not there moves nothing),
    with a few names that are anywhere in the catalog or nowhere."""
    rng = random.Random(f"{seed}/{query}")
    plain = index.suggest(query, BIG)
    present = sorted({b.name for b in plain.brands} | {m.brand for m in plain.models})
    anywhere = list(names) + ["Nobody", "ZZZ"]
    hints = []
    for _ in range(count):
        picks = rng.sample(present, min(len(present), rng.randint(1, 3)))
        picks += rng.sample(anywhere, rng.randint(0, 2))
        hints.append(tuple(picks) or (rng.choice(anywhere),))
    return hints


def all_names(entries: list[dict], aliases: list[dict]) -> list[str]:
    return sorted({e["brand"] for e in entries}) + sorted({a["alias"] for a in aliases})


class Oracle:
    """Which entries of a plain answer tie, worked out from the index's data and the ledger's run rule (S2) and
    never from the hint's code: the tier of each entry of an answer, as a value that is equal for entries that tie
    and sorts as the plain order does."""

    def __init__(self, index: MatchIndex) -> None:
        self.index = index
        self.keys_of: dict[int, list[str]] = defaultdict(list)
        for key, ids in index.name_by_key.items():
            for brand_id in ids:
                self.keys_of[brand_id].append(key)

    def named_by_runs(self, query: str) -> dict[int, int]:
        """S2: brand id to the length of the longest run of the first twelve tokens that is a key of the brand."""
        longest: dict[int, int] = {}
        for run in runs(tokens(query)[:MAX_TOKENS]):
            if len(run.key) >= BRAND_MIN_KEY:
                for brand_id in self.index.name_by_key.get(run.key, ()):
                    longest[brand_id] = max(longest.get(brand_id, 0), len(run.key))
        return longest

    def brand_tiers(self, query: str, answer: Suggestions) -> list[tuple]:
        key = search_norm(query)
        named = self.named_by_runs(query)
        out = []
        for position, brand in enumerate(answer.brands):
            keys = self.keys_of[brand.brand_id]
            if key in keys:
                out.append((0, 0))
            elif any(k.startswith(key) for k in keys):
                out.append((1, 0))
            elif brand.brand_id in named:
                out.append((2, -named[brand.brand_id]))
            else:
                out.append((3, position))               # read as a misspelling: a tier of its own
        return out

    def model_tiers(self, query: str, answer: Suggestions) -> list[tuple]:
        named = self.named_by_runs(query)
        if named:
            return [(-named[m.brand_id],) for m in answer.models]
        ranked, _ = self.index._rank(None, None, [query])
        score = {model_id: tuple(row[:3]) for model_id, row in ranked}
        return [score[m.model_id] for m in answer.models]


def promoted(items: list, tiers: list, preferred: Callable[[object], bool]) -> list:
    """``items`` with each run of equal consecutive tiers re-ordered, the preferred first: written here, apart from the code."""
    out: list = []
    for _, run in itertools.groupby(zip(items, tiers), key=lambda pair: pair[1]):
        group = [item for item, _ in run]
        out += [i for i in group if preferred(i)] + [i for i in group if not preferred(i)]
    return out


def check_hint(index: MatchIndex, oracle: Oracle, query: str, hint: Sequence[str], big: int = BIG) -> None:
    """The hinted answer is the plain one with each run of tied entries re-ordered, preferred first, and the limit
    trades places only inside a run. ``big`` is a limit no list reaches: the whole list (a brand's models are all of them)."""
    ids = index.brand_ids(hint)
    plain = index.suggest(query, big)
    hinted = index.suggest(query, big, hint)
    where = f"{query!r} hint {tuple(hint)}"

    brand_tiers = oracle.brand_tiers(query, plain)
    assert brand_tiers == sorted(brand_tiers), f"the oracle's tiers are not the plain order for {where}"
    assert [b.brand_id for b in hinted.brands] == [
        b.brand_id for b in promoted(list(plain.brands), brand_tiers, lambda b: b.brand_id in ids)], where
    model_tiers = oracle.model_tiers(query, plain)
    assert [m.model_id for m in hinted.models] == [
        m.model_id for m in promoted(list(plain.models), model_tiers, lambda m: m.brand_id in ids)], where

    brand_tier_of = {b.brand_id: tier for b, tier in zip(plain.brands, brand_tiers)}
    model_tier_of = {m.model_id: tier for m, tier in zip(plain.models, model_tiers)}
    brand_of = {m.model_id: m.brand_id for m in plain.models}
    for limit in (1, 2, 3, 5, 8, 13, 20):
        cut, base = index.suggest(query, limit, hint), index.suggest(query, limit)
        assert cut.brands == hinted.brands[:limit], (where, limit)             # a smaller limit is the start of a larger one
        assert cut.models == hinted.models[:limit], (where, limit)
        trades_places_within_one_tier_only(
            [b.brand_id for b in base.brands], [b.brand_id for b in cut.brands], brand_tier_of, lambda i: i in ids)
        trades_places_within_one_tier_only(
            [m.model_id for m in base.models], [m.model_id for m in cut.models], model_tier_of, lambda i: brand_of[i] in ids)


def trades_places_within_one_tier_only(base_ids, cut_ids, tier_of, hinted) -> None:
    """The limit kept ``cut_ids`` where the plain answer would have kept ``base_ids``: what came in is hinted, and what
    went out was of the same tiers as what came in."""
    came_in, went_out = set(cut_ids) - set(base_ids), set(base_ids) - set(cut_ids)
    assert all(hinted(i) for i in came_in)
    assert sorted(tier_of[i] for i in came_in) == sorted(tier_of[i] for i in went_out)


@pytest.mark.parametrize("seed", [1, 2])
def test_the_hinted_answer_is_the_plain_one_with_each_run_of_tied_entries_reordered_and_nothing_else(seed):
    entries, aliases = synthetic(seed)
    index = MatchIndex.from_entries(entries, aliases)
    oracle = Oracle(index)
    queries = queries_of(entries, aliases, seed, 300)
    assert len(queries) > 400
    for query in queries:
        for hint in hints_for(index, all_names(entries, aliases), query, seed):
            check_hint(index, oracle, query, hint)


def test_the_same_holds_on_the_vector_catalog_and_on_the_catalog_of_ties(main, ties):
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    queries = sorted({case["query"] for case in vectors["suggest"] if case["query"]})
    assert len(queries) > 150
    names = all_names(suggest_vectors.CATALOG, suggest_vectors.ALIASES)
    oracle = Oracle(main)
    for query in queries:
        for hint in hints_for(main, names, query, 0):
            check_hint(main, oracle, query, hint)
    tie_queries = sorted({case["query"] for case in vectors["prefer"]["cases"]} | {"tx10", "ko", "w", "n", "e"})
    names = all_names(suggest_vectors.TIES, suggest_vectors.TIE_ALIASES)
    oracle = Oracle(ties)
    for query in tie_queries:
        for hint in hints_for(ties, names, query, 0, 6):
            check_hint(ties, oracle, query, hint)


def test_the_hint_does_move_things_in_those_catalogs_so_that_the_properties_are_not_empty():
    """If no query of the generated sets were reordered, the properties above would hold of anything."""
    moved = {"brands": 0, "models": 0, "pushed_out": 0}
    entries, aliases = synthetic(1)
    index = MatchIndex.from_entries(entries, aliases)
    for query in queries_of(entries, aliases, 1, 300):
        for hint in hints_for(index, all_names(entries, aliases), query, 1):
            plain, hinted = index.suggest(query, BIG), index.suggest(query, BIG, hint)
            moved["brands"] += plain.brands != hinted.brands
            moved["models"] += plain.models != hinted.models
            cut_plain, cut_hinted = index.suggest(query, 3), index.suggest(query, 3, hint)
            moved["pushed_out"] += cut_plain.models != cut_hinted.models or cut_plain.brands != cut_hinted.brands
    assert moved["brands"] > 60 and moved["models"] > 100 and moved["pushed_out"] > 120, moved


def test_the_oracle_can_tell_a_wrong_answer_from_a_right_one(ties):
    """A hint that lifts a worse match, or ignores the exact key, or reorders a brand's own models, fails ``check_hint``."""
    oracle = Oracle(ties)
    plain = ties.suggest("tx1000", BIG)
    tiers = oracle.model_tiers("tx1000", plain)
    assert len(set(tiers)) == 2 and tiers[-1] < tiers[0]                  # the four equal matches, and the worse one after them
    wrong = promoted(list(plain.models), [0] * len(tiers), lambda m: m.brand == "SOUTH")        # one run: the worse one lifted
    assert [m.model for m in wrong][:2] == ["TX1000", "TX10001"]
    right = promoted(list(plain.models), tiers, lambda m: m.brand == "SOUTH")
    assert [m.model for m in right][:2] == ["TX1000", "TX1000"] and right[-1].model == "TX10001"
    assert oracle.brand_tiers("al", ties.suggest("al", BIG))[0] == (0, 0)


# --- no hint is today's answer --------------------------------------------------------------------------------


class Before(MatchIndex):
    """``suggest`` as it was before the hint (D100): a copy of its code, to hold the answer with no hint to."""

    def suggest(self, query, limit=8):                                   # type: ignore[override]
        key = search_norm(query)
        if not key or limit <= 0:
            return Suggestions((), ())
        words = tokens(query)
        named = self._named_before(words)
        found: list = []
        if named:
            rest = self._rest_of_query(words, named)
            for brand_id in named:
                if len(found) >= limit:
                    break
                found.extend(self._models_of(brand_id, rest, limit - len(found)))
        else:
            ranked, _ = self._rank(None, None, [query])
            found = [self._suggestion(model_id) for model_id, _ in ranked[:limit]]
        return Suggestions(tuple(self._brands_before(key, named, limit)), tuple(found))

    def _named_before(self, words):
        longest: dict[int, int] = {}
        for run in runs(words[:MAX_TOKENS]):
            if len(run.key) >= BRAND_MIN_KEY:
                for brand_id in self.name_by_key.get(run.key, ()):
                    longest[brand_id] = max(longest.get(brand_id, 0), len(run.key))
        return dict(sorted(longest.items(), key=lambda kv: (-kv[1], kv[0])))

    def _brands_before(self, key, named, limit):
        from bisect import bisect_left
        starting: dict[int, bool] = {}
        at = bisect_left(self._keys, (key,))
        while at < len(self._keys) and self._keys[at][0].startswith(key):
            other, brand_id = self._keys[at]
            starting[brand_id] = starting.get(brand_id, False) or other == key
            at += 1
        ordered = sorted(starting, key=lambda b: (not starting[b], -self.brands[b][3], self.brands[b][0], b))
        out = dict.fromkeys(ordered)
        for brand_id in named:
            out.setdefault(brand_id)
        if len(out) < limit:
            for brand_id, _ in self._fuzzy_brands(key):
                out.setdefault(brand_id)
        return [BrandSuggestion(b, self.brands[b][0], self.brands[b][3]) for b in list(out)[:limit]]


def test_with_no_hint_and_with_a_hint_naming_no_brand_the_answer_is_what_suggest_gave_before_for_every_random_query():
    checked = 0
    for seed in (1, 2, 3, 4):
        entries, aliases = synthetic(seed)
        before = Before.from_entries(entries, aliases)
        index = MatchIndex.from_entries(entries, aliases)
        rng = random.Random(seed)
        for query in queries_of(entries, aliases, seed, 200):
            junk = rng.sample(["Nobody", "ZZZ", "海信", "", " ", "!!!", "ALF", "Alfaa", "SAMSUN"], rng.randint(1, 3))
            for limit in (1, 2, 3, 8, 20, BIG):
                want = before.suggest(query, limit)
                assert index.suggest(query, limit) == want, (query, limit)
                assert index.suggest(query, limit, ()) == index.suggest(query, limit, junk) == want, (query, limit, junk)
                assert tuple(index.suggest_brands(query, limit, junk)) == want.brands, (query, limit, junk)
                checked += 1
    assert checked > 5000


def test_with_every_brand_hinted_nothing_moves_but_all_of_the_hints_code_runs_and_the_answer_is_the_old_one():
    for seed in (1, 2):
        entries, aliases = synthetic(seed)
        before = Before.from_entries(entries, aliases)
        index = MatchIndex.from_entries(entries, aliases)
        everyone = [name for name, *_ in index.brands.values()]
        assert index.brand_ids(everyone) == frozenset(index.brands)
        for query in queries_of(entries, aliases, seed, 150):
            for limit in (1, 3, 8, 20):
                assert index.suggest(query, limit, everyone) == before.suggest(query, limit), (query, limit)


def test_the_existing_vectors_are_what_suggest_gave_before_with_no_hint_and_with_an_unknown_one(main):
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    for case in vectors["suggest"]:
        for hint in ((), ["Nobody"], ["海信海信"], ["ALF", ""]):
            got = main.suggest(case["query"], case["limit"], hint)
            assert [b["brandId"] for b in case["brands"]] == [b.brand_id for b in got.brands], case["note"]
            assert [m["modelId"] for m in case["models"]] == [m.model_id for m in got.models], case["note"]
            assert [m["remoteIds"] for m in case["models"]] == [list(m.remote_ids) for m in got.models], case["note"]


# --- the vectors of the hint ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vectors():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def _answered(case: dict) -> dict:
    return {"brands": [{"brandId": b.brand_id, "name": b.name, "modelCount": b.model_count} for b in case["got"].brands],
            "models": [{"modelId": m.model_id, "brandId": m.brand_id, "brand": m.brand, "model": m.model,
                        "remoteIds": list(m.remote_ids), "partNumber": m.part_number} for m in case["got"].models]}


def test_the_vectors_of_the_hint_are_the_catalogs_answered_with_the_hint(vectors, main, ties):
    section = vectors["prefer"]
    assert section["ties"] == {"catalog": suggest_vectors.TIES, "aliases": suggest_vectors.TIE_ALIASES}
    indexes = {"main": main, "ties": ties}
    assert len(section["cases"]) >= 120 and {c["catalog"] for c in section["cases"]} == {"main", "ties"}
    for case in section["cases"]:
        got = indexes[case["catalog"]].suggest(case["query"], case["limit"], case["prefer"])
        answer = _answered({"got": got})
        assert case["brands"] == answer["brands"] and case["models"] == answer["models"], case["note"]
        assert len(case["brands"]) <= max(case["limit"], 0) and len(case["models"]) <= max(case["limit"], 0)
        assert isinstance(case["prefer"], list) and all(isinstance(n, str) for n in case["prefer"])


def test_the_vectors_of_the_hint_agree_with_the_oracle_and_the_unhinted_answers_in_the_file(vectors, main, ties):
    indexes = {"main": main, "ties": ties}
    oracles = {name: Oracle(index) for name, index in indexes.items()}
    plain_in_file = {(c["query"], c["limit"]): c for c in vectors["suggest"]}
    for case in vectors["prefer"]["cases"]:
        index, oracle = indexes[case["catalog"]], oracles[case["catalog"]]
        check_hint(index, oracle, case["query"], case["prefer"])
        if case["catalog"] == "main" and not case["prefer"] and (case["query"], case["limit"]) in plain_in_file:
            twin = plain_in_file[(case["query"], case["limit"])]
            assert case["brands"] == twin["brands"] and case["models"] == twin["models"], case["note"]


def test_the_vectors_of_the_hint_cover_each_row_of_the_rule_and_show_that_it_moves_things(vectors, main, ties):
    cases = vectors["prefer"]["cases"]
    notes = " | ".join(c["note"] for c in cases)
    for fragment in ("exact key", "start with the query", "same length", "never moved", "equal score", "limit", "Chinese name",
                     "share", "stands for both", "no brand", "ignored", "no similarity", "order of the names", "nothing moves",
                     "never added", "worse match", "shorter run", "typing:", "its models", "search key", "changes nothing"):
        assert fragment in notes, fragment
    indexes = {"main": main, "ties": ties}
    moved = [c for c in cases if c["prefer"] and (c["brands"], c["models"]) != _both(indexes[c["catalog"]].suggest(c["query"], c["limit"]))]
    assert len(moved) >= 60
    assert any(c["brands"] != _both(indexes[c["catalog"]].suggest(c["query"], c["limit"]))[0] for c in moved)      # brands moved
    assert any(c["models"] != _both(indexes[c["catalog"]].suggest(c["query"], c["limit"]))[1] for c in moved)      # models moved
    assert any(c["prefer"] and (c["brands"], c["models"]) == _both(indexes[c["catalog"]].suggest(c["query"], c["limit"])) for c in cases)   # and some do not
    assert {c["limit"] for c in cases} >= {1, 2, 3, 4, 5, 7, 8}
    assert {len(c["prefer"]) for c in cases} >= {0, 1, 2, 4}


def _both(found: Suggestions) -> tuple[list, list]:
    answer = _answered({"got": found})
    return answer["brands"], answer["models"]


def test_the_vectors_leave_what_they_held_before_the_hint_as_it_was(vectors):
    assert set(vectors) == {"about", "aliases", "catalog", "constants", "format", "prefer", "suggest", "suggestModels"}
    assert vectors["catalog"] == suggest_vectors.CATALOG and vectors["aliases"] == suggest_vectors.ALIASES
    assert (len(vectors["catalog"]), len(vectors["aliases"]), len(vectors["suggest"]), len(vectors["suggestModels"])) == (79, 14, 238, 26)


def test_design_quotes_the_size_of_the_vectors_of_the_hint(vectors):
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    section = design[design.index("### D106"):design.index("### D107")]
    cases, tied = vectors["prefer"]["cases"], vectors["prefer"]["ties"]
    for fact in (f"{len(cases)} cases", f"{len(tied['catalog'])} entries", f"{len(tied['aliases'])} aliases"):
        assert fact in section, fact


# --- warm -----------------------------------------------------------------------------------------------------------


def big_entries(brands: int = 20) -> list[dict]:
    """Brand N has N models: the biggest are the last."""
    entries: list[dict] = []
    for n in range(1, brands + 1):
        for m in range(n):
            entries.append({"brand": f"BRAND{n:02d}", "model": f"M{m:03d}X", "remotes": [len(entries) + 1]})
    return entries


class Reads:
    """Counts the times the index reads a whole brand's list from the file (the statement ``_read_brand_models`` runs)."""

    def __init__(self, index: MatchIndex) -> None:
        self.count = 0
        index.conn.set_trace_callback(self._see)

    def _see(self, statement: str) -> None:
        self.count += "SELECT model_id FROM controls WHERE model_id BETWEEN" in statement


def asks_inside(index: MatchIndex, brands: range) -> None:
    for n in brands:
        index.suggest(f"brand{n:02d} m00", 8)


def test_warm_reads_the_whole_model_lists_of_the_sixteen_biggest_brands_once_and_says_how_many():
    index = MatchIndex.from_entries(big_entries())
    reads = Reads(index)
    assert matching.WARM_BRANDS == 16 == BRAND_LISTINGS
    assert index.warm() == 16 and reads.count == 16
    assert sorted(index._kept) == sorted(i for i, b in index.brands.items() if b[3] >= 5)     # BRAND05 to BRAND20
    assert index._brand_models.cache_info().currsize == 0              # it took no place of the cache's
    assert all(listing._keys is not None for listing in index._kept.values())       # the search keys are made too


def test_the_brands_kept_are_the_ones_with_the_most_models_ties_to_the_lower_id_and_all_of_a_small_catalog():
    index = MatchIndex.from_entries(big_entries(20))
    assert index.warm(3) == 3 and sorted(index.brands[i][0] for i in index._kept) == ["BRAND18", "BRAND19", "BRAND20"]
    even = MatchIndex.from_entries([{"brand": b, "model": f"M{m}", "remotes": [1]} for b in "ABCDEF" for m in range(4)])
    assert even.warm(2) == 2 and sorted(even.brands[i][0] for i in even._kept) == ["A", "B"]     # equal counts: the lower ids
    small = MatchIndex.from_entries(big_entries(3))
    assert small.warm() == 3 and len(small._kept) == 3
    assert MatchIndex.from_entries(big_entries(3)).warm(100) == 3


def test_a_brand_with_no_model_is_not_kept_and_warm_of_nothing_keeps_nothing():
    index = MatchIndex.from_entries(big_entries(4))
    index.conn.execute("INSERT INTO brands VALUES (99, 'EMPTY', 'empty', 0, 0)")
    index.brands[99] = ("EMPTY", "empty", 0, 0)
    assert index.warm() == 4 and 99 not in index._kept
    assert index.warm(0) == 0 and index._kept == {}
    assert index.warm(-3) == 0 and index._kept == {}


def test_traffic_over_other_brands_cannot_evict_what_warm_kept_and_asking_inside_a_kept_brand_reads_nothing():
    index = MatchIndex.from_entries(big_entries(40))
    index.warm()
    reads = Reads(index)
    for _ in range(3):     # every brand small and big: the cache of sixteen lists is emptied many times over
        asks_inside(index, range(1, 25 + 1))
    assert reads.count >= 9 * 3                                        # BRAND01 to BRAND24 are read again and again: they are not kept
    reads.count = 0
    asks_inside(index, range(25, 41))                                  # the sixteen biggest, which warm read
    assert reads.count == 0
    index.suggest("brand 4", 8, ["brand39"])
    index.suggest("brand39 brand40", 8, ["brand40"])
    assert index.match("brand40", None, ["brand40"]) and reads.count == 0       # a brand-only answer lists from the kept list too


def test_without_warm_a_brand_is_read_when_first_needed_and_then_cached_and_a_brand_outside_the_kept_ones_is_too():
    index = MatchIndex.from_entries(big_entries(40))
    reads = Reads(index)
    index.suggest("brand39 m00", 8)
    assert reads.count == 1
    index.suggest("brand39 m001", 8)
    assert reads.count == 1                                            # cached
    index.warm()
    reads.count = 0
    index.suggest("brand03 m00", 8)
    assert reads.count == 1                                            # not among the kept: read when first needed
    index.suggest("brand03 m001", 8)
    assert reads.count == 1


def test_warm_changes_no_answer_with_or_without_a_hint_and_calling_it_again_replaces_what_was_kept():
    entries = big_entries()
    warm, cold = MatchIndex.from_entries(entries), MatchIndex.from_entries(entries)
    assert warm.warm() == 16
    for query in ("b", "brand20", "brand20 m", "brand20 m019", "brand07 m00", "brand01", "m003x", "brand1", "zzz", "brand19 brand20"):
        for limit in (1, 8, 20):
            for prefer in ((), ("brand20",), ("brand05", "brand01")):
                assert warm.suggest(query, limit, prefer) == cold.suggest(query, limit, prefer), (query, limit, prefer)
    assert warm.match("brand20", None, ["brand20"]) == cold.match("brand20", None, ["brand20"])
    first = dict(warm._kept)
    assert warm.warm(2) == 2 and sorted(warm._kept) == sorted(first)[-2:]
    assert warm.suggest("brand20 m0", 8) == cold.suggest("brand20 m0", 8)
    assert warm.warm(0) == 0 and warm.suggest("brand20 m0", 8) == cold.suggest("brand20 m0", 8)


def test_warm_over_the_vector_catalog_changes_none_of_the_answers_of_the_vectors(vectors):
    index = MatchIndex.from_entries(vectors["catalog"], vectors["aliases"])
    assert 0 < index.warm() <= 16
    for case in vectors["suggest"]:
        got = index.suggest(case["query"], case["limit"])
        assert [m["modelId"] for m in case["models"]] == [m.model_id for m in got.models], case["note"]
        assert [b["brandId"] for b in case["brands"]] == [b.brand_id for b in got.brands], case["note"]
    for case in vectors["suggestModels"]:
        got = index.suggest_models(case["brandId"], case["query"], case["limit"])
        assert [m["modelId"] for m in case["models"]] == [m.model_id for m in got], case["note"]


def test_warm_on_an_index_over_a_bundle_file_reads_what_the_file_holds(tmp_path):
    source = MatchIndex.from_entries(big_entries(30))
    source.conn.commit()
    target = sqlite3.connect(tmp_path / "c.sqlite")
    source.conn.backup(target)
    target.close()
    index = MatchIndex.open(tmp_path / "c.sqlite")
    reads = Reads(index)
    assert index.warm(5) == 5 and reads.count == 5
    asks_inside(index, range(26, 31))
    assert reads.count == 5


# --- the real bundle --------------------------------------------------------------------------------------------------


def test_the_hint_and_warm_over_the_real_selected_bundle(real_selected):
    _, directory = real_selected
    from remote_ledger.bundle import build

    real = MatchIndex.open(directory / build.BUNDLE_FILE)
    plain = real.suggest("s", BIG)
    hinted = real.suggest("s", BIG, ["SONY"])
    assert [b.name for b in hinted.brands][0] == "SONY" and sorted(b.brand_id for b in hinted.brands) == sorted(b.brand_id for b in plain.brands)
    assert real.suggest("s", 8, ["索尼"]) == real.suggest("s", 8, ["SONY"]) == real.suggest("s", 8, ["新力", "Sony"])
    assert real.suggest("sony", 8, ["Nobody"]) == real.suggest("sony", 8)
    oracle = Oracle(real)
    for query in ("s", "sa", "sony", "sony samsung", "samsung un50", "un50nu6900f", "lg 42", "三星", "三星 索尼", "kd49x80", "philips"):
        for hint in (["SONY"], ["SAMSUNG", "LG"], ["PHILIPS", "索尼"]):
            check_hint(real, oracle, query, hint, big=10 ** 6)       # Samsung alone has 21,512 models
    before = [real.suggest(q, 8) for q in ("sony kd", "samsung un50", "lg 42", "s")]
    kept = real.warm(4)
    assert kept == 4 and [real.suggest(q, 8) for q in ("sony kd", "samsung un50", "lg 42", "s")] == before
