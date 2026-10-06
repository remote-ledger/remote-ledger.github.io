"""The cross-language vectors of the signal table (D91): ``tests/vectors/bundle_vectors.json``.

A Kotlin reader of the bundle is right when, from each vector's ``blobHex`` and
``catalogCarrierHz``, it produces the vector's frequency, intro, repeat and press. These
tests hold the committed file to the ledger's own decoder, to a second computation of the
microseconds written here from the words alone (Decimal arithmetic, no ``pronto`` module), and
to D78's play numbers, so the file is a fair oracle for a port.

The vectors are self-contained (each carries its blob), so the file does not have to follow
the catalog when data changes; it is regenerated, with ``rl bundle vectors``, when the decoder or
the blob format does.
"""

from __future__ import annotations

import json
import re
import struct
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from bundle_corpus import make_corpus
from remote_ledger import cli, parallel
from remote_ledger.bundle import build as bb
from remote_ledger.bundle import vectors
from remote_ledger.bundle.signals import blob_words, decode_blob, frequency_hz, press

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "tests" / "vectors" / "bundle_vectors.json"


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


@pytest.fixture(scope="module")
def document():
    return json.loads(FILE.read_text(encoding="utf-8"))


def half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def microseconds(cycles: list[int], word: int) -> list[int]:
    """Cycles to microseconds with nothing but the Pronto clock: one period is
    ``word x 0.241246`` microseconds, a duration is its cycles x the period, half up."""
    period = Decimal(word) * Decimal("0.241246")
    return [half_up(Decimal(c) * period) for c in cycles]


def test_the_file_is_documented_and_big_enough(document):
    assert document["format"] == 1 and "DESIGN.md D91" in document["about"]
    assert len(document["vectors"]) >= 100
    assert FILE.read_text(encoding="utf-8").endswith("}\n")


def test_it_covers_every_protocol_family_and_every_source(document):
    got = {v["protocol"] or "(unnamed)" for v in document["vectors"]}
    assert got == set(document["protocols"])
    # the ledger's protocols that the imports use, the raw captures of LIRC and SmartIR,
    # and the authored remotes
    assert len(got) >= 29
    assert {"NEC1", "NEC2", "NECx1", "NECx2", "RC5", "RC6", "Sony12", "Sony15", "Sony20", "Samsung36",
            "Sharp", "Denon", "JVC", "Pioneer-2Part", "Panasonic", "Proton", "Thomson7", "Aiwa",
            "Blaupunkt", "RCA-38", "RECS80", "RECS80-0068", "F12_relaxed", "SharpDVD", "JVC-48",
            "Fujitsu", "Denon-K", "Teac-K", "(unnamed)"} <= got
    assert {v["source"] for v in document["vectors"]} == {"authored", "lirc", "smartir", "irblaster"}
    for protocol in got:
        assert sum(1 for v in document["vectors"] if (v["protocol"] or "(unnamed)") == protocol) >= 1


def test_the_press_cases_of_d78_are_all_there(document):
    plays = [v["play"] for v in document["vectors"]]
    assert {p["rule"] for p in plays} == {"ledger", "full-signal"}
    assert {p["introEmpty"] for p in plays} == {True, False}
    assert {p["repeatPasses"] for p in plays} >= {0, 1, 2, 3}
    assert any(p["repeatPasses"] != p["helperRepeatPasses"] for p in plays)   # Sharp, Denon
    longest = max(len(blob_words(bytes.fromhex(v["blobHex"]))) for v in document["vectors"])
    shortest = min(len(blob_words(bytes.fromhex(v["blobHex"]))) for v in document["vectors"])
    assert longest > 400 and shortest < 40       # a long capture and a short code


def test_every_expected_value_is_what_the_ledgers_decoder_gives_from_the_blob(document):
    for v in document["vectors"]:
        blob = bytes.fromhex(v["blobHex"])
        signal = decode_blob(blob, v["catalogCarrierHz"])
        assert v["introUs"] == list(signal.intro), v["remote"]
        assert v["repeatUs"] == list(signal.repeat), v["remote"]
        assert v["frequencyWord"] == blob_words(blob)[1]
        assert v["frequencyHz"] == frequency_hz(blob)
        assert v["play"]["pressUs"] == press(signal.intro, signal.repeat, v["play"]["repeatPasses"])


