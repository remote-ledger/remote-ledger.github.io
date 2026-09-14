"""Warning records: scope, ordering and shape (D32)."""

from remote_ledger.warnings import (
    CARRIER_OFF_NOMINAL, RAW_CARRIER_WORD_DRIFT, REDUNDANT_CANDIDATE,
    Warning_, sorted_warnings,
)

FILE = "remotes/topping/RC-15A.json"


def test_location_is_emitted_only_to_the_depth_a_warning_has():
    """A fixed <file>:<key>:<form-id> was wrong for two of the three codes."""
    assert Warning_(CARRIER_OFF_NOMINAL, FILE, "m").location == FILE
    assert Warning_(RAW_CARRIER_WORD_DRIFT, FILE, "m", key="KEY_POWER",
                    candidate="primary", form="primary.raw").location == \
        f"{FILE}:KEY_POWER:primary:primary.raw"


def test_a_pair_warning_names_both_groups():
    """redundant-candidate is about a PAIR; naming one let two distinct
    warnings on a key collide on a supposedly total sort key."""
    w = Warning_(REDUNDANT_CANDIDATE, FILE, "m", key="KEY_POWER",
                 candidate="mode2", peer="mode3")
    assert w.location == f"{FILE}:KEY_POWER:mode2+mode3"
    assert w.sort_key == (FILE, "KEY_POWER", "mode2", "mode3", "", REDUNDANT_CANDIDATE)


def test_absent_scope_fields_are_omitted_not_null():
    assert Warning_(CARRIER_OFF_NOMINAL, FILE, "m").to_json() == {
        "code": CARRIER_OFF_NOMINAL, "file": FILE, "message": "m",
    }


def test_sort_key_is_total_across_pair_warnings_on_one_key():
    a = Warning_(REDUNDANT_CANDIDATE, FILE, "m", key="K", candidate="a", peer="b")
    b = Warning_(REDUNDANT_CANDIDATE, FILE, "m", key="K", candidate="a", peer="c")
    assert a.sort_key != b.sort_key
    assert sorted_warnings([b, a]) == [a, b]


def test_str_is_the_stderr_line_format():
    w = Warning_(CARRIER_OFF_NOMINAL, FILE, "carrier is off nominal")
    assert str(w) == f"WARN {FILE} {CARRIER_OFF_NOMINAL} carrier is off nominal"
