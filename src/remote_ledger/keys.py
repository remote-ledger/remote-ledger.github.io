"""The canonical key vocabulary, and the mapping from a source's key to it (R22, D83).

Every source spells a key its own way: ``KEY_VOLUMEUP``, ``VOL+``, ``Vol +``,
``volume_up``. The vocabulary is the one list of meanings they are mapped onto:
for each canonical key a group, a display name, a standard icon, a text glyph,
a colour and whether holding it repeats it. A client that shows a generated
layout, the icon of a key, a macro or a comparison between two remotes reads the
canonical id and never the spelling.

Three pieces, all ledger data under ``vocabulary/`` (``paths.VOCABULARY_DIR``):

* ``keys.json``, the contract: ``{"version", "groups", "keys"}``;
* ``aliases.json``, the spellings that mean each key, and the word rewrites
  (``VOL`` to ``VOLUME``) the folding applies;
* the schemas ``keys.schema.json`` and ``aliases.schema.json``.

:func:`canonical_id` is the mapping, a normalisation pipeline (:func:`fold`)
followed by one lookup. It is **conservative**: a spelling that is not in the
table, or that two keys could claim, is ``None``, which a client shows in its
"More" group under the key's own label. A wrong mapping would put the wrong icon
on a key that sends a different signal, and ``None`` costs nothing but an icon.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping

from . import paths
from .errors import ValidationError
from .serialize import load
from .validate import Problem, schema_problems

#: The groups every vocabulary has (D83): consumers may rely on these ids and
#: may find more.
CORE_GROUPS = (
    "power", "volume", "channel", "navigation", "numbers", "media", "input",
    "color", "menu", "apps", "other",
)

#: The ten digit keys, which the digit rule of :func:`resolve` answers with.
DIGIT_IDS = tuple(f"DIGIT_{n}" for n in range(10))

#: How a source prefixes a name, never part of its meaning: the Linux input
#: names (``KEY_``, ``BTN_``) and, from the SmartIR import (D43), the
#: ``sources`` command group. Removed from the start of a spelling only, and only
#: with its underscore, so the word *key* in a label (``KEY LOCK``) stays.
_NAME_PREFIX = re.compile(r"^(?:(?:KEY|BTN)_)?(?:SOURCES_)?")
#: The dashes that are a hyphen to a reader: U+2010 to U+2015 (hyphen, non-breaking
#: hyphen, figure dash, en dash, em dash, horizontal bar) and U+2212 (minus sign).
_DASHES = re.compile("[" + "".join(map(chr, (*range(0x2010, 0x2016), 0x2212))) + "]")
#: A hyphen between two letters or digits joins them (``A-B``, ``S-VIDEO``); any
#: other hyphen is a sign (``VOL-``, ``-/--``).
_INNER_HYPHEN = re.compile(r"(?<=[^\W_])-(?=[^\W_])")
#: Signs become words, as the IR Blaster import already names them (D49), so
#: that ``VOL+`` and ``KEY_VOL_PLUS`` fold to one spelling.
_SIGNS = str.maketrans({"+": " PLUS ", "-": " MINUS ", "*": " STAR ", "#": " HASH "})
#: What separates words. Symbols and ``!``, ``?``, ``|`` are *not* separators: a
#: label made of ⏩ and | is a spelling like any other, and ``POWER?`` is not
#: ``POWER``. Nor is ``/``: it says a key does one thing *or* another (``TV/AV``,
#: ``P/C``), so it stays in the spelling, and ``P/C`` is not ``PC``. A name has
#: lost its slashes already (LIRC writes ``tv_av``), so a compound is listed in
#: both forms.
_SEPARATORS = re.compile(r"[\s_.\\,:;()\[\]{}\"'`=~]+")
#: ``CHANNEL`` is here for the SmartIR import, whose ``sources`` group lists
#: ``Channel 1`` to ``Channel 9``: the code that tunes channel N, which is the digit.
_DIGIT = re.compile(r"^(?:NUM|NUMBER|NUMERIC|NUMPAD|DIGIT|KP|KEYPAD|CHANNEL)?([0-9])$")
_DIGIT_WORDS = {
    "ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4,
    "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9,
}


@dataclass(frozen=True)
class Group:
    id: str
    order: int
    name: str


@dataclass(frozen=True)
class Key:
    id: str
    group: str
    order: int
    name: str
    icon: str | None
    glyph: str | None
    color: str | None
    repeat: bool


# --- folding ---------------------------------------------------------------------


def _words(text: str, tokens: Mapping[str, str]) -> list[str]:
    """The words of a spelling, in the order the table sees them.

    Upper case (NFKC first, so full-width and compatibility forms are the plain
    ones), the name prefix removed, signs turned into words, split on
    separators, and each word replaced by its ``tokens`` entry if it has one.
    """
    text = unicodedata.normalize("NFKC", text).upper()
    text = _NAME_PREFIX.sub("", text, count=1)
    text = _DASHES.sub("-", text)
    text = _INNER_HYPHEN.sub(" ", text)
    text = text.translate(_SIGNS)
    return [tokens.get(word, word) for word in _SEPARATORS.split(text) if word]


def fold(text: str, tokens: Mapping[str, str] | None = None) -> str:
    """The normal form of a spelling: its words, upper case, one space apart.

    ``fold("Vol +") == fold("KEY_VOL_PLUS") == "VOLUME PLUS"``. With no
    ``tokens`` the shipped table is used.
    """
    return " ".join(_words(text, load_vocabulary().tokens if tokens is None else tokens))


def squash(text: str, tokens: Mapping[str, str] | None = None) -> str:
    """:func:`fold` with the spaces removed, which is what a lookup compares.

    ``VOLUME UP`` (a label), ``VOLUME_UP`` (a name) and ``VOLUMEUP`` (LIRC's
    spelling) are one entry. The price is that two spellings differing only in
    where a space falls cannot mean different keys; the validator refuses a
    table that says they do.
    """
    return "".join(_words(text, load_vocabulary().tokens if tokens is None else tokens))


def digit_of(squashed: str) -> int | None:
    """The digit a squashed spelling names, by rule: ``1``, ``NUM1``, ``KP1``,
    ``KEYPAD1``, ``NUMERIC1``, ``DIGIT1``, ``CHANNEL1``, ``ONE``. Not ``10``,
    ``0/10`` or ``D1``."""
    word = _DIGIT_WORDS.get(squashed)
    if word is not None:
        return word
    match = _DIGIT.match(squashed)
    return int(match.group(1)) if match else None


# --- the vocabulary --------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Vocabulary:
    version: int
    groups: tuple[Group, ...]
    keys: tuple[Key, ...]
    tokens: Mapping[str, str]
    aliases: Mapping[str, tuple[str, ...]]
    #: Squashed spelling -> canonical id: each key's own id and display name, and
    #: every alias. Built by :func:`build_index`, which is also the collision check.
    index: Mapping[str, str] = field(repr=False, default_factory=dict)
    _cache: dict[str, str | None] = field(repr=False, default_factory=dict)

    def key(self, key_id: str) -> Key:
        return next(k for k in self.keys if k.id == key_id)

    def resolve(self, text: str) -> str | None:
        """The canonical id one spelling means, or None."""
        try:
            return self._cache[text]
        except KeyError:
            pass
        squashed = squash(text, self.tokens)
        found: str | None = None
        if squashed:
            digit = digit_of(squashed)
            found = DIGIT_IDS[digit] if digit is not None else self.index.get(squashed)
        self._cache[text] = found
        return found

    def canonical_id(self, key_name: str, label: str | None = None) -> str | None:
        """The canonical id of a key, from its label if it has one, else its name.

        The label decides when there is one, because it says more than the name
        can: an imported key is named by folding its label to ASCII (D49), so ⏩
        is ``KEY_`` and ``-►.◄-`` is ``KEY_MINUS_MINUS``, and reading that name
        would answer for a label that means something else. A name is read only
        for a key with no label (every LIRC and SmartIR key, and an authored key
        that names its meaning and prints nothing).
        """
        if label is not None and label.strip():
            return self.resolve(label)
        return self.resolve(key_name)


def _documents(directory: Path) -> tuple[Any, Any, list[Problem]]:
    """Parse and schema-check the two files; the problems are the schema's."""
    docs: list[Any] = []
    problems: list[Problem] = []
    for name, schema in paths.VOCABULARY_SCHEMAS.items():
        where = f"{paths.VOCABULARY_REPO_DIR}/{name}"
        try:
            doc = load(directory / name)
        except OSError as exc:
            doc = None
            problems.append(Problem(where, f"could not be read: {exc.strerror or exc}"))
        except Exception as exc:  # a parse error, or one of D28's refusals
            doc = None
            problems.append(Problem(where, f"could not be parsed: {exc}"))
        else:
            problems += schema_problems(doc, schema, where)
        docs.append(doc)
    return docs[0], docs[1], problems


