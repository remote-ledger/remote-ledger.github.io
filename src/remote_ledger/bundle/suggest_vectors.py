"""Cross-language vectors of ``suggest`` (D100): ``tests/vectors/suggest_vectors.json``.

``suggest`` was written first in Kotlin, for a phone, and ported to ``matching.py`` when the
catalog moved to a service. The port was held to **vectors made from the Kotlin**: the same
queries answered by both over the real selected and full bundles, tens of thousands of them,
and every brand, model and order was equal (D100 says how many). This file is what stays: the
ledger's own record of the behaviour, written by ``matching.py`` once it gave what the Kotlin
gave, so a later port (or a later change here) is held to it without the Kotlin or a bundle.

It is **self-contained and independent of the data**, like the matching vectors (D97): a small
catalog written below (the catalog of those vectors, a few brands more, and the brand aliases of
D101), and for each query the brands and the models offered, each model with the remotes that
control it. Ids are the small catalog's own numbering: brands from 1 in the order of their search
keys, models from 1 in the order of their search keys inside a brand (``MatchIndex.from_entries``),
which is the numbering a tie that goes to the lower id depends on.

The cases are chosen so that each rule of the module's docstring (S1 to S5) and each tie-break is
the only thing that could give the answer: the notes say which. Typing a few queries one
character at a time (``typing``) shows the order a person sees change as they go.

The hint (S6, D106) has its own section, ``prefer``: the same questions with brand names that go first
where the rules do not tell entries apart, asked of this catalog and of a second small one made of ties
(``TIES``: brands that start alike with as many models as each other, one model under four brands, an
alias that two brands share), because the first has few ties for a hint to break. A case there says which
catalog it asks (``main`` is the one above, ``ties`` the other, which the section holds), and the section
leaves everything above it as it was: a port that does not implement the hint ignores it.
"""

from __future__ import annotations

from typing import Any

from .. import matching
from . import matching_vectors
from .textnorm import search_norm

FORMAT = 1

#: What the small catalog of the matching vectors does not have: a model that is the start of
#: others with fewer remotes, models that tie in remotes and differ in case and script, a brand
#: with more models than the default limit, a brand a character from another, and two brands with
#: Chinese names for the searches of D101.
EXTRA: list[dict[str, Any]] = [
    # the exact key leads even with fewer remotes; then most remotes first (S4)
    {"brand": "TVCO", "model": "TV 1", "remotes": [1701]},
    {"brand": "TVCO", "model": "TV 10", "remotes": [1702, 1703, 1704]},
    {"brand": "TVCO", "model": "TV 100", "remotes": [1705, 1706]},
    {"brand": "TVCO", "model": "TV 1000", "remotes": [1707, 1708]},
    # one remote each: by name, in code point order, so a capital comes before a small letter
    {"brand": "TVCO", "model": "alpha 2", "remotes": [1711]},
    {"brand": "TVCO", "model": "ALPHA 3", "remotes": [1712]},
    {"brand": "TVCO", "model": "Alpha 1", "remotes": [1710]},
    # names in code point order, not UTF-16 order: U+FF46 is before U+20000 though its UTF-16 unit is after
    {"brand": "CPCO", "model": "\U00020000 1", "remotes": [1801]},
    {"brand": "CPCO", "model": "ｆ 1", "remotes": [1802]},
    # a brand with more models than the default limit
    *({"brand": "BIGCO", "model": f"BG-{n:02d}", "remotes": list(range(1900 + n, 1900 + n + 1 + (n % 3 == 0)))}
      for n in range(1, 13)),
    # brands a character from SONY (the matcher's similarity reads one as the other) and with two models
    {"brand": "SONNY", "model": "SONNY 1", "remotes": [2003]},
    {"brand": "SANYO", "model": "AV 100", "remotes": [2001]},
    {"brand": "SANYO", "model": "AV 200", "remotes": [2002]},
    # a brand of one character (a run needs two to name one), a brand that starts with it, and a
    # brand a character from the start of a longer name (the matcher's reading, S1)
    {"brand": "Q", "model": "Q 100", "remotes": [2301]},
    {"brand": "QR", "model": "QR 200", "remotes": [2302]},
    {"brand": "QRSTU", "model": "QRSTU 300", "remotes": [2303]},
    # a model whose key is twelve letters minus one, for the words that come after the twelfth (S3)
    {"brand": "ZED", "model": "A B C D E F G H I J K", "remotes": [2304]},
    # two brands with Chinese names (D101)
    {"brand": "HISENSE", "model": "55E7", "remotes": [2101]},
    {"brand": "HISENSE", "model": "55E7HQ", "remotes": [2102, 2103]},
    {"brand": "HISENSE", "model": "65U8", "remotes": [2104]},
    {"brand": "SKYWORTH", "model": "55G20", "remotes": [2201, 2202]},
    {"brand": "SKYWORTH", "model": "65G20", "remotes": [2203]},
    {"brand": "SKYWORTH", "model": "43E2A", "remotes": [2204]},
]

CATALOG: list[dict[str, Any]] = matching_vectors.CATALOG + EXTRA

ALIASES: list[dict[str, str]] = matching_vectors.ALIASES + [
    {"brand": "HISENSE", "alias": "海信"},
    {"brand": "SKYWORTH", "alias": "创维"}, {"brand": "SKYWORTH", "alias": "創維"},
]


