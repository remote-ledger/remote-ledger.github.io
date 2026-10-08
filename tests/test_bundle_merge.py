"""Folding the protocol fragments of a device into one remote (D103 to D105, SPEC R27).

Three layers:

* the rule on hand-made records (``merge.fold``): every branch of it, one at a time, so that a
  widened or narrowed rule fails the test that names it;
* the exporter on a small ledger made by the importer itself, where each device is a case of the
  rule: the rows of ``remotes``, ``keys``, ``controls`` and ``remote_refs``, ``dataVersion``,
  ``rl bundle --verify`` and the matcher over the result, and that nothing but those tables moved
  against the same ledger built with no folding;
* the real ledger: every ref maps to one remote, no key is lost, and the numbers DESIGN D103
  quotes are the data's (``test_design_quotes_the_numbers_of_the_measurement``).

The reference is not the builder: the expectations are worked out here from the rule as the
design states it, and the keys of a folded fragment are read back from the compile stage's own
Pronto strings.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import pytest

from bundle_corpus import floor_of_the_small_ledgers, make_corpus
from remote_ledger import cli, parallel
from remote_ledger.bundle import build as bb
from remote_ledger.bundle import catalog, corpus, merge, writer
from remote_ledger.bundle.corpus import RemoteRecord, pronto_blob
from remote_ledger.bundle.verify import verify_directory
from remote_ledger.cli import compiled_artifact
from remote_ledger.matching import MatchIndex
from remote_ledger.remote import load_remote
from test_bundle_verify import refresh

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


# --- records made by hand ---------------------------------------------------------------------


def key(name, canon, signal, confidence=2):
    """A key of a record: ``(name, label, canonical id, confidence rank, blob)``; the blob is
    named by ``signal`` and is a signal of its own."""
    return (name, None, canon, confidence, pronto_blob(f"0000 006D 0000 0001 00{signal:02X} 0010"))


def rec(ref, keys, *, protocol="Sony12", carrier=40000, min_sends=3, empty=True, full=False, tier=2,
        maker="SONY", pairs=(("SONY", "KD-1"),), source="irblaster", other=0, aliases=()):
    """A remote file as ``corpus.read_corpus`` gives it."""
    return RemoteRecord(
        where=f"remotes/{ref}.json", source=source, manufacturer=maker, model=f"IR Blaster DB ({protocol})",
        aliases=aliases, controls=tuple(f"{b} | {m}" for b, m in pairs),
        control_pairs=tuple(pairs) if source == "irblaster" else None, protocol=protocol,
        carrier_hz=carrier, min_sends=min_sends, intro_empty=empty, full_signal=full, tier=tier,
        keys=tuple(keys), other_candidates=other)


def frag(number, protocol, keys, **kw):
    return rec(f"irblaster/SONY/{number}-{protocol}", keys, protocol=protocol, **kw)


POWER = key("KEY_POWER", "POWER", 1)
ONE = key("KEY_1", "DIGIT_1", 2)
TWO = key("KEY_2", "DIGIT_2", 3)
THREE = key("KEY_3", "DIGIT_3", 4)


def carriers(records):
    return list(merge.fold(records).carrier)


# --- the rule: a fragment with no test key goes to a sibling that has one ---------------------


def test_a_fragment_with_no_test_key_is_folded_into_a_sibling_that_has_one():
    records = [frag(7, "Sony12", [POWER]), frag(7, "Sony15", [ONE, TWO])]
    folded = merge.fold(records)
    assert folded.carrier == (0, 0) and folded.parts == {0: (1,)} and folded.folded == 1


def test_a_device_with_one_fragment_is_left_alone():
    assert carriers([frag(7, "Sony12", [ONE])]) == [0]
    assert carriers([frag(7, "Sony12", [POWER])]) == [0]


def test_a_fragment_stays_when_no_sibling_has_a_test_key():
    assert carriers([frag(7, "Sony12", [ONE]), frag(7, "Sony15", [TWO])]) == [0, 1]
    # two with none and a third that is not a fragment of this device
    assert carriers([frag(7, "Sony12", [ONE]), frag(7, "Sony15", [TWO]), frag(8, "Sony12", [POWER])]) == [0, 1, 2]


def test_a_sibling_with_another_carrier_is_not_a_target():
    assert carriers([frag(7, "Sony12", [POWER], carrier=36000), frag(7, "Sony15", [ONE])]) == [0, 1]
    assert carriers([frag(7, "Sony12", [POWER], carrier=40000), frag(7, "Sony15", [ONE], carrier=40001)]) == [0, 1]


@pytest.mark.parametrize("what, target, folded", [
    ("minSends: both repeat and helper passes differ", {"min_sends": 2}, {}),
    ("the intro alone: the same passes, the intro empty or not",
     {"min_sends": 4, "empty": False}, {"min_sends": 3, "empty": True}),
    ("the rule alone: the same numbers, one plays the whole signal",
     {"full": True}, {}),
])
def test_a_sibling_with_another_play_rule_is_not_a_target(what, target, folded):
    first = frag(7, "Sony12", [POWER], **target)
    second = frag(7, "Sony15", [ONE], **folded)
    a, b = merge.play_of(first), merge.play_of(second)
    assert a != b and a[0] == b[0], f"{what}: the case does not differ in play only"
    assert carriers([first, second]) == [0, 1]
    # the same play is what makes it a target
    assert carriers([first, frag(7, "Sony15", [ONE], **target)]) == [0, 0]


def test_each_of_the_five_fields_of_a_play_rule_matters():
    """The carrier and D78's four numbers: each moved alone makes the siblings play differently,
    which is what ``play_of`` compares (the three cases of the test above stand for the last four)."""
    base = frag(7, "Sony12", [POWER])
    other = {"carrier": 40001, "min_sends": 4, "empty": False, "full": True}
    assert len({merge.play_of(base)} | {merge.play_of(replace(base, **{
        {"carrier": "carrier_hz", "min_sends": "min_sends", "empty": "intro_empty",
         "full": "full_signal"}[k]: v})) for k, v in other.items()}) == 5


def test_two_fragments_that_both_have_a_test_key_stay_two_remotes():
    """They may be two encodings of one key (a Power in Sony12 and another in Sony15) and the
    device may answer to only one, so each is a row a person can try, even with one carrier
    and one play rule."""
    a, b = frag(7, "Sony12", [POWER]), frag(7, "Sony15", [key("KEY_POWER", "POWER", 9)])
    assert merge.play_of(a) == merge.play_of(b)
    assert carriers([a, b]) == [0, 1]
    # not even when their test keys are different keys
    assert carriers([frag(7, "Sony12", [POWER]), frag(7, "Sony15", [key("KEY_MUTE", "MUTE", 9)])]) == [0, 1]
    # and the one that has none still goes into one of them
    assert carriers([a, b, frag(7, "Sony20", [ONE])]) == [0, 1, 0]


def test_an_absorbed_fragment_is_never_a_target_and_nothing_chains():
    records = [frag(7, "Sony12", [POWER]), frag(7, "Sony15", [ONE]), frag(7, "Sony20", [TWO])]
    folded = merge.fold(records)
    assert folded.carrier == (0, 0, 0) and folded.parts == {0: (1, 2)}
    assert all(folded.carrier[c] == c for c in folded.carrier)


@pytest.mark.parametrize("better, worse", [
    (a, b) for i, a in enumerate(merge.TEST_KEY_ORDER) for b in merge.TEST_KEY_ORDER[i + 1:]])
def test_with_several_siblings_the_best_test_key_wins_even_when_it_is_not_the_lowest_id(better, worse):
    records = [frag(7, "Sony12", [key("KEY_X", worse, 5)]), frag(7, "Sony15", [ONE]),
               frag(7, "Sony20", [key("KEY_Y", better, 6)])]
    assert carriers(records) == [0, 2, 2], (better, worse)
    # the order of the files is the order of the ids: swap them and the same one wins
    swapped = [records[2], records[1], records[0]]
    assert carriers(swapped) == [0, 0, 2]


def test_with_several_siblings_of_one_test_key_the_lowest_id_wins():
    records = [frag(7, "Sony12", [POWER]), frag(7, "Sony15", [ONE]), frag(7, "Sony20", [key("KEY_POWER", "POWER", 8)])]
    assert carriers(records) == [0, 0, 2]
    # the ranking is by the test key's place in the order, not by its key number
    records = [frag(7, "Sony12", [ONE, TWO, POWER]), frag(7, "Sony15", [THREE]), frag(7, "Sony20", [key("KEY_POWER", "POWER", 8)])]
    assert merge.key_to_try(records[0]) == (0, 2) and merge.key_to_try(records[2]) == (0, 0)
    assert carriers(records) == [0, 0, 2]


def test_a_sibling_that_cannot_take_it_is_passed_over_for_one_that_can():
    records = [frag(7, "Sony12", [POWER], carrier=36000), frag(7, "Sony15", [ONE]),
               frag(7, "Sony20", [key("KEY_VOL_PLUS", "VOLUME_UP", 6)])]
    assert carriers(records) == [0, 2, 2]          # the Power remote is at another carrier


def test_the_test_key_is_the_first_of_the_order_and_the_lowest_key_number_of_it():
    keys = [key(f"KEY_{n}", c, 10 + n) for n, c in enumerate(["DIGIT_1", "MUTE", None, "VOLUME_UP", "POWER_ON", "POWER", "POWER"])]
    assert merge.key_to_try(frag(7, "Sony12", keys)) == (0, 5)              # POWER at 5 and 6: the lowest
    assert merge.key_to_try(frag(7, "Sony12", keys[:5])) == (2, 4)          # no POWER or POWER_OFF
    assert merge.key_to_try(frag(7, "Sony12", keys[:4])) == (3, 3)
    assert merge.key_to_try(frag(7, "Sony12", keys[:2])) == (4, 1)
    assert merge.key_to_try(frag(7, "Sony12", [keys[0], keys[2]])) is None
    assert merge.TEST_KEY_ORDER == ("POWER", "POWER_OFF", "POWER_ON", "VOLUME_UP", "MUTE")


# --- what is never a sibling ------------------------------------------------------------------


def test_fragments_of_two_makers_or_two_product_lists_are_not_one_device():
    keep = [frag(7, "Sony12", [POWER]), frag(7, "Sony15", [ONE], maker="SONY ", pairs=(("SONY", "KD-1"),))]
    assert carriers(keep) == [0, 1]
    assert carriers([frag(7, "Sony12", [POWER]), frag(7, "Sony15", [ONE], pairs=(("SONY", "KD-2"),))]) == [0, 1]
    assert carriers([frag(7, "Sony12", [POWER]), frag(7, "Sony15", [ONE], pairs=(("SONY", "KD-1"), ("SONY", "KD-2")))]) == [0, 1]
    assert carriers([frag(7, "Sony12", [POWER]), frag(7, "Sony15", [ONE], aliases=("x",))]) == [0, 1]
    # the same product list in another order is the same list
    both = (("SONY", "KD-1"), ("SONY", "KD-2"))
    assert carriers([frag(7, "Sony12", [POWER], pairs=both), frag(7, "Sony15", [ONE], pairs=both[::-1])]) == [0, 0]


def test_the_same_number_in_two_folders_is_two_devices():
    records = [rec("irblaster/SONY/7-Sony12", [POWER]), rec("irblaster/AIWA/7-Sony15", [ONE], protocol="Sony15")]
    assert carriers(records) == [0, 1]
    assert carriers([rec("irblaster/SONY/7-Sony12", [POWER]), rec("irblaster/SONY/70-Sony15", [ONE], protocol="Sony15")]) == [0, 1]


@pytest.mark.parametrize("source, base", [("lirc", "lirc/sony"), ("smartir", "smartir/Sony"), ("authored", "sony")])
def test_a_file_of_another_source_is_never_a_fragment_whatever_it_is_called(source, base):
    records = [rec(f"{base}/7-Sony12", [POWER], source=source), rec(f"{base}/7-Sony15", [ONE], protocol="Sony15", source=source)]
    assert carriers(records) == [0, 1]
    assert merge.device_of(records[0]) is None


def test_only_a_file_named_number_and_protocol_is_a_fragment():
    for name in ("RC-15", "Sony12", "7", "7-", "x7-Sony12"):
        assert merge.device_of(rec(f"irblaster/SONY/{name}", [POWER])) is None, name
    assert merge.device_of(frag(7, "Sony12", [POWER]))[0] == "irblaster/SONY/7"
    assert merge.device_of(frag(7, "Pioneer-2Part", [POWER]))[0] == "irblaster/SONY/7"   # a hyphen inside the protocol
    assert merge.ref_of(frag(7, "Sony12", [POWER])) == "irblaster/SONY/7-Sony12"


# --- the remote that carries the fragments -----------------------------------------------------


def test_the_merged_remote_has_its_own_keys_then_the_folded_ones_each_in_its_order():
    records = [frag(7, "Sony12", [POWER, ONE]), frag(7, "Sony15", [TWO, THREE]), frag(7, "Sony20", [key("KEY_4", "DIGIT_4", 7)])]
    folded = merge.fold(records)
    merged = folded.remote(records, 0)
    assert [k[0] for k in merged.keys] == ["KEY_POWER", "KEY_1", "KEY_2", "KEY_3", "KEY_4"]
    assert merged.keys == records[0].keys + records[1].keys + records[2].keys
    assert (merged.where, merged.protocol, merged.carrier_hz, merged.manufacturer) == (
        records[0].where, "Sony12", 40000, "SONY")
    assert merged.min_sends == records[0].min_sends and corpus.play(merged) == corpus.play(records[0])
    assert [folded.first_key(records, i) for i in range(3)] == [0, 2, 4]


def test_a_remote_nothing_is_folded_into_is_its_own_record():
    records = [frag(7, "Sony12", [POWER]), frag(8, "Sony12", [ONE])]
    folded = merge.fold(records)
    assert folded.remote(records, 0) is records[0] and folded.remote(records, 1) is records[1]
    assert folded.parts == {} and folded.folded == 0 and folded.first_key(records, 1) == 0


def test_the_tier_of_a_merged_remote_is_the_weakest_of_its_keys_and_candidates_are_added():
    records = [frag(7, "Sony12", [key("KEY_POWER", "POWER", 1, 1)], tier=1, other=1),
               frag(7, "Sony15", [key("KEY_1", "DIGIT_1", 2, 3)], tier=3, other=2),
               frag(7, "Sony20", [key("KEY_2", "DIGIT_2", 3, 0)], tier=0)]
    merged = merge.fold(records).remote(records, 0)
    assert merged.tier == 3 and merged.other_candidates == 3
    assert [k[3] for k in merged.keys] == [1, 3, 0]
    stronger = merge.fold([records[0], replace(records[1], tier=2)]).remote([records[0], replace(records[1], tier=2)], 0)
    assert stronger.tier == 2                       # the weakest of the two, not the carrier's own


def test_a_canonical_key_that_both_have_is_kept_twice_and_the_carriers_has_the_lower_number():
    """D85: a reader that wants one key of a canonical id takes the lowest ``n``, which is the
    carrier's; the folded fragment's is kept after it, because it is another signal."""
    own = key("KEY_CH_PLUS", "CHANNEL_UP", 20)
    theirs = key("KEY_CH_PLUS", "CHANNEL_UP", 21)
    records = [frag(7, "Sony12", [own, POWER]), frag(7, "Sony15", [ONE, theirs])]
    merged = merge.fold(records).remote(records, 0)
    ups = [n for n, k in enumerate(merged.keys) if k[2] == "CHANNEL_UP"]
    assert ups == [0, 3] and merged.keys[min(ups)] == own and merged.keys[3] == theirs
    assert len({k[4] for k in merged.keys}) == len(merged.keys)               # no signal was dropped


def test_the_carriers_test_key_is_the_merged_remotes():
    """A folded fragment has none of the five test keys (that is why it is folded), so the
    merge cannot change which key is tried."""
    records = [frag(7, "Sony12", [ONE, key("KEY_VOL_PLUS", "VOLUME_UP", 6)]), frag(7, "Sony15", [TWO, THREE, POWER])]
    assert carriers(records) == [0, 1]                 # both testable: nothing is folded
    records = [frag(7, "Sony12", [ONE, key("KEY_VOL_PLUS", "VOLUME_UP", 6)]), frag(7, "Sony15", [TWO, THREE])]
    folded = merge.fold(records)
    assert merge.key_to_try(folded.remote(records, 0)) == merge.key_to_try(records[0]) == (3, 1)


# --- the exporter, on a small ledger made by the importer -------------------------------------

#: db id -> the devices of the rule, each a case: what the importer writes for it is one file per
#: protocol, ``irblaster/<MAKER>/<id>-<protocol>``.
DB = {
    # Sony15 has no test key and goes into Sony12; CH+ is in both
    10: {"models": [("SONY", "KD-10"), ("SONY", "SHARED")],
         "keys": [("POWER", "A50", "SONY12"), ("CH+", "490", "SONY12"),
                  ("1", "0090", "SONY15"), ("2", "4090", "SONY15"), ("CH+", "0490", "SONY15")]},
    # the same carrier and another play rule (NEC1 and NEC2): left alone
    11: {"models": [("NEC", "N-11")],
         "keys": [("1", "00FF609F", "NEC"), ("2", "00FFE01F", "NEC"), ("POWER", "00FF609F", "NEC2")]},
    # another carrier: left alone
    12: {"models": [("NEC", "N-12")], "keys": [("1", "00FF609F", "NEC"), ("POWER", "811", "RC5")]},
    # both have a test key: left alone
    13: {"models": [("SONY", "KD-13"), ("SONY", "SHARED")],
         "keys": [("POWER", "A50", "SONY12"), ("POWER", "2818", "SONY15")]},
    # three siblings: Volume up in Sony12, Power in Sony20: the Sony15 with no test key goes to the
    # Sony20, the better test key, though the Sony12 has the lower id
    14: {"models": [("SONY", "KD-14")],
         "keys": [("VOL+", "5D0", "SONY12"), ("1", "0090", "SONY15"), ("POWER", "0CB9C", "SONY20")]},
    # the same test key in both: the lowest id, the Sony12
    15: {"models": [("SONY", "KD-15")],
         "keys": [("POWER", "A50", "SONY12"), ("1", "0090", "SONY15"), ("POWER", "0CB9C", "SONY20")]},
    # one fragment only
    16: {"models": [("SONY", "KD-16")], "keys": [("1", "090", "SONY12")]},
    # two fragments and neither has a test key
    17: {"models": [("SONY", "KD-17")], "keys": [("1", "090", "SONY12"), ("2", "0090", "SONY15")]},
    # two fragments folded into one
    18: {"models": [("SONY", "KD-18")],
         "keys": [("POWER", "A50", "SONY12"), ("1", "0090", "SONY15"), ("2", "00A9C", "SONY20")]},
    # a brand the selected profile does not carry
    19: {"models": [("ZZZBRAND", "Z-19")], "keys": [("POWER", "A50", "SONY12"), ("1", "0090", "SONY15")]},
}

#: ref -> (the ref of the remote that carries it, the number of its first key there, its keys)
EXPECTED_REFS = {
    "irblaster/SONY/10-Sony12": ("irblaster/SONY/10-Sony12", 0, 2),
    "irblaster/SONY/10-Sony15": ("irblaster/SONY/10-Sony12", 2, 3),
    "irblaster/NEC/11-NEC1": ("irblaster/NEC/11-NEC1", 0, 2),
    "irblaster/NEC/11-NEC2": ("irblaster/NEC/11-NEC2", 0, 1),
    "irblaster/NEC/12-NEC1": ("irblaster/NEC/12-NEC1", 0, 1),
    "irblaster/NEC/12-RC5": ("irblaster/NEC/12-RC5", 0, 1),
    "irblaster/SONY/13-Sony12": ("irblaster/SONY/13-Sony12", 0, 1),
    "irblaster/SONY/13-Sony15": ("irblaster/SONY/13-Sony15", 0, 1),
    "irblaster/SONY/14-Sony12": ("irblaster/SONY/14-Sony12", 0, 1),
    "irblaster/SONY/14-Sony15": ("irblaster/SONY/14-Sony20", 1, 1),
    "irblaster/SONY/14-Sony20": ("irblaster/SONY/14-Sony20", 0, 1),
    "irblaster/SONY/15-Sony12": ("irblaster/SONY/15-Sony12", 0, 1),
    "irblaster/SONY/15-Sony15": ("irblaster/SONY/15-Sony12", 1, 1),
    "irblaster/SONY/15-Sony20": ("irblaster/SONY/15-Sony20", 0, 1),
    "irblaster/SONY/16-Sony12": ("irblaster/SONY/16-Sony12", 0, 1),
    "irblaster/SONY/17-Sony12": ("irblaster/SONY/17-Sony12", 0, 1),
    "irblaster/SONY/17-Sony15": ("irblaster/SONY/17-Sony15", 0, 1),
    "irblaster/SONY/18-Sony12": ("irblaster/SONY/18-Sony12", 0, 1),
    "irblaster/SONY/18-Sony15": ("irblaster/SONY/18-Sony12", 1, 1),
    "irblaster/SONY/18-Sony20": ("irblaster/SONY/18-Sony12", 2, 1),
    "irblaster/ZZZBRAND/19-Sony12": ("irblaster/ZZZBRAND/19-Sony12", 0, 1),
    "irblaster/ZZZBRAND/19-Sony15": ("irblaster/ZZZBRAND/19-Sony12", 1, 1),
}
OTHER_REFS = {"lirc/acme/TV-1", "lirc/zed/Z-9", "smartir/Acme/media_player_7", "topping/RC-15A"}
FOLDED = {r for r, (carrier, _, _) in EXPECTED_REFS.items() if r != carrier}


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    return make_corpus(tmp_path_factory.mktemp("merge") / "ledger", DB)


@pytest.fixture(scope="module")
def full(ledger):
    built = bb.build_bundle(ledger, "full", aliases=())
    assert built.problems == []
    return built


@pytest.fixture(scope="module")
def selected(ledger):
    with floor_of_the_small_ledgers():
        built = bb.build_bundle(ledger, "selected", aliases=())
    assert built.problems == []
    return built


@pytest.fixture(scope="module")
def unfolded(ledger):
    """The same ledger built with the rule switched off: every file is its own remote."""
    patch = pytest.MonkeyPatch()
    patch.setattr(catalog, "fold", lambda records: merge.Folded(tuple(range(len(records)))))
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


def tree_refs(ledger: Path) -> list[str]:
    return [p.relative_to(ledger / "remotes").as_posix()[:-5] for p in sorted((ledger / "remotes").glob("**/*.json"))]


def test_the_ledger_of_the_cases_is_what_the_cases_say(ledger):
    """The premises: the importer wrote one file per protocol, and the play of the pairs the
    cases name is what each case needs."""
    refs = tree_refs(ledger)
    assert {r for r in refs if r.startswith("irblaster/")} == set(EXPECTED_REFS)
    records, problems = corpus.read_corpus(ledger)
    assert problems == []
    by_ref = {merge.ref_of(r): r for r in records}
    nec1, nec2 = by_ref["irblaster/NEC/11-NEC1"], by_ref["irblaster/NEC/11-NEC2"]
    assert nec1.carrier_hz == nec2.carrier_hz and merge.play_of(nec1) != merge.play_of(nec2)
    assert merge.key_to_try(nec1) is None and merge.key_to_try(nec2) is not None
    nec, rc5 = by_ref["irblaster/NEC/12-NEC1"], by_ref["irblaster/NEC/12-RC5"]
    assert nec.carrier_hz != rc5.carrier_hz and merge.key_to_try(nec) is None and merge.key_to_try(rc5) is not None
    for number in (14, 15):
        sony = {p: by_ref[f"irblaster/SONY/{number}-{p}"] for p in ("Sony12", "Sony15", "Sony20")}
        assert len({merge.play_of(r) for r in sony.values()}) == 1
    assert merge.key_to_try(by_ref["irblaster/SONY/14-Sony12"])[0] == 3       # Volume up
    assert merge.key_to_try(by_ref["irblaster/SONY/14-Sony20"])[0] == 0       # Power
    assert merge.key_to_try(by_ref["irblaster/SONY/15-Sony12"])[0] == merge.key_to_try(by_ref["irblaster/SONY/15-Sony20"])[0] == 0
    assert merge.key_to_try(by_ref["irblaster/SONY/17-Sony12"]) is merge.key_to_try(by_ref["irblaster/SONY/17-Sony15"]) is None


def test_the_remotes_are_the_files_less_the_folded_fragments(ledger, full):
    refs = tree_refs(ledger)
    assert len(refs) == len(EXPECTED_REFS) + len(OTHER_REFS) == 26
    got = [ref for (ref,) in rows(full, "SELECT ref FROM remotes ORDER BY id")]
    assert got == [r for r in refs if r not in FOLDED] and len(got) == 20
    assert full.stats["remotes"] == 20 and full.stats["remoteRefs"] == 26
    assert full.stats["foldedFragments"] == len(FOLDED) == 6 and full.stats["mergedRemotes"] == 5


def test_a_remote_keeps_the_id_of_its_file_whatever_is_folded(ledger, full, unfolded):
    """D89: the id is the file's place in path order, so a remote that does not move keeps its
    id and a gap is left where a fragment was (D104)."""
    ids = {ref: rid for rid, ref in rows(full, "SELECT id, ref FROM remotes")}
    everything = {ref: rid for rid, ref in rows(unfolded, "SELECT id, ref FROM remotes")}
    assert everything == {ref: i + 1 for i, ref in enumerate(tree_refs(ledger))}
    assert ids == {ref: everything[ref] for ref in ids}
    gone = {everything[ref] for ref in FOLDED}
    assert len(gone) == 6 and not set(ids.values()) & gone and set(ids) | FOLDED == set(everything)


def test_remote_refs_maps_every_ref_of_the_ledger_to_the_remote_that_carries_its_keys(ledger, full):
    names = {rid: ref for rid, ref in rows(full, "SELECT id, ref FROM remotes")}
    table = {ref: (names[remote_id], first, count)
             for ref, remote_id, first, count in rows(full, "SELECT ref, remote_id, first_n, key_count FROM remote_refs")}
    assert set(table) == set(tree_refs(ledger)) and len(table) == 26
    for ref, expected in EXPECTED_REFS.items():
        assert table[ref] == expected, ref
    counts = dict(rows(full, "SELECT ref, key_count FROM remotes"))
    for ref in OTHER_REFS:                                  # a file nothing was folded into: itself, all its keys
        assert table[ref] == (ref, 0, counts[ref])


def test_the_lookup_of_an_old_ref_gives_one_remote_and_the_range_of_its_keys(ledger, full):
    """D105's lookup, run against the compile stage's own output for each file: the keys
    ``first_n`` to ``first_n + key_count - 1`` of the remote the ref maps to are the file's keys,
    in the file's order, with its signals."""
    conn = conn_of(full)
    vocab = {i: k for i, k in conn.execute("SELECT id, key FROM vocab_keys")}
    signal = {i: b for i, b in conn.execute("SELECT id, words FROM signals")}
    for ref in tree_refs(ledger):
        found = conn.execute("SELECT r.id, r.ref, f.first_n, f.key_count FROM remote_refs f "
                             "JOIN remotes r ON r.id = f.remote_id WHERE f.ref = ?", (ref,)).fetchall()
        assert len(found) == 1, ref
        remote_id, _, first, count = found[0]
        artifact = compiled_artifact(load_remote(ledger / "remotes" / f"{ref}.json"))["keys"]
        wanted = [pronto_blob(artifact[name]["candidates"]["primary"]["prontoHex"]) for name in sorted(artifact)]
        stored = conn.execute("SELECT signal_id FROM keys WHERE remote_id = ? AND n >= ? AND n < ? ORDER BY n",
                              (remote_id, first, first + count)).fetchall()
        assert [signal[s] for (s,) in stored] == wanted, ref
        assert len(wanted) == count
    assert vocab


