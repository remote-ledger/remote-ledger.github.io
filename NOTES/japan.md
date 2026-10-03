# The japan family: Pioneer, JVC, Sharp, Denon

For the integrator. Four SwiftRemote database protocols (1,673 + 1,023 + 590 +
519 distinct codes; 18,378 keys) registered in the ledger and mapped.
Nothing here edits DESIGN, SPEC, README, `build/`, `site/` or `remotes/`.

## The decision you have to make

**For all four protocols the database's codes do not mean what the app does
with them.** `FROM_DB_HEX` follows the app, as the brief says, so the ledger
signal is what SwiftRemote transmits today and the oracle gate passes. The
evidence below says the app is wrong for these four: for 3,605 of the 3,805
distinct codes it transmits something other than the signal the real remotes
send. Importing
with `FROM_DB_HEX` reproduces the app's behaviour in the ledger; importing with
`FROM_DB_HEX_WIRE` (same signature, same module) puts the evidence-supported
signal there instead. I did not choose for you. What decides it is whether the
ledger records what the app sends or what the remotes send.

The codes are bit strings in the order the bits go on the wire, first bit the
most significant bit of the hexcode. The app reads them four different ways,
all of them wrong for that data:

- **JVC** (`jvc.dart:53-66`) and **Pioneer** (`pioneer.dart:96-123`) send each
  byte *least* significant bit first, so every byte goes out with its bits in
  reverse order: `C0` is the wire string `1100 0000` and is sent as
  `0000 0011`.
- **Sharp** (`sharp.dart:65-67`) masks the code with `0x1FFF` and takes bits
  12-8 as the address and 7-0 as the command, a register layout the data does
  not have. The data is address (5 bits), command (8), the trailer (2) and a
  pad bit, in wire order.
- **Denon** (`denon.dart:77-80`) builds its 13-bit field from the first three
  nibbles plus `nib3.substring(3, 4)`, the *last* bit of the fourth nibble. The
  data's thirteenth bit is the *first* (`substring(0, 1)`). Every code whose
  fourth digit is 8 or E (332 of 519) loses the top bit of its command.

**Evidence**, independent of the app, in three layers:

1. *IrpTransmogrifier's own decodes of real captures are in the database in
   wire order.* Its teaser files (`src/test/teaserfiles` @c945e76, which publish
   the decode they expect) give a Pioneer receiver's Setup key as Pioneer-2Part
   `{D0=170,F0=91,D=175,F=36}`, Return as `{...,F=34}`; Sharp's Power as
   `{D=1,F=22}` and Input Cycle `{D=1,F=19}`; Denon's `left`, `0` and `OK` as
   `{D=8,F=175}`, `{D=8,F=129}`, `{D=8,F=187}`. Their wire-order hexcodes
   `55DAF524`, `55DAF544`, `8344`, `8644`, `17A8`, `1408`, `16E8` are all in the
   database, and `FROM_DB_HEX_WIRE` maps each to exactly the published
   parameters while `FROM_DB_HEX` maps each to something else
   (`tests/test_irblaster_japan.py`,
   `test_the_wire_reading_gives_the_published_decode_and_the_apps_reading_does_not`).
   Read the app's way, Sharp's Power would be one of eight codes `0x0116`,
   `0x2116` ... `0xE116`, and none is in the database; the app-order Pioneer
   Setup and Return codes `AA5BAF24` and `AA5BAF22` are not either.
2. *Real remotes' waveforms from the repository's own LIRC import.* The
   database and `remotes/lirc` share models (`Denon RC-129A`, `JVC RM-C360`,
   `Sharp G1071SA`, `Pioneer CU-XR015`, ...). For every database key of such a
   model, `tools/irblaster_wire_order_japan.py` asks whether the code's data
   field is a frame in that model's LIRC conf. "Neither" is mostly keys the conf
   does not have.

   | DB protocol | models | keys | app reading only | wire reading only | both | neither |
   |---|---|---|---|---|---|---|
   | Pioneer | 12 | 638 | 0 | 358 | 0 | 280 |
   | JVC | 19 | 1,172 | 0 | 702 | 0 | 470 |
   | Sharp | 16 | 1,026 | 6 | 594 | 17 | 409 |
   | Denon | 25 | 1,524 | 0 | 276 | 851 | 397 |

   Denon's 851 "both" are the codes whose fourth digit is 0 or 6, where the
   dropped bit is zero anyway. Sharp's 6 "app only" are coincidences of a
   13-bit register. Nowhere does the app's reading find a real frame that the
   wire reading misses, except those six.
