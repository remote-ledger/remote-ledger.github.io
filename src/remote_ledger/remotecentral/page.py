"""The pages of RemoteCentral's Infrared Hex Code Database, read as text (DESIGN section 30).

The database is three levels of one template: the index lists the brands, a brand page lists its models,
and a model page lists, under each *remote model* the codes were learned from, a row per function with
its Pronto hex. Brand and model pages are paginated. Nothing here reads the network; the fetcher
(``tools/fetch_remotecentral.py``) and the tests hand it the HTML.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

BASE = "https://www.remotecentral.com"
ROOT = "/cgi-bin/codes/"

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_BANNER = re.compile(r"contains\s+([\d,]+)\s+brands\s+with\s+([\d,]+)\s+models")
_BRAND_LINK = re.compile(r'<a\s+href="' + re.escape(ROOT) + r'([^/"?#]+)/"[^>]*>(.*?)</a>', re.S)
_PAGE_LINK = re.compile(r'href="' + re.escape(ROOT) + r'[^"]*?/page-(\d+)/"')
_TOTAL = re.compile(r'total of <span class="bluetext">([\d,]+)</span>')
_TITLE = re.compile(r'class="filetoptitle">(.*?)</div>', re.S)
_ITEM = re.compile(
    r"<b>Remote Model:</b>\s*(?P<remote>.*?)</td>"
    r'|<td width="38%" class="filematchleft"><b>(?P<label>.*?)</b>.*?<span id="HexCode\d+">(?P<hex>.*?)</span>',
    re.S)


def text(fragment: str) -> str:
    """A fragment of HTML as one line of text."""
    return _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", fragment))).strip()


def banner(index: str) -> tuple[int, int] | None:
    """The brands and models the index says the database holds."""
    found = _BANNER.search(text(index))
    return (int(found[1].replace(",", "")), int(found[2].replace(",", ""))) if found else None


def brands(index: str) -> list[tuple[str, str]]:
    """``[(slug, name)]`` of every brand the index links, in page order, once each."""
    seen: dict[str, str] = {}
    for slug, label in _BRAND_LINK.findall(index):
        if slug.startswith("page-"):
            continue
        seen.setdefault(slug, text(label))
    return list(seen.items())


def last_page(page: str) -> int:
    """The number of the last page of a paginated listing (1 when it has no page bar)."""
    return max([int(n) for n in _PAGE_LINK.findall(page)] + [1])


def models(brand_page: str, brand: str) -> list[tuple[str, str]]:
    """``[(slug, name)]`` of the models one page of a brand's listing links."""
    pattern = re.compile(r'<a\s+href="' + re.escape(ROOT + brand) + r'/([^/"?#]+)/"[^>]*>(.*?)</a>', re.S)
    seen: dict[str, str] = {}
    for slug, label in pattern.findall(brand_page):
        if slug.startswith("page-"):
            continue
        seen.setdefault(slug, text(label))
    return list(seen.items())


def total(page: str) -> int | None:
    """The count a listing page announces ("a total of N models" or "N IR codes")."""
    found = _TOTAL.search(page)
    return int(found[1].replace(",", "")) if found else None


@dataclass
class Group:
    """The codes of one remote model, as the page lists them."""
    remote: str
    rows: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ModelPage:
    title: str
    announced: int | None
    groups: list[Group]

    @property
    def codes(self) -> int:
        return sum(len(g.rows) for g in self.groups)


def model_page(page: str) -> ModelPage:
    """One page of a model: its title, the count it announces and its groups of rows. A row is
    ``(function name, the hex as the page gives it, whitespace folded)``."""
    title = _TITLE.search(page)
    name = text(title[1]) if title else ""
    if name.endswith(" Infrared Codes"):
        name = name[: -len(" Infrared Codes")]
    groups: list[Group] = []
    for item in _ITEM.finditer(page):
        if item["remote"] is not None:
            groups.append(Group(text(item["remote"])))
            continue
        if not groups:
            groups.append(Group(""))
        groups[-1].rows.append((text(item["label"]), _SPACE.sub(" ", html.unescape(_TAGS.sub("", item["hex"]))).strip()))
    return ModelPage(name, total(page), groups)
