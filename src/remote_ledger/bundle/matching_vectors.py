"""Cross-language vectors of the matcher (D97): ``tests/vectors/matching_vectors.json``.

A port of ``matching.py`` (a Kotlin search on a phone, or a service in another language) is
right when it gives these. The file is **self-contained and independent of the data**: the
catalog is a small one written below, and every expected answer is what ``matching.py`` gives
over it, so the file changes only when the rules do and it is checked for drift (``rl bundle
matching-vectors --check``, and a test).

It holds, in order: the constants of the rules; the normalisation of texts into tokens and
keys; the similarity of pairs of keys; the catalog; and queries with their answers, each
answer an integer score in thousandths, the evidence words, the remote ids and, for a
brand-only answer, the models listed.
"""

from __future__ import annotations

from typing import Any

from .. import matching
from .textnorm import search_norm

FORMAT = 1

#: A small catalog: each entry is a model of a brand, the remotes that control it (ids are
#: arbitrary), and ``kind`` 1 for a remote's own part number. It holds what each rule needs: a
#: pair of models one character apart, a model that is the start of another, a model that is a
#: part of a longer one under another brand (``S 360``), one model under two brands, brands
#: written with spaces and accents, and a brand with models to list.
CATALOG: list[dict[str, Any]] = [
    {"brand": "SAMSUNG", "model": "UN50NU6900F", "remotes": [101]},
    {"brand": "SAMSUNG", "model": "UN50NU6800F", "remotes": [102]},
    {"brand": "SAMSUNG", "model": "UN55NU6900F", "remotes": [103]},
    {"brand": "SAMSUNG", "model": "UE 50 HU 6900", "remotes": [104]},
    {"brand": "SAMSUNG", "model": "BN59-01199F", "remotes": [105], "kind": 1},
    {"brand": "SAMSUNG", "model": "LE32R73BD LCD TV", "remotes": [106, 107]},
    {"brand": "SONY", "model": "KD - 49 X 8088", "remotes": [201, 202]},
    {"brand": "SONY", "model": "KD - 49 X 7055", "remotes": [203]},
    {"brand": "SONY", "model": "BDP - S 360", "remotes": [204, 205, 206]},
    {"brand": "SONY", "model": "BDP - S 300", "remotes": [205]},
    {"brand": "SONY", "model": "RM-ED011", "remotes": [207], "kind": 1},
    {"brand": "SONY", "model": "KDL-40EX720", "remotes": [208]},
    {"brand": "BRAVO", "model": "S 360", "remotes": [301]},
    {"brand": "LG", "model": "42LB5800", "remotes": [401]},
    {"brand": "LG", "model": "42LB5700", "remotes": [402]},
    {"brand": "LG", "model": "OLED55C9PUA", "remotes": [403]},
    {"brand": "LG", "model": "32LJ500B", "remotes": [404]},
    {"brand": "PHILIPS", "model": "42PF9966", "remotes": [501]},
    {"brand": "PHILIPS", "model": "50PF9966", "remotes": [502]},
    {"brand": "PHILIPS", "model": "32PFL3605", "remotes": [503]},
    {"brand": "HARMAN KARDON", "model": "AVR 161", "remotes": [601]},
    {"brand": "ACCESS HD", "model": "HD 7000", "remotes": [701]},
    {"brand": "Topping", "model": "DX3 Pro", "remotes": [801]},
    {"brand": "Topping", "model": "D50s", "remotes": [802]},
    {"brand": "Topping", "model": "RC-15A", "remotes": [801, 802], "kind": 1},
    {"brand": "Ünï", "model": "Ω1000", "remotes": [901]},
    {"brand": "ACME", "model": "RC-5", "remotes": [1001]},
    {"brand": "ZED", "model": "RC-5", "remotes": [1002]},
    {"brand": "ZED", "model": "X5", "remotes": [1003]},
    {"brand": "VIZIO", "model": "V505-G9", "remotes": [1101, 1102]},
    {"brand": "VIZIO", "model": "D43f-F1", "remotes": [1101]},
    {"brand": "VIZIO", "model": "M50Q6-J01", "remotes": [1103]},
    {"brand": "ROKU", "model": "ULTRA", "remotes": [1201]},
    {"brand": "ROKU", "model": "Streaming Stick", "remotes": [1202]},
    {"brand": "LG", "model": "32 LC 2 RB - ZJ(DVD)", "remotes": [405]},
    {"brand": "DVD", "model": "D 100", "remotes": [1301]},
    {"brand": "PANASONIC", "model": "NV - FS 200", "remotes": [1401]},
    {"brand": "PANASONIC", "model": "Panasonic NV-HD600", "remotes": [1402]},
    {"brand": "WZRD", "model": "PANASONIC", "remotes": [1403]},
    {"brand": "ORBITECH", "model": "CI 500 TWN(SAT 1)", "remotes": [1501]},
    {"brand": "TELESTAR", "model": "CI 500 TWN(SAT 1)", "remotes": [1502]},
    {"brand": "SAT", "model": "SAT1", "remotes": [1503]},
]

