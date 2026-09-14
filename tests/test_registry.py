"""D18's gates, enforced mechanically rather than by convention."""

import json
from pathlib import Path

import pytest

from remote_ledger import pronto, protocols

VECTORS = Path(__file__).parent / "vectors"
VECTOR_INDEX = VECTORS / "index.json"
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


def check_gate_2(name: str, entry: dict, vectors_dir: Path) -> None:
    """Assert D18 gate 2 for one protocol. Raises AssertionError on failure.

    Factored out so the gate's own success and failure paths are testable
    (see test_review_regressions). The earlier version was only reachable
    through the live index, so its success branch was dead code -- and it
    contained a NameError nobody could see.
    """
    vector_name = entry.get("gate2_golden_vector")

    if not vector_name:
        reason = entry.get("gate2_pending_reason", "")
        assert len(reason) > 60, (
            f"{name} has no cited golden vector, so it is NOT verified per "
            "D18 gate 2; index.json must record why"
        )
        assert entry.get("snapshot_is_evidence") is False
        return

    # A self-derived snapshot proves stability, not correctness. Citing one
    # as the golden vector would make the gate a rubber stamp.
    assert vector_name != entry.get("regression_snapshot"), (
        f"{name}: the regression snapshot is this encoder's own output and "
        "cannot serve as its own independent vector"
    )
    assert entry.get("gate2_vector_source"), f"{name}: a vector needs a citation (R18)"

    path = vectors_dir / vector_name
    assert path.is_file(), f"{name}: cited vector {path} is missing"
    expected = path.read_text().strip()
    assert expected, f"{name}: cited vector is empty"

    params = entry.get("gate2_vector_params")
    assert params, (
        f"{name}: index.json must record the protocol parameters the vector "
        "corresponds to, or nothing can be compared against it"
    )
    signal = protocols.REGISTRY[name].encode(**params)
    assert pronto.encode(signal) == expected, (
        f"{name}: this encoder disagrees with the cited golden vector"
    )


@pytest.mark.parametrize("name", sorted(V1_REGISTRY))
def test_gate_2_golden_vector(name):
    """Either a cited vector is compared here, byte for byte, or the gap is
    recorded with a reason.

    D10 makes an independently cited golden vector a hard gate: it is the
    only test layer that catches a wrong constant. So this *performs* the
    comparison rather than checking that some other test mentions the
    filename -- an indirection that accepted the self-derived snapshot as
    evidence.
    """
    check_gate_2(name, json.loads(VECTOR_INDEX.read_text())[name], VECTORS)


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
