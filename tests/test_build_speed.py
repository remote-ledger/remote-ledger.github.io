"""The build's speed-ups change nothing observable (R12, D19).

``rl build`` on the full import took 18 minutes. What was done about it is
three kinds of change, and each has to be shown to leave every output byte, every
message and every verdict where it was:

* the pure arithmetic under the encoder is memoised (`quantize`);
* a document the schema accepts is recognised with the references inlined, and
  anything else goes to the schema as written;
* the per-remote loops of the stages run in worker processes, results taken in
  the order of the input.

The last is the one that could silently become scheduling-dependent, so most of
this file runs the same work serially and in parallel and compares the bytes.
"""

from __future__ import annotations

import copy
import json
import os
import random
import time
from decimal import Decimal
from pathlib import Path

import pytest

from remote_ledger import parallel, pronto
from remote_ledger.cli import main
from remote_ledger.errors import BoundsError, ValidationError
from remote_ledger.numeric import decimal_context
from remote_ledger.signal import IrSignal
from remote_ledger.validate import (
    SCHEMA_DIR, _accepting_validator, _validator, schema_problems, validate_files,
)

ROOT = Path(__file__).resolve().parents[1]

# CPython 3.12 counts the threads of the process before fork() and warns when
# there is more than one. Between two pools run back to back, the previous
# pool's manager thread has been joined but the kernel can still list it for a
# moment (most often on a single core), so the warning is raised for a process
# that has no live thread besides the caller. It is shown only to a test runner;
# the command line never sees it (DeprecationWarning, raised outside __main__).
pytestmark = pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded:DeprecationWarning"
)


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    """No worker count leaks in from the environment or out to another test."""
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


# --- the cycle table under quantize -----------------------------------------


def _direct_quantize(signal, word):
    """quantize as it was: one Decimal division per duration, nothing kept."""
    period = pronto.period_us(word)
    return tuple(
        tuple(pronto.us_to_cycles(d, period) for d in seq) for seq in signal.sequences
    )


def test_quantize_returns_what_us_to_cycles_returns():
    rng = random.Random(20260930)
    for carrier in (30_000, 36_000, 36_700, 38_000, 38_400, 40_000, 56_000, 60_000):
        word = pronto.frequency_word(carrier)
        durations = [rng.randint(30, 1_000_000) for _ in range(120)]
        durations += [564, 1692, 9024, 4512, 560, 889]
        signal = IrSignal(
            carrier_hz=carrier,
            intro=tuple(durations[:60]),
            repeat=tuple(durations[60:]),
        )
        expected = _direct_quantize(signal, word)
        assert pronto.quantize(signal) == expected      # cold
        assert pronto.quantize(signal) == expected      # warm, from the table


def test_the_cycle_table_is_bounded_and_dropping_it_changes_nothing(monkeypatch):
    monkeypatch.setattr(pronto, "_CYCLES_TABLE_LIMIT", 5)
    word = pronto.frequency_word(38_000)
    signal = IrSignal(carrier_hz=38_000, intro=tuple(range(400, 1000, 30)) * 1,
                      repeat=(564, 1692))
    assert pronto.quantize(signal) == _direct_quantize(signal, word)
    assert len(pronto._CYCLES_BY_WORD[word]) <= 5 + len(signal.intro) + len(signal.repeat)
    assert pronto.quantize(signal) == _direct_quantize(signal, word)


def test_a_duration_that_rounds_away_is_still_refused_when_cached():
    signal = IrSignal(carrier_hz=38_000, intro=(5, 100))
    for _ in range(2):                  # the second time from the table
        with pytest.raises(BoundsError) as caught:
            pronto.quantize(signal)
        assert "intro[0] (5 us) in cycles is 0" in str(caught.value)


