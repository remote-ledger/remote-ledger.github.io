"""``rl import lirc``: the LIRC remotes database, imported under SPEC R19.

DESIGN.md section 14 is the contract; each decision is cited where it is
implemented. In outline, every ``begin remote`` block of every upstream file
becomes one ledger remote under ``remotes/lirc/`` (D34, D37), each button one
key holding one form (D36):

* an ``irp`` form, when the block is NEC1, NECx2 or Sony20 shaped **and** our
  encoder's rendering of the decoded parameters passes R13's cross-check
  against lircd's own expansion of the block;
* otherwise a ``raw`` form holding lircd's expansion itself -- the first send
  as ``intro``, the repeat as ``repeat``, or ``repeat`` alone when the two
  are the same;
* otherwise a line in ``IMPORT.md``, never a silent drop.

Every form is ``plausible`` and cites its upstream file, line and block
(D35, D38). Output is a pure function of the checkout and its commit, so
re-running reproduces it byte for byte (D39).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..crosscheck import check_key
from ..encoding import CSS_IDENT
from ..errors import LedgerError
from ..fmt import format_document
from ..remote import remote_from_doc
from ..serialize import dumps, load
from .conf import IrCode, Remote as LircRemote, parse_file
from .errors import (
    LircConfigError,
    LircError,
    LircNoTimings,
    LircTerminated,
    LircTransmitError,
    LircUndefinedBehaviour,
    LircUnsupportedProtocol,
)
from .transmit import transmit_each

UPSTREAM = "lirc-remotes"
UPSTREAM_URL = "git.code.sf.net/p/lirc-remotes/code"
IMPORT_ROOT = "remotes/lirc"
REPORT = "IMPORT.md"
TIER = "plausible"
#: The schema's bound on minSends (remote.schema.json).
MIN_SENDS_MAX = 10
#: The schema's bounds on carrierHz (remote.schema.json).
CARRIER_MIN, CARRIER_MAX = 10_000, 500_000
#: Placeholders that header lines use in place of a device name (D37).
PLACEHOLDERS = {"", "?", "-", "--", "n/a", "na", "none", "unknown", "various", "all"}
_EMAIL = re.compile(r"<[^<>]*@[^<>]*>|\S+@\S+")
_SLUG = re.compile(r"[^A-Za-z0-9._+-]")


# --- the upstream file's header comment (D35, D37) ---------------------------

@dataclass(frozen=True)
class Header:
    contributor: str | None = None
    controls: tuple[str, ...] = ()


_FIELD = re.compile(r"^#\s*([^:]{3,80}?)\s*:\s*(.*)$")
_CONTRIBUTED = re.compile(r"^#\s*contributed\s+by\s*:?\s*(.*)$", re.I)
_CONTROLS_FIELDS = (
    "devices being controlled by this remote",
    "device(s) controlled by this remote",
)


def _clean_person(text: str) -> str | None:
    """A contributor's name without any email address (D34)."""
    name = _EMAIL.sub("", text).strip(" \t,;()[]<>")
    name = re.sub(r"\s+", " ", name)
    return name or None


def parse_header(text: str) -> Header:
    """The contributor and the devices-controlled line, from column-0
    comments. Both irrecord header generations are recognised; a field's
    value may continue on following comment lines until the next field or a
    bare ``#``."""
    contributor = None
    controls_text: list[str] = []
    collecting = False
    for line in text.splitlines():
        if not line.startswith("#"):
            collecting = False
            continue
        if m := _CONTRIBUTED.match(line):
            contributor = contributor or _clean_person(m[1])
            collecting = False
            continue
        if m := _FIELD.match(line):
            collecting = m[1].strip().lower() in _CONTROLS_FIELDS
            if collecting and m[2].strip():
                controls_text.append(m[2])
            continue
        body = line.lstrip("#").strip()
        if not body:
            collecting = False
        elif collecting:
            controls_text.append(body)
    devices: list[str] = []
    for part in re.split(r"[,;]", " ".join(controls_text)):
        device = re.sub(r"\s+", " ", _EMAIL.sub("", part)).strip(" .")
        if device.lower() not in PLACEHOLDERS and device not in devices:
            devices.append(device)
    return Header(contributor=contributor, controls=tuple(devices))


# --- names (D37) -----------------------------------------------------------------

