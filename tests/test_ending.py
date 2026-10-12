"""D217: a signal's ending, the final frame some remotes send when a key is released.

Pronto Hex holds an intro and a repeat, so the ending travels beside the Pronto string as a Pronto string of its own
(``pronto.encode_ending``: the intro of a signal with no repeat) and in the bundle's blob after the Pronto words. These
tests hold that representation; the first protocol that uses it is registered in its own change.
"""

import struct

import pytest

from remote_ledger import pronto
from remote_ledger.bundle.corpus import pronto_blob
from remote_ledger.bundle.signals import (blob_ending_pronto, blob_to_pronto, blob_words, decode_blob, frequency_hz,
                                          has_ending)
from remote_ledger.bundle.verify import _decode_chunk
from remote_ledger.errors import ValidationError
from remote_ledger.signal import IrSignal

CARRIER = 38_000
WITH = IrSignal(carrier_hz=CARRIER, intro=(210, 760) * 3, repeat=(210, 900) * 2, ending=(210, 800, 210, 13_800))
WITHOUT = IrSignal(carrier_hz=CARRIER, intro=(210, 760) * 3, repeat=(210, 900) * 2)


def test_the_ending_is_a_pronto_string_of_its_own_and_the_signals_string_is_what_it_was():
    assert pronto.encode(WITH) == pronto.encode(WITHOUT)
    ending = pronto.encode_ending(WITH)
    assert ending == "0000 006D 0002 0000 0008 001E 0008 020D"           # the intro of a signal with no repeat
    assert pronto.encode_ending(WITHOUT) is None
    assert pronto.decode(ending, carrier_hz=CARRIER).intro == (210, 789, 210, 13_805)      # the cycles it rounds to


def test_a_blob_without_an_ending_is_the_blob_it_was():
    text = pronto.encode(WITHOUT)
    words = [int(w, 16) for w in text.split()]
    assert pronto_blob(text) == struct.pack(f">H{len(words)}H", len(words), *words) == pronto_blob(text, None)
    blob = pronto_blob(text)
    assert not has_ending(blob) and blob_ending_pronto(blob) is None and decode_blob(blob, CARRIER).ending == ()


def test_the_ending_follows_the_pronto_words_in_the_same_blob():
    text, ending = pronto.encode(WITH), pronto.encode_ending(WITH)
    blob = pronto_blob(text, ending)
    base = [int(w, 16) for w in text.split()]
    assert blob_words(blob) == base + [2, 0x0008, 0x001E, 0x0008, 0x020D]          # the pair count, then its durations
    assert has_ending(blob) and blob_to_pronto(blob) == text and blob_ending_pronto(blob) == ending
    assert frequency_hz(blob) == 38_029
    signal = decode_blob(blob, CARRIER)
    assert (signal.intro, signal.repeat, signal.ending) == (
        pronto.decode(text, carrier_hz=CARRIER).intro, pronto.decode(text, carrier_hz=CARRIER).repeat, (210, 789, 210, 13_805))
    assert pronto.encode(signal) == text and pronto.encode_ending(signal) == ending


def test_a_blob_with_an_ending_is_a_different_signal_from_one_without():
    assert pronto_blob(pronto.encode(WITH), pronto.encode_ending(WITH)) != pronto_blob(pronto.encode(WITHOUT))


@pytest.mark.parametrize("extra, why", [
    ([0], "burst pair count and then that many pairs"),            # an ending of no pairs
    ([2, 1, 2, 3], "burst pair count and then that many pairs"),   # a pair count of 2 and three words
    ([1, 1, 2, 3], "burst pair count and then that many pairs"),   # a pair count of 1 and three words
    ([1], "burst pair count and then that many pairs"),
])
def test_a_blob_with_a_malformed_tail_is_not_a_blob(extra, why):
    words = [int(w, 16) for w in pronto.encode(WITHOUT).split()] + extra
    blob = struct.pack(f">H{len(words)}H", len(words), *words)
    with pytest.raises(ValidationError, match=why):
        decode_blob(blob, CARRIER)
    with pytest.raises(ValidationError):
        blob_to_pronto(blob)


def test_a_blob_shorter_than_its_header_says_is_not_a_blob():
    words = [int(w, 16) for w in pronto.encode(WITHOUT).split()][:-2]
    with pytest.raises(ValidationError, match="header says"):
        decode_blob(struct.pack(f">H{len(words)}H", len(words), *words), CARRIER)


def test_the_ending_must_be_the_signals_own_frequency_word_and_an_intro_alone():
    text = pronto.encode(WITH)
    for bad in ("0000 0070 0002 0000 0008 001E 0008 020D",        # another frequency word
                "0000 006D 0001 0001 0008 001E 0008 020D",        # a repeat as well
                "0000 006D 0002 0000 0008 001E"):                 # shorter than it says
        with pytest.raises(ValueError, match="an ending is the intro"):
            pronto_blob(text, bad)


def test_the_bundles_own_check_decodes_and_encodes_the_ending_back():
    good = pronto_blob(pronto.encode(WITH), pronto.encode_ending(WITH))
    assert _decode_chunk([(1, good, CARRIER), (2, pronto_blob(pronto.encode(WITHOUT)), CARRIER)]) == []
    words = blob_words(good)
    words[-1] = 0                                             # a gap of no cycles: not a signal
    bad = struct.pack(f">H{len(words)}H", len(words), *words)
    problems = _decode_chunk([(3, bad, CARRIER)])
    assert len(problems) == 1 and problems[0].startswith("signal 3 at 38000 Hz")