3. *Structure.* 561 of Sharp's 590 distinct codes end in the bits `100` and the
   other 29 in `010`: the trailers `1:2` and `2:2` plus a zero pad bit, in wire
   order. A register layout would have no reason to. The top three bits are
   never `000` or `111`. Denon's fourth digit is only ever 0, 6, 8 or E, so its
   low three bits are `000` or `110` and bit 0 is never set: thirteen data
   bits, then three bits the app ignores.

How many codes differ: `tools/irblaster_oracle_japan.py --reading wire` reports
1,667 of 1,673 Pioneer, 1,020 of 1,023 JVC, 586 of 590 Sharp and 332 of 519
Denon codes produce a different signal under the two readings. The other 200
are the 187 Denon codes whose fourth digit is 0 or 6 (the dropped bit is zero
anyway) and 13 Pioneer, JVC and Sharp codes on which the readings happen to
agree.

Two things in the wire reading are weaker than the rest and are stated rather
than hidden. (a) Sharp's 29 codes with the trailer `2:2` are recordings of the
complement half; `_sharp_wire` returns the *complemented* function by the IRP's
definition. Only one such key is in the LIRC overlap and it does not match, so
nothing independent supports that inversion, and refusing those 29 would be a
defensible alternative. (b) Denon's hex bits 2 and 1 are `00` or `11` in the
database; the real remotes' frames carry the trailer `00` either way (230 of
the 309 LIRC-overlap keys with fourth digit 6 and 25 of the 26 with E match a
real frame that way; the rest are keys the confs lack), so the wire reading
ignores them, but what they encode is not known.

## What was registered

Four protocols, from IrpTransmogrifier's `IrpProtocols.xml` (IRP strings
identical at `c945e76`, where the other ledger citations are pinned, and in the
1.2.14 release the vectors were rendered from):

| Ledger protocol | DB protocol | IRP |
|---|---|---|
| `Pioneer-2Part` | `Pioneer` | `{40k,564}<1,-1\|1,-3>(16,-8,D0:8,~D0:8,F0:8,~F0:8,1,^90m,(16,-8,D:8,~D:8,F:8,~F:8,1,^90m)+) [...]` |
| `JVC` | `JVC` | `{37.9k,527,33%}<1,-1\|1,-3>(16,-8,D:8,F:8,1,^59.08m,(D:8,F:8,1,^46.42m)*) [...]` |
| `Sharp` | `Sharp` | `{38k,264}<1,-3\|1,-7>(D:5,F:8,1:2,1,^67m,(D:5,~F:8,2:2,1,^67m,D:5,F:8,1:2,1,^67m)*)[...]` |
| `Denon` | `Denon` | `{38k,264}<1,-3\|1,-7>(D:5,F:8,0:2,1,^67m,(D:5,~F:8,3:2,1,^67m,D:5,F:8,0:2,1,^67m)*)[...]` |

The modules are `src/remote_ledger/protocols/{pioneer,jvc,sharp,denon}.py`,
with `_pulse.py` and `_addr_cmd.py` for what they share; registration is a
block at `protocols/__init__.py:35-45`.