def _tied(brand: str, models: list[str], start: int) -> list[dict[str, Any]]:
    """The entries of one brand of ``TIES``: every second model has two remotes (so that "most remotes
    first" has something to order), and the ids of the remotes are numbered from ``start``."""
    return [{"brand": brand, "model": model, "remotes": [start + 10 * i + j for j in range(1 + i % 2)]}
            for i, model in enumerate(models)]


#: A catalog made of ties, for the hint (S6): ``AL`` is a key that others start; ALPINE and ALTEC have
#: five models each, so only the name tells them apart; ALPHA has two, ALPS one; ACID and ACME are two
#: brands a query can name by runs of the same length; KORAX and KOREX are each a character from
#: ``korix``; four brands have the model ``TX1000`` and one of them also ``TX10001``, a worse match of it;
#: ZEBRA has nine models, none a tie.
TIES: list[dict[str, Any]] = [
    *_tied("AL", ["A1"], 1000), *_tied("AC", ["Z1"], 1100),
    *_tied("ALPINE", ["P1", "P2", "P3", "P4", "P5"], 1200), *_tied("ALTEC", ["T1", "T2", "T3", "T4", "T5"], 1300),
    *_tied("ALPHA", ["H1", "H2"], 1400), *_tied("ALPS", ["S1"], 1500),
    *_tied("ACID", ["X100", "X101"], 1600), *_tied("ACME", ["X100", "X102", "X103"], 1700),
    *_tied("KORAX", ["K1"], 1800), *_tied("KOREX", ["K2"], 1900),
    *_tied("EAST", ["TX1000"], 2000), *_tied("NORTH", ["TX1000"], 2100),
    *_tied("SOUTH", ["TX1000"], 2200), *_tied("WEST", ["TX1000"], 2300),
    *_tied("SOUTH", ["TX10001"], 2400),
    *_tied("ZEBRA", [f"ZZ{n}" for n in range(9)], 2500),
]

#: Chinese names for ``TIES``: one each for NORTH and SOUTH, and one that EAST and WEST share.
TIE_ALIASES: list[dict[str, str]] = [
    {"brand": "NORTH", "alias": "北方"}, {"brand": "SOUTH", "alias": "南方"},
    {"brand": "EAST", "alias": "方向"}, {"brand": "WEST", "alias": "方向"},
]


def _brands(found: Any) -> list[dict[str, Any]]:
    return [{"brandId": b.brand_id, "name": b.name, "modelCount": b.model_count} for b in found]


def _models(found: Any) -> list[dict[str, Any]]:
    return [{"modelId": m.model_id, "brandId": m.brand_id, "brand": m.brand, "model": m.model,
             "remoteIds": list(m.remote_ids), "partNumber": m.part_number} for m in found]


