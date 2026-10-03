# Philips family: RC5, RC6, RCA_38, Thomson7

> **Update after the import was written (NOTES/import-design.md, D50, D53).** The
> owner chose the wire reading, which is what `FROM_DB_HEX["Thomson7"]` already
> was. `hex_philips.FROM_DB_HEX_APP["Thomson7"]` now holds the app's reading
> (`thomson7_as_the_app_sends` as a hex map); `explain` in the oracle tool uses it.

For the integrator. Nothing here edits DESIGN.md; the items that belong in it
are listed at the end. Line numbers are for this branch.

## What this branch adds

| DB protocol | Ledger protocol | Registered | Hex map | Oracle |
|---|---|---|---|---|
| `RC5` (2,428 codes) | `RC5` | already | `irblaster/hex_philips.py` L51 | 2,428 matched |
| `RC6` (1,237) | `RC6` (new) | `protocols/rc6.py` | L67 | 1,237 matched |
| `RCA_38` (60) | `RCA-38` (new) | `protocols/rca38.py` | L77 | 60 matched |
| `Thomson7` (29) | `Thomson7` (new) | `protocols/thomson7.py` | L85 | 29 **mismatched, all explained** (see below) |

Carriers and minimum sends for the importer (`FROM_DB_HEX`, `MIN_SENDS` are at
hex_philips.py L125 and L137):

| DB protocol | `protocol.carrierHz` | min sends | evidence |
|---|---|---|---|
| `RC5` | 36000 | 1 | the app's `freq` is 36000 for every code and the IRP says 36k |
| `RC6` | 36000 | 1 | same |
| `RCA_38` | 38700 | 1 | the app's frequency is 38700 and the IRP says 38.7k; the registry's nominal is right, so nothing to override |
| `Thomson7` | 33000 | 2 | the IRP says 33k and so does the app. **2 is the app's behaviour, not a hardware fact**: its encoder duplicates the frame in every press (thomson7.dart L100-103). The captured remote shows 5-9 repeats while a key is held (Thomson-0625.exp), so 1 is also defensible |

In every case the registry's `nominal_carrier_hz` is what the oracle compares
at, so the importer can take it without an override.

## The Thomson7 finding: the app does not send what its database means

This is the one disagreement the oracle found, and it is not small.

