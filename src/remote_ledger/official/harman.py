"""The custom installation notes of Harman's JBL Synthesis and Lexicon AV receivers and processors: their RC-5 code sets (DESIGN section 88).

JBL Synthesis's SDR-35, SDR-38, SDP-55 and SDP-58 and Lexicon's MC-10, RV-9 and RV-6 are built on Arcam's platform, and their ``Custom Installation Notes: Serial programming
interface and IR remote commands`` are Arcam's document with the product's name (the same ``SH289E`` as Arcam's AVR11 to AVR41): **its last two pages are the RC-5 code set**, a
table of functions with the code as ``system-command`` in decimal and as ``Data1 - Data2`` in hex (``Standby  16-12  0x10 - 0x0C``), which §64's reader (``arcam.function_rows``)
reads as it reads Arcam's own. **A key is the ledger's ``RC5``**: the system code the device, the command the function, the label the table's name for it. **A file for each product the
title names** (``Synthesis SDR-35`` of JBL, ``MC-10`` of Lexicon), the same keys. The codes are Arcam's, so the files are the products' names for them: **every one of the 118 codes of the
JBL document is in the public Arcam tables already**. A row whose decimal and hex are not one code, a digit row whose command is not the digit, and **two rows of one code (the table
gives ``Yellow`` and ``HOME`` 16-43, ``Blue`` and ``INFO`` 16-55)** make their keys Untested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..import_common import form_compiles
from .arcam import function_rows, key_names
from .common import DOUBTFUL_TIER, IMPORT_ROOT, TIER, Report, Snapshot, slug

MAKER = "harman"
PROTOCOL = {"name": "RC5", "carrierHz": 36000, "minSends": 1}


@dataclass(frozen=True)
class Document:
    file: str
    brand: str
    host: str                      # what the citation calls the document's host
    models: tuple[str, ...]        # a file for each: the names the title gives, with the line of the brand a name needs (``Synthesis SDR-35``)


DOCUMENTS: tuple[Document, ...] = (
    Document("RS232_SDR35_38_SDP55_58_SH289E_E_2Jun21.pdf", "JBL", "jblsynthesis.com custom installation notes",
             ("Synthesis SDR-35", "Synthesis SDR-38", "Synthesis SDP-55", "Synthesis SDP-58")),
    Document("RS232_Protocol_Documentation.pdf", "Lexicon", "lexicon.com custom installation notes", ("MC-10", "RV-9", "RV-6")),
)


def build(snapshot: Snapshot, authored: dict[tuple[str, str], str], report: Report) -> list[tuple[str, dict[str, Any]]]:
    gate: dict[tuple, str | None] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for document in DOCUMENTS:
        rows = function_rows(snapshot.sheet_of(document.file, "tables"))
        entry, sha = snapshot.documents[document.file], snapshot.sha(document.file)
        report.count(MAKER, f"{document.file}: rows read", len(rows))
        prefix = (f"{document.host}/{document.file}@{sha} (retrieved {entry.get('retrieved', snapshot.retrieved)}"
                  + (f", from the Wayback Machine snapshot {entry['snapshot']}" if "snapshot" in entry else "") + ")")
        by_code: dict[tuple[int, int], list[str]] = {}
        for row in rows:
            by_code.setdefault((row.system, row.command), []).append(row.function)
        members: list[tuple[str, dict[str, Any]]] = []
        for row in rows:
            key = (row.system, row.command)
            if key not in gate:
                gate[key] = form_compiles(IMPORT_ROOT, PROTOCOL, "K", {"id": "primary.irp", "type": "irp", "device": row.system, "function": row.command,
                                                                       "confidence": TIER, "source": "probe"})
            if gate[key]:
                report.unrepresented[(MAKER, document.file, f"does not compile: {gate[key]}")] += 1
                continue
            cite = (f"{prefix} page {row.page} table {row.table} row '{row.written}' ({row.stated}): RC5 system {row.system} command {row.command}"
                    + ("; the table's decimal and hex say the same" if row.agrees else "; the table's decimal and hex do not say the same code, so the key rests on neither alone"))
            doubts = []
            if not row.agrees:
                doubts.append("its decimal and hex are not one code")
            if row.doubt:
                doubts.append(row.doubt)
            if len(by_code[key]) > 1:
                doubts.append("the table gives this code to " + " and ".join(f"'{n}'" for n in by_code[key]) + ", and a code is one function")
            if row.doubt or len(by_code[key]) > 1:
                cite += "; " + "; ".join(d for d in doubts if d != "its decimal and hex are not one code")
            for d in doubts[(0 if row.agrees else 1):]:
                note = (MAKER, f"{document.file} page {row.page}", f"'{row.written}' ({row.stated}): {d}")
                if note not in report.notes:
                    report.notes.append(note)
            members.append((row.function, {"id": "primary.irp", "type": "irp", "device": row.system, "function": row.command,
                                           "confidence": DOUBTFUL_TIER if doubts else TIER, "source": cite}))
        for model in document.models:
            final = model
            while (document.brand.casefold(), final.casefold()) in authored:
                renamed = f"{final} ({document.brand} table)"
                report.notes.append((MAKER, final, f"kept as '{renamed}': the ledger's {authored[(document.brand.casefold(), final.casefold())]} has the name (R15)"))
                final = renamed
            keys = {n: {"label": m[0], "forms": [m[1]]} for n, m in zip(key_names([m[0] for m in members]), members)}
            report.count(MAKER, "files")
            report.count(MAKER, "keys", len(keys))
            out.append((f"{IMPORT_ROOT}/{MAKER}/{slug(document.brand + ' ' + final)}.json",
                        {"manufacturer": document.brand, "model": final, "aliases": [], "controls": [f"{document.brand} {model}"], "protocol": dict(PROTOCOL), "keys": keys}))
    return out