def key_name(original: str) -> str:
    """A LIRC button name as a CSS identifier (D29), deterministically.

    A trailing ``+``/``-`` is the commonest reason a name is illegal
    (``VOL+``, ``CH-``), so it becomes ``_PLUS``/``_MINUS``; any other
    character outside ``[A-Za-z0-9_]`` becomes ``_``, and a name that would
    start with a digit gets ``KEY_``.
    """
    if CSS_IDENT.match(original):
        return original
    stem, suffix = original, ""
    if stem.endswith("+"):
        stem, suffix = stem[:-1], "_PLUS"
    elif stem.endswith("-"):
        stem, suffix = stem[:-1], "_MINUS"
    name = re.sub(r"[^A-Za-z0-9_]", "_", stem) + suffix
    if not name or not (name[0].isalpha() or name[0] == "_"):
        name = "KEY_" + name
    return name


def slug(text: str) -> str:
    return _SLUG.sub("_", text) or "_"


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".lircd.conf", ".conf"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# --- decoding a block to protocol parameters (D36.1) ---------------------------

def _lsb(bits: str) -> int:
    """Bits in transmission order, read LSB-first as IRP fields are."""
    return sum(int(b) << i for i, b in enumerate(bits))


def _sent_bits(r: LircRemote, code: int) -> str:
    """pre_data, code and post_data in the order lircd sends them: each
    field most significant bit first (transmit.c send_data reverses the
    value, then emits its low bit first)."""
    parts = []
    for value, width in ((r.pre_data, r.pre_data_bits), (code, r.bits),
                         (r.post_data, r.post_data_bits)):
        if width:
            parts.append(format(value & ((1 << width) - 1), f"0{width}b"))
    return "".join(parts)


def decode_candidates(r: LircRemote, code: IrCode) -> list[tuple[str, dict[str, int]]]:
    """Protocol parameters the block *might* describe, most likely first.

    Only a plain SPACE_ENC block qualifies: anything that alters bits per
    send (toggles, repeat masks) or adds bursts no IRP here has (plead, foot,
    pre/post pulses), or a button with several codes, is left to the raw
    path. A candidate is only a hypothesis; the cross-check decides.
    """
    if not r.is_space_enc() or code.next or code.signals is not None:
        return []
    if (r.toggle_bit_mask or r.toggle_mask or r.repeat_mask or r.plead
            or r.pfoot or r.sfoot or r.pre_p or r.pre_s or r.post_p or r.post_s):
        return []
    bits = _sent_bits(r, code.code)
    if len(bits) == 32:
        d, s, f, nf = (_lsb(bits[i:i + 8]) for i in (0, 8, 16, 24))
        if f ^ nf != 0xFF:
            return []
        params = {"device": d, "subdevice": s, "function": f}
        return [("NEC1", params), ("NECx2", params)]
    if len(bits) == 20:
        return [("Sony20", {
            "function": _lsb(bits[0:7]),
            "device": _lsb(bits[7:12]),
            "subdevice": _lsb(bits[12:20]),
        })]
    return []


# --- the report (D36.3, D39) ---------------------------------------------------

@dataclass
class Report:
    commit: str
    files: Counter = field(default_factory=Counter)
    remotes: Counter = field(default_factory=Counter)
    keys: Counter = field(default_factory=Counter)
    #: (upstream file, reason) for files that yield nothing.
    skipped_files: list[tuple[str, str]] = field(default_factory=list)
    #: (upstream file, block, line, reason) for blocks that yield nothing.
    skipped_blocks: list[tuple[str, str, int, str]] = field(default_factory=list)
    #: (upstream file, block, button, line, reason).
    skipped_keys: list[tuple[str, str, str, int, str]] = field(default_factory=list)
    #: (upstream file, block, target path) for authored-data collisions.
    collisions: list[tuple[str, str, str]] = field(default_factory=list)

    def render(self) -> str:
        out = [
            "# LIRC import report",
            "",
            "Generated by `rl import lirc` (DESIGN.md section 14); never hand-edited.",
            f"Upstream: `{UPSTREAM_URL}` @ `{self.commit}`.",
            "",
            "Everything upstream holds that the import could not represent is",
            "listed here with its reason (SPEC R19, condition 5).",
            "",
            "## Totals",
            "",
            "| | Count |",
            "|---|---|",
        ]
        for label, counter in (("Upstream files", self.files),
                               ("Remote blocks", self.remotes),
                               ("Buttons", self.keys)):
            for what, n in sorted(counter.items()):
                out.append(f"| {label}: {what} | {n:,} |")
        sections = (
            ("Files that yield no remote", ["File", "Reason"],
             sorted(self.skipped_files)),
            ("Remote blocks skipped", ["File", "Block", "Line", "Reason"],
             sorted(self.skipped_blocks)),
            ("Blocks skipped because an authored remote wins (R19, condition 4)",
             ["File", "Block", "Would have been"], sorted(self.collisions)),
            ("Buttons skipped", ["File", "Block", "Button", "Line", "Reason"],
             sorted(self.skipped_keys)),
        )
        for title, header, rows in sections:
            out += ["", f"## {title} ({len(rows):,})", ""]
            if not rows:
                out.append("None.")
                continue
            out.append("| " + " | ".join(header) + " |")
            out.append("|" + "---|" * len(header))
            for row in rows:
                out.append("| " + " | ".join(_md(v) for v in row) + " |")
        return "\n".join(out) + "\n"


