"""Completing a thin remote from the remotes that share its address (D353 to D355, SPEC R29).

Three layers, as for the fold (``test_bundle_merge.py``):

* the rule on hand-made records (``family.complete``): every line of it, one at a time, so that a
  widened or narrowed rule fails the test that names it;
* the exporter on a small ledger made by the importer itself: the rows of ``remotes``, ``keys``,
  ``remote_refs`` and ``remote_completions``, that nothing but those moved against the same
  ledger built without completing, and ``rl bundle --verify`` on a good bundle and a damaged one;
* the real ledger: what a completion may never change, and the numbers DESIGN D354 quotes.

The reference is not the builder: the expectations are worked out here from the rule as the
design states it.
"""

from __future__ import annotations

import random
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from bundle_corpus import make_corpus
from remote_ledger import parallel
from remote_ledger.bundle import build as bb
from remote_ledger.bundle import catalog, corpus, family, family_eval, merge, writer
from remote_ledger.bundle.corpus import RemoteRecord, pronto_blob
from remote_ledger.bundle.verify import verify_directory
from remote_ledger.keys import load_vocabulary
from test_bundle_verify import refresh

ROOT = Path(__file__).resolve().parent.parent
ORDER = {k.id: n for n, k in enumerate(load_vocabulary().keys)}


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


# --- records made by hand ---------------------------------------------------------------------


def key(canon, signal, confidence=2, name=None):
    """A key of a record: ``(name, label, canonical id, confidence rank, blob)``; the blob is named
    by ``signal`` and is a signal of its own."""
    return (name or f"KEY_{canon or signal}", None, canon, confidence,
            pronto_blob(f"0000 006D 0000 0001 {signal:04X} 0010"))


def rec(n, keys, *, carrier=38000, min_sends=2, empty=False, full=False, tier=2, maker="FAMCO"):
    """A remote file as ``corpus.read_corpus`` gives it (one that is nobody's fragment)."""
    return RemoteRecord(
        where=f"remotes/lirc/{maker.lower()}/R{n:03d}.json", source="lirc", manufacturer=maker, model=f"R{n}",
        aliases=(), controls=(f"{maker} T{n}",), control_pairs=None, protocol="NEC1", carrier_hz=carrier,
        min_sends=min_sends, intro_empty=empty, full_signal=full, tier=tier, keys=tuple(keys), other_candidates=0)


def complete(records):
    return family.complete(records, merge.fold(records), ORDER)


def added(done, i):
    return [k[2] for k in done[i].keys] if i in done else []


#: The signals of a family: its first four are the thin remote's own.
POWER, VOLUP, MUTE, ONE = (key("POWER", 1), key("VOLUME_UP", 2), key("MUTE", 3), key("DIGIT_1", 4))
TWO, THREE, CHUP, CHDOWN = (key("DIGIT_2", 5), key("DIGIT_3", 6), key("CHANNEL_UP", 7), key("CHANNEL_DOWN", 8))
OWN = [POWER, VOLUP, MUTE, ONE]


def with_peers(own, peers, **kw):
    """The thin remote first, then ``peers`` (each a list of keys), one remote each."""
    return [rec(0, own, **kw)] + [rec(n, keys, **kw) for n, keys in enumerate(peers, 1)]


def test_a_thin_remote_gets_the_keys_most_of_its_corroborators_have_in_the_order_of_the_vocabulary():
    peers = [OWN + [TWO, THREE, CHUP, CHDOWN]] * 3 + [OWN + [TWO, THREE]]
    done = complete(with_peers(OWN, peers))
    assert done[0].corroborators == 4
    assert list(done) == [0, 4]            # the peer with six signals is thin too, and takes what the three that have eight share
    # CHANNEL_UP and CHANNEL_DOWN come before the digits in the vocabulary
    assert added(done, 0) == ["CHANNEL_UP", "CHANNEL_DOWN", "DIGIT_2", "DIGIT_3"]
    assert added(done, 0) == sorted(added(done, 0), key=ORDER.__getitem__)
    assert family.UNTESTED == 3                                              # the tier of a reasoned candidate
    for name, label, canon, tier, blob in done[0].keys:
        assert (name, label, tier) == (canon, None, 3)
    assert {k[4] for k in done[0].keys} == {TWO[4], THREE[4], CHUP[4], CHDOWN[4]}


def test_a_remote_that_is_not_thin_is_left_alone():
    many = [key("DIGIT_9", 100 + n, name=f"K{n}") for n in range(family.THIN_BELOW - len(OWN))]
    under, at = OWN + many[:-1], OWN + many
    assert len(under) == family.THIN_BELOW - 1 and len(at) == family.THIN_BELOW
    assert added(complete(with_peers(under, [under + [TWO]] * 3)), 0) == ["DIGIT_2"]      # one under the bound: thin
    assert complete(with_peers(at, [at + [TWO]] * 3)) == {}                                # at the bound: not thin


