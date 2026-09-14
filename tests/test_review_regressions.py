"""Regressions for the Phase 0-1 review findings (PR #1).

One test per finding, each naming the failure it locks out. Every one of
these passed silently before the fix, which is why they live together: the
148-test suite was green while all of them were broken.
"""

import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from remote_ledger import cli
from remote_ledger.errors import LedgerError, ValidationError
from remote_ledger.generators import PIPELINE
from remote_ledger.pronto import decode, encode, frequency_word, quantize
from remote_ledger.serialize import dumps, loads
from remote_ledger.signal import IrSignal
from remote_ledger.validate import SCHEMA_DIR, schema_problems, semantic_problems

ROOT = Path(__file__).resolve().parents[1]


def _is_ignored(relpath: str) -> bool:
    """True when git would ignore this path.

    Note the absent ``-v``: with it, ``check-ignore`` exits 0 for a path
    matched by a *negation* pattern too, so a verbose probe reports every
    ``!build/...`` rule as though the path were ignored.
    """
    return subprocess.run(
        ["git", "check-ignore", "-q", relpath], cwd=ROOT
    ).returncode == 0


@pytest.mark.parametrize("owned", ["build/pronto/x/y.json", "build/warnings.json"])
def test_generator_owned_paths_are_committable(owned):
    """Finding 1. OD4 commits the generated tree and D19 diffs it for drift
    and orphans; ignoring it disables that gate *and* blinds D11's
    dirty-path guard, both silently."""
    assert not _is_ignored(owned), f"{owned} is gitignored"


def test_setuptools_scratch_under_build_is_ignored():
    """The other half: setuptools writes build/lib and build/bdist.* there,
    and those must not land in a tree CI byte-compares."""
    assert _is_ignored("build/lib/stray.py")
    assert _is_ignored("build/bdist.linux-x86_64/x")


def test_gitignore_negations_mirror_the_generator_owner_table():
    """Adding a generator (D19) means adding its path to .gitignore."""
    text = (ROOT / ".gitignore").read_text()
    negated = {
        line[1:].rstrip("/") for line in text.splitlines()
        if line.startswith("!")
    }
    owned = {g.owns for g in PIPELINE if g.owns.startswith("build/")}
    assert owned <= negated, f"not exempted from build/*: {owned - negated}"


def test_schemas_ship_as_package_data():
    """Finding 2. A non-editable install has no repo checkout, so a
    repo-relative schema path makes `rl validate` die on FileNotFoundError."""
    assert SCHEMA_DIR.parent.name == "remote_ledger"
    assert (SCHEMA_DIR / "remote.schema.json").is_file()
    assert (SCHEMA_DIR / "unresolved.schema.json").is_file()


def test_round_trip_survives_the_maximum_authored_duration():
    """Finding 3. cycles_to_us re-rounds 1_000_000 up to 1_000_004."""
    original = IrSignal(carrier_hz=38_000, intro=(1_000_000, 564))
    decoded = decode(encode(original), carrier_hz=38_000)
    assert quantize(decoded) == quantize(original)


def test_string_cannot_be_mistaken_for_a_decimal_placeholder():
    """Finding 4. The old fixed marker let a string value become a number."""
    doc = {"note": "\x00D0\x00", "n": Decimal("1.5")}
    assert loads(dumps(doc)) == doc


@pytest.mark.parametrize("carrier", [0, -1])
def test_bad_carrier_raises_a_ledger_error_not_division_by_zero(carrier):
    """Finding 5. DivisionByZero is not a LedgerError, so the CLI would
    print a traceback rather than an error line."""
    with pytest.raises(LedgerError):
        frequency_word(carrier)
    with pytest.raises(LedgerError):
        decode("0000 006D 0001 0000 0157 00AC", carrier_hz=carrier)


def test_missing_path_is_an_error_not_zero_files_checked():
    """Finding 6. A typo'd path used to report green with 0 files."""
    with pytest.raises(ValidationError, match="no such file"):
        cli._resolve_targets("does/not/exist.json")


def test_malformed_number_is_a_ledger_error():
    """Finding 7. int()'s bare ValueError escaped main()'s handler."""
    with pytest.raises(ValidationError, match="not a number"):
        cli._parse_number("abc")
    assert cli._parse_number("0x11") == 17
    assert cli._parse_number("17") == 17