def test_encode_gives_what_it_gave_before_the_table():
    """A snapshot taken from the code as it was before the cycle table, run
    through the table twice."""
    signal = IrSignal(carrier_hz=38_000, intro=(9024, 4512, 564, 1692),
                      repeat=(9024, 2256, 564, 96_000))
    expected = "0000 006D 0002 0002 0157 00AC 0015 0040 0157 0056 0015 0E43"
    assert pronto.encode(signal) == expected
    assert pronto.encode(signal) == expected


def test_decimal_context_is_still_a_fresh_pinned_copy():
    saved = decimal_module_context_prec()
    with decimal_context() as one:
        one.prec = 5                    # a caller scribbling on its context ...
    with decimal_context() as two:
        assert two.prec == 34           # ... does not reach the next entry
    assert decimal_module_context_prec() == saved


def decimal_module_context_prec():
    import decimal
    return decimal.getcontext().prec


# --- the inlined validator ---------------------------------------------------

RICH = {
    "manufacturer": "Acme", "model": "X-1", "aliases": ["X-1B"], "controls": ["Thing"],
    "protocol": {
        "name": "NEC1", "carrierHz": 38000, "unitUs": 560, "minSends": 2,
        "defaultGapUs": 40000,
        "tolerance": {"absUs": 120, "relative": 0.2, "gapUs": 200},
        "claims": {
            "unitUs": {"reason": "worn", "source": "https://example.org/a"},
            "defaultGapUs": {"reason": "worn", "source": "https://example.org/b"},
            "tolerance": {"reason": "worn", "source": "https://example.org/c"},
        },
    },
    "variants": {
        "mode2": {"label": "Mode 2", "confidence": "untested", "source": "manual p.4",
                  "override": {"subdevice": "0xEE", "function": 7}},
        "note": {"confidence": "plausible", "source": "forum"},
    },
    "keys": {
        "KEY_POWER": {"forms": [
            {"id": "primary.irp", "type": "irp", "device": "0x11", "subdevice": 238,
             "function": "0x18", "confidence": "verified", "verifiedBy": "check",
             "source": "asr forum"},
            {"id": "primary.raw", "type": "raw", "intro": [9000, 4500, 560, 560],
             "repeat": [9000, 2250, 560, 40000], "truncated": True, "carrierHz": 38000,
             "confidence": "plausible", "source": "capture",
             "claims": {"truncated": {"reason": "cut", "source": "https://x.test/"}}},
            {"id": "primary.pronto", "type": "pronto", "confidence": "derived",
             "hex": "0000 006D 0000 0002 0158 00AB 0016 0040", "derivedFrom": "primary.irp"},
        ]},
        "KEY_MUTE": {"forms": [
            {"type": "pronto", "hex": "0000 006D 0000 0002 0158 00AB 0016 0040",
             "confidence": "untested", "source": "a manual"},
        ]},
        "KEY_RAW": {"forms": [
            {"type": "raw", "intro": [9000, 4500, 560, 560],
             "confidence": "plausible", "source": "capture"},
        ]},
    },
    "layouts": {
        "original": {"original": True, "areas": ["KEY_POWER KEY_MUTE"],
                     "printedLabels": {"KEY_POWER": "PWR"}, "shape": {"KEY_MUTE": "circle"},
                     "label": "Factory", "source": "photo"},
    },
}

_JUNK = [
    None, True, False, 0, -1, 7, 255, 65535, 65536, 10**9, 0.5, Decimal("0.5"),
    Decimal("3.0"), "", "x", "KEY_X", "0x1F", "0X1f", "0xZZ", "0x12345", "a b", "primary",
    "derived", "irp", "raw", "pronto", "verified", "0000 006D 0000 0001 0158 00AB",
    [], [1, 2], [0], ["a"], {}, {"a": 1}, {"reason": "r"}, {"reason": "r", "source": "s"},
]
_NAMES = ["id", "type", "device", "unknown", "claims", "candidate", "source", "forms",
          "confidence", "hex", "intro", "repeat", "truncated", "KEY_NEW", "9bad", ""]


