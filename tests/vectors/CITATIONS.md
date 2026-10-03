# Golden vector citations

D10 holds these to R18's own standard: a vector is cited data, not a
convenience fixture. **No protocol ships in the registry without at least one
independently cited golden vector** — that is a hard gate, not a preference,
because it is the only test layer that catches a wrong constant. A round-trip
test confirms the decoder inverts the encoder; it says nothing about whether
the NEC1 lead-in is 16 units or 15.

Gate status is machine-checked by `tests/test_registry.py` against
`index.json` in this directory. A protocol listed as `pending` there is **not
verified**, and saying so out loud is the point.

---

## NEC1

### Gate 1 — IRP string with its source: **MET**

> `{38.0k,564}<1,-1|1,-3>(16,-8,D:8,S:8,F:8,~F:8,1,^108m,(16,-4,1,^108m)*)`

Source: <http://www.hifi-remote.com/johnsfine/DecodeIR.html> — John Fine's
DecodeIR documentation on hifi-remote.com, retrieved 2026-09-14.
Independently confirms every timing the encoder implements: unit 564 µs,
lead-in `16,-8` units (9024 µs mark / 4512 µs space), `<1,-1|1,-3>` bit
encoding, LSB-first bit order, the `D:8,S:8,F:8,~F:8` field layout, and the
`^108m` frame extent.

**The carrier differs between the two sources, and both are cited.** This
source gives **38.0 kHz**. IrpTransmogrifier's `IrpProtocols.xml`
(@`c945e76`, L1571) gives the same IRP verbatim at **38.4 kHz**, and the
registry's `nominal_carrier_hz` follows IrpTransmogrifier, as does D18's
table. It changes no output: `nominal_carrier_hz` is informational, and a
file's `protocol.carrierHz` is authoritative (D3). An earlier note here said
D18 should be "corrected" to 38.0k. That was one citation overruling
another, and it is withdrawn.

### Gate 2 — independently cited golden Pronto string: **MET** (see §Gate 2b)

A published NEC1 Pronto string with stated parameters now exists:
IrpTransmogrifier's own test suite asserts it byte for byte. Our timings
reproduce it exactly. Our bytes differ in 4 words, because the two tools
round differently. Gate 2b, below, has the details.

The frequency-word formula, which the emitted header depends on, is
independently confirmed:

> "Frequency = 1000000/(N \* .241246)" where N is the decimal value of the
> second hex word of the preamble.

Source: <http://www.remotecentral.com/features/irdisp2.htm> — Remote
Central, "Infrared Remote Control Codes, Part 2". This is the citation for
D6 rule 2 and for the `0.241246` constant.

**The question that page raised is settled: `0068`.** It gives Sony's word
as `N = 103` (`0x0067`) for 40 kHz, where the exact quotient is 103.6287, so
`0067` implies truncation. D6 rule 5 rounds half up, to `0068`. Both
generators consulted agree with D6:

- IrpTransmogrifier: `Pronto.java` L88, `Math.round(1000000d / (frequency *
  FREQUENCY_CONSTANT))`.
- MakeHex: `IRP.cpp` L447, `floor(4145146. / m_frequency + 0.5)`.
- Every 40 kHz vector below starts `0000 0068`, and so does Girr's
  published Sony reference (`src/test/reference/commandset_sony.girr` @
  `5ca171e`).

`0067` comes from capture-side dumpers that truncate. Arduino-IRremote's
`ir_Pronto.hpp` @ `6158d65` L207, for example, uses integer division. It is
not a generation rule. `test_the_40khz_frequency_word_is_0068` pins this.

### Gate 3 — invariant test: **MET**

`tests/test_nec.py` — frame extent, pair counts, bit order and field layout
recovered from the emitted words, and the NEC complement relation.

### Regression snapshot (not a vector, and not evidence)

`nec1_topping_power.pronto` is this encoder's **own** output for the Topping
RC-15A Power key, committed so a refactor cannot change the bytes unnoticed.
It is self-derived: it proves stability, not correctness, and it does not
satisfy Gate 2. Labelled here so no one later mistakes it for a citation.

---

## Gate 2, split into 2a and 2b

The original gate 2 asked for "an independently cited golden vector" and
meant a Pronto string. Pursuing one surfaced that it was two claims wearing
one name:

- **Gate 2a — structural.** Do our protocol constants match what an
  independent implementation publishes? This is what catches "the NEC1
  lead-in is 15 units, not 16", which is the failure D10 names.
- **Gate 2b — byte-level.** Do our *emitted Pronto bytes* match someone
  else's? This additionally covers the microsecond-to-Pronto quantization.

Splitting them is not a relaxation. 2b is unchanged and still open for every
protocol; 2a is a check that did not exist before and that no protocol had
passed.

### Gate 2a: **MET** for NEC1 and NECx2

Source: `crankyoldgit/IRremoteESP8266` — `src/ir_Samsung.cpp` and
`test/ir_NEC_test.cpp`, retrieved 2026-09-19, which in turn cites
<http://elektrolab.wz.cz/katalog/samsung_protocol.pdf>.

It publishes a 560 µs tick with a 16-tick NEC header (8960/4480 µs), an
8-tick NECx header (4480/4480 µs), a 1-tick bit mark, 3-tick one-space and
1-tick zero-space over 32 bits. Feeding our encoder *their* tick reproduces
those durations exactly — see `tests/test_vectors_structural.py`.

**Recorded rather than smoothed over:** their tick is 560 µs where
IrpTransmogrifier and DecodeIR both say 564 µs, and their frame extent is
193 ticks (108 080 µs) where the IRP says `^108m` (108 000 µs). Both are
legitimate implementations of the same protocol and real receivers tolerate
0.7 %. That divergence is exactly why this vector verifies structure and
not bytes.

### Gate 2a: **MET** for Sony20, on weaker evidence

Source: a 2015 hardware capture of the Sony RMT-B118P
(`jose1711/lirc_remotes`, `sony/RMT-B118P.lirc.conf`). A measurement carries
instrument bias, so it verifies the ratios and field layout, not absolute
durations. `index.json` says so, and `test_registry` requires any
capture-based citation to say so. Gate 2b, below, now covers the absolute
durations.

### Gate 2a: **MET** for RC5, on weaker evidence

Source: the Meridian 562+565 code set in Flipper-IRDB
(`Lucaslhm/Flipper-IRDB@d126fb1b6f1e`,
`_Converted_/Pronto/M/Meridian/562+565.ir`). It is a collection converted
from Pronto, its provenance upstream is unstated, and it is **cited, not
vendored**: SPEC §4 excludes Flipper-IRDB files from before its CC0 cutoff,
and the shallow clone it was read from cannot date this one.

It does what the published vectors cannot. Both of those have F below 64, so
neither sets the second start bit to 0. This set has 23 frames that do, and
the encoder reproduces all 48 of them -- 24 ending on a mark and 24 on a
space, 37 with T=1 and 11 with T=0 -- from the address, command and toggle
bit each decodes to. A measurement carries instrument bias, so this verifies
the frame layout and the extent, not absolute durations; gate 2b covers
those.

To repeat it, check out the three sources and run
`python tools/rc5_capture_audit.py --lirc DIR --irdb DIR --flipper DIR`.

### Gate 2b: **MET** for all four, and restated

**Gate 2b as first written cannot be met, because the reference tools
disagree with each other.** Pronto records durations as carrier cycles. The
three encoders consulted convert microseconds to cycles three different
ways:

| Encoder | Rule | 9024 µs at word `006C` (period 26.0546 µs) |
|---|---|---|
| **Ours (D6 rule 4)** | each duration against the period the word implies: `round(t / (word × 0.241246))` | 346 cycles, which plays as 9014.9 µs |
| **IrpTransmogrifier** | each duration against the *nominal* carrier: `round(t × f / 10⁶)` | 347 cycles, which plays as 9040.9 µs |
| **MakeHex** | against the word's period, but each mark+space pair cumulatively | 346 for the mark; spaces absorb the pair's rounding |

"Byte-identical to someone else's Pronto" therefore names no single target.
Matching IrpTransmogrifier would mean adopting a rule whose output drifts
further from the intended durations when played back. It was considered and
declined, and D6 rule 4 stands.

**What gate 2b checks now** (`tests/test_vectors_pronto.py`, and
`check_gate_2` in `test_registry.py`):