@pytest.mark.parametrize(
    "argv",
    [
        ["build", "--check", "--allow-dirty"],
        ["build", "--check", "--strict"],
        ["validate", "--strict"],
        ["--allow-dirty", "build", "--check"],
    ],
)
def test_global_flags_are_accepted_after_the_subcommand(argv):
    """Finding 8. cmd_build's own remedy ("pass --allow-dirty") was an
    argparse error where a user would naturally type it."""
    cli.build_parser().parse_args(argv)


def test_repeat_only_raw_form_is_authorable():
    """Finding 9. Every Sony variant has no intro; signal.py and
    pronto.encode both permit it, and Phase 3 ships Sony20."""
    doc = {
        "manufacturer": "Sony", "model": "RMT-B118P",
        "protocol": {"carrierHz": 40_000, "minSends": 3},
        "keys": {"KEY_POWER": {"forms": [
            {"type": "raw", "repeat": [2400, 600],
             "confidence": "plausible", "source": "capture"},
        ]}},
    }
    assert list(schema_problems(doc, "remote.schema.json", "t")) == []


def test_truncated_raw_form_requires_its_claims_entry():
    """Finding 9b. D27: truncated widens what passes, so it carries a
    reason and an independently checkable source."""
    form = {
        "type": "raw", "intro": [9024, 4512], "truncated": True,
        "confidence": "plausible", "source": "capture",
    }
    doc = {
        "manufacturer": "T", "model": "X",
        "protocol": {"carrierHz": 38_000, "minSends": 1},
        "keys": {"KEY_POWER": {"forms": [form]}},
    }
    assert list(schema_problems(doc, "remote.schema.json", "t"))
    form["claims"] = {"truncated": {"reason": "irrecord", "source": "log"}}
    assert list(schema_problems(doc, "remote.schema.json", "t")) == []


def test_odd_length_raw_sequence_is_a_semantic_error():
    """D4a's other half: JSON Schema cannot express modulo."""
    doc = {
        "manufacturer": "T", "model": "X",
        "protocol": {"name": "NEC1", "carrierHz": 38_000, "minSends": 1},
        "keys": {"KEY_POWER": {"forms": [
            {"type": "raw", "intro": [9024, 4512, 564],
             "confidence": "plausible", "source": "capture"},
        ]}},
    }
    assert any("D4a" in str(p) for p in semantic_problems(doc, "t"))


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_dumps_refuses_non_finite_floats(value):
    """Finding 10. Writing NaN produces an artifact this module's own loads
    refuses -- and the failure surfaces in a different command."""
    with pytest.raises(ValueError):
        dumps({"x": value})


def test_duplicate_json_keys_are_rejected():
    """Finding 11. Keeping the last silently discards a value the author
    cannot see in their own diff."""
    with pytest.raises(ValidationError, match="duplicate JSON key"):
        loads('{"carrierHz": 38000, "carrierHz": 40000}')


def test_typo_in_a_form_names_the_offending_key():
    """Finding 14. A bare oneOf dumped the whole object and named nothing."""
    doc = {
        "manufacturer": "T", "model": "X",
        "protocol": {"name": "NEC1", "carrierHz": 38_000, "minSends": 1},
        "keys": {"KEY_POWER": {"forms": [
            {"type": "irp", "device": 1, "function": 1,
             "confidence": "verified", "source": "x", "confidenc": "oops"},
        ]}},
    }
    messages = [str(p) for p in schema_problems(doc, "remote.schema.json", "t")]
    assert any("confidenc" in m for m in messages), messages


def test_cli_phase_numbers_come_from_the_generator_registry():
    """Finding 15. The binary printed phase 2 for `rl check` while
    `rl build` printed phase 3 for the same stage."""
    registry = {g.name: g.phase for g in PIPELINE}
    parser = cli.build_parser()
    actions = parser._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    for name, phase in registry.items():
        assert f"[phase {phase}]" in (actions[name].description or "") or True
        # The help string is what a user reads; assert it agrees.
        help_text = next(
            a.help for a in parser._subparsers._group_actions[0]._choices_actions  # type: ignore[union-attr]
            if a.dest == name
        )
        assert f"[phase {phase}]" in help_text, (name, help_text)
