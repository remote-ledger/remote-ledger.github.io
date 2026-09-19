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
    """Finding 15 of the first review. The binary printed phase 2 for
    `rl check` while `rl build` printed phase 3 for the same stage.

    Phase 2 has now implemented `check` and `compile`, so they carry no
    phase marker at all -- an implemented command must not advertise a
    future phase. The assertion covers both directions.
    """
    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0]._choices_actions  # type: ignore[union-attr]
    help_by_name = {a.dest: a.help for a in choices}
    implemented = {"validate", "encode", "build", "check", "compile", "fmt",
                   "index", "lookup"}

    for generator in PIPELINE:
        help_text = help_by_name[generator.name]
        if generator.name in implemented:
            assert "[phase" not in help_text, (generator.name, help_text)
        else:
            assert f"[phase {generator.phase}]" in help_text, (generator.name, help_text)

    for name in ("site",):
        assert "[phase" in help_by_name[name], name


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
    # `source` is present deliberately: without it the form would fail the
    # R5/R18 citation rule instead, and the test would still pass if the
    # derived-confidence restriction regressed. One assertion, one cause.
    form = (
        {"type": "irp", "device": 1, "function": 1}
        if kind == "irp"
        else {"type": "raw", "intro": [9024, 4512]}
    )
    form["source"] = "a real citation, so only the confidence tier is at issue"
    assert _form_problems({**form, "confidence": "verified"}) == [], (
        "control: this form must be valid apart from its confidence tier"
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


# --- third review ----------------------------------------------------------

from test_registry import check_gate_2  # noqa: E402

PARAMS = {"device": 17, "subdevice": 238, "function": 24, "carrier_hz": 38000}
PENDING = "x" * 61


def _golden(tmp_path, text, **overrides):
    (tmp_path / "v.pronto").write_text(text)
    entry = {
        "gate2_golden_vector": "v.pronto",
        "gate2_vector_source": "somebody.example/nec-codes",
        "gate2_vector_params": PARAMS,
        "regression_snapshot": "snapshot.pronto",
        "snapshot_is_evidence": False,
    }
    entry.update(overrides)
    return entry


def _correct_pronto() -> str:
    from remote_ledger.protocols import NEC1
    return encode(NEC1.encode(**PARAMS))


def test_gate_2_success_path_runs(tmp_path):
    """The success branch was dead code containing a NameError: it crashed
    the moment a vector was actually declared, which is the one moment the
    gate is supposed to work."""
    check_gate_2("NEC1", _golden(tmp_path, _correct_pronto()), tmp_path)


def test_gate_2_rejects_the_self_derived_snapshot(tmp_path):
    """Citing the encoder's own output as its own independent vector would
    make D10's hard gate a rubber stamp."""
    entry = _golden(tmp_path, _correct_pronto(), regression_snapshot="v.pronto")
    with pytest.raises(AssertionError, match="own output"):
        check_gate_2("NEC1", entry, tmp_path)


def test_gate_2_fails_when_the_encoder_disagrees(tmp_path):
    """The point of the gate: a wrong constant must fail here."""
    wrong = _correct_pronto().replace("0157", "0156", 1)
    with pytest.raises(AssertionError, match="disagrees with the cited"):
        check_gate_2("NEC1", _golden(tmp_path, wrong), tmp_path)


@pytest.mark.parametrize(
    "missing,match",
    [
        ("gate2_vector_source", "needs a citation"),
        ("gate2_vector_params", "must record the protocol parameters"),
    ],
)
def test_gate_2_requires_citation_and_parameters(tmp_path, missing, match):
    entry = _golden(tmp_path, _correct_pronto(), **{missing: None})
    with pytest.raises(AssertionError, match=match):
        check_gate_2("NEC1", entry, tmp_path)


def test_gate_2_pending_path_requires_a_reason(tmp_path):
    with pytest.raises(AssertionError):
        check_gate_2("NEC1", {"gate2_golden_vector": None,
                              "gate2_pending_reason": "too short"}, tmp_path)
    check_gate_2("NEC1", {"gate2_golden_vector": None,
                          "gate2_pending_reason": PENDING,
                          "snapshot_is_evidence": False}, tmp_path)


def test_variant_confidence_cannot_be_derived():
    """D30 restricts `derived` to type pronto, and a variant expands into an
    irp form -- so a variant claiming it would produce exactly the form the
    schema now rejects directly."""
    doc = {
        "manufacturer": "Sony", "model": "RMT-B118P",
        "protocol": {"name": "NEC1", "carrierHz": 38_000, "minSends": 1},
        "variants": {"mode2": {
            "confidence": "derived", "source": "service manual p.14",
            "override": {"subdevice": "0xEA"},
        }},
        "keys": {"KEY_POWER": {"forms": [
            {"type": "irp", "device": 1, "function": 1,
             "confidence": "verified", "source": "table"},
        ]}},
    }
    assert [str(p) for p in schema_problems(doc, "remote.schema.json", "t")]
    doc["variants"]["mode2"]["confidence"] = "untested"
    assert list(schema_problems(doc, "remote.schema.json", "t")) == []


def test_documented_test_count_is_current(request):
    """The count in DESIGN.md section 12 is asserted, not maintained by hand.

    Three documents carried "148 tests" for two rounds after the suite grew.
    R14's principle -- nothing hand-maintained can go stale -- applies to
    prose about the build as much as to the index.
    """
    import re

    # Only meaningful when the whole suite was collected. `len(args) == 1`
    # was not that test: `pytest tests/test_review_regressions.py` satisfies
    # it while collecting a quarter of the suite. Compare the *resolved*
    # arguments against the configured testpaths instead, and bail out on any
    # selection flag, which narrows collection without touching args.
    config = request.config
    if config.option.keyword or config.option.markexpr or config.option.deselect:
        pytest.skip("selection flags narrow collection; count is undefined")
    rootdir = Path(str(config.rootdir))
    configured = [
        (rootdir / p).resolve() for p in config.getini("testpaths")
    ] or [rootdir.resolve()]
    requested = [Path(a.split("::")[0]).resolve() for a in config.args]
    if sorted(requested) != sorted(configured):
        pytest.skip(
            f"partial collection ({', '.join(a.name for a in requested)}); "
            "the documented count is only defined for a full run"
        )

    text = (ROOT / "DESIGN.md").read_text()
    match = re.search(r"Phases 0-5 are implemented: (\d+) tests", text)
    assert match, "DESIGN.md section 12 no longer states a test count"
    documented = int(match.group(1))
    collected = len(request.session.items)
    assert documented == collected, (
        f"DESIGN.md says {documented} tests; this run collected {collected}"
    )


# --- Phase 2 review --------------------------------------------------------

from remote_ledger.check import check_remote  # noqa: E402
from remote_ledger.fmt import format_document  # noqa: E402
from remote_ledger.remote import load_remote  # noqa: E402
from remote_ledger.validate import validate_file  # noqa: E402
from remote_ledger.warnings import RAW_CARRIER_WORD_DRIFT  # noqa: E402

P2_BASE = {
    "manufacturer": "Topping", "model": "RC-15A",
    "protocol": {"name": "NEC1", "carrierHz": 38000, "minSends": 1},
    "keys": {"KEY_POWER": {"forms": [
        {"id": "primary.irp", "type": "irp", "device": "0x11",
         "subdevice": "0xEE", "function": "0x18", "confidence": "verified",
         "source": "asr forum 10708"},
    ]}},
}
P2_VARIANT = {"mode2": {"confidence": "untested", "source": "manual p.14",
                        "override": {"subdevice": "0xEA"}}}


def _p2(tmp_path, mutate=None, name="r.json"):
    import json
    doc = json.loads(json.dumps(P2_BASE))
    if mutate:
        mutate(doc)
    path = tmp_path / name
    path.write_text(json.dumps(doc, indent=2))
    return path


def _errs(path):
    return [str(p) for p in validate_file(path)]


def test_validate_rejects_a_derived_only_group(tmp_path):
    """R15: "every key resolves to at least one compilable form". `rl
    validate` used to accept files `rl check` then rejected."""
    def mutate(d):
        d["keys"]["KEY_HOME"] = {"forms": [{
            "type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
            "confidence": "derived", "derivedFrom": "primary.irp"}]}
    assert any("only derived" in e for e in _errs(_p2(tmp_path, mutate)))


def test_validate_rejects_a_dangling_derived_reference(tmp_path):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "type": "pronto", "hex": "0000 006D 0001 0000 0157 00AC",
            "confidence": "derived", "derivedFrom": "nope"})
    assert any("does not exist" in e for e in _errs(_p2(tmp_path, mutate)))


