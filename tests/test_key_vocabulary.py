"""The canonical key vocabulary: its data, its validator, and the mapping (D83 to D85).

Three things are held here, each against the file that ships.

* The **data** has the shape downstream code depends on (``version``, ``groups``,
  ``keys``, the eight fields of a key) and passes every rule of the validator.
* The **validator** refuses what it says it refuses. Each rule has a test that breaks
  exactly that rule in a copy of the shipped files and asks for the message.
* The **mapping** (``canonical_id``) is conservative: the cases that must map do,
  and so do the cases that must not, which are the ones that matter, since a wrong
  mapping puts the wrong icon on a key that sends another signal.
"""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from remote_ledger import keys, paths
from remote_ledger.keys import (
    CORE_GROUPS, DIGIT_IDS, canonical_id, digit_of, fold, load_vocabulary, squash,
    vocabulary_problems,
)

ROOT = Path(__file__).resolve().parents[1]
KEYS_DOC = json.loads((paths.VOCABULARY_DIR / paths.KEYS_FILE).read_text(encoding="utf-8"))
ALIASES_DOC = json.loads((paths.VOCABULARY_DIR / paths.ALIASES_FILE).read_text(encoding="utf-8"))
KEY_FIELDS = ["id", "group", "order", "name", "icon", "glyph", "color", "repeat"]


# --- the data ----------------------------------------------------------------------


def test_the_shipped_vocabulary_is_valid():
    assert [str(p) for p in vocabulary_problems()] == []


def test_the_shape_is_the_contract_downstream_code_reads():
    assert list(KEYS_DOC) == ["version", "groups", "keys"]
    assert KEYS_DOC["version"] == 1
    assert all(list(g) == ["id", "order", "name"] for g in KEYS_DOC["groups"])
    assert all(list(k) == KEY_FIELDS for k in KEYS_DOC["keys"])
    assert list(ALIASES_DOC) == ["version", "tokens", "aliases"] and ALIASES_DOC["version"] == 1


def test_it_is_a_vocabulary_of_about_two_hundred_keys_in_the_core_groups():
    ids = [k["id"] for k in KEYS_DOC["keys"]]
    assert 150 <= len(ids) <= 250 and len(set(ids)) == len(ids)
    assert set(CORE_GROUPS) <= {g["id"] for g in KEYS_DOC["groups"]}
    # the ids the contract names
    for needed in (
        "POWER", "POWER_ON", "POWER_OFF", "VOLUME_UP", "VOLUME_DOWN", "MUTE", "CHANNEL_UP",
        "CHANNEL_DOWN", "UP", "DOWN", "LEFT", "RIGHT", "OK", "BACK", "HOME", "MENU", "EXIT",
        "INFO", "GUIDE", "PLAY", "PAUSE", "STOP", "FAST_FORWARD", "REWIND", "NEXT", "PREVIOUS",
        "RECORD", "INPUT", "HDMI_1", "COLOR_RED", "COLOR_GREEN", "COLOR_YELLOW", "COLOR_BLUE",
        "SUBTITLE", "AUDIO", *DIGIT_IDS,
    ):
        assert needed in ids


def test_every_key_has_an_icon_a_glyph_or_a_colour():
    for key in KEYS_DOC["keys"]:
        assert key["icon"] or key["glyph"] or key["color"], key["id"]
    colours = {k["id"]: k["color"] for k in KEYS_DOC["keys"] if k["color"]}
    assert colours == {"COLOR_RED": "red", "COLOR_GREEN": "green",
                       "COLOR_YELLOW": "yellow", "COLOR_BLUE": "blue"}


def test_digits_are_text_and_the_arrows_hold_to_repeat():
    by_id = {k["id"]: k for k in KEYS_DOC["keys"]}
    for n in range(10):
        assert by_id[f"DIGIT_{n}"]["glyph"] == str(n) and by_id[f"DIGIT_{n}"]["group"] == "numbers"
    repeating = {k["id"] for k in KEYS_DOC["keys"] if k["repeat"]}
    assert {"VOLUME_UP", "VOLUME_DOWN", "CHANNEL_UP", "CHANNEL_DOWN", "UP", "DOWN", "LEFT",
            "RIGHT"} <= repeating
    # a key that changes state must not fire again because a finger rests on it
    assert not repeating & {"POWER", "POWER_ON", "POWER_OFF", "MUTE", "INPUT", "OK", "PLAY",
                            "PAUSE", "STOP", "RECORD", "EJECT", *DIGIT_IDS}