def _positions(node, prefix=()):
    yield prefix, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _positions(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _positions(v, prefix + (i,))


def _mutate(doc, rng):
    doc = copy.deepcopy(doc)
    for _ in range(rng.choice((1, 1, 2, 3))):
        spots = [(p, n) for p, n in _positions(doc) if p]
        path, _node = rng.choice(spots)
        parent = doc
        for step in path[:-1]:
            parent = parent[step]
        last = path[-1]
        op = rng.choice(("replace", "replace", "replace", "delete", "rename", "add"))
        if op == "replace":
            parent[last] = copy.deepcopy(rng.choice(_JUNK))
        elif op == "delete":
            del parent[last]
        elif op == "rename" and isinstance(parent, dict):
            parent[rng.choice(_NAMES)] = parent.pop(last)
        elif op == "add":
            target = parent[last]
            if isinstance(target, dict):
                target[rng.choice(_NAMES)] = copy.deepcopy(rng.choice(_JUNK))
            elif isinstance(target, list):
                target.append(copy.deepcopy(rng.choice(_JUNK)))
    return doc


_EDGES = [None, True, -1, 0, 255, 65535, 65536, 10**9, Decimal("0.5"), "", "x",
          "0x1F", "0x12345", "derived", [], {}, [0], {"a": 1}]


def _single_point_mutants(doc):
    """Every document one edit away from ``doc``: each value replaced by each
    edge value, each member deleted, each object given an unknown member."""
    for path, node in _positions(doc):
        if not path:
            continue
        for edge in _EDGES:
            mutant = copy.deepcopy(doc)
            parent = mutant
            for step in path[:-1]:
                parent = parent[step]
            parent[path[-1]] = copy.deepcopy(edge)
            yield mutant
        mutant = copy.deepcopy(doc)
        parent = mutant
        for step in path[:-1]:
            parent = parent[step]
        del parent[path[-1]]
        yield mutant
        if isinstance(node, dict):
            mutant = copy.deepcopy(doc)
            target = mutant
            for step in path:
                target = target[step]
            target["unknown"] = 1
            yield mutant
        if isinstance(path[-1], str):
            mutant = copy.deepcopy(doc)
            parent = mutant
            for step in path[:-1]:
                parent = parent[step]
            parent["9 bad name"] = parent.pop(path[-1])
            yield mutant


def _seeds():
    seeds = [copy.deepcopy(RICH)]
    for rel in ("topping/RC-15A.json", "sony/RMT-B118P.json", "samsung/BN59-01199F.json",
                "lirc/2wire/2wire.json", "smartir/Yamaha/media_player_9999.json"):
        from remote_ledger.serialize import load
        doc = load(ROOT / "remotes" / rel)
        # A few keys of each: mutations should land on the shapes, not be
        # spread over the thousands of near-identical keys of a big file.
        doc["keys"] = dict(list(doc["keys"].items())[:5])
        seeds.append(doc)
    return seeds


def test_the_rich_seed_is_valid_so_the_mutations_start_from_something_real():
    assert list(_validator("remote.schema.json").iter_errors(RICH)) == []
    assert _accepting_validator("remote.schema.json").is_valid(RICH)


def test_the_inlined_validator_accepts_exactly_what_the_schema_as_written_accepts():
    """The only way inlining could weaken a check is to accept a document the
    schema rejects. Edit real and synthetic documents -- every value, every
    member, once each, and then random combinations -- and require the two
    validators to agree on every result."""
    original = _validator("remote.schema.json")
    inlined = _accepting_validator("remote.schema.json")
    seeds = _seeds()
    accepted = rejected = 0

    def agree(doc):
        nonlocal accepted, rejected
        want = not any(True for _ in original.iter_errors(doc))
        assert inlined.is_valid(doc) == want, json.dumps(doc, default=str)[:400]
        accepted += want
        rejected += not want

    for seed in (seeds[0], seeds[1]):                       # RICH and the Topping
        for mutant in _single_point_mutants(seed):
            agree(mutant)
    rng = random.Random(20260930)
    for i in range(400):
        agree(_mutate(seeds[i % len(seeds)], rng))
    # Both verdicts occurred often, so agreement is not the agreement of two
    # validators that always say the same thing.
    assert accepted > 50 and rejected > 500, (accepted, rejected)


def test_what_is_said_about_an_invalid_document_is_what_the_schema_as_written_says():
    """The errors, their text and their order come from the schema as written,
    not from the inlined copy (whose schemas, which the sort key includes,
    differ)."""
    original = _validator("remote.schema.json")
    rng = random.Random(7)
    seeds = _seeds()
    checked = 0
    for i in range(200):
        doc = _mutate(seeds[i % len(seeds)], rng)
        errors = sorted(original.iter_errors(doc), key=str)
        said = list(schema_problems(doc, "remote.schema.json", "f.json"))
        assert [(p.path, p.message) for p in said] == [
            ("f.json" + "".join(f"[{q!r}]" for q in e.absolute_path), e.message)
            for e in errors
        ]
        checked += bool(errors)
    assert checked > 50


def test_every_reference_in_the_schema_is_one_the_inliner_resolves():
    """If a reference the inliner leaves alone appears, the inlined copy is
    merely slower, not wrong -- but it should be noticed, because the speed-up
    would be quietly gone."""
    schema = json.loads((SCHEMA_DIR / "remote.schema.json").read_text())
    inlined = _accepting_validator("remote.schema.json").schema
    left = []

    def walk(node, path="#"):
        if isinstance(node, dict):
            if "$ref" in node:
                left.append(path)
            for k, v in node.items():
                if k != "$defs":
                    walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(inlined)
    assert left == [], left
    assert schema["$defs"] == inlined["$defs"]          # the originals are kept


# --- ordered_map -------------------------------------------------------------


def _slow_identity(i):
    time.sleep((12 - i) * 0.004)        # later items finish first
    return i


def _raise_on_two_and_three(i):
    if i == 2:
        raise ValueError("two")
    if i == 3:
        raise KeyError("three")
    return i


def _pid(_):
    return os.getpid()


def _workers_seen_from_inside(_):
    return parallel.worker_count(10_000)


def test_results_come_back_in_input_order_whatever_order_they_finish_in():
    parallel.configure(4)
    assert list(parallel.ordered_map(_slow_identity, range(12))) == list(range(12))


def test_an_explicit_count_uses_other_processes_and_one_uses_none():
    parallel.configure(3)
    pids = set(parallel.ordered_map(_pid, range(30)))
    assert os.getpid() not in pids and 1 <= len(pids) <= 3
    parallel.configure(1)
    assert set(parallel.ordered_map(_pid, range(30))) == {os.getpid()}


def test_the_first_failure_in_input_order_is_the_one_raised():
    parallel.configure(4)
    with pytest.raises(ValueError, match="two"):
        list(parallel.ordered_map(_raise_on_two_and_three, range(8), chunk=1))
    parallel.configure(1)
    with pytest.raises(ValueError, match="two"):
        list(parallel.ordered_map(_raise_on_two_and_three, range(8)))


def test_a_worker_never_starts_a_pool_of_its_own():
    parallel.configure(3)
    assert set(parallel.ordered_map(_workers_seen_from_inside, range(9))) == {1}


def test_an_automatic_count_leaves_a_small_input_alone(monkeypatch):
    monkeypatch.setattr(parallel, "usable_cpus", lambda: 16)
    assert parallel.worker_count(5) == 1
    assert parallel.worker_count(parallel.AUTO_MIN_ITEMS) == 8
    assert set(parallel.ordered_map(_pid, range(5))) == {os.getpid()}


def test_the_automatic_count_is_the_usable_cpus_capped_at_eight(monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(64)))
    assert parallel.worker_count(10_000) == parallel.MAX_AUTO_JOBS == 8
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2, 3})
    assert parallel.worker_count(10_000) == 4           # taskset -c 0-3
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0})
    assert parallel.worker_count(10_000) == 1


