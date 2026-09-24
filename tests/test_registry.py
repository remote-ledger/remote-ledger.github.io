"""D18's gates, enforced mechanically rather than by convention."""

import json
import warnings
from pathlib import Path

import pytest

from remote_ledger import pronto, protocols

from reference_quantizers import RULES

VECTORS = Path(__file__).parent / "vectors"
VECTOR_INDEX = VECTORS / "index.json"
V1_REGISTRY = {"NEC1", "NECx2", "Sony20"}
BACKLOG = {"NEC2", "NEC", "Sony12", "Sony15", "RC5", "RC6"}
#: Not backlogged -- `Samsung32` does not exist in any consulted source, and
#: the question it stood for is now answered: the BN59-01199F speaks NECx2,
#: per IRDB (device 7, subdevice 7) and corroborated by IRremoteESP8266's
#: SAMSUNG timings. There was never a Samsung protocol to add.
NONEXISTENT = {"Samsung32"}


def test_registry_holds_exactly_the_shipped_protocols():
    """D18: narrow by design. Phase 3 added Sony20 -- and established that
    Samsung32, which the design listed, does not exist."""
    assert set(protocols.REGISTRY) == V1_REGISTRY


def test_backlog_is_not_in_the_registry():
    assert not (BACKLOG | NONEXISTENT) & set(protocols.REGISTRY)


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
    vector_name = entry.get("gate2b_golden_pronto")

    if not vector_name:
        reason = entry.get("gate2b_pending_reason") or ""
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
    assert entry.get("gate2b_vector_source"), f"{name}: a vector needs a citation (R18)"

    path = vectors_dir / vector_name
    assert path.is_file(), f"{name}: cited vector {path} is missing"
    expected = path.read_text().strip()
    assert expected, f"{name}: cited vector is empty"

    params = entry.get("gate2b_vector_params")
    assert params, (
        f"{name}: index.json must record the protocol parameters the vector "
        "corresponds to, or nothing can be compared against it"
    )
    signal = protocols.REGISTRY[name].encode(**params)
    ours = pronto.encode(signal)
    rule = entry.get("gate2b_vector_quantization", "d6")
    if rule == "d6":
        assert ours == expected, (
            f"{name}: this encoder disagrees with the cited golden vector"
        )
        return

    # The vector came from a tool that converts microseconds to cycles by
    # its own rule (see reference_quantizers). Quantizing OUR timings by
    # THAT rule must reproduce it exactly: this is what checks every
    # duration, extent and lead-out we compute. Our own bytes may then
    # differ, but only where the two rules round differently -- declared,
    # so a change to D6 rule 4, or any other difference, fails here.
    assert rule in RULES, f"{name}: unknown quantization rule {rule!r}"
    assert RULES[rule](signal) == expected, (
        f"{name}: this encoder's timings disagree with the cited golden vector"
    )
    provenance = entry.get("gate2b_vector_provenance")
    assert provenance in ("published", "reproducible"), (
        f"{name}: say whether the vector is published or merely reproducible"
    )
    if provenance == "reproducible":
        assert entry.get("gate2b_vector_reproduce"), (
            f"{name}: a generated vector must state how to regenerate it"
        )
    declared = entry.get("gate2b_byte_divergence")
    assert declared is not None, (
        f"{name}: a vector quantized by another rule must declare where "
        "this encoder's bytes differ from it"
    )
    a, b = ours.split(), expected.split()
    assert len(a) == len(b), f"{name}: word counts differ ({len(a)} vs {len(b)})"
    actual = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert actual == declared, (
        f"{name}: bytes differ from the vector at words {actual}, "
        f"not the declared {declared}"
    )