**What the app does.** `thomson7.dart` L51-105 masks the 12-bit code with
`0xF7F` (L57-58), then sends bits 3..0 (most significant first), its own
toggle, then bits 11..5 (its description, L6-10, says "last4 + toggleBit +
first7").
`thomson7_as_the_app_sends` (hex_philips.py L111) transcribes that, and
`tools/irblaster_oracle_philips.py` `explain` (L133) shows it reproduces the
app's pattern for all 29 codes, so the model of the app is exact. Read as the
IRP's `D:4,T:1,F:7`, the app's frames have thirteen different devices and only
two commands (12 and 76) across the one remote's 29 keys, 20 distinct frames:
nine keys send the same signal as another key. The hexcode's bit 4 never
reaches the air (`300` "1" and `310` "3" are the same frame).

**What the database means.** Read the twelve hexcode bits left to right as the
frame in transmission order (the first bit sent is hexcode bit 11, the toggle's
place is bit 7) and the IRP's least-significant-first order gives
`D = reverse4(hex >> 8)`, `F = reverse7(hex & 0x7F)`. Then:

1. All 29 codes are device 12, one address, 29 distinct commands, which is what
   one remote looks like. The app's reading gives a remote with no address.
2. The app's own mask `0xF7F` clears bit 7, which is the toggle's place in
   exactly this layout and a meaningless bit in the layout the app then uses.
3. **A hardware capture agrees, key for key.** IrpTransmogrifier's test data
   holds an `irscope` capture of a Thomson TV remote (`Thomson-0625.ict`, with
   `.exp` giving its expected decode as Thomson7 despite the file name):
   Vol+ D=12 F=74, Vol- F=42, Mute F=80, Up F=104, Down F=88. SwiftRemote's
   Thomson7 remote (id 800296, "B2B.TEST RCT100 PROMO") has VOL+, VOL-, MUTE,
   J UP, J DOWN as `329`, `32A`, `305`, `30B`, `30D`. Under the reading above
   those are exactly D=12 and F=74, 42, 80, 104, 88. Five of five, on a
   different remote of the same make: that is not chance.
   `tools/philips_capture_audit.py` prints it from the capture and the
   database; `tests/test_irblaster_philips.py` pins it without the capture.

So **the app's `last4 + toggle + first7` is the wrong way round**; the frame is
`first4 + toggle + last7` with the toggle taking bit 7's place. A SwiftRemote
Thomson7 key would not be recognised by the TV the captured remote controls.
It is worth fixing there (db_button_import and `thomson7.dart`, and the
IR-finder search profile at ir_finder_search.dart L285-290 that treats bits 4
and 7 as unimportant, which is the same mistake).

**The decision, and how to reverse it.** The brief says the database's meaning
follows the app. Here the app's own frames contradict the app's own mask and an
independent capture, and the brief also says to find out which is right when
the two disagree. `FROM_DB_HEX["Thomson7"]` therefore gives the capture-backed
reading, the oracle reports 29 mismatches with the stated cause (not a
tolerance), and the exit status stays 0 only because each is explained. If the
owner would rather import what the app sends today, point the dict entry at a
function returning `thomson7_as_the_app_sends`'s `(device, function)` and
refuse codes with bit 4 or bit 7 set (14 of the 29 have bit 4); the oracle then
matches the 15 it can. I did not do that because it would import 29 codes
known not to be the remote's.

**Not proven.** That a real Thomson TV obeys the corrected frames: the capture
is a different remote, and nothing here transmits. The database's remote is a
test entry (`B2B.TEST`), 31 keys, the only Thomson7 in the DB.

**Timings.** The app uses 460 us marks, 2,000 and 4,600 us spaces. The IRP has
500, 2,000, 4,500. The capture sits at marks 0.961 and spaces 1.020 of the IRP
(marks about 480 us, spaces 2,050 and 4,600), so here the app is closer to the
hardware than the IRP. Within the oracle's tolerance either way; the encoder
follows the IRP (hex map and encoder are independent of the app's timing).

## RC5

Hex map: hex_philips.py L51, a transcription of db_button_import.dart L226-235
(and `_readPackedFrameData`, rc5.dart L157-166). Twelve bits: bit 11 is the
second start bit, 10..6 the address, 5..0 the low command bits; the start bit
is turned back into command bit 6 by inversion, i.e. `~F:1:6`. All 4,096
twelve-bit codes are representable, and 2,428 of 2,428 DB codes compare
**exactly**, every duration, at the toggle the app showed.

**Toggle (D3b).** The app's preview shows T=1 for every code (rc5.dart L139-146:
the preview resolves `!_toggleFlag` without consuming it, true for a fresh
state; rc6.dart L132-139 does the same). The ledger
compiles T=0. So the ledger's compiled frame differs from the app's preview in
exactly the toggle bit for all 2,428 codes; the oracle encodes each code at
both toggle states, requires one to match, and reports the count where the
ledger's own T=0 would not. That is the D3b gap, not a mismatch, and nothing
here closes it.

**Endings.** The app and the ledger end identically: a frame ending on a mark
gets the gap appended, one ending on a space has that space lengthened, both to
114,000 us (rc5.dart L93-107, rc5.py L125-138). Both start on S1's mark, with
the leading idle half-bit dropped. No framing allowance is needed.

A published raw sequence IrpTransmogrifier holds for RC5
(`BiphaseDecoderNGTest.java` L22 @ c945e76, decoded D=12 F=3 T=1) is also
reproduced exactly (tests/test_irblaster_philips.py); the existing RC5 index
entry does not cite it.

## RC6

**Which IRPT entry.** The app's frame is the 6,-2 leader, a start bit `1`, mode
bits `000` (the literal `'1000'` at rc6.dart L71), the toggle at double width
(L92: `i == 4`), then sixteen payload bits, biphase with the opposite polarity
to RC5. That is IrpTransmogrifier's `RC6` (RC6-0-16) exactly. `RC6-6-20` has
mode 6 and a four-bit subdevice (`6:3`, `S:4`); `RC6-M-16` is the same frame
with the mode a parameter, and `RC6` is its M=0 case, which the database
prefers. Every one of the 1,237 database codes is four hex digits, mode 0.
Only `RC6` is registered; nothing about modes other than 0 was checked.

**Hex map.** `device = hex >> 8`, `function = hex & 0xFF` (hex_philips.py L67).
The database's Philips TV codes agree with Girr's published command set
(`philips_tv_cmdset_rc6.girr`: device 0, power 12, volume 16/17, mute 13):
`000C`, `0010`, `0011`, `000D`. All 65,536 codes are representable.

**Toggle.** As for RC5: the app's preview is T=1 for all 1,237 codes, the
ledger T=0 (rc6.py has `toggle` for tests only; a file cannot set it).

**The one framing difference, and who is right.** The app ends every frame
after the standard's six-unit signal-free time (rc6.dart L99, a 2,664 us
space, 3,108 us after a last bit of 1). The IRP's `^107m` makes the final space
whatever remains of a 107 ms frame period (83,912 us, or 84,356 after a last
bit of 1). Same marks and spaces to the last one; the ledger's final space is
longer in all 1,237 codes, and the oracle requires it never to be shorter. The
two are not in conflict: six units is the minimum idle, 107 ms the repeat
period the database uses. Nothing here says which the app should use; it only
matters for how fast a held key repeats.

