"""The committed corpus, and the generated tree that must match it."""

import json
from pathlib import Path

import pytest

from remote_ledger.generators import PIPELINE, owned_paths, registered
from remote_ledger.remote import load_remote
from remote_ledger.validate import corpus_files, schema_problems, validate_file

ROOT = Path(__file__).resolve().parents[1]
CORPUS = corpus_files(ROOT)
IMPORT_ROOT = ROOT / "remotes" / "lirc"
#: Authored files, tested one by one. Imported ones are thousands and are
#: regenerated rather than written (D39): `rl build --check` validates every
#: one of them in CI, and test_the_imported_tree_keeps_r19 below checks R19's
#: invariants over all of them at once.
AUTHORED = [p for p in CORPUS if IMPORT_ROOT not in p.parents]
IMPORTED = [p for p in CORPUS if IMPORT_ROOT in p.parents]


def test_the_corpus_is_not_empty():
    assert AUTHORED, "Phase 3 authors seed data under remotes/"


@pytest.mark.parametrize("path", AUTHORED, ids=lambda p: p.name)
def test_every_seed_file_validates(path):
    assert [str(p) for p in validate_file(path)] == []


def test_the_imported_tree_keeps_r19():
    """SPEC R19's conditions 2 and 3 over every imported form, and the
    report condition 5 requires. A cheap scan of the JSON, not a load."""
    import re

    from remote_ledger.serialize import load

    shape = re.compile(r"^lirc-remotes@[0-9a-f]{7} remotes/\S+:\d+ \[block ")
    assert (IMPORT_ROOT / "IMPORT.md").is_file()
    assert (IMPORT_ROOT / "COPYING").is_file()
    bad = []
    for path in IMPORTED:
        for key, spec in load(path)["keys"].items():
            for form in spec["forms"]:
                if (form["confidence"] != "plausible" or "verifiedBy" in form
                        or not shape.match(form.get("source", ""))):
                    bad.append(f"{path.relative_to(ROOT)}:{key}")
    assert bad == []


@pytest.mark.parametrize("path", AUTHORED, ids=lambda p: p.name)
def test_every_seed_form_carries_a_citation(path):
    """R5/R18 on real data, not only in the schema."""
    remote = load_remote(path)
    for key, forms in remote.keys.items():
        for form in forms:
            if form.is_derived:
                assert form.derived_from, f"{key}:{form.id}"
            else:
                assert form.source, f"{key}:{form.id} has no citation"


def test_unresolved_records_what_was_checked_and_not_found():
    """R20: a device checked and not found must read differently from one
    nobody has looked at (D12). Each entry is a status -- the device and when
    it was checked -- and the schema forbids carrying a history alongside."""
    entries = json.loads((ROOT / "unresolved.json").read_text())
    assert entries
    assert list(schema_problems(entries, "unresolved.schema.json", "unresolved.json")) == []


def test_a_resolved_device_leaves_unresolved_json():
    """The list is a record of *open* questions, not a log of past ones.

    The Samsung sat here until its protocol was identified as NECx2; now it
    is a remote, so it must not also read as checked-and-not-found. R20's
    three states are only meaningful while each device is in exactly one.
    """
    unresolved = {
        e["device"] for e in json.loads((ROOT / "unresolved.json").read_text())
    }
    controlled = {
        device
        for path in CORPUS
        for device in (load_remote(path).raw.get("controls") or [])
    }
    assert not (unresolved & controlled), "a device is in both states"
    assert "Samsung UN50NU6900F" not in unresolved
    assert "UN50NU6900F" in controlled


def test_every_generator_is_registered():
    """D19 in full: the gate widened by itself at Phases 3, 5 and 6, each
    time by registering a generator rather than editing CI."""
    assert {g.name for g in registered()} == {"check", "compile", "index", "site"}
    assert set(owned_paths()) == {
        "build/warnings.json", "build/pronto", "build/index.json", "site"
    }
    assert [g.name for g in PIPELINE if not g.registered] == []


