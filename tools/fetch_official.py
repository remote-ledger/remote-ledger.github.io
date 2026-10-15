#!/usr/bin/env python3
"""Fetch the IR documents a manufacturer publishes into ``sources/official/<maker>/`` (DESIGN D123).

The documents are what ``rl import official`` is pinned to: a manufacturer's page is not a
repository, so the pin is the file as the server sent it, and ``MANIFEST.json`` records its URL,
size, SHA-256, the retrieval date (today: do not pass --date) and the server's ``Last-Modified``
(and, for a copy from the Wayback Machine, the address and snapshot it was archived from). Run it, then
``tools/official_sheets_to_csv.py <maker>`` for the spreadsheets, review the diff, and import.

    python3 tools/fetch_official.py marantz|anthem|oppo|kef|jvc|arcam|infocus|cambridge|hegel|cary|nad|simaudio|rotel
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
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
        # the older products' sheets (DESIGN section 74): two 3-page sheets, issue B (PDFs made on 6 September 2006), on the product pages of the
        # A18, A28 and A38 (amplifiers) and of the CD17 and CD37 (CD players); their covers list the models they cover
        ("A18-A28-A38_amps_rc.pdf", "https://www.arcam.co.uk/ugc/tor/a18/IR%20Codes/amps_rc.pdf", None),
        ("CD17-CD37_cd_rc.pdf", "https://www.arcam.co.uk/ugc/tor/cd17/IR%20Codes/cd_rc.pdf", None),
        # the older products' serial control notes, which end with the RC-5 codes (DESIGN section 74)
        ("AVR500-600-AV888_RS232_SH217_E_7.pdf", "https://www.arcam.co.uk/ugc/tor/av888/RS232/AVR600_RS232_SH217_E_7.0.pdf", None),
        ("AVR400_RS232_SH235_E_1.2.pdf", "https://www.arcam.co.uk/ugc/tor/avr400/RS232/AVR400_RS232_SH235_E_1.2.pdf", None),
        ("AVR390-550-850-AV860-SR250_RS232_SH274E_D.pdf",
         "https://www.arcam.co.uk/ugc/tor/av860/RS232/RS232_860_850_550_390_250_SH274E_D_181018.pdf", None),
        ("BDP100-BDP300_RS232_SH231_E_2.pdf", "https://www.arcam.co.uk/ugc/tor/bdp100/RS232/BDP100_BDP300_RS232_SH231_E_2.pdf", None),
        ("CDS27_RS232_SH272_E_1.pdf", "https://www.arcam.co.uk/ugc/tor/cds27/RS232/CDS27_RS232_SH272_E_1_070814.pdf", None),
        ("D33_RS232_SH236_E_D.pdf", "https://www.arcam.co.uk/ugc/tor/d33/RS232/D33_RS232_SH236_E_D.0.pdf", None),
        ("ST60_RS232_SH309_C.pdf", "https://www.arcam.co.uk/ugc/tor/ST60/Custom%20Installation%20Notes/SH309_RS232_ST60_C.pdf", None),
        ("Solo_bar_IR_codes_V2.0.pdf", "https://www.arcam.co.uk/ugc/tor/Solobar/IRcodes/Solo_bar_IR_codes_V2.0.pdf", None),
        ("Solo_Movie-Movie_2.1-Music_2_RS232_SH284E_A.pdf",
         "https://www.arcam.co.uk/ugc/tor/solomovie/RS232/SH284E_Solo_Movie_RS232_issue_A_130116.pdf", None),
    ],
    # InFocus's IR code PDFs for its projectors (DESIGN sections 75 to 77): its own site is gone (infocus.com no longer serves them), so these are
    # the Wayback Machine's copies, each the snapshot named in its URL. 36 of the 43 distinct PDFs of 54 addresses found (an `also` names the others that
    # are the same file under another product page); not pinned: the IN80's and the JTouch's (a copyright notice: the private repository has the IN80's),
    # the LP500/LP530's (the archive holds the site's "page does not exist"), the LP800's and LP810's (two-digit codes with no protocol or customer code)
    # and two Mondopad documents that carry the same 22 rows as the one that names the models.
    "infocus": [
        ("540-640-IR_codes.pdf",
         "https://web.archive.org/web/20040212180123id_/http://www.infocus.com:80/service/lp640/downloads/540-640-IR_codes.pdf", None),  # also InFocus-LP540-IRCodes.pdf, InFocus-LP640-IRCodes.pdf
        ("IN2x_IR_Codes.pdf",
         "https://web.archive.org/web/20060508074130id_/http://www.infocus.com/service/in24/downloads/IN2x_IR_Codes.pdf", None),
        ("IN5140_IRCodes.pdf",
         "https://web.archive.org/web/20140705063913id_/http://infocus.com/resources/documents/IN5140/IN5140_IRCodes.pdf", None),
        ("IN5148HD-IR-Codes.pdf",
         "https://web.archive.org/web/20170506020520id_/http://www.infocus.com:80/resources/documents/IN5148HD-IR-Codes.pdf", None),
        ("IN7x_IR_Codes.pdf",
         "https://web.archive.org/web/20060312091902id_/http://www.infocus.com/service/IN72/downloads/IN7x_IR_Codes.pdf", None),  # also InFocus-IN70-IRCodes.pdf
        ("InFocus-IN100-IRCodes.pdf",
         "https://web.archive.org/web/20140617004308id_/http://infocus.com:80/resources/documents/IN100/InFocus-IN100-IRCodes.pdf", None),
        ("InFocus-IN1100-IRCodes.pdf",
         "https://web.archive.org/web/20140705062522id_/http://infocus.com/resources/documents/IN1100/InFocus-IN1100-IRCodes.pdf", None),
        ("InFocus-IN20-IRCodes.pdf",
         "https://web.archive.org/web/20140705034713id_/http://infocus.com/resources/documents/IN20/InFocus-IN20-IRCodes.pdf", None),
        ("InFocus-IN2100-IRCodes.pdf",
         "https://web.archive.org/web/20140705022822id_/http://infocus.com/resources/documents/IN2100/InFocus-IN2100-IRCodes.pdf", None),
        ("InFocus-IN3100-IRCodes.pdf",
         "https://web.archive.org/web/20140705014410id_/http://infocus.com/resources/documents/IN3100/InFocus-IN3100-IRCodes.pdf", None),
        ("InFocus-IN32-IRcodes.pdf",
         "https://web.archive.org/web/20140705042859id_/http://infocus.com/resources/documents/IN30/InFocus-IN32-IRcodes.pdf", None),  # also InFocus-IN34-IRcodes.pdf
        ("InFocus-IN110-IRCodes.pdf",
         "https://web.archive.org/web/20140629071636id_/http://www.infocus.com:80/resources/documents/IN110/InFocus-IN110-IRCodes.pdf", None),
        ("InFocus-IN3118HD-IRCodes.pdf",
         "https://web.archive.org/web/20140705033833id_/http://infocus.com/resources/documents/IN3110/InFocus-IN3118HD-IRCodes.pdf", None),
        ("IN3114_IN3116_IRCodes.pdf",
         "https://web.archive.org/web/20131008173450id_/http://www.infocus.com:80/sites/default/files/SupportDocs/InFocus_IN3110_Projector_Series/InFocus_IN3114_Projector/IN3114_IN3116_IRCodes.pdf", None),   # also InFocus-IN2110-IRCodes.pdf, InFocus-IN3110-IRCodes.pdf
        ("InFocus-IN3910-IRCodes.pdf",
         "https://web.archive.org/web/20140705045937id_/http://infocus.com/resources/documents/IN3910/InFocus-IN3910-IRCodes.pdf", None),
        ("IN5550-IR-Codes.pdf",
         "https://web.archive.org/web/20140705081607id_/http://infocus.com/resources/documents/IN5550/IN5550-IR-Codes.pdf", None),
        ("InFocus-IN3900LB-IRCodes.pdf",
         "https://web.archive.org/web/20140705051935id_/http://infocus.com/resources/documents/IN3900/InFocus-IN3900LB-IRCodes.pdf", None),
        ("SP8604_IRCodes.pdf",
         "https://web.archive.org/web/20130616204510id_/http://www.infocus.com/sites/default/files/SupportDocs/Home_Theater_Projectors/InFocus_ScreenPlay_SP8604_Home_Theatre_Projector/SP8604_IRCodes.pdf", None),
        ("InFocus-IN5300-IRCodes.pdf",
         "https://web.archive.org/web/20140705055607id_/http://infocus.com/resources/documents/IN5300/InFocus-IN5300-IRCodes.pdf", None),
        ("InFocus-IN5310-IRCodes.pdf",
         "https://web.archive.org/web/20140705075434id_/http://infocus.com/resources/documents/IN5310/InFocus-IN5310-IRCodes.pdf", None),  # also InFocus-IN5310HD-IRCodes.pdf
        ("InFocus-IN5500-IRCodes.pdf",
         "https://web.archive.org/web/20140705081359id_/http://infocus.com/resources/documents/IN5500/InFocus-IN5500-IRCodes.pdf", None),  # also InFocus-IN5532-IN5534-IRCodes.pdf, InFocus-IN5533-IN5535-IRCodes.pdf
        ("InFocus-LP120-IRCodes.pdf",
         "https://web.archive.org/web/20140705074143id_/http://infocus.com/resources/documents/LP100/InFocus-LP120-IRCodes.pdf", None),
        ("InFocus-LP240-LP250-IRCodes.pdf",
         "https://web.archive.org/web/20140705071014id_/http://infocus.com/resources/documents/LP200/InFocus-LP240-LP250-IRCodes.pdf", None),
        ("InFocus-LP600-IRCodes.pdf",
         "https://web.archive.org/web/20140705063504id_/http://infocus.com/resources/documents/LP600/InFocus-LP600-IRCodes.pdf", None),
        ("InFocus-LP650-IRCodes.pdf",
         "https://web.archive.org/web/20140705075838id_/http://infocus.com/resources/documents/LP600/InFocus-LP650-IRCodes.pdf", None),
        ("InFocus-LP815-IRCodes.pdf",
         "https://web.archive.org/web/20140705082241id_/http://infocus.com/resources/documents/LP800/InFocus-LP815-IRCodes.pdf", None),  # also InFocus-LP820-IRCodes.pdf
        ("InFocus-LP840-IRCodes.pdf",
         "https://web.archive.org/web/20140705055934id_/http://infocus.com/resources/documents/LP800/InFocus-LP840-IRCodes.pdf", None),  # also InFocus-LP850-IRCodes.pdf, InFocus-LP860-IRCodes.pdf
        ("InFocus-LS110-IRCodes.pdf",
         "https://web.archive.org/web/20140705064620id_/http://infocus.com/resources/documents/LS110/InFocus-LS110-IRCodes.pdf", None),
        ("InFocus-SP4800-IRCodes.pdf",
         "https://web.archive.org/web/20140705074151id_/http://infocus.com/resources/documents/ScreenPlay/InFocus-SP4800-IRCodes.pdf", None),
        ("InFocus-SP4805-IRCodes.pdf",
         "https://web.archive.org/web/20140705080741id_/http://infocus.com/resources/documents/ScreenPlay/InFocus-SP4805-IRCodes.pdf", None),
        ("InFocus-SP5000-IRCodes.pdf",
         "https://web.archive.org/web/20140705081124id_/http://infocus.com/resources/documents/ScreenPlay/InFocus-SP5000-IRCodes.pdf", None),
        ("InFocus-SP7200-IRCodes.pdf",
         "https://web.archive.org/web/20140705055827id_/http://infocus.com/resources/documents/ScreenPlay/InFocus-SP7200-IRCodes.pdf", None),
        ("InFocus-SP7205-IRCodes.pdf",
         "https://web.archive.org/web/20140705070148id_/http://infocus.com/resources/documents/ScreenPlay/InFocus-SP7205-IRCodes.pdf", None),
        ("Mondopad_IR_codes.pdf",
         "https://web.archive.org/web/20140705054058id_/http://infocus.com/resources/documents/BigTouch/Mondopad_IR_codes.pdf", None),
        ("X2_IRCodes.pdf",
         "https://web.archive.org/web/20140705065926id_/http://infocus.com/resources/documents/X/X2_IRCodes.pdf", None),
        ("X3_IRCodes.pdf",
         "https://web.archive.org/web/20140705064703id_/http://infocus.com/resources/documents/X/X3_IRCodes.pdf", None),
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
    for attempt in range(5):         # the Wayback Machine answers a script that is too quick with 429 or 5xx: wait and ask again
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read(), {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
        except urllib.error.URLError:
            if attempt == 4:
                raise
        time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("maker", choices=sorted(DOCUMENTS))
    parser.add_argument("--date", default=date.today().isoformat(), help="the retrieval date to record")
    parser.add_argument("--root", default="sources/official")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="fetch only these documents of the maker: the others and the maker's "
                        "retrieval date stay as they are, and each fetched document records its own date")
    args = parser.parse_args(argv)
    target = Path(args.root) / args.maker
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    manifest.update({"maker": args.maker, "retrieved": manifest["retrieved"] if args.only else args.date})
    manifest.setdefault("documents", {})
    manifest.setdefault("sheets", {})
    if args.only and (missing := set(args.only) - {name for name, _, _ in DOCUMENTS[args.maker]}):
        raise SystemExit(f"--only: {sorted(missing)} are not documents of {args.maker}")
    for name, url, member in DOCUMENTS[args.maker]:
        if args.only and name not in args.only:
            continue
        time.sleep(4.0 if WAYBACK.match(url) else 1.0)
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
        if args.only:
            entry["retrieved"] = args.date
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
