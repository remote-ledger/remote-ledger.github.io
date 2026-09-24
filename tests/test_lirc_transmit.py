"""The lircd port (``remote_ledger.lirc``) against lircd 0.10.2's own output.

Every ``.out`` file in ``tests/vectors/lirc/`` was written by the compiled
lirc 0.10.2 ``irsimsend`` (see the README there for the build, the one-line
``plugins/file.c`` patch and the command); the ``.conf`` files are synthetic,
each written to exercise named lircd rules. The first test replays every
vector through the port and demands byte-identical output; the coverage test
then proves, from the port's own rule counters, that those vectors really do
exercise each feature -- so a match is evidence about that feature, not about
a fixture that happened to avoid it.

The whole LIRC remotes corpus is compared the same way, outside pytest, by
``tools/lirc_oracle_compare.py``.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote_ledger.lirc import (
    IrSimSend,
    LircConfigError,
    LircNoTimings,
    LircTransmitError,
    LircUnsupportedProtocol,
    parse_file,
    transmit,
)
from remote_ledger.lirc import conf as C

VEC = Path(__file__).parent / "vectors" / "lirc"
VECTORS = json.loads((VEC / "index.json").read_text())["vectors"]
ORACLE = json.loads((VEC / "oracle.json").read_text())
STATUS = ORACLE["exit_status"]
LOG_ERRORS = ORACLE["log_errors"]
BY_ID = {v["id"]: v for v in VECTORS}
ACCEPTED = [v for v in VECTORS if not v["id"].startswith("reject_")]
REJECTED = [v for v in VECTORS if v["id"].startswith("reject_")]
KEYSYM = [v for v in VECTORS if "keysym" in v]


def _ids(vectors):
    return [v["id"] for v in vectors]


def _replay(vec):
    """Run the port the way the vector's irsimsend run was made.

    Returns the session, the button names irsimsend would print, the lines
    its file driver would write, and the reasons of any refused sends.
    """
    cfg = parse_file(VEC / vec["conf"])
    sim = IrSimSend(cfg, count=vec["count"],
                    keep_min_repeat=vec.get("keep_min_repeat", False))
    names, lines, failures = [], [], []

    def record(results):
        for res in results:
            lines.extend(res.lines())
            if res.kind == "failed":
                failures.append(res.reason)

    if "keysym" in vec:
        # simsend_keysym: the head remote only, first case-insensitive match,
        # and the keysym printed as given (irsimsend.cpp:221-234).
        head = cfg.remotes[0]
        record(sim.send_code(head, head.get_code_by_name(vec["keysym"])))
        names.append(vec["keysym"])
    else:
        for _, code, results in sim.run():
            names.append(code.name)
            record(results)
    return SimpleNamespace(sim=sim, names=names, lines=lines, failures=failures)


def _oracle_lines(vec_id):
    return (VEC / f"{vec_id}.out").read_text().splitlines()


def _oracle_sends(vec_id):
    """Split a timings-only .out into sends: tuples of ints, gap last."""
    sends, cur = [], []
    for line in _oracle_lines(vec_id):
        if line == "# end":
            sends.append(tuple(cur))
            cur = []
        else:
            kind, value = line.split()
            assert kind == ("space" if len(cur) % 2 else "pulse")
            cur.append(int(value))
    assert not cur, "trailing output without # end"
    return sends


def test_every_vector_has_oracle_output():
    """The index, the outputs on disk and the recorded statuses agree."""
    assert set(STATUS) == set(BY_ID)
    for v in VECTORS:
        assert (VEC / f"{v['id']}.out").is_file(), v["id"]
        assert (VEC / f"{v['id']}.stdout").is_file(), v["id"]
        assert (VEC / v["conf"]).is_file(), v["id"]


@pytest.mark.parametrize("vec", ACCEPTED, ids=_ids(ACCEPTED))
def test_port_reproduces_oracle_output(vec):
    """Line for line what lircd's file driver wrote, and in the same order."""
    got = _replay(vec)
    assert got.lines == _oracle_lines(vec["id"])
    if STATUS[vec["id"]] == 0:
        assert got.names == (VEC / f"{vec['id']}.stdout").read_text().splitlines()
    assert got.sim.terminated == (STATUS[vec["id"]] == -10)