def build_index(
    keys: list[dict[str, Any]], tokens: Mapping[str, str], aliases: Mapping[str, list[str]],
) -> tuple[dict[str, str], list[Problem]]:
    """The squashed spelling -> id table, and every way the spellings disagree.

    A key's own id and display name are spellings of it without being listed. A
    spelling is a problem when it folds to nothing, when it is a digit (the rule
    answers those, so an entry could only repeat it or contradict it), when it
    folds like another spelling of the same key (one of them is redundant), and
    when it folds like a spelling of another key (a spelling means one key). The
    first claimant keeps the spelling, so one clash is reported once.
    """
    where = f"{paths.VOCABULARY_REPO_DIR}/{paths.ALIASES_FILE}"
    claims: dict[str, tuple[str, str, bool]] = {}   # squashed -> (id, spelling, listed)
    problems: list[Problem] = []

    def claim(key_id: str, spelling: str, at: str, listed: bool) -> None:
        squashed = squash(spelling, tokens)
        digit = digit_of(squashed) if squashed else None
        if not squashed:
            problems.append(Problem(at, f"{spelling!r} folds to nothing"))
        elif digit is not None:
            problems.append(Problem(
                at, f"{spelling!r} is the digit {digit}, which the digit rule answers "
                    f"(DIGIT_{digit}); an entry could only repeat it or contradict it"))
        elif squashed not in claims:
            claims[squashed] = (key_id, spelling, listed)
        else:
            other, other_spelling, other_listed = claims[squashed]
            if other == key_id and not (listed or other_listed):
                return  # the id and the display name of one key may fold alike
            if other == key_id:
                what = "" if other_listed else " (its own id or display name)"
                problems.append(Problem(
                    at, f"{spelling!r} folds like {other_spelling!r}{what}, which {key_id} "
                        "already has; one of them is redundant"))
            else:
                problems.append(Problem(
                    at, f"{spelling!r} folds to {squashed!r}, which {other} has as "
                        f"{other_spelling!r}; a spelling means one key"))

    known = {k["id"] for k in keys}
    for key in keys:
        if key["id"] not in DIGIT_IDS:  # the digit rule answers those
            at = f"{paths.VOCABULARY_REPO_DIR}/{paths.KEYS_FILE}['keys'][{key['id']!r}]"
            claim(key["id"], key["id"], at, listed=False)
            claim(key["id"], key["name"], at, listed=False)
    for key_id in sorted(aliases):
        at = f"{where}['aliases'][{key_id!r}]"
        if key_id not in known:
            problems.append(Problem(at, f"{key_id!r} is not a canonical key of keys.json"))
            continue
        for spelling in aliases[key_id]:
            claim(key_id, spelling, at, listed=True)
    return {squashed: owner for squashed, (owner, _, _) in claims.items()}, problems