def test_the_merged_remote_has_its_keys_then_the_folded_ones_and_its_counts(full):
    names = {i: k for i, k in rows(full, "SELECT id, key FROM vocab_keys")}
    by_remote = defaultdict(list)
    for remote_id, n, canon in rows(full, "SELECT remote_id, n, canon FROM keys ORDER BY remote_id, n"):
        by_remote[remote_id].append(names.get(canon))
    remote = {ref: (rid, count, tier) for rid, ref, count, tier in rows(full, "SELECT id, ref, key_count, tier FROM remotes")}
    rid, count, tier = remote["irblaster/SONY/10-Sony12"]
    assert by_remote[rid] == ["CHANNEL_UP", "POWER", "DIGIT_1", "DIGIT_2", "CHANNEL_UP"] and count == 5
    rid, count, _ = remote["irblaster/SONY/18-Sony12"]
    assert by_remote[rid] == ["POWER", "DIGIT_1", "DIGIT_2"] and count == 3
    rid, count, _ = remote["irblaster/SONY/14-Sony20"]
    assert by_remote[rid] == ["POWER", "DIGIT_1"] and count == 2
    assert tier == 2
    # a remote nothing was folded into is what it was
    rid, count, _ = remote["irblaster/SONY/13-Sony15"]
    assert by_remote[rid] == ["POWER"] and count == 1
    assert rows(full, "SELECT COUNT(*) FROM remotes r WHERE key_count != (SELECT COUNT(*) FROM keys k WHERE k.remote_id = r.id)") == [(0,)]


