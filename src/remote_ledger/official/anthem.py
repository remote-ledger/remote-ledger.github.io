"""Anthem's MRX and AVM IR code table (``AVM-MRXx40-IR-hex-*.xlsx``).

One sheet lists, for zone 1 and zone 2, each function with the NEC1 *device* (two bytes) and *data*
(one byte) as sent, the same as a ``NEC1`` shorthand string, and a Pronto hex. The other names the
models of each of its three remote layouts. The codes are imported as ``irp`` NEC1 forms read from
the device and data columns, **and each is checked against the sheet's own Pronto hex**: the hex is
decoded bit by bit and must say the same device, sub device and function. 84 of the 104 rows do. The
other 20 (inputs 21 to 30) do not: there the sheet's NEC1 and Pronto columns repeat the codes of
inputs 11 to 20 and its data column does not (D125), so those keys are Untested and say so.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..import_common import form_compiles
from ..irblaster.importer import fold_label
from ..pronto import ProntoParseError, parse_words
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "anthem"
BRAND = "Anthem"
HOST = "anthemav.com (storage.googleapis.com/sandbox1-anthemav/an)"
DOCUMENT = "AVM-MRXx40-IR-hex-20251202185749500.xlsx"
LAYOUT_SHEET = "MRX x10-x40 remote layout"
CODES_SHEET = "MRX x10-x40 IR hex codes"
PROTOCOL = {"name": "NEC1", "carrierHz": 38000, "minSends": 1}
_INPUT = re.compile(r"^input (\d+)$")
_HEX = re.compile(r"^[0-9A-Fa-f]{2}$")
#: The sheet's note 2: the codes of inputs 21 to 30 are for the x20 group (AVM 60 and the MRX x20), not the x10, and not the x40.
INPUTS_21_30 = "x20"


def groups_of(rows: list[list[str]]) -> dict[str, list[str]]:
    """``{'x40': ['AVM 70', ...]}`` from the layout sheet's lines ``MRX x40: AVM 70, AVM 90, ...``."""
    out: dict[str, list[str]] = {}
    for row in rows:
        if len(row) >= 2 and re.fullmatch(r"MRX x\d+:", row[0].strip()):
            out[row[0].strip().removeprefix("MRX ").removesuffix(":")] = [m.strip() for m in row[1].split(",") if m.strip()]
    return out


def pronto_bytes(pronto: str) -> tuple[int, int, int, int] | None:
    """The four NEC bytes a Pronto hex sends, read bit by bit from its first burst (a space of about
    1.5 times a mark is a one, as in the sheet's own strings), or None if it is not NEC-shaped."""
    try:
        words = parse_words(pronto)
    except ProntoParseError:
        return None
    once = words[2]
    pairs = [(words[4 + 2 * i], words[5 + 2 * i]) for i in range(once)]
    if len(pairs) < 34:
        return None
    bits = [1 if space > 1.5 * mark else 0 for mark, space in pairs[1:33]]
    return tuple(sum(bits[8 * k + i] << i for i in range(8)) for k in range(4))   # type: ignore[return-value]


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    sha = snapshot.sha(DOCUMENT)
    groups = groups_of(snapshot.sheet_of(DOCUMENT, LAYOUT_SHEET))
    rows = snapshot.sheet_of(DOCUMENT, CODES_SHEET)
    header = [c.strip() for c in rows[0]]
    col = {name: header.index(name) for name in ("Zone", "Function", "Device", "Data", "Pronto classic")}
    codes: list[dict[str, Any]] = []
    for number, row in enumerate(rows[1:], start=2):
        get = lambda k: row[col[k]].strip() if col[k] < len(row) else ""   # noqa: E731
        zone, function, device, data = get("Zone"), get("Function"), get("Device"), get("Data")
        if zone not in ("1", "2") or not function:
            if function or zone:
                report.notes.append((MAKER, f"{DOCUMENT} row {number}", f"the sheet says: {' | '.join(c for c in row if c)[:200]}"))
            continue
        if len(device) != 4 or not _HEX.match(data) or not all(_HEX.match(device[i:i + 2]) for i in (0, 2)):
            report.unrepresented[(MAKER, DOCUMENT, "the device or data cell is not hex")] += 1
            continue
        d0, d1, f = int(device[:2], 16), int(device[2:], 16), int(data, 16)
        decoded = pronto_bytes(" ".join(get("Pronto classic").split()))
        agrees = decoded == (d0, d1, f, ~f & 0xFF)
        codes.append({"row": number, "zone": zone, "function": function, "device": device, "data": data,
                      "triple": (d0, d1, f), "agrees": agrees, "decoded": decoded})
        report.count(MAKER, "rows read")
        if not agrees:
            report.count(MAKER, "rows whose Pronto hex disagrees with their data column")
    out = []
    gate: dict[tuple, str | None] = {}
    for group, models in groups.items():
        members = [c for c in codes if not (_INPUT.match(c["function"]) and 21 <= int(_INPUT.match(c["function"])[1]) <= 30
                                              and group != INPUTS_21_30)]
        for model in models:
            if (BRAND.casefold(), model.casefold()) in authored:
                report.collisions.append((MAKER, model))
                continue
            keys: dict[str, dict[str, Any]] = {}
            names: Counter = Counter()
            for c in members:
                label = c["function"] if c["zone"] == "1" else f"zone 2 {c['function']}"
                name = "KEY_" + (fold_label(label) or "COMMAND")
                names[name] += 1
                if names[name] > 1:
                    name = f"{name}_{names[name]}"
                d0, d1, f = c["triple"]
                if (d0, d1, f) not in gate:
                    gate[(d0, d1, f)] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {
                        "id": "primary.irp", "type": "irp", "device": d0, "subdevice": d1, "function": f,
                        "confidence": TIER, "source": "probe"})
                if gate[(d0, d1, f)]:
                    report.unrepresented[(MAKER, DOCUMENT, f"does not compile: {gate[(d0, d1, f)]}")] += 1
                    continue
                source = (f"{HOST}/{DOCUMENT}@{sha} (retrieved {snapshot.retrieved}) sheet '{CODES_SHEET}' row {c['row']} "
                          f"zone {c['zone']} '{c['function']}' (device {c['device']}, data {c['data']}): NEC1 device {d0} "
                          f"subdevice {d1} function {f}")
                if c["agrees"]:
                    source += "; the sheet's own Pronto hex decodes to the same device, sub device and function"
                else:
                    got = c["decoded"]
                    source += ("; the sheet contradicts itself here: its Pronto hex decodes to function "
                               f"{got[2] if got else 'nothing'} and its NEC1 column repeats that code, which an earlier input of "
                               "the sheet already has, while its data column gives this one")
                if _INPUT.match(c["function"]):
                    source += "; the sheet's note 1: the factory remote does not send this code, it is for programmable remotes"
                form = {"id": "primary.irp", "type": "irp", "device": d0, "subdevice": d1, "function": f,
                        "confidence": TIER if c["agrees"] else DOUBTFUL_TIER, "source": source}
                keys[name] = {"label": label, "forms": [form]}
                report.count(MAKER, "keys")
            report.count(MAKER, "files")
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(model)}.json",
                        {"manufacturer": BRAND, "model": model, "aliases": [], "controls": [f"{BRAND} {model}"],
                         "protocol": dict(PROTOCOL), "keys": keys}))
    return out