**Pioneer-2Part, not Pioneer** (`pioneer.py:1-47`). The app's 136 durations are
two 68-duration frames (`pioneer.dart:126-131`, `db_button_import.dart:237-243`): the primary address and
command, then the secondary pair, or the primary again. In the database 1,102 of
1,673 codes have different halves, and 694 of those have `F5` (device 175 in
wire order) as the second address, the "Pioneer Mix" shape that IrpTransmogrifier's
own `PioneerMix` teaser files decode. That is `Pioneer-2Part`:
the intro is both frames and the repeat the second alone (`+` makes one pass of
the repeat mandatory in the intro; this is IrpTransmogrifier's own rendering). The 571 codes with equal
halves are its degenerate case and render to the same two frames. Plain
`Pioneer` is one frame at a 108 ms extent, so it would send a different signal
and no database code needs it; D18 says the registry holds what something uses,
so it is not registered. IrpTransmogrifier prefers plain Pioneer when it
*decodes* equal halves; nothing here depends on that.

*Parameters.* A remote file's irp form has `device`, `subdevice`, `function`;
Pioneer-2Part has four numbers. `device = D0*256 + D` and `function = F0*256 +
F`, first frame in the high byte, `subdevice` omitted and refused. The IRP's
defaults (`D=D0`, `F=F0`) are not assumed: an equal pair is written out
(`0xADAD`). Both fit the schema's 16-bit `hexOrInt`.

**JVC** (`jvc.py:1-35`). `JVC{2}` is the repeat frame alone, `JVC_squashed` is
decode-only, `JVC-48` and `JVC-56` are Kaseikyo-family frames (432 us unit, 48
and 56 bits). The app's 36 durations are a lead-in, 16 bits and a stop mark, so
it is JVC; the intro is that frame and the repeat is the same bits with *no*
lead-in at a 46.42 ms extent, as IrpTransmogrifier renders it and as its own
`JVC.ict` shows (a 36-duration frame with a lead-in followed by a 34-duration
frame without). The app sends the intro and omits the repeat. The IRP's 33 %
duty cycle is not modelled (`IrSignal` has none, for any protocol).

**Sharp and Denon** (`_addr_cmd.py:1-20`). The app sends three frames (normal,
complement, normal), which is the IRP's intro (normal) plus one pass of its
repeat (complement, normal). `Sharp{1}`, `Sharp{2}`, `Denon{1}`, `Denon{2}` are
the two halves alone (the Denon ones decode-only); `Sharp_Old` is a 3-bit device
at a 49 ms extent; `SharpDVD` and `Denon-K` are Kaseikyo-family frames. None is
registered.

`extent_us` is `None` for all four (`pioneer.py:133` and the others). D31 pads a
truncated sequence to a single figure, and these have several extents inside one
sequence (two frames in Pioneer-2Part's intro and Sharp's and Denon's repeat,
and JVC's intro and repeat differ). `None` sends a truncated raw form down the
`defaultGapUs` branch; a figure would make D31 raise.

## The contract: carriers and minSends

`hex_japan.py` exports `FROM_DB_HEX`, `MIN_SENDS`, and the alternative
`FROM_DB_HEX_WIRE`. A mapping returns the ledger protocol, so for `Pioneer`
that is always `Pioneer-2Part`.

| DB protocol | ledger protocol | `protocol.carrierHz` | `minSends` |
|---|---|---|---|
| `Pioneer` | `Pioneer-2Part` | 40000 | 1 |
| `JVC` | `JVC` | **38000** | 1 |
| `Sharp` | `Sharp` | 38000 | 1 |
| `Denon` | `Denon` | 38000 | 1 |

Carriers are the app's (`defaultFrequencyHz` in each Dart file, which
`buildButtonFromDbRow` stores on the key), and match the registry's nominal
carriers except JVC, where the registry follows IrpTransmogrifier's `37.9k`.
The two are 0.26 % apart and give the same Pronto frequency word (`006D`), so
`carrier-off-nominal` does not fire; the LIRC import has 90 of 108 JVC confs at
38000 and none at 37900. `minSends` is 1: the encoders ignore the `_repeat`
flag `sendIR` passes (none reads it), so a held button re-sends the same pattern,
and the database holds no repeat count.

## Oracle results (`tools/irblaster_oracle_japan.py`, app reading)

Every distinct code, 3,805 in all. "Gaps differ" means every mark and space is
within tolerance and only the idle gaps are not; they are counted separately,
never folded into "matched" (below).

| DB protocol | codes | matched | matched, gaps differ | mismatched | unrepresentable |
|---|---|---|---|---|---|
| Pioneer | 1,673 | 0 | 1,673 | 0 | 0 |
| JVC | 1,023 | 776 | 247 | 0 | 0 |
| Sharp | 590 | 587 | 3 | 0 | 0 |
| Denon | 519 | 518 | 1 | 0 | 0 |

Nothing is unrepresentable under the app's reading: every code is well formed
(4 or 8 hex digits, upper case) and every field fits. Under the wire reading
nothing is either (all 590 Sharp codes carry a known trailer, every Denon pad
bit is zero). The tool's refusals are for malformed codes that are not in the data (a wrong
length, no hex digits), and are counted with a stable reason.