def test_no_brand_is_a_key_an_icon_or_a_glyph():
    """Apps are only the generic ones (D83): no brand name as an id, name, icon or glyph."""
    brands = re.compile(r"NETFLIX|YOUTUBE|PRIME|DISNEY|HULU|SPOTIFY|SAMSUNG|\bLG\b|SONY|PHILIPS", re.I)
    for key in KEYS_DOC["keys"]:
        for field in ("id", "name", "icon", "glyph"):
            assert not (key[field] and brands.search(key[field])), (key["id"], field)


def test_the_vocabulary_loads_sorted_and_is_cached():
    vocabulary = load_vocabulary()
    assert vocabulary is load_vocabulary()
    assert [g.order for g in vocabulary.groups] == sorted(g.order for g in vocabulary.groups)
    assert len(vocabulary.keys) == len(KEYS_DOC["keys"])
    assert vocabulary.key("VOLUME_UP").icon == "volume_up"
    # keys come in reading order: the groups in their order, each group's keys in theirs
    place = {g.id: g.order for g in vocabulary.groups}
    positions = [(place[k.group], k.order) for k in vocabulary.keys]
    assert positions == sorted(positions) and vocabulary.keys[0].id == "POWER"


def test_no_generator_owns_the_vocabulary_and_no_generated_tree_holds_a_copy():
    """Hand-written ledger data: `rl build --check` has nothing of it to compare (D83)."""
    from remote_ledger.generators import owned_paths

    assert [p for p in owned_paths() if p.startswith("src") or "vocabulary" in p] == []
    for tree in ("build", "site"):
        assert not list((ROOT / tree).rglob("keys.json")) + list((ROOT / tree).rglob("aliases.json"))


def test_the_icon_tool_checks_the_names_against_a_codepoints_file(tmp_path):
    used = sorted({k["icon"] for k in KEYS_DOC["keys"] if k["icon"]})
    tool = [sys.executable, str(ROOT / "tools" / "vocabulary_icons.py"), "--codepoints"]
    complete = tmp_path / "complete.codepoints"
    complete.write_text("".join(f"{name} e0{i:02x}\n" for i, name in enumerate(used)) + "spare e0ff\n")
    ran = subprocess.run([*tool, str(complete)], capture_output=True, text=True)
    assert ran.returncode == 0 and f"{len(used)} icons used" in ran.stdout and "0 missing" in ran.stdout
    short = tmp_path / "short.codepoints"
    short.write_text("".join(f"{name} e0{i:02x}\n" for i, name in enumerate(used) if i))
    ran = subprocess.run([*tool, str(short)], capture_output=True, text=True)
    assert ran.returncode == 1 and f"MISSING {used[0]}" in ran.stdout


