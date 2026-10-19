"""A layout's picture: the SVG profile and where a layout finds its file (R29, D357-D359)."""

import json
from pathlib import Path

import pytest

from remote_ledger import cli
from remote_ledger.svgart import (MAX_BYTES, MAX_ELEMENTS, MAX_TEXT, art_problems, inspect_art, read_art,
                                  ref_problem)
from remote_ledger.validate import validate_file

NS = 'xmlns="http://www.w3.org/2000/svg"'
KEYS = {"KEY_POWER", "KEY_UP", "KEY_OK", "ch1"}


def svg(body: str = "", root: str = 'viewBox="0 0 100 200"') -> str:
    return f'<svg {NS} {root}>{body}</svg>'


GOOD = svg(
    '<g id="body"><rect x="5" y="5" width="90" height="190" rx="20" fill="#333" stroke="#111"/></g>'
    '<g data-key="KEY_POWER"><circle cx="30" cy="20" r="10" fill="#c00"/>'
    '<text x="30" y="24" font-size="10" text-anchor="middle" fill="#fff">I/O</text></g>'
    '<g data-key="KEY_OK"><circle cx="50" cy="100" r="14" fill="#69c"/></g>'
    '<g class="inert"><rect x="60" y="20" width="20" height="14" rx="4" fill="#888"/></g>'
)


def problems(text, keys=KEYS):
    return list(art_problems(text, keys, where="t"))


def has(text, fragment, keys=KEYS):
    found = problems(text, keys)
    assert any(fragment in p for p in found), (fragment, found)


# --- what passes -------------------------------------------------------------------------------

def test_a_plain_picture_has_no_problems():
    assert problems(GOOD) == []


def test_the_report_lists_the_keys_in_order_and_counts_the_inert_ones():
    r = inspect_art(GOOD, KEYS, where="t")
    assert r.keys == ("KEY_POWER", "KEY_OK") and r.inert == 1
    assert r.viewbox == (0.0, 0.0, 100.0, 200.0) and r.problems == ()


def test_the_remotes_keys_need_not_all_be_drawn():
    """R7: a remote may have keys with no button, and a picture may omit them."""
    assert problems(GOOD, KEYS | {"KEY_NEVER_DRAWN"}) == []


def test_a_xml_declaration_and_a_gradient_are_fine():
    text = ('<?xml version="1.0" encoding="UTF-8"?>' + svg(
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff"/>'
        '<stop offset="100%" stop-color="#999"/></linearGradient></defs>'
        '<g data-key="KEY_UP"><rect x="1" y="1" width="9" height="9" fill="url(#g)"/></g>'))
    assert problems(text) == []


# --- the profile is closed ---------------------------------------------------------------------

@pytest.mark.parametrize("element", ["script", "style", "image", "use", "foreignObject", "a", "filter",
                                     "animate", "set", "mask", "clipPath", "pattern", "marker", "symbol",
                                     "switch", "iframe"])
def test_elements_that_load_run_or_link_are_refused_by_name(element):
    has(svg(f"<{element}/>"), "is not allowed")


def test_an_unknown_element_is_refused_even_if_harmless():
    has(svg("<blink/>"), "not an element of the profile")


@pytest.mark.parametrize("attr", ['onclick="x()"', 'onload="x()"', 'href="https://x"', 'style="fill:red"',
                                  'xlink:href="#a"', 'pointer-events="none"', 'filter="url(#f)"',
                                  'clip-path="url(#c)"', 'mask="url(#m)"', 'tabindex="0"'])
def test_attributes_outside_the_list_are_refused(attr):
    text = f'<svg {NS} xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 9 9"><rect {attr} width="1" height="1"/></svg>'
    assert any("`" in p for p in problems(text)), problems(text)


@pytest.mark.parametrize("text,why", [
    ('<!DOCTYPE svg [<!ENTITY a "aaaa">]>' + GOOD, "doctype"),
    ("<!DOCTYPE svg>" + GOOD, "doctype"),
    (GOOD.replace("</svg>", "<![CDATA[x]]></svg>"), "cdata"),
    ('<?xml-stylesheet href="x"?>' + GOOD, "processing instruction"),
], ids=["entity", "doctype", "cdata", "pi"])
def test_constructs_that_expand_or_include_are_refused_before_parsing(text, why):
    found = problems(text)
    assert found and why.split()[0] in found[0].lower(), found


def test_malformed_xml_is_a_problem_not_a_crash():
    has("<svg", "not well-formed")


def test_the_root_must_be_an_svg_in_the_svg_namespace():
    has('<svg viewBox="0 0 1 1"/>', "root is not")
    has('<html xmlns="http://www.w3.org/1999/xhtml"/>', "root is not")