def test_a_remote_with_too_few_signals_to_be_recognised_by_is_left_alone():
    assert family.MIN_SIGNALS == 3
    assert complete(with_peers(OWN[:2], [OWN + [TWO]] * 3)) == {}
    assert complete(with_peers(OWN[:3], [OWN + [TWO]] * 3)) != {}


def test_two_keys_with_one_signal_count_once():
    twin = key("POWER", 1, name="KEY_POWER2")
    assert complete(with_peers([POWER, twin, VOLUP], [OWN + [TWO]] * 3)) == {}      # three keys, two signals
    assert added(complete(with_peers([POWER, VOLUP, MUTE, twin], [OWN + [TWO]] * 3)), 0) == ["DIGIT_1", "DIGIT_2"]


def test_fewer_than_three_corroborators_leave_the_remote_alone():
    assert family.MIN_CORROBORATORS == 3
    assert complete(with_peers(OWN, [OWN + [TWO]] * 2)) == {}
    assert complete(with_peers(OWN, [OWN + [TWO]] * 3)) != {}


@pytest.mark.parametrize("what, other", [
    ("another carrier", {"carrier": 36000}),
    ("another number of passes", {"min_sends": 3}),
    ("another intro", {"empty": True}),
    ("the whole signal", {"full": True}),
])
def test_a_remote_that_plays_otherwise_does_not_corroborate(what, other):
    """Its keys would be played another way here than where they were found."""
    records = with_peers(OWN, [OWN + [TWO]] * 3)
    odd = [rec(n, OWN + [TWO], **other) for n in range(1, 4)]
    assert merge.play_of(records[0]) != merge.play_of(odd[0]), what
    assert complete([records[0]] + odd) == {}
    assert complete([records[0], *records[1:3], odd[0]]) == {}              # two that play alike are too few


def test_a_remote_must_share_three_quarters_of_the_signals_and_name_them_alike():
    own = OWN + [TWO, THREE, CHUP, CHDOWN]                                  # eight signals: six are needed
    five = [OWN + [TWO]] * 3
    six = [OWN + [TWO, THREE]] * 3
    assert complete(with_peers(own, [p + [key("OK", 20)] for p in five])) == {}
    assert complete(with_peers(own, [p + [key("OK", 20)] for p in six])) != {}
    # each of them agrees on the names of the signals it shares
    renamed = [key("OK", 1), key("MENU", 2), key("EXIT", 3)] + [ONE, TWO, THREE]
    assert complete(with_peers(own, [renamed + [key("OK", 20)]] * 3)) == {}


def test_two_remotes_that_send_one_signal_for_two_functions_do_not_corroborate():
    """The same code a TV and a clone's player use for different keys: the names tell them apart."""
    other = [key("MENU", 1), key("HOME", 2), key("BACK", 3), key("EXIT", 4)]
    assert complete(with_peers(OWN, [other + [TWO]] * 4)) == {}
    # three quarters of the shared signals named alike is enough (5 of 6), two thirds is not (4 of 6)
    own = OWN + [TWO, THREE, CHUP, CHDOWN]
    five_of_six = [POWER, VOLUP, MUTE, ONE, TWO, key("HOME", 6), key("OK", 40)]
    four_of_six = [POWER, VOLUP, MUTE, ONE, key("MENU", 5), key("HOME", 6), key("OK", 40)]
    assert added(complete(with_peers(own, [five_of_six] * 3)), 0) == ["OK"]
    assert complete(with_peers(own, [four_of_six] * 3)) == {}


def test_a_corroborator_needs_two_shared_keys_that_both_name():
    unnamed = [key(None, n) for n in range(1, 5)]
    assert complete(with_peers(unnamed, [unnamed + [TWO]] * 3)) == {}       # nothing to compare names by
    one_named = [key("POWER", 1)] + unnamed[1:]
    assert complete(with_peers(one_named, [one_named + [TWO]] * 3)) == {}


def test_a_signal_is_added_when_two_fifths_of_the_corroborators_have_it_and_at_least_two():
    base = OWN
    five = [base + [TWO]] * 2 + [base] * 3                                  # 2 of 5: ceil(2) = 2
    assert added(complete(with_peers(OWN, five)), 0) == ["DIGIT_2"]
    six = [base + [TWO]] * 2 + [base] * 4                                   # 2 of 6: ceil(2.4) = 3
    assert complete(with_peers(OWN, six)) == {}
    seven = [base + [TWO]] * 3 + [base] * 4                                 # 3 of 7: ceil(2.8) = 3
    assert added(complete(with_peers(OWN, seven)), 0) == ["DIGIT_2"]
    one_of_three = [base + [TWO]] + [base] * 2                              # one is under the floor of two
    assert complete(with_peers(OWN, one_of_three)) == {}