def semantic_problems(keys_doc: Any, aliases_doc: Any) -> Iterator[Problem]:
    """What the schemas cannot say, over documents that already conform to them."""
    where = f"{paths.VOCABULARY_REPO_DIR}/{paths.KEYS_FILE}"

    def duplicates(values: list[Any]) -> list[Any]:
        seen, repeated = set(), []
        for value in values:
            if value in seen and value not in repeated:
                repeated.append(value)
            seen.add(value)
        return repeated

    groups, keys = keys_doc["groups"], keys_doc["keys"]
    for group_id in duplicates([g["id"] for g in groups]):
        yield Problem(f"{where}['groups']", f"group id {group_id!r} appears more than once")
    for order in duplicates([g["order"] for g in groups]):
        yield Problem(f"{where}['groups']", f"group order {order} is used by more than one group")
    group_ids = {g["id"] for g in groups}
    for needed in CORE_GROUPS:
        if needed not in group_ids:
            yield Problem(f"{where}['groups']", f"the group {needed!r} is missing; consumers rely on it")

    for key_id in duplicates([k["id"] for k in keys]):
        yield Problem(f"{where}['keys']", f"key id {key_id!r} appears more than once")
    by_group: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        if key["group"] not in group_ids:
            yield Problem(
                f"{where}['keys'][{key['id']!r}]",
                f"group {key['group']!r} is not a group of this file",
            )
        by_group.setdefault(key["group"], []).append(key)
    for group_id, members in sorted(by_group.items()):
        for order in duplicates([k["order"] for k in members]):
            clash = [k["id"] for k in members if k["order"] == order]
            yield Problem(
                f"{where}['keys']",
                f"{', '.join(clash)} share order {order} in group {group_id!r}; "
                "an order is unique within its group",
            )
    for group_id in sorted(group_ids - set(by_group)):
        yield Problem(f"{where}['groups']", f"group {group_id!r} has no key; a layout would have an empty group")
    present = {k["id"] for k in keys}
    for digit_id in DIGIT_IDS:
        if digit_id not in present:
            yield Problem(f"{where}['keys']", f"{digit_id} is missing; the digit rule answers with it")

    tokens = aliases_doc["tokens"]
    at = f"{paths.VOCABULARY_REPO_DIR}/{paths.ALIASES_FILE}['tokens']"
    for word, target in sorted(tokens.items()):
        if _words(word, {}) != [word]:
            yield Problem(at, f"{word!r} is not a folded word (upper case, no separators or signs)")
        elif _words(target, {}) != [target]:
            yield Problem(at, f"{word!r} maps to {target!r}, which is not a folded word")
        elif word == target:
            yield Problem(at, f"{word!r} maps to itself")
        elif target in tokens:
            yield Problem(at, f"{word!r} maps to {target!r}, which is itself rewritten; "
                              "a word is rewritten once, so point it at the final word")
    _, problems = build_index(keys, tokens, aliases_doc["aliases"])
    yield from problems