**No bitspec exception was needed.** DESIGN D18 says RC6's double-width
trailer "needs a bitspec exception none of the other protocols require"; the
encoder builds the frame as per-unit levels and run-length encodes them, so it
does not. The registry's `encode` interface is unchanged. Mode is not a
parameter (mode 0 only) and the toggle is the existing `toggle=` keyword, so no
interface decision was needed beyond that.

## RCA-38

**Which IRPT entry.** The app's frame is an 8,-8 lead-in, 24 bits of `addr(4)`,
`cmd(8)`, `~addr(4)`, `~cmd(8)`, one stop mark and a gap, at 38.7 kHz with a
460 us unit (rca_38.dart: description L6-10, constants L44-50, encode L55-82). `RCA` and `RCA(Old)` are 58 kHz;
`RCA-38(Old)` has the longer first lead-in and a double stop mark. Only
`RCA-38` fits. Hex map: three digits, address nibble then command byte
(db_button_import.dart L216-224); all 4,096 codes representable. The 60 DB
codes are all address F and compare exactly.

**The gap is `-16`, not an extent**, so `extent_us` is `None` (D3). Because the
complement half always contains twelve 1 bits, every frame is the same
59,340 us whatever the data, so an extent would change nothing.

**Disagreement recorded, not smoothed over.** The real-remote capture in
IrpTransmogrifier's test data (`RCA-38.ict`, 31 keys) sits uniformly 1.085
times the IRP's durations, marks and spaces alike: a 500 us unit against the
IRP's 460. The app and the ledger both use 460, which is within tolerance for
every duration, so nothing mismatches. If a receiver turns out to care, a
remote file can set `protocol.unitUs` to 500 with a `claims` entry (D27).

## Vectors and gates

All three new protocols have the three gates (tests/vectors/CITATIONS.md has
the text). Gate 1 for each is IrpTransmogrifier's database @c945e76, corroborated
by DecodeIR's own documentation, which I fetched. Gate 2b:

- `RC6`: two **published** Pronto strings (ProtocolNGTest L230-237,
  ShortProntoNGTest L20), both also what 1.2.14 renders.
- `RCA-38` and `Thomson7`: **reproducible** only, from the 1.2.14 release;
  `test_registry` warns about them as intended. No published Pronto exists in
  IrpTransmogrifier's tests, Girr or IRDB. Their gate 2a is the two
  hardware captures above, cited and not vendored (GPL-3.0 data of another
  project); `tools/philips_capture_audit.py --irpt DIR` re-runs it.

**A quantizer discrepancy.** `tests/reference_quantizers.py` `irpt` rounds in
exact decimal; IrpTransmogrifier rounds a double. On an exact half cycle the
two differ (Thomson7 D=12 F=74 T=1: a 34,500 us gap is 1138.5 cycles at
33 kHz, the tool gives 0x472, the rule 0x473). The Thomson7 vector uses T=0,
which has no tie, and the shared rule is untouched.

## Shared files touched (append-only, will conflict with the other agents)

- `protocols/__init__.py`: one block after `__all__` (L41-47), the docstring's
  backlog sentence (RC6 removed) and one paragraph.
- `tests/test_registry.py` L17-20: two lines after `BACKLOG` extending
  `V1_REGISTRY` and removing `RC6` from `BACKLOG`.
- `tests/vectors/index.json`: three entries appended; `pronto-vectors.json`:
  four entries; `CITATIONS.md`: one section at the end.

## For DESIGN.md (not edited)

- D18's table and the "backlog" sentence (L222-228): RC6 is registered (mode 0
  only); its remark that RC6 needs a bitspec exception is wrong as built.
  `RCA-38` and `Thomson7` are new rows.
- D3b (L332-342) ends "RC6 is still unregistered"; it is not, and the same
  open question now applies to RC6 and Thomson7: the database stores no toggle
  and the app alternates it, so three protocols compile T=0.
- Section 12's gate-2b table: four vectors added (two published, two
  reproducible), and the quantizer-tie note above.
- The test count (the one expected failure in the suite).

## Reproducing

```
python tools/irblaster_oracle_philips.py --oracle ORACLE
python tools/philips_capture_audit.py --irpt <IrpTransmogrifier @c945e76> --db swiftremote.sqlite
```

The first prints per-protocol counts and exits 1 on an unexplained mismatch.
The fixtures in tests/fixtures/irblaster/ are 39, 38, 20 and 29 of the app's
own records for RC5, RC6, RCA_38 and Thomson7; the full jsonl is not committed.
