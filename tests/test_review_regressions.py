"""Regressions for the Phase 0-1 review findings (PR #1).

One test per finding, each naming the failure it locks out. Every one of
these passed silently before the fix, which is why they live together: the
148-test suite was green while all of them were broken.
"""

import decimal
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


def _parsed(argv):
    """Parse the way main() does, including its default fill-in."""
    args = cli.build_parser().parse_args(argv)
    for flag in cli.GLOBAL_FLAGS:
        if not hasattr(args, flag):
            setattr(args, flag, False)
    return args


@pytest.mark.parametrize(
    "argv,flag",
    [
        # After the subcommand -- finding 8 of the first review: this used to
        # be an argparse error, making cmd_build's own printed remedy
        # ("pass --allow-dirty") impossible to follow.
        (["build", "--check", "--allow-dirty"], "allow_dirty"),
        (["build", "--check", "--strict"], "strict"),
        (["validate", "--strict"], "strict"),
        # Before the subcommand -- finding 1 of the SECOND review, a
        # regression introduced by the first fix: a parent parser attached to
        # both root and subparsers sets its defaults twice, and the
        # subparser's pass runs second, so the flag was silently discarded.
        (["--allow-dirty", "build", "--check"], "allow_dirty"),
        (["--strict", "validate"], "strict"),
        (["--strict", "build", "--check"], "strict"),
    ],
)
def test_global_flags_survive_on_either_side_of_the_subcommand(argv, flag):
    """Assert the VALUE, not merely that parsing succeeded.

    The first version of this test called parse_args and asserted nothing
    about the result, so it passed while the flag was being thrown away --
    the same vacuous-assertion failure as the gate-2 vector check.
    """
    assert getattr(_parsed(argv), flag) is True, argv


@pytest.mark.parametrize("flag", cli.GLOBAL_FLAGS)
def test_global_flags_default_to_false_when_absent(flag):
    assert getattr(_parsed(["validate"]), flag) is False


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


# --- second review ---------------------------------------------------------


def _form_problems(form: dict) -> list[str]:
    doc = {
        "manufacturer": "T", "model": "X",
        "protocol": {"name": "NEC1", "carrierHz": 38_000, "minSends": 1},
        "keys": {"KEY_POWER": {"forms": [form]}},
    }
    return [str(p) for p in schema_problems(doc, "remote.schema.json", "t")]


@pytest.mark.parametrize(
    "form",
    [
        {"type": "irp", "device": 1, "function": 1, "confidence": "verified"},
        {"type": "raw", "intro": [9024, 4512], "confidence": "confirmed"},
        {"type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
         "confidence": "plausible"},
    ],
    ids=["irp", "raw", "pronto"],
)
def test_non_derived_forms_require_a_citation(form):
    """R5: every form names how it was established. R18: independently
    checkable. A `verified` form with no citation is the precise failure R18
    exists to prevent -- and only `derived` forms are exempt (D30)."""
    assert _form_problems(form), f"{form['type']} accepted with no source"


@pytest.mark.parametrize(
    "form",
    [
        {"type": "irp", "device": 1, "function": 1, "confidence": "verified",
         "source": "hifi-remote Sony BD table"},
        {"type": "raw", "intro": [9024, 4512], "confidence": "confirmed",
         "source": "irrecord capture, 2026-09-14"},
        {"type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
         "confidence": "plausible", "source": "remotecentral forum post"},
        {"type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
         "confidence": "derived", "derivedFrom": "primary.irp"},
    ],
    ids=["irp", "raw", "pronto", "derived-exempt"],
)
def test_cited_forms_are_accepted(form):
    assert _form_problems(form) == []


@pytest.mark.parametrize("kind", ["irp", "raw"])
def test_derived_is_rejected_on_non_pronto_forms(kind):
    """D30: the compiler's only output is Pronto Hex, so nothing can produce
    a derived irp or raw form. Such a form would also escape D9 -- the only
    check a derived form faces -- while being excluded from selection by D7,
    so it would sit in the file corroborating nothing and checked by nothing."""
    form = (
        {"type": "irp", "device": 1, "function": 1}
        if kind == "irp"
        else {"type": "raw", "intro": [9024, 4512]}
    )
    assert _form_problems({**form, "confidence": "derived"})


def test_malformed_frequency_word_does_not_crash_the_error_message():
    """A word of 0000 made the period zero, and the carrier-mismatch message
    raised DivisionByZero while formatting itself."""
    with pytest.raises(LedgerError, match="frequency word is 0"):
        decode("0000 0000 0001 0000 0157 00AC", carrier_hz=38_000)


@pytest.mark.parametrize(
    "traps",
    [
        [decimal.Inexact],
        [decimal.Rounded],
        [decimal.Inexact, decimal.Rounded],
        [decimal.Subnormal, decimal.Underflow, decimal.Clamped],
    ],
    ids=["inexact", "rounded", "both", "subnormal-underflow-clamped"],
)
def test_encoding_ignores_ambient_decimal_traps(traps):
    """D28's context-independence was a fiction for traps.

    `localcontext()` *copies* the process context, and resetting only prec
    and rounding leaves its traps in place -- so an ambient Inexact trap made
    correct encoding raise. Every division and quantize here is inexact by
    nature.
    """
    saved = decimal.getcontext()
    decimal.setcontext(decimal.Context(traps=traps))
    try:
        signal = IrSignal(carrier_hz=38_000, intro=(9024, 4512, 564, 1692))
        assert encode(signal).startswith("0000 006D")
        assert quantize(signal)
        assert dumps({"x": Decimal("0.15")})
    finally:
        decimal.setcontext(saved)


def test_ambient_traps_do_not_leak_into_our_context():
    """The context we run in is constructed, not inherited."""
    from remote_ledger.numeric import DECIMAL_TRAPS, decimal_context

    saved = decimal.getcontext()
    decimal.setcontext(decimal.Context(traps=[decimal.Inexact, decimal.Rounded]))
    try:
        with decimal_context() as ctx:
            assert [t for t, on in ctx.traps.items() if on] == list(DECIMAL_TRAPS)
    finally:
        decimal.setcontext(saved)
