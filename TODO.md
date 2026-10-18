# TODO

Project status as of 2026-10-12.

**Where things stand.** v1 (DESIGN §8 Phases 0–6) is complete. Six
sources have been imported under SPEC R19:

- **LIRC**: 3,138 remotes, 112,789 keys (DESIGN §14).
- **SmartIR**: `media_player` and `fan` only, 62 remotes, 914 keys
  (DESIGN §15).
- **IR Blaster**, as shipped in SwiftRemote: 10,084 remote files, 412,775
  keys, GPL-3.0 by inheritance only (DESIGN §17, §18, §38).
- **hifi-remote.com's Sony code pages**: 151 files, 6,772 keys, a table of
  reference codes with no licence recorded (DESIGN §27).
- **The JP1 device upgrades** of its forum: 2,938 files, 116,042 keys, read for
  fifty-eight executors (DESIGN §28, §32, §33, §34, §35, §36, §39, §42, §44, §46, §48, §49, §51, §53, §56, §59, §61).
- **Manufacturers' own IR tables**: Marantz's AV receiver charts, Anthem's IR
  hex sheet, Oppo's remote code workbooks, KEF's IR code sheets, JVC's
  projector guide, LX-NZ3 and LX-UH1 sheets and 2018 and LEO3 code tables, BenQ's LK952 code list, Peerless-AV's Neptune TV code sheet, Arcam's custom installation notes, two of Cambridge Audio's lists
  of NEC codes, Hegel's RC-5 tables, Cary Audio's DMS table, InFocus's projector IR code PDFs, Lyngdorf's NEC code table, Emotiva's Pronto hex PDFs, Denon's AVR-4311 remote code list, master IR hex spreadsheet, X-series remote code lists of 2013 to 2020 and 24 per-model remote code lists of 2003 to 2012, Hitachi America's TV remote IR codes, Classé's IR code lists and HEX PDFs, Cyrus Audio's RC-5 tables, Yamaha's standard and extended IR code tables, JBL Synthesis's and Lexicon's RC-5 code sets, NAD's T-series IR tables and Crestron IR files, Simaudio's table of IR codes and Rotel's IR hex code PDFs, 940 files, 88,794
  keys (DESIGN §29, §31, §58, §60, §62, §63, §64, §65, §66, §68, §69, §71, §72, §73, §74, §75, §76, §77, §78, §79, §80, §81, §82, §83, §84, §85, §86, §87, §88, §89, §90, §91, §92, §93, §94, §95; Linn's RC-5 table moved to the private repository, §70).

SPEC v0.11 makes growing coverage from every source whose licence permits
it the standing goal, with no fixed list of sources (DESIGN §8, Phase 8).

---

## 1. New sources (Phase 8)

Each new source needs: a licence check (R19.1), a DESIGN section, an
importer, a registered `remotes/<source>/` entry in
[paths.py](src/remote_ledger/paths.py), and a committed `IMPORT.md` report.
Land it in two PRs, code and then data.

- [ ] **Survey candidate sources.** Confirm each licence before any code.
      Candidates include Flipper-IRDB's files from after its CC0 cutoff and
      IrScrutinizer / JP1 exports. Record for each one why it does or does
      not meet R19. **Flipper-IRDB surveyed 2026-10-18** (`Lucaslhm/Flipper-IRDB`, 8,901 `.ir`
      files, GitHub's licence API says CC0-1.0): the `LICENSE` and the README's "By submitting you
      agree to license your work under CC0" were added on 2025-08-07 (PR #960), so **only a file
      added after that is CC0** (the earlier ones have no licence of their own, which is the
      exclusion of SPEC §4). **85 files were added after it; 3 are earlier files moved (the same
      blob), so 82 qualify**, with 1,972 signals: NEC 524, raw 391, NECext 336, SIRC15 155, SIRC20 145,
      RC5 132, Samsung32 110, Kaseikyo 96, SIRC 30, Pioneer 26, RC6 14, RC5X 13 (23 files are raw only:
      air conditioners, fans). Many of the devices are not in the ledger (Fosi Audio ZD3, Wanbo T6
      Max, Sony RM-PJ30, Canon LV-X320, Roksan K3...). **Not done: it needs a registered import root of
      its own, and Talismote's `BundleVectorsTest` pins the set of sources (`authored`, `lirc`,
      `smartir`, `irblaster`, `hifi-remote`, `jp1`, `official`), so a new source is a change in
      both repositories: the owner's call.** The importer would read the checkout at a pinned commit,
      take the files whose first commit is after the cutoff and whose blob is not in the history
      before it, map NEC, NECext, Samsung32, SIRC, RC5, RC5X, RC6, Kaseikyo and Pioneer to the
      ledger's protocols and keep the raw signals as raw forms (as SmartIR's).