def test_generated_tree_holds_no_absolute_paths():
    """D20: an absolute path is a non-reproducible value -- it differs
    between a laptop and CI, so the artifact would drift on every machine
    and `rl build --check` would fail for no reason."""
    for owned in owned_paths():
        target = ROOT / owned
        files = [target] if target.is_file() else list(target.rglob("*"))
        for file in files:
            if not file.is_file():
                continue
            text = file.read_text(encoding="utf-8")
            # This machine's paths, not the substring "/home/": an upstream
            # LIRC block is literally named "/home/pi/remote-jbl-iiip.conf",
            # which is data, and reproducible.
            assert str(ROOT) not in text, f"{file}: absolute path"
            assert str(Path.home()) not in text, f"{file}: home directory"


def test_generation_is_byte_reproducible(tmp_path):
    """D20's actual invariant, asserted directly rather than by proxy.

    The first version of this grepped every artifact for the word
    "generated" as a stand-in for "contains a generation timestamp" -- and
    flagged the site's own footer, which says "Generated from the ledger,
    never hand-edited". Regenerating and comparing tests the property that
    matters instead of a spelling that correlates with it.

    Over a sample: every authored remote and every 50th imported one,
    generated twice into separate trees. Comparing the *whole* committed
    tree against a fresh one is exactly `rl build --check`, which CI runs
    beside pytest (D11); repeating it here cost two and a half minutes per
    run once the LIRC import landed.
    """
    import shutil

    from remote_ledger.generators import registered

    sample = tmp_path / "corpus"
    for path in AUTHORED + IMPORTED[::50]:
        target = sample / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    shutil.copyfile(ROOT / "unresolved.json", sample / "unresolved.json")

    trees = []
    for name in ("one", "two"):
        out = tmp_path / name
        for generator in registered():
            assert generator.run(sample, out) == []
        trees.append({
            p.relative_to(out).as_posix(): p.read_bytes()
            for p in sorted(out.rglob("*")) if p.is_file()
        })
    assert trees[0] == trees[1]
    # And an authored remote's artifact is the one committed.
    for path in AUTHORED:
        rel = "build/pronto/" + path.relative_to(ROOT / "remotes").as_posix()
        assert trees[0][rel] == (ROOT / rel).read_bytes(), rel


def test_the_samsung_is_authored_at_an_honest_tier():
    """SPEC section 1 describes this entry as inherited from a sibling
    remote's shared universal address, not a capture of this remote --
    `plausible` is the tier that says so, and IRDB's 7,7 is that shared
    address."""
    path = ROOT / "remotes" / "samsung" / "BN59-01199F.json"
    remote = load_remote(path)
    assert remote.protocol.name == "NECx2"
    for key, forms in remote.keys.items():
        for form in forms:
            assert form.confidence == "plausible", key
            assert "irdb" in (form.source or "").lower()
            assert form.device == form.subdevice == 7  # NECx2 has S = D


def _reverse_bits(byte: int) -> int:
    return int(f"{byte:08b}"[::-1], 2)


def test_the_topping_is_the_capture_read_lsb_first():
    """The RC-15A's first entry transcribed IRremoteESP8266's MSB-first dump
    (0x11EE18E7) straight into NEC1's LSB-first fields, giving device 0x11
    where the remote sends 0x88. The complement check could not see it:
    reversing the bits of a byte and of its complement leaves a complement
    pair. So each form is re-derived here from the value its own citation
    quotes. Forms citing Tasmota's LSB-first `DataLSB` are read as written."""
    import re

    remote = load_remote(ROOT / "remotes" / "topping" / "RC-15A.json")
    assert len(remote.keys) == 13
    for key, forms in remote.keys.items():
        (form,) = forms
        src = form.source or ""
        if m := re.search(r"'[^']+ 0X([0-9A-F]{8})'", src):
            quoted = [int(m[1][i:i + 2], 16) for i in (0, 2, 4, 6)]
            d, s, f, nf = (_reverse_bits(b) for b in quoted)
        else:
            m = re.search(r'DataLSB\\?":\\?"0x([0-9A-F]{8})', src)
            assert m, f"{key}: cites no value it can be re-derived from"
            d, s, f, nf = (int(m[1][i:i + 2], 16) for i in (0, 2, 4, 6))
        assert (form.device, form.subdevice, form.function) == (d, s, f), key
        assert d ^ s == f ^ nf == 0xFF, key
