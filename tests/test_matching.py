"""The matcher (D96, D97): ``matching.py``, and the vectors a port is held to.

The rules are checked against an independent reference where there is one: the distance
against a plain matrix implementation of optimal string alignment written here, the
similarity against the formula of the rules spelled out with the numbers worked by hand, the
tokens against the search key of the bundle. The index is tried on the small catalog of the
vectors, and on the real selected bundle for what only real data shows: how long a query takes.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from remote_ledger import cli, matching
from remote_ledger.bundle import build, matching_vectors, search_eval
from remote_ledger.bundle.textnorm import search_norm
from remote_ledger.matching import Candidate, MatchIndex, halves, similarity, tokens

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "vectors" / "matching_vectors.json"


@pytest.fixture(scope="module")
def index():
    return MatchIndex.from_entries(matching_vectors.CATALOG)


def ask(index, brand=None, model=None, texts=(), limit=5):
    return [(c.brand, c.model, c.permille) for c in index.match(brand, model, list(texts), limit)]


# --- tokens and keys -------------------------------------------------------------------------


@pytest.mark.parametrize("text, words", [
    ("UN50-NU 6900/F", ["un50", "nu", "6900", "f"]),
    ("Ünï-Test 50/60Hz", ["uni", "test", "50", "60hz"]),
    ("Ｓony ① KD-49X8088", ["sony", "1", "kd", "49x8088"]),
    ("café", ["cafe"]),            # a combining mark is dropped and does not split the word
    ("O'Brien", ["o", "brien"]),
    ("??", []),
    ("", []),
])
def test_tokens(text, words):
    assert tokens(text) == words


def test_the_tokens_of_a_text_are_its_search_key_in_pieces():
    rng = random.Random(11)
    pool = [chr(c) for c in range(0x20, 0x250)] + list("☃⏩–—‑·∕/\\|[](){}~`'\"")
    for _ in range(500):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(0, 16)))
        assert "".join(tokens(text)) == search_norm(text), repr(text)
        assert all(w and w == w.lower() for w in tokens(text))


def test_runs_are_the_keys_of_up_to_eight_adjacent_tokens_without_repeats():
    got = matching.runs(["kd", "49", "x", "8088"])
    assert [r.key for r in got] == ["kd", "kd49", "kd49x", "kd49x8088", "49", "49x", "49x8088", "x", "x8088", "8088"]
    assert [(r.start, r.end) for r in got][:4] == [(0, 1), (0, 2), (0, 3), (0, 4)]
    long = matching.runs(list("abcdefgh"))
    assert max(r.end - r.start for r in long) == matching.MAX_RUN == 8
    assert [r.key for r in matching.runs(["a", "a"])] == ["a", "aa"]      # the second a is the same key


# --- similarity -------------------------------------------------------------------------------


def osa(a: str, b: str, lookalike: float = 0.5) -> float:
    """Optimal string alignment by the textbook matrix, in whole edits."""
    look = {("o", "0"), ("0", "o"), ("i", "1"), ("1", "i"), ("l", "1"), ("1", "l"), ("s", "5"),
            ("5", "s"), ("b", "8"), ("8", "b"), ("z", "2"), ("2", "z")}
    d = [[0.0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = float(i)
    for j in range(len(b) + 1):
        d[0][j] = float(j)
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0.0 if a[i - 1] == b[j - 1] else (lookalike if (a[i - 1], b[j - 1]) in look else 1.0)
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def test_the_distance_is_optimal_string_alignment_with_cheap_look_alikes():
    rng = random.Random(5)
    alphabet = "ab0o1lsz25b8x"
    for _ in range(1500):
        a = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 9)))
        b = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 9)))
        assert halves(a, b) / 2 == osa(a, b), (a, b)


@pytest.mark.parametrize("query, entry, permille", [
    ("un50nu6900f", "un50nu6900f", 1000),
    ("un50nu690f", "un50nu6900f", 909),         # one deletion: 1000 - ceil(500 x 2 / 11)
    ("un50un6900f", "un50nu6900f", 909),        # a swap of neighbours is one edit
    ("un5onu69oof", "un50nu6900f", 863),        # three look-alikes are one and a half: ceil(500 x 3 / 11) = 137
    ("un50nu6800f", "un50nu6900f", 909),
    ("un50nu6900", "un50nu6900f", 981),         # prefix: 800 + 200 x 10 // 11
    ("un50nu6900fxza", "un50nu6900f", 957),     # the entry is the start of the query: 800 + 200 x 11 // 14
    ("abcd", "abce", 750),                      # four characters: one edit is under the threshold
    ("abcde", "abcdf", 800),                    # five: one edit is exactly the threshold
    ("abcd", "abcde", 800),                     # a prefix of four is too short for the prefix rule
    ("abcde", "abcdef", 966),                   # a prefix of five is not: 800 + 200 x 5 // 6
    ("s360", "bdps360", 571),                   # a suffix is not a prefix
    ("1", "l", 500), ("o", "0", 500), ("ab", "ba", 500), ("x5", "x6", 500),
    ("", "abc", 0), ("abc", "", 0), ("", "", 0),
])
def test_similarity(query, entry, permille):
    assert similarity(query, entry) == permille


def test_similarity_is_an_integer_between_0_and_1000_and_1000_only_for_equal_keys():
    rng = random.Random(9)
    for _ in range(2000):
        a = "".join(rng.choice("abo01") for _ in range(rng.randint(1, 8)))
        b = "".join(rng.choice("abo01") for _ in range(rng.randint(1, 8)))
        value = similarity(a, b)
        assert isinstance(value, int) and 0 <= value <= 1000
        assert (value == 1000) == (a == b)
        assert similarity(a, b) == similarity(b, a)


def test_a_look_alike_is_never_read_into_a_brand(index):
    assert similarity("l9", "19") == 750 and similarity("l9", "19", lookalikes=False) == 500
    assert halves("o", "0") == 1 and halves("o", "0", lookalikes=False) == 2
    # the brand ZED typed with a digit for its letter is not found (it would be at 833 if look-alikes
    # counted in a brand), while the same kind of swap in a model is half an edit
    assert similarity("2ed", "zed") == 833 and similarity("2ed", "zed", lookalikes=False) == 666
    assert index.match("2ed", None, []) == []
    assert index.match(None, "UN5ONU6900F", [])[0].model == "UN50NU6900F"


def test_a_key_of_four_must_be_exact_and_the_allowed_edits_grow_with_the_length():
    """The threshold is 20% of the longer key: 4 exact, 5 to 9 one edit, 10 to 14 two."""
    def allowed(n: int) -> int:
        word = "abcdefghijklmnopqrstuvwxyz"[:n]
        most = 0
        for edits in range(1, 5):
            changed = word[: n - edits] + "#" * edits           # that many substitutions
            if similarity(changed, word) >= matching.THRESHOLD:
                most = edits
        return most
    assert [allowed(n) for n in (4, 5, 9, 10, 14, 15)] == [0, 1, 1, 2, 2, 3]


def test_a_limit_only_ever_spares_work_that_could_not_reach_the_threshold():
    rng = random.Random(21)
    for _ in range(3000):
        a = "".join(rng.choice("abc01o") for _ in range(rng.randint(1, 12)))
        b = "".join(rng.choice("abc01o") for _ in range(rng.randint(1, 12)))
        exact = halves(a, b)
        longest = max(len(a), len(b))
        limit = (1000 - matching.THRESHOLD) * longest // 500
        capped = halves(a, b, limit)
        assert capped == exact if exact <= limit else capped > limit, (a, b)
        full, cut = similarity(a, b), similarity(a, b, cut=True)
        assert (cut >= matching.THRESHOLD) == (full >= matching.THRESHOLD)
        if full >= matching.THRESHOLD:
            assert cut == full


# --- the answer, on the small catalog ---------------------------------------------------------------


def test_a_model_alone_is_found_and_a_brand_in_the_text_raises_the_score(index):
    alone = ask(index, texts=["UN50NU6900F"])
    named = ask(index, texts=["samsung un50nu6900f"])
    assert alone[0][:2] == ("SAMSUNG", "UN50NU6900F") == named[0][:2]
    assert (alone[0][2], named[0][2]) == (855, 900)            # 1000 x 900 x 950, and x 1000
    assert alone[0][2] > alone[1][2]                           # the confusable models come after


def test_separators_and_case_do_not_matter(index):
    for text in ("un 50 nu-6900 f", "UN-50-NU-6900-F", "Un50Nu6900F", "un50/nu6900.f"):
        assert ask(index, texts=[text])[0][:2] == ("SAMSUNG", "UN50NU6900F"), text


def test_a_dropped_suffix_a_longer_label_a_typo_and_a_look_alike_still_find_it(index):
    for text in ("UN50NU6900", "UN50NU6900FXZA", "un50nu690f", "un50un6900f", "UN50NU69OOF"):
        assert ask(index, texts=[text])[0][:2] == ("SAMSUNG", "UN50NU6900F"), text


def test_the_start_of_a_model_needs_five_characters_and_a_brand_that_narrows_it(index):
    assert ask(index, texts=["samsung un50nu69"])[0][:2] == ("SAMSUNG", "UN50NU6900F")
    got = ask(index, texts=["samsung un50"])                  # four characters: no model counts
    assert got[0][1] is None and got[0][0] == "SAMSUNG"


def test_a_model_that_is_no_entry_gives_nothing_and_a_brand_gives_its_models(index):
    assert ask(index, texts=["zx99qq1234"]) == []
    got = index.match("Samsung", "ZX99QQ1234", [])
    assert [(c.brand, c.model) for c in got] == [("SAMSUNG", None)]
    assert got[0].remote_ids == () and got[0].evidence == ("brand", "brand:given")
    # its models, the one with most remotes first (LE32R73BD has two), then by name
    assert got[0].models[:3] == ("LE32R73BD LCD TV", "BN59-01199F", "UE 50 HU 6900")
    assert len(got[0].models) == 6 and got[0].permille == 1000


def test_a_model_of_another_brand_does_not_count_when_a_brand_is_named(index):
    assert [(c.brand, c.model) for c in index.match("Samsung", "KD-49X8088", [])] == [("SAMSUNG", None)]
    # with no brand named it does
    assert ask(index, model="KD-49X8088")[0][:2] == ("SONY", "KD - 49 X 8088")


def test_a_run_inside_a_longer_run_that_matched_is_not_tried(index):
    assert [(b, m) for b, m, _ in ask(index, texts=["bdp-s360"])][:1] == [("SONY", "BDP - S 360")]
    assert "BRAVO" not in {b for b, _, _ in ask(index, texts=["bdp-s360"])}
    assert ask(index, texts=["s 360"])[0][:2] == ("BRAVO", "S 360")


def test_one_model_under_two_brands_gives_both_in_the_order_of_their_names(index):
    got = ask(index, model="RC-5")
    assert [g[:2] for g in got] == [("ACME", "RC-5"), ("ZED", "RC-5")] and got[0][2] == got[1][2] == 950


def test_a_model_of_two_characters_is_exact_and_only_as_the_model(index):
    assert ask(index, model="X5")[0][:2] == ("ZED", "X5")
    assert ask(index, model="X6") == []
    assert ask(index, texts=["X5"]) == []                      # a text run needs four characters


def test_a_word_with_no_digit_is_a_model_only_of_a_brand_the_text_names(index):
    assert ask(index, texts=["roku ultra"])[0] == ("ROKU", "ULTRA", 900)
    assert ask(index, texts=["ultra"]) == []                   # no brand named: no model
    assert ask(index, texts=["roku streaming stick"])[0][:2] == ("ROKU", "Streaming Stick")
    # the brand's own name is not a model key: "samsung" is the brand, whatever it is a prefix of
    assert ask(index, texts=["samsung"])[0][:2] == ("SAMSUNG", None)
    keys = index.model_keys(None, ["roku ultra stick x"])
    assert keys and all(within for run, _, _, within in keys if not any(c.isdigit() for c in run.key))


def test_a_brand_of_two_letters_is_named_and_a_brand_a_text_hints_at_takes_nothing_away(index):
    assert ask(index, texts=["lg"])[0][:2] == ("LG", None)
    # DVD is a brand, and it is in the name of a model of LG: both are named, the model is found
    assert ask(index, texts=["LG 32 LC 2 RB - ZJ(DVD)"])[0][:2] == ("LG", "32 LC 2 RB - ZJ(DVD)")
    # the model of two brands is found whichever, though the text names a third brand (SAT)
    assert {b for b, m, _ in ask(index, texts=["CI 500 TWN(SAT 1)"])} == {"ORBITECH", "TELESTAR"}
    # but a brand that was *given* does take the models of the others away
    got = index.match("DVD", "CI 500 TWN(SAT 1)", [])
    assert [(c.brand, c.model) for c in got] == [("DVD", None)]


def test_a_run_that_matched_well_hides_what_is_inside_it_and_one_that_only_starts_alike_does_not(index):
    got = ask(index, texts=["PANASONIC NV - FS 200"])
    assert got[0] == ("PANASONIC", "NV - FS 200", 900)
    # the long run 'panasonicnvfs200' is the start-of-a-key match of the model PANASONIC of another
    # brand (912): a prefix, so it does not hide 'nvfs200'
    assert ("WZRD", "PANASONIC") in [(b, m) for b, m, _ in got]


def test_a_part_number_is_a_candidate_with_its_kind_in_the_evidence(index):
    got = index.match(None, None, ["bn59-01199f"])
    assert got[0].model == "BN59-01199F" and "kind:remote" in got[0].evidence
    assert "kind:remote" not in index.match(None, None, ["un50nu6900f"])[0].evidence


def test_brands_are_found_through_hyphens_accents_typos_and_extra_words(index):
    assert ask(index, "harman-kardon", "AVR161")[0][:2] == ("HARMAN KARDON", "AVR 161")
    assert ask(index, "Samsun", "UN50NU6900F")[0][:3] == ("SAMSUNG", "UN50NU6900F", 1000)
    assert ask(index, "Phillips", "42PF9966")[0][:2] == ("PHILIPS", "42PF9966")
    assert ask(index, "Uni", "")[0][:2] == ("Ünï", None)
    assert ask(index, "Ünï", "Ω1000")[0][:2] == ("Ünï", "Ω1000")
    assert ask(index, "Samsung Electronics Co")[0][:3] == ("SAMSUNG", None, 900)
    # a brand that is not one: nothing
    assert ask(index, "Sam") == [] and ask(index, "Zzzzzz") == []


def test_text_with_no_model_in_it_gives_nothing_and_so_does_nothing(index):
    assert ask(index, texts=["Made in China", "Serial 12345678", "Input 100-240V 50/60Hz"]) == []
    assert ask(index, "", "", []) == [] and ask(index) == []


def test_two_brands_in_a_text_are_both_named(index):
    got = index.match(None, None, ["Sony Samsung UN50NU6900F"])
    assert got[0].brand == "SAMSUNG" and got[0].evidence[2] == "brand:text"


def test_the_answer_is_at_most_five_and_best_first_and_a_candidate_is_what_the_contract_says(index):
    got = index.match(None, None, ["UN50NU6900F"], limit=2)
    assert len(got) == 2
    assert [c.score for c in got] == sorted((c.score for c in got), reverse=True)
    top = got[0]
    assert isinstance(top, Candidate) and top.remote_ids == (101,) and isinstance(top.evidence, tuple)
    assert top.score == 0.855 and top.permille == 855
    assert matching.match(index, None, None, ["UN50NU6900F"])[0] == top        # the function form
    assert len(index.match(None, None, ["50pf9966 42pf9966 32pfl3605"], limit=9)) <= 9


def test_a_query_of_many_lines_and_tokens_is_bounded_and_does_not_fail(index):
    lines = [" ".join(f"x{i}y{j}" for j in range(30)) for i in range(40)]
    started = time.perf_counter()
    assert ask(index, "Samsung", "UN50NU6900F", lines)[0][:2] == ("SAMSUNG", "UN50NU6900F")
    assert time.perf_counter() - started < 5
    assert len(index.model_keys("a b c d e f g h", lines)) <= matching.MAX_KEYS


def test_the_same_query_gives_the_same_answer_in_every_process(tmp_path):
    out = []
    for seed in ("0", "1", "42"):
        done = subprocess.run(
            [sys.executable, "-m", "remote_ledger.cli", "bundle", "matching-vectors", "--file",
             str(tmp_path / f"v{seed}.json")],
            cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        out.append((tmp_path / f"v{seed}.json").read_bytes())
    assert out[0] == out[1] == out[2]


# --- the vectors ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vectors():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_the_committed_vectors_are_what_the_matcher_gives():
    """The file does not depend on the repository's data, so it is held to the code exactly."""
    from remote_ledger.serialize import dumps

    assert VECTORS.read_text(encoding="utf-8") == dumps(matching_vectors.build())