def vocabulary_problems(directory: Path | None = None) -> list[Problem]:
    """Every problem of the shipped vocabulary: schema, then the relations.

    The schema first and alone, as for a remote (``validate.validate_file``): the
    relations are only meaningful over a file of the right shape.
    """
    keys_doc, aliases_doc, problems = _documents(directory or paths.VOCABULARY_DIR)
    if problems:
        return problems
    return list(semantic_problems(keys_doc, aliases_doc))


def vocabulary_from(directory: Path) -> Vocabulary:
    """The vocabulary in ``directory``, or a ValidationError listing what is wrong."""
    keys_doc, aliases_doc, problems = _documents(directory)
    if not problems:
        problems = list(semantic_problems(keys_doc, aliases_doc))
    if problems:
        raise ValidationError(
            "the canonical key vocabulary is invalid:\n  " + "\n  ".join(map(str, problems))
        )
    tokens = dict(aliases_doc["tokens"])
    index, _ = build_index(keys_doc["keys"], tokens, aliases_doc["aliases"])
    groups = tuple(sorted((Group(**g) for g in keys_doc["groups"]), key=lambda g: g.order))
    group_order = {g.id: g.order for g in groups}
    return Vocabulary(
        version=keys_doc["version"],
        groups=groups,
        # in reading order: groups in their order, a group's keys in theirs
        keys=tuple(sorted((Key(**k) for k in keys_doc["keys"]),
                          key=lambda k: (group_order[k.group], k.order))),
        tokens=tokens,
        aliases={k: tuple(v) for k, v in aliases_doc["aliases"].items()},
        index=index,
    )


@lru_cache(maxsize=None)
def load_vocabulary() -> Vocabulary:
    """The shipped vocabulary, validated once per process."""
    return vocabulary_from(paths.VOCABULARY_DIR)


def canonical_id(key_name: str, label: str | None = None) -> str | None:
    """The canonical id of a key (``VOLUME_UP``), or None when it has none.

    ``key_name`` is the key's name in the ledger (``KEY_VOL_PLUS``) and ``label``
    the text its source shows for it (``"VOL+"``), which only imported and some
    authored keys carry. None means "no confident meaning": the key stays in a
    client's "More" group under its own label.
    """
    return load_vocabulary().canonical_id(key_name, label)
