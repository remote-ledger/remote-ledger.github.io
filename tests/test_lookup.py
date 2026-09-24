"""`rl lookup` and R20's three states."""

import json
from pathlib import Path

import pytest

from remote_ledger.index import build_index
from remote_ledger.lookup import keys_for, render, search

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def index():
    return build_index(ROOT)[0]


@pytest.mark.parametrize(
    "query,expect_field",
    [
        ("Topping", "manufacturer"),
        ("RC-15A", "model"),
        ("rc-15a", "model"),          # case-insensitive
        ("DX3 Pro", "controls"),
        ("dx3", "controls"),          # substring
    ],
)
def test_search_hits_every_identifying_field(index, query, expect_field):
    """SPEC section 1's premise: people know their *device*, not the remote
    model that shipped with it."""
    matches = search(index, query)
    topping = [m for m in matches if m.kind == "remote"
               and m.entry["file"] == "remotes/topping/RC-15A.json"]
    assert topping and topping[0].matched_on == expect_field


def test_a_query_naming_maker_and_model_matches(index):
    """"Topping RC-15A" is a substring of neither field alone."""
    matches = search(index, "Topping RC-15A")
    assert matches and matches[0].entry["model"] == "RC-15A"


def test_an_empty_query_matches_nothing(index):
    assert search(index, "   ") == []


# --- R20's three states, which is the point of the whole mechanism ---------

def test_state_one_in_the_ledger(index):
    matches = search(index, "DX3 Pro")
    out = render(matches, "DX3 Pro", keys_for(ROOT, matches))
    assert "Topping RC-15A" in out
    assert "[verified]" in out            # tier
    assert "audiosciencereview.com" in out  # citation
    assert "KEY_POWER" in out


def test_state_two_checked_and_not_found(index):
    """The state R20 exists for. A device someone spent an afternoon failing
    to find must not read the same as one nobody has typed in."""
    matches = search(index, "Sony BDP-BX510")
    assert matches and matches[0].kind == "unresolved"
    out = render(matches, "Sony BDP-BX510")
    assert "[checked, nothing found]" in out
    assert "checked   2026-" in out


def test_state_three_nobody_has_looked(index):
    out = render(search(index, "Yamaha RX-V385"), "Yamaha RX-V385")
    assert "nobody has looked" in out


def test_the_three_states_render_differently(index):
    found = render(search(index, "DX3 Pro"), "q")
    unresolved = render(search(index, "Sony BDP-BX510"), "q")
    absent = render(search(index, "Yamaha RX-V385"), "q")
    assert len({found, unresolved, absent}) == 3
    assert "[checked, nothing found]" not in found
    assert "nobody has looked" not in unresolved


def test_alternates_are_surfaced_with_their_tier(tmp_path):
    """D32: `rl lookup` surfaces every non-primary group with its tier, so
    untested alternates cannot accumulate unseen."""
    doc = json.loads((ROOT / "remotes" / "topping" / "RC-15A.json").read_text())
    doc["variants"] = {"mode2": {"label": "Command mode 2",
                                 "confidence": "untested", "source": "manual",
                                 "override": {"subdevice": "0xEA"}}}
    (tmp_path / "remotes" / "t").mkdir(parents=True)
    (tmp_path / "remotes" / "t" / "a.json").write_text(json.dumps(doc))
    index, _ = build_index(tmp_path)
    matches = search(index, "RC-15A")
    out = render(matches, "RC-15A", keys_for(tmp_path, matches))
    assert "Command mode 2" in out and "untested" in out
    assert "untested alternate candidate(s)" in out


def test_an_imported_remote_says_so(tmp_path):
    """SPEC R19 / D34: the path is the trust boundary, and the lookup states
    it, so an imported remote cannot be mistaken for an authored one."""
    doc = json.loads((ROOT / "remotes" / "topping" / "RC-15A.json").read_text())
    (tmp_path / "remotes" / "lirc" / "topping").mkdir(parents=True)
    (tmp_path / "remotes" / "lirc" / "topping" / "RC-15A.json").write_text(json.dumps(doc))
    index, _ = build_index(tmp_path)
    matches = search(index, "RC-15A")
    out = render(matches, "RC-15A", keys_for(tmp_path, matches))
    assert "imported  from remotes/lirc/" in out
