# TODO

Project status as of 2026-10-08.

**Where things stand.** v1 (DESIGN §8 Phases 0–6) is complete. Six
sources have been imported under SPEC R19:

- **LIRC**: 3,138 remotes, 112,789 keys (DESIGN §14).
- **SmartIR**: `media_player` and `fan` only, 62 remotes, 914 keys
  (DESIGN §15).
- **IR Blaster**, as shipped in SwiftRemote: 10,013 remote files, 411,265
  keys, GPL-3.0 by inheritance only (DESIGN §17, §18).
- **hifi-remote.com's Sony code pages**: 151 files, 6,772 keys, a table of
  reference codes with no licence recorded (DESIGN §27).
- **The JP1 device upgrades** of its forum: 2,566 files, 99,929 keys, read for
  thirty-eight executors (DESIGN §28, §32, §33, §34, §35, §36).
- **Manufacturers' own IR tables**: Marantz's AV receiver charts, Anthem's IR
  hex sheet and Oppo's remote code workbooks, 189 files, 12,844 keys (DESIGN
  §29, §31).

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
- [ ] **The rest of the JP1 upgrades** (DESIGN D120, D136, D140, D145, D155):
      719 upgrades of 161 other executors (MCE 83, XMP 48, Nokia32 40, GI Cable
      20, Panasonic (old) 19, RC-5/5x Combo 16, TDC 16, RC6-M-20n 15, NEC1-f16
      Official 13, RC5/6 combo 12...), among them 106 `Manual Settings` upgrades
      that give raw executor bytes with no protocol, and 25 upgrades of read
      executors that yield nothing: 18 RCA at 56 kHz, which needs a protocol of
      its own (D152, D18's gate), and 6 Pioneer MIX of variants 4 and 5. Pioneer
      3DEV, 4DEV and DVD (18) need the executors' code read (D155),
      and 2,073 KeymapMaster files. MCE is RC6-6-32 and Nokia32 is its own
      frame: neither is a registered protocol (D18's gate). **The Yamaha combo's Y1 to Y3 styles**
      (1,087 functions of 15 upgrades) send NEC with 16 data bits, which needs
      NEC1-f16 and NEC2-f16 registered with a cited golden vector (D18's gate). Each executor needs its
      translator found (the RemoteMaster source has the classes the
      `protocols.ini` expressions name) and checked against the ledger's codes
      first, as RC-5 was. The forum's file section holds about
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
- [ ] **The other makers' tables**: Arcam (RC-5 tables in PDFs), KEF (NEC, one
      page), T+A (about 150 Pronto codes), JVC (the projectors' long hex, 37
      pages) and Cambridge Audio (PDFs through the Wayback Machine). Each is a
      document pinned by hash as D123 does it, and a PDF needs a reader of its
      own. Kaleidescape's PDF says "All rights reserved": a source of that
      kind goes to the private repository (D130).
- [ ] **Marantz's direct-access sheets** (DESIGN D126): volume level, preset
      and channel commands are RC-5 with an extension byte, and so are 1,034
      rows of the charts the import counts and does not use. They need an
      RC-5x protocol, which needs D18's gate: an independently cited golden
      vector.
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

## 6. Matching

- [ ] **A model whose name repeats its brand hides the exact model typed after
      it** (DESIGN D99, D150). `PHILIPS BDP 3390` finds `Philips BDP3300`, a JP1
      model, and not the catalog's `BDP 3390`: the whole text is one edit from
      the JP1 model's key and rule 4 of D96 then does not try the run inside it.
      Given as brand and model, or without the brand, the exact model comes
      first. Of the 2,351 models that only JP1 has, 2,061 begin with their
      brand, and so do all 151 of hifi-remote.com's and all 96 of the
      manufacturers' (the IR Blaster's: 1,580 of 271,250), so this grows with
      each import of that kind. A change to the matcher changes its
      vectors (D97): its own pull request.

## Not planned

Capturing from hardware (SPEC §4), unmodulated signals (D1a), and a
contribution workflow (OD1).
