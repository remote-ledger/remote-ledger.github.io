"""Per-context output encoding (D29).

`html.escape` is correct for exactly one of the four contexts generated text
lands in. These cover the other three.
"""

import pytest

from remote_ledger.encoding import citation, css_ident, is_safe_url, json_payload, text


@pytest.mark.parametrize("value", ["https://a.example/x", "http://a.example",
                                   "mailto:x@example.com"])
def test_allowed_schemes_become_links(value):
    assert is_safe_url(value)
    assert citation(value).startswith("<a href=")


@pytest.mark.parametrize("value", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "//evil.example/x",          # scheme-relative: a browser adds the page's
    "file:///etc/passwd",
    "",
    "   ",
])
def test_everything_else_renders_as_text(value):
    """An allowlist, not a denylist. A `source` is author-supplied free text
    and R18 explicitly permits a non-URL citation, so this path assumes the
    string is unsafe and proves otherwise."""
    assert not is_safe_url(value)
    assert "<a href=" not in citation(value)


def test_a_hostile_citation_is_escaped_as_text():
    rendered = citation('javascript:alert(1)"><img src=x onerror=alert(1)>')
    assert "<img" not in rendered and "&lt;img" in rendered


def test_a_bare_domain_is_not_promoted_to_a_link():
    """Guessing `https://` onto author text would invent a claim the
    citation did not make."""
    assert not is_safe_url("audiosciencereview.com/threads/x.10708")


def test_a_same_repo_cross_reference_renders_as_text():
    """R18 admits this shape explicitly."""
    assert citation("re-derived from <f>, see commit abc") == \
        "re-derived from &lt;f&gt;, see commit abc"


def test_text_escapes_quotes_for_attribute_context():
    assert text('a"b<c') == "a&quot;b&lt;c"


def test_json_payload_cannot_close_the_script_tag():
    """The one way a JSON island still becomes script injection."""
    out = json_payload({"x": "</script><script>alert(1)</script>"})
    assert "</script>" not in out
    assert "<\\/script>" in out


def test_json_payload_is_deterministic():
    assert json_payload({"b": 1, "a": 2}) == json_payload({"a": 2, "b": 1})


@pytest.mark.parametrize("name", ["KEY_POWER", "_x", "A1"])
def test_css_ident_accepts_legal_identifiers(name):
    assert css_ident(name) == name


@pytest.mark.parametrize("name", ["1KEY", "KEY-A", "KEY.A", "a b", "", "}x{"])
def test_css_ident_rejects_anything_that_could_break_a_grid_rule(name):
    """Constrained at the source rather than escaped at render time."""
    with pytest.raises(ValueError, match="legal CSS identifier"):
        css_ident(name)