- [ ] **Decide the excluded sources**: IRDB, Global Caché and Remote
      Central (SPEC §4). They are excluded by their licences alone, and the
      owner's decision of 2026-10-08 (D110) is that a table of reference
      remote codes needs no licence. That reasoning bears on all three; none
      has been changed.
- [ ] **The rest of the JP1 upgrades** (DESIGN D120, D136, D140, D145, D155, D192):
      392 upgrades of 141 other executors, among them 106 `Manual Settings` upgrades that
      give raw executor bytes with no protocol, and 30 upgrades of read executors that
      yield nothing (`IMPORT.md` lists them: files with no brand, 6 Pioneer MIX
      of variants 4 and 5, `RCA-38 Official`...), and there are 2,073 KeymapMaster
      files. Each executor needs its translator found (the RemoteMaster source
      has the classes the `protocols.ini` expressions name), a protocol
      registered through D18's gate where the ledger has none (the RC-5
      extensions are a candidate), and the rule checked against another project's data first, as RC-5 and, last, RCA,
      TDC, GI Cable and RC6-M-20n were (§46, §48). The forum's file section holds about
      2,000 more upgrades than the GitHub copy, behind a free login.
- [ ] **Data whose licence is unclear goes to the private repository**, not
      here (DESIGN D130). RemoteCentral's Infrared Hex Code Database, learned
      codes with every key Untested, is its first resident. When a licence is
      cleared a source moves here by its own pull request: its `remotes/` and
      `sources/` directories, its entries in `paths.IMPORTS`, the bundle's
      `SOURCES` and the notices' `FACTS`, and its removal from the private
      repository.
- [ ] **Data of other makers or sites whose licence is unclear**: import it into
      the private repository first, so that it is never in this one's history.
