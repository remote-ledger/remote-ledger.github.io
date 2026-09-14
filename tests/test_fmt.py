"""`rl fmt` (D9, D17, D20, D33)."""

import json

import pytest

from remote_ledger.fmt import format_document
from remote_ledger.pronto import encode
from remote_ledger.remote import load_remote
from remote_ledger.serialize import loads

BASE = {
    "model": "RC-15A", "manufacturer": "Topping",
    "protocol": {"minSends": 1, "carrierHz": 38000, "name": "NEC1"},
    "keys": {"KEY_POWER": {"forms": [
        {"source": "asr forum 10708", "confidence": "verified", "type": "irp",
         "function": 24, "device": 17, "subdevice": 238, "id": "primary.irp"},
    ]}},
}
MODE2 = {"mode2": {"confidence": "untested", "source": "manual p.14",
                   "override": {"subdevice": "0xEA"}}}


@pytest.fixture
def path(tmp_path):
    def _write(doc=None, name="r.json"):
        p = tmp_path / name
        p.write_text(json.dumps(doc or BASE, indent=2))
        return p
    return _write


def test_fmt_is_idempotent(path):
    p = path()
    once = format_document(p)
    p.write_text(once)
    assert format_document(p) == once


def test_fmt_applies_the_declared_key_order(path):
    doc = loads(format_document(path()))
    assert list(doc) == ["manufacturer", "model", "protocol", "keys"]
    assert list(doc["protocol"]) == ["name", "carrierHz", "minSends"]
    assert list(doc["keys"]["KEY_POWER"]["forms"][0])[:3] == ["id", "type", "device"]


def test_fmt_canonicalises_address_spelling(path):
    """Humans read 0x11, not 17."""
    form = loads(format_document(path()))["keys"]["KEY_POWER"]["forms"][0]
    assert form["device"] == "0x11" and form["subdevice"] == "0xEE"


def test_fmt_never_reorders_the_forms_array(path):
    """D20: array order is semantic -- D7's tie-break is array position."""
    doc = json.loads(json.dumps(BASE))
    doc["keys"]["KEY_POWER"]["forms"] = [
        {**doc["keys"]["KEY_POWER"]["forms"][0], "id": "zzz"},
        {**doc["keys"]["KEY_POWER"]["forms"][0], "id": "aaa", "function": 25},
    ]
    out = loads(format_document(path(doc)))
    assert [f["id"] for f in out["keys"]["KEY_POWER"]["forms"]] == ["zzz", "aaa"]


def test_sort_touches_set_like_arrays_only(path):
    doc = json.loads(json.dumps(BASE))
    doc["aliases"] = ["b", "a"]
    unsorted = loads(format_document(path(doc)))
    assert unsorted["aliases"] == ["b", "a"]
    sorted_out = loads(format_document(path(doc, name="s.json"), sort=True))
    assert sorted_out["aliases"] == ["a", "b"]


def test_expand_writes_the_variant_longhand_and_is_idempotent(path):
    """Acceptance criterion 6: running it twice changes nothing."""
    doc = json.loads(json.dumps(BASE))
    doc["variants"] = MODE2
    p = path(doc)
    once = format_document(p, expand_variants=True)
    p.write_text(once)
    twice = format_document(p, expand_variants=True)
    assert once == twice
    forms = loads(once)["keys"]["KEY_POWER"]["forms"]
    assert len(forms) == 2
    assert forms[1]["expandedFrom"]["variant"] == "mode2"


def test_refresh_rewrites_a_stale_derived_form(path):
    doc = json.loads(json.dumps(BASE))
    doc["keys"]["KEY_POWER"]["forms"].append({
        "type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
        "confidence": "derived", "derivedFrom": "primary.irp",
    })
    p = path(doc)
    out = loads(format_document(p, refresh=True))
    derived = out["keys"]["KEY_POWER"]["forms"][1]
    remote = load_remote(p, expand_variants=False)
    assert derived["hex"] == encode(remote.render(remote.keys["KEY_POWER"][0]))


def test_refresh_removes_a_cache_whose_variant_stopped_expanding(path):
    """D33 premise 1: a metadata-only variant expands to nothing, so a cache
    claiming to be its expansion is stale by definition."""
    doc = json.loads(json.dumps(BASE))
    doc["variants"] = {"mode2": {"confidence": "untested", "source": "s"}}
    doc["keys"]["KEY_POWER"]["forms"].append({
        "id": "mode2.irp", "type": "irp", "candidate": "mode2",
        "device": 17, "subdevice": 234, "function": 24,
        "confidence": "untested", "source": "s",
        "expandedFrom": {"variant": "mode2", "form": "primary.irp",
                         "overridden": ["subdevice"], "inherited": ["device", "function"]},
    })
    out = loads(format_document(path(doc), refresh=True))
    assert len(out["keys"]["KEY_POWER"]["forms"]) == 1


def test_refresh_does_not_create_caches_that_expand_would(path):
    """--refresh repairs what is there; --expand is what adds."""
    doc = json.loads(json.dumps(BASE))
    doc["variants"] = MODE2
    out = loads(format_document(path(doc), refresh=True))
    assert len(out["keys"]["KEY_POWER"]["forms"]) == 1