# --- through the compile stage, the CLI and the corpus reader, with a protocol of the test's own ----------------------

def _protocol():
    from remote_ledger.protocols.base import Protocol

    def encode(*, device, subdevice, function, carrier_hz, unit_us=None):
        frame = (210, 760 + 136 * (function & 15))
        return IrSignal(carrier_hz=carrier_hz, intro=frame * 2, repeat=frame, ending=(210, 800 + device, 210, 13_800))

    return Protocol(name="ENDTEST", irp="{38k}()", irp_source="a protocol of this test", unit_us=136,
                    nominal_carrier_hz=CARRIER, extent_us=None, bits=8, encode=encode)


@pytest.fixture
def with_the_test_protocol(monkeypatch):
    from remote_ledger.protocols import REGISTRY

    monkeypatch.setitem(REGISTRY, "ENDTEST", _protocol())


DOC = {
    "manufacturer": "Acme", "model": "Ender", "protocol": {"name": "ENDTEST", "carrierHz": CARRIER, "minSends": 1},
    "keys": {"KEY_1": {"forms": [{"id": "primary.irp", "type": "irp", "device": 3, "function": 1,
                                  "confidence": "plausible", "source": "a test"}]}},
}


def test_a_group_compiles_to_its_pronto_string_and_its_ending(with_the_test_protocol, tmp_path):
    from remote_ledger.remote import remote_from_doc

    remote = remote_from_doc(DOC, tmp_path / "remotes" / "acme" / "ender.json")
    text, ending = remote.compile_pair("KEY_1", "primary")
    assert text == remote.compile_group("KEY_1", "primary") == pronto.encode(WITHOUT_SHAPE)
    assert ending == "0000 006D 0002 0000 0008 001F 0008 020D"


WITHOUT_SHAPE = IrSignal(carrier_hz=CARRIER, intro=(210, 896) * 2, repeat=(210, 896))


def test_the_compile_stage_writes_the_ending_beside_the_pronto_string_and_only_for_a_signal_that_has_one(
        with_the_test_protocol, tmp_path):
    from remote_ledger.cli import compiled_artifact
    from remote_ledger.remote import remote_from_doc

    remote = remote_from_doc(DOC, tmp_path / "remotes" / "acme" / "ender.json")
    entry = compiled_artifact(remote)["keys"]["KEY_1"]["candidates"]["primary"]
    assert entry["endingHex"] == "0000 006D 0002 0000 0008 001F 0008 020D" and entry["prontoHex"].startswith("0000 006D 0002 0001")
    plain = remote_from_doc({**DOC, "protocol": {"name": "NEC1", "carrierHz": 38400, "minSends": 1},
                             "keys": {"KEY_1": {"forms": [{"id": "primary.irp", "type": "irp", "device": 1, "subdevice": 2,
                                                          "function": 3, "confidence": "plausible", "source": "x"}]}}},
                            tmp_path / "remotes" / "acme" / "plain.json")
    assert "endingHex" not in compiled_artifact(plain)["keys"]["KEY_1"]["candidates"]["primary"]


def test_the_corpus_reader_puts_the_ending_in_the_blob(with_the_test_protocol, tmp_path):
    import json

    from remote_ledger.bundle.corpus import read_remote

    path = tmp_path / "remotes" / "acme" / "ender.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(DOC), encoding="utf-8")
    record = read_remote(tmp_path, path)
    (name, label, canon, rank, blob), = record.keys
    assert has_ending(blob) and blob_ending_pronto(blob) == "0000 006D 0002 0000 0008 001F 0008 020D"
    assert blob_to_pronto(blob).startswith("0000 006D 0002 0001")


def test_rl_encode_prints_the_ending_on_a_line_of_its_own(with_the_test_protocol, capsys):
    from remote_ledger import cli

    assert cli.main(["encode", "--protocol", "ENDTEST", "--device", "3", "--function", "1", "--carrier", "38000"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("0000 006D 0002 0001") and lines[1] == "ending: 0000 006D 0002 0000 0008 001F 0008 020D"
    assert len(lines) == 2


def test_two_candidates_that_differ_only_in_their_ending_are_not_redundant(with_the_test_protocol, tmp_path):
    from remote_ledger.check import check_distinctness
    from remote_ledger.remote import remote_from_doc

    two = {**DOC, "keys": {"KEY_1": {"forms": [
        {"id": "primary.irp", "type": "irp", "device": 3, "function": 1, "confidence": "plausible", "source": "a test"},
        {"id": "alt.irp", "type": "irp", "device": 40, "function": 1, "confidence": "plausible", "source": "a test",
         "candidate": "alt"}]}}}
    remote = remote_from_doc(two, tmp_path / "remotes" / "acme" / "two.json")
    assert remote.compile_group("KEY_1", "primary") == remote.compile_group("KEY_1", "alt")      # the same Pronto string
    assert check_distinctness(remote, "KEY_1") == []                                              # not the same artifact