def test_the_vectors_state_the_constants_and_the_normalisation(vectors):
    constants = vectors["constants"]
    for name in ("THRESHOLD", "MIN_SCORE", "PREFIX_MIN", "MAX_RUN", "PER_KEY", "LIMIT", "GRAM"):
        assert constants[name] == getattr(matching, name)
    assert constants["LOOKALIKES"] == ["0o", "1i", "1l", "2z", "5s", "8b"]
    assert (constants["THRESHOLD"], constants["MIN_SCORE"], constants["GRAM"]) == (800, 650, 3)
    for case in vectors["tokens"]:
        assert case["tokens"] == tokens(case["text"]) and case["key"] == search_norm(case["text"])
        assert "".join(case["tokens"]) == case["key"]


def test_the_similarity_vectors_agree_with_the_textbook_reference(vectors):
    for case in vectors["similarity"]:
        q, e = case["query"], case["entry"]
        if not q or not e:
            assert case["permille"] == 0
            continue
        if q == e:
            assert case["permille"] == 1000
            continue
        longest = max(len(q), len(e))
        edit = 1000 - -(-int(osa(q, e) * 2) * 500 // longest)
        short, long_ = sorted((q, e), key=len)
        prefix = 800 + 200 * len(short) // len(long_) if len(short) >= 5 and long_.startswith(short) else 0
        assert case["permille"] == max(edit, prefix), case


def test_the_query_vectors_are_the_catalog_answered_and_in_order(vectors):
    index = MatchIndex.from_entries(vectors["catalog"])
    assert vectors["catalog"] == matching_vectors.CATALOG
    assert len(vectors["queries"]) >= 30
    for q in vectors["queries"]:
        got = index.match(q["brand"], q["model"], q["texts"])
        want = q["answer"]
        assert [(c.brand, c.model, c.permille, list(c.evidence), list(c.remote_ids), list(c.models))
                for c in got] == [(a["brand"], a["model"], a["permille"], a["evidence"],
                                   a["remoteIds"], a["models"]) for a in want], q["note"]
        scores = [a["permille"] for a in want]
        assert scores == sorted(scores, reverse=True), q["note"]
        assert len(want) <= matching.LIMIT
        for a in want:
            assert (a["model"] is None) == (a["models"] != [] or a["evidence"][0] == "brand")


def test_the_vectors_cover_each_rule(vectors):
    notes = " | ".join(q["note"] for q in vectors["queries"])
    for fragment in ("suffix", "label", "separators", "letter O", "swapped", "dropped", "brand only",
                     "no BRAVO", "part number", "another brand", "two brands", "typo", "nothing"):
        assert fragment in notes, fragment
    evidence = {w for q in vectors["queries"] for a in q["answer"] for w in a["evidence"]}
    assert evidence >= {"model:exact", "model:prefix", "model:edit", "via:model", "via:text",
                        "brand:given", "brand:text", "brand:none", "kind:remote", "brand"}
    assert {a["permille"] for q in vectors["queries"] for a in q["answer"]} >= {1000, 950, 900, 855}


def test_design_quotes_the_size_of_the_vectors(vectors):
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    section = design[design.index("### D97"):design.index("### D98")]
    for fact in (f"a small catalog of {len(vectors['catalog'])} entries", f"{len(vectors['queries'])} queries",
                 f"keys of {len(vectors['tokens'])} texts", f"similarity of {len(vectors['similarity'])} pairs"):
        assert fact in section, fact


def test_the_command_writes_the_vectors_and_check_compares_them(tmp_path, monkeypatch, capsys):
    target = tmp_path / "m.json"
    monkeypatch.chdir(tmp_path)
    assert cli.main(["bundle", "matching-vectors", "--file", str(target)]) == 0
    assert target.read_bytes() == VECTORS.read_bytes()
    assert cli.main(["bundle", "matching-vectors", "--file", str(target), "--check"]) == 0
    target.write_text(target.read_text().replace("permille", "permil", 1))
    assert cli.main(["bundle", "matching-vectors", "--file", str(target), "--check"]) == 1
    assert "differs from the vectors the matcher gives" in capsys.readouterr().err


# --- over the real selected bundle --------------------------------------------------------------------


def test_a_query_takes_well_under_100_ms_on_the_real_selected_bundle(real_selected):
    """The claim is for the full catalog and is measured by ``rl bundle search-eval --timing``
    (D99); this is the same measurement over the selected bundle, 105,233 models, on whatever
    machine runs the tests, so the limit is generous: the median and the 95th percentile."""
    _, directory = real_selected
    started = time.perf_counter()
    real = MatchIndex.open(directory / build.BUNDLE_FILE)
    assert time.perf_counter() - started < 2                           # opening reads the brands only
    queries = search_eval.generate(real.conn, per_class=15)
    assert len(queries) >= 100
    timings = []
    for q in queries:
        began = time.perf_counter()
        real.match(None, None, [q.text])
        timings.append(time.perf_counter() - began)
    timings.sort()
    assert timings[len(timings) // 2] < 0.05 and timings[int(len(timings) * 0.95)] < 0.1


def test_real_answers_for_three_things_a_person_types(real_selected):
    _, directory = real_selected
    real = MatchIndex.open(directory / build.BUNDLE_FILE)
    top = real.match(None, None, ["samsung un50nu6900f"])
    assert (top[0].brand, top[0].model) == ("SAMSUNG", "UN50NU6900F") and top[0].remote_ids
    assert real.match(None, None, ["samsung"])[0].model is None
    assert real.match("Samsung", "ZX99QQ1234", [])[0].model is None       # an invented model gives the brand
    assert real.match(None, None, ["zq9x7wv5kk3"]) == []