def _md(value: Any) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return f"`{text}`" if text and not text[0].isdigit() else text


# --- one block (D36, D38) ---------------------------------------------------------

def _citation(where: str, line: int, block: str, contributor: str | None,
              how: str, notes: Iterable[str]) -> str:
    """D35's fixed shape."""
    text = f"{UPSTREAM}@{{sha}} {where}:{line} [block {block}]"
    if contributor:
        text += f" (contributed by {contributor})"
    text += f": {how}"
    extra = [n for n in notes if n]
    if extra:
        text += "; " + "; ".join(extra)
    return text


def _raw_form(first, second) -> dict[str, Any]:
    form: dict[str, Any] = {"id": "primary.raw", "type": "raw"}
    if first != second:
        form["intro"] = list(first)
    form["repeat"] = list(second)
    return form


def _probe(protocol: dict[str, Any], key: str, forms: list[dict[str, Any]]):
    """Load a one-key remote in memory and cross-check it (R13)."""
    doc = {"manufacturer": "probe", "model": "probe", "protocol": protocol,
           "keys": {key: {"forms": forms}}}
    remote = remote_from_doc(doc, Path(IMPORT_ROOT) / "probe.json")
    mismatches, _ = check_key(remote, key)
    return remote, mismatches


#: remote.schema.json's bounds on a raw sequence.
RAW_ITEM_MAX, RAW_LEN_MAX = 1_000_000, 2048


def _compiles(protocol: dict[str, Any], key: str, form: dict[str, Any]) -> str | None:
    """None if the form is schema-legal and compiles to Pronto, else why
    not (the schema's raw bounds, then D28's)."""
    for name in ("intro", "repeat"):
        seq = form.get(name) or []
        if len(seq) > RAW_LEN_MAX:
            return f"{name} has {len(seq)} durations; the schema allows {RAW_LEN_MAX}"
        if any(not 1 <= d <= RAW_ITEM_MAX for d in seq):
            return f"{name} holds a duration outside 1-{RAW_ITEM_MAX} us"
    try:
        remote, _ = _probe(protocol, key, [form])
        remote.compile_group(key, "primary")
    except (LedgerError, ValueError) as exc:
        return str(exc)
    return None


