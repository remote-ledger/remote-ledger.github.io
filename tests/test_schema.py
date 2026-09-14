"""The schema self-tests (D20) and the constraints the schema carries."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"
SCHEMAS = sorted(SCHEMA_DIR.glob("*.json"))


def _walk(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_schema_is_valid(path):
    Draft202012Validator.check_schema(json.loads(path.read_text()))


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_every_array_declares_x_order(path):
    """D20: the annotation is what makes "adding an array forces the question"
    real rather than aspirational. Without this test a new field silently
    inherits whichever rule the implementer assumed.
    """
    missing = [
        loc
        for loc, node in _walk(json.loads(path.read_text()))
        if node.get("type") == "array" and "x-order" not in node
    ]
    assert not missing, f"array nodes without x-order: {missing}"


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_x_order_values_are_machine_readable(path):
    for loc, node in _walk(json.loads(path.read_text())):
        if "x-order" in node:
            assert node["x-order"] in ("semantic", "set"), loc


def test_semantic_arrays_are_the_expected_ones():
    """Order carries meaning for exactly these: D7's tie-break is array
    position, and a layout row's order IS the geometry."""
    schema = json.loads((SCHEMA_DIR / "remote.schema.json").read_text())
    semantic = {
        loc.rsplit(".", 1)[-1]
        for loc, node in _walk(schema)
        if node.get("x-order") == "semantic"
    }
    assert semantic == {"forms", "intro", "repeat", "areas"}


def test_every_numeric_field_declares_bounds():
    """D28's standing rule: a numeric field without bounds is a schema bug."""
    schema = json.loads((SCHEMA_DIR / "remote.schema.json").read_text())
    unbounded = [
        loc
        for loc, node in _walk(schema)
        if node.get("type") in ("integer", "number")
        and not ("minimum" in node and "maximum" in node)
    ]
    assert not unbounded, f"numeric nodes without explicit bounds: {unbounded}"