The comparison is the brief's, with three explicit choices. The tolerance is
12 % of the *larger* of the two durations or 150 us, whichever is more, so it is
symmetric. The ledger signal is `intro` then `repeat`, against the app's pattern;
the app may stop on a frame boundary, and for JVC and Pioneer-2Part it always
does, **sending the intro and omitting the repeat** (1,023 and 1,673 codes), the
one length difference allowed and counted. The carrier check is the registry's
nominal carrier within 5 % of the app's (all pass).

Deviations over the codes that did match their marks and spaces, relative to the
larger duration:

| | worst non-gap deviation | app gap / ledger gap |
|---|---|---|
| Pioneer | **11.3 %** (a one-space, 1500 against 1692 us) | 1.195 for every code |
| JVC | 0.4 % | 0.72 to 1.47 |
| Sharp | 7.9 % (860 against 792, and 1720 against 1848 us) | 0.91 to 1.14 |
| Denon | 7.9 % | 0.86 to 1.11 |

**For the importer: write the `irp` form only.** Do not add the app's raw
pattern as a second, cross-checked form. D8 (`crosscheck.py:93-105`) requires
equal burst counts and holds the terminal gap to 150 us, so such a form would
fail on the missing repeat (every JVC and Pioneer-2Part code) and on the gap (the
1,924 "gaps differ" codes) for exactly the reasons below, which are the app's
choices and not errors in either signal.

Pioneer is within the tolerance by 0.7 points and the result depends on how the
tolerance is read: relative to the larger duration (as here) or to the IRP's
value it passes (192 us against 203 us allowed), but relative to the app's own
1500 us it would not (12.8 %). I did not widen it.

## Disagreements between the app and the IRP

**Gaps (the "gaps differ" column).** An IRP `^E` pads each frame to a fixed
*extent*, so the gap after a frame depends on its data (JVC's runs from 12.2 ms
to 29.0 ms). The app uses one constant gap per protocol: 21,000 us for JVC,
26,000 for Pioneer, 43,560 for Sharp and Denon. Real signals follow the extent:
over the LIRC import's frames, JVC's periods are 59 ms (2,175 frames) and 46 ms
(1,994), Sharp's 67-68 ms (1,393), Denon's 65-68 ms, Pioneer's 89-90 ms (1,034).
The app's gap is a stand-in for the average. The ledger follows the IRP.

Two refinements. Denon's and Sharp's 43,560 us is exactly 165 units, which is
the *superseded* form of both IRPs that IrpTransmogrifier keeps in a comment
beside the live ones (`(D:5,F:8,0:2,1,-165,D:5,~F:8,3:2,1,-165)*`); its own
published Denon Pronto string (`ShortProntoNGTest.java` L21) is that form, with
a 0x0677-cycle (43.5 ms) gap, and the app follows it. The live `^67m` form is
registered because it is what the source's active definition says
(`denon.py:1-35`); `tests/test_denon.py` checks the published string
against our encoder, every mark and space equal and only the gaps different.
And for **Pioneer** the IRP is not clearly the better figure: a real Pioneer
receiver captured in IrpTransmogrifier's `PioneerMix2.ict` has a 25.4 ms gap, as
does IRremoteESP8266's measured minimum (25,181 us, `src/ir_Pioneer.cpp`), and
880 of the LIRC import's Pioneer gaps are 25.3-25.5 ms. The app's 26 ms is
within 3 % of that. The IRP's `^90m` gives 21.8 ms. So the "gaps differ" count
for Pioneer (all 1,673) is a disagreement, but not evidence the app is the
wrong one (see the timings below: the periods agree).