def import_block(
    r: LircRemote, where: str, contributor: str | None, report: Report,
    upstream: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]] | None:
    """(protocol block, keys) for one lircd remote, or None if it yields
    nothing. ``where`` is the upstream path cited; ``upstream`` the same,
    for the report."""
    block = r.name or "?"

    def skip_block(reason: str) -> None:
        report.remotes[f"skipped: {reason}"] += 1
        report.skipped_blocks.append((upstream, block, r.line, reason))

    if r.is_grundig() or r.is_bo() or r.is_serial():
        return skip_block("lircd cannot send this protocol (GRUNDIG/BO/SERIAL)")
    if not (CARRIER_MIN <= r.freq <= CARRIER_MAX):
        return skip_block(f"carrier {r.freq} Hz is outside {CARRIER_MIN}-{CARRIER_MAX} (D1a)")
    if r.min_repeat + 1 > MIN_SENDS_MAX:
        return skip_block(f"min_repeat {r.min_repeat} exceeds the schema's minSends of {MIN_SENDS_MAX}")
    if not r.codes:
        return skip_block("no buttons")

    protocol: dict[str, Any] = {"carrierHz": r.freq, "minSends": r.min_repeat + 1}
    notes = []
    if "frequency" not in r.field_lines:
        notes.append("carrier: lircd's default 38 kHz, the block sets none")
    if r.toggle_bit_mask or r.toggle_mask:
        notes.append("toggle as lircd sends it on a first press")
    raw_how = ("raw_codes capture" if r.is_raw()
               else "expanded to raw by lircd 0.10.2's transmit rules")

    # First occurrence of each name wins, as get_code_by_name finds it.
    seen: set[str] = set()
    buttons: list[tuple[IrCode, tuple[int, ...], tuple[int, ...]]] = []
    for code, sent in transmit_each(r, sends=2):
        folded = code.name.lower()
        def skip_key(reason: str) -> None:
            report.keys[f"skipped: {reason}"] += 1
            report.skipped_keys.append((upstream, block, code.name, code.line, reason))
        if folded in seen:
            skip_key("duplicate name; lircd sends the first")
            continue
        seen.add(folded)
        if code.next:
            skip_key("several codes on one button")
            continue
        if isinstance(sent, LircNoTimings):
            return skip_block("no timings: a scancode-only driver's remote")
        if isinstance(sent, LircTransmitError):
            skip_key(f"lircd refuses to send it: {sent}")
            continue
        if isinstance(sent, LircError):
            skip_key(f"lircd cannot send it: {sent}")
            continue
        first, second = sent
        buttons.append((code, first, second))
    if not buttons:
        return skip_block("no button survives") if r.codes else None

    # D36.1: which protocol, if any, the block decodes to -- chosen by how
    # many buttons pass the cross-check under it.
    accepted: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    for code, first, second in buttons:
        raw = dict(_raw_form(first, second), confidence=TIER, source="probe")
        for name, params in decode_candidates(r, code):
            irp = {"id": "primary.irp", "type": "irp", **params,
                   "confidence": TIER, "source": "probe"}
            try:
                _, mismatches = _probe(dict(protocol, name=name), "K", [irp, raw])
            except (LedgerError, ValueError):
                continue
            if not mismatches:
                accepted[name][code.name] = params
                break
    chosen = max(sorted(accepted), key=lambda n: len(accepted[n]), default=None)
    if chosen:
        protocol["name"] = chosen

    keys: dict[str, dict[str, Any]] = {}
    taken: set[str] = set()
    for code, first, second in buttons:
        name = key_name(code.name)
        base, n = name, 2
        while name.casefold() in taken:
            name, n = f"{base}_{n}", n + 1
        taken.add(name.casefold())
        renamed = f"button {code.name!r}" if name != code.name else ""
        if chosen and code.name in accepted[chosen]:
            form = {"id": "primary.irp", "type": "irp", **accepted[chosen][code.name]}
            how = f"decoded to {chosen} from a parametric block"
        else:
            form = _raw_form(first, second)
            how = raw_how
        form["confidence"] = TIER
        form["source"] = _citation(where, code.line, block, contributor, how,
                                   [renamed, *notes])
        why = _compiles(protocol, name, form)
        if why:
            taken.discard(name.casefold())
            report.keys["skipped: does not compile to Pronto"] += 1
            report.skipped_keys.append(
                (upstream, block, code.name, code.line, f"does not compile to Pronto: {why}"))
            continue
        keys[name] = {"forms": [form]}
        report.keys[f"imported: {form['type']}" + (f" {chosen}" if form["type"] == "irp" else "")] += 1
    if not keys:
        return skip_block("no button compiles")
    report.remotes["imported"] += 1
    return protocol, keys


# --- the whole tree (D37, D39) ----------------------------------------------------

def authored_names(root: Path) -> dict[tuple[str, str], str]:
    """(manufacturer, model-or-alias), casefolded, for every file outside
    the import root -- the names an import must never shadow (R19.4)."""
    from ..validate import corpus_files

    names: dict[tuple[str, str], str] = {}
    for path in corpus_files(root):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(IMPORT_ROOT + "/"):
            continue
        doc = load(path)
        maker = doc["manufacturer"].casefold()
        for name in [doc["model"], *(doc.get("aliases") or [])]:
            names[(maker, name.casefold())] = rel
    return names


