"""A layout's picture: a flat SVG whose key groups are the tappable keys (R29, D357-D359).

A layout may carry a drawing of the remote's face, written as a small SVG. Each key is one
``<g data-key="KEY_NAME">`` group, so the drawing is also the map from a place on the face to a key:
a client that shows the picture asks which group is under the finger and sends that key. Nothing
else says where a key is.

An SVG is a program-shaped document (scripts, links, external files, entity expansion), and a client
shows it inside its own page. So the format is **a profile, not SVG**: a short list of elements and
attributes, a check of every value, and nothing that can load, run or link. A file that passes can be
put into a page or drawn by any renderer without a sanitiser in between, and a file that does not is
refused whole rather than cleaned (a cleaned file would no longer be the file that was reviewed).

The checks are text checks over the parsed tree. They do not render the picture: where a key is drawn
is the author's claim (R8) and the reviewer's to confirm, as for a grid layout.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .layout import KEY_NAME

SVG_NS = "http://www.w3.org/2000/svg"
SUFFIX = ".svg"
#: A picture is a few dozen shapes. The largest of twenty drawn in the pilot was 14 KB.
MAX_BYTES = 40_000
MAX_ELEMENTS = 1_500
MAX_DEPTH = 8
MAX_ATTRIBUTE = 4_000
#: A printed legend: ``1``, ``+``, ``OK``, ``MENU``. A wordmark is a logo, which the profile keeps out.
MAX_TEXT = 8

SHAPES = frozenset({"rect", "circle", "ellipse", "path", "polygon", "polyline"})
ELEMENTS = SHAPES | {"svg", "g", "line", "text", "title", "desc", "defs", "linearGradient",
                     "radialGradient", "stop"}
#: What may be filled and therefore tapped. A ``line`` has no inside.
GRADIENTS = frozenset({"linearGradient", "radialGradient"})

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
NUMBER = re.compile(rf"^{_NUM}$")
LENGTH = re.compile(rf"^{_NUM}(?:px)?$")
FRACTION = re.compile(rf"^{_NUM}%?$")
NUMBERS = re.compile(rf"^{_NUM}(?:[\s,]+{_NUM})*$")
PATH_DATA = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9eE+\-., \t\r\n]+$")
TRANSFORM = re.compile(
    rf"^(?:\s*(?:translate|scale|rotate|skewX|skewY|matrix)\(\s*{_NUM}(?:[\s,]+{_NUM}){{0,5}}\s*\)\s*)+$")
ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
RGB = re.compile(rf"^rgba?\(\s*{_NUM}%?(?:\s*,\s*{_NUM}%?){{2,3}}\s*\)$")
URL = re.compile(r"^url\(#([A-Za-z][A-Za-z0-9_-]{0,63})\)$")
COLOUR_NAMES = frozenset({
    "black", "white", "gray", "grey", "silver", "red", "maroon", "yellow", "olive", "lime", "green",
    "aqua", "teal", "blue", "navy", "fuchsia", "purple", "orange", "transparent"})
GREY_NAMES = frozenset({"black", "white", "gray", "grey", "silver", "transparent"})
VIEWBOX = re.compile(rf"^\s*({_NUM})[\s,]+({_NUM})[\s,]+({_NUM})[\s,]+({_NUM})\s*$")
#: The one processing instruction a file may start with. Anything else that opens ``<?`` is refused.
DECLARATION = re.compile(r'^<\?xml\s+version="1\.[01]"(?:\s+encoding="[Uu][Tt][Ff]-8")?(?:\s+standalone="(?:yes|no)")?\s*\?>')
ASPECT = re.compile(r"^(?:none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max)(?:\s+(?:meet|slice))?)$")
#: How far apart a grey's channels may be (of 255) and still read as grey.
GREY_SPREAD = 24

COLOUR_ATTRS = frozenset({"fill", "stroke", "stop-color"})
ENUMS: dict[str, frozenset[str]] = {
    "stroke-linejoin": frozenset({"miter", "round", "bevel"}),
    "stroke-linecap": frozenset({"butt", "round", "square"}),
    "fill-rule": frozenset({"nonzero", "evenodd"}),
    "text-anchor": frozenset({"start", "middle", "end"}),
    "dominant-baseline": frozenset({"auto", "middle", "central", "hanging", "alphabetic"}),
    "font-weight": frozenset({"normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900"}),
    "font-family": frozenset({"sans-serif", "serif", "monospace"}),
    "gradientUnits": frozenset({"objectBoundingBox", "userSpaceOnUse"}),
    "spreadMethod": frozenset({"pad", "reflect", "repeat"}),
}
UNIT_INTERVAL = frozenset({"opacity", "fill-opacity", "stroke-opacity", "stop-opacity"})
LENGTHS = frozenset({"x", "y", "dx", "dy", "width", "height", "rx", "ry", "cx", "cy", "r", "fx", "fy",
                     "x1", "y1", "x2", "y2", "stroke-width", "font-size", "letter-spacing"})

#: Presentation attributes any element of the profile may carry.
GLOBAL = frozenset({"id", "class", "transform", "fill", "stroke", "stroke-width", "stroke-linejoin",
                    "stroke-linecap", "fill-rule", "opacity", "fill-opacity", "stroke-opacity",
                    "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline",
                    "letter-spacing"})
PER_ELEMENT: dict[str, frozenset[str]] = {
    "svg": frozenset({"viewBox", "width", "height", "preserveAspectRatio", "version"}),
    "g": frozenset({"data-key"}),
    "rect": frozenset({"x", "y", "width", "height", "rx", "ry"}),
    "circle": frozenset({"cx", "cy", "r"}),
    "ellipse": frozenset({"cx", "cy", "rx", "ry"}),
    "line": frozenset({"x1", "y1", "x2", "y2"}),
    "polygon": frozenset({"points"}),
    "polyline": frozenset({"points"}),
    "path": frozenset({"d"}),
    "text": frozenset({"x", "y", "dx", "dy"}),
    "linearGradient": frozenset({"x1", "y1", "x2", "y2", "gradientUnits", "gradientTransform", "spreadMethod"}),
    "radialGradient": frozenset({"cx", "cy", "r", "fx", "fy", "gradientUnits", "gradientTransform", "spreadMethod"}),
    "stop": frozenset({"offset", "stop-color", "stop-opacity"}),
}
#: Elements that take no presentation attributes of their own beyond identity.
IDENTITY_ONLY = frozenset({"title", "desc", "defs", "stop", "linearGradient", "radialGradient"})

#: Refused by name, so the message says why and not only that the element is not on the list.
DANGEROUS = {
    "script": "runs code", "style": "restyles by selector and can load files", "image": "loads a file",
    "use": "includes another element or file", "foreignObject": "embeds HTML", "a": "links away",
    "filter": "loads and computes", "animate": "changes values over time", "set": "changes values over time",
    "animateTransform": "changes values over time", "animateMotion": "changes values over time",
    "mask": "references other elements", "clipPath": "references other elements",
    "pattern": "references other elements", "marker": "references other elements",
    "symbol": "references other elements", "switch": "chooses by browser", "iframe": "embeds a page",
}


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class ArtReport:
    """What a picture holds and what is wrong with it."""

    keys: tuple[str, ...]          # the data-key groups, in document order
    inert: int                     # groups drawn as buttons that send nothing
    size: int                      # bytes
    viewbox: tuple[float, float, float, float] | None
    problems: tuple[str, ...]


def _colour_problem(name: str, value: str, ids: set[str]) -> str | None:
    v = value.strip()
    if v == "none" or v in COLOUR_NAMES or HEX.match(v) or RGB.match(v):
        return None
    m = URL.match(v)
    if m:
        return None if m.group(1) in ids else f"`{name}` refers to #{m.group(1)}, which is not a gradient of this file"
    return (f"`{name}` is {value!r}: a colour is none, a hex colour, rgb(), one of the basic names "
            "or url(#gradient)")


def _attribute_problem(el: str, name: str, value: str, ids: set[str]) -> str | None:
    """The reason one attribute value is outside the profile, or None."""
    if len(value) > MAX_ATTRIBUTE:
        return f"`{name}` is {len(value)} characters; the limit is {MAX_ATTRIBUTE}"
    if name == "id":
        return None if ID.match(value) else f"`id` {value!r} is not a plain identifier"
    if name == "class":
        return None if value == "inert" else f"`class` is {value!r}; the only class is `inert`"
    if name == "data-key":
        return None if KEY_NAME.match(value) else f"`data-key` {value!r} is not a key name (D29)"
    if name in COLOUR_ATTRS:
        return _colour_problem(name, value, ids)
    if name in ENUMS:
        return None if value in ENUMS[name] else f"`{name}` is {value!r}; allowed: {', '.join(sorted(ENUMS[name]))}"
    if name in UNIT_INTERVAL:
        ok = NUMBER.match(value) and 0 <= float(value) <= 1
        return None if ok else f"`{name}` is {value!r}; it is a number from 0 to 1"
    if name in LENGTHS:
        pattern = FRACTION if el in GRADIENTS else LENGTH
        return None if pattern.match(value) else f"`{name}` is {value!r}; it is a plain number"
    if name == "offset":
        return None if FRACTION.match(value) else f"`offset` is {value!r}; it is a number or a percentage"
    if name == "points":
        if not NUMBERS.match(value) or len(re.split(r"[\s,]+", value.strip())) % 2:
            return f"`points` is not a list of x,y pairs of numbers"
        return None
    if name == "d":
        return None if PATH_DATA.match(value) else "`d` holds characters that are not path data"
    if name in ("transform", "gradientTransform"):
        return None if TRANSFORM.match(value) else f"`{name}` is {value!r}; allowed: translate, scale, rotate, skewX, skewY, matrix of numbers"
    if name == "preserveAspectRatio":
        return None if ASPECT.match(value) else f"`preserveAspectRatio` is {value!r}"
    if name == "version":
        return None if value == "1.1" else "`version` is 1.1 or absent"
    if name == "viewBox":
        return None  # checked once, on the root, where its absence is also a problem
    if name in ("width", "height"):
        return None if LENGTH.match(value) and float(value.removesuffix("px")) > 0 else f"`{name}` is {value!r}; it is a positive number"
    return f"`{name}` is not an attribute of the profile"


def _is_grey(value: str) -> bool:
    v = value.strip()
    if v == "none" or v in GREY_NAMES:
        return True
    if HEX.match(v):
        h = v[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        channels = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
        return max(channels) - min(channels) <= GREY_SPREAD
    m = RGB.match(v)
    if m:
        parts = [float(p.strip().rstrip("%")) for p in v[v.index("(") + 1:-1].split(",")[:3]]
        scale = 2.55 if v.count("%") else 1
        parts = [p * scale for p in parts]
        return max(parts) - min(parts) <= GREY_SPREAD
    return False


def inspect_art(text: str, keys: set[str] | None = None, *, where: str = "svg") -> ArtReport:
    """Parse a picture and list everything the profile says about it (D358).

    ``keys`` is the remote's key names; None skips the check that every ``data-key`` names one (a
    caller that has no remote to hand, such as a unit test of the profile alone).
    """
    problems: list[str] = []
    raw = text.encode("utf-8")
    size = len(raw)
    if size > MAX_BYTES:
        problems.append(f"{where}: the file is {size} bytes; the limit is {MAX_BYTES}")
    body = DECLARATION.sub("", text, count=1)
    low = body.lower()
    for marker, why in (("<!doctype", "a DOCTYPE can declare entities"), ("<!entity", "an entity can expand to gigabytes"),
                        ("<![cdata[", "CDATA is not needed for a legend"), ("<?", "a processing instruction")):
        if marker in low:
            problems.append(f"{where}: contains {marker}...: {why}")
    if problems:
        return ArtReport((), 0, size, None, tuple(problems))
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return ArtReport((), 0, size, None, (f"{where}: not well-formed XML: {exc}",))

    if root.tag != f"{{{SVG_NS}}}svg":
        return ArtReport((), 0, size, None, (f"{where}: the root is not <svg xmlns=\"{SVG_NS}\">",))

    # Gradients are the only thing a colour can point at; collect their ids first.
    seen_ids: set[str] = set()
    gradient_ids: set[str] = set()
    count = 0
    for el in root.iter():
        count += 1
        if "id" in el.attrib:
            if el.attrib["id"] in seen_ids:
                problems.append(f"{where}: the id {el.attrib['id']!r} is used twice")
            seen_ids.add(el.attrib["id"])
            if _local(el.tag) in GRADIENTS:
                gradient_ids.add(el.attrib["id"])
    ids = gradient_ids
    if count > MAX_ELEMENTS:
        problems.append(f"{where}: {count} elements; the limit is {MAX_ELEMENTS}")

    viewbox = None
    m = VIEWBOX.match(root.attrib.get("viewBox", ""))
    if not m:
        problems.append(f"{where}: <svg> needs a viewBox of four numbers: the picture's own coordinate system")
    else:
        viewbox = tuple(float(g) for g in m.groups())  # type: ignore[assignment]
        if viewbox[2] <= 0 or viewbox[3] <= 0:
            problems.append(f"{where}: the viewBox has no area")

    keys_seen: list[str] = []
    inert = 0

    def visit(el: ET.Element, depth: int, *, fill: str, in_defs: bool, in_key: bool, in_inert: bool) -> None:
        nonlocal inert
        name = _local(el.tag)
        at = f"{where}: <{name}>"
        if depth > MAX_DEPTH:
            problems.append(f"{at} is nested {depth} deep; the limit is {MAX_DEPTH}")
            return
        if not el.tag.startswith(f"{{{SVG_NS}}}"):
            problems.append(f"{at} is not in the SVG namespace")
            return
        if name in DANGEROUS:
            problems.append(f"{at} is not allowed: it {DANGEROUS[name]}")
            return
        if name not in ELEMENTS:
            problems.append(f"{at} is not an element of the profile")
            return
        allowed = (frozenset({"id"}) if name in IDENTITY_ONLY else GLOBAL) | PER_ELEMENT.get(name, frozenset())
        if name in IDENTITY_ONLY:
            allowed |= PER_ELEMENT.get(name, frozenset())
        for attr, value in el.attrib.items():
            if attr.startswith("{") or attr.lower().startswith("on") or attr not in allowed:
                what = "an event handler" if attr.lower().startswith("on") else (
                    "a namespaced attribute" if attr.startswith("{") else "not an attribute of this element in the profile")
                problems.append(f"{at} has `{_local(attr)}`, {what}")
                continue
            reason = _attribute_problem(name, attr, value, ids)
            if reason:
                problems.append(f"{at}: {reason}")

        here_fill = el.attrib.get("fill", fill)
        key = el.attrib.get("data-key")
        inert_here = el.attrib.get("class") == "inert"
        if key is not None:
            if name != "g":
                problems.append(f"{at} has `data-key`; only a <g> is a key")
            elif in_defs:
                problems.append(f"{at} key {key!r} is inside <defs>, where nothing is drawn")
            elif in_key:
                problems.append(f"{at} key {key!r} is inside another key's group; a key group holds shapes only")
            elif inert_here:
                problems.append(f"{at} key {key!r} is `inert`: a button that sends something is not inert")
            else:
                if key in keys_seen:
                    problems.append(f"{where}: the key {key!r} is drawn twice")
                keys_seen.append(key)
                if keys is not None and key not in keys:
                    problems.append(f"{where}: `data-key` {key!r} does not name a key in `keys` (R29)")
        if inert_here:
            if name != "g":
                problems.append(f"{at} is `inert`; only a <g> can be")
            elif in_key or in_inert:
                problems.append(f"{at} `inert` group is inside another key or inert group")
            else:
                inert += 1
        if name == "defs":
            for child in el:
                if _local(child.tag) not in GRADIENTS:
                    problems.append(f"{where}: <defs> holds <{_local(child.tag)}>; it holds gradients only")
        if name in ("text",):
            body_text = "".join(el.itertext())
            if not body_text.strip():
                problems.append(f"{at} is empty")
            elif len(body_text) > MAX_TEXT:
                problems.append(f"{at} holds {len(body_text)} characters; a printed legend is at most {MAX_TEXT} (a longer one is a wordmark)")
            if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co") for c in body_text if c not in " "):
                problems.append(f"{at} holds a control or invisible character")
            if len(el):
                problems.append(f"{at} holds elements; legends are plain text")
        elif name in ("title", "desc"):
            if len("".join(el.itertext())) > 120:
                problems.append(f"{at} is longer than 120 characters")
        elif el.text and el.text.strip() and name not in ("title", "desc"):
            problems.append(f"{at} holds text; only <text>, <title> and <desc> do")
        for child in el:
            if child.tail and child.tail.strip():
                problems.append(f"{at} holds text outside any <text>")
        # a key's shapes must be paintable, or a tap on the picture lands on nothing
        for child in el:
            visit(child, depth + 1, fill=here_fill, in_defs=in_defs or name == "defs",
                  in_key=in_key or (key is not None and name == "g"),
                  in_inert=in_inert or inert_here)
        if inert_here and name == "g":
            for shape in _shapes(el, here_fill):
                if not _is_grey(shape[1]):
                    problems.append(f"{where}: an inert button is filled {shape[1]!r}; draw inert buttons grey (D358)")
                    break
        if key is not None and name == "g" and not in_key and not in_defs and not inert_here:
            if not any(f != "none" for _, f in _shapes(el, here_fill)):
                problems.append(f"{where}: key {key!r} has no shape with a fill, so a tap on it hits nothing (a `line`, or `fill=\"none\"`, cannot be tapped)")

    def _shapes(group: ET.Element, inherited: str):
        out = []

        def walk(el: ET.Element, fill: str) -> None:
            f = el.attrib.get("fill", fill)
            if _local(el.tag) in SHAPES:
                out.append((el, f))
            for child in el:
                walk(child, f)
        for child in group:
            walk(child, inherited)
        return out

    visit(root, 0, fill="black", in_defs=False, in_key=False, in_inert=False)
    return ArtReport(tuple(keys_seen), inert, size, viewbox, tuple(sorted(set(problems))))


def art_problems(text: str, keys: set[str] | None = None, *, where: str = "svg"):
    """Every problem with one picture, as strings (the shape layout.py's checks yield)."""
    yield from inspect_art(text, keys, where=where).problems


# -- where a picture is ---------------------------------------------------------------------------

REF = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._ +()\[\]-]*/)*[A-Za-z0-9][A-Za-z0-9._ +()\[\]-]*\.svg$")