def test_the_environment_variable_and_the_flag(monkeypatch):
    monkeypatch.setenv(parallel.ENV_JOBS, "3")
    assert parallel.worker_count(10_000) == parallel.worker_count(2) == 3
    parallel.configure(2)               # the flag beats the environment
    assert parallel.worker_count(10_000) == 2
    parallel.configure(None)
    for bad in ("0", "-2", "many", "1.5"):
        monkeypatch.setenv(parallel.ENV_JOBS, bad)
        with pytest.raises(ValidationError, match="RL_JOBS"):
            parallel.worker_count(10_000)


def test_the_flag_is_global_checked_and_not_remembered(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(["--jobs", "0", "validate"])
    assert caught.value.code == 2
    assert "--jobs" in capsys.readouterr().err
    (tmp_path / "remotes" / "t").mkdir(parents=True)
    (tmp_path / "remotes" / "t" / "a.json").write_text(json.dumps(_nec("A", "1")))

    seen, real = [], parallel.configure
    monkeypatch.setattr(parallel, "configure", lambda n: (seen.append(n), real(n))[1])
    # Before or after the subcommand, short or long -- and gone after the call.
    assert main(["-j", "2", "validate"]) == 0
    assert main(["validate", "--jobs", "1"]) == 0
    assert main(["validate"]) == 0
    assert seen == [2, None, 1, None, None, None]


# --- whole runs, serial against parallel -------------------------------------


def _nec(manufacturer, model, n_keys=6, subdevice="0xEE"):
    return {
        "manufacturer": manufacturer, "model": model,
        "protocol": {"name": "NEC1", "carrierHz": 38000, "minSends": 1},
        "keys": {
            f"KEY_{i}": {"forms": [
                {"id": "primary.irp", "type": "irp", "device": "0x11",
                 "subdevice": subdevice, "function": f"0x{0x10 + i:02X}",
                 "confidence": "plausible", "source": f"test key {i}"},
            ]}
            for i in range(n_keys)
        },
    }


def _corpus(root: Path, *, broken=False) -> Path:
    """Twenty-odd remotes under five directories, covering the shapes the
    stages treat differently: layouts, raw captures, pronto, variants, claims."""
    def put(rel, doc):
        path = root / "remotes" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2) if isinstance(doc, dict) else doc)

    for rel in ("topping/RC-15A.json", "sony/RMT-B118P.json",
                "samsung/BN59-01199F.json", "lirc/2wire/2wire.json",
                "lirc/3m/MP8640.json", "smartir/Yamaha/media_player_9999.json",
                "smartir/Yamaha/media_player_1120.json"):
        put(rel, (ROOT / "remotes" / rel).read_text())
    for i in range(14):
        put(f"gen{i % 4}/model{i:02d}.json", _nec(f"Maker {i % 4}", f"M{i:02d}", 3 + i % 5))
    variant = _nec("Variant Co", "V1")
    variant["variants"] = {"alt": {"label": "Alternate", "confidence": "untested",
                                   "source": "manual p.2", "override": {"subdevice": "0x01"}}}
    put("variant/v1.json", variant)
    (root / "unresolved.json").write_text(json.dumps(
        [{"device": "Some Device", "checked": "2026-09-24"}]))
    if broken:
        no_protocol = _nec("Bad", "NoProtocol")
        del no_protocol["protocol"]
        put("zz_bad/a_no_protocol.json", no_protocol)
        duplicate = _nec("Bad", "DupId")
        duplicate["keys"]["KEY_0"]["forms"].append(dict(duplicate["keys"]["KEY_0"]["forms"][0]))
        put("zz_bad/b_duplicate_id.json", duplicate)
        odd = _nec("Bad", "OddRaw")
        odd["keys"]["KEY_0"]["forms"] = [{
            "id": "primary.raw", "type": "raw", "intro": [9000, 4500, 560],
            "confidence": "plausible", "source": "capture"}]
        put("zz_bad/c_odd_raw.json", odd)
        put("zz_bad/d_not_json.json", "{ this is not json")
    return root


