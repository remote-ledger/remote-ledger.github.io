"""Variant declaration, expansion and cache semantics (D17, D22, D23, D26, D33)."""

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.forms import load_forms
from remote_ledger.variants import (
    check_cache_cardinality, check_cache_premises, check_declared,
    expand, load_variants, merge_expansions,
)

PARENT = {
    "type": "irp", "device": "0x1A", "subdevice": "0xDA", "function": "0x15",
    "confidence": "verified", "verifiedBy": "sony-table-check",
    "source": "hifi-remote Sony BD table",
}
MODE2 = {"mode2": {"label": "Command mode 2", "confidence": "untested",
                   "source": "service manual p.14",
                   "override": {"subdevice": "0xEA"}}}


def test_override_may_not_name_a_protocol():
    """D23: one protocol per remote file in v1."""
    with pytest.raises(ValidationError, match="D23"):
        load_variants({"m": {"confidence": "untested", "source": "s",
                             "override": {"protocol": "Sony20"}}})


def test_label_defaults_to_the_candidate_id():
    v = load_variants({"m": {"confidence": "untested", "source": "s"}})["m"]
    assert v.label == "m"
    assert not v.expands  # metadata-only: no override (D26)


def test_primary_needs_no_declaration():
    with pytest.raises(ValidationError, match="needs no `variants` entry"):
        load_variants({"primary": {"confidence": "untested", "source": "s"}})


def test_expansion_strips_evidence_and_records_provenance():
    """D22: an expanded form carries two distinct claims needing two
    distinct citations."""
    out = expand("K", load_forms("K", [PARENT]), load_variants(MODE2))
    assert len(out) == 1
    form = out[0]
    assert form["subdevice"] == 0xEA          # overridden
    assert form["device"] == 0x1A             # inherited
    assert form["confidence"] == "untested"   # the variant's, never inherited
    assert form["source"] == "service manual p.14"
    # A complement check that held at the original address is FALSE here.
    assert "verifiedBy" not in form
    assert form["expandedFrom"] == {
        "variant": "mode2", "form": "primary.irp",
        "overridden": ["subdevice"], "inherited": ["device", "function"],
    }


def test_expansion_copies_the_selected_irp_form_not_a_higher_ranked_raw():
    forms = load_forms("K", [
        {"type": "raw", "intro": [1, 1], "confidence": "confirmed", "source": "s"},
        PARENT,
    ])
    out = expand("K", forms, load_variants(MODE2))
    assert out[0]["expandedFrom"]["form"] == "primary.irp"


def test_a_raw_only_key_is_skipped_silently():
    forms = load_forms("K", [
        {"type": "raw", "intro": [1, 1], "confidence": "plausible", "source": "s"},
    ])
    assert expand("K", forms, load_variants(MODE2)) == []


def test_an_authored_irp_form_suppresses_expansion():
    """D17/D33: a form WITHOUT expandedFrom is authored, and that is what
    takes authority from the variant."""
    forms = load_forms("K", [
        PARENT,
        {"type": "irp", "candidate": "mode2", "device": "0x1A",
         "subdevice": "0xEA", "function": "0x15", "confidence": "confirmed",
         "source": "tested on hardware 2026-09-14"},
    ])
    assert expand("K", forms, load_variants(MODE2)) == []


def test_metadata_only_variant_does_not_expand():
    variants = load_variants({"m": {"confidence": "untested", "source": "s"}})
    assert expand("K", load_forms("K", [PARENT]), variants) == []


def test_merge_replaces_in_place_and_is_idempotent():
    """The naive always-append would double on every `--expand` run."""
    raw = [dict(PARENT)]
    fresh = expand("K", load_forms("K", raw), load_variants(MODE2))
    once, changed = merge_expansions(raw, fresh)
    assert changed == ["+mode2"] and len(once) == 2
    twice, changed2 = merge_expansions(once, fresh)
    assert changed2 == [] and twice == once


