"""Lints against the two test defects this suite has repeatedly shipped.

Four review rounds each found a test that looked like coverage and asserted
nothing: a parse that was never inspected, a string emptiness check standing
in for a file comparison, an `or True` disjunct, and a `capsys` fixture that
was never read. The pattern is tests confirming that something *ran* rather
than that it did the *right thing*. These two checks catch the mechanical
half of that; the rest is judgement.
"""

import ast
from pathlib import Path

import pytest

TEST_FILES = sorted(Path(__file__).parent.glob("test_*.py"))
CAPTURE_FIXTURES = {"capsys", "capfd", "capsysbinary", "capfdbinary"}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("path", TEST_FILES, ids=lambda p: p.name)
def test_no_assertion_can_always_pass(path):
    """`assert x or True` and `assert True` are decoration, not verification."""
    offenders = []
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if isinstance(test, ast.Constant) and test.value:
            offenders.append((node.lineno, "assert <truthy constant>"))
        elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
            if any(
                isinstance(v, ast.Constant) and v.value for v in test.values
            ):
                offenders.append((node.lineno, "assert ... or <truthy constant>"))
    assert not offenders, f"{path.name}: always-true assertions at {offenders}"


@pytest.mark.parametrize("path", TEST_FILES, ids=lambda p: p.name)
def test_capture_fixtures_are_actually_read(path):
    """Requesting capsys and never reading it means the output is swallowed.

    pytest captures stdout regardless, so a test that prints a report and
    never inspects it produces a message no one sees and nothing checks --
    which is what happened to the D18 gate-2 status line.
    """
    offenders = []
    for node in ast.walk(_parse(path)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in node.args.args} & CAPTURE_FIXTURES
        if not params:
            continue
        used = {
            n.id
            for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        unread = params - used
        if unread:
            offenders.append((node.name, sorted(unread)))
    assert not offenders, f"{path.name}: capture fixtures never read: {offenders}"