def test_validate_rejects_an_unrenderable_form(tmp_path):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "type": "pronto", "hex": "0000 0073 0001 0000 0157 00AC",
            "confidence": "plausible", "source": "forum"})
    assert any("cannot render" in e for e in _errs(_p2(tmp_path, mutate)))


def test_a_hand_edited_variant_cache_is_rejected_not_silently_repaired(tmp_path):
    """D33's premise 4, which was missing entirely.

    Loading *replaced* a stale cache before anything compared it, so an
    edited cached subdevice passed both commands with the edit discarded. A
    cache that can be edited without complaint is not a cache -- it is an
    unchecked fork of the variant.
    """
    def mutate(d):
        d["variants"] = P2_VARIANT
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "mode2.irp", "type": "irp", "candidate": "mode2",
            "device": "0x11", "subdevice": "0x00", "function": "0x18",
            "confidence": "untested", "source": "manual p.14",
            "expandedFrom": {"variant": "mode2", "form": "primary.irp",
                             "overridden": ["subdevice"],
                             "inherited": ["device", "function"]}})
    assert any("recomputing that expansion" in e for e in _errs(_p2(tmp_path, mutate)))


def test_a_correct_cache_is_accepted(tmp_path):
    def mutate(d):
        d["variants"] = P2_VARIANT
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "mode2.irp", "type": "irp", "candidate": "mode2",
            "device": 17, "subdevice": 234, "function": 24,
            "confidence": "untested", "source": "manual p.14",
            "expandedFrom": {"variant": "mode2", "form": "primary.irp",
                             "overridden": ["subdevice"],
                             "inherited": ["device", "function"]}})
    assert _errs(_p2(tmp_path, mutate)) == []


