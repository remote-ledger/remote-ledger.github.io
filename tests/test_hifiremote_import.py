"""``rl import hifi-remote`` (DESIGN D110 to D114): the page reader, the reading of a marker
and a command cell, and the whole import over a small snapshot and over the real one."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.hifiremote import importer as hi
from remote_ledger.hifiremote.pages import DEFAULT_COLOUR, Span, read_page
from remote_ledger.remote import load_remote
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / hi.SNAPSHOT


def cell(text: str, colour: str = DEFAULT_COLOUR) -> str:
    return f"<td><p><b><span style='font-size:10.0pt;color:{colour}'>{text}</span></b></p></td>"


def page(title: str, intro: list[str], tables: list[tuple[list[str], list[tuple[str, str]]]]) -> str:
    """A page as Word writes one: paragraphs with hard newlines in them, and for each table
    the marker paragraphs and the rows. A row's text is ``(command code, cell html)``."""
    out = [f"<html><head><title>{title}</title></head><body>"]
    out += [f"<p class=MsoNormal><span style='color:silver'>{p}</span></p>" for p in intro]
    for markers, rows in tables:
        out += [f"<p class=MsoNormal><b><span style='color:silver'>{m}</span></b></p>"
                for m in markers]
        out.append("<table><tr>" + cell("Command Code") + cell("Command(s)") + "</tr>")
        for code, html in rows:
            out.append(f"<tr>{cell(code)}<td>{html}</td></tr>")
        out.append("</table>")
    out.append("</body></html>")
    return "\n".join(out)


def span(text: str, colour: str = DEFAULT_COLOUR) -> str:
    return f"<p><span style='color:{colour}'>{text}</span></p>"


# --- the reader --------------------------------------------------------------------------


def test_a_paragraph_with_hard_newlines_is_one_line_and_a_cell_keeps_its_colours():
    html = page("Sony Test (1)", ["first line\nsecond line"], [
        (["Sony:1 (commands\nfrom 16/48)"], [("21", span("Power")),
                                             ("11", span("Enter, ") + span("Set", "#3366ff"))])])
    p = read_page(html)
    assert p.title == "Sony Test (1)"
    assert "first line second line" in p.intro
    (table,) = p.tables
    assert table.header == ["Command Code", "Command(s)"]
    assert table.before[-1] == "Sony:1 (commands from 16/48)"
    assert [(r.code_text, r.text) for r in table.rows] == [("21", "Power"), ("11", "Enter, Set")]
    assert table.rows[1].spans == [Span("Enter, ", "silver"), Span("Set", "#3366ff")]


def test_a_table_of_another_shape_is_read_without_its_rows():
    html = ("<html><title>t</title><table><tr><td>a</td><td>b</td><td>c</td></tr>"
            "<tr><td>1</td><td>2</td><td>3</td></tr></table></html>")
    (table,) = read_page(html).tables
    assert table.header == ["a", "b", "c"] and table.rows == []


def test_a_malformed_row_of_a_command_table_stops_the_read():
    html = ("<html><title>t</title><table><tr><td>Command Code</td><td>Command(s)</td></tr>"
            "<tr><td>1</td><td>a</td><td>extra</td></tr></table></html>")
    with pytest.raises(ValueError):
        read_page(html)


def test_the_real_blu_ray_page_reads_as_the_author_wrote_it():
    p = read_page((SNAPSHOT / "Sony_bluray.htm").read_bytes().decode(hi.ENCODING))
    assert p.title.startswith("Sony Blu-ray (26.226; 26.234; 26.242")
    (table,) = p.tables
    assert len(table.rows) == 87 and table.rows[0].code_text == "0" and table.rows[0].text == "1"
    set_row = next(r for r in table.rows if r.code_text == "11")
    assert [(s.text, s.colour) for s in set_row.spans] == [("Enter, ", "silver"), ("Set", "#3366ff")]
    assert any(t.startswith("Sony:26.151") for t in table.before)


# --- markers and protocols -------------------------------------------------------------