def test_a_guess_is_not_a_vote():
    guessed = key("DIGIT_2", 5, confidence=family.UNTESTED)
    peers = [OWN + [guessed]] * 3 + [OWN + [TWO]]
    assert complete(with_peers(OWN, peers)) == {}                           # one plausible vote of the two it needs
    peers = [OWN + [guessed]] * 3 + [OWN + [TWO]] * 2
    assert added(complete(with_peers(OWN, peers)), 0) == ["DIGIT_2"]


def test_a_key_is_named_by_the_canonical_key_seven_tenths_of_its_namers_agree_on():
    def peers(names):
        return [OWN + [key(c, 5)] for c in names]

    assert added(complete(with_peers(OWN, peers(["DIGIT_2"] * 3))), 0) == ["DIGIT_2"]
    assert added(complete(with_peers(OWN, peers(["DIGIT_2"] * 7 + ["DIGIT_3"] * 3))), 0) == ["DIGIT_2"]      # 7 of 10
    assert complete(with_peers(OWN, peers(["DIGIT_2"] * 6 + ["DIGIT_3"] * 4))) == {}                          # 6 of 10
    assert complete(with_peers(OWN, peers(["DIGIT_2", "DIGIT_3", "DIGIT_4"]))) == {}                          # no two agree
    # a supporter that gives it no name is not a namer, but the signal has to be named at least twice
    assert added(complete(with_peers(OWN, peers(["DIGIT_2"] * 2 + [None] * 3))), 0) == ["DIGIT_2"]
    assert complete(with_peers(OWN, peers(["DIGIT_2"] + [None] * 4))) == {}


def test_a_signal_nobody_names_is_not_added_as_an_unnamed_key():
    assert complete(with_peers(OWN, [OWN + [key(None, 5)]] * 4)) == {}


def test_a_canonical_key_the_remote_has_is_not_added_again_and_one_added_goes_to_one_signal():
    # the remote has DIGIT_2 on another signal: the family's is not added beside it
    own = OWN + [key("DIGIT_2", 90)]
    assert complete(with_peers(own, [OWN + [key("DIGIT_2", 5), key("DIGIT_2", 90)]] * 3)) == {}
    # two signals named DIGIT_2: the better supported one gets it, the other is dropped
    peers = [OWN + [key("DIGIT_2", 5), key("DIGIT_2", 6)]] * 2 + [OWN + [key("DIGIT_2", 5)]] * 2
    done = complete(with_peers(OWN, peers))
    assert added(done, 0) == ["DIGIT_2"] and done[0].keys[0][4] == key("DIGIT_2", 5)[4]


def test_the_most_supported_keys_come_first_and_the_remote_grows_only_to_the_target(monkeypatch):
    # seven peers: DIGIT_2 is in all of them, DIGIT_3 in five, CHANNEL_UP in three (three is enough: ceil(2.8))
    peers = ([OWN + [TWO, THREE, CHUP]] * 3) + [OWN + [TWO, THREE]] * 2 + [OWN + [TWO]] * 2
    assert added(complete(with_peers(OWN, peers)), 0) == ["CHANNEL_UP", "DIGIT_2", "DIGIT_3"]
    monkeypatch.setattr(family, "TARGET_KEYS", len(OWN) + 2)
    assert added(complete(with_peers(OWN, peers)), 0) == ["DIGIT_2", "DIGIT_3"]        # the least supported is the one left out
    monkeypatch.setattr(family, "TARGET_KEYS", len(OWN))
    assert complete(with_peers(OWN, peers)) == {}


def test_the_target_is_the_size_of_the_remote_not_of_what_is_added(monkeypatch):
    monkeypatch.setattr(family, "TARGET_KEYS", 5)
    peers = [OWN + [TWO, THREE, CHUP, CHDOWN]] * 4
    assert len(added(complete(with_peers(OWN, peers)), 0)) == 1


# --- a remote keeps its test key and its alternative ------------------------------------------


def test_a_remote_that_has_a_test_key_is_given_none_that_would_come_before_it():
    peers = [OWN + [key("POWER_OFF", 30), key("POWER_ON", 31), key("DIGIT_2", 5)]] * 4
    # it has Power: nothing is better, and Power off / Power on are worse and may be added
    assert added(complete(with_peers(OWN, peers)), 0) == ["POWER_ON", "POWER_OFF", "DIGIT_2"]
    # it has Volume up and Mute but no Power: the three kinds of power are not added
    own = [VOLUP, MUTE, ONE, TWO]
    peers = [own + [POWER, key("POWER_OFF", 30), THREE]] * 4
    assert added(complete(with_peers(own, peers)), 0) == ["DIGIT_3"]
    # it has Mute but no Volume up: Volume up would come before the alternative it has
    own = [POWER, MUTE, ONE, TWO]
    peers = [own + [VOLUP, THREE]] * 4
    assert added(complete(with_peers(own, peers)), 0) == ["DIGIT_3"]


