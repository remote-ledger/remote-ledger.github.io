"""The importer's parsers of its own formats (D51, D56b): the one place each is
read, and the proof that they are the inverses of what the importer writes.

``parse_citation`` is what the app API (D74) takes every key's database id,
hexcode and protocol from. It is tested here on the labels that could move a
split, and then over **every key of the committed tree**: the label it returns
is the key's own ``label`` field, the id is the file's, the citation is exactly
what ``format_citation`` writes for its parts, and the hexcode and protocol are
the ones the form's ``(protocol, device, subdevice, function)`` read back from
through ``FROM_DB_HEX`` -- which also proves each citation says what its form is.
"""

from __future__ import annotations

import pytest

from remote_ledger.irblaster import importer as imp
from remote_ledger.irblaster.importer import (
    citation_commit, format_citation, parse_citation, split_controls_entry,
)
from app_corpus import COMMIT
from real_irblaster import REAL, ROOT, read_real_tree

needs_data = pytest.mark.skipif(
    not REAL.is_dir(), reason="the imported database is not in this tree")


@pytest.mark.parametrize("label", [
    "POWER", "VOL+", "??", "A B", "1 \t\t\t", "O'K", "'", "''", "it's 'quoted'", "x' ABCD NEC",
    "' 20DF10EF NEC: 32 wire bits, bytes bit-reversed as NEC1", "a: b as c", "Ünï ► ⏩", " lead",
    "tab\there", "line\nbreak", "remote 9, 'fake' 00 NEC: x as NEC1",
])
def test_parse_citation_is_the_inverse_of_format_citation(label):
    source = format_citation("6aafd15", 4711, label, "20DF10EF", "NEC", "NEC1")
    assert parse_citation(source) == (4711, label, "20DF10EF", "NEC")
    assert citation_commit(source) == "6aafd15"


@pytest.mark.parametrize("text", [
    "", "irblaster-db@6aafd15", "lirc-remotes@6aafd15 remotes/x:1 [block y]",
    "irblaster-db@6aafd1 remote 1, 'A' 20DF10EF NEC: x as NEC1",           # short sha
    "irblaster-db@6aafd15 remote x, 'A' 20DF10EF NEC: x as NEC1",           # id
    "irblaster-db@6aafd15 remote 1, A 20DF10EF NEC: x as NEC1",             # no quotes
    "irblaster-db@6aafd15 remote 1, 'A' 20DF10EF NEC: x as NEC1 trailing",
    "xirblaster-db@6aafd15 remote 1, 'A' 20DF10EF NEC: x as NEC1",
])
def test_parse_citation_refuses_what_is_not_a_citation(text):
    with pytest.raises(ValueError):
        parse_citation(text)


def test_the_label_ends_at_the_last_quote_whatever_it_holds():
    """The tail after the label has no quote in it (``[^']*`` for the phrase makes
    that a rule), so the split cannot be moved by a label that looks like a tail."""
    label = "x' 00FF609F NEC: 32 wire bits, bytes bit-reversed as NEC1' ZZ"
    source = format_citation("6aafd15", 1, label, "20DF10EF", "NEC", "NEC1")
    assert parse_citation(source) == (1, label, "20DF10EF", "NEC")
    # a label that is itself a whole citation of another key
    inner = format_citation("6aafd15", 9, "A", "00FF609F", "NEC", "NEC1")
    nested = format_citation("6aafd15", 1, inner, "20DF10EF", "NEC", "NEC1")
    assert parse_citation(nested) == (1, inner, "20DF10EF", "NEC")


def test_the_first_separator_is_the_one_whatever_the_model_holds():
    """D56b: no pipe in a brand, so the first ` | ` ends it; a model that still
    held one (the importer refuses it today) stays whole."""
    assert split_controls_entry("A | B | C") == ("A", "B | C")
    assert split_controls_entry("A B | C D") == ("A B", "C D")


def test_split_controls_entry_inverts_controls_entry():
    for brand, model in [("ACME", "TV-1"), ("A B", "C"), ("A", "B C"), ("X.", "Y . Z"),
                         ("Ünï", "Ω"), ("", "")]:
        assert split_controls_entry(imp.controls_entry(brand, model)) == (brand, model)
    with pytest.raises(ValueError):
        split_controls_entry("NO SEPARATOR")


def test_report_totals_reads_what_the_report_wrote():
    report = imp.Report(commit=COMMIT)
    report.ids, report.key_rows = 12345, 1234567
    totals = imp.report_totals(report.render())
    assert totals["Remote ids in the database"] == 12345
    assert totals["Key rows in the database"] == 1234567
    assert imp.report_totals("no table here") == {}


@needs_data
def test_report_totals_reads_the_committed_report():
    totals = imp.report_totals((ROOT / "remotes/irblaster/IMPORT.md").read_text(encoding="utf-8"))
    assert totals["Keys skipped"] + totals["Keys imported"] == (
        totals["Key rows in the database"] - totals["Key rows dropped as exact duplicates"])


@needs_data
def test_parse_citation_round_trips_over_every_key_of_the_real_tree():
    """All of them (411,265 on the import of 6aafd15)."""
    facts, failures = read_real_tree()
    assert failures[:5] == []
    assert facts["keys"] > 400_000 and len(facts["rows"]) == facts["keys"]
    assert facts["files"] == len(list(REAL.glob("*/*.json")))