def test_the_carriers_own_fields_stay_and_the_play_is_the_siblings_too(full, unfolded):
    """What is folded is played by the remote it is folded into exactly as it was played by its
    own: the carrier, the four play numbers, and the protocol of the carrier (D104)."""
    columns = "ref, source, brand_id, model, protocol, carrier_hz, repeat_passes, helper_repeat_passes, intro_empty, rule"
    merged = {r[0]: r for r in rows(full, f"SELECT {columns} FROM remotes")}
    plain = {r[0]: r for r in rows(unfolded, f"SELECT {columns} FROM remotes")}
    for ref, row in merged.items():
        assert row == plain[ref], ref                          # the carrier's row, field for field
    for ref in FOLDED:
        carrier = EXPECTED_REFS[ref][0]
        assert plain[ref][5:] == plain[carrier][5:], ref        # carrier_hz, play numbers, rule
    assert merged["irblaster/SONY/10-Sony12"][4] == "Sony12"


def test_no_folded_remote_id_is_left_anywhere(ledger, full, unfolded):
    gone = {rid for rid, ref in rows(unfolded, "SELECT id, ref FROM remotes") if ref in FOLDED}
    assert len(gone) == 6
    alive = {rid for (rid,) in rows(full, "SELECT id FROM remotes")}
    assert not gone & alive
    assert not {r for (r,) in rows(full, "SELECT DISTINCT remote_id FROM controls")} & gone
    assert not {r for (r,) in rows(full, "SELECT DISTINCT remote_id FROM keys")} & gone
    assert not {r for (r,) in rows(full, "SELECT DISTINCT remote_id FROM remote_refs")} & gone
    assert {r for (r,) in rows(full, "SELECT DISTINCT remote_id FROM controls")} <= alive