def test_a_remote_with_no_test_key_may_be_given_one_and_can_then_be_tried():
    own = [ONE, TWO, THREE, CHUP]
    peers = [own + [POWER, VOLUP, MUTE]] * 3
    done = complete(with_peers(own, peers))
    assert added(done, 0) == ["POWER", "VOLUME_UP", "MUTE"]
    assert merge.key_to_try(rec(0, own)) is None
    assert merge.key_to_try(rec(0, list(own) + list(done[0].keys))) is not None


def test_the_test_key_and_the_alternative_of_a_remote_that_had_them_are_what_they_were():
    """The rule a service uses (the backend's ``TestKeyRule``), run before and after, on every
    way of holding some of the five keys."""
    order = list(merge.TEST_KEY_ORDER)
    pick = lambda canons: next((c for c in order if c in canons), None)           # noqa: E731
    alt = lambda canons, test: next((c for c in family.ALTERNATIVE_ORDER if c in canons and c != test), None)   # noqa: E731
    rng = random.Random(5)
    gave = 0
    for _ in range(80):
        have = rng.sample(order, rng.randint(1, 5))
        own = [key(c, 100 + n) for n, c in enumerate(have)] + [ONE, TWO, THREE]
        every = [key(c, 200 + n) for n, c in enumerate(order)]
        done = complete(with_peers(own, [own + every + [CHUP]] * 3))
        after = {k[2] for k in own} | {c for c in added(done, 0)}
        assert pick(after) == pick(have), have
        if alt(have, pick(have)) is not None:           # the alternative it had is the one it has
            assert alt(after, pick(after)) == alt(have, pick(have)), have
        else:                                           # one it had none of may be given one, never a worse test key
            gave += alt(after, pick(after)) is not None
    assert gave, "the cases have a remote that is given an alternative"


# --- reading, order and the files -------------------------------------------------------------


def test_the_result_does_not_depend_on_the_order_of_the_files():
    peers = [OWN + [TWO, THREE, CHUP]] * 3 + [OWN + [TWO, THREE]] * 2 + [OWN + [TWO]] * 2
    records = with_peers(OWN, peers)
    reference = {records[i].where: [k[4] for k in d.keys] for i, d in complete(records).items()}
    rng = random.Random(9)
    for _ in range(5):
        shuffled = records[:]
        rng.shuffle(shuffled)
        got = {shuffled[i].where: [k[4] for k in d.keys] for i, d in complete(shuffled).items()}
        assert got == reference


def test_the_fragments_folded_into_a_remote_count_as_its_own_keys():
    def frag(protocol, keys, number=7):
        return RemoteRecord(
            where=f"remotes/irblaster/SONY/{number}-{protocol}.json", source="irblaster", manufacturer="SONY",
            model=f"IR Blaster DB ({protocol})", aliases=(), controls=("SONY | KD-1",),
            control_pairs=(("SONY", "KD-1"),), protocol=protocol, carrier_hz=38000, min_sends=2,
            intro_empty=False, full_signal=False, tier=2, keys=tuple(keys), other_candidates=0)

    records = [frag("Sony12", [POWER, VOLUP]), frag("Sony15", [ONE, THREE])] + [rec(n, OWN + [TWO]) for n in range(1, 4)]
    folded = merge.fold(records)
    assert folded.carrier[1] == 0                       # Sony15 has no test key: it is folded into Sony12
    done = family.complete(records, folded, ORDER)
    # the remote is Sony12 with Sony15's keys: four signals, three of them the peers' (the fold's two keys add none of
    # their MUTE or DIGIT_2), and a fragment is nobody's completion of its own
    assert added(done, 0) == ["MUTE", "DIGIT_2"] and 1 not in done


# --- the exporter, on a small ledger made by the importer -------------------------------------

#: NEC codes: ``00FF`` and a function byte with its complement.
def nec(function: int) -> str:
    return f"00FF{function:02X}{function ^ 0xFF:02X}"


#: db id -> a thin remote and four that share its address.
FAMILY = [("POWER", nec(1)), ("VOL+", nec(2)), ("MUTE", nec(3)), ("1", nec(4))]
EXTRA = {"2": nec(5), "3": nec(6), "CH+": nec(7), "CH-": nec(8), "OK": nec(9), "VOL-": nec(10)}
DB = {
    301: {"models": [("FAMCO", "FX-1")], "keys": [(label, code, "NEC") for label, code in FAMILY]},
    302: {"models": [("FAMCO", "FX-2")],
          "keys": [(label, code, "NEC") for label, code in FAMILY + [(k, EXTRA[k]) for k in ("2", "3", "CH+", "CH-", "OK")]]},
    303: {"models": [("FAMCO", "FX-3")],
          "keys": [(label, code, "NEC") for label, code in FAMILY + [(k, EXTRA[k]) for k in ("2", "3", "CH+", "CH-")]]},
    304: {"models": [("FAMCO", "FX-4")],
          "keys": [(label, code, "NEC") for label, code in FAMILY + [(k, EXTRA[k]) for k in ("2", "3", "CH+")]]},
    305: {"models": [("FAMCO", "FX-5")],
          "keys": [(label, code, "NEC") for label, code in FAMILY + [(k, EXTRA[k]) for k in ("2", "3", "VOL-")]]},
}
THIN = "irblaster/FAMCO/301-NEC1"


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    return make_corpus(tmp_path_factory.mktemp("family") / "ledger", DB)