def _stale_derived(root: Path) -> None:
    """Passes validation, fails the cross-check: a derived form that no longer
    matches what its parent compiles to (D9)."""
    doc = _nec("Stale", "S1")
    doc["keys"]["KEY_0"]["forms"].append({
        "id": "primary.pronto", "type": "pronto", "confidence": "derived",
        "hex": "0000 006D 0000 0002 0158 00AB 0016 0040", "derivedFrom": "primary.irp"})
    path = root / "remotes" / "stale" / "s1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for top in ("build", "site") if (root / top).is_dir()
        for p in sorted((root / top).rglob("*")) if p.is_file()
    }


def _run(root: Path, monkeypatch, capsys, *argv) -> tuple[int, str, str]:
    monkeypatch.chdir(root)
    capsys.readouterr()
    status = main(list(argv))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_a_parallel_build_writes_the_bytes_a_serial_build_writes(
    tmp_path, monkeypatch, capsys
):
    serial = _corpus(tmp_path / "serial")
    parallel_ = _corpus(tmp_path / "parallel")
    assert _run(serial, monkeypatch, capsys, "build", "--jobs", "1")[0] == 0
    status, out, err = _run(parallel_, monkeypatch, capsys, "build", "--jobs", "3")
    assert status == 0, err
    one, three = _tree(serial), _tree(parallel_)
    assert one == three
    # Not vacuous: an artifact and a script per remote, an index, a page.
    assert len([p for p in one if p.startswith("build/pronto/")]) == 22
    assert len([p for p in one if p.startswith("site/r/")]) == 22
    assert {"build/index.json", "build/warnings.json", "site/index.html",
            "site/index.json"} <= set(one)
    # And each count gives the same bytes again (a worker count of 2, and of 8).
    for jobs in ("2", "8"):
        other = _corpus(tmp_path / f"jobs{jobs}")
        assert _run(other, monkeypatch, capsys, "build", "--jobs", jobs)[0] == 0
        assert _tree(other) == one


