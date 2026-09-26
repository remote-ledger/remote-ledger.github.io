"""``rl import smartir`` (SPEC R19, DESIGN section 15).

Every profile here is synthetic, written for this file: the importer is
tested on what its rules say, not on upstream data. (Upstream itself is
checked separately: ``tools/smartir_oracle_compare.py`` cross-checks the
decoder over a live SmartIR checkout, and the real import was run and its
``IMPORT.md`` inspected by hand -- see remotes/smartir/README.md.)
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path

import pytest

from remote_ledger.check import check_remote
from remote_ledger.cli import main
from remote_ledger.remote import load_remote
from remote_ledger.smartir.importer import IMPORT_ROOT, REPORT, key_name, write_import
from remote_ledger.validate import corpus_files, validate_file

#: A minimal valid Pronto Hex string (also used by tests/test_forms.py):
#: 0000 (learned) 006D (carrier word) 0001/0000 (1 burst pair once, 0
#: repeat) 0157 00AC (the one burst pair).
PRONTO_SAMPLE = "0000 006D 0001 0000 0157 00AC"


def _broadlink(*units: int) -> str:
    """base64 of a well-formed Broadlink IR packet, each unit < 256 so no
    escape sequence is needed -- enough to exercise the decode+compile path
    without needing a real capture."""
    body = bytes(units)
    header = bytes([0x26, 0x00, len(body) & 0xFF, len(body) >> 8])
    return base64.b64encode(header + body).decode()


CITATION = re.compile(
    r"^smartir@[0-9a-f]{7} codes/\S+\.json#.+? "
    r"\(SmartIR profile \S+, .+\): "
    r"(broadlink IR packet, base64-decoded"
    r"|broadlink pronto capture, upstream-provided verbatim)(; .+)?$"
)


# --- key_name (D43-adjacent naming) -----------------------------------------

@pytest.mark.parametrize("path, expected", [
    (("on",), "KEY_ON"),
    (("volumeUp",), "KEY_VOLUMEUP"),
    (("nextChannel",), "KEY_NEXTCHANNEL"),
    (("sources", "HDMI"), "KEY_SOURCES_HDMI"),
    (("sources", "HDMI Side"), "KEY_SOURCES_HDMI_SIDE"),
    (("reverse", "mediumLow"), "KEY_REVERSE_MEDIUMLOW"),
])
def test_key_names_become_css_identifiers(path, expected):
    assert key_name(path) == expected


# --- the whole tree ----------------------------------------------------------------

@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "upstream"
    (root / "codes" / "media_player").mkdir(parents=True)
    (root / "codes" / "fan").mkdir(parents=True)
    (root / "codes" / "climate").mkdir(parents=True)
    (root / "codes" / "light").mkdir(parents=True)

    (root / "codes" / "media_player" / "1000.json").write_text(json.dumps({
        "manufacturer": "Acme",
        "supportedModels": ["Model X"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Base64",
        "commands": {
            "on": _broadlink(200, 50, 10, 10),
            "off": _broadlink(200, 50, 10, 11),
            "volumeUp": [_broadlink(1, 2, 3, 4), _broadlink(1, 2, 3, 4)],
            "sources": {
                "HDMI": _broadlink(9, 9, 9, 9),
                "AV Side": _broadlink(8, 8, 8, 8),
            },
        },
    }))
    (root / "codes" / "media_player" / "9999.json").write_text(json.dumps({
        "manufacturer": "Yamaha",
        "supportedModels": ["AX-380"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Pronto",
        "commands": {"off": PRONTO_SAMPLE},
    }))
    (root / "codes" / "media_player" / "bad.json").write_text("{ not json")
    (root / "codes" / "media_player" / "unsupported.json").write_text(json.dumps({
        "manufacturer": "Nope",
        "supportedModels": ["N1"],
        "supportedController": "ESPHome",
        "commandsEncoding": "Raw",
        "commands": {"off": "[1, -2]"},
    }))
    (root / "codes" / "fan" / "1020.json").write_text(json.dumps({
        "manufacturer": "Kaze",
        "supportedModels": ["Unknown"],
        "supportedController": "Broadlink",
        "commandsEncoding": "Base64",
        "commands": {
            "off": _broadlink(5, 5),
            "default": {"low": _broadlink(6, 6), "high": _broadlink(7, 7)},
        },
    }))
    (root / "codes" / "climate" / "1.json").write_text(json.dumps({
        "manufacturer": "Toyotomi", "supportedModels": ["X"],
        "supportedController": "Broadlink", "commandsEncoding": "Base64",
        "commands": {"cool": {"auto": {"16": _broadlink(1, 1)}}},
    }))
    (root / "codes" / "light" / "1.json").write_text(json.dumps({
        "manufacturer": "Iris", "supportedModels": ["Y"],
        "supportedController": "Broadlink", "commandsEncoding": "Base64",
        "commands": {"on": _broadlink(1, 1)},
    }))
    return root


@pytest.fixture
def ledger(tmp_path):
    root = tmp_path / "ledger"
    root.mkdir()
    return root


def test_the_import_writes_one_file_per_profile(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    written = sorted(p.relative_to(ledger).as_posix()
                     for p in (ledger / IMPORT_ROOT).rglob("*.json"))
    assert written == [
        "remotes/smartir/Acme/media_player_1000.json",
        "remotes/smartir/Kaze/fan_1020.json",
        "remotes/smartir/Yamaha/media_player_9999.json",
    ]


def test_a_flat_command_and_a_grouped_one_both_import(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    doc = json.loads((ledger / "remotes/smartir/Acme/media_player_1000.json").read_text())
    assert doc["manufacturer"] == "Acme"
    assert doc["model"] == "SmartIR media_player 1000"
    assert doc["controls"] == ["Model X"]
    assert "KEY_ON" in doc["keys"]
    assert "KEY_SOURCES_HDMI" in doc["keys"]
    assert "KEY_SOURCES_AV_SIDE" in doc["keys"]


def test_a_list_valued_command_imports_the_first_and_says_so(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    doc = json.loads((ledger / "remotes/smartir/Acme/media_player_1000.json").read_text())
    source = doc["keys"]["KEY_VOLUMEUP"]["forms"][0]["source"]
    assert "first of 2 upstream captures" in source


def test_pronto_encoding_passes_through_verbatim(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    doc = json.loads((ledger / "remotes/smartir/Yamaha/media_player_9999.json").read_text())
    form = doc["keys"]["KEY_OFF"]["forms"][0]
    assert form["type"] == "pronto" and form["hex"] == PRONTO_SAMPLE


def test_every_imported_form_is_plausible(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    for path in (ledger / IMPORT_ROOT).rglob("*.json"):
        for spec in json.loads(path.read_text())["keys"].values():
            for form in spec["forms"]:
                assert form["confidence"] == "plausible"


def test_every_citation_has_the_smartir_shape(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    for path in (ledger / IMPORT_ROOT).rglob("*.json"):
        for spec in json.loads(path.read_text())["keys"].values():
            for form in spec["forms"]:
                assert CITATION.match(form["source"]), form["source"]


def test_out_of_scope_categories_are_reported_not_dropped(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    assert not (ledger / IMPORT_ROOT / "toyotomi").exists()
    assert not (ledger / IMPORT_ROOT / "iris").exists()
    text = (ledger / IMPORT_ROOT / REPORT).read_text()
    assert "category out of scope (climate)" in text
    assert "category out of scope (light)" in text


def test_malformed_and_unsupported_files_are_reported_not_dropped(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    text = (ledger / IMPORT_ROOT / REPORT).read_text()
    assert "bad.json" in text and "not valid JSON" in text
    assert "unsupported.json" in text and "ESPHome" in text


def test_authored_data_wins(ledger, checkout):
    """R19.4: an authored remote with the same manufacturer+model as what
    the import would produce is left alone, and the import skips it."""
    authored_dir = ledger / "remotes" / "acme"
    authored_dir.mkdir(parents=True)
    (authored_dir / "curated.json").write_text(json.dumps({
        "manufacturer": "Acme", "model": "SmartIR media_player 1000",
        "aliases": [], "protocol": {"carrierHz": 38000, "minSends": 1},
        "keys": {},
    }))
    report = write_import(ledger, checkout, "a" * 40)
    assert not (ledger / IMPORT_ROOT / "acme").exists()
    assert report.collisions == [
        ("codes/media_player/1000.json", "remotes/smartir/Acme/media_player_1000.json"),
    ]


def test_imported_files_validate_and_cross_check(ledger, checkout):
    write_import(ledger, checkout, "a" * 40)
    for path in corpus_files(ledger):
        assert [str(p) for p in validate_file(path)] == []
        problems, _ = check_remote(load_remote(path))
        assert problems == []


def test_the_import_is_byte_reproducible(ledger, checkout):
    """R19.5 / D44: the same checkout and commit give the same bytes, and a
    re-run removes what upstream no longer holds."""
    write_import(ledger, checkout, "a" * 40)
    first = {p: p.read_bytes() for p in sorted((ledger / IMPORT_ROOT).rglob("*"))
             if p.is_file()}
    fan_1020 = checkout / "codes" / "fan" / "1020.json"
    original = fan_1020.read_text()
    fan_1020.unlink()
    write_import(ledger, checkout, "a" * 40)
    assert not (ledger / "remotes/smartir/Kaze/fan_1020.json").exists()
    fan_1020.write_text(original)
    write_import(ledger, checkout, "a" * 40)
    second = {p: p.read_bytes() for p in sorted((ledger / IMPORT_ROOT).rglob("*"))
              if p.is_file()}
    assert first == second


def test_authored_files_in_the_import_root_survive(ledger, checkout):
    """README.md and LICENSE are authored; only *.json and IMPORT.md are the
    importer's (D44)."""
    (ledger / IMPORT_ROOT).mkdir(parents=True)
    (ledger / IMPORT_ROOT / "LICENSE").write_text("MIT")
    write_import(ledger, checkout, "a" * 40)
    assert (ledger / IMPORT_ROOT / "LICENSE").read_text() == "MIT"


def test_the_cli_dispatches_to_smartir(ledger, checkout, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "-c", "user.name=t", "-c",
                    "user.email=t@t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(checkout), "-c", "user.name=t", "-c",
                    "user.email=t@t", "commit", "-qm", "x"], check=True)
    monkeypatch.chdir(ledger)
    assert main(["import", "smartir", str(checkout)]) == 0
    out = capsys.readouterr().out
    assert "remotes/smartir/" in out and REPORT in out
