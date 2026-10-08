# TODO

Project status as of 2026-10-06.

**Where things stand.** v1 (DESIGN §8 Phases 0–6) is complete. Four
sources have been imported under SPEC R19:

- **LIRC**: 3,138 remotes, 112,789 keys (DESIGN §14).
- **SmartIR**: `media_player` and `fan` only, 62 remotes, 914 keys
  (DESIGN §15).
- **IR Blaster**, as shipped in SwiftRemote: 10,013 remote files, 411,265
  keys, GPL-3.0 by inheritance only (DESIGN §17, §18).
- **hifi-remote.com's Sony code pages**: 151 files, 6,772 keys, a table of
  reference codes with no licence recorded (DESIGN §27).
- **The JP1 device upgrades** of its forum: 1,789 files, 67,417 keys, read for
  seven executors (DESIGN §28).

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
- [ ] **The rest of the JP1 upgrades** (DESIGN D120): 1,462 upgrades of other
      executors (RC-5 and its combos, MCE and RC-6, Sony Combo, Panasonic
      Combo, NEC1 Combo, Nokia32, JVC, Aiwa, Denon, Pioneer...) and 2,073
      KeymapMaster files. Each executor needs its translator found and checked
      against the ledger's codes first. The forum's file section holds about
      2,000 more upgrades than the GitHub copy, behind a free login.
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
