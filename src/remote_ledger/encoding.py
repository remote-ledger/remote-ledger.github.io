"""Per-context output encoding for the site (D29).

``html.escape`` is correct for exactly one of the four places generated text
lands. The site interpolates author-supplied strings -- ``source``
citations, labels, printed labels -- and ``source`` is the one that matters
most, because the site turns it into a clickable link.

R18 explicitly permits a citation that is *not* a URL ("re-derived from
`<file>`'s protocol, see commit `<sha>`"), so this path must assume the
string is not a safe URL and prove otherwise.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlparse

#: The only schemes that may become an href. Everything else -- `javascript:`,
#: `data:`, a scheme-relative `//host` -- renders as plain text.
SAFE_SCHEMES = frozenset({"http", "https", "mailto"})
#: D29: constrained at the source rather than escaped at render time, so a
#: key name can never be mistaken for grid syntax.
CSS_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def text(value: Any) -> str:
    """HTML text and attribute values."""
    return html.escape(str(value), quote=True)


def is_safe_url(value: str) -> bool:
    """True only for an absolute http(s)/mailto URL.

    A scheme-relative ``//evil.example`` parses with an empty scheme and a
    netloc, which a browser resolves against the page's own scheme -- so
    requiring a scheme from the allowlist is not the same as rejecting
    ``javascript:`` by name, and is why this is an allowlist.
    """
    candidate = value.strip()
    if candidate.startswith("//"):
        return False
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme.lower() not in SAFE_SCHEMES:
        return False
    return bool(parsed.netloc or (parsed.scheme.lower() == "mailto" and parsed.path))


def citation(value: str) -> str:
    """Render a citation: a link when it is safely one, plain text otherwise.

    A bare domain like ``audiosciencereview.com/...`` has no scheme, so it
    renders as text. That is the conservative outcome and the right one --
    guessing ``https://`` onto author-supplied text would be inventing a
    claim the citation did not make.
    """
    escaped = text(value)
    if is_safe_url(value):
        return f'<a href="{escaped}" rel="noopener noreferrer">{escaped}</a>'
    return escaped


def css_ident(value: str) -> str:
    """A key name destined for `grid-area`. Constrained, not escaped."""
    if not CSS_IDENT.match(value):
        raise ValueError(
            f"{value!r} is not a legal CSS identifier, so it cannot be "
            "interpolated into a grid rule (D29)"
        )
    return value


def json_payload(data: Any) -> str:
    """Data for a script block: emitted as JSON, never concatenated into JS.

    ``</`` is escaped so nothing in the payload can close the script tag
    early -- the one way a JSON island can still become script injection.
    """
    return json.dumps(data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