def _naming(stem: str, block: str | None, several: bool) -> tuple[str, str]:
    """(model, file slug) for one block (D37)."""
    if several:
        return f"{stem} [{block}]", f"{slug(stem)}.{slug(block or '_')}"
    return stem, slug(stem)


def import_tree(checkout: Path, commit: str, authored: dict[tuple[str, str], str]):
    """Every upstream file, as ``{repo path: document}`` plus the report."""
    sha = commit[:7]
    report = Report(commit=commit)
    out: dict[str, dict[str, Any]] = {}
    # Per manufacturer, casefolded: upstream holds pairs such as
    # DigiMatrix.lircd.conf / digimatrix.lircd.conf, and foo.conf /
    # foo.lircd.conf, which would otherwise share a path -- one silently
    # overwriting the other -- and a model name, which R15 forbids.
    taken_models: dict[str, set[str]] = defaultdict(set)
    taken_slugs: dict[str, set[str]] = defaultdict(set)
    for conf in sorted((checkout / "remotes").glob("*/*.conf")):
        upstream = conf.relative_to(checkout).as_posix()
        maker = conf.parent.name
        try:
            config = parse_file(conf)
        except LircConfigError as exc:
            report.files["skipped: lircd rejects the file"] += 1
            report.skipped_files.append((upstream, f"lircd rejects the file: {exc}"))
            continue
        if not config.file_order:
            report.files["skipped: no remote block"] += 1
            report.skipped_files.append((upstream, "no remote block (for example a lircmd config)"))
            continue
        header = parse_header(conf.read_text(encoding="utf-8", errors="replace"))
        several = len(config.file_order) > 1
        produced = 0
        for r in config.file_order:
            model, file_slug = _naming(_stem(conf), r.name, several)
            if (model.casefold() in taken_models[maker.casefold()]
                    or file_slug.casefold() in taken_slugs[maker.casefold()]):
                # A later file that collides is named by its whole upstream
                # file name, which is unique in its directory.
                model, file_slug = _naming(conf.name, r.name, several)
            base, model_base, n = file_slug, model, 2
            while (file_slug.casefold() in taken_slugs[maker.casefold()]
                   or model.casefold() in taken_models[maker.casefold()]):
                file_slug, model, n = f"{base}_{n}", f"{model_base} ({n})", n + 1
            taken_models[maker.casefold()].add(model.casefold())
            taken_slugs[maker.casefold()].add(file_slug.casefold())
            target = f"{IMPORT_ROOT}/{slug(maker)}/{file_slug}.json"
            if (maker.casefold(), model.casefold()) in authored:
                report.remotes["skipped: an authored remote wins"] += 1
                report.collisions.append((upstream, r.name or "?", target))
                continue
            result = import_block(r, upstream, header.contributor, report, upstream)
            if result is None:
                continue
            protocol, keys = result
            for spec in keys.values():
                for form in spec["forms"]:
                    form["source"] = form["source"].replace("{sha}", sha)
            doc: dict[str, Any] = {"manufacturer": maker, "model": model, "aliases": []}
            if header.controls:
                doc["controls"] = list(header.controls)
            doc["protocol"] = protocol
            doc["keys"] = keys
            out[target] = doc
            produced += 1
        report.files["imported" if produced else "skipped: no block imported"] += 1
    return out, report


def write_import(root: Path, checkout: Path, commit: str) -> Report:
    """Rewrite ``remotes/lirc/`` wholesale (D39): every ``*.json`` and
    ``IMPORT.md`` are the importer's; anything else there (``README.md``,
    ``COPYING``) is authored and left alone."""
    docs, report = import_tree(checkout, commit, authored_names(root))
    target_root = root / IMPORT_ROOT
    for stale in sorted(target_root.rglob("*.json")) if target_root.exists() else []:
        if stale.relative_to(root).as_posix() not in docs:
            stale.unlink()
    for rel, doc in sorted(docs.items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(doc, sort_keys=False), encoding="utf-8", newline="\n")
        # rl fmt's canonical form (D20), so the committed file is exactly
        # what the formatter would produce and `rl fmt --check` stays clean.
        text = format_document(path)
        path.write_text(text, encoding="utf-8", newline="\n")
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / REPORT).write_text(report.render(), encoding="utf-8", newline="\n")
    for empty in sorted((p for p in target_root.rglob("*") if p.is_dir()), reverse=True):
        if not any(empty.iterdir()):
            empty.rmdir()
    return report
