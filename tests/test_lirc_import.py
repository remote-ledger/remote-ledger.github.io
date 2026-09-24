"""``rl import lirc`` (SPEC R19, DESIGN section 14).

Every conf here is synthetic, written for this file: the importer is
tested on what its rules say, not on upstream data.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from remote_ledger.check import check_remote
from remote_ledger.cli import main
from remote_ledger.lirc import parse
from remote_ledger.lirc.importer import (
    IMPORT_ROOT,
    REPORT,
    Report,
    decode_candidates,
    import_block,
    import_tree,
    key_name,
    parse_header,
    write_import,
)
from remote_ledger.lirc.transmit import transmit, transmit_each
from remote_ledger.remote import load_remote
from remote_ledger.validate import corpus_files, validate_file

#: NEC1 as irrecord writes it: the 32 bits as one MSB-first number. This is
#: the Topping RC-15A's Power in IRremoteESP8266's notation, 0x11EE18E7 --
#: exactly the value the ledger once copied into NEC1's LSB-first fields.
NEC1_CONF = """\
# contributed by Ada Example <ada@example.invalid>
# devices being controlled by this remote:
#   DX3 Pro, D50s; unknown
#
begin remote
  name  EXAMPLE_NEC
  bits           16
  flags SPACE_ENC|CONST_LENGTH
  eps            30
  aeps          100
  header       9024  4512
  one           564  1692
  zero          564   564
  ptrail        564
  repeat       9024  2256
  pre_data_bits  16
  pre_data   0x11EE
  gap          108000
  begin codes
    KEY_POWER   0x18E7
    VOL+        0x629D
    KEY_POWER   0x0000
  end codes
end remote
"""

#: The same frame with a plain 40 ms gap: no ^108m extent, so our NEC1
#: rendering does not match what lircd sends, and the key must stay raw.
NEC1_SHORT_GAP = NEC1_CONF.replace("flags SPACE_ENC|CONST_LENGTH", "flags SPACE_ENC").replace(
    "gap          108000", "gap           40000")

RC5_CONF = """\
begin remote
  name  EXAMPLE_RC5
  bits           13
  flags RC5|CONST_LENGTH
  one           889   889
  zero          889   889
  plead         889
  toggle_bit_mask 0x800
  gap          113792
  frequency    36000
  begin codes
    KEY_1   0x1001
  end codes
end remote
"""


def _block(text, name=None):
    config = parse(text, "remotes/example/x.lircd.conf")
    return next(r for r in config.file_order if name is None or r.name == name)


def _import(text):
    report = Report(commit="0" * 40)
    header = parse_header(text)
    result = import_block(_block(text), "remotes/example/x.lircd.conf",
                          header.contributor, report, "remotes/example/x.lircd.conf")
    return result, report


# --- the header (D34, D37) ------------------------------------------------------

def test_header_contributor_drops_the_email():
    assert parse_header(NEC1_CONF).contributor == "Ada Example"


def test_header_controls_split_and_placeholders_dropped():
    assert parse_header(NEC1_CONF).controls == ("DX3 Pro", "D50s")


def test_header_newer_irrecord_format():
    text = "# Device(s) controlled by this remote: BDP-S185\n#\nbegin remote\n"
    assert parse_header(text).controls == ("BDP-S185",)


def test_header_value_continues_on_following_comment_lines():
    text = ("# devices being controlled by this remote:\n"
            "#  SONY BLU RAY BDP S360\n#\n")
    assert parse_header(text).controls == ("SONY BLU RAY BDP S360",)


# --- names (D37) -------------------------------------------------------------------

@pytest.mark.parametrize("original,expected", [
    ("KEY_POWER", "KEY_POWER"),
    ("VOL+", "VOL_PLUS"),
    ("CH-", "CH_MINUS"),
    ("1", "KEY_1"),
    ("Vol-Up", "Vol_Up"),
    ("a.b", "a_b"),
])
def test_key_names_become_css_identifiers(original, expected):
    assert key_name(original) == expected


# --- decoding (D36.1) ----------------------------------------------------------------

def test_an_msb_first_nec_value_decodes_lsb_first():
    """The Topping lesson, as a rule: 0x11EE18E7 as sent is D=0x88, S=0x77."""
    r = _block(NEC1_CONF)
    code = r.get_code_by_name("KEY_POWER")
    (name, params), _ = decode_candidates(r, code)
    assert name == "NEC1"
    assert params == {"device": 0x88, "subdevice": 0x77, "function": 0x18}


def test_a_matching_block_imports_as_irp():
    (protocol, keys), report = _import(NEC1_CONF)
    assert protocol["name"] == "NEC1"
    form = keys["KEY_POWER"]["forms"][0]
    assert form["type"] == "irp"
    assert (form["device"], form["subdevice"], form["function"]) == (0x88, 0x77, 0x18)
    assert "decoded to NEC1 from a parametric block" in form["source"]


def test_a_block_that_does_not_cross_check_stays_raw():
    """No ^108m extent: the decode is a hypothesis the cross-check rejects,
    so the key keeps lircd's own expansion instead."""
    (protocol, keys), _ = _import(NEC1_SHORT_GAP)
    assert "name" not in protocol
    form = keys["KEY_POWER"]["forms"][0]
    assert form["type"] == "raw"
    assert "expanded to raw by lircd 0.10.2's transmit rules" in form["source"]
    first, second = transmit(_block(NEC1_SHORT_GAP), "KEY_POWER")
    assert form["intro"] == list(first) and form["repeat"] == list(second)