@pytest.mark.parametrize("text,bits,codes", [
    ("Sony:26.226; 26.234; 26.242", None, ["26.226", "26.234", "26.242"]),
    ("Sony:17; 57; 81", None, ["17", "57", "81"]),
    ("Sony:26.202 (CD-R specific commands, similar to MD)", None, ["26.202"]),
    ("Sony:26.241 Memory Stick", None, ["26.241"]),
    ("Sony 15 :26", 15, ["26"]),
    ("Sony15:4", 15, ["4"]),
    ("Sony20:1.0", 20, ["1.0"]),
    ("Sony:12; (44) (old stuff: menus, DSP)", None, ["12", "44"]),
    ("Sony:(16.0); 16.8", None, ["16.0", "16.8"]),
    ("Sony:208 ((some) commands from 144/176?)", None, ["208"]),
    ("Sony:16; 48 (volume, power, inputs)", None, ["16", "48"]),
])
def test_a_marker_gives_its_codes_and_the_frame_it_names(text, bits, codes):
    marker = hi.parse_marker(text)
    assert marker is not None and marker.bits == bits and marker.text == text
    assert [c.text for c in marker.codes] == codes


def test_parentheses_around_a_code_are_remembered_and_prose_is_not_a_marker():
    codes = hi.parse_marker("Sony:12; (44) (old)").codes
    assert [c.parenthesised for c in codes] == [False, True]
    assert hi.parse_marker("Sony Blu-ray (26.226)") is None
    assert hi.parse_marker("Magenta = Plasma TV") is None


@pytest.mark.parametrize("bits,device,sub,expected", [
    (None, 1, None, "Sony12"), (None, 31, None, "Sony12"),
    (None, 32, None, "Sony15"), (None, 255, None, "Sony15"),
    (None, 26, 226, "Sony20"), (None, 0, 0, "Sony20"),
    (15, 26, None, "Sony15"), (20, 1, 0, "Sony20"), (15, 4, None, "Sony15"),
])
def test_the_frame_is_read_from_the_marker_then_the_dot_then_the_size(bits, device, sub, expected):
    assert hi.protocol_of(bits, hi.Code(device, sub, False)) == (expected, None)


@pytest.mark.parametrize("bits,device,sub", [
    (None, 256, None),       # no frame holds it
    (12, 40, None),          # a five-bit device field
    (None, 32, 5),           # Sony20's device is five bits
    (None, 26, 256),         # a subdevice is eight bits
    (15, 26, 3),             # Sony15 has no subdevice
    (20, 26, None),          # Sony20 needs one
])
def test_a_code_no_frame_can_hold_yields_nothing_and_says_why(bits, device, sub):
    protocol, why = hi.protocol_of(bits, hi.Code(device, sub, False))
    assert protocol is None and why


# --- a command cell ------------------------------------------------------------------------


def alternatives(*pairs):
    return hi.split_alternatives([Span(t, c) for t, c in pairs])


def test_a_cell_is_split_at_commas_outside_brackets_and_each_name_keeps_its_colours():
    alts = alternatives(("Monitor 1 (input 1 out), Monitor Select 1", "silver"))
    assert [a.text for a in alts] == ["Monitor 1 (input 1 out)", "Monitor Select 1"]
    alts = alternatives(("Enter, ", "silver"), ("Set", "#3366ff"))
    assert [(a.text, sorted(a.colours)) for a in alts] == [("Enter", []), ("Set", ["#3366ff"])]
    alts = alternatives(("Shuttle Rev 2, [Scan R2/Slow R1]", "silver"))
    assert [a.text for a in alts] == ["Shuttle Rev 2", "[Scan R2/Slow R1]"]


@pytest.mark.parametrize("text,label", [
    ("Power On (discrete)", "Power On"),
    ("Volume –", "Volume -"),
    ("Pause (discrete) [used by jog/shuttle]", "Pause (discrete) [used by jog/shuttle]"),
    ("Up (Menu)", "Up (Menu)"),
    ("Green [probably incorrect]", "Green [probably incorrect]"),
])
def test_a_label_keeps_the_pages_words_but_a_trailing_discrete_and_dashes(text, label):
    assert hi.label_of(text) == label


