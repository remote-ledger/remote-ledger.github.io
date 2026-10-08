"""The search key of a text, as the bundle stores and a reader computes it (D90).

One function, small enough to port line by line:

1. Unicode NFKD, so an accented letter is the letter and a mark, and a
   compatibility form (``Ｓ``, ``²``, ``①``) is the plain one.
2. Lower case (the full ``lower()``, not ``casefold()``: Kotlin's
   ``lowercase()`` and Python's ``lower()`` agree on it, and ``ß`` keeps its
   shape in both).
3. Keep only the code points whose general category is a letter (``L*``) or a
   number (``N*``): every space, punctuation mark, symbol and combining mark is
   dropped, and nothing is put in its place.

``UN50NU6900F``, ``un 50 nu-6900 f`` and ``UN50-NU6900/F`` are one key,
``un50nu6900f``. Python's ``str.isalnum`` is exactly "category L* or N*" (checked
over every code point of Unicode 15.0), which is why the filter is a regular
expression and not a loop.

What this does *not* do: no letter is turned into a look-alike digit, no brand
word is removed and no suffix is cut. Those are the matcher's business and are
done on the query, never on what the bundle stores (``matching.py``).
"""

from __future__ import annotations

import re
import unicodedata

_NOT_ALNUM = re.compile(r"[\W_]+")


def search_norm(text: str) -> str:
    """The search key of ``text`` (empty when it holds no letter or digit)."""
    return _NOT_ALNUM.sub("", unicodedata.normalize("NFKD", text).lower())