def test_identical_sends_leave_only_a_repeat():
    """RC5 resends the whole frame, with the toggle as lircd sends it."""
    (protocol, keys), _ = _import(RC5_CONF)
    form = keys["KEY_1"]["forms"][0]
    assert "intro" not in form and form["repeat"]
    assert protocol == {"carrierHz": 36000, "minSends": 1}
    assert "toggle as lircd sends it on a first press" in form["source"]


def test_a_defaulted_carrier_is_said_to_be_defaulted():
    (protocol, keys), _ = _import(NEC1_CONF)
    assert protocol["carrierHz"] == 38000
    assert "carrier: lircd's default 38 kHz" in keys["KEY_POWER"]["forms"][0]["source"]


# --- keys (D37) -------------------------------------------------------------------------

def test_duplicates_import_the_first_and_report_the_rest():
    (_, keys), report = _import(NEC1_CONF)
    assert keys["KEY_POWER"]["forms"][0]["function"] == 0x18
    assert any("duplicate name" in row[-1] for row in report.skipped_keys)


def test_a_renamed_key_cites_its_original_name():
    (_, keys), _ = _import(NEC1_CONF)
    assert "button 'VOL+'" in keys["VOL_PLUS"]["forms"][0]["source"]


def test_every_imported_form_is_plausible_and_unverified():
    """R19.3: nothing lands above Plausible, and no verifiedBy is written."""
    for text in (NEC1_CONF, NEC1_SHORT_GAP, RC5_CONF):
        (_, keys), _ = _import(text)
        for spec in keys.values():
            for form in spec["forms"]:
                assert form["confidence"] == "plausible"
                assert "verifiedBy" not in form


# --- the whole tree (D37, D39) ---------------------------------------------------------

CITATION = re.compile(
    r"^lirc-remotes@[0-9a-f]{7} remotes/\S+:\d+ \[block .+\]"
    r"( \(contributed by [^)@]+\))?: "
    r"(raw_codes capture|decoded to (NEC1|NECx2|Sony20) from a parametric block"
    r"|expanded to raw by lircd 0\.10\.2's transmit rules)(; .+)?$"
)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "upstream"
    (root / "remotes" / "acme").mkdir(parents=True)
    (root / "remotes" / "acme" / "RC-1.lircd.conf").write_text(NEC1_CONF)
    (root / "remotes" / "acme" / "Two.lircd.conf").write_text(
        NEC1_CONF + "\n" + RC5_CONF)
    (root / "remotes" / "acme" / "mouse.lircmd.conf").write_text("# lircmd\n")
    (root / "remotes" / "topping").mkdir()
    (root / "remotes" / "topping" / "RC-15A.lircd.conf").write_text(NEC1_CONF)
    return root


@pytest.fixture
def ledger(tmp_path):
    root = tmp_path / "ledger"
    (root / "remotes" / "topping").mkdir(parents=True)
    authored = Path(__file__).resolve().parents[1] / "remotes" / "topping" / "RC-15A.json"
    (root / "remotes" / "topping" / "RC-15A.json").write_text(authored.read_text())
    return root