@pytest.mark.parametrize("vec", REJECTED, ids=_ids(REJECTED))
def test_port_rejects_what_lircd_rejects(vec):
    """lircd exits 1 ("Cannot parse") and writes nothing; the port raises."""
    assert STATUS[vec["id"]] == 1
    assert (VEC / f"{vec['id']}.out").read_bytes() == b""
    assert (VEC / f"{vec['id']}.stdout").read_bytes() == b""
    with pytest.raises(LircConfigError):
        parse_file(VEC / vec["conf"])


@pytest.mark.parametrize("vec", REJECTED, ids=_ids(REJECTED))
def test_rejections_give_lircds_reason_and_line(vec):
    """The port's error is the one lircd logged, at the line lircd logged."""
    with pytest.raises(LircConfigError) as exc:
        parse_file(VEC / vec["conf"])
    logged = LOG_ERRORS[vec["id"]]
    assert logged[-1] == f"reading of file '{vec['conf']}' failed"
    if logged[0] == "unexpected end of file":  # logged without a line number
        assert logged[:-1] == [exc.value.reason]
    else:
        assert logged[:-1] == [f"error in configfile line {exc.value.line}:",
                               exc.value.reason]


@pytest.mark.parametrize("vec", ACCEPTED, ids=_ids(ACCEPTED))
def test_refused_sends_give_lircds_reason(vec):
    """Every send lircd refused, it logged; the port refuses the same ones."""
    # transmit.c:515-516 logs a second line after "invalid send buffer"
    logged = [e for e in LOG_ERRORS[vec["id"]]
              if e != "this remote configuration cannot be used to transmit"]
    assert _replay(vec).failures == logged


@pytest.mark.parametrize("vec", KEYSYM, ids=_ids(KEYSYM))
def test_transmit_matches_single_button_oracle(vec):
    """transmit() is `irsimsend -c N -k KEY` on a freshly loaded remote."""
    cfg = parse_file(VEC / vec["conf"])
    sends = transmit(cfg.remotes[0], vec["keysym"], sends=vec["count"])
    assert sends == _oracle_sends(vec["id"])


# Each feature the port claims, the vector that exercises it, and the rule
# counter that proves the vector really took that path in lircd's code.
COVERAGE = [
    ("NEC-style ditto repeat", "nec_ditto.c2", "repeat sends the repeat timings"),
    ("CONST_LENGTH padding", "const_length.c2", "CONST_LENGTH gap = min_gap - sum"),
    ("CONST_LENGTH refusal", "const_length.c2", "CONST_LENGTH too short gap: refused"),
    ("NO_HEAD_REP header sum quirk", "const_length.c2",
     "NO_HEAD_REP|CONST_LENGTH: header left out of sum"),
    ("RC5 toggle_bit_mask", "rc5_toggle.c2", "toggle_bit_mask (1 bit) forces the bit"),
    ("multi-bit toggle_bit_mask", "rc5_toggle.c2", "toggle_bit_mask (>1 bit) XORs a bit"),
    ("RC6 rc6_mask", "rc6.c2", "rc6_mask double-width bit"),
    ("SPACE_FIRST / first space dropped", "space_first.c2", "leading space dropped"),
    ("plead merging into data", "pre_post.c2", "adjacent pulses merged"),
    ("NO_HEAD_REP", "repeat_flags.c3", "NO_HEAD_REP drops the header"),
    ("NO_FOOT_REP", "repeat_flags.c3", "NO_FOOT_REP drops the foot"),
    ("REPEAT_HEADER", "repeat_flags.c3", "REPEAT_HEADER header before repeat"),
    ("repeat_gap", "repeat_flags.c3", "repeat_gap replaces the gap"),
    ("repeat_mask", "repeat_flags.c3", "repeat_mask XOR on repeat"),
    ("toggle_mask", "toggle_mask.c5", "toggle_mask XORs a bit"),
    ("multi-code buttons", "multi_code.c4", "multi-code: later code sent"),
    ("low-gap concatenation", "multi_code.c4", "low-gap concatenation"),
    ("RAW_CODES", "raw.c2", "raw code sent"),
    ("raw ditto via stale pointer", "raw.c2", "repeat read through a raw code's pointer"),
    ("repeat_countdown concatenation", "min_repeat.c2.keep-min-repeat",
     "low-gap concatenation"),
    ("raw unrolling", "min_repeat.c2.keep-min-repeat",
     "raw signals unrolled for concatenation"),
    ("RC-MM", "rcmm_xmp.c2", "RCMM data"),
    ("XMP", "rcmm_xmp.c2", "XMP data"),
    ("trailing space held back", "rc5_toggle.c2", "pending final space not emitted"),
    ("zero-length halves, even buffer trimmed", "zero_durations.c2",
     "even-length buffer trimmed"),
]


