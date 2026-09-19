"""R13's cross-check, and the Phase 2 acceptance criteria from DESIGN.md §8."""

import json

import pytest

from remote_ledger.check import check_remote
from remote_ledger.remote import load_remote
from remote_ledger.validate import validate_file
from remote_ledger.warnings import (
    CARRIER_OFF_NOMINAL, RAW_CARRIER_WORD_DRIFT, REDUNDANT_CANDIDATE,
)

BASE = {
    "manufacturer": "Topping", "model": "RC-15A",
    "protocol": {"name": "NEC1", "carrierHz": 38000, "minSends": 1},
    "keys": {"KEY_POWER": {"forms": [
        {"id": "primary.irp", "type": "irp", "device": "0x11",
         "subdevice": "0xEE", "function": "0x18", "confidence": "verified",
         "verifiedBy": "nec-complement-check", "source": "asr forum 10708"},
    ]}},
}


@pytest.fixture
def write(tmp_path):
    def _write(mutate=None, name="r.json"):
        doc = json.loads(json.dumps(BASE))
        if mutate:
            mutate(doc)
        path = tmp_path / name
        path.write_text(json.dumps(doc, indent=2))
        return path
    return _write


def _check(path):
    assert validate_file(path) == [], [str(p) for p in validate_file(path)]
    return check_remote(load_remote(path))


def test_a_clean_file_passes(write):
    """One warning is expected and by design: NEC1's nominal carrier is
    38.4 kHz and this remote declares 38 kHz, which maps to a different
    frequency word. DESIGN D3 calls that the single most common deviation,
    and warns rather than demanding a citation for it."""
    problems, warnings = _check(write())
    assert problems == []
    assert [w.code for w in warnings] == [CARRIER_OFF_NOMINAL]


def test_corrupted_second_form_in_the_same_group_is_caught(write):
    """Acceptance criterion 1."""
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "primary.irp.alt", "type": "irp", "device": "0x11",
            "subdevice": "0xEE", "function": "0x19",  # one bit off
            "confidence": "plausible", "source": "forum",
        })
    problems, _ = _check(write(mutate))
    assert any("disagrees with the trusted form" in p for p in problems)


def test_two_differing_candidates_on_one_key_pass(write):
    """Acceptance criterion 2 -- and the whole reason D16 exists. Different
    subdevices necessarily produce different waveforms, so cross-checking
    them against each other would reject exactly SPEC §1's data."""
    def mutate(d):
        d["variants"] = {"mode2": {"label": "Mode 2", "confidence": "untested",
                                   "source": "manual p.14",
                                   "override": {"subdevice": "0xEA"}}}
    path = write(mutate)
    problems, warnings = _check(path)
    assert problems == []
    remote = load_remote(path)
    assert set(remote.groups("KEY_POWER")) == {"primary", "mode2"}
    # They differ -- which is the point, and not a warning.
    assert remote.compile_group("KEY_POWER", "primary") != remote.compile_group(
        "KEY_POWER", "mode2"
    )
    assert not any(w.code == REDUNDANT_CANDIDATE for w in warnings)


def test_a_derived_only_group_fails_validation_not_compilation(write):
    """Acceptance criterion 3 -- caught at validation, the cheap place."""
    def mutate(d):
        d["keys"]["KEY_HOME"] = {"forms": [{
            "type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
            "confidence": "derived", "derivedFrom": "primary.irp",
        }]}
    problems, _ = check_remote(load_remote(write(mutate)))
    assert any("only derived forms" in p for p in problems)


def test_pronto_form_with_a_mismatched_frequency_word_fails(write):
    """Acceptance criterion 4a. A Pronto string is a generated artifact, not
    a measurement: its word is right or the string is from another remote.

    Caught at *validation* now that R15's "every key resolves to at least one
    compilable form" is actually enforced -- a form that cannot render is a
    validation failure, which is the cheaper place to find it.
    """
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "type": "pronto", "hex": "0000 0073 0001 0000 0157 00AC",
            "confidence": "plausible", "source": "forum",
        })
    path = write(mutate)
    problems = [str(p) for p in validate_file(path)]
    assert any("frequency word" in p for p in problems), problems


