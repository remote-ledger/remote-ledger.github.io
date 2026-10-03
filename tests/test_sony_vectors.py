"""Sony12, Sony15 and Sony20 against other people's published frames (D18 gate 2a).

``tests/vectors/sirc-structural.json`` holds three kinds of evidence, each
cited to a pinned commit:

* **IrpTransmogrifier's teaser set** (``src/test/teaserfiles``): 80 captures,
  each paired with the decode IrpTransmogrifier expects. They end in a 500 ms
  gap that is a capture artefact, and their durations are exactly nominal, so
  they check the lead-in, bit shapes, field order and bit order -- not the
  ``^45m`` extent.
* **Girr's Sony12 reference set**: 25 commands at D=1 with their Pronto, which
  *do* carry the 45 ms lead-out. Evidently IrpTransmogrifier's output (the
  first test below shows it matches that tool's rule exactly), committed to a
  sibling repository.
* **One published Sony15 Pronto string** with the decode asserted of it
  (IrpTransmogrifierNGTest testDecodeSony15). Its header and lead-out are not
  ours, and the test below says exactly how.

What none of it can do is tell us what a DB hexcode *means*; that is the
app's, and tests/test_irblaster_hex_sony.py covers it.
"""

import json
from pathlib import Path

import pytest

from remote_ledger import pronto
from remote_ledger.protocols import REGISTRY
from reference_quantizers import RULES
from sirc_reference import field, read_bits

VECTORS = Path(__file__).parent / "vectors"
DATA = json.loads((VECTORS / "sirc-structural.json").read_text())
TEASER = DATA["teaser"]["captures"]
GIRR = DATA["girrSony12"]
ASSERTION = DATA["irptDecodeAssertion"]


def _encode(protocol, params, carrier_hz=40_000):
    return REGISTRY[protocol].encode(
        device=params["device"], subdevice=params.get("subdevice"),
        function=params["function"], carrier_hz=carrier_hz)


def test_the_corpus_covers_every_variant_and_is_not_trivially_small():
    counts = {p: sum(c["protocol"] == p for c in TEASER) for p in ("Sony12", "Sony15", "Sony20")}
    assert counts["Sony12"] >= 20 and counts["Sony15"] >= 40 and counts["Sony20"] >= 10
    assert len(GIRR["commands"]) == 25


@pytest.mark.parametrize("capture", TEASER, ids=lambda c: f"{c['file']}:{c['note']}")
def test_every_teaser_capture_is_reproduced_up_to_its_lead_out(capture):
    """Every duration but the last space. The capture's 500 ms gap stands in
    for the extent, which this does not claim to check."""
    theirs = [int(x) for x in capture["durationsUs"].split()]
    ours = list(_encode(capture["protocol"], capture["params"]).repeat)
    assert len(ours) == len(theirs) + 1
    assert ours[:-1] == theirs


@pytest.mark.parametrize("capture", TEASER, ids=lambda c: f"{c['file']}:{c['note']}")
def test_the_reference_reader_agrees_with_the_published_decode(capture):
    """The reader in sirc_reference.py knows nothing of the encoder. Reading
    a capture's *own* durations must give the D, F and S IrpTransmogrifier
    expects of it, so a mismatch above cannot be the reader's fault."""
    theirs = [int(x) for x in capture["durationsUs"].split()] + [600]
    bits = read_bits(theirs)
    p = capture["params"]
    widths = {"Sony12": 5, "Sony15": 8, "Sony20": 5}[capture["protocol"]]
    assert field(bits, 0, 7) == p["function"]
    assert field(bits, 7, widths) == p["device"]
    if capture["protocol"] == "Sony20":
        assert field(bits, 12, 8) == p["subdevice"]


@pytest.mark.parametrize("command", GIRR["commands"], ids=lambda c: c["name"])
def test_girrs_sony12_pronto_is_reproduced_word_for_word_by_its_own_rule(command):
    """Timings exactly, lead-out included (the rule is IrpTransmogrifier's)."""
    signal = _encode("Sony12", {"device": GIRR["device"], "function": command["function"]})
    assert RULES["irpt-nominal-carrier"](signal) == command["ccf"]


@pytest.mark.parametrize("command", GIRR["commands"], ids=lambda c: c["name"])
def test_our_bytes_differ_from_girrs_only_in_the_lead_out_word(command):
    signal = _encode("Sony12", {"device": GIRR["device"], "function": command["function"]})
    ours, theirs = pronto.encode(signal).split(), command["ccf"].split()
    assert len(ours) == len(theirs) == 30
    differing = [i for i, (a, b) in enumerate(zip(ours, theirs)) if a != b]
    assert differing in ([], [29])


def test_the_published_sony15_string_has_our_frame_but_not_our_header_or_lead_out():
    """D=164, F=61, as asserted by IrpTransmogrifier's decode test. It parses
    to the same sixteen pairs we emit except the last word, and it puts them
    in the once-sequence (n1=16, n2=0) where we use the repeat -- the shape
    IrpTransmogrifier renders for ``*`` is the one we emit, so only the
    decode input differs."""
    words = pronto.parse_words(ASSERTION["pronto"])
    assert words[2:4] == [16, 0], "intro-only, which is how a decode input is written"
    published = words[4:]
    ours = pronto.parse_words(pronto.encode(_encode("Sony15", ASSERTION["params"])))[4:]
    assert len(published) == len(ours) == 32
    assert published[:-1] == ours[:-1], "lead-in, 15 bits, field order, bit order"
    assert published[-1] == 0x0300 and ours[-1] == 0x0315
    # Their string totals 44.4 ms, so it does not claim the 45 ms extent; ours
    # does, and that is IrpTransmogrifier's own render (0318 under its rule).
    assert sum(published) * 25 == 44_400


def test_the_published_sony15_string_decodes_to_the_asserted_parameters():
    """Read independently of the encoder, from its own durations."""
    words = pronto.parse_words(ASSERTION["pronto"])
    durations = [w * 25 for w in words[4:]]
    bits = read_bits(durations)
    assert (field(bits, 0, 7), field(bits, 7, 8)) == (
        ASSERTION["params"]["function"], ASSERTION["params"]["device"])