def test_check_agrees_with_the_tree_either_way_and_finds_what_it_always_found(
    tmp_path, monkeypatch, capsys
):
    root = _corpus(tmp_path / "c")
    assert _run(root, monkeypatch, capsys, "build", "--jobs", "3")[0] == 0
    for jobs in ("1", "3"):
        status, out, err = _run(root, monkeypatch, capsys, "build", "--check", "--jobs", jobs)
        assert (status, err) == (0, ""), err
        assert "0 difference(s)" in out

    # Drift in an artifact and a script, an orphan in each tree, a missing file.
    artifact = root / "build" / "pronto" / "gen1" / "model01.json"
    artifact.write_text(artifact.read_text().replace("0000 006D", "0000 006E", 1))
    script = root / "site" / "r" / "gen2" / "model02.js"
    script.write_text(script.read_text() + "// edited\n")
    (root / "build" / "pronto" / "gen1" / "gone.json").write_text("{}\n")
    (root / "site" / "r" / "gen2" / "gone.js").write_text("x\n")
    (root / "build" / "pronto" / "topping" / "RC-15A.json").unlink()

    results = {
        jobs: _run(root, monkeypatch, capsys, "build", "--check", "--jobs", jobs)
        for jobs in ("1", "3")
    }
    assert results["1"] == results["3"]
    status, out, err = results["3"]
    assert status == 1
    lines = err.splitlines()
    assert any("model01.json: drifted" in line for line in lines)
    assert any("model02.js: drifted" in line for line in lines)
    assert any("gen1/gone.json: orphaned" in line for line in lines)
    assert any("gen2/gone.js: orphaned" in line for line in lines)
    assert any("RC-15A.json: missing from the committed tree" in line for line in lines)
    assert "5 difference(s)" in out