@pytest.mark.parametrize("name", sorted(V1_REGISTRY))
def test_gate_2b_golden_pronto(name):
    """Either a cited vector is compared here, byte for byte, or the gap is
    recorded with a reason.

    D10 makes an independently cited golden vector a hard gate: it is the
    only test layer that catches a wrong constant. So this *performs* the
    comparison rather than checking that some other test mentions the
    filename -- an indirection that accepted the self-derived snapshot as
    evidence.
    """
    entry = json.loads(VECTOR_INDEX.read_text())[name]
    # D10's hard gate, now that every registry protocol meets it: a protocol
    # that ships without a vector fails here rather than warning. The
    # pending path in check_gate_2 remains for protocols being prepared.
    assert entry.get("gate2b_golden_pronto"), (
        f"{name} is in the registry with no cited golden vector (D10)"
    )
    check_gate_2(name, entry, VECTORS)


@pytest.mark.parametrize("name", sorted(V1_REGISTRY))
def test_gate_2a_structural_vector_is_declared(name):
    """Split from gate 2 once it became clear the two are different claims:
    2a verifies every structural constant against an independent
    implementation; 2b verifies the emitted Pronto bytes. 2a catches the
    16-versus-15 lead-in case D10 names; 2b catches the quantization layer."""
    entry = json.loads(VECTOR_INDEX.read_text())[name]
    vector = entry.get("gate2a_structural_vector")
    if not vector:
        assert len(entry.get("gate2a_pending_reason") or "") > 30
        return
    # A citation, not a shrug -- and one that says what KIND of evidence it
    # is, because a published constant table and a hardware capture do not
    # establish the same thing. A capture carries instrument bias, so it
    # verifies ratios and layout rather than absolute durations.
    assert len(vector) > 40, f"{name}: gate 2a citation is too thin"
    assert "/" in vector, f"{name}: gate 2a citation names no source"
    if "capture" in vector.lower():
        assert "bias" in vector.lower() or "ratio" in vector.lower(), (
            f"{name}: a capture-based vector must say what it cannot establish"
        )


def unverified_protocols() -> list[str]:
    """Registry protocols with no cited golden vector (D18 gate 2)."""
    index = json.loads(VECTOR_INDEX.read_text())
    return sorted(
        n for n in protocols.REGISTRY
        if not index.get(n, {}).get("gate2b_golden_pronto")
    )


def unpublished_protocols() -> list[str]:
    """Registry protocols whose vector is reproducible but not published:
    generated by a pinned release of another tool, not found in print."""
    index = json.loads(VECTOR_INDEX.read_text())
    return sorted(
        n for n in protocols.REGISTRY
        if index.get(n, {}).get("gate2b_golden_pronto")
        and index[n].get("gate2b_vector_provenance") != "published"
    )


def gate_2_report() -> str:
    lines = []
    if pending := unverified_protocols():
        lines.append(
            f"D18 gate 2 UNMET for: {', '.join(pending)} -- structurally "
            "tested, byte-level correctness unproven."
        )
    if generated := unpublished_protocols():
        lines.append(
            f"D18 gate 2b rests on a tool-generated, unpublished vector for: "
            f"{', '.join(generated)}."
        )
    if not lines:
        return ""
    return " ".join(lines) + " See tests/vectors/CITATIONS.md."


def test_every_registry_protocol_has_a_vector_index_entry():
    index = json.loads(VECTOR_INDEX.read_text())
    assert set(index) >= set(protocols.REGISTRY)


def test_unverified_protocols_are_reported():
    """Make the gap visible on every run, not only when someone looks.

    The earlier version printed to stdout, which pytest captures and
    discards unless -s is passed -- so the message appeared zero times in a
    normal run, and the test took a `capsys` fixture it never read. A
    warning surfaces in pytest's warnings summary by default, and the text
    is asserted here rather than merely emitted.
    """
    report = gate_2_report()
    if not report:
        pytest.skip("every registry protocol has a published cited vector")
    for name in unverified_protocols() + unpublished_protocols():
        assert name in report
    assert "CITATIONS.md" in report
    warnings.warn(report, stacklevel=1)


def test_unknown_protocol_names_the_gate():
    from remote_ledger.errors import ValidationError

    with pytest.raises(ValidationError, match="D18"):
        protocols.get("Samsung32")