def test_a_foreign_namespace_element_is_refused():
    has(svg('<x:y xmlns:x="urn:x"/>'), "not in the SVG namespace")


def test_a_viewbox_is_required_and_needs_area():
    has(f"<svg {NS}/>", "viewBox")
    has(svg(root='viewBox="0 0 0 5"'), "no area")
    has(svg(root='viewBox="a b c d"'), "viewBox")


# --- values ------------------------------------------------------------------------------------

@pytest.mark.parametrize("attr", ['fill="url(https://x/y)"', 'fill="url(#nope)"', 'fill="javascript:x"',
                                  'fill="expression(1)"', 'stroke="var(--x)"', 'fill="#12"'])
def test_a_colour_cannot_point_anywhere_but_a_gradient_of_this_file(attr):
    has(svg(f'<rect width="1" height="1" {attr}/>'), "`fill`" if "fill" in attr else "`stroke`")


@pytest.mark.parametrize("attr", ['width="1em"', 'width="-"', 'x="calc(1+1)"', 'rx="1 2"', 'r="1e999x"'])
def test_a_length_is_a_plain_number(attr):
    has(svg(f"<rect {attr}/>"), attr.split("=")[0])


@pytest.mark.parametrize("d,ok", [("M0,0 L10 10 h5 v5 c1 1 2 2 3 3 z", True), ("M0 0 L<script>", False),
                                  ("M0 0 url(#x)", False), ("M 0 0 ; 1", False)])
def test_path_data_is_numbers_and_commands_only(d, ok):
    assert (problems(svg(f'<path d="{d}"/>')) == []) is ok


def test_a_transform_is_a_list_of_numeric_functions():
    assert problems(svg('<g transform="translate(5 5) rotate(10)"><rect width="1" height="1"/></g>')) == []
    has(svg('<g transform="url(#x)"/>'), "transform")
    has(svg('<g transform="translate(a)"/>'), "transform")


def test_points_are_even_in_number():
    has(svg('<polygon points="1,2 3"/>'), "points")


def test_opacity_is_from_zero_to_one():
    has(svg('<rect opacity="2" width="1" height="1"/>'), "opacity")


def test_a_font_is_a_generic_family():
    assert problems(svg('<text font-family="sans-serif">1</text>')) == []
    has(svg('<text font-family="Comic Sans">1</text>'), "font-family")


def test_a_class_can_only_be_inert():
    has(svg('<g class="key"/>'), "`inert`")


def test_ids_are_plain_and_unique():
    has(svg('<g id="a"/><g id="a"/>'), "used twice")
    has(svg('<g id="a b"/>'), "identifier")


def test_an_attribute_has_a_length_limit():
    has(svg(f'<path d="{"M0 0 " * 1000}"/>'), "characters")


# --- text, size and depth ----------------------------------------------------------------------

def test_a_legend_is_short_so_a_wordmark_cannot_be_drawn_as_one():
    assert problems(svg("<text>" + "x" * MAX_TEXT + "</text>")) == []
    has(svg("<text>" + "x" * (MAX_TEXT + 1) + "</text>"), "wordmark")


def test_text_is_not_allowed_outside_text_title_and_desc():
    has(svg("<g>stray</g>"), "holds text")
    has(svg("<g/>tail"), "outside any <text>")


def test_an_empty_or_nested_legend_is_refused():
    has(svg("<text> </text>"), "empty")
    has(svg("<text><g/></text>"), "holds elements")


def test_invisible_characters_in_a_legend_are_refused():
    has(svg("<text>a‮b</text>"), "invisible")


def test_the_size_and_element_limits():
    has(svg("<!--" + "x" * MAX_BYTES + "-->"), "limit")
    many = "<g/>" * (MAX_ELEMENTS + 1)
    has(svg(many), "elements; the limit")


def test_nesting_has_a_depth_limit():
    deep = "<g>" * 12 + "</g>" * 12
    has(svg(deep), "deep")


# --- keys --------------------------------------------------------------------------------------

def test_a_key_group_must_name_a_key_of_the_remote():
    has(svg('<g data-key="KEY_GHOST"><rect width="1" height="1"/></g>'), "does not name a key")


def test_the_key_check_is_skipped_when_there_is_no_remote():
    assert problems(svg('<g data-key="KEY_GHOST"><rect width="1" height="1"/></g>'), keys=None) == []


def test_a_key_is_drawn_once():
    one = '<g data-key="KEY_UP"><rect width="1" height="1"/></g>'
    has(svg(one + one), "drawn twice")