def build() -> dict[str, Any]:
    """The vectors, from ``matching.py``."""
    index = matching.MatchIndex.from_entries(CATALOG, ALIASES)
    brand_id = {norm: i for i, (_, norm, _, _) in index.brands.items()}
    suggestions: list[dict[str, Any]] = []
    in_brand: list[dict[str, Any]] = []

    def ask(query: str, note: str, limit: int | None = None) -> None:
        answer = index.suggest(query) if limit is None else index.suggest(query, limit)
        suggestions.append({
            "note": note, "query": query, "limit": matching.SUGGEST_LIMIT if limit is None else limit,
            "brands": _brands(answer.brands), "models": _models(answer.models)})

    def typing(text: str, note: str, limit: int | None = None) -> None:
        for n in range(1, len(text) + 1):
            ask(text[:n], f"typing: {note}, {n} of {len(text)} characters", limit)

    def inside(brand: str, query: str, note: str, limit: int | None = None) -> None:
        owner = brand_id[search_norm(brand)]
        answer = index.suggest_models(owner, query) if limit is None else index.suggest_models(owner, query, limit)
        in_brand.append({
            "note": note, "brandId": owner, "brand": brand, "query": query,
            "limit": matching.SUGGEST_LIMIT if limit is None else limit, "models": _models(answer)})

    # -- S0: what asks for nothing --------------------------------------------------------------
    ask("", "the empty query offers nothing")
    ask("  -- ?? ", "a query with no letter or digit has no key and offers nothing")
    ask("sony", "a limit of 0 offers nothing", 0)
    ask("sony", "a negative limit offers nothing", -3)
    ask("s", "a negative limit offers no brand either, though a list that long is cut from the end", -1)
    ask("sony samsung", "nor any model of two brands", -2)

    # -- S1: the brands -------------------------------------------------------------------------
    ask("s", "one character: the brands that start with it, most models first: SAMSUNG and SONY tie on 6 and go by name, then SKYWORTH 3, SANYO 2, SAT 1 and SONNY 1 by name")
    ask("sa", "two characters: SAMSUNG 6 models, SANYO 2, SAT 1")
    ask("sams", "a prefix of one brand")
    ask("samsun", "one character short of the brand: the brand it starts")
    ask("sat", "the brand whose key is the query, however few models it has: SAT, and its one model")
    ask("lg", "a brand of two letters, typed")
    ask("harman-kardon", "separators do not matter")
    ask("Harman K", "a brand typed with a space and a capital")
    ask("t", "TVCO has 7 models, Topping 3, TELESTAR 1")
    ask("to", "Topping alone")
    ask("Ünï", "an accented brand typed as it is written")
    ask("uni", "the same brand without the accents")
    ask("u", "the first letter of the brand: it is offered at one character")
    ask("sony roku", "two brands named by a run of the same length: the lower id first (ROKU before SONY)")
    ask("sony samsung", "two brands named by runs: the longer run first (SAMSUNG, 7, before SONY, 4)")
    ask("kd 49 sony", "a brand named by a word of the query that goes on")
    ask("lg sony philips x", "three brands named by a run each: the longest run first (PHILIPS 7, SONY 4, LG 2)")
    ask("sony sony", "the same brand twice is offered once")
    ask("Samsung Electronics", "a brand and another word: the brand is named by its word")
    ask("phillips", "no key starts with it and no word is a brand: only the matcher's similarity (875) reads it as PHILIPS")
    ask("samsng", "a typo of a brand: SAMSUNG by the matcher's similarity")
    ask("sonyy", "a typo of a short brand: SONNY and SONY are each one edit in five characters away, exactly the threshold (800), best first by id")
    ask("sonn", "four characters must be exact: one edit is 750, under the threshold, and no brand starts with 'sonn'")
    ask("sony", "the exact key first, then the brand the matcher reads it as (SONNY: one insertion in five characters, 800)")
    ask("sonny", "the exact key first, and SONY after it as the matcher's reading")
    ask("sony", "with room for one brand the matcher's reading is not asked", 1)
    ask("sony sonny", "two brands named by runs: the longer first (SONNY 5, SONY 4)")
    ask("q", "a brand of one character is offered as a start, but a run needs two characters to name it: no models")
    ask("qr", "the exact key, and the brand whose key it starts (QRSTU)")
    ask("qr stv", "QR is named by its word, QRSTU is what the matcher reads 'qrstv' as (one edit in five): the named brand first")
    ask("qr stv", "a limit of 1 keeps the named brand", 1)
    ask("zzzz", "no brand at all")
    ask("zzzzzz", "no brand at all, longer")
    ask("sa", "the limit cuts the list after SANYO, in the same order", 2)
    ask("s", "the limit cuts the list after the order: SAMSUNG and SONY", 2)
    ask("s", "a limit of 1", 1)
    ask("s", "a limit of 20 is more than there are", 20)
    ask("a", "a one-character query gives the brands that start with it only: ACME (2 models) and ACCESS HD (1)")
    ask("o", "one brand starts with 'o'")
    ask("x", "no brand starts with it")
    ask("5", "a digit, which no brand starts with")

    # -- S2 and S3: a query that names a brand lists its models ----------------------------------------
    ask("samsung", "a brand alone: its models, most remotes first (LE32R73BD LCD TV has 2), then by name")
    ask("samsung", "the same with a smaller limit", 3)
    ask("sony", "a brand alone: BDP - S 360 with 3 remotes, KD - 49 X 8088 with 2, then 1 each by name")
    ask("samsung un50", "a brand and the start of a model: the models whose key starts with 'un50', one remote each, by name")
    ask("un50 samsung", "the brand after the model typed so far")
    ask("samsung un50nu69", "a longer start")
    ask("samsung UN50NU6900F", "the whole model: the one whose key it is")
    ask("samsung un5 0", "words of the model typed apart are joined")
    ask("samsung un60", "a start no model has: the brand and no models")
    ask("sony kd 49", "the rest of the query, 'kd49', starts two models: the one with 2 remotes first")
    ask("sony kd-49-x-8088", "separators of the model do not matter")
    ask("sony kd", "the rest 'kd' also starts KDL-40EX720: most remotes first, then by name")
    ask("sony kd", "a limit of 2 keeps the first two", 2)
    ask("sony kd50", "a typo is not forgiven inside a brand")
    ask("sony un50", "a model of another brand does not count")
    ask("sony bdp s 3", "a model written in words: BDP - S 360 and BDP - S 300 by remotes")
    ask("sony samsung", "two brands named: SAMSUNG's six models first, then SONY's up to the limit of 8")
    ask("sony samsung", "the limit is reached inside the first brand", 4)
    ask("sony samsung", "the limit falls inside SAMSUNG's six models", 5)
    ask("sony samsung", "the limit is exactly SAMSUNG's six: none of SONY's", 6)
    ask("sony samsung", "one more than SAMSUNG's six: SONY's first model", 7)
    ask("sony samsung", "twelve is all of both brands' models", 12)
    ask("sony samsung", "and a larger limit adds nothing", 14)
    ask("sony samsung un5", "the rest 'un5' is the model typed so far, and only SAMSUNG has models that start so")
    ask("lg 42", "the rest '42' starts two LG models, one remote each: by name")
    ask("lg lg 42", "a brand typed twice: a run counts once, at its first place, so only the first word is the brand's and the rest is 'lg42'")
    ask("roku ultra", "a model with no digit, inside the brand named")
    ask("roku", "a brand alone: its two models with one remote each, by name")
    ask("zed rc 5", "a model of two characters inside its brand")
    ask("acme rc5", "the same model name under another brand")
    ask("vizio v505", "a model with a hyphen typed without it")
    ask("harman kardon avr", "a brand of two words")
    ask("harman kardon avr 161", "a brand of two words and the whole model")
    ask("bigco", "a brand with more models than the limit: the first eight, most remotes first (2 remotes where the number is a multiple of 3)")
    ask("bigco", "the same with a limit of 12", 12)
    ask("bigco bg-1", "the models whose key starts with 'bg1': BG-12 with 2 remotes first, then BG-10 and BG-11")
    ask("tvco", "the models of TVCO: TV 10 with 3 remotes, TV 100 and TV 1000 with 2, then 1 each by name in code point order")
    ask("tvco tv1", "the exact key leads, the others by remotes: TV 1, TV 10, TV 100, TV 1000")
    ask("tvco tv10", "TV 10 leads and TV 1 is not offered")
    ask("tvco tv100", "TV 100 leads and TV 1000 follows")
    ask("tvco tv1000", "the last model")
    ask("tvco alpha", "three models of one remote each, by name in code point order: 'ALPHA 3', 'Alpha 1', 'alpha 2'")
    ask("cpco", "names compare by code point, not by UTF-16 unit: U+FF46 comes before U+20000, though its UTF-16 unit is the later")
    ask("sanyo", "a brand with two models of one remote each")
    ask("ünï", "a brand whose own name has accents")
    ask("uni Ω1", "the model of an accented brand")
    ask("sat sat1", "a brand and a model of the same name")
    ask("sat1", "a model typed with no brand: found by the matcher, and no brand is offered because none is named")
    ask("orbitech ci 500", "a model with brackets, typed without them")
    ask("a b c d e f g h i j k samsung", "samsung is the twelfth token: it is named, and no model starts with the rest, 'abcdefghijk'")
    ask("a b c d e f g h i j k l samsung", "samsung is the thirteenth token: only the first twelve are looked at, so no brand is named")
    ask("samsung a b c d e f g h i j k l un50", "the rest takes every word that is not a brand's, not only the first twelve")
    ask("zed a b c d e f g h i j k", "ZED and eleven letters: the rest is the whole key of one model, which is offered")
    ask("zed a b c d e f g h i j k l", "the rest takes every word, so with a twelfth letter after ZED it starts no model, though the first twelve words would")

    # -- S5: no brand named: the models the matcher finds for the query as one text -----------------------
    ask("un50nu69", "no brand named: the models the matcher finds for the query as one text")
    ask("un50nu6900", "a prefix of one model: the matcher's prefix rule needs five characters")
    ask("un50", "four characters: the matcher wants five, so no models")
    ask("un50nu6900f", "a whole model alone: it first, then the models one character from it")
    ask("un50nu690f", "a typo is forgiven here, as the matcher forgives it")
    ask("UN50NU69OOF", "a letter O for a zero costs half an edit")
    ask("kd49x8088", "a model of SONY, the brand not typed")
    ask("un50nu6900f", "the same with a limit of 1", 1)
    ask("kd49x80", "the start of a model: the matcher's prefix rule, 5 characters or more")
    ask("bn59-01199f", "a part number: a model of kind 1")
    ask("rm-ed011", "another part number")
    ask("bdp-s360", "a run inside a longer one that matched is not tried: SONY's model, not BRAVO's S 360")
    ask("s 360", "that other run, alone: BRAVO's S 360")
    ask("42lb580", "a start of two LG models: the matcher offers the one that scores higher")
    ask("ultra", "a model with no digit alone offers nothing")
    ask("made in china", "words with no model offer nothing")

    # -- S4: the models of one brand, by themselves -------------------------------------------------------
    inside("SONY", "", "an empty query: the brand's models, most remotes first")
    inside("SONY", "", "a limit of 2", 2)
    inside("SONY", "kd", "a start: most remotes first, then by name")
    inside("SONY", "kd49", "a longer start")
    inside("SONY", "KD - 49 X 80", "the key ignores case and separators")
    inside("SONY", "kd50", "a typo is not forgiven")
    inside("SONY", "UN50", "the models of another brand do not count")
    inside("SONY", "rm-ed011", "a part number")
    inside("SONY", "kdl", "a device")
    inside("SONY", "bdps360", "a model with three remotes")
    inside("SONY", "kd", "a limit of 1", 1)
    inside("SONY", "k", "one character")
    inside("TVCO", "tv1", "the model whose key is the query leads even with one remote")
    inside("TVCO", "tv10", "and again one level down")
    inside("TVCO", "tv100", "and again")
    inside("TVCO", "", "an empty query: most remotes first, then by name in code point order")
    inside("TVCO", "tv", "all the TV models: the exact key 'tv' is none, so by remotes")
    inside("TVCO", "tv1", "a limit of 2 keeps the exact one and the first that goes on", 2)
    inside("BIGCO", "", "more models than the limit")
    inside("BIGCO", "", "a limit of 20", 20)
    inside("BIGCO", "bg0", "the first nine")
    inside("CPCO", "", "code point order")
    inside("SAMSUNG", "un5", "a start of three models, one remote each: by name")
    inside("SAMSUNG", "un50nu69", "two models")
    inside("SAMSUNG", "zz", "no model")
    inside("HISENSE", "55e7", "the exact model leads, the one that goes on follows")

    # -- typing one character at a time ----------------------------------------------------------------------
    typing("samsung un50nu6900f", "a brand and a model")
    typing("sony kd-49x8088", "a brand and a model with separators")
    typing("harman kardon avr 161", "a brand of two words")
    typing("tvco tv1000", "a model that is the start of others")

    # -- brand aliases (D101) -----------------------------------------------------------------------------------
    ask("海信", "an alias typed whole: the brand and its models, as its own name would give them")
    ask("海", "the first character of an alias: the brand it starts, as the start of a brand's name does")
    ask("索尼", "an alias of SONY")
    ask("新力", "another alias of SONY, a regional form")
    ask("索", "the first character of an alias: it starts one alias, of SONY")
    ask("新", "the first character of the other alias of SONY")
    ask("创维", "Simplified")
    ask("創維", "Traditional")
    ask("创", "the start of the Simplified")
    ask("創", "the start of the Traditional")
    ask("维", "the end of an alias is not a start")
    ask("創維 55g", "an alias and the start of a model")
    ask("创维 55g", "the same in Simplified")
    ask("创维55g2", "no space between the name and the model: one token is cut where Han characters end")
    ask("創維 43", "a model that starts with digits")
    ask("海信 55E7", "an alias and a whole model: the exact one leads, the one that goes on follows")
    ask("海信55E7", "no space")
    ask("海信 55e7h", "the start of the longer model")
    ask("海信 99", "no model of that name: the brand only")
    ask("55E7 海信", "the model before the brand")
    ask("三星 un50", "an alias of SAMSUNG and a model")
    ask("三星", "the models of SAMSUNG", 3)
    ask("松下", "an alias of PANASONIC")
    ask("國際牌", "its regional form")
    ask("國際牌 nv", "a regional form and a start")
    ask("飞利浦", "Simplified")
    ask("飛利浦", "Traditional")
    ask("飞利浦 42", "an alias and a start of two models")
    ask("索尼 新力", "two aliases of one brand name it once")
    ask("索尼 sony", "an alias and the brand's own name name it once")
    ask("索尼 三星", "two aliases of two brands: the same length, so the lower id first (SAMSUNG before SONY)")
    ask("三星 索尼 kd", "two brands named and a rest that only SONY has")
    ask("索尼 TV 55", "a model of another brand holds the alias in its name: SONY is named, and 'tv55' starts none of SONY's models")
    ask("索尼tv55", "the same without spaces")
    ask("日本", "a query that is no alias")
    ask("电视", "a word for a kind of device: no brand")
    ask("三星电视", "a name that only starts with an alias is not that alias")
    ask("海信电视", "the same")
    ask("sony 索尼 kd 49", "both the brand's name and its alias, and a model")
    ask("哈", "a start no alias has")
    ask("西部数据", "an alias that two brands share names both: the brands by models (one each) and then name, the models of both in the order of the brands' ids, WD's first")
    ask("西部", "the start of a shared alias offers both brands")
    ask("西部数据 wdtv l", "a shared alias and the start of a model: WESTERN DIGITAL's, not WD's")
    ask("西部数据", "the limit falls between the two brands' models", 1)
    ask("wd", "a brand's own name is not shared: WD alone")
    typing("创维 55g20", "an alias and a model")
    typing("索尼", "an alias of two characters")

    return {
        "format": FORMAT,
        "about": "Cross-language vectors of suggest (DESIGN.md D100): a port gives these brands and "
                 "models, in this order. Brand ids number the brands from 1 in the order of their search "
                 "keys, model ids the models from 1 in the order of their search keys inside a brand.",
        "constants": {
            "SUGGEST_LIMIT": matching.SUGGEST_LIMIT, "BRAND_MIN_KEY": matching.BRAND_MIN_KEY,
            "BRAND_CANDIDATES": matching.BRAND_CANDIDATES, "MAX_RUN": matching.MAX_RUN,
            "MAX_TOKENS": matching.MAX_TOKENS, "BRAND_MODELS": matching.BRAND_MODELS,
        },
        "catalog": CATALOG,
        "aliases": ALIASES,
        "prefer": _prefer(index),
        "suggest": suggestions,
        "suggestModels": in_brand,
    }


