#!/usr/bin/env python3
"""Fetch the IR documents a manufacturer publishes into ``sources/official/<maker>/`` (DESIGN D123).

The documents are what ``rl import official`` is pinned to: a manufacturer's page is not a
repository, so the pin is the file as the server sent it, and ``MANIFEST.json`` records its URL,
size, SHA-256, the retrieval date (today: do not pass --date) and the server's ``Last-Modified``
(and, for a copy from the Wayback Machine, the address and snapshot it was archived from). Run it, then
``tools/official_sheets_to_csv.py <maker>`` for the spreadsheets, review the diff, and import.

    python3 tools/fetch_official.py marantz|anthem|oppo|kef|jvc|arcam|cambridge|hegel|cary|nad|simaudio|rotel
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

WAYBACK = re.compile(r"^https://web\.archive\.org/web/(?P<snapshot>\d{14})id_/(?P<original>https?://.+)$")
USER_AGENT = "remote-ledger snapshot (+https://github.com/remote-ledger)"
NAD = "https://cdn.shopify.com/s/files/1/0784/9751/3722/files/"
MARANTZ = ("https://www.marantz.com/on/demandware.static/-/Library-Sites-marantz_europe_shared/default/"
           "dw78eade36/archive-downloads/")
#: maker -> [(file name to keep, url, member of the zip to keep or None)]
DOCUMENTS = {
    "marantz": [
        ("marantz-master-ir.xls", MARANTZ + "marantz-master-ir.xls", None),
        ("marantz-2014-ir-command-sheet.xls", MARANTZ + "marantz-2014-ir-command-sheet.xls", None),
        ("marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls",
         MARANTZ + "marantz_fy18_av_sr_nr_ir_code_v02-02072018.xls", None),
    ],
    # Oppo's own host no longer resolves (the company stopped in 2018 and 2019): these are the Wayback Machine's
    # copies, each the snapshot named in its URL (``id_``, the raw bytes: the plain form answers a script with a
    # page of its own), which the manifest records with the original address.
    "oppo": [
        ("BDP-103_BDP-103D_Remote_Code_v1.2.xls",
         "https://web.archive.org/web/20240105021353id_/http://download.oppodigital.com/BDP103/BDP-103_BDP-103D_Remote_Code_v1.2.xls", None),
        ("BDP-103_BDP-105_Remote_Code_v1.1.xls",
         "https://web.archive.org/web/20260308143620id_/http://download.oppodigital.com/BDP103/BDP-103_BDP-105_Remote_Code_v1.1.xls", None),
        ("UDP-203_Remote_Code_v1.2.xls",
         "https://web.archive.org/web/20251211233638id_/http://download.oppodigital.com/UDP203/UDP-203_Remote_Code_v1.2.xls", None),
    ],
    # KEF's one-page IR code sheets, four from its own asset host and two (the LSX II and the XIO, which KEF
    # links from the product page) from the image host its product pages use, whose URLs carry a 40-digit hex id.
    "kef": [
        ("LS50Wireless-IRcode-INT.pdf", "https://assets.kef.com/pdf_doc/ls50w/LS50Wireless-IRcode-INT.pdf", None),
        ("LS50WII_IR-Codes-for-Universal-Remote-Control.pdf",
         "https://assets.kef.com/pdf_doc/LS50WII/IR-Codes-for-Universal-Remote-Control.pdf", None),
        ("LS60W_IR_Codes_for_Universal_Remote_Control.pdf",
         "https://assets.kef.com/product-support/ls60-wireless/LS60W_IR_Codes_for_Universal_Remote_Control.pdf", None),
        ("LSX-IRcode-INT.pdf", "https://assets.kef.com/pdf_doc/LSX/LSX-IRcode-INT.pdf", None),
        ("LSX-II_IR-Codes-for-Universal-Remote-Control.pdf",
         "https://images.salsify.com/image/upload/s--X_6hTA78--/3aa7ccb467cc8144a9e09034f2dd27fa5ddee5a6.pdf", None),
        ("XIO_IR-Command-Codes-for-Universal-Remote-Control.pdf",
         "https://images.salsify.com/image/upload/s--cq1M8mR1--/c8d2b757680914060ac95213150814ec7b4fdc2d.pdf", None),
    ],
    # JVC's D-ILA projector IR emulation guide (long hex), 37 pages; its last page says it "may be distributed freely, as long as
    # the author and copyright information remain intact", so the PDF is kept whole.
    "jvc": [
        ("DILA_Remote_Code_Emulation_Long_Hex.pdf",
         "https://support.jvc.com/consumer/support/documents/DILA_Remote_Code_Emulation_Long_Hex.pdf", None),
    ],
    # Arcam's custom installation notes: for each product family a PDF of the serial control commands and the RC-5
    # remote codes (a table of system and command codes in decimal and hex). They are on Arcam's own host, arcam.co.uk.
    "arcam": [
        ("AVR380-450-750_RS232_SH256E_2.pdf", "https://www.arcam.co.uk/ugc/tor/avr750/IR%20Codes/RS232_AVR750450380_SH256E_2.pdf", None),
        ("AVR5-10-20-30-40-11-21-31-41_RS232_SH289E_F.pdf",
         "https://www.arcam.co.uk/ugc/tor/AVR21/Custom%20Installation%20Notes/RS232_5_10_20_30_40_11_21_31_41__SH289E_F_07Oct21.pdf", None),
        ("SA10-SA20_RS232_SH277E_B.pdf", "https://www.arcam.co.uk/ugc/tor/SA20/Custom%20Installation%20Notes/SH277E_RS232_SA10_SA20_B.pdf", None),
        ("SA30_RS232_SH306E_4.pdf", "https://www.arcam.co.uk/ugc/tor/SA30/Custom%20Installation%20Notes/SH306E_RS232_SA30_4.pdf", None),
        ("CDS50_RS232_SH275E_C.pdf", "https://www.arcam.co.uk/ugc/tor/CDS50/Installation%20Notes/SH275E_CDS50_RS232_C_110619.pdf", None),
        ("UDP411_RS232_SH265_E_1.pdf", "https://www.arcam.co.uk/ugc/tor/udp411/RS232/UDP411_RS232_SH265_E_1_070814.pdf", None),
        ("Solo_IR_Remote_Codes.pdf", "https://www.arcam.co.uk/ugc/tor/solomovie51/IR%20Codes/solo_rc.pdf", None),
        ("Solo_Mini_RS232.pdf", "https://www.arcam.co.uk/ugc/tor/solomini/RS232/solomini_rs232.pdf", None),
    ],
    # Cambridge Audio's driver-updates page lists 16 PDFs of IR codes and a list of the 752BD, CXU and CXUHD remotes' NEC codes.
    # Only two carry no copyright or rights notice (the others do, so they are held in the private repository): the AXR85/AXR100
    # table and the remotes list. They are on Cambridge's own host, cambridgeaudio.com.
    "cambridge": [
        ("AXR85_100_IR_Codes.pdf",
         "https://www.cambridgeaudio.com/sites/default/files/compliance/doc/AXR85%20100%20IR%20Codes.pdf", None),
        ("752BD-CXU-CXUHD_remotes_Sept_2024_v2.pdf",
         "https://www.cambridgeaudio.com/sites/default/files/compliance/doc/752BD-CXU-CXUHD%20remotes%20-%20Sept.%202024%20v2.pdf", None),
    ],
    # Hegel's RC-5 code tables, one page each (Excel printed to PDF; the newer three add each code's Pronto hex), on its support
    # site. The site hands a file out by its number and ignores the rest of the address, so the name after the number is ours.
    "hegel": [
        (name, f"https://support.hegel.com/component/jdownloads/download/{number}-{slug}", None) for name, number, slug in (
            ("Hegel_Rost_IR_Codes.pdf", 63, "hegel-rost-ir-codes"), ("Hegel_H190_IR_Codes.pdf", 65, "hegel-h190-ir-codes"),
            ("Hegel_H360_IR_Codes.pdf", 66, "hegel-h360-ir-codes"), ("Hegel_H590_IR_Codes.pdf", 67, "hegel-h590-ir-codes"),
            ("Hegel_H90_IR_Codes.pdf", 68, "hegel-h90-ir-codes"), ("Hegel_HD12_IR_Codes.pdf", 69, "hegel-hd12-ir-codes"),
            ("Hegel_HD25_IR_Codes.pdf", 70, "hegel-hd25-ir-codes"), ("Hegel_HD30_IR_Codes.pdf", 72, "hegel-hd30-ir-codes"),
            ("Hegel_Mohican_IR_Codes.pdf", 73, "hegel-mohican-ir-codes"), ("Hegel_H390_H590_IR_codes_v1.pdf", 85, "h390-h590-ir-codes"),
            ("Hegel_H95_H120_H190_IR_codes_v2.pdf", 86, "h95-h120-h190-ir-codes"), ("Hegel_P20_P30_IR_codes_v2.pdf", 87, "p20-p30-ir-codes"))
    ],
    # Simaudio's "Table of IR codes" (Technical Notice, Rev. 1.1, December 2002): the RC-5 and RC-6 codes of its Moon preamplifiers,
    # integrated amplifiers, CD and DVD players, three pages with no copyright or rights text, on its own host.
    "simaudio": [
        ("Simaudio-RC-5-IR-codes-rev1.1.pdf", "https://simaudio.com/wp-content/uploads/2019/11/Simaudio-RC-5-IR-codes-rev1.1.pdf", None),
    ],
    # Cary Audio's "DMS IR Remote Codes" (one page, Word to PDF, 2 November 2018): the RC-5 codes of its DMS network music players, a table
    # of 38 keys with two system codes, no copyright or rights text, on its own host.
    "cary": [
        ("DMS-IR-Remote-Codes.pdf", "https://www.caryaudio.com/wp-content/uploads/2018/11/DMS-IR-Remote-Codes.pdf", None),
    ],
    # NAD's "IR Commands by Model" pages for its T-series receivers and processors, 3 pages each, no copyright or rights text: the members
    # of two zips of control documents on its site (the zips also hold RS-232 command lists and a test program, which are not read). The
    # T775 has two, of 5 February and 24 March 2008 (DESIGN section 72).
    "nad": [
        ("T175_IR_Commands.pdf", NAD + "NAD_TXX5_Control_Docs.zip", "T175_IR_Commands.pdf"),
        ("T765_IR_Commands.pdf", NAD + "NAD_TXX5_Control_Docs.zip", "T765_IR_Commands.pdf"),
        ("T775_IR_Commands_2008-02.pdf", NAD + "NAD_TXX5_Control_Docs.zip", "T775 IR Commands.pdf"),
        ("T775_IR_Commands.pdf", NAD + "NAD_TXX5_Control_Docs.zip", "T775_IR_Commands.pdf"),
        ("T785_IR_Commands.pdf", NAD + "NAD_TXX5_Control_Docs.zip", "T785_IR_Commands.pdf"),
        ("T187_IR_Commands.pdf", NAD + "NAD_TXX7_Control_Docs.zip", "T187_IR_Commands.pdf"),
        ("T777_IR_Commands.pdf", NAD + "NAD_TXX7_Control_Docs.zip", "T777_IR_Commands.pdf"),
        ("T787_IR_Commands.pdf", NAD + "NAD_TXX7_Control_Docs.zip", "T787_IR_Commands.pdf"),
        # the 16 Crestron IR driver files of January 2005 for its older products and the readme that says which file is for which model
        ("crestron_readme.pdf", NAD + "NAD-CrestronIR.zip", "readme.pdf"),
        ("crestron_cd1.ir", NAD + "NAD-CrestronIR.zip", "cd1.ir"),
        ("crestron_cd2.ir", NAD + "NAD-CrestronIR.zip", "cd2.ir"),
        ("crestron_cd3.ir", NAD + "NAD-CrestronIR.zip", "cd3.ir"),
        ("crestron_cd4.ir", NAD + "NAD-CrestronIR.zip", "cd4.ir"),
        ("crestron_cd5.ir", NAD + "NAD-CrestronIR.zip", "cd5.ir"),
        ("crestron_dvd1.ir", NAD + "NAD-CrestronIR.zip", "dvd1.ir"),
        ("crestron_dvd2.ir", NAD + "NAD-CrestronIR.zip", "dvd2.ir"),
        ("crestron_receiver1.ir", NAD + "NAD-CrestronIR.zip", "receiver1.ir"),
        ("crestron_receiver2_1.ir", NAD + "NAD-CrestronIR.zip", "receiver2_1.ir"),
        ("crestron_receiver2_2.ir", NAD + "NAD-CrestronIR.zip", "receiver2_2.ir"),
        ("crestron_receiver3.ir", NAD + "NAD-CrestronIR.zip", "receiver3.ir"),
        ("crestron_receiver4.ir", NAD + "NAD-CrestronIR.zip", "receiver4.ir"),
        ("crestron_tape.ir", NAD + "NAD-CrestronIR.zip", "tape.ir"),
        ("crestron_tuner.ir", NAD + "NAD-CrestronIR.zip", "tuner.ir"),
        ("crestron_tv1.ir", NAD + "NAD-CrestronIR.zip", "tv1.ir"),
        ("crestron_tv2.ir", NAD + "NAD-CrestronIR.zip", "tv2.ir"),
    ],
    # Rotel's IR Codes page (a table of 59 models, each with a link to a PDF of its Pronto hex codes) and the 52 PDFs it links, on Rotel's
    # own host, rotel.com. The page is kept as the evidence of which document is for which model (DESIGN section 69).
    "rotel": [
        ("Rotel_IR_Codes.html", "https://www.rotel.com/manuals-resources/ir-codes", None),
        ("P5-X5_HEX.PDF",
         "https://www.rotel.com/sites/default/files/product/ir/P5-X5%20HEX.PDF", None),
        ("P5-P5S2-X5-X5S2_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/P5-P5S2-X5-X5S2%20HEX.pdf", None),
        ("Q5_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/Q5%20HEX.pdf", None),
        ("X3_HEX.PDF",
         "https://www.rotel.com/sites/default/files/product/ir/X3%20HEX.PDF", None),
        ("X3-X3S2_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/X3-X3S2%20HEX.pdf", None),
        ("X430_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/X430%20HEX.pdf", None),
        ("P5-X5-X5S2_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/P5-X5-X5S2%20HEX.pdf", None),
        ("CD11_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/CD11%20HEX.pdf", None),
        ("CD11-CD11MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/CD11-CD11MKII%20HEX.pdf", None),
        ("CD14_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/CD14%20HEX.pdf", None),
        ("CD14andCD14MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/CD14%26CD14MKII%20HEX.pdf", None),
        ("RCD06SE_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RCD06SE%20HEX.pdf", None),
        ("RCD12_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RCD12%20HEX.pdf", None),
        ("RCD1520_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RCD1520%20HEX.pdf", None),
        ("RCD1570_HEX_0.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RCD1570%20HEX_0.pdf", None),
        ("RCD1572_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RCD1572%20HEX.pdf", None),
        ("RCD1572-RCD1572MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RCD1572-RCD1572MKII%20HEX.pdf", None),
        ("DX3-DX5_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/DX3-DX5%20HEX.pdf", None),
        ("A10MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/A10MKII%20HEX.pdf", None),
        ("A11_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/A11%20HEX.pdf", None),
        ("A11-A11MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/A11-A11MKII%20HEX.pdf", None),
        ("A12-A14_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/A12-A14%20HEX.pdf", None),
        ("A12-A14-A12MKII-A14MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/A12-A14-A12MKII-A14MKII%20HEX.pdf", None),
        ("A10MKIIandA8_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/A10MKII%26A8%20HEX.pdf", None),
        ("RA05SE_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RA05SE%20HEX.pdf", None),
        ("RA12_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RA12%20HEX.pdf", None),
        ("RA1520_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RA1520%20HEX.pdf", None),
        ("RA1570_HEX_0.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RA1570%20HEX_0.pdf", None),
        ("RA1572_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RA1572%20HEX.pdf", None),
        ("RA1572-RA1572MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RA1572-RA1572MKII%20HEX.pdf", None),
        ("RA1592-RA1592MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RA1592-RA1592MKII%20HEX.pdf", None),
        ("S14_RAS-5000_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/S14%20RAS-5000%20HEX.pdf", None),
        ("RC1550_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RC1550%20HEX.pdf", None),
        ("RC1570_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RC1570%20HEX.pdf", None),
        ("RC1572_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RC1572%20HEX.pdf", None),
        ("RC1572MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RC1572MKII%20HEX.pdf", None),
        ("RC1580_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RC1580%20HEX.pdf", None),
        ("RC1590_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RC1590%20HEX.pdf", None),
        ("RC1590-RC1590MKII_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RC1590-RC1590MKII%20HEX.pdf", None),
        ("RDD1580_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RDD1580%20HEX.pdf", None),
        ("RCX1500_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RCX1500%20HEX.pdf", None),
        ("RAP1580_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RAP1580%20HEX.pdf", None),
        ("RSP1570_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RSP1570%20HEX.pdf", None),
        ("RSP1572_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RSP1572%20HEX.pdf", None),
        ("RSP1576_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RSP1576%20HEX.pdf", None),
        ("RSP1582_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RSP1582%20HEX.pdf", None),
        ("RSX1560_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RSX1560%20HEX.pdf", None),
        ("RSX1562_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RSX1562%20HEX.pdf", None),
        ("RDG1520_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RDG1520%20HEX.pdf", None),
        ("RT11_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RT11%20HEX.pdf", None),
        ("RT12_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/RT12%20HEX.pdf", None),
        ("T14_HEX.pdf",
         "https://www.rotel.com/sites/default/files/product/ir/T14%20HEX.pdf", None),
    ],
    "anthem": [
        ("AVM-MRXx40-IR-hex-20251202185749500.xlsx",
         "https://storage.googleapis.com/sandbox1-anthemav/an/AVM-MRXx40-IR-hex-20251202185749500.xlsx", None),
    ],
}


def fetch(url: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(urllib.parse.quote(url, safe=":/%?=&"), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read(), {k.lower(): v for k, v in response.headers.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("maker", choices=sorted(DOCUMENTS))
    parser.add_argument("--date", default=date.today().isoformat(), help="the retrieval date to record")
    parser.add_argument("--root", default="sources/official")
    args = parser.parse_args(argv)
    target = Path(args.root) / args.maker
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    manifest.update({"maker": args.maker, "retrieved": args.date})
    manifest.setdefault("documents", {})
    manifest.setdefault("sheets", {})
    for name, url, member in DOCUMENTS[args.maker]:
        time.sleep(1.0)
        data, headers = fetch(url)
        if member is not None:
            data = zipfile.ZipFile(__import__("io").BytesIO(data)).read(member)
        if name.endswith(".xls") and not data.startswith(b"\xd0\xcf\x11\xe0"):
            raise SystemExit(f"{name}: {url} did not answer with a spreadsheet ({data[:40]!r}); nothing was pinned")
        if name.endswith(".html") and b"<html" not in data[:2000].lower() and b"<!doctype" not in data[:200].lower():
            raise SystemExit(f"{name}: {url} did not answer with a web page ({data[:40]!r}); nothing was pinned")
        if name.endswith(".ir") and not data.startswith(b"\x04\xf0IR"):
            raise SystemExit(f"{name}: {url} did not answer with a Crestron IR file ({data[:40]!r}); nothing was pinned")
        if name.endswith(".pdf") and not data.startswith(b"%PDF"):
            raise SystemExit(f"{name}: {url} did not answer with a PDF ({data[:40]!r}); nothing was pinned")
        (target / name).write_bytes(data)
        entry = {"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if member is not None:
            entry["member"] = member          # the file of the zip at ``url`` that is kept
        archived = WAYBACK.match(url)
        if archived:
            # a copy from the Wayback Machine: the address it was archived from and when, and what the origin said
            entry["original"], entry["snapshot"] = archived["original"], archived["snapshot"]
        if "last-modified" in headers:
            entry["lastModified"] = headers["last-modified"]
        elif "x-archive-orig-last-modified" in headers:
            entry["lastModified"] = headers["x-archive-orig-last-modified"]
        manifest["documents"][name] = entry
        print(f"{name}: {len(data):,} bytes", file=sys.stderr)
    manifest["documents"] = dict(sorted(manifest["documents"].items()))
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