def test_a_model_lists_the_remote_that_carries_a_fragment_once(full):
    def remotes_of(model):
        return [ref for (ref,) in rows(
            full, "SELECT r.ref FROM controls c JOIN models m ON m.id = c.model_id JOIN remotes r ON r.id = c.remote_id "
                  "WHERE m.name = ? ORDER BY r.ref", model)]
    assert remotes_of("KD-10") == ["irblaster/SONY/10-Sony12"]
    assert remotes_of("KD-14") == ["irblaster/SONY/14-Sony12", "irblaster/SONY/14-Sony20"]
    assert remotes_of("KD-18") == ["irblaster/SONY/18-Sony12"]
    # a model two devices list has the remote of each, a device of two fragments once
    assert remotes_of("SHARED") == ["irblaster/SONY/10-Sony12", "irblaster/SONY/13-Sony12", "irblaster/SONY/13-Sony15"]
    assert remotes_of("N-11") == ["irblaster/NEC/11-NEC1", "irblaster/NEC/11-NEC2"]
    assert rows(full, "SELECT COUNT(*) FROM models WHERE id NOT IN (SELECT model_id FROM controls)") == [(0,)]


def test_only_remotes_keys_controls_sources_and_the_refs_moved(full, unfolded):
    """Folding changes no brand, no model, no spelling, no gram and no signal: the tables a
    matcher reads are the unfolded bundle's, row for row (D104)."""
    for table, order in (("brands", "id"), ("models", "id"), ("ngram", "gram"), ("signals", "id"), ("vocab_keys", "id"),
                         ("vocab_groups", "id"), ("excluded_brands", "name"), ("brand_aliases", "norm, brand_id")):
        assert rows(full, f"SELECT * FROM {table} ORDER BY {order}") == rows(unfolded, f"SELECT * FROM {table} ORDER BY {order}"), table
    # every key of the unfolded bundle is a key of the folded one, once, under the carrier
    carrier = {ref: c for ref, c, _, _ in rows(
        full, "SELECT f.ref, r.ref, f.first_n, f.key_count FROM remote_refs f JOIN remotes r ON r.id = f.remote_id")}

    def pairs(built, name):
        return Counter((name(ref), canon, signal) for ref, canon, signal in rows(
            built, "SELECT r.ref, k.canon, k.signal_id FROM keys k JOIN remotes r ON r.id = k.remote_id"))

    assert pairs(full, lambda ref: ref) == pairs(unfolded, lambda ref: carrier[ref])
    assert rows(full, "SELECT COUNT(*) FROM keys") == rows(unfolded, "SELECT COUNT(*) FROM keys")
    sources = {i: (r, k) for i, r, k in rows(full, "SELECT id, remote_count, key_count FROM sources")}
    plain = {i: (r, k) for i, r, k in rows(unfolded, "SELECT id, remote_count, key_count FROM sources")}
    assert sources[4][0] == plain[4][0] - 6 and sources[4][1] == plain[4][1]       # remotes of the IR Blaster import
    assert [sources[i] for i in (1, 2, 3)] == [plain[i] for i in (1, 2, 3)]
    assert rows(unfolded, "SELECT COUNT(*) FROM remote_refs") == [(26,)]       # with no folding the table is the identity


