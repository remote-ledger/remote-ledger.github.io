"""Form identity, candidate grouping and precedence (D7, D16, D21)."""

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.forms import (
    PRIMARY, group_by_candidate, load_forms, select, select_irp,
    substitute_truncated_gap,
)

IRP = {"type": "irp", "device": 1, "function": 2, "confidence": "verified", "source": "s"}


def f(**kw):
    return {**IRP, **kw}


def test_auto_id_is_candidate_dot_type():
    forms = load_forms("K", [f()])
    assert forms[0].id == "primary.irp"
    assert forms[0].candidate == PRIMARY


def test_auto_id_is_never_positional():
    """D21: `.2`/`.3` suffixes in array order silently retargeted every
    reference when two same-type forms were reordered."""
    with pytest.raises(ValidationError, match="explicit `id`"):
        load_forms("K", [f(), f(function=3)])


def test_two_same_type_forms_are_fine_with_explicit_ids():
    forms = load_forms("K", [f(id="a"), f(id="b", function=3)])
    assert [x.id for x in forms] == ["a", "b"]


def test_ids_are_unique_within_the_key_not_just_the_group():
    with pytest.raises(ValidationError, match="share the id"):
        load_forms("K", [f(id="x"), f(id="x", candidate="mode2")])


def test_explicit_id_may_not_shadow_an_auto_id():
    with pytest.raises(ValidationError, match="share the id"):
        load_forms("K", [f(), f(id="primary.irp", candidate="mode2", function=3)])


def test_hex_and_int_addresses_both_normalise():
    a = load_forms("K", [f(device="0x11")])[0]
    b = load_forms("K", [f(device=17)])[0]
    assert a.device == b.device == 17


def test_grouping_preserves_array_order():
    forms = load_forms("K", [
        f(id="first"), f(candidate="m2", function=3), f(id="second", function=4),
    ])
    groups = group_by_candidate(forms)
    assert [x.id for x in groups["primary"]] == ["first", "second"]
    assert [x.id for x in groups["m2"]] == ["m2.irp"]


def test_a_duplicated_type_needs_explicit_ids_on_every_form_of_it():
    """Not only on the second. Leaving one implicit would make
    `primary.irp` mean "whichever one nobody named", which is positional in
    spirit and just as fragile (D21)."""
    with pytest.raises(ValidationError, match="explicit `id`"):
        load_forms("K", [f(), f(id="named", function=3)])


def test_precedence_confidence_then_type_then_order():
    forms = load_forms("K", [
        {"type": "raw", "intro": [1, 1], "confidence": "confirmed", "source": "s"},
        f(confidence="verified"),
    ])
    # Confidence outranks type: the confirmed raw capture wins.
    assert select(forms).type == "raw"


def test_precedence_type_breaks_a_confidence_tie():
    forms = load_forms("K", [
        {"type": "raw", "intro": [1, 1], "confidence": "verified", "source": "s"},
        f(confidence="verified"),
    ])
    assert select(forms).type == "irp"


def test_precedence_array_order_is_the_final_tie_break():
    """Without it, two same-tier same-type forms are a coin flip and R12's
    byte-identical guarantee does not hold."""
    forms = load_forms("K", [f(id="first"), f(id="second", function=9)])
    assert select(forms).id == "first"
    reversed_forms = load_forms("K", [f(id="second", function=9), f(id="first")])
    assert select(reversed_forms).id == "second"


def test_derived_forms_are_excluded_from_selection():
    forms = load_forms("K", [
        {"type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
         "confidence": "derived", "derivedFrom": "primary.irp"},
        f(confidence="untested"),
    ])
    assert select(forms).type == "irp"


def test_a_group_of_only_derived_forms_cannot_be_selected_from():
    forms = load_forms("K", [
        {"type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
         "confidence": "derived", "derivedFrom": "primary.irp"},
    ])
    with pytest.raises(ValidationError, match="only derived forms"):
        select(forms)


def test_select_irp_filters_before_ranking():
    """D17: D7 ranks confidence before type, so a confirmed raw capture
    outranks a verified irp form -- and a variant handed that has no address
    to override."""
    forms = load_forms("K", [
        {"type": "raw", "intro": [1, 1], "confidence": "confirmed", "source": "s"},
        f(confidence="verified"),
    ])
    assert select(forms).type == "raw"
    assert select_irp(forms).type == "irp"


def test_select_irp_returns_none_for_a_raw_only_group():
    forms = load_forms("K", [
        {"type": "raw", "intro": [1, 1], "confidence": "plausible", "source": "s"},
    ])
    assert select_irp(forms) is None


# --- D31's gap formula ------------------------------------------------------

GAP = dict(extent_us=108_000, default_gap_us=None, unit_us=564, where="w")


def test_odd_sequence_keeps_every_duration_and_appends_the_gap():
    out = substitute_truncated_gap([9024, 4512, 564], **GAP)
    assert out == (9024, 4512, 564, 93_900)
    assert sum(out) == 108_000


def test_even_sequence_discards_the_declared_bad_final_space():
    """A value declared untrustworthy is not evidence, so it contributes
    nothing -- not even a lower bound."""
    out = substitute_truncated_gap([9024, 4512, 564, 1], **GAP)
    assert out == (9024, 4512, 564, 93_900)


def test_over_extent_is_an_error_never_a_clamp():
    with pytest.raises(ValidationError, match="Clamping would fabricate"):
        substitute_truncated_gap([110_000, 5_000], **GAP)


def test_default_gap_is_used_when_no_extent_is_known():
    out = substitute_truncated_gap(
        [9024, 4512], extent_us=None, default_gap_us=13_000, unit_us=564, where="w"
    )
    assert out == (9024, 13_000)


def test_neither_extent_nor_default_gap_is_an_error_not_a_guess():
    with pytest.raises(ValidationError, match="never\\s+a guess"):
        substitute_truncated_gap(
            [9024, 4512], extent_us=None, default_gap_us=None, unit_us=564, where="w"
        )


def test_empty_sequence_is_rejected_rather_than_defined():
    """n = 0 would make m = -1. Omit the key instead of writing []."""
    with pytest.raises(ValidationError, match="at least one duration"):
        substitute_truncated_gap([], **GAP)