@pytest.fixture(scope="module")
def full(ledger):
    built = bb.build_bundle(ledger, "full", aliases=())
    assert built.problems == []
    return built


@pytest.fixture(scope="module")
def plain(ledger):
    """The same ledger built with completing switched off."""
    patch = pytest.MonkeyPatch()
    patch.setattr(catalog, "complete", lambda records, folded, order: {})
    try:
        built = bb.build_bundle(ledger, "full", aliases=())
    finally:
        patch.undo()
    assert built.problems == []
    return built


def conn_of(built: bb.Bundle) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.deserialize(built.files[bb.BUNDLE_FILE])
    return conn


def rows(built: bb.Bundle, sql: str, *args):
    return conn_of(built).execute(sql, args).fetchall()


def test_the_ledger_of_the_cases_is_what_the_cases_say(ledger):
    records, problems = corpus.read_corpus(ledger)
    assert problems == []
    by_ref = {merge.ref_of(r): r for r in records}
    assert sorted(r for r in by_ref if r.startswith("irblaster/FAMCO/")) == [f"irblaster/FAMCO/{n}-NEC1" for n in range(301, 306)]
    thin = by_ref[THIN]
    assert sorted(k[2] for k in thin.keys) == ["DIGIT_1", "MUTE", "POWER", "VOLUME_UP"]
    for number in range(302, 306):
        peer = by_ref[f"irblaster/FAMCO/{number}-NEC1"]
        assert merge.play_of(peer) == merge.play_of(thin) and len(peer.keys) >= 7


def test_the_rule_completes_the_remotes_it_says_and_no_others(ledger, full, plain):
    """Worked out by hand from the rule. 301 has four signals and four corroborators (302 to 305
    share all four) and takes the keys that two fifths of them, two, have: 2, 3, CH+ and CH-. The
    others are thin too (under 30 keys): 304 is corroborated by 302, 303 and 305 and takes CH-
    (in two of three); 305 by 302, 303 and 304 and takes CH+ and CH-; 303 is corroborated by three
    and has nothing to add (OK and VOL- are in one remote each); 302 shares seven of its nine
    signals with only two."""
    done = {ref: (first, count, corr) for ref, first, count, corr in rows(
        full, "SELECT r.ref, c.first_n, c.key_count, c.corroborators FROM remote_completions c "
              "JOIN remotes r ON r.id = c.remote_id")}
    assert done == {THIN: (4, 4, 4), "irblaster/FAMCO/304-NEC1": (7, 1, 3), "irblaster/FAMCO/305-NEC1": (7, 2, 3)}
    assert full.stats["completedRemotes"] == 3 and full.stats["completionKeys"] == 7
    assert plain.stats["completedRemotes"] == 0 and rows(plain, "SELECT COUNT(*) FROM remote_completions") == [(0,)]


def test_the_added_keys_follow_the_files_keys_and_are_untested_and_named(ledger, full):
    rid = rows(full, "SELECT id FROM remotes WHERE ref = ?", THIN)[0][0]
    keys = rows(full, "SELECT k.n, v.key, k.label, k.confidence FROM keys k LEFT JOIN vocab_keys v ON v.id = k.canon "
                      "WHERE k.remote_id = ? ORDER BY k.n", rid)
    assert [n for n, *_ in keys] == list(range(8))
    assert sorted(c for _, c, *_ in keys[:4]) == ["DIGIT_1", "MUTE", "POWER", "VOLUME_UP"]
    assert [c for _, c, _, _ in keys[4:]] == ["CHANNEL_UP", "CHANNEL_DOWN", "DIGIT_2", "DIGIT_3"]       # vocabulary order
    assert {(label, conf) for _, _, label, conf in keys[4:]} == {(None, 3)}
    assert {conf for _, _, _, conf in keys[:4]} == {2}


def test_nothing_but_the_completed_remotes_and_their_tables_moved(ledger, full, plain):
    def table(built, sql):
        return rows(built, sql)

    for name in ("brands", "models", "controls", "brand_aliases", "remote_refs", "signals", "ngram", "sources", "vocab_keys"):
        a, b = table(full, f"SELECT * FROM {name} ORDER BY 1, 2"), table(plain, f"SELECT * FROM {name} ORDER BY 1, 2")
        assert a == b, name
    done = {rid: (first, count) for rid, first, count in rows(full, "SELECT remote_id, first_n, key_count FROM remote_completions")}
    moved = rows(full, "SELECT id, tier, key_count FROM remotes ORDER BY id")
    was = rows(plain, "SELECT id, tier, key_count FROM remotes ORDER BY id")
    assert [m[0] for m in moved] == [w[0] for w in was]
    for (rid, tier, kc), (_, tier0, kc0) in zip(moved, was):
        assert tier == tier0                                          # the tier is the weakest of the files' keys
        assert kc == kc0 + done.get(rid, (0, 0))[1]
    keys_a = rows(full, "SELECT remote_id, n, canon, label, signal_id, confidence FROM keys ORDER BY 1, 2")
    keys_b = set(rows(plain, "SELECT remote_id, n, canon, label, signal_id, confidence FROM keys"))
    assert {k for k in keys_a if k[1] < done.get(k[0], (10**9, 0))[0]} == keys_b
    assert rows(full, "SELECT COUNT(*) FROM signals WHERE id NOT IN (SELECT signal_id FROM keys)") == [(0,)]


