"""Gate 2b: our timings against other tools' Pronto output (D18, D10).

Every vector in pronto-vectors.json came from someone else's encoder. Each is
checked two ways:

1. **Timings, exactly.** Quantizing our microsecond signal by the tool's own
   rule (reference_quantizers) must reproduce the tool's bytes word for word.
   That checks every duration we compute -- lead-in, bits, the ``^108m`` and
   ``^45m`` lead-outs -- against an implementation we did not write.
2. **Bytes, where they differ.** D6 rule 4 rounds differently from both
   tools, so our own bytes may differ from a vector, but only at the words
   the manifest declares. A change to D6 rule 4, or any other difference,
   fails.

Only MakeHex rounds against the word's period as we do, and there our
bytes match outright except for the one lead-out its cumulative pair
rounding shifts.
"""

import json
import re
from pathlib import Path

import pytest

from remote_ledger import pronto
from remote_ledger.protocols import REGISTRY

from reference_quantizers import RULES, irpt
from test_registry import check_gate_2

VECTORS = Path(__file__).parent / "vectors"
MANIFEST = json.loads((VECTORS / "pronto-vectors.json").read_text())
SINGLE = [v for v in MANIFEST if "functions" not in v]
SWEEPS = [v for v in MANIFEST if "functions" in v]


def _signal(vector, **extra):
    return REGISTRY[vector["protocol"]].encode(**vector["params"], **extra)


def _differing(a: str, b: str) -> list[int]:
    a, b = a.split(), b.split()
    assert len(a) == len(b)
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


@pytest.mark.parametrize("vector", SINGLE, ids=lambda v: v["file"])
def test_our_timings_reproduce_the_vector_exactly(vector):
    expected = (VECTORS / vector["file"]).read_text().strip()
    assert RULES[vector["quantization"]](_signal(vector)) == expected


@pytest.mark.parametrize("vector", SINGLE, ids=lambda v: v["file"])
def test_our_bytes_differ_only_where_declared(vector):
    expected = (VECTORS / vector["file"]).read_text().strip()
    ours = pronto.encode(_signal(vector))
    assert _differing(ours, expected) == vector["byteDivergence"]


@pytest.mark.parametrize("vector", SWEEPS, ids=lambda v: v["file"])
def test_makehex_sweep(vector):
    """Every function of one address: timings exact under MakeHex's rule,
    and our own bytes identical except where declared."""
    text = (VECTORS / vector["file"]).read_text()
    table = dict(re.findall(r"Function: (\d+)\n([0-9A-F ]+)", text))
    lo, hi = map(int, vector["functions"].split(".."))
    assert sorted(map(int, table)) == list(range(lo, hi + 1))
    rule = RULES[vector["quantization"]]
    for f, expected in table.items():
        expected = expected.strip()
        signal = _signal(vector, function=int(f))
        assert rule(signal) == expected, f"F={f}"
        declared = vector["byteDivergence"].get(f, [])
        assert _differing(pronto.encode(signal), expected) == declared, f"F={f}"


@pytest.mark.parametrize("vector", MANIFEST, ids=lambda v: v["file"])
def test_every_vector_says_where_it_came_from(vector):
    """R18 for test data: a published vector is pinned to a commit, and a
    generated one says exactly how to regenerate it."""
    assert vector["provenance"] in ("published", "reproducible")
    if vector["provenance"] == "published":
        assert re.search(r"/[0-9a-f]{40}/", vector["source"]), "pin a commit"
    else:
        assert vector["reproduce"].strip()
    assert len(vector["note"]) > 60


def test_the_40khz_frequency_word_is_0068():
    """DESIGN section 12's open question, settled: both tools emit 0068 at
    40 kHz, as D6 rule 5's round-half-up does. Remote Central's 0067 is a
    capture-side truncation, not a generation rule."""
    at_40k = [v for v in MANIFEST if v["params"]["carrier_hz"] == 40_000]
    assert at_40k
    for vector in at_40k:
        text = (VECTORS / vector["file"]).read_text()
        words = {line.split()[1] for line in text.splitlines() if line.startswith("0000 ")}
        assert words == {"0068"} == {f"{pronto.frequency_word(40_000):04X}"}


def _entry(tmp_path, text, **overrides):
    (tmp_path / "v.pronto").write_text(text)
    entry = {
        "gate2b_golden_pronto": "v.pronto",
        "gate2b_vector_source": "https://example.invalid/pinned",
        "gate2b_vector_params": {"device": 12, "subdevice": 243, "function": 34,
                                 "carrier_hz": 38_400},
        "gate2b_vector_quantization": "irpt-nominal-carrier",
        "gate2b_vector_provenance": "published",
        "gate2b_byte_divergence": [4, 71, 72, 75],
    }
    entry.update(overrides)
    return entry


def _published_nec1():
    return (VECTORS / "irpt_nec1_D12_F34.pronto").read_text().strip()


def test_gate_accepts_another_rules_vector(tmp_path):
    check_gate_2("NEC1", _entry(tmp_path, _published_nec1()), tmp_path)


def test_gate_catches_a_wrong_timing(tmp_path):
    """A word both rules agree on (a bit mark) changed by one cycle."""
    words = _published_nec1().split()
    words[6] = "0017"
    with pytest.raises(AssertionError, match="timings disagree"):
        check_gate_2("NEC1", _entry(tmp_path, " ".join(words)), tmp_path)


def test_gate_catches_an_undeclared_byte_difference(tmp_path):
    entry = _entry(tmp_path, _published_nec1(), gate2b_byte_divergence=[4, 71, 72])
    with pytest.raises(AssertionError, match="not the declared"):
        check_gate_2("NEC1", entry, tmp_path)


def test_gate_requires_a_regeneration_recipe_for_generated_vectors(tmp_path):
    entry = _entry(tmp_path, _published_nec1(), gate2b_vector_provenance="reproducible")
    with pytest.raises(AssertionError, match="regenerate"):
        check_gate_2("NEC1", entry, tmp_path)


def test_the_reference_rule_is_not_ours():
    """If irpt() quietly became D6 rule 4, the timing check would reduce to
    comparing our bytes with themselves and every declared divergence would
    be wrong. Pin that the two rules really differ on the published NEC1."""
    signal = REGISTRY["NEC1"].encode(device=12, subdevice=243, function=34,
                                     carrier_hz=38_400)
    assert irpt(signal) != pronto.encode(signal)