1. **Timings, exactly.** Our microsecond signal, quantized by the *tool's
   own* rule (`tests/reference_quantizers.py`, transcribed from each tool's
   source with line citations), must reproduce the tool's vector word for
   word. This checks every duration we compute against an implementation we
   did not write, including the `^108m` / `^45m` lead-outs, which gate 2a
   did not cover.
2. **Bytes, where they differ.** Our own bytes may differ from a vector only
   at the words `pronto-vectors.json` declares. That pins D6 rule 4:
   changing it, or any other difference, fails.

**The vectors** (full provenance in `pronto-vectors.json`):

| Protocol | Vector | Provenance | Our bytes differ at |
|---|---|---|---|
| NEC1 | D=12 S=243 F=34 @ 38.4k: IrpTransmogrifier `IrpTransmogrifierNGTest.java` L479-481 @ `c945e76`, a byte-exact `assertEquals` | **published** | words 4, 71, 72, 75 (lead-in marks and both gaps) |
| NEC1 | D=12 S=34 F=56 @ 38.4k: `ProtocolNGTest.java` L187-193 @ `c945e76`; also IrScrutinizer's `NEC1_PRONTO` | **published** | words 4, 71, 72, 75 |
| Sony20 | D=12 S=34 F=56 @ 40k: IrpTransmogrifier `Decoder.java` L48 @ `705ce35` (parameters decoded from it, not stated beside it) | **published** | word 45 (lead-out) |
| Sony20 | 26.226, F=0..127 @ 40k: MakeHex @ `1373d90`, its own `Sony20.IRP` with only `Device` changed | **reproducible** | only F=127, word 45 |
| NECx2 | D=7 S=7 F=2 @ 38.4k: IrpTransmogrifier 1.2.14 release, `render -n D=7,S=7,F=2 -p necx2` | **reproducible** | word 71 (lead-out) |
| RC5 | D=1 F=1 @ 36k, T=0 and T=1: IrpTransmogrifier `ShortProntoNGTest.java` L17-18 @ `c945e76` (constants `RC5_1_1_0`, `RC5_1_1_1`; parameters decoded from them and from their shared short code, not stated beside them) | **published** | word 27 (lead-out) |
| RC5 | D=7 F=5 @ 36k, T=0: IrpTransmogrifier `IrpTransmogrifierNGTest.java` L237-239 @ `c945e76`, `decode --strict -p rc5` asserted to give `RC5: {D=7,F=5}` | **published** | word 25 (lead-out) |

**RC5's second vector earns its place.** D=1 F=1 cannot tell the address
bits from the command bits, or catch a swapped bit order: both are mostly
zeros. D=7 F=5 can, and it is a decode assertion by the tool, so it checks the
field layout from the other direction. Neither vector has the second start
bit set to anything but 1, so the complement `~F:1:6` is pinned by the
Flipper set below instead.

"Reproducible" means generated here from a pinned release, not found in
print. Both tools earn that trust independently:

- The same IrpTransmogrifier jar reproduces both published NEC1 strings
  exactly.
- The same MakeHex build reproduces a MakeHex output published on
  RemoteCentral on 2013-09-25 (rc-discrete thread 7132), all 72 words.

**The one honest residual: NECx2 has no *published* vector.** None with
stated parameters was found, and `test_registry` warns about it on every
run. Searched and not found: IrpTransmogrifier's full history, IrScrutinizer,
Girr, IrpMaster and IrMaster tests, the harctoolbox docs, and probonopd/irdb.
MakeHex's own NECx2 definition uses a fixed `1,-78` lead-out instead of
`^108m`, so it is a different signal and cannot serve.

**IRP divergences recorded, not smoothed over:**

- IrpTransmogrifier gives NECx2 and Sony20 with `*` where DecodeIR has `+`.
  It also gives NECx2 at 38.4k where DecodeIR has 38.0k.
- Our encoder emits the whole frame as the repeat with no intro. That is the
  shape IrpTransmogrifier renders for `*`, and every vector above matches it
  in its pair counts. R3's `minSends` is what guarantees the frame goes out
  at least once.

---

## NEC2 and NECx1 (the SwiftRemote import), and a second search for NECx2

Both were in or beside the backlog "a few lines' difference from NEC1", and
the gate was applied to them as to anything else.

### Gate 1 — IRP strings with their sources: **MET** for both

