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

## The japan family: Pioneer-2Part, JVC, Sharp, Denon

Added for the SwiftRemote database import (`NOTES/japan.md` has the
decisions and what is not proven). Each IRP is quoted verbatim in its module
under `src/remote_ledger/protocols/`.

| Protocol | Gate 1: IRP source | Gate 2a: structure | Gate 2b: timings | Gate 3 |
|---|---|---|---|---|
| Pioneer-2Part | IrpTransmogrifier `IrpProtocols.xml` @`c945e76` L2060 | two IrScope captures of a Pioneer receiver, `PioneerMix2.ict` | **reproducible**: 1.2.14 `render`, D0=170 F0=91 D=175 F=36 | `tests/test_pioneer.py` |
| JVC | same file, L1207 | `JVC.ict`, D=5 F=0 and F=19 | **reproducible**: 1.2.14 `render`, D=5 F=19 | `tests/test_jvc.py` (all 65,536 pairs) |
| Sharp | same file, L2461 | `Sharp_Pronto.txt`, 'Power' D=1 F=22 and 'Input Cycle' D=1 F=19 | **reproducible**: 1.2.14 `render`, D=1 F=22 | `tests/test_sharp.py` (all 8,192 pairs) |
| Denon | same file, L494 | `Denon.ict`, D=8 F=175, 129 and 187; plus IrpTransmogrifier's own Denon Pronto string | **reproducible**: 1.2.14 `render`, D=8 F=175 | `tests/test_denon.py` (all 8,192 pairs) |

**Gate 1.** The four IRP strings are identical in the 1.2.14 release's
`IrpProtocols.xml` (the file the vectors were rendered from) and in the file
at `c945e76`, where the other ledger citations are pinned; only the line
numbers differ. `Pioneer-2Part` is registered and plain `Pioneer` is not:
every SwiftRemote database code sends two frames, and the 2-part IRP covers
all of them (the degenerate case, equal halves, renders to the same two
frames). The registry holds only what something uses (D18).

**Gate 2a: IrpTransmogrifier's teaser files.** `src/test/teaserfiles` @`c945e76`
publishes captures with the decode IrpTransmogrifier expects for each (the
`.exp` files). `tests/vectors/irpt_teaser_japan.json` keeps the first signal of
nine of them with their published decodes (the files are cited, not vendored
whole); `tests/teaser_japan.py` feeds each decode's parameters to our encoder
and compares layout and durations. Two are measured: the Pioneer receiver
(a 40.16 kHz IrScope capture with its own bias, a 548 us mark against 564) and
the Denon receiver (37.4 kHz, marks 254-292 us). `JVC.ict`'s durations are
exact multiples of 525 us, so it reads as generated, and `Sharp_Pronto.txt`
is a Pronto-hex export. All of them are therefore weaker than a published
constant table: they verify the **ratios, the field layout and the bit order**,
with the oracle tolerance (12 % or 150 us) and never the idle gaps, because a
gap depends on the extent and not on the protocol. A swapped bit order or a
wrong complement still fails them by a factor of two to three, and each test
file has a case that proves it.

**Gate 2b: what was searched, and why none is published.** The 1.2.14 release
is the source of all four vectors, so `test_registry` warns about them as it
does about NECx2. IrpTransmogrifier's test sources @`c945e76` were searched for
`Denon`, `Sharp`, `JVC` and `Pioneer`:

- `ShortProntoNGTest.java` L21 holds a Denon Pronto string and asserts
  `long2short` leaves it unchanged. Its parameters are not stated, and
  IrpTransmogrifier decodes it as `{D=1,F=3}`. It is the *superseded* form of
  the IRP, a fixed 165-unit (43,560 us) gap after each frame, not the `^67m`
  form the database now carries, so its gap words cannot match this encoder.
  `tests/test_denon.py` checks every mark and space against it and the gaps
  against what it should differ in.
- `DecoderNGTest.testDecodePioneer` asserts a decode of one plain-`Pioneer`
  frame, with no parameters stated.
- `ProtocolNGTest` builds `sharp` and `denon` from the superseded IRPs and
  asserts nothing about their rendering.

Nothing asserts a render of `Pioneer-2Part`, `JVC`, `Sharp`, or the live
`Denon`. Parameters were chosen from published real-world decodes (the Pioneer
receiver's Setup key, a JVC `.exp` entry, Sharp's Power code, Denon's `left`
key), so the *parameter values* are cited even though the waveform is generated.

**Reproduce:** `java -jar IrpTransmogrifier-1.2.14-jar-with-dependencies.jar
render -n D0=170,F0=91,D=175,F=36 -p Pioneer-2Part` and likewise `-n D=5,F=19
-p JVC`, `-n D=1,F=22 -p Sharp`, `-n D=8,F=175 -p Denon`.

**Wider than one vector.** `tests/vectors/irpt_render_sweeps_japan.json` holds 34
parameter sets per protocol (corners, the published decodes, a seeded random
sample) rendered by the same jar in signed microseconds (`render -r`), and
`tests/test_irpt_sweeps_japan.py` requires every intro and repeat duration to
be identical: 136 renders, 0 differences. That checks the encoders against
IrpTransmogrifier's implementation of the IRP over the whole parameter range,
including every extent-padded gap, with no Pronto quantization in between.

**Where our bytes differ from the vectors** (D6 rule 4 against the tool's
nominal-carrier rule; all timings agree exactly under the tool's own rule):

| Vector | Our bytes differ at | Why |
|---|---|---|
| Denon, Sharp | words 35, 67, 99 (the three lead-out gaps), by 1 to 2 cycles | rounding of a long gap against the word's period |
| JVC | words 4, 39, 73 (lead-in mark, two gaps), by 1, 3 and 4 cycles | the frequency word `006D` implies a 38.03 kHz period; the tool rounds against 37.9 kHz |
| Pioneer-2Part | 201 of 208 words: the lead-in mark and every bit mark and space | at 40 kHz a 564 us unit is 22.56 cycles; D6 rounds against the word's 25.09 us period to 22, the tool against the nominal carrier to 23. The 8-unit lead-in space and both gaps agree |

The Pioneer figure is a property of D6 rule 4 at 40 kHz and 564 us, not of
this protocol's constants; it does not occur at 38 kHz or 38.4 kHz, where the
two roundings agree for 564 us.
