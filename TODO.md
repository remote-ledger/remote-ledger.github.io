# TODO

Project status as of 2026-09-26, master at `43f8b7c` (PR #21).

**Where things stand.** v1 (DESIGN §8 Phases 0–6) is complete. Two
databases have been imported under SPEC R19:

- **LIRC**: 3,139 remotes, 112,846 keys (DESIGN §14).
- **SmartIR**: `media_player` and `fan` only, 62 remotes, 914 keys
  (DESIGN §15).

SPEC v0.10 makes growing coverage from every source whose licence permits
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
- [ ] **Re-check the excluded sources periodically**: IRDB, Global Caché
      and Remote Central (SPEC §4). They are excluded by their licences
      alone, so a licence change would admit them.
- [ ] **Factor out a common importer skeleton** once a third importer
      exists. `import_common.py` already holds the probe, compile gate and
      authored-name helpers. The two `Report` classes and the
      `write_import` loops are the obvious next candidates.

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

- [ ] **Sony BDP-BX510 is still in `unresolved.json`.** No source ties the
      RMT-B118P to the BX510, and the subdevice is disputed: 218 in SPEC
      §1's cited hifi-remote.com table (which could not be retrieved)
      against 226 in two independent sources (DESIGN §13).
- [ ] **The BX510's fallback subdevices 234 and 242** have no
      corroboration.
- [ ] **RMT-B118P: 11 of 38 keys** come from a single capture and are
      tiered Plausible. They need a second source.
- [ ] **NECx2 has no published golden vector.** Gate 2b rests on a
      reproducible IrpTransmogrifier render, and `test_registry` warns
      about it on every run.

## 5. Backlog (blocked on D18's three-part gate, none scheduled)

- [ ] Protocols `NEC2`, `NEC` (`S` = `~D`), `Sony12`, `Sony15`, `RC5` and
      `RC6`. Each needs an independently cited golden vector.

## Not planned

Capturing from hardware (SPEC §4), unmodulated signals (D1a), and a
contribution workflow (OD1).