def test_a_source_is_credited_with_its_own_keys_only(ledger, full, plain):
    assert rows(full, "SELECT name, remote_count, key_count FROM sources ORDER BY id") == rows(
        plain, "SELECT name, remote_count, key_count FROM sources ORDER BY id")
    assert full.stats["keys"] == plain.stats["keys"] + full.stats["completionKeys"]


def test_the_refs_cover_the_files_keys_and_the_completion_is_beyond_them(ledger, full):
    rid, count = rows(full, "SELECT id, key_count FROM remotes WHERE ref = ?", THIN)[0]
    assert rows(full, "SELECT first_n, key_count FROM remote_refs WHERE ref = ?", THIN) == [(0, 4)] and count == 8
    assert rows(full, "SELECT COUNT(*) FROM remote_refs WHERE remote_id = ?", rid) == [(1,)]


def test_the_meta_and_the_manifest_state_the_rule_and_the_count(ledger, full):
    import json

    meta = dict(rows(full, "SELECT key, value FROM meta"))
    assert meta["completionRule"] == writer.COMPLETION_RULE_TEXT and "remote_completions" in meta["completionRule"]
    assert meta["count.remoteCompletions"] == str(full.stats["completedRemotes"]) and meta["schemaVersion"] == "1"
    assert json.loads(full.files[bb.MANIFEST_FILE])["counts"]["remoteCompletions"] == full.stats["completedRemotes"]
    assert ("remote_completions", "remote_id") in writer.DIGEST_TABLES
    assert [c[1] for c in conn_of(full).execute("PRAGMA table_info(remote_completions)")] == [
        "remote_id", "first_n", "key_count", "corroborators"]


def test_the_data_version_moves_with_the_completion_and_a_build_is_repeatable(ledger, full, plain):
    again = bb.build_bundle(ledger, "full", aliases=())
    assert again.stats["dataVersion"] == full.stats["dataVersion"] != plain.stats["dataVersion"]
    assert again.files[bb.BUNDLE_FILE] == full.files[bb.BUNDLE_FILE]


# --- rl bundle --verify ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def written(ledger, full, tmp_path_factory):
    directory = tmp_path_factory.mktemp("family-bundle")
    bb.write_bundle(full, directory, ledger)
    return directory


def damaged(written, tmp_path, *statements, update=True):
    directory = tmp_path / "bundle"
    shutil.copytree(written, directory)
    conn = sqlite3.connect(directory / bb.BUNDLE_FILE, isolation_level=None)
    for sql in statements:
        conn.execute(sql)
    conn.close()
    if update:
        refresh(directory)
    return directory


def problems_of(ledger, directory):
    return verify_directory(ledger, directory, aliases=())[0]


def test_a_good_bundle_verifies(ledger, written):
    problems, facts = verify_directory(ledger, written, aliases=())
    assert problems == [] and facts["remotes"] == 9


RID = "(SELECT id FROM remotes WHERE ref = 'irblaster/FAMCO/301-NEC1')"
MUTATIONS = [
    ("an added key that is not untested", f"UPDATE keys SET confidence = 2 WHERE remote_id = {RID} AND n = 4",
     ["added keys that are not untested or not named", "is not the file's"]),
    ("an added key with no name", f"UPDATE keys SET canon = NULL WHERE remote_id = {RID} AND n = 4",
     ["added keys that are not untested or not named"]),
    ("an added key that is a text", f"UPDATE keys SET label = 'x' WHERE remote_id = {RID} AND n = 4",
     ["added keys that are not untested or not named"]),
    ("a canonical key added twice",
     f"UPDATE keys SET canon = (SELECT canon FROM keys WHERE remote_id = {RID} AND n = 4) WHERE remote_id = {RID} AND n = 5",
     ["canonical keys added twice, or added to a remote that has the key"]),
    ("a canonical key added to a remote that has it",
     f"UPDATE keys SET canon = (SELECT canon FROM keys WHERE remote_id = {RID} AND n = 0) WHERE remote_id = {RID} AND n = 4",
     ["canonical keys added twice, or added to a remote that has the key"]),
    ("a completion row missing", f"DELETE FROM remote_completions WHERE remote_id = {RID}",
     ["remote_refs: the refs of remote"]),                 # the keys the refs do not cover are no longer the completion's
    ("a completion row for a remote that has none",
     f"INSERT INTO remote_completions VALUES ((SELECT id FROM remotes WHERE ref = 'irblaster/FAMCO/302-NEC1'), 9, 1, 3)",
     ["rows of remote_completions that do not end at the last key of their remote"]),
    ("a completion that starts in the wrong place",
     f"UPDATE remote_completions SET first_n = 3, key_count = 5 WHERE remote_id = {RID}",
     ["rows of added keys that are not untested or not named", "remote_refs: the refs of remote"]),
    ("a completion that names the wrong number of corroborators",
     f"UPDATE remote_completions SET corroborators = 9 WHERE remote_id = {RID}",
     ["remote_completions has 3 rows and the tree gives 3; 2 differ"]),
    ("a completion of a remote that is gone",
     f"UPDATE remote_completions SET remote_id = 9999 WHERE remote_id = {RID}",
     ["rows of remote_completions.remote_id are wrong or dangling"]),
    ("a key added that the tree does not give",
     f"UPDATE keys SET signal_id = (SELECT id FROM signals WHERE id != (SELECT signal_id FROM keys WHERE remote_id = {RID} "
     f"AND n = 4) ORDER BY id LIMIT 1) WHERE remote_id = {RID} AND n = 4", ["key 4"]),
]


