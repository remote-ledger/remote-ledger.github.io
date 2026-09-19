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

Discrepancy recorded rather than smoothed over: this source gives the carrier
as **38.0 kHz**, while DESIGN.md D18's table records 38.4 kHz. Both appear in
the wild. It changes no output — `nominal_carrier_hz` is informational and a
file's `protocol.carrierHz` is authoritative (D3) — but D18's table should be
corrected to match a citation rather than a recollection.

### Gate 2 — independently cited golden Pronto string: **PENDING**

Not satisfied. No published, complete NEC1 Pronto Hex string with stated
protocol parameters has been obtained, so the byte-level claim — "this
encoder reproduces what other tools produce" — is **unproven**.

What *is* independently confirmed is the frequency-word formula, which the
emitted header depends on:

> "Frequency = 1000000/(N \* .241246)" where N is the decimal value of the
> second hex word of the preamble.

Source: <http://www.remotecentral.com/features/irdisp2.htm> — Remote
Central, "Infrared Remote Control Codes, Part 2". This is the citation for
D6 rule 2 and for the `0.241246` constant.

**Open question that page raises, for Phase 3 (Sony20).** It gives Sony's
word as `N = 103` (`0x0067`) for a 40 kHz carrier. The exact quotient is
103.6287, so `0x0067` implies **truncation**, while D6 rule 5's
`ROUND_HALF_UP` yields `104` (`0x0068`). Both words appear in published Sony
codes. This does **not** affect NEC1: at 38 kHz the quotient is 109.0828, so
truncation and round-half-up both give `0x006D`. It must be resolved against
a cited Sony vector before `Sony20` ships — see DESIGN.md §10.

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

Gate 2a is **PENDING** for Sony20: no independent implementation's Sony20
timing constants have been obtained.

### Gate 2b: **PENDING** for all three

Still no published complete Pronto string with stated parameters. The
formula is cited (Remote Central, above); the open question is its rounding
direction, since that page's Sony word `0067` implies truncation where D6
rule 5 mandates round-half-up (`0068`).
