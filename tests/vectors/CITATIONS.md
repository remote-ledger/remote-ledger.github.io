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

## Aiwa, Blaupunkt and the Kaseikyo family

Eight protocols registered together because SwiftRemote's database names
three of them under labels no published list contains -- `REC80`, `RCC2026`,
`RCC0082` -- and each turned out to be a known waveform, found by matching
timings and bit counts against IrpTransmogrifier's database. The app that
defined those names says it found no public definition
(`iodn/android-ir-blaster` `report-source.md`: "Evidence gap").

| DB name | Ledger protocol(s) | IRP source |
|---|---|---|
| `RCC0082` | Blaupunkt | `IrpProtocols.xml` L441 (release 1.2.14 and @`c945e76` alike), alternate name Motorola; also the fixture string in `ProtocolNGTest.java` L100 |
| `RCC2026` | Aiwa | `IrpProtocols.xml` L223; also, commented out, `ProtocolNGTest.java` L108 |
| `REC80` | Panasonic, JVC-48, Fujitsu, Teac-K, Denon-K, SharpDVD | `IrpProtocols.xml` L1939, L1219, L929, L2830, L511, L2500 (@`c945e76`: L1958, L1219, L929, L2849, L511, L2519) |

Every IRP string is verbatim from that file; the two commits agree on all
eight. `Kaseikyo` and `Aiwa2`, which the same file lists, are deliberately not
registered (see `protocols/kaseikyo.py` and `protocols/aiwa.py`).

### Gate 1 and gate 3: MET for all eight

`tests/test_kaseikyo.py` (six protocols), `tests/test_aiwa.py`,
`tests/test_blaupunkt.py`. Blaupunkt is swept exhaustively (512 frames); the
others sweep every field completely against awkward fixed values and add
20,000 seeded random frames, because Panasonic's 16 M and Denon-K's 1 M
frames are too many for pure Python. Each decoder is written from the IRP's
layout, not from the encoder. Checked separately, 200 randomly chosen and
boundary parameter sets across the eight were rendered by the
IrpTransmogrifier 1.2.14 release (`render -r`) and compared with the encoders'
intro and repeat: 200 of 200 identical.

### Gate 2a: MET for six, PENDING for two

A hardware capture or a published constant table, each with a decode that the
source itself asserts:

- **Panasonic** -- two sources. `crankyoldgit/IRremoteESP8266`
  `src/ir_Panasonic.cpp` @`1e2f0f3` L28-L35 and L104-L112 publish the timings
  (3456/1728, 432, 1296, and a 74,736 us gap, which is exactly 173 units) and
  the layout (16-bit manufacturer, device, subdevice, function, XOR), and our
  frame reproduces it exactly. **The two spell the bytes differently**: they
  send the 48-bit value MSB-first, the IRP sends each field LSB-first, so their
  `device` is the bit-reverse of the IRP's `D`. Their manufacturer `0x4004` is
  our vendor bytes 02 20, which is why every Panasonic code in the DB starts
  `4004`. Second, `Panasonic.ict` in IrpTransmogrifier's teaser tests, decoded
  by that project as `{D=176,S=0,F=54}`.
- **Aiwa** -- `Aiwa_left.ict`, `{D=8,S=0,F=21}`.
- **Blaupunkt** -- `Blaupunkt.ict`, key Ch+, `{F=21,D=2}`.
- **Teac-K** -- `Teac_0_4_Input.ict`, `{D=0,S=4,F=19}`, which carries the
  shorter `8,-8` repeat as well as the frame.
- **Denon-K** -- `Denon-K_Denon.ict`, `{D=4,S=1,F=28}`.
- **Fujitsu** -- `Fujitsu_pronto.txt`, a Pronto capture, `{D=132,F=0}`.

All of those files are IrpTransmogrifier's own test resources
(`src/test/teaserfiles`, pinned at `c945e76`) and are copied, one frame each,
to `irpt-teaser-captures.json`. A capture carries instrument bias, so these
verify layout and ratios (every duration within 12 %, 150 us), not absolute
durations. One real difference showed up: Blaupunkt's capture has a 20.6 ms
sync gap where the IRP says 45 units (23.0 ms), 10.7 % apart.