def test_the_selected_profile_has_the_refs_of_the_files_it_carries_and_no_other(selected, full):
    refs = {ref for (ref,) in rows(selected, "SELECT ref FROM remote_refs")}
    assert not any("ZZZBRAND" in ref for ref in refs)
    # the brands of the curated list that the ledger has are SONY and NEC: the files of the other
    # sources and of the brand that is not on the list are left out, folded or not
    assert refs == {ref for ref in EXPECTED_REFS if "ZZZBRAND" not in ref} and len(refs) == 20
    assert {ref for (ref,) in rows(selected, "SELECT ref FROM remotes")} == {
        ref for ref, (carrier, _, _) in EXPECTED_REFS.items() if ref == carrier and "ZZZBRAND" not in ref}
    assert rows(selected, "SELECT COUNT(*) FROM remote_refs WHERE remote_id NOT IN (SELECT id FROM remotes)") == [(0,)]
    assert selected.stats["leftOutRemotes"] == 6 and selected.stats["remoteRefs"] == 20


def test_data_version_covers_the_refs_and_the_folding(full, unfolded, ledger):
    assert ("remote_refs", "ref") in writer.DIGEST_TABLES
    versions = {built.stats["dataVersion"] for built in (full, unfolded)}
    assert len(versions) == 2                              # the same ledger folded and not is another version
    conn = conn_of(full)
    before = writer.content_digest(conn)
    assert before == dict(conn.execute("SELECT key, value FROM meta"))["dataVersion"]
    conn.execute("UPDATE remote_refs SET first_n = first_n + 1 WHERE ref = 'irblaster/SONY/10-Sony15'")
    assert writer.content_digest(conn) != before
    conn = conn_of(full)
    conn.execute("DELETE FROM remote_refs WHERE ref = 'topping/RC-15A'")
    assert writer.content_digest(conn) != before


def test_meta_and_the_manifest_count_the_refs_and_state_the_rule(full):
    meta = dict(rows(full, "SELECT key, value FROM meta"))
    assert meta["count.remoteRefs"] == "26" and meta["count.remotes"] == "20" and meta["schemaVersion"] == "1"
    assert meta["mergeRule"] == writer.MERGE_RULE_TEXT and "remote_refs" in meta["mergeRule"]
    manifest = json.loads(full.files[bb.MANIFEST_FILE])
    assert manifest["counts"]["remoteRefs"] == 26 and manifest["counts"]["remotes"] == 20 and manifest["schemaVersion"] == 1
    assert rows(full, "PRAGMA user_version") == [(1,)]


def test_two_builds_of_the_folded_ledger_are_the_same_bytes(ledger, full):
    for jobs in (1, 3):
        parallel.configure(jobs)
        assert bb.build_bundle(ledger, "full", aliases=()).files == full.files


def test_the_table_needs_nothing_newer_than_sqlite_3_28(full):
    (sql,) = [s for (s,) in rows(full, "SELECT sql FROM sqlite_master WHERE name = 'remote_refs'")]
    assert "WITHOUT ROWID" in sql and not re.search(r"\b(STRICT|GENERATED|RETURNING)\b", sql)
    assert [c[1] for c in conn_of(full).execute("PRAGMA table_info(remote_refs)")] == ["ref", "remote_id", "first_n", "key_count"]


# --- the matcher and suggest over a merged bundle ---------------------------------------------