def test_and_what_plain_arithmetic_on_the_words_gives(document):
    """A second computation that shares no code with the ledger: the words of the blob, the
    Pronto clock and half-up rounding."""
    for v in document["vectors"]:
        blob = bytes.fromhex(v["blobHex"])
        (count,) = struct.unpack(">H", blob[:2])
        words = struct.unpack(f">{count}H", blob[2:])
        assert len(blob) == 2 + 2 * count and words[0] == 0
        word, n1, n2 = words[1], words[2], words[3]
        assert count == 4 + 2 * (n1 + n2)
        body = list(words[4:])
        assert v["introUs"] == microseconds(body[:2 * n1], word), v["remote"]
        assert v["repeatUs"] == microseconds(body[2 * n1:], word), v["remote"]
        assert v["frequencyHz"] == half_up(Decimal(1_000_000) / (Decimal(word) * Decimal("0.241246")))
        assert v["frequencyWord"] == word
        # the catalog's carrier is the file's, and its word is the blob's
        assert half_up(Decimal(1_000_000) / (Decimal(v["catalogCarrierHz"]) * Decimal("0.241246"))) == word


def test_the_play_numbers_are_d78s_and_the_press_is_the_intro_then_the_repeat_passes(document):
    for v in document["vectors"]:
        play = v["play"]
        helper = play["helperRepeatPasses"]
        if play["rule"] == "full-signal":
            assert play["repeatPasses"] == max(helper, 1) and play["introEmpty"] is False
        else:
            assert play["repeatPasses"] == helper
        assert play["introEmpty"] == (v["introUs"] == [])
        assert play["pressUs"] == v["introUs"] + v["repeatUs"] * play["repeatPasses"]
        assert v["introUs"] or v["repeatUs"]
        assert len(v["introUs"]) % 2 == 0 and len(v["repeatUs"]) % 2 == 0


def test_a_vector_names_its_key_the_way_the_bundle_does(document):
    for v in document["vectors"]:
        assert v["canon"] is None or re.fullmatch(r"[A-Z][A-Z0-9_]*", v["canon"])
        assert v["label"] is None or isinstance(v["label"], str)
        assert v["confidence"] in (0, 1, 2, 3) and v["n"] >= 0
        assert v["remote"].split("/")[0] in {"irblaster", "lirc", "smartir", "topping", "meridian",
                                              "samsung", "sony"}
    assert len({(v["remote"], v["n"]) for v in document["vectors"]}) == len(document["vectors"])


def test_vectors_are_made_from_a_bundle_and_the_same_one_gives_the_same_file(tmp_path):
    root = make_corpus(tmp_path / "ledger")
    built = bb.build_bundle(root, "full")
    bundle = tmp_path / "catalog.sqlite"
    bundle.write_bytes(built.files[bb.BUNDLE_FILE])
    first = vectors.build_vectors(bundle)
    assert first == vectors.build_vectors(bundle)
    assert 20 <= len(first["vectors"]) <= built.stats["keys"]
    for v in first["vectors"]:
        blob = bytes.fromhex(v["blobHex"])
        assert v["introUs"] == list(decode_blob(blob, v["catalogCarrierHz"]).intro)
    assert "Sharp" in first["protocols"] and "(unnamed)" in first["protocols"]


def test_the_command_writes_the_file_and_check_compares_it(tmp_path, monkeypatch, capsys):
    root = make_corpus(tmp_path / "ledger")
    out = tmp_path / "bundle"
    monkeypatch.chdir(root)
    assert cli.main(["bundle", "--profile", "full", "--out", str(out)]) == 0
    target = tmp_path / "v" / "vectors.json"
    capsys.readouterr()
    assert cli.main(["bundle", "vectors", "--from", str(out), "--file", str(target)]) == 0
    assert "vectors over" in capsys.readouterr().out
    assert cli.main(["bundle", "vectors", "--from", str(out), "--file", str(target), "--check"]) == 0
    target.write_text(target.read_text().replace("introUs", "introMs", 1))
    assert cli.main(["bundle", "vectors", "--from", str(out), "--file", str(target), "--check"]) == 1
    assert "differs from the vectors this tree gives" in capsys.readouterr().err
    assert json.loads(target.read_text().replace("introMs", "introUs", 1))["format"] == 1