def test_merge_updates_a_stale_cache_without_appending():
    raw = [dict(PARENT)]
    fresh = expand("K", load_forms("K", raw), load_variants(MODE2))
    stale = dict(fresh[0], subdevice=0x00)
    merged, changed = merge_expansions([dict(PARENT), stale], fresh)
    assert changed == ["~mode2"] and len(merged) == 2
    assert merged[1]["subdevice"] == 0xEA


def test_undeclared_candidate_tag_is_an_error():
    """D26: a compiled fallback cannot exist without a label."""
    forms = load_forms("K", [PARENT, dict(PARENT, candidate="ghost", id="g")])
    with pytest.raises(ValidationError, match="no `variants` entry declares"):
        check_declared("K", forms, load_variants(MODE2))


def test_two_caches_for_one_variant_is_an_error():
    """D21's id uniqueness is per KEY, so distinct explicit ids could both
    claim one variant and make "replace the one match" meaningless."""
    cache = {"type": "irp", "candidate": "mode2", "device": 1, "function": 2,
             "confidence": "untested", "source": "s",
             "expandedFrom": {"variant": "mode2", "form": "primary.irp"}}
    forms = load_forms("K", [PARENT, dict(cache, id="a"), dict(cache, id="b")])
    with pytest.raises(ValidationError, match="claim to be the expansion"):
        check_cache_cardinality("K", forms)


def test_cache_beside_an_authored_irp_form_in_one_variant_group():
    cache = {"type": "irp", "candidate": "mode2", "id": "c", "device": 1,
             "function": 2, "confidence": "untested", "source": "s",
             "expandedFrom": {"variant": "mode2", "form": "primary.irp"}}
    authored = {"type": "irp", "candidate": "mode2", "id": "a", "device": 1,
                "function": 2, "confidence": "confirmed", "source": "s"}
    forms = load_forms("K", [PARENT, cache, authored])
    with pytest.raises(ValidationError, match="at most one"):
        check_cache_cardinality("K", forms)


def test_raw_evidence_may_coexist_with_a_variant_cache():
    """Deliberately permitted: a hardware capture confirming a variant is
    exactly the evidence you want, and it cross-checks against the
    expansion (D33)."""
    cache = {"type": "irp", "candidate": "mode2", "id": "c", "device": 1,
             "function": 2, "confidence": "untested", "source": "s",
             "expandedFrom": {"variant": "mode2", "form": "primary.irp"}}
    capture = {"type": "raw", "candidate": "mode2", "intro": [1, 1],
               "confidence": "confirmed", "source": "captured 2026-09-14"}
    forms = load_forms("K", [PARENT, cache, capture])
    check_cache_cardinality("K", forms)  # no raise


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda v: {}, "no longer exists"),
        (lambda v: load_variants({"mode2": {"confidence": "untested",
                                            "source": "s"}}), "metadata-only"),
    ],
    ids=["variant-deleted", "variant-became-metadata-only"],
)
def test_cache_premises_catch_a_stale_cache(mutate, match):
    """Cardinality alone would let a cache outlive the thing it caches."""
    cache = {"type": "irp", "candidate": "mode2", "id": "c", "device": 1,
             "function": 2, "confidence": "untested", "source": "s",
             "expandedFrom": {"variant": "mode2", "form": "primary.irp"}}
    forms = load_forms("K", [PARENT, cache])
    with pytest.raises(ValidationError, match=match):
        check_cache_premises("K", forms, mutate(None))


def test_cache_premises_catch_the_wrong_parent():
    """Add a higher-confidence irp form and the cache is now derived from
    the wrong parent, even though it still parses."""
    cache = {"type": "irp", "candidate": "mode2", "id": "c", "device": 1,
             "function": 2, "confidence": "untested", "source": "s",
             "expandedFrom": {"variant": "mode2", "form": "primary.irp"}}
    better = {"type": "irp", "id": "better", "device": 1, "function": 2,
              "confidence": "confirmed", "source": "hardware"}
    forms = load_forms("K", [dict(PARENT, id="primary.irp"), better, cache])
    with pytest.raises(ValidationError, match="wrong parent"):
        check_cache_premises("K", forms, load_variants(MODE2))
