"""D18's gates, enforced mechanically rather than by convention."""

import json
from pathlib import Path

import pytest

from remote_ledger import protocols

VECTOR_INDEX = Path(__file__).parent / "vectors" / "index.json"
V1_REGISTRY = {"NEC1"}
BACKLOG = {"NEC2", "NEC", "Sony12", "Sony15", "RC5", "RC6"}
PHASE_3 = {"Sony20", "Samsung32"}


def test_registry_holds_exactly_the_shipped_protocols():
    """D18: narrow by design. Phase 3 adds Sony20 and Samsung32."""
    assert set(protocols.REGISTRY) == V1_REGISTRY


def test_backlog_is_not_in_the_registry():
    assert not (BACKLOG | PHASE_3) & set(protocols.REGISTRY)


@pytest.mark.parametrize("name", sorted(V1_REGISTRY))
def test_gate_1_irp_string_and_source(name):
    """A registry entry with an empty irp_source fails the suite (D3, D18)."""
    proto = protocols.REGISTRY[name]
    assert proto.irp.strip()
    assert proto.irp_source.strip()
    assert len(proto.irp_source) > 20, "a source has to identify something"


@pytest.mark.parametrize("name", sorted(V1_REGISTRY))
def test_gate_2_vector_status_is_declared(name):
    """Every protocol either HAS a cited vector or says out loud that it doesn't.

    D10 makes an independently cited golden vector a hard gate: it is the only
    test layer that catches a wrong constant. This test does not let a missing
    vector pass silently -- it requires the gap to be recorded, with a reason,
    in tests/vectors/index.json, so the debt is committed rather than
    forgotten.
    """
    entry = json.loads(VECTOR_INDEX.read_text())[name]
    if entry.get("gate2_golden_vector"):
        assert Path(entry["gate2_golden_vector"]).name
    else:
        reason = entry.get("gate2_pending_reason", "")
        assert len(reason) > 60, (
            f"{name} has no cited golden vector, so it is NOT verified per "
            "D18 gate 2; index.json must record why"
        )
        assert entry.get("snapshot_is_evidence") is False


def test_unverified_protocols_are_reported(capsys):
    """Make the gap visible on every run, not only when someone looks."""
    index = json.loads(VECTOR_INDEX.read_text())
    pending = [n for n, e in index.items() if not e.get("gate2_golden_vector")]
    if pending:
        print(
            f"\nD18 gate 2 UNMET for: {', '.join(sorted(pending))} "
            "-- structurally tested, byte-level correctness unproven. "
            "See tests/vectors/CITATIONS.md."
        )
    assert set(index) >= set(protocols.REGISTRY)


def test_unknown_protocol_names_the_gate():
    from remote_ledger.errors import ValidationError

    with pytest.raises(ValidationError, match="D18"):
        protocols.get("Sony20")