def test_the_matcher_and_suggest_answer_with_the_remote_that_carries_a_fragment(ledger, full, unfolded, tmp_path):
    directory = tmp_path / "merged"
    bb.write_bundle(full, directory, ledger)
    index = MatchIndex.open(directory / bb.BUNDLE_FILE)
    ids = {ref: rid for rid, ref in rows(full, "SELECT id, ref FROM remotes")}
    top = index.match("SONY", "KD-10")[0]
    assert (top.brand, top.model) == ("SONY", "KD-10") and top.remote_ids == (ids["irblaster/SONY/10-Sony12"],)
    top = index.match("SONY", "KD-14")[0]
    assert top.remote_ids == (ids["irblaster/SONY/14-Sony12"], ids["irblaster/SONY/14-Sony20"])
    offered = index.suggest("sony kd 18").models
    assert [m.remote_ids for m in offered if m.model == "KD-18"] == [(ids["irblaster/SONY/18-Sony12"],)]
    # every answer of the unfolded bundle is the folded one's with the ids of the folded fragments
    # replaced by the remote that carries them (and then once)
    plain_dir = tmp_path / "plain"
    bb.write_bundle(unfolded, plain_dir, ledger)
    plain = MatchIndex.open(plain_dir / bb.BUNDLE_FILE)
    plain_ids = {ref: rid for rid, ref in rows(unfolded, "SELECT id, ref FROM remotes")}
    carrier = {plain_ids[ref]: ids[carrying] for ref, (carrying, _, _) in EXPECTED_REFS.items()}
    for query in ("SONY KD-10", "KD-14", "sony kd 18", "shared", "n-11", "kd16", "zzzbrand", "SONY"):
        with_ids = [(c.brand, c.model, c.score, c.evidence, tuple(sorted({carrier.get(r, r) for r in c.remote_ids})))
                    for c in plain.match(None, None, [query])]
        assert with_ids == [(c.brand, c.model, c.score, c.evidence, tuple(sorted(c.remote_ids)))
                            for c in index.match(None, None, [query])], query
        # the same brands in the same order, and the same models; their order inside a brand follows the number
        # of remotes (S: most remotes first), which folding lowers for a device of several fragments (D104)
        assert plain.suggest(query).brands == index.suggest(query).brands
        assert sorted((m.brand, m.model) for m in plain.suggest(query, 50).models) == sorted(
            (m.brand, m.model) for m in index.suggest(query, 50).models), query


# --- rl bundle --verify ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def written(ledger, tmp_path_factory):
    out = {}
    for profile in ("full", "selected"):
        with floor_of_the_small_ledgers():
            built = bb.build_bundle(ledger, profile, aliases=())
        directory = tmp_path_factory.mktemp(f"merge-{profile}")
        bb.write_bundle(built, directory, ledger)
        out[profile] = directory
    return out


def damaged(written, tmp_path, profile, *statements, update=True):
    directory = tmp_path / "bundle"
    shutil.copytree(written[profile], directory)
    conn = sqlite3.connect(directory / bb.BUNDLE_FILE, isolation_level=None)
    for sql in statements:
        conn.execute(sql)
    conn.close()
    if update:
        refresh(directory)
    return directory


def problems_of(ledger, directory):
    return verify_directory(ledger, directory, aliases=())[0]


def test_a_good_bundle_of_either_profile_verifies_with_its_refs(ledger, written):
    for profile, remotes, refs in (("full", 20, 26), ("selected", 15, 20)):
        problems, facts = verify_directory(ledger, written[profile], aliases=())
        assert problems == [] and facts["remotes"] == remotes and facts["remoteRefs"] == refs, profile


def split_off(ledger):
    """The statements that make the folded fragment 10-Sony15 a remote of its own again, as a bundle
    written with no folding has it: a row, its three keys taken from the carrier, a ref of its own."""
    refs = tree_refs(ledger)
    mine, carrier = refs.index("irblaster/SONY/10-Sony15") + 1, refs.index("irblaster/SONY/10-Sony12") + 1
    return [
        f"INSERT INTO remotes SELECT {mine}, 'irblaster/SONY/10-Sony15', brand_id, model, source, tier, 3, 'Sony15', "
        f"carrier_hz, repeat_passes, helper_repeat_passes, intro_empty, rule FROM remotes WHERE id = {carrier}",
        f"INSERT INTO keys SELECT {mine}, n - 2, canon, label, signal_id, confidence FROM keys "
        f"WHERE remote_id = {carrier} AND n >= 2",
        f"DELETE FROM keys WHERE remote_id = {carrier} AND n >= 2",
        f"UPDATE remotes SET key_count = 2 WHERE id = {carrier}",
        f"UPDATE remote_refs SET remote_id = {mine}, first_n = 0 WHERE ref = 'irblaster/SONY/10-Sony15'",
        f"INSERT INTO controls SELECT model_id, {mine} FROM controls WHERE remote_id = {carrier}",
    ]


MUTATIONS = [
    ("a ref missing", "DELETE FROM remote_refs WHERE ref = 'irblaster/SONY/10-Sony15'", "full",
     ["remote_refs: the refs of remote", "remote_refs has 25 rows and the tree gives 26"]),
    ("a ref of a file the tree does not have",
     "INSERT INTO remote_refs VALUES ('irblaster/SONY/99-Sony15', 7, 0, 1)", "full",
     ["remote_refs has 27 rows and the tree gives 26", "remote_refs: irblaster/SONY/99-Sony15 holds the keys"]),
    ("a ref that maps to another remote",
     "UPDATE remote_refs SET remote_id = (SELECT id FROM remotes WHERE ref = 'irblaster/SONY/14-Sony20') "
     "WHERE ref = 'irblaster/SONY/10-Sony15'", "full", ["remote_refs"]),
    ("a ref that maps to nothing",
     "UPDATE remote_refs SET remote_id = 9999 WHERE ref = 'irblaster/SONY/10-Sony15'", "full",
     ["rows of remote_refs.remote_id are wrong or dangling"]),
    ("a folded fragment's keys start in the wrong place",
     "UPDATE remote_refs SET first_n = 1 WHERE ref = 'irblaster/SONY/10-Sony15'", "full",
     ["remote_refs: irblaster/SONY/10-Sony15 holds the keys 1 to 3 of remote"]),
    ("a folded fragment's run of keys is the wrong length",
     "UPDATE remote_refs SET key_count = 2 WHERE ref = 'irblaster/SONY/10-Sony15'", "full",
     ["the refs of remote", "remote_refs has"]),
    ("the carrier's own ref is not its own in the table",
     "UPDATE remote_refs SET first_n = 1 WHERE ref = 'irblaster/SONY/10-Sony12'", "full",
     ["rows of remotes whose own ref is not theirs in remote_refs"]),
    ("a key of the folded fragment missing from the carrier",
     "DELETE FROM keys WHERE remote_id = (SELECT id FROM remotes WHERE ref = 'irblaster/SONY/10-Sony12') AND n = 4",
     "full", ["rows of remotes.key_count are wrong", "5 keys in the file"]),
    ("a folded fragment's key with another signal",
     "UPDATE keys SET signal_id = (SELECT signal_id FROM keys WHERE n = 0 AND remote_id = "
     "(SELECT id FROM remotes WHERE ref = 'irblaster/SONY/13-Sony12')) WHERE n = 3 AND remote_id = "
     "(SELECT id FROM remotes WHERE ref = 'irblaster/SONY/10-Sony12'); "
     "DELETE FROM signals WHERE id NOT IN (SELECT signal_id FROM keys)", "full", ["key 3 (KEY_2) is not the file's"]),
    ("a folded fragment's keys in the carrier in another order",
     "UPDATE keys SET n = n + 10 WHERE n = 2 AND remote_id = (SELECT id FROM remotes WHERE ref = 'irblaster/SONY/10-Sony12'); "
     "UPDATE keys SET n = 2 WHERE n = 3 AND remote_id = (SELECT id FROM remotes WHERE ref = 'irblaster/SONY/10-Sony12'); "
     "UPDATE keys SET n = 3 WHERE n = 12 AND remote_id = (SELECT id FROM remotes WHERE ref = 'irblaster/SONY/10-Sony12')",
     "full", ["is not the file's"]),
    ("a tier that is the carrier's and not the weakest",
     "UPDATE remotes SET tier = 0 WHERE ref = 'irblaster/SONY/10-Sony12'", "full",
     ["source, tier, protocol, carrier or key count differ from the file"]),
    ("a fragment that the rule folds, kept as a remote of its own", lambda ledger: split_off(ledger), "full",
     ["the rule of D103 folds it into irblaster/SONY/10-Sony12"]),
    ("a model linked to a folded fragment's remote id",
     lambda ledger: [f"INSERT INTO controls VALUES (1, {tree_refs(ledger).index('irblaster/SONY/10-Sony15') + 1})"],
     "full", ["rows of controls.remote_id are wrong or dangling"]),
    ("a ref of a left-out brand in the selected bundle",
     "INSERT INTO remote_refs VALUES ('irblaster/ZZZBRAND/19-Sony15', "
     "(SELECT id FROM remotes WHERE ref = 'irblaster/SONY/10-Sony12'), 5, 1)", "selected",
     ["remote_refs"]),
    ("the refs of a fragment of a carried brand missing in the selected bundle",
     "DELETE FROM remote_refs WHERE ref = 'irblaster/SONY/18-Sony20'", "selected", ["remote_refs"]),
]


