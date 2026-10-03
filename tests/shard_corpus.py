"""A throwaway corpus with an imported database in it (D69), for the tests of
the shards and of what reads them."""

import json
from pathlib import Path

import pytest

from remote_ledger import generators

ROOT = Path(__file__).resolve().parents[1]
TOPPING = json.loads((ROOT / "remotes" / "topping" / "RC-15A.json").read_text())


def write_remote(root, where, manufacturer, model, controls=()):
    doc = json.loads(json.dumps(TOPPING))
    doc.update(manufacturer=manufacturer, model=model, controls=list(controls))
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def make_corpus(tmp_path):
    """Authored, LIRC-style and imported-database remotes, and R20's middle row."""
    write_remote(tmp_path, "remotes/topping/RC-15A.json", "Topping", "RC-15A", ["DX3 Pro"])
    write_remote(tmp_path, "remotes/lirc/sony/RM-1.json", "sony", "RM-1", ["SONY KDL 40"])
    write_remote(tmp_path, "remotes/irblaster/ZENITH/1-NEC1.json", "ZENITH",
                 "IR Blaster DB 1 (NEC1)", ["ZENITH | Z 100", "AKAI | AK 77"])
    write_remote(tmp_path, "remotes/irblaster/AKAI/2-NEC1.json", "AKAI",
                 "IR Blaster DB 2 (NEC1)", ["AKAI | AK 80"])
    write_remote(tmp_path, "remotes/irblaster/3B_TECH/3-NEC1.json", "3B TECH",
                 "IR Blaster DB 3 (NEC1)", ["3B TECH | LT 26"])
    (tmp_path / "unresolved.json").write_text(
        json.dumps([{"device": "Sony BDP-BX510", "checked": "2026-09-14"}]))
    return tmp_path


@pytest.fixture
def corpus(tmp_path):
    return make_corpus(tmp_path)


def generate(root, out=None):
    """The index stage, into ``out`` (default: in place), which must be clean."""
    assert generators.run_index(root, out or root) == []
