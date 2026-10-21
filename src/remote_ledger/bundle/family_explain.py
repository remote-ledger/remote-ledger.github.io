"""``rl bundle family-explain REF``: the evidence for the keys a remote is completed with (D360).

A key that the bundle adds to a thin remote (D357) is a claim: that the device answers to it. The
claim is checked by looking at the files it came from, so this prints, for one remote of the
ledger, **each key it has and each key it would be given, with its code**, and for each added key
the corroborating remotes that hold it, each with its ref, the name its own file gives the key and
its code as that file writes it. A person who wants to know why the bundle says a Samsung TV has a
Hulu key reads it here and then opens the files.

It is computed from the tree and not read from a bundle: the rule is a pure function of the files
(D357), so the report is the one a bundle built from this tree holds, and what a bundle holds of
it is in ``remote_completions`` and ``completion_evidence`` for a service to show.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

from .. import paths
from ..forms import PRIMARY, Form, select
from ..remote import Remote, load_remote
from . import family as family_rule
from .corpus import RemoteRecord
from .merge import Folded, ref_of
from .signals import blob_to_pronto


def _hex(value: int | None) -> str:
    return "-" if value is None else f"0x{value:02X}"


def code_of(remote: Remote, form: Form) -> str:
    """A form's code as a person reads it: the protocol and its device, subdevice and function for a
    parametric form, the capture for a raw one, the words for a Pronto one."""
    if form.type == "irp":
        parts = [remote.protocol.name or "irp", f"D={_hex(form.device)}"]
        if form.subdevice is not None:
            parts.append(f"S={_hex(form.subdevice)}")
        parts.append(f"F={_hex(form.function)}")
        return " ".join(parts)
    if form.type == "raw":
        return f"raw capture, {len(form.intro) + len(form.repeat)} durations, {form.carrier_hz or remote.protocol.carrier_hz} Hz"
    return f"pronto {form.hex}"


class _Files:
    """Finds, in the files that make up a remote, the key that sends a signal, and its code."""

    def __init__(self, root: Path, records: list[RemoteRecord], folded: Folded) -> None:
        self.root, self.records, self.folded = root, records, folded
        self._loaded: dict[str, Remote] = {}

    def _load(self, where: str) -> Remote:
        if where not in self._loaded:
            self._loaded[where] = load_remote(paths.locate(self.root, where))
        return self._loaded[where]

    def find(self, i: int, blob: bytes, canon: str | None = None) -> tuple[str, str, str, str] | None:
        """``(file's ref, key name in the file, code, tier)`` of the key of remote ``i`` (a carrier)
        that sends ``blob``, in its own file or in a fragment folded into it. Where a file has two keys
        for one signal, the one called ``canon`` is the answer when there is one."""
        found = None
        for j in (i, *self.folded.parts.get(i, ())):
            record = self.records[j]
            for name, _label, key_canon, _rank, key_blob in record.keys:
                if key_blob == blob and (found is None or (key_canon == canon and found[0] != canon)):
                    found = (key_canon, record, name)
        if found is None:
            return None
        _, record, name = found
        remote = self._load(record.where)
        form = select(remote.groups(name)[PRIMARY])
        return ref_of(record), name, code_of(remote, form), form.confidence


def _tree_version(root: Path) -> str:
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return commit + (" plus uncommitted changes" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown (not a git checkout)"


def explain(root: Path, records: list[RemoteRecord], folded: Folded, order: dict[str, int], ref: str, *,
            supporters: int = 5, pronto: bool = False) -> str:
    """The report for the remote whose file is ``ref`` (a ref of the ledger, ``samsung/BN59-01199F``).
    ``supporters`` is how many holders are listed under each added key, ``0`` for all of them."""
    index = next((i for i, r in enumerate(records) if ref_of(r) == ref), None)
    if index is None:
        raise KeyError(f"no remote {ref!r} in the ledger")
    carrier = folded.carrier[index]
    fam = family_rule.Family(records, folded, order)
    remote = fam.remotes[carrier]
    files = _Files(root, records, folded)
    out = [f"{ref}"]
    if carrier != index:
        out.append(f"  (a fragment folded into {ref_of(records[carrier])}, D103: its keys are that remote's)")
    record = records[carrier]
    out.append(f"  {record.manufacturer} {record.model}; {record.source}; {record.protocol or 'no protocol name'} at "
               f"{record.carrier_hz} Hz; ledger tree {_tree_version(root)}")

    def listing(keys: tuple) -> list[str]:
        lines = [f"  {'n':>3}  {'key':<18} {'code':<34} file's key (tier)"]
        for n, (name, _label, canon, _rank, blob) in enumerate(keys):
            found = files.find(carrier, blob, canon)
            code, where, tier = (found[2], found[1], found[3]) if found else ("?", name, "?")
            lines.append(f"  {n:>3}  {(canon or name):<18} {code:<34} {where} ({tier})")
            if pronto:
                lines.append(f"        pronto {blob_to_pronto(blob)}")
        return lines

    out += ["", f"The remote's own keys: {len(remote.keys)}" + (" (thin: fewer than "
            f"{family_rule.THIN_BELOW})" if len(remote.keys) < family_rule.THIN_BELOW else "")]
    out += listing(remote.keys)
    if len(remote.keys) >= family_rule.THIN_BELOW:
        out += ["", f"It has {family_rule.THIN_BELOW} keys or more, so it is carried as its file has it (D357, line 1)."]
        return "\n".join(out) + "\n"

    tally: Counter[str] = Counter()
    found = fam.complete_remote(remote.keys, fam.plays[carrier], frozenset((carrier,)), tally)
    if found is None:
        corr = next((k for k in tally if k.startswith("corroborators")), None)
        if corr is None:
            reason = f"it has fewer than {family_rule.MIN_SIGNALS} signals to be recognised by"
        elif corr.endswith("+"):
            reason = "the remotes that corroborate it hold no key it lacks that enough of them agree on and name alike"
        else:
            reason = f"it has {corr.split(': ')[1]} corroborating remotes and {family_rule.MIN_CORROBORATORS} are needed"
        out += ["", f"It is not completed: {reason}."]
        return "\n".join(out) + "\n"

    out += ["", f"Completed with {len(found.keys)} untested keys from {len(found.family)} corroborating remotes.",
            "A remote corroborates it when it plays alike, has three quarters of its signals and gives the shared "
            "ones the same names; a key is added when two fifths of them hold it, named as most of them name it "
            "(DESIGN section 97, D357).", ""]
    out.append("The corroborating remotes: " + ", ".join(ref_of(records[j]) for j in found.family[:12])
               + (f", and {len(found.family) - 12} more" if len(found.family) > 12 else ""))
    out += ["", f"The keys added (untested), after the {len(remote.keys)} above:"]
    for k, (key, holders) in enumerate(zip(found.keys, found.holders)):
        n = len(remote.keys) + k
        canon, blob = key[2], key[4]
        names = Counter()
        for j in holders:
            for _name, _label, c, rank, b in fam.remotes[j].keys:
                if b == blob and c is not None and rank <= family_rule.VOTES_FROM:
                    names[c] += 1
        shown = holders if not supporters else holders[:supporters]
        sample = files.find(holders[0], blob, canon)
        code = sample[2] if sample else "?"
        out.append(f"  {n:>3}  {canon:<18} {code:<34} held by {len(holders)} of {len(found.family)}; named "
                   f"{canon} by {names[canon]} of the {sum(names.values())} that give it a canonical name"
                   f"{'' if sum(names.values()) == len(holders) else f', {len(holders) - sum(names.values())} give it none'}")
        for j in shown:
            held = files.find(j, blob, canon)
            if held:
                out.append(f"        {held[0]}: {held[1]} = {held[2]} ({held[3]})")
        if len(shown) < len(holders):
            out.append(f"        and {len(holders) - len(shown)} more (--all lists them)")
        if pronto:
            out.append(f"        pronto {blob_to_pronto(blob)}")
    return "\n".join(out) + "\n"
