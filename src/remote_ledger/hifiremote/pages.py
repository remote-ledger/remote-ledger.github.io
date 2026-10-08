"""One page of hifi-remote.com's Sony code pages, read into plain data.

The pages are Word's "Save as web page" output: a heading, some prose, then for
each group of device codes a line ``Sony:<codes>`` (the *marker*) and a table of
two columns, ``Command Code`` and ``Command(s)``. A colour on part of a command
cell is the author's annotation, and a legend in the prose says what it means.
Nothing here interprets a colour or a device code; that is
:mod:`.importer`'s, which has the page-specific knowledge.

Parsing uses the standard library's ``html.parser``. A page's text is read as it
is displayed: a hard newline inside a paragraph is a space, a tag boundary is
not, and a non-breaking space is a space.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: The colour Word writes for text with no annotation. These pages set every
#: character's colour, so "no colour" is spelled out.
DEFAULT_COLOUR = "silver"

_COLOUR = re.compile(r"(?:^|[;\s])color\s*:\s*([#A-Za-z0-9]+)", re.I)
_BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "li", "br"}


@dataclass(frozen=True)
class Span:
    """A run of one cell's text in one colour (lower case, as written)."""

    text: str
    colour: str


@dataclass
class Row:
    code_text: str
    spans: list[Span]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class Table:
    #: Text of the paragraphs between the previous table (or the top of the page)
    #: and this one, in order; the markers are among them.
    before: list[str] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)


@dataclass
class Page:
    title: str
    tables: list[Table]
    #: Paragraphs before the first table: the prose and the legend.
    intro: list[str]


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.tables: list[Table] = []
        self.intro: list[str] = []
        self._in_title = False
        self._skip = 0
        self._depth = 0
        self._table: Table | None = None
        self._row: list[list[Span]] | None = None
        self._cell: list[Span] | None = None
        self._colours: list[str] = [DEFAULT_COLOUR]
        self._stack: list[bool] = []
        self._para: list[str] = []
        self._pending: list[str] = []

    # -- paragraphs outside tables
    def _flush_para(self) -> None:
        text = _tidy("".join(self._para))
        self._para = []
        if self._table is None and text:
            self._pending.append(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._flush_para()
                self._table = Table(before=self._pending)
                self._pending = []
        elif self._table is not None and self._depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []
        if tag in _BLOCKS and self._table is None:
            self._flush_para()
        colour = None
        if tag in ("span", "font", "p", "b", "i"):
            m = _COLOUR.search(a.get("style") or "")
            colour = m[1].lower() if m else (a.get("color") or "").lower() or None
        if tag in ("span", "font", "b", "i", "p", "a", "u"):
            self._stack.append(colour is not None)
            if colour is not None:
                self._colours.append(colour)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style"):
            self._skip -= 1
        elif tag in ("span", "font", "b", "i", "p", "a", "u") and self._stack:
            if self._stack.pop():
                self._colours.pop()
        if tag == "table" and self._depth:
            self._depth -= 1
            if self._depth == 0 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
        elif self._table is not None and self._depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(self._cell)
                self._cell = None
            elif tag == "tr" and self._row is not None:
                self._finish_row(self._row)
                self._row = None
        if tag in _BLOCKS and self._table is None:
            self._flush_para()

    def _finish_row(self, cells: list[list[Span]]) -> None:
        table = self._table
        assert table is not None
        if not table.header:
            table.header = [_tidy("".join(s.text for s in c)) for c in cells]
            return
        if len(table.header) != 2:
            return  # a table of another shape (a page of prose); nobody reads its rows
        if len(cells) != 2:
            raise ValueError(f"a row of {len(cells)} cells in a two-column table")
        code = _tidy("".join(s.text for s in cells[0]))
        table.rows.append(Row(code, _merge(cells[1])))

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._skip:
            return
        elif self._cell is not None:
            self._cell.append(Span(data.replace("\n", " "), self._colours[-1]))
        elif self._table is None:
            self._para.append(data.replace("\n", " "))


def _merge(spans: list[Span]) -> list[Span]:
    """Whitespace tidied, empty runs dropped, neighbours of one colour joined."""
    out: list[Span] = []
    for span in spans:
        text = span.text.replace("\xa0", " ")
        if not text.strip() and not out:
            continue
        if out and out[-1].colour == span.colour:
            out[-1] = Span(out[-1].text + text, span.colour)
        else:
            out.append(Span(text, span.colour))
    tidied = [Span(re.sub(r"\s+", " ", s.text), s.colour) for s in out]
    if tidied:
        tidied[0] = Span(tidied[0].text.lstrip(), tidied[0].colour)
        tidied[-1] = Span(tidied[-1].text.rstrip(), tidied[-1].colour)
    return [s for s in tidied if s.text.strip()]


def read_page(html: str) -> Page:
    """One page's title, the prose before its first table, and its tables."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    reader._flush_para()
    intro = reader.tables[0].before if reader.tables else reader._pending
    return Page(_tidy(reader.title), reader.tables, intro)