- `NEC2`: `{38.4k,564}<1,-1|1,-3>(16,-8,D:8,S:8,F:8,~F:8,1,^108m)*`
- `NECx1`: `{38.4k,564}<1,-1|1,-3>(8,-8,D:8,S:8,F:8,~F:8,1,^108m,(8,-8,~D:1,1,^108m)*)`

Source: IrpTransmogrifier's `IrpProtocols.xml`, verbatim: the 1.2.14 release
(`list -i NEC2`, `list -i NECx1`) and `@c945e76` L1670 and L1742, identical in
both. (Each is followed there by the parameter list
`[D:0..255,S:0..255=255-D,F:0..255]`, which is not part of the IRP.)

Independently corroborated by DecodeIR (hifi-remote.com/johnsfine/
DecodeIR.html, retrieved 2026-10-02), which gives NEC2 as `(16,-8,D:8,S:8,
F:8,~F:8,1,^108m)+` and NECx1 as `(8,-8,D:8,S:8,F:8,~F8,1,^108m,(8,-8,~D:1,
1,^108m)*)`: the same frames, the same extent, and, for NECx1, the same
one-bit repeat. Three differences, recorded and not smoothed over: DecodeIR
writes `+` where IrpTransmogrifier writes `*` for NEC2 (the signal is the
same, a frame in the repeat slot); it writes the carrier as 38.0k; and its
NECx1 string has `~F8`, which is a typo for `~F:8`, since its own prose says
the stream is `D:8,S:8,F:8,~F:8`. `nominal_carrier_hz` follows
IrpTransmogrifier, as NEC1's does.

### Gate 2a — structural: **MET** for NEC2 and NECx1, and now also a second source for NECx2

Source: `src/test/teaserfiles/NECx2_NECx1.ict` and its expected decodes in
`NECx2_NECx1.exp`, IrpTransmogrifier `@c945e76`. It is a hardware capture
(irscope, measured carrier 38404 Hz) of a single remote that sends NEC2,
NECx2 and NECx1 on different keys, and the `.exp` is the tool's own decode of
it, run by `dotest.sh`: `NEC2: {D=31,F=223}`, `NECx2: {D=67,S=83,F=57}`,
`NECx1: {D=44,S=44,F=4}`.

**A capture carries instrument bias**: its medians sit within about 2 % of the
IRP's, without one direction (NEC2 lead-in 8930/4470 against 9024/4512, bit
mark 552 against 564, zero space 570 against 564). So it verifies layout,
ratios and the extent, not absolute durations, and gate 2b covers those.
What it establishes that no other source here does:

- every duration of a real frame, quantised to 564 us, is the multiple our
  encoder emits -- lead-in width (16 against 8 units), LSB-first bit order,
  the complement byte, the stop mark;
- the `^108m` extent: a frame's active time plus its trailing gap sums to
  107.1 ms on all three (-0.8 %);
- NECx1's repeat is the three-pair frame the IRP describes (an 8-unit
  lead-in, one bit, a stop mark, padded to 108 ms) and its bit is a one for
  D=44, which is even, as `~D:1` requires;
- NEC2 and NECx2 repeat the whole frame and nothing else (four and three full
  frames in the captures, no short ones).

`nec-family-captures.json` keeps medians and the first frame quantised to
564 us units, not the durations; `tests/test_nec_captures.py` runs it.

It also bears on the carrier, as one remote and so only as corroboration:
38404 Hz is IrpTransmogrifier's 38.4k, not DecodeIR's 38.0k.
## Sony12 and Sony15 (added for the SwiftRemote database import)

Both are `Sony20` with a different field after the command, which is exactly
the kind of variant D18 warns about. They went through the three gates
separately; nothing was waved through on the strength of Sony20's vectors.

### Gate 1 — IRP strings with their source: **MET**

> `{40k,600}<1,-1|2,-1>(4,-1,F:7,D:5,^45m)*[D:0..31,F:0..127]` (Sony12)
> `{40k,600}<1,-1|2,-1>(4,-1,F:7,D:8,^45m)*[D:0..255,F:0..127]` (Sony15)

