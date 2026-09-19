"""Layouts (D14, D29; R8-R11)."""

import json

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.layout import GAP, Grid, layouts_problems, parse
from remote_ledger.validate import validate_file

# SPEC section 6's own example: the RMT-B118P D-pad, transcribed from the
# ASCII diagram in its lircd.conf.
DPAD = [
    ".        KEY_UP    .         .",
    "KEY_LEFT KEY_OK    KEY_RIGHT .",
    "KEY_BACK KEY_DOWN  .         KEY_OPTION",
]
DPAD_KEYS = {"KEY_UP", "KEY_LEFT", "KEY_OK", "KEY_RIGHT",
             "KEY_BACK", "KEY_DOWN", "KEY_OPTION"}


def test_the_spec_dpad_parses():
    grid = parse(DPAD)
    assert (grid.height, grid.width) == (3, 4)
    assert grid.names == DPAD_KEYS


def test_the_spec_dpad_round_trips_to_css():
    """R9's whole design: the array drops into a real CSS rule untouched."""
    css = parse(DPAD).to_css()
    assert css.startswith("grid-template-areas:")
    assert '"KEY_LEFT KEY_OK KEY_RIGHT ."' in css
    # And parsing the emitted rows gives the same grid back.
    rows = [l.strip().strip('";') for l in css.splitlines()[1:]]
    assert parse(rows).rows == parse(DPAD).rows


def test_a_dpad_is_five_ordinary_cells_not_a_plus():
    """The known limit in R9 never bites here: a D-pad reads as a plus but
    is five cells with the corners left empty, each its own rectangle."""
    for key in ("KEY_UP", "KEY_OK", "KEY_LEFT", "KEY_RIGHT", "KEY_DOWN"):
        assert len(parse(DPAD).cells(key)) == 1


def test_a_repeated_name_spans_without_a_rowspan_field():
    """R9: repeating a name across adjacent cells spans it."""
    grid = parse(["KEY_VOL KEY_VOL", "KEY_VOL KEY_VOL"])
    assert len(grid.cells("KEY_VOL")) == 4


def test_gaps_are_not_names():
    assert GAP not in parse(DPAD).names


@pytest.mark.parametrize(
    "areas,match",
    [
        ([], "holds no rows"),
        (["KEY_A KEY_B", "KEY_C"], "same cell count"),
        (["   "], "row 0 is empty"),
    ],
)
def test_structural_errors(areas, match):
    with pytest.raises(ValidationError, match=match):
        parse(areas)


@pytest.mark.parametrize(
    "areas",
    [
        ["KEY_A KEY_B", "KEY_A KEY_A"],          # L-shaped
        ["KEY_A KEY_B KEY_A"],                   # split horizontally
        ["KEY_A .", ". KEY_A"],                  # diagonal
    ],
    ids=["l-shaped", "split", "diagonal"],
)
def test_a_name_must_form_one_rectangle(areas):
    """CSS grid's own constraint, so enforcing it is matching CSS rather
    than inventing a rule."""
    with pytest.raises(ValidationError, match="not a single\\s+rectangle"):
        parse(areas)


@pytest.mark.parametrize("name", ["1KEY", "KEY-A", "KEY.A", "-x", "KEY+A"])
def test_area_names_must_be_css_identifiers(name):
    """D29: R11 leans on CSS grid for collision-checking, which holds only
    while every name is a legal identifier."""
    with pytest.raises(ValidationError, match="legal CSS identifier"):
        parse([f"{name} KEY_B"])


def test_whitespace_cannot_occur_inside_a_name_by_construction():
    """D29 lists whitespace among what the pattern excludes, but that part
    is structurally redundant: splitting happens first, so "KEY A" is two
    cells rather than one ill-formed name. The pattern's real work is
    excluding punctuation -- above all `.`, which means "gap"."""
    assert parse(["KEY_A KEY_B"]).names == {"KEY_A", "KEY_B"}
    assert parse(["KEY_A    KEY_B"]).width == 2


def test_two_keys_cannot_claim_one_cell():
    """R11: the format itself makes this unexpressible -- a cell holds one
    token, so there is nothing for the validator to separately enforce."""
    grid = parse(["KEY_A KEY_B"])
    assert grid.rows == (("KEY_A", "KEY_B"),)


# --- the ledger-specific checks ---------------------------------------------

def _problems(layouts, keys=DPAD_KEYS):
    return list(layouts_problems(layouts, set(keys), "f"))


def test_a_clean_layout_has_no_problems():
    assert _problems({"original": {"original": True, "areas": DPAD}}) == []


def test_an_area_must_name_a_real_key():
    assert any("does not name a key" in p
               for p in _problems({"l": {"areas": ["KEY_GHOST"]}}))


@pytest.mark.parametrize("field", ["printedLabels", "shape"])
def test_sibling_maps_may_not_orphan(field):
    """R10. An orphan here is the quiet kind: a label for KEY_OPTIN renders
    nothing and reports nothing."""
    layout = {"areas": DPAD, field: {"KEY_OPTIN": "OPTIO"}}
    assert any(f"`{field}`" in p for p in _problems({"l": layout}))


def test_at_most_one_layout_is_the_original():
    """R8's "exactly one, if present", made checkable."""
    layouts = {"a": {"original": True, "areas": DPAD},
               "b": {"original": True, "areas": DPAD}}
    assert any("Exactly one layout" in p for p in _problems(layouts))


def test_no_original_at_all_is_fine():
    """R7 already allows a remote whose factory arrangement nobody has
    transcribed."""
    assert _problems({"mine": {"areas": DPAD}}) == []


def test_layouts_are_optional():
    assert _problems(None) == [] and _problems({}) == []


# --- end to end -------------------------------------------------------------

@pytest.fixture
def remote_with_layout(tmp_path):
    def _build(layouts):
        doc = json.loads(
            (
                __import__("pathlib").Path(__file__).resolve().parents[1]
                / "remotes" / "topping" / "RC-15A.json"
            ).read_text()
        )
        template = doc["keys"]["KEY_POWER"]["forms"][0]
        for i, key in enumerate(sorted(DPAD_KEYS)):
            form = dict(template, id=f"{key}.irp", function=f"0x{0x20 + i:02X}")
            doc["keys"][key] = {"forms": [form]}
        doc["layouts"] = layouts
        path = tmp_path / "r.json"
        path.write_text(json.dumps(doc, indent=2))
        return path
    return _build


def test_the_spec_dpad_validates_on_a_real_remote(remote_with_layout):
    path = remote_with_layout({"original": {
        "original": True, "label": "Factory face",
        "source": "ASCII diagram in RMT-B118P.lirc.conf (jose1711/lirc_remotes)",
        "areas": DPAD,
        "printedLabels": {"KEY_BACK": "RETURN", "KEY_OPTION": "OPTIO"},
    }})
    assert [str(p) for p in validate_file(path)] == []


def test_a_typod_printed_label_fails_instead_of_rendering_nothing(remote_with_layout):
    """The Phase 4 acceptance criterion."""
    path = remote_with_layout({"original": {
        "original": True, "areas": DPAD,
        "printedLabels": {"KEY_OPTIN": "OPTIO"},   # typo for KEY_OPTION
    }})
    problems = [str(p) for p in validate_file(path)]
    assert any("KEY_OPTIN" in p for p in problems), problems