@pytest.mark.parametrize("what, sql, profile, messages", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_way_the_refs_or_the_folding_can_be_wrong_is_found(ledger, written, tmp_path, what, sql, profile, messages):
    statements = sql(ledger) if callable(sql) else sql.split("; ")
    directory = damaged(written, tmp_path, profile, *statements)
    got = "\n".join(problems_of(ledger, directory))
    assert got, f"{what}: nothing was reported"
    assert any(m in got for m in messages), f"{what}: {messages} not in\n{got}"


def test_a_bundle_written_before_the_refs_is_told_to_be_built_again(ledger, written, tmp_path):
    directory = damaged(written, tmp_path, "full", "DROP TABLE remote_refs", update=False)
    assert any("the bundle has no remote_refs table: it was written before the fragments were folded "
               "(D103, D105); build it again" in p for p in problems_of(ledger, directory))


def test_the_count_of_refs_must_agree_in_the_table_meta_and_the_manifest(ledger, written, tmp_path):
    directory = damaged(written, tmp_path, "full", "UPDATE meta SET value = '99' WHERE key = 'count.remoteRefs'", update=False)
    assert any("remote_refs has 26 rows, meta count.remoteRefs says 99" in p for p in problems_of(ledger, directory))
    directory = tmp_path / "2" / "b"
    shutil.copytree(written["full"], directory)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["counts"]["remoteRefs"] = 7
    (directory / "manifest.json").write_bytes(json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode() + b"\n")
    assert any("remote_refs has 26 rows, the manifest says 7" in p for p in problems_of(ledger, directory))


def test_a_stale_bundle_is_found_when_a_fragment_is_added_to_the_tree(ledger, written, tmp_path):
    """Another fragment of device 10 with no test key is added to the tree: the bundle built
    before it has no ref for it and none of its keys in the carrier."""
    moved = tmp_path / "ledger"
    shutil.copytree(ledger, moved)
    source = moved / "remotes/irblaster/SONY/10-Sony15.json"
    (moved / "remotes/irblaster/SONY/10-Sony15b.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    got = problems_of(moved, written["full"])
    assert any("remote_refs has 26 rows and the tree gives 27" in p for p in got), got
    assert any("irblaster/SONY/10-Sony12: 5 keys, the file has 8" in p for p in got), got


def test_the_cli_reports_the_folding_and_the_check_and_verify_pass(ledger, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(ledger)
    out = tmp_path / "out"
    assert cli.main(["bundle", "--profile", "full", "--out", str(out)]) == 0
    assert "fragments: 6 protocol fragments with no test key are folded into 5 remotes; remote_refs: 26 refs" in (
        capsys.readouterr().out)
    assert cli.main(["bundle", "--profile", "full", "--out", str(out), "--check"]) == 0
    assert cli.main(["bundle", "--verify", str(out)]) == 0
    assert "verified against the tree: 20 remotes (26 refs)" in capsys.readouterr().out


# --- the real ledger ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real(real_records):
    """Rows of the full bundle of the real ledger, as ``assemble`` makes them (the SQLite file
    is not needed for what is asked of it here)."""
    from remote_ledger.bundle import notices

    collected = catalog.collect(real_records)
    sources = notices.build_sources(ROOT, real_records)
    return collected, catalog.assemble(collected, None, "full", sources)


def test_every_ref_of_the_ledger_maps_to_exactly_one_remote_of_the_bundle(real_records, real):
    collected, assembled = real
    refs = [merge.ref_of(r) for r in real_records]
    assert len(set(refs)) == len(refs) == 13_369
    table = {ref: (remote_id, first, count) for ref, remote_id, first, count in assembled.remote_refs}
    assert len(assembled.remote_refs) == len(table) == 13_369 and set(table) == set(refs)
    remotes = {row[0]: row for row in assembled.remotes}
    assert len(remotes) == len(assembled.remotes) == 13_369 - collected.folded.folded
    for ref, (remote_id, first, count) in table.items():
        assert remote_id in remotes, ref
    # a remote is carried by its own ref, and the refs that map to it cut its keys into runs
    runs = defaultdict(list)
    for ref, (remote_id, first, count) in table.items():
        runs[remote_id].append((first, count, ref))
    for remote_id, row in remotes.items():
        at = 0
        for first, count, ref in sorted(runs[remote_id]):
            assert first == at, ref
            at += count
        assert at == row[6]
        assert table[row[1]] == (remote_id, 0, runs[remote_id][0][1]) or table[row[1]][:2] == (remote_id, 0)


def test_no_key_of_the_ledger_is_lost_or_changed_by_folding(real_records, real):
    """The (canonical key, signal) pairs of all the files are those of the merged remotes, and
    each file's keys are the run of its ref, in its own order, with its tier and text."""
    collected, assembled = real
    by_id = defaultdict(list)
    for remote_id, n, canon, label, signal_id, confidence in assembled.keys:
        by_id[remote_id].append((n, canon, label, signal_id, confidence))
    assert all([k[0] for k in keys] == list(range(len(keys))) for keys in by_id.values())
    blob = {sid: words for sid, words in assembled.signals}
    canon_of = {i: k for i, k, *_ in assembled.vocab_keys}
    files = Counter()
    merged = Counter()
    for record in real_records:
        for name, label, canon, confidence, words in record.keys:
            files[(canon, words, confidence)] += 1
    for remote_id, keys in by_id.items():
        for n, canon, label, signal_id, confidence in keys:
            merged[(canon_of.get(canon), blob[signal_id], confidence)] += 1
    assert files == merged and sum(files.values()) == 531_868
    # and per ref: its run of the carrier's keys is the file's keys
    for i, record in enumerate(real_records):
        remote_id, first, count = next(row[1:] for row in assembled.remote_refs if row[0] == merge.ref_of(record)) if i % 97 == 0 else (None, 0, 0)
        if remote_id is None:
            continue
        run = by_id[remote_id][first:first + count]
        assert [(canon_of.get(c), blob[s], conf) for _, c, _, s, conf in run] == [
            (k[2], k[4], k[3]) for k in record.keys], record.where


def test_the_folded_fragments_of_the_ledger_follow_the_rule_worked_out_again(real_records, real):
    """The rule of D103 written a second way, from refs and the vocabulary, over every file."""
    collected, _ = real
    order = ["POWER", "POWER_OFF", "POWER_ON", "VOLUME_UP", "MUTE"]
    devices = defaultdict(list)
    for i, r in enumerate(real_records):
        m = re.fullmatch(r"remotes/irblaster/([^/]+)/(\d+)-[^/]+\.json", r.where)
        if m:
            devices[m.groups()].append(i)

    def rank(r):
        have = {k[2] for k in r.keys}
        return next((order.index(c) for c in order if c in have), None)

    def play(r):
        return (r.carrier_hz, *corpus.play(r))

    want = list(range(len(real_records)))
    for members in devices.values():
        tried = [i for i in members if rank(real_records[i]) is not None]
        for i in members:
            if rank(real_records[i]) is not None:
                continue
            fit = [t for t in tried if play(real_records[t]) == play(real_records[i])
                   and real_records[t].manufacturer == real_records[i].manufacturer]
            if fit:
                want[i] = min(fit, key=lambda t: (rank(real_records[t]), t))
    assert list(collected.folded.carrier) == want
    assert sum(1 for i, c in enumerate(want) if i != c) == collected.folded.folded == 315


def test_the_test_key_of_every_remote_is_the_carriers_and_the_testable_remotes_are_unchanged(real_records, real):
    """Folding gives no remote a new test key and takes none away: the 12,094 remotes that can be
    tried before can be tried after, with the same key and signal."""
    collected, _ = real
    folded = collected.folded
    tried = 0
    for i, record in enumerate(real_records):
        if folded.carrier[i] != i:
            assert merge.key_to_try(record) is None, record.where        # only what has none is folded
            continue
        merged = folded.remote(real_records, i)
        before = merge.key_to_try(record)
        assert merge.key_to_try(merged) == before, record.where
        if before is not None:
            tried += 1
            assert merged.keys[before[1]] == record.keys[before[1]]
    assert tried == 12_094
    assert sum(1 for i, r in enumerate(real_records) if folded.carrier[i] == i and merge.key_to_try(folded.remote(real_records, i)) is None) == 960


@pytest.fixture(scope="module")
def real_selected_unfolded(real_records):
    patch = pytest.MonkeyPatch()
    patch.setattr(catalog, "fold", lambda records: merge.Folded(tuple(range(len(records)))))
    try:
        # never shipped, only compared with: it is 20,287,488 bytes, over the cap, because folding the
        # fragments (D103) is what gives the real one room (D116)
        built = bb.build_bundle(ROOT, "selected", records=real_records, max_bytes=10**9)
    finally:
        patch.undo()
    assert built.problems == []
    return built


def test_over_the_real_selected_bundle_the_matcher_answers_as_before_with_the_carriers_ids(real_selected, real_selected_unfolded, tmp_path):
    from remote_ledger.bundle import search_eval

    built, directory = real_selected
    plain_dir = tmp_path / "plain"
    bb.write_bundle(real_selected_unfolded, plain_dir, ROOT)
    index = MatchIndex.open(directory / bb.BUNDLE_FILE)
    plain = MatchIndex.open(plain_dir / bb.BUNDLE_FILE)
    carrier = {}
    refs = {ref: rid for rid, ref in plain.conn.execute("SELECT id, ref FROM remotes")}
    ids = {ref: rid for rid, ref in index.conn.execute("SELECT id, ref FROM remotes")}
    for ref, remote_id, _, _ in index.conn.execute("SELECT f.ref, r.ref, f.first_n, f.key_count FROM remote_refs f "
                                                   "JOIN remotes r ON r.id = f.remote_id"):
        carrier[refs[ref]] = ids[remote_id]
    assert sum(1 for a, b in carrier.items() if a != b) == 315
    queries = search_eval.generate(plain.conn, per_class=15)
    assert len(queries) >= 100
    for q in queries:
        want = [(c.brand, c.model, c.score, c.evidence, tuple(sorted({carrier[r] for r in c.remote_ids})))
                for c in plain.match(None, None, [q.text])]
        got = [(c.brand, c.model, c.score, c.evidence, tuple(sorted(c.remote_ids))) for c in index.match(None, None, [q.text])]
        assert got == want, q.text
        # the same brands in the same order; the same models, which only change places inside a brand
        # where the number of remotes (most first) moved, so they are compared as sets while the list
        # is not cut by the limit
        assert index.suggest(q.text).brands == plain.suggest(q.text).brands
        everything = plain.suggest(q.text, 1_000).models
        if len(everything) < 1_000:
            assert {(s.brand, s.model) for s in index.suggest(q.text, 1_000).models} == {(s.brand, s.model) for s in everything}, q.text


def test_the_selected_bundle_still_carries_the_selection_and_fits(real_selected, real_selected_unfolded):
    built, _ = real_selected
    assert built.selection.chosen == real_selected_unfolded.selection.chosen
    # no cap, no budget and every brand (D117): it is the whole ledger, and folding is what makes it
    # 160 KB smaller than the unfolded one (51,478,528 bytes)
    assert bb.select.SELECTED_MAX_BYTES is None and built.stats["bytes"] > 51_000_000
    assert built.stats["bytes"] < real_selected_unfolded.stats["bytes"]
    assert built.stats["remoteRefs"] == real_selected_unfolded.stats["remotes"] == 13_369
    assert built.stats["remotes"] == 13_054 and built.stats["foldedFragments"] == 315
    assert built.stats["keys"] == real_selected_unfolded.stats["keys"] == 531_868


def test_design_quotes_the_numbers_of_the_measurement(real_records, real):
    """DESIGN D103 states what the data says about the rule; each figure is recomputed here from
    the records, so that the text cannot go stale and the owner who widens the rule reads
    what it would add."""
    text = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    section = text[text.index("### D103"):text.index("### D104")]
    order = ["POWER", "POWER_OFF", "POWER_ON", "VOLUME_UP", "MUTE"]
    devices = defaultdict(list)
    for i, r in enumerate(real_records):
        found = merge.device_of(r)
        if found:
            devices[found].append(i)
    split = {d: m for d, m in devices.items() if len(m) > 1}

    def tested(r):
        return merge.key_to_try(r) is not None

    untestable = sum(1 for m in split.values() for i in m if not tested(real_records[i]))
    withtest = sum(1 for m in split.values() for i in m if tested(real_records[i]))
    absorbable = ambiguous = nothing = other_carrier = other_play = 0
    pairs = same_key = full_extra = 0
    full_removed = 0
    for members in split.values():
        tried = [i for i in members if tested(real_records[i])]
        for i in members:
            if tested(real_records[i]):
                continue
            fit = [t for t in tried if merge.play_of(real_records[t]) == merge.play_of(real_records[i])]
            absorbable += bool(fit)
            ambiguous += len(fit) > 1
            if not fit:
                if not tried:
                    nothing += 1
                elif not any(real_records[t].carrier_hz == real_records[i].carrier_hz for t in tried):
                    other_carrier += 1
                else:
                    other_play += 1
        for a in range(len(tried)):
            for b in range(a + 1, len(tried)):
                ra, rb = real_records[tried[a]], real_records[tried[b]]
                ka, kb = merge.key_to_try(ra), merge.key_to_try(rb)
                if order[ka[0]] == order[kb[0]] and ra.keys[ka[1]][4] != rb.keys[kb[1]][4] \
                        and merge.play_of(ra) == merge.play_of(rb):
                    same_key += 1
                pairs += merge.play_of(ra) == merge.play_of(rb)
        full_removed += len(members) - len({merge.play_of(real_records[i]) for i in members})
    quoted = [
        f"{len(devices):,} devices", f"{len(split):,} have two or more fragments",
        f"{sum(len(m) for m in split.values()):,} fragments",
        f"{untestable:,} fragments have no test key", f"{withtest:,} have one",
        f"{absorbable:,} of them can be folded", f"{ambiguous:,} have more than one",
        f"{nothing:,} have no sibling with a test key", f"{other_carrier:,} only siblings at another carrier",
        f"{other_play:,} only siblings with another play rule",
        f"{pairs:,} pairs of testable fragments share a carrier and a play rule",
        f"{same_key:,} of them have the same test key with different signals",
        f"{full_removed:,} remotes", f"{full_removed - absorbable:,} more",
    ]
    for fact in quoted:
        assert fact in section, f"DESIGN D103 no longer says {fact!r}"
    assert absorbable == real[0].folded.folded