def ref_problem(ref: str) -> str | None:
    """Why a layout's ``svg`` reference is not acceptable, or None (D359)."""
    if not isinstance(ref, str) or not ref:
        return "`svg` is empty"
    if ref.startswith("/") or "\\" in ref or ref.startswith("~"):
        return f"`svg` {ref!r} is not a relative path with `/` separators"
    if any(part in ("..", ".") for part in ref.split("/")):
        return f"`svg` {ref!r} leaves the folder of the file that names it"
    if not REF.match(ref):
        return f"`svg` {ref!r} is not a plain path ending in {SUFFIX}"
    return None


def read_art(base: Path, ref: str) -> tuple[str | None, str | None]:
    """``(text, None)`` for the file a reference names beside ``base`` (a directory), else ``(None, why)``."""
    bad = ref_problem(ref)
    if bad:
        return None, bad
    path = (base / ref)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(base.resolve())
    except FileNotFoundError:
        return None, f"`svg` names {ref!r}, which does not exist beside this file"
    except (ValueError, OSError):
        return None, f"`svg` {ref!r} does not stay inside the folder of the file that names it"
    if not resolved.is_file():
        return None, f"`svg` {ref!r} is not a file"
    try:
        return resolved.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"`svg` {ref!r} cannot be read as UTF-8 text: {exc}"