def test_names_fold_alike_get_their_command_code_on_every_member_and_nothing_folds_to_empty():
    names = hi._key_names([(104, "Green"), (110, "Green [probably incorrect]"), (5, "Volume +"),
                           (6, "Volume -"), (7, "Up"), (8, "UP"), (9, ","), (10, "?")])
    assert names[104] == "KEY_GREEN" and names[110] == "KEY_GREEN_PROBABLY_INCORRECT"
    assert names[5] == "KEY_VOLUME_PLUS" and names[6] == "KEY_VOLUME_MINUS"
    assert names[7] == "KEY_UP_7" and names[8] == "KEY_UP_8"
    assert names[9] == "KEY_COMMAND_9" and names[10] == "KEY_COMMAND_10"


def test_the_kind_of_device_is_the_title_without_the_codes_or_the_first_line_when_it_says_nothing():
    kind = lambda title, intro=(): hi.type_of(read_page(page(title, list(intro), [])))
    assert kind("Sony Blu-ray (26.226; 26.234; 26.242; 26.151; 26.135; 26.164)") == "Blu-ray"
    assert kind("Sony IDTV (Free-to-air digital receiver) (23.13)") == "IDTV (Free-to-air digital receiver)"
    assert kind("Sony x (x)", ["Sony System Off (4)"]) == "System Off"


# --- a small snapshot, imported --------------------------------------------------------------