#: Texts for the normalisation vectors.
TOKEN_TEXTS = [
    "UN50-NU 6900/F", "Ünï-Test 50/60Hz", "Ｓony ① KD-49X8088", "O'Brien & Sons", "", "??  --", "cafés",
    "ß straße", "Вниз ВВЕРХ", "RM-ED011", "a_b.c,d", "42LB5800",
]

#: Pairs of keys (query, catalog entry) for the similarity vectors.
SIMILARITY_PAIRS = [
    ("un50nu6900f", "un50nu6900f"), ("un50nu6900", "un50nu6900f"), ("un50nu6900fxza", "un50nu6900f"),
    ("un50nu690f", "un50nu6900f"), ("un50un6900f", "un50nu6900f"), ("un5onu69oof", "un50nu6900f"),
    ("un50nu6800f", "un50nu6900f"), ("abcd", "abce"), ("abcde", "abcdf"), ("abcde", "abcde"),
    ("abcd", "abcde"), ("abcde", "abcdef"), ("42lb580", "42lb5800"), ("x5", "x6"), ("", "abc"),
    ("samsung", "samsungelectronics"), ("samsun", "samsung"), ("phillips", "philips"),
    ("sony", "sonic"), ("s360", "bdps360"), ("1", "l"), ("o", "0"), ("ab", "ba"),
]