Source: IrpTransmogrifier `src/main/resources/IrpProtocols.xml` @`c945e76`,
L2581 and L2591, verbatim, and identical in the 1.2.14 release's copy
(L2562, L2572). John Fine's DecodeIR documentation on hifi-remote.com gives
the same timings and field layouts with `+` where this has `*`; the
registry records IrpTransmogrifier's spelling. Both emit the whole frame as
the repeat, as Sony20 does.

### Gate 2a — structural: **MET**, on `tests/vectors/sirc-structural.json`

- **IrpTransmogrifier's teaser set**, `src/test/teaserfiles` @`c945e76`:
  `.ict` captures each paired with the decode IrpTransmogrifier expects in
  the `.exp` beside it. The encoders reproduce **every one** of the 21
  Sony12 captures (`Sony12B` D=23 F=70..76; `Sony` D=16, D=36 sets), the 49
  Sony15 captures (`Sony15B` D=167 F=91..98; `Sony`) and the 10 Sony20
  captures copied, duration for duration. These are exactly nominal
  durations ending in a 500 ms gap, so they carry no instrument bias but
  are not an independent measurement either, and the gap stands in for the
  extent: they verify ratios, lead-in, bit shapes, field order and bit
  order, not `^45m`. No test in the pinned tree reads these files; they are
  published test data with expected decodes, which is weaker than an
  assertion.
- **Measured captures of Sony15 and Sony20 remotes**, from the same teaser
  directory (`Sony_15_20.ict` and `Sony_A2172.ict`, each with the decode
  IrpTransmogrifier expects; AV receivers at D=48 and D=176, D=16, D=26).
  Unlike the nominal files above these are evidently from hardware: durations
  jitter by one 25 µs sample tick and each capture holds three to seven
  frames. All 25 (20 Sony15, 5 Sony20) have a first frame within one tick of
  ours on every duration, and **a frame period of 45.0 ms to within 150 µs**
  (44,975-45,150 µs): the one independent measurement of the `^45m` extent
  here, and of a real remote sending SIRC at least three times. Captures carry
  instrument bias, so this verifies ratios and the extent to within a tick,
  not exact durations. There is no measured Sony12 capture in the set.
- **Girr's Sony12 reference set**, `src/test/reference/commandset_sony.girr`
  @`5ca171e`: 25 commands at D=1 with their Pronto. Every string is
  reproduced word for word under IrpTransmogrifier's rule, lead-out
  included; our own bytes differ from them only in the last word.