@pytest.mark.parametrize("feature, vec_id, rule", COVERAGE, ids=[c[0] for c in COVERAGE])
def test_vectors_exercise_each_feature(feature, vec_id, rule):
    got = _replay(BY_ID[vec_id])
    assert got.lines == _oracle_lines(vec_id)
    assert got.sim.lircd.trace[rule] > 0, f"{vec_id} never exercises {feature}"


def test_keep_min_repeat_changes_the_press():
    """irsimsend's zeroed min_repeat hides the countdown; lircd's does not."""
    plain = _oracle_lines("min_repeat.c2")
    kept = _oracle_lines("min_repeat.c2.keep-min-repeat")
    assert plain != kept
    assert len(kept) > len(plain)


# --- parser semantics, checked against the values lircd must have used ------

def _remote(cfg, name):
    return next(r for r in cfg.remotes if r.name == name)


def test_numbers_are_base_0():
    """config_file.c:249-333: strtoul/strtol with base 0, durations included."""
    cfg = parse_file(VEC / "numbers.conf")
    r = cfg.remotes[0]
    assert r.bits == 8  # "010"
    assert (r.phead, r.shead) == (9000, 0o4311)  # "0x2328 04311"
    assert (r.pone, r.sone) == (560, 0o1540)
    assert (r.pzero, r.szero) == (560, 0x230)
    assert r.ptrail == 560  # "01060"
    assert (r.pre_data_bits, r.pre_data) == (8, 0xFF)  # "0x8", "0377"
    assert r.gap == 108000
    codes = {c.name: c.code for c in r.codes}
    assert codes == {"KEY_OCT": 0o17, "KEY_HEX": 0xF0, "KEY_DEC": 99,
                     "KEY_NEG": 0xFF, "KEY_WIDE": 0xFF}
    # the durations lircd actually sent carry the octal values
    assert "space 2249" in _oracle_lines("numbers.c2")
    assert any("Invalid code : KEY_NEG" in msg for _, msg in cfg.warnings)


def test_32_bit_fields_wrap_and_plus_is_accepted():
    r = parse_file(VEC / "numbers_edge.conf").remotes[0]
    assert r.gap == (2**32 + 10000) % 2**32 == 10000
    assert (r.pone, r.sone, r.codes[0].code) == (500, 1500, 0x0A)
    assert _oracle_lines("numbers_edge.c2")[-2:] == ["space 10000", "# end"]


def test_code_lines_cite_the_source():
    path = VEC / "nec_ditto.conf"
    text = path.read_text().splitlines()
    cfg = parse_file(path)
    r = cfg.remotes[0]
    assert text[r.line - 1].strip() == "begin remote"
    assert text[r.end_line - 1].strip() == "end remote"
    assert text[r.field_lines["header"] - 1].split()[0] == "header"
    for code in r.codes:
        assert text[code.line - 1].split()[0] == code.name
    raw = parse_file(VEC / "raw.conf")
    raw_text = (VEC / "raw.conf").read_text().splitlines()
    for rem in raw.remotes:
        for code in rem.codes:
            words = raw_text[code.line - 1].split()
            if code.signals is None:  # raw_flag_codes: an ordinary code line
                assert words == [code.name, "0x1"]
            else:
                assert words == ["name", code.name]