def snapshot(tmp_path: Path, pages: dict[str, str]) -> Path:
    directory = tmp_path / "snap"
    directory.mkdir()
    entries = {}
    for name, html in pages.items():
        data = html.encode(hi.ENCODING)
        (directory / name).write_bytes(data)
        entries[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    (directory / hi.MANIFEST).write_text(json.dumps(
        {"source": hi.UPSTREAM_URL, "retrieved": "2026-10-08", "pages": entries}), encoding="utf-8")
    return directory


BLU_RAY = page("Sony Blu-ray (26.226; 26.135; 26.164)", [
    "Magenta = Portable Blu-ray player (BDP-SX*, RMT-B113*)",
    "Blue = Home Entertainment Server (HES-V1000, RMT-HS001A)",
    "Olive = probably incorrect codes for Green and Yellow",
], [(["Sony:26.226", "Sony:26.135 (Portable Blu-ray player)", "Sony:26.164 (Home Entertainment Server)"], [
    ("21", span("Power")),
    ("11", span("Enter, ") + span("Set", "#3366ff")),
    ("18", span("Volume Up", "fuchsia")),
    ("52", span("Import", "#3366ff")),
    ("104", span("Green")),
    ("110", span("Green [probably incorrect]", "olive")),
    ("26", span("Play On (discrete)")),
    ("300", span("Out of range")),
    ("48-57", span("0-9")),
])])
TV = page("Sony TV (1, 164, 26)", [], [
    (["Sony:1"], [("21", span("Power"))]),
    (["Sony:164"], [("21", span("Power"))]),
    (["Sony 15 :26"], [("21", span("Power"))]),
])


@pytest.fixture()
def small(tmp_path):
    directory = snapshot(tmp_path, {"index.htm": "<html></html>", "Sony_bluray.htm": BLU_RAY,
                                    "Sony_tv.htm": TV, "Sony_links.htm": "<html><title>Links</title></html>"})
    return hi.import_tree(directory, {})


def test_every_table_becomes_one_file_per_device_code_and_every_row_a_key(small):
    docs, report = small
    assert sorted(docs) == [
        "remotes/hifi-remote/sony/bluray-26.135.json", "remotes/hifi-remote/sony/bluray-26.164.json",
        "remotes/hifi-remote/sony/bluray-26.226.json", "remotes/hifi-remote/sony/tv-1.json",
        "remotes/hifi-remote/sony/tv-164.json", "remotes/hifi-remote/sony/tv-26.json"]
    tv = docs["remotes/hifi-remote/sony/tv-26.json"]
    assert tv["protocol"] == {"name": "Sony15", "carrierHz": 40000, "minSends": 3}
    assert docs["remotes/hifi-remote/sony/tv-1.json"]["protocol"]["name"] == "Sony12"
    assert docs["remotes/hifi-remote/sony/bluray-26.226.json"]["protocol"]["name"] == "Sony20"
    assert tv["manufacturer"] == "Sony" and tv["model"] == "TV 26"
    assert tv["controls"] == ["Sony TV (device code 26)"]
    assert dict(report.protocols) == {"Sony20": 3, "Sony12": 1, "Sony15": 2}
    assert report.skipped_pages == [("Sony_links.htm", "no `Command Code | Command(s)` table"),
                                    ("index.htm", "the index of the pages, not a device")]


def test_a_colour_the_legend_limits_to_a_device_leaves_the_command_out_of_the_others(small):
    docs, report = small
    plain = docs["remotes/hifi-remote/sony/bluray-26.226.json"]["keys"]
    portable = docs["remotes/hifi-remote/sony/bluray-26.135.json"]["keys"]
    server = docs["remotes/hifi-remote/sony/bluray-26.164.json"]["keys"]
    assert "KEY_VOLUME_UP" not in plain and "KEY_VOLUME_UP" in portable and "KEY_VOLUME_UP" not in server
    assert "KEY_IMPORT" not in plain and "KEY_IMPORT" in server and "KEY_IMPORT" not in portable
    assert report.limited["Sony_bluray.htm"] == 4     # volume up for 226 and 164, import for 226 and 135
    # a name in a limited colour is not a name for the others
    assert "also named" not in plain["KEY_ENTER"]["forms"][0]["source"]
    assert "also named 'Set'" in server["KEY_ENTER"]["forms"][0]["source"]


def test_a_citation_names_the_page_its_hash_the_table_the_row_the_marker_and_the_reading(small):
    docs, _ = small
    key = docs["remotes/hifi-remote/sony/bluray-26.164.json"]["keys"]["KEY_ENTER"]
    form = key["forms"][0]
    sha = hashlib.sha256(BLU_RAY.encode(hi.ENCODING)).hexdigest()[:8]
    assert key["label"] == "Enter"
    assert form["source"] == (
        f"hifi-remote.com/sony/Sony_bluray.htm@{sha} (retrieved 2026-10-08) table 1, command 11 "
        "'Enter, Set' under 'Sony:26.164 (Home Entertainment Server)': Sony20 device 26 "
        "subdevice 164 function 11; also named 'Set'; the page's legend: "
        "'Blue = Home Entertainment Server (HES-V1000, RMT-HS001A)'")
    assert (form["device"], form["subdevice"], form["function"]) == (26, 164, 11)
    assert form["confidence"] == "plausible" and form["type"] == "irp"


def test_a_row_the_page_doubts_is_untested_and_a_discrete_note_goes_to_the_citation(small):
    docs, _ = small
    keys = docs["remotes/hifi-remote/sony/bluray-26.226.json"]["keys"]
    doubtful = keys["KEY_GREEN_PROBABLY_INCORRECT"]["forms"][0]
    assert doubtful["confidence"] == "untested" and "Olive = probably incorrect" in doubtful["source"]
    assert keys["KEY_GREEN"]["forms"][0]["confidence"] == "plausible"
    discrete = keys["KEY_PLAY_ON"]
    assert discrete["label"] == "Play On" and "the page writes it '(discrete)'" in discrete["forms"][0]["source"]


def test_a_row_that_is_not_one_command_number_is_listed_not_dropped(small):
    _, report = small
    assert {(r[2], r[4]) for r in report.skipped_rows} == {
        ("300", "the command code is not one number from 0 to 127"),
        ("48-57", "the command code is not one number from 0 to 127")}


def test_an_authored_remote_of_the_same_name_wins(tmp_path):
    directory = snapshot(tmp_path, {"Sony_tv.htm": TV})
    docs, report = hi.import_tree(directory, {("sony", "tv 26"): "remotes/sony/x.json"})
    assert "remotes/hifi-remote/sony/tv-26.json" not in docs and len(docs) == 2
    assert report.collisions == [("Sony_tv.htm", 3, "remotes/hifi-remote/sony/tv-26.json")]


def test_a_table_with_no_marker_or_a_code_no_frame_holds_is_listed(tmp_path):
    html = page("Sony Odd (9)", [], [([], [("1", span("A"))]), (["Sony:300"], [("1", span("B"))]),
                                      (["Sony:9"], [("1", span("C"))])])
    docs, report = hi.import_tree(snapshot(tmp_path, {"Sony_odd.htm": html}), {})
    assert list(docs) == ["remotes/hifi-remote/sony/odd-9.json"]
    assert report.skipped_tables == [("Sony_odd.htm", 1, "no `Sony:<device code>` line before it")]
    assert report.skipped_codes[0][:2] == ("Sony_odd.htm", "300")


def test_two_tables_for_one_device_code_stop_the_import(tmp_path):
    html = page("Sony Twice (9)", [], [(["Sony:9"], [("1", span("A"))]), (["Sony:9"], [("2", span("B"))])])
    with pytest.raises(ValidationError, match="yielded twice"):
        hi.import_tree(snapshot(tmp_path, {"Sony_twice.htm": html}), {})


def test_the_snapshot_is_the_one_the_manifest_pins(tmp_path):
    directory = snapshot(tmp_path, {"Sony_tv.htm": TV})
    (directory / "Sony_tv.htm").write_bytes(TV.replace("Power", "Power ").encode(hi.ENCODING))
    with pytest.raises(ValidationError, match="not the page the manifest pins"):
        hi.load_snapshot(directory)
    (directory / "Sony_tv.htm").unlink()
    with pytest.raises(ValidationError, match="unreadable"):
        hi.load_snapshot(directory)
    with pytest.raises(ValidationError, match="cannot read the snapshot manifest"):
        hi.load_snapshot(tmp_path / "nowhere")


def test_writing_is_wholesale_keeps_the_readme_and_removes_what_the_snapshot_no_longer_yields(tmp_path):
    root = tmp_path / "repo"
    (root / "remotes").mkdir(parents=True)
    (root / hi.IMPORT_ROOT).mkdir(parents=True)
    (root / hi.IMPORT_ROOT / "README.md").write_text("authored", encoding="utf-8")
    stale = root / hi.IMPORT_ROOT / "sony" / "gone-1.json"
    stale.parent.mkdir()
    stale.write_text("{}", encoding="utf-8")
    directory = snapshot(tmp_path, {"Sony_bluray.htm": BLU_RAY, "Sony_tv.htm": TV})
    report = hi.write_import(root, directory)
    assert not stale.exists() and (root / hi.IMPORT_ROOT / "README.md").read_text() == "authored"
    assert report.remotes["imported"] == 6
    written = sorted(p.name for p in (root / hi.IMPORT_ROOT / "sony").glob("*.json"))
    assert written[0] == "bluray-26.135.json" and len(written) == 6
    text = (root / hi.IMPORT_ROOT / hi.REPORT).read_text(encoding="utf-8")
    assert text.startswith("# hifi-remote.com Sony import report") and "## Rows skipped (2)" in text
    # what it wrote loads, compiles, and a second run changes nothing
    for path in (root / hi.IMPORT_ROOT / "sony").glob("*.json"):
        remote = load_remote(path)
        for key in remote.keys:
            remote.compile_group(key, "primary")
    before = {p: p.read_bytes() for p in (root / hi.IMPORT_ROOT).rglob("*") if p.is_file()}
    hi.write_import(root, directory)
    assert before == {p: p.read_bytes() for p in (root / hi.IMPORT_ROOT).rglob("*") if p.is_file()}


# --- the real snapshot -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_import(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    (root / "remotes").mkdir()
    report = hi.write_import(root, SNAPSHOT)
    return root, report


def test_the_committed_import_is_exactly_what_the_pinned_snapshot_yields(real_import):
    """SPEC R19.5: re-running the import over the pinned upstream reproduces every imported file
    byte for byte, and the report with them."""
    root, _ = real_import
    committed = {p.relative_to(ROOT / hi.IMPORT_ROOT).as_posix(): p.read_bytes()
                 for p in (ROOT / hi.IMPORT_ROOT).rglob("*") if p.is_file() and p.name != "README.md"}
    fresh = {p.relative_to(root / hi.IMPORT_ROOT).as_posix(): p.read_bytes()
             for p in (root / hi.IMPORT_ROOT).rglob("*") if p.is_file()}
    assert fresh == committed


def test_the_real_snapshot_reads_all_of_its_tables_and_leaves_no_row_or_code_unexplained(real_import):
    _, report = real_import
    assert report.pages["read"] == 58 and report.tables["read"] == 104
    assert report.remotes["imported"] == 151
    assert sum(n for k, n in report.keys.items() if k.startswith("imported")) == 6_772
    assert report.skipped_tables == [] and report.skipped_codes == [] and report.collisions == []
    assert sorted(p for p, _ in report.skipped_pages) == [
        "Sony_devtheory.htm", "Sony_dsp.htm", "Sony_efctable.htm", "Sony_links.htm",
        "Sony_missing.htm", "Sony_pronto.htm", "Sony_updates.htm", "index.htm"]
    assert {r[0] for r in report.skipped_rows} == {"Sony_md.htm", "Sony_rcvr.htm"} and len(report.skipped_rows) == 4


def test_every_colour_the_pages_use_has_a_legend_but_one(real_import):
    """The Blu-ray, TV, CD, DVD, receiver, satellite, VCR and boombox pages colour commands. All
    but one colour has its legend line on the page; the one that has not (red on the boombox
    page, one row) is recorded as having none."""
    _, report = real_import
    used = {(page, colour) for page, counter in report.colours.items() for colour in counter}
    assert {(p, c) for p, c in used if f"{p} {c}" not in report.legends} == {("Sony_bb.htm", "red")}
    assert ("Sony_bluray.htm", "fuchsia") in hi.LIMITED_TO and ("Sony_bluray.htm", "#3366ff") in hi.LIMITED_TO


def test_the_pages_give_the_frame_the_size_rule_gives_except_where_they_say_otherwise(real_import):
    root, _ = real_import
    seen = {}
    for path in (root / hi.IMPORT_ROOT / "sony").glob("*.json"):
        doc = load(path)
        code = doc["controls"][0].rsplit("device code ", 1)[1].rstrip(")")
        seen[path.stem] = (code, doc["protocol"]["name"])
    assert seen["bluray-26.226"] == ("26.226", "Sony20")
    assert seen["tv-26"] == ("26", "Sony15")           # the page's own `Sony 15 :26`
    assert seen["lcd-1.0"] == ("1.0", "Sony20")        # `Sony20:1.0`
    assert seen["tv-1"] == ("1", "Sony12") and seen["tv-164"] == ("164", "Sony15")
    assert seen["rcvr-44"] == ("44", "Sony15")         # an AV2 code, device 12 plus 32


def test_a_legend_that_limits_a_command_to_one_device_does_so_in_the_real_files():
    plain = load(ROOT / hi.IMPORT_ROOT / "sony" / "bluray-26.226.json")["keys"]
    portable = load(ROOT / hi.IMPORT_ROOT / "sony" / "bluray-26.135.json")["keys"]
    server = load(ROOT / hi.IMPORT_ROOT / "sony" / "bluray-26.164.json")["keys"]
    assert (len(plain), len(portable), len(server)) == (84, 86, 85)
    assert "KEY_VOLUME_UP" in portable and "KEY_VOLUME_UP" not in plain
    assert "KEY_IMPORT" in server and "KEY_IMPORT" not in plain


def test_the_manifest_records_the_date_and_a_hash_for_every_page():
    manifest = json.loads((SNAPSHOT / hi.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["source"] == hi.UPSTREAM_URL and len(manifest["pages"]) == 66
    on_disk = {p.name for p in SNAPSHOT.iterdir()} - {hi.MANIFEST}
    assert on_disk == set(manifest["pages"])
    for name, entry in manifest["pages"].items():
        data = (SNAPSHOT / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"] and len(data) == entry["bytes"], name