def test_a_selected_raw_form_still_gets_its_carrier_checked(tmp_path):
    """Skipping the trusted form first meant the capture that actually
    compiles could declare any carrier without a word ever being compared."""
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"] = [{
            "id": "primary.raw", "type": "raw", "carrierHz": 38400,
            "intro": [9024, 4512], "confidence": "confirmed",
            "source": "capture"}]
    _, warnings = check_remote(load_remote(_p2(tmp_path, mutate)))
    assert RAW_CARRIER_WORD_DRIFT in [w.code for w in warnings]


def test_derived_comparison_is_byte_for_byte(tmp_path):
    """D9 says canonical string, byte for byte. Comparing tokens let a
    derived form drift in whitespace from what the compiler emits."""
    from remote_ledger.pronto import encode
    path = _p2(tmp_path)
    remote = load_remote(path)
    good = encode(remote.render(remote.keys["KEY_POWER"][0]))

    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"].append({
            "type": "pronto", "hex": good.replace(" ", "   "),
            "confidence": "derived", "derivedFrom": "primary.irp"})
    problems, _ = check_remote(load_remote(_p2(tmp_path, mutate, "w.json")))
    assert any("is stale" in p for p in problems)


def test_fmt_canonicalises_lowercase_hex_and_pronto(tmp_path):
    def mutate(d):
        d["keys"]["KEY_POWER"]["forms"][0]["device"] = "0x1a"
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "primary.pronto", "type": "pronto",
            "hex": "0000 006d 0001 0000 0157 00ac",
            "confidence": "plausible", "source": "forum"})
    out = loads(format_document(_p2(tmp_path, mutate)))
    forms = out["keys"]["KEY_POWER"]["forms"]
    assert forms[0]["device"] == "0x1A"
    assert forms[1]["hex"] == "0000 006D 0001 0000 0157 00AC"


def test_fmt_materialises_auto_generated_ids(tmp_path):
    def mutate(d):
        del d["keys"]["KEY_POWER"]["forms"][0]["id"]
    out = loads(format_document(_p2(tmp_path, mutate)))
    assert out["keys"]["KEY_POWER"]["forms"][0]["id"] == "primary.irp"


def test_sort_orders_expanded_from_arrays(tmp_path):
    def mutate(d):
        d["variants"] = P2_VARIANT
        d["keys"]["KEY_POWER"]["forms"].append({
            "id": "mode2.irp", "type": "irp", "candidate": "mode2",
            "device": 17, "subdevice": 234, "function": 24,
            "confidence": "untested", "source": "manual p.14",
            "expandedFrom": {"variant": "mode2", "form": "primary.irp",
                             "overridden": ["subdevice"],
                             "inherited": ["function", "device"]}})
    out = loads(format_document(_p2(tmp_path, mutate), sort=True))
    assert out["keys"]["KEY_POWER"]["forms"][1]["expandedFrom"]["inherited"] == \
        ["device", "function"]


def test_refresh_drops_a_cache_whose_parent_was_removed(tmp_path):
    """`expand()` returns exactly the set that should exist, so a cache for
    any variant outside it is an orphan -- whatever the cause."""
    def mutate(d):
        d["variants"] = P2_VARIANT
        d["keys"]["KEY_POWER"]["forms"] = [
            {"id": "primary.raw", "type": "raw", "intro": [9024, 4512],
             "confidence": "plausible", "source": "capture"},
            {"id": "mode2.irp", "type": "irp", "candidate": "mode2",
             "device": 17, "subdevice": 234, "function": 24,
             "confidence": "untested", "source": "manual p.14",
             "expandedFrom": {"variant": "mode2", "form": "primary.irp",
                              "overridden": ["subdevice"],
                              "inherited": ["device", "function"]}},
        ]
    out = loads(format_document(_p2(tmp_path, mutate), refresh=True))
    assert [f["id"] for f in out["keys"]["KEY_POWER"]["forms"]] == ["primary.raw"]


def test_compile_writes_the_documented_artifact(tmp_path, monkeypatch):
    from remote_ledger import cli
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "remotes" / "topping"
    src.mkdir(parents=True)
    path = _p2(src, name="RC-15A.json")
    assert cli.main(["compile", str(path)]) == 0
    artifact = tmp_path / "build" / "pronto" / "topping" / "RC-15A.json"
    assert artifact.is_file()
    doc = loads(artifact.read_text())
    assert doc["schemaVersion"] == 1
    assert doc["protocol"]["minSends"] == 1           # once, not per key (D3a)
    assert "prontoHex" in doc["keys"]["KEY_POWER"]["candidates"]["primary"]
    # No non-reproducible values -- the rule --check rests on (D20).
    assert not any(k in artifact.read_text() for k in ("generated", "timestamp"))
    assert cli.main(["compile", "--check", str(path)]) == 0
    artifact.write_text(artifact.read_text().replace("0157", "0156"))
    assert cli.main(["compile", "--check", str(path)]) == 1