**JVC-48 and SharpDVD have no gate-2a evidence for their own bytes.**
Searched: IrpTransmogrifier's test resources, IRremoteESP8266, the DecodeIR
documentation, Flipper and Arduino-IRremote. Two things do exist.
`Arduino-IRremote` `src/ir_Kaseikyo.hpp` @`6158d65` L113-L117 publishes vendor
IDs 0x2002 Panasonic, 0x3254 Denon, 0x5AAA Sharp and 0x0103 JVC, which are the
fixed bytes (2,32), (84,50), (170,90) and (3,1) of Panasonic, Denon-K,
SharpDVD and JVC-48 read little-endian, and L99-L106 give the shared 432 us
unit and 8/4-unit header; Flipper's `infrared_protocol_kaseikyo_i.h` says the
same. That corroborates the vendor bytes and the frame, not the layout of the
remaining bytes. And `probonopd/irdb`, which lists both by name
with device codes -- JVC-48 for JVC receivers and CD players (device 34),
SharpDVD for the Sharp RRMCGA030WJSA (device 8, subdevice 48) -- and whose
SharpDVD keys agree with the SwiftRemote database code for code (digits 1-9 =
F 1-9, 0 = F 10, Up 32, Down 33, Left 34, Menu 27, Enter 28). That
corroborates the *parameter mapping*, not the waveform. Both share the
Panasonic frame, whose waveform is verified. What is not verified is the
layout after the vendor bytes (JVC-48's is Panasonic's, SharpDVD's is not)
and, for SharpDVD, its 400 us unit, 38 kHz carrier and 48-unit gap.

### Gate 2b: MET for all eight, every one reproducible, none published

| Protocol | Vector | Provenance | Our bytes differ at |
|---|---|---|---|
| Aiwa | D=8 S=0 F=21 @ 38.123k | reproducible | words 5, 91, 93, 95 |
| Blaupunkt | D=2 F=21 @ 30.3k | reproducible | 40 words: see below |
| Panasonic | D=176 S=0 F=54 @ 37k | reproducible | word 103 (lead-out) |
| JVC-48 | D=34 S=33 F=12 @ 37k | reproducible | word 103 |
| Fujitsu | D=132 S=132 F=0 @ 37k | reproducible | word 103 |
| Teac-K | D=0 S=4 F=19 @ 37k | reproducible | words 103, 107 |
| Denon-K | D=4 S=1 F=28 @ 37k | reproducible | word 103 |
| SharpDVD | D=8 S=48 F=1 @ 38k | reproducible | none: bytes identical |

All are `render -n ... -p <protocol>` from the IrpTransmogrifier 1.2.14
release, which also reproduces the published NEC1 strings exactly. Our
timings, quantized by that tool's own rule, reproduce each vector word for
word. `test_registry` warns about all eight on every run, as it does for
NECx2. **No published vector with stated parameters was found for any of
them**: IrpTransmogrifier's tests assert decodes of captures, not Pronto
strings it generated.

Two published Pronto strings exist and are not usable as gate 2b, because
they are captures and cannot reproduce under an exact rule: `GRAHAM_PANASONIC`
(`IrpTransmogrifierNGTest.java` L29, decoded at L581 as Panasonic
`{D=176,S=16,F=17}`; a shorter gap and carrier word 0x71 against our 0x70) and
`Fujitsu_pronto.txt`. Both are used for gate 2a.

**Blaupunkt's 40 differing words are one rounding case, not an error.** At
30.3 kHz a 512 us unit is 15.51 cycles. IrpTransmogrifier rounds each duration
against the nominal carrier (16 cycles, 528 us on playback); D6 rule 4 rounds
against the period the frequency word implies (15 cycles, 496 us). Neither is
wrong; they land 3 % either side of 512, and every unit-length run differs.

### What is not proven

- Eight encoders ship on IrpTransmogrifier's definitions and a render of
  IrpTransmogrifier; none has a published Pronto vector.
- Blaupunkt's closing sync cannot be carried (`IrSignal.ending` is reserved,
  D1). The ledger drops it as IrpTransmogrifier's own Pronto does, which warns.
- Fujitsu `E`, Teac-K `X` and SharpDVD `E` are IRP parameters a remote file
  cannot name; the encoders fix them at the IRP default and say so.