@pytest.mark.parametrize("what, statement, expected", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_a_bundle_damaged_in_one_way_is_refused(ledger, written, tmp_path, what, statement, expected):
    problems = problems_of(ledger, damaged(written, tmp_path, statement))
    text = "\n".join(problems)
    assert problems, what
    assert any(e in text for e in expected), (what, problems)


def test_a_bundle_written_before_the_completion_is_told_to_be_built_again(ledger, written, tmp_path):
    directory = damaged(written, tmp_path, "DROP TABLE remote_completions", update=False)
    assert any("the bundle has no remote_completions table" in p for p in problems_of(ledger, directory))


def test_the_counts_of_the_meta_and_of_the_manifest_are_the_tables(ledger, written, tmp_path):
    directory = damaged(written, tmp_path, "UPDATE meta SET value = '99' WHERE key = 'count.remoteCompletions'", update=False)
    assert any("remote_completions has 3 rows, meta count.remoteCompletions says 99" in p for p in problems_of(ledger, directory))


# --- the real ledger ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real(real_records):
    collected = catalog.collect(real_records)
    return collected, collected.completions


def test_no_completion_of_the_real_ledger_touches_a_test_key_a_tier_or_a_name(real_records, real):
    collected, done = real
    assert done, "the real ledger has thin remotes with a family"
    pick = lambda canons: next((c for c in merge.TEST_KEY_ORDER if c in canons), None)           # noqa: E731
    alt = lambda canons, test: next((c for c in family.ALTERNATIVE_ORDER if c in canons and c != test), None)   # noqa: E731
    for i, completion in done.items():
        remote = collected.folded.remote(real_records, i)
        own = {k[2] for k in remote.keys if k[2] is not None}
        new = [k[2] for k in completion.keys]
        every = own | set(new)
        assert len(remote.keys) < family.THIN_BELOW and len(remote.keys) + len(new) <= family.TARGET_KEYS or len(remote.keys) >= family.TARGET_KEYS
        assert len(new) == len(set(new)) and not set(new) & own and new == sorted(new, key=ORDER.__getitem__), remote.where
        assert {k[3] for k in completion.keys} == {family.UNTESTED} and {k[1] for k in completion.keys} == {None}
        assert {k[4] for k in completion.keys}.isdisjoint({k[4] for k in remote.keys}), remote.where
        assert completion.corroborators >= family.MIN_CORROBORATORS
        if pick(own) is not None:                                       # a remote that had a test key keeps it
            assert pick(every) == pick(own), remote.where
            if alt(own, pick(own)) is not None:                         # and the alternative it had
                assert alt(every, pick(every)) == alt(own, pick(own)), remote.where


def test_a_completion_reads_only_keys_that_a_corroborator_holds_at_the_tier_of_evidence(real_records, real):
    collected, done = real
    held: dict[bytes, set[int]] = {}
    for j, remote in ((j, collected.folded.remote(real_records, j)) for j in range(len(real_records)) if collected.folded.carrier[j] == j):
        for k in remote.keys:
            if k[3] <= family.VOTES_FROM:
                held.setdefault(k[4], set()).add(j)
    for i, completion in list(done.items())[:300]:
        for k in completion.keys:
            assert len(held.get(k[4], ())) >= family.MIN_SUPPORT, (real_records[i].where, k[2])


def test_design_quotes_the_numbers_of_the_completion(real_records, real):
    """DESIGN D354 states what the rule does to the real ledger; recomputed here from the records,
    so the text cannot go stale and the owner who widens the rule reads what it would add."""
    collected, done = real
    section = _design_section()
    carriers = [i for i in range(len(real_records)) if collected.folded.carrier[i] == i]
    thin = [i for i in carriers if len(collected.folded.remote(real_records, i).keys) < family.THIN_BELOW]
    keys = sum(len(d.keys) for d in done.values())
    funnel = Counter()
    family.complete(real_records, collected.folded, ORDER, funnel)
    for fact in (f"{len(thin):,} thin remotes", f"{len(done):,} are completed", f"{keys:,} keys",
                 f"{funnel['corroborators: 0']:,} have no corroborator",
                 f"{funnel['fewer than MIN_SIGNALS signals']:,} have fewer than three signals"):
        assert fact in section, f"DESIGN D354 no longer says {fact!r}"


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / max(whole, 1):.0f}%"


