"""The committed IR Blaster tree, read independently of the app API's generator
(plain ``json``), for the tests that check the importer's parsers and the API
against it. Read once per process."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from remote_ledger.irblaster import importer as imp
from remote_ledger.irblaster.importer import (
    FROM_DB_HEX, citation_commit, format_citation, parse_citation, split_controls_entry,
)

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "remotes" / "irblaster"


def _form_int(value):
    if value is None:
        return None
    return int(value, 16) if isinstance(value, str) else value


@lru_cache(maxsize=1)
def read_real_tree():
    """``(facts, failures)``: counts and sets from every file, and a line for
    everything that does not round-trip. ``failures`` empty is the claim."""
    facts = {"keys": 0, "ids": set(), "brands": set(), "pairs": set(), "files": 0,
             "rows": set()}
    failures: list[str] = []
    for path in sorted(REAL.glob("*/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        ledger = doc["protocol"]["name"]
        file_id = int(path.name.split("-", 1)[0])
        facts["files"] += 1
        for entry in doc.get("controls", []):
            brand, model = split_controls_entry(entry)
            if imp.controls_entry(brand, model) != entry:
                failures.append(f"{path}: controls entry {entry!r} does not round-trip")
            facts["brands"].add(brand)
            facts["pairs"].add((brand, model))
        for name, spec in doc["keys"].items():
            facts["keys"] += 1
            (form,) = spec["forms"]
            source = form["source"]
            try:
                db_id, label, hexcode, protocol = parse_citation(source)
            except ValueError as exc:
                failures.append(f"{path} {name}: {exc}")
                continue
            facts["ids"].add(db_id)
            facts["rows"].add((db_id, label, protocol, hexcode))
            if db_id != file_id:
                failures.append(f"{path} {name}: id {db_id}, file {file_id}")
            if label != spec["label"]:
                failures.append(f"{path} {name}: parsed label {label!r}, key label {spec['label']!r}")
            if format_citation(citation_commit(source), db_id, label, hexcode, protocol,
                               ledger) != source:
                failures.append(f"{path} {name}: does not re-format to itself")
            try:
                fields = tuple(FROM_DB_HEX[protocol](hexcode))
            except (KeyError, ValueError) as exc:
                failures.append(f"{path} {name}: {protocol} {hexcode}: {exc}")
                continue
            held = (ledger, _form_int(form["device"]), _form_int(form.get("subdevice")),
                    _form_int(form["function"]))
            if fields != held:
                failures.append(f"{path} {name}: {protocol} {hexcode} reads {fields}, form {held}")
    return facts, failures