def test_the_import_writes_one_file_per_block(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    written = sorted(p.relative_to(ledger).as_posix()
                     for p in (ledger / IMPORT_ROOT).rglob("*.json"))
    assert written == [
        "remotes/lirc/acme/RC-1.json",
        "remotes/lirc/acme/Two.EXAMPLE_NEC.json",
        "remotes/lirc/acme/Two.EXAMPLE_RC5.json",
    ]
    two = json.loads((ledger / "remotes/lirc/acme/Two.EXAMPLE_RC5.json").read_text())
    assert two["model"] == "Two [EXAMPLE_RC5]" and two["manufacturer"] == "acme"


def test_authored_data_wins(ledger, checkout):
    """R19.4: the upstream topping/RC-15A collides with the authored one."""
    report = write_import(ledger, checkout, "a" * 40)
    assert not (ledger / "remotes/lirc/topping").exists()
    assert report.collisions == [("remotes/topping/RC-15A.lircd.conf", "EXAMPLE_NEC",
                                  "remotes/lirc/topping/RC-15A.json")]


def test_what_is_not_imported_is_reported(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    text = (ledger / IMPORT_ROOT / REPORT).read_text()
    assert "mouse.lircmd.conf" in text and "no remote block" in text
    assert "an authored remote wins" in text or "authored remote wins" in text


def test_imported_files_validate_and_cross_check(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    for path in corpus_files(ledger):
        assert [str(p) for p in validate_file(path)] == []
        problems, _ = check_remote(load_remote(path))
        assert problems == []


def test_every_citation_has_d35s_shape(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    for path in (ledger / IMPORT_ROOT).rglob("*.json"):
        for spec in json.loads(path.read_text())["keys"].values():
            for form in spec["forms"]:
                assert CITATION.match(form["source"]), form["source"]
                assert "@" not in form["source"].split(": ", 1)[0].split("(contributed", 1)[-1]


def test_the_import_is_byte_reproducible(ledger, checkout):
    """R19.5 / D39: the same checkout and commit give the same bytes, and a
    re-run removes what upstream no longer holds."""
    write_import(ledger, checkout, "a" * 40)
    first = {p: p.read_bytes() for p in sorted((ledger / IMPORT_ROOT).rglob("*"))
             if p.is_file()}
    (checkout / "remotes/acme/RC-1.lircd.conf").unlink()
    write_import(ledger, checkout, "a" * 40)
    assert not (ledger / "remotes/lirc/acme/RC-1.json").exists()
    (checkout / "remotes/acme/RC-1.lircd.conf").write_text(NEC1_CONF)
    write_import(ledger, checkout, "a" * 40)
    second = {p: p.read_bytes() for p in sorted((ledger / IMPORT_ROOT).rglob("*"))
              if p.is_file()}
    assert first == second


def test_authored_files_in_the_import_root_survive(ledger, checkout):
    """README.md and COPYING are authored; only *.json and IMPORT.md are the
    importer's (D39)."""
    (ledger / IMPORT_ROOT).mkdir(parents=True)
    (ledger / IMPORT_ROOT / "COPYING").write_text("GPL")
    write_import(ledger, checkout, "a" * 40)
    assert (ledger / IMPORT_ROOT / "COPYING").read_text() == "GPL"


def test_the_cli_refuses_a_checkout_at_another_commit(ledger, checkout, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "-c", "user.name=t", "-c",
                    "user.email=t@t", "commit", "-qm", "x", "--allow-empty"], check=True)
    monkeypatch.chdir(ledger)
    assert main(["import", "lirc", str(checkout), "--commit", "deadbee"]) != 0
    assert "not the requested deadbee" in capsys.readouterr().err
    assert main(["import", "lirc", str(checkout)]) == 0


# --- transmit_each (D36) --------------------------------------------------------------

VECTORS = Path(__file__).parent / "vectors" / "lirc"


@pytest.mark.parametrize("conf", sorted(VECTORS.glob("*.conf")), ids=lambda p: p.name)
def test_transmit_each_equals_a_fresh_transmit_per_button(conf):
    """The importer's fast path must give every button exactly what a fresh
    session would, whatever was sent before it."""
    from remote_ledger.lirc import parse_file
    from remote_ledger.lirc.errors import LircError

    try:
        config = parse_file(conf)
    except LircError:
        pytest.skip("lircd rejects this vector by design")

    def norm(x):
        return (type(x).__name__, str(x)) if isinstance(x, LircError) else x

    for r in config.file_order:
        for code, sent in transmit_each(r):
            try:
                fresh = transmit(r, code)
            except LircError as exc:
                fresh = exc
            assert norm(sent) == norm(fresh), (conf.name, r.name, code.name)


def test_upstream_names_that_differ_only_by_case_stay_apart(ledger, tmp_path):
    """Upstream holds DigiMatrix.lircd.conf beside digimatrix.lircd.conf.
    Their stems would share a path -- one silently overwriting the other --
    and a model name, which R15 forbids; the later one is named by its
    whole file name instead."""
    root = tmp_path / "up2"
    (root / "remotes" / "asus").mkdir(parents=True)
    for name in ("DigiMatrix.lircd.conf", "digimatrix.lircd.conf", "digimatrix.conf"):
        (root / "remotes" / "asus" / name).write_text(NEC1_CONF)
    docs, _ = import_tree(root, "a" * 40, {})
    assert sorted(docs) == [
        "remotes/lirc/asus/DigiMatrix.json",
        "remotes/lirc/asus/digimatrix.conf.json",
        "remotes/lirc/asus/digimatrix.lircd.conf.json",
    ]
    models = [d["model"].casefold() for d in docs.values()]
    assert len(set(models)) == 3