def test_validate_says_the_same_things_in_the_same_order_either_way(
    tmp_path, monkeypatch, capsys
):
    root = _corpus(tmp_path / "v", broken=True)
    one = _run(root, monkeypatch, capsys, "validate", "--jobs", "1")
    three = _run(root, monkeypatch, capsys, "validate", "--jobs", "3")
    assert one == three
    status, out, err = three
    assert status == 1
    order = [line for line in err.splitlines() if line.startswith("ERROR ")]
    names = [name for name in ("a_no_protocol", "b_duplicate_id", "c_odd_raw", "d_not_json")
             if any(name in line for line in order)]
    assert names == ["a_no_protocol", "b_duplicate_id", "c_odd_raw", "d_not_json"]
    positions = [next(i for i, line in enumerate(order) if n in line) for n in names]
    assert positions == sorted(positions)
    # The same command over one directory and over one file.
    assert _run(root, monkeypatch, capsys, "validate", "remotes/zz_bad", "--jobs", "3") == \
        _run(root, monkeypatch, capsys, "validate", "remotes/zz_bad", "--jobs", "1")


def test_check_and_build_report_a_stale_derived_form_the_same_way_either_way(
    tmp_path, monkeypatch, capsys
):
    root = _corpus(tmp_path / "s")
    _stale_derived(root)
    for command in (("check",), ("build",), ("build", "--check")):
        one = _run(root, monkeypatch, capsys, *command, "--jobs", "1")
        three = _run(root, monkeypatch, capsys, *command, "--jobs", "3")
        assert one == three, command
        assert one[0] == 1 and "is stale" in one[2], command
    assert not (root / "build" / "pronto").exists()      # nothing written past a failure


def test_a_broken_corpus_stops_the_build_before_anything_is_written(
    tmp_path, monkeypatch, capsys
):
    root = _corpus(tmp_path / "b", broken=True)
    one = _run(root, monkeypatch, capsys, "build", "--jobs", "1")
    three = _run(root, monkeypatch, capsys, "build", "--jobs", "3")
    assert one == three and one[0] == 1
    assert not (root / "build").exists() and not (root / "site").exists()


def test_validate_files_runs_every_rule_on_every_file(tmp_path):
    root = _corpus(tmp_path / "all", broken=True)
    targets = sorted((root / "remotes").glob("**/*.json"))
    parallel.configure(3)
    got = [str(p) for p in validate_files(targets)]
    parallel.configure(1)
    assert got == [str(p) for p in validate_files(targets)]
    assert any("a_no_protocol" in line for line in got)
    assert any("could not be parsed" in line for line in got)
    assert any("even length" in line for line in got)               # a semantic rule
    assert any("share the id" in line for line in got)              # a structural rule


def test_the_site_scripts_do_not_depend_on_the_batch_a_worker_took(tmp_path):
    """payload() still returns the whole mapping; each script is the same
    function of its remote whether one or all of them are computed together."""
    from remote_ledger.site import payload, site_extra
    from remote_ledger.validate import corpus_files
    root = _corpus(tmp_path / "p")
    _, whole = payload(root)
    files = corpus_files(root)
    pieces = {}
    for path in files:
        pieces.update(site_extra(root, [path]))
    assert pieces == whole and list(pieces) == sorted(pieces)


def test_text_the_parent_has_buffered_is_not_written_again_by_the_workers():
    """Workers are forked. A forked copy of a half-full stdout buffer written
    out by each of them would repeat the parent's output once per worker; the
    standard library flushes before forking, and this keeps it that way."""
    import subprocess
    import sys

    script = (
        "from remote_ledger import parallel\n"
        "print('before', end='')            # buffered: stdout is a pipe\n"
        "parallel.configure(3)\n"
        "assert list(parallel.ordered_map(abs, range(-20, 0))) == list(range(20, 0, -1))\n"
        "print(' after')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert done.stdout == "before after\n" and done.stderr == ""
