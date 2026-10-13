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
  projector guide, Arcam's custom installation notes, two of Cambridge Audio's lists
  of NEC codes, Hegel's RC-5 tables, Simaudio's table of IR codes and Rotel's IR hex code PDFs, 476 files, 43,699
  keys (DESIGN §29, §31, §58, §60, §62, §63, §64, §65, §66, §68, §69; Linn's RC-5 table moved to the private repository, §70).

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
      not meet R19.
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
- [ ] **The other makers' tables**: Arcam's older products (the AVR300, AV8
      and AV9 have their codes in handbooks, not in installation notes; the
      retailer-hosted copy of the AVR250/390/550/850/860 notes is not read, being
      a third party's). **Cambridge Audio's other fifteen IR code PDFs** (thirteen
      documents: the Azur 851A, C, E, N and W, CXA, CXC, CXN, CXR, Edge, Evo, ONE and the CXU's
      2015 one, on cambridgeaudio.com) say "(c) Copyright Cambridge Audio Ltd" and
      eight of them add "All rights are reserved", so they went to the private repository
      (D130), as T+A's did; the two documents of §65 have no such text. JVC's Interface Specifications page lists
      a 2014 guide (ver 0.44), long hex too, for the DLA-RS49 to RS67 and X500 to X900; the page
      answers 404 and no copy has been found (a search found the 2011 remote control guide v1.4, an earlier edition of the family
      §63 reads, with the same grant, and the 2014 RS-232 specifications, which have no code table; the 2013 models' remote
      codes are in JVC's mobile user guide, 35 codes under "(c) 2013 JVCKENWOOD", so private). **Leads not yet read:** NAD's
      `NAD-CrestronIR.zip` (17 Crestron `.ir` files of 2005, a readme maps them to models; NEC, customer code 877C; the binary
      format is not documented and was not decoded) and its two Pronto `.ccf` files, on nadelectronics.com/software; and **Linn's 22-page
      legacy PDF** (`Linn_RC5_Codes_v0102.pdf`, 2011, the codes of
      all the older products by address, no notice text), which §67 does not read because its table cells run over several lines
      and the text cannot say which line belongs to which code (`0x12` is `Phono 1 Laser Auxiliary 2` with `Auxiliary 2` again
      below the next row): it needs the page images. Each is a document
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