**Pioneer timings.** The IRP's unit is 564 us (NEC-derived, `Pioneer` is
"distinguished from NEC2 only by frequency"); the app's are 8500/4225, 500 and
500/1500. IRremoteESP8266's, from a measured Pioneer (its issue #1220), are
8506/4191, 568 and 487/1542; the capture in `PioneerMix2.ict` reads 8548/4227,
548 and 527/1577. The app's header is within 0.1 % of the measured one and its
spaces within 3 % (its bit mark, 500 against 568, is 12 % short), where
IrpTransmogrifier's nominal header is 6 % high. The oracle's 11.3 % is the app's
spaces against the *nominal* IRP, not against a real remote. The frame periods
agree to 3 % whichever you take: 89.1 ms from IRremoteESP8266's figures, 90.0
from the IRP's `^90m`, 87.2 from the app's (a 61.2 ms frame and its 26 ms gap).
The IRP's frame is longer (68.2 ms against about 64) because its unit is nominal,
so the same period leaves a shorter gap.

**Denon and Sharp timings.** The app uses marks of 280 us and spaces of 860 and
1720 (a ratio of 2); the IRP's `<1,-3|1,-7>` is 264, 792 and 1848 (a ratio of
2.33), and the Denon capture in `Denon.ict` reads 255, 795 and 1846 on average.
The IRP is within 3 % of a real receiver and the app's zero-space is 8 % above
and its one-space 7 % below. All inside the tolerance.

**JVC** is the clean one on timings: 8400/4200/525/525/1575 against
8432/4216/527/527/1581, under 0.4 %.

## What is not proven

- **Gate 2b is reproducible, not published, for all four.** No IrpTransmogrifier
  test asserts a render of `Pioneer-2Part`, `JVC`, `Sharp` or the live `Denon`;
  `tests/vectors/CITATIONS.md` lists what was searched. `test_registry` warns
  about each, as it does for NECx2. The IRPs and their parameters are cited and
  exact (136 renders identical duration for duration,
  `tests/test_irpt_sweeps_japan.py`), but the golden string is IrpTransmogrifier
  agreeing with IrpTransmogrifier's database.
- **Gate 2a is weaker than a published constant table**: two real captures
  (Pioneer, Denon), a Pronto export (Sharp), and a JVC file whose durations are
  exact multiples of 525 us and so look generated. They verify layout, bit order
  and ratios, and nothing about absolute durations or gaps.
- **Nothing was run against hardware.** The wire reading is supported by real
  waveforms (LIRC) and decodes (IrpTransmogrifier), not by a device obeying it.
- **The Sharp complement-frame inversion and Denon's `11` flag** (above).
- **D6 rounding at 40 kHz.** Our Pronto bytes differ from IrpTransmogrifier's
  in 201 of 208 words for Pioneer-2Part: a 564 us unit is 22.56 cycles, which
  D6 rule 4 (against the word's period) rounds to 22 and the tool (against the
  nominal carrier) to 23. The timings are identical under the tool's rule; it is
  a property of D6 at this carrier and unit, recorded in `pronto-vectors.json`.
- Pioneer hexcodes of other than 8 digits, and JVC or Denon codes longer than 4,
  are not in the data. The app keeps the last four digits of a longer JVC or
  Denon code and the mapping does the same; a shorter code, a Sharp code of
  other than 4 digits and a Pioneer code of other than 8 are refused.

## Edits to shared files, for the merge

All append-only, one block per protocol:

- `src/remote_ledger/protocols/__init__.py:35-45`: four imports, a
  `REGISTRY.update(...)` and an `__all__ +=`.
- `tests/test_registry.py:16`: one line adding the four names to `V1_REGISTRY`.
- `tests/vectors/index.json`, `tests/vectors/pronto-vectors.json`: four entries
  each, at the end.
- `tests/vectors/CITATIONS.md`: one section at the end.
- `src/remote_ledger/irblaster/__init__.py`: a docstring only, as the brief says
  other families will also create it.

The suite passes except `test_documented_test_count_is_current`, as expected:
1,051 passed, 8 skipped, 1 failed (882 passed before; 170 tests added).

## Reproduce

```
python tools/irblaster_oracle_japan.py --oracle ORACLE            # the table above
python tools/irblaster_oracle_japan.py --oracle ORACLE --reading wire
python tools/irblaster_wire_order_japan.py --db assets/db/swiftremote.sqlite --oracle ORACLE
```

`tests/fixtures/irblaster/{pioneer,jvc,sharp,denon}.json` hold 37, 37, 31 and
38 of the oracle's codes, chosen to span the classes the mappings distinguish,
and `tests/test_irblaster_japan.py` runs the same comparison over them.