def test_legacy_toggle_bit_becomes_a_mask():
    """config_file.c:1272-1286: counted from the MSB of all bits, 1-based."""
    cfg = parse_file(VEC / "legacy.conf")
    assert _remote(cfg, "legacy_toggle_bit").toggle_bit_mask == 1 << (13 - 2)
    assert _remote(cfg, "legacy_repeat_bit").toggle_bit_mask == 1 << (13 - 3)
    both = _remote(cfg, "both_toggles")
    assert both.toggle_bit_mask == 0x1
    assert any("uses both toggle_bit and toggle_bit_mask" in m for _, m in cfg.warnings)
    rc6 = _remote(parse_file(VEC / "rc6.conf"), "rc6_legacy")
    assert rc6.rc6_mask == rc6.toggle_bit_mask == 1 << (37 - 5)


def test_reverse_flips_the_first_code_only():
    """config_file.c:1254-1271 never touches the code->next list."""
    r = _remote(parse_file(VEC / "legacy.conf"), "reversed")
    assert (r.pre_data, r.post_data) == (0x8, 0xC)
    codes = {c.name: c.codes for c in r.codes}
    assert codes == {"KEY_5": (0x80,), "KEY_6": (0x01, 0x80)}
    assert r.flags & C.COMPAT_REVERSE and not r.flags & C.REVERSE


def test_remotes_are_sorted_by_bit_count_then_raw_size():
    cfg = parse_file(VEC / "sort_order.conf")
    assert [r.name for r in cfg.file_order] == ["r_raw", "r_raw_small", "r_32", "r_16", "r_code"]
    assert [r.name for r in cfg.remotes] == ["r_16", "r_code", "r_32", "r_raw_small", "r_raw"]


def test_include_splices_and_fixes_up_once():
    cfg = parse_file(VEC / "include_parent.conf")
    assert [r.name for r in cfg.file_order] == ["parent_first", "child", "parent_last"]
    assert _remote(cfg, "child").codes[0].code == 0x80  # REVERSE applied once


def test_long_lines_are_read_in_4095_byte_pieces():
    """fgets(buf, 4096) splits the line, and a number with it."""
    code = parse_file(VEC / "long_line.conf").remotes[0].codes[0]
    assert len(code.signals) == 1 + 900 + 1 + 1
    assert code.signals[818:822] == (1690, 16, 90, 1690)


def test_crlf_is_stripped():
    r = parse_file(VEC / "crlf.conf").remotes[0]
    assert (r.name, r.gap, r.codes[0].code) == ("crlf", 40000, 0x0A)


def test_multi_code_list_ends_at_a_comment():
    r = _remote(parse_file(VEC / "multi_code.conf"), "multi_slow")
    assert {c.name: c.codes for c in r.codes} == {
        "KEY_A": (0x01, 0x02, 0x03), "KEY_B": (0x10,), "KEY_C": (0x40, 0x80)}


# --- the one-button API ------------------------------------------------------

def test_transmit_nec_press_and_ditto():
    r = parse_file(VEC / "nec_ditto.conf").remotes[0]
    press, ditto = transmit(r, "KEY_POWER")
    assert press[:2] == (9000, 4500) and sum(press) == 108000
    assert ditto == (9000, 2250, 560, 96190)


def test_transmit_refuses_what_lircd_cannot_send():
    cfg = parse_file(VEC / "unsupported.conf")
    with pytest.raises(LircUnsupportedProtocol):
        transmit(_remote(cfg, "grundig_timed"), "KEY_A")
    with pytest.raises(LircUnsupportedProtocol):
        transmit(_remote(cfg, "serial"), "KEY_D")
    with pytest.raises(LircNoTimings) as exc:
        transmit(_remote(parse_file(VEC / "sort_order.conf"), "r_code"), "KEY_Y")
    assert exc.value.code == 0x5678
    with pytest.raises(LircTransmitError) as exc:
        transmit(_remote(parse_file(VEC / "const_length.conf"), "too_short"), "KEY_ONES")
    assert exc.value.reason.startswith("too short gap")
    with pytest.raises(LircTransmitError):
        transmit(cfg.remotes[0], "NO_SUCH_BUTTON")