def test_the_files_ship_with_the_package():
    """A non-editable install has no checkout (paths.py): the data is package data."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"vocabulary/*.json"' in pyproject and '"schema/*.json"' in pyproject
    assert paths.VOCABULARY_DIR.parent == Path(keys.__file__).parent
    for name in paths.VOCABULARY_SCHEMAS:
        assert (paths.VOCABULARY_DIR / name).is_file()


# --- the validator -----------------------------------------------------------------


def _problems(tmp_path, mutate_keys=None, mutate_aliases=None) -> list[str]:
    key_doc, alias_doc = copy.deepcopy(KEYS_DOC), copy.deepcopy(ALIASES_DOC)
    if mutate_keys:
        mutate_keys(key_doc)
    if mutate_aliases:
        mutate_aliases(alias_doc)
    (tmp_path / paths.KEYS_FILE).write_text(json.dumps(key_doc), encoding="utf-8")
    (tmp_path / paths.ALIASES_FILE).write_text(json.dumps(alias_doc), encoding="utf-8")
    return [str(p) for p in vocabulary_problems(tmp_path)]


def _key(doc, key_id):
    return next(k for k in doc["keys"] if k["id"] == key_id)


def test_the_unmodified_copy_is_valid(tmp_path):
    assert _problems(tmp_path) == []


@pytest.mark.parametrize("bad", ["volume_up", "KEY_VOLUME_UP", "BTN_OK", "VOLUME__UP", "_UP", "UP_",
                                 "1UP", "VOLUME UP", "Volume_Up"])
def test_an_id_is_upper_snake_case_without_a_source_prefix(tmp_path, bad):
    found = _problems(tmp_path, lambda d: _key(d, "UP").update(id=bad))
    assert found and any("keys" in line for line in found)


def test_a_key_needs_an_icon_a_glyph_or_a_colour(tmp_path):
    def strip(doc):
        key = _key(doc, "POWER_ON")           # carries an icon and a glyph
        key.update(icon=None, glyph=None, color=None)
    found = _problems(tmp_path, strip)
    assert found and any("is not valid under any of the given schemas" in line for line in found)
    # one of the three is enough, in any form
    for icon, glyph, color in (("power", None, None), (None, "ON", None), (None, None, "red")):
        assert _problems(tmp_path, lambda d: _key(d, "POWER_ON").update(
            icon=icon, glyph=glyph, color=color)) == []


@pytest.mark.parametrize("change", [
    {"color": "purple"}, {"icon": "Power Settings"}, {"icon": ""}, {"glyph": "TOO LONG AT ALL"},
    {"glyph": ""}, {"repeat": "yes"}, {"order": 0}, {"order": 1000}, {"name": ""},
    {"group": "Power"}, {"extra": 1},
])
def test_a_key_field_outside_its_type_is_refused(tmp_path, change):
    assert _problems(tmp_path, lambda d: _key(d, "MUTE").update(change))


@pytest.mark.parametrize("field", KEY_FIELDS)
def test_every_field_of_a_key_is_required(tmp_path, field):
    assert _problems(tmp_path, lambda d: _key(d, "MUTE").pop(field))


def test_an_unknown_version_is_refused(tmp_path):
    assert _problems(tmp_path, lambda d: d.update(version=2))
    assert _problems(tmp_path, mutate_aliases=lambda d: d.update(version=2))


def test_a_group_must_exist(tmp_path):
    found = _problems(tmp_path, lambda d: _key(d, "MUTE").update(group="nowhere"))
    assert any("'nowhere' is not a group of this file" in line for line in found)


def test_ids_are_unique(tmp_path):
    found = _problems(tmp_path, lambda d: d["keys"].append(dict(_key(d, "MUTE"), order=99)))
    assert any("key id 'MUTE' appears more than once" in line for line in found)


def test_an_order_is_unique_within_its_group(tmp_path):
    found = _problems(tmp_path, lambda d: _key(d, "MUTE").update(order=1))
    assert any("VOLUME_UP, MUTE share order 1 in group 'volume'" in line for line in found)
    # the same number in another group is fine: orders are per group
    assert _problems(tmp_path, lambda d: _key(d, "UP").update(order=1)) == []


def test_groups_are_unique_and_the_core_ones_stay(tmp_path):
    def twice(doc):
        doc["groups"].append(dict(doc["groups"][0], order=99))
    assert any("group id 'power' appears more than once" in line for line in _problems(tmp_path, twice))

    def same_order(doc):
        doc["groups"][1]["order"] = doc["groups"][0]["order"]
    assert any("group order 1 is used by more than one" in line
               for line in _problems(tmp_path, same_order))

    def drop_apps(doc):
        doc["groups"] = [g for g in doc["groups"] if g["id"] != "apps"]
        doc["keys"] = [k for k in doc["keys"] if k["group"] != "apps"]
    assert any("the group 'apps' is missing" in line for line in _problems(tmp_path, drop_apps))


def test_a_group_may_not_be_empty(tmp_path):
    def add_empty(doc):
        doc["groups"].append({"id": "spare", "order": 99, "name": "Spare"})
    assert any("group 'spare' has no key" in line for line in _problems(tmp_path, add_empty))


def test_the_ten_digit_keys_are_required(tmp_path):
    def drop(doc):
        doc["keys"] = [k for k in doc["keys"] if k["id"] != "DIGIT_7"]
    assert any("DIGIT_7 is missing" in line for line in _problems(tmp_path, drop))


def test_an_alias_names_a_key_that_exists(tmp_path):
    found = _problems(tmp_path, mutate_aliases=lambda d: d["aliases"].update(NOT_A_KEY=["X"]))
    assert any("'NOT_A_KEY' is not a canonical key" in line for line in found)


def test_aliases_never_collide_across_ids(tmp_path):
    """The one rule that keeps a spelling meaning one key."""
    found = _problems(tmp_path, mutate_aliases=lambda d: d["aliases"]["MUTE"].append("VOL UP"))
    assert any("'VOL UP' folds to 'VOLUMEUP', which VOLUME_UP has as" in line for line in found)
    # across a different spelling that folds alike: case, signs, separators, tokens
    for spelling in ("vol_up", "Vol.Up", "KEY_VOLUMEUP", "volume-up"):
        found = _problems(tmp_path, mutate_aliases=lambda d: d["aliases"]["MUTE"].append(spelling))
        assert any("which VOLUME_UP has as" in line for line in found), spelling


def test_an_alias_may_not_repeat_what_its_own_key_already_says(tmp_path):
    found = _problems(tmp_path, mutate_aliases=lambda d: d["aliases"]["MUTE"].append("mute"))
    assert any("folds like 'MUTE' (its own id or display name)" in line for line in found)
    spelled = ALIASES_DOC["aliases"]["MUTE"][0]
    found = _problems(tmp_path, mutate_aliases=lambda d: d["aliases"]["MUTE"].append(
        spelled.replace(" ", "_").lower()))
    assert any("one of them is redundant" in line for line in found)


def test_an_alias_that_folds_to_nothing_is_refused(tmp_path):
    for spelling in ("   ", "KEY_", "_._", "()"):
        found = _problems(tmp_path, mutate_aliases=lambda d: d["aliases"]["MUTE"].append(spelling))
        assert any("folds to nothing" in line for line in found), spelling


def test_a_digit_is_never_an_alias(tmp_path):
    for spelling in ("5", "NUM 5", "KP5", "five"):
        if digit_of(squash(spelling, {})) is None:
            continue
        found = _problems(tmp_path, mutate_aliases=lambda d: d["aliases"]["MUTE"].append(spelling))
        assert any("is the digit 5" in line for line in found), spelling


def test_two_keys_may_not_share_a_display_name(tmp_path):
    found = _problems(tmp_path, lambda d: _key(d, "MUTE").update(name="Volume up"))
    assert any("which VOLUME_UP has as 'VOLUME_UP'" in line for line in found)


def test_a_token_is_one_folded_word_rewritten_once(tmp_path):
    for token, target, text in (
        ("vol", "VOLUME", "is not a folded word"),
        ("VOL", "volume", "which is not a folded word"),
        ("A-B", "AB", "is not a folded word"),
        ("VOL", "VOL", "maps to itself"),
        ("CHAN", "CH", "which is itself rewritten"),
    ):
        found = _problems(tmp_path, mutate_aliases=lambda d: d["tokens"].update({token: target}))
        assert any(text in line for line in found), (token, target, found)


def test_an_unreadable_or_unparseable_file_is_a_problem_not_a_traceback(tmp_path):
    assert any("could not be read" in line for line in
               [str(p) for p in vocabulary_problems(tmp_path)])
    (tmp_path / paths.KEYS_FILE).write_text("{", encoding="utf-8")
    (tmp_path / paths.ALIASES_FILE).write_text('{"version": 1, "version": 1}', encoding="utf-8")
    found = [str(p) for p in vocabulary_problems(tmp_path)]
    assert any("keys.json: could not be parsed" in line for line in found)
    assert any("duplicate JSON key" in line for line in found)


def test_a_broken_vocabulary_cannot_be_loaded(tmp_path):
    _problems(tmp_path, lambda d: _key(d, "MUTE").update(group="nowhere"))
    with pytest.raises(keys.ValidationError, match="'nowhere' is not a group"):
        keys.vocabulary_from(tmp_path)


def test_the_validator_runs_with_rl_validate_and_rl_build(tmp_path, monkeypatch, capsys):
    """Normal validation covers the vocabulary: a corpus-wide run reports it, a run on a path does not."""
    from remote_ledger.cli import main

    broken = tmp_path / "vocabulary"
    broken.mkdir()
    _problems(broken, lambda d: _key(d, "MUTE").update(group="nowhere"))
    monkeypatch.setattr(paths, "VOCABULARY_DIR", broken)
    work = tmp_path / "repo"
    (work / "remotes").mkdir(parents=True)
    monkeypatch.chdir(work)
    for argv in (["validate"], ["build"]):
        assert main(argv) == 1
        err = capsys.readouterr().err
        assert "keys.json" in err and "'nowhere' is not a group" in err
    # the command line still checks one remote file on its own
    assert main(["validate", str(ROOT / "remotes" / "topping" / "RC-15A.json")]) == 0
    assert "'nowhere'" not in capsys.readouterr().err


# --- folding ----------------------------------------------------------------------


@pytest.mark.parametrize("text, folded", [
    ("Vol +", "VOLUME PLUS"),
    ("VOL+", "VOLUME PLUS"),
    ("KEY_VOL_PLUS", "VOLUME PLUS"),
    ("vol_plus", "VOLUME PLUS"),
    ("ＶＯＬ＋", "VOLUME PLUS"),                    # full-width forms
    ("VOL" + chr(0x2212), "VOLUME MINUS"),         # a true minus sign
    ("VOL" + chr(0x2013), "VOLUME MINUS"),         # an en dash
    ("Vol-Up", "VOLUME UP"),                       # a hyphen between words joins them
    ("S-VIDEO", "S VIDEO"),
    ("A-B", "A B"),
    ("-/--", "MINUS / MINUS MINUS"),               # a sign, and the slash stays
    ("KEY_PWR", "POWER"),
    ("BTN_CH_UP", "CHANNEL UP"),
    ("KEY_SOURCES_HDMI_1", "HDMI 1"),              # the SmartIR import's `sources` group
    ("KEY LOCK", "KEY LOCK"),                      # the word KEY is not a prefix without its underscore
    ("  P. UP  ", "P UP"),
    ("TV/AV", "TV/AV"),
    ("TV / AV", "TV / AV"),
    ("*", "STAR"),
    ("#", "HASH"),
    ("⏩|", "⏩|"),
    ("", ""),
    ("KEY_", ""),
    ("_", ""),
])
def test_fold(text, folded):
    assert fold(text) == folded


def test_squash_removes_the_spaces_and_keeps_the_slash():
    assert squash("Volume Up") == squash("VOLUME_UP") == squash("KEY_VOLUMEUP") == "VOLUMEUP"
    assert squash("P/C") == "P/C" != squash("PC") == "PC"
    assert squash("TV / AV") == squash("TV/AV") == "TV/AV"


@pytest.mark.parametrize("text, digit", [
    ("0", 0), ("9", 9), ("NUM1", 1), ("NUMBER3", 3), ("NUMERIC7", 7), ("NUMPAD2", 2), ("KP5", 5),
    ("KEYPAD4", 4), ("DIGIT8", 8), ("CHANNEL6", 6), ("ONE", 1), ("ZERO", 0), ("NINE", 9),
    ("10", None), ("11", None), ("D1", None), ("N1", None), ("010", None), ("ONEONE", None), ("", None),
])
def test_the_digit_rule(text, digit):
    assert digit_of(text) == digit


# --- the mapping: what must map ---------------------------------------------------


@pytest.mark.parametrize("name, label, expected", [
    # names alone (LIRC, SmartIR, authored)
    ("KEY_POWER", None, "POWER"),
    ("KEY_VOLUMEUP", None, "VOLUME_UP"),
    ("KEY_VOLUMEDOWN", None, "VOLUME_DOWN"),
    ("KEY_MUTE", None, "MUTE"),
    ("KEY_CHANNELUP", None, "CHANNEL_UP"),
    ("KEY_CHANNELDOWN", None, "CHANNEL_DOWN"),
    ("KEY_FASTFORWARD", None, "FAST_FORWARD"),
    ("KEY_REWIND", None, "REWIND"),
    ("KEY_OK", None, "OK"),
    ("KEY_ENTER", None, "ENTER"),
    ("KEY_RED", None, "COLOR_RED"),
    ("KEY_EPG", None, "GUIDE"),
    ("KEY_SETUP", None, "SETTINGS"),
    ("volume_up", None, "VOLUME_UP"),
    ("Volume__up_", None, "VOLUME_UP"),
    ("_fastforward_", None, "FAST_FORWARD"),
    ("BTN_POWER", None, "POWER"),
    ("KEY_SOURCES_HDMI_1", None, "HDMI_1"),
    ("KEY_SOURCES_TV", None, "INPUT_TV"),
    # labels (IR Blaster): what the source prints, whatever the name says
    ("KEY_VOL_PLUS", "VOL+", "VOLUME_UP"),
    ("KEY_X", "Vol +", "VOLUME_UP"),
    ("KEY_X", "VOL UP", "VOLUME_UP"),
    ("KEY_X", "V+", "VOLUME_UP"),
    ("KEY_X", "VOL-", "VOLUME_DOWN"),
    ("KEY_P_PLUS", "P+", "CHANNEL_UP"),
    ("KEY_X", "CH +", "CHANNEL_UP"),
    ("KEY_X", "P -", "CHANNEL_DOWN"),
    ("KEY_X", "PR+", "CHANNEL_UP"),
    ("KEY_X", "PWR", "POWER"),
    ("KEY_X", "Power", "POWER"),
    ("KEY_X", "standby", "POWER"),
    ("KEY_X", "ON/OFF", "POWER"),
    ("KEY_X", "ON / OFF", "POWER"),
    ("KEY_X", "SKIP NEXT", "NEXT"),
    ("KEY_X", "SKIP PREV.", "PREVIOUS"),
    ("KEY_X", "PLAY/PAUSE", "PLAY_PAUSE"),
    ("KEY_X", "PLAY / PAUSE", "PLAY_PAUSE"),
    ("KEY_X", "AV/SOURCE", "INPUT"),
    ("KEY_X", "TV/AV", "INPUT"),
    ("KEY_X", "EXIT/RETURN", "EXIT"),
    ("KEY_X", "RETURN", "BACK"),
    ("KEY_X", "SLEEP/TIMER", "SLEEP"),
    ("KEY_X", "OPEN/CLOSE", "EJECT"),
    ("KEY_X", "-/--", "DASH"),
    ("KEY_X", "10+", "DIGIT_10_PLUS"),
    ("KEY_X", "+10", "DIGIT_10_PLUS"),
    ("KEY_X", "A-B", "REPEAT_A_B"),
    ("KEY_X", "S-VIDEO", "INPUT_SVIDEO"),
    ("KEY_X", "P.MODE", "PICTURE_MODE"),
    ("KEY_X", "TXT", "TELETEXT"),
    ("KEY_X", "SUB-T", "SUBTITLE"),
    ("KEY_X", "HDMI 2", "HDMI_2"),
    ("KEY_X", "HDMI2", "HDMI_2"),
    ("KEY_X", "Input HDMI 3", "HDMI_3"),
    ("KEY_X", "⏩", "FAST_FORWARD"),
    ("KEY_X", "⏪", "REWIND"),
    ("KEY_X", "|⏪", "PREVIOUS"),
    ("KEY_X", "⏩|", "NEXT"),
    ("KEY_X", "REV ⏪", "REWIND"),
    ("KEY_X", "⏩/FWD", "FAST_FORWARD"),
])
def test_what_maps(name, label, expected):
    assert canonical_id(name, label) == expected


@pytest.mark.parametrize("name, label, expected", [
    ("KEY_0", None, "DIGIT_0"), ("KEY_9", None, "DIGIT_9"), ("KEY_X", "5", "DIGIT_5"),
    ("KEY_KP7", None, "DIGIT_7"), ("NUM_3", None, "DIGIT_3"), ("KEY_NUMERIC_1", None, "DIGIT_1"),
    ("number_2", None, "DIGIT_2"), ("KEY_X", "Number 8", "DIGIT_8"), ("KEY_X", "ONE", "DIGIT_1"),
    ("KEY_SOURCES_CHANNEL_4", None, "DIGIT_4"), ("BTN_6", None, "DIGIT_6"),
])
def test_digits_by_rule(name, label, expected):
    assert canonical_id(name, label) == expected


def test_every_key_maps_to_itself_by_its_id_and_its_display_name():
    for key in load_vocabulary().keys:
        assert canonical_id(key.id) == key.id, key.id
        assert canonical_id(f"KEY_{key.id}") == key.id, key.id
        assert canonical_id("KEY_X", key.name) == key.id, key.name


def test_every_alias_maps_to_its_key_however_it_is_spelled():
    vocabulary = load_vocabulary()
    for key_id, aliases in vocabulary.aliases.items():
        for alias in aliases:
            assert canonical_id("KEY_X", alias) == key_id, alias
            assert canonical_id("KEY_X", alias.lower()) == key_id, alias
            assert canonical_id("KEY_X", alias.upper()) == key_id, alias
            assert canonical_id(alias) == key_id, alias                       # as a name too
            assert canonical_id(f"KEY_{alias.replace(' ', '_')}") == key_id, alias
            assert canonical_id(f"BTN_{alias.replace(' ', '_')}") == key_id, alias
            assert canonical_id("KEY_X", f"  {alias}  ") == key_id, alias


# --- the mapping: what must not -----------------------------------------------------


@pytest.mark.parametrize("name, label", [
    # nothing to go on
    ("KEY_", None), ("KEY_UNLABELED", "??"), ("KEY_", "??"), ("KEY_X", "?"), ("KEY_X", "???"),
    ("_", None), ("KEY__00FF02FD", "??"), ("KEY_000", None), ("KEY_0x1C", None), ("n17", None),
    ("KEY_X", "\\/"), ("KEY_X", "/\\"), ("KEY_X", "+"), ("KEY_X", "-"), ("KEY_X", "^"),
    # ambiguous on their own: a glyph that is an arrow or a play key, a word with two meanings
    ("KEY_X", "►"), ("KEY_X", "◄"), ("KEY_X", "MODE"), ("KEY_X", "PROGRAM"), ("KEY_X", "VIDEO"),
    ("KEY_X", "TIME"), ("KEY_X", "SELECT"), ("KEY_X", "INDEX"), ("KEY_X", "LIST"), ("KEY_X", "i"),
    ("KEY_AGAIN", None), ("KEY_SELECT", None), ("KEY_102ND", None),
    # a letter, a number, a letter and a slash
    ("KEY_A", None), ("KEY_B", None), ("KEY_C", None), ("KEY_X", "10"), ("KEY_X", "0/10"),
    ("KEY_X", "D1"), ("KEY_X", "11"), ("KEY_X", "P/C"), ("KEY_X", "N/P"), ("KEY_X", "L/R"),
    # P. UP sits beside UP, P+ and P- in 555 remotes: it is not a channel key
    ("KEY_X", "P. UP"), ("KEY_X", "P. DOWN"), ("KEY_X", "P. LEFT"), ("KEY_X", "P. RIGHT"),
    # a device-qualified key says which device, and a layout of one device cannot place it
    ("TV_POWER", None), ("KEY_X", "POWER TV"), ("KEY_X", "POWER VCR"), ("CD_PLAY", None),
    ("VCR_STOP", None), ("TV_VOL_UP", None), ("KEY_X", "TV VOL+"), ("KEY_X", "MASTER VOL+"),
    # two functions on one key
    ("KEY_X", "RIGHT / VOL+"), ("KEY_X", "UP/CH+"), ("KEY_X", "►/VOL +"), ("KEY_X", "RED/AUDIO"),
    ("KEY_X", "TV/SAT"), ("KEY_X", "PAUSE/STEP"),
    # power: never ON or OFF unless it says so
    ("KEY_POWER2", None), ("KEY_X", "POWER2"), ("KEY_X", "POWER 1"), ("KEY_X", "POWER BUTTON?"),
    # the word key, a label that only contains a meaning, an unknown brand
    ("KEY_X", "KEY LOCK"), ("KEY_X", "VOLUME CONTROL"), ("KEY_X", "NETFLIX"), ("KEY_X", "YouTube"),
    # a hyphen inside a word is a separator, but SKIP BACK and SKIP FORWARD jump seconds
    ("KEY_X", "SKIP BACK"), ("KEY_X", "SKIP FORWARD"),
    # the arc of a soundbar is not an aspect ratio
    ("KEY_X", "ARC"),
])
def test_what_does_not_map(name, label):
    assert canonical_id(name, label) is None


def test_a_bare_power_is_power_and_on_and_off_need_the_word():
    assert canonical_id("KEY_POWER") == canonical_id("KEY_X", "POWER") == "POWER"
    assert canonical_id("KEY_X", "ON") == canonical_id("KEY_X", "POWER ON") == "POWER_ON"
    assert canonical_id("KEY_X", "OFF") == canonical_id("KEY_X", "POWER OFF") == "POWER_OFF"
    assert canonical_id("KEY_X", "PWR OFF") == "POWER_OFF" and canonical_id("KEY_ON") == "POWER_ON"
    # a key that says both is the toggle, and standby is a toggle too
    for text in ("ON/OFF", "POWER ON/OFF", "STANDBY/ON", "ON/STANDBY", "STANDBY"):
        assert canonical_id("KEY_X", text) == "POWER", text
    # nothing else reaches POWER_ON or POWER_OFF
    for text in ("POWER", "POWER TOGGLE", "STANDBY", "ON/OFF", "SLEEP", "POWER TV", "TV ON", "TV OFF"):
        assert canonical_id("KEY_X", text) not in ("POWER_ON", "POWER_OFF"), text


def test_the_label_decides_when_there_is_one():
    """An imported name is the label folded to ASCII, so it says less than the label does."""
    assert canonical_id("KEY_MINUS_MINUS", "-►.◄-") is None            # the name alone says DASH
    assert canonical_id("KEY_MINUS_MINUS", None) == "DASH"
    assert canonical_id("KEY_UP", "Exit") == "EXIT"                    # the label wins, even against a name
    assert canonical_id("KEY_UP", "▲") is None                         # and an unknown label is None
    # a missing or blank label leaves the name to answer
    for blank in (None, "", "   ", "\t"):
        assert canonical_id("KEY_UP", blank) == "UP"


def test_an_id_that_looks_like_a_key_name_is_not_a_prefix_trick():
    assert canonical_id("KEY_KEY_UP") is None                          # only one prefix is removed
    assert canonical_id("KEYUP") is None
    assert canonical_id("KEY UP") is None                              # without the underscore it is a word
    assert canonical_id("UP") == "UP"


def test_the_mapping_is_total_and_stable_on_odd_input():
    for text in ("\x00", "‮", "💥", "A" * 5000, "\n\t", "KEY_́", "٣", "①", "½"):
        assert canonical_id("KEY_X", text) in {None, *(k.id for k in load_vocabulary().keys)}
    assert canonical_id("KEY_X", "①") == "DIGIT_1"                     # NFKC makes it the digit


def test_a_mapping_never_depends_on_how_often_it_was_asked():
    first = [canonical_id("KEY_X", t) for t in ("VOL+", "P/C", "OK", "??")]
    assert first == [canonical_id("KEY_X", t) for t in ("VOL+", "P/C", "OK", "??")]
    assert first == ["VOLUME_UP", None, "OK", None]