def test_design_quotes_the_leave_one_out_figures(real_records, real):
    collected, _ = real
    section = _design_section()
    c = family_eval.evaluate(real_records, collected.folded, ORDER)
    assert c["stubs"] == 357, "the sample of the evaluation moved: the figures of D354 are to be measured again"
    for fact in (
            f"| stubs cut from remotes held in full | {c['stubs']:,} |",
            f"| completed | {c['completed']:,} ({_pct(c['completed'], c['stubs'])}) |",
            f"| keys added | {c['keys added']:,}, {c['keys added'] / c['completed']:.1f} a completed stub |",
            f"| added keys the remote has (an upper bound of precision) | {c['in the remote']:,} ({_pct(c['in the remote'], c['keys added'])}) |",
            f"| named right, where the remote names them | {c['named right']:,} of {c['named by the remote']:,} "
            f"({_pct(c['named right'], c['named by the remote'])}) |",
            f"| canonical keys of the remotes that the stub lacks, found and named right | {c['named right']:,} of "
            f"{c['canonical keys to find']:,} ({_pct(c['named right'], c['canonical keys to find'])}; "
            f"{_pct(c['named right'], c['canonical keys to find in a completed stub'])} of those of the stubs that were completed) |"):
        assert fact in section, f"DESIGN D354 no longer says {fact!r}"


#: The rows of the table "what each line is worth" in D354: the first column as the text has it, and
#: the constants of ``family`` that one row changes.
LINES = [
    ("the rule", {}),
    ("no share of the corroborators (any two supporters)", {"SUPPORT": (0, 1)}),
    ("names by six tenths and not seven", {"NAME_SHARE": (3, 5)}),
    ("no agreement on names (line 2)", {"AGREEING": (0, 1), "MIN_AGREEING": 0}),
    ("half of the signals shared and not three quarters", {"SHARED": (1, 2)}),
    ("two corroborators and not three", {"MIN_CORROBORATORS": 2}),
]


@pytest.mark.parametrize("name, patch", LINES, ids=[n for n, _ in LINES])
def test_design_quotes_what_each_line_is_worth(real_records, real, monkeypatch, name, patch):
    collected, _ = real
    for key_, value in patch.items():
        monkeypatch.setattr(family, key_, value)
    c = family_eval.evaluate(real_records, collected.folded, ORDER)
    row = (f"| {name} | {_pct(c['completed'], c['stubs'])} | {c['keys added'] / c['completed']:.1f} | "
           f"{_pct(c['in the remote'], c['keys added'])} | {_pct(c['named right'], c['named by the remote'])} | "
           f"{_pct(c['named right'], c['canonical keys to find in a completed stub'])} |")
    assert row in _design_section(), f"DESIGN D354 no longer says {row!r}"


def test_the_two_numbers_d354_gives_for_a_floor_of_two_and_for_the_bounds_of_thin(real_records, real, monkeypatch):
    collected, done = real
    section = _design_section()
    monkeypatch.setattr(family, "MIN_CORROBORATORS", 2)
    two = family.complete(real_records, collected.folded, ORDER)
    monkeypatch.setattr(family, "MIN_CORROBORATORS", 3)
    assert f"completes {len(two):,} and not {len(done):,} ({sum(len(d.keys) for d in two.values()):,} keys and not " \
           f"{sum(len(d.keys) for d in done.values()):,})" in section
    assert f"adds {sum(len(d.keys) for d in two.values()) - sum(len(d.keys) for d in done.values()):,} keys" in section
    for bound, thin_text in ((41, "makes {thin:,} remotes thin and completes {n:,} with {k:,} keys"),
                             (51, "{thin:,} are thin and {n:,} are completed with {k:,} keys")):
        monkeypatch.setattr(family, "THIN_BELOW", bound)
        funnel = Counter()
        wide = family.complete(real_records, collected.folded, ORDER, funnel)
        assert thin_text.format(thin=funnel["thin"], n=len(wide), k=sum(len(d.keys) for d in wide.values())) in section
    monkeypatch.setattr(family, "THIN_BELOW", 30)


def _design_section() -> str:
    text = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    return text[text.index("### D354"):text.index("**To widen the rule**: `THIN_BELOW`")]