- **One published Sony15 string with its decode asserted**,
  `IrpTransmogrifierNGTest.java` L383-389 @`c945e76` (`testDecodeSony15`):
  `decode -p sony15` of it must give `Sony15: {D=164,F=61}`. Its sixteen
  pairs are ours exactly except the last word. It differs in two other
  ways, and `tests/test_sony_vectors.py` pins both: it puts the frame in
  the once-sequence (a decode input is written that way), and it totals
  44.4 ms, so its `0300` is not the `^45m` lead-out (`0318` under
  IrpTransmogrifier's rule). It is therefore **not** a gate 2b vector.

### Gate 2b — golden Pronto

| Protocol | Vector | Provenance | Our bytes differ at |
|---|---|---|---|
| NEC2 | D=90 S=165 F=38 @ 40k: `DecoderNGTest.java` L178-L186 @ `c945e76`, `testDecodePioneer` | **published**, with a caveat below | words 4 and 6-71 (every duration but the lead-in space: 564 us is 22.5 cycles at 40 kHz and the two rounding rules split the half differently) |
| NEC2 | D=12 S=34 F=56 @ 38.4k: IrpTransmogrifier 1.2.14 release, `render -n D=12,S=34,F=56 -p nec2` | **reproducible** | words 4, 71 (lead-in mark and gap) |
| NECx1 | D=12 S=34 F=56 @ 38.4k: the same release, `render ... -p necx1` | **reproducible** | words 71, 77 (both gaps) |
| NECx1 | D=13 S=34 F=56 @ 38.4k: the same | **reproducible** | words 71, 77 |

**NEC2's published vector is weaker than NEC1's, and says so.** The string is
a Pioneer signal, which IrpTransmogrifier defines as NEC2 at 40 kHz
("distinguished from NEC2 only by frequency", `IrpProtocols.xml`). The test
asserts it decodes as Pioneer and, within a 2000 Hz tolerance, as NEC2 (L185),
and not as NEC2 within 1000 Hz (L190). No parameters are stated. The 1.2.14
release decodes it as `Pioneer: {D=90,F=38}` and its `render -n D=90,F=38 -p
pioneer` reproduces the string byte for byte, which fixes the parameters; S
defaults to 255-D = 165, and our NEC2 encoder at 40 kHz reproduces every
duration under IrpTransmogrifier's rounding rule. What was **not** re-run is
the NEC2 half of the assertion: that release's `decode` lists NEC, NEC-f16,
NEC-Shirriff-32 and Pioneer for the string, not NEC2, so the claim "the tool
decodes this as NEC2" belongs to the pinned commit's library, which was not
built here. It is the only Pronto string in the tests that has NEC2's
shape, which is why NEC2 also carries the render at 38.4k, where the carrier is
the IRP's own.

**NECx1 has no published Pronto vector.** IrpTransmogrifier's tests at
`c945e76` mention NECx1 only in the capture's decode expectations above. Two
renders are used so that both values of the one field NECx1 adds to NECx2, the
repeat frame's bit `~D:1`, are pinned: an even D gives a one-bit (`1,-3`), an
odd D a zero-bit (`1,-1`). The capture shows only the even case.

**NECx2: searched again, still no published Pronto vector.** `git grep` of
IrpTransmogrifier `@c945e76` for `necx`, `nec2`, `samsung32` and `nec-f16`
across `src/test`, and for Pronto strings with an 8-unit lead-in at 36, 38,
38.4 and 40 kHz, finds only the teaser files above (decode expectations on a
capture, now used for gate 2a), `IrpDatabaseNGTest`'s database checks and
`DecoderNGTest`'s Pioneer test. NECx2's gate 2b therefore stays "reproducible",
and `test_registry` still warns about it, now together with NECx1.

### Gate 3 — invariant tests: **MET**

`tests/test_nec2.py` and `tests/test_necx1.py`: lead-in widths, pair counts,
each sequence padding to its own 108 ms, LSB-first order and the complement
byte read back out of the durations, an exhaustive round trip over all 65,536
device/function pairs and all 256 subdevices, and, for NECx1, the repeat bit
for each of the 256 devices.

### Not established

- NEC2's and NECx1's *byte-level* Pronto against a published NEC2 or NECx1
  vector, as above.
- Whether a real NECx1 receiver needs the repeat frame sent at all: the
  ledger encodes it, `minSends` never plays it.
| Sony12 | D=1 F=21 @ 40k: Girr `commandset_sony.girr` L42-47 @`5ca171e`, evidently IrpTransmogrifier's output (all 25 commands match its rule word for word) committed to a sibling repository with the parameters beside it. D=1 F=21 is Sony's TV power, `A90` in transmission order | **published** | word 29 (lead-out) |
| Sony12 | D=23 F=70 @ 40k: IrpTransmogrifier 1.2.14 release, `render -n D=23,F=70 -p sony12`; the first capture in its own teaser set, whose durations we also reproduce | **reproducible** | word 29 |
| Sony15 | D=164 F=61 @ 40k: IrpTransmogrifier 1.2.14 release, `render -n D=164,F=61 -p sony15`; the parameters of the one published Sony15 string | **reproducible** | word 35 |

**The honest residual: Sony15 has no *published* byte-level vector.** The
only published Sony15 string (above) does not carry the `^45m` lead-out,
the other candidates in the pinned tree are captures, and none was found
elsewhere: IrpTransmogrifier's tests and documentation, Girr's reference and
test files (its only Sony15 file, `sony_vlp_hw50es.girr`, states parameters
and no waveform), and the teaser set. Searched with `gh search code` as
well, which returned nothing. `test_registry` warns about it on every run,
as it does for NECx2. The decode of a real frame (D=164 F=61) and 49 teaser
captures do pin its framing; what is unproven is only the lead-out word
against a published string, which the render supplies.

### Gate 3 — invariant tests: **MET**

`tests/test_sony12.py` and `tests/test_sony15.py`: the whole 4,096- and
32,768-frame spaces round-tripped through a frame reader written from the
layout (`tests/sirc_reference.py`), extent, pair count, field and bit order,
bounds. `tests/test_sony.py` gained exhaustive Sony20 sweeps in the same
style.