def build() -> dict[str, Any]:
    """The vectors, from ``matching.py``."""
    index = matching.MatchIndex.from_entries(CATALOG)
    queries: list[dict[str, Any]] = []

    def ask(brand: str | None, model: str | None, texts: list[str], note: str) -> None:
        answer = index.match(brand, model, texts)
        queries.append({
            "note": note, "brand": brand, "model": model, "texts": texts,
            "answer": [
                {"brand": c.brand, "model": c.model, "remoteIds": list(c.remote_ids),
                 "permille": c.permille, "evidence": list(c.evidence), "models": list(c.models)}
                for c in answer],
        })

    ask(None, None, ["UN50NU6900F"], "a model alone, as typed")
    ask(None, None, ["samsung un50nu6900f"], "the brand named in the text")
    ask(None, None, ["UN50NU6900"], "the suffix dropped: a prefix of one model, the other kept out")
    ask(None, None, ["UN50NU6900FXZA"], "a label that goes on past the entry")
    ask(None, None, ["un 50 nu-6900 f"], "separators do not matter")
    ask(None, None, ["UN50NU69OOF"], "letter O for digit 0 costs half an edit")
    ask(None, None, ["un50nu690f"], "one character dropped")
    ask(None, None, ["un50un6900f"], "two neighbours swapped")
    ask(None, None, ["samsung un50nu69"], "the brand and the start of a model")
    ask(None, None, ["samsung un50"], "too short a start: the brand only")
    ask(None, None, ["samsung"], "a brand typed: its models, most remotes first")
    ask("Samsung Electronics Co", None, [], "a brand given with more words")
    ask(None, None, ["KD-49X8088 Sony"], "the model and then the brand")
    ask(None, None, ["sony kd 49 x 8088"], "a model written with spaces")
    ask(None, None, ["bdp-s360"], "a run inside a longer one that matched is not tried: no BRAVO S 360")
    ask(None, None, ["s 360"], "that other run, alone")
    ask(None, None, ["BN59-01199F"], "a remote's own part number")
    ask(None, None, ["rm-ed011"], "another part number")
    ask("Samsung", "UN50NU6900F", ["Model: UN50NU6900FXZA", "Made in Mexico", "S/N 1234ABCD5678"],
        "what a provider reads off a photo")
    ask("Samsung", "ZX99QQ1234", [], "a model nobody has: the brand, with its models")
    ask("Samsung", "KD-49X8088", [], "a model of another brand does not count for this one")
    ask(None, "RC-5", [], "one model under two brands: both, in the order of brand names")
    ask(None, "X5", [], "a model of two characters, given as the model, must be exact")
    ask("harman-kardon", "AVR161", [], "a brand written with a hyphen")
    ask("Samsun", "UN50NU6900F", [], "a brand with a typo")
    ask("Phillips", "42PF9966", [], "another brand typo")
    ask("Uni", "omega1000", [], "accents and another script")
    ask("Ünï", "Ω1000", [], "the same, written as the catalog does")
    ask(None, None, ["Sony Samsung UN50NU6900F"], "two brands named: the model's brand is one")
    ask(None, None, ["Made in China", "Serial 12345678", "Input 100-240V 50/60Hz"], "text with no model in it")
    ask("", "", [], "nothing asked")
    ask(None, None, ["42lb580"], "a prefix of one of two models")
    ask(None, None, ["vizio"], "a brand with three models")
    ask("Vizio", None, ["V505-G9"], "a brand and a model in the texts")
    ask(None, None, ["roku ultra"], "a model with no digit, tried among the models of the brand the text names")
    ask(None, None, ["ultra"], "that word alone: no brand named, so no model")
    ask(None, None, ["lg"], "a brand of two letters, typed")
    ask(None, None, ["LG 32 LC 2 RB - ZJ(DVD)"], "six tokens make one model; DVD in it is also a brand")
    ask(None, None, ["PANASONIC NV - FS 200"], "a long run that only starts like a model does not hide the exact one")
    ask(None, None, ["CI 500 TWN(SAT 1)"], "a brand named only by a text does not take a model from the others")
    ask("DVD", "CI 500 TWN(SAT 1)", [], "a brand that was given does")
    return {
        "format": FORMAT,
        "about": "Cross-language vectors of the matcher (DESIGN.md D97): a port gives these "
                 "tokens, keys, similarities and answers. Scores are integers in thousandths.",
        "constants": {
            "THRESHOLD": matching.THRESHOLD, "MIN_SCORE": matching.MIN_SCORE,
            "PREFIX_MIN": matching.PREFIX_MIN, "PREFIX_BASE": matching.PREFIX_BASE,
            "PREFIX_SPAN": matching.PREFIX_SPAN, "LOOKALIKE_HALVES": matching.LOOKALIKE_HALVES,
            "LOOKALIKES": sorted("".join(p) for p in matching.LOOKALIKES if p[0] < p[1]),
            "WEIGHT_MODEL": matching.WEIGHT_MODEL, "WEIGHT_TEXT": matching.WEIGHT_TEXT,
            "BRAND_GIVEN": matching.BRAND_GIVEN, "BRAND_GIVEN_PART": matching.BRAND_GIVEN_PART,
            "BRAND_TEXT": matching.BRAND_TEXT, "BRAND_MIN_KEY": matching.BRAND_MIN_KEY,
            "FACTOR_NAMED": matching.FACTOR_NAMED, "FACTOR_NONE": matching.FACTOR_NONE,
            "FACTOR_OTHER": matching.FACTOR_OTHER, "MAX_RUN": matching.MAX_RUN,
            "TEXT_KEY_MIN": matching.TEXT_KEY_MIN, "TEXT_KEY_MAX": matching.TEXT_KEY_MAX,
            "COVER_MIN": matching.COVER_MIN,
            "MAX_KEYS": matching.MAX_KEYS, "MAX_TOKENS": matching.MAX_TOKENS,
            "PER_KEY": matching.PER_KEY, "MIN_OVERLAP_PERCENT": matching.MIN_OVERLAP_PERCENT,
            "BRAND_CANDIDATES": matching.BRAND_CANDIDATES, "BRAND_MODELS": matching.BRAND_MODELS,
            "LIMIT": matching.LIMIT, "GRAM": matching.GRAM,
        },
        "tokens": [{"text": t, "tokens": matching.tokens(t), "key": search_norm(t)} for t in TOKEN_TEXTS],
        "similarity": [{"query": q, "entry": e, "permille": matching.similarity(q, e)}
                       for q, e in SIMILARITY_PAIRS],
        "catalog": CATALOG,
        "queries": queries,
    }