@pytest.mark.parametrize("name", ["1KEY", "KEY-A", "KEY.A", "KEY A", ""])
def test_a_key_name_is_a_css_identifier(name):
    has(svg(f'<g data-key="{name}"><rect width="1" height="1"/></g>'), "not a key name", keys=None)


def test_only_a_group_can_be_a_key():
    has(svg('<rect data-key="KEY_UP" width="1" height="1"/>'), "only a <g>")


def test_keys_do_not_nest_and_do_not_hide_in_defs():
    inner = '<g data-key="KEY_OK"><rect width="1" height="1"/></g>'
    has(svg(f'<g data-key="KEY_UP"><rect width="1" height="1"/>{inner}</g>'), "inside another key")
    has(svg(f"<defs>{inner}</defs>"), "holds")


def test_a_key_with_nothing_to_tap_is_refused():
    """SVG hit-testing finds a shape by its fill: `fill="none"` and a bare `line` have no inside."""
    has(svg('<g data-key="KEY_UP"><rect width="9" height="9" fill="none" stroke="#000"/></g>'), "hits nothing")
    has(svg('<g data-key="KEY_UP"><line x1="0" y1="0" x2="5" y2="5" stroke="#000"/></g>'), "hits nothing")
    has(svg('<g data-key="KEY_UP"><text>a</text></g>'), "hits nothing")


def test_a_fill_is_inherited_from_the_group():
    assert problems(svg('<g data-key="KEY_UP" fill="#900"><rect width="9" height="9"/></g>')) == []
    has(svg('<g data-key="KEY_UP" fill="none"><rect width="9" height="9"/></g>'), "hits nothing")


# --- inert buttons are grey (D358) -------------------------------------------------------------

@pytest.mark.parametrize("fill", ["#888", "#8a8a8a", "#999999", "gray", "silver", "rgb(120,125,130)", "none"])
def test_an_inert_button_may_be_grey(fill):
    assert problems(svg(f'<g class="inert"><rect width="9" height="9" fill="{fill}"/></g>')) == []


@pytest.mark.parametrize("fill", ["#c00", "#2e8b57", "red", "rgb(200,0,0)", "url(#g)"])
def test_an_inert_button_may_not_be_coloured(fill):
    text = svg(f'<defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs>'
               f'<g class="inert"><rect width="9" height="9" fill="{fill}"/></g>')
    has(text, "draw inert buttons grey")


def test_an_inert_group_carries_no_key():
    has(svg('<g class="inert" data-key="KEY_UP"><rect width="1" height="1" fill="#888"/></g>'), "not inert")


def test_the_inert_fill_is_read_through_the_group():
    has(svg('<g class="inert" fill="red"><rect width="9" height="9"/></g>'), "grey")


# --- where a layout finds its picture ----------------------------------------------------------

@pytest.mark.parametrize("ref", ["a.svg", "dir/a.svg", "a b (1).svg", "RM-W100.original.svg", "x/y/z.svg"])
def test_a_plain_relative_reference_is_accepted(ref):
    assert ref_problem(ref) is None


@pytest.mark.parametrize("ref", ["", "/etc/x.svg", "../a.svg", "a/../b.svg", "./a.svg", "a\\b.svg", "~/a.svg",
                                 "a.png", "a.svg.exe", "a/", "http://x/a.svg", "a:b.svg", "a\nb.svg"])
def test_a_reference_that_leaves_the_folder_or_is_not_svg_is_refused(ref):
    assert ref_problem(ref)


def test_read_art_reports_a_missing_file_and_reads_a_present_one(tmp_path):
    (tmp_path / "a.svg").write_text(GOOD, encoding="utf-8")
    assert read_art(tmp_path, "a.svg") == (GOOD, None)
    text, why = read_art(tmp_path, "b.svg")
    assert text is None and "does not exist" in why


def test_a_symlink_out_of_the_folder_is_refused(tmp_path):
    outside = tmp_path / "outside.svg"
    outside.write_text(GOOD, encoding="utf-8")
    inner = tmp_path / "in"
    inner.mkdir()
    (inner / "a.svg").symlink_to(outside)
    text, why = read_art(inner, "a.svg")
    assert text is None and "stay inside" in why


def test_a_picture_that_is_not_utf8_is_refused(tmp_path):
    (tmp_path / "a.svg").write_bytes(b"<svg \xff\xfe/>")
    text, why = read_art(tmp_path, "a.svg")
    assert text is None and "UTF-8" in why