def test_raw_form_one_word_off_only_warns(write):
    """Acceptance criterion 4b. A capture's carrier is a measurement with
    instrument error, and quantization is coarse enough to absorb it."""
    def mutate(d):
        # A raw capture that agrees on timing but declares 38.4 kHz.
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "primary.raw", "type": "raw", "carrierHz": 38400,
            "intro": _nec_intro(), "repeat": _nec_repeat(),
            "confidence": "plausible", "source": "irrecord capture",
        })
    problems, warnings = _check(write(mutate))
    assert problems == []
    assert sorted(w.code for w in warnings) == sorted(
        [CARRIER_OFF_NOMINAL, RAW_CARRIER_WORD_DRIFT]
    )


def test_raw_form_several_words_off_fails(write):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "primary.raw", "type": "raw", "carrierHz": 30000,
            "intro": _nec_intro(), "repeat": _nec_repeat(),
            "confidence": "plausible", "source": "irrecord capture",
        })
    problems, _ = _check(write(mutate))
    assert any("word" in p for p in problems)


def test_truncated_capture_exceeding_its_extent_fails_rather_than_clamping(write):
    """Acceptance criterion 5."""
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"] = [{
            "type": "raw", "intro": [110000, 5000], "truncated": True,
            "confidence": "plausible", "source": "capture",
            "claims": {"truncated": {"reason": "recorder stops on release",
                                     "source": "capture log"}},
        }]
    problems, _ = check_remote(load_remote(write(mutate)))
    assert any("Clamping would fabricate" in p for p in problems)


def test_a_valid_truncated_capture_is_padded_to_the_extent(write):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"] = [{
            "type": "raw", "intro": [9024, 4512, 564], "truncated": True,
            "confidence": "plausible", "source": "capture",
            "claims": {"truncated": {"reason": "recorder stops on release",
                                     "source": "capture log"}},
        }]
    path = write(mutate)
    assert validate_file(path) == []
    remote = load_remote(path)
    signal = remote.render(remote.keys["KEY_POWER"][0])
    assert sum(signal.intro) == 108_000


def test_stale_derived_form_is_caught_by_string_comparison(write):
    """D9: comparing canonical strings is stronger than comparing decoded
    signals, and immune to D25's lossy cycles -> microseconds direction."""
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
            "confidence": "derived", "derivedFrom": "primary.irp",
        })
    problems, _ = _check(write(mutate))
    assert any("is stale" in p for p in problems)


def test_a_correct_derived_form_passes(write, tmp_path):
    from remote_ledger.pronto import encode
    path = write()
    remote = load_remote(path)
    good = encode(remote.render(remote.keys["KEY_POWER"][0]))

    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "type": "pronto", "hex": good,
            "confidence": "derived", "derivedFrom": "primary.irp",
        })
    problems, _ = _check(write(mutate, name="r2.json"))
    assert problems == []


def test_identical_candidates_warn_as_redundant(write):
    """D16: compared as compiled Pronto strings by exact equality, not with
    D8's tolerances -- the claim is "these two emit the same artifact"."""
    def mutate(d):
        d["variants"] = {"same": {"confidence": "untested", "source": "s",
                                  "override": {"subdevice": "0xEE"}}}
    _, warnings = _check(write(mutate))
    codes = [w.code for w in warnings]
    assert REDUNDANT_CANDIDATE in codes
    warning = next(w for w in warnings if w.code == REDUNDANT_CANDIDATE)
    assert warning.candidate and warning.peer
    assert warning.candidate < warning.peer  # reported once, not from both sides


def _nec_intro():
    from remote_ledger.protocols import NEC1
    return list(NEC1.encode(device=0x11, subdevice=0xEE, function=0x18,
                            carrier_hz=38000).intro)


def _nec_repeat():
    from remote_ledger.protocols import NEC1
    return list(NEC1.encode(device=0x11, subdevice=0xEE, function=0x18,
                            carrier_hz=38000).repeat)
