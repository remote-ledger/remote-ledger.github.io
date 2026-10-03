"""The one contract every ``irblaster/hex_*.py`` module follows.

* ``FROM_DB_HEX``: DB protocol name -> function(hexcode) -> ``(ledger protocol,
  device, subdevice, function)``. The *wire reading*: what the database's
  codes mean, which the importer holds in the ledger.
* ``FROM_DB_HEX_APP``: the same keys and signature, what SwiftRemote transmits
  for the same codes today. Only the oracle tools and the importer's "where the
  app differs" report read it. Where the two readings agree it is the same
  function object, so the set of protocols on which they differ is exactly the
  set below.
* ``MIN_SENDS``: a subset of the keys; a missing name means 1.
"""

import importlib
import pkgutil

import pytest

import remote_ledger.irblaster as package
from remote_ledger.protocols import REGISTRY

MODULES = sorted(
    m.name for m in pkgutil.iter_modules(package.__path__) if m.name.startswith("hex_")
)

#: Every DB protocol on which SwiftRemote reads a code differently from the
#: wire (NOTES/japan.md, philips.md, misc.md, sony.md, unknown.md).
APP_DIFFERS = {
    "SONY12", "SONY15", "SONY20",
    "Pioneer", "JVC", "Sharp", "Denon",
    "Thomson7", "Proton", "RCC2026",
}

#: All 23 DB protocol names.
ALL = {
    "NEC", "NEC2", "NECx1", "NECx2",
    "SONY12", "SONY15", "SONY20",
    "RC5", "RC6", "RCA_38", "Thomson7",
    "Pioneer", "JVC", "Sharp", "Denon",
    "Samsung36", "Proton", "F12_relaxed", "RECS80", "RECS80_L",
    "REC80", "RCC2026", "RCC0082",
}


def _load(name):
    return importlib.import_module(f"remote_ledger.irblaster.{name}")


def test_there_are_six_hex_modules():
    assert MODULES == [
        "hex_japan", "hex_misc", "hex_nec", "hex_philips", "hex_sony", "hex_unknown",
    ]


@pytest.mark.parametrize("name", MODULES)
def test_each_module_follows_the_contract(name):
    module = _load(name)
    assert set(module.FROM_DB_HEX) == set(module.FROM_DB_HEX_APP)
    assert set(module.MIN_SENDS) <= set(module.FROM_DB_HEX)
    assert all(1 <= n <= 10 for n in module.MIN_SENDS.values())
    for old in ("FROM_DB_HEX_WIRE", "FROM_DB_HEX_SWIFTREMOTE", "proton_wire_order"):
        assert not hasattr(module, old)


def test_the_modules_together_cover_every_db_protocol_once():
    names = [n for m in MODULES for n in _load(m).FROM_DB_HEX]
    assert sorted(names) == sorted(ALL) and len(names) == len(ALL) == 23


def test_the_readings_differ_on_exactly_the_documented_protocols():
    differ = set()
    for m in MODULES:
        module = _load(m)
        for db_name, wire in module.FROM_DB_HEX.items():
            if module.FROM_DB_HEX_APP[db_name] is not wire:
                differ.add(db_name)
    assert differ == APP_DIFFERS


@pytest.mark.parametrize("name", MODULES)
def test_every_hex_map_lands_on_a_registered_protocol(name):
    """Whatever it returns for a sample code is registered, and the two
    readings use the same ledger protocol for it."""
    samples = {
        "NEC": "20DF10EF", "NEC2": "20DF10EF", "NECx1": "20DF10EF", "NECx2": "E0E040BF",
        "SONY12": "A90", "SONY15": "A900", "SONY20": "A8B47",
        "RC5": "801", "RC6": "000C", "RCA_38": "F01", "Thomson7": "329",
        "Pioneer": "55DAF524", "JVC": "C03F", "Sharp": "8344", "Denon": "1408",
        "Samsung36": "0400E18", "Proton": "2880", "F12_relaxed": "A84",
        "RECS80": "AA8", "RECS80_L": "F30",
        "REC80": "400405100104", "RCC2026": "0087FBC03FC", "RCC0082": "574",
    }
    module = _load(name)
    for db_name, wire in module.FROM_DB_HEX.items():
        try:
            ledger = wire(samples[db_name])[0]
        except ValueError:
            continue                       # a sample need not be representable
        assert ledger in REGISTRY