# --- end to end: a remote file, its schema and its layouts -------------------------------------

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def remote_with(tmp_path):
    def build(layouts, art=None, name="a.svg"):
        doc = json.loads((ROOT / "remotes" / "topping" / "RC-15A.json").read_text())
        template = doc["keys"]["KEY_POWER"]["forms"][0]
        for i, key in enumerate(sorted(KEYS - {"KEY_POWER"})):
            doc["keys"][key] = {"forms": [dict(template, id=f"{key}.irp", function=f"0x{0x20 + i:02X}")]}
        if layouts is not None:
            doc["layouts"] = layouts
        path = tmp_path / "r.json"
        path.write_text(json.dumps(doc, indent=2))
        if art is not None:
            (tmp_path / name).write_text(art, encoding="utf-8")
        return path
    return build


SOURCE = "Photo of the remote, https://commons.wikimedia.org/wiki/File:Example.jpg"


def errors(path):
    return [str(p) for p in validate_file(path)]


def test_a_remote_with_no_layouts_is_unchanged(remote_with):
    assert errors(remote_with(None)) == []


def test_a_layout_with_a_picture_and_no_grid_validates(remote_with):
    path = remote_with({"original": {"original": True, "source": SOURCE, "svg": "a.svg"}}, GOOD)
    assert errors(path) == []


def test_a_layout_may_have_a_grid_and_a_picture(remote_with):
    path = remote_with({"original": {"source": SOURCE, "svg": "a.svg", "areas": ["KEY_UP KEY_OK"]}}, GOOD)
    assert errors(path) == []


def test_a_layout_with_neither_is_refused_by_the_schema(remote_with):
    assert errors(remote_with({"original": {"source": SOURCE}}))


def test_a_grid_layout_needs_no_picture_as_before(remote_with):
    assert errors(remote_with({"original": {"areas": ["KEY_UP KEY_OK"]}})) == []


def test_a_named_picture_that_is_missing_fails_where_an_absent_one_would_not(remote_with):
    path = remote_with({"original": {"source": SOURCE, "svg": "gone.svg"}})
    assert any("does not exist" in e for e in errors(path))


def test_a_picture_needs_a_source(remote_with):
    path = remote_with({"original": {"svg": "a.svg"}}, GOOD)
    assert any("needs a `source`" in e for e in errors(path))


def test_a_bad_picture_is_reported_through_the_layout(remote_with):
    path = remote_with({"original": {"source": SOURCE, "svg": "a.svg"}}, svg("<script/>"))
    found = errors(path)
    assert any("is not allowed" in e and "a.svg" in e for e in found), found


def test_a_picture_key_that_the_remote_lacks_is_reported(remote_with):
    art = svg('<g data-key="KEY_GHOST"><rect width="1" height="1"/></g>')
    path = remote_with({"original": {"source": SOURCE, "svg": "a.svg"}}, art)
    assert any("KEY_GHOST" in e for e in errors(path))


def test_the_sibling_maps_still_may_not_orphan_beside_a_picture(remote_with):
    path = remote_with({"original": {"source": SOURCE, "svg": "a.svg", "printedLabels": {"KEY_OPTIN": "x"}}}, GOOD)
    assert any("KEY_OPTIN" in e for e in errors(path))


def test_a_reference_that_climbs_out_is_refused(remote_with):
    path = remote_with({"original": {"source": SOURCE, "svg": "../a.svg"}})
    assert any("leaves the folder" in e for e in errors(path))


# --- the command -------------------------------------------------------------------------------

def test_rl_art_check_reports_and_sets_the_exit_code(remote_with, tmp_path, capsys):
    remote = remote_with(None)
    good = tmp_path / "good.svg"
    good.write_text(GOOD, encoding="utf-8")
    assert cli.main(["art", "check", str(remote), str(good)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    total = len(json.loads(remote.read_text())["keys"])
    assert f"2 of {total} keys drawn" in out and "1 inert" in out and "not drawn:" in out and "ch1" in out
    bad = tmp_path / "bad.svg"
    bad.write_text(svg("<script/>"), encoding="utf-8")
    assert cli.main(["art", "check", str(remote), str(bad)]) == cli.EXIT_ERROR
    assert "is not allowed" in capsys.readouterr().err


def test_a_layout_with_a_picture_keeps_the_documented_field_order(remote_with):
    from remote_ledger.serialize import order_keys
    path = remote_with({"o": {"shape": {"KEY_UP": "rect"}, "svg": "a.svg", "source": SOURCE, "label": "L",
                              "areas": ["KEY_UP"], "original": True, "printedLabels": {"KEY_UP": "U"}}}, GOOD)
    layout = json.loads(path.read_text())["layouts"]["o"]
    assert list(order_keys("layout", layout)) == ["original", "label", "source", "areas", "svg",
                                                  "printedLabels", "shape"]
