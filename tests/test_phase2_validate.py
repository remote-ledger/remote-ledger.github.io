"""Semantic validation added in Phase 2 (D16, D21, D26, D27, D33)."""

import json

import pytest

from remote_ledger.validate import validate_file

BASE = {
    "manufacturer": "Topping", "model": "RC-15A",
    "protocol": {"name": "NEC1", "carrierHz": 38000, "minSends": 1},
    "keys": {"KEY_POWER": {"forms": [
        {"id": "primary.irp", "type": "irp", "device": "0x11",
         "subdevice": "0xEE", "function": "0x18",
         "confidence": "verified", "source": "asr forum 10708"},
    ]}},
}


@pytest.fixture
def write(tmp_path):
    def _write(mutate=None):
        doc = json.loads(json.dumps(BASE))
        if mutate:
            mutate(doc)
        p = tmp_path / "r.json"
        p.write_text(json.dumps(doc, indent=2))
        return p
    return _write


def msgs(path):
    return [str(p) for p in validate_file(path)]


def test_clean_file(write):
    assert msgs(write()) == []


@pytest.mark.parametrize("field,value", [
    ("unitUs", 560),
    ("defaultGapUs", 13000),
    ("tolerance", {"relative": 0.2}),
])
def test_overriding_fields_require_a_claims_entry(write, field, value):
    """D27: a reason justifies; only a source lets someone else re-derive."""
    def mutate(d):
        d["protocol"][field] = value
    assert any("claims" in m for m in msgs(write(mutate)))

    def with_claim(d):
        d["protocol"][field] = value
        d["protocol"]["claims"] = {field: {"reason": "why", "source": "where"}}
    assert msgs(write(with_claim)) == []


def test_a_claim_with_no_source_is_not_enough(write):
    def mutate(d):
        d["protocol"]["unitUs"] = 560
        d["protocol"]["claims"] = {"unitUs": {"reason": "worn remote"}}
    assert any("claims" in m for m in msgs(write(mutate)))


def test_undeclared_candidate_tag(write):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "ghost", "type": "irp", "candidate": "ghost",
            "device": 1, "function": 2, "confidence": "untested", "source": "s",
        })
    assert any("variants" in m for m in msgs(write(mutate)))


def test_duplicate_same_type_forms_need_explicit_ids(write):
    def mutate(d):
        forms = d["keys"]["KEY_POWER"]["forms"]
        del forms[0]["id"]
        forms.append({k: v for k, v in forms[0].items()} | {"function": "0x19"})
    assert any("explicit `id`" in m for m in msgs(write(mutate)))


def test_odd_length_raw_sequence_unless_truncated(write):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"] = [{
            "type": "raw", "intro": [9024, 4512, 564],
            "confidence": "plausible", "source": "capture",
        }]
    assert any("D4a" in m for m in msgs(write(mutate)))


def test_truncated_permits_an_odd_length_sequence(write):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"] = [{
            "type": "raw", "intro": [9024, 4512, 564], "truncated": True,
            "confidence": "plausible", "source": "capture",
            "claims": {"truncated": {"reason": "r", "source": "s"}},
        }]
    assert msgs(write(mutate)) == []


def test_stale_cache_whose_variant_vanished(write):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "mode2.irp", "type": "irp", "candidate": "mode2",
            "device": 1, "function": 2, "confidence": "untested", "source": "s",
            "expandedFrom": {"variant": "mode2", "form": "primary.irp"},
        })
    assert any("variants" in m or "no longer exists" in m for m in msgs(write(mutate)))