# -- the hint (S6, D106) -----------------------------------------------------------------------------


def _prefer(main: matching.MatchIndex) -> dict[str, Any]:
    """The ``prefer`` section: ``suggest`` with a hint, over the catalog above (``main``) and ``TIES``."""
    indexes = {"main": main, "ties": matching.MatchIndex.from_entries(TIES, TIE_ALIASES)}
    cases: list[dict[str, Any]] = []

    def ask(catalog: str, query: str, names: list[str], note: str, limit: int | None = None) -> None:
        index = indexes[catalog]
        used = matching.SUGGEST_LIMIT if limit is None else limit
        answer = index.suggest(query, used, names)
        cases.append({
            "note": note, "catalog": catalog, "query": query, "limit": used, "prefer": names,
            "brands": _brands(answer.brands), "models": _models(answer.models)})

    def typing(catalog: str, text: str, names: list[str], note: str) -> None:
        for n in range(1, len(text) + 1):
            ask(catalog, text[:n], names, f"typing: {note}, {n} of {len(text)} characters")

    # -- brands that start with the query: the hint goes before most models, name and id ---------------
    ask("main", "s", [], "no hint: SAMSUNG and SONY tie on 6 models and go by name, then SKYWORTH 3, SANYO 2, SAT 1 and SONNY 1")
    ask("main", "s", ["SONY"], "a hinted brand goes first among the brands that start with the query: SONY before SAMSUNG, which the name would put first")
    ask("main", "s", ["SAT"], "the hint replaces most models: SAT has one model and goes before SAMSUNG with six; the others keep their order")
    ask("main", "s", ["SAT", "SONY"], "two hinted brands keep the order they have without the hint: SONY (6 models) before SAT (1), then the rest")
    ask("main", "s", ["SONY", "SAT"], "the order of the names means nothing: the same answer as SAT, SONY")
    ask("main", "s", ["SONY", "SAT", "sony", "Sat "], "a name given twice, in other case and with a space, is the same brand: the same answer")
    ask("main", "s", ["SAT"], "the limit cuts after the hinted order: SAT takes the first place, SAMSUNG the second", 2)
    ask("main", "s", ["SAT"], "a limit of 1 keeps the hinted brand", 1)
    ask("main", "s", ["SAT", "SONY"], "a limit of 3: the two hinted, then the first of the rest", 3)
    ask("main", "s", ["SAMSUNG"], "a hint for the brand that was first anyway changes nothing")
    ask("main", "sa", ["SAT"], "two characters: SAT goes before SAMSUNG and SANYO")
    ask("main", "t", ["TELESTAR"], "TVCO has 7 models, Topping 3, TELESTAR 1: the hinted TELESTAR goes first")
    ask("main", "t", ["TELESTAR", "Topping"], "two hinted brands: Topping (3 models) before TELESTAR (1), then TVCO")
    ask("main", "s", ["索尼"], "an alias of a brand stands for the brand: SONY first")
    ask("main", "s", ["Nobody"], "a name that is no brand is ignored: the answer without a hint")
    ask("main", "s", ["Nobody", "SAT"], "an unknown name beside a known one: the known one counts")
    ask("main", "s", ["Sonn", "Samsun"], "no similarity: a misspelt name is no brand, and the answer is the one without a hint")
    ask("main", "s", ["Sony Electronics"], "a name that is no brand's key, though it starts with one: ignored")
    ask("main", "s", ["S.O.N.Y"], "names are compared by their search key: separators do not matter")
    ask("main", "s", ["ｓｏｎｙ"], "full-width letters are the same key")
    ask("main", "s", [" "], "a blank name is no brand")
    ask("main", "x", ["SONY"], "a hint for a brand that does not start with the query adds nothing: no brand starts with x")
    ask("main", "o", ["SONY", "SAT"], "a hinted brand the query does not match is never added: ORBITECH alone")
    # -- the brand whose key is the query --------------------------------------------------------------
    ask("main", "q", [], "no hint: the exact key Q first, then the brand whose key starts with it, QR, then QRSTU")
    ask("main", "q", ["QRSTU"], "the brand whose key is the query stays first however the others are hinted: Q, then the hinted QRSTU, then QR")
    ask("main", "q", ["QRSTU"], "the limit keeps the exact key first, then the hinted brand", 2)
    ask("main", "q", ["QRSTU"], "a limit of 1 is the exact key", 1)
    ask("main", "q", ["Q"], "a hint for the exact key changes nothing")
    ask("main", "qr", ["QRSTU"], "the exact key QR first, QRSTU after it, the only brand that starts with it")
    ask("main", "西部数据", [], "no hint: an alias that two brands share is the exact key of both: by models (one each), then name: WD before WESTERN DIGITAL, and their models in the order of the brands")
    ask("main", "西部数据", ["WESTERN DIGITAL"], "a shared alias is an exact key of both brands, which tie: the hinted one goes first, and so do its models")
    ask("main", "西部数据", ["WESTERN DIGITAL"], "the limit of models falls between the two brands: the hinted brand's model", 1)
    ask("main", "西部数据", ["西部数据"], "a name that two brands share stands for both: nothing moves")
    ask("main", "西部", ["WESTERN DIGITAL"], "the start of a shared alias offers both brands, the hinted one first")
    ask("main", "西部数据 wdtv l", ["WESTERN DIGITAL"], "a shared alias and the start of a model: WESTERN DIGITAL's model, as without a hint")
    # -- brands named by a run of the query: equal runs tie, a longer run is better ------------------------
    ask("main", "sony roku", [], "no hint: ROKU and SONY are named by runs of 4 characters each and go by id, ROKU first, and so do their models")
    ask("main", "sony roku", ["SONY"], "runs of the same length tie: the hinted brand goes before the id, and its models first")
    ask("main", "sony roku", ["SONY"], "the limit of models is filled from the hinted brand first: the first three models of SONY", 3)
    ask("main", "sony roku", ["SONY"], "a limit of 7: SONY's six models and the first of ROKU's", 7)
    ask("main", "sony roku", ["ROKU"], "a hint for the brand that was first by id changes nothing")
    ask("main", "sony samsung", ["SONY"], "a brand named by a shorter run is not lifted above one named by a longer run: SAMSUNG (7) stays first")
    ask("main", "lg sony philips x", ["LG"], "three runs of three lengths: the hint for the shortest changes nothing")
    ask("main", "lg sony philips x", ["LG", "SONY", "PHILIPS"], "all three hinted: the same order, the runs differ in length")
    ask("main", "索尼 三星", ["索尼"], "two aliases named by runs of the same length: the hinted brand's before the id: SONY before SAMSUNG")
    ask("main", "索尼 三星", ["SONY"], "the brand's own name hints the same brand")
    ask("main", "索尼 三星 kd", ["SONY"], "a rest that only SONY has: its models, with or without the hint")
    # -- brands the matcher only reads the query as ----------------------------------------------------------
    ask("main", "sony", [], "no hint: the exact key, then SONNY, which the matcher reads sony as (one insertion in five)")
    ask("main", "sony", ["SONNY"], "a brand that is only the matcher's reading is never moved, and never above the exact key")
    ask("main", "sonyy", [], "no hint: SONNY and SONY are each a character from sonyy, best first by id: SONNY, SONY")
    ask("main", "sonyy", ["SONY"], "brands the matcher reads the query as are never moved, whatever is hinted")
    ask("main", "qr stv", ["QRSTU"], "QR is named by its word, QRSTU is what the matcher reads qrstv as: the named brand stays first")
    ask("main", "qr stv", ["QRSTU"], "a limit of 1 keeps the named brand", 1)
    ask("main", "phillips", ["PHILIPS"], "a brand that only the matcher's reading offers: the same answer")
    # -- models, when no brand is named -------------------------------------------------------------------------
    ask("main", "ci500twnsat1", [], "no hint: two brands have the model 'CI 500 TWN(SAT 1)', an equal match: by brand name, ORBITECH before TELESTAR")
    ask("main", "ci500twnsat1", ["TELESTAR"], "models of an equal score, similarity and key length: the hinted brand's first")
    ask("main", "ci500twnsat1", ["TELESTAR"], "the limit of models keeps the hinted brand's", 1)
    ask("main", "ci500twnsat1", ["ORBITECH"], "a hint for the brand that was first by name changes nothing")
    ask("main", "ci500twnsat1", ["SONY"], "a hint for a brand with no such model adds nothing")
    ask("main", "un50nu6900f", ["SONY", "SAMSUNG"], "only SAMSUNG has models like it: the exact match first and the near ones after it, as without a hint")

    # -- the catalog of ties ---------------------------------------------------------------------------------------
    ask("ties", "al", [], "no hint: AL is the key, then by models: ALPINE and ALTEC have five and go by name, ALPHA 2, ALPS 1")
    ask("ties", "al", ["ALPS"], "the hinted ALPS goes before the models and the names of the brands that start with al, and not above AL, the exact key")
    ask("ties", "al", ["ALPS"], "the limit lets the hinted brand in place of an equal: ALPS takes the place of ALPINE", 2)
    ask("ties", "al", ["ALPS"], "a limit of 1: the first place is the exact key's", 1)
    ask("ties", "al", ["ALPS"], "a limit of 3", 3)
    ask("ties", "al", ["ALPS"], "a limit of 5 is the whole list: AL, ALPS, then the others as before", 5)
    ask("ties", "al", ["ALTEC"], "the hint goes before the name: ALTEC before ALPINE, which have five models each")
    ask("ties", "al", ["ALPS", "ALPHA"], "two hinted brands in their own order (ALPHA has more models): ALPHA, ALPS")
    ask("ties", "al", ["ALPHA", "ALPS"], "the same hint in another order: the same answer")
    ask("ties", "al", ["ALPS", "ALPHA", "ALTEC", "ALPINE"], "every brand that starts with it hinted: nothing moves")
    ask("ties", "al", ["AL"], "a hint for the exact key changes nothing")
    ask("ties", "al", ["ZEBRA"], "a brand that does not start with the query is never added")
    ask("ties", "alp", ["ALPS"], "ALPS first among ALPINE, ALPHA, ALPS")
    ask("ties", "alp", ["ALPHA"], "ALPHA first, ALPINE and ALPS in their order")
    ask("ties", "alp", ["ALPS", "ZEBRA", "EAST", "ACME"], "names of brands the query does not offer are no use: ALPS only")
    ask("ties", "alp", ["ALPS"], "the limit cuts the hinted whole: ALPS, ALPINE", 2)
    ask("ties", "alp", ["ALPS"], "a limit of 1", 1)
    ask("ties", "alp", ["alps"], "a name in lower case")
    ask("ties", "alp", ["A-L-P-S"], "a name with separators")
    ask("ties", "alp", ["ALP"], "a name that is the start of a brand's is no brand's")
    ask("ties", "a", [], "no hint: a one-character query lists the brands that start with it, most models first")
    ask("ties", "a", ["ALPS", "AC"], "two hinted brands with one model each: by name, AC before ALPS, then the rest in their order")
    ask("ties", "ac", [], "no hint: AC is the key, then ACID (2 models) and ACME (3): ACME, ACID")
    ask("ties", "ac", ["ACID"], "the exact key stays first: AC, ACID, ACME")
    ask("ties", "acid acme", [], "no hint: ACID and ACME are named by runs of 4 characters each: by id, ACID first, and their models")
    ask("ties", "acid acme", ["ACME"], "equal runs: the hinted brand and its models first, each brand's models in their own order")
    ask("ties", "acid acme", ["ACME"], "the limit is filled from the hinted brand's models first", 3)
    ask("ties", "acid acme", ["ACME"], "a limit of 1", 1)
    ask("ties", "acid acme", ["ACID"], "a hint for the brand that was first by id changes nothing")
    ask("ties", "alpine ac", [], "no hint: ALPINE is named by a run of 6, AC by one of 2")
    ask("ties", "alpine ac", ["AC"], "a brand named by a shorter run is not lifted above one named by a longer one, and neither are its models")
    ask("ties", "korix", [], "no hint: KORAX and KOREX are both a character from korix: by id")
    ask("ties", "korix", ["KOREX"], "brands that the matcher only reads the query as are never moved")
    ask("ties", "korix", ["KOREX", "KORAX"], "all of them hinted: nothing moves")
    ask("ties", "korix", ["KOREX"], "and a limit of 1 does not let the hinted one in", 1)
    ask("ties", "kor", ["KOREX"], "the same two brands as brands that start with the query are moved: KOREX first")
    ask("ties", "tx1000", [], "no hint: the model TX1000 of four brands, an equal match: by brand name; SOUTH's TX10001 is only the start of a longer model and last")
    ask("ties", "tx1000", ["WEST"], "models of an equal score, similarity and key length: WEST's first, and the worse match stays last")
    ask("ties", "tx1000", ["SOUTH"], "SOUTH's TX1000 first, and its TX10001 stays after every TX1000, hinted or not")
    ask("ties", "tx1000", ["SOUTH", "WEST"], "two hinted brands: their models in the order they had, then the others")
    ask("ties", "tx1000", ["WEST", "SOUTH"], "the order of the names means nothing")
    ask("ties", "tx1000", ["WEST", "NORTH", "SOUTH", "EAST"], "every brand that has the model hinted: nothing moves, and the worse match stays last")
    ask("ties", "tx1000", ["SOUTH"], "the limit keeps the hinted model in the place of an equal one", 1)
    ask("ties", "tx1000", ["WEST"], "SOUTH's TX1000 is pushed out by WEST's", 3)
    ask("ties", "tx1000", ["SOUTH"], "four equal matches fill the limit of four: the worse one is not let in, hinted or not", 4)
    ask("ties", "tx1000", ["SOUTH"], "a limit of 5 is the whole list", 5)
    ask("ties", "tx1000", ["ZEBRA", "ALPS"], "brands with no such model add nothing")
    ask("ties", "tx1000", ["南方"], "a Chinese name of a brand stands for the brand: SOUTH's first")
    ask("ties", "tx1000", ["北方", "南方"], "two Chinese names: NORTH's and SOUTH's, in their order")
    ask("ties", "tx1000", ["方向"], "a name that two brands share stands for both: EAST's and WEST's first")
    ask("ties", "tx1000", ["方向", "南方"], "a shared name and another: EAST, SOUTH, WEST in their order, then NORTH")
    ask("ties", "tx", ["WEST"], "four characters are too few for the matcher's models: no model, no change")
    ask("ties", "tx100", ["WEST"], "five characters: TX1000 under four brands, a prefix match of equal weight, WEST first")
    ask("ties", "方向", [], "no hint: a name that EAST and WEST share is the key of both, and names both: EAST, WEST and their models")
    ask("ties", "方向", ["WEST"], "the exact key of two brands that tie: the hinted one first, and its model first")
    ask("ties", "方向", ["WEST"], "the limit of models keeps the hinted brand's", 1)
    ask("ties", "方向", ["南方"], "a brand the query does not name is not added")
    ask("ties", "方向 tx", ["WEST"], "a shared name and the start of a model: both brands' TX1000, the hinted one's first")
    ask("ties", "北方 南方", ["南方"], "two Chinese names of runs of the same length: SOUTH before NORTH, and its models")
    ask("ties", "zebra", ["ZEBRA"], "the models of one brand are never reordered by a hint")
    ask("ties", "alpine p", ["ALPINE", "ZEBRA"], "the same inside a brand with a start of a model")
    ask("ties", "alpine", ["ALPINE"], "most remotes first, then by name, as without a hint")
    ask("ties", "alpine", ["ALPS"], "a hint for another brand changes nothing inside a brand")
    typing("ties", "alp", ["ALPS"], "brands that start with the query")
    typing("ties", "acid acme", ["ACME"], "two named brands")

    return {
        "about": "suggest with a hint (DESIGN.md D106): the brands named in prefer go first where the rules "
                 "do not tell entries apart, and nowhere else. A case asks `main`, the catalog and aliases "
                 "above, or `ties`, the catalog and aliases of this section, which has more ties for a hint "
                 "to break; ids number brands and models as for the catalog above. A port that gives the "
                 "answers of `suggest` need not read this section.",
        "ties": {"catalog": TIES, "aliases": TIE_ALIASES},
        "cases": cases,
    }