- [ ] **InFocus's remaining documents** (§75 to §77 read 36 of the 43 distinct PDFs the Wayback Machine holds): the LP800's and LP810's two-digit codes (no protocol or customer code stated, and no other source with those codes), two Mondopad PDFs that carry the same 22 rows as the one read, the LP500/LP530's PDF (the archive has only the site's "page does not exist"), **the IN80's (read into the private repository, `manufacturers`) and the JTouch's (©2014 ... All rights reserved; its 13 keys have no customer code)**.
- [ ] **The other makers' tables**: Arcam's older products (§74 read the two IR sheets and eight more notes, and Arcam's own copy of the
      AVR250/390/550/850/860 notes; the AVR300, AV8 and AV9 have their codes in handbooks, not in installation notes; the Radia Remote documents
      of 2025 say "© 2025 HARMAN ... All rights reserved" and are for the private repository; the Solo Movie 2.1 and 5.1's
      2008 notes (SH194) are read in §78). **Cambridge Audio's other fifteen IR code PDFs** (thirteen
      documents: the Azur 851A, C, E, N and W, CXA, CXC, CXN, CXR, Edge, Evo, ONE and the CXU's
      2015 one, on cambridgeaudio.com) say "(c) Copyright Cambridge Audio Ltd" and
      eight of them add "All rights are reserved", so they went to the private repository
      (D130), as T+A's did; the two documents of §65 have no such text. JVC's Interface Specifications page lists
      a 2014 guide (ver 0.44), long hex too, for the DLA-RS49 to RS67 and X500 to X900; the page
      answers 404 and no copy has been found (a search found the 2011 remote control guide v1.4, an earlier edition of the family
      §63 reads, with the same grant, and the 2014 RS-232 specifications, which have no code table; the 2013 models' remote
      codes are in JVC's mobile user guide, 35 codes under "(c) 2013 JVCKENWOOD", so private). **Leads closed on 2026-10-14:** NAD's two Pronto `.ccf` files (`NADRCVRandDVD.ccf`, `NAD_C420.ccf`) keep their Pronto words in the clear,
      and the NAD codes in them (29 NEC codes of the C420 tuner; 62 at 0x87/0x7C and 0x85/0x9A in the other) **are all in the ledger
      already** (§72, §73), so there is nothing to import; Linn's 22-page legacy PDF is **read into the private repository** (§70: private
      pull request #6, its `docs/linn.md` LN-4 and LN-5); **Emotiva's 2006 "Emotiva Pronto codes revE2"** (a Sunfire Corporation document: 153 commands of a
      12-bit code with its complement and no NEC, which §80 does not read: the ledger has no protocol for it, and as Pronto-only files its names and
      numbers, which are in the text apart from the strings, would have to be paired by place); **Denon's AVR-4311CI/AVR-4311 list** (§81) leaves its Kaseikyo tables for remote IDs 2 to 4 and the iPod,
      Sirius and HD Radio ones (the document writes their ID and parity bits as `*`; for ID 1 the document's own notes give them, so they could be derived: not done); **Classé's `CP-800 IR codes Rev1`** (§83; a spreadsheet page whose one system heading `016(019)` covers every row, where the HEX PDF of the same product has Mute and Volume under system 200: not read, the HEX PDF has the product), **Denon's other documents on `assets.denon.com`** (§84 reads its master spreadsheet of 2022, §94 24 of the archive's about 75 per-model IR code PDFs of 2003 to 2013 and §95 two X-series Word lists of 2013 and 2018; the archive also has the 2014 and 2016 versions of the master. **Left of the per-model lists:** the nine of 2008 whose pages say `CONFIDENTIAL` (the AVR-588, 688, 788, 888, 988, 1508, 1708, 1908 and 2308CI: the private repository's, **next, but not with §94's readers**: their 12 pages have another layout, a `Genre` heading over each Sharp table (`AV AMP`, `AVP`, `CDR`, `RCVR`, each with its own `System Address`, so devices other than 2, 4 and 12) with the key's name in a `FUNCTION` column, and a Kaseikyo section of a few tables at the end, so the reader is a new one), the one-page zone sheets (full 48-bit rows with a `Function` column), and the tables for remote IDs 2 to 4 and 1 to 4 of the lists that are read, which write their ID and parity bits as `*`), **Cyrus's DAB 8 and DVD 8 tables** (§85: they state no system code, so a key's device is unknown; the Cyrus 3 and 5's `Up`/`Down` and similar codes of its second table are read), and Cambridge Audio's support archive (the live driver-updates page answers a
      script with 403) has nothing left: the Wayback Machine's copy of that page of 6 June 2026 lists exactly the IR PDFs already read
      (the 15 files of 13 documents in the private repository and the two public ones of §65), and its other files are serial and RS-232
      protocols, not IR codes. **Leads of 2026-10-16:** SnapAV's `CD-C600_IR_Commands` (one page of Yamaha's CD player, `Copyright© 2009 YAMAHA CORPORATION`: the
      private repository's, not done; the rest of that folder is read in §87 or is no IR table); **Pioneer's two AV receiver IR workbooks** of 2010 and FY15 (`Copyright (c) Pioneer
      Corporation, all rights reserved`: 131 files in the private repository, its pull request #9, awaiting the owner); the Wayback Machine's index answers a narrow path
      prefix (`pioneerelectronics.com/StaticFiles/PUSA/Files/Home%20Custom%20Install/`) where a whole host times out. **Leads of 2026-10-18** (SnapAV's other folders; §90 reads
      Denon's X-series lists of the amplifiers folder and §91 and §92 the projection-screens folder's BenQ and JVC sheets): the image-only `HT5550_IR_Codes.pdf` (no text layer) is left; **§93 reads Peerless-AV's Neptune sheet; looked at and not read:** Digital Projection's `VIP 2000 IRcodes` (Wayback Machine, 2006: 41 hex frames in an unknown bit order: the digits read naturally bit-reversed and the picture formats directly, and no other source has the custom code), Eiki's `LC-WXN200`/`EK-308U` sheets (live on eiki.com: Sanyo LC7461 format, which the registry has no protocol for: adding one is D18's three gates), the Binary B-660/B-200/B-500 matrix IR hex sheets (SnapAV's MediaDistribution folder: routing codes, niche), Episode's `EA-MINI-xD-35_IRHex_Protocol` (`(c) 2013 Episode`: the private repository's) and Denon's per-model PDFs of 2012 and 2013 on `assets.denon.com` (**read in §94**: their text has the Kaseikyo rows' ten data bits as well as the identifiers, and 27 rows' identifiers and bits disagree; the Sharp rows' six bits are in the 2011 lists only). A document is
      pinned by hash as D123 does it; a PDF is read as the lines of text of its
      pages (KEF's was the first, D243, JVC's the second, D247), which suits a
      table and not prose. Kaleidescape's PDF says "All rights reserved": a source
      of that kind goes to the private repository (D130).
- [ ] **Marantz's direct-access sheets** (DESIGN D126): volume level, preset
      and channel commands are RC-5 with an extension byte, and so are 1,034
      rows of the charts the import counts and does not use. `RC5x` is
      registered now (§57, D226 to D228) and the 1,013 rows with a number for an
      extension are read (§58, D229 to D231). **Still unread: the 18 rows whose extension is a
      range**, with the 3 `-∞ dB` rows of the 2015 workbook's direct sheet (§60: the five direct-access sheets are read now). The
      16 JP1 `RC-5/5x Combo` upgrades are the other reading `RC5x` opened (§59).
- [ ] **Receiver commands in the key vocabulary** (D84, owner's call): the
      official import maps 16.9% of its keys, because tuner presets, surround
      modes, Pure Direct, discrete mute on and off, and a source key per input
      are not among its 156 keys (DESIGN §22).
- [ ] **Anthem inputs 21 to 30** (DESIGN D125): the sheet's Pronto hex and its
      data column disagree for 20 rows, and the 80 keys are Untested. Anthem,
      or a receiver, would say which is right.
- [ ] **The One For All code lists** of hifi-remote.com are setup codes, not IR
      codes, and are not imported.
- [ ] **Factor out a common importer skeleton** now that a third importer
      exists. `import_common.py` already holds the probe, compile gate and
      authored-name helpers. The SmartIR and IR Blaster `Report` classes
      and their `write_import` loops are the obvious next candidates.

## 2. SmartIR follow-ups

- [ ] **Infer the carrier for recognised timing families** (DESIGN §15,
      "What the 38 kHz default costs"). Nothing is dropped today, but about
      129 raw keys (RC5/RC6 at 36 kHz, Sony SIRC at 40 kHz) compile at
      38 kHz and lose range. Record an inferred carrier as a cited `claims`
      entry. This goes further than D41's "no protocol detection", so write
      the decision down first.
- [ ] **Stop dropping keys from Pronto profiles that mix carriers.** Each
      file takes its carrier from the first command, and a later command
      whose word disagrees fails to compile. No such case exists today
      (0 of 10 Pronto keys), but it is the one remaining path where a
      carrier costs a key. Options: split the profile into one file per
      carrier, or allow a per-form carrier on `pronto` forms.
- [ ] **R19.4 and `controls`** (DESIGN §15, open point). SmartIR's model
      names are synthetic, so the authored-wins check almost never fires.
      The two sides only agree in `controls`. Decide whether R19.4
      compares `controls`, and how the index resolves two remotes that
      claim the same device.
- [ ] **Widen the scope**: the `climate` (357 files) and `light` (5) state
      matrices, and the ESPHome controller (4 files).
- [ ] **Look at the 62 skipped buttons**: packet types `0x78`, `0x79`,
      `0xb1` and `0xb2`, and malformed base64. Identify the other packet
      types before deciding to decode them.

## 3. LIRC follow-ups (optional)

From [remotes/lirc/IMPORT.md](remotes/lirc/IMPORT.md). These are all
deliberate skips, each reported with its reason.

- [ ] **34 blocks skipped because `min_repeat` exceeds the schema's
      `minSends` maximum of 10** (values from 10 up to 416). Decide whether
      to raise the bound; that would be a SPEC R3 edit.
- [ ] **237 buttons that hold several codes** and **642 duplicate names**.
      Candidate groups (D16) might represent some of them.
- [ ] 197 scancode-only remotes and 5 GRUNDIG/BO/SERIAL blocks cannot be
      represented. No action unless the scope changes.

## 4. Open evidence questions (authored data)

- [ ] **Sony BDP-BX510 is still in `unresolved.json`.** Its remote is
      RMT-B119A according to search summaries of retailer pages, and no
      source has codes or a key list for that remote. The subdevice is
      settled as 226 (DESIGN §13); the table cited for 218 was found, and it
      lists no 218.
- [ ] **The Blu-ray command modes 234 and 242** are on hifi-remote.com's
      table and in the import, but not as variants of RMT-B118P: the catalog
      bundle carries only a key's primary group (DESIGN §13, §23).
- [ ] **NECx2 has no published golden vector.** Gate 2b rests on a
      reproducible IrpTransmogrifier render, and `test_registry` warns
      about it on every run.

## 5. Backlog (blocked on D18's three-part gate, none scheduled)

- [ ] Protocol `NEC` (`S` = `~D`). It needs an independently cited golden
      vector. `NEC2`, `Sony12`, `Sony15`, `RC5` and `RC6` have since landed
      (DESIGN §16, §18).

## Not planned

Capturing from hardware (SPEC §4), unmodulated signals (D1a), and a
contribution workflow (OD1).
