"""Schema conformance and the two semantic checks Phase 1 delivers."""

import json
from pathlib import Path

import pytest

from remote_ledger.validate import schema_problems, semantic_problems

GOOD = {
    "manufacturer": "Topping",
    "model": "RC-15A",
    "controls": ["DX3 Pro", "D50s"],
    "protocol": {"name": "NEC1", "carrierHz": 38000, "minSends": 1},
    "keys": {
        "KEY_POWER": {
            "forms": [
                {
                    "type": "irp",
                    "device": "0x11",
                    "subdevice": "0xEE",
                    "function": "0x18",
                    "confidence": "verified",
                    "verifiedBy": "nec-complement-check",
                    "source": "audiosciencereview.com/.../10708",
                }
            ]
        }
    },
}


def errors(doc):
    return [str(p) for p in schema_problems(doc, "remote.schema.json", "t")]


def test_good_document_validates():
    assert errors(GOOD) == []
    assert list(semantic_problems(GOOD, "t")) == []


def test_key_name_must_be_a_css_identifier():
    """D29: R11 leans on CSS grid, which holds only for legal identifiers."""
    doc = json.loads(json.dumps(GOOD))
    doc["keys"]["KEY.POWER"] = doc["keys"].pop("KEY_POWER")
    assert errors(doc)


def test_derived_form_must_not_carry_a_source():
    """D30: derivedFrom IS its citation; a second claim is decoration."""
    doc = json.loads(json.dumps(GOOD))
    doc["keys"]["KEY_POWER"]["forms"].append(
        {
            "type": "pronto",
            "hex": "0000 006D 0022 0002",
            "confidence": "derived",
            "derivedFrom": "primary.irp",
            "source": "should not be here",
        }
    )
    assert errors(doc)


def test_derived_form_without_source_is_fine():
    doc = json.loads(json.dumps(GOOD))
    doc["keys"]["KEY_POWER"]["forms"].append(
        {
            "type": "pronto",
            "hex": "0000 006D 0022 0002",
            "confidence": "derived",
            "derivedFrom": "primary.irp",
        }
    )
    assert errors(doc) == []


def test_non_derived_pronto_may_not_claim_derivedFrom():
    doc = json.loads(json.dumps(GOOD))
    doc["keys"]["KEY_POWER"]["forms"].append(
        {
            "type": "pronto",
            "hex": "0000 006D 0022 0002",
            "confidence": "plausible",
            "derivedFrom": "primary.irp",
            "source": "a forum post",
        }
    )
    assert errors(doc)


def test_variant_may_not_override_protocol():
    """D23: one protocol per remote file in v1."""
    doc = json.loads(json.dumps(GOOD))
    doc["variants"] = {
        "mode2": {
            "confidence": "untested",
            "source": "service manual p.14",
            "override": {"protocol": "Sony20"},
        }
    }
    assert errors(doc)


def test_variant_override_of_subdevice_is_fine():
    doc = json.loads(json.dumps(GOOD))
    doc["variants"] = {
        "mode2": {
            "confidence": "untested",
            "source": "service manual p.14",
            "override": {"subdevice": "0xEA"},
        }
    }
    assert errors(doc) == []


def test_tolerance_relative_is_capped():
    """D28: past 50% the check stops discriminating."""
    doc = json.loads(json.dumps(GOOD))
    doc["protocol"]["tolerance"] = {"relative": 0.9}
    assert errors(doc)


def test_claims_entry_needs_reason_and_source():
    doc = json.loads(json.dumps(GOOD))
    doc["protocol"]["claims"] = {"unitUs": {"reason": "worn remote"}}
    assert errors(doc)


def test_empty_raw_sequence_rejected():
    """D31: a present sequence holds >= 1 duration; omit the key instead."""
    doc = json.loads(json.dumps(GOOD))
    doc["keys"]["KEY_POWER"]["forms"] = [
        {"type": "raw", "intro": [], "confidence": "plausible", "source": "x"}
    ]
    assert errors(doc)


def test_unknown_protocol_is_a_semantic_error():
    doc = json.loads(json.dumps(GOOD))
    doc["protocol"]["name"] = "Sony20"
    assert errors(doc) == []
    assert any("D18" in str(p) for p in semantic_problems(doc, "t"))


def test_unidentified_protocol_forbids_irp_forms():
    """D24: nothing can dispatch an encoder without a protocol."""
    doc = json.loads(json.dumps(GOOD))
    del doc["protocol"]["name"]
    assert errors(doc) == []
    assert any("D24" in str(p) for p in semantic_problems(doc, "t"))


def test_unidentified_protocol_allows_raw_forms():
    doc = json.loads(json.dumps(GOOD))
    del doc["protocol"]["name"]
    doc["keys"]["KEY_POWER"]["forms"] = [
        {
            "type": "raw",
            "intro": [9024, 4512, 564, 564],
            "confidence": "plausible",
            "source": "irrecord capture",
        }
    ]
    assert errors(doc) == []
    assert list(semantic_problems(doc, "t")) == []


def test_at_most_one_of_each_form_type_shape():
    """Unknown properties are rejected -- typos fail loudly (additionalProperties)."""
    doc = json.loads(json.dumps(GOOD))
    doc["keys"]["KEY_POWER"]["forms"][0]["confidenc"] = "verified"
    assert errors(doc)
