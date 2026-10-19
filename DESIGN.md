# Remote Ledger — Design & Build Plan

**Draft v2.1** · Status: v1 complete; all three seed remotes authored; LIRC, SmartIR and IR Blaster imports built (§14, §15, §17); 41 protocols (§18, §37, §41, §43, §45, §47, §52, §55, §57) · Implements [SPEC.md](SPEC.md) v0.11

SPEC.md says *what* the format has to hold and why. This says *how it gets
built*: the resolved open decisions, the one intermediate representation
everything funnels through, the protocol encoders, the repo layout, and a
seven-phase plan where each phase ends in something you can run.

Every design decision below is numbered **D*n*** and names the spec
requirement (**R*n*** / **OD*n***) it serves. Numbers are stable
identifiers assigned in the order decisions were *made*, not the order they
appear — D16–D20 came out of the v0.1 review, D21–D26 out of the v0.2
review, D27–D29 out of the v0.3 review, and D30–D33 out of the v0.4 review,
each sitting wherever it belongs topically. D34–D45 came with the LIRC and
SmartIR imports (§14, §15), D46–D56 with the IR Blaster importer (§17) and
D57–D68 with the protocols it needed (§18), D69–D71 for the index (§19), D72–D73 for the build driver (§20), D74–D82 for the app API (§21), D83–D87 for the canonical key vocabulary (§22) and D88–D95 for the catalog bundle (§23) and D96–D99 for finding a device in it (§24).
§11 lists what changed.

---

## 1. Resolved open decisions

SPEC §12 left six decisions open. All six are now closed. The spec's own
framing is kept where it held up; where a decision changes the spec, §9
lists the edits to make.

| # | Decision | Resolution | Consequence |
|---|---|---|---|
| OD1 | Personal tool, or open to contributions? | **Personal tool** for v1 | No PR/review workflow, no CONTRIBUTING. Citations (R18) still hold to the same standard — they're what makes the data re-checkable by *you*, later. Reversible without a rewrite. |
| OD2 | Git-only, or hosted lookup surface? | **Both** — R16 script *and* R17 site | Site is a generated static page with no framework and no server, so upkeep stays near zero. See §7. |
| OD3 | Is `raw` first-class? | **Yes** | Third encoder path, its own symmetric comparison tolerance (D8), and an explicit truncation flag (D4a). Most public captures are lircd-style microsecond timings; normalizing at authoring time would discard the evidence the citation points at. |
| OD4 | Index committed, or on demand? | **Committed** to `build/` | Diffable, browsable without running anything. CI regenerates and fails on drift, so it can't go stale (D13). |
| OD5 | Does `untested` belong in the format? | **Yes** | The BX510 fallback subdevices are useful *as open questions*. Without the tier they'd have to masquerade as working codes. The tier alone isn't enough to *represent* them, though — see D16. |
| OD6 | Custom layouts committed, or schema-only? | **Schema-only** | The repo commits `original: true` layouts (an objective, citable fact, R8). Personal rearrangements use the identical shape but live in an app's local storage. Follows directly from OD1. |

**Stack:** Python 3.12+. Runtime dependencies: `jsonschema` only. Dev adds
`pytest`. No JVM, no Node for the core toolchain.

**Protocol engine:** hand-written encoders, no external oracle. §5 covers
what that costs and how the test strategy pays for it. (The oracle tools the
imports added, §14, §15 and §17, are development tools that compare an
importer with another program's output; none runs in a build, and D68 says
how far a generated vector can be trusted.)

---

## 2. Architecture in one picture

Everything — all three form types, the cross-check, and the Pronto output —
funnels through a single intermediate representation. That is the whole
architecture; the rest is detail.

```
  remotes/<mfr>/<model>.json
          │
          ▼
   ┌─────────────┐   schema + semantic checks (R15)
   │  validate   │───────────────────────────────► errors
   └─────────────┘
          │
          ▼  for each key, for every NON-DERIVED form (D5a)
   ┌───────────────────────────────────────────┐
   │  form → IrSignal                          │
   │    irp    → protocols/<name>.encode()     │  D3
   │    raw    → parse µs list                 │  D4
   │    pronto → pronto.decode()               │  D5
   └───────────────────────────────────────────┘
          │
          ├──────────────► cross-check within each candidate group (R13, D8, D16)
          │                `derived` forms bypass this — string-diffed by D9
          │
          ▼  trusted form per candidate group (R6 precedence, D7)
   ┌─────────────┐
   │ pronto.enc  │  D6
   └─────────────┘
          │
          ▼
  build/pronto/**  +  build/warnings.json  +  build/index.json  +  site/
```

**D1 — One intermediate representation: `IrSignal`.** Serves R12, R13.

```python
@dataclass(frozen=True)
class IrSignal:
    carrier_hz: int          # strictly positive — see D1a
    intro:  tuple[int, ...]  # µs, alternating mark/space, even length
    repeat: tuple[int, ...]
    ending: tuple[int, ...]  # sent once when the key is released (D217); empty for every protocol but the ones whose remotes send a final frame
```

Durations are integer microseconds, always starting with a mark, always
even length (a sequence ends on a space — the trailing gap is part of the
signal, not an afterthought).

Why this matters: because `pronto.decode()` also produces an `IrSignal`,
cross-checking a stored Pronto string against a parametric form is the same
code path as checking two parametric forms. There is exactly one comparison
function, not one per form-type pair.

**D1a — Unmodulated signals are rejected in v1, not represented.** Serves
R12, R15. v0.1 declared `carrier_hz = 0` as "unmodulated" while D6's
encoding divides by the carrier and defines only the modulated `0000`
output — a representable state with no defined behavior. Rather than leave
that gap: `carrier_hz` must be strictly positive, the validator rejects
`carrierHz <= 0` with a message naming this decision, and `IrSignal`
enforces it in `__post_init__`.

Pronto does have an unmodulated format — word 0 is `0100`, with the
frequency word retained as a bare timebase — so this is deferrable, not
impossible. It stays deferred because no protocol in the v1 registry (D18)
emits one, so under D10's gate there would be no cited vector to prove the
encoder right. The day a genuinely unmodulated remote needs an entry, add
`0100` support *and* its vector together.

**D2 — Every stage is a pure function.** Serves R12. No form renderer reads
the clock, the filesystem, or a random source. `compile(json) → pronto` is
referentially transparent, which is what makes "byte-identical output"
testable rather than aspirational.

---

## 3. The form renderers

### D3 — `irp` forms: a protocol registry

Serves R4, R12. A registry entry is a **record with machine-readable
metadata**, not just a function — v0.3 put the IRP string in a docstring,
which left D4a's extent lookup and D18's gate as prose that validation
would have had to read:

```python
@dataclass(frozen=True)
class Protocol:
    name: str                 # registry key, e.g. "NEC1"
    irp: str                  # the IRP string, verbatim
    irp_source: str           # where it came from — D18 gate 1, now checkable
    unit_us: int              # 564
    nominal_carrier_hz: int   # 38400 — INFORMATIONAL ONLY, see below
    extent_us: int | None     # ^108m → 108_000; None when the protocol declares none
    bits: int                 # 32 — for the invariant test, D18 gate 3
    encode: Callable[..., IrSignal]
```

```python
def encode(*, device: int, subdevice: int | None, function: int,
           carrier_hz: int, unit_us: int | None) -> IrSignal
```

The remote's `protocol` block (R3) supplies `carrier_hz` and optionally
overrides `unit_us`; the form supplies the parameters.

Making `extent_us` a field is what lets D4a substitute a truncated
capture's gap without inspecting prose. Making `irp_source` a field turns
D18's first gate from a convention into a test: a registry entry with an
empty `irp_source` fails the suite. Extents for the v1 registry:
`NEC1` 108 000 µs, `Sony20` 45 000 µs, `NECx2` 108 000 µs. (v0.1 listed
`Samsung32` here, which never existed; see D18.) Of the 28 protocols the
registry holds since §18, 13 declare an extent and 15 declare none, because
their IRPs pad with a fixed gap (`-80`, `-173`) or carry several extents inside
one sequence, which D31's single figure cannot express.

**Carrier ownership, since v0.4 left two numbers in play.** The registry
said `default_carrier_hz: 38400` for NEC1 while D24 required a file-level
`carrierHz` and the example used `38000`, with nothing saying which won. It
is the file's, without exception:

- **`protocol.carrierHz` is required and authoritative.** Every encoder and
  every comparison uses it (D8 step 1). There is no fallback path.
- **`nominal_carrier_hz` is informational only** — it records what the IRP
  definition says, for documentation and to prefill a new file. It is never
  consulted at compile time, and it is renamed from `default_` precisely
  because that prefix invited the confusion.

R3 puts carrier at the remote level as a fact about the *hardware*, and real
remotes deviate from a protocol's nominal figure — 38 kHz against NEC1's
nominal 38.4 kHz is the single most common such deviation, and it is exactly
the Topping case. A deviation large enough to change the frequency word
(D6) raises a **warning** (`carrier-off-nominal`, D32), not a `claims`
requirement: `carrierHz` overrides no default, it *is* the value, and
demanding a citation for 38000-vs-38400 on most NEC files would be
boilerplate that devalues `claims` (D27) wherever it genuinely matters.

**D18 — The v1 registry is exactly what the seed data proves, and it grows
one cited protocol at a time.** Serves R12, R19, D10.

v0.1 declared eight protocols while the build plan delivered three. The
five undelivered ones — `NEC2`, `NEC`, `Sony12`, `Sony15`, `RC5` — had no
phase, no owner, and no cited vector, which violates D10's own gate in the
same document that states it. (They become **six** in the backlog below,
where `RC6` joins them; v0.1 had already excluded RC6 from the registry, so
it was never one of the undelivered five.) Narrow the registry to what the
seed data actually exercises:

| Name | IRP definition | Covers | Phase |
|---|---|---|---|
| `NEC1` | `{38.4k,564}<1,-1\|1,-3>(16,-8,D:8,S:8,F:8,~F:8,1,^108m,(16,-4,1,^108m)*)` | Topping RC-15A | 1 |
| `NECx2` | `{38.0k,564}<1,-1\|1,-3>(8,-8,D:8,S:8,F:8,~F:8,1,^108m)+` | Samsung BN59-01199F | post-6 |
| `Sony20` | `{40k,600}<1,-1\|2,-1>(4,-1,F:7,D:5,S:8,^45m)+` | Sony RMT-B118P | 3 |
| `RC5` | `{36k,msb,889}<1,-1\|-1,1>((1,~F:1:6,T:1,D:5,F:6,^114m)*,T=1-T)[D:0..31,F:0..127,T@:0..1=0]` | Meridian MSR | post-v1 (§16) |
| `NEC2` | `{38.4k,564}<1,-1\|1,-3>(16,-8,D:8,S:8,F:8,~F:8,1,^108m)*` | DB `NEC2` | §18 (D61) |
| `NECx1` | `{38.4k,564}<1,-1\|1,-3>(8,-8,D:8,S:8,F:8,~F:8,1,^108m,(8,-8,~D:1,1,^108m)*)` | DB `NECx1` | §18 (D61) |
| `Sony12` | `{40k,600}<1,-1\|2,-1>(4,-1,F:7,D:5,^45m)*[D:0..31,F:0..127]` | DB `SONY12` | §18 (D62) |
| `Sony15` | `{40k,600}<1,-1\|2,-1>(4,-1,F:7,D:8,^45m)*[D:0..255,F:0..127]` | DB `SONY15` | §18 (D62) |
| `RC6` | `{36k,444,msb}<-1,1\|1,-1>((6,-2,1:1,0:3,<-2,2\|2,-2>(T:1),D:8,F:8,^107m)*,T=1-T) [D:0..255,F:0..255,T@:0..1=0]` | DB `RC6` | §18 (D63) |
| `RCA-38` | `{38.7k,460,msb}<1,-2\|1,-4>(8,-8,D:4,F:8,~D:4,~F:8,1,-16)*[D:0..15,F:0..255]` | DB `RCA_38` | §18 (D63) |
| `Thomson7` | `{33k,500}<1,-4\|1,-9>((D:4,T:1,F:7,1,^80m)*,T=1-T) [D:0..15,F:0..127,T@:0..1=0]` | DB `Thomson7` | §18 (D63) |
| `Pioneer-2Part` | `{40k,564}<1,-1\|1,-3>(16,-8,D0:8,~D0:8,F0:8,~F0:8,1,^90m,(16,-8,D:8,~D:8,F:8,~F:8,1,^90m)+) [D0:0..255,F0:0..255,D:0..255=D0,F:0..255=F0]` | DB `Pioneer` | §18 (D64) |
| `JVC` | `{37.9k,527,33%}<1,-1\|1,-3>(16,-8,D:8,F:8,1,^59.08m,(D:8,F:8,1,^46.42m)*) [D:0..255,F:0..255]` | DB `JVC` | §18 (D64) |
| `Sharp` | `{38k,264}<1,-3\|1,-7>(D:5,F:8,1:2,1,^67m,(D:5,~F:8,2:2,1,^67m,D:5,F:8,1:2,1,^67m)*)[D:0..31,F:0..255]` | DB `Sharp` | §18 (D64) |
| `Denon` | `{38k,264}<1,-3\|1,-7>(D:5,F:8,0:2,1,^67m,(D:5,~F:8,3:2,1,^67m,D:5,F:8,0:2,1,^67m)*)[D:0..31,F:0..255]` | DB `Denon` | §18 (D64) |
| `Samsung36` | `{37.9k,560,33%}<1,-1\|1,-3>(4500u,-4500u,D:8,S:8,1,-9,E:4,F:8,~F:8,1,^108m)*[D:0..255,S:0..255,F:0..255,E:0..15]` | DB `Samsung36` | §18 (D65) |
| `Proton` | `{38.5k,500}<1,-1\|1,-3>(16,-8,D:8,1,-8,F:8,1,^63m)*[D:0..255,F:0..255]` | DB `Proton` | §18 (D65) |
| `F12_relaxed` | `{37.9k,422}<1,-3\|3,-1>(D:3,S:1,F:8,-80)*  [D:0..7,S:0..1,F:0..255]` | DB `F12_relaxed` | §18 (D65) |
| `RECS80` | `{38k,158,msb}<1,-31\|1,-47>(1:1,T:1,D:3,F:6,1,-45m)* {}[D:0..7,F:0..63, T@:0..1=0]` | DB `RECS80` | §18 (D65) |
| `RECS80-0068` | `{33.3k,180,msb}<1,-31\|1,-47>(1:1,T:1,D:3,F:6,1,^138m)* [D:0..7,F:0..63, T@:0..1=0]` | DB `RECS80_L` | §18 (D65) |
| `Aiwa` | `{38.123k,550}<1,-1\|1,-3>(16,-8,D:8,S:5,~D:8,~S:5,F:8,~F:8,1,-42,(16,-8,1,-165)*)[D:0..255,S:0..31,F:0..255]` | DB `RCC2026` | §18 (D66) |
| `Blaupunkt` | `{30.3k,512}<-1,1\|1,-1>(1,-5,1023:10, -44, (1,-5,1:1,F:6,D:3,-236)+ ,1,-5,1023:10,-44)[F:0..63,D:0..7]` | DB `RCC0082` | §18 (D66) |
| `Panasonic` | `{37k,432}<1,-1\|1,-3>(8,-4,2:8,32:8,D:8,S:8,F:8,(D^S^F):8,1,-173)* [D:0..255,S:0..255,F:0..255]` | DB `REC80` | §18 (D66) |
| `JVC-48` | `{37k,432}<1,-1\|1,-3>(8,-4,3:8,1:8,D:8,S:8,F:8,(D^S^F):8,1,-173)* [D:0..255,S:0..255,F:0..255]` | DB `REC80` | §18 (D66) |
| `Fujitsu` | `{37k,432}<1,-1\|1,-3>(8,-4,20:8,99:8,0:4,E:4,D:8,S:8,F:8,1,-110)* [D:0..255,S:0..255=D,F:0..255,E:0..15=0]` | DB `REC80` | §18 (D66) |
| `Teac-K` | `{37k,432}<1,-1\|1,-3>(8,-4,67:8,83:8,X:4,D:4,S:8,F:8,T:8,1,-100,(8,-8,1,-100)*) {T=D+S:4:0+S:4:4+F:4:0+F:4:4} [D:0..15,S:0..255,F:0..255,X:0..15=1]` | DB `REC80` | §18 (D66) |
| `Denon-K` | `{37k,432}<1,-1\|1,-3>(8,-4,84:8,50:8,0:4,D:4,S:4,F:12,((D*16)^S^(F*16)^(F:8:4)):8,1,-173)* [D:0..15,S:0..15,F:0..4095]` | DB `REC80` | §18 (D66) |
| `SharpDVD` | `{38k,400}<1,-1\|1,-3>(8,-4,170:8,90:8,15:4,D:4,S:8,F:8,E:4,C:4,1,-48)*{C = D ^ S:4:0 ^ S:4:4 ^ F:4:0 ^ F:4:4 ^ E:4}[D:0..15,S:0..255,F:0..255,E:0..15=1]` | DB `REC80` | §18 (D66) |

> ⚠️ **`Samsung32` was struck from this table: it never existed — and the
> question it stood for is now answered.** v0.1 carried
> `{38.4k,564}<1,-1\|1,-3>(9,-9,D:8,S:8,F:8,~F:8,1,^108m)*` for it, written
> from memory during design, and it survived nine review rounds because
> every round read this document rather than a source.
>
> The BN59-01199F speaks **`NECx2`**. IRDB records the shared Samsung TV
> address as `NECx2`, device 7, subdevice 7; DecodeIR gives NECx2's IRP
> verbatim as `{38.0k,564}<1,-1|1,-3>(8,-8,D:8,S:8,F:8,~F:8,1,^108m)+` and
> notes that most NECx2 signals have `S = D`; and IRremoteESP8266's SAMSUNG
> implementation independently gives an **8**-tick lead-in with the layout
> *customer byte, the same customer byte again, command, inverted command*.
> Three sources, one protocol, and it is an NEC variant rather than a
> Samsung-specific one. The fabricated entry's `9,-9` was the tell.
>
> This also retires §10's "disputed lead-in" for good: the ~4500 µs real
> captures were an **8**-unit lead-in, not a 9-unit one at a different tick.

The first three rows are the whole v1 registry (`RC5`, the fourth, joined for
§16), and the narrowness was the point: it matches the project's own premise that coverage is
built one lookup at a time (R19), rather than declared up front and
half-delivered. **The registry now holds 28.** The other 24 rows were added in
one stretch for the SwiftRemote database import (§17), because a database code
whose protocol the registry does not hold cannot become an `irp` form, and
each went through the three gates below: §18 records each family's decisions,
and §12 each protocol's gate-2b vector. Every IRP string above is the
registry's own, verbatim. DB `NEC` lands on `NEC1`, `NECx2` on `NECx2`, `SONY20`
on `Sony20` and `RC5` on `RC5`.

That growth bent the premise, and it should be said where the rule is stated.
It was "one lookup at a time"; it was 24 protocols in one go, for a corpus of
413,331 keys. What did not bend is the gate. Twenty-two of the 28 have a gate-2b
vector that only a pinned tool release can generate (D68), and three of those
have no gate-2a evidence either: the gate held, but on weaker evidence than
the v1 protocols' and the table in §12 says which.

**Adding a protocol is a self-contained change** requiring all three of:

1. Its IRP string in the module docstring, with the source it came from.
2. At least one **independently cited** golden vector (D10).
3. An invariant test — framing, bit count, total extent.

Backlog, each blocked on that gate and none scheduled: `NEC` (`S` defaulted
to `~D`). The relaxed `NEC1-f16` and `NEC2-f16`, which hold 635 of the
database's codes that no other registered NEC variant can (D67), joined in §37.
`RC5` left the
backlog in §16, and `NEC2`, `Sony12`, `Sony15` and `RC6` in §18, each through
the gate.

`NEC2` and `NEC` were the tempting ones — both are a few lines' difference from
`NEC1`, and waving them through on that basis is precisely how an unverified
encoder ships. The gate applied to `NEC2` identically (D61), and applies to `NEC`
and the `-f16` forms. v0.1 said RC6 was further out because its trailer bit is
double-width and needs a bitspec exception none of the other protocols require.
**That was wrong as built** (D63): the encoder builds the frame as per-unit
levels and run-length encodes them, and the registry's `encode` interface is
unchanged. RC6 is registered for mode 0 only.

**D23 — One protocol per remote file in v1.** Serves R3, D17, D20.

`variants.override.protocol` had nowhere to land: dispatch reads the
remote-level `protocol` block (R3), and the compiled artifact (D20) stores
exactly one. Supporting a per-variant protocol means per-candidate protocol
metadata in the schema *and* in every consumer of the output — a much larger
change than the seed data justifies.

So, as a rule rather than an accident: **a remote file declares exactly one
protocol.** A form may not override it, and `variants.override` accepts only
`device`, `subdevice`, and `function`. The validator rejects a `protocol`
key in an override, naming this decision.

Known limit, in the open: a remote whose buttons speak two protocols — some
universal remotes switch protocol by mode — can't be one file in v1. The
fallback is the one R7 and R20 already use elsewhere: it gets an entry for
the protocol that is known, and the rest is recorded as absent rather than
guessed. Lifting the limit is a schema change (per-candidate `protocol`)
plus a compiled-output change (`protocol` moves inside each candidate),
deferred until a real file needs it.

**D24 — The `protocol` block, in full.** Serves R3, D4a, D8.

v0.2 introduced `defaultGapUs` and `protocol.tolerance` in passing, with no
shape and no ownership rule. Both live here:

```json
"protocol": {
  "name": "NEC1", "carrierHz": 38000, "minSends": 1, "unitUs": 564,
  "defaultGapUs": 13000,
  "tolerance": { "absUs": 100, "relative": 0.15, "gapUs": 150 },
  "claims": {
    "unitUs":       { "reason": "…", "source": "…" },
    "defaultGapUs": { "reason": "…", "source": "…" },
    "tolerance":    { "reason": "…", "source": "…" }
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `name` | no | A key in the D18 registry, or omitted for a capture whose protocol isn't identified yet |
| `carrierHz` | **yes** | Strictly positive (D1a) |
| `minSends` | **yes** | Hardware fact, carried to output, never baked into the waveform (D3a) |
| `unitUs` | no | Overrides the registry's IRP unit; requires a `claims` entry (D27) |
| `defaultGapUs` | conditional | Terminal gap for a `truncated` raw form (D4a) when `extent_us` is `None`; requires a `claims` entry |
| `tolerance` | no | Overrides D8's defaults; requires a `claims` entry |
| `claims` | conditional | Citation sidecar — required for each overriding field present (D27) |

Two conditional rules the validator enforces:

- **`name` omitted ⇒ no `irp` forms.** Nothing can dispatch an encoder
  without a protocol, so a file with an unidentified protocol is `raw`/
  `pronto` only. That's the legitimate state D4 describes — a capture that
  landed before anyone worked out what it was.
- **`defaultGapUs` is required exactly when** a `truncated` raw form exists
  *and* `name` is omitted or its registry entry has `extent_us = None`. All
  three v1 protocols declared an extent, so in practice this only bit
  raw-only files — which is exactly where truncation is most likely. Since §18
  twenty-three of the 41 protocols declare none (D3), so it now bites a file that
  names one of them as well. A truncated form with neither an extent nor
  `defaultGapUs` is a validation error, never a guess (D4a).

**D27 — Anything that loosens a check carries a citation, not just a
reason.** Serves R5, R18, D24.

v0.3 made `tolerance.reason` schema-required and called that the enforceable
form of "the override is itself a citable claim." It isn't: "worn remote
with visible jitter" explains a choice to a reader but is not
*independently checkable*, which is the whole of what R18 demands. A reason
justifies; only a source lets someone else re-derive.

So a uniform sidecar, `claims`, valid on the `protocol` block and on any
form:

```json
"claims": { "<field>": { "reason": "why this deviates", "source": "<citable>" } }
```

**Every field that overrides a default or widens what passes requires a
`claims` entry with both keys non-empty.** In v1 that set is exactly:

| Field | Level | What it widens |
|---|---|---|
| `unitUs` | protocol | Replaces the registry's IRP unit — changes every emitted duration |
| `defaultGapUs` | protocol | Supplies a gap no extent vouches for |
| `tolerance` | protocol | Loosens the one check meant to catch a wrong code |
| `truncated` | `raw` form | Exempts the terminal gap from comparison (D4a) |

One sidecar in one shape, so the validator rule is mechanical — "for each
field in the override set present here, require `claims[field].reason` and
`claims[field].source`" — and it generalizes: **any field added later that
widens what passes joins the set**, which is the rule worth keeping more
than the list.

**D3a — `minSends` is metadata, not geometry.** Serves R3. The encoder emits
the intro and repeat sequences *once each*. `protocol.minSends: 3` is
recorded in the compiled output for the playback app to honor; the compiler
never multiplies the repeat sequence into the Pronto string. Baking it in
would make the same code compile differently depending on a field that
describes the hardware, not the waveform.

**D3b — Open: the toggle bit of RC5, RC6, Thomson7, RECS80 and RECS80-0068.**
RC5's `T` flips on each press, so one Pronto string can carry only one of its
two states. RC5 landed in §16 and emits `T=0`, the IRP's own default, with no
marker: the compiled artifact (D20) still has no toggle field, and a remote
file cannot set one (`encode` takes `toggle` for the tests only). A player
therefore sends the same `T` on every press. Whether that matters depends on
the receiver, and nothing the ledger holds says how a given unit treats a
repeated `T`. The open choice is unchanged: emit a `toggle: true` marker for
the player to alternate, or emit both states as two candidate groups (D16),
which reuses machinery that already exists. Either needs the player to
alternate, so it is a contract with the apps, not a ledger change alone.

§18 made it five protocols. RC6 (D63), Thomson7 (D63), RECS80 and RECS80-0068
(D65) have a toggle too, and the database stores none, so every imported key
of those protocols compiles `T=0`. SwiftRemote alternates `T` itself, and its
preview shows `T=1`: for 47,137 keys of RC5, RC6, RECS80 and RECS80-0068 the
compiled signal matches the app's only at `T=1`, which the oracle counts as
its own allowance and not as a match (D58).

### D4 — `raw` forms: lircd-style microsecond lists

Serves R4, OD3. `{"type": "raw", "intro": [9024, 4512, 564, ...], "repeat":
[...], "carrierHz": 38000}`. Validation: all positive, first element is a
mark, even length unless `truncated` (D4a). `carrierHz` falls back to the
remote's `protocol` block. This is the form a capture lands in before anyone
has worked out which protocol it is — a file can be useful with nothing but
`raw` forms.

**D4a — Truncation is declared, never inferred.** Serves R13, OD3. A lircd
capture routinely omits or mangles its final space, because the recorder
stops when the button is released. v0.1 exempted "a truncated capture" from
gap comparison without defining a field to say so or a rule to detect it —
an unimplementable exemption. Make it an explicit claim: `"truncated": true`
on a `raw` form declares that its final space is not evidence. Then:

- A truncated form may have odd length, ending on a mark; every other form
  must be even.
- At load, the missing or untrustworthy final space is replaced per D31's
  formula, using the registry entry's `extent_us` (D3) — never by parsing an
  IRP string or a docstring. A truncated form with neither an extent nor
  `protocol.defaultGapUs` is a validation error, not a guess.
- D8 skips the terminal-gap comparison when either side is truncated.
- **`truncated` requires a `claims` entry** (D27) with both a `reason` and
  an independently checkable `source`. It widens what passes, so it carries
  the same burden as any other loosened check.

Nothing infers truncation from the data. An undeclared short final gap is a
mismatch, and fails.

**D31 — The gap substitution, as a formula.** Serves R12, D4a.

"The gap implied by `extent_us`" left three questions open, each of which
changes the result. Answering all three:

*It applies per sequence, independently.* In IRP an extent sits **inside** a
sequence — `(16,-8,…,1,^108m,(16,-4,1,^108m)*)` carries one in the intro and
another in the repeat — so intro and repeat are each padded to their own
extent. They are never summed.

*The existing final space is dropped, not adjusted.* For each sequence
`d₁…dₙ` in a `truncated` form:

```
m    = n if n is odd else n − 1     # count of durations KEPT; the declared-bad
                                    # final space, if present, is discarded
head = d₁ + … + d_m                 # a duration sum, in µs

gap  = extent_us − head             # branch A: the protocol declares an extent
gap  = protocol.defaultGapUs        # branch B: it does not

sequence = (d₁, …, d_m, gap)
```

`m` is an index and `head` a sum of microseconds — v0.5 wrote
`d₁ … d_head`, which conflated the two and named no such element.

Discarding rather than adjusting is the point of the flag: a declared-bad
value is not evidence, so it contributes nothing — not even a lower bound.

*An over-extent capture is an error, never a clamp — and only branch A can
produce one.* Let
`effective_unit_us = protocol.unitUs if present else registry.unit_us`. If
`gap < effective_unit_us` — including any negative value, the case where the
marks alone already consume the extent — validation fails, naming the
observed head and the extent. A clamp would fabricate a waveform that
doesn't satisfy the extent it claims; the real meaning of that arithmetic is
that the capture is corrupt or the assumed protocol is wrong, and both
deserve a human. The floor is one protocol unit, a loose sanity bound: real
NEC1 gaps run 20–58 ms against a 564 µs unit, so it only catches genuine
breakage.

**Why `effective_unit_us` is always defined where it's used.** Branch A is
reachable only when a registry entry exists, since that is where
`extent_us` comes from (D3) — so `registry.unit_us` is always available
there, and the floor was never actually undefined. Branch B computes
nothing: `defaultGapUs` is stated outright and D28 bounds it to
1–1 000 000 µs, so there is no subtraction to go negative and no floor to
define. A raw-only file with an unidentified protocol takes branch B *by
construction* — no `name` ⇒ no registry entry ⇒ no extent (D24) — which is
exactly the case that read as underspecified.

After substitution the sequence is even-length and D28's bounds apply to the
substituted gap like any other duration.

*An absent sequence and an empty one are different things.* With `n = 0`,
`m = n − 1 = −1` and the formula is meaningless — so an empty sequence is
rejected rather than defined. **A sequence key that is present must hold at
least one duration; `[]` is a validation error, and the way to say "no
repeat sequence" is to omit `repeat` entirely.** This is the same
distinction Pronto draws and that D25 relies on: `n1 = 0` in a header means
the intro is *absent*, not present-and-empty. Stating it here keeps D31's
arithmetic total — every sequence it sees has `n ≥ 1`, so `m ≥ 0` — without
a special case inside the formula.

**Why substitute at all**, given D8 skips the terminal-gap comparison for a
truncated form: because the *compiled output* needs a complete waveform. A
Pronto string has an even burst-pair count and a real trailing gap (D6), so
emission needs a value even though comparison ignores it. The substitution
serves D6, not D8 — which is also why getting it wrong would be invisible to
the cross-check and is worth pinning down here.

### D5 — `pronto` forms: decode, don't trust

Serves R4, R13. A stored Pronto string that is *evidence* — a hand-entered
string from a forum post — is parsed back into an `IrSignal` like any other
form and faces the same cross-check (D8). A `derived` one is not.

**D5a — `derived` forms are outside the signal pipeline entirely.** Serves
R13, D7, D9, D25.

v0.3 said three different things: the architecture diagram rendered *every*
form to an `IrSignal`, D5 said a stored Pronto is decoded "like any other
form," D25 said derived forms are never decoded, and D9 checked them by
string equality. One rule, stated once:

> A `derived` form is never decoded, never rendered to an `IrSignal`, and
> never cross-checked by D8. It is validated **solely** by D9.

The reason it isn't a special case so much as a category difference: a
derived form is by definition the compiler's own earlier output, so the only
meaningful question about it is "is this still what the compiler emits."
That question is answered exactly by byte-comparing canonical strings — a
strictly stronger check than comparing decoded signals, and one that
sidesteps D25's lossy cycles → µs direction.

Combined with D7 already excluding `derived` from selection, a derived form
now participates in exactly one thing (D9) and nothing else. That is the
whole of its role, and it's why storing one is free rather than risky.

**D30 — What a `derived` form is, exactly — and why it carries no
`source`.** Serves R4, R5, R18, D5a, D9.

The examples showed a derived form with no `source` while SPEC R5 requires
every form to name how it was established. One of the two had to change, and
it's the spec — because a derived form's citation is `derivedFrom`, and it
is the *best* citation in the format.

```json
{ "type": "pronto", "confidence": "derived",
  "derivedFrom": "primary.irp",
  "hex": "0000 006D 0022 0002 0157 00AC …" }
```

| Field | Rule |
|---|---|
| `type` | **Must be `pronto`.** The compiler's only output is Pronto Hex (R12, D6), so nothing can produce a derived `raw` or `irp` form. A `confidence: "derived"` form of any other type is a validation error |
| `confidence` | `"derived"` |
| `derivedFrom` | **Required** — a form id in the same candidate group, itself non-derived (D21) |
| `hex` | Required |
| `source` | **Forbidden**, not merely optional |

`source` is forbidden rather than optional because a second, human-written
claim about mechanically produced data could only ever be unverifiable
decoration — and, worse, could contradict the mechanical one.

**Does `derivedFrom` satisfy R5?** Yes, and R18 already anticipates this
shape: it accepts "a same-repo cross-reference (*re-derived from `<file>`'s
protocol, see commit `<sha>`*)" as an independently checkable citation. A
`derivedFrom` id is that, in machine-readable form — and it is the only
citation in the entire ledger that **CI actually verifies**, since D9
re-derives it from the named parent and byte-compares on every build. Every
other citation in the format is a pointer a human must go and check. This
one is checked for you, every commit.

**D25 — The decoding contract.** Serves R4, R12, D9.

*Accepted input.* Word 0 must be `0000`. `0100` (unmodulated) is rejected
per D1a; `5000` and `5001` are rejected outright — they are references into
a Pronto-internal protocol table, not waveforms, so there is nothing to
decode. Parsing tolerates any run of whitespace and either hex case. The
word count must be exactly `4 + 2 × (n1 + n2)`; `n1` or `n2` may be zero
(Sony emits `n1 = 0`, a one-shot code emits `n2 = 0`). Anything else is a
parse error naming the offending word index.

*Cycles → microseconds.* `µs = round_half_up(cycles × period_us)`, using the
same `Decimal` period and rounding mode as D6. It inverts D6's µs → cycles
and — the part worth being explicit about — **is lossy in that direction**,
because `encode` already quantized. `decode(encode(s))` recovers `s` only to
within one cycle. Three consequences, each of which would otherwise be a
latent flake:

- §5's round-trip property asserts equality **in cycles**, never in
  microseconds.
- D8 compares **in cycles**, which is what makes an `irp` ↔ `pronto` check
  exact by construction rather than by luck.
- **D9 diffs the canonical string, not the signal.** Regenerating a
  `derived` form and comparing the emitted Pronto text byte-for-byte is
  strictly stronger than comparing decoded signals, and sidesteps the lossy
  direction entirely. Decoding is therefore only needed for `pronto` forms
  that are *evidence* — a hand-entered string from a forum post — never for
  `derived` ones.

*Canonical spelling.* A hand-authored `pronto` form may arrive lowercase or
irregularly spaced. It decodes fine, and `rl fmt` rewrites it to D6's
canonical spelling so the committed bytes match what the compiler emits.

---

## 4. Pronto Hex: the exact contract

R12 says "same JSON input always produces byte-identical Pronto Hex." That
only means something if the rounding is pinned down, so:

**D6 — The Pronto encoding rules, fixed.**

1. Format: `0000 FFFF NNNN MMMM` then burst pairs, all uppercase 4-hex-digit
   words, single-space separated, no trailing space.
2. Frequency word: `round_half_up(1_000_000 / (carrier_hz * 0.241246))`.
   The constant `0.241246` µs is the Pronto base clock and is pinned in
   source as a `Decimal`, never a float literal recomputed elsewhere.
3. Period: `freq_word * 0.241246` µs.
4. Each duration → `round_half_up(duration_us / period_us)` carrier cycles.
5. **Rounding is `ROUND_HALF_UP` on `Decimal`, never Python's built-in
   `round()`** — which is banker's rounding and would make `x.5` cases
   depend on parity. This is the single most likely source of a
   reproducibility bug in the whole project.
6. `NNNN` = intro pair count, `MMMM` = repeat pair count. A protocol with no
   intro (every Sony variant) emits `NNNN = 0000` and puts its whole
   sequence in the repeat slot.
7. Durations are computed from **IRP units multiplied out exactly**, never
   from rounded nominal values. `16 × 564 = 9024 µs`, not "9 ms".
8. `carrier_hz` is strictly positive; there is no unmodulated encoding in
   v1 (D1a).
9. Every emitted word must fit four hex digits, and no duration may round to
   zero cycles — see D28 for the full numeric bounds.
10. All of this arithmetic runs inside one pinned `Decimal` context (D28),
    so a library that mutates the process-wide context cannot change the
    output.

**Rule 4 is a choice, and the reference tools make different ones.** This
was found after v1, while pursuing D18's gate 2b.

- **IrpTransmogrifier** rounds each duration against the *nominal* carrier:
  `round(t × f / 10⁶)`.
- **MakeHex** rounds against the word's period, as rule 4 does, but
  cumulatively over each mark+space pair.

At `006C`, a 9024 µs lead-in becomes 346 cycles under rule 4 and plays as
9014.9 µs. Under IrpTransmogrifier's rule it becomes 347 cycles and plays as
9040.9 µs. Rule 4 is the closer of the two, because a player runs at the
period the word implies, not at the nominal carrier. So rule 4 stands, and
two consequences follow:

- Our bytes differ from IrpTransmogrifier's in a handful of words per code
  (four in an NEC1 frame).
- A golden vector checks our *timings* under the tool's own rule, and pins
  where our bytes differ (D10).

Rule 7 is not pedantry — it changes the emitted bytes:

| Lead-in source | µs | Cycles @ `006D` | Word |
|---|---|---|---|
| IRP-exact, `16 × 564` | 9024 | 343.17 | `0157` |
| Rounded nominal "9 ms" | 9000 | 342.26 | `0156` |

Both appear in the wild. The ledger emits the first.

**Worked check — NEC1 @ 38 kHz.** Frequency word `1000000/(38000 × 0.241246)
= 109.08 → 109 = 0x006D`; period `26.2958 µs`. Lead-in mark `9024 µs →
343 = 0157`, lead-in space `4512 µs → 172 = 00AC`. Bit-zero pair `564/564 µs
→ 21/21 = 0015 0015`; bit-one space `1692 µs → 64 = 0040`. Pair count
`1 lead + 32 bits + 1 stop-and-gap = 34 = 0022`. Repeat sequence `16,-4,1` +
gap = 2 pairs = `0002`, with `4 × 564 = 2256 µs → 86 = 0056`.

Result: `0000 006D 0022 0002 0157 00AC 0015 0015 ...` — which matches the
canonical published NEC Pronto prefix. Sony @ 40 kHz checks out the same
way: frequency word `104 = 0068`, period `25.09 µs`, lead `2400 µs → 96 =
0060`, unit `600 µs → 24 = 0018`, double unit `1200 µs → 48 = 0030`.

> ⚠️ **This invalidates the sample string in README.md.** The README shows
> `0000 006D 0022 0000 0156 00AB ...`. Under D6 the compiler emits `0157
> 00AC`, and `0022 0000` drops NEC1's repeat sequence. The README string is
> illustrative and predates the tooling — regenerate it from the real
> compiler output in Phase 3 rather than leaving a wrong example in the
> front door. Logged in §9.

**D16 — A key holds candidate groups; cross-checking happens within a
group, never across.** Serves R4, R6, R13, OD5.

This is the flaw that would have stopped Phase 3 dead. SPEC §1 is built
around the BX510 resolving to three candidates — subdevice 218 verified, 234
and 242 offered as untested fallbacks. Store those as forms on one key and
v0.1's cross-check compares all of them pairwise and fails, because
different subdevices *necessarily* produce different waveforms. The tooling
would have rejected exactly the data the spec exists to hold.

The cause is that "several forms on one key" was quietly doing two unrelated
jobs:

| Relationship | Means | Must agree? |
|---|---|---|
| **Forms within a candidate** | Several *representations of one signal* — an `irp` form, its `raw` capture, a `pronto` string | **Yes.** Disagreement is a real error (R13) |
| **Candidates within a key** | Several *hypotheses about which signal the device answers to* | **No.** Disagreement is the entire point |

So every form carries an optional `candidate` tag, defaulting to
`"primary"`:

```json
"KEY_POWER": {
  "forms": [
    { "type": "irp", "device": "0x1A", "subdevice": "0xDA", "function": "0x15",
      "confidence": "verified", "source": "hifi-remote.com Sony BD command table" },
    { "type": "pronto", "hex": "0000 0068 ...", "confidence": "derived",
      "derivedFrom": "primary.irp" },
    { "type": "irp", "candidate": "mode2",
      "device": "0x1A", "subdevice": "0xEA", "function": "0x15",
      "confidence": "untested",
      "source": "Sony BD alternate command mode, service manual p.14" }
  ]
}
```

Cross-check (D8) partitions forms by `candidate` and compares only within a
partition. Precedence (D7) selects one trusted form *per group*. The
`primary` group is the key's default; every other group compiles alongside
it as a labelled fallback, which is what lets `rl lookup` and the site
present SPEC §1's actual answer — one device, several candidates, a
different reason to trust each — instead of collapsing it.

Two checks still run *across* groups:

- **Distinctness.** Two groups that would ship the identical artifact mean
  one is redundant: a `redundant-candidate` warning (D32), not an error — a
  data-quality signal, not a broken build. Defined concretely, since "render
  to identical signals" left the comparison open and `raw` forms make the
  choice observable: compare each group's **compiled Pronto string** — D6's
  canonical output from the group's D7-selected form — by **exact string
  equality**, not D8's tolerances.

  Exact strings rather than tolerant comparison because the warning's claim
  is precisely "these two candidates emit the same artifact," and the
  artifact *is* that string. Under D8's ±15 % raw tolerance, two captures of
  genuinely different addresses could match on every burst and warn
  falsely; conversely two groups that differ by one cycle ship different
  bytes and are not redundant. Comparing strings also makes `raw`-versus-
  `irp` work with no special case, since every selected form compiles to
  Pronto regardless of type. The `check` stage computes these itself — it
  already renders each form to an `IrSignal` for D8, so emitting Pronto is
  one further call — which is why this needs no reordering against
  `compile` in D11's pipeline.
- **A `primary` must exist**, and — per D21 — must hold at least one
  non-derived form. Every key needs a default answer that actually
  compiles.

**D17 — Remote-level `variants` expand into candidate groups at load.**
Serves R1, R3, D16.

D16 on its own would make the BX510 miserable to author: its alternate
subdevice applies to *every* key, so tagging forms by hand means duplicating
forty forms to change one byte. `variants` states it once, at the level the
fact actually lives at:

```json
"variants": {
  "mode2": { "label": "Command mode 2", "confidence": "untested",
             "source": "Sony BD alternate command mode, service manual p.14",
             "override": { "subdevice": "0xEA" } },
  "mode3": { "label": "Command mode 3", "confidence": "untested",
             "source": "Sony BD alternate command mode, service manual p.14",
             "override": { "subdevice": "0xF2" } }
}
```

At load, each variant expands: for every key whose `primary` group has an
`irp` form, a copy is made with the override applied, tagged
`candidate: "mode2"`, carrying the variant's own confidence and source. The
compiler and cross-checker only ever see candidate-tagged forms — **one
mechanism in the engine, one shorthand at the authoring layer.** Expansion
is deterministic, and `rl fmt --expand` writes it longhand when you want to
read what it produced.

**Exactly one parent, exactly one source group.** Since D21 permits a group
to hold several forms of one type, "the `primary` group's `irp` form" needed
narrowing:

- A variant expands **from the `primary` group only.** There are no variants
  of variants in v1.
- It copies the group's **selected IRP form**: D7's precedence applied to
  the `irp` forms *only*. Not "the D7-selected form" as v0.6 said — D7
  ranks confidence before type, so a `confirmed` raw capture outranks a
  `verified` irp form, and a variant handed that has no `device`,
  `subdevice`, or `function` to override. Filtering to `irp` first, then
  ranking, keeps the choice deterministic (same precedence, same D21 ids,
  same array-order tie-break) while guaranteeing the parent is addressable.
- A key whose `primary` group has no `irp` form at all is skipped silently,
  not an error: a raw-only key simply has no address for a variant to
  re-target. Note this is now the *only* skip case — with the filter in
  place, a key that has an `irp` form can always be expanded, whatever else
  outranks it.

One parent per expanded form is also what makes `expandedFrom.form` a single
id and D33's recompute-and-diff well-defined.

Three limits, stated now rather than discovered later:

- A variant may override only `device`, `subdevice`, and `function` — never
  `protocol` (D23), and never `confidence`, `source`, or any other evidence
  field.
- It never overrides an explicitly-authored form in the same candidate
  group, so a variant can be corrected one key at a time by hand.
- **Its confidence must be stated explicitly and is never inherited.**
  Re-addressing a verified code does not make the new address verified, and
  the expansion must not launder a tier onto a hypothesis nobody checked.

**D22 — Expansion merges provenance explicitly; it never inherits
evidence.** Serves R5, R18, D17.

"Carries the variant's own confidence and source" leaves two real
corruptions open. A copied form could retain `verifiedBy:
"nec-complement-check"` — a claim that is simply *false* for an arbitrary
alternate address, since the complement relationship doesn't hold there. And
dropping the primary's citation outright discards the provenance of the
fields the variant *didn't* touch: those function codes are still the ones
the verified source established.

Both follow from noticing that an expanded form carries **two distinct
claims needing two distinct citations** — where the address hypothesis came
from, and where the inherited parameters came from. So expansion partitions
fields explicitly:

| Fields | Treatment |
|---|---|
| Named in `override` | Replaced by the variant's values |
| `confidence`, `source` | Replaced by the variant's — both required on it |
| `verifiedBy`, `derivedFrom`, `id`, any verification note | **Stripped.** Each asserts something about the *original* parameter set, and would be a false claim about the new one |
| Everything else (`type`, untouched `device` / `function`) | Carried unchanged |
| `expandedFrom` | **Added**, recording exactly what happened |

```json
{ "type": "irp", "candidate": "mode2", "id": "mode2.irp",
  "device": "0x1A", "subdevice": "0xEA", "function": "0x15",
  "confidence": "untested",
  "source": "Sony BD alternate command mode, service manual p.14",
  "expandedFrom": { "variant": "mode2", "form": "primary.irp",
                    "overridden": ["subdevice"],
                    "inherited": ["device", "function"] } }
```

`expandedFrom.form` is a D21 form id, so the inherited values' citation is
one dereference away and R18's "independently checkable" holds for both
claims at once — with no path by which a `verified` tier reaches an address
nobody tested.

**D33 — A form carrying `expandedFrom` is a generated cache, never an
override.** Serves R12, D9, D17, D22.

D17 says a variant never overrides an explicitly-authored form, and
`rl fmt --expand` writes expansions out longhand. Those two together were a
trap: an expanded copy *looks* explicitly authored, so it would suppress the
very expansion that produced it — and then editing the variant or the parent
form later would leave a stale copy silently in charge of what compiles.

The fix is that `expandedFrom` (D22) makes the two cases mechanically
distinguishable, so "explicitly authored" gets a definition instead of a
vibe:

- **A form with `expandedFrom` is a cache.** It does not suppress
  expansion — it *is* the expansion. Every build recomputes it from the
  variant and the parent and diffs the result; a mismatch is an error, and
  `rl fmt --refresh` rewrites it.
- **A form without `expandedFrom` is authored.** That is what suppresses
  expansion for that key.
- **To hand-correct one key, delete `expandedFrom`.** Removing the field is
  the deliberate act that transfers authority from the variant to you — a
  one-line diff that says exactly what it means, rather than an invisible
  consequence of having run a formatter.
- **At load, a cached expansion is replaced in place, never appended to.**
  For each (key, variant) pair: if a form already carries
  `expandedFrom.variant == <variant>`, the recomputed form is diffed against
  it and takes its position in the array; if none does, the recomputed form
  is appended. Exactly one of the two happens. The naive implementation —
  always append — would give a key two forms for one variant after the first
  `--expand`, doubling on every run.
- **Exactly one cache per (key, variant), and at most one `irp` form per
  variant group.** D21's id uniqueness is per *key*, so two forms with
  distinct explicit ids can both claim
  `expandedFrom.variant == "mode2"` and slip past it — "replace the one
  match" then has no defined meaning. Two validation rules close that:

  1. At most one form per (key, variant) may carry `expandedFrom`. Two is an
     error naming both ids — never "replace the first," which would pick
     arbitrarily.
  2. A variant's candidate group holds **at most one `irp` form total** —
     either the cache or an authored one, never both. An authored `irp` form
     there is precisely what D17 means by suppressing expansion, so a cache
     beside it is a contradiction about which one governs.

  Note what rule 2 deliberately *doesn't* forbid: authored `raw` or `pronto`
  forms coexisting with the cached `irp` form. A hardware capture confirming
  a variant is exactly the evidence you want, and it cross-checks against
  the expansion through D8 like any other corroboration. That is also how a
  variant graduates out of `untested` — capture it, author the `irp` form,
  delete the cache, and the group becomes ordinary authored data.

- **A cache is valid only while all four of its premises hold.** Cardinality
  alone would let a cache outlive the thing it caches: delete the parent
  form, or turn a variant into a metadata-only entry (which D26 permits),
  and the orphan still satisfies "exactly one per (key, variant)." All four
  are checked on every build:

  1. `expandedFrom.variant` names a `variants` entry that **still exists**
     and **still carries an `override`.** A metadata-only variant expands to
     nothing, so a cache claiming to be its expansion is stale by
     definition.
  2. `expandedFrom.form` still **resolves** to a form in the `primary`
     group.
  3. That form is still the group's **currently selected IRP form** (D17).
     Add a higher-confidence `irp` form to `primary` and the cache is now
     derived from the wrong parent, even though it still parses.
  4. The recomputed content **matches** (the diff already in the bullets
     above).

  Any failure is a **validation error**, and `rl fmt --refresh` repairs all
  four mechanically — recomputing from the correct parent for 2–4, and
  *removing* the cache for 1, since a variant with no override has no
  expansion to hold. This is deliberately the same lifecycle D9 gives a
  `derived` form: stale is an error, `--refresh` is the fix, and nothing
  rots silently. Two kinds of generated-and-committed form content, one
  rule.

This makes `--expand` safe to run at any time, and it generalizes D9: the
ledger now has two kinds of committed-but-generated form content — a
`derived` form's `hex` and an expanded form's parameters — validated by the
identical regenerate-and-diff rule. Neither can rot, and neither needs a
human to remember to refresh it.

**D26 — Every non-`primary` candidate group is declared in `variants`.**
Serves D16, D17, D20.

D16 lets forms be hand-tagged with any `candidate` id, but only `variants`
defined a `label` — while D20 requires every compiled fallback to carry one,
so a hand-authored group had no way to get its name. Rather than add a
second metadata block, `variants` becomes the single declaration site:

- An entry **with** `override` auto-expands into a candidate group (D17,
  D22).
- An entry **without** `override` declares metadata only, for a group whose
  forms are hand-authored and tagged by `candidate`.
- Either way it supplies `label`, `confidence`, and `source` for the group.
- `label` is optional and **defaults to the candidate id**, so a throwaway
  group needs no boilerplate.
- **A `candidate` tag other than `primary` resolving to no `variants` entry
  is a validation error.** That's what closes D20's gap: a compiled fallback
  can't exist without a label, because its group couldn't have validated
  without a declaration.

`primary` is the one id needing no declaration — it's the default group and
its label is fixed.

**D21 — Forms have stable ids, and every candidate group needs a
non-derived one.** Serves R12, R15, D7, D16.

Two gaps in the same place. D7 excludes `derived` from selection while D16
only requires that a `primary` group *exist* — so a group holding nothing
but derived forms validates clean and then fails at compile time, the worst
place to find it. And `derivedFrom: "irp"` names a *type*, not a form:
ambiguous the moment a group holds two `irp` forms, and unable to survive a
reorder.

- **Every form has an `id`, unique within its key, and it never depends on
  array position.** The auto-assigned id is `<candidate>.<type>` — and it is
  assigned *only when that is unique within the group*. If a group holds two
  forms of the same type, **every form of that type must carry an explicit
  `id`**, or validation fails.

  v0.3 appended `.2`, `.3` … in array order, which made the ids anything but
  stable: reordering two same-type forms silently retargeted every
  `derivedFrom` and `expandedFrom` pointing at them — a provenance
  corruption with no diff to show for it. Positional suffixes are gone
  entirely. `primary.irp` is stable *because* it is unique, and the
  explicit-id requirement is what keeps that true. An explicit id matches
  `^[A-Za-z0-9][A-Za-z0-9._-]*$` and may not collide with another form's
  auto-assigned id. `rl fmt` writes every id out longhand.
- **`derivedFrom` names an id, not a type.** It must resolve to a form in
  the *same* candidate group; that form must not itself be `derived`, so
  derivation is one level deep and there are no chains or cycles to detect.
  A dangling reference is a validation error.
- **Every candidate group must contain at least one non-derived form.** This
  is what makes "a group always compiles" true rather than hoped-for. It
  subsumes D16's "a `primary` must exist": `primary` must exist *and* be
  compilable, as must every fallback.

**D28 — Numeric bounds, and a pinned `Decimal` context.** Serves R12, R15.

"Byte-reproducible" (D20) has two holes v0.3 left open: nothing bounded the
values, and `Decimal` rounding is read from a *process-wide* context that
any imported library can mutate.

*The context.* Every Pronto computation runs inside an explicit
`decimal.localcontext()` with `prec` and `rounding` set locally — never
`decimal.getcontext()`, whose state is global and writable by anything in
the process. The clock constant is `Decimal("0.241246")`, constructed from a
string so no float ever enters the calculation:

```python
with decimal.localcontext() as ctx:
    ctx.prec = 34
    ctx.rounding = decimal.ROUND_HALF_UP
```

*The bounds*, each a validation error rather than a silent truncation into
four hex digits:

| Quantity | Bound | Why |
|---|---|---|
| `carrierHz` | 10 000 – 500 000 | Real IR carriers are 30–60 kHz; this is a wide margin that still catches a units mistake (38 instead of 38000) |
| Frequency word | 1 – `0xFFFF` | Follows from the carrier bound; stated so the check exists independently |
| Burst cycle count | **1** – `0xFFFF` | The lower bound is the important one: a duration that rounds to **zero cycles** is a real failure — a burst that vanishes — and must never be emitted as `0000` |
| `n1`, `n2` | 0 – `0xFFFF`, with `n1 + n2 ≥ 1` | Either sequence may be empty (D25); both cannot be |
| `raw` duration | 1 – 1 000 000 µs | A single burst longer than a second is a corrupt capture |
| Durations per sequence | ≤ 2048 **durations** | Duration entries — `n` in D31's notation — not Pronto burst pairs, of which this is 1024. Bounds the work a malformed file can cause |
| `unitUs` | 1 – 10 000 | Real units run 200–900 µs; scales every duration the encoder emits |
| `defaultGapUs` | 1 – 1 000 000 | Same bound as any other duration (D31) |
| `minSends` | 1 – 10 | Sony needs 3; nothing needs more than a handful |
| `tolerance.absUs` | 0 – 10 000 | `0` means exact |
| `tolerance.relative` | 0.0 – 0.5 | **The one that matters.** Past 50 % the check stops distinguishing a wrong code from a right one |
| `tolerance.gapUs` | 0 – 1 000 000 | |

The zero-cycle bound is worth dwelling on: at 38 kHz one cycle is ~26 µs, so
any duration under ~13 µs rounds to zero. No real protocol has one — which
is exactly why hitting it means something upstream is wrong, and silently
emitting a `0000` burst word would hide it.

*Non-finite values.* `json.load` accepts the literals `NaN`, `Infinity`, and
`-Infinity` by default, which is a real hole under a schema that only sets
`minimum`/`maximum` — comparisons against `NaN` are all false, so an
unbounded-by-accident field would sail through. The loader passes
`parse_constant=` a function that raises, rejecting all three at parse time,
before any schema or bounds check runs.

*Decimal in, decimal throughout.* Pinning the arithmetic context is not
enough on its own: ordinary `json.load` parses `"relative": 0.15` into a
**binary float**, which then either raises on contact with a `Decimal` or
silently drags the calculation back onto binary rounding. The loader passes
`parse_float=Decimal`, so every non-integer JSON number enters as a
`Decimal` constructed from the literal *text* — `0.15` becomes exactly
`Decimal("0.15")`, never the binary approximation. `parse_int` stays at the
default, since Python's ints are already exact.

On the way out the guarantee is **canonical, value-stable output — not
lexical preservation**. `str()` alone gives neither: `Decimal("1e-2")`
prints as `0.01` while `Decimal("1.0E+2")` prints as `1.0E+2`. But
`format(d, "f")` alone — v0.7's answer — doesn't reach byte-identity
either, because it preserves insignificant zeros (`1.0` and `1.00` stay
distinct) and signed zero (`-0`).

v0.8 reached for `Decimal.normalize()`, which is **wrong, and wrong at the
stdlib default rather than only under a hostile context.** `normalize()`
rounds to the ambient context's precision, and at Python's default
`prec = 28` it silently turns `0.1000…001` (39 digits) into `0.1` and
rewrites a 31-digit value's low digits — data loss inside a function whose
entire job is to preserve a value. Pinning the *Pronto* arithmetic context
doesn't help: serialization happens elsewhere, and a global anyone can
mutate is not a foundation for a byte-identity guarantee.

The fix is to be **context-independent by construction**, operating on the
coefficient rather than asking `decimal` to re-round:

```python
def canon(d: Decimal) -> str:
    sign, digits, exp = d.as_tuple()
    if not isinstance(exp, int):            # NaN / Infinity — already rejected
        raise ValueError("non-finite")      # at parse time, see above
    if not digits or not any(digits):       # zero at any exponent, either sign
        return "0"
    digits = list(digits)
    while exp < 0 and digits[-1] == 0:      # drop insignificant trailing zeros
        digits.pop()
        exp += 1
    return format(Decimal((sign, tuple(digits), exp)), "f")
```

`as_tuple()` reads the stored coefficient and exponent exactly; the
`Decimal((sign, digits, exp))` constructor applies no context; and
`format(…, "f")` with no precision is context-independent, so it only
renders plain notation (`1.0E+2 → 100`). Nothing in the path can round.

Verified by running it across `prec ∈ {1, 3, 9, 28, 34, 99}` × three
rounding modes: output is **identical in every context**, idempotent, and
numerically equal values always agree. `0.15 → 0.15`, `1.0 → 1`,
`1.00 → 1`, `1e-2 → 0.01`, `1.0E+2 → 100`, `12.340 → 12.34`,
`-2.50 → -2.5`, `-0 → 0`, and the 39-digit value survives intact — where
v0.8 returned `0.1` for it at default precision.

So the property D19 and D20 need now genuinely holds: **numerically equal
values always produce identical bytes, and a second `rl fmt` is a no-op** —
under any ambient `decimal` context, which is the part that makes it a
guarantee rather than a hope. The visible cost is that a source file written
`1.0` is rewritten to `1`: still a JSON number against a `number` schema,
and a one-time normalization rather than recurring churn.

*The standing rule*, worth more than the table: **every numeric field in the
schema declares its type (`integer` vs `number`) and explicit
`minimum`/`maximum`.** A numeric field without bounds is a schema bug, not a
permissive default. v0.4's table covered the waveform values and silently
omitted every knob in the `protocol` block — which is how a `relative: 50`
typo could have quietly switched cross-checking off.

**D7 — Form precedence, fully deterministic, scoped to one candidate
group.** Serves R6, R12, D16. Selection runs independently within each
group. SPEC R6 gives two tie-break levels; a third is needed or two
same-tier same-type forms are a coin flip.

1. **`derived` is excluded from selection entirely.** SPEC §5 already calls
   it "not independent evidence on its own" — so it can never *be* the
   trusted form. It exists only to be cross-checked. This is implied by the
   spec but not stated as a rule; state it.
2. Confidence: `confirmed` > `verified` > `plausible` > `untested`.
3. Form type: `irp` > `raw` > `pronto`.
4. **Array order — lowest index wins.** The final tie-break, so selection is
   total.

**D8 — Cross-check: one comparison, entirely in carrier cycles.** Serves
R13, OD3, D16.

Forms are partitioned by `candidate` (D16) and compared only within a
partition. v0.2 said signals were quantized to the Pronto cycle grid and
*then* applied a tolerance stated in microseconds, without saying what it
applied to — two units in one rule. The algorithm, stated once:

0. **Exclude `derived` forms** (D5a). They never reach this algorithm.
1. **Fix the comparison carrier, and compare frequency *words*, not hertz.**
   Both signals quantize at the file's `protocol.carrierHz`; D23 guarantees
   there is exactly one. Carrier agreement is then checked in the quantized
   domain: `expected = freq_word(protocol.carrierHz)` per D6 rule 2.

   This is the only correct comparison, because a `pronto` form stores a
   *word*, not a frequency. Decoding `006D` back to hertz yields 38 028.9 Hz
   — so comparing it against a file's declared `38000` would fail
   **correctly generated output**, while ignoring the header entirely would
   let a string from a 36 kHz remote pass unnoticed. Word equality is exact,
   deterministic, and canonical, since `freq_word` is many-to-one and the
   word *is* the representation. Never decode a frequency word to hertz for
   the purpose of comparing it — it's lossy in precisely the way D25
   describes for cycles → µs.

   - A **`pronto`** form whose word 1 ≠ `expected` **fails**, with a message
     naming both words and both approximate frequencies. A Pronto string is
     a generated artifact, not a measurement: its word is either right, or
     the string came from a different remote.
   - A **`raw`** form whose own `carrierHz` maps to a word differing by
     **±1 warns**; beyond that it fails. A capture's carrier is a
     measurement with real instrument error, and the quantization is coarse
     enough to absorb it — 37 900 Hz and 38 000 Hz are both word `006D`,
     while 38 400 Hz is `006C`, one away. A ±1 difference shifts
     quantization by under 1 %, which the raw tolerance below already
     covers, so it's a data-quality signal rather than a broken build. The
     comparison itself always uses the file's carrier, so the warning
     changes no arithmetic.
2. **Compute the period** exactly as D6 does —
   `period_us = freq_word(carrierHz) × 0.241246`, inside D28's pinned
   `Decimal` context.
3. **Check structure.** Sequence count, and the length of each sequence,
   must match exactly. A differing burst *count* is a structural
   disagreement, never rounding, and always fails.
4. **Quantize both** with D6's own function, which is what makes an
   `irp` ↔ `pronto` comparison exact by construction:
   `c = round_half_up(µs / period_us)`.
5. **Compare cycle counts.** Every comparison from here is an integer count.
   Nothing is compared in microseconds:

| Pair | Non-terminal bursts | Terminal gap of each sequence |
|---|---|---|
| `irp` ↔ `irp`, `irp` ↔ `pronto`, `pronto` ↔ `pronto` | `c_a == c_b` | `\|c_a − c_b\| ≤ ceil(gapUs / period_us)` |
| anything ↔ `raw` | `\|c_a − c_b\| ≤ allow(a, b)` | as above; skipped entirely if either side is `truncated` (D4a) |

where, for the **pre-quantization** microsecond durations `µs_a` and `µs_b`
at the same index:

```
allow(a, b) = ceil( max(absUs, relative × min(µs_a, µs_b)) / period_us )
```

6. **Aggregate as a star, not all-pairs.** Every non-derived form in the
   group is compared against **the form D7 selected**, and never against
   each other. R13 is already shaped this way — "compile the trusted
   form… cross-check every additional form the key holds" — and it's the
   only aggregation that makes sense given the raw tolerance is
   **non-transitive**: with all-pairs, the verdict would depend on a
   relation that admits `A ≈ T` and `B ≈ T` without `A ≈ B`, and a failure
   message would have no canonical reference to name. Star-shaped, the
   reference is exactly the artifact being shipped, the cost is O(n) rather
   than O(n²), and the message reads "form `raw.2` disagrees with the
   trusted form `primary.irp` at burst 14."

   Named consequence: two non-trusted forms may disagree with each other
   while both corroborating the trusted one. That is accepted, not
   overlooked — what ships is the trusted form, and both forms corroborate
   *it*. In practice a group holds one to three non-derived forms, so this
   is a question of specifying the rule rather than of outcomes.

Defaults: `absUs = 100`, `relative = 0.15`, `gapUs = 150` (SPEC R13), each
overridable via `protocol.tolerance` with its required `claims` entry (D24,
D27) and within D28's bounds.

`min(µs_a, µs_b)` rather than v0.1's bare `a`: the predicate has to be
**symmetric**, or the verdict depends on which form happened to come first
in the array and R12's determinism quietly fails for every `raw`
comparison. `min` is also the conservative reading, and `ceil` rounds the
allowance outward by at most one cycle — both deterministic, neither
order-dependent.

The `raw` defaults follow LIRC's own decoder settings (`aeps = 100 µs`,
`eps = 30 %`, tightened to 15 % here since we're comparing against a
generated reference rather than decoding blind).

**D9 — Stored `derived` forms become checked-in golden fixtures.** Serves
R12, R14's anti-staleness principle. SPEC §5's example stores a `derived`
Pronto row inline, which risks exactly the drift R14 avoids for the index.
Rather than ban it, the validator checks it — and D5a fixes it as the
*only* check a derived form faces:

1. Resolve `derivedFrom` to its parent form id, in the same candidate group
   and itself non-derived (D21).
2. Render **that parent** to an `IrSignal` and emit Pronto from it (D6).
3. Diff the **canonical string, byte-for-byte**, against the stored `hex`.

Comparing strings rather than decoded signals is both stronger and immune to
D25's lossy cycles → µs direction. A stale form fails the build instead of
rotting quietly, and `rl fmt --refresh` rewrites them. Because the parent is
named by a stable id, "derived from what?" always has one answer — which is
what makes this a genuine regression test rather than a restatement. A
`derived` form is thus free to store and, per D5a, risky nowhere.

---

## 5. Testing, and the cost of having no oracle

Hand-written encoders with no external reference implementation is the
chosen path, and it has one specific weakness worth naming plainly:
**most of the obvious tests are self-consistent and would pass even if an
encoder were wrong.** A round-trip test confirms the decoder inverts the
encoder; it says nothing about whether the NEC1 lead-in is 16 units or 15.
So the test strategy has to be built around the one layer that *is*
independent.

**D10 — Golden vectors are cited data, held to R18.** Serves R12, R18.
`tests/vectors/` holds published Pronto strings from independent sources,
each with a citation in `tests/vectors/CITATIONS.md` in exactly the format
R18 demands of the ledger's own entries. The test suite obeys the project's
own sourcing rules. This is the only layer that catches a wrong constant, so
**no protocol ships in the registry without at least one cited vector.**
That's a hard gate, not a preference, and `test_gate_2b_golden_pronto`
fails for any registry protocol without one.

**What a vector proves, stated precisely.** Other encoders quantize
microseconds to cycles by other rules (D6, after rule 10). So a vector is
checked in two steps:

1. Our microsecond signal, run through the *tool's* rule
   (`tests/reference_quantizers.py`, transcribed from each tool's source),
   must reproduce the vector word for word. That verifies every duration
   against code we did not write.
2. Our own bytes may then differ from the vector only at the words the
   manifest declares.

A vector says whether it is **published**, meaning found in print and
pinned to a commit, or **reproducible**, meaning generated here by a pinned
release, with the command recorded.

The four layers, in descending order of how much they actually prove:

| Layer | Catches | Doesn't catch |
|---|---|---|
| **Cited golden vectors** (D10) | Wrong units, wrong lead-in, wrong bit order, wrong carrier | Nothing — this is the load-bearing layer |
| **Protocol invariants** | Broken framing: NEC `~F` complement, Sony bit counts, total extent = 108 ms / 45 ms | A consistently wrong unit |
| **Round-trip property** | Encoder/decoder asymmetry, rounding drift | Any error the encoder and decoder share |
| **Corpus snapshot** | Unintended output changes across a refactor | An error present when the snapshot was taken |

Property test, via `hypothesis` if it earns its place:
`decode(encode(sig)) ≈ sig` within one cycle, over random valid signals.

**D11 — CI is the enforcement point, and runs exactly two commands.**
Serves R21, D19, D32. Single `ubuntu-latest` job, Python 3.12:

```
pytest
rl build --check
```

**That's the whole job, and the shortness is load-bearing.** v0.7 had CI run
`rl validate` → `rl check` → `pytest` → `rl build --check`, which quietly
defeated D32: a corpus-wide `rl check` *writes* `build/warnings.json`, so by
the time the tree comparison ran, the working copy already held the freshly
regenerated warnings file. The diff came up clean and a brand-new warning
passed CI without anyone committing it — the mechanism acknowledging itself.

So the rule, rather than just the corrected recipe:

> **No command that writes into `build/` or `site/` may run in a job that
> also runs a `--check`.** A corpus-wide `rl check` is a generator run (it
> owns `build/warnings.json`, D19) and belongs in the same category as
> `rl compile`, `rl index`, and `rl site` — useful locally, never in CI
> ahead of the gate.

`rl build --check` already subsumes the dropped steps: it runs
`validate → check → compile → index → site` into a temp directory (D19), so
validation and cross-check failures still fail the job, with nothing written
to the working tree.

**Defense in depth, so the hole can't be reopened by a helpful edit.**
`--check` refuses to run when any generator-owned path has uncommitted
modifications in a git working tree, naming them, because that is exactly
the case where the comparison would be vacuous. Skipped when not in a git
repo; `--allow-dirty` overrides for anyone who wants it. Locally you run
`rl build` (write mode), not `rl build --check`, so this costs nothing in
normal use.

---

## 6. Repo layout and CLI

```
remote-ledger/
├── README.md  SPEC.md  DESIGN.md
├── unresolved.json                     negative results — see D12
├── .gitattributes                      pins LF on generated JSON (D20)
├── src/remote_ledger/schema/           package data, not repo-relative:
│   ├── remote.schema.json              a non-editable install has no
│   ├── unresolved.schema.json          checkout to read from
│   ├── keys.schema.json                the canonical key vocabulary (D83)
│   └── aliases.schema.json             and its aliases (D85)
├── src/remote_ledger/vocabulary/       hand-written, shipped as package data, never
│   ├── keys.json                       generated: the canonical keys and groups (D83)
│   └── aliases.json                    the spellings that mean each (D85)
├── remotes/                            hand-authored, the actual ledger.
│   ├── sony/RMT-B118P.json             `remotes/**/*.json` is uniformly
│   ├── topping/RC-15A.json             one remote — no reserved names
│   └── samsung/BN59-01199F.json
├── build/                              generated, committed (OD4), wholly
│   ├── index.json                      owned by the generators (D19)
│   ├── warnings.json                   every warning, corpus-wide (D32)
│   └── pronto/<mfr>/<model>.json
├── site/                               generated static site (R17);
│   └── app/v1/                         the SwiftRemote app's API (§21)
├── src/remote_ledger/
│   ├── signal.py      IrSignal, cycle quantization        D1
│   ├── pronto.py      encode / decode                     D5 D6
│   ├── protocols/     one module per family (41 protocols) D3 D18 §18 §37 §41 §43 §45 §47 §52 §55 §57
│   ├── irblaster/     importer + six hex_*.py maps        §17 §18
│   ├── lirc/ smartir/ importers                           §14 §15
│   ├── forms.py       form → IrSignal, precedence         D7
│   ├── crosscheck.py  pairwise comparison                 D8
│   ├── layout.py      grid-template-areas parser          D14
│   ├── model.py       load, normalize, dataclasses
│   ├── validate.py    R15 + R11 area→key
│   ├── index.py       R14                                 D13
│   ├── lookup.py      R16
│   ├── site.py        R17                                 D15
│   ├── keys.py        canonical_id, the vocabulary loader  R22 D83-D85
│   ├── keys_report.py `rl keys report`                    D86
│   ├── bundle/        the catalog bundle: corpus, catalog, select,
│   │                  writer, sign, verify, vectors, notices  R23 D88-D95
│   │                  search_eval, matching_vectors            R24 D97 D98
│   ├── matching.py    the matcher over a bundle's search structures  R24 D96
│   └── cli.py         `rl`
├── tests/vectors/ + CITATIONS.md                          D10
└── .github/workflows/ci.yml                               D11
```

**D32 — Errors, warnings, and how a warning gets noticed.** Serves R21,
D8, D13.

v0.4 introduced a warning (a `raw` carrier one frequency word off) without
saying what a warning *does*. Two exit codes and one channel:

- **Exit 0** when there are no errors; **exit 1** when there is at least
  one. Warnings never change the exit code. `--strict` promotes them for
  anyone who wants it; CI does not need it, for the reason below.
- Warnings go to stderr as `WARN <location> <code> <message>`, where `code`
  is a stable slug and `<location>` is emitted **only to the depth the
  warning actually has** — v0.5's fixed `<file>:<key>:<form-id>` was wrong
  for two of the three codes that exist:

| Code | Scope | stderr location |
|---|---|---|
| `carrier-off-nominal` (D3) | file | `remotes/topping/RC-15A.json` |
| `redundant-candidate` (D16) | **candidate-group pair** | `…/RMT-B118P.json:KEY_POWER:mode2+mode3` |
| `raw-carrier-word-drift` (D8) | form | `…/RC-15A.json:KEY_POWER:primary.raw` |

- **Every warning is recorded in `build/warnings.json`**, a generated,
  committed artifact with its own D19 generator, owned by `rl check`:

```json
{ "schemaVersion": 1,
  "warnings": [
    { "code": "carrier-off-nominal",
      "file": "remotes/topping/RC-15A.json",
      "message": "carrierHz 38000 maps to word 006D; NEC1 nominal 38400 maps to 006C" }
  ] }
```

  Absent scope fields (`key`, `candidate`, `peer`, `form`) are **omitted,
  not null**. `redundant-candidate` is about a *pair* of groups, so it
  carries both — `candidate` and `peer`, ordered so `candidate < peer`
  lexicographically, which also means the pair is reported once rather than
  from each side. v0.6 named only one of the two, leaving two distinct
  warnings on one key able to collide on a key claimed to be total. The
  array is sorted by
  `(file, key or "", candidate or "", peer or "", form or "", code)`, which
  **is** total. Messages are generated text and so fall under D20's
  no-non-reproducible-values rule — no paths outside the repo, no timing, no
  counts that depend on iteration order.

That committed artifact is the part that makes warnings matter: a new
warning appears as a *diff*, and D19's tree check fails until it's
committed. Acknowledging a warning is committing the regenerated file.

*It is a separate artifact rather than a field in the index, and it arrives
in Phase 3.* v0.5 put warnings in `build/index.json`, whose generator isn't
registered until Phase 5 (§8) — so through Phases 3 and 4 the rule
described a file nothing wrote. A standalone `build/warnings.json` owned by
`rl check` registers in Phase 3, alongside the gate itself, and D13's index
needs no change. Before Phase 3 there is no gate and a human is running the
command, so warnings are stderr-only and that is enough.

This is also why CI doesn't need `--strict`. Promoting warnings to errors
there would make the `raw` carrier tolerance pointless — the whole reason
it's a warning is that a ±1 word difference is instrument error the
comparison already absorbs (D8 step 1). Routing it through a committed
artifact instead means a warning can't accumulate unseen, and can't block
authoring either.

**Global flags:** `--strict` promotes warnings to errors (D32), accepted by
`validate`, `check`, and `build`. `--allow-dirty` relaxes `--check`'s
clean-tree requirement (D11).

| Command | Requirement | Does |
|---|---|---|
| `rl validate [path]` | R15, R11 | Schema + semantic checks; exit non-zero on any error. Run with no path it also validates the canonical key vocabulary (D83) |
| `rl compile [path] [--check]` | R12 | Write `build/pronto/…`; `--check` diffs instead |
| `rl check [path]` | R13 | Cross-check every candidate group; the data gate |
| `rl index [--check]` | R14 | Regenerate `build/index.json` |
| `rl site [--check]` | R17 | Generate `site/` |
| `rl app [--check]` | D74 | Generate `site/app/v1/`, the app API (§21) |
| `rl keys report [--json]` | R22, D86 | How much of the corpus the canonical key vocabulary maps, per source and per remote; read-only (§22) |
| `rl bundle [--profile selected\|full] [--out DIR] [--check]` | R23, D88 | Build the catalog bundle an app ships: `catalog.sqlite`, `notices.json`, `manifest.json`. An artifact, not a stage: never under `build/` or `site/`, never committed, not covered by `rl build --check` (§23) |
| `rl bundle --verify DIR` | D88, D95 | Check a written bundle against the tree; decode every signal |
| `rl bundle sign --key KEY.pem DIR`, `rl bundle verify-signature --pub PUB.pem DIR` | D93 | ECDSA P-256 / SHA-256 detached signature of the manifest, with `openssl` |
| `rl bundle vectors --file FILE [--from DIR] [--check]` | D91 | The cross-language vectors of the signal table |
| `rl bundle matching-vectors --file FILE [--check]` | D97 | The cross-language vectors of the matcher |
| `rl bundle search-eval [--bundle DIR] [--queries FILE] [--timing]` | R24, D98 | Hit rates of the matcher over a bundle, on generated and hand-written queries, as a Markdown report (§24) |
| **`rl build [--check]`** | **D19** | **The whole pipeline — validate → check → compile → index → site → app — whole-tree; `--check` is the CI gate for drift *and* orphans** |
| `rl lookup "Sony BDP-BX510"` | R16 | Matching files, candidates, tiers, citations. Offline |
| `rl fmt [--refresh] [--expand] [--sort]` | D9, D17, D20 | Canonicalize field order, hex spelling, and decimal form; refresh `derived` forms; expand `variants` longhand; `--sort` canonically orders *set-like* arrays only, never semantic ones (D20) |

**Schema notes.** Form objects are a discriminated union on `type`, so
`jsonschema` produces one precise error rather than three
near-miss ones. Integer parameters accept either an int or a
`^0x[0-9A-Fa-f]{1,4}$` string; the loader normalizes to int and `rl fmt`
canonicalizes files back to hex strings, which is how a human wants to read
a device address. `confidence` is a closed enum of the five SPEC §5 tiers.

**D12 — `unresolved.json` records negative results, and lives outside
`remotes/`.** Serves R20, R15. SPEC R20 requires "a device with no entry yet
must look different from one that was checked and found to have no known
remote" — but nothing in the per-remote schema can express a device that has
*no* remote. A separate file holds them:

```json
[{ "device": "Vizio D32h-J09", "checked": "2026-09-14" }]
```

The index (D13) folds these in, so `rl lookup` and the site can say
"checked, nothing found, last checked on this date" instead of falling
through to the same blank as an unsearched device. Small addition, and R20
doesn't hold without it.

An entry is a status, and the schema holds it to that: `device` and
`checked`, nothing else. Earlier entries also carried the sources searched
and a narrative of why each fell short. On the page those read as a case
file where a reader wanted an answer, and each re-check grew the narrative
instead of replacing it. The argument lives where it can be reviewed, in
git history and in the prose that cites it (§13 for the BX510). Re-checking
a device updates `checked` and nothing more.

v0.1 put this file under `remotes/`, where it would have been swept up by
the corpus glob and failed `remote.schema.json` — it's a top-level array,
not a remote object. Hoisting it to the repo root is better than carving out
a reserved filename: `remotes/**/*.json` stays uniformly "one file, one
remote", with no special case for the loader, validator, or indexer to
remember. It gets its own `schema/unresolved.schema.json` and its own
validation pass.

**D13 — The index is generated *and* committed, with drift as a build
failure.** Serves R14, OD4. OD4 chose a committed index for diffability; R14
insists nothing hand-maintained can go stale. Both hold as long as the tree
check runs in CI (D19) — the file is committed but never hand-edited,
exactly like `build/pronto/`. Shape: `manufacturer`, `model`, `aliases`,
`controls`, a per-key candidate and tier summary, a rolled-up confidence per
file, and the `unresolved.json` entries.

**D19 — The generated tree is diffed whole, which makes orphans a build
failure for free.** Serves R12, R14, R21, OD4.

v0.1 checked `index` and `site` for drift but never `build/pronto/` — the
largest generated artifact and the one consumers actually load. `rl check`
only ever promised cross-validation, so a compiled Pronto string could
diverge from its source indefinitely. And no per-command check catches an
*orphan*: rename `topping/RC-15A.json` and the old
`build/pronto/topping/RC-15A.json` lingers forever — a stale compiled code
with no source, which is precisely the failure OD4 accepted committed
artifacts in exchange for avoiding.

One rule fixes both: **`build/` and `site/` are wholly owned by the
generator.** `rl build` regenerates the entire tree from scratch;
`rl build --check` regenerates into a temp directory and diffs the whole
tree — *file set and contents*. A file in the committed tree but absent from
the fresh one fails as an orphan; a differing byte fails as drift. No
per-artifact orphan logic is needed anywhere.

**Each generator declares the path it owns**, and `--check` diffs the union
of the paths whose generators are registered:

| Generator | Owns | Registered |
|---|---|---|
| `compile` | `build/pronto/**` | Phase 3 |
| `check` | `build/warnings.json` (D32) | Phase 3 |
| `index` | `build/index.json` | Phase 5 |
| `site` | `site/**` | Phase 6 |

That's what lets the gate turn on in Phase 3, when only `compile` and
`check` exist, and widen by itself as Phases 5 and 6 register the other two
(§8). Staging the gate needs no staging logic; an unowned path under
`build/` or `site/` is an orphan by definition.

**`rl build` is the pipeline, and it runs the checks.** The order is
`validate` → `check` → `compile` → `index` → `site`, aborting at the first
stage that produces an error — a file whose forms contradict each other has
no business compiling (R13), so the gate is not optional and not a separate
invocation you might forget. `rl check` remains available as a subset for a
tighter loop.

**The order is fixed; membership follows the registry.** `rl build` runs
`validate`, then each **registered** generator in that order — it never
invokes a stage that doesn't exist yet. So in Phase 3 `rl build` *is*
`validate → check → compile`; Phase 5 adds `index`, Phase 6 adds `site`.
One registry drives both consumers: which stages run, and which paths
`--check` diffs. That's what makes D11's two-command CI job correct at every
phase rather than only at the last one.

**Corpus-wide artifacts are written only by a corpus-wide run.**
`build/warnings.json`, `build/index.json`, and `site/` each describe the
whole ledger, so a path-scoped invocation (`rl check remotes/sony/`) reports
to stderr and **refuses to write them**, saying so rather than emitting a
file that claims corpus scope from partial input. Per-file artifacts are the
exception: `rl compile remotes/sony/RMT-B118P.json` may write that one
file's `build/pronto/…` entry, since its scope is exactly the input's.
`--check` is unaffected either way — it regenerates into a temp directory
from the full corpus.

CI runs `rl build --check` (D11). The individual commands keep their own
`--check` for a tighter development loop, but the tree diff is the gate.

**D20 — Every generated file is byte-reproducible by construction.** Serves
R12, D19.

D19 is only viable if regeneration is deterministic to the byte — and v0.2
pinned only JSON, while D19 also diffs `site/`. The contract covers **all**
generated text:

*Common to every generated file.* UTF-8, no BOM; LF endings; exactly one
trailing newline. **No non-reproducible value may appear anywhere** — no
timestamp, no tool version, no absolute path, no hostname. This is the rule
D19 rests on: a single `"generated": "<ISO 8601>"` field would fail CI on
every single run.

*Ordering, everywhere.* Nothing is emitted in filesystem or set-iteration
order; the corpus is walked as `sorted(glob(...))`. Beyond that, v0.4
contained a flat contradiction — "candidates by id with `primary` first"
cannot hold alongside `sort_keys=True` when candidates are an object, since
`mode2` sorts before `primary`. Byte order and reading order are different
concerns and now have different rules:

| Artifact | Rule |
|---|---|
| **JSON object keys** | Lexicographic, via `sort_keys=True`. Full stop — no artifact-specific exceptions, including candidates. The default is identified by the *name* `primary`, which is normative (D16) and needs no ordering to convey it |
| **JSON arrays** | **Classified per field in the schema by an `x-order` annotation, `"semantic"` or `"set"`.** A semantic array is never reordered by anything; a set array in a *generated* artifact is emitted in a declared canonical sort. See below |
| **Rendered output** (HTML, `rl lookup` text) | Explicit sort with `primary` first, then remaining candidates by id — this is where reading order belongs, and these artifacts have no key-sorting rule to conflict with |

Sorting is by Unicode code point — Python's default — and never
locale-aware, so CI and a laptop in a different locale produce the same
bytes.

*Semantic versus set-like arrays.* v0.6's blanket "never reordered" was too
strong — it contradicted D32, which sorts `warnings` precisely so the file
is byte-stable. Both rules are right about different arrays, so the
distinction belongs in the schema, declared per field:

| Class | Fields (v1) | Rule |
|---|---|---|
| **Semantic** — order carries meaning | `forms` (D7's final tie-break is literally array position), a layout's `areas` (row order *is* the geometry) | Never sorted, deduped, or "stabilized" by any generator or formatter. A blanket canonicalize-everything pass would break selection silently, which is the risk v0.6 was guarding against |
| **Set-like** — order carries nothing | `aliases`, `controls`, `warnings` (D32), `expandedFrom.inherited` / `overridden` | In a generated artifact, emitted in a declared canonical sort — required, since byte-stability has to come from somewhere. In a hand-authored file, left exactly as written; `rl fmt --sort` normalizes on request, never silently |

The classification is a schema annotation, not a convention: **every
`"type": "array"` node carries `x-order` with the value `"semantic"` or
`"set"`, and a schema self-test asserts it.** A `"set"` array of objects
additionally carries `x-sort`, the field-name tuple to order by — `warnings`
declares `["file", "key", "candidate", "peer", "form", "code"]` (D32); a
`"set"` array of strings needs none, sorting by code point. That test is
what makes "adding an array field forces the question" real rather than
aspirational — without it, a new field silently inherits whichever rule the
implementer assumed.

*Source files are a different contract.* D20 governs **generated** files.
`rl fmt` rewrites hand-authored files in `remotes/`, and there it uses a
declared key order rather than `sort_keys`, because those files are read by
people and are never byte-compared by D19:

| Object | Key order |
|---|---|
| Remote root | `manufacturer`, `model`, `aliases`, `controls`, `protocol`, `variants`, `keys`, `layouts` |
| `protocol` | `name`, `carrierHz`, `unitUs`, `minSends`, `defaultGapUs`, `tolerance`, `claims` |
| Form | `id`, `type`, `candidate`, `device`, `subdevice`, `function`, `intro`, `repeat`, `truncated`, `hex`, `confidence`, `verifiedBy`, `derivedFrom`, `expandedFrom`, `source`, `claims` |
| Variant | `label`, `confidence`, `source`, `override` |
| Layout | `original`, `label`, `source`, `areas`, `printedLabels`, `shape` |

**Any key not named in its object's list sorts lexicographically after every
key that is.** That fallback is what keeps `rl fmt` total: adding a schema
field never leaves the formatter undefined, and the new key simply lands at
the end until someone decides where it belongs. The array rule is absolute
in both contexts.

*JSON.* `json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False,
separators=(",", ": "))`. `sort_keys=True` in preference to a hand-chosen
key order — one rule, nothing per-artifact to remember, and no way for a
dict-insertion change to churn the diff.

*HTML, CSS, JS.* Rendered from fixed templates with no autoformatting pass,
each value encoded for the context it lands in (D29). `site/index.json` is
written by the same JSON serializer as `build/index.json` and must be
byte-identical to it — the site needs its own copy because GitHub Pages
serves only `site/`.

*`.gitattributes`.* `*.json`, `*.html`, `*.css`, `*.js` all pinned
`text eol=lf`, so a CRLF checkout can't manufacture a spurious drift
failure.

`build/pronto/<mfr>/<model>.json`:

```json
{
  "schemaVersion": 1,
  "manufacturer": "Topping",
  "model": "RC-15A",
  "protocol": { "name": "NEC1", "carrierHz": 38000, "minSends": 1 },
  "keys": {
    "KEY_POWER": {
      "candidates": {
        "primary": {
          "prontoHex": "0000 006D 0022 0002 0157 00AC ...",
          "confidence": "verified",
          "source": "audiosciencereview.com/.../10708 (user halfSpinDoctor)"
        }
      }
    }
  }
}
```

The candidate named `primary` is the default; any others are labelled
fallbacks (D16), each carrying its own `label`, `confidence`, and `source`.
`minSends` sits once at the protocol level rather than per key (D3a) — it's
a fact about the hardware, and a consumer repeating the Pronto repeat
sequence needs it exactly once. `schemaVersion` is the consumer's
compatibility hook, and the only field that isn't a pure function of the
source file.

---

## 7. Layouts and the site

**D29 — Output is encoded per context, and key names are constrained at the
source.** Serves R9, R11, R17, D20.

`html.escape` was v0.3's whole answer, and it is only correct for one of the
four places generated text actually lands. The site interpolates
author-supplied strings — `source` citations, labels, printed labels — and
`source` is the one that matters most, because the site turns it into a
clickable link:

| Context | Rule |
|---|---|
| HTML text and attribute values | `html.escape(value, quote=True)` |
| A URL in `href` | **Scheme allowlist: `http`, `https`, `mailto` only.** Anything else — `javascript:`, `data:`, a scheme-relative `//host` — renders as plain text, never as a link. A `source` is author-supplied free text (R18 permits a non-URL citation), so this path must assume it is not a safe URL |
| Data embedded for scripts | Emitted as `<script type="application/json">` and read with `JSON.parse`, never string-concatenated into JS. `</` is escaped as `<\/` so no payload can close the tag early |
| A per-remote script, `site/r/<path>.js` (D40) | One call, `ledgerRemote(...<args>)`, whose arguments are a single `json.dumps` array with `ensure_ascii`. JSON is a subset of JavaScript, so the data stays data. `ensure_ascii` escapes U+2028/U+2029, which older engines reject in a string literal. There is no HTML context, so `</script>` is harmless there |
| A CSS identifier in `grid-area` / `grid-template-areas` | **Constrained at the source, not escaped**: key names must match `^[A-Za-z_][A-Za-z0-9_]*$`, enforced by the schema |

The last row is the one that earns its place. R9's whole design is that a
layout's `areas` array drops into a real `grid-template-areas` rule
untouched (D14) — which only stays safe if a key name *cannot* be hostile or
even merely awkward. Constraining the namespace beats escaping at render
time: keys are ours to define, `KEY_POWER` already satisfies the pattern,
and the pattern excludes both `.` (which means "gap" in
`grid-template-areas`) and whitespace (which separates cells), so a key can
never be mistaken for grid syntax. This also retroactively hardens R11 —
"CSS's own grid rules do the collision-checking for free" holds only while
every name is a legal CSS identifier in the first place.

**D14 — Parse `grid-template-areas` by splitting on whitespace.** Serves R9,
R11. Each string in `areas` is one row; `.` is a gap. The parser checks
uniform column count and that each name forms a single rectangle — these are
CSS's own constraints (R11), so enforcing them is matching CSS, not
inventing rules. The renderer drops the array into a real
`grid-template-areas` rule untouched, which is the entire point of R9 and
the reason no bespoke coordinate system exists here.

The ledger-specific checks — v0.4 named only the first:

- Every name in `areas` resolves to a key in `keys`.
- **Every key in `printedLabels` and in `shape` resolves to a key in
  `keys`** too. An orphan here is the quiet kind of error: a label for
  `KEY_OPTIN` (typo for `KEY_OPTION`) renders nothing and reports nothing,
  and R10's whole premise is that these maps track the grid without being
  part of it.
- **At most one layout may set `original: true`.** SPEC R8 says "exactly
  one, if present"; two is a contradiction about a physical fact, and
  whichever the renderer picked would be arbitrary.
- A key present in `keys` but absent from every layout is **fine** — R7
  allows a remote with codes and no known arrangement, so partial layout
  coverage is a legitimate state, not a warning.

**D15 — The site is one generated HTML file plus `index.json`.** Serves R17,
OD2. *(Since D40, plus one small script per remote under `site/r/`; the page
itself is unchanged in kind.)* No framework, no build step, no server — `site.py` emits static files
deployable to GitHub Pages. Client-side substring search over manufacturer,
model, aliases, and controls, ignoring case, spaces and punctuation, as
`rl lookup` does. `BDP-S360` must find a header that says
`BDP S360`, or the page would report a device that is in the ledger as one
nobody has looked for; a per-remote view showing each key's forms
with its confidence badge and a citation, a copy-to-clipboard Pronto string,
and the layout rendered through actual CSS grid.

**A `derived` form renders its parent's citation, attributed.** D30 forbids
a derived form from carrying a `source`, which would otherwise leave it as
the one displayed form with nothing to show. It renders as
*"derived from `primary.irp`"* — the id linking to that form's row on the
same page — followed by the parent's own citation, hyperlinked only if it
passes D29's scheme allowlist. So every displayed form still has a
traceable citation; a derived one just reaches it in two hops, which is
exactly what `derivedFrom` means.

Honoring R20 in the UI is a requirement, not polish: a device found in
`unresolved.json` renders as "checked — nothing found", a remote with no
`layouts` says "no layout recorded yet", and an absent device says "not in
the ledger". Three distinct states, never one shared blank.

OD2 was the decision with the most ongoing upkeep attached. Keeping the site
to a single dependency-free generated page is what keeps that cost near
zero — if it ever grows a framework, revisit the decision rather than
absorb the maintenance quietly.

---

## 8. Build plan

Seven phases. Each ends with something runnable, and the order front-loads
the parts most likely to be wrong.

| # | Phase | Delivers | Done when |
|---|---|---|---|
| **0** ✅ | Skeleton | Repo layout, `pyproject.toml`, both schemas, `.gitattributes`, CI. **SPEC §12 replaced by §1's resolutions** (§9) | Done. `rl --help` runs; `rl build --check` exits 0 with no generators registered |
| **1** ✅ | **Core signal path** | **SPEC §7 R12 given a real contract** (§9), then `signal.py` (D1a), `pronto.py` encode + decode (D6, D25), numeric bounds + pinned `Decimal` context (D28), `protocols/nec.py` as a metadata record (D3), `protocol` block schema (D24), serialization contract (D20) | Code done, suite green: round-trip holds in cycles, a zero-cycle duration is rejected, carrier is compared as words. D18 gate 2 was met only after v1, and not in the form first written. "Compiles to the published golden Pronto, byte for byte" turned out to be unmeetable, because the reference tools round differently from each other. Our *timings* reproduce IrpTransmogrifier's published NEC1 vectors exactly, and our bytes differ only at the 4 words D6 rule 4 rounds differently. See §12 |
| **2** ✅ | Forms, candidates & cross-check | The nine SPEC edits landed first (§9), then `forms.py` (D7, D21), candidate groups (D16, D26), `variants.py` (D17, D22, D23, D33), `claims` (D27), `crosscheck.py` (D8, D5a), `remote.py`, `check.py` (D9, D16), `fmt.py`, `warnings.py` (D32); `rl check` / `rl compile` / `rl fmt` wired | Done. All six criteria verified: a corrupted second form is caught at `intro[35]`; two differing candidates pass and genuinely differ; a derived-only group fails validation; a mismatched Pronto frequency word fails while a raw form one word off only warns; an over-extent truncated capture errors rather than clamping; `rl fmt --expand` twice changes nothing |
| **3** ✅ | **Seed data** | `Sony20` added (IRP cited); `remotes/topping/RC-15A.json` authored; `compile` and `check` registered as generators, so `rl build --check` is live in CI over `build/pronto/**` and `build/warnings.json` (D19, D32); D18's table corrected | Done, but only after Phase 6. The corpus validates, compiles and cross-checks green, and the gate catches drift *and* orphans. At Phase 3's close, two of the three seed files were blocked on sources. The Samsung followed once its protocol was identified as `NECx2` (D18, PR #7). The Sony followed from a cited hardware capture that also overturned SPEC §1's subdevice (§13, PR #8). What remains open is the BX510's own link to that remote, which `unresolved.json` records |
| **4** ✅ | Layouts | `layout.py` (D14) parsing `grid-template-areas` by splitting on whitespace, CSS's own uniform-width and single-rectangle constraints, the CSS-identifier key rule (D29), and R8/R10's ledger-specific checks; wired into `rl validate` | Done. SPEC §6's D-pad parses, round-trips through a real CSS declaration, and validates on a remote; a typo'd `printedLabels` key fails instead of rendering nothing |
| **5** ✅ | Index & lookup | `index.py` (D13, R14) computed from the files and committed (OD4), folding in `unresolved.json` (D12) and surfacing R15's alias conflicts; `index` registered as a generator so the gate widened to all of `build/` by itself; `rl lookup` (R16) | Done. R20's three states render visibly differently — in the ledger, checked and not found, nobody has looked. The acceptance query `rl lookup "Sony BDP-BX510"` returns the *second* state rather than three candidates. That is still the honest answer: the RMT-B118P is now authored, but no source ties it to the BX510, and the sources contradict SPEC §1's addressing (§13). R20 asks for exactly that answer |
| **6** ✅ | Site | `site.py` (D15): one HTML file plus `index.json`, no framework and no server, with the ledger embedded as a JSON island so it works from `file://` too; `encoding.py` (D29) for per-context output; two-hop citations for `derived` forms (D30); layouts rendered through real CSS grid (R9); Pages workflow. **Gate covers `build/` + `site/` — full D19** | Done. R20's three states render visibly differently; a `source` that is not an `http(s)`/`mailto` URL renders as text, not a link. Searching the Samsung now reaches the remote (PR #7); at Phase 6's close it reached a *checked-and-not-found* entry, because the protocol had not yet been identified |

Phase 1 is the real risk and deserves the most care — it is where a wrong
constant would silently poison everything downstream. Phase 2 now carries
the second-largest risk, since D16's candidate model is what decides whether
Phase 3's seed data is representable at all; build the BX510 variant case as
a fixture in Phase 2, before authoring the real file. Phase 3 is the first
honest proof: three devices, three protocols, three different reasons to
trust the codes, exactly as SPEC §1 framed the problem.

**The CI gate turns on in Phase 3 and widens by itself.** D19's generators
each own a path, and `--check` diffs the union of whichever are registered —
so Phase 3 gets a real drift-and-orphan gate over `build/pronto/**` **and
`build/warnings.json`**, the two owners that exist by then, without waiting
for the index and site that don't. Phases 5 and 6 extend it by registering a
generator rather than by editing the CI config — which is also what keeps
D11's two-command job correct at every phase.

**v1 is complete at Phase 6.** Not in v1 and not planned: capturing from
hardware (SPEC §4 out-of-scope), unmodulated signals (D1a), the six
backlogged protocols (D18), and — per OD1 — any contribution workflow.

**Phase 7, post-v1: the LIRC import (§14).** SPEC v0.9 reversed v1's ban on
bulk import under R19's five conditions. It lands in four PRs, in order:
this design; the lircd transmit port, verified against lircd itself (D36);
index and site sharding, so the ledger scales past one page (D40); then
the data. SmartIR (§15), RC-5 (§16) and the IR Blaster import (§17, §18) followed
as post-v1 work outside the seven phases, and the last of them is the one that
no longer fits D40's promise (§17).

**Phase 8, post-v1: more sources.** SmartIR followed as the second import
(§15, PR #21) and the IR Blaster database as the third (§17). SPEC v0.11
makes that the standing direction rather than an exception: every database
whose licence permits republishing is a candidate. Each lands the way
SmartIR did, as its own §-numbered design section, an importer, and a
registered `remotes/<source>/` directory. The SPEC needs no edit to admit
it.

---

## 9. Edits to the existing docs — scheduled, not deferred

**SPEC.md is normative for the format; DESIGN.md is normative for the
build.** Where they disagree, the spec is wrong and the edit below is a
deliverable, not a follow-up — because the format is what a file on disk has
to satisfy, and a file can't satisfy a document that describes flat `forms`
while the validator enforces candidate groups.

**The routing rule**, so this list stops needing to be reconstructed by
hand: a decision that changes **what a file on disk must contain** is a spec
edit. A decision that only constrains *tooling* is not. v0.3's list was
assembled from the decisions that happened to be discussed as format
changes, and so missed most of D21–D27, each of which adds a required field
or constraint to the format while being written up as a build decision.

Applying the rule, in the phase that makes each true:

| Phase | Edit | From |
|---|---|---|
| **~~0~~ done** | **SPEC §12** — replaced with §1's resolutions, keeping the reasoning. Retitled "Resolved decisions" | §1 |
| **~~1~~ done** | **SPEC §7, R12** — said "compile deterministically" and stopped. Now carries the contract in outline with the normative rules by reference | D6, D25, D28 |
| **~~2~~ done** | **SPEC §5, R4 — the substantive one.** R4 says a key holds a `forms` array, which conflates two relationships. It has to say forms are partitioned into *candidate groups*: forms within a group are representations of one signal and must agree; groups are competing hypotheses and must not be compared. Without it, SPEC §1's own BX510 example is unrepresentable — the clearest sign it belongs in the spec, not only here | D16 |
| **~~2~~ done** | **SPEC §5, R4 — form identity.** Every form has an `id`, unique within its key and never positional; `derivedFrom` names an id in the same group, not a type; every candidate group holds at least one non-derived form | D21 |
| **~~2~~ done** | **SPEC §5 — the worked example.** Read `"derivedFrom": "irp"`, which D21 makes invalid. Fixed to `primary.irp` in Phase 1, since it is the same JSON block as the Pronto-string correction above | D21 |
| **~~2~~ done** | **SPEC §5 — `variants`.** A new remote-level block: it declares every non-`primary` candidate group, auto-expands when it carries an `override`, and writes an `expandedFrom` provenance record into each form it generates. Overrides are limited to `device`, `subdevice`, `function`. A form bearing `expandedFrom` is a regenerated cache, not an authored override | D17, D22, D23, D26, D33 |
| **~~2~~ done** | **SPEC §5, R5 — the `derived` form.** R5 requires every form to name how it was established, which a `derived` form appears to violate. It doesn't: `derivedFrom` *is* its citation, and the only one CI verifies. R5 has to say so, and that a `derived` form must be `type: pronto` and must **not** carry a `source` | D30 |
| **~~2~~ done** | **SPEC §5, R3 — the protocol block.** R3 names carrier, timing, and min-sends. The real field set adds `unitUs`, `defaultGapUs`, `tolerance`, and `claims`, with `name` optional (⇒ no `irp` forms) and exactly one protocol per file | D23, D24 |
| **~~2~~ done** | **SPEC §5, R4 — the `raw` form.** Its shape, and `truncated` as a declared property, with the per-sequence gap substitution stated as a formula: drop the declared-bad space, pad to the sequence's own extent, and fail rather than clamp when the marks already exceed it | D4a, D31 |
| **~~2~~ done** | **SPEC §10, R18 — citations for overrides.** R18 covers a form's evidence but not the fields that *loosen* a check. Every overriding field needs a `claims` entry with a reason **and** an independently checkable source | D27 |
| **~~2~~ done** | **SPEC §5, R6** — `derived` is excluded from selection, array order is the final tie-break, precedence resolves per group | D7 |
| **~~2~~ done** | **SPEC §7, R13** — scope "cross-check every additional form" to *within a candidate group*; exclude `derived` forms from cross-checking entirely; point at D8's algorithm, which R13's "only the trailing gap-fill may drift" doesn't cover once `raw` is first-class (OD3) | D5a, D8 |
| **~~3~~ done** | ~~**README.md**~~ — **corrected: the wrong Pronto string is in SPEC §5, not README.md, which contains no Pronto string at all.** Every revision from v0.1 carried the misattribution, and it was found only by grepping during implementation — a design document naming a file it never checked. Fixed in Phase 1 rather than 3, since verified compiler output now exists: `0156 00AB → 0157 00AC`, and `0022 0000 → 0022 0002` (v0.1 dropped NEC1's repeat sequence) | D6 |
| **~~4~~ done** | **SPEC §6, R10** — key names must match `^[A-Za-z_][A-Za-z0-9_]*$`. R11 leans on CSS grid for collision-checking, which holds only while every area name is a legal CSS identifier | D29 |
| **~~4~~ done** | **SPEC §6, R8 / R10 — enforce what they already imply.** Every key in `printedLabels` and `shape` must resolve to a real key, and at most one layout may set `original: true`. R8 says "exactly one, if present" and R10 describes sibling maps; neither states the constraint as checkable | D14 |
| **~~5~~ done** | **SPEC §11, R20** — reference `unresolved.json`; as written R20 has no mechanism behind it, and Phase 5 is where the mechanism lands | D12 |
| **7 done** | **SPEC §3, §4, §2 and R19 (v0.9).** R19 now permits importing a database on five checkable conditions: the licence permits republishing, each form cites its origin, nothing lands above Plausible, authored data wins, and the import is regenerable. §4 names the sources that stay excluded, and why. **R15** is scoped to a manufacturer: the global model-name check was written against three files, and the import has 55 cross-maker pairs such as Apple's and Pioneer's `CD` | D34–D40, D13 |
| **post-6 done** | **SPEC §1 and §5's tier table.** §1 stated the lookups' claims as if they were the ledger's contents, and its RMT-B118P row claimed Verified at subdevice 218. It now records the claims as claims, then what the ledger holds. Plausible now also covers a single capture that nothing cross-checks, which is how PR #8 tiered 11 keys: no existing tier fitted, and the data came before the definition | §13 |
| **post-7 done** | **SPEC §2, §3, §4, R19 and §12 (v0.10).** R19 admits a third database, the IR Blaster code database as shipped in SwiftRemote. R19.1 now says how each of the three meets the licence condition: LIRC by a reading (Debian's), SmartIR by a stated MIT licence, and IR Blaster **by inheritance only**, because nobody in its lineage says where the data came from. R19.2 generalises the citation beyond LIRC's file, block and line, and adds that where a source stores a code as a hexcode and a protocol name, the reading is the importer's claim and the committed report counts where the source's own app reads it differently. R19.3 adds that one source of unknown origin is Plausible, and R19.4 stops naming `remotes/lirc/`. §2 gains the database as prior art, §3 stops calling the admitted sources "openly licensed", §4 counts three, and §12's note on gate-2b vectors counts 28 protocols | D46, D50, D55, D68 |
| **8 done** | **SPEC header, §2, §3, §4 and R19 (v0.11).** Imports are open to every source R19's conditions admit, and there is no fixed list. R19.1's licence boundary generalises to `remotes/<source>/`, registered with its licence. R19.2 says how a SmartIR form is produced, as it already did for LIRC. A new paragraph under R19 says that a missing parameter, such as a carrier or repeat count, is defaulted and cited rather than used as a reason to drop the key | §15 |

Decisions that are *not* spec edits, for contrast: D19's tree ownership,
D20's serialization, D28's `Decimal` context, D10's vector-citation gate,
D18's registry metadata. None of them changes what a valid file contains.

The Phase 2 edits land *before* the Phase 2 code, not after it. That
ordering is the point: the spec change is what authorizes the
implementation, and doing it in the other order is how a design document
quietly becomes the real spec. Sixteen edits in total, **ten of them in
Phase 2** — a fair signal that Phase 2 is the one to slow down on.

All sixteen have landed. A seventeenth followed v1 (SPEC v0.8), after the
seed-data research overturned part of §1. It broke the ordering rule above,
and only in hindsight: PR #8 tiered data before the spec defined that tier.
Recording it as a late edit is more honest than folding it back into Phase 3.

---

## 10. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| A hand-written encoder is subtly wrong, with no oracle to catch it | **High** | D10's cited golden vectors, gated: no protocol ships without one. The honest residual: a protocol whose only published vectors share a common ancestor error stays wrong. If a vector can't be sourced independently, don't ship the protocol. **Since §18 the residual is live**: 22 of the 28 protocols' vectors are generated by the same tool whose database supplied their IRP strings, and three of them (RECS80-0068, JVC-48, SharpDVD) have no capture or published table behind the IRP either. They are in the registry, so that last sentence is not met for them (D68) |
| Rounding drift breaks byte-identical output (R12) | Medium | D6 pins `ROUND_HALF_UP` on `Decimal` and the clock constant; corpus snapshot test in CI |
| ~~Samsung32's lead-in is disputed~~ — **resolved, and it was never a timing dispute** | — | The protocol was misidentified. The BN59-01199F speaks `NECx2`: an **8**-unit lead-in at 564 µs, 8 × 564 = 4512 µs, which is what the "real captures" were showing (D18, PR #7). Phase 3's interim explanation, Samsung36's 9-unit lead-in at 500 µs, fitted the same ~4500 µs and was wrong. It was recorded as a hypothesis only, never as data, and the device waited in `unresolved.json` until a cited source settled it (D12) |
| `raw` jitter tolerance passes a genuinely wrong code | Medium | D8's tolerances are tight by LIRC standards, symmetric so order can't change a verdict, and per-file overridable; an override needs a `source` explaining itself |
| Candidate groups (D16) let untested alternates accumulate and never get resolved | Medium | They're open questions by design, so make them visible rather than silent: `rl lookup` and the site surface every non-`primary` group with its tier, and the index rolls up an "unresolved alternates" count per file |
| Site upkeep outgrows its value (OD2) | Low | D15 keeps it dependency-free; a framework is the signal to revisit OD2 |
| Committed `build/` creates merge noise | Low | Single-author repo (OD1); D19's tree check makes drift loud |
| The LIRC import makes the repo and the site large (~2,800 upstream files, ~115k keys with timings) | Medium | D40 shards the index and site per remote, so no committed file grows with the corpus. D20's one-line integer arrays cut raw forms roughly in half. The size is measured before the data PR, not after |
| Imported data is taken for authored data | Medium | R19's conditions, enforced: the `remotes/lirc/` path is the licence *and* trust boundary, every form's citation names its upstream file and line, nothing is above Plausible, and the site labels imported remotes as imported |
| An imported key's defaulted carrier is wrong for its protocol | Low | Nothing is dropped over it. The default is cited on every form, and every such form is Plausible. §15 measures it: about 14% of SmartIR's raw keys are 36 or 40 kHz families played at 38 kHz, which costs range rather than function. Inferring the carrier from a recognised timing family is the planned fix |
| The GPL reading of the LIRC database is wrong | Low | It is Debian's reading, cited, and the only one on record (the upstream repository states no licence). The boundary is one directory, so reversing it is one deletion and one re-import |
| The IR Blaster data belongs to someone who does not allow its republication | Medium | Nothing mitigates the *probability*: its origin is unknown and the licence is inherited from the app it ships in, not granted for the data (D46). What is bounded is the cost: one directory, one deletion, the same exit as LIRC's. R19.1 now names the footing, and the importer's README says it without softening |
| The wire reading of a hexcode is wrong for a family, and the ledger holds a bug SwiftRemote does not | Medium | For ten protocols the ledger holds a reading that differs from the app's, for 44,789 keys (D57). The evidence is published decodes, real LIRC frames and structure, and no device was tested. The reading is isolated in one `FROM_DB_HEX` function per protocol, the app's reading is kept beside it as `FROM_DB_HEX_APP`, and `IMPORT.md` counts every code on which they differ, so reversing a family is one function and one re-import. Plausible is the tier that says none of it was checked on hardware |
| The IR Blaster import makes the index, the site page, `rl build` and CI too heavy | Medium | Measured, not mitigated (§17). `build/index.json` is 15 MB (12× larger) and the page embeds 11.5 MB; `rl build` and `rl build --check` take 18 minutes each; `rl lookup` takes 12 to 17 s. D40's per-remote sharding bounds the per-remote files and no longer the index. The decision is open |
| Provenance quietly degrades as variants are expanded and re-expanded | Low | D22's field partition is mechanical and `expandedFrom` is machine-checkable; `rl fmt --expand` shows exactly what a variant produced. D21's non-positional ids remove the silent-retarget path |
| A `claims` entry gets written to satisfy the validator rather than to inform | Low | **Presence is machine-validated; truthfulness is not.** The validator confirms `reason` and `source` are there and non-empty — it cannot confirm the source says what the claim says, or that it exists. Reviewing that is a human job, and with OD1 the human is you, reading the diff. The gain over v0.3 is narrow but real: nothing had to be written down at all before |

---

**Start here:** Phase 0 and Phase 1. `signal.py` + `pronto.py` +
`protocols/nec.py` + one cited NEC vector is roughly 400 lines and settles
the question the whole project rests on — whether a hand-written encoder
reproduces a published Pronto string byte for byte.

---

## 11. Revision history

### v2.1 — the IR Blaster import and twenty-four protocols

Not a review: the work in §17 and §18 made earlier statements stale or false,
and this entry records what it changed. §14 to §16 landed without entries
here, so nothing below covers the LIRC, SmartIR or Meridian work. Ten rows.

| Issue | Resolution |
|---|---|
| D18 said the registry held three protocols (four since §16) and named `NEC2`, `Sony12`, `Sony15` and `RC6` as backlog | **D18** — the table carries all 28, every IRP string the registry's own; the backlog is `NEC` and the relaxed `-f16` forms. "One lookup at a time" is marked as bent, since 24 protocols arrived in one stretch, though through the same gate |
| D18 said RC6 needs a bitspec exception | **D18, D63** — it did not: the encoder builds per-unit levels and run-length encodes them, and the `encode` interface is unchanged. RC6 is registered for mode 0 only |
| D3b counted RC5 as the one toggle protocol and said RC6 is unregistered | **D3b** — five protocols have a toggle (RC5, RC6, Thomson7, RECS80, RECS80-0068), and 47,137 imported keys compile `T=0` and match the app only at `T=1`. Still open, still a contract with the apps |
| D3 listed `Samsung32`'s extent, which never existed, and D24 said all three protocols declare one | **D3, D24** — corrected. Fifteen of the 28 declare no extent, so D24's `defaultGapUs` rule now bites a file that names one of them, not only a raw-only file |
| §12's gate-2b table covered four protocols and one of them was reproducible-only | **§12** — 28 rows, each with its vector's provenance and where our bytes differ. Six are published and 22 reproducible only. **D68** says what that does and does not establish |
| §10's first mitigation, "if a vector can't be sourced independently, don't ship the protocol", is not met for `RECS80-0068`, `JVC-48` and `SharpDVD` | **§10, D68** — said in the risk row and in D68 rather than left for a reader to find. The owner may prefer to remove the three until a source appears |
| D40 promised that nothing committed grows with the whole corpus; the index does, at 12× | **§17** — measured (15 MB index, 11.5 MB page, an 18-minute `rl build`), not fixed, and the open decision is named. D40 carries a pointer |
| R19.1 named one licence and one directory, and the third import's footing is weaker | **SPEC v0.10, §9, D46** — R19.1 now says how each of the three meets the condition, and the IR Blaster's is "by inheritance and nothing more". The repository root has no licence file, which D46 flags as a TODO |
| The working rule for the importer was "a hexcode means what the app's code says", and for ten protocols the app and the data disagree | **D50, D57, D53** — the owner chose the wire reading. The app's reading is kept beside it as `FROM_DB_HEX_APP` and `IMPORT.md` counts the 44,789 affected keys. What the app does differently, a stale copy of upstream among it, is listed in §18 for the app's owner |
| §6's layout named `samsung.py`, §12 documented 890 tests, and README said "33 numbered decisions" | **§6, §12, README** — fixed. The count is 2,257 on this branch; the suite asserts it (`test_documented_test_count_is_current`), and whoever adds tests next re-fixes it |

### v0.9 — eighth review

Three substantive findings plus three consistency items — six rows.

| Issue | Resolution |
|---|---|
| **`canon()` was context-sensitive — and lossy at the stdlib default, not just under a hostile context.** `Decimal.normalize()` rounds to the ambient precision, so at Python's default `prec = 28` it turned a 39-digit value into `0.1` | **D28** — replaced with a context-independent algorithm operating on `as_tuple()`'s coefficient and exponent: nothing in the path can round, since `as_tuple()` reads stored values, the tuple constructor applies no context, and `format(…, "f")` without precision is context-independent. Verified across `prec ∈ {1, 3, 9, 28, 34, 99}` × three rounding modes: output identical in every context, idempotent, equal values always agreeing. Pinning the *Pronto* context was never going to cover this — serialization happens elsewhere |
| A cached expansion could outlive its premises: parent deleted, or variant converted to metadata-only (which D26 permits), and the orphan still satisfied cardinality | **D33** — a cache is valid only while four premises hold: the variant exists **and still carries an `override`**; `expandedFrom.form` resolves; that form is still the group's **currently selected IRP form**; and the content matches. Any failure is a validation error, and `rl fmt --refresh` repairs all four — recomputing for the last three, *removing* the cache for the first, since a variant with no override has no expansion to hold. Deliberately the same lifecycle D9 gives a `derived` form: two kinds of generated form content, one rule |
| D19 registered only `compile` and `check` in Phase 3, while the pipeline read as always running `index` and `site` | **D19** — the order is fixed, membership follows the registry: `rl build` runs `validate` then each *registered* generator, never a stage that doesn't exist yet. Phase 3 *is* `validate → check → compile`. One registry drives both which stages run and which paths `--check` diffs — which is what makes D11's two-command job correct at every phase rather than only the last |
| D20's array classification had no annotation name or machine-readable values | **D20** — `x-order` with `"semantic"` or `"set"` on every `"type": "array"` node, asserted by a schema self-test; `"set"` arrays of objects additionally carry `x-sort`, the field tuple to order by (`warnings` declares its six). The self-test is what makes "forces the question" real instead of aspirational |
| `rl fmt`'s key order was a partial list with no fallback | **D20** — the full order for all five object kinds, plus the rule that **any key not named sorts lexicographically after every key that is**. That fallback keeps `rl fmt` total: a new schema field never leaves the formatter undefined |
| `--strict` documented in D32 but absent from the CLI contract | Added as a global flag alongside `--allow-dirty`, with the commands that accept each |

### v0.8 — seventh review

Five findings plus one stale paragraph — six rows.

| Issue | Resolution |
|---|---|
| **CI could self-acknowledge a new warning.** A corpus-wide `rl check` writes `build/warnings.json`, and D11 ran it *before* `rl build --check` — so the tree comparison saw the freshly rewritten file, came up clean, and let a brand-new warning pass uncommitted | **D11** — CI now runs exactly two commands, `pytest` and `rl build --check`, and the shortness is load-bearing. The stated rule rather than the corrected recipe: **no command that writes into `build/` or `site/` may run in a job that also runs a `--check`** — a corpus-wide `rl check` is a generator run (D19) and belongs with `compile`/`index`/`site`. `rl build --check` already subsumes the dropped steps into a temp directory. Plus defense in depth, so a helpful edit can't reopen it: `--check` refuses to run when a generator-owned path has uncommitted git modifications — exactly the case where the comparison is vacuous — with `--allow-dirty` for anyone who wants it |
| Cached-expansion cardinality undefined: multiple caches for one (key, variant), or a cache beside an authored form | **D33** — two rules. At most one form per (key, variant) may carry `expandedFrom` (D21's id uniqueness is per *key*, so distinct explicit ids could both claim one variant and make "replace the one match" meaningless); and a variant group holds at most one `irp` form total, cache or authored, never both. What it deliberately permits: authored `raw`/`pronto` forms alongside the cache — a hardware capture confirming a variant is the evidence you want, and it's how a variant graduates out of `untested` |
| `format(d, "f")` still didn't make equal values byte-identical — it preserves insignificant zeros and signed zero | **D28** — normalize first: `canon(d) = "0" if d == 0 else format(d.normalize(), "f")`. Verified by running it: `1.0`/`1.00`/`1` all emit `1`, `-0`/`0.00` emit `0`, `1e-2` emits `0.01`, `1.0E+2` emits `100`, and the function is idempotent. *(v0.9 replaced this: the test ran only in the default `decimal` context, and `normalize()` turns out to round there too — so the claim was tested, but not by a test capable of failing.)* |
| Cross-group redundancy comparison unspecified — which forms, and with what tolerance | **D16** — compare each group's **compiled Pronto string** (D6's output from its D7-selected form) by **exact string equality**, not D8's tolerances. The warning's claim is "these two emit the same artifact," and the artifact is that string; under ±15 % two captures of genuinely different addresses could match and warn falsely, while two groups one cycle apart ship different bytes and aren't redundant. Also makes `raw`-versus-`irp` work with no special case, and `check` computes it without needing `compile`'s files |
| `rl fmt --sort` referenced by D20 but absent from the CLI table | Added, with the scope stated: it orders *set-like* arrays only, never semantic ones |
| Stale paragraph | §8's Phase 3 gate prose named only `build/pronto/`; it now names both Phase 3 owners, `build/pronto/**` and `build/warnings.json` |

### v0.7 — sixth review

Six findings, plus the two cleanups folded into a final row — seven rows.

| Issue | Resolution |
|---|---|
| `build/warnings.json` wasn't in the generator contract — absent from the layout, D19's owner list, and `rl build`'s "all three" | **D19** — a four-row owner table with `check → build/warnings.json` registered in Phase 3; layout, CLI, and architecture diagram updated. Two rules v0.6 left implicit now stated: **`rl build` runs the pipeline** `validate → check → compile → index → site`, aborting at the first erroring stage (a file whose forms contradict each other has no business compiling, R13), and **corpus-wide artifacts are written only by a corpus-wide run** — a path-scoped `rl check` reports to stderr and refuses to write a file that would claim corpus scope from partial input, while per-file `build/pronto/…` entries are the exception since their scope matches the input's |
| Variant expansion could select a non-addressable parent | **D17** — it copies the **selected IRP form**: D7's precedence applied to the `irp` forms *only*. D7 ranks confidence before type, so a `confirmed` raw capture outranks a `verified` irp form and would leave the override with no `device`/`subdevice`/`function` to act on. Filtering first, then ranking, keeps determinism and guarantees addressability — and makes "no `irp` form at all" the only remaining skip case |
| D20 forbade generators from sorting arrays; D32 sorts `warnings` | **D20** — arrays are classified **per field in the schema** as *semantic* (`forms`, a layout's `areas` — never reordered, since D7's tie-break is array position and a row's order *is* the geometry) or *set-like* (`aliases`, `controls`, `warnings`, `expandedFrom.inherited`/`overridden`, `searched` — canonically sorted in generated artifacts, untouched in hand-authored ones unless `rl fmt --sort` is asked). A schema annotation rather than a convention, so a new array field forces the question |
| D31 was undefined at `n = 0`, where `m = −1` | **D31** — empty sequences are rejected, not defined: a present sequence key must hold ≥ 1 duration, and the way to say "no repeat" is to omit `repeat`. The same absent-versus-empty distinction Pronto already draws, and that D25 relies on for `n1 = 0`. Keeps D31's arithmetic total with no special case |
| `redundant-candidate` names one group but compares two, so two warnings on one key could collide on a supposedly total sort key | **D32** — it carries `candidate` **and** `peer`, ordered `candidate < peer`, which also reports each pair once instead of from both sides. The sort key gains `peer` and is now genuinely total |
| The Decimal section overclaimed byte stability — `str()` gives `0.01` for `Decimal("1e-2")` and `1.0E+2` for `Decimal("1.0E+2")` | **D28** — reframed as **canonical, value-stable output, not lexical preservation**, with a fixed plain-notation formatter (`format(d, "f")`) instead of `str()`. The property D19 and D20 actually need: equal values produce identical bytes and a second `rl fmt` is a no-op. A source `1e-2` normalizes once to `0.01` — not recurring churn |
| Cleanups | Phase 2 says **ten** SPEC edits, matching §9. D28's per-sequence cap is **2048 durations** — `n` in D31's notation — explicitly not Pronto burst pairs, of which it is 1024 |

### v0.6 — fifth review

Six findings plus a set of count corrections. Eight rows, because the D32
and variant-expansion findings each resolved in two places.

| Issue | Resolution |
|---|---|
| D31 rejected `gap < unit_us` but `unit_us` is undefined for a raw-only file — and `sequence = d₁ … d_head` was malformed, since `head` is a duration sum, not an index | **D31** — the formula now carries an explicit kept-count `m` distinct from the sum `head`. On the floor: it applies to **branch A only**, which is reachable only when a registry entry exists (that's where `extent_us` comes from), so `effective_unit_us` is always defined where it's used. A raw-only file takes branch B by construction — no `name` ⇒ no registry entry ⇒ no extent — and branch B computes nothing, so D28's bounds are the whole constraint |
| D32 required warnings in `build/index.json`, whose generator isn't registered until Phase 5 | **D32** — warnings move to a standalone `build/warnings.json` owned by `rl check` and registered in **Phase 3**, alongside the gate. D13's index needs no change. Before Phase 3 there is no gate and a human is reading stderr, so stderr-only is stated as sufficient |
| Warning storage underspecified — the fixed `<file>:<key>:<form-id>` location was wrong for two of the three codes | **D32** — a scope table (file / candidate group / form) with the location emitted only to the depth a warning has, absent fields **omitted rather than null**, and a total sort key so D20's ordering rule applies with no special case |
| `parse_float` unspecified, so `tolerance.relative` arrived as a binary float | **D28** — `parse_float=Decimal`, so every non-integer number enters as a `Decimal` built from the literal text (`0.15` exactly, not the binary approximation); `parse_int` stays default; `rl fmt` emits `str(d)` verbatim so the round-trip is byte-stable |
| D17 didn't say which `primary` `irp` form a variant copies when several exist | **D17** — expansion is from the `primary` group only, copies **the D7-selected form** (the one that would actually compile, so the variant means "same button, different address" relative to what ships), and silently skips a key with no `irp` form. One parent per expanded form is what makes `expandedFrom.form` a single id |
| D33 didn't say whether a cached expansion is replaced or appended at load | **D33** — replaced in place, matched on `expandedFrom.variant`; appended only when absent. The naive always-append would double on every `--expand` and then fail D21's id-uniqueness check, so it's stated rather than left for the check to discover |
| D30 forbids `source` while D15 promises every displayed form a citation | **D15** — a `derived` form renders *"derived from `primary.irp`"*, linking to that form's row, then the parent's own citation. Every form still has a traceable citation; a derived one reaches it in two hops, which is what `derivedFrom` means |
| Counts | §9 says **sixteen** edits, **ten** in Phase 2 (was "nine"). v0.5's history below says **eight issues plus one cleanup row** (was "seven plus two"). D18 now distinguishes the five *undelivered* protocols from the **six** in the backlog, where `RC6` joins them |

### v0.5 — fourth review

Eight issues plus a cleanup row. All resolved above.

| Issue | Resolution |
|---|---|
| A `derived` form had no `source` while SPEC R5 requires one for every form | **D30** — the exact shape: `type` must be `pronto`, `derivedFrom` required, `source` **forbidden** rather than optional. And yes, `derivedFrom` satisfies R5 — R18 already accepts a same-repo cross-reference, and this is the one citation in the ledger that **CI verifies**, since D9 re-derives it every build. Added to §9 as a Phase 2 spec edit |
| Truncation had no deterministic gap formula | **D31** — extents apply **per sequence** (IRP puts them inside a sequence, so intro and repeat pad independently); a declared-bad final space is **dropped, not adjusted**, since a bad value isn't even a lower bound; and an over-extent capture is an **error, never a clamp**, because clamping would fabricate a waveform that fails the extent it claims. Also states why substitution exists at all — it serves D6 emission, not D8 comparison, so an error here would be invisible to the cross-check |
| D28 bounded the waveform values but no knob in the `protocol` block | **D28** — bounds for `unitUs`, `defaultGapUs`, `minSends`, and all three `tolerance` fields, with `relative ≤ 0.5` the load-bearing one (past 50 % the check stops discriminating). Plus rejection of `NaN`/`Infinity` via `parse_constant`, which `json.load` otherwise accepts and against which every `minimum`/`maximum` comparison is silently false. Standing rule: a numeric field without explicit bounds is a schema bug |
| D8 defined a pair comparator but no aggregation rule, over a non-transitive relation | **D8 step 6** — star against the D7-selected form, never all-pairs. R13 is already shaped that way, the reference is the artifact actually shipped, failures name a canonical form, and the cost is O(n). Named consequence: two non-trusted forms may disagree while both corroborating the trusted one — accepted, since what ships is the trusted form |
| Registry `default_carrier_hz` vs file `carrierHz` — no ownership rule | **D3** — the file's is authoritative with no fallback path; the registry value is informational and renamed `nominal_carrier_hz`, since `default_` invited the confusion. A word-changing deviation warns rather than requiring a claim — 38 vs 38.4 kHz is the common case, and a citation requirement there would be boilerplate that devalues `claims` where it matters |
| `rl fmt --expand` could leave a stale expansion in authority | **D33** — a form carrying `expandedFrom` is a regenerated cache, recomputed and diffed every build; a form without one is authored, and that is what suppresses expansion. To hand-correct a key you **delete `expandedFrom`** — a one-line diff that transfers authority deliberately instead of by side effect. Generalizes D9: two kinds of committed-but-generated form content, one regenerate-and-diff rule |
| `sort_keys=True` and "candidates, `primary` first" could not both hold | **D20** — byte order and reading order separated: object keys always lexicographic with no exceptions, **arrays never reordered by anything** (D7's tie-break is literally array position, so a blanket canonicalizer would break selection silently), and `primary`-first reserved for rendered output. Also states that D20 governs generated files only — `rl fmt` uses a hand-chosen key order for hand-authored ones |
| Warnings existed with no defined behavior | **D32** — exit 0/1 on errors only, stable warning codes on stderr, and warnings recorded in a committed artifact so a new one appears as a tree diff that D19 fails until acknowledged. That's why CI doesn't need `--strict`: promoting the ±1 carrier warning to an error would defeat the reason it's a warning. *(v0.5 put that artifact in `build/index.json`; v0.6 moved it to `build/warnings.json` for the phase reason above.)* |
| Cleanups | D18's backlog is six protocols, so §8 no longer says five. D14 now validates `printedLabels`/`shape` orphans and duplicate `original: true` layouts, with the matching SPEC §6 edit scheduled in Phase 4 |

The `claims` risk in §10 is also rephrased as you put it: presence is
machine-validated, truthfulness is not, and reviewing it is a human job.

### v0.4 — third review

Nine issues, three rated most important. All resolved above.

| Issue | Resolution |
|---|---|
| **Pronto carrier validation was ambiguous** — decoding `006D` gives 38 028.9 Hz, so comparing against a declared `38000` fails valid output, while ignoring the header lets a wrong carrier pass | **D8 step 1** — compare frequency *words*, never hertz: `expected = freq_word(protocol.carrierHz)`. A `pronto` word mismatch fails (a generated artifact is right or from another remote); a `raw` carrier one word off warns (a measurement with instrument error, and 37 900/38 000 Hz are the same word) |
| **D8 and D9 disagreed about derived forms** — the diagram and D5 decoded every form, D25 said derived ones never are, D9 checked strings | **D5a** — one rule: a `derived` form is never decoded, never rendered, never cross-checked, and validated *solely* by D9. With D7 already excluding it from selection, it now participates in exactly one thing. D9 restated as three explicit steps from the named parent |
| **Auto-generated form ids were order-dependent** — `.2`/`.3` from array order silently retargeted `derivedFrom` on a reorder | **D21** — positional suffixes deleted. The auto id `<candidate>.<type>` is assigned *only when unique*; a group with two same-type forms requires explicit ids on all of them. Stable because unique, not because positional |
| §9 omitted the normative format changes in D21–D27 | **§9** — a routing rule (changes what a file must contain ⇒ spec edit; constrains only tooling ⇒ not), then the full list. Grew from 6 edits to 14, nine of them in Phase 2 |
| Protocol extent wasn't machine-readable — D4a needed `^108m` but D3 exposed only `encode()` and a docstring | **D3** — a registry entry is a `Protocol` record with `extent_us`, `irp`, `irp_source`, `unit_us`, `bits`. Validation reads fields, never prose, and D18's first gate becomes a test |
| `tolerance.reason` explains a choice but isn't independently checkable under R18 | **D27** — a uniform `claims` sidecar requiring both `reason` and `source`, applied to `unitUs`, `defaultGapUs`, `tolerance`, and `truncated`. The durable part is the rule: any field added later that widens what passes joins the set |
| D16's example still read `"derivedFrom": "irp"` | Fixed to `primary.irp`; the matching SPEC example is a Phase 2 edit (§9) |
| Pronto numeric bounds unspecified; `Decimal` rounding read from a mutable process-wide context | **D28** — bounds for carrier, frequency word, burst count, pair counts, and raw durations, with **zero cycles** an error rather than a silent `0000`; all arithmetic inside an explicit `localcontext()` |
| `html.escape` is wrong for three of the four output contexts | **D29** — per-context encoding: escape for HTML, an `http`/`https`/`mailto` scheme allowlist for `href` (a `source` is author-supplied text), `<script type="application/json">` + `JSON.parse` for data, and for CSS identifiers a constraint at the source — key names must match `^[A-Za-z_][A-Za-z0-9_]*$`, which also hardens R11 |

### v0.3 — second review

Ten further issues, six rated must-fix. All are resolved above.

| Issue | Resolution |
|---|---|
| A candidate group could hold only `derived` forms — validating clean, then failing at compile. And `derivedFrom: "irp"` named a type, not a form | **D21** — every form gets a stable `id`, `derivedFrom` resolves to one within the same group (depth 1, so no cycles), and every group must hold ≥ 1 non-derived form |
| Variant expansion could keep a `verifiedBy` that is false at the new address, or discard the citation for inherited values | **D22** — an expanded form carries two claims needing two citations. Explicit field partition (replace / strip / carry) plus an `expandedFrom` record pointing at the parent form id |
| `variants.override.protocol` had nowhere to land | **D23** — one protocol per remote file in v1; `override` accepts only `device`, `subdevice`, `function`. The two-protocol remote is named as a known limit with its deferred fix |
| `defaultGapUs` and `protocol.tolerance` were used but never defined | **D24** — the full `protocol` block, with two conditional validator rules and a schema-**required** `tolerance.reason` |
| D8 quantized to cycles, then applied a µs tolerance to something unstated | **D8** rewritten as a five-step algorithm. Everything compares in integer cycles; µs tolerances convert once via `ceil` |
| Phase 3 enabled `rl build --check` before the index and site existed | **D19** — each generator owns a subtree and `--check` diffs the union of the registered ones, so the gate turns on in Phase 3 and widens by itself in 5 and 6. No staging logic |
| SPEC.md would describe flat forms for all of Phase 2 | **§9** rewritten: SPEC is normative for the format, DESIGN for the build, and each edit is assigned to the phase that makes it true. The R4/R13/R6 edits land *before* the Phase 2 code |
| Pronto decoding undefined; cycles → µs is fractional | **D25** — accepted headers (`0000` only), word-count rule, `round_half_up` inverse, and the explicit note that it's lossy — so round-trip and D8 compare in cycles, and **D9 diffs the canonical string instead** |
| Hand-authored candidate groups had no label source | **D26** — `variants` is the single declaration site; an entry without `override` is metadata-only, `label` defaults to the candidate id, and an undeclared tag is a validation error |
| D20 pinned JSON but D19 also diffs `site/` | **D20** widened to all generated text: explicit sort order everywhere, `html.escape` on interpolation, `site/index.json` byte-identical to `build/index.json`, `.gitattributes` covering html/css/js |

### v0.2 — first review

The v0.1 review found seven implementation-blocking issues. All are resolved
above; five were rated must-fix before Phase 0 and none of them survive.

| Issue | Resolution |
|---|---|
| Untested alternatives would fail mandatory cross-checking — SPEC §1's own BX510 data was unrepresentable | **D16** candidate groups (cross-check partitions, never compares across), **D17** remote-level `variants` so a whole-remote address change is authored once. D7 and D8 rescoped to a group. The most substantive change, and it propagates back into SPEC §5 R4 (§9) |
| `build/pronto/` could drift or orphan with no check | **D19** — `build/` and `site/` are generator-owned; `rl build --check` diffs the whole tree, file set included, so orphans fail for free. D11 CI updated |
| Registry declared eight protocols, plan delivered three | **D18** — registry narrowed to `NEC1`, `Sony20`, `Samsung32`, with a three-part gate for adding a fourth and the other five moved to an explicit backlog |
| `raw` tolerance asymmetric; "truncated" had no field or detection rule | **D8** uses `min(a, b)` so order can't change a verdict; **D4a** makes truncation a declared property with a defined gap-substitution rule |
| `carrier_hz = 0` representable but undefined | **D1a** — rejected in v1, with the Pronto `0100` path noted for when a cited vector exists |
| `_unresolved.json` broke the corpus glob | **D12** — hoisted to repo root with its own schema, so `remotes/**/*.json` stays uniformly one-file-one-remote |
| Generated artifact contracts underspecified | **D20** — canonical serialization, `.gitattributes`, the `build/pronto` shape, and the no-non-reproducible-values rule that D19 depends on |

One consequence worth noting: narrowing the registry (D18) removed RC5, which
retired the `toggle` output field v0.1 promised — so **D3b** is now a deferred
note rather than a live contract.

---

## 12. Implementation status

Phases 0-6 are implemented: 4122 tests, `jsonschema` the only runtime
dependency. Phase 2 landed its nine SPEC edits *before* its code, per §9 --
the spec change is what authorises the implementation. (That count is asserted by the suite itself -- see
`test_documented_test_count_is_current` -- so it cannot drift the way the
three stale "148" figures did. It was 890 before the IR Blaster work, which
added 1,367; it moves again whenever a test is added, and the figure here
has to be re-fixed with it.) Post-v1 work has landed outside the phases: RC5
(§16), the LIRC and SmartIR imports (§14, §15), and the IR Blaster importer
with the 24 protocols it needed (§17, §18), and the canonical key vocabulary
(§22). The registry held 28 protocols then, and holds 40 since §55 (30 after §37, 31 after §41, 33 after §43, 37 after §45, 38 after §47, 39 after §52).
`rl encode --protocol NEC1 --device 0x88 --subdevice 0x77
--function 0x18 --carrier 38000` emits the Topping RC-15A Power key. Until
the RC-15A was corrected this read `0x11 / 0xEE`: the capture's MSB-first
digits, transcribed without reversing, which the NEC complement check cannot
catch (SPEC §1).

**Everything D6 predicted, confirmed by running it.** The frequency words
(`38000 → 006D`, `40000 → 0068`, `38400 → 006C`), the period
(26.295814 µs), the pair counts (`0022`/`0002`), the lead-in (`0157 00AC`),
the bit words (`0015 0015` / `0015 0040`), and both extents summing to
exactly 108 000 µs. D6 rule 7 is now a test: IRP-exact `16 × 564 = 9024 µs`
gives `0157 00AC`, while a rounded nominal "9 ms" gives `0156 00AB` — the
two strings differ, as claimed.

### D18 gate 2: met after v1, and restated

At v1 this section was titled "the one thing not delivered". NEC1 had no
independently cited golden Pronto string, and D10 makes that the only test
layer that catches a wrong constant. Two things closed it:

1. **Gate 2a, structural** (PRs #7, #8). The NEC family's constants match
   IRremoteESP8266's published tick table. Sony20's match a hardware
   capture, which is weaker because a capture carries instrument bias.
2. **Gate 2b, a golden vector per protocol** (post-v1; the table below grew from
   four rows to 28 with §18). The strongest is
   IrpTransmogrifier's own test assertion for NEC1 `D=12,F=34`. Our timings
   reproduce it exactly, but our bytes differ in 4 words. The cause is
   quantization, not a constant: IrpTransmogrifier rounds against the
   nominal carrier and D6 rule 4 against the word's period, and MakeHex does
   a third thing (D6, after rule 10). So gate 2b now checks timings under
   the tool's own rule and pins the byte differences (D10).

| Protocol | Gate 2b vector | Provenance | Our bytes differ at |
|---|---|---|---|
| NEC1 | IrpTransmogrifier test assertions, two parameter sets (D=12 F=34 and D=12 S=34 F=56, both at 38.4k) | published | words 4, 71, 72, 75 (lead-in mark and both gaps) |
| Sony20 | IrpTransmogrifier `Decoder.java` string (D=12 S=34 F=56), plus a 128-function MakeHex sweep (26.226, F=0..127) matching our bytes on 127 | published, and reproducible | word 45 (lead-out); the sweep, only F=127 |
| NECx2 | IrpTransmogrifier 1.2.14 `render`, D=7 S=7 F=2 | reproducible only. **No published NECx2 vector was found**, searched twice | word 71 |
| RC5 | IrpTransmogrifier `ShortProntoNGTest` (D=1, F=1, both toggle states) and `IrpTransmogrifierNGTest.testDecodeRc5` (D=7, F=5) | published | words 27 and 25 (lead-out). Timings exact under the tool's rule |
| NEC2 | `DecoderNGTest.testDecodePioneer` (D=90 S=165 F=38 at 40 kHz; IrpTransmogrifier defines Pioneer as NEC2 at 40 kHz, and **the NEC2 half of the assertion was not re-run**), plus a 1.2.14 render at 38.4k | published, weakly; the render reproducible | 67 of 72 words (564 µs is 22.5 cycles at 40 kHz); the render, words 4 and 71 |
| NECx1 | 1.2.14 `render`, D=12 and D=13 (both polarities of the repeat bit) | reproducible only | words 71, 77 |
| Sony12 | Girr `commandset_sony.girr` D=1 F=21 (L42-47 @`5ca171e`), plus a 1.2.14 render, D=23 F=70 | published (Girr's strings are evidently IrpTransmogrifier's output), and reproducible | word 29 (lead-out) |
| Sony15 | 1.2.14 `render`, D=164 F=61. The one published Sony15 string has no `^45m` lead-out and cannot serve (D62) | reproducible only | word 35 |
| RC6 | `ProtocolNGTest` L230-237 (D=12 F=34 T=0) and `ShortProntoNGTest` L20 (D=1 F=3) | published | words 41, 43 (lead-out) |
| RCA-38 | 1.2.14 `render`, D=15 F=144 | reproducible only | words 4, 5 (lead-in) |
| Thomson7 | 1.2.14 `render`, D=12 F=74 T=0 | reproducible only | 19 of 30 words (500 µs is 16.5 cycles at the nominal carrier) |
| Pioneer-2Part | 1.2.14 `render`, D0=170 F0=91 D=175 F=36 | reproducible only | 201 of 208 words (564 µs is 22.56 cycles at 40 kHz) |
| JVC | 1.2.14 `render`, D=5 F=19 | reproducible only | words 4, 39, 73 |
| Sharp | 1.2.14 `render`, D=1 F=22 | reproducible only | words 35, 67, 99 (the three gaps) |
| Denon | 1.2.14 `render`, D=8 F=175. IrpTransmogrifier's own Denon string is the superseded IRP and is checked for marks and spaces only (D64) | reproducible only | words 35, 67, 99 |
| Samsung36 | 1.2.14 `render`, D=32 S=0 E=7 F=24 and D=18 S=52 E=5 F=86 (`function` 1816 and 1366) | reproducible only | words 39, 81 |
| Proton | 1.2.14 `render`, D=20 F=1 and D=18 F=53 | reproducible only | words 4, 41 |
| F12_relaxed | 1.2.14 `render`, D=0 S=1 F=16 and D=3 S=1 F=33 | reproducible only | word 27 |
| RECS80 | 1.2.14 `render`, D=6 F=56 T=1 and D=2 F=1 T=0. A published decode assertion gives the same fields and serves gate 2a | reproducible only | word 27 |
| RECS80-0068 | The same two renders. **No gate-2a evidence** (D65) | reproducible only | 12 of 28 words |
| Aiwa | 1.2.14 `render`, D=8 S=0 F=21 | reproducible only | words 5, 91, 93, 95 |
| Blaupunkt | 1.2.14 `render`, D=2 F=21 | reproducible only | 40 of 58 words (512 µs is 15.51 cycles at 30.3 kHz) |
| Panasonic | 1.2.14 `render`, D=176 S=0 F=54 | reproducible only | word 103 (lead-out) |
| JVC-48 | 1.2.14 `render`, D=34 S=33 F=12. **No gate-2a evidence** (D66) | reproducible only | word 103 |
| Fujitsu | 1.2.14 `render`, D=132 S=132 F=0 | reproducible only | word 103 |
| Teac-K | 1.2.14 `render`, D=0 S=4 F=19 | reproducible only | words 103, 107 |
| Denon-K | 1.2.14 `render`, D=4 S=1 F=28 | reproducible only | word 103 |
| SharpDVD | 1.2.14 `render`, D=8 S=48 F=1. **No gate-2a evidence** (D66) | reproducible only | none: our bytes are identical |

**Six of the 28 are published and 22 are not.** "1.2.14 `render`" is IrpTransmogrifier's
1.2.14 release run with the command recorded in `pronto-vectors.json`
(`render -n D=…,F=… -p <protocol>`); a *reproducible* vector is one generated here by a
pinned release, and a *published* one was found in print and pinned to a commit (D10).
All 28 are checked the same way: our microsecond signal, quantized by the generating
tool's own rule, must reproduce the vector word for word, and our own bytes may differ
only at the words listed (D10). `test_registry` warns, on every run, about the 22 whose gate 2b rests on
a tool-generated, unpublished vector, and names them. **D68 says what that does and does not
establish**, and that three of them (RECS80-0068, JVC-48, SharpDVD) have no gate-2a evidence
behind the IRP either.

Gate 2a for the 24 added protocols is set out per family in §18: IrpTransmogrifier's teaser
captures and their published decodes, IRremoteESP8266's constant tables, Girr's reference
sets, and 25 measured Sony captures that bound `^45m` to 150 µs. A capture carries
instrument bias, so it verifies layout and ratios and not absolute durations.

`tests/vectors/CITATIONS.md` and `pronto-vectors.json` carry every source,
pinned to a commit. The self-derived snapshot is still labelled as proving
stability, not correctness.

### Two findings from the citations

1. **The Sony rounding question: settled as `0068`.** Remote Central gives
   Sony's frequency word as `N = 103` (`0x0067`) at 40 kHz. The exact
   quotient is 103.6287, so `0067` implies truncation, where D6 rule 5 gives
   `0068`. Both generators consulted agree with D6: IrpTransmogrifier
   (`Pronto.java` L88) and MakeHex (`IRP.cpp` L447) both round half up.
   Every 40 kHz vector starts `0000 0068`. `0067` comes from capture-side
   dumpers that truncate, such as Arduino-IRremote's integer division. It
   is not a generation rule.
2. **NEC1's carrier: both values are cited, and the registry follows
   IrpTransmogrifier.** DecodeIR says 38.0 kHz. IrpTransmogrifier's
   `IrpProtocols.xml` gives the identical IRP at 38.4 kHz, and D18's table
   and `nominal_carrier_hz` follow it. This note once said D18 "should be
   corrected" to 38.0k, which would have been one citation overruling
   another. It changes no output either way (D3).

---

## 13. A correction to SPEC §1

SPEC §1's opening table is the example the whole format was designed
around. Phase-6 gap work turned up evidence against one of its claims, and
recording that matters more than the tidiness of leaving it alone.

**The RMT-B118P's subdevice is 226, not 218.** Two independent sources
agree: a 2015 hardware capture of the remote (`jose1711/lirc_remotes`,
`bits 12` + `post_data 0x47` decoding as Sony20 `F:7,D:5,S:8` → device 26,
subdevice 226) and IRDB's Sony Blu-ray player entry (`26,226.csv`). Against
that, IRDB's `26,218.csv` is a **PlayStation button set** — SELECT, L3,
TRIANGLE, CROSS — and subdevices 234 and 242 do not appear under Sony at
all, so §1's "alternate subdevice 234 / 242" fallbacks have no corroboration
either.

§1 cites hifi-remote.com's official Sony BD command table for the 218
figure, and that table could not be retrieved. So this was a contradiction
between sources rather than a settled error. What was *not* in doubt was the protocol and the function codes:
27 of 38 functions cross-check between the capture and IRDB with zero
mismatches, which is the methodology §1 itself describes.

**The table, retrieved (2026-10-07).** It is at
`http://www.hifi-remote.com/sony/Sony_bluray.htm`, under "Rhm5757's Sony Code
Page" (`/sony/`, a page per device type, "Last Updated 1/27/2026" on its
index). The lookup that §1 records called it "Sony BD". `Sony_bd.htm` is a
404, as were the wiki pages an earlier session tried. The page is
titled "Sony Blu-ray (26.226; 26.234; 26.242; 26.151; 26.135; 26.164)" and
says the first three are "the usual 3 Blu-ray modes". Three findings:

1. **All 38 of RMT-B118P's function codes match the page's command table**,
   including the 11 that only the capture recorded (Forward 28, Pop Up/Menu 41,
   Top Menu 44, the D-pad 57 to 61, Display 65, SEN 76, Favorites 94). Those
   11 are now Verified, with the page as the second source. The code page's
   index says most of its data comes from Pronto and One For All remotes, so
   it is independent of the capture and of IRDB, but it is a hobbyist
   compilation, not a Sony publication. Each key's citation says so, and
   the 27 keys that were already Verified now carry it as a third source.
2. **218 is not a Blu-ray mode.** The page lists no 218 among the Blu-ray
   codes, and `Sony_ps2.htm` is titled "Sony PS2 (26.218)", so the page agrees
   with IRDB's `26,218.csv`. §1's 218 claim has no support left. The "official
   table" it cited names 226.
3. **234 and 242 are real, as the other two modes of the same table.**
   They are not recorded as variants (D17): the catalog bundle carries only a
   key's `primary` group, and a test asserts the corpus has no other (§23), so
   a variant would be silently dropped there. Nothing says this remote can be
   set to send them either. The page's other three Blu-ray codes (26.151 for the UHP-H1 and
   its RMT-VB210, 26.135 for the BDP-SX portables and their RMT-B113, 26.164
   for the HES-V1000 and its RMT-HS001A) carry no list of which keys those
   remotes have, so no remote is authored for them.

**The BX510 stays in `unresolved.json`.** Search results for retailer
listings (Full Compass, manuals.plus) and for Sony Canada's BX510 support page
give the BX510's remote as RMT-B119A. Those pages refused automated fetches,
so that rests on the search summaries. The RMT-B118P's service manual (Sony,
August 2011) covers the BDP-BX18, S185 and S186, not the BX510. So the file
the BX510 would need is an RMT-B119A with its own key list, and no code
source names that remote.

The irony is worth stating plainly. §1's BX510 row is what motivated
candidate groups (D16) — competing subdevices that must coexist without
being cross-checked against each other. That machinery is right and stays;
it is the specific subdevice values that the evidence disputes.

SPEC v0.8 carries this into §1 itself (§9's post-6 row). §1's table now
states the lookups' claims as claims, and a paragraph after it records what
the ledger actually holds, so the spec no longer describes a file that
differs from the one on disk.

**Topping, searched the same day (2026-10-07).** Topping publishes no IR code
table: its site has no download or FAQ for one, and the one remote it lists
(RC22, "Others > Remote control Series") is images. What exists:

- **A manufacturer list, relayed.** Thirteen lines of `RC_1X_IR_*` names with
  `RC_20_USER_CODE 0X8877`, posted as received from Topping for the DX7s
  (irplus-remote/irplus-codes.github.io issue #379, 2018-06-28) and for the
  D90SE (#540, 2021-07-03, "from Topping Services"), and on ASR. The thirteen
  lines are identical in both issues and on ASR, so it is one source, not three. It names no
  protocol and no bit order, so it is read as sent-order bytes, `88 77`.
  Twelve of its entries are RC-15A functions and all twelve agree, so each of
  those keys cites it. It has no entry for `0x50` (the capture's "M", the
  ledger's `KEY_GAIN`), which stays on the capture and the Flipper files. The
  RC-15A was already Verified and stays so. It names the 0x55 key
  `HEADPHONE_LINEOUT` where the ledger has `KEY_OK`: the code agrees, and a
  key name here is one device's reading of a shared code.
- **The RC-16A, bundled with the MX5.** Topping's own MX5 quick guide
  (`dl.topping.audio/usermanual/mx5.pdf`, page 2) draws the remote with
  "RC-16A" printed on it: Power, Mute, +, −, <, >, a centre key, A, B, C1,
  C2, GAIN and a brightness key. ASR post #26 (Kobe beef, 2022-11-29) says
  the RC-16A "bundled with MX5 is not compatible with RC-15A" and lists
  twelve codes at `5A A5`. Those are halfSpinDoctor's RC-15A list with the
  prefix changed, so the file is **Plausible**, and its citations say a copy
  cannot be ruled out. The post's low bytes are all functions the RC-15A holds
  Verified, which is what fixes its bit order (the palindromic `5A` and `A5`
  could not). The centre key (the guide's "Output channel switching") has no
  code, so it is a gap in the layout and not a key. A research pass
  reported a later ASR post (2025-08-02) with the same codes as Pronto files
  on Google Drive. Its permalink resolved to the same page here, so that post
  was not read and is not cited.
- **Nothing for the others.** The RC21 (retailers: DX7s, D70, DX3 Pro, D50s),
  the RC22 (retailers: a long list; same face as the RC-15A) and the RC23
  (D900, A900, PRE900, DX9 Discrete; the DX9 Discrete manual lists it) have no
  code anywhere, so no file is authored. Flipper-IRDB's two Topping files are
  not importable under R19.1 (its CC0 grant covers only commits from
  2319685, 2025-08-07, on, and only the Pro+ file is later), and its `DX7.ir` (2022) is autogenerated from the IR Plus app's database,
  whose licence is unknown, with its bytes in a different order from every
  capture, so it is not cited.

**What came from the three "hifi" sites.** hifi-remote.com gave the Sony
table above and nothing on Topping (its forum refused automated fetches; its
wiki has no Topping page). hifiengine.com answers every automated fetch with
a Cloudflare challenge, so nothing from it was read. hifishark.com lists
equipment for sale and holds no remote data.

---

## 14. Importing LIRC

SPEC R19 (v0.9) permits one import: LIRC's remotes database. The owner
chose it over the alternatives because it is the only large source that fits
the format: it is keyed by *remote* model (R1), 85% of it is irrecord
hardware capture, and each file credits a contributor. The other large
sources are keyed by device or by address, and their licences are
conditional or absent (SPEC §4). The choices below turn R19's five
conditions into mechanism.

**D34 — The licence boundary is a directory.** Serves R19.1. Everything
imported lives under `remotes/lirc/`, beside the licence it is republished
under:

- `COPYING` holds the GPL-2.0 text, taken from the lirc 0.10.2 tarball.
- `README.md` states the origin (`git.code.sf.net/p/lirc-remotes/code` @
  `291b40f`) and why GPL-2.0-or-later. The upstream repository states no
  licence. Debian's copyright file for `lirc-compat-remotes` 0.9.0-2
  (sources.debian.org/data/main/l/lirc-compat-remotes/0.9.0-2/debian/copyright)
  records `Files: * License: GPL-2.0+`. Debian packages only the pre-0.9.0
  subset. The later files come from the same project, and no statement to
  the contrary exists. That is a reading, not a grant, and the README says
  so.

Every imported form credits its file's contributor by name. Email addresses
are not copied, because the pinned upstream file keeps the full notice. The
site marks an imported remote as imported, with the licence.

**D35 — A citation says where, and how.** Serves R19.2, R18. Each form's
`source` has one compact, fixed shape:

```
lirc-remotes@291b40f remotes/sony/RM-U305.lircd.conf:57 [block RM-U305]
(contributed by <name>): <how>
```

`<how>` is one of three phrases, because the three ways a form is produced
establish different things:

- `raw_codes capture`: the durations are the capture's own.
- `decoded to <protocol> from a parametric block`: the block's parameters,
  decoded into an `irp` form (D36).
- `expanded to raw by lircd 0.10.2's transmit rules`: what lircd would
  send for this button, and not a capture.

**D36 — lircd is the reference for what a block means.** Serves R19.2,
R19.5. A parametric block is a *description* of a signal, and lircd's
transmit code is the only authority on what it describes: header, pre/post
data, toggle bits, `CONST_LENGTH` arithmetic, repeat frames, gap merging.
So the importer uses a pure-Python port of lircd 0.10.2's `config_file.c`
and `transmit.c` (`src/remote_ledger/lirc/`), and the port is gated like a
protocol encoder (D10):

- **Unit vectors.** Synthetic confs covering every feature, with outputs
  generated by lircd's own `irsimsend` and committed with their provenance.
- **A whole-corpus comparison.** `tools/lirc_oracle_compare.py` runs the
  port and `irsimsend` over every upstream file. Over lirc-remotes @
  `291b40f` it compared 124,498 buttons with 0 mismatches.

The importer calls `transmit_each`, not `transmit` once per button. Loading
a remote replays lircd's `calculate_signal_lengths` over every button, so a
fresh session per button was quadratic in the remote's size. `transmit_each`
replays it once and restores that state before each button. Every button
therefore still gets exactly what a fresh `transmit` would give it, and a
test holds the two equal.

Each block then becomes one of three things:

1. **An `irp` form**, when its shape is NEC1, NECx2 or Sony20. Accepted
   only if our encoder's rendering of the decoded parameters matches lircd's
   own expansion of the block under D8's raw tolerance, for both first press
   and repeat. Otherwise it falls back to 2.
2. **A `raw` form** holding lircd's expansion. `intro` is the first send
   and `repeat` is the next one, omitted when the two are the same. The
   trailing gap comes from the block's own `gap` by lircd's rule, so it is
   declared, not inferred, and needs no `truncated` claim. A toggle bit
   takes the state lircd sends on its first press, and the citation says
   so.
3. **A line in the import report**, never a silent drop. This covers: no
   timings (scancode drivers); `GRUNDIG`, `BO` and `SERIAL`, which lircd
   itself refuses to send; carrier 0 (D1a); buttons with several codes;
   anything lircd rejects as unsendable; and `min_repeat` above the
   schema's 10.

**D37 — Names are the upstream's, made legal.** Serves R1, R10, D29.

- **Manufacturer** is the upstream directory, as written (`sony`).
- **Model** is the file's stem. In a file with several `begin remote`
  blocks, each block becomes its own remote, `<stem> [<block name>]`. That
  is lircd's own unit, and it keeps R3's one protocol per file.
- **Path** is `remotes/lirc/<manufacturer>/<model-slug>.json`.
- **`controls`** comes from the header's "devices being controlled" line,
  split on commas and semicolons, with placeholders ("unknown", "?", "-")
  dropped.
- **Key names** that are not CSS identifiers (about 13%) are mapped
  deterministically: a *trailing* `+` or `-` becomes `_PLUS` or `_MINUS`
  (`VOL+`, `CH-`), anything else outside `[A-Za-z0-9_]` becomes `_`, a
  leading digit gets `KEY_`, and collisions get `_2`, `_3` in file order.
  The form's citation records the original name. *(Refined while
  implementing: the first draft mapped every `-` to `MINUS`, which read
  `Vol-Up` as `VolMINUSUp`. A `-` in the middle of a name is a separator.)*
- **A duplicated button name** in one block imports its first occurrence,
  which is the one lircd's `get_code_by_name` returns, and reports the
  others.

**D38 — The protocol block, and the tier.** Serves R3, R19.3.

- `name` is the decoded protocol, or omitted for a raw-only file (D24).
- `carrierHz` is the block's `frequency`, or lircd's default 38 kHz when
  it has none (3,039 of 3,394 upstream blocks). The citation says which.
- `minSends` is `min_repeat + 1`.
- Every form is `plausible`, and no `verifiedBy` is written.

**D39 — Authored data wins; the import is a regenerable cache.** Serves
R19.4, R19.5. `rl import lirc <checkout> --commit <sha>` rewrites
`remotes/lirc/` wholesale. The same checkout and commit reproduce it byte
for byte, including `remotes/lirc/IMPORT.md`, the report of everything D36
could not represent. An upstream remote colliding with any file outside
`remotes/lirc/` is skipped and reported. The collision test is
case-insensitive, on manufacturer and model or alias. This is D33's
lifecycle, applied to a whole tree: generated content is regenerated, and
curating a remote means moving it out, after which it is authored.

**D40 — The index and site are sharded per remote.** Serves R14, R16,
R17, OD4. At ~115k keys, the one-page site of D15 would be a single HTML
file over GitHub's 100 MB limit, and `build/index.json` a 40 MB file
rewritten by every change. So nothing committed grows with the whole
corpus:

- **`build/index.json`** keeps one summary per remote (manufacturer,
  model, aliases, controls, protocol, rolled-up tier, key count, file) and
  the unresolved list. It loses its per-key map, which already exists in
  each remote's `build/pronto/…` artifact.
- **`rl lookup`** matches against the summary, then reads matching
  remotes' artifacts for their keys.
- **The site** embeds only the summary in `index.html`, and writes one
  `site/r/<path>.js` per remote. Search stays client-side, and opening a
  remote loads its script. A `<script src>` works from `file://` where
  `fetch` does not, which keeps D15's promise.
- **D20 amended:** an array of integers (a `raw` sequence) serializes on
  one line. No authored raw form exists yet, so no committed file changes.

`rl build --check` still regenerates and diffs the whole tree (D19). Only
the files' shapes change.

*(Revised by §17: with the IR Blaster database imported, `build/index.json`
is 15 MB, 12× larger, and `site/index.html` embeds it, so "nothing committed
grows with the whole corpus" holds for the per-remote files and no longer for
the index. The decision about it is open.)*

**The result, first import** (lirc-remotes @ `291b40f`, recorded in
`remotes/lirc/IMPORT.md`):

- **Imported:** 3,139 remotes from 2,655 upstream files, 112,846 keys.
  Of those, 108,565 are `raw`, 3,348 NEC1, 766 Sony20 and 167 NECx2 `irp`.
  (Since §16 curated the Meridian MSR out of the import: 3,138 remotes from
  2,654 upstream files, 112,789 keys, 108,508 `raw`.)
- **Reported, not imported:** 20 files with no usable remote, 197
  scancode-only blocks, 36 blocks whose `min_repeat` exceeds the schema's
  `minSends` limit of 10, 237 multi-code buttons, 642 duplicate names, 94
  sends lircd itself refuses, and 40 keys that would not compile.
- **Why so few `irp` forms:** only ~11% of NEC-shaped blocks pass the
  cross-check. The rest fail for structural reasons:
  - full-frame repeats (NEC2, which is not in the registry);
  - a gap the conf sets itself;
  - a CONST_LENGTH lead-out whose measured timings drift past D8's
    6-cycle allowance.
  For those, `raw` is what lircd sends.
- **Size:** the generated tree is 222 MB in about 9.4k files, and
  compresses to ~12 MB. The largest file is 2.3 MB, and `site/index.html`
  is 1 MB.
- **Speed:** `rl build` takes 3.5 minutes. Pytest parametrizes only over
  authored files, and one scan checks R19 across the imported ones.

---

## 15. Importing SmartIR

R19 (v0.9) admits any source meeting its five conditions, not only LIRC.
SmartIR (`smartHomeHub/SmartIR`) is the second: its own prior-art entry
(SPEC §2) already called it "closest existing prior art to Remote Ledger's
shape", and unlike IRDB, Flipper-IRDB's pre-CC0 files, Global Caché and
Remote Central, its licence -- MIT, confirmed live against GitHub's
license API, not assumed -- actually permits republishing (R19.1).

**D41 — Scope is two categories, one controller.** Serves R19.1, R19.5.
SmartIR's `codes/` has four categories. `climate` (358 files) and `light`
(5) each describe a *state matrix* -- mode × fan speed × temperature, or
brightness × colour temperature, sometimes hundreds of states in one file
-- not a physical remote's buttons; mapping that onto this schema's `keys`
would be a real semantic stretch, not a mechanical one, so both are out of
scope for v1 and reported as such, per file, in `IMPORT.md`, the same
non-silent standard D36.3 set for LIRC. `media_player` (56) and `fan` (17)
are genuinely button-shaped and are what this import covers.

Within those two, only `supportedController: "Broadlink"` profiles are
decoded (`Base64` or `Pronto` encoding); every other controller (`Xiaomi`,
`ESPHome`, `LOOKin`, `MQTT`) is a different, unrelated wire format and is
skipped and reported, not guessed at.

**D42 — A Broadlink packet is arithmetic, not protocol emulation.** Serves
R19.2. Unlike an lircd.conf block, a Broadlink capture (`Base64`) carries
no symbolic bit semantics to decode -- it is already an undifferentiated
mark/space pulse train, base64-encoded: a one-byte type (`0x26` for IR), a
repeat count, a little-endian payload length, then the durations
themselves, each one byte (~32.84 us/tick) unless that byte is `0x00`, in
which case the next two bytes (big-endian) hold the value. So there is no
analog of `lirc/transmit.py`'s lircd port here -- decoding is
`src/remote_ledger/smartir/broadlink.py`, about seventy lines, and the
oracle is different in kind too: not a compiled reference binary, but the
community `broadlink` PyPI package's own `data_to_pulses` (MIT-licensed),
the closest thing this format has to a reference decoder.
`tools/smartir_oracle_compare.py` runs both over a live checkout; last run
(SmartIR @ `e4df295`), 966 `Broadlink`/`Base64` commands compared across
`media_player` and `fan`, 0 mismatches. A `Pronto`-encoded command is
simply passed through verbatim as a `pronto` form -- SmartIR already
stores it in this project's own wire format.

No IRP (NEC1/Sony20/...) detection is attempted from the decoded timings.
D36.1 works because lircd's config already carries `pre_data`/`code`/
`post_data` bit widths to decode; a Broadlink capture has none of that, so
recovering protocol parameters from raw timings is a real pattern-
recognition problem, not a mechanical decode, and stays out of scope.
Every SmartIR-imported key gets a `raw` form, or `pronto` for the one
verbatim-Pronto file -- never `irp`.

A Broadlink `Base64` capture is protocol-blind: it records no carrier and
no repeat count, so both are defaulted -- 38 kHz, 1 send -- and every
citation says so, the same pattern D38 uses for LIRC's own defaulted
carrier. A `Pronto` command is different: its own hex already declares a
carrier (word 1), so that word is read, not defaulted, via
`pronto.decode`'s own word-based comparison (D8) -- guessing 38 kHz for a
36 or 40 kHz Pronto profile would reject every one of its buttons outright.
`minSends` still defaults to 1 either way, since neither encoding records
a repeat count.

**What the 38 kHz default costs: no keys dropped, some played
off-carrier.** Carrier handling dropped nothing in the first import: the
report lists zero "does not compile" skips, and all 10 Pronto keys declare
38 kHz (`006D`) anyway. The default affects how some keys play, not
coverage. Classifying the 904 `raw` keys by lead-in and bit timing gives:

| Timing family | Keys | Usual carrier | Mainly |
|---|---|---|---|
| NEC-like, 9 ms lead-in | 445 | 38 kHz | Yamaha, LG, Onkyo |
| Samsung-like, 4.5 ms lead-in | 142 | 38 kHz | Samsung, Thomson, TCL |
| RC6 | 82 | 36 kHz | Philips, Sky |
| RC5 | 29 | 36 kHz | Philips |
| Sony SIRC | 18 | 40 kHz | Sony |
| Kaseikyo | 7 | 37 kHz | Mitsubishi |
| unrecognised | 181 | — | Noblex, Sharp, Pace |

About 129 keys (14%) are therefore compiled at 38 kHz for a protocol
that usually runs at 36 or 40 kHz. That is a 5% error. It is within the
passband of a typical demodulating receiver, but it costs range, so a
user may see such a key work at close range and fail across a room.
Dropping those keys would lose them outright, which is worse, so they
stay (SPEC R19: a missing parameter is defaulted, not a reason to drop).
Any SmartIR user has already been sending them through a Broadlink
transmitter's own fixed carrier, so 38 kHz is at least the condition in
which upstream found them to work. This is an unverified, uncited claim
and is recorded here only as a reason the risk is low.

The improvement, not yet built, is to infer the carrier from a recognised
timing family. That would record the value as a `claims` entry citing the
protocol's published carrier, keep the form Plausible, and leave the 181
unrecognised keys on the default. It is a deliberate step past D41's
"no protocol detection", so it needs its own decision before any code.

An odd-length Broadlink decode has no recorded trailing gap. Marking it
`truncated: true` would need a `defaultGapUs` claim to substitute one
(D4a), and this import has no source for that value -- inventing one would
be exactly the guess R19 exists to forbid -- so such a command is skipped
and reported instead, on the same footing as one that fails to decode at
all. It fired for none of the real import's 914 keys, only exercised by a
synthetic test vector.

**D43 — Names: SmartIR has no remote, only a device and a catalog id.**
Serves R1, R2, R10. A LIRC file names an actual remote (D37); a SmartIR
profile names none at all -- only a manufacturer, the device model(s) it
controls (`supportedModels`), and its own catalog id
(`codes/media_player/1000.json`). R2 requires `model` to identify the
remote, not the device, so inventing a plausible-looking remote model
would be a guess this project's whole ethos argues against. Instead:

- **`model`** is a synthetic designation built from the category and
  catalog id: `"SmartIR media_player 1000"`.
- **`controls`** holds the real `supportedModels` list, where R2 already
  says controlled products belong.
- **`manufacturer`** and the directory it's written under keep SmartIR's
  own casing (`Philips`, not `philips`) -- there is no rule to lowercase
  it by, so none is invented.
- **Key names** come from the command's path in the `commands` tree
  (dotted for a nested group, e.g. `fan`'s `forward`/`reverse` × speed, or
  `media_player`'s `sources` map): non-identifier characters fold to `_`,
  the whole name upper-cases, and it's `KEY_`-prefixed. camelCase is left
  as spelled (`volumeUp` → `KEY_VOLUMEUP`), matching how the rest of the
  corpus already spells that button, rather than splitting it (which
  would give `KEY_VOLUME_UP`, a plausible-looking but invented split).
- **A command upstream lists as several redundant captures of the same
  button** (a JSON array, not a string) imports only the first; the
  citation records how many there were, the same "first wins" rule D37
  uses for a LIRC duplicate name, applied at the command level here since
  SmartIR's duplication is within one button, not across two.

**D44 — A citation says where, and how.** Serves R19.2, R18. Each form's
`source` has one compact, fixed shape:

```
smartir@e4df295 codes/media_player/1000.json#off (SmartIR profile 1000,
Philips): broadlink IR packet, base64-decoded
```

`<how>` is one of two phrases: `broadlink IR packet, base64-decoded`, or
`broadlink pronto capture, upstream-provided verbatim`. Unlike D35's LIRC
citation, there is no `(contributed by <name>)` clause -- SmartIR's
`commands` tree carries no per-code attribution the way an lircd.conf
header does, so nothing is invented there either; attribution for the
whole database lives at `remotes/smartir/README.md` and `LICENSE`, as MIT
requires.

**D45 — Authored data wins; the import is a regenerable cache.** Serves
R19.4, R19.5. `rl import smartir <checkout>` rewrites `remotes/smartir/`
wholesale, exactly as D39 describes for LIRC: the same checkout and commit
reproduce it byte for byte, an upstream profile colliding with any file
outside `remotes/smartir/` (by manufacturer and model or alias,
case-insensitive) is skipped and reported, and only `*.json` and
`IMPORT.md` are the importer's -- `README.md` and `LICENSE` are authored
and left alone. `authored_names()` and the compile-gate pair
(`probe`/`form_compiles`) are shared with the LIRC importer verbatim,
factored into `src/remote_ledger/import_common.py` once this import needed
them too; nothing about either depends on the upstream format.
`src/remote_ledger/paths.py`'s `IMPORTS` registry (D40) gained a
`remotes/smartir/` entry alongside LIRC's, so the index and site label a
SmartIR remote as imported, under MIT, rather than falling through to
"authored" for want of a registered prefix.

*Open point, not fixed here:* D43's synthetic model names mean R19.4's
collision check almost never fires for SmartIR -- an authored remote and
an imported profile would only collide by coincidence, since nothing
upstream names a real remote to collide on. The devices they agree about
live in each side's `controls`, which the check does not compare. Deciding
whether it should -- and how to resolve an authored remote and an imported
profile that both claim the same controlled device -- is left as a
follow-up, not guessed at here.

**The result, first import** (SmartIR @ `e4df295`, recorded in
`remotes/smartir/IMPORT.md`):

- **Imported:** 62 remotes from 73 upstream files (56 `media_player`, 17
  `fan`), 914 keys. Of those, 904 are `raw`, 10 `pronto`.
- **Reported, not imported:** 357 `climate` files and 5 `light` files
  (out of scope, D41); 4 files on a non-`Broadlink` controller; 1 file not
  valid JSON; 6 files where every button failed to decode or compile; 62
  individual buttons across the remaining files -- malformed base64,
  declared lengths longer than what followed, and codes carrying a type
  byte other than `0x26` (community-contributed data this project
  declines to guess the format of, D42). Zero authored-data collisions:
  nothing already curated overlaps SmartIR's synthetic model names.

---

## 16. RC-5, and curating the Meridian MSR

A user imported `lirc/meridian/MSR` from the store and tried it on a Meridian
565. At least one button worked; Off did not. This section records what was
wrong with the imported file, what was changed, and what is still not known.

### What was wrong

The LIRC conf (`remotes/meridian/MSR.lircd.conf`, contributed 2005) declares
`flags RC5`, `bits 13` and **no `plead`**. In RC-5 the first start bit, S1, is
a lone mark, and LIRC confs spell it as `plead 889` in front of 13 data bits
(S2, T, five address bits, six command bits). Of the pinned upstream's 417
RC5-flag blocks, 379 declare a `plead`. Without one, lircd sends the 13 bits
and **no S1**, and the import reproduced that faithfully: its oracle suite
shows the port matching lircd's own output. The import was right about what
the conf says. The conf does not describe an RC-5 frame.

Why that is a missing start bit and not just a different one:

- A Meridian 562/565 code set in Flipper-IRDB decodes as 14-bit RC-5,
  address 19. The ledger's `Off` is exactly that set's `OFF` frame less its
  first two durations. More generally, the conf's 13 bits with S1 put back in
  front reproduce the set's frame for 36 of the 57 keys; 8 differ only in the
  toggle bit, which is arbitrary per press; 13 keys are not in the set.
- A strict receiver rejects all 57 imported frames as invalid biphase. A
  sampling receiver reads addresses 6, 7, 24 and 25, never 19.

What this does **not** explain is why at least one button worked. Neither
receiver model above predicts it, so the 565's decoder is more forgiving
than either, or something else is going on. That is recorded, not resolved.

### What was right

The codes. All 57 are address 19. IRDB lists RC5, device 19 for Meridian's
Surround Processor and System Remote with the same functions (Off is 12 in
both). 44 keys are in both IRDB and the Flipper set, 4 are in one of them,
and 9 are in the conf alone (Slow, Band, Audio, EPG, Angle, A-B, Phase,
Chapter, Setup). The labels sometimes differ -- the Flipper set calls the
DVD code `TEXT`, IRDB lists two names each for codes 80 and 85 -- but the
codes agree.

### What changed

- **`RC5` joins the registry**, through D18's gate. Gate 1: the IRP, verbatim
  from IrpTransmogrifier's database. Gate 2b: three published vectors, D=1
  F=1 in both toggle states and D=7 F=5, each reproduced exactly under the
  tool's rounding, with our bytes differing at the lead-out word only. Gate
  3: `tests/test_rc5.py`, including an exhaustive round trip of all 8,192
  address/command/toggle frames. Gate 2a, weaker: the encoder reproduces all
  48 frames of the Flipper set from the address, command and toggle bit each
  decodes to -- 24 end on a mark and 24 on a space, 23 use the extended
  command range, and both toggle states occur.
- **The MSR left `remotes/lirc/`**, which is R19.4's own route for curating
  an import: it is now `remotes/meridian/MSR.json`, RC5 forms at address 19.
  48 keys are Verified (the conf agrees with a second independent source)
  and 9 are Plausible. The next LIRC import skips the block and reports the
  collision. The compiled and site copies of the old file were orphans and
  are deleted.
- **`controls` names the 565 and the 562.** The Flipper set is the only link
  between this remote and those units.

`tools/rc5_capture_audit.py` regenerates the per-key evidence, the comparison
with the Flipper set and the encoder check from checkouts of the three
sources. The strict-receiver and sampling-receiver readings, and the scan of
the other imports, were one-off analyses and are not part of it.

### Not changed, and not known

- **The toggle (D3b).** Everything compiled here has `T=0`. The old import
  fixed `T` per key as well (1 for most keys, 0 for the extended range), so
  this is not a regression, and not a fix either. If a receiver treats a
  repeated `T` as a held key, only the first press after it last saw the
  other state would work, and no frame fixes that: the player has to
  alternate. Nothing here says whether the 565 behaves that way.
- **No hardware test.** The frames now have the right shape and the codes are
  well corroborated. Whether a 565 obeys them is for a person to confirm,
  which is what the Confirmed tier (SPEC §5) is for.
- **Licence.** The Flipper-IRDB code set and IRDB's tables are cited by
  permalink and compared; none of their data is copied. SPEC §4 excludes
  Flipper-IRDB files from before its CC0 cutoff, and the shallow clone used
  here could not date this one.
- **`chiro/C-802` has the same shape** (RC5 flag, 13 bits in total, no
  `plead`) at 40 kHz with 900 us units, converted from Slink-e device files.
  It is not standard Philips RC-5 and there is no second source, so it is
  left alone. Every other 36 kHz RC-5-shaped frame in the import -- 9,252
  across 27 files -- decodes as a valid 14-bit frame.
- **"RC-5X" means two things.** The conf calls the 7-bit-command extension
  RC-5X; that is part of IrpTransmogrifier's plain `RC5`. Its `RC5x` is a
  different 20-bit protocol and stays unregistered.

---

## 17. Importing the IR Blaster database

SPEC R19 (v0.10) admits a third source: the code database that SwiftRemote
ships, which SwiftRemote took, with the rest of its code, from IR Blaster. It
is the weakest of the three on licence and the least remote-shaped of them on
structure, and it adds a problem neither LIRC nor SmartIR had. LIRC's blocks
and SmartIR's captures say what they are; this database stores each code as a
**hexcode and a protocol name**, and what a hexcode means is itself a claim
the ledger has to check. D50 and §18 deal with that. This section is the
importer's own decisions, D46 to D56, with the measurements the full run gave.

The source is `assets/db_src/swiftremote.sql` at SwiftRemote
`6aafd15e1c95cf494ac729339b9a4701a4ab8f0a`: four tables, `brands`,
`remotes(id)`, `models(brand, model, id)` and `keys(id, label, hexcode,
protocol)`. It holds 9,388 remote ids, 413,331 keys, 23 protocol names and
58,766 distinct `(protocol, hexcode)` codes. The importer is
`src/remote_ledger/irblaster/importer.py`, the command `rl import irblaster
<checkout>`, and the end-to-end oracle `tools/irblaster_oracle_import.py`.

**D46 — The licence boundary is a directory, and R19.1 is met by inheritance
only.** Serves R19.1. Everything imported lives under `remotes/irblaster/`,
registered in `paths.py`'s `IMPORTS` (name "IR Blaster database (as shipped in
SwiftRemote)", licence `GPL-3.0-only`, readme `remotes/irblaster/README.md`).
`LICENSE` is SwiftRemote's own, byte for byte. `README.md` says, without
softening it, what the licence rests on: SwiftRemote is GPL-3.0 and so, it
says, is this repository, so the data is republished under the licence it
arrived under. That is not a grant by the data's authors and not a reading of
one (LIRC's is a reading, D34), because nobody in the chain says where the data
came from.

Lineage as far as it is written down: SwiftRemote forked IR Blaster
(`github.com/iodn/android-ir-blaster`, GPL-3.0, KaijinLab Inc.), itself a fork
of `github.com/TalkingPanda0/osram-remote`. No project in it names the data's
source, and IR Blaster's own audit (`report-source.md`) lists `REC80`,
`RCC2026` and `RCC0082` as having no public definition. The README closes with
the same exit as LIRC's: deleting the directory removes every imported file.
This is the one import whose condition 1 is weaker than the others', and SPEC
R19.1 now says so in its own words rather than leaving it to this section.

> **TODO for the integrator.** The importer's README says this repository is
> GPL-3.0. At this commit the repository root holds no `LICENSE` and
> `pyproject.toml` declares no licence, so the sentence cannot be checked from
> the tree. Either the root licence is added, or the README's wording changes,
> before the data is committed. Nothing in the notes settles which.

**D47 — One ledger remote per (database id, ledger protocol).** Serves R3,
D23. A file holds one protocol; 581 ids use several database protocols, and one
database protocol (`REC80`) lands on six ledger protocols. So an id becomes
`remotes/irblaster/<slug(manufacturer)>/<id>-<slug(ledger protocol)>.json`
(`importer.py`'s `target_path` and `Importer.import_id`). A database protocol
that maps to a ledger protocol gets one file per ledger protocol it reaches,
and an (id, protocol) whose keys are all unrepresentable gets none. On the real
database that is 10,013 files from 9,388 ids; 573 ids are split over several
files and 27 ids write none (every key refused, all listed).

`slug` is the other importers' (`[^A-Za-z0-9._+-]` becomes `_`), applied to the
directory by `dir_slug`, which also refuses `.` and `..` and turns a leading or
trailing dot into `_`. Thirteen brands end in a dot (`C.P.`, `T.V.E.`); a
directory name ending in a dot cannot be checked out on Windows, and a brand
named `..` must never reach a path. No brand in this database is a Windows
reserved name, and none collides with another once casefolded (checked).

**D48 — Names, and why R2 cannot be honoured.** Serves R1, R2, R10. The
database has no remote model: an id is a bag of keys and the list of products
it is filed under, as with SmartIR (D43). So:

- **`manufacturer`** is the brand with the most `models` rows for the id. A tie
  goes to the casefolded alphabetical first, then to the exact string, so the
  rule is total (`pick_manufacturer`). The database's casing is kept. 3,934
  ids have several brands and 976 have a tie.
- **`model`** is `IR Blaster DB <id> (<ledger protocol>)`, always with the
  protocol, so the two files of one id never share a name (R15). R2's "`model`
  identifies the remote" is not met and cannot be: nothing in the data does.
- **`controls`** is every `models` row of the id as `<BRAND> <MODEL>`, sorted
  and de-duplicated (two rows can spell the same string: 280,956 rows give
  280,954 distinct). Ids carry up to 6,268 (id 286), and 43 files have more than
  1,000. A split id repeats its list in each file, 306,631 entries in all. The
  schema puts no bound on `controls`.
- **`aliases`** is `[]`.

[The `controls` format above describes the tree as the importer first wrote it;
see the placeholder after D56 for the decision that changes it.]

**D49 — Key names are the label folded mechanically, and every member of an
ambiguous name carries its code.** Serves R10, D29. `key_base`: ASCII
upper-case, then `??` becomes `UNLABELED`, `+` `PLUS`, `-` `MINUS`, `/` `SLASH`,
`*` `STAR`, `#` `HASH` (each as a separate word), every other run of
non-`[A-Z0-9]` becomes one `_`, trimmed, prefixed `KEY_`. Three consequences:

- A `-` inside a word is `MINUS` too (`A-B` is `KEY_A_MINUS_B`), where LIRC's
  D37 learned to treat it as a separator. The decision was made, and it is
  spelled out so nobody reads `MINUS` as a claim.
- Only ASCII letters are upper-cased. `str.upper` also changes some non-ASCII
  letters (`ß` becomes `SS`) and its tables move between Unicode versions, and
  a regenerated import must not depend on the interpreter.
- A label with nothing alphanumeric in it (`►`, `⏩`, `?`: 6,942 keys) is
  `KEY_`, valid under the schema's key-name pattern.

The labels are not unique in an id (16,367 repeats; `??` on 9,853 imported
keys), so `key_names` does what D21 does for ids and avoids position: when a
folded name carries more than one distinct `(label, hexcode)` in the file,
**every** member gets `_<HEXCODE>`, so a name does not depend on the order the
rows arrive in. A collision left over (two labels that fold alike on one code)
gets `_2`, `_3` in sorted order of `(label, DB protocol)`. A name the rule makes
can collide with another key's plain name (`KEY_OK_A_2` against `OK A 2`), and
the loop numbers again until the name is free. On the real database 360,384
keys have the plain name, 50,505 carry a code and 376 are numbered. Keys are
written in name order.

Members are the keys *written* to the file, not the rows of the id: a refused
key does not make its neighbour's name ambiguous. The cost is that a hex map
that later accepts a code can rename a key. The original label, with its
spacing, is in the citation (D51). An exact duplicate row is dropped and
counted; the real schema's primary key makes it impossible, and the code does
not rely on that.

**D50 — The form is one `irp` form per key, read the way the data means it.**
Serves R19.3. `confidence: plausible`, `id: primary.irp` (`rl fmt` writes the
auto id, so leaving it out would not survive `rl fmt --check`), ledger protocol
and `device`/`subdevice`/`function` from `FROM_DB_HEX[<DB protocol>]`, with
`subdevice` left out when the protocol has none.

**The hexcode is read as the wire reading.** The database stores every code in
wire order, and for Sony, Pioneer, JVC, Sharp, Denon, Thomson7, Proton and
RCC2026 SwiftRemote's own encoder reads it differently (D57 has the table and
the evidence). The ledger holds what the data means, per independent evidence:
published decodes of real remotes, real LIRC frames, structure. No second form
is added for the app's reading, and no non-primary candidate either. The raw
pattern would fail D8 (D64, "For the importer"), and a candidate group per
reading would put 44,789 keys' worth of a bug into the data. The disagreement
is measured and reported instead (D55).

**D51 — The citation is short and fixed.** Serves R19.2, R18. The shape, 94.9
characters on average (median 95, longest 134, because of long labels), is

```
irblaster-db@6aafd15 remote 286, 'VOL+' 20DF40BF NEC: 32 wire bits, bytes bit-reversed as NEC1
```

The prefix is the pinned SwiftRemote commit's first seven characters, then the
remote id, the label as the database spells it, the hexcode and the database
protocol. `<how>` is a fixed phrase **per database protocol**, with the ledger
protocol appended (`importer.py`'s `HOW`; REC80's six ledger protocols need the
suffix to be told apart). An earlier draft of the phrase carried the
parameters (`wire-order hex read as Sony12 D=1 F=21`). The form already does,
413,331 times, so the phrase does not, and a reader checks the claim by applying
the phrase to the hexcode. No claim about trust is made beyond `plausible`, and
none about where the code came from, because that is not known (D46).

**D52 — The protocol block.** Serves R3, D3, D24, D27.

- `name` is the ledger protocol, `carrierHz` the registry's
  `nominal_carrier_hz`, so no `carrier-off-nominal` warning appears (D32). The
  app's own carriers are within 5 % of every one (the oracle tools check it);
  D60 has the table. One consequence worth seeing: NEC1's 38,400 is 1.0 % from
  the 38,000 the app sends for a database NEC code, and the LIRC import and
  the authored Topping use 38,000, so these files differ in carrier word
  (`006C` against `006D`) from those.
- `minSends` is `hex_*.MIN_SENDS`, default 1: Sony 3, NECx2 2, Thomson7 2, Aiwa
  (RCC2026) 2. Sharp and Denon stay at 1 although the app sends three frames
  per press (their intro plus one pass of the repeat); the oracle tool plays
  their whole signal for that reason and says so.
- **Samsung36 alone** gets `unitUs: 500` and `claims.unitUs`. The IRP says 560
  µs; the app, IrpTransmogrifier's eight real captures (median unit about 496
  µs) and IRremoteESP8266's `sendSamsung36` (512 µs marks, 490/1468 µs spaces)
  agree on about 500 (D65). The claim's source cites the capture audit and the
  IRremoteESP8266 line range. At 500 µs the compiled signal equals the app's to
  the microsecond but for the lead-out, which no field changes. `rl build
  --check` passes on it.
- **Raised, not decided.** Where another protocol shows the same pattern, the
  IRP's unit disagreeing with the app and with real captures, the question was
  raised and no override written. `Blaupunkt` (IRP 512 µs, app 528,
  `Blaupunkt.ict` about 532; the app's sync gap is 9.1 % short of the IRP's and
  nearer the capture) and `Thomson7` (IRP 500/2000/4500, app 460/2000/4600,
  capture marks 0.961× and spaces 1.020× of the IRP) both agree with the app
  against the IRP by a few percent, and neither has a second source like
  IRremoteESP8266's. `RCA-38` is a different case (the IRP and the app say 460
  µs, only the capture says 500) and `Pioneer-2Part` is mixed (app marks 500 µs,
  IRP 564, IRremoteESP8266 568, the capture 548).

**D53 — The hex maps have one contract.** Serves D50. Every
`irblaster/hex_*.py` exports `FROM_DB_HEX` (DB protocol name → function(hexcode)
→ `(ledger protocol, device, subdevice, function)`: the wire reading, what the
importer writes), `FROM_DB_HEX_APP` (the same keys and signature: what
SwiftRemote does today, used only by the oracle tools and by the report in D55)
and `MIN_SENDS`. Where the app and the data agree the APP entry **is** the same
function object, so the set of protocols on which they differ is a property a
test can state (`tests/test_irblaster_hex_contract.py`): the ten Sony12,
Sony15, Sony20, Pioneer, JVC, Sharp, Denon, Thomson7, Proton and RCC2026.

Before this contract the family modules named the two readings three different
ways: `hex_japan` had them the other way round (`FROM_DB_HEX_WIRE`), `hex_misc`
kept Proton's wire reading outside the table (`proton_wire_order`), and
`hex_unknown` called its table `FROM_DB_HEX_SWIFTREMOTE`. Those names are gone.
The per-family oracle tools keep proving the encoders against what the app
transmits, now through the APP tables, and their docstrings say so
(`irblaster_oracle_japan.py --reading app`, the default, is the proof;
`--reading wire` is the importer's reading and cannot fail).

**D54 — What cannot be represented is skipped and listed, never dropped.**
Serves R19.5. A hex map that raises `ValueError` (its text has no hexcode in
it, so the report groups on it), a DB protocol with no map (none today: all 23
have one) and a form that does not compile to Pronto
(`import_common.form_compiles`, memoised per distinct signal, since 413,331 keys
are 58,766 distinct codes and the answer is a function of the protocol,
carrier, unit and parameters) are skipped key by key. An id with no
representable key writes no file and is listed as a skipped remote; so is an id
with no `models` row (no manufacturer to file it under; none today). An authored
collision (`import_common.authored_names`) skips the file and counts its keys
(none today; the synthetic model names make it unlikely, as D45 said of
SmartIR).

On the real database **2,066 keys, 964 distinct codes** are skipped, all for
reasons D67 gives: NEC 406 codes / 1,076 keys and NEC2 229 / 434 (byte 4 is not
the complement of byte 3, and the only registry-shaped protocol that could hold
it, the `-f16` form, is not registered), RCC2026 21 / 75 (not an Aiwa frame),
REC80 308 / 481 (a Kaseikyo-family layout the registry does not hold). Every
reason is a row of `IMPORT.md`, every key one row. The figure going in was about
2,500; the family counts add to 2,066 exactly.

**D55 — The report says where SwiftRemote's reading differs.** Serves R19.5,
D50. `IMPORT.md` ends with a section, per DB protocol, of imported distinct
codes, codes whose `(ledger protocol, D, S, F)` under `FROM_DB_HEX_APP` differs
from the wire reading (or which the app's reading cannot send as a frame at
all), imported keys, keys affected, and the first three codes in hexcode order
with both readings. A code the wire reading refuses is not in it: it is in the
skipped list.

**Measured: 9,140 distinct codes, 44,789 imported keys** (Sony12 892, Sony15
733, Sony20 1,213, Pioneer 1,667, JVC 1,020, Sharp 586, Denon 332, Thomson7 29,
Proton 1,458, RCC2026 1,210 codes). The figure going in was about 12,800; the
ten protocols' distinct codes add to 9,399 in all, so 12,800 cannot be reached
from this database, and 9,140 is what the tool recomputes independently. The
full list is not committed, since it would double `IMPORT.md`;
`tools/irblaster_oracle_import.py --list-differences` prints it from the dump
for whoever fixes the app.

**D56 — Authored data wins; the import is a regenerable cache.** Serves
R19.4, R19.5. As D39 and D45: `rl import irblaster <checkout> [--commit SHA]`
rewrites `remotes/irblaster/` wholesale. `*.json` and `IMPORT.md` are the
importer's, `README.md` and `LICENSE` are authored and never touched, files are
written as they are produced (peak 260 MB, not the whole tree), stale files are
removed afterwards, and every file passes through `fmt.format_document` so `rl
fmt --check` stays clean. The input is the SQL executed into an in-memory
sqlite (what `tools/build_ir_db.py` does to a file), and the pinned commit is
the checkout's `git rev-parse HEAD`.

One addition over LIRC and SmartIR: `cmd_import` refuses a checkout whose
`assets/db_src/swiftremote.sql` differs from the commit (`INPUT` on the
importer module), because an importer that reads one named file would otherwise
cite a tree the data did not come from. Output is a pure function of the dump,
the commit and the code: every ordering is sorted, the key names do not depend
on row order, and a test shuffles the dump four ways and compares the bytes.

**D56a -- A key may carry a `label`, and every imported key does.** Serves R10,
D49. The SwiftRemote app will query the ledger online instead of bundling its
database, and needs each key's text for display, search and ranking without
parsing a citation. A key's name is a mechanical fold of the label (D49) and
loses it: `KEY_VOL_PLUS` for `VOL+`, `KEY_UNLABELED_<HEX>` for `??`, and 3,880
imported keys (3,931 rows; 1,567 distinct labels) have a lower-case letter in
theirs. So the schema's `key` object gets an optional `label`, a non-empty
string, "the text the source shows for this key; display only, never an
identifier", and `rl fmt` orders a key's own properties `label`, then `forms`.
Decisions inside it:
- **It is free text and unique to nothing.** Two keys of one file may carry the
  same label (16,367 repeats in the data), and the name stays the only
  identifier. The schema asks `minLength: 1` and no more: the database has no
  empty or whitespace-only label, but it does have five with leading or trailing
  whitespace (one ends in tabs, `'1 \t\t\t'`) and one with a tab in it, and the
  label is written exactly as the database holds it, `??` included. A stricter rule would have to reject or
  rewrite what the app shows.
- **It travels with the key.** `Remote.labels` (key name to label, only for keys
  that have one); the compiled artifact gets `"label"` beside `"candidates"` in a
  key's entry (`cli.compiled_artifact`, which `generators.run_compile` calls);
  the site's per-remote script gets a `labels` map beside `keys` (not inside it,
  where a candidate named `label` would collide), and the page shows the label
  next to the key name; `rl lookup` prints it after the key name, quoted
  (`json.dumps`) so spaces and control characters show; `build/index.json` is
  untouched, because D40's index carries nothing per key. The importer writes
  the label on every key, from the database row, and keeps the citation as it was.
- **A remote without labels generates the very bytes it did before.** Every
  addition is conditional: the artifact's `label` only on a key that has one, the
  script's `labels` only for a remote that has any, and, the one place that needs
  an argument, **the page**: the JavaScript line that renders a key's name and
  one CSS rule are part of `site/index.html` only when some remote in the corpus
  has a label (`site.page_parts`, one line of `SCRIPT` replaced, asserted to be
  there exactly once). Making the page's script unconditional would have changed
  `site/index.html` for a corpus with no labels; if the integrator prefers that
  (one generator path, no gate) it is a one-line change, and `rl build` rewrites
  the page anyway when the import lands, since the corpus then has labels.
- **Tested as JavaScript.** `tests/page_driver.py` runs the page's own script
  under node against a stub document and returns what it renders (skipped when
  node is absent); the tests check the label is escaped (D29), that a key
  without one has no span, and that `norm()` and `hit()` find the pipe form of D56b.

**D56b -- `controls` entries are `<BRAND> | <MODEL>` in this tree, and the
importer proves the format parseable.** Serves R2, D48. `<BRAND> <MODEL>` cannot
be taken apart again: 584 of the database's 4,912 brands are several words
(`ACCESS HD`, `A TREND`), and the first import wrote the two spellings of
`CRISTOR | ATLAS HD 200 S` and `CRISTOR ATLAS | HD 200 S` as one string. The
owner's app needs brand and model apart. So each entry is the brand, a space, a
pipe, a space and the model, and `entry.split(" | ", 1)` is its exact inverse.
- **The importer refuses a pipe anywhere in a brand or model**, not only the
  five-character ` | ` the brief named: with no pipe in the brand the first
  ` | ` of an entry is the separator whatever the model holds, but a brand ending
  in a pipe (`A |`, then ` | `) would break `split` and `A | | B` could be either
  reading. The database has no pipe in any brand or model (checked over the 4,912
  brands and the 280,956 `models` rows), so the stronger rule costs nothing today.
  `controls_entry` raises `ValidationError`, and `iter_documents` checks every
  `models` row before the first file is written, so a later checkout that breaks
  it stops the import instead of leaving a half-written tree.
- **It is the tree's convention, not the ledger's.** Elsewhere `controls` is free
  text (`DX3 Pro`), and every other import writes plain product names. The
  importer's module docstring and `remotes/irblaster/README.md` say so. Nothing
  in the schema, the index or lookup depends on the shape.
- **Search is unchanged and finds both spellings.** `lookup.normalise` and the
  page's `norm()` drop every non-alphanumeric character (PR #20), so
  `sony kd 49x8088` normalises to the same string as `SONY | KD - 49 X 8088` and
  as the plain `SONY KD - 49 X 8088`. `tests/test_lookup.py` pins it in Python and
  `tests/test_site.py` in the page's own script. One consequence that was already
  true and stays: a query's words may fall in different `controls` entries of one
  remote (the every-word fallback), so `access hd dl20` can match a remote that
  lists `ACCESS HD | A 1` and `DE LONGHI | DL 20`.
- **What changes in size.** `controls` are 63 % of the index; each entry gains two
  characters (a pipe and a space), about +0.61 MB on `build/index.json` (+4.1 %),
  the same on its copy in `site/` and in the page's island. Computed from the
  entries, not measured on a full build.

*D48 and D49 above describe the first import. D56a and D56b supersede them in two places: `controls` entries are `<BRAND> | <MODEL>` (not `<BRAND> <MODEL>`), and the original label is the key's `label` as well as part of the citation. The measurements below were taken at importer commit `0d093c2`, before either; the figures after both are in D56a and D56b and in the importer's own report.*

### The full run, measured

The real database was imported into a scratch copy of `remotes/`, `src/`,
`tools/`, `build/`, `site/` and `pyproject.toml`, then built: importer commit
`0d093c2`, SwiftRemote `6aafd15`. **These figures are from that scratch copy.**
When the data is committed, `remotes/irblaster/IMPORT.md` is the record, and this
section should be checked against it.

| | Before (LIRC + SmartIR + authored) | After |
|---|---|---|
| remote files | 3,204 | 13,217 (+10,013) |
| keys | 113,819 | 525,084 (+411,265) |
| `remotes/` | 79.8 MB | 240.9 MB; `remotes/irblaster/` 161.2 MB (9.0 MB gzipped tar) |
| `build/pronto/` | 76.3 MB | 336.5 MB; `irblaster/` 260.2 MB, 10,013 files (10.1 MB gzipped) |
| `site/r/` | 65.2 MB | 285.2 MB; `irblaster/` 219.9 MB, 10,013 scripts (9.1 MB gzipped) |
| `build/index.json` | 1,251,008 B | **15,075,980 B** (12.0×) |
| `site/index.json` | 1,251,008 B | 15,075,980 B (a copy, D20) |
| `site/index.html` | 1,006,251 B | **11,537,234 B** (11.5×) |
| `build/warnings.json` | 129 warnings | 129 warnings |

The largest single files are 223 KB in `remotes/irblaster/`, 204 KB in
`build/pronto/irblaster/` and 180 KB in `site/r/irblaster/`; nothing is near a
host's per-file limit. The 129 warnings are all `carrier-off-nominal`, all there
before, none from this import: every file carries the registry's own carrier
(D52), so the import adds no warning of any kind.

Times, one core, on a 64-core, 125 GB machine:

| | wall | peak RSS |
|---|---|---|
| `rl import irblaster` (from an empty directory, and again over its own output) | 64.8 s, 68.9 s | 260 MB |
| `rl validate remotes/irblaster` | 2 min 9 s | 32 MB |
| `rl build` (validate, check, compile, index, site) | **18 min 2 s** | 736 MB |
| `rl build --check` | 17 min 44 s, 0 differences against the tree `rl build` wrote | 732 MB |
| `tools/irblaster_oracle_import.py` (16 worker processes; 6 min 13 s of CPU) | 30 s | |
| the whole test suite, in the scratch copy with the data and the fixes below | 3 min 7 s (1 min 37 s without the data) | 957 MB |

D40 recorded 3.5 minutes for the LIRC-era tree (113,819 keys; the machine was
not recorded). Scaled by keys that is 16 minutes for 525,084, so 18 is about
linear. Re-running the import over its own output gives byte-identical files
(`diff -r`, and the report's sha256 matches), and so does importing into an
empty tree.

**What does not scale**, in the order it will be felt:

1. **The index and the site page.** `build/index.json` grows 12×, to 15 MB, and
   `site/index.html` embeds it (11.5 MB) and parses it at load. D40's promise
   that "nothing committed grows with the whole corpus" holds for the
   per-remote files and **no longer holds for the index.** 63 % of the index is
   `controls`: 306,631 strings (a split id repeats its list), 7.2 MB of the 11.5
   MB the index serialises to. Nothing in the index needs more than a prefix of
   them to search by, and the per-remote artifact already has them. `index.py`
   and `site.py` were not changed. How long the page takes to open in a browser
   was not measured, so it is not known.
2. **`rl lookup`.** It calls `index.build_index(root)`, which loads every remote
   file. The query `BDP-S360` takes 2.5 s on the committed tree and 12 s with the
   import, and a query that matches many remotes (`TELEFUNKEN`) 17 s.
3. **`rl build` and `rl build --check`**: 18 minutes each, with CI's `--check`
   doing the whole thing again (D11, D19). `validate` is 2 minutes of that for
   the import alone.
4. **The repository.** +641 MB in the working tree (161 + 260 + 220), about 28
   MB compressed per generation of the data, and a regeneration that changes a
   hex map rewrites a large share of three trees.
5. **`tests/test_seed_data.py` treated every directory but `remotes/lirc/` as
   authored**, so the 10,013 imported files would have become 20,000
   parametrized tests (22,282 collected, an hour). It now scans the IR Blaster
   tree as it scans LIRC's (`test_the_irblaster_import_keeps_r19`, skipped until
   the tree exists) and samples every 200th file for the reproducibility test.
   SmartIR's 62 files are still tested as authored, as before.
6. **Directory names.** Brands with non-ASCII names slug to underscores
   (`_________` holds one remote, a nine-letter Cyrillic brand); 223 of 1,755
   directories contain an underscore. As with LIRC's `slug`, nothing is
   transliterated.

*Open, and not decided here:* what to do about the index before the data lands
in the main branch. It is 15 MB, rewritten by every regeneration, copied into
`site/`, and embedded in the page. The two obvious levers are `controls` (63 %)
and the eager load, and neither was touched.

**Two tests of the existing suite that the data broke**, both fixed:
`tests/test_seed_data.py` (above) and `tests/test_site.py`'s
`test_no_external_resources_are_loaded`, which asserted `"cdn"` is not in the
whole of `site/index.html`, data included. The import's controls hold `ORION G
20 LCDN` and fourteen more, so it failed, and pytest then spent half an hour
building a text diff of an 11.5 MB string for the failure message. It now checks
the page with the data island cut out. With both fixed the suite in the scratch
copy passes but for three tests: the documented-count test, and two that need a
git repository and a `.gitignore` the scratch copy lacked
(`test_setuptools_scratch_under_build_is_ignored` and
`test_gitignore_negations_mirror_the_generator_owner_table`). Whether the
repository's `.gitignore` needs a line for `remotes/irblaster/` was not
checked.

### The end-to-end oracle

`tools/irblaster_oracle_import.py`, run over the scratch output with the app's
signals for all 58,766 distinct codes, found **0 unexplained**: no problem of any
kind. The tool also re-derives every file's manufacturer, model, controls and
path from the dump's `models` table, and checks `IMPORT.md`'s totals, skipped
list and difference table against the files. Distinct codes, then keys, per DB
protocol:

| DB protocol | codes | matched | differs by reading | unrepresentable | keys | matched | differs by reading | unrepresentable |
|---|---|---|---|---|---|---|---|---|
| Denon | 519 | 187 | 332 | 0 | 1,675 | 620 | 1,055 | 0 |
| F12_relaxed | 138 | 138 | 0 | 0 | 331 | 331 | 0 | 0 |
| JVC | 1,023 | 3 | 1,020 | 0 | 5,782 | 5 | 5,777 | 0 |
| NEC | 33,522 | 33,116 | 0 | 406 | 274,082 | 273,006 | 0 | 1,076 |
| NEC2 | 5,084 | 4,855 | 0 | 229 | 10,853 | 10,419 | 0 | 434 |
| NECx1 | 1,439 | 1,439 | 0 | 0 | 6,887 | 6,887 | 0 | 0 |
| NECx2 | 1,238 | 1,238 | 0 | 0 | 8,120 | 8,120 | 0 | 0 |
| Pioneer | 1,673 | 6 | 1,667 | 0 | 5,838 | 25 | 5,813 | 0 |
| Proton | 1,476 | 18 | 1,458 | 0 | 7,300 | 155 | 7,145 | 0 |
| RC5 | 2,428 | 2,428 | 0 | 0 | 34,813 | 34,813 | 0 | 0 |
| RC6 | 1,237 | 1,237 | 0 | 0 | 6,633 | 6,633 | 0 | 0 |
| RCA_38 | 60 | 60 | 0 | 0 | 89 | 89 | 0 | 0 |
| RCC0082 | 300 | 300 | 0 | 0 | 3,509 | 3,509 | 0 | 0 |
| RCC2026 | 1,231 | 0 | 1,210 | 21 | 4,880 | 0 | 4,805 | 75 |
| REC80 | 2,723 | 2,415 | 0 | 308 | 14,816 | 14,335 | 0 | 481 |
| RECS80 | 396 | 396 | 0 | 0 | 5,165 | 5,165 | 0 | 0 |
| RECS80_L | 159 | 159 | 0 | 0 | 526 | 526 | 0 | 0 |
| SONY12 | 907 | 15 | 892 | 0 | 7,103 | 212 | 6,891 | 0 |
| SONY15 | 738 | 5 | 733 | 0 | 3,728 | 46 | 3,682 | 0 |
| SONY20 | 1,213 | 0 | 1,213 | 0 | 4,599 | 0 | 4,599 | 0 |
| Samsung36 | 643 | 643 | 0 | 0 | 1,488 | 1,488 | 0 | 0 |
| Sharp | 590 | 4 | 586 | 0 | 5,083 | 92 | 4,991 | 0 |
| Thomson7 | 29 | 0 | 29 | 0 | 31 | 0 | 31 | 0 |
| **total** | **58,766** | **48,662** | **9,140** | **964** | **413,331** | **366,476** | **44,789** | **2,066** |

What the classes rest on:

- **matched** is the family tools' rule (D58: carrier within 5 %, same number of
  durations, each within 12 % or 150 µs), applied to the key compiled *through
  its file* and decoded from Pronto, with only the allowances D58 lists, each
  counted under its own note and none folded in. The counts of keys under an
  allowance: NEC's frame without a lead-out 273,006; a toggle bit a file cannot
  carry (D3b) in RC5 34,813, RC6 6,633, RECS80 5,165 and RECS80_L 526, where
  **every key** of these four compiles `T=0` and matches only at `T=1`, which is
  what the app's preview shows; RC6's longer final space 6,633; the lead-out of
  Samsung36 1,488 and of the REC80 vendors Fujitsu, Teac-K and SharpDVD 1,061;
  idle gaps of JVC, Pioneer, Sharp and Denon, only where both signals have a gap;
  Sharp's and Denon's three frames; Blaupunkt's closing sync. Nothing else
  differs by more than the tolerance for any of the 366,476 keys.
- **differs by reading** is the 9,140 codes (44,789 keys) on which
  `FROM_DB_HEX_APP` gives other fields from `FROM_DB_HEX` or cannot send the
  code. The tool recomputes that set from the two tables and requires it to equal
  `IMPORT.md`'s counts (it does, per protocol). For every such key it requires
  that the app's signal is what the app's own reading compiles to: the encoding,
  within the tolerance, and to the microsecond before Pronto's rounding for
  21,013 keys (Proton, Sony12, Sony20, most of Sony15 and 586 of RCC2026's); or,
  for the 1,890 Sony15 keys whose codes the app masks to 15 bits, what the masked
  code compiles to; or, for the 4,219 RCC2026 keys the app reads as no Aiwa
  frame, what a port of its stale encoder produces, exactly. That confirms the
  classification. It is not a claim that either reading is right (D50).
- **unrepresentable** is a key `IMPORT.md` lists, whose code the wire reading
  refuses, and which no file holds: 964 codes, the 2,066 keys of D54.
- Thirteen codes on which the two readings happen to agree match (Pioneer 6, JVC
  3, Sharp 4): the "13 Pioneer, JVC and Sharp codes" of D64.

Per family the counts agree with the family tools' own: NEC 33,116 / 4,855 /
1,439 / 1,238 codes matched with 635 unrepresentable; REC80 2,171 + 244 lead-out
codes matched with 308 unrepresentable; Pioneer 1,667, JVC 1,020, Sharp 586 and
Denon 332 codes differ; Sony 2,838 of 2,858 differ.

### What is not proven (the import)

- **No hardware.** Every claim is a comparison of waveforms with the app's own, or
  with published decodes of real remotes. Nothing here says a device obeys a
  compiled key, and for the ten protocols whose reading differs from the app's
  the evidence is the families' (published decodes, LIRC frames, structure), not a
  receiver.
- **The provenance of the data.** Nobody says where it came from (D46).
  `plausible` is the tier for one source nothing cross-checked, and here the
  source is itself unattributed.
- **`matched` means the same signal as the app's, within 12 %.** Where the app and
  the IRP differ in a unit (D52) the ledger follows the IRP, except Samsung36.
- **Sharp's complement-frame inversion and Denon's `11` flag** (D64) are still the
  weakest parts of those two readings.
- **How the site page behaves in a browser** with the 11.5 MB island.

---

## 18. Twenty-four protocols and the readings of the database's hex

The database names 23 protocols; the registry held four (NEC1, NECx2, RC5,
Sony20). A code whose protocol the registry does not hold cannot become an `irp`
form, so six families of protocols were registered first, each protocol through
D18's three gates, and a seventh piece of work wrote the importer (§17). The
registry now holds **28**: the four, and 24 added here (`NEC2`, `NECx1`, `Sony12`,
`Sony15`, `RC6`, `RCA-38`, `Thomson7`, `Pioneer-2Part`, `JVC`, `Sharp`, `Denon`,
`Samsung36`, `Proton`, `F12_relaxed`, `RECS80`, `RECS80-0068`, `Aiwa`,
`Blaupunkt`, `Panasonic`, `JVC-48`, `Fujitsu`, `Teac-K`, `Denon-K`, `SharpDVD`).
D18's table carries all 28 with their IRP strings; §12 holds the gate-2b vector for
each. This section records what was decided per family, and one finding that
cuts across all of them.

Three terms, used throughout:

- **The wire reading** of a hexcode is the reading under which the code is the
  bit string that goes on the wire, first bit the most significant bit of the
  hexcode, so that a field the protocol sends least-significant-bit first
  appears bit-reversed. It is what `FROM_DB_HEX` implements and what the importer
  writes (D50).
- **The app's reading** is what SwiftRemote does with the same code
  (`FROM_DB_HEX_APP`), which is what the app transmits.
- **The oracle** is the app's own signal for every distinct code, produced by
  running SwiftRemote's Dart (`buildButtonFromDbRow`, then `previewIRButton`) and
  stored as `by_protocol/<DB protocol>.jsonl`, one object per distinct hexcode
  (`protocol`, `hex`, `appProtocol`, `params`, `code`, `freq`, `mode`, and
  `pattern`, the app's mark and space durations in µs). The files are not
  committed. `tests/fixtures/irblaster/` holds a few dozen of the oracle's rows
  per protocol, unedited, so the per-family tests run without it.

> **TODO for the integrator.** The Dart run that produced the oracle is not in
> this repository, and the notes do not say where its harness lives. The
> committed fixtures cover a few dozen codes per protocol, so the full counts in
> this section and in §17 cannot be regenerated from the repository alone. Either
> commit the harness or record in `tools/` where it is.

**D57 — The ledger holds the wire reading, and for ten protocols that is not
what SwiftRemote transmits.** Serves R19.3, D50. The owner's decision: the ledger
records what a real remote sends, not what the app's encoder does with a code.
The six family agents worked to the opposite rule, that a hexcode means what the
app's code says, and each that found the two apart said so rather than quietly
following the app. Three (Sony, Thomson7, RCC2026) followed the evidence and
offered the app's reading beside it; two (the Japanese four, and Proton) followed
the app and put the wire reading beside it for the owner to choose. The owner chose
the wire reading for all ten, and D53 gave every module one contract.
**This is the one finding that touches the data's meaning.** In the table below
every row is a protocol on which the two readings give different signals.

### Where SwiftRemote reads the database differently from the wire

| DB protocol (ledger protocol) | What SwiftRemote does with the hex | Evidence that the wire reading is the data's | Codes that differ | Keys |
|---|---|---|---|---|
| `SONY12` (Sony12) | Packs it as an integer `cmd \| addr << 7`, low bit sent first. The data is the frame's bits in transmission order, first bit most significant: TV power (D=1, F=21) is `A90`, which the app reads as command 0x10 on address 0x15 | Girr's Sony12 reference set at D=1: 25 of 25 commands are in the DB as wire hex, 7 as the app's packing (chance, since the DB fills about a fifth of the 4,096 values). Standard-label keys give the standard function under the wire reading for 169 of 264 distinct label/hex pairs (64 %), under the app's for 9 | 892 of 907 | 6,891 |
| `SONY15` (Sony15) | The same, masked to the 15-bit frame without a word | A Sony projector's protocol manual (via Girr `sony_vlp_hw50es.girr`): D=84, 26 of 30 as wire hex, 1 as the app's. 109 of 141 label/hex pairs (77 %) against 2. All 738 distinct codes have bit 0 clear, which is what padding a 15-bit frame leaves; under the app's packing that is inexplicable, and **362 of the 738 codes have bit 15 set, which the app masks away and sends another code** | 733 of 738 | 3,682 |
| `SONY20` (Sony20) | The same | The ledger's own hardware-verified `remotes/sony/RMT-B118P.json` (D=26, S=226): **38 of 38 keys** are in the DB as wire hex, 0 of 38 as the app's packing. The projector manual's Sony20 D=26 S=42: 19 of 22 against 0. 193 of 260 label/hex pairs (74 %) against 3 | 1,213 of 1,213 | 4,599 |
| `Pioneer` (Pioneer-2Part) | Sends each of four bytes least significant bit first (`pioneer.dart:96-123`) | IrpTransmogrifier's decodes of real captures are in the DB in wire order: a Pioneer receiver's Setup key is `{D0=170,F0=91,D=175,F=36}` and the DB holds it as `55DAF524` (Return, F=34, as `55DAF544`); the app-order codes `AA5BAF24` and `AA5BAF22` are not in the DB. Across the 12 models the DB shares with the repository's LIRC import, 638 keys: wire reading finds a real frame for 358, the app's for 0, both 0, neither 280 | 1,667 of 1,673 | 5,813 |
| `JVC` (JVC) | Sends each of two bytes least significant bit first (`jvc.dart:53-66`) | LIRC overlap, 19 models, 1,172 keys: wire reading finds a real frame for 702, the app's for 0, both 0, neither 470 | 1,020 of 1,023 | 5,777 |
| `Sharp` (Sharp) | Masks with `0x1FFF` and takes bits 12-8 as address and 7-0 as command, a register layout the data does not have (`sharp.dart:65-67`). The data is address (5 bits), command (8), trailer (2) and a pad bit, in wire order | IrpTransmogrifier's Sharp Pronto export gives Power `{D=1,F=22}` and Input Cycle `{D=1,F=19}`; the DB holds them as `8344` and `8644`. Read the app's way, Power would be one of eight codes `0x0116`, `0x2116` … `0xE116`, and none is in the DB. 561 of the 590 codes end in the bits `100` and the other 29 in `010`: the trailers `1:2` and `2:2` plus a zero pad bit. LIRC overlap, 16 models, 1,026 keys: wire 594, app 6 (coincidences of a 13-bit register), both 17, neither 409 | 586 of 590 | 4,991 |
| `Denon` (Denon) | Builds its 13-bit field from the first three nibbles plus `nib3.substring(3, 4)`, the *last* bit of the fourth nibble (`denon.dart:77-80`). The data's thirteenth bit is the *first* (`substring(0, 1)`) | Denon receiver decodes `left`, `0` and `OK` as `{D=8,F=175}`, `{D=8,F=129}`, `{D=8,F=187}`; the DB holds `17A8`, `1408`, `16E8`. The fourth digit is only ever 0, 6, 8 or E, so its low three bits are `000` or `110` and bit 0 is never set: thirteen data bits, then three the app ignores. LIRC overlap, 25 models, 1,524 keys: wire 276, app 0, both 851 (the fourth digit is 0 or 6, where the dropped bit is zero), neither 397. Every code whose fourth digit is 8 or E (332 of 519) loses the top bit of its command in the app | 332 of 519 | 1,055 |
| `Thomson7` (Thomson7) | Masks with `0xF7F`, then sends bits 3..0, **its own toggle**, then bits 11..5. The frame is `first4 + toggle + last7` with the toggle in bit 7's place, and the app's mask clears exactly that bit | All 29 DB codes are device 12 under the wire reading, one address and 29 commands, as one remote looks; the app's reading gives thirteen devices and two commands across the same 29 keys (20 distinct frames; hexcode bit 4 never reaches the air). **A hardware capture agrees key for key**: IrpTransmogrifier's `Thomson-0625.ict` (`.exp`: Thomson7) decodes Vol+ F=74, Vol- 42, Mute 80, Up 104, Down 88 at D=12, and the DB's Thomson7 remote holds VOL+, VOL-, MUTE, J UP, J DOWN as `329`, `32A`, `305`, `30B`, `30D`. Five of five, on a different remote of the same make | 29 of 29 | 31 |
| `Proton` (Proton) | Sends the hex's **low** byte first, then its high byte (`proton.dart:58-59,75,82`), the reverse of the IRP's `D:8,1,-8,F:8` | In 169 of the DB's 187 Proton remotes the high byte is the same on every key, and in none is the low byte (an address does that; a command does not). IrpTransmogrifier's Proton capture is nine keys, all D=20, F = 0, 1, 8, 9 for the digits: DB remote 18 holds them as `2800 2880 2810 2890`, with `0x28` = rev8(20) leading, high byte first; its P+ P- VOL- VOL+ NORMAL/OK are `28E8 2818 2828 28C8 28E4` | 1,458 of 1,476 | 7,145 |
| `RCC2026` (Aiwa) | Builds the 42 wire bits from the **last** 42 of a 44-bit number. SwiftRemote's copy of the encoder is stale: upstream `iodn/android-ir-blaster` fixed it on 2026-09-20 in commit `3bb60e3178` (the first 42, "padding after the 42 wire bits"), and SwiftRemote's copy is the V2.0.0 original, `c0658e8` | Under the first-42 reading `~S` is the complement of `S` in 1,210 codes and `~F` of `F` in 1,211; the stale way, 292 and 315 (`D` and `~D` pair up either way). The DB's `LEFT` key `10077FEA15C` is Aiwa D=8, S=0, F=21, exactly IrpTransmogrifier's teaser assertion for a real Aiwa remote's `left` key (`Aiwa_left.exp`). Digits and POWER decode to F = 1..9, 10, 0 (`76044FE01FC`, `76044FC03FC` …), which is how `probonopd/irdb` tables Aiwa. The app's own Universal Power default `0087FBC03FC` is Aiwa D=0 S=1 F=0 this way and F=192 the stale way. **The app reads a valid Aiwa frame for only 71 of the 1,231 codes (586 of 4,880 keys)** | 1,210 of 1,231 (the other 21 are refused by both readings) | 4,805 |
| **Total** | | | **9,140** | **44,789** |

What the table does and does not say. "Differs" means the two readings give a
different `(ledger protocol, device, subdevice, function)`, or the app's reading
cannot send the code as a frame at all. The end-to-end oracle (§17) proves for each
such key that the app's signal is exactly what the app's own reading compiles to,
so the classification is measured and not assumed. **That does not prove the wire
reading right.** The evidence for it is of three kinds, in order of strength:
decodes of real remotes that other people published (IrpTransmogrifier's teaser
captures and Girr's reference sets, a Sony manual), real frames from the
repository's own LIRC import, and the structure of the data (pad bits, trailers,
constant bytes). No device was tested. Where the evidence is weakest is stated
under D64 (Sharp's complement frame, Denon's `11` flag) and D62 (a remote that
mixes conventions cannot be excluded for SONY12 and SONY20, where every hexcode is
valid either way).

**On the Sony row: the app's reading is deliberate and tested for the editor**
(`test/sony12_protocol_test.dart:7-33`). It is the import path
(`db_button_import.dart:246-285`, repeated in
`ir_finder/ir_finder_models.dart:274-287` and
`universal_power/power_params.dart:86-99`) that reads database codes with it.

**D58 — Every hex map is checked against the app's own signal, and the framing the
check forgives is named.** Serves D10, D18, R19.2. For each DB protocol a family
tool (`tools/irblaster_oracle_<family>.py`) takes every distinct code, has the
ledger compile it through its own encoder, and compares with the app's signal:
the registry's nominal carrier within 5 % of the app's, the same number of
durations, each within 12 % of the larger of the two or 150 µs, whichever is
more. The tolerance is symmetric and was never widened to make a code pass; the
widest non-gap deviation in the NEC family is 0.71 %. Each code lands in one
class: *matched*; *matched, with a named framing difference*; *differs by
reading* (D57, and only when the app's own reading accounts for the app's
signal); *unrepresentable* (D54); or *unexplained*, which exits non-zero. There
is none. `tools/irblaster_oracle_import.py` then repeats the check end to end, for
every key through its written file (§17).

The framing the check forgives is listed here so that nothing hides inside "matched",
and each is counted under its own note:

| Difference | Where | Why it is allowed |
|---|---|---|
| The app's legacy NEC path ends on the last mark (67 durations); the ledger's NEC1 ends in `^108m` | NEC (273,006 keys) | The app's gap is dropped from the comparison for `NEC` only (D61) |
| The toggle bit | RC5, RC6, RECS80, RECS80-0068 (47,137 keys) | The app alternates `T` and its preview shows `T=1`; a compiled file carries `T=0` (D3b). Every key of the four matches only at `T=1`. Thomson7's toggle is in hexcode bit 7 and is ignored |
| RC6's final space | RC6 (6,633) | The app idles six units after the frame, the IRP pads to `^107m`; the ledger's space is longer, and the check requires it never to be shorter (D63) |
| The lead-out | Samsung36 (1,488 keys); REC80's Fujitsu, Teac-K and SharpDVD (1,061 keys) | The IRP pads to an extent the app does not (D65) or the app uses Panasonic's 173 units for all six vendors (D66) |
| Idle gaps | JVC, Pioneer-2Part, Sharp, Denon, only where both signals have a gap | The IRPs pad each frame to an extent, so the gap depends on the data; the app uses one constant per protocol (D64) |
| The repeat sequence | JVC and Pioneer-2Part | The app sends the intro and omits the repeat, the one length difference allowed |
| Three frames | Sharp, Denon | The app sends normal, complement, normal; the tool plays the intro and one pass of the repeat, though `minSends` stays 1 |
| Two copies of one frame | NECx2 | The app sends it twice; the ledger has it once in the repeat slot and `minSends` 2 (D3a) |
| Blaupunkt's closing sync | Blaupunkt | `IrSignal.ending` is reserved (D1), so the ledger drops it; the tool re-adds it from the intro (D66) |

**D59 — The mapping from hexcode to parameters, per database protocol.** Serves
D50, D53. Every function raises `ValueError` with fixed text and no hexcode in it,
so the importer's report can group on the reason (D54). `rev*n*` is the
bit-reversal of an *n*-bit field. All of it is the wire reading (D57); where the
app's reading differs, `FROM_DB_HEX_APP` holds that one.

| DB protocol | Digits | Ledger protocol | Reading |
|---|---|---|---|
| `NEC`, `NEC2`, `NECx1`, `NECx2` | 8 | `NEC1`, `NEC2`, `NECx1`, `NECx2` | `D` = rev8(byte 0), `S` = rev8(byte 1), `F` = rev8(byte 2); byte 3 must be the complement of byte 2, or the code is refused (D61, D67) |
| `SONY12` | 3 | `Sony12` | 12 wire bits, first most significant: `F` (7 bits, LSB first) then `D` (5). `A90` is D=1, F=21 |
| `SONY15` | 4 | `Sony15` | 15 wire bits then a pad bit that must be 0: `F` (7), `D` (8) |
| `SONY20` | 5 | `Sony20` | 20 wire bits: `F` (7), `D` (5), `S` (8). `A8B47` is D=26, S=226, F=21 |
| `RC5` | 3 | `RC5` | Bit 11 is the second start bit, inverted back into command bit 6 (`~F:1:6`); bits 10-6 are `D`; bits 5-0 are the low command bits |
| `RC6` | 4 | `RC6` | `D` = high byte, `F` = low byte, mode 0 |
| `RCA_38` | 3 | `RCA-38` | `D` = high nibble, `F` = low byte |
| `Thomson7` | 3 | `Thomson7` | `D` = rev4(hex >> 8), `F` = rev7(hex & 0x7F); bit 7, the toggle's place, is ignored |
| `Pioneer` | 8 | `Pioneer-2Part` | Four bytes, each bit-reversed: `D0`, `F0`, `D`, `F`. `device` = `D0`·256 + `D`, `function` = `F0`·256 + `F`; no subdevice |
| `JVC` | 4 (the last four if longer) | `JVC` | `D` = rev8(high byte), `F` = rev8(low byte) |
| `Sharp` | 4 | `Sharp` | 15 wire bits and a pad bit that must be 0: `D` = rev5 of the top five, `F` = rev8 of the next eight, then a two-bit trailer. `10` is the normal frame; `01` is a recording of the complement frame, whose `F` is inverted back; anything else is refused |
| `Denon` | 4 (the last four if longer) | `Denon` | `D` = rev5 of the top five, `F` = rev8 of the next eight; hex bits 2 and 1 are ignored, bit 0 must be 0 |
| `Samsung36` | 7 | `Samsung36` | `A(8) B(8) C(4) D(8)`: `D` = rev8(A), `S` = rev8(B), `E` = rev4(C), `F` = rev8(D); `function` = `E`·256 + `F` (D65) |
| `Proton` | 4 | `Proton` | `D` = rev8(high byte), `F` = rev8(low byte) |
| `F12_relaxed` | 1 to 3, read as a number | `F12_relaxed` | 12 bits: `D` = rev3 of the top three, `S` = bit 8, `F` = rev8 of the low eight |
| `RECS80`, `RECS80_L` | 3 | `RECS80`, `RECS80-0068` | Nine bits, MSB first (no reversal): `D` the top three, `F` the next six; the low three bits must be 0 |
| `REC80` | 12 | `Panasonic`, `JVC-48`, `Fujitsu`, `Teac-K`, `Denon-K`, `SharpDVD` | 48 wire bits, every byte bit-reversed into IRP order. Bytes 0-1 name the vendor: `02 20` Panasonic, `03 01` JVC-48, `14 63` Fujitsu, `43 53` Teac-K, `54 32` Denon-K, `AA 5A` SharpDVD. Each vendor's own check byte or fixed nibble must hold, or the code is refused (D67) |
| `RCC2026` | 11 | `Aiwa` | 44 bits: the **first** 42 are `D`(8), `S`(5), `~D`(8), `~S`(5), `F`(8), `~F`(8), each LSB first, and the last two must be 0. Refused unless the three complements hold |
| `RCC0082` | 3 | `Blaupunkt` | Nine biphase bits (the top bit of the first digit and the low two bits of the last are unused and must be 0), each inverted to get the wire bit: `F` the first six LSB first, `D` the next three |

**D60 — Carrier and `minSends`, per ledger protocol.** Serves R3, D3a, D24, D32.
`protocol.carrierHz` is the registry's `nominal_carrier_hz`, so no
`carrier-off-nominal` warning appears (D52); the app's own value is within 5 % of
it in every case. The family agents' working notes recommended the app's carrier
for some protocols (NEC1 38,000, NECx2 38,400, JVC 38,000, SharpDVD 37,000, Aiwa
38,222). The importer overruled them and takes the registry's figure, so no file
carries a carrier the registry does not name and no `carrier-off-nominal` warning
is added. The table gives both numbers.

| Ledger protocol | Written (`carrierHz`) | The app sends | `minSends` | Why |
|---|---|---|---|---|
| NEC1 | 38,400 | 38,000 (legacy path) | 1 | 1.0 % apart; word `006C` against `006D` |
| NEC2 | 38,400 | 38,222 | 1 | The same word, `006C` |
| NECx1 | 38,400 | 38,400 | 1 | |
| NECx2 | 38,000 | 38,400 | 2 | The app sends two back-to-back frames, which the ledger's one frame in the repeat slot loses (D3a). The registry's 38.0k is DecodeIR's figure; the capture measures 38,404 Hz and IrpTransmogrifier gives 38.4k. 1.0 % apart |
| Sony12, Sony15, Sony20 | 40,000 | 40,000 | 3 | The app sends three frames; Sony hardware wants SIRC at least three times |
| RC5, RC6 | 36,000 | 36,000 | 1 | |
| RCA-38 | 38,700 | 38,700 | 1 | |
| Thomson7 | 33,000 | 33,000 | 2 | **The app's behaviour, not a hardware fact**: its encoder duplicates the frame every press (`thomson7.dart` L100-103). The captured remote sends 5 to 9 repeats while a key is held, so 1 is also defensible |
| Pioneer-2Part | 40,000 | 40,000 | 1 | |
| JVC | 37,900 | 38,000 | 1 | 0.26 % apart, the same word `006D`. The LIRC import has 90 of 108 JVC confs at 38,000 and none at 37,900 |
| Sharp, Denon | 38,000 | 38,000 | 1 | The app sends three frames per press (intro plus one pass of the repeat); `minSends` stays 1 |
| Samsung36 | 37,900 | 38,000 | 1 | Also `unitUs: 500` with a claim (D52, D65) |
| Proton | 38,500 | 38,500 | 1 | |
| F12_relaxed | 37,900 | 38,000 | 1 | |
| RECS80 | 38,000 | 38,000 | 1 | |
| RECS80-0068 | 33,300 | within 5 % | 1 | |
| Aiwa | 38,123 | 38,222 | 2 | The app sends the frame and then the tail once per press: one intro plus one repeat. D3a does not define `minSends` for an intro-plus-repeat signal; D38's `min_repeat + 1` was used. **A gap in D3a, not closed here** |
| Blaupunkt | 30,300 | 30,300 | 1 | |
| Panasonic, JVC-48, Fujitsu, Teac-K, Denon-K | 37,000 | 37,000 | 1 | |
| SharpDVD | 38,000 | 37,000 | 1 | The IRP says 400 µs at 38 kHz; the app sends 432 µs at 37 kHz. 2.6 % apart |

**D61 — NEC2 and NECx1 join; the NEC map reads each hex byte bit-reversed.**
Serves D18, D50. The four NEC-family database protocols (33,522 + 5,084 + 1,439 +
1,238 distinct codes) land on four ledger protocols: `NEC` on `NEC1`, the others
on themselves. `NEC2` was in D18's backlog and `NECx1` in neither; both are "a few
lines' difference from NEC1", which D18 names as precisely how an unverified
encoder ships, and the gate was applied to them as to anything else.

- **IRP.** From IrpTransmogrifier's `IrpProtocols.xml`, verbatim, in the 1.2.14
  release and at `c945e76` (L1670, L1742), with DecodeIR as a second source.
  `NEC2` is `{38.4k,564}<1,-1|1,-3>(16,-8,D:8,S:8,F:8,~F:8,1,^108m)*` and `NECx1`
  `{38.4k,564}<1,-1|1,-3>(8,-8,D:8,S:8,F:8,~F:8,1,^108m,(8,-8,~D:1,1,^108m)*)`.
  DecodeIR differs in three ways, recorded and not smoothed over: `+` where
  IrpTransmogrifier has `*` for NEC2 (the same signal), 38.0k, and a `~F8` in
  NECx1's string that is a typo for `~F:8`. `nominal_carrier_hz` follows
  IrpTransmogrifier, as NEC1's does. **NECx2's registry entry is still sourced
  from DecodeIR** (`38.0k`, `+`), so the family mixes the two; the capture's 38,404
  Hz and the app's 38,400 both favour 38.4k, and NECx2 was not changed.
- **Vectors.** NEC2 is **published, weakly**: `DecoderNGTest.java` L178-L186
  @`c945e76`, `testDecodePioneer`, a 40 kHz string asserted to decode as Pioneer
  and, at 2000 Hz tolerance, as NEC2, with no parameters stated. IrpTransmogrifier
  defines Pioneer as NEC2 at 40 kHz, and its 1.2.14 release reproduces the string
  byte for byte with `render -n D=90,F=38 -p pioneer`, which fixes the parameters
  (S defaults to 255 − D = 165). Our NEC2 encoder at 40 kHz reproduces every
  duration under the tool's rounding. **The NEC2 half of the assertion was not
  re-run**: 1.2.14's `decode` lists NEC, NEC-f16, NEC-Shirriff-32 and Pioneer for
  it, and the pinned commit was not built. Our bytes differ from the string at 67
  of 72 words, because 564 µs is 22.5 cycles at 40 kHz and the two rounding rules
  split the half the other way. NEC2 also carries a 38.4k render (reproducible).
  NECx1 is **reproducible only**: two renders, D=12 and D=13, pin both polarities
  of the repeat bit `~D:1`, and `test_registry` warns about it. NECx2 was searched
  again, with Pronto strings that have 8-unit lead-ins at four carriers, and still
  has no published vector.
- **Gate 2a.** IrpTransmogrifier's `teaserfiles/NECx2_NECx1.ict` and its `.exp`
  @`c945e76`: a hardware capture (irscope, measured carrier 38,404 Hz) of one remote
  that sends NEC2, NECx2 and NECx1, with the tool's own decodes `NEC2 {D=31,F=223}`,
  `NECx2 {D=67,S=83,F=57}` and `NECx1 {D=44,S=44,F=4}`. Our encoders reproduce
  every duration of a real frame quantised to 564 µs, and the 108 ms extent within
  0.8 % (107.1 ms); NECx1's repeat is the three-pair frame the IRP describes, with
  a one-bit for even D; NEC2 and NECx2 repeat the whole frame and nothing else. A
  capture carries instrument bias, so this is ratios and layout only.
  `tests/vectors/nec-family-captures.json` keeps medians and the quantised first
  frame, no raw durations, as the Sony20 capture's precedent has it. Gate 3:
  `tests/test_nec2.py` and `tests/test_necx1.py`, with exhaustive round trips.
- **Mapping and the bit order.** The app sends the 32-bit word MSB first
  (`lib/utils/ir.dart` L126-L143; the loops at `nec2.dart` L60, `necx1.dart` L82,
  `necx2.dart` L49), and NEC sends each byte LSB first, so each hex byte is the
  bit reversal of the NEC byte it carries. IrpTransmogrifier calls this
  `NEC-Shirriff-32`. It is backed by two checks that do not involve the app. The
  ledger's own `remotes/samsung/BN59-01199F.json` has eight `irp` keys (D 7, S 7,
  from IRDB, which files them as NECx2); inverting the map gives `E0E040BF` for
  POWER (F=2), `E0E0F00F` for MUTE and so on, and all eight exist in the DB, 74 to
  128 keys each under `NECx2` and 4 to 8 under `NEC` (a bit-order mistake would not
  hit all eight, nor would a byte-order one). And codes derived by hand from the
  IRP's byte order (`20DF10EF` is D 0x04, S 0xFB, F 0x08). The validity rule
  `b2 ^ b3 == 0xFF` is invariant under per-byte reversal, so the split into
  representable and refused does not depend on the reversal being right; the
  waveform comparison does.
- **Which protocol a code lands on is what the app transmits.** The app sends
  every DB `NEC` code with a 9000/4500 µs lead-in, so it is `NEC1` whatever its D
  and S. Extended NEC is common: of the 33,116 representable `NEC` codes 8,634 have
  S = ~D, 1,363 have S = D and **23,119 have a free S**, and NEC1's IRP has an
  explicit `S:8`, so all are NEC1 (NEC2 1,679 / 44 / 3,132; NECx1 74 / 1,306 / 59;
  NECx2 85 / 480 / 673). IrpTransmogrifier's `S:0..255=255-D` is only a default,
  which the ledger does not use: its encoders require an explicit subdevice.
- **Framing the check forgives.** The app's `NEC` path ends on the last mark, so the
  `^108m` gap is dropped from that comparison; NECx2 is two copies of one frame in
  the app and one in the ledger (`minSends` 2); NECx1's repeat frame is in the
  ledger but the app never sends it, and `minSends` 1 does not play it either.
- **Where the app and the reference differ.** None needed the ledger to follow the
  app; in each case it follows the IRP and the app's waveform is within tolerance of
  it. The ones that are the app's to fix are in the findings below: the 107,904 µs
  frame total its comments call 108,800 (finding 9), NECx1's unused repeat helper
  (10), the legacy path every DB `NEC` code reaches (11), NEC2 being NEC (12) and a
  nine-digit truncation (15). Two belong here. **The app's timings are rounded where
  the IRP's are exact**: 9000/4500, 560 and 1690 µs for legacy NEC, 562/1687 for the
  rest, against 16 × 564 = 9024, all within 0.71 %, so no `unitUs` override. And
  **the DB labels one code two ways**: 3,695 distinct `NEC` hexcodes also appear under
  `NEC2`, `NECx1` or `NECx2` (3,112 / 580 / 312), and the app sends different
  waveforms for them. 82 `NEC` codes (231 keys, 6 remotes) are `E0E0xxxx`, D = S = 7,
  the Samsung TV address IRDB files as NECx2 and the same DB holds as 147 codes under
  `NECx2` (4,512 keys, 112 remotes). They map to `NEC1` because that is what the app
  sends; whether they should have been NECx2 (an 8-unit lead-in) is a curation
  question the code cannot answer. Also 1,410 `NEC` codes share a (D, S) with some
  `NECx1` or `NECx2` code (613 with `NECx2`). The map refuses a code that is not
  exactly eight hex digits, so it does not inherit the app's nine-digit truncation.
- **Not proven.** An odd-D NECx1 repeat bit on hardware (the render says a
  zero-bit; the capture has only D=44); NEC2's and NECx1's byte-level Pronto
  against a published vector; that a real receiver needs the repeats `minSends`
  leaves out; and that the 635 refused codes are what they look like (D67).

**D62 — Sony12 and Sony15 join, each through its own gates.** Serves D18, D50. Both
share Sony20's frame code, and nothing was waved through on that basis: D18's
"trivial variation" warning was right to insist, because the frame code is shared
and the evidence (80 teaser captures, 25 measured ones, 25 Girr strings) is per
protocol.

- **IRP.** IrpTransmogrifier's `IrpProtocols.xml` @`c945e76` L2581 and L2591,
  verbatim, byte-identical in the 1.2.14 release (L2562, L2572), as D18's table
  gives them: `…F:7,D:5,^45m)*[D:0..31,F:0..127]` for Sony12 and
  `…F:7,D:8,^45m)*[D:0..255,F:0..127]` for Sony15. DecodeIR's documentation has `+` for `*`. The
  registry's Sony20 string is still DecodeIR's `+` spelling, the cosmetic
  difference D18's gate-1 note already records. One frame builder sits behind
  three entry points; Sony12 takes `D:5` and Sony15 `D:8`, neither takes a
  subdevice, and one given is an error pointing at Sony20.
- **Vectors.** Sony12: Girr's `commandset_sony.girr` D=1 F=21 (L42-47 @`5ca171e`),
  **published** (Girr's output is evidently IrpTransmogrifier's, committed to a
  sibling repository with the parameters beside it; all 25 of its commands match
  the tool's rule word for word), plus a 1.2.14 render D=23 F=70 (reproducible).
  Sony15: a 1.2.14 render D=164 F=61, **reproducible only**, and `test_registry`
  warns as for NECx2. The one published Sony15 string with a decode assertion
  (`IrpTransmogrifierNGTest.java` L383-389 @`c945e76`, D=164 F=61) is **not usable
  as a gate-2b vector**: its sixteen pairs equal ours except the last word, but it
  puts the frame in the once-sequence (a decode input is written that way) and it
  totals 44.4 ms, so its `0300` is not the `^45m` lead-out (`0318` under the tool's
  rule). Searched for a published Sony15 byte-level vector with the lead-out and
  not found: IrpTransmogrifier's tests and docs @`c945e76`, the `Decoder.java`
  strings @`705ce35` that gave Sony20 its vector (they hold no Sony12 or Sony15),
  Girr's reference and test files (its only Sony15 file, `sony_vlp_hw50es.girr`, is
  parameters with no waveform), the teaser set, and `gh search code`, which
  returned nothing and may have been restricted.
- **Gate 2a**, `tests/vectors/sirc-structural.json`, tested in
  `tests/test_sony_vectors.py`. IrpTransmogrifier's teaser set: 80 captures
  reproduced duration for duration (21 Sony12, 49 Sony15, 10 Sony20), exactly
  nominal and ending in a 500 ms gap, so they check ratios, lead-in, bit shapes and
  field and bit order, not the 45 ms extent; no test in the pinned tree reads them,
  so they are test data with expected decodes and not assertions. **Twenty-five
  measured captures** (`Sony_15_20.ict`, `Sony_A2172.ict`: 20 Sony15 at D=48 and
  D=176, 5 Sony20 at D=16 and D=26), evidently from real remotes: durations jitter
  by one 25 µs sample tick and each holds 3 to 7 frames. Every first frame is within
  one tick of ours on every duration and its period is **45.0 ms to within 150 µs**
  (44,975 to 45,150 µs). That is the only hardware measurement of `^45m` among the
  Sony vectors, and every capture has at least three frames, which supports
  `minSends` 3. No measured Sony12 capture exists. Girr's 25 Sony12 strings
  reproduce under the tool's rule, lead-out included.
- **Gate 3.** `tests/test_sony12.py` (all 4,096 frames) and `tests/test_sony15.py`
  (all 32,768), against `tests/sirc_reference.py`, a frame reader written from the
  layout and not from the encoder. `tests/test_sony.py` gained Sony20 sweeps (all
  8,192 address pairs at one function, all functions at the BX510 address, every
  field bit alone); the 20-bit space (1,048,576) is swept, not exhaustive.
- **Mapping.** D59. The 13-bit Sony20 address splits as `D` = the low five bits and
  `S` = the high eight, because the wire order is `F:7,D:5,S:8`. **Verified, not
  assumed**: for all 1,213 codes the app's own `params.address` equals D | S << 5
  and its waveform equals the ledger's, and the LIRC importer already splits 20 bits
  the same way (`lirc/importer.py:193-197`). Hexcodes are taken as the DB spells
  them (3, 4, 5 digits); the app's lenience (it strips non-hex characters and
  accepts any length) is not copied.
- **Framing.** None. The app emits three frames, each padded to exactly 45,000 µs
  (it removes the last space and re-adds `45000 − used`), which is `^45m`; the
  ledger's one frame as repeat, played `minSends` = 3 times, is identical. Under the
  app's reading every compared duration is **exactly** equal (70,746 of 70,746 for
  SONY12, 36,096 of 36,096 for SONY15, 152,838 of 152,838 for SONY20), so nothing
  rests on the tolerance. The app's Sony encoders never read `params['_repeat']`
  (contrast `rc5.dart:139`), so a press always sends three frames and each
  hold-loop tick another three.
- **The reading** (D57): 2,838 of the 2,858 distinct Sony codes. **Not proven.** (1)
  Sony15 has no published byte-level vector with the `^45m` lead-out. (2) The
  teaser captures are exactly nominal, so they establish layout and not an
  independent timing measurement. (3) Girr's Sony12 strings are IrpTransmogrifier's
  output and so test our encoder against the same IRP, not a second implementation;
  the IRP itself is agreed independently by DecodeIR's documentation (a different
  author) on timings and fields. (4) Only Sony20 has hardware-verified data in the
  ledger (RMT-B118P); the wire reading for SONY12 and SONY15 rests on Girr's set,
  the projector manual, label statistics and SONY15's pad bit, which is strong and
  not a hardware test, and **a remote that mixes conventions cannot be excluded for
  SONY12 and SONY20, where every hexcode is valid either way**. (5) No Sony frame
  was tested on hardware.
- **Follow-up, not done.** `lirc/importer.py:191-197` turns only 20-bit Sony blocks
  into an `irp` form; 12- and 15-bit blocks could now become Sony12 and Sony15.

**D63 — RC6, RCA-38 and Thomson7 join; RC6 needed no bitspec exception.** Serves
D18, D3b, D50.

- **IRP.** IrpTransmogrifier's `IrpProtocols.xml` @`c945e76`, verbatim (RC6
  L2143-L2145, RCA-38 L2254-L2256, Thomson7 L2869-L2873), each corroborated by
  DecodeIR's documentation (retrieved 2026-10-03), which gives the same frames with
  `+` for `*`. The database has four RCA entries and a family of RC6 ones, and the
  app's frames pick exactly one of each. `RC6` is mode bits `000` and a sixteen-bit
  payload; `RC6-6-20` (mode 6, a four-bit subdevice) and `RC6-M-16` (mode a
  parameter, of which `RC6` is the M=0 case) are not it. `RCA-38` is the 38.7 kHz
  frame with a plain `8,-8` lead-in and a single stop mark; `RCA` and `RCA(Old)` (58
  kHz) and `RCA-38(Old)` (a longer first lead-in and a double stop mark) are not.
- **RC6 is mode 0 only, and D18 was wrong that it needs a bitspec exception.** D18
  said its trailer bit is double-width and "needs a bitspec exception none of the
  other protocols require". The encoder builds the frame as per-unit levels and
  run-length encodes them, so it does not; the registry's `encode` interface is
  unchanged, mode is not a parameter, and the toggle is the existing `toggle=`
  keyword. Nothing about modes other than 0 was checked.
- **RCA-38's gap is `-16`, not an extent**, so `extent_us` is `None` (D3). The
  complement half always contains twelve 1 bits, so every frame is the same 59,340
  µs whatever the data, and an extent would change nothing.
- **Vectors.** RC6 has two **published** Pronto strings (`ProtocolNGTest.java`
  L230-L237, D=12 F=34 T=0, the assertion `approximatelyEquals`; and
  `ShortProntoNGTest.java` L20, D=1 F=3, which ends on a space where the first does
  not), both also what the 1.2.14 release renders, with our bytes differing at the
  lead-out word only (41 and 43). RCA-38 (D=15 F=144) and Thomson7 (D=12 F=74 T=0) are
  **reproducible only**, from the 1.2.14 release, and `test_registry` warns about
  them. Searched and not found for those two: every IrpTransmogrifier test source
  @`c945e76`, Girr (its Philips RC6 command set,
  `philips_tv_cmdset_rc6.girr`, lists device 0 with power as function 12 and volume
  16/17, which agrees with the DB's RC6 codes `000C`, `0010`, `0011`, `000D`, but
  holds no waveforms), IrScrutinizer, and `probonopd/irdb` (one Thomson7 row, a Sony
  receiver at D=8 F=8, no waveform).
- **Gate 2a.** RC6: three published microsecond sequences @`c945e76`
  (`BiphaseWithDoubleToggleDecoderNGTest.java` L27-L32, D=255 F=0 in both toggle
  states; `BiphaseDecoderNGTest.java` L23-L26, D=120 F=3), each asserted to decode to
  the stated fields; the encoder reproduces all three to the microsecond
  (`tests/test_rc6.py`). They are exact multiples of the unit and appear to be that
  tool's own output, so they verify layout, the double-width trailer and the extent,
  and not that a receiver accepts them. RCA-38 and Thomson7: IrpTransmogrifier's
  `teaserfiles/RCA-38.ict` (31 keys) and `Thomson-0625.ict` (7 keys), `irscope`
  captures of real remotes with the `.exp` decodes. They are cited, not vendored
  (GPL-3.0 test data of another project), and `tools/philips_capture_audit.py
  --irpt DIR` repeats the audit: every key's bits decode to the `.exp` fields (bit
  order, the RCA complement half, Thomson's toggle position) and the encoder's frame
  is within 12 % or 150 µs of every duration. **RCA-38 disagrees with its IRP by a
  uniform 8.5 %**: marks and spaces, lead-in included, are all 1.085× the IRP's, a
  unit of about 500 µs where the IRP and DecodeIR say 460. An instrument bias on
  marks would not move spaces the same way, though one remote's clock could. The
  encoder follows the IRP, as does the app, and a remote file can set
  `protocol.unitUs` to 500 with a `claims` entry (D27) if a receiver turns out to
  care. **Thomson7 agrees to 4 %**: marks 0.961× and spaces 1.020× of the IRP's, the
  first frame's period 80.16 to 80.19 ms against `^80m`, the carrier measured at
  33.19 kHz. Here the app's durations (460 µs marks, 2,000 and 4,600 µs spaces) are
  closer to the capture than the IRP's (500, 2,000, 4,500); that is not used to bend
  the encoder.
- **RC5's map** (RC5 itself registered in §16): twelve bits, all 4,096 codes
  representable, 2,428 of 2,428 DB codes compare **exactly**, every duration, at the
  toggle the app showed. The endings agree too: a frame ending on a mark gets the
  gap appended and one ending on a space has that space lengthened, both to 114,000
  µs (`rc5.dart` L93-107, `rc5.py` L125-138), and both start on S1's mark with the
  leading idle half-bit dropped, so no framing allowance is needed. A published raw
  sequence IrpTransmogrifier holds for RC5
  (`BiphaseDecoderNGTest.java` L22 @`c945e76`, D=12 F=3 T=1) also reproduces exactly
  (`tests/test_irblaster_philips.py`); the RC5 index entry does not cite it.
- **Thomson7 and the wire reading** (D57). The database's Thomson7 codes all belong
  to one remote (id 800296, "B2B.TEST RCT100 PROMO"), a test entry with 31 keys and
  the only Thomson7 in the database. The encoder follows the IRP's timings and not the
  app's, though here the app's are the closer to the capture. The oracle reports all 29
  codes as explained mismatches and not as matches: the app's frames differ from the
  ledger's for the reason D57 states and no other.
- **The toggle (D3b).** The database stores no toggle, and the app alternates it
  every press. The app's preview shows `T=1` for every RC5 and RC6 code
  (`rc5.dart` L139-146: the preview resolves `!_toggleFlag` without consuming it,
  true for a fresh state; `rc6.dart` L132-139 does the same), and the ledger
  compiles `T=0`. The oracle encodes each code at both states and requires one to
  match; the ledger's own `T=0` matches for none of them. That is the D3b gap, not a
  mismatch, and nothing here closes it.
- **RC6's final space, and who is right.** The app ends every frame after the
  standard's six-unit signal-free time (`rc6.dart` L99, a 2,664 µs space, 3,108 µs
  after a last bit of 1). The IRP's `^107m` makes the final space whatever remains
  of a 107 ms frame period (83,912 µs, or 84,356 after a last bit of 1). Same
  marks and spaces to the last one; the ledger's final space is longer for all
  1,237 codes, and the oracle requires it never to be shorter. They are not in
  conflict: six units is the minimum idle, 107 ms the repeat period the database
  uses. It matters only to how fast a held key repeats.
- **A quantizer discrepancy** found while choosing the Thomson7 vector: D68.
- **Not proven.** That a real Thomson TV obeys the corrected frames: the capture is
  a different remote, and nothing here transmits. RC6 modes other than 0. The RCA-38
  unit, which rests on one capture.

**D64 — Pioneer-2Part, JVC, Sharp and Denon join.** Serves D18, D50, D31.

- **IRP.** IrpTransmogrifier's `IrpProtocols.xml` @`c945e76`, identical in the 1.2.14
  release (Pioneer-2Part L2060, JVC L1207, Sharp L2461, Denon L494); D18's table has
  the strings.
- **Pioneer-2Part, not Pioneer.** The app's 136 durations are two 68-duration frames
  (`pioneer.dart:126-131`, `db_button_import.dart:237-243`): the primary address and
  command, then the secondary pair, or the primary again. In the database 1,102 of
  1,673 codes have different halves, and 694 of those have `F5` (device 175 in
  wire order) as the second address, the "Pioneer Mix" shape that IrpTransmogrifier's
  own `PioneerMix` teaser files decode. The 571 codes with equal halves are its
  degenerate case and render to the same two frames. Plain `Pioneer` is one frame at
  a 108 ms extent, so it would send a different signal and no database code needs
  it; D18 says the registry holds what something uses, so it is not registered.
  IrpTransmogrifier prefers plain Pioneer when it *decodes* equal halves; nothing
  here depends on that. **Parameters.** An `irp` form has `device`, `subdevice` and
  `function`; Pioneer-2Part has four numbers. `device` = `D0`·256 + `D` and
  `function` = `F0`·256 + `F`, first frame in the high byte, `subdevice` omitted and
  refused. The IRP's defaults (`D=D0`, `F=F0`) are not assumed: an equal pair is
  written out (`0xADAD`). Both fit the schema's 16-bit `hexOrInt`.
- **JVC.** `JVC{2}` is the repeat frame alone, `JVC_squashed` is decode-only, and
  `JVC-48` and `JVC-56` are Kaseikyo-family frames. The app's 36 durations are a
  lead-in, 16 bits and a stop mark, so it is JVC; the intro is that frame and the
  repeat is the same bits with *no* lead-in at a 46.42 ms extent, as
  IrpTransmogrifier renders it and as its own `JVC.ict` shows (a 36-duration frame
  with a lead-in followed by a 34-duration frame without). The app sends the intro
  and omits the repeat. The IRP's 33 % duty cycle is not modelled (`IrSignal` has
  none, for any protocol).
- **Sharp and Denon.** The app sends three frames (normal, complement, normal),
  which is the IRP's intro (normal) plus one pass of its repeat (complement,
  normal). `Sharp{1}`, `Sharp{2}`, `Denon{1}` and `Denon{2}` are the two halves
  alone (the Denon ones decode-only); `Sharp_Old` is a 3-bit device at a 49 ms
  extent. None is registered. `SharpDVD` and `Denon-K` are Kaseikyo-family frames,
  registered under D66.
- **`extent_us` is `None` for all four.** D31 pads a truncated sequence to a single
  figure, and these have several extents inside one sequence (two frames in
  Pioneer-2Part's intro and in Sharp's and Denon's repeat, and JVC's intro and
  repeat differ). `None` sends a truncated raw form down the `defaultGapUs` branch;
  a figure would make D31 raise.
- **A superseded IRP, and the app follows it.** Denon's and Sharp's 43,560 µs is
  exactly 165 units, the *superseded* form of both IRPs that IrpTransmogrifier keeps
  in a comment beside the live ones (`(D:5,F:8,0:2,1,-165,D:5,~F:8,3:2,1,-165)*`).
  IrpTransmogrifier's own published Denon Pronto string (`ShortProntoNGTest.java`
  L21) is that form, with a 0x0677-cycle (43.5 ms) gap. The live `^67m` form is
  registered because it is what the source's active definition says;
  `tests/test_denon.py` checks the published string against our encoder, every mark
  and space equal and only the gaps different.
- **Vectors.** All four are **reproducible only**, 1.2.14 `render` (D0=170 F0=91
  D=175 F=36; D=5 F=19; D=1 F=22; D=8 F=175), and `test_registry` warns about each.
  Searched, and why none is published: `ShortProntoNGTest.java` L21 holds a Denon
  string with no stated parameters (decoded as `{D=1,F=3}`) in the superseded form
  above; `DecoderNGTest.testDecodePioneer` asserts a decode of one plain-`Pioneer`
  frame, no parameters; `ProtocolNGTest` builds `sharp` and `denon` from the
  superseded IRPs and asserts nothing about their rendering. Nothing asserts a
  render of `Pioneer-2Part`, `JVC`, `Sharp` or the live `Denon`. The *parameter
  values* are cited from real decodes (the Pioneer receiver's Setup key, a JVC
  `.exp` entry, Sharp's Power, Denon's `left`) even though the waveform is
  generated. **Wider than one vector**: 34 parameter sets per protocol (corners, the
  published decodes, a seeded random sample) rendered by the same jar in microseconds
  (`render -r`) and compared duration for duration, **136 renders and 0
  differences**, every extent-padded gap included
  (`tests/test_irpt_sweeps_japan.py`).
- **Gate 2a is weaker than a published constant table.** Two real captures (a
  Pioneer receiver, `PioneerMix2.ict`, 40.16 kHz with a 548 µs mark against 564; a
  Denon receiver, `Denon.ict`, 37.4 kHz, marks 254 to 292 µs), a Pronto export
  (`Sharp_Pronto.txt`), and a JVC file (`JVC.ict`) whose durations are exact
  multiples of 525 µs and so look generated. They verify layout, bit order and
  ratios, never the idle gaps or absolute durations;
  `tests/vectors/irpt_teaser_japan.json` keeps the first signal of nine of them. Gate 3:
  exhaustive round trips for JVC (all 65,536 pairs), Sharp (8,192) and Denon
  (8,192), and a 1,024-encode sweep plus 2,000 random frames for Pioneer-2Part.
- **For the importer: write the `irp` form only.** D8 requires equal burst counts
  and holds the terminal gap to 150 µs, so the app's raw pattern as a second,
  cross-checked form would fail on the missing repeat (every JVC and Pioneer-2Part
  code) and on the gap (the 1,924 "gaps differ" codes), for reasons that are the
  app's choices and not errors in either signal.
- **The oracle, under the app's reading** (D58): Pioneer 1,673 codes, 0 matched,
  1,673 matched with gaps differing; JVC 776 and 247; Sharp 587 and 3; Denon 518 and
  1; no mismatch and nothing unrepresentable. "Gaps differ" means every mark and
  space is within tolerance and only the idle gaps are not; it is counted
  separately, never folded into "matched". Worst non-gap deviation: Pioneer
  **11.3 %** (a one-space, 1500 against 1692 µs; its gap is 1.195× for every code),
  JVC 0.4 %, Sharp 7.9 %, Denon 7.9 %. Pioneer is within the tolerance by 0.7
  points, and the result depends on how the tolerance is read: relative to the
  larger duration (as here), or to the IRP's value, it passes (192 µs against 203
  allowed), but relative to the app's own 1500 µs it would not (12.8 %). It was not
  widened. Under the wire reading (D57), 1,667 of 1,673 Pioneer, 1,020 of 1,023 JVC,
  586 of 590 Sharp and 332 of 519 Denon codes give a different signal from the app's.
  The other 200 are the 187 Denon codes whose fourth digit is 0 or 6, where the
  dropped bit is zero anyway, and 13 Pioneer, JVC and Sharp codes on which the two
  readings happen to agree.
- **Gaps.** An IRP `^E` pads each frame to a fixed *extent*, so the gap after a
  frame depends on its data (JVC's runs from 12.2 ms to 29.0 ms). The app uses one
  constant per protocol: 21,000 µs for JVC, 26,000 for Pioneer, 43,560 for Sharp and
  Denon. Real signals follow the extent: over the LIRC import's frames JVC's
  periods are 59 ms (2,175 frames) and 46 ms (1,994), Sharp's 67 to 68 ms (1,393),
  Denon's 65 to 68 ms, Pioneer's 89 to 90 ms (1,034). For Pioneer the IRP is not
  clearly the better figure: a real receiver captured in `PioneerMix2.ict` has a 25.4
  ms gap, as does IRremoteESP8266's measured minimum (25,181 µs, `src/ir_Pioneer.cpp`),
  and 880 of the LIRC import's Pioneer gaps are 25.3 to 25.5 ms. The app's 26 ms is
  within 3 % of that; the IRP's `^90m` gives 21.8 ms. The frame periods agree to 3 %
  whichever you take: 89.1 ms from IRremoteESP8266's figures, 90.0 from the IRP, 87.2
  from the app. The ledger follows the IRP, and the "gaps differ" count for Pioneer
  is a disagreement and not evidence the app is the wrong one.
- **Timings.** Pioneer's IRP unit is 564 µs (NEC-derived; Pioneer is "distinguished
  from NEC2 only by frequency"); the app's are 8500/4225, 500 and 500/1500. A
  measured Pioneer in IRremoteESP8266 (its issue #1220) reads 8506/4191, 568 and
  487/1542, and `PioneerMix2.ict` 8548/4227, 548 and 527/1577. The app's header is
  within 0.1 % of the measured one and its spaces within 3 %; its bit mark, 500
  against 568, is 12 % short, and IrpTransmogrifier's nominal header is 6 % high. The
  oracle's 11.3 % is the app's spaces against the *nominal* IRP, not against a real
  remote. Denon and Sharp: the app's marks are 280 µs and its spaces 860 and 1720 (a
  ratio of 2); the IRP's `<1,-3|1,-7>` is 264, 792 and 1848 (2.33), and `Denon.ict`
  reads 255, 795 and 1846 on average, so the IRP is within 3 % of a real receiver and
  the app's zero-space is 8 % above and its one-space 7 % below. JVC is the clean one:
  8400/4200/525/525/1575 against 8432/4216/527/527/1581, under 0.4 %.
- **Carriers and `minSends`.** D60. The encoders ignore the `_repeat` flag `sendIR`
  passes (none reads it), so a held button re-sends the same pattern, and the
  database holds no repeat count.
- **Not proven.** (1) **Sharp's complement-frame inversion.** 29 of Sharp's codes
  end in the trailer `2:2`, recordings of the complement half; `_sharp_wire` returns
  the *complemented* function by the IRP's definition. Only one such key is in the
  LIRC overlap and it does not match, so nothing independent supports that
  inversion, and refusing those 29 would be a defensible alternative. (2) **Denon's
  `11` flag.** Hexcode bits 2 and 1 are `00` or `11` in the database; the real
  remotes' frames carry the trailer `00` either way (230 of the 309 LIRC-overlap
  keys with fourth digit 6, and 25 of the 26 with E, match a real frame that way;
  the rest are keys the confs lack), so the wire reading ignores them, but what they
  encode is not known. (3) Nothing was run against hardware. (4) **D6 rounding at 40
  kHz**: our Pronto bytes differ from IrpTransmogrifier's in 201 of 208 words for
  Pioneer-2Part, because a 564 µs unit is 22.56 cycles, which rule 4 (against the
  word's period) rounds to 22 and the tool (against the nominal carrier) to 23. The
  timings are identical under the tool's rule; it is a property of D6 at this
  carrier and unit, recorded in `pronto-vectors.json`. (5) Pioneer codes of other
  than eight digits, and JVC or Denon codes longer than four, are not in the data;
  the app keeps the last four digits of a longer JVC or Denon code and the mapping
  does the same.

**D65 — Samsung36, Proton, F12_relaxed, RECS80 and RECS80-0068 join.** Serves D18,
D24, D27, D50. Five protocols, 2,812 distinct codes, **0 unrepresentable and 0
mismatched** under the app's reading, with 643 "matched with the lead-out
differing" (all Samsung36).

- **IRP.** IrpTransmogrifier's `IrpProtocols.xml` @`c945e76` (Samsung36 L2409-L2411,
  Proton L2084-L2086, F12_relaxed L893-L895, RECS80 L2272-L2275, RECS80-0068
  L2288-L2290), each character for character the same in the 1.2.14 release; D18's
  table has the strings.
- **Ledger names are IrpTransmogrifier's names**, as NEC1, NECx2, RC5 and Sony20
  already were, so DB `RECS80_L` registers as `RECS80-0068`. **`RECS80_L` is a
  different IRP definition, not a carrier variant.** The app's own description says
  "33.3 kHz … Same bit string as RECS80", which reads like a carrier change; it is
  three differences: carrier 33.3 kHz, unit 180 µs against 158 (bit spaces
  5,580/8,460 against 4,898/7,426), and the ending (the app pads the whole frame to
  138,000 µs, the IRP's `^138m`, where `recs80.dart` ends with a plain 45,000 µs gap,
  `-45m`). All 159 codes match `RECS80-0068` exactly and none matches `RECS80`. Not
  registered: `RECS80-0045` (the same IRP as `RECS80` minus the empty `{}`, so a second
  name for one waveform) and `RECS80-0090` (carrier `0k`, nothing to modulate).
- **Samsung36's `function` is the 12-bit value `E:F` (`E`·256 + `F`).** The IRP has
  four parameters, `D,S,E,F`; the schema gives an `irp` form exactly `device`,
  `subdevice` and `function`, with `additionalProperties: false`. The `E:4` nibble
  sits directly in front of `F` on the wire, so packing it above F's eight bits is
  contiguous and lossless. 269 of the 643 distinct Samsung36 codes have `E` ≠ 0 (the
  DB uses E = 0, 1, 3, 4, 7, 8, B, E, F), so refusing them would lose 42 %. This is the
  ledger's own packing: IrpTransmogrifier's `F` is the low byte only, so a future
  IRDB-style import of a Samsung36 `{D,S,E,F}` must pack `E` the same way. **It is a
  design decision the owner has not made.** If it is rejected, the one-line fallback
  is to raise `ValueError` for `E` ≠ 0 in `hex_misc.samsung36` and cut `FUNCTION_MAX`
  to 0xFF.
- **Samsung36's unit and extent, where the IRP disagrees with the app and with
  hardware.** Three sources agree against the IRP's 560 µs unit and `^108m`: the app
  (500 µs bits, 500/1500 spaces, a 500/4500 divider, a 59,000 µs lead-out,
  `samsung36.dart:39-43`); IrpTransmogrifier's eight real captures (every duration
  0.84 to 1.00× the encoder's, median 0.886, a unit of about 496 µs, and a frame
  period of 122.25 to 122.28 ms against `^108m`); and IRremoteESP8266's
  `sendSamsung36` (`crankyoldgit/IRremoteESP8266@1e2f0f3`, `src/ir_Samsung.cpp` L59-63
  and L175-190, "Works on real devices": a 4515/4438 µs header, 512 µs bit marks, 490
  and 1468 µs spaces, a 512/4438 µs divider, MSB first at 38 kHz). That supports the
  unit, and its authors call their own gap "just a guess", so it says nothing about
  the extent. The IRP's `4500u,-4500u` header is exact; only the `560`-based parts are
  off. The registry follows the IRP (gate 1 is "the IRP verbatim") and **the importer
  writes `unitUs: 500` with a `claims.unitUs` entry** (D52), the one place a database
  file overrides a registry unit. At 500 µs the compiled signal equals the app's to the
  microsecond but for the lead-out, and no field changes `^108m`, so the lead-out stays
  at 39 to 48 ms where the app and the hardware have 59 to 61 ms. At the registry's 560
  µs the oracle comparison passes **only at the boundary**: the IRP's marks are exactly
  12.0 % above the app's, as are the 1,680 µs one-spaces (against 1,500) and the 5,040
  µs divider (against 4,500), and the tool's limit is 12 % of the app's value,
  inclusive. `test_samsung36_at_the_registry_unit_is_exactly_at_the_tolerance` pins
  that, so it cannot slide unnoticed.
- **Proton, not Proton-40, by the carrier.** The IRPs are identical but for 40.5 kHz
  against 38.5 kHz, and the app's Proton carries 38,500 Hz. IrpTransmogrifier's Proton
  capture measured 37.7 kHz (a rough IrScope figure, closer to 38.5 than 40.5). A
  carrier is a `protocol.carrierHz` choice, not a second encoder, so this costs nothing
  if wrong.
- **F12: only the relaxed form is registered.** Strict `F12` is `((D:3,S:1,F:8,-80)2)*`,
  two frames per repeat unit; `F12-0` and `F12-1` are DecodeIR's `H` cases; `F12x` has a
  16-unit gap. The app's frame is one frame and its lead-out is **exactly** the IRP's:
  `-80` merges into the last bit's own space, giving 35,026 µs after a zero bit and
  34,182 after a one. The app's constant is `0xD300 = 54016` with a comment saying
  54000 (`f12_relaxed.dart:40`); 54016 is `(12×4+80)×422`, so the code is right and
  the comment is wrong. `extent_us` is `None` because `-80` is a plain gap. The app
  parses the hex as a number and pads to 12 bits, so the DB's 11 two-digit and one
  one-digit codes are right as they stand, and the map accepts one to three digits.
- **RECS80's toggle is not a form field** (D3b, as for RC5). Compiled output is
  `T=0`, the IRP's default. The app flips `T` on every press, so there is no single app
  signal, and the oracle's preview is always `T=1`. The ledger's own R13 cross-check
  *does* tell the two apart: an `irp` form and an app-raw form in one candidate group
  disagree at `repeat[3]` for every RECS80 code. For RECS80-0068 the extent makes the
  lead-out absorb the toggle's 16 units. **Hex bits the app drops**: the app uses the
  top nine of the hex's twelve, and the map treats a code with any of the low three set
  as unrepresentable, because two hexcodes would give one signal. None of the 555 DB
  codes has any.
- **Vectors.** All five are **reproducible only**, two renders each, the second setting
  every field to a different, asymmetric value so a swapped field or reversed bit order
  cannot pass; `test_registry` warns about each. Our timings reproduce every one
  exactly under the tool's own rule. **Gate 2a is stronger than NECx2's**, from the same
  repository @`c945e76`: for RECS80, a published decode assertion
  (`IrpTransmogrifierNGTest.java` L333-L336: eleven exact durations assert `RECS80:
  {D=6,F=56,T=1}`, reproduced to the microsecond) and a real capture (L348-L351:
  `D=2,F=1`, `T=1` then `T=0`); for Samsung36, `Samsung36.ict` (eight keys of a Samsung
  Blu-ray remote); for Proton, `Proton.ict` (nine keys); for F12_relaxed, `F12.ict`
  (eleven keys of a *strict*-F12 remote, whose frame is F12_relaxed's). The captures
  are cited, not vendored (a GPL-3.0 repository; the files are "used with permission
  of the author" there), and `tools/misc_capture_audit.py --irpt DIR` re-runs the
  comparison. Every key decodes, by a threshold decoder written from the frame layout,
  to the `.exp` fields and re-encodes to the same bit pattern: 8 of 8, 9 of 9 and 11 of
  11. Capture ratios (every duration but the lead-out, then the frame period) are
  Samsung36 0.84 to 1.00× and 1.13×, Proton 1.01 to 1.10× (median 1.063, a unit of
  about 532 µs against 500) and 1.01× (63.7 ms against `^63m`), F12 0.94 to 1.08× and
  0.99× (53.5 against 54.0 ms). **RECS80-0068 has no gate-2a evidence**: no capture,
  table or assertion was found for its 180 µs / 33.3 kHz clock, and the unit, the
  5,580/8,460 µs spaces and the 138 ms extent rest on the IRP string and a render alone
  (recorded as pending, with a reason, in `tests/vectors/index.json`).
- **The DB corroborates two bit orders against these captures**, independently of the
  app's code. Its Samsung BD remotes (ids 159, 2665, 5249) carry UP, DOWN, LEFT, RIGHT,
  OK, PLAY and REW at `0400E18`, `0400E98`, `0400ED8`, `0400E58`, `0400E38`, `0400E28`,
  `0400E48`, which are exactly rev8(D=32), rev8(S=0), rev4(E=7) and rev8(F=24, 25, 27,
  26, 28, 20, 18) for the capture's decodes (695 of the DB's Samsung36 keys start with
  `04`, which is rev8(32)). The Proton evidence is in D57.
- **Proven by mutation and sweep.** Each of the five encoders' raw durations equals
  IrpTransmogrifier 1.2.14's `render -r` for 140 parameter sets (28 per protocol:
  boundary values and a seeded random sample; a one-off, not committed), and mutating
  the encoders 14 ways was caught 14 times. Proton, F12_relaxed and both RECS80s round-trip exhaustively
  through an independently written decoder (65,536, 4,096 and 1,024 frames each),
  Samsung36 per field plus 20,000 seeded frames.
- **Not proven.** No hardware test of anything. RECS80-0068's clock (above). F12_relaxed's
  capture is of strict F12, and nothing here tests that a receiver accepts one frame.
  The `E:F` packing has no external reference. Whether the app's Proton bytes are really
  swapped for the receivers in the field rests on one capture of one remote and the
  structure of the DB. Not searched for a published Pronto string: DecodeIR's own test
  data, Girr, IrScrutinizer, IRDB.
- **Two stale statements in the code, for whoever next edits it.**
  `protocols/__init__.py`'s docstring gives Samsung36 as `{38k,500}...(9,-9,...)`,
  where the IRP is `{37.9k,560,33%}...(4500u,-4500u,...)`, and says the BN59-01199F's
  protocol is "open work" when it is NECx2 (D18, PR #7).

**D66 — Aiwa, Blaupunkt and the Kaseikyo family join: the three names no published
list contains.** Serves D18, D50, R3. The database's `REC80`, `RCC2026` and `RCC0082`
appear in no published protocol list, and the app that defined them says so
(`iodn/android-ir-blaster`'s `report-source.md`: "Evidence gap: no authoritative
public protocol definition was found", describing their encoders as "legacy
application behavior"). The working rule going in was that a protocol with no
independent reference is not registered. **All three turned out to be known
waveforms**, so it applied to none. Each pattern was compared with every one of
IrpTransmogrifier's 217 IRP definitions by carrier, unit, header, bit coding and bit
count, a mechanical grep over the IRP strings and not a name search. Names were
searched too and found nothing: no hit in the XML, the DecodeIR and
IrpTransmogrifier documentation, IRremoteESP8266, Arduino-IRremote or Flipper, and a
GitHub code search for `RCC2026` finds only SwiftRemote's upstream. The names are the
legacy app's; the waveforms are not new. Eight protocols are registered between them.

- **`RCC0082` is `Blaupunkt`** (IRP alternate name Motorola, `IrpProtocols.xml:441`):
  `{30.3k,512}<-1,1|1,-1>(1,-5,1023:10,-44,(1,-5,1:1,F:6,D:3,-236)+,1,-5,1023:10,-44)`.
  The app's pattern is a 22-duration sync (a mark, five units of space, ten one-unit
  marks, a long gap), a `1,-5` frame with ten biphase bits and a gap of about 210
  units, and the same sync again, at a 528 µs unit and 30.3 kHz; rendering
  `Blaupunkt D=0,F=16` with the 1.2.14 release gives the same shape run for run. The
  app's transition coder (`rcc0082.dart:75-88`) is a biphase coder whose hex 1 is the
  IRP's 0, and the first of its ten bits is a fixed dummy. All 300 codes map (D 0 to
  7, F 0 to 63) and match. Anchors from IrpTransmogrifier's real-remote
  `Blaupunkt.exp`: D=2, Play 10, Pause 11, Stop 12, Ch- 20, Ch+ 21 are the DB's
  PLAY, PAUSE, STOP, P-, P+ at `574`, `174`, `674`, `6B4`, `2B4`.
- **`RCC2026` is `Aiwa`** (`IrpProtocols.xml:223`):
  `{38.123k,550}<1,-1|1,-3>(16,-8,D:8,S:5,~D:8,~S:5,F:8,~F:8,1,-42,(16,-8,1,-165)*)`.
  The header 8800/4400, the unit 550, the 42 bits, a 23,100 µs gap and a header-only
  tail of 8800/4400/550/90750 are the app's numbers to the microsecond. The bits are
  the same ones upstream's own test calls NEC42's `D:13,~D:13,F:8,~F:8`, with `D13 = D
  + 256·S`. Matching them to NEC42 (Flipper, upstream's own test) would have been a
  shortcut that was wrong: the waveform is the same, but Flipper's NEC family is
  9000/4500/560 and has no 550 µs variant, where IrpTransmogrifier's `Aiwa` is exact to
  the microsecond. The reading is D57's.
- **`REC80` is six protocols, not one.** The waveform is the Kaseikyo/AEHA frame: 37
  kHz, a 432 µs unit, an `8,-4` header, 48 bits `<1,-1|1,-3>`, a stop mark and a
  173-unit (74,736 µs) gap. The DB hexcode is the 48 wire bits, first bit the top bit
  of the hex, so each IRP byte is the bit reversal of the hex byte (which is why
  Panasonic's vendor bytes `02 20` show up as `4004` at the front of every Panasonic
  code, the 0x4004 IRremoteESP8266 calls Panasonic's manufacturer code). The first two
  bytes sort the 2,723 codes into exactly six vendors, each an IrpTransmogrifier
  protocol:

  | Bytes 0-1 | Ledger protocol | `IrpProtocols.xml` | Codes | Keys | Remotes |
  |---|---|---|---:|---:|---:|
  | `02 20` | Panasonic | L1939 | 1,687 (+9 bad) | 11,875 (+9) | 236 |
  | `54 32` | Denon-K | L511 | 440 | 1,355 | 32 |
  | `14 63` | Fujitsu | L929 | 60 (+111 refused) | 92 (+161) | 5 |
  | `43 53` | Teac-K | L2830 | 70 (+62 refused) | 587 (+180) | 14 |
  | `AA 5A` | SharpDVD | L2500 | 114 (+126 refused) | 382 (+131) | 8 |
  | `03 01` | JVC-48 | L1219 | 44 | 44 | 1 |

  The line numbers are the 1.2.14 release's (at `c945e76`: Panasonic L1958, Teac-K
  L2849, SharpDVD L2519, the rest the same). Arduino-IRremote `src/ir_Kaseikyo.hpp`
  @`6158d65` L113-L117 publishes the vendor IDs 0x2002 Panasonic, 0x3254 Denon,
  0x5AAA Sharp and 0x0103 JVC, which are the same bytes little-endian. **No REC80
  remote mixes two of the six**, so R3's one-protocol-per-file rule is met without
  splitting any remote: 236 Panasonic, 32 Denon-K, 14 Teac-K, 8 SharpDVD, 5 Fujitsu, 1
  JVC-48, and 7 with no representable key. The mapping is corroborated beyond the
  checksums. IrpTransmogrifier's teaser tests decode real remotes: Panasonic D=176 S=0,
  Audio F=51 and Angle F=144 are the DB's AUDIO and ANGLE; Fujitsu D=132, Power 0, Vol+
  32, Vol- 33, Menu 64 are the DB's POWER, VOL+, VOL-, MENU; Denon-K D=4 S=1, Up 27, Down
  28, Left 29, Right 30; Teac-K D=0 S=4, Power 0, Vol+ 32, Vol- 48. `probonopd/irdb`
  lists SharpDVD D=8 S=48 (digits 1 to 9 are F 1 to 9, 0 is F 10, Up 32, Down 33, Left
  34, Menu 27, Enter 28), Denon-K D=2 S=1 and Teac-K D=0 S=4, and the DB agrees code
  for code. All are pinned in `tests/test_irblaster_unknown.py` (`ANCHORS`).
- **Registered:** the eight, from IrpTransmogrifier's `IrpProtocols.xml`, verbatim and
  identical across the 1.2.14 release and `c945e76`, apart from line numbers; D18's
  table has the strings. **Not registered:** `Kaseikyo` (generic: its `D` is four bits
  and its last byte a parity pair, so it cannot carry Panasonic's `D:8`, and 343 of the
  1,696 Panasonic codes have a byte that is not a four-bit `D` plus the vendor parity);
  `Kaseikyo56`, `Panasonic2`, `JVC-56` and `Fujitsu-56` (56 bits; no DB code is);
  `Aiwa2` (the same frame, no tail); `Mitsubishi-K` (no REC80 code has its vendor
  bytes).
- **Decisions in the encoders.** *Fixed gaps are not extents*: none of the IRPs writes
  `^`, so `extent_us` is `None` for all eight, and D31 supplies a gap only through
  `defaultGapUs` for a truncated capture, as for D64's four. *Intro and repeat follow
  IrpTransmogrifier's render.* `(...)*` with no intro makes the whole frame the repeat
  (Panasonic, JVC-48, Fujitsu, Denon-K, SharpDVD, as NECx2 and Sony20 already are);
  Teac-K is the frame, then `(8,-8,1,-100)*`; Aiwa is the frame, then `(16,-8,1,-165)*`,
  the NEC1 shape; **Blaupunkt is the unusual one**: `intro` is the sync plus one frame
  and `repeat` is the frame, because that is what IrpTransmogrifier's Pronto does for
  a bare `+`. *Parameters the IRP defaults and a form cannot name are fixed at the
  default* (Fujitsu `E`=0, Teac-K `X`=1, SharpDVD `E`=1), and a code that needs another
  value is unrepresentable (D67). Fujitsu's `S=D` default is not assumed: `subdevice` is
  required, as NEC1's is. *Blaupunkt's closing sync is dropped*: `IrSignal.ending` is
  reserved (D1), and IrpTransmogrifier drops it too, with a warning. The ending is the
  opening sync, so the oracle tool re-adds it from the intro when it compares with the
  app, which sends all three parts. A ledger player sends no closing sync, and whether
  a real Blaupunkt receiver minds is not known. *The IRP's unit and carrier, not the
  app's* (D60).
- **Gates.** Gate 1 and gate 3 for all eight: `tests/test_kaseikyo.py` (six
  protocols), `tests/test_aiwa.py`, `tests/test_blaupunkt.py`. Blaupunkt is swept
  exhaustively (512 frames); the others sweep every field completely against awkward
  fixed values and add 20,000 seeded random frames, because Panasonic's 16 M and
  Denon-K's 1 M frames are too many for pure Python; each decoder is written from the
  IRP's layout, not from the encoder. Separately, 200 randomly chosen and boundary
  parameter sets across the eight, rendered by the 1.2.14 release (`render -r`), match
  the encoders' intro and repeat: 200 of 200. **Gate 2b is reproducible for all eight and
  published for none**: `render -n … -p <protocol>`, our timings reproducing each vector
  word for word under the tool's rule. IrpTransmogrifier's tests assert decodes of
  captures, not Pronto strings it generated. Two published Pronto strings exist and
  cannot serve, because they are captures that cannot reproduce under an exact rule:
  `GRAHAM_PANASONIC` (`IrpTransmogrifierNGTest.java` L29, decoded at L581 as Panasonic
  `{D=176,S=16,F=17}`; a shorter gap and carrier word `0x71` against our `0x70`) and
  `Fujitsu_pronto.txt`; both are used for gate 2a. **Gate 2a is met for six.** Panasonic
  has two sources: IRremoteESP8266 `src/ir_Panasonic.cpp` @`1e2f0f3` L28-L35 and
  L104-L112 publish the timings (3456/1728, 432, 1296, and a 74,736 µs gap, exactly 173
  units) and the layout (a 16-bit manufacturer, device, subdevice, function, XOR), and
  our frame reproduces it exactly; and `Panasonic.ict`, which IrpTransmogrifier decodes
  as `{D=176,S=0,F=54}`. The others are teaser captures with the project's decodes:
  Aiwa `Aiwa_left.ict` `{D=8,S=0,F=21}`, Blaupunkt `Blaupunkt.ict` key Ch+ `{F=21,D=2}`,
  Teac-K `Teac_0_4_Input.ict` `{D=0,S=4,F=19}` (which carries the shorter `8,-8` repeat
  as well as the frame), Denon-K `Denon-K_Denon.ict` `{D=4,S=1,F=28}`, and Fujitsu
  `Fujitsu_pronto.txt`, a Pronto capture, `{D=132,F=0}`. They are copied, one frame
  each, to `irpt-teaser-captures.json`; instrument bias means they verify layout and
  ratios (every duration within 12 %, 150 µs), not absolute durations.
  **Gate 2a is pending for `JVC-48` and `SharpDVD`**, whose own byte layout has none:
  searched in IrpTransmogrifier's test resources, IRremoteESP8266, the DecodeIR
  documentation, Flipper and Arduino-IRremote. Two things exist. Arduino-IRremote
  corroborates the vendor bytes and the shared frame (L99-L106: the 432 µs unit, the
  8/4-unit header, 1/3-unit bits), and Flipper's `infrared_protocol_kaseikyo_i.h` says
  the same. `probonopd/irdb` lists both by name with device codes (JVC-48 for JVC
  receivers and CD players, device 34; SharpDVD for the Sharp RRMCGA030WJSA, device 8,
  subdevice 48) and its SharpDVD keys agree with the DB code for code, which
  corroborates the *parameter mapping* and not the waveform. Both share Panasonic's
  frame, whose waveform is verified. What is not verified is the layout after the vendor
  bytes (JVC-48's is Panasonic's, SharpDVD's is not) and, for SharpDVD, its 400 µs unit,
  38 kHz carrier and 48-unit gap. **Blaupunkt's 40 differing words are one rounding
  case, not an error**: at 30.3 kHz a 512 µs unit is 15.51 cycles, which the tool rounds
  to 16 (528 µs on playback) and D6 rule 4 to 15 (496 µs). They land 3 % either side of
  512, and every unit-length run differs.
- **Where the app and the reference differ.** (1) RCC2026's bit window (D57). (2)
  **REC80's lead-out.** The app ends every frame with 173 units (74,736 µs), Panasonic's;
  the IRPs say Fujitsu 110, Teac-K 100 and SharpDVD 48 (at a 400 µs unit): 244 codes,
  1,061 keys. Everything else in those frames is within tolerance, the ledger follows the
  IRP, and this is the only place a final duration is outside the tolerance
  (`LEADOUT_DOCUMENTED` in the tool restricts it to those three protocols). (3)
  **SharpDVD's unit**: the IRP has 400 µs at 38 kHz, the app sends 432 µs at 37 kHz;
  every duration differs by 7.4 %, inside the tolerance, not corrected, and the importer
  writes the IRP's 38,000. (4) **RCC0082's constants**, none beyond 9.1 %: the IRP's unit
  is 512 µs, the app's 528 (3.0 %) and `Blaupunkt.ict`'s about 532; the IRP's sync gap is
  45 units (23,040 µs), the app's 40 (21,120 µs, 9.1 %) and the capture's 20,568 µs; the
  IRP's frame gap is 236 units (120,832 µs), the app's 210 or 211 (110,880 and 111,408
  µs, 8.9 to 9.0 %) and the capture's 121,635 µs. So the app's sync is closer to the
  capture than the IRP is, and its frame gap is about 9 % short of both. The ledger
  follows the IRP; `protocol.unitUs` would carry the app's 528, with a `claims` entry
  (D24, D27), and none is set. (5) **IRremoteESP8266 spells the bytes the other way
  round.** Its Panasonic `device` is the bit reversal of the IRP's `D`, the same waveform
  under different parameter names; a reader comparing the DB's `4004 0D00…` with a
  published Panasonic table should expect this, and `tests/test_kaseikyo.py` shows the
  reversal rather than hiding it.
- **Not proven.** No eight-protocol vector is published. JVC-48 and SharpDVD have no
  gate-2a evidence for their own byte layout (above), and SharpDVD's 400 µs, 38 kHz and
  48-unit gap are the IRP's, unverified. Whether any receiver minds Blaupunkt's missing
  closing sync, or the app's 9 % shorter frame gap, is untested. **The three DB names
  are the legacy app's labels and nothing more**: no source was found for why a legacy
  database labels a Panasonic, Denon, Sharp, Fujitsu, Teac and JVC bag "REC80" (a web
  search summary describes the older Panasonic "REC-80" as a roughly 22-bit code, but
  that page, `users.telenet.be/davshomepage/panacode.htm`, was unreachable and has not
  been read) or an Aiwa frame "RCC2026". The tool's Python ports of the three app
  encoders reproduce the oracle for all 4,254 codes, but the Dart oracle was not run
  again, and SwiftRemote itself was not run.

**D67 — What the registry still refuses.** Serves R19.5, D54. Every refusal is a
skipped key with its reason in `IMPORT.md`, and each has one cause: the code is not a
frame of any protocol the registry holds. 964 distinct codes, 2,066 keys.

| DB protocol | Codes / keys | Reason | What would hold it |
|---|---|---|---|
| `NEC` | 406 / 1,076 (53 remotes) | Byte 4 is not the complement of byte 3 | `NEC1-f16`, unregistered |
| `NEC2` | 229 / 434 (18 remotes) | The same | `NEC2-f16`, unregistered |
| `REC80` | 126 / 131 | `SharpDVD`-shaped (`AA 5A`), but the IRP's fixed `15:4` nibble is not 15 (byte 2 is `B0` in 67, `84` in 21, others in the rest) | No published definition of this layout; none was invented |
| `REC80` | 69 / 94 | Fujitsu with `E` = 8 or 9; `E` is an IRP parameter but a form has only device, subdevice and function | A form that can name `E` |
| `REC80` | 62 / 180 | Teac-K whose check byte is not `D+S:4:0+S:4:4+F:4:0+F:4:4`; in 51 of them it is the constant `01` whatever `F` is, so a different layout under Teac-K's vendor bytes and not a bad checksum | Unknown |
| `REC80` | 42 / 67 | Fujitsu with the fixed `0:4` nibble set to 1 | Unknown |
| `REC80` | 9 / 9 | Panasonic whose check byte is not `D^S^F`; all start `400405…` (D=0xA0, S=0x08), most likely typos in the DB | |
| `RCC2026` | 21 / 75 | All device D=102, S=0 (the `6604CF…` group): `~S` is 23 where it should be 31, and in 20 of them `~F` is not `F`'s complement either. Real Aiwa remotes with D=102 S=0 appear in IrpTransmogrifier's `Aiwa2_Aiwa.exp`, so the device is real and these codes are probably corrupt | |

The 635 NEC and NEC2 codes carry DecodeIR's own list of fourth-byte variants
(hifi-remote.com/johnsfine/DecodeIR.html, "Variant IRstreams in NEC protocols", retrieved
2026-10-02). In NEC / NEC2: byte 4 unrelated to byte 3, 235 / 201; complementing bits 0 to
6 only (Yamaha style y1), 62 / 27; bits 1 to 7 only (y2), 50 / 0; the complement with the
nibbles swapped (style rnc), 46 / 1; bits 1 to 6 only (y3), 13 / 0. The refusal text names
the pattern, and y1 to y3 are what IrpTransmogrifier's `NEC1-Yamaha` and `NEC-Yamaha`
hold, rnc is `NEC1-rnc`. All four NEC ledger protocols spell the fourth byte `~F:8`, so
none of the other three is an alternative for a code NEC1 cannot hold; **registering
`NEC1-f16` and `NEC2-f16` (a gate-2 task of its own) would recover all 635**. Whether the
"unrelated" ones are real `-f16` devices or mis-conversions cannot be said from the data.

**D68 — Gate 2b accepts a reproducible vector where none is published, and says so on
every run; the reference quantizer has a tie it gets wrong.** Serves D10, D18. Of the 28
registry protocols **six have a published gate-2b vector** (NEC1, Sony20, RC5, NEC2
weakly, Sony12, RC6) and **22 have one only a pinned IrpTransmogrifier release can
generate**: NECx2, NECx1, Sony15, RCA-38, Thomson7, Pioneer-2Part, JVC, Sharp, Denon,
Samsung36, Proton, F12_relaxed, RECS80, RECS80-0068, Aiwa, Blaupunkt, Panasonic, JVC-48,
Fujitsu, Teac-K, Denon-K and SharpDVD. `test_registry` warns about all 22 on every run
and names them, as it did for NECx2 alone before. §12 lists each vector.

What that does and does not establish should be said plainly. Gate 1 takes each IRP
string from IrpTransmogrifier's database, and a reproducible vector is that same tool
rendering that same string. So for these 22 the vector checks that **our encoder
implements the IRP**, and not that the IRP is right. What stands between that and a
wrong constant is gate 2a (captures and published constant tables, §18's per-family
paragraphs) and the duration-for-duration renders (136 for D64's four, 140 for D65's
five, 200 for D66's eight), none of which is a Pronto vector. **Three protocols, `RECS80-0068`,
`JVC-48` and `SharpDVD`, have no gate-2a evidence either**, so their only evidence beyond
the IRP string is a render by the tool that holds it. §10's first row says "if a vector
can't be sourced independently, don't ship the protocol". That rule is not met for those
three. They are in the registry, with the reason recorded in `tests/vectors/index.json`
(`gate2a_pending_reason`), and they are the weakest entries in it; the owner may prefer
to remove them until a source appears.

**The reference quantizer.** `tests/reference_quantizers.py`'s `irpt` rule rounds in
exact decimal, half up; IrpTransmogrifier computes `Math.round(0.000001 * us *
frequency)` in doubles (`IrCoreUtils.java:180-182`, `Pronto.java`'s `pulses`), so a
duration exactly half-way between two cycle counts can round either way. Two cases were
found. Thomson7 D=12 F=74 T=1: a 34,500 µs gap is 1138.5 cycles at 33 kHz, and the tool
gives `0x472` where the rule says `0x473`. Proton at 38.5 kHz with a 25,000 µs gap is 962.5
cycles: the jar renders `03C2`, `irpt()` predicts `03C3`; of Proton's 17 possible lead-out
gaps, 8 are ties and the jar rounds three down (17,000, 21,000 and 25,000). It is one cycle
(26 µs) and not a timing disagreement, but it would fail a gate-2b check for no real reason.
The vectors were chosen to avoid ties (Thomson7 uses `T=0`, Proton D=20 F=1 and D=18 F=53),
and the shared helper was **not changed**. A faithful fix is `math.floor(0.000001 * t * f +
0.5)`.

> **TODO for the integrator.** Decide whether to fix `reference_quantizers.irpt`. It is a
> code change in a shared test helper and outside the documentation work.

### Findings for the app owner

Nothing in SwiftRemote was changed or tested on hardware. Each finding below is a
measurement of the app's behaviour against the data, an independent reference, or its own
comments, and each is a candidate fix on the app's side. Items 1 to 8 change what a
user's device receives. Items 9 to 15 are wrong comments, dead code, behaviour that
stays inside the tolerance but that the owner may want to know about, and one bug in
the data's own history.

| # | Where | Finding | Scale |
|---|---|---|---|
| 1 | `db_button_import.dart:246-285` (and `ir_finder_models.dart:274-287`, `power_params.dart:86-99`) | Reads a Sony hexcode as a packed integer, `cmd \| addr << 7`, where the data is in wire order (D57). A fix: read the DB hex in transmission order, reversing the frame's bits after dropping SONY15's pad bit, at least on the DB-import path in `_deriveProtocolFieldTextFromHex`. The editor's packing is deliberate and tested (`sony12_protocol_test.dart:7-33`); the import is the path that is wrong | 2,838 of 2,858 distinct Sony codes |
| 2 | The same, SONY15 | Masks the integer to 15 bits without a word, so 362 codes with bit 15 set transmit a different code than they spell | 362 codes |
| 3 | `pioneer.dart:96-123`, `jvc.dart:53-66` | Send each byte least significant bit first; the data is in wire order | 1,667 + 1,020 codes |
| 4 | `sharp.dart:65-67` | Masks with `0x1FFF` and reads a register layout the data does not have | 586 codes |
| 5 | `denon.dart:77-80` | Takes its thirteenth bit from `substring(3, 4)` where the data has it at `substring(0, 1)`; every code whose fourth digit is 8 or E loses its command's top bit | 332 of 519 codes |
| 6 | `thomson7.dart` L51-105, and the IR-finder profile `ir_finder_search.dart` L285-290 | Sends `last4 + toggle + first7` where the data and a hardware capture are `first4 + toggle + last7`. **The encoder contradicts its own mask `0xF7F`**, which clears bit 7, the toggle's place in the layout the data has and a meaningless bit in the layout the app then uses; one remote's 29 keys come out as thirteen devices and two commands. The finder profile treats bits 4 and 7 as unimportant, the same mistake. (Its description at L6-10 says "last4 + toggleBit + first7", which is what it implements, so the comment and the code agree and both are wrong) | 29 of 29 codes |
| 7 | `proton.dart:58-59,75,82` | Sends the low byte first, "last 8 bits first, separator, first 8 bits"; the IRP and the capture send the device byte first | 7,300 keys across 187 remotes |
| 8 | `rcc2026.dart:64-67` | **A stale copy of upstream.** It builds the 42 wire bits from the last 42 of a 44-bit number (`bin.substring(bin.length - 42)`); `iodn/android-ir-blaster` fixed this on 2026-09-20 in commit `3bb60e3178` ("fix: preserve all 42 RCC2026 database payload bits"), to `bin.substring(0, 42)`, with a test that pins the bundled code `38863BD42BC` as NEC42-layout D13=284, F=10. SwiftRemote's copy is the V2.0.0 original (`c0658e8`). `rec80.dart` and `rcc0082.dart` are byte-identical to upstream's, so only this file drifted. The fix is upstream's three changed lines. Two bits are shifted for every code; a valid Aiwa frame results for only 71 | all 1,231 codes (4,880 keys) |
| 9 | `nec.dart` L42, `nec2.dart` L42, `necx1.dart` L41, `necx2.dart` L40 | `targetUs = 0x1A580; // 108800`, and the descriptions say "pad to 108800us". `0x1A580` is **107,904**. The comments are wrong, not the waveform, which is 0.09 % from the IRP's `^108m` | every NEC2, NECx1 and NECx2 code (7,761) |
| 10 | `necx1.dart` L43-61, `encodeToggleFrame` | "Not used automatically", and wrong if it were: the repeat bit is `(firstByte & 1) == 1 ? 3T : T` with `firstByte` = rev8(D), where the IRP's `~D:1` is the complement of D's bit 0. For D=44 it would send a one-unit space where the IRP and the capture have three | nothing calls it |
| 11 | `db_button_import.dart` L14-L27 → `ir.dart` L390-L398 | Any DB `NEC` code is sent by the legacy path (38,000 Hz, 67 durations, no lead-out, no repeat). The app's own `nec` encoder (38,222 Hz, 562/1687, a gap to 107,904) is unreachable from the DB | 33,522 NEC codes |
| 12 | `nec2.dart` | "identical builder to NEC in this implementation": one frame, no repeat, so the app does not implement what separates NEC2 from NEC1. Harmless at `minSends` 1 | |
| 13 | `f12_relaxed.dart:40` | The constant is `0xD300 = 54016` and the comment says 54000. 54016 is `(12×4+80)×422`, so the code is right and the comment is wrong | |
| 14 | `recs80_l.dart` | Its description says "Same bit string as RECS80", which reads like a carrier change; it differs in carrier, unit and ending (D65) | |
| 15 | The NEC hex parsing (the notes do not record the file) | `int.tryParse` then `& 0xFFFFFFFF` for `NEC` sends the low 32 bits of a nine-digit code, which is how `807F42BD0` was sent as `07F42BD0` until SwiftRemote's `b78cb5d` ("fix malformed NEC database code") changed the data. No code in the database has nine digits now | |

Not bugs, but places where the app and the references differ and the ledger followed the
reference: the NEC family's rounded timings (within 0.71 %); the one constant gap per
protocol where the IRPs pad to an extent (D64); Pioneer's, Denon's and Sharp's units (D64);
RC6's six-unit final space (D63); RCA-38's and Thomson7's units (D63); Samsung36's unit and
59,000 µs lead-out, where the app agrees with hardware and the IRP does not (D65);
REC80's use of Panasonic's 173-unit gap for the three vendors whose IRPs say otherwise
(D66). Sony's encoders never read `_repeat`, so a press sends three frames and each
hold-loop tick three more, which agrees with `minSends` 3.

### Reproducing the evidence

With `IRBLASTER_ORACLE` set to the oracle directory (the tools default to it) and
`swiftremote.sqlite` from the SwiftRemote checkout:

```
python tools/irblaster_oracle_nec.py                       # NEC, NEC2, NECx1, NECx2 (--file tests/fixtures/irblaster/<p>.json for the subset)
python tools/irblaster_oracle_sony.py --oracle $IRBLASTER_ORACLE --db swiftremote.sqlite
python tools/irblaster_oracle_philips.py --oracle $IRBLASTER_ORACLE
python tools/philips_capture_audit.py --irpt <IrpTransmogrifier @c945e76> --db swiftremote.sqlite
python tools/irblaster_oracle_japan.py [--reading wire]    # the default, app, is the proof of the encoders
python tools/irblaster_wire_order_japan.py --db swiftremote.sqlite --oracle $IRBLASTER_ORACLE
python tools/irblaster_oracle_misc.py
python tools/misc_capture_audit.py --irpt <IrpTransmogrifier @c945e76>
python tools/irblaster_oracle_unknown.py --oracle $IRBLASTER_ORACLE --db swiftremote.sqlite
python tools/irblaster_oracle_import.py --checkout <SwiftRemote> [--list-differences]
```

The vectors that are renders were generated with
`java -jar IrpTransmogrifier-1.2.14-jar-with-dependencies.jar render -n D=…,F=… -p
<protocol>` (and `render -r` for signed microseconds); the command for each is
recorded in `tests/vectors/index.json`. The teaser captures are cited, not vendored,
and the audit tools re-run them from a checkout of IrpTransmogrifier at `c945e76`.

### What is not proven (the protocols)

- **No hardware.** Nothing was sent to a receiver. For ten protocols the ledger holds a
  reading that differs from the app's on evidence that is published decodes, real LIRC
  frames and structure (D57); a remote that mixes conventions cannot be excluded for
  SONY12 and SONY20.
- **22 of 28 gate-2b vectors are generated, not published**, and three protocols have no
  gate-2a evidence either (D68).
- **The wire reading's weakest parts** are Sharp's complement-frame inversion and Denon's
  `11` flag (D64).
- **Samsung36's `E:F` packing** has no external reference and is awaiting the owner (D65).
- **The toggle** (D3b) is open for five protocols, and an `intro`-plus-`repeat` `minSends`
  is undefined in D3a (D60).
- **The importer's licence footing** is inheritance only (D46).

## 19. Indexing the imported database

The IR Blaster import (section 17) adds 10,013 remotes to a corpus of about 3,200. Its `controls` strings alone are 306,633 and 63% of what an index of it would weigh, and `build/index.json` is what the SwiftRemote app in the field downloads on every store search. This section records how the index, the page and `rl lookup` were changed so that nothing a shipped client sees grows, and R20's three states still hold. The code is `src/remote_ledger/index.py` (`build_all`, `Shard`, `shard_files`, `inputs_record`, `load_committed`), `generators.py` (`run_index`), `site.py` (`shard_script` and the page's script) and `cli.py` (`cmd_lookup`). Numbers are from the full import in a scratch copy of the tree (13,217 remotes), measured with `gzip -9` where gzip is named; GitHub Pages' own compression level was not measured.

### The problem, as measured before the change

The import adds 10,013 remotes to the ledger's 3,204. `build/index.json` went
from 1,251,008 bytes to 15,075,980 (1,791,023 gzipped), `site/index.json` is a
copy of it, `site/index.html` embeds it and went from 1,006,251 to 11,537,234
bytes, and `rl lookup`, which rebuilds the index from the files on every call,
went from 2.5 s to 12.0 to 15.7 s. The one reader this repository cannot see
made the first of those the real constraint: the SwiftRemote app in the field
downloads `https://remote-ledger.github.io/index.json` on every Remote Ledger
store search (cached for twelve hours), parses it on the main isolate, and
keeps the whole of it in memory. It reads `schemaVersion`, `remotes` (the
fields manufacturer, model, artifact, aliases, controls, keyCount, confidence,
protocol, importedFrom) and `unresolved`, and ignores any other key
(`remote_ledger_index.dart`, `RemoteLedgerIndex.fromJson`). Gzipped, the file it
fetches today is 86 KB; unsharded it would have been 1.79 MB, twenty times that,
and 15 MB of JSON to decode on a phone.

### D69 -- The imported database is indexed in shard files; index.json keeps what it had

**What moves.** The entries of every remote under a root listed in
`paths.SHARDED` (today `remotes/irblaster/`, shard name `irblaster`) leave
`index.json`. Everything else stays exactly as it was: the same entries, the
same order, `unresolved`, `schemaVersion: 1`. The table is explicit rather than
a size threshold, so a regeneration cannot move a root by itself: adding a root
to it (or removing one) changes `index.json` for the app in the field, and
should be done on purpose. The root stays in `paths.IMPORTS` too; sharding
changes where an entry is listed, not what it is.

**What index.json gains.** One additive key, and only while a shard exists:

```json
"shards": [
  {"name": "irblaster", "path": "index/irblaster/manifest.json",
   "remotes": 10013, "root": "remotes/irblaster/"}
]
```

`path` is relative to `index.json`'s own URL, so it means the same in `build/`
and in `site/` and the two copies stay byte-identical (D20). The key is omitted
when no root has remotes, so **a corpus without an imported database, which is
`master` today, indexes byte for byte as before: nothing differs, not even an
empty `shards`.** With the full import `index.json` is 1,251,172 bytes against
1,251,008 (+164 bytes, +60 bytes gzipped: 86,311 against 86,251). The whole of
that is the advertisement and the longer count in it. `schemaVersion` stays 1,
which is honest: the field is additive and no reader of version 1 reads it. A
future breaking change to an entry would be a new version; this is not one.

**The manifest and the parts.** The advertisement points at a manifest, not at
the parts, so that how a shard is split never costs the app anything: the
advertisement is 164 bytes in `index.json` however many parts there are.

```json
// build/index/irblaster/manifest.json  (and site/index/irblaster/manifest.json)
{"name": "irblaster", "remotes": 10013, "root": "remotes/irblaster/", "schemaVersion": 1,
 "parts": [{"key": "a", "path": "a.json", "remotes": 749, "bytes": 646426}, ...]}
// build/index/irblaster/a.json
{"remotes": [ <index entries> ], "schemaVersion": 1}
```

A part's entries have **exactly the shape of `index.json`'s `remotes`** (they
are the same `summarise` output, so the shape cannot drift), in the same order,
so one parser serves both; a part is a valid index with no `unresolved`. Part
paths are relative to the manifest. `bytes` is the part's size as written, so a
client can say what it is about to fetch; gzipped sizes are not recorded, since
they depend on the compressor and a generated file must not.

**The split: by the first letter of the manufacturer, 28 parts, decided by
measurement.** `shard_key` is the first ASCII letter of the manufacturer
(lower-cased), `0` for a first digit, `_` for anything else; leading ASCII
punctuation is skipped, and only ASCII is looked at, as D49's key folding is, so
a part does not depend on the interpreter's Unicode tables. Measured on the full
import, with `gzip -9`:

| split | files | total gzipped | largest gzipped |
|---|---|---|---|
| one file | 1 | 1,686,913 | 1,686,913 |
| 4, 8, 16, 32 equal chunks of the sorted list | 4 to 32 | 1,688,619 to 1,700,175 | 546,169 to 163,325 |
| **by initial** | **28** | **1,698,104** | **366,698 (`s`)** |

Splitting costs under 1 % in transfer (deflate finds little across 10,013
entries that a smaller window does not), so the choice is about what else it
buys. By initial a remote stays in its part until its manufacturer changes, so a
regeneration rewrites only the parts whose brands it touched and a client that
holds the others keeps them; equal chunks would shift every later boundary
whenever one remote was added. A part is at most 3.2 MB (the `s` part has 1,867
remotes) where the whole is 13.8 MB, which matters to a phone that decodes JSON
and to GitHub's file viewer. The rule is not guaranteed to stay balanced (it is
not a hash); if a part ever grows past a few MB, a two-letter key is a change to
`shard_key` and nothing else, because the manifest names every part.

**What a split does not buy, and a client must not assume.** A free-text search
over this data cannot skip a part. The imported entries' `controls` name devices
of other makers than the entry's own (`TELEFUNKEN/286-RC5` lists 6,268 models of
many brands), so a query for "Sony" matches entries in every part, and a client
that fetched only the `s` part would answer "not in the ledger" about devices
that are in it. R20 would be broken by an optimisation. So the rule for any
reader is: **consult the index and every part before saying a device is not in
the ledger.** The parts exist so that a client can show results as they arrive,
cache them separately, revalidate only those that changed, and decode one at a
time (filter, then drop: peak memory is the largest part, not the whole). A
client that browses by manufacturer can fetch one part.

**Where the files are, and who owns them.** `build/index/` is owned by the
`index` stage, which now owns two paths (`Generator.also_owns`, so the owner
table, the `.gitignore` mirror and D11's dirty-path guard follow from the
registry; `.gitignore` gains `!build/index/`). `rl build --check` diffs it with
the rest of the tree, so drift, a missing part and an orphan part are caught
with no code of their own (`tests/test_index_shards.py` proves each). The stage
removes `build/index/` before it writes it, so a part whose last remote has gone
is deleted rather than left to be reported as an orphan; the site stage does the
same for `site/index/`. The site holds the manifest and the parts as JSON,
byte-identical to `build/` (for clients: Pages serves only `site/`), and a
script copy of each part for the page (D71). Raw and gzipped, on the full import:

| | raw | gzipped |
|---|---|---|
| `index.json` | 1,251,172 | 86,311 |
| 28 parts, JSON | 13,826,204 | 1,698,104 |
| 28 parts, the page's scripts (compact JSON) | 10,015,255 | 1,625,528 |
| the manifest | 2,875 | 525 |

The shard is committed twice more than `index.json` is (JSON in `build/` and in
`site/`, scripts in `site/`): about 38 MB of text, 5 MB gzipped, against the
641 MB the import already adds.

### D70 -- rl lookup reads the committed index when it is the index of these files

`rl lookup` called `build_index(root)`, which loads every remote file, on every
call. It now loads `build/index.json` and every part its `shards` advertises,
merges them in the order an unsharded index would have had (`merge`, so a lookup
prints what it printed before), and searches that. R20 holds across the shard by
construction: the three states are decided from the one merged index, so a
device named only in a shard remote's controls reads "in the ledger", the
middle row still comes from `unresolved.json`, and absent still means absent
(`tests/test_lookup_shards.py`; on the full import `Sony BDP-BX510` is still
"checked, nothing found" and `BDP-S360` and `TELEFUNKEN` print byte-identical
output before and after, compared with `cmp`).

**Freshness is a content digest, written by the generator.**
`build/index/inputs.json` holds `{"files": N, "schemaVersion": 1, "sha256": ...}`:
SHA-256 over every `remotes/**/*.json` in corpus order and then
`unresolved.json`, each as its path, its length and its bytes
(`inputs_record`). `rl lookup` recomputes it (0.13 s for the core's 3,205 files,
0.48 s for the full import's 13,218, warm cache, 0.59 s with the index and parts
loaded; cold-disk reads were not measured; `pathlib`'s `resolve` and
`relative_to` had cost more than the hashing until they were replaced by string
work), and trusts the committed files only if it matches and the manifest, the
parts and the advertised counts agree. It is content, not mtime: a checkout
resets every mtime, so a clone could not tell fresh from stale, and an edit
within the same second would be missed. Otherwise `rl lookup` prints one line on
stderr saying why (`note: the remotes or unresolved.json differ from what build/
was generated from; rebuilding the index from the files (`rl build` refreshes
it)`) and rebuilds in memory as it always did, so the answer is right and only
slow. The digest does not cover the generator's code: a changed `summarise` with
unchanged remotes gives a fresh-looking, stale index until `rl build` runs, which
is what `rl build --check` in CI exists to catch. `keys_for` is unchanged: the keys
of the remotes a lookup matches are still compiled from their files, so a lookup
never shows a code the committed tree has not caught up with (D40).

**The cost to know about.** The digest is one global value in a committed file,
so any two branches that edit remote files conflict in `build/index/inputs.json`
(one line). The resolution is `rl index` (15 s on the full import, not a full
build), which rewrites it and the index; `rl build --check` fails any merged tree
where it is wrong. A per-directory digest would merge cleanly (about 2,000
lines, one per brand directory) and was not worth its size until that conflict
is seen to hurt. The alternative to a committed digest, trusting a clean `git
status` of `remotes/`, needs git and cannot see an index that was committed
unbuilt; mtimes cannot survive a checkout.

**Timings** (one core, this machine, warm cache, `rl lookup <query>` wall time,
including interpreter start, 0.1 s):

| corpus | query | before | after |
|---|---|---|---|
| core (3,204) | `BDP-S360` | 2.51 s | 0.27 s |
| core | `DX3 Pro` | 2.55 s | 0.27 s |
| core + 304 imported | `BDP-S360` | 3.76 s | 0.35 s |
| full (13,217) | `BDP-S360` | 12.9 s | 1.18 s |
| full | `Yamaha RX-V385` (absent) | 12.0 s | 1.15 s |
| full | `TELEFUNKEN` (3.3 MB printed) | 15.7 s | 5.16 s |

The last row is dominated by compiling the keys of every matched remote, which a
broad query pays however the index is read. `search` also normalises each field
once instead of twice (it is 709,500 regex calls on the full import, and most of
the remaining time); it was checked equal to the old implementation over 490
queries (every maker and model in a sample, their prefixes, pairs, and the
punctuation-only and empty cases) on the real merged index.

### D71 -- The page embeds the core and loads the shard when the visitor searches

`site/index.html` embeds `index.json`'s entries, `unresolved`, the import table
and a description of the shards (`{name, root, remotes, parts: [{key, remotes,
script}]}`, 2.7 KB), and nothing of the imported remotes: 1,012,616 bytes
(90,288 gzipped), where the unchanged code gives 11,537,234 (1,737,619). The
committed master page is 1,006,251, so the new script and the part list cost 6.4 KB.

**Scripts, not fetch.** The first time the visitor types something with a letter
or digit in it, the page appends one `<script src="index/<name>/<key>.js">` per
part. This is D40's reason and keeps D15's promise: a page opened from disk
still finds the imported database, where `fetch` fails from `file://`. The
price is the script copy of each part, 10.0 MB (1.63 MB gzipped), which the
visitor downloads once, on the first search, in 28 parallel requests; with
`fetch` the JSON copy that clients need would have served the page too, and
offline use of the imported database would have been lost. I kept the promise.
A part script is `ledgerShard(...[name, key, entries]);` in the encoding of
`ledgerRemote` (compact JSON, `ensure_ascii`, so U+2028 cannot end a string
literal, D29).

**What the page says, and R20.** The page never says "nobody has looked up this
device" until every part has answered:

- Before the first search the status line says `3204 remote(s), 1 recorded as
  checked-and-not-found. 10,013 more are in the IR Blaster database (as shipped in
  SwiftRemote), searched when you type`, and nothing is requested.
- While parts are loading it says `Still searching the IR Blaster database ...: 12
  of 28 files loaded`, shows what matches in the core and the parts that have
  arrived, and, if nothing matches yet, `Nothing yet for "q" in the part of the
  ledger loaded so far; still searching ...`, which is not the absent message.
- A device in a shard reads as in the ledger: its card, with its import label,
  appears as soon as its part arrives.
- A part that fails (network, a missing file) is `did not load in full (1 of 28
  files missing)` with a Try again button, and a query that finds nothing says
  that this does not show that nobody has looked up the device. Failure is never
  read as absence.
- When every part has loaded and nothing matches, the absent message is the one
  it always was.

**Behaviour changes to know about.** The list-capped note moved from a paragraph
under the cards into the status line (`Showing 200 of 3204; refine ...`), so a
part arriving changes one line, not the cards. A part arriving that adds a card
redraws the list, and the remotes the visitor has opened are opened again
(`opened`, reset when the query changes); one that adds nothing does not redraw.
Fields are normalised once per remote and cached on the entry (`_n`), and the
query is compiled once, because 306,631 regex calls per keystroke is not
interactive: in V8 (node 22) running every part script takes 181 ms, normalising
once 163 ms, a search 2 to 30 ms, and the imported entries hold about 53 MB of
heap. The page's search rule is `lookup.normalise`'s, unchanged
(`test_the_page_normalises_queries_as_lookup_does` still holds).

**How the page was checked.** `tests/page_harness.js` runs the page's own
script, taken from the generated `index.html`, in node against a stand-in for the
DOM that runs the `index/` and `r/` scripts the page appends, and
`tests/test_site_page.py` asserts what a visitor reads at each step, including
with one part unavailable (skipped when node is not installed; CI's ubuntu image
has it). A mutation that lets the page say "absent" while parts are loading fails
two of its tests. Separately, in scratch, the generated page was driven in jsdom
30 on node 22 (real `<script src>` injection, from `file://` and from a local
http server, a blocked part, Try again, opening a shard remote from its `r/` script;
34 checks over http). That is not a browser: see below.

### The other changes, and one pre-existing bug

- **`rl index --check` and `rl site --check` could never pass.** They regenerate
  only their own stage into a temporary tree and then diffed it against every
  registered path, so everything else read as an orphan (the first line of the
  output on `master` is `build/warnings.json: orphaned`). `diff_tree` takes the
  stages to compare, and both commands pass their own. `rl build --check` was
  right and is unchanged.
- `rl index` and `rl site` write the shard files with the rest of their stage.
  The index stage runs the per-remote summaries once; the site stage runs them
  again, as before (about 12 s each on the full import, against minutes for
  compile); the fast-build work may fold the two.
- The `Generator` dataclass gained `also_owns` and `paths`; `owned_paths()` takes
  an optional list of stages. `tests/test_seed_data.py` and
  `tests/test_review_regressions.py` changed to match (`build/index` in the owner
  table, `g.paths` in the `.gitignore` mirror test, and `build/index.json` and the
  shard files in the committable-paths test).

### What was verified, and what was not

On the full import in a scratch copy, one `rl build` (the only full one; 17.1
minutes and 676 MB peak, against 18 minutes and 736 MB before: not a claim that
it is faster, the machine was busy with other runs): it succeeds;
`build/pronto/`, `site/r/` and `build/warnings.json` are byte-identical to the
tree the unchanged code wrote (`diff -r`); the new `index.json` is the old one
with the imported entries removed and `shards` added, and the 28 parts together
are exactly the 10,013 removed entries, in index order (compared entry by
entry); the shard files are byte-identical to those of an independent run of
the index stage in another process; the JSON under `site/index/` equals
`build/index/`; `rl index --check` passes. `rl build --check` was **not** run
on the full tree (it would be a second 17-minute run). It was run with 0
differences on the committed corpus (3.6 minutes, D11's guard active in the git
checkout) and, after a fresh `rl build`, on the core plus 304 imported remotes,
which exercises the shards through `diff_tree` end to end. On the committed
corpus (no import) `build/index.json` and `site/index.json` are byte-identical
to `master`'s, as are `build/pronto/`, `site/r/` and `build/warnings.json`; what
differs is `site/index.html` (the new page script, +4.5 KB) and the new
`build/index/inputs.json`. On the core plus 304 imported remotes `index.json`
differs from `master`'s by exactly the eight lines of the advertisement. The
suite: 2,317 passed, 9 skipped, 1 failed
(`test_documented_test_count_is_current`, DESIGN.md's count against the suite's;
70 tests were added) against 2,246 passed and 2 failed before, the second being
the island test that the regenerated page fixes.

Not verified:

- **Any real browser.** jsdom is a DOM in node, not a rendering engine: no layout,
  no browser's script scheduling or cache, and no phone's speed. The V8 timings
  are desktop numbers; a phone is several times slower on 10 MB of script. Safari
  and Firefox were not tried. The page already needs optional chaining and
  `\p{L}` regexes, and now also `Element.append` and `remove`, which every
  browser that has the first two has.
- **GitHub Pages' compression and headers.** The gzip figures are `gzip -9`; Pages
  is assumed to gzip JSON and JS, and to allow cross-origin reads, which a future
  web client would rely on.
- **The Android app.** I read `remote_ledger_index.dart`; I did not run the app.
  The claim that it is unaffected rests on its reading three keys, and on `shards`
  being a fourth that `fromJson` never touches. It no longer sees the imported
  remotes at all, which it ships anyway as its own IR database.
- **The page under 10,013 remotes of `r/` scripts opened at once**: nothing opens
  more than 200 cards, and only 8 automatically.
- **Concurrent edits.** The conflict in `inputs.json` is argued, not observed.

### Consequences for earlier decisions

D13's index gains an additive `shards` field, present only when a sharded import exists, and the shard files; D15's page embeds the core index and loads the shard on search; D19's owner table has `index` own `build/index.json` and `build/index/`; D20's site copies of the shard are byte-identical to `build/`'s; D40's list of what an import changes now includes the index and the site, not only per-remote files; SPEC R16's lookup reads the committed index when it is the index of the files in the tree, and R20's third state holds across the shard, with a pending or failed load not counting as that state. Nothing in `remotes/` or the schema changed for this.

## 20. Building the corpus in minutes, with the same bytes

The IR Blaster import (section 17) made the corpus 13,217 remotes and 525,084 keys, and `rl build --check`, which CI runs on every push, took 17 minutes. **D72** says what the time went on and what was changed in the arithmetic and the schema check without changing an output byte; **D73** records the build driver's worker processes. The code is `src/remote_ledger/parallel.py` (new) and small edits to `numeric.py`, `pronto.py`, `signal.py`, `validate.py`, `cli.py`, `generators.py`, `index.py` and `site.py`; the tests are `tests/test_build_speed.py`. Paths are relative to the repository; the numbers are this machine's, measured on the code as it was before (`df9ce03`), one core each.

**D72 -- The hot loops were made cheaper first.** The time was 69% in `compile_group`, 88% of that in `pronto.quantize`, and 20% in `jsonschema`; the subsections below show the profile and each change.

**D73 -- The per-remote loops of every stage run in worker processes, in input order.** Worker count is `--jobs`/`-j N`, else `RL_JOBS`, else `min(usable CPUs, 8)`; `1` forces the serial path for debugging; the output does not depend on the count (R12), and `tests/test_build_speed.py` says so. After it, `rl build --check` and `rl build` take about 1.5 minutes on four cores and the suite with the data about 1.5 to 2 minutes. D40's earlier "3.5 minutes" and section 17's "18 minutes" describe the code before this section.

### The problem

After the SwiftRemote import the corpus is 13,217 remotes and 525,084 keys, and
`rl build --check`, which CI runs on every push, took 17 minutes on this machine
(`rl build` 17, `rl validate remotes/irblaster` 2, the suite with the data 3).
The importer measured 18 minutes; the numbers below are this branch's own
measurements of the code as it was (`df9ce03`), one core each.

### Where the time went

cProfile over a 500-remote sample (379 imported from the IR Blaster database, 121 others),
before any change, 93 s under the profiler:

- **69 % in `Remote.compile_group`**, 88 % of that in `pronto.quantize`. Every
  duration of every signal went through `us_to_cycles`, a Decimal division
  wrapped in *two nested* `decimal_context()` entries, each of which constructed a
  fresh `decimal.Context` and, as a generator-based context manager, a
  generator. A full build enters that context some 8 million times per 500
  remotes. And `compile_group` runs three times per key (`check_distinctness`,
  the compile stage, the site stage), plus once more in `check_key` for the
  comparison. Also in the same loop, `check_bounds` built its error message (an
  f-string) for every duration whether or not the bound was broken, and so did
  `IrSignal._check_sequence`.
- **20 % in jsonschema** (`Draft202012Validator.iter_errors`), of which a fifth
  was resolving `$ref`: a registry lookup and a new validator object per hop.
- The rest (loading, `load_forms`, serialisation) was a tenth. The loading is
  done about seven times per remote (validate twice, check, compile, index, the
  site twice), which is wasteful and is not worth removing: it is 4 s of 93.

On the full set, one core, with the changes of sections 1 and 2 below and
before any parallelism: `validate` 131 s, `check` 40 s, `compile` 43 s, `index` 12 s,
`site` 47 s (build_index again, then the scripts), `diff_tree` 4 s: 276 s in
all. Validation is 47 % of that, and about four fifths of it is jsonschema.

### What was changed, and why none of it is observable

**1. The arithmetic (commit "Stop building a Decimal context...").**
`numeric.decimal_context` is now a small class around one pinned `Context`
built at import (`decimal.localcontext` copies it on entry, as it already
copied the Context it was handed before; the template is never made current, so
its flags stay clear). `pronto.quantize` keeps, per frequency word, a table of
the cycle counts `us_to_cycles` has returned (the same function, so the same
values; bounded at 50,000 entries per word, dropped when exceeded, because a
raw capture may hold any duration); the bounds checks and
`IrSignal._check_sequence` take a fast path for the passing case and build their
message only when the check fails. The test compares `quantize` with
`us_to_cycles` called directly on 8 carriers and ~900 random durations, cold and
warm, with the table limit forced to 5, and checks that a duration that rounds
away is still refused when it comes from the table. Sample: 39.7 s to 12.1 s,
zero differences.

**2. The schema (commit "Recognise a valid document with the schema's
references inlined").** `validate.schema_problems` first asks a copy of the
schema with every `#/$defs/<name>` reference inlined (the schema has no
recursion; one `$ref` has a `description` sibling, which is an annotation) whether
the document is valid. If it is, nothing more is done. If it is not, the document
is validated *again by the schema as written* and that validator's errors are
what is reported, so the text of every message and the order they are sorted into
(the sort key is `str(error)`, which includes the schema the error came from,
and the inlined schemas differ) are exactly what they were. Inlining can make a
valid file cheaper and cannot change a word said about an invalid one. The only
way it could weaken a check is to accept a document the schema rejects; the test
compares the two on 3,900 mutants of two documents (every value replaced by 18
edge values, every member deleted, every object given an unknown member, every
member renamed to an illegal name, then 400 random combinations), and I
verified the test has teeth by breaking the inliner ten ways (a citation that
accepts anything, `hexOrInt` without its maximum, `additionalProperties`
relaxed on `key` and on `irpForm`, the `derived` tier dropped, the key-name
pattern dropped, ...): each is caught. That is a strong test, not a proof. The
validator is 30 % faster on a valid document (5.7 s to 4.0 s on 500 files).

**3. Worker processes (commits "Run the per-remote loops...", tests).**
Each stage is a loop over the remotes whose iterations are independent, so
`parallel.ordered_map(func, items)` runs `func` over the items in forked
workers and yields the results in the order of the *input*. validate
(`validate.validate_files`, used by `rl validate` and the first step of
`rl build`), `rl check` and the build's check stage, the compile stage, the
index (`index.build_index`, which is also what `rl lookup` calls) and the site
stage all use it. Decisions:

- **Order is the input's.** A bounded window of chunks (3 per worker) is in
  flight; the parent takes them from the front. The ERROR lines, the warnings,
  the index and every file are therefore the same whatever the scheduler does.
  An exception raised for an item is raised when that item's turn comes, so the
  first failing item in input order is the one reported, as in the serial loop.
- **Workers write their own files** in the compile and site stages (each remote
  is its own artifact and its own script), so nothing large travels back and
  the site stage no longer holds every remote's keys at once (that map is, I
  believe, most of the old 736 MB peak; I did not measure it alone). `site.payload` still returns the whole mapping (tests and
  any caller use it); its loop body is unchanged and is now
  `site.site_extra(root, files)`, which `build_site` calls one remote at a time
  from the workers.
- **Worker count**: `--jobs N` / `-j N` on any subcommand (it sits in the
  shared parent parser like `--strict`), else `RL_JOBS`, else
  `min(usable CPUs, 8)`. "Usable" is `len(os.sched_getaffinity(0))` where it
  exists, not `os.cpu_count()`: the brief's `min(os.cpu_count(), 8)` would start
  8 workers under `taskset -c 0-3` and in a container with a CPU mask, which is
  the situation CI is in (GitHub's runner is 4 vCPUs; `cpu_count()` there says 4,
  so the two agree on the runner and differ only under a mask). `--jobs 1` and
  `RL_JOBS=1` run the old in-process loop, with no pool created. A bad value is
  a `ValidationError` (`ERROR $RL_JOBS is 'x'; ...`, exit 1), or argparse's
  usage error for the flag.
- **An automatic count leaves a small input serial** (under 64 items): a pool
  costs more than a handful of remotes. An explicit count is always honoured,
  which is what lets the test compare serial and parallel runs on a corpus of
  22 files.
- **One pool per loop, not one for the process.** Workers are forked when the
  loop starts, so they see the current directory and module state of that
  moment (`Remote.where` is relative to the working directory; a pool kept from
  an earlier call, in a process that has since changed directory or patched a
  module, as the tests do, would be stale) and nothing outlives the call. It
  costs a few milliseconds a stage, and the workers' tables start empty each
  time, which costs less than a second a stage. `fork` is requested explicitly
  where it exists (the default start method becomes `forkserver` in Python 3.14);
  elsewhere the platform default is used and everything sent to a worker is a
  module-level function.
- A worker never starts a pool of its own; `main()` clears the count in a
  `finally`, so one call's `--jobs` cannot leak into the next call in the same
  process; the standard library flushes stdout before forking, so buffered
  output is not written again by the workers (a test asserts it).

### What was not touched, on purpose

`compiled_artifact` (cli.py), the per-key entry in `site.site_extra`, `summarise`
(index.py) and `load_remote` are untouched, because the label change is
being made there. `diff_tree`, `Generator`, the registry, the order of the
stages, `--check`'s refusal on a dirty owned path (D11) and the exit codes are
untouched. The two loops I edited in index.py and site.py are the places a merge
with the index-shard change will meet mine: `build_index`'s loop became
`for summary, problem in ordered_map(partial(_summary_of, root), corpus_files(root))`
(the body is `_summary_of`, unchanged), and `site.payload` is now two lines
that call `site_extra` (the old body, with `for path in files:`). If the shard
split writes more than one file from the index, its writers can use
`ordered_map` the same way, with the worker writing the file.

### Proof that the output did not change

Every comparison below is of bytes (`diff -rq`, `cmp`, or `filecmp` with
`shallow=False` inside `rl build --check` itself).

- **Full tree, 13,217 remotes, 26,438 generated files.** `rl build --check`
  with the new code against the `build/` and `site/` the importer generated
  with the old code: 0 differences, at `--jobs 1`, 4 and 8 (and by default on 4
  and on 56 cores). `rl build` (write
  mode, `build/` and `site/` deleted first) at the default worker count and with
  4: `diff -rq` against what the old code's `rl build` wrote (which I
  regenerated myself, and which is itself identical to the importer's tree): no
  difference.
- **Committed master tree, 3,204 remotes.** `rl build --check` with the new
  code reports **one** difference, `site/index.html`, exactly as the old code
  does on that tree: `paths.IMPORTS` gained the IR Blaster entry on the
  integration branch, the page embeds it, and master's committed page does not
  have it yet. The new code's `rl build` writes the same `site/index.html` as the
  old code (identical to the byte), and after committing that one file
  `rl build --check` reports 0 differences, in a real git repository, so
  D11's dirty-path guard ran too. Everything else in master's `build/` and
  `site/` is byte-identical to what the new code generates.
- **Failures and drift are reported the same.** Four damaged copies of the full
  tree, each run with the old code and with the new at two to eight workers,
  stdout, stderr and exit status compared byte for byte (all the same): (A) five
  invalid files spread across the sorted corpus (a missing `protocol`, a
  duplicate form id, an odd-length raw sequence, a file that is not JSON, an
  empty `manufacturer`), `rl validate` and `rl build`; (B) derived forms gone
  stale in three files and a raw form disagreeing with its irp form, `rl check`
  (4 errors and the 129 `carrier-off-nominal` warnings) and `rl build --check`
  (one of the three stale files fails *validation*, so the build stops there);
  (B2) the same without that file, so that the build's check stage is the one
  reporting (3 errors); (C) a clean corpus and a tampered generated tree: an
  artifact changed, one deleted, an orphan directory under `build/pronto/`, an
  orphan script, a changed script, a changed `build/index.json` and
  `build/warnings.json` -- seven differences, the same seven, in the same order.
  So `--check` detects every drift and orphan it detected, and `validate` runs
  every rule on every file.
- **Small corpora in the suite** (`tests/test_build_speed.py`): `build` writes
  the same tree at 1, 2, 3 and 8 workers; `build --check` agrees with the tree
  either way and reports drift, orphans and a missing file identically;
  `validate`, `check` and `build` give identical output on a corpus with broken
  files; `ordered_map` keeps input order when later items finish first and raises
  the first failure in input order.

### Timings

This machine: 64 cores, 125 GB, shared with other agents (load average 4-6 while
these ran), Python 3.12.3, jsonschema 4.26.0. One run each unless a range is
given; the timing runs are not repeated enough for more than a rough figure; "4 cores" is `taskset -c 0-3` with the worker count left at its default,
which the affinity makes 4 (`--jobs 4` forced gave the same: 99.8 s against
102.9 s in the first pair, which is the machine's noise, see below). "64 cores"
is the whole machine, default, which is 8 workers. "1 core" is `--jobs 1` on one
core. Before is the code at `df9ce03`, one core.

| | before | 1 core | 4 cores | 64 cores (8 workers) |
|---|---|---|---|---|
| `rl validate remotes/irblaster` (10,013 files) | 123.5 s | 79.6 s | 28.0 s | 10.2 s |
| `rl build --check` (13,217 remotes) | 1,025.9 s | 280.0 s | 77 to 103 s | 43.8 s |
| `rl build` (13,217 remotes) | 1,036.7 s | 270.9 s | 75.2 s | 38.4 s |
| `rl validate` (committed master tree, 3,204) | 62.7 s | 52.0 s | 14.8 s | |
| `rl build --check` (committed master tree) | 216.1 s | 82.3 s | 23.6 s | 13.5 s |
| `rl build` (committed master tree) | 213.6 s | | 22.6 s | |
| the suite with the data, full tree | 179.5 s | 138.4 s | 96.7 to 108.1 s | 88.5 s |
| the suite, no data (this worktree's own tree) | 87.0 s | | | 48.8 s |
| `rl lookup BDP-S360` (full tree) | 14.1 s | | 4.4 s | |

The 4-core `--check` varied between 77 s (twice, back to back) and 103 s
(when other jobs were running on the machine); I would quote the upper figure.
Total CPU time at 4 cores is about 275 s of user time, against 1,019 s before,
so the single-core gain is about 3.7x and the four-core scaling about 3.5x of
that. Peak memory: before, 739 MB in one process. After, no process exceeds
165 MB (GNU time's maximum; sampling gave 141 to 153 MB), and together the
processes hold 216 MB at 4 workers and 230 to 244 MB at 8 (proportional set
size, which counts each shared page once; the sum of their resident sizes, which
counts it once per process, is 417 MB and 743 MB). The suite's own peak (960 MB) is the
test process, which the speed-ups did not change.

The three failures of the suite in the scratch copy with the data (the
documented test count; two tests that need a git repository and a `.gitignore`
the scratch copy lacks) are the same three as before; in this worktree two fail,
as they do at `df9ce03`: the documented count, and
`test_the_island_parses_and_carries_the_ledger`, which reads the committed
`site/index.html` whose imports map predates the IR Blaster entry.

### What could not be sped up

- **jsonschema itself.** After the inlining it is still about 40 % of the
  serial work (validate is 131 s of 276 on the full tree, about four fifths of
  it the schema), because it descends into 1,800 subschemas per remote at 4
  microseconds a descent in pure Python. The remedies I did not take, in the
  order of what they would save: a hand-written accept-only validator for the
  schema's own keywords (I estimate two thirds of the schema's time, not
  measured, and it is a second implementation
  of the schema that has to be kept equal to it: the differential test used here
  would be its guard, but I did not think it right to put an unrequested second
  validator in the path of `rl validate` without being asked); skipping
  `check_cache_content` for a file with no variants (provably the same, 5 % of
  validation, but it edits semantic code for a small gain); a per-process memo of
  the key specs seen (the `source` text is unique per key, so nothing repeats).
- **What is serial.** `diff_tree` (4 s: it walks and compares ~26,000 files),
  building and writing the 15 MB index and the 11.5 MB page (a few seconds), the
  second `build_index` that the site stage runs (3.7 s of CPU at 4 workers: it
  could reuse the index stage's result, but the stages are independent callables
  and sharing a result across them means state that outlives a call, which I
  preferred not to add), and the process start of each stage.
- **The suite.** With the data it is 96 to 108 s at 4 cores. The remainder is
  thousands of small tests and a few tests that load every remote one at a time
  from the test process (`test_a_resolved_device_leaves_unresolved_json`, 8 s,
  loads all 13,217 with `load_remote`). Those loops are the tests' own and I did
  not change them; they could use `ordered_map` too.

### Beyond the brief

- The worker count follows `os.sched_getaffinity`, not `os.cpu_count()` (above).
- One pool per loop rather than a persistent one (above).
- `rl lookup` is faster as a side effect (14.1 s to 4.4 s at 4 cores) because
  `build_index` is parallel; it is not a stage and I did not otherwise touch it.
- The inlined-schema accept path: the brief's list had "compile the schema
  validator once", which was already done (`lru_cache`); the cost was in
  jsonschema's per-hop work, not in building the validator.
- `rl check` (the per-file command) is parallel too, since it is the same loop.
  `rl compile`, `rl fmt` and `rl import` are not.
- `main()` clears the worker count when it returns.
- 25 tests were added (`tests/test_build_speed.py`). The test count that
  DESIGN.md section 12 asserts (`test_documented_test_count_is_current`) needs
  the integrator's recount; it fails at `df9ce03` already.

### Not proven, or not measured

- **CI timings were not measured**; there was no CI to run. A 4-vCPU GitHub
  runner is a slower core than this machine's, and may be shared; the figure
  to expect is this table's 4-core column scaled by that, not the column itself.
  The 4-core numbers are the 64-core machine constrained with `taskset`, with
  other jobs running beside them. The suite and `rl build --check` run in
  sequence in CI, so a CI job is their sum.
- The inlined validator is tested, not proven, to accept only what the schema
  as written accepts (3,900 mutants, ten injected faults all caught). If a
  future schema uses a reference the inliner leaves alone, jsonschema still
  resolves it, so the result stays right and only slower; a test fails if one
  appears, so that the loss is noticed.
- `fork` was the only start method exercised. Under `spawn` (Windows) the code
  is written to work (module-level functions, picklable arguments) but was not
  run.
- Python 3.12.3 and jsonschema 4.26.0 only; CI installs the newest jsonschema
  that satisfies `>=4.20`. A different jsonschema may change the time, not the
  result: whatever the inlined validator accepts is the schema's own verdict
  anyway, and what it does not accept goes to the schema as written.
- Output equality was shown on this corpus and on five damaged copies of it; it
  rests on the loops being independent per remote, which holds for every stage
  as the code is today. A stage that began to carry state from one remote to the
  next would need to stay serial, and `ordered_map` would not notice.

## 21. The app API

The SwiftRemote Android app reads the imported database through a static API instead of bundling its 51 MB sqlite (the owner's decision after §17). A fifth generated stage, `app`, writes `site/app/v1/` from the committed `remotes/irblaster/` tree (a pure function of it and of the hex modules, D75), `rl app [--check]` runs it alone, and `rl build --check` covers it (drift, missing files and orphans). It is published because it sits in `site/`, the only directory GitHub Pages serves. `index.json` is untouched: `build/index.json` and `site/index.json` are byte for byte what they were before the stage. The pipeline is now `validate → check → compile → index → site → app`. Consequences for earlier decisions: D19's owner table gains `site/app/v1` (owner `app`), and the site stage's `owns` gains `excludes`, so every generated file has one owner; D69's `diff_tree` stage selection takes the exclusion; the importer's exports `parse_citation`, `format_citation`, `split_controls_entry` and `app_reading_differs` are what the stage reads the tree with (section 17).

### D74 — The API is a stage of its own, `app`, which owns `site/app/v1`

Two ways were considered: write the API from inside the site stage, or add a
stage. A stage, because its input is different (the 10,013 files of one import,
not the corpus), its cost is its own (about 10 s, against the site's 47 s serial in D72), it
wants its own command (`rl app [--check]`, so that iterating on it does not build
a 335 MB site), and a stage's path is what D11, D19 and `rl build --check` already
know how to guard.

The consequence is an overlap that has to be settled: `site` already owns the whole
of `site/`. `Generator` gained one field, `excludes`, "paths under `owns` that
another stage owns", and the site stage has `excludes=("site/app/v1",)`. Each
generated file therefore has exactly one owner:

- **D19.** `owned_paths()` is now `build/warnings.json`, `build/pronto`,
  `build/index.json`, `build/index`, `site`, `site/app/v1`. `diff_tree` walks the
  stages and subtracts a stage's `excludes` from what it compares, so an orphan in
  `site/app/v1` is reported once, by the app stage (tested), and `rl site --check`
  and `rl index --check`, which regenerate one stage into an empty directory, do
  not call the API's files orphans (tested; the existing `diff_tree(..., stages)`
  contract of D69 would otherwise have broken `rl site --check` the moment the
  directory existed).
- **D11.** `_dirty_owned_paths` hands `git status` every owned path, `site/app/v1`
  now among them (tested with a stubbed `subprocess.run`). `rl build --check` was run
  in the real checkout with the tree clean: 0 differences.
- **`.gitignore`.** Nothing to add: its mirror of the owner table is `build/` only,
  and `site/` was never ignored. `test_the_app_files_are_not_gitignored` asks
  `git check-ignore` about three of the new paths.
- **No import, no API.** With no file under `remotes/irblaster/` the stage writes
  nothing and removes any `site/app/v1` (the index writes no shard then either,
  D69). A corpus without the import therefore generates the very tree it did
  before (tested with a one-remote corpus: `rl build --check` passes and there is
  no `site/app`).
- **The directory is the stage's.** It is removed before it is written, so a file
  the data no longer yields disappears instead of being reported as an orphan.
- **Order.** `check, compile, index, site, app`. The stage reads nothing the others
  write, so the order only fixes the message `validate -> ... -> app`.
  `generators.PIPELINE` says `phase=7`; nothing prints it.
- **`rl app`.** Prints one line (files, bytes, brands, models, remotes, keys, the
  protocols that have signal shards, the power rows, the count of keys the import
  could not represent, `dataVersion`) and writes; `--check` writes to a temporary
  tree and diffs only this stage's path.

### D75 — A pure function of the tree, and the importer owns the formats it reads

Inputs: the JSON under `remotes/irblaster/` and the two tables
`FROM_DB_HEX` / `FROM_DB_HEX_APP` of the hex modules. Not read: the SwiftRemote
checkout, the SQL dump, `build/`. The one thing read outside the JSON is
`remotes/irblaster/IMPORT.md`'s totals, only to *print* how many keys the import
could not represent (2,066), which the tree holds no trace of; no file depends on
it (`importer.report_totals`, tested on a rendered report and on the real one).

`importer.py` now exports the three readers of its own formats, so the API rests on
no regex of its own:

- `parse_citation(source) -> (db_id, label, hexcode, db_protocol)`, with
  `format_citation(...)` as the one writer (`Importer.document` uses it, so the
  importer's output is unchanged, byte for byte) and `citation_commit(source)`.
  The label is free text (quotes, spaces, tabs, `??`, a whole citation), so the
  regex takes it greedily and forces the tail: after the last `'` come a hexcode, a
  DB protocol, a phrase and a ledger protocol, none of which can hold a quote (the
  phrase is `[^']*`, a rule and not a habit). The last quote is therefore always the
  one the importer wrote, and a label cannot move the split. A lazy label with that
  tail is an equivalent mutant; a lazy label with a free-form phrase is caught by
  the tests, and so is splitting on the wrong quote.
- `split_controls_entry(entry) -> (brand, model)`, the inverse of `controls_entry`,
  on the **first** ` | ` (D56b).
- `app_reading_of` / `app_reading_differs`, D55's test as a function, extracted
  from `Importer.app_reading` (which now calls it).

`tests/test_irblaster_citation.py` runs the parser over **every key of the real
tree** (411,265, from 10,013 files, in about 4 s): the label it returns equals the
key's own `label`; the id equals the file name's; `format_citation` of the parts
returns the same string; and the hexcode and DB protocol are the ones the form's
`(protocol, device, subdevice, function)` read back from through `FROM_DB_HEX` --
which also shows each citation says what its form is. Zero failures. The oracle
tool and `test_irblaster_import.py` keep their own regexes on purpose: they are
oracles for the importer, and an oracle that shares code with what it checks proves
less.

A file the generator cannot read is a problem, not a guess, and the stage writes
nothing: a name that is not `<id>-<protocol>.json`; a key with other than one form;
a citation that does not parse; a label that is not the citation's; a citation of
another remote or another commit; a DB protocol the API does not know; a `controls`
entry with no separator; an id none of whose files has a `controls` entry (no brand
would list its keys); two files that disagree on a carrier or a `minSends`; two keys
of one code that compile to different Pronto strings; two brands with one key.

### D76 — The files, and where they differ from the spec

All JSON is compact UTF-8, keys sorted, one trailing newline (D20: no timestamp, no
path, nothing that differs between machines). Non-ASCII is written as itself (37,611
of the 2,176,891 key rows in the brand files have a non-ASCII label; two brands are
Cyrillic).

- `manifest.json`: `schemaVersion` 1; `dataVersion`; `source {name, commit}`;
  `counts {brands, models, remotes, keys}`; `protocols` (all 23, in the frozen order);
  `paths`; `playRules`; `power`.
- `brands.json`: `[[name, key, protoMask], ...]`. The key is the lower-case hex of
  the first **ten hexadecimal digits** of the SHA-1 of the UTF-8 name (the brief's
  "sha1(name)[:10]" read as a slice of the hex digest: 40 bits). No collision among
  the 4,902 brands (a collision is a problem and stops the stage; tested over the
  real tree).
- `b/<key>.m.json`: `{brand, hash, ids: [[id, protoMask, nKeys]...], models:
  [[model, [indexes into ids]]...]}`. `hash` is the first six hex digits of the
  SHA-256 of the `.k.json` bytes.
- `b/<key>.k.json`: `{brand, r: [[id, [[label, protoIdx, hex]...]]...]}`.
- `s/<DB protocol>.json`: only where `appReadingDiffers`: `{p, ledger, carrierHz,
  carrierHzByLedger, minSends, play, s: {hex: pronto}}`.
- `power.json`: `[[protoIdx, hex, label, nIds, rank]...]` (D79).

**The protocol order is a literal and never moves.** `app_api.PROTOCOLS` is the 23
names in ASCII-NOCASE order *today*; it is a tuple, not a sort, so the database
gaining a protocol appends a bit instead of renumbering every `protoMask` a client
has cached. A test fails until the new name is appended
(`test_the_protocol_order_is_frozen_and_complete`), and asserts the order is also
what `ORDER BY UPPER(protocol)` gives, which the old `listProtocols*` used.
`protoMask` is an integer; 23 bits fit the 32 a JavaScript or Dart integer may use.

**The orders are SQLite's, which are two different folds.**
`COLLATE NOCASE` (brands, models) folds ASCII letters to *lower* case and compares
the UTF-8 bytes; `UPPER` (keys) folds to *upper*, ASCII only, and BINARY compares the
result. They differ on `[ \ ] ^ _ \``, which sort before the letters under the first
and after the capitals under the second. `nocase_key` and `upper_key` are tested
against SQLite's own `NOCASE` and `UPPER` (including `ß`, `é`, `ı`, `ǆ`, and a
mutation that folds the other way or with Unicode). Ties are SQLite's to leave to
chance; here they break on the exact text, so the order is total: brands and models
by (folded, text), keys of an id by (UPPER label, UPPER protocol, UPPER hex, then the
`(label, protocol, hex)` itself), ids ascending. Android's SQLite may be built with
ICU, which would make its `UPPER` Unicode-aware; the brief says ASCII-only and so does
the tool's comparison, which uses Python's `sqlite3`. That is the one place the old
app's order for a non-ASCII label may differ from the API's; the API's is canonical
either way.

**Spec deviations, each deliberate:**

1. `dataVersion` is the first twelve hex digits of one SHA-256 over **every file but
   the manifest** (each as its path, a NUL and the SHA-256 of its bytes, in path
   order), not over `brands.json`. A change to a hexcode, a label or a signal leaves
   `brands.json` as it was (the per-brand hash is in `.m`), so a cache keyed on the
   brief's value would serve stale keys. It is a pure function of the other files.
2. `carrierHz` in the protocol table is the carrier, or **null** when a DB protocol's
   ledger protocols disagree, which only `REC80` does (Denon-K, Fujitsu, JVC-48,
   Panasonic, Teac-K at 37,000 Hz, SharpDVD at 38,000); `carrierHzByLedger` always
   carries each. None of the ten signal protocols has the problem.
3. `power.json` rows carry a fifth column, `rank` (D79).
4. Extra manifest keys: `source`, `playRules` (D78) and `power` (D79), and per
   protocol `carrierHzByLedger` and `play`. A protocol with no key in the tree is
   still listed (`ledger: []`, `minSends: null`, `appReadingDiffers: false`).
5. `counts.models` is the number of distinct `(brand, model)` pairs (279,447), as a
   model name under two brands is two models.

Brands with no key left, models only unrepresentable ids list, and ids with none are
absent, as the old queries' join on `keys` made them (D80 has the numbers).

How the app's seven queries map onto the files:

| old query | from the files |
|---|---|
| `listBrands(search, protocol)` | `brands.json`, already NOCASE-sorted; a protocol is a bit of `protoMask`; the search is the client's `LIKE '%q%'` on the name |
| `listModelsDistinct(brand, search, protocol)` | `b/<key>.m.json` `models` (NOCASE order); a protocol filter keeps a model if any of its ids has the bit |
| `listProtocolsForBrand` / `listProtocolsFor` | the OR of the ids' masks, for the brand or for one model's ids |
| `fetchCandidateKeys` / `countCandidateKeys` | the model's ids (from `.m`), their rows from `.k`; protocol equals; hex prefix and search over `hex` and `label`; order (rank, `UPPER(label)`, `UPPER(protocol)`, `UPPER(hex)`, id), which only the client can apply across ids. Every row is unique by `(id, label, hex, protocol)`: the old queries' repetition per model is gone |

All 2,176,891 hexcodes in the brand files are upper-case, so `UPPER(hex) LIKE 'P%'`
is a plain prefix test on the upper-cased query.

### D77 — `appReadingDiffers` is computed

`app_api.candidates()` is the set of DB protocols whose `FROM_DB_HEX_APP` entry is
not the same function object as `FROM_DB_HEX` (D53's contract). Their keys' signals
are compiled; then `importer.app_reading_differs(protocol, hexcode)` is asked of every
code of the protocol present in the tree, and the protocol differs if any code does
(the app's fields differ from the wire's, or the app's reading cannot send the code at
all). A protocol with no code present is false. Nothing is a table.

On the real tree it is **exactly** SONY12, SONY15, SONY20, Pioneer, JVC, Sharp, Denon,
Thomson7, Proton and RCC2026, as the brief expected (nothing to investigate). Tested
three ways: on the real tree; on the synthetic one against an independent comparison;
and by patching `FROM_DB_HEX_APP` (a wrapper returning the same fields leaves a
protocol undifferent and without a shard, and one differing code makes it differ).

`s/<DB protocol>.json` holds one Pronto string per code **present**, 9,378 in all
(Denon 519, JVC 1,023, Pioneer 1,673, Proton 1,476, RCC2026 1,210, SONY12 907, SONY15
738, SONY20 1,213, Sharp 590, Thomson7 29), each the ledger's own compile
(`Remote.compile_group` on a loaded file: forms, variants, protocol block and
encoder). **All 45,944 keys of these protocols are compiled**, not one per code, and
the generator stops if two keys of one `(DB protocol, hexcode)` give different
strings. They never do: zero conflicts, within files and across them. The test builds
every shard's strings again with the registry's encoder directly (no file, no form
loader) and compares.

### D78 — How one press is played: `play`

Each shard, and the manifest's protocol table, says how many times the app plays the
Pronto string's repeat sequence after its intro for one key press:

```
"play": {"repeatPasses": N, "helperRepeatPasses": M, "introEmpty": bool, "rule": "ledger" | "full-signal"}
```

**What a Pronto string's two sequences mean for playback.** Word 0 is `0000`, word 1
the frequency word (carrier = 1,000,000 / (word1 x 0.241246 us) Hz), **word 2 the
number of burst pairs of the first sequence, word 3 the number of the second**; the
durations follow, in carrier cycles, a mark then a space, `2 x word2` of them for the
first sequence and `2 x word3` for the second. The first is the **intro**, played
once; the second is the **repeat**, played `repeatPasses` times after it. A
zero-length intro (word 2 = `0000`) means the repeat sequence alone is the frame: Sony,
Proton and Thomson7 are that, and a press is the repeat sequence `repeatPasses` times.
This is `_tryParseProntoSequences` and `_remoteLedgerSends` of the app's
`lib/utils/remotes_io.dart`, read, not run.

**How `repeatPasses` is derived**, not written: `helperRepeatPasses` is what
`_remoteLedgerSends` plays for a ledger remote today -- the repeat `minSends` times when
the intro is empty, `minSends - 1` times after it otherwise. That is the rule of the
tool `tools/irblaster_oracle_import.py` too (`burst`). `repeatPasses` is that, raised
to the oracle's framing for the protocols the oracle plays as a whole signal, the intro
once and the repeat once (`FULL_SIGNAL`): **Sharp and Denon**, whose IRP puts the
complement frame in the repeat sequence, so that a `minSends` of 1 would send only the
first. For them `repeatPasses` is 1 and `helperRepeatPasses` 0, both stated, with
`rule: "full-signal"`. That set is `app_api.FULL_SIGNAL_PROTOCOLS`, and the oracle tool
imports it (a test pins `tool.FULL_SIGNAL is app_api.FULL_SIGNAL_PROTOCOLS`), so the app's
playback rule and the oracle's are one definition.

On the real tree (pinned in `test_how_one_press_plays_is_pinned_for_the_ten`):

| DB protocol | minSends | intro | helper | repeatPasses | rule |
|---|---|---|---|---|---|
| SONY12, SONY15, SONY20 | 3 | empty | 3 | 3 | ledger |
| Thomson7 | 2 | empty | 2 | 2 | ledger |
| Proton | 1 | empty | 1 | 1 | ledger |
| RCC2026 | 2 | present | 1 | 1 | ledger |
| Pioneer, JVC | 1 | present | 0 | 0 | ledger |
| Sharp, Denon | 1 | present | 0 | **1** | full-signal |

The intro's emptiness is a property of each signal, so the generator reads it from
every Pronto string of a protocol and **stops** if a protocol's signals disagree (none
does): a single `repeatPasses` could not then be stated. One correction to the
paraphrase in the brief: it said `max(minSends - 1, ...)`; for an empty intro the
helper plays `minSends` passes, not `minSends - 1`, and the table above follows the
helper.

### D79 — `power.json`

The rule is the app's `powerLabelRank` (`lib/universal_power/power_code.dart`, at
6aafd15), ported and tested against a second port written as the Dart is (a loop over
characters; `tools/app_api_vs_sql.py dart_rank`): fold the label to ASCII upper case,
every other run of characters one underscore, trimmed; exactly `POWER`, `PWR`, `OFF`,
`ON`, `POWER_OFF`, `POWER_ON`, `PWR_OFF` or `PWR_ON` is rank 0; a label holding
`POWER` or `PWR` and also `OFF` or `ON` is rank 0 (`ON` is a substring: `POWER BUTTON`
is rank 0, kept); `POWER` or `PWR` alone is rank 1; exactly `STANDBY` or `SLEEP` is
rank 1; rank 2 (the app's `secondary` set) cannot be reached, since each of its names
holds `POWER`. **Rank 0 or 1 counts**, the app's default depth of 2. The rule text is
in `manifest.json` `power.rule`.

One row per distinct `(protocol, hexcode)`, `[protoIdx, hex, label, nIds, rank]`:
`nIds` counts the distinct ids that use the code under a label of that rank, ordered by
`nIds` (most first), then hex, protocol index and label; the label is the best-ranked,
then most used, then first in `(UPPER label, label)` of those. The limit is 3,000 and
does not bind (`power.total` is 2,733; `rows` is what was written).

**The count disagrees with the brief's 2,757**, and the three numbers are all explained.
Over every row of the SQL dump the same rule gives **2,759** distinct codes: 2,757 of
them are found by the app's five search words (`power`, `off`, `pwr`, `standby`,
`sleep`), which is presumably where the brief's figure came from, and two more are
REC80 codes labelled `ON+` and `ON-`, which `powerLabelRank` ranks 0 (normalised, `ON`)
and the search words miss. The tree holds **2,733** because 26 of the 2,759 codes are
among the 964 the importer could not represent. The tool checks the 2,733 against the
SQL rows (`power.json` equals the rule over the held rows, in order, with each row's
label and rank).

For the app: of the 2,733 rows, 323 are on the ten signal protocols (all ten occur),
so a whole sweep needs all ten shards (4.2 MB, 103 KB gzipped) unless the rows carry
their Pronto string. That would add about 50 KB to a 73 KB file and save ten requests;
it was **not** done, since the brief fixes the row shape.

### D80 — Checked against the database it replaces

`tools/app_api_vs_sql.py` (not a unit test: it needs the SQL dump) loads the dump into
memory, runs the app's own queries on it, restricts them to the keys the ledger holds,
and compares with `site/app/v1`. A key is "held" unless its `(DB protocol, hexcode)` is
in `IMPORT.md`'s skipped lists (the importer's own record, so the generator's parser is
not what decides what is expected; it also checks those 2,066 rows are the dump's rows
of those codes and no others). `quickWinsFirst`'s `CASE` is the app's SQL, evaluated by
SQLite on each label, not a port. The full run (about 130 s):

| compared | result |
|---|---|
| brand list in `ORDER BY name COLLATE NOCASE` order | same, but the 3 brands below |
| per brand: models (NOCASE), with and without a protocol filter; ids of every model; protocols of the brand and of every (brand, model) | same, all 4,902 brands |
| per (brand, model, protocol): the key rows as a set | same, 306,068 triples |
| per brand: the literal `fetchCandidateKeys` SQL, `quickWinsFirst` true and false, in its `ORDER BY` | same, all 4,902 brands, ties compared as sets |
| the same, per (brand, model, protocol), a seeded sample | same, 3,000 triples |
| every `protoMask`, `nKeys`, `hash`, per-id key order, `counts` | consistent |
| `power.json` and the shards' code sets | same |

**Unexplained differences: 0.** The explained ones, each counted and each checked
against its cause:

| difference | count |
|---|---|
| keys the importer did not represent (rows of the dump) | 2,066 (964 codes) |
| ids with models and keys and no held key | 27 |
| (brand, model) pairs only those ids list | 655 (of 280,102) |
| brands only those ids list | 3 (`JVC TEAC`, `TVIX`, `XMHELLO`) |
| old brand-only listing rows (one per model and key) | see below |

The old queries' repetition: a brand-only listing returned one row per model and key;
over the whole dump that is 18,944,280 rows, and 2,181,228 after de-duplicating by
`(id, label, hex, protocol)` per brand. The API's brand files hold 2,176,891 of them;
the other 4,337 are the refused keys, once under each brand that lists them (every other
row matches, per brand, as above).

### D81 — Parallel, like the other stages

The loop over the 10,013 files (read, parse, compile the ten protocols' keys) and the
loop over the brands (render and hash their two files) run through
`parallel.ordered_map`: the results are the input's order, so the bytes do not depend on
the worker count (R12; tested at 1, 2 and 3 workers, and the real tree at 1 and 4 and
by default, byte-compared). The merge into ids and brands, the key sort per id, the
power list and the manifest stay in the parent. Workers return small per-file records
and rendered bytes (nothing is written by a worker: the stage writes in one place, so
that `--check` and the writer share one function and a test can build the API in memory).

Measured on this 64-core machine, where "4 cores" is `taskset -c 0-3` with the worker
count left at its default. Generation is `build_app_api` alone; `rl app --check` adds
writing 57 MB to a temporary tree and comparing 9,817 files:

| | 1 core, `-j 1` | 4 cores | 64 cores (8 workers) |
|---|---|---|---|
| generation | 10.7 s | 6.7 s | |
| `rl app --check` | 13.4 s | 9.7 s | 9.0 s |
| `rl build` | | 84 s | |
| `rl build --check` (0 differences) | | 89 s | |

It scales little because the parent's share (the merge, 411k key sorts, the power list)
is about 5 s of the 10.7. **Peak memory is the new maximum of the build**: 484 MB for
the largest process (`rl app --check` and `rl build`; 466 MB serial), against the 165 MB
D73 recorded for any process; the parent holds the 411,265 keys as lists, and their
rendered files. Nothing near a CI runner's limit. The first run, before the path
handling was made cheap (23,000 `resolve` calls were 7 of 26 profiled seconds), took
13.5 s serial and 10.4 s on 4 cores; the bytes were identical.

### D82 — Sizes, requests, and what is the app's to decide

Measured on the generated tree (`gzip -9`; level 6 differs by under 2%; GitHub Pages'
own compression was not measured):

| | files | raw | gzip |
|---|---|---|---|
| `manifest.json` | 1 | 5,491 B | 1,401 B |
| `brands.json` | 1 | 138,760 B | 62,455 B |
| `b/*.m.json` | 4,902 | 6,904,619 B | 1,992,848 B |
| `b/*.k.json` | 4,902 | 45,800,773 B | 10,725,820 B |
| `s/*.json` | 10 | 4,217,699 B | 103,473 B |
| `power.json` | 1 | 73,284 B | 14,230 B |
| **total** | **9,817** | **57,140,626 B** | **12,900,227 B** |

Per file, raw bytes at p50 / p90 / p99 / max: `.k` 2,110 / 16,053 / 131,699 /
2,436,695 (gzip 681 / 3,740 / 27,635 / 526,339); `.m` 143 / 1,294 / 15,582 / 604,348
(gzip 132 / 496 / 3,944 / 95,581); all files 880 / 8,141 / 94,511 / 2,436,695. Largest
brand shards (`.k`): BRAVO 2,436,695 B (526,339 gzipped), CM REMOTES 782,943, ZAPP
542,353, MURAT ELEKTRONIK 396,672; largest `.m`: PHILIPS 604,348, SAMSUNG 564,689.
Signal shards: Pioneer 1,761,877 B (27,805 gzipped), RCC2026 600,350, JVC 387,901,
Proton 323,436, Sharp 300,505, SONY20 291,312, Denon 264,366, SONY12 143,498, SONY15
139,674, Thomson7 4,780. `site/` is now 378 MB in all, 321 MB before (GitHub Pages' limit is 1 GB). An id
under several brands is written once per brand: 2,176,891 key rows for 411,265 keys.

**Against the brief's estimates**: they hold. `brands.json` is bigger (139 KB / 62 KB
gzipped, against 119 / 50), by the ten-digit key per brand, which does not compress;
`.m` is 6.9 MB / 2.0 MB (6.7 / 1.6); `.k` 45.8 MB / 10.7 MB (46 / 11); the largest
`.k` 2.44 MB / 526 KB (2.5 / 550); signals 4.2 MB / 103 KB (4.3 / 130); the total 57.1
MB in 9,817 files (57, 9.8k). Only the power count differs (D79).

**Requests** (each file one request; the app's seven queries never cross brands):

| flow | requests | bytes (raw / gzipped) |
|---|---|---|
| open the finder | 2: `manifest.json`, `brands.json` | 144 KB / 64 KB |
| choose a brand | 2: its `.m` and `.k` | median brand 2.3 KB / 0.8 KB; p90 17 KB / 4.3 KB; p99 146 KB / 31 KB; BRAVO 2.5 MB / 549 KB |
| models, protocols, filter, search, page, count | 0 | -- |
| send a key of one of the 13 protocols the app reads as the wire | 0: its own encoder | -- |
| send a key of a signal protocol | 1 per protocol per session: `s/<P>.json` | 4.8 KB to 1.76 MB / 0.4 to 27.8 KB |
| Universal Power, all brands | 1 (`power.json`), plus up to 10 shards to play the 323 rows on signal protocols | 73 KB / 14 KB; plus 4.2 MB / 103 KB |
| Universal Power, one brand | 2 (`.m`, `.k`) plus the shards of the brand's signal protocols | the brand's, plus the shards' |

Today the app carries 51 MB and makes none. 942 of the 4,902 brands have at least one
signal protocol.

**Not decided here, the app's to settle**: the client has to apply the `CASE` rank of
`quickWinsFirst` and merge a brand's ids itself, because only it knows the order; a
`quickWinsFirst` ranking uses `LIKE`, ASCII-case-insensitive, so Dart's
`toUpperCase()` on a non-ASCII label would rank differently from SQLite's; whether to
cache by `dataVersion` or by a brand's `hash`; whether to ask for `power.json` rows with
Pronto inline (D79).

### What is not proven

- **No client has read these files.** The Android app was not run, nor Dart written;
  `_remoteLedgerSends`, `_tryParseProntoSequences` and `powerLabelRank` were read, and
  the first two are not run here. `repeatPasses` is what that reading gives.
- **That the readings are right.** `appReadingDiffers` says where the app and the wire
  disagree; which is correct is D50's question (no hardware).
- **Android's SQLite.** Its `UPPER` and `NOCASE` are assumed ASCII-only, as the brief
  says; with ICU they would not be (D76).
- **GitHub Pages**: its gzip level, its headers, that a `b/` directory of 9,804 files is
  served without trouble. The 1 GB limit is respected (378 MB).
- **The filters the client applies** (`LIKE` search, hex prefix) are not in the
  comparison, which covers the rows they filter. All hexcodes are upper-case.
- **Sharp's and Denon's three frames** are `play` by the oracle's rule, which D64 calls
  the weakest part of those two readings.
- **The 2,066 keys the import refused** (964 codes, 27 ids' worth of nothing) are not in
  the app's new database: a loss against today's, by D54's rule, and not the API's.

---

## 22. The canonical key vocabulary

A source spells a key as it likes: `KEY_VOLUMEUP` in LIRC, `VOL+` or `Vol +` in the IR Blaster database (which D49 folds to `KEY_VOL_PLUS`), `volumeUp` in SmartIR. The ledger keeps the spelling and never rewrites it. This section adds the one list of *meanings* those spellings are mapped onto, so that anything that has to understand a key reads an id and not a spelling: a generated layout that groups the volume keys and puts the arrows in a cross, a standard icon for each key, the language a macro or a prompt uses ("volume up"), a comparison between two remotes. SPEC R22 states the requirement.

It adds: `keys.json` (the vocabulary: groups, canonical keys, and for each its display name, icon, glyph, colour and whether holding it repeats it), `aliases.json` (the spellings that mean each key), a schema for each, a validator, `keys.canonical_id(key_name, label)` (the mapping), and `rl keys report` (how far the mapping reaches into the corpus). It changes nothing that exists: no remote file, no importer, and not one byte of `build/`, `site/` or `site/app/v1/` (`rl build --check` reports 0 differences, as before). The vocabulary is **not** added to the app API: a new file there changes `dataVersion` and so every client's cache, which is a decision for the change that needs it. The files are read by code and written by hand, so no stage owns them (D19's owner table is unchanged).

### D83 — The vocabulary is ledger data: two hand-written files, a schema each, a validator, a version

**Where.** `src/remote_ledger/vocabulary/keys.json` and `aliases.json`, with `schema/keys.schema.json` and `schema/aliases.schema.json` beside the others. They ship as package data (`pyproject.toml` lists `vocabulary/*.json` as it lists `schema/*.json`) and `paths.VOCABULARY_DIR` finds them the way `validate.SCHEMA_DIR` finds the schemas: a consumer that installs the package has no checkout, and `canonical_id` must work for it. They are not repo-relative like `unresolved.json`, because the mapping is a library function before it is a command.

**The contract**, the shape downstream code depends on (the field order is the file's, and a test pins it):

```json
{ "version": 1,
  "groups": [ {"id": "power", "order": 1, "name": "Power"}, ... ],
  "keys":   [ {"id": "VOLUME_UP", "group": "volume", "order": 1, "name": "Volume up",
               "icon": "volume_up", "glyph": null, "color": null, "repeat": true}, ... ] }
```

- **`id`** is `UPPER_SNAKE_CASE` and never starts with `KEY_` or `BTN_`: those are how a source spells a key, not what it means. Ids are named for the meaning (`VOLUME_UP`, not `KEY_VOLUMEUP`), never for a source, and never for a device (`CD_PLAY` is not a key; it is `PLAY` on a CD player, D87).
- **`group`**, **`order`**: an order is unique within its group (not across groups), so a layout reads a group in order without sorting ties.
- **`icon`** is a Material Symbols name (snake_case) or null. **`glyph`** is up to eight characters of text, for a key shown as text (a digit, `CH+`, `OK`) or as a caption beside an icon. **`color`** is `red`, `green`, `yellow`, `blue` or null. **Every key has an icon, a glyph or a colour** (the schema's `anyOf`). A recommended reading, which the client decides: draw the icon when there is one, with the glyph as its caption; a key with only a glyph is text; a colour key is a dot.
- **`repeat`** says whether holding the key should fire it again. True for the keys one adjusts or moves with (volume, channel, the four arrows, page, bass, treble, zoom, brightness, tuning), false for anything that changes state (power, mute, input, OK, the transport keys, the digits): a finger resting on Power must not switch the set off again. 32 of the 156 are true.
- **`version`** is 1 and the schema accepts nothing else (`const`), as `schemaVersion` does elsewhere: a reader that sees another number refuses instead of guessing. Within a version an id is never renamed, removed or given another meaning, and a key, a group, an alias or a token may be added; anything else is version 2. A reader ignores ids it does not know and shows them in its "More" group, which is not in the file because it holds no canonical key.

**The groups.** Eleven are required (`power`, `volume`, `channel`, `navigation`, `numbers`, `media`, `input`, `color`, `menu`, `apps`, `other`; the validator requires them and no group may be empty) and three the data asked for: `sound`, `picture` and `teletext`. Teletext is the one that decides it: `TEXT` is on 4,613 of the 10,013 IR Blaster remotes (46%), `HOLD`, `REVEAL` and `SIZE` on more than 300 each, and `SUBPAGE` and `MIX` on about 175; in a "More" group they would be most of what that group holds for a TV that has them. `MENU` is in `menu` and not in `navigation`: `navigation` is the cross (`UP`, `DOWN`, `LEFT`, `RIGHT`, `OK`) and the ways out of it (`BACK`, `HOME`, `EXIT`, `CANCEL`, `PAGE_UP`, `PAGE_DOWN`); `menu` is what opens a screen (`MENU`, `SETTINGS`, `INFO`, `GUIDE`, `DISPLAY`, ...).

| group | keys | | group | keys |
|---|---|---|---|---|
| power | 4 | | menu | 13 |
| volume | 3 | | sound | 15 |
| channel | 12 | | picture | 17 |
| navigation | 11 | | teletext | 6 |
| numbers | 16 | | apps | 2 |
| media (named Playback) | 23 | | other | 4 |
| input | 26 | | color | 4 |

156 keys in all. **Choices the data or the layout asked for:**

- *`ENTER` is not `OK`.* They sit in `numbers` and `navigation`. In 37% of the LIRC remotes that have `KEY_ENTER` there is a `KEY_OK` too (229 of 612), so merging them would put two keys on one id in 229 files, and the layout could not tell the centre key from the confirm key.
- *`STANDBY` is `POWER`*, a toggle with the standby symbol; `POWER_ON` and `POWER_OFF` exist for a key that says on or off and for nothing else (D84).
- *`FORWARD` is `FAST_FORWARD`.* Of the 538 LIRC remotes with a `KEY_FORWARD`, 55 also have a `KEY_FASTFORWARD`; the others use `FORWARD` for the one fast-forward key, and so do 1,494 IR Blaster remotes (3 have both).
- *`SOURCE`, `AV`, `TV/AV` and `INPUT SELECT` are all `INPUT`*, the key that steps through the inputs. A key that selects *one* input is `INPUT_TV`, `INPUT_DVD`, `HDMI_2` and so on: `TV` is a source here (4,922 keys), not a device.
- *Apps are the generic ones only:* `APPS` and `BROWSER`. A streaming service is a brand, and a brand is never an icon here, so none is a key, an id or a glyph (a test searches for the common ones). `NETFLIX` and `YOUTUBE` are among the unmapped (55 and 35 keys) and show as text.
- *Icons.* Every icon is a Material Symbols name from the Outlined, Rounded and Sharp styles' shared list. **82 distinct names are used**, checked against `google/material-design-icons` at `737e332` (the codepoints file, 4,299 names) by `tools/vocabulary_icons.py`: 0 missing. That needs the network, so it is a tool and not a test. The icon set is Apache-2.0; nothing was copied, only names. 92 keys carry an icon (11 of them with a glyph as caption), 60 are text only, 4 are colours. Whether the 60 want a drawn icon, and a description for a screen reader, is the icon work's.

**Where it is validated.** The schemas hold what structure can say: the id pattern and the prefix rule, field types and bounds, the icon/glyph/colour rule, the version. `keys.semantic_problems` holds what needs two parts of a file related: ids unique, every group named by a key exists, an order unique within its group, no empty group, the eleven groups present, `DIGIT_0` to `DIGIT_9` present (the digit rule answers with them), and the alias rules of D85. It runs as `rl validate` with no path, as the first step of `rl build` (and so of `rl build --check`), and in the suite (`tests/test_key_vocabulary.py` breaks each rule in a copy of the shipped files and asks for the message). `rl validate <file>` is about that file's remote and does not run it.

### D84 — The mapping is a fold and one lookup, and it prefers no answer to a wrong one

`canonical_id(key_name, label=None)` returns an id or None. The pipeline, `keys.fold`, applied identically to a spelling in a file and to one in the alias table:

1. NFKC and upper case, so full-width and compatibility forms are the plain ones (`ＶＯＬ＋` is `VOL+`, `①` is `1`).
2. One name prefix removed from the start, and only with its underscore: `KEY_`, `BTN_`, and SmartIR's `SOURCES_` (D43's `sources` command group, as in `KEY_SOURCES_HDMI_1`). The word *key* in a label (`KEY LOCK`) is not a prefix.
3. A hyphen between two letters or digits joins them (`A-B`, `S-VIDEO`, `Vol-Up`); any other hyphen is a sign.
4. `+`, `-`, `*`, `#` become the words `PLUS`, `MINUS`, `STAR`, `HASH`, the way D49 already names them, so `VOL+` and `KEY_VOL_PLUS` meet.
5. Split on spaces, `_`, `.`, `,`, `:`, `;`, brackets, quotes, `=`, `~` and `\`. **Not `/`**, and not `!`, `?`, `|` or symbols: `P/C` says "this or that" and is not `PC` (D85), `POWER?` is not `POWER`, and ⏩ is a spelling like any other.
6. Each word is replaced by its `tokens` entry if it has one (`VOL` to `VOLUME`, `PWR` to `POWER`, `CH`, `CHAN` and `CHNL` to `CHANNEL`, `COLOUR` to `COLOR`).
7. The words are joined without spaces (`squash`), which is what the lookup compares: `VOLUME UP`, `VOLUME_UP` and LIRC's `VOLUMEUP` are one spelling.

Then **the digit rule** answers `0` to `9` however the source writes them (`1`, `KEY_KP1`, `NUM_1`, `NUMERIC 1`, `NUMBER 1`, `DIGIT 1`, `ONE`, and `CHANNEL 1`, which only the SmartIR import has: its `sources` group lists `Channel 1` to `Channel 9`, the codes that tune them) and not `10`, `0/10`, `D1`. Everything else is **one lookup** in a table of squashed spellings: each key's own id and display name, and every alias.

**The label decides when there is one.** An imported key is named by folding its label to ASCII (D49), so a name says less than the label: ⏩ is `KEY_`, and `-►.◄-` is `KEY_MINUS_MINUS`, which on its own is `DASH`. A first version read both and let the name rescue a label it did not know; measured on the tree it rescued 597 keys, and the ones it got wrong were these (15 keys of `-►.◄-`, 6 of `◄--`, 7 of `TAPE ◄►` read as `INPUT_TAPE`). So the name is read only for a key with no label (every LIRC and SmartIR key; an authored key that names its meaning and prints nothing). A blank label is no label.

**Conservative means these, each decided on the data and each a test in `test_what_does_not_map`:**

- *A bare `POWER` is `POWER`.* `POWER_ON` and `POWER_OFF` are reached by `ON`, `OFF`, `POWER ON`, `PWR OFF`, `TURN ON` and little else; `ON/OFF`, `STANDBY` and `POWER ON/OFF` say both and are the toggle. Nothing else maps to either (a test walks the list).
- *A device-qualified key is not mapped:* `POWER TV`, `TV_POWER`, `CD_PLAY`, `VCR_STOP`, `TV VOL+`. A layout is of one device, and an icon that hides *which* device a power key is for is worse than the text. LIRC's universal remotes are made of these (`remotes/lirc/philips/FA920.json`: `TV_0` to `TV_9`, `VCR_PLAY`, `LD_TRACK_UP`); they are most of the worst remotes in D86.
- *A key with two functions is not mapped:* `RIGHT / VOL+`, `UP/CH+`, `RED/AUDIO`, `TV/SAT`, `PAUSE/STEP`. It is both, and which depends on the mode. The exceptions are compounds that say one thing twice (`⏩/FWD`), listed.
- *A word with several meanings is not mapped:* `MODE`, `TIME`, `VIDEO`, `PROGRAM`, `PROG`, `SELECT`, `INDEX`, `LIST`, `AUTO`, `MEMORY`, `RESET`, `STILL`, `SCAN`, `TEST`. `SELECT` is the only OK-like key in 87% of the LIRC remotes that have it, and in the other 13% a `KEY_OK` is there as well; `INDEX` has a teletext key beside it in 92% of the IR Blaster remotes that have it and in 20% of the LIRC ones.
- *A glyph that is an arrow or a play key is not mapped:* `►`, `◄`, `/\`, `\/`. D87 has the numbers.
- *One that the data says is something else:* `P. UP`, `P. DOWN` (D85); `SKIP BACK` and `SKIP FORWARD`, which jump seconds on a recorder, are not `PREVIOUS` and `NEXT`; `ARC` is a soundbar's HDMI input as often as an aspect ratio; `OPT` is options or optical.

### D85 — The aliases: what the table holds, how a collision is caught, and what building it found

`aliases.json` is `{"version", "tokens", "aliases"}`: the six word rewrites of step 6, and for each canonical id the spellings that mean it besides its id and display name, 591 in all (a compound of a glyph and a word, such as `REV ⏪`, is listed only in the forms the data has). A spelling is written as a person would (`VOL+`, `Standby/On`, `⏩|`); the validator folds every one and **refuses** a table in which two ids claim a spelling that folds alike (`VOL UP` under `MUTE` is refused because `VOLUME_UP` has it), in which one id lists two spellings that fold alike (`VOL+` and `vol_plus`: one is redundant), in which a spelling folds to nothing, or is a digit (the rule answers those), or in which two keys' ids and display names fold alike. A token must be one folded word and is rewritten once. The same function folds the spelling in a file and in the table, so there is no second set of rules to drift from the first. Every alias in the table is tested to map to its id however it is spelled: lower case, upper case, as a name, with `KEY_` or `BTN_` in front, with spaces for underscores.

A compound with a slash is listed twice, with and without it, because a name has already lost its slash (LIRC writes `tv_av`). The file keeps the aliases of an id sorted and, where spellings fold alike, the most compact one (`VOL+`, not `VOL +`).

**What building it found**, each a change the first report made visible:

- **`P/C` was `PC`.** Step 5 first split on `/`, and the table's `PC` (the computer input) then took `P/C`, a key on 4,284 IR Blaster remotes (43%) whose meaning is not known. That was 4,310 keys mapped wrongly, the largest error the report showed, and it moved irblaster's keys mapped from 83.2% to 81.9% when it was fixed. The slash is now part of the spelling.
- **`P. UP` is not a channel key.** `P+` and `P-` are `CHANNEL_UP` and `CHANNEL_DOWN`, and the first table also had `P UP` and `P DOWN`. In 555 remotes `P. UP` sits beside `UP`, `DOWN`, `LEFT`, `RIGHT` (97%) and `P+`, `P-` (95%), with `P. DOWN`, `P. LEFT`, `P. RIGHT` after it: a second pad, not a channel key. The duplicate check below is what showed it.
- **The duplicate check.** For each id, how often two *different* spellings land on it in one remote. Most are right (`PREV` and `SKIP PREV.` in 794 remotes: two previous keys), and `CHANNEL_DOWN` with `PMINUS` and `PDOWN` in 597 was the finding above. After it, the worst left are `P-` beside `CH -` (194 remotes of 5,057 with `P-`: a second key, or `P-` meaning another thing there; accepted, `P+` is the channel key in the other 96%) and the transport pairs. Across the corpus **4,240 of 13,217 remotes have a canonical id on two or more keys** (`POWER` twice, `SKIP PREV.` and `PREV`), which a layout must expect: place one, show the rest under their own label.
- **`PREVIOUS CHANNEL` is `CHANNEL_DOWN`, a judgement.** SmartIR's `previousChannel` and `nextChannel` (55 keys, the only way those TVs change channel) are channel down and up; LIRC's `Prev_ch` (12 keys) may be "last channel". Taken as the pair, which is also the sequence. `PRE-CH` (72 keys), the likelier "last channel", is left unmapped.
- **Non-English labels: none qualify.** Of the 121,252 unmapped keys, 79 have a non-ASCII letter (70 of them Cyrillic, 9 Latin; 70 distinct spellings, one to three keys each: `Вниз`, `Вверх`, `Menü`, `Grün`). A search for about a hundred common German, Spanish, French, Italian, Portuguese and Dutch key words found none on more than seven keys (`PROGRAMM`, seven; `PAUSA`, five). A foreign label earns an entry only where it is frequent in the data, and none is, so the table has no entry that is not English or a glyph. The mechanism (NFKC, upper case, Unicode letters kept) would take one.
- **Glyph labels are spellings.** ⏩ and ⏪ are `FAST_FORWARD` and `REWIND` (1,155 and 1,144 keys), `|⏪`, `I⏪` and `!⏪` are `PREVIOUS`, `⏩|`, `⏩I` and `⏩!` are `NEXT` (the bar is written as `|`, `I` or `!` in the data), and a glyph with the word that says the same (`REV ⏪`, `⏩/FWD`) is listed beside them. 24,589 labels in the data have no letter or digit in them, of which `??` is 9,853.

### D86 — The coverage report, and the numbers

`rl keys report [--json]` prints, to standard output and deterministically: the keys mapped and the remotes with at least 90, 75 and 50 percent of their keys mapped, per source and overall; the same for remotes with at least 10 keys (one key mapped is a remote of 100 percent, and 7% of the remotes, 893, have fewer than ten); the 50 most frequent unmapped names with their keys and remotes; and the 20 remotes with the lowest coverage among those with ten keys. It reads `remotes/` and the vocabulary and nothing else, writes nothing, takes about 2 s (`ordered_map`, D81: 2.1 s wall and 5.3 s of CPU on the 64-core machine, 8 workers) and gives the same bytes at one worker and at three (tested). A percentage is **rounded down** to a tenth, so 89.97 is never printed as 90.0. `--json` is the same data with sorted keys (D20) and the percentages as numbers.

The number that matters is the per-remote one, because an unmapped key is shown in the "More" group and a remote with half its keys there is a poor Simple layout. The final numbers, which `test_design_quotes_the_numbers_the_report_prints` compares with a fresh run, so that this block cannot go stale (R14):

```
All remotes:

source       remotes     keys      keys mapped  >=90% of keys   >=75% of keys   >=50% of keys
-----------  -------  -------  ---------------  -------------  --------------  --------------
irblaster     10,084  412,775  81.9% (338,239)  44.7% (4,509)   69.7% (7,034)   92.9% (9,377)
lirc           3,138  112,789   58.3% (65,819)    18.8% (593)   49.6% (1,557)   79.9% (2,508)
smartir           62      914      81.7% (747)     54.8% (34)      67.7% (42)      79.0% (49)
hifi-remote      151    6,772    43.5% (2,951)       3.9% (6)      14.5% (22)      39.7% (60)
jp1            2,938  116,042   71.0% (82,496)    20.6% (607)   62.1% (1,826)   87.1% (2,559)
official         940   88,794   16.6% (14,783)      1.4% (14)       6.4% (61)     19.7% (186)
authored           5      128       77.3% (99)      40.0% (2)       60.0% (3)       60.0% (3)
all           17,318  738,214  68.4% (505,134)  33.2% (5,765)  60.8% (10,545)  85.1% (14,742)

Remotes with at least 10 keys:

source       remotes     keys      keys mapped  >=90% of keys  >=75% of keys   >=50% of keys
-----------  -------  -------  ---------------  -------------  -------------  --------------
irblaster      9,454  409,858  82.0% (336,226)  44.7% (4,230)  70.4% (6,665)   94.2% (8,912)
lirc           2,867  111,431   58.2% (64,915)    17.3% (496)  49.3% (1,415)   80.9% (2,320)
smartir           39      786      84.3% (663)     58.9% (23)     76.9% (30)      94.8% (37)
hifi-remote      128    6,653    43.7% (2,908)       3.9% (5)     14.8% (19)      42.1% (54)
jp1            2,783  115,109   71.1% (81,894)    20.1% (562)  63.0% (1,754)   88.2% (2,457)
official         887   88,534   16.5% (14,684)       0.3% (3)      5.6% (50)     19.3% (172)
authored           4      120       75.8% (91)      25.0% (1)      50.0% (2)       50.0% (2)
all           16,162  732,491  68.4% (501,381)  32.9% (5,320)  61.4% (9,935)  86.3% (13,954)
```

(The `official` row is the import of §29 and §31 (D123 to D127, D131 to D133): 189 files of Marantz's, Anthem's and Oppo's own tables. **Its keys map the least of any source, 21.6%, and only 6 of its 189 remotes reach 90%**, because a receiver's chart is the whole command set of a receiver (tuner presets and frequency steps, surround modes, Pure Direct, discrete `Audio Mute On` and `Off`, a source key for each input), which the 156 keys of D84 do not cover: 10,068 of its 12,844 keys are unmapped, under 584 different names, and what does map is volume, power, the cursor, bass, treble and sleep. Oppo's player tables are the exception, and are why the share is not lower: 750 of its 864 keys (86.8%) map. Widening the vocabulary to the receiver commands is the owner's call (D84 is theirs) and is in TODO.md, not done here. The `jp1` row is the import of §28 (D118 to D122), §32 (D134 to D136), §33 (D137 to D140) and §34 (D141 to D145), added after the same figures; the combos of §33 map less of their keys (52.5% of the 9,715) and lower the row from 76.8% to 74.0%, §34's executors (61.8% of 10,963) to 72.5%, §35's (54.6% of 3,958) to 71.8%, §36's (59.3% of 2,228) to 71.5%, §39's (31.7% of 1,975) to 70.8%, §42's (80.0% of 3,391) to 71.1% and §44's (81.5% of 2,442) to 71.3%. The `hifi-remote` row is the import of §27 (D109 to D117), added after the figures in D85 to D87's prose were measured; those figures are the corpus without it. Its keys map less than the others' because the pages describe commands of one kind of device: `Index Mark`, `Disc +`, `Scan Reverse`, and `Select`, which D84 does not map.)

(The `authored` row is the Meridian MSR, Samsung BN59-01199F, Sony RMT-B118P and Topping RC-15A: 94 of 116 keys; the 22 left are the Meridian's tape and VCR sources, the Topping's DAC settings and `KEY_SEN`.)

Read it as: **9 in 10 remotes have at least half their keys mapped, 2 in 3 at least three quarters, and 2 in 5 at least nine tenths.** The numbers went *down* while the table improved: the first pass, with 113 keys, mapped 77.0% of the keys and put 40.3% of the remotes at 90 percent, and 4,310 of those keys were `P/C` read as `PC` and 1,215 were `P. UP` and `P. DOWN` read as channel keys (D85). Coverage counts only as far as precision is held.

Two things bound it, and neither is the vocabulary. **Keys with no label cost about 7 points.** Setting aside the keys whose text is `??`, `?`, empty or only separators or `/`, a bare number or a hex-like name (14,662 keys, 2.8%) and recounting with a throwaway script, 52.0% of the IR Blaster remotes are at 90 percent instead of 44.8%; 3,608 of its 10,013 remotes (36%) have at least one such key. **LIRC is the other limit,** 18.8% at 90 percent, because much of its 3,138 files is universal remotes that hold several devices (D84), raw dumps keyed by number (`remotes/lirc/rc-5/RC-5.json` has 2,048 keys and maps none; the IR Blaster has several 256-key dumps, `KEY_000` to `KEY_255`) and keyboard-style key sets (`KEY_A` to `KEY_Z`).

### D87 — What is left, and where the next point would come from

The 15 most frequent unmapped names, all on purpose (`rl keys report` prints 50):

| keys | remotes | name | why it is not mapped |
|---|---|---|---|
| 9,853 | 2,497 | `??` | the source's own "unknown label": nothing to map |
| 4,314 | 4,284 | `P/C` | meaning not known; on 43% of IR Blaster remotes; not `PC` (D85) |
| 1,995 | 1,012 | `\/` and `/\` | arrows that are the cursor on one remote and a channel or volume key on another |
| 1,139 | 1,123 | `►` | the right arrow or Play |
| 1,116 | 1,111 | `◄` | the left arrow or reverse |
| 661 | 659 | `MODE` | a different function on every device |
| 658 | 658 | `KEY_AGAIN` | a Linux input code with no common meaning on a remote |
| 650 | 650 | `P. DOWN` | a second pad, not channel down (D85) |
| 565 | 565 | `P. UP` | the same |
| 541 | 541 | `TIME` | elapsed time or the clock |
| 514 | 512 | `VIDEO` | an input on one remote, a mode on another |
| 510 | 510 | `PROGRAM` | a channel, or a CD's memory |
| 501 | 500 | `PROG` | the same |
| 470 | 470 | `P. RIGHT` | the second pad |
| 469 | 469 | `P. LEFT` | the second pad |

The remaining ranks of the 50 are the same kinds of thing: the ambiguous words of D84, device-qualified keys (`CD_PLAY`, `CD_STOP`, `tv_vcr`), and keys named after a Linux code with no common meaning (`KEY_102ND`, `KEY_10CHANNELSUP`, `KEY_KPPLUS`, `KEY_POWER2`, `KEY_C`). **Where the next gain is, not done because it is not a per-key mapping:**

- **The arrow pad, with the remote as context.** 895 IR Blaster remotes have all four of `/\`, `\/`, `◄`, `►`, and in 788 of them (88%) no `UP`, `DOWN`, `LEFT` or `RIGHT` key is mapped anywhere else: the four are the cursor pad. A function over a whole remote (`canonical_ids(keys)`) could map them there and nowhere else; it would add 3,167 keys and, since the pad is what a Simple layout is built around, matters for more than the percentage. It is a second entry point beside `canonical_id` and would need its own tests, so it is left for the change that builds the layout.
- **Device-qualified keys,** by reading the device from the prefix and handing the layout one remote per device. The same follow-up.
- **The owner's review of the vocabulary and of the 330 most frequent mapped spellings** (99.2% of the 403,832 mapped keys; reviewed by eye for this section, not by anyone who owns a remote).

### What is not proven

- **The mapping's precision.** Recall is measured (D86); precision has no ground truth, since no labelled set exists. What was done is reading the 330 most frequent mapped spellings, which are 99.2% of the mapped keys, the duplicate statistics of D85 and the label-or-name comparison of D84, and the corrections those led to. A spelling that is wrong on a few remotes and right on the rest (`P+`, `FORWARD`, `PREVIOUS CHANNEL`) is accepted and named above.
- **That every icon is the right icon,** or that a client can draw it: 82 names exist in the icon set (a tool, not a test), and none was rendered.
- **That a layout can be built from it.** No client has read the files, and the reading of `icon`, `glyph` and `color` in D83 is a recommendation.
- **Hardware.** A mapping says what a key is called, not that its code works; D50 is still that question.

---

## 23. The catalog bundle

An app that has to work offline needs the catalog on the phone, and the app API (§21) is too big to ship (57 MB, 9,817 files, one brand at a time, and only the IR Blaster import). This section defines **bundle format v1**: one prebuilt SQLite file that an app can ship as an asset and open directly, the exporter that writes it (`rl bundle`), the two profiles it comes in (`full`, and `selected`, a subset for an app to ship), a manifest with a detached signature, and the notices of the sources it holds. It changes nothing that exists: no remote file, no importer, and not one byte of `build/`, `site/` or `site/app/v1/` (`rl build --check` reports 0 differences, as before, and a test builds a bundle between two checks and compares both trees). The code is `src/remote_ledger/bundle/`, the data it reads is `remotes/`, `src/remote_ledger/vocabulary/` (§22) and one plain-text list (`bundle/data/selected_brands.txt`). SPEC R23 states the requirement.

### D88 — The bundle is an artifact, not a stage: `rl bundle`

A stage is a tree the repository commits and `rl build --check` diffs (D19). A bundle is 19 to 50 MB of binary that changes with every remote, and nobody reads it in a pull request, so it is **not committed, owns no path of D19's table and is registered in no `generators.PIPELINE`**. The pipeline is still `check, compile, index, site, app`; `owned_paths()` is what it was (tested). It is written like the `app` stage in every other way: a function of the committed tree and of the code (`bundle.build.build_bundle(root, profile)` makes every file in memory, `write_bundle` writes them), workers through `parallel.ordered_map` (D81, so the bytes do not depend on the worker count), and a `--check`.

| Command | Does |
|---|---|
| `rl bundle [--profile selected\|full] [--out DIR] [--max-bytes N]` | Build and write `catalog.sqlite`, `notices.json`, `manifest.json` under `DIR` (default `bundle-out/<profile>`, which `.gitignore` lists) |
| `rl bundle ... --check` | Build in memory and compare with the files in `--out`; a signed manifest is compared without its `signature`. Writes nothing |
| `rl bundle --verify DIR` | Check the written bundle against the tree (D95 lists what) |
| `rl bundle sign --key KEY.pem DIR` and `rl bundle verify-signature --pub PUB.pem DIR` | D93 |
| `rl bundle vectors --file FILE [--from DIR] [--check]` | The cross-language vectors of the signal table (D91) |

**It is never written under `build/` or `site/`**: the writer refuses (`refuse_owned_path`), and with it the case D11 guards against, a command that writes into a tree a `--check` then compares. **Where its input is read from.** `remotes/` only: each remote is loaded and compiled by the very functions the `compile` stage uses (`load_remote`, `Remote.compile_group`, the selection of D7), not read from `build/pronto/`, so a bundle cannot be stale against the files it is built from, and the signals are the ledger's **wire readings** (D55): the 44,789 keys of the ten protocols whose second reading differs (D77) are in the bundle as the wire has them. The whole corpus compiles in about 4 s on eight workers. A key whose remote has several candidate groups (D16) is carried as its `primary` group; no remote of the corpus has another (`otherCandidates` in the report is 0), and the report counts them so that one appearing is not silent.

### D89 — Format v1: the file and its tables

**The file.** SQLite 3, UTF-8, `page_size` 4096, `auto_vacuum` none, rollback journal (header bytes 18 and 19 are 1: a bundle opens read-only from an asset and never needs the journal), `application_id` `0x524C4231` (`RLB1`), `user_version` 1, no free page. **Only what Android 11's SQLite 3.28 reads**: no `STRICT` table (3.37), no generated column (3.31), no `RETURNING`, no module, no trigger, no view; the schema is plain tables, two plain indexes and `WITHOUT ROWID` (3.8.2). A test reads `sqlite_master` for each of those words.

**Deterministic.** The inserts of each table are sorted by its key and made in one transaction, the pragmas are fixed, a final `VACUUM` rewrites the file densely in the order of the schema, and nothing is read from the clock, the machine, the environment or the temporary directory the file is built in. Two builds are the same bytes (tested in one process at 1 and 3 workers, and in three processes with three `PYTHONHASHSEED`s), and `rl bundle --check` is that test run against a directory. **What is not fixed is the SQLite library**: its version number is in the header (offset 96) and a different library may lay pages out differently, so two machines with different libraries may write different bytes for one tree. `dataVersion` (D93) is the digest of the *rows*, so it is the same on both; the SHA-256 of the file is not, and the manifest names the file it signed. A publisher builds once, on one machine, and publishes that.

**The tables.** Ids are integers; a table with a ``rowid`` has it as `id`.

| Table | Columns | Holds |
|---|---|---|
| `meta` | `key`, `value` (text) | `schemaVersion` (1), `dataVersion`, `vocabularyVersion`, `profile`, `selection` (the rule that chose the brands), `tiers` (`confirmed,verified,plausible,untested`: the index is the number stored in `tier` and `confidence`), `gramLength` (3), `normalisation`, `sqliteMinVersion` (3.28.0), `count.*` of each table, `playRule.ledger` and `playRule.full-signal` (D78 in a sentence each) |
| `sources` | `id`, `name`, `spdx`, `licence_kind`, `licence_notes`, `licence_text`, `upstream_url`, `upstream_commit`, `remote_count`, `key_count` | D94: one row per source, 1 authored, 2 LIRC, 3 SmartIR, 4 IR Blaster (a literal that only grows, like `app_api.PROTOCOLS`) |
| `vocab_groups`, `vocab_keys` | `id`, `key`, `grp`, `ord`, `name`, `icon`, `glyph`, `color`, `repeat` | The canonical key vocabulary (D83), 156 keys in 14 groups, `id` its position in reading order. The bundle is self-contained: `keys.canon` joins here. `vocabularyVersion` is its `version` |
| `brands` | `id`, `name`, `norm`, `first_model`, `model_count` | D90. A brand's models are the rows `first_model` to `first_model + model_count - 1` of `models` |
| `brand_aliases` | `alias`, `norm`, `brand_id` | D101: other names a person types for a brand of this bundle, each written out; the key `norm` and the brand are its primary key, so an alias that two brands share is a row for each; `WITHOUT ROWID`. Added after the first bundles of format 1, without a new version (D101): a reader that does not know it never reads it |
| `models` | `id`, `brand_id`, `name`, `kind` | D90. `kind` 0 is a product a person owns, 1 a remote's own part number |
| `controls` | `model_id`, `remote_id` | The device model to the remotes that control it; `WITHOUT ROWID`, so the table is its own index by model. No index by remote: "which models does this remote control" is a scan of 120,000 to 307,000 small rows and the app rarely asks |
| `remotes` | `id`, `ref`, `brand_id`, `model`, `source`, `tier`, `key_count`, `protocol`, `carrier_hz`, `repeat_passes`, `helper_repeat_passes`, `intro_empty`, `rule` | One row per remote file of the ledger (13,217), **except that a protocol fragment with no test key is a part of a sibling's row** (D103: 12,902 rows in the full bundle), see below |
| `remote_refs` | `ref`, `remote_id`, `first_n`, `key_count` | D105: every remote file of the ledger the profile carries (13,217 in the full bundle), to the remote that carries its keys and where they start there; `WITHOUT ROWID`. Added after the first bundles of format 1, without a new version (D105) |
| `remote_completions` | `remote_id`, `first_n`, `key_count`, `corroborators` | D353: one row for each thin remote that was completed from the remotes that share its address (827 in the full bundle): its keys `first_n` to the last are the added ones, untested; `WITHOUT ROWID`. Added after D105, without a new version |
| `keys` | `remote_id`, `n`, `canon`, `label`, `signal_id`, `confidence` | One row per key (525,084), primary key `(remote_id, n)`, `WITHOUT ROWID` |
| `signals` | `id`, `words` | D91 |
| `ngram` | `gram`, `ids` | D90 |
| `excluded_brands` | `name`, `norm`, `api_key` | D92: the brands that are not in this bundle |

**A remote** is a remote file of the ledger, which is one protocol (R3), **with the fragments folded into it** (D103). An IR Blaster database id with several protocols is several files (573 of its ids are), and each is a remote a model points at, except a fragment with no test key, which the sibling that has one carries (315 files in the full bundle); `remote_refs` (D105) says which remote carries each file.

- `id` is the position, in the path order of the **whole ledger** and from 1, of the file that carries the remote (D103 leaves a gap where a folded fragment was), **in every profile**: the full and the selected bundle of one tree number a remote alike, so the remote ids that a matcher over the full catalog returns are the ids on the phone. `ref` is its path under `remotes/` without `.json` (`irblaster/ACER/1103-NEC1`, `lirc/sony/839`, `topping/RC-15A`) and **is its name across ledger versions**: the `id` of a remote moves when a file is added before it, its `ref` does not move unless the file does. Brand, model, signal and vocabulary ids are numbered inside one bundle.
- `brand_id` is the brand of the file's `manufacturer`, NULL when the remote is in the bundle for another brand it controls and its own maker is not (391 remotes in the selected bundle). `model` is the file's own `model` where it is a name (LIRC's, the authored remotes') and **NULL where it is a placeholder the importer made** (`IR Blaster DB 1103 (NEC1)`, `SmartIR media_player 7`, D43, D46).
- `source` is a row of `sources`. `tier` is the **weakest** tier of the remote's keys (`index.rolled_up_confidence`: a remote is as trustworthy as the key you happen to press); `key_count` is the number of its `keys` rows; `protocol` is the ledger's protocol name, NULL for a capture the ledger did not identify (D24; 3,062 LIRC remotes), and for a remote with fragments folded into it the protocol of its own file (D104); `carrier_hz` the file's carrier.
- **The play fields are D78's, per remote.** `helper_repeat_passes` is what `_remoteLedgerSends` plays for a ledger remote: the repeat sequence `minSends` times when the intro is empty, `minSends - 1` times after it otherwise. `repeat_passes` is that, raised to one for a remote whose keys are all of a database protocol the oracle plays as the whole signal (`app_api.FULL_SIGNAL_PROTOCOLS`: Sharp, Denon); `rule` is `ledger` or `full-signal` accordingly, and `intro_empty` says whether word 2 of every one of its signals is 0. D78 stated these per database protocol and checked that a protocol's signals agree; here they are per file and the exporter **stops** if a file's signals disagree about the intro (none does, in 13,217 files) or its keys are played by two rules. The same arithmetic as `app_api._play`, over a file instead of a protocol; for the IR Blaster files it gives D78's table (tested, `PLAY` in `tests/test_bundle.py`). For a LIRC, SmartIR or authored remote there is no database protocol, so the rule is `ledger`.

**A key** has its position `n` in the remote: its file's keys in the order of the key names and then, where fragments are folded into it, theirs (D104), `canon` the vocabulary id of its canonical key (`canonical_id(name, label)`, D84) or NULL, `signal_id`, and `confidence`, the tier of the form that compiled to the signal (0 confirmed to 3 untested: 524,996 keys are plausible and 88, those of the three authored remotes that cite a second source, verified; none is confirmed). **`label` is the key's own text where the bundle has nothing better, and NULL where the canonical key says it already.** The text is the source's `label` if the key has one, else its name (`KEY_AGAIN`). It is stored when the key has no canonical id (it is then the only name the key has), or when its text is not a spelling of the canonical key's id or display name (`VOL+` on `VOLUME_UP`; spellings compared by `keys.squash`, so `Volume Up`, `VOLUME_UP` and `volumeup` are the key's name and `STANDBY` on `POWER` is not). 220,000 of 525,084 keys store one, 1.5 MB. A key that stores none draws as its canonical key's icon and display name; a canonical id that two keys of a remote share (4,240 remotes have one, D85) has no label to tell them apart and a layout places one and lists the rest.

### D90 — Search: the search key, `brands`, `models` and `ngram`

FTS5 is not in every Android SQLite, so the search is ordinary columns and one posting table. The bundle holds the structures; a reader's algorithm is the reader's (§24 has one). What is fixed here is what is stored and how to read it.

**The search key** of a text (`bundle/textnorm.search_norm`): Unicode **NFKD**, **lower case** (`lower()`, not `casefold()`: Kotlin's `lowercase()` agrees with it and `ß` stays), then **keep only the code points whose general category is a letter or a number** (`L*`, `N*`). Every space, punctuation mark, symbol and combining mark is dropped and nothing replaces it: `UN50NU6900F`, `un 50 nu-6900 f` and `UN50-NU6900/F` are `un50nu6900f`; `Ünï` is `uni`; `Ｓony ①` is `sony1`. Python's `str.isalnum` is exactly "category L or N" (checked over all of Unicode 15.0), so the filter is `re.sub(r"[\W_]+", "", ...)`. It deliberately does no more: no letter is turned into a digit, no brand word dropped and no suffix cut. Those are for the matcher, and are done to the query and never to what is stored.

**`brands`**: one row per brand **search key**. `ORION`, `Orion` and `orion` (the IR Blaster import, the authored remotes and LIRC) are one brand; 5,028 brands for 5,496 spellings. `name` is the spelling the **whole ledger** writes most often, the first in code point order among equals (`ORION`; LIRC's lower-case `2wire` where no other source has it). `norm` is the key, with an index. A brand's models are a **contiguous range** of `models`, `first_model` and `model_count`: the models are written sorted by brand, so the range replaces an index of 276,000 rows (3 MB) and costs nothing to read (`WHERE id BETWEEN first_model AND first_model + model_count - 1`).

**`models`**: one row per `(brand, search key of the name)`, so `ACME | TV-1`, `ACME | tv-1` and a LIRC remote named `TV-1` are one model; 276,247 models for 279,447 import pairs and the other sources' names. `name` is its commonest spelling. A model **is** the thing a person types, and where it comes from depends on the source:

- the IR Blaster import files each remote under a list of `<BRAND> | <MODEL>` products with brand and model kept apart (D56b): each is a device, `kind` 0;
- SmartIR, LIRC and the authored remotes keep `controls` as free text, so each entry is a device of the remote's manufacturer, `kind` 0;
- a remote's own `model` and its `aliases` are **part numbers** (what a person reads on the back of a remote, `BN59-01199F`), `kind` 1, except where the model is a placeholder (D89). A model that is both a device and a part number is `kind` 0.

**`controls`** links a model to every remote that lists it, across sources; each model has at least one.

**`ngram`** is the candidate generator for a typo. A model's **grams** are every three characters of `^` + search key + `$` (`un5` gives `^un`, `un5`, `n5$`); `^` and `$` cannot occur in a key, a gram that starts with `^` is the start of a key, one that ends in `$` its end, and a key of one or two characters still has a gram. For each gram the table holds the ids of the models that have it, ascending, as the **differences between neighbours** (the first from 0), each an unsigned **LEB128 varint**: seven bits a byte, low group first, the high bit set on every byte but the last (`[1, 2, 300]` is `01 01 AA 02`). A query is turned into its grams, their postings are read and merged, and the models that share the most grams are the candidates, which the reader then ranks by its own distance; a model name's key is computed for those few and never stored, which keeps the bundle 4 MB smaller and costs a reader a few hundred keys per query. 35,763 grams hold 3.3 MB of postings for 276,247 models. Chosen over a table of `(gram, model_id)` rows, which is 10 bytes a row and about 25 MB for the full catalog, and over a deletion table (the same size).

### D91 — Signals: shared, binary, sorted

**The data-size reasoning.** The IR Blaster data has **412,775 keys but only 58,344 distinct compiled signals** (14%): the whole ledger has 653,959 keys and 175,271 (27%); LIRC alone shares little (112,789 keys, 97,775 signals, the raw captures). A key row is 20 bytes and a signal is 129 on average (the longest, a LIRC capture, 628 words), so storing each signal once is what makes the catalog small: the signals are 20.1 MB of blobs for 155,964, where one blob per key would be 72.9 MB. The key rows are the other half, and the reason `keys` has no text where the canonical key says it (D89).

**The blob** is `signals.words`: a big-endian `uint16` **count of words**, then the words, each a big-endian `uint16`. The words are exactly those of the Pronto Hex string the ledger compiled the key to (`pronto.encode`): word 0 is `0000`, word 1 the frequency word, word 2 the number of burst pairs of the intro and word 3 of the repeat sequence, then the durations in carrier cycles, a mark then a space, first the intro and then the repeat (D6, D25, D78); **a signal with an ending (D217) has more words after those, the number of burst pairs of the ending and then its durations in cycles**. The count is redundant with the length of the blob and with words 2 and 3 (`count = 4 + 2 x (n1 + n2)`, and `1 + 2 x n3` more for an ending); it is there so that a reader can reject a damaged blob without trusting anything else. One SQLite row per signal, `id` from 1 in the **sorted order of the blobs** as bytes: the length prefix makes that order by length first, then by content, so signals of one protocol and of one remote's family sit together, which is what compresses (D95). **Microseconds** are `round_half_up(cycles x period)` with `period = word1 x 0.241246 us` (D25: lossy in this direction by under half a cycle); the **carrier** is the catalog's (`remotes.carrier_hz`, what the file declared, `38000`) and the frequency word says another number (`006D` is 38,029 Hz): which an output transmits is its own decision, and both are in the vectors.

**Test vectors**, `tests/vectors/bundle_vectors.json` (`rl bundle vectors --file tests/vectors/bundle_vectors.json`): 151 keys, chosen by a hash of `(remote, n)`, 4 for each source and protocol of the full bundle (29 protocol families, with the raw captures as `(unnamed)`) and the extremes (the shortest and the longest signal, each rule and each intro, the most passes). Each vector carries the `blobHex`, `frequencyWord`, `frequencyHz`, `catalogCarrierHz`, `introUs`, `repeatUs` computed by **the ledger's own decoder run on the blob** (`pronto.decode`), and `play`: the remote's four D78 fields and `pressUs`, the intro once and then the repeat sequence `repeatPasses` times, which is what one press transmits. A Kotlin reader is right when it makes those from the blob and the carrier. `tests/test_bundle_vectors.py` holds the file to the decoder, to a second computation that shares no code with it (Decimal arithmetic on the words) and to D78's numbers. The vectors are self-contained, so they do not have to follow the catalog; they are regenerated when the decoder or the format changes.

### D92 — Profiles: `full` and `selected`

**`full`** is every brand and remote: what a backend serves and what `selected` is cut from. **`selected`** is what an app ships in its install: since D117 **every brand, with no budget and no cap**, because the owner asked for all the data and for size not to be a factor (it was at most 20,000,000 bytes so that an app that ships it keeps its whole install near 30 MB, then 20,250,000, D92 and D116). A build over a limit given with `--max-bytes N` still fails. The ledger has no popularity data, so the rule is a proxy plus a list a person reviews, and every number of it is a constant of `bundle/select.py` or a line of `bundle/data/selected_brands.txt` (since D117 the list says only in which order the brands are taken, because all of them are):

1. **The curated list**, `selected_brands.txt`: 112 well-known brands in the categories the owner named (television, AV receiver, set-top box, streaming box, projector, soundbar, disc player; air conditioners are out of scope), a brand per line, `#` to comment, matched by search key. **The order is the priority**: the bundle takes the brands from the top and carries each **whole** (all its models, remotes and signals) when what it adds still fits the budget, by an estimate that is within 3% of the file (the budget was 19,000,000 bytes, then 19,609,000; since D117 there is none, so every brand of the list is carried and none is skipped); one that does not fit a budget that is set is skipped, the next is tried, and the command names the brands it skipped. The owner changes what ships by moving or adding a line.
2. **The proxy fill**: the other brands, ranked by **models per byte** (the number of models a brand is listed with, over what its remotes and signals would add), among those with at least 40 models and 60% of their keys mapped to a canonical key, are added in that order while they fit what the list left of the budget (since D117 there is no floor and no budget, so every other brand is added, 5,260 of them with the JP1 import of §28: the order only matters when a budget is set). Models per brand is the one popularity signal the catalog has; dividing by the cost keeps out the makers of generic replacement remotes (`BRAVO`: 2,945 models, 2,275 remotes, 112,034 keys). Of the three proxies the owner named, **models per brand ranks the brands, and remotes per brand count as the cost** (with their keys and signals); **the third, the share of keys with signals, is 100% for every brand** (every key the ledger holds has a compiled signal), so it carries no information and the share of keys that map to a canonical key stands in for it.

A remote is carried when **any** of its brands is chosen (its maker or a brand of a model it controls); a model when its brand is. A remote that is in for one brand keeps all its keys and signals, and the models it controls under brands that were not chosen are not in the bundle.

**Everything not in the subset is recorded**: `excluded_brands` holds each left-out brand (`name`, `norm`) and `api_key`, the ten hex digits (SHA-1 of the exact name, D76) of the shard of the app API (§21) that has its remotes, comma-separated when the import spells it two ways; **NULL where the app API has none**, because the API serves the IR Blaster import only. Since D117 nothing is left out of the selected bundle, so the table is empty and **0 brands, 0 remotes, 0 keys** are missing from it, and **0 remotes of LIRC and SmartIR** are in no static file of the ledger that the bundle does not hold. Before it, 4,992 brands were left out, 228 of them with no shard, and 1,794 remotes of LIRC and SmartIR (the authored ones counted with them) were in no static file of the ledger at all until the full bundle was published; that finding is why `full` exists, and the selected bundle no longer has it.

**What the numbers said** (D95, at a 20 MB cap; D117 removed the cap and these are history): the list did not fit. Samsung, LG, Sony, Panasonic and Philips, the first five lines, are 11.6 MB of the 19 by the estimate (Sony alone has 1,061 remotes, and most of the bytes of a brand with old equipment are LIRC raw captures: 5.0 MB of signals for 1,375 of the 4,394 remotes); the bundle carries **35 brands of the list and one by the proxy**, and **77 brands of the list are left out for lack of room** (the first are JVC, Grundig, Thomson, Telefunken, Loewe Opta, Beko, Vestel...). Levers the owner has, none taken here: reorder the list so the brands that matter most come first (the cheap ones are taken wherever the budget allows), shorten it, raise the cap, or drop what is bulkiest per remote (the LIRC raw captures, about 6 MB of the 19). **The owner signs off on the list**; until then it is a proposal. *Since D117:* nothing is skipped (0 brands of the list are left out) and nothing is left out of the bundle. The profile carries 112 brands of the list and 5,353 by the proxy, 5,465 in all, with 17,318 remote files, and its file is the size of `full`.

### D93 — The manifest, `dataVersion` and the signature

`manifest.json` (compact JSON, sorted keys, the format of D76) lists: `schemaVersion` (1), `dataVersion`, `vocabularyVersion`, `profile`, `bundle` and `notices` (`file`, `bytes`, `sha256`; the bundle also `gzipBytes`, `gzip -9` with no clock in its header), `counts` (brands, brandAliases, models, remotes, remoteRefs, keys, signals) and `brands` (`included`, `excluded`). `rl bundle sign` adds `signature` (`algorithm`, `file`, `format`, `keyId`) and writes `manifest.sig`.

**`dataVersion`** is twelve hex digits of the SHA-256 of **every row of every table but `meta`**, each table in key order, as JSON with ASCII escapes and blobs as hex (`writer.content_digest`). A file cannot hold the hash of its own bytes, and the version has to be inside the file (an app that ships the asset has no manifest to read), so it is the version of the *content*; the manifest repeats it and adds the SHA-256 of the whole file, which a signature covers. It does not depend on the SQLite library that laid out the pages (tested: the same rows in a file with another page size give the same version), and changing any row changes it.

**The scheme.** ECDSA over P-256 with SHA-256, the signature DER encoded, detached: `manifest.sig` is the signature of the bytes of `manifest.json`. One signature covers the bundle and the notices through the hashes the manifest lists, and the manifest's own fields. Chosen because every Android version has it (`Signature.getInstance("SHA256withECDSA")`; Ed25519 is only on newer ones) and because `openssl` makes and checks it, so no cryptography is written here: the commands run `openssl dgst -sha256 -sign` and `-verify` as a subprocess, name `openssl` in the error when it is missing, and accept nothing but a P-256 key. **To verify, as an app does:**

```kotlin
val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(publicKeyDer))  // SubjectPublicKeyInfo
val sig = Signature.getInstance("SHA256withECDSA")
sig.initVerify(key)
sig.update(manifestJsonBytes)
check(sig.verify(manifestSigDer))          // then: sha256(catalog.sqlite) == manifest.bundle.sha256
```

(`openssl pkey -pubin -in release.pub.pem -outform DER` gives the DER of the public key to ship in the app.)

**Key id, custody, rotation.** The key id is the first sixteen hex digits of the SHA-256 of the public key's DER SubjectPublicKeyInfo, written into the manifest before it is signed (`signature.keyId`), so the signature covers the claim of who signed; `verify-signature` fails if the manifest names another key than the one given. The private key is generated once (`openssl ecparam -name prime256v1 -genkey -noout -out release.pem`), lives only where the publishing job runs (a secret of the CI that publishes, or an offline machine), **is never in this repository, a bundle or a test** (`.gitignore` keeps `*.pem` out as a net, `!*.pub.pem` lets a public key in, and a test asks `git grep` for a private key header and `git ls-files` for a key file), and is separate from any app-store signing key. An app holds the public keys it trusts, by id. **Rotation** is a new key with a new id: the app that trusts both ships first, the first bundle signed with the new key after, the old key dropped in a later app release. A compromised key is replaced the same way and cannot be revoked faster than an app update; that is the cost of an offline-verifiable scheme and is stated here and not hidden.

### D94 — Notices

`notices.json` (and the `sources` table, from the same data) lists, per source, what an app shows on a screen of open-source notices: `name`, `spdx`, `licenceKind`, `licenceNotes`, `licenceText` (the full text), `upstreamUrl`, `upstreamCommit`, and `contributed`: the ledger's remotes and keys of that source and the bundle's. **Nothing is inferred**: the SPDX id is `paths.IMPORTS`' (`GPL-2.0-or-later`, `MIT`, `GPL-3.0-only`); the text is the import directory's own `COPYING` or `LICENSE`, byte for byte, read at build time (a missing file stops the build); the commit is the one `IMPORT.md` names; and **`licenceNotes` are sentences of the import directory's README, quoted word for word**, which the exporter looks up in the README on every build and the tests again, so a README that stops saying it stops the build rather than leave a notice that says it. Where the repository says a licence holds **by inheritance only**, the notice says it in the repository's words and its `licenceKind` is `inherited`: *"Licence: GPL-3.0-only, by inheritance and nothing more."* and *"nobody in the chain states a licence for the data itself, because nobody in the chain says where the data came from. This is not a grant from the data's authors, and it is not a reading of one, as LIRC's is."* (LIRC's is `reading`: *"This is a reading of the licence, not a grant."*; SmartIR's is `stated`.) The IR Blaster entry also lists the lineage the README gives. **The authored remotes have no licence entry** (`spdx` and `licenceText` null, `licenceKind` `none`): the repository has no licence file for them and states none, and the notice says that; a test fails if a licence file appears at the root, so that the notice is revisited. Which licence the authored remotes should carry is the owner's to say.

### D95 — Numbers, and what is not proven

Measured on the 64-core development machine (eight workers), `gzip -9` as the command, SQLite 3.45 as Python links it. **Both profiles**, same tree:

| | `full` | `selected` |
|---|---|---|
| file | 62,341,120 B | 62,341,120 B |
| `gzip -9` | 15,563,694 B | 15,563,675 B |
| brands | 5,465 | 5,465 |
| models | 283,086 | 283,086 |
| remote files | 17,318 | 17,318 |
| remotes, the fragments of D103 folded | 16,961 | 16,961 |
| keys of the files | 738,214 | 738,214 |
| keys, the completion's (D353) included | 746,183 | 746,183 |
| signals | 184,872 | 184,872 |
| left out | none | none: 0 brands, 0 remotes, 0 keys |
| `rl bundle` (read, build, write) | 27 s, 0.89 GB peak | 27 s, 0.89 GB peak |
| `rl bundle --verify` | 23 s | 23 s |

Where the bytes are, `full` (selected is the same shape): signals 22.4 MB (44.5%), keys 10.8 (21.5%), models 6.9 (13.8%), ngram 5.2 (10.2%), controls 3.4 (6.7%), remotes 1.3 (2.7%). For the app API (§21) the same catalog was 57 MB and 9,817 files, and 12.9 MB gzipped; the whole ledger's compiled corpus is 358 MB. The IR Blaster signals alone are 8,889,348 bytes as blobs (8.9 MB of binary words) and 284,114 bytes gzipped on their own (measured again at §38; when the whole ledger had 155,964 signals they were 20.1 MB, 892,095 gzipped, 23 times smaller). Sorted, the signals compress far better than the rest of the file (the other 28 MB gzip to about 11.8 MB), so **on the wire the catalog is its rows and not its signals**.

**What `--verify` checks** (21 s on `full`, no check repaired): the manifest's sizes and hashes; `application_id`, `user_version`, `page_size`, `quick_check`; every count against `meta` and the manifest; `dataVersion` recomputed from the rows; every reference between tables; a brand's models are its range; the grams recomputed from the models' names; every remote is a file of the tree and that remote, with the files folded into it (D103: id in path order, carrier, protocol, tier, play numbers, and every key's canonical id, text, confidence and blob); `remote_refs` is the refs of the files the profile carries, each to its carrier (D105); the full profile has every remote of the tree and the selected one every remote of a brand it carries; 150 remotes (chosen by hash) compiled again from `build/pronto/` where it has them, the compile stage's own output, and their Pronto strings compared with the blobs; a sample of models and remotes linked both ways; and **every `(signal, carrier)` decoded by the ledger's decoder and encoded back to the same words** (155,964 of them on `full`). `tests/test_bundle_verify.py` breaks a good bundle in 24 ways at the row level and in several at the file level (a changed byte, a wrong size, a missing file, a stale tree, a stale compile artifact) and requires each to be reported.

**Not proven.**

- **No Android has opened it.** The schema is written for SQLite 3.28 and a test refuses the newer features by name, but no bundle was read on a phone or by a SQLite older than 3.45, and no Kotlin reader exists: the vectors are the oracle for one, not a test of one. Room's `createFromAsset` validates a schema against its entities, and `WITHOUT ROWID` tables have not been tried with it; reading with `SQLiteDatabase` needs none of that.
- **Search quality.** The structures are what a matcher needs; how well a query finds its remote is measured in §24 (D99), and only on generated queries.
- **The selection is a proposal.** It is a judgement of popularity on a list nobody but its author has read. The owner signs off on it; since D117 every one of them fits, because nothing limits the size.
- **The 4.5 MB gzip of the selected bundle is the transfer size; the install size depends on how the app stores the asset** (compressed in the package, or not: SQLite needs it uncompressed on disk to open it read-only).
- **Hardware**, as everywhere: a bundle says what the ledger says, and D50 is still the question whether a signal moves a device.
- **The signature is not tested with a real publishing key or an app.** The commands and the format are; where the key lives is the owner's.
- **Reproducibility across SQLite versions** is not claimed (D89).

---

## 24. Finding a device in the catalog

The bundle (§23) holds the search structures; this section is the **matcher** that reads them (`src/remote_ledger/matching.py`), the vectors a port is held to, and a **test set and harness** that measure how often it finds the right remote (`bundle/search_eval.py`, `rl bundle search-eval`). The matcher has two users and one set of rules: a search on a phone, where a person types a brand and a model or a part number, and a service that turns what a provider read off a photo (a brand, a model and some visible lines of text) into catalog entries. It changes nothing that exists (no stage, no generated file; `rl build --check` reports 0 differences). SPEC R24 states the requirement.

### D96 — The matcher: simple rules, integers, no model

`match(brand, model, texts) -> [Candidate(brand, model, remote_ids, score, evidence)]` (`MatchIndex.match`, or `matching.match(index, ...)`), five candidates at most, best first, over a bundle opened read-only (`MatchIndex.open(path)`) or a small catalog (`MatchIndex.from_entries`). A candidate with no model is a brand: `models` then lists its models. The **rules are in the module's docstring** and are the specification; in short:

1. **Keys and tokens.** The key of a text is the bundle's search key (D90); its tokens are the runs of letters and digits of the same NFKD lower-case text, marks dropped, so the tokens joined are the key (`UN50-NU 6900/F` is `un50nu6900f`, tokens `un50 nu 6900 f`). A run of Han ideographs is a token of its own (D102).
2. **Similarity of two keys**, an integer of thousandths: equal is 1000; **edit** is `1000 - 1000 x d / longer key` (rounded down) with `d` the optimal-string-alignment distance, a swap of two neighbours one edit and a substitution between the look-alikes `o/0 i/1 l/1 s/5 b/8 z/2` half an edit; **prefix**, when the shorter key has at least five characters and starts the longer, is `800 + 200 x shorter / longer`. The larger counts, and only from **800**, which is 20% of the longer key: a key of four characters must be exact, five to nine may differ by one edit, ten to fourteen by two. Look-alikes are cheaper, never free, so two different keys are never equal, and a brand is never read through them.
3. **Brands.** A given brand is found by its key (1000), or by the same similarity among the brands that share grams with it (`Samsung Electronics` is `samsung` at 878, `Phillips` is `philips` at 875). Runs of it that are a brand's key score 900. A run of any text that is exactly a brand's key, of two characters or more (`LG`), scores 800: a **hint**, since a text holds incidental words (`DVD`, `PLUS` and `COLOR` are brands). A brand's aliases (D101) are keys of the brand wherever a brand's own key is looked up (D102).
4. **Model keys.** A run is one to eight adjacent tokens of a line. The key of the whole `model` and each of its runs weigh 1000; the runs of each text weigh 900, among all models when they have 4 to 24 characters and a digit (a model number has one) and among the models of the named brands alone when they have none (`roku ultra`), never when the run is itself a brand's key. Sixty keys at most, **longest first**; a run inside a longer run that gave a candidate **by the exact rule** is not tried (`bdp-s360` is `bdps360` and not also another brand's `s360`), and one inside a longer run that gave a candidate **by the edit rule at 900 or more** is tried only among the models of the named brands, and only an exact match of it counts (D168: `philips bdp 3390` is `philipsbdp3300` at 928, and must still find the brand's own exact `bdp3390`).
5. **Candidates.** From the grams of a key (D90) the models sharing at least 40% of them, the forty sharing most; and the same inside each named brand's models.
6. **Score** = similarity (at 800 or more) x the key's weight x a **brand factor**: 1000 if the candidate's brand is a named one, 600 if a brand was *given* and it is not that one, else 950. Under 650 is dropped. **A model counts only if it matches a real entry**, so an invented model number gives nothing; and **when no model matches but a brand is named the answer is the brand and its models** (most remotes first, fifty at most).
7. **Order**: score, similarity, the longer catalog key, then names.

**Why these.** Integers because a port in another language must get the same order, and a float's last bit is not the same in two languages. The look-alike half-edit is the "letters for digits only where safe" the owner asked for: an OCR's `O` for `0` is close, not equal. The brand factor 600 and the floor 650 are how a model an LLM made up, or a real model of the wrong brand, is dropped when the provider named a brand. **What the first version got wrong, found by running it on the catalog** (D99): a two-letter brand (`LG`) was never named, because a brand needed three characters; a brand named by a stray word of a text (`DVD` in a model's own name) took every model of another brand away, so the penalty became the *given* brand's alone; a long run that matched a short model by its start (`PANASONIC` under another brand) hid the exact match inside it, so a prefix match does not hide; and a model of six tokens (`32 LC 2 RB - ZJ(DVD)`) was never tried, so a run may be eight.

**Speed.** A query takes **about 3 ms at the median, 11 ms at the 95th percentile and 28 ms at worst** over the full catalog (320 generated queries, one thread, a 64-core machine, SQLite 3.45, caches cold at the start; runs differ by a millisecond or two); about 1.6, 5.5 and 11 ms over the selected bundle. Opening the index reads the brands (5,028) and nothing else; a model's key, its remotes and a gram's posting list are read when a query needs them, with small caches (`rl bundle search-eval --timing` reports it). Two things keep it there: the distance stops as soon as it is certainly above what could reach 800, and the posting lists of a key are counted once for all the brands it is asked in.

### D97 — The vectors a port is held to: `tests/vectors/matching_vectors.json`

Written by `rl bundle matching-vectors --file tests/vectors/matching_vectors.json` (`--check` compares). **Self-contained and independent of the data**: a small catalog of 51 entries written in `bundle/matching_vectors.py` (a pair of models one character apart, one that starts another, a part of a longer model under another brand, one model under two brands, brands written with spaces, hyphens and accents, a brand with models to list, a model with no digit, a model whose name holds a brand alias, one company spelled two ways, a model that repeats its brand beside one an edit away), and 66 queries, each with a note on the rule it shows, and the matcher's answer: for each candidate the brand, model, remote ids, integer score, evidence and, for a brand, its models. The first 41 are the queries the file had before brand aliases existed, unchanged; the next 21 are Chinese (D102) and the last 2 are D168's. The file also holds the constants (and `HAN_RANGES`), the 11 aliases of the catalog's brands, the tokens and keys of 19 texts and the similarity of 23 pairs of keys. Because it does not follow the repository's data it can be held to the code exactly, and a test does (`test_the_committed_vectors_are_what_the_matcher_gives`); it changes when the rules do. A port builds the small catalog, asks the queries and compares integers. The similarity vectors are checked in the suite against a textbook matrix implementation written there, and the answers against the rules by hand (`tests/test_matching.py`).

### D98 — The search test set and its harness

`rl bundle search-eval [--bundle DIR] [--queries FILE] [--seed N] [--per-class N] [--out FILE] [--timing]` reads a written bundle, generates queries from it, runs each as **typed text** through the matcher over the bundle's own tables, and prints a Markdown report: a table of top-1 and top-5 per class and overall, the hand-written queries in a table of their own, the generated queries that missed, and with `--timing` the time each took. Same bundle and seed, same report.

**The generator** takes devices from the bundle (a `kind` 0 model whose search key has six characters or more and a digit, a name of at most 40 characters, at most two of a brand in a class, so that no big brand is the test set), ranked by a hash of the seed and the device, and applies **eight classes**, 40 devices each, 320 queries: `exact` (`SAMSUNG UN50NU6900F`), `case` (lower, upper or title), `punctuation` (separators stripped, or a space at every letter-digit boundary, or one hyphen), `dropped-suffix` (`UN50NU6900`, for a model with trailing letters), `typo` (one character dropped or two neighbours swapped, never the first), `brand-partial` (the brand and the first 60% of the model), `model-only` and `reversed` (`UN50NU6900F SAMSUNG`). **Expected** are the remote ids the bundle's `controls` give the device; **a hit** is an answer that is a model, not a brand, whose remote ids contain all of them. Top-1 is the first answer, top-5 any of the five.

**Honesty.** The queries are the catalog's own names changed by mechanical rules. They never misspell a brand, never name a device the catalog does not have, never use a nickname or a model written from memory, and the exact class is a lookup. Real people type worse. **The rates are an upper bound on search quality, not an estimate of it**, and the report says so in its own text. The classes were written once and not changed after a result was seen. What was changed after seeing results is the matcher (the four corrections in D96), and, once, how the devices of a class are sampled (for speed; the sample it gave was kept); the numbers below are those of the last version.

**Real queries.** `src/remote_ledger/bundle/data/real_queries.json` is the place, read by default (`--queries FILE` reads another) and reported in a table of its own. Format: `{"format": 1, "queries": [{"query": "samsung un50nu6900", "expect": {"brand": "SAMSUNG", "model": "UN50NU6900F"}, "note": "who typed it, where"}, ...]}`. `expect` names a device as the catalog spells it (case, spaces and punctuation do not matter) and the remotes that control it are what the answer must contain; `null` means the query names nothing the catalog has and is right when the answer holds no model (an invented model, a phrase). A device that the bundle being scored does not have is counted and left out of the rates, and the report says how many. The file ships empty, with its own description and one unscored example; **the owner's hand-written queries are the number that matters for the 95% target**, and none have been written.

### D99 — What was measured, and what is not proven

Seed 1, 40 devices a class. **Selected bundle** (5,465 brands, 283,086 models: the same as the full one since D117) and **full bundle** (5,465 brands, 283,086 models):

| class | selected top-1 | selected top-5 | full top-1 | full top-5 |
|---|---|---|---|---|
| exact | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) |
| case | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) |
| punctuation | 39 (97.5%) | 40 (100.0%) | 39 (97.5%) | 40 (100.0%) |
| dropped-suffix | 36 (90.0%) | 40 (100.0%) | 36 (90.0%) | 40 (100.0%) |
| typo | 27 (67.5%) | 38 (95.0%) | 27 (67.5%) | 38 (95.0%) |
| brand-partial | 21 (52.5%) | 34 (85.0%) | 21 (52.5%) | 34 (85.0%) |
| model-only | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) |
| reversed | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) |
| **all (320)** | **283 (88.4%)** | **312 (97.5%)** | **283 (88.4%)** | **312 (97.5%)** |

**Read it as the upper bound it is** (D98). **A partial name is ambiguous**: `LOEWE OPTA CONCEPT 5` is the start of the `CONCEPT 5xx` models, `TOSHIBA 37 AV 5` of every `37 AV 50x` and `LORENZEN ART. NR` of `ART. NR. 3845`, `3846`, `3883` and more. The eight misses of the first five, the same in both bundles, are two typo queries (`SHADOW RC 570(DVB)`, `PANASONIC TH-50XP80U`) and six brand-partial queries (`CPC HSRC 1`, `PHILIPS DVD - R 3`, `UNITED UTV20`, `SONY KVC-29`, `CLASSIC IRC 81`, `JVC LT 42 D`). **None of them is of the classes that should not miss, and five of those answer after the first** (D352). A person typing half a model number has to be shown a list, which the answer is: five candidates, and the brand's models when none matches.

**Why the rates are not the ones D99 first printed (91.2% and 97.8%), and why they move.** The generator ranks devices by a hash of the seed, the brand and the model's id, and an id is a place in the catalog's sorted list of models, so a catalog that gains models shifts the ids after them and the draw of 40 devices a class is a different draw. Fifty-six draws now stand in this section: 91.2%, 89.7% (§29), 93.1% (§31), 87.5% (§32), 90.3% (§33), 89.7% (§34), 90.6% (§35), 90.3% (§36), 85.6% (§38), 87.5% (§39), 89.7% (§42), 89.1% (§44), 88.4% (§46), 88.4% (§48), 88.8% (§49), 88.1% (§51), 92.2% (§53), 90.0% (§56), 89.1% (§58), 91.2% (§59), 90.9% (§60), 89.4% (§61), 87.5% (§62), 90.9% (§63), 85.9% (§64), 90.0% (§65), 89.4% (§66), 89.4% (§67), 89.7% (§68), 89.4% (§69), 90.0% (§70), 87.8% (§71), 88.8% (§72), 87.8% (§73), 88.4% (§74), 90.6% (§75), 88.4% (§76), 90.9% (§77), 88.1% (§78), 88.1% (§79), 91.6% (§80), 88.4% (§81), 87.5% (§82), 86.9% (§83), 88.4% (§84), 85.6% (§85), 85.6% (§86), 85.6% (§87), 87.2% (§88), 82.5% (§89), 87.5% (§90), 90.3% (§91), 89.1% (§92), 89.4% (§93), 90.6% (§94) and **88.4% (§95)** at the first answer, about 88.9% on average. The matcher changed twice among them, at §40 (D168) and at §50 (D204, which changed none of the 320 answers of §49's catalog). **None of the eight misses of the first five is a Denon model**: the eight expected devices are IR Blaster remotes (of SHADOW, PANASONIC, CPC, PHILIPS, UNITED, SONY, CLASSIC and JVC), and none of the answers of any of the 320 queries holds one of the 8 new files (checked: each answer's remotes against the new files). **The 320 queries of this draw were also run against the catalog as it was before §95, and none of them answers differently (0 of 320, by brand and model, the first five each)**: the first-answer rate is 2.2 points below §94's and the first-five rate, 97.5%, the same, a draw (the models' ids moved by 9). The table is the bound of 320 queries it was. The 95% target is not established by this: it needs real queries.

**Not proven.**

- **Real typing.** Nobody has typed a query into this. The owner's queries are the measurement, and the file for them is empty.
- **Photo text.** The matcher is written for a provider's brand, model and lines, and tested on the lines of the small catalog and on generated text. No provider's output has been run through it; how often the right model is among a photo's lines is the evaluation of the providers, not of this.
- **Words with no digit.** A model without a digit (`Ultra`, `Streaming Stick`) is found only when its brand is named (D96, rule 4): `ultra` alone finds nothing. A model of two or three characters is found only when it is given as the model, and exact. A typed query that a brand names wrongly by a stray word takes nothing away (D96), but may add a candidate.
- **Brand-only text is hinted at, not given**: `Samsung` in a text scores 800 and as the brand 1000, so the same word is a weaker answer typed than read.
- **Kotlin.** No port exists; the vectors are what one is held to. Python's `str.lower()` and `unicodedata` are Unicode 15.0; a platform with an older table may differ for a character newer than its table, which no catalog name has.
- **Full-catalog speed on a phone.** The timings are of one machine; a phone is slower by a factor nobody has measured, and the index here is Python reading SQLite, not an app's.

---

## 25. Suggesting while a person types, and other names for a brand

The matcher of §24 answers a query that is finished. A person typing wants a list that follows them, character by character (`suggest`), and a person who types in Chinese has no use for a catalog that only knows `HISENSE` (brand aliases). This section adds both. It changes no remote file, no importer and no generated tree (`rl build --check` reports 0 differences); the bundle gains one table, additively (D101). SPEC R25 and R26 state the requirements. A later change (D106 and D107, SPEC R28) lets a caller say which brands a person already uses, so that they go first where the rules cannot tell two entries apart, and lets a service read the biggest brands' model lists once at start; it changes no answer for a caller that does not ask. The code is `src/remote_ledger/matching.py` (`suggest`, `suggest_brands`, `suggest_models`, and the alias lookups in `MatchIndex`), `bundle/aliases.py` (the list's validator and loader), `bundle/suggest_vectors.py`, and the data `bundle/data/brand_aliases.json` with its schema `schema/brand_aliases.schema.json`.

### D100 — `suggest`: the rules, and a port held to answers made from the Kotlin it came from

`suggest` first existed as a Kotlin function beside a Kotlin port of §24's matcher, written for an app that searches the catalog on a phone. When the search moved to a service it came here, so that the Python is the one reference. (D99 said no port existed; one did by then, and this is the port the other way.) The API, which mirrors the Kotlin's types:

```python
MatchIndex.suggest(query: str, limit: int = 8) -> Suggestions
MatchIndex.suggest_brands(query: str, limit: int = 8) -> list[BrandSuggestion]
MatchIndex.suggest_models(brand_id: int, query: str, limit: int = 8) -> list[ModelSuggestion]
matching.suggest(index, query, limit=8)                       # the function form, as match
Suggestions(brands: tuple[BrandSuggestion, ...], models: tuple[ModelSuggestion, ...])   # .is_empty
BrandSuggestion(brand_id: int, name: str, model_count: int)
ModelSuggestion(model_id: int, brand_id: int, brand: str, model: str,
                remote_ids: tuple[int, ...], part_number: bool)
```

**The rules** are the module docstring's S1 to S5, in order, and are the specification. In short: *brands* are the brand whose key (or alias, D101) is the query's key, then those whose key starts with it (most models first, then by name in code point order, then by id), then those the query names by a run of its words (the longest run first), then, with room left, those the matcher would read the query as (`Phillips`); *models*, when the query names a brand, are that brand's whose key starts with the rest of the query (the exact key first, then most remotes first, then by name), and when it names none the models of `match` over the query as one text, brand-only answers left out. A query with no letter or digit, or a limit of 0 or less, offers nothing; a smaller limit is always the start of a larger one (tested).

**How it was held to the Kotlin.** The Kotlin was run once, over the real bundles, through its JDBC test source, on a throwaway list of queries, and Python answered the same list: `suggest` (brands and models, with their ids, remote ids and the part-number flag), `suggest_brands` against the brands of `suggest`, `suggest_models` for a brand and a start, and `match` as typed text (its top five, with scores and evidence), each compared field by field as JSON.

| | queries | compared | differ |
|---|---|---|---|
| Selected bundle `c5da65940468` (36 brands, 105,233 models) | 36,305 | 139,187 | **0** |
| Full bundle `41c99342480e` (5,028 brands, 276,247 models) | 61,870 | 217,765 | **0** |

The queries: every brand name of the bundle (also lower and upper case, as the key, and with spaces around), every brand name typed a character at a time (every brand of the selected bundle; 800 of the full one), brand typos, up to 3,200 models spread over the brands (as written and in lower case), 260 of them typed a character at a time and 1,140 more at a few lengths, a brand and the model typed so far (6,000, and 60 typed a character at a time), the model before the brand, typos of models (a character dropped, swapped, changed, added, and with a brand), a dropped suffix and a longer label, queries that match nothing, **every one-character and two-character query over letters and digits** (1,368) and three-character ones, punctuation, spaces, full-width and accented and Greek and Cyrillic and non-BMP text and combining marks, more words than the matcher looks at, two and three brands in one query, a few dozen typed phrases, the ledger's real queries (none yet: D98), models of one brand through `suggest_models` (2,011 and 9,905 calls, limits 3, 8 and 60, a brand that does not exist), and limits of 1, 2, 3, 5, 20 and 50 on 300 queries each and of 0, -1 and -100 on six. Limit 8 is the default and most queries. **Every brand and model, in every order, was the same**: the ties of model count and of remote count, the exact key first, code point order against UTF-16 order, and the two places where a limit stops a list. Run again on the bundles the new exporter writes (D101), the Kotlin, which reads `brands`, `models`, `controls` and `ngram` of format version 1 and never the new table, gives output **byte-identical** to what it gave on the old bundles, and Python on them differs from it only for the 31 queries per bundle that hold Chinese characters, where Python now finds the brand (D102).

**What the Kotlin does that looks odd, and is kept**, because the behaviour is the specification and the Kotlin is not the thing to second-guess here:

- A brand typed twice loses only its first word to the brand: a run's key counts once, at its first place (`runs`), so the second `lg` of `lg lg 42` is part of the rest (`lg42`) and no model starts with it.
- The brand names are looked for in the first twelve tokens only, but **the rest** of the query is made from every token, not the first twelve.
- A brand named by a run is offered after every brand whose key starts with the query, however many: with a limit the named brand can be cut by brands that merely start alike.
- The matcher's reading of the query (`Phillips`) is asked only when the list is not full, and never for one character.
- Inside a brand a typo is not forgiven (`kd50` offers no `KD - 49`): it is a start-of-key match, which is what typing wants; `match` forgives typos once the person has typed it all.
- A model with no digit is offered through a named brand (`roku ul`) but not alone (`ultra`, S5), because that is `match`'s rule 4.

**The vectors left behind**: `tests/vectors/suggest_vectors.json` (`rl bundle suggest-vectors --file FILE`, `--check` compares) holds 238 cases of `suggest` and 26 of `suggest_models`, written by the Python once it gave what the Kotlin gave, over a catalog of 85 entries (the small catalog of D97, a few brands more) and 14 brand aliases (D101), self-contained and independent of the data. Each case has a note that says which rule or tie-break it shows; six queries are typed a character at a time (76 cases); limits of -3 to 20, among them every limit that falls on the edge between two brands' models; brands, models with their ids and remote ids, a part number. `tests/test_suggest.py` holds the file to the code exactly, to its notes (each rule and tie-break must be among them), and to an independent reference written in the test (what is offered inside a brand and the brands that start a query, worked out again from the entries). The 210 of its 264 cases with no Chinese in them were also answered by the Kotlin over its small catalog: all equal. The ids in the file are the small catalog's numbering (`MatchIndex.from_entries`): brands from 1 in the order of their search keys, models from 1 in that order inside a brand, which a tie that goes to the lower id depends on.

**Speed**, Python reading SQLite on the 64-core development machine: opening the index 1 ms (selected) and 29 ms (full); `suggest` at limit 8 over 6,000 typing queries median 0.04 ms (selected) and 0.13 ms (full), 95th percentile 2 ms and 0.5 ms; reading a brand's whole model list takes 35 ms for Samsung's 21,512 models and making their keys 20 ms more, once (16 lists are kept), after which a keystroke inside it takes about 2.5 ms. A service that wants the first keystroke inside a big brand quick can ask for it once at startup (`warm`, D107).

**Not proven.** The queries are made from the catalog's own names by mechanical rules, as D98 says of the evaluation: they say the two implementations agree, not that people are offered what they want. Python's `lower()` and `unicodedata` are Unicode 15.0, which the Kotlin matched for every code point; a platform with another table may differ. Nothing was run on a phone.

### D101 — Brand aliases: the list, the table, and why the format is still version 1

**The list**, `bundle/data/brand_aliases.json`, has a schema (`schema/brand_aliases.schema.json`) and a validator (`bundle/aliases.py`) like the key vocabulary of §22. The file is `{"header": {...}, "brands": [{"brand": ..., "aliases": [{"name": ...}, ...]}, ...]}`. A brand, spelled as the catalog spells it and matched by search key, has its aliases: other names a person types, **each written out as it is typed**. `创维` and `創維` are two entries, `索尼` and `新力` two more; nothing converts Traditional to Simplified characters when a person searches, so a name that is not listed is not found, and a mistake in the list is a mistake in one entry. **The exporter reads `brand`, `aliases`, `name` and the header's `status` and `intentional_shared_aliases`; every other field is information for the person who reviews the list and is allowed and ignored**: the tags of a name (`script`: `hans` Simplified, `hant` Traditional, `both` the same in the two; `region`: `CN`, `TW`, `HK`, `SG` or `any`; `confidence`: `certain` or `likely`), a brand's counts of `models` and `remotes`, the header's `date`, `method`, `counts`. The shape is the one a reviewed list of the whole catalog arrives in, so that it drops in as the file without conversion (the loader reads a draft of 132 brands and 225 aliases in that shape, and a full bundle builds from it with no problem and no note). **The list that shipped with this decision was a seed**: its header said so (`"status": "seed"`), it held 33 names of 20 brands that are well known (Hisense, Xiaomi, Skyworth, Changhong, Haier, Midea, Gree, Konka, Samsung, Sony, Panasonic, Sharp, Toshiba, Philips, Apple, Huawei, Yamaha, Pioneer, Denon, Onkyo) and nobody who reads Chinese had reviewed it. D108 replaces it with the reviewed list.

**The rules of the data**, each a message of the validator (the module's docstring has them): an alias has a search key of two characters or more (a key of one is never looked up); **its key has no letter or digit of ASCII** (D102 says what this buys); no key is listed twice under one brand; a brand is listed once; **an alias key under two brands is an error unless the header declares it** (`intentional_shared_aliases`, below); **an alias is never the key of a brand of the catalog**, which is checked when a bundle is built against every brand of the ledger.

**An alias that two brands share, on purpose.** The catalog spells one company two ways in a few places (`WESTERN DIGITAL` and `wd`), and a Chinese name of the company belongs to both. The header's `intentional_shared_aliases` maps such an alias to the brands that share it (`"西部数据": ["WESTERN DIGITAL", "wd"]`); the list then holds it under each of them. The validator requires the declaration to name exactly the brands the list has the alias under (by search key), and says so when it names others, fewer, or an alias that no brand has; **an alias under two brands that the header does not declare stays an error**, because two brands with one name is otherwise a guess. A shared alias names every one of its brands (D102). A schema problem or a broken rule stops the build and names the entry. **A brand the list names and the catalog lacks is reported and is not an error**: `rl bundle` prints `note: the brand 'Gree' of brand_aliases.json is not in the catalog, so its aliases (1) are not in the bundle`, and the report says how many aliases a profile left out because it does not carry their brand (the selected bundle has 24 of the 33, the full one 32).

**The table**: `brand_aliases(alias TEXT, norm TEXT, brand_id INTEGER, PRIMARY KEY (norm, brand_id)) WITHOUT ROWID`, one row per alias of a brand the bundle carries: the alias as written, its search key, and the id of its brand (an alias that two brands share is a row for each; a profile that carries one of them has the row of that one). `meta` has `count.brandAliases`, the manifest's `counts` has `brandAliases`, and the table is one of those `dataVersion` digests (`writer.DIGEST_TABLES`), so adding or changing an alias is a new `dataVersion` and, because the digest now names the table, **every bundle built with this exporter has a new `dataVersion` even where no row of the catalog moved** (the selected bundle `c5da65940468` is `f3e4567134c8`, the full one `41c99342480e` is `6eb6823f9a51`). The files grow by one page (19,443,712 to 19,447,808 bytes selected, 50,311,168 to 50,315,264 full; the gzip of the full one is 12,741,285). `rl bundle --verify` recomputes the rows from the list that ships and the brands of the bundle, and checks that no alias is a brand's own key, that every key is its alias's, and that the brand exists; a bundle with no such table is told to be built again.

**`meta.schemaVersion` stays 1, and so do `user_version` and `application_id`.** The rule, from now on: *a change that removes or alters what a version-1 reader reads raises the version; adding a table or a `meta` row does not.* A version-1 reader reads `brands`, `models`, `controls` and `ngram` by name and column and the rest of the tables the same way, and ignores a table it does not know; this one is read only by a reader that wants it. The proof is not an argument: the Kotlin matcher, which reads those four tables and was written before the new one existed, was run over the 98,175 queries of D100 on the bundles with the table, and its output is **byte-identical** to what it gave on the bundles without. A bump to 2 would have made every reader of version 1 refuse a file it reads perfectly, in exchange for telling it something it can learn from `meta.count.brandAliases` or from `sqlite_master`. The cost is that "format 1" now comes in two shapes, with the table and without; **a reader that wants aliases must check for the table** (`MatchIndex` does) and one that does not never needs to know. A bundle written before the table works in the matcher (it has no aliases) and is refused by `--verify` as stale.

### D102 — The matcher reads an alias as a brand's name; Han ideographs are cut from what is next to them

**Where an alias counts.** `MatchIndex` reads the table when it opens (`alias_by_key`, `aliases`) and holds one map from every key that names a brand, its own or an alias's (`name_by_key`); a brand's key wins a clash, a row whose brand the bundle does not have is ignored, and no table at all is no aliases. Every lookup that asked "is this key a brand's?" asks that map, at the same weights and with the same evidence words: the `brand` given (exact: 1000, `brand:given`), a run of it (900), a run of a text (800, `brand:text`), the brands a suggestion names by a run (S2), the start of a key (S1: an alias's key that starts with what was typed offers its brand, so `海` offers Hisense and Haier), and rule 4's "never a model key when it is itself a brand's key". The brand then **answers as any brand does**: a query with no model gives the brand-only answer listing its models, `suggest` offers the brand and its models for the rest of the query. **An alias that two brands share names both** (D101): `name_by_key` maps a key to the ids of its brands in order, every lookup adds each of them at the same weight with the same evidence, so a query for it answers with both brands (a brand-only answer lists each one's models, in the order of the brand ids), `suggest` offers both brands (S1 by models and name, and as named brands by id) and the models of the first up to the limit and then of the second, and the words of the alias are the brands' words in S3. **Aliases are exact**: they are not looked up by similarity, because two characters is all `海信` has and one edit is already half of it. A key must have two characters, as a brand's must.

**A search typed in Latin letters is what it was.** An alias's key holds no letter or digit of ASCII (D101), so no query made of Latin letters and digits can equal one, start one or hold a run that does, and the new code is a lookup that fails. That is the proof; the tests and the Kotlin check it: the first 41 queries of `matching_vectors.json` (everything D97 had) are byte for byte the file's earlier content, with and without the table, and the Latin ones give the same on an index with and without aliases for 1,500 random queries (`match` and `suggest`, with and without a given brand and model) and for the 320 generated queries of the evaluation and every third prefix of each over the real selected bundle; and over the 98,113 queries of D100 that hold no Chinese character, the Python on the bundles with the table gives what the Kotlin gives, **0 differences in 356,704 comparisons**.

**Han ideographs are cut from what is next to them** (rule 1). A person typing Chinese does not put a space between a name and a number: `海信55E7` is one run of letters and digits, and so would be one token that equals no alias and no model. A run of Han ideographs (the CJK Unified Ideograph blocks, `matching.HAN_RANGES`) is now a token of its own and a letter or digit next to it starts another: `海信55E7` is `海信` and `55e7`, `Sony索尼KD-49` is `sony`, `索尼`, `kd`, `49`. The key of a text is still its tokens joined, and **a text with no ideograph has the tokens it had**. This rule is not in the Kotlin, which is why the 31 queries per bundle that hold Chinese differ from it (above). It is a rule of its own and can be removed without touching anything else here (`tokens`).

**What is not in this version**, so that nobody looks for it: no Traditional-to-Simplified folding, no Pinyin (`haixin`), no Chinese word for the kind of device (`电视`, `空调`: `海信电视` is one token and names nothing), no Chinese-only model names, no alias with a Latin letter or digit in it, and no similarity for an alias (a typo of `创维` finds nothing). Models stay Latin, so a person types a Chinese brand and then the model.

**The vectors have Chinese cases.** `matching_vectors.json` (D97) keeps its 41 queries and gains 21: an alias alone (Simplified, Traditional, a regional form), as the given brand, with a model number with and without the space, a model of another brand, two aliases, an alias with a brand's name, an alias that is also part of a model name (the model that holds it is found as well), words that are no alias, a name that only starts with an alias, one character, and an alias that two brands share (alone, given, with a model of one of them); the file also states the aliases (`aliases`) and `HAN_RANGES`. `suggest_vectors.json` has 54 of its 238 cases with Chinese, including both scripts of Skyworth, `海信 55E7`, typing an alias a character at a time and a name that is no alias.

**Not proven.** The names of the seed are written from what is commonly known and the Traditional forms with care, not converted by a tool and not read by a native reader of both: the list says so in its header, and the owner's review is the check that matters. Whether a person who types a Chinese name is offered what they want depends on that list. (D108: the list has since been reviewed.)

### D106 — `suggest(..., prefer=)`: a hint that replaces only the last tie-break

An app keeps, for a person, the brands they have already set up, and a service that answers its search wants `s` to offer *their* Sony before a Samsung that has the same number of models. A first version of that was written outside this repository as a copy of the index whose class overrode three of the matcher's private methods (`_named_by_runs`, `_rank`, `_brands_for`) and so depended on the exact text of the code it overrode. This is the same rule as a public argument, so that the next change here cannot break it silently. SPEC R28 states the requirement; the rule is S6 of the module's docstring.

```python
MatchIndex.suggest(query: str, limit: int = 8, prefer: Iterable[str] = ()) -> Suggestions
MatchIndex.suggest_brands(query: str, limit: int = 8, prefer: Iterable[str] = ()) -> list[BrandSuggestion]
MatchIndex.brand_ids(names: Iterable[str]) -> frozenset[int]      # which brands the names stand for
matching.suggest(index, query, limit=8, prefer=())                 # the function form
```

**The rule.** `prefer` is the names of brands. A name is a brand's by its search key, as a given brand is (rule 3): case, spaces, accents and punctuation do not matter, an alias names its brand (`索尼` is Sony), a name two brands share names both, and **a name that is no brand's key is ignored: there is no similarity** (`Samsun` is nothing; a guess would move entries the person did not choose). The names are a set: their order, their number and a name given twice mean nothing. The ledger does not cap the number; a caller that takes them from outside decides how many it will accept. A single string is refused (`TypeError`), because `prefer="Sony"` would otherwise be read as four names of one letter and silently do nothing. **The hint replaces only the last tie-break of the order, and only between entries that the rules before it treat as equal**; inside each run of such entries the hinted ones come first and the others follow, each group in the order it had without the hint:

| list | the entries tie when | the order broke the tie by | with a hint |
|---|---|---|---|
| brands (S1) | their key, or an alias's, is the query's key | most models, name, id | the hinted brands first |
| brands (S1) | their key, or an alias's, starts with the query | most models, name, id | the same |
| brands (S1) | the query names them by a run (S2) of the same length | id | the same |
| brands (S1) | the matcher only reads the query as them (`Phillips`) | similarity, id | **never moved** |
| models, a brand is named (S3) | their brand is named by a run of the same length | the brand's id | the hinted brand's models first, each brand's own order (S4) kept |
| models, none is named (S5) | score, similarity and key length are equal | brand name, model name, id | the hinted brands' models first |

The tie-break that is replaced is the one that stands in for popularity (most models) or for nothing at all (a name, an id): none of it says which brand the person means, and the hint does. **What the hint never does**: remove an entry, add one, or lift an entry above a better match. The brand whose key is the query stays before every other (`al` with `ALPS` is `AL`, `ALPS`, then the rest); a brand named by a shorter run stays after one named by a longer; a model that matches worse (`TX10001` for `tx1000`: only the start of a longer model) stays after every one that matches better, hinted or not; the models of one brand are never reordered (S4: a hint is about brands); a brand the matcher only reads the query as is never moved, because the person did not type it. A hint for a brand that the query does not offer adds nothing.

**The limit.** A list is cut after its order, so the hint can change *which* entries are inside the limit, among entries of one run and nowhere else. Write the answer without a hint for a very large limit as runs of tied entries; with a hint the answer is the first `limit` entries of the same runs, each re-ordered, the hinted first. Every entry before the run that holds the cut is the same entry in the same place; inside that run the hinted entries take the places first. So a hinted entry can push out an entry of its own run and nothing else (`al`, limit 2: `AL, ALPINE` becomes `AL, ALPS`; limit 1 stays `AL`), and a smaller limit is still the start of a larger one. The hinted list is made whole and then cut, which is why a smaller limit is the start of a larger one.

**How.** `brand_ids(prefer)` is the set of ids, and that set is passed to the three places where the order is made: `_named_by_runs` sorts `(-length, not hinted, id)` (which gives S1's third group and S3's order of brands at once), `_brands_for` sorts the first two groups `(not the exact key, not hinted, -models, name, id)` and leaves the matcher's reading where it was, and `_hinted` re-orders `_rank`'s models inside each run of equal score, similarity and key length. With `prefer` empty, or naming no brand, the set is empty and every key is the one it was. Nothing is stored: the hint is an argument and the index has no state of it; `match` has no hint (what a photo says is not a preference).

**Checked.**

- *The same rule as the earlier version.* The earlier version (the override, with its own tests and an independent oracle) and this one were asked the same 158,923 questions: the 134 cases of the new vectors, six generated catalogs made of ties (brands that start alike, models several brands have, aliases that two or three brands share) with about 11,000 questions each, the vector catalog, the catalog of ties and the four real bundles (selected and full, before and after the alias table; 15,000 or 25,000 questions each, hints of one to five brands, aliases and unknown names among them, limits 1 to 1,000), with a hint that names brands in the answer, brands that are not, and junk. **0 differ**; the hint moved the answer in 21,687 of them, so the comparison is not empty. The script is not committed (it needs the other implementation); the vectors are what is left of it.
- *No hint is today's answer.* A copy of `suggest` as it was before the hint, in the test, gives the same answer as the code for 4 generated catalogs, 1,305 random queries at six limits (7,830 comparisons), with no `prefer`, with names that are no brand, and (a different path through the same code) with every brand hinted; and the 238 cases of `suggest` that the file held before are byte for byte what they were (the diff of the vectors only adds), also with an unknown name.
- *The rule, by hand and by oracle.* `tests/test_suggest_prefer.py` has the rows of the table above written out by hand on the catalog of ties, and an oracle that works out the run of every entry of an answer from the index's keys and the ledger's run rule, never from the hint's code, and checks the whole answer and the limits 1, 2, 3, 5, 8, 13 and 20 for 3,593 pairs of a query and a hint: 853 generated queries on two catalogs, the 191 queries of the vectors on the vector catalog, 49 on the catalog of ties, the 134 cases of the new vectors and 11 queries on the real selected bundle. A test shows that the oracle tells a wrong ordering from a right one.
- *The vectors of the hint*: `suggest_vectors.json` gains a section `prefer` of 134 cases over two catalogs, the 79 entries and 14 aliases of D100 and `TIES` (36 entries and 4 aliases, in `bundle/suggest_vectors.py`) made of ties for a hint to break, which the first catalog has few of. Each case has a note and says which catalog it asks; they cover every row of the table, the limit's edge, an alias and a shared alias, names compared by key, unknown names, the order of the names, and brands typed a character at a time. The 238 cases above and the file's other keys are unchanged, so a port that does not implement the hint ignores the section. The answers were also given by the earlier version over the same two catalogs: 134 of 134 equal. `rl build --check` reports 0 differences.

**Speed.** On the full bundle, 4,700 typed prefixes of brands and models, limit 8, a hint of five brands: median 0.21 ms, 90th percentile 0.54 ms, 99th 2.0 ms, with or without the hint (0.21, 0.59, 2.05 without). The selected bundle is the same to the digit's rounding. The earlier version asked for every brand of S1, the matcher's readings included, and cut afterwards; this one asks for those only when there is room, as without a hint.

**Not proven.** That people are better served by the order: the rule is the most cautious one that moves anything (a tie-break and nothing else), and whether `most models` was the right default is a question for the plain order too. No port to another language exists, and the Kotlin of D100 does not have the hint; the vectors are what one is held to.

### D107 — `warm`: the biggest brands' model lists are read once and kept

Inside a brand `suggest` needs the brand's whole model list, most remotes first (S4), and reads it from the bundle the first time somebody types inside the brand, then keeps 16 lists (`BRAND_LISTINGS`, least recently used out). On the full bundle, on the development machine, the first request inside Philips (24,362 models) takes 68 ms, Samsung (21,512) 51, Grundig (13,752) 34, Sony (12,031) 33, LG (10,783) 25, against 1 to 3 ms afterwards. Sixteen requests inside other brands empty the cache, and the biggest brand is a cold read again (57 ms in the measurement below). A service that wants the first keystroke inside a big brand quick needs those lists read before anybody types and kept.

```python
MatchIndex.warm(count: int = WARM_BRANDS) -> int      # WARM_BRANDS = 16; returns how many brands are kept
```

`warm` reads the whole model lists of the `count` brands with the most models (ties to the lower id; a brand with no model is not one), makes their search keys, and keeps them in a dictionary that is **outside the cache**: `_brand_list` looks there first and falls back to the cache. So other traffic can never evict them, and they take none of the cache's 16 places, which stay for the other brands. It changes no answer (tested on a warm and a cold index, with and without a hint, over the 238 vectors and `match`'s brand-only answers, which list from the same kept list), only how long one takes. Calling it again replaces what was kept (`warm(count)` with a different count, `warm(0)` forgets); it is not for two threads at once, like the rest of the index, and meant to be called once at start before the index is shared. **Why not a bigger cache**: the cache evicts by recency, so it would keep whichever lists were typed in last, not the ones that are expensive and likely; sixteen of 5,028 brands is a guarantee and a bigger cache only moves the number.

Measured, full bundle (132,828 models in the 16 biggest brands): `warm()` 301 ms once (20 ms to open the index), resident memory 60 to 92 MiB (+32 MiB), the first request inside each of the five biggest brands 3.4, 2.8, 2.3, 2.0 and 1.3 ms; after 100 requests inside other brands, a request inside Philips 3.1 ms where the unkept index takes 57 ms. Selected bundle (36 brands, 102,969 models in the 16 biggest): 226 ms, +25 MiB. `tests/test_suggest_prefer.py` counts the reads of a whole list (the statement the reader runs): 16 for `warm`, none for a request inside a kept brand after three passes over twenty-five other brands, one for a brand that is not kept and none for its second request. Not measured: a phone, and a bundle bigger than the full one.

### D108 — The reviewed list of Chinese names ships

**What ships.** `bundle/data/brand_aliases.json` is no longer the seed of D101. It holds **133 brands and 226 names**: the 132 brands and 225 names of a list drafted for the whole catalog, and Gree (格力), which the seed had and the catalog does not carry. Its header says `"status": "reviewed"`, `"date": "2026-10-07"`: a reader of Chinese went through the list on that day and accepted it as it stands, with the remark that the names need not be exact to the last one. It is a good list, not a perfect one, and the header says that the 50 names tagged `likely` were not fully confirmed. Every name of the seed is still in it; two of its brands gained a name (`樂聲牌` for Panasonic in Hong Kong, `山葉` for Yamaha in Taiwan). A reader who finds a wrong or missing name fixes it by editing one entry; nothing else has to change (D101).

**How it was made.** The draft was written for the brands of the full bundle, every spelling of the catalog read by eye. A name is in only where it is the established Chinese name of the brand's electronics in at least one Chinese-speaking market; where that could not be confirmed it is left out. The Simplified names are the author's, checked against the Chinese Wikipedia and shops' and companies' pages where they were not certain. The Traditional forms were made by OpenCC (`s2twp`, compared with `s2t` and `s2hk`; the three agreed for every name but three, decided by hand: 西部数据, 华为, 丽台) and read by eye, because the tool does not know brand names. Regional names that are not a conversion (新力, 國際牌, 樂聲牌, 輝達, 威騰, 山葉, 百靈, 安麗, 凱蒂貓, 吉蒂貓, 麗台) are written out by hand. Every name has two characters or more and a Han ideograph, as the validator requires.

**Choices that were judgement calls**, so that nobody has to guess them. *Ordinary words* are in where they are the name people use for the brand: 苹果 (Apple), 小米 (Xiaomi), 山水 (Sansui), 先锋 (Pioneer). They are out where the word means something else first: 现代 (Hyundai, the car maker), 兄弟 (Brother), 创新 (Creative), and 博士 alone for Bose (博士音响 is in). A person who types one of these and finds nothing can ask for it to be added. *Two names in one script* are two entries (宏碁 and 宏基 for Acer). *One company the catalog spells twice*, `WESTERN DIGITAL` and `wd`, has its three names under both brands and declared in `intentional_shared_aliases`; a search for any of them answers with both brands (D101, D102). 肯特 (Grundig in the mainland, according to the Chinese Wikipedia) is not in: its author was not sure of it.

**What it does to the bundles.** The full bundle (5,028 brands) carries 225 of the 226 names (Gree is the one note), the selected one 44 (181 names belong to brands the profile leaves out, 1 to the brand the catalog lacks). Built from the tree after D103 to D107 were in: the full bundle is 50,663,424 bytes (13,156,342 gzipped), dataVersion `936023abe5fa`; the selected one 19,468,288 bytes, dataVersion `d18cbf35cd0d`; `rl bundle --verify` passes on both. **Every bundle built from here has a new `dataVersion`**, because the table is one of the digests (D101); these two figures move again with the next change to the catalog.

**Proven.** On the real full bundle, each of the 225 names, typed alone, makes `suggest` offer its brand and makes `match` given that name as the brand name it (0 of 225 do not), in Simplified and in Traditional, for the regional forms, and for the shared names (西部数据 and 威騰 offer both `WESTERN DIGITAL` and `wd`). A name followed by a model number finds the model (`索尼 KD-49`, `三星 UN50`). The tests of the list assert what the seed had, the counts, the tags of regional names, the company with two spellings and that no two brands share a name that is not declared.

**Not proven.** That the list is complete (the catalog has 5,028 brands and 132 have a Chinese name here; most of the others have none that people use, and a well-known brand that is missing is a name to add) or that every name is the one people type today. The first real searches will show; a missing name is one entry.

---

## 26. Folding the protocol fragments of a device into one remote

The IR Blaster import writes one file per database id and ledger protocol (D47): the remotes of one database id that have keys in Sony12 and in Sony15 are `irblaster/AIWA/2312-Sony12` and `irblaster/AIWA/2312-Sony15`, and the bundle carried each as a remote of its own. A service that lists the remotes of a device and asks a person to try one then shows several rows that are one physical remote, and some of them cannot be tried at all: it asks the person to send one key (Power, else Power off, Power on, Volume up, Mute, in that order: the **test key**) and a fragment with none of them has nothing to send. This section changes the exporter so that a fragment of that kind is carried by a sibling. It changes no remote file, no importer and no generated tree (`rl build --check` reports 0 differences: the merge exists only in the bundle); the bundle gains one table, additively (D105). SPEC R27 states the requirement. The code is `src/remote_ledger/bundle/merge.py` (the rule), `catalog.py` (`collect`, `assemble`), `writer.py` and `verify.py`.

### D103 — Which fragments fold into which: the rule, and the data it was chosen on

**The problem, measured.** In the bundle as D95 describes it, 1,199 of the full bundle's 13,217 remotes (9.1%) and 740 of the selected bundle's 4,394 (16.8%) have no key of the test order, so a person cannot be asked to try them. Of those, 458 and 335 are a fragment of a device whose other fragment has a test key, and in the lists of the 100 models with the most remotes, 155 of the 202 rows that cannot be tried (full) and 192 of 231 (selected) are such fragments.

**The rule**, in `merge.fold`. Each line below is one decision of the code, so that the owner can widen it by changing one:

1. A **device** is the files of the IR Blaster import that share their path without the protocol (`irblaster/AIWA/2312`) and the same maker, product list and aliases. A file of any other source is a device of its own (only the IR Blaster import writes a file per protocol).
2. A fragment is **testable** when it has a key of the test order (`merge.TEST_KEY_ORDER`; a key is its canonical id, D84). **Only a fragment that is not testable is folded, and only into a testable sibling.** Two testable fragments are never folded into one another.
3. The sibling must have **the same carrier and the same play rule**: `carrier_hz`, `repeat_passes`, `helper_repeat_passes`, `intro_empty` and `rule` (D89, D78), so that a key that moves is played by its new remote exactly as its own played it.
4. When several siblings qualify, the one whose **test key is best in the order** (Power, Power off, Power on, Volume up, Mute) takes it, and where two have the same, the **lowest remote id** (the first in path order). A folded fragment is never a target, so nothing chains.

**The data, both profiles, the rule as it stands** (`merge.fold` over the whole ledger; `selected` counts the files its brands carry, D92; since D117 the selected profile carries every brand, so its column is what it was before and the full column is both, remeasured at §38):

| | full | selected |
|---|---|---|
| IR Blaster devices | 9,381 | 2,549 |
| devices with two or more fragments, and their fragments | 617, 1,320 | 364, 798 |
| fragments with no test key, and with one | 524, 796 | 348, 450 |
| fragments with no test key that **can be folded** | **357** | **298** |
| of those, with more than one qualifying sibling (best key decides in 8 and the lowest id in 4, both profiles) | 12 | 12 |
| fragments with no test key that **cannot** | 167 | 50 |
| no sibling with a test key | 21 (10 devices) | 13 (6 devices) |
| only siblings at another carrier | 65 | 27 |
| only siblings with the same carrier and another play rule | 81 | 10 |
| pairs of testable fragments | 189 | 92 |
| of those, with the same carrier and play rule (a full merge would join them) | 36 | 32 |
| of those, with the same test key and different signals | 10 | 8 |
| rows a **full** merge (every fragment of one carrier and rule into one remote) would remove | 400 | 336 |
| rows the rule removes | 357 | 298 |

In words: 9,381 devices are in the ledger as IR Blaster remotes, and 617 have two or more fragments (1,320 fragments). 524 fragments have no test key and 796 have one. 357 of them can be folded, 12 have more than one qualifying sibling and 167 cannot: 21 have no sibling with a test key, 65 only siblings at another carrier and 81 only siblings with another play rule (the NEC family: an NEC1 fragment beside an NEC2 one has the same carrier and a different intro; Sony fragments nearly always share both). Of the 189 pairs of testable fragments, 36 pairs of testable fragments share a carrier and a play rule, and 10 of them have the same test key with different signals: a Power in two Sony protocols for one device in seven, a Power in RC5 and another in RC6, and a Power in NEC1 and another in NECx1 (twice). The other 26 are a Power in one fragment and a Volume up (22) or a Mute (4) in the other. A full merge would remove 400 remotes, 43 more than the rule does (36 are those pairs, 7 are fragments none of which has a test key, so that the merged remote could still not be tried).

**Why the conservative rule, and why the data does not say it is wrong.** The physical remote has both sets of keys, so a fragment with no test key loses nothing when it rides on a sibling: the sibling's test key is still the one tried, and its keys are all there. Two fragments that each have a test key may be **two encodings of one key**, and a device may answer to only one of them: 10 devices have a Power in two protocols with different signals, and a full merge would join 36 pairs, 26 of which put a Volume up or a Mute in the shadow of a Power in the other encoding. Folding them would remove a signal that a person could try, and the device may answer only to the other. The data shows the case is real, and mostly Sony, so the rule keeps each of them a row; that costs 43 rows the full merge would remove, which the table counts so the owner can see them. The measurement does not show the rule is wrong in the other direction either: a folded fragment has no key to try, so folding it takes no row a person could try away (the 12,018 remotes that can be tried before can be tried after, with the same test key and signal; a test holds this over the whole ledger).

**What it leaves, and why.** 167 fragments stay a row that cannot be tried (146 of them with a testable sibling that plays differently). They need a remote that has a carrier and a play rule **per key**, which the format does not have: that is a change of format and not of this rule. 21 have no sibling with a test key at all, and a wider test key rule (Volume down, Channel up, Play, Stop) would reach some of them; that is a different question from folding and is not touched here.

**To widen the rule**: line 2 is the only one that keeps testable fragments apart, and it is in `fold` as the two lines that skip a fragment that has a test key and take only the testable ones as targets. Letting every fragment of one carrier and play rule fold into the best-keyed one is that change; `test_design_quotes_the_numbers_of_the_measurement` recomputes the table above, so the numbers are then re-measured and not guessed. To make the choice among several siblings different, line 4 is one `min` over `(position of the test key, id)`.

### D104 — The merged remote: what it holds, what stays and what moves

- **The carrier is a file of the ledger.** The remote that carries a folded fragment is the testable sibling's, with **its own ref, id, maker, model, source, carrier and play fields, and protocol** (the protocol of the file with the test key: the name of the folded fragments' protocols is in their refs, which `remote_refs` keeps, D105). The ref is a path of the ledger, so `rl bundle --verify` still finds the file, "a remote's ref is its name across ledger versions" (D89) still holds, and a count kept by ref and test key (a person confirmed Power of `irblaster/AIWA/2312-Sony12`) is still that remote's, because the test key does not change (below). The remote id is still **the position of its file in the path order of the whole ledger**, in both profiles (D89): the folded fragment's id is not reused, so ids have gaps (12,902 remotes in the full bundle have ids up to 13,217) and a remote that nothing was folded into has the id it had.
- **Keys**: the carrier's own keys first, in their order, then each folded fragment's, in the order of the fragments in the ledger (path order), each in its own; `n` runs from 0 without a gap, and `key_count` is the number of rows. `tier` is the weakest tier of all of them (D89's rule for a remote, over more keys). **A canonical key that both have is kept twice**: the carrier's has the lower `n` and so is the one a reader uses (D85), and the folded fragment's stays after it, because it is another signal for the same key (214 keys of 68 folded fragments in the full bundle, 168 of 64 in the selected; **no key of a folded fragment has the same canonical key and the same signal as the carrier's**, so nothing is a copy). Nothing is dropped: the keys of the bundle are the keys of the ledger, 525,084 and 165,646, and a test checks the multiset of (canonical key, signal, tier) of the files against that of the remotes.
- **The test key is unchanged.** A fragment is folded only if it has none of the five canonical ids, so the merged remote's test key, its signal and its alternative are the carrier's. A reader that built its list from the old bundle gets the same test signal for every remote that could be tried (12,018 of them in the full bundle, 3,654 in the selected one), and the same distinct test signals (4,730 and 1,731).
- **Models, brands, grams and signals are the same rows.** The exporter counts brands and models over the files, as it did, and only the remote a link names changes: a model's `controls` rows name the carrier in place of a folded fragment, once (306,581 rows become 295,313, and 120,902 become 110,393). The tables `brands`, `brand_aliases`, `models`, `ngram`, `signals`, `vocab_*` and `excluded_brands` are **row for row what the same tree gives with no folding** (a test builds both and compares), so `dataVersion` moves only through `remotes`, `keys`, `controls`, `remote_refs` and the counts. The matcher gives the same answers for every query with the remote ids of folded fragments replaced by their carrier's (tested over the real selected bundle on generated queries), and `suggest` offers the same brands and models; **a model's place among its brand's suggestions follows its number of remotes (D100), which falls where it listed a device twice**, so the order of models can differ from before and a limit can cut another.
- **The selection does not move.** `select.choose` reads the files, as it did: the same brands are chosen (a test compares both builds) and the estimate is within the 2.5% of D92 (the file is 19,468,288 bytes, under the 20,000,000 of D92). The `sources` table counts remotes as rows of `remotes`.
- **What it did to the lists a service builds**, measured with the ranking of a service that gives a person the remotes of a model (a script run on both bundles; its code is not in this repository): the 100 models with the most remotes, **the same 100 before and after**:

  | | full, before | full, after | selected, before | selected, after |
  |---|---|---|---|---|
  | remotes in the bundle | 13,217 | 12,902 | 4,394 | 4,096 |
  | remotes that can be tried | 12,018 | 12,018 | 3,654 | 3,654 |
  | remotes with no test key | 1,199 (9.1%) | 884 (6.9%) | 740 (16.8%) | 442 (10.8%) |
  | of those, a fragment of a device whose other fragment has one | 458 | 143 | 335 | 37 |
  | rows in the 100 lists (identical test signals collapsed) | 542 | 393 | 462 | 272 |
  | rows that cannot be tried | 202 | 53 | 231 | 41 |
  | models with at least one such row | 65 | 19 | 86 | 14 |
  | models with nothing to try (all the catalog's models) | 1,192 | 1,192 | 636 | 636 |

  The 149 of 155 (full) and 190 of 192 (selected) rows that a fragment of one carrier and rule made are gone, as the measurement of D103 said they would; the rows left are those of the 164 and 50 fragments that play differently. **A device with nothing to try stays one**: folding gives a remote a test key only by never taking one away, so it cannot help a device none of whose fragments has a Power, Volume up or Mute key (1,192 models in the full bundle). The 100 models with the most remotes are other models after the merge (the most any has is still 11 remotes); chosen again over the new bundle they are 34 (full) and 35 (selected) models with such a row, 76 and 73 rows of 469 and 355.
- **The vectors of D91 were regenerated** (`rl bundle vectors`): the file had to follow, because `--check` compares it with the full bundle, and 5 of its 151 vectors moved (a vector for a key of a folded fragment names the carrier's remote and protocol and the key's number there, and a folded fragment's own ref is no remote any more); the 29 protocols and every case D78 names are still covered, and the blobs are self-contained as before.
- **Size and time**, same machine as D95 (SQLite 3.45, eight workers): the full bundle 50,315,264 to 50,655,232 bytes (+339,968: `remote_refs` less what the folded fragments' rows saved; gzip 12,741,285 to 12,855,401), the selected 19,447,808 to 19,468,288 (gzip 4,485,537 to 4,502,565), `dataVersion` `6eb6823f9a51` to `c7706cddc4ee` and `f3e4567134c8` to `ed609d120645`. `rl bundle` 22.9 s and 12.5 s (23.0 s and 12.7 s before; 0.8 GB peak), `--verify` 20.6 s and 11.8 s.

### D105 — `remote_refs`: no ref goes missing

A ref names a remote across ledger versions (D89), and something outside the bundle may hold one: a saved remote, a count of confirmations, a link. A folded fragment's ref is no longer a row of `remotes`, so the bundle says where it went:

```sql
CREATE TABLE remote_refs (
  ref       TEXT PRIMARY KEY,
  remote_id INTEGER NOT NULL,
  first_n   INTEGER NOT NULL,
  key_count INTEGER NOT NULL
) WITHOUT ROWID;
```

**One row for every remote file of the ledger that the profile carries**, 13,217 in the full bundle and 4,394 in the selected one, **including the identity rows** of the remotes that carry their own ref (`first_n` 0, `key_count` the remote's). A file that was folded maps to the remote that carries it, and its keys are the rows `first_n` to `first_n + key_count - 1` of that remote in `keys` (the carrier's own keys are the run of its identity row, `first_n` 0, and the refs that map to one remote cut its keys into runs one after the other with no gap, which `--verify` checks).

**The lookup**, to resolve a ref a reader holds, exactly:

```sql
SELECT r.* , f.first_n, f.key_count
FROM remote_refs f JOIN remotes r ON r.id = f.remote_id
WHERE f.ref = :ref;
```

gives **one row, or none when the profile does not carry the file** (the selected bundle leaves most of the ledger out, D92, and a ref that is in no row has to be looked for in the full bundle or is gone from the ledger). That row is the remote to read: its `keys` rows, its carrier and play fields apply to the whole remote, which now holds the old ref's keys. **A key the reader kept as `(ref, n)`** (the `n` of D89 in the old file) is `(remote, first_n + n)` in the new remote; **a key kept as a canonical id** is the remote's key of that id with the lowest `n` (D85), which for a canonical key both have is the carrier's, not the folded fragment's: a reader that needs the folded fragment's signal for a key that both have reads the row `first_n + n`. To go the other way, `SELECT ref, first_n, key_count FROM remote_refs WHERE remote_id = :id ORDER BY first_n` lists the files a remote holds, the carrier's own first. **A reader that keeps a count by `(ref, test key)` needs the lookup only for a ref of a folded fragment, which has no test key and so no count**; one that keeps saved remotes by ref needs it for every saved ref, which is one indexed read.

**What a version-1 reader does** to open the file: nothing. `meta.schemaVersion` stays 1, with `user_version` and `application_id`, by D101's rule (*a change that removes or alters what a version-1 reader reads raises the version; adding a table or a `meta` row does not*). A reader written before this table reads `brands`, `models`, `controls`, `ngram`, `remotes` and `keys` by name and column: it sees fewer remotes, with more keys, and never reads the table, which a reader that wants it checks for in `sqlite_master`. **It does see changed rows**, and that is stated here and not hidden: `remotes` and `keys` are tables it reads, and their rows changed (a remote has keys of another protocol after its own, `key_count` is larger, and a remote id of a folded fragment is gone from `controls`), so a reader that holds a remote id or a ref from an earlier bundle will not find a folded fragment. That is the reason the table exists, and the reason `meta.mergeRule` states the rule in a sentence. A bump to 2 would have refused a file that reads perfectly for what it reads.

**The rest of the file.** `meta` has `count.remoteRefs` and `mergeRule`, the manifest's `counts` has `remoteRefs`, and the table is one of the digests of `dataVersion` (`writer.DIGEST_TABLES`, after `remotes`), so a bundle built with this exporter has a new `dataVersion` even where no remote moved. **`rl bundle --verify` checks** the table against the tree: the refs are exactly those of the files the profile carries; each maps to the remote that `merge.fold` over the tree gives, at the `first_n` and `key_count` the files' order gives; each remote's own ref is its identity row; the runs of a remote's refs cover its keys exactly; every remote is the carrier's row with the folded files' keys after its own and the weakest tier (and a remote the rule folds, found as a remote of its own, is reported); and a sample of remotes is compiled again from the compile stage's own output, file by file, into the keys the remote holds. A bundle with no such table is told to be built again (as for D101). `tests/test_bundle_merge.py` breaks a good bundle in fifteen ways at the row level and requires each to be reported.

### What is not proven (the folding)

- **Whether a folded fragment's keys work on the device.** Folding by file number is the ledger's own naming, not a check that two fragments are one physical remote, and the carrier's test key says the device answers to the carrier's protocol, not to the folded fragment's: a person who confirms Power in Sony12 has not confirmed a Volume up in Sony15 that the same remote now holds. That needs a device, as D50 says of every signal.
- **The 164 and 50 fragments left**, and the 1,192 models with nothing to try, are not helped: they need a remote with a carrier and a rule per key, or a wider test key, which are other changes.
- **Nothing was run by an app.** The tables are as the service's code and a reader of format 1 read them (a service that serves the bundle read the new one, and its opt-in tests against both bundles pass except the figures that are about the old counts), but no phone opened it.

---

## 27. Importing hifi-remote.com's Sony code pages

The fourth import under SPEC R19, and the first from a website. `hifi-remote.com/sony/` is "Rhm5757's Sony Code Page": a person's compilation, "most of the information from [Pronto and One For All] remotes", of which command number each Sony device code answers to, one page per kind of device (TV, Blu-ray, receiver, CD, VCR, and 53 more kinds). It has no remote model and no capture, only tables of `Command Code | Command(s)` headed by lines such as `Sony:26.226; 26.234; 26.242`. It came up in the BX510 lookup behind SPEC §1, whose "official Sony BD table" it is (§13), and the owner asked for the rest of it to be imported rather than cited a key at a time.

It adds `rl import hifi-remote`, `src/remote_ledger/hifiremote/` (`pages.py`, the reader; `importer.py`), `tools/fetch_hifi_remote.py`, the pinned snapshot `sources/hifi-remote/`, and `remotes/hifi-remote/` with its `README.md` and `IMPORT.md`. It registers the source in `paths.IMPORTS`, in the bundle's `SOURCES` (appended, so no existing source's id moves) and its notices, and in the keys report. The pages hold 66 files (the index and 65 linked), of which 58 have command tables: 104 tables, 3,880 rows. The import writes **151 files and 6,772 keys**: 84 Sony20, 36 Sony15 and 31 Sony12 files.

### D109 — The unit is a device code: one file per table per device code

A page says that a device code answers to a command number, and nothing about a remote. So a file here is not a remote: its `model` is the kind of device and the code (`Blu-ray 26.226`, `Receiver 16`, `TV 26`), its `controls` says the same in a sentence (`Sony Blu-ray (device code 26.226)`), and its path is the page and the code (`remotes/hifi-remote/sony/bluray-26.226.json`). A table headed with three codes gives three files that differ in their subdevice and nothing else, because a device code is what a person types into a universal remote and what the app selects, and a file that stood for two would hide which one the key was read at. The kind of device is the page's title without its codes (`Sony x (x)`, which says nothing, takes the page's first line, `System Off`).

What this does not do is make a remote of any of them. The authored RMT-B118P is the physical remote whose keys the Blu-ray table lists, and `test_the_hifi_remote_blu_ray_table_agrees_with_the_authored_sony` holds that every one of its 38 functions is in `bluray-26.226` at the same address. The authored data wins (R19.4) by name, and the names do not collide.

### D110 — A table of reference remote codes needs no licence

The owner decided this on 2026-10-08, when the import was agreed: *reference remote code has nothing to do with licence.* The list of facts a table holds (this device code, this command number, this name) is the ledger's own subject, and no licence file, SPDX id or "firmness" statement is wanted for it. SPEC R19.1 says so, for a source of this kind; `paths.IMPORTS` gives it `licence: "none: a table of reference codes"` (the site's banner reads it); the bundle's notices give it `licenceKind: "none"`, like the authored remotes, with no text and no SPDX id, and a note that the repository records none; and the README of the import directory names the pages and says the same. What stays is the attribution: every citation names the page, its hash, the table and the row, so that the source is visible and checkable (R18), and the report says what the pages are (a hobbyist's compilation, not a Sony publication).

**What was not changed.** SPEC §4 still excludes IRDB, Global Caché and Remote Central, "by their licences alone". The reasoning of this decision bears on them (they are tables of codes too), and the decision does not say they are admitted: TODO.md has the question. Nor did the three imports already made lose their licence files, which belong to files that are whole works.

### D111 — The pin is a snapshot, committed beside the data

A website has no commit. R19.5 asks that re-running over the same pinned upstream reproduces every file byte for byte, so the pin has to be bytes: `sources/hifi-remote/` holds the 66 pages as the server sent them (4.4 MB, Windows-1252 as Word wrote them) and a `MANIFEST.json` with the retrieval date, the source URL and, for each page, its SHA-256, size, `Last-Modified` and `ETag`. `tools/fetch_hifi_remote.py` makes it (a page a second, a User-Agent that names the project) and the importer refuses a page whose hash differs from the manifest's, so a page that moved cannot pass for the pinned one. It sits at the repository root and not under `remotes/`, because `rl validate` reads every `*.json` under `remotes/` as a remote and the manifest is not one. Every citation carries the first eight hex digits of its page's hash and the retrieval date. The snapshot of 2026-10-08 has the Blu-ray page as last modified on 2026-01-28 by its server.

`test_the_committed_import_is_exactly_what_the_pinned_snapshot_yields` runs the import into a scratch tree and compares every file and the report byte for byte with the committed ones: R19.5 is a test, not a promise. To refresh: run the tool, review the page diff, run `rl import hifi-remote sources/hifi-remote`, then `rl build`.

### D112 — A device code's frame is the page's word, then the dot, then its size

Sony's infrared frames come in three shapes (`Sony12`: 5-bit device; `Sony15`: 8-bit device; `Sony20`: 5-bit device and 8-bit subdevice), and a code on a page does not name one. The importer reads, in this order: the bit count in the marker when there is one (four markers have one: `Sony15:4`, `Sony20:1.0`, `Sony20:4.5` and `Sony 15 :26`); a dotted code (`26.226`) as Sony20; a bare code below 32 as Sony12 and from 32 up as Sony15, because a 12-bit frame's device field cannot hold 32. A code no frame can hold is listed, not imported (none is). Every citation says when the frame was inferred from the size. The page says of the receivers that their *AV2* codes are the normal code plus 32 and listed after the semicolon (`16; 48`), and writes a few of those in parentheses, unconfirmed (`12; (44)`): they are imported like the rest, and the citation says the page parenthesised the code.

Checked against the index page's own lists of device codes by frame (22 Sony12 and 33 Sony15 bare codes, and the dotted Sony20 ones): the rule agrees with every code the index lists that a table is headed with. The index is behind the tables on six codes (44 and 50, the AV2 codes just described, and 16.96, 16.104, 25.35, 25.43, which the receiver page has) and lists seven codes no table is headed with (Sony12 4 and 26, Sony15 36, Sony20 25.0, 26.26, 50.0, 100.0). One contradiction is the page's: the TV table is marked `Sony 15 :26`, while its prose and the index speak of "26.26", a Sony20 code. The marker is what the table is for, so the import reads Sony15 with device 26 and says so. `protocol.minSends` is 3 and the carrier 40 kHz, as the authored RMT-B118P has them and as the registry's nominal; every key passes the compile gate.

### D113 — A colour is the page's annotation; one legend limits a command to a device

Ten pages colour parts of their command cells, and say what each colour means in a legend line of their prose (`Olive = obsolete`, `Orange = foreign`, `Magenta = Plasma TV`, `Green = works on newer receivers`, `Orange = TiVo code`, `Light Blue=on CFD-E75 remote`, and so on). The reader keeps each name's colours (a cell `Enter, Set` has `Set` in blue), and the importer quotes the legend line in the citation of every key whose name carries the colour. A colour with no legend line is recorded as having none (one row of the boombox page, red). The colour of a word is never turned into a tier by itself.

Exactly one page's legend limits a command to a device: the Blu-ray page says Magenta is the portable players (26.135) and Blue the Home Entertainment Server (26.164), on one table headed for six codes. Those two colours are the only entries of `LIMITED_TO`; a command in them is in the files of those device codes only (`26.226` has 84 keys, `26.135` 86, `26.164` 85), and a name in them is a name for those devices only (`Set` is the server's name for command 11). Every other colour describes the command and leaves it in every file of its table. The page's other six Blu-ray codes "presumably use the same set of commands" is its own wording, and the citation of each says which marker the row is under. A legend or a row that says the code is **probably incorrect** makes the key Untested, the tier of a candidate nobody has confirmed (SPEC §5): 12 keys, the Green and Yellow commands 110 and 111 on the six Blu-ray files. Nothing else is above or below Plausible (R19.3).

### D114 — Names, labels, and what a cell with several names says

A command cell lists the names the command goes by, separated by commas (`Enter, Set`; `0, 10`; 468 of the 3,880 do). The label of the key is the first name that applies to the device, as the page writes it, with Word's dashes made plain (`Volume –` is `Volume -`, so D84's fold meets it) and a trailing `(discrete)` left to the citation, so that `Power On (discrete)` is the key the vocabulary calls `POWER_ON`; the other names are in the citation (`also named 'Set'`). The key name is D49's fold of the label (`fold_label`: `Volume +` is `KEY_VOLUME_PLUS`), and where two commands fold alike every one of them gets `_<command code>`, so a name does not depend on the order of the rows (`KEY_UP_7`, `KEY_UP_8`); a label that folds to nothing (a comma, a question mark) is `KEY_COMMAND_<code>`. Brackets and parentheses other than `(discrete)` stay in the label (`Up (Menu)`, `[Sync] Stop`), which keeps the keys apart and costs the vocabulary's mapping a few of them.

Four rows are not commands: `48-57 0-9`, `65-90 A-Z`, `97-122 a-z` (the MiniDisc keyboard) and `48-63 cassette codes`. They are listed in `IMPORT.md`, not dropped. The page's typos are left as written (`XM Recevier`).

The vocabulary maps 43.5% of these keys (D86's table has the row). It is low because the pages describe commands of one kind of device (`Index Mark`, `Disc +`, `Scan Reverse`, and `Select`, which D84 deliberately does not map), and nothing in the vocabulary was changed for them.

### D115 — What it does to the rest, and what is not proven

**The selected bundle moved, and D116 moved it back.** Sony is the third brand of the curated list, and its 151 files and 6,772 keys cost 609,400 estimated bytes (about 627,000 of the file) under a budget that had 1,000 to spare. With the budget as it was, the selected profile carried 33 brands and no proxy brand: Sanyo, Loewe, Haier, Integra and the proxy's brand moved out, and Skyworth and Westinghouse, which are smaller, came in, because the rule skips what does not fit and tries the next. D116 raises the budget and the cap so that the selection is the one made before the import.

**The pins moved with it:** 13,369 remote files, 531,868 keys, 160,606 distinct signals, 12,094 remotes with a key to try, and the figures in `test_bundle_*`, `test_keys_report` and `test_search_eval`; D116 moves the selected profile's own figures again (36 brands, 4,545 remote files, 172,418 keys, 70,342 signals).

### D116 — The selected profile's budget and cap follow the data it carries

The two numbers are the owner's (D92, D95): the cap of 20,000,000 bytes, and an estimated budget of 19,000,000 under it. The first bytes the hifi-remote.com import adds to a brand the list carries have to come from somewhere, and the choices were these:

- **Leave the 33-brand selection.** It is what the rule gives, and it puts two small brands (Skyworth, Westinghouse) in front of three bigger ones the list ranks higher (Sanyo, Loewe, Haier).
- **Raise the budget alone.** The rule is greedy and not monotone in its budget, so this does not give the dropped brands back. Scanning budgets from 19.0 to 19.7 million at 10,000-byte steps, each one brings some of the four back and takes others in (Arcam, Comcast, GigaBlue, Changhong, Beko, Loewe Opta) and drops Skyworth, Westinghouse, Metz or Mitsubishi. Only budgets from 19,608,400 to 19,609,999 give exactly the brands chosen before the import (35 of the list and `SOMMIGE MERKEN` by the proxy).
- **Keep the hifi-remote.com files out of the selected profile.** It would need a rule the bundle does not have: a brand is carried whole (D92), and the verifier, the folding of D103 and a dozen tests rest on that.

D116 takes the second, with the window: **`BUDGET_BYTES` 19,000,000 to 19,609,000, and `SELECTED_MAX_BYTES` 20,000,000 to 20,250,000.** The estimate is 19,608,400 and the file 20,148,224 bytes (4,612,020 gzipped), 148,224 over the old cap and 101,776 under the new one. The selection is the one before the import, brand for brand (`test_the_selected_profile_carries_what_it_carried_before_the_import`), so the owner's reviewed list is the list that ships. What it costs is **0.15 MB of the install**, and a margin of 600 bytes where there were 1,000: the rule is as knife-edged as it was, and the next import into a carried brand moves the selection again until the budget is tuned again. Whether the install target allows 20.25 MB is the owner's to say; the alternative is the first choice above.

### D117 — The selected profile has no budget, no cap and no floor

The owner answered D116's question on 2026-10-08, the day it was asked: *I want all the data, I don't want budget to be a factor, increase it to whatever size necessary.* So the selected profile stops being a subset by size.

**What changed**, all in `bundle/select.py`: `BUDGET_BYTES` and `SELECTED_MAX_BYTES` are `None` (they were 19,609,000 and 20,250,000; the budget code is still there, and a number puts it back), and so are **the proxy fill's floor of 40 models and 60% of keys mapped** (`MIN_MODELS` 0, `MIN_MAPPED_SHARE` 0.0). The floor is not a budget, but "all the data" is more than a bigger budget gives: with no budget and the floor left in, the selected profile still left out **4,427 brands, 2,460 remotes and 91,992 keys (17% of the ledger)**, the brands with fewer than 40 models or poorly mapped keys: 1,783 IR Blaster remotes, 656 LIRC, 18 SmartIR and three authored ones. Topping is among them: the RC-15A and RC-16A made it a brand of two models, so the app's bundle would have lacked the very remotes this section's lookups began with.

**What it gives**: 5,028 brands, 112 from the list and 4,916 by the proxy, **none left out**; 13,369 remote files, 531,868 keys, 160,606 signals. The file is **51,318,784 bytes** (12,899,777 gzipped), the size of `full`, and `rl bundle --verify` passes (21 s). The two profiles now hold the same data, and differ in the profile name and the selection text of `meta` and in nothing a client reads: the search evaluation of D99 gives the same table for both. They are both kept, as the profile is how a budget would come back, and the owner can retire one.

**What it costs**: the app that ships the selected bundle ships 51 MB and not 20, so its install is far over the 30 MB D92 aimed at. The owner decided that size is not the constraint. `rl bundle --max-bytes N` still fails a build over N, and no longer has a default.

**What stays true of D116** is its finding about a budget and not its numbers: the greedy rule is not monotone in the budget, so setting one again will drop and take in brands in ways that need re-tuning. The tests that use a small made-up ledger to exercise the subset keep the floor of 40 models they were written against (`conftest.py` for a test function, `floor_of_the_small_ledgers` for a fixture that builds once), and the tests over the real tree check the shipped rule: `test_the_selected_profile_carries_every_brand_and_has_neither_a_budget_nor_a_cap`.

**Not proven.**
- *That any of it works on a device.* A table says what a device code answers to, as one person compiled it from other people's remotes; no row was sent to hardware. Where the authored RMT-B118P has a key, the table agrees (38 of 38); that is the only cross-check.
- *That the frame is right where it was inferred.* 65 files rest on the size rule (the bare codes) and 82 on the dot; the four with a bit count in the marker are the page's word, not a check. D50's hardware questions apply.
- *That one device code is one kind of device.* The page types are the author's. The same code can head tables on two pages (26.153 on the CD page and the CD/cassette/tuner page, 26.73 on the DVD and the DVD/receiver pages) and the two files are separate models with, where the pages differ, different keys.
- *Whether a colour whose legend names a kind of device limits a command to some codes of its table.* `camcorder only` (DVD and VCR pages), `TiVo code` and `UltimateTV code` (the satellite page) and `foreign` (TV) could, but the pages do not say which of a table's codes they mean, so none is limited; each citation quotes the legend.
- *The Blu-ray page's six codes.* Its words are "presumably the same set"; only the portable players' and the server's commands are marked apart.
- *The BX510.* It is still in `unresolved.json` (§13).

---

## 28. Importing the JP1 device upgrades of hifi-remote.com's forum

The fifth import under SPEC R19, and the second from hifi-remote.com. The forum's JP1 File Section holds, for each model of a device, an *upgrade*: what a person programmed into a JP1 universal remote (a remote with a cable to a PC) so that it sends that device's codes. `github.com/hifiremote/deviceupgrades` collects them: 3,251 `.rmdu` files for RemoteMaster, 2,073 `.txt` files for KeymapMaster, and `jp1-master.xls`, the forum's index of them. The survey that found it (the search for more sources) measured its NEC codes as 74% new; the measurement below says why that was too high (D119) and what the value is instead.

It adds `rl import jp1`, `src/remote_ledger/jp1/` (`rmdu.py`, the reader and the decoders; `importer.py`), `tools/jp1_master_to_csv.py`, `sources/jp1-device-upgrades/` (the index as CSV and `MASTER.json`), and `remotes/jp1/` with its `README.md` and `IMPORT.md`. It registers the source in `paths.IMPORTS`, in the bundle's `SOURCES` (appended) and its notices (`licenceKind` none, as D110 has it), and in the keys report. Pinned to commit `87b0ac05bc3b73eb5d392b9f468ae24a875ff83e` (2026-05-27). The import writes **1,789 files and 67,417 keys under 762 brands**: 1,506 NEC1, 64 NEC2, 38 NECx1, 89 NECx2, 34 Sony12, 18 Sony15 and 40 Sony20 files.

### D118 — Brand and category come from the forum's own index, converted once

A file has a free-text `Description` and no brand field. The master index has a curated `Brand` and `Category` for 5,328 files (1,435 brands) and joins to 3,148 of the 3,251 `.rmdu` files by file name (spaces and underscores and case do not matter; where two rows share a name the category of the directory picks), 97%. The workbook is BIFF, which the standard library cannot read, so `tools/jp1_master_to_csv.py` (it needs `xlrd`, which the ledger does not depend on) writes `jp1-master.csv` and `MASTER.json`: the commit it was made from and the SHA-256 of the workbook. `rl import jp1` refuses a CSV made from another commit or another workbook. A file the index does not list takes the longest brand of the index that begins its description (95 of the 1,789), and one with no brand (11) is listed in `IMPORT.md`, not imported. The index spells some brands two ways (`SONY`, `Sony`), and the import uses the spelling most rows use, the first in the alphabet among equals, so that a directory and a model's maker do not depend on the order of a set (a first version did, and two runs differed; `test_brand_spellings_and_the_order_of_brands_do_not_depend_on_the_process`).

### D119 — Seven executors are read, each as its translator says, each checked against the ledger

An upgrade's `Protocol.name` names an executor of the remote, and its translators store a function's OBC byte **reversed**, and for NEC **complemented too**: `hifiremote/Protocols`' `protocols.ini` says `CmdTranslator=Translator(lsb,comp)` for NEC1. The survey took the byte as the command, and that is why it found 74% of the codes new: read that way, 42% of the keys match the ledger. Read as the translator says, **87.6%** do, which is the evidence the rule is right, since a wrong bit order would match almost none. Each rule below was found and checked that way, against the ledger's own codes, before any file was written:

| executor | the rule | files | keys | already in the ledger |
|---|---|---|---|---|
| NEC1, NEC1 (No Repeats) | device = parameter 0; sub device = parameter 1, or the device's complement if `null`; function = reverse8(~OBC) | 1,506 | 56,045 | 87.9% |
| NECx1, NECx2 | the same, and a sub device is needed | 38, 89 | 1,496, 4,470 | 85.0%, 80.4% |
| NEC2 | the same | 64 | 1,883 | 62.1% |
| Sony 12/15 | OBC's lowest bit picks device 1 (parameter 0) or device 2 (parameter 2); function = reverse8(OBC) & 0x7F; below 32 is the 12-bit frame | 34 + 18 | 1,462 + 423 | 92.7%, 95.0% |
| Sony20 | device, sub device, function = reverse8(OBC), at most 127 | 40 | 1,638 | 90.2% |

A `null` NEC device number is read from the second byte of `FixedData` (the complement of its reverse): 2,127 of the 2,325 functions that need it are in the ledger (91.5%). The percentages are shares of the keys the ledger held before this import, from every source, so they measure the rule and not the file. Each frame goes through the compile gate. A Sony 12/15 upgrade that uses both devices becomes two files, one per frame (`TV [Sony12]`, `TV [Sony15]`), as R3 has one protocol per file.

**Overall**: 58,544 of the 67,417 keys (86.8%) are codes the ledger held, nearly all as anonymous groups of the IR Blaster import. 6,494 distinct codes are new, 124 of the 441 devices (a device is protocol, device and sub device) are new, 178 of the files have no key the ledger held, and 762 brands are covered, of which 344 were not in the catalog (the catalog has 5,372 brands now, from 5,028). So what the import adds is mostly **the model**: the upgrade's description is the name of a real device, under its real brand, with a citation, where the ledger held the same code under an id with a list of names (`controls`).

### D120 — What is not read, and why

**1,462 upgrades and 2,073 KeymapMaster files are not read**, counted by executor in `IMPORT.md`: RC-5 (151; read since §32) and its combos, MCE (83) and RC-6 (42), Sony Combo (82), Manual Settings (75, raw executor bytes with no protocol), Panasonic Combo (53), NEC1 Combo (46), Nokia32 (40), JVC, Aiwa, Denon, Pioneer, Samsung36, XMP and about forty more. Each has a translator of its own (RC-5's, for one, puts the device slot in the low bits of the byte and a flag for OBCs above 63, and a first test found 86% of digit keys decoded by a simple rule and could not tell the rest), and a decoder that is not checked against the ledger is a guess. They are the next PR of this source, one executor at a time, each with its rule and its hit rate. `ExtFunction` lines (24 of the read files) are functions of another device the upgrade borrows, and are skipped. A function with no OBC byte or with more than one, **4,031 of 71,000**, is listed (a name with no code, or the combos' two bytes).

### D121 — Names, paths and the rest of the file

The maker is the brand and the model the description without the brand it begins with (`Maximum XO-506C`: `XO-506C`); a description that says nothing (`Learned Signal Upgrade`) is replaced by the index's, or the file name. `controls` is the description as written. A model that two files share gets the file name in brackets. The path is `remotes/jp1/<brand>/<category>-<file name>.json`, with the frame appended when a file splits and a number when two names slug alike, in path order. A key's label is the function's name verbatim and its name D49's fold of it (`num_0` is `KEY_NUM_0`); where two functions fold alike, or a function has no name, every one gets `_<function number>`. `minSends` is 3 for Sony, as RMT-B118P has it, and 1 for the NEC family. Every key is Plausible and cites the commit, the repository path, the function, its OBC byte, the executor and its parameters, and the reading (`jp1-device-upgrades@87b0ac0 TV/TV SAMSUNG - UN40H5203AFXZC (BN59-01178W).rmdu function 0 'POWER' (OBC BF; NECx2, parms 7 7 null): NECx2 device 7 subdevice 7 function 2`). The authored BN59-01199F has the same Power: `test_the_committed_samsung_upgrade_agrees_with_the_authored_bn59_on_every_key_they_share`.

### D122 — What it does to the rest, and what is not proven

The corpus is **15,158 remote files and 599,285 keys**, 5,372 brands and 279,632 models; the pins of the tests and the figures of §22 to §24 follow (the D86 row for `jp1`: 76.7% of its keys map to the vocabulary, 90.2% of its remotes have half of theirs). The bundle is 54,444,032 bytes, and per D117 and the backend direction nothing in it is a budget. The catalog shows a brand by the spelling most of its files use, so a few small brands changed spelling (`VIZIO` is `Vizio`, `APPLE` is `apple`), and the test that pinned the head of the curated list now compares search keys.

**Not proven.** That a code works on a device: an upgrade is what a person programmed into a remote, some from a manual and some learned (`Learned Signal Upgrade` is a common description), and no row was sent to hardware. That the rules hold for the 12% of NEC1 keys and the 38% of NEC2 keys the ledger did not have (the hit rate is evidence for the rule, not for those keys). That the forum's brand column is right: it is the forum's. And the licence: the upstream repository states none, the files are forum uploads, and D110 is the reason that does not matter here.

## 29. Importing the manufacturers' own IR code tables

The sixth import under SPEC R19, and the first from a manufacturer: the maker's own table of the codes its product accepts, as a spreadsheet it publishes. The owner chose to take the new sources one at a time (the survey that found them is in TODO.md); this is the first, with **Marantz's** AV receiver command charts (three workbooks, from the Archive pages of marantz.com) and **Anthem's** MRX and AVM IR hex sheet (anthemav.com). The next is RemoteCentral's learned-code database, a different kind of source (learned from remotes, so the lowest tier), and the other makers' tables follow it.

It adds `rl import official`, `src/remote_ledger/official/` (`common.py`, the snapshot and the report; `marantz.py`; `anthem.py`; `importer.py`), `tools/fetch_official.py`, `tools/official_sheets_to_csv.py`, `sources/official/<maker>/`, and `remotes/official/` with its `README.md` and `IMPORT.md`. It registers the source in `paths.IMPORTS`, in the bundle's `SOURCES` (appended) and its notices (`licenceKind` none, as D110 has it), and in the keys report. The import writes **174 files and 11,980 keys**: Marantz 162 files and 10,892 keys, Anthem 12 files and 1,088 keys.

### D123 — A document that is not a repository is pinned by its hash

A manufacturer's page is not a commit. `tools/fetch_official.py` keeps each document as the server sent it, with a `MANIFEST.json` of its URL, size, SHA-256, the retrieval date (2026-10-08) and the server's `Last-Modified`. The spreadsheets are BIFF and Office Open XML, which the standard library cannot read, so `tools/official_sheets_to_csv.py` (it needs `xlrd` and `openpyxl`, which the ledger does not depend on, as D118's tool does for the JP1 index) writes one CSV per sheet read, every cell as text, and records each CSV's SHA-256 and the document and sheet it came from. `rl import official` reads only the CSVs, and refuses a document or a CSV that is not the one the manifest pins. Every citation names the document, **the first eight hex digits of its hash**, the retrieval date, the sheet and the row, and the importer's test checks each one against the manifest. The snapshots are 4.0 MB (Marantz) and 176 KB (Anthem), committed.

Every key is **Plausible**: a maker's word is one source (R19.3), and no row was sent to hardware. The licence is none recorded, as a table of reference codes (D110). Anthem's separate PDF for the MRX SLM says it may not be reproduced without written permission; it is not imported.

### D124 — Marantz: a chart per workbook, a column a model, the maker's Pronto hex kept as it is

Each workbook's `AVR Commands` sheet (1,124, 801 and 559 commands) is a chart: a row a command with its RC-5 *system*, *command* and *extension* (Marantz's RC-5x adds a byte), a column a model with an `X` (or `@`) where the model accepts it, and, for the basic commands, Marantz's **Pronto hex**. A model's file comes from the **newest** workbook that lists it (the master chart gives 6,250 keys, the 2014 one 4,034, the FY18 one 608), and a column headed by two names is one model and its alias. The signal of a command is:

- **its Pronto hex, verbatim, as a `pronto` form** (10,803 keys), with no protocol name; the hex is the maker's own signal, extension commands included;
- **else, with no extension, RC5** device = system, function = command, as an `irp` form (89 keys);
- **else, with an extension and no hex, nothing**: it needs an RC-5x protocol, the ledger has none (D126), and the report counts it (1,034 rows: 396, 484 and 154 by workbook, each row once however many models list it).

One file holds one carrier (R3) and the bundle needs one play rule a file (D78), so a model's commands are filed by the Pronto frequency word and by whether the string has an intro: the biggest group has the model's own name, and the others say what they are, `SR5004 [RC-5]` or `AV7005 [36.0 kHz, intro]`. 76 models make 76 plain files, 76 `[RC-5]` files and 10 of the other kind: **162 files**. A key's name is `KEY_` and D49's fold of the chart's command name, and where two names fold alike each gets `_<system>_<command>_<extension>`; the label is the chart's own name. A second name of a column is an alias of the plain file only (two files may not claim one alias, R15). A mark other than a bare `X` is in the citation, as is the chart's own description of the command.

**The check.** The chart gives the RC-5 system and command in two columns and the Pronto hex in a third: two statements of one signal. The tests decode each of the **1,640** Pronto strings at 36 kHz with no extension byte from its bits, and every one says the system and command of its own row (no mismatch). The other 31 are RC-5x frames, carried verbatim. One Pronto per workbook, `SURROUND MODE (Back)`, has a repeat sequence of length zero, which D28 refuses; it is listed in `IMPORT.md` and not imported.

### D125 — Anthem: NEC1 from the data columns, each checked against the sheet's own Pronto hex

`MRX x10-x40 IR hex codes` lists 104 functions (zone 1 and zone 2) with the NEC1 *device* (two bytes) and *data* (one byte) as sent, the same as a `NEC1` string, and a Pronto hex; the layout sheet names the models of each remote layout. The codes are `irp` NEC1 forms from the device and data columns, and **each is decoded from the sheet's Pronto hex bit by bit and must say the same device, sub device and function**. 84 of the 104 rows do. **The other 20 do not**: for inputs 21 to 30 (and their zone 2 twins) the sheet's NEC1 and Pronto columns repeat the codes of inputs 11 to 20 and only its data column differs (input 21's data is `0xC6`, where the Pronto repeats input 11's `0xA1`). The import takes the data column and marks those keys **Untested**, with a citation that says the sheet contradicts itself there. Which of the two is the typo is Anthem's to say. The sheet's notes say two things the citations carry. **Note 2**: "Codes for inputs 21-30 do not apply to MRX x10, only MRX x20 and AVM 60"; the layout sheet names three groups (x40: AVM 70, AVM 90, MRX 1140, 740, 540; x20: AVM 60, MRX 1120, 720, 520; x10: MRX 710, 510, 310), so inputs 21 to 30 go to the four x20 models only, which have 104 keys (20 of them Untested) and the other eight have 84. **Note 1**: "The factory remote control does not use codes for inputs 1-30. They are for accessing inputs directly from programmable remotes", so those keys are codes the receiver accepts, not keys of its remote. The Untested keys are 80 of the 1,088.

### D126 — What is not read, and why

**Marantz's other sheets**: `Direct Vol.`, `Zone Speaker Direct Vol.`, `AM FM Preset Direct`, `XM SIRIUS Preset Direct` and `XM SIRIUS Ch. Direct` list direct-access commands, a volume level, a preset or a channel in one command (system 16, command 111, extension `00`, `01`...), with no Pronto hex. They are RC-5 with an extension byte, as the 1,034 rows of D124, and need an RC-5x protocol, which needs the three-part gate of D18 (an independently cited golden vector) before it exists. They are the next step for Marantz once it does. **Models the chart marks otherwise** (`---`) are not taken. Of the **other makers' tables**, none is read yet (TODO.md).

### D127 — What it does to the rest, and what is not proven

The corpus is **15,332 remote files and 611,265 keys**, 5,372 brands (both makers were in the catalog) and 279,886 models. The bundle is 54,947,840 bytes, the same file for both profiles, and `rl bundle --verify` reads it against the tree (15,017 remotes, 167,707 signals). **The keys these files add map the least of any source to the vocabulary, 16.9%** (§22): the charts are the whole command set of a receiver, and the vocabulary of D84 is the generic keys of a remote. The generated search sample moved with the catalog (D99).

**Not proven.** That a code works on a device: no row was sent to hardware. That a column's `X` means a receiver *does* answer the code and not only that Marantz lists it; the charts are the maker's and the oldest says its updates go back to 2012. That Anthem's data column is right for inputs 21 to 30 (D125). That a file's model is what a person calls the thing they own: the model is the receiver's name as the chart's column heads it, and a remote that controls it is named in `controls`.

## 30. Data kept elsewhere: extra roots and the private repository

The owner's decision of 2026-10-09: **data whose licence is unclear is kept in a private repository, in this repository's layout and format, and is not imported here until the licence is cleared.** The case that raised it is RemoteCentral's Infrared Hex Code Database (learned codes; the site says "All rights reserved" and states no licence): §29's way of importing, and D110's reasoning that a table of reference codes needs no licence, were the owner's for sources whose pages offer them, and this is a source whose owners have said nothing. The decision does not move what is already here (the hifi-remote.com pages, the JP1 upgrades and the manufacturers' tables stay under D110), and a pull request that would have imported RemoteCentral here was closed unmerged.

What it needs of this repository is one thing: the tools that read the corpus must be able to read data that is not in it, and the committed trees, `build/` and `site/`, must never hold it. That is an **extra root**.

### D128 — An extra root is another directory laid out like this one

`--extra-root DIR`, repeatable, is accepted by `rl validate`, `rl check`, `rl keys report` and `rl bundle`, which read the corpus and write nothing into `build/` or `site/` (`bundle` writes to `--out`, never inside either). The remotes of `DIR/remotes/` join the corpus for that call: they follow the repository's own in the order of `corpus_files`, each root in its own sorted order, so **the ids the bundle gives the repository's remotes are the same with or without an extra root**. Their paths are repository-relative as the repository's are (`remotes/<source>/...`, `paths.rel`), so everything that labels a remote by its path labels theirs. In the bundle the sources of the extra roots take the ids after the repository's (`corpus.sources()`), in the order the roots were given and then each registry's, and each has a notice of its own, read from its own root (`licence_file`, README and `IMPORT.md` are under `DIR`). Every other command, and in particular those that write the committed trees (`build`, `index`, `site`, `app`, `compile`, `fmt`, `import`), does not have the option, **there is no environment variable and no default**, and the state belongs to the call (`main` clears it), so a stray setting cannot put an extra root's data in `site/`. A test holds eight of them to refusing the flag. A bundle built with an extra root is the extra root's data in a file: **keeping it private is a policy and not something the option can enforce.**

### D129 — Each extra root says what its data is, and the roots may not clash

`DIR/imports.json` is `{"format": 1, "sources": {"<name>": {...}}}`, one entry for each directory under `DIR/remotes/`, with what `paths.IMPORTS` and `notices.FACTS` hold for a public source: `name`, `licence` (a statement: `none: unclear`), `upstream_url`, `kind` (`none`, by default, `reading`, `stated` or `inherited`), and for `none` a `note`, for the others `licence_file` and `readme_sentences`; `readme` defaults to `remotes/<name>/README.md`. The rules are the ones that keep an import's path the licence boundary (D34): a remote under an extra root that no source claims is refused, because it would be labelled authored; a source may not be named `authored` or be one of the repository's or another root's; no path may be in two roots (`corpus_files` refuses it); a root may not be the repository, inside it or contain it, nor be given twice. **R15 is checked across the roots**: the index stage checks the repository's own files and does not run over an extra root, so `rl validate` with one reports a model or alias claimed by a file of an extra root and by any other (the repository's own claims are not repeated), and an importer there skips a name the repository has, as the importers here do.

### D130 — The private repository, and how a source leaves it

It is `remote-ledger/remote-ledger-private`: `remotes/<source>/` and `sources/<source>/` as here, an `imports.json`, the importers and fetchers of its sources (a package, `remote_ledger_private`, over `remote_ledger`), their tests, and a document for each source, and **no `build/` or `site/`**, which are this repository's. Its first source is RemoteCentral's, 2,187 files and 54,975 keys, every key Untested. Run from either checkout, the tools read both: over the real data, `rl validate --extra-root` finds 17,519 files and no error, `rl keys report` the row the data had here (57.8% of its keys mapped), and `rl bundle --profile full --extra-root` a bundle of 63,385,600 bytes that `--verify` reads against the trees (17,204 remotes, 211,680 signals): the size and the counts the same data made when it was in this repository. **A source leaves the private repository for this one when its licence is cleared**, by a pull request here that moves its two directories, adds it to `paths.IMPORTS`, the bundle's `SOURCES` and the notices' `FACTS`, and writes its section; nothing in either repository's history needs rewriting for that.

**Not proven.** That the private repository stays private: the options of this repository cannot make a bundle private, and the closed pull request that first carried the RemoteCentral data was public for a time, so the data may be cached or cloned (GitHub keeps a closed pull request's commits reachable through its refs, and its support removes them). That the tools stay compatible: the private importers use `remote_ledger`'s internals (`import_common`, `official.common`, `irblaster.importer`), so the repository pins the commit it was last run against.

## 31. Oppo's remote code workbooks

The second batch of manufacturers' own tables (§29): Oppo's three remote code workbooks, for the BDP-103 and BDP-105 Blu-ray players, the BDP-103D and the UDP-203 and UDP-205 Ultra HD players. Each is a workbook of three sheets, the three code sets (`Remote Code 1` to `3`) a player's remote can be set to, with a row for each key: its NEC function in hex and decimal, the Pronto string Pronto's own TSU3000 remote takes, and the Pronto classic hex. The survey that found them (the manufacturers' tables, ranked) rated them A-: popular players, and the survey found the first code set's custom code (`0x49`) in the ledger as an anonymous IR Blaster group, and the others (`0x61`, `0x43`) and both Ultra HD players absent.

It adds `src/remote_ledger/official/oppo.py`, `oppo` to the fetcher and the sheet converter, `sources/official/oppo/` (the three workbooks and 12 CSVs, 1.6 MB), and `remotes/official/oppo/` (**15 files, 864 keys, every key Plausible**) beside Marantz and Anthem; it registers nothing new, since `official` is already a source.

### D131 — The workbooks are the Wayback Machine's copies

Oppo's download host (`download.oppodigital.com`) no longer resolves, and the support pages still link to it. The three workbooks are the Wayback Machine's copies, each the snapshot named in its URL (`BDP-103_BDP-103D_Remote_Code_v1.2.xls` 2024-01-05, `BDP-103_BDP-105_Remote_Code_v1.1.xls` 2026-03-08, `UDP-203_Remote_Code_v1.2.xls` 2025-12-11), and the manifest records the original address, the snapshot and **the origin's own `Last-Modified`** as the archive kept it (2014-01-31, 2012-10-23 and 2016-12-13), so a citation says "from the Wayback Machine snapshot 20251211233638" and the manifest says when Oppo last changed the file. The raw form of an archived URL (`.../<snapshot>id_/<original>`) is the original bytes; the plain form answered a script with an HTML page of the archive's own, which the tool pinned for a moment on the first run, so `fetch_official.py` now **refuses a spreadsheet that does not begin like one**. The three files are byte-identical to the copies the survey had fetched another way.

### D132 — Each row is checked against the sheet's other statements of its code

A sheet is NEC1 with one custom code (`49`, `61` and `43` for the three sets), and its `Device` cell is that byte and its complement. The key's function is read from the hex column and made an `irp` NEC1 form with device, sub device (the complement) and function, as the Anthem codes are, and each row is checked against **three other statements of the same code**: the decimal column, the TSU3000 string (`900A 006D 0000 0001 49B6 1AE5`: device bytes, function, and its complement), and the NEC frame decoded bit by bit from the classic hex. **The classic hex is the signal, and on all 519 rows it says the key's code** (the tests decode every one). On seven of the nine sheets the `Picture Adj.` row's TSU3000 string is `...0EE1` where function `0E` needs `...0EF1` (`E1` is the complement of `1E`): a typo in a derived string, with the hex column, the decimal column and the signal agreeing on `0E`. The rule is that a row whose **signal** disagrees with its code is Untested (none is) and one where only another column does is Plausible with the discrepancy in its citation, which these seven are.

A workbook serves the models its file name gives (`BDP-103` and `BDP-105`; `BDP-103` and `BDP-103D`; `UDP-203`, and `UDP-205`, whose support page links the UDP-203 workbook, whose sheets say `UDP-20X`), and a model two workbooks name takes the newest (`BDP-103` from v1.2). The first code set has the model's name and the others are `BDP-103 [code set 2]` and `[code set 3]`, so a player a person set to code set 2 finds its own file; `controls` is `Oppo <model>`. A key's name is `KEY_` and the fold of its label (`Vol +` is `KEY_VOL_PLUS`).

### D133 — What it does to the rest, what is not read, and what is not proven

The corpus is **15,347 remote files and 612,129 keys**, 5,372 brands (Oppo was in the catalog) and 279,905 models, with 167,869 distinct signals (162 more: the five models share three code sets, so the 864 keys are 180 distinct codes, 60 for each custom code, and 18 of them were already in the ledger). Both bundle profiles are 54,988,800 bytes and `rl bundle --verify` passes on each. 86.8% of Oppo's keys map to the vocabulary (§22), which lifts the `official` row from 16.9% to 21.6%, and the 15 files carry the `carrier-off-nominal` warning Anthem's 12 do (the maker's own strings use word `006D`, 38.0 kHz). The search evaluation's draw moved again (D99).

**Not read**: each workbook's `Notes` (discrete input selection is a sequence, `INPUT`, a 2 second wait and the input's number; a picture preset is `Picture Adj.`, a number and `SETUP`), which are macros and are in `IMPORT.md` as notes; the `Layout` sheets, which are empty; and Oppo's pages for the older BDP-83 and BDP-93, which link no code file.

**Not proven.** That any code was sent to a player. That the `DARBEE` key, which the v1.2 workbook lists for `BDP-103`, belongs to the plain BDP-103 and not only the `D` model: the file's name gives both and the rule takes the newest. That the Wayback copies are what Oppo last published: the `Last-Modified` dates are the archive's record of the origin's headers.

## 32. More JP1 upgrades: the RC-5 executor

The first of the further executors D120 left for later PRs, and the biggest: **151 upgrades** that name the `RC-5` executor, a "quickie combo" of up to three RC-5 device numbers in one upgrade (`protocols.ini`'s own words). Nothing else of §28 changes: the pinned commit, the master index, the reader and the importer are the same, with one rule added, `rc5_signal` in `src/remote_ledger/jp1/rmdu.py`, and `RC-5` in the executors it reads. The import now writes **1,936 files and 73,065 keys** (147 files and 5,648 keys more), and 26 brands that the catalog did not have.

### D134 — The rule is the translator's own

`protocols.ini` gives the executor `CmdTranslator=Rc5Translator()`: not an expression, a class of RemoteMaster. Its source (`com/hifiremote/jp1/translate/Rc5Translator.java` in the git-svn mirror `Elemecca/remote-master` @ `bd86e76`; the bit offsets of its helpers count from the most significant bit, `Translate.java`) says what the OBC byte holds: **its low two bits select a device slot** (a slot above the third is the first), **its top six bits are the command, complemented**, and the slot's `OBC>63` flag adds the seventh bit. `ProtocolParms` is `Device 1, OBC>63, Device 2, OBC>63, Device 3, OBC>63`, and a slot with no device is the nearest earlier slot that has one, as the executor's own defaults (`Device 2 = [Device 1]`) make it. The signal is the ledger's `RC5` at its nominal 36 kHz, device and function, `irp` and Plausible like the rest of §28.

### D135 — Checked against the ledger, and against Arcam's own table

As for the NEC family (D119), the rule was checked before any file was written. **5,755 keys decode, and 94.9% of them are signals the ledger already held** from other sources (D119 has 87.6% of the keys for the NEC family), and by distinct code 1,383 of 1,594 (86.8%): a wrong bit order or complement would match almost none. What is new is the 211 distinct codes the signal table gains, and the rest is the same codes under their device's real name, with a citation. The second check is another source altogether. **Arcam's own table of its amplifiers' RC-5 codes** (`arcam.co.uk/ugc/tor/a18/IR Codes/amps_rc.pdf`, system code 16, as fetched on 2026-10-08 for the survey and not pinned here) lists PHONO select 16-1, AV select 16-2, Tuner select 16-3, tape select 16-5, CD select 16-7, Mute 16-13, Volume up 16-16, Volume down 16-17, Power-on 16-123 and Power-off 16-124, and two forum upgrades of Arcam equipment, decoded through the translator, send exactly those, the last two through the AVR100's second device slot, whose flag adds the seventh bit. A test holds the committed files to it.

### D136 — What is skipped, and what is still not read

Of the 151 upgrades **147 are files**; two are skipped because another source already has a remote of that name (`Hermstedt Hifidelio`, `VESTEL TV`), one has no brand (`Pionier N555 ST`) and one decodes nothing (`Beko`). Of their functions 238 have no OBC byte (they are blank in the upgrade) and 40 select a slot with no device, all in `IMPORT.md`. **Still not read**: the RC-5 variants (RC-5/5x Combo 16, RC5/6 combo 12, and three single ones), each with a translator of its own (the combo's keeps two bytes, `DefaultCmd=00 00`), and the other 186 executors (MCE 83, Sony Combo 82, Panasonic Combo 53, NEC1 Combo 46, RC-6 42, Nokia32 40, JVC 33, Panasonic 33, Aiwa 31...; Sony Combo, NEC1 Combo, NEC 4DEV Combo and its Yamaha variant are read since §33, and RC-6, JVC, Aiwa, Denon, Panasonic and their combos since §34), each of which needs its class read and checked as this one was (TODO.md).

The corpus is **15,494 remote files and 617,777 keys**, 5,398 brands (26 more), 280,163 models (258 more) and 168,080 signals (211 more). Both bundle profiles are 55,177,216 bytes and `rl bundle --verify` passes on each. 78.1% of the new keys map to the vocabulary (§22). The search evaluation's draw moved again (D99).

**Not proven.** That a function does what its name says (§28: the upgrades are what people programmed, some learned), that the ledger's nominal 36 kHz is the carrier each upgrade's remote sent (the translator does not say), and that the Arcam check generalises: it covers two upgrades of one maker's equipment, against a table that is itself the maker's.

## 33. More JP1 upgrades: the combos whose functions are two bytes

The next executors of D120, and the next largest after §32: four **combos**, which put several devices, frames or styles in one upgrade and so write each function as **two OBC bytes**: `Sony Combo (12/15/20)` (82 upgrades), `NEC1 Combo` (46), `NEC 4DEV Combo` (30) and `NEC 4DEV Yamaha Combo` (15). **173 upgrades become 248 files** (a combo is one file for each ledger protocol it sends, as Sony 12/15 is) and **9,715 keys**, all Plausible, so the JP1 source is now **2,184 files and 82,780 keys**. The reader's one new piece is `combo_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D137 — The rules are the translators' (RemoteMaster's `Translator` and `SonyComboTranslator`)

The generic `Translator`'s arguments are `index, bits, bit offset`; it extracts the bits from the offset (counted from the most significant bit of the bytes), complements them for `comp` and reverses them for `lsb`, and puts the result in parameter `index`. `SonyComboTranslator` is a class of its own. What they give:

- **Sony Combo (12/15/20)**: the first byte's top seven bits are the command, reversed, and its lowest bit says Sony15. The second byte is the device reversed in all eight bits for Sony15; else the device reversed in its top five bits, an index of the four `ProtocolParms` sub devices in the next two, and Sony20 in its lowest bit (Sony20 takes the indexed sub device, otherwise it is Sony12).
- **NEC1 Combo**: `CmdParms=Sub Device,OBC`, so the first byte is the sub device and the second the function, each `rev8(~byte)`; the device is the one parameter (`Device Code`, default 184). A `null` device is read from `FixedData`, which holds the executor's effective values: the one case, `Onkyo Master File` (373 functions), decodes to the default, `E2` being 184.
- **NEC 4DEV Combo**: the first byte is the function, as for NEC; in the second the top two bits are the device slot and the bits at offsets 11 and 15 the style (NEC1, NEC2, NECx1, NECx2); the slot's device and sub device come from `ProtocolParms` (a `null` device from `FixedData`, a `null` sub device the complement, as for NEC).
- **NEC 4DEV Yamaha Combo**: the same, with a Y style in the bits at offsets 13 and 14. Its plain style (`NEC`) is that frame; **Y1, Y2 and Y3 send a second byte that is not the complement of the first**, NEC with 16 data bits, which the ledger has no protocol for. Those **1,087 of its 2,356 functions are refused** and counted in `IMPORT.md`; the other 1,269 are imported.

### D138 — Each rule checked against the ledger, and one against a Verified source

As for the NEC family (D119) and RC-5 (D135), each rule was measured against the ledger's own signals before any file was written (a wrong one matches almost nothing):

| executor | upgrades | keys decoded | already in the ledger |
|---|---|---|---|
| Sony Combo (12/15/20) | 82 | 3,950 | 97.9% (883 of 956 distinct codes) |
| NEC1 Combo | 46 | 2,869 | 70.9%, or 81.2% of the 2,496 that are not `Onkyo Master File`, whose 373 are a full Onkyo command set the ledger mostly lacks (7 of them are there) |
| NEC 4DEV Combo | 30 | 1,627 | 80.1% (746 of 1,039) |
| NEC 4DEV Yamaha Combo, plain style | 15 | 1,269 | 58.9% (230 of 480) |

NEC1 Combo failed first, at 2.6%, which is how a swapped pair of bytes shows itself (`CmdParms` list `Sub Device` before `OBC`). **The Sony rule has a second check, against a Verified source**: the ledger's authored RMT-B118P has 38 keys Verified against hifi-remote's own Sony Blu-ray table (Sony20, device 26, sub device 226), and the forum's `Sony UHP-H1` upgrade, whose functions choose their sub device through the index, decodes to Sony20 codes that include **all 38**. A test holds the committed file to it.

### D139 — What is skipped, and what is still not read

317 functions are blank in their upgrades (200, 84, 11 and 22 by executor); every other function of the 173 upgrades decodes or is a Yamaha Y style, so nothing is lost to a missing parameter, to the compile gate or to naming. Of the 173 upgrades none is skipped for a brand or for a name another source has. **Still not read**: the Yamaha Y styles (D140), `Sony Combo (12/20)` (6) and `Sony Egg` (1), and the other executors, about 1,130 upgrades by D136's count less these (MCE 83, Panasonic Combo 53, RC-6 42, Nokia32 40, JVC 33, Panasonic 33, Aiwa 31...).

### D140 — What it does to the rest, and what is not proven

The corpus is **15,742 remote files and 627,492 keys**, 5,406 brands (8 more), 280,549 models (386 more) and 169,166 signals (1,086 more). Both bundle profiles are 55,689,216 bytes and `rl bundle --verify` passes on each. **52.5% of the new keys map to the vocabulary** (§22), fewer than the rest of the source: a combo is an AV receiver's or an amplifier's whole command set (tuner, zone and sound-mode keys the vocabulary does not have), and the `jp1` row falls from 76.8% to 74.0%. The search evaluation's draw moved again (D99).

The Yamaha Y styles are the largest thing left in this batch: 1,087 functions of Onkyo and Yamaha receivers. Reading them needs `NEC1-f16` and `NEC2-f16`, which D18's gate has not admitted (an independently cited golden vector), and is in TODO.md.

**Not proven.** That a function does what its name says (§28), that a `null` device that `FixedData` fills is the one the upgrade's remote sent (it is for the 373 functions of one file, and the executor's default), that the Yamaha plain style's lower rate (58.9%) is only the ledger's thinner coverage of Onkyo and Yamaha receivers and not a fault in a rule it shares with the NEC 4DEV Combo (80.1%), and that the Sony check generalises beyond one Blu-ray player's table.

## 34. More JP1 upgrades: RC-6, JVC, Aiwa, Denon, Panasonic and their combos

The executors whose rules are one `Translator` each, and the combos built from the same pieces: **`RC-6`** (42 upgrades), **`JVC`** (33) and **`JVC Combo`** (9), **`Aiwa`** (31), **`Denon`** (14) and **`Denon Combo (Official)`** (28), **`Panasonic`** (33) and **`Panasonic Combo`** (53). **243 upgrades become 240 files and 10,963 keys**, all Plausible, and the JP1 source is now **2,424 files and 93,743 keys**. The reader's new pieces are `simple_signal` and three more branches of `combo_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D141 — The rules are the translators' own, composed of four primitives

Each executor's `CmdTranslator` (`protocols.ini`, with the translator semantics of D137) is one or two of `Translator()` (the OBC as it is), `Translator(lsb)` (reversed), `Translator(lsb,comp)` (complemented, then reversed), and the device is a parameter:

- **RC-6**: the OBC as it is; device and function are the ledger's `RC6` (mode 0, 8 and 8 bits).
- **JVC**: the function is `rev8(~OBC)`; **JVC Combo** is two bytes, `CmdParms=OBC,Device` over `Translator(lsb,comp,0,8,8) Translator(lsb,comp,1,8,0)`, so the device is the first byte and the function the second, each `rev8(~byte)`.
- **Aiwa**: the function is the OBC reversed; the sub device is five bits; a `null` device or sub device is the protocol's default, 0 (its own comment, and the one `FixedData` I checked says the same).
- **Denon**: the device is five bits and the function the OBC reversed. **Denon Combo**: the first byte's top five bits are the device (complemented, reversed) and the bit at offset 5 says Sharp or Denon (`Translator(lsb,comp,1,1,5)`, complemented, so a set bit is Sharp); the second byte is the function, `rev8(~byte)`. A combo upgrade is one frame or the other (28 upgrades: 24 Denon files and 4 Sharp files).
- **Panasonic**: device, sub device, the function `rev8(~OBC)`, and **the two OEM bytes of the ledger's frame, which fixes them at 2 and 32**: an upgrade with other OEM bytes is refused (47 functions, OEM 162). **Panasonic Combo** is two bytes, the sub device first and the function second, each `rev8(~byte)`, with the main device a parameter (a `null` one is in `FixedData`, `Translator(lsb,comp,0,8,16)`: `FA` is the default, 160).

### D142 — Each rule checked against the ledger

As for the earlier executors (D119, D135, D138), each rule was measured against the ledger's own signals before any file was written, and every one cleared it first time:

| executor | upgrades | keys decoded | already in the ledger |
|---|---|---|---|
| RC-6 | 42 | 1,745 | 91.9% |
| JVC | 33 | 1,275 | 87.9% |
| Panasonic Combo | 53 | 2,974 | 87.2% |
| Aiwa | 31 | 991 | 81.2% |
| Panasonic | 33 | 1,322 | 78.8% |
| Denon Combo | 28 | 1,608 | 73.6% (Denon frames 70.8%, Sharp frames 93.3%) |
| Denon | 14 | 378 | 69.3% |
| JVC Combo | 9 | 714 | 65.3% |

The `FixedData` and default fallbacks were measured too: Aiwa's 284 keys with a `null` parameter hit 73% on their own, and Panasonic Combo's 82 with a `null` device hit 80 of 82. A wrong orientation (reversed or not, complemented or not) would match almost nothing; the gaps are most likely the ledger's thinner coverage of these devices' codes, which D145 lists among what is not proven.

### D143 — What is skipped

Of the 243 upgrades **3 yield nothing**: `Sky PerfecTV (Japan)` and `Philips 40PFL7505D` decode no function, and `Aiwa OEM remote RC-ZAS02` is named like an authored remote; the rest are files, 240 in all. 627 functions are blank in their upgrades, 47 are Panasonic OEM bytes the frame does not have, and 42 are RC-6 functions whose device is `null` (the executor has two variants whose `FixedData` differ, and I did not guess which). All are in `IMPORT.md`.

### D144 — What it does to the rest

The corpus is **15,982 remote files and 638,455 keys**, 5,410 brands (4 more), 280,965 models (416 more) and 170,297 signals (1,131 more). Both bundle profiles are 56,225,792 bytes and `rl bundle --verify` passes on each. **61.8% of the new keys map to the vocabulary** (§22), so the `jp1` row goes from 74.0% to 72.5%. The search evaluation's draw moved again, and one of its eight misses is a device of this import (D99).

### D145 — What is still not read, and what is not proven

**Not read**: `MCE` (83 upgrades) is RC6-6-32, a mode the ledger's `RC6` (mode 0 only) does not have, and `Nokia32` (40) a frame of its own; both need a protocol admitted through D18's gate. Also `RC-5/5x Combo` (16), `RC5/6 combo` (12), `Panasonic (old)` (19), `Pioneer MIX` (27), `XMP` (48), `GI Cable` (20), `RCA` (20), `Samsung36` (19), `Sharp Combo` (11), `Denon-K` (11), `Proton` (12), the 75 `Manual Settings` upgrades, and the KeymapMaster files (TODO.md).

**Not proven.** That a function does what its name says (§28); that Panasonic's OEM bytes 2 and 32 are the ones every upgrade means where the parameters say `null`, which is the executor's default; that the lowest rates here (JVC Combo 65.3%, Denon 69.3%) are the ledger's gaps and not a fault in a rule.

## 35. More JP1 upgrades: Sharp, Samsung36, Proton, F12, RECS80, Denon-K and two more combos

Eight more executors: **`NEC 2DEV Combo`** (8 upgrades), **`Sharp Combo (Official)`** (11), **`Sharp`** (6), **`Samsung36`** (19), **`Proton`** (12), **`F12`** (9), **`RECS80 (45)`** (17) and **`Denon-K`** (11). **93 upgrades become 94 files and 3,958 keys**, all Plausible, and the JP1 source is now **2,518 files and 97,701 keys**. The reader's new pieces are seven branches of `simple_signal` and `combo_signal` in `src/remote_ledger/jp1/rmdu.py`, and one change to the importer (D147); the pinned commit and the index are §28's.

### D146 — The rules are the translators' own

As in D141, each executor's `CmdTranslator` (`protocols.ini`) is a `Translator()` of the OBC as it is, `Translator(lsb)` reversed, or `Translator(lsb,comp)` complemented and then reversed, with the device a parameter and a `null` parameter in `FixedData`:

- **NEC 2DEV Combo** is the NEC 4DEV Combo of D137 with two device slots instead of four: the second OBC byte's top bit, not its top two, picks the slot, and its style bits say NEC1, NEC2 or NECx1 as before. Eight upgrades are 12 files, one per frame.
- **Sharp Combo (Official)** has the PID of the Denon Combo (`00 9C`) and the same translators, so it has the same rule (D141): the first byte's top five bits are the device, the bit at offset 5 says Denon or Sharp, the second byte is the function. All 582 functions it yields are Sharp frames.
- **Sharp**: the device is five bits and the function the OBC reversed, not complemented.
- **Samsung36**: device and sub device are parameters, the four-bit E is parameter 2, and a `null` one is in `FixedData` (`Translator(lsb,0) Translator(lsb,1,8,8) Translator(lsb,2,4,16,0)`); the function is the OBC reversed, with E in the high bits of the ledger's function (D65).
- **Proton**: the device is a parameter and the function is the OBC complemented and reversed.
- **F12**: a four-bit device parameter whose low three bits are the ledger's `F12_relaxed` device and the top bit its sub device; the function is the OBC reversed.
- **RECS80 (45)**: a three-bit device and a six-bit function, `63 - (OBC >> 2)`.
- **Denon-K** is two bytes (`Translator(lsb,comp,0,4) Translator(lsb,comp,1,12,4)`): a four-bit field that is the ledger's sub device, then the 12-bit function (the first byte's low nibble and the second byte), each complemented and reversed. The device is parameter 0 or `FixedData`'s (`FD` is 4, the default), and **the two OEM bytes, parameters 1 and 2, must be 84 and 50, which the ledger's frame fixes**; an upgrade with others is refused.

### D147 — The JP1 importer uses the IR Blaster import's protocol extras

The first Samsung36 measurement matched **none** of the ledger's codes, though the rule was right: the IR Blaster import files Samsung36 with `unitUs: 500` and a `claims` entry (D52's protocol block, D65), because the IRP's 560 µs unit disagrees with the captures, and a JP1 file that left them out compiled the same code to different words. `Importer.block` now starts from the same `PROTOCOL_EXTRAS` the IR Blaster importer uses, and the compile gate strips `claims` before it compiles, as it did not need to until a protocol had one. `test_a_jp1_samsung36_key_compiles_to_the_signal_the_ir_blaster_import_has_for_the_same_code` holds the two together, so a new protocol extra reaches the JP1 files without anyone remembering to copy it.

### D148 — Each rule checked against the ledger

As in D119, D135, D138 and D142, the rules were measured against the ledger's own signals before any file was written:

| executor | upgrades | keys decoded | already in the ledger |
|---|---|---|---|
| RECS80 (45) | 17 | 467 | 99.8% |
| NEC 2DEV Combo | 8 | 424 | 98.1% (NEC1 270 of 275, NEC2 10 of 10, NECx1 136 of 139) |
| Sharp Combo | 11 | 582 | 97.3% |
| Sharp | 6 | 231 | 97.0% |
| Samsung36 | 19 | 690 | 91.7% |
| Proton | 12 | 256 | 79.7% |
| F12 | 9 | 62 | 50.0% (31 of 62) |
| Denon-K | 11 | 1,246 | 46.5% |

**The two lowest are kept, and are the least proven of the batch.** F12's rate rests on 62 keys: two of its upgrades (Emerson RC-7635 and iHome ih5) match 8 of 8 and 9 of 9, so the rule is right for the frame, and the other six upgrades that yield keys hold 45. Denon-K's three plain command sets (`Denon 3312`, `AVR-S510BT`, `AVR-S760H Zone 1`) match 58 of 59, 45 of 45 and 57 of 58, so the rule is right for the frame there too; what this does not tell apart, for the larger receivers' upgrades (`AVR-x4000` has 360 functions), is the ledger's thinner coverage of their codes from a fault in a function's reading. A rate this low would be a reason to drop a rule, except that the rule's matches are not by chance: a wrong orientation or a wrong complement matches almost nothing (D142).

### D149 — What is skipped

Of the 93 upgrades **3 yield nothing**: `Denon AVR S500BT` and `Denon AVR X5200W` (Denon-K: every function has one OBC byte where the translator needs two) and `DX Antenna DTA-3500` (F12: its device parameter is `null`). Of the 4,276 functions 3,958 are keys and **318 are skipped**: 158 have no single OBC byte (92 F12, 36 Samsung36, 30 RECS80), 156 no second one (112 Denon-K, 34 NEC 2DEV Combo, 10 Sharp Combo) and 4 are F12 functions whose device is missing or over four bits. All are in `IMPORT.md`, and the skipped upgrades raise "skipped: no function decodes" from 5 to 8.

### D150 — What it does to the rest

The corpus is **16,076 remote files and 642,413 keys**, 5,421 brands (11 more), 281,128 models (163 more) and 170,746 signals (449 more). Both bundle profiles are 56,438,784 bytes and `rl bundle --verify` passes on each. **54.6% of the new keys map to the vocabulary** (§22), so the `jp1` row goes from 72.5% to 71.8%. The search evaluation's draw moved again (D99): **none of its six misses is a device of this import**, but one of them shows a weakness the JP1 names make worse. `PHILIPS BDP 3390`, an exact query, is answered with `Philips BDP3300` and not with `BDP 3390`: the whole text, `philipsbdp3390`, is one edit from the key of a JP1 model whose name repeats its brand, which scores 928, and D96's rule 4 then does not try the run inside it. Typed as a brand and a model, or without the brand, the exact model is first. A JP1 model name begins with its brand in 2,061 of the 2,351 models that only JP1 has (the IR Blaster's: 1,580 of 271,250), so the weakness grows with this source; it is in TODO.md and not fixed here, because a change to the matcher changes its vectors (D97).

**Not read**: 792 upgrades of 171 executor names, counted in `IMPORT.md`: `MCE` (83) and `Nokia32` (40), which need protocols admitted through D18's gate (D145), `Manual Settings` (75 and 31 more of its spellings, raw executor bytes with no protocol), `XMP` and `XMP (Slingbox)` (48), `Pioneer MIX` (27), `GI Cable` (20), `RCA` (20), `Panasonic (old)` (19), `RC-5/5x Combo` (16), `TDC` (16), `RC6-M-20n` (15), `NEC1-f16 Official` (13), `RC5/6 combo` (12), and 157 executors of fewer than 12 upgrades each (388 in all), and the 2,073 KeymapMaster files (TODO.md). `RCA` needs a check first: the executor's default carrier is 56 kHz and the ledger's `RCA` is 38 kHz.

**Not proven.** That a function does what its name says (§28); that the Denon-K and F12 rules are right where the ledger has no copy of a code (D148); that Sharp Combo's `Denon` branch is right, since all 582 of its functions are Sharp frames and the branch it shares with the Denon Combo is unexercised here; that Samsung36's 500 µs unit (D65) is the one a Samsung receiver wants, which is the IR Blaster import's claim and not measured here.

## 36. More JP1 upgrades: Pioneer, RCA, Panasonic Multi-Device, RECS80 (68), JVC-48 and Teac-K

The executors whose protocols the ledger already registers and had not been read: **`Pioneer`** (8 upgrades) and **`Pioneer MIX`** (27), **`Panasonic Multi-Device (Hacked)`** (8), **`RECS80 (68)`** (2), **`JVC-48`** (1), **`Teac-K`** (2), and the RCA family, **`RCA`** (20), **`RCA-56`** (1), **`RCA-38`** (3) and **`RCA-38 Official`** (1). **73 upgrades are read and 48 become files and 2,228 keys**, all Plausible; the JP1 source is now **2,566 files and 99,929 keys**. The reader's new pieces are `rca_signal`, `pioneer_mix_signal` and five more branches of `simple_signal` and `combo_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D151 — The rules are the translators' own

As in D141, each executor's `CmdTranslator` (`protocols.ini`, and for the one that is a class RemoteMaster's `PioneerMixTranslator`) says what the OBC is, the device is a parameter, and a `null` parameter is in `FixedData`:

- **Pioneer** is `Translator(lsb,comp)` for device and function: one frame. The ledger registers Pioneer as `Pioneer-2Part`, a first part `D0:F0` and a second `D:F` (pioneer.py), and a single frame is its degenerate case with the halves equal, the convention the IR Blaster import uses for its own.
- **Pioneer MIX**, variants 2 and 3, is `Translator(lsb,comp,3) PioneerMixTranslator()` over two bytes: the first is the function, `rev8(~OBC)`; the second is a flag byte whose lowest bit says a two-part signal and whose next two pick one of the upgrade's prefix commands (`Cmd1` to `Cmd4`, the executor's own notes: "Device 1 = the device code found in both the single-style signals and the first half of the double-style signals", "Device 2 = ... the second half"). A one-part signal is `Device 1` and the function; a two-part one is `Device 1` and the prefix command, then `Device 2` and the function: `Pioneer-2Part` with `D0:D` and `F0:F`. A `null` parameter is in `FixedData`, at the byte the `DeviceTranslator` offsets name (variant 2: 0, 2, 1, 3; variant 3: 0, 5, 1, 2, 3, 4). Variants 4 and 5 choose the device through more flag bits, and are not read.
- **Panasonic Multi-Device (Hacked)** is the Panasonic Combo of D141 with its two bytes the other way round: `Translator(lsb,comp,0,8,8) Translator(lsb,comp,1)` puts the sub device in the second byte and the function in the first; the OEM bytes are parameters 2 and 3, which the ledger's frame fixes at 2 and 32.
- **RECS80 (68)**: the six-bit function is `63 - (OBC >> 2)` as for RECS80 (45) (D146), and **the device is kept complemented** (`Translator(0,3,2,comp)`, where (45) has no `comp`), so parameter 0 is device 7. **JVC-48** is the device and sub device parameters and `rev8(~OBC)`. **Teac-K** is the first byte as `rev8(~OBC)` (the second is not part of the function), a four-bit device, the sub device parameter (or `FixedData`'s), and the OEM bytes 67 and 83, which the ledger's frame fixes.

### D152 — RCA is read only where it sends 38 kHz

The ledger has one RCA protocol, `RCA-38`, and its carrier is 38.7 kHz. The JP1 `RCA` executor sends 56 kHz, its second variant and `RCA-56` choose 57 or 56 kHz or 38 kHz in parameter 1, and `RCA-38` and `RCA-38 Official` send 38 kHz. **Only the 38 kHz upgrades are read** (6 files: `RCA-38` ×3, `RCA-56` ×1 and the `RCA` variant 2 ×2); the first variant of `RCA` has no carrier parameter and its 18 upgrades yield nothing, with the reason in `IMPORT.md`. Writing a 56 kHz code as `RCA-38` would name the wrong carrier, and a carrier that is not the protocol's nominal raises the `carrier-off-nominal` warning on every file; a protocol of its own would need D18's gate, and is in TODO.md. The function is the OBC as it is for `RCA-38` and `RCA-38 Official`, and the OBC complemented for `RCA` and `RCA-56` (`Translator(comp)`). The device is the four-bit parameter 0; **a `null` one is not read**, because the only upgrade that has it (`RCA-38 Official`, 84 functions) matches none of the ledger's codes under the executor's default.

What the 56 kHz upgrades would add is small for the TVs: the eight upgrades of the first variant whose device is 15, decoded the same way, match 207 of 246 (84.1%) of the ledger's `RCA-38` codes, which the IR Blaster import files at 38.7 kHz for every RCA. The upgrades of the other devices (a console, converter boxes, a VCR) match none of the ledger's codes.

### D153 — Each rule checked against the ledger

As in D119, D135, D138, D142 and D148, each rule was measured against the ledger's own signals before any file was written:

| executor | upgrades | keys decoded | already in the ledger |
|---|---|---|---|
| JVC-48 | 1 | 44 | 100.0% |
| RCA-56 (38 kHz) | 1 | 52 | 98.1% |
| Panasonic Multi-Device | 8 | 360 | 96.1% |
| Pioneer MIX (variants 2, 3) | 21 of 27 | 1,001 | 77.5% |
| RECS80 (68) | 2 | 55 | 67.3% |
| RCA-38 | 3 | 114 | 64.9% |
| Teac-K | 2 | 121 | 57.9% |
| Pioneer | 8 | 319 | 53.9% |
| RCA (variant 2, 38 kHz) | 2 | 162 | 52.5% |

**An independent check of Pioneer MIX's rule.** IrpTransmogrifier's teaser capture of a Pioneer receiver, decoded as `D0=170, F0=91, D=175, F=36`, is the ledger's golden vector for `Pioneer-2Part` (CITATIONS.md). Two forum upgrades of Pioneer plasma TVs, `PDP-5080HD` (`TV guide`) and `PDP-SX4280D` (`EPG`), have a function whose OBC and flag byte (`DB 03`, `DB 05`) the rule decodes to exactly that signal (`device 0xAAAF`, `function 0x5B24`), and a test holds both. The same upgrades' other functions are in the ledger at 52 of 60 and 50 of 51.

**What the rates do and do not say.** Eleven of Pioneer MIX's 21 upgrades match 95% or more, and two recorders (`DVR-HS_recorder2` and `3`, 132 keys) match none of the ledger's codes, though their device pair, 171 and 175, is that of two recorders that match every key; their prefix commands differ (58, 60 and 106, 108 against 44 and 42), and nothing here tells the ledger's gap from a fault in the rule for those commands. **RECS80 (68) rests on one upgrade**: `ADS CD` matches 37 of 40 with the complemented device and 16 of 40 without; the other, a doorbell's `Velleman 8050-8051`, matches nothing under any reading I tried (15 keys). **Teac-K's two upgrades both have device 0**, so the device mapping is untested beyond it. `JVC-48` is one upgrade, but its 44 of 44 agree with the IR Blaster data, a second source for a protocol whose IRP has no capture behind it (D68).

### D154 — What is skipped

Of the 73 upgrades **25 yield nothing**: 18 of the `RCA` executor's first variant (D152), 6 of Pioneer MIX's variants 4 and 5, and `RCA-38 Official` (D152). Of their 3,317 functions 2,228 are keys and **1,089 are skipped**: 579 at an RCA carrier that is not 38 kHz, 268 of Pioneer MIX's variants 4 and 5, 126 with no single OBC byte (blank functions: 56 RCA, 43 Pioneer, 27 RCA-38), 84 of `RCA-38 Official`, 31 with no second OBC byte, and 1 whose flag byte has a bit the variant does not use. All are in `IMPORT.md`; "skipped: no function decodes" goes from 8 to 33.

### D155 — What it does to the rest, what is not read, and what is not proven

The corpus is **16,124 remote files and 644,641 keys**, 5,422 brands (1 more), 281,214 models (86 more) and 171,210 signals (464 more). Both bundle profiles are 56,676,352 bytes and `rl bundle --verify` passes on each. **59.3% of the new keys map to the vocabulary** (§22), so the `jp1` row goes from 71.8% to 71.5%. The search evaluation's draw moved again (D99): none of its eight misses is a device of this import, and no exact query misses this time.

**Not read**: **719 upgrades of 161 executor names**, counted in `IMPORT.md`: `MCE` (83) and `Nokia32` (40), which need protocols admitted through D18's gate (D145), `Manual Settings` (75 and 31 more of its spellings), `XMP` and `XMP (Slingbox)` (48), `GI Cable` (20), `Panasonic (old)` (19), `RC-5/5x Combo` (16), `TDC` (16), `RC6-M-20n` (15), `NEC1-f16 Official` (13), `RC5/6 combo` (12), and 149 executors of fewer than 12 each (362 in all); and the 2,073 KeymapMaster files. Of the registered-protocol executors I looked at and did not read: `Pioneer 3DEV` (6) and `4DEV` (8), whose translators pick the device with a bit of each of two bytes and need the executor's code read; `Pioneer DVD` (4); `Pioneer MIX`'s variants 4 and 5 (6 upgrades); the Panasonic MIX, Mixed and VCR combos (16); `Panasonic Combo2` (6), the old name of `Panasonic Multi-Device (Hacked)` in `protocols.ini`, five of whose upgrades are a variant (2) that file does not define while the sixth matches 34 of 34 of the ledger's codes and adds none; `Thomson` and `Thomson7` (12), whose rule matched only the two upgrades with parameter 3 (43 of 160 keys); `Blaupunkt` (3), whose codes match under every mapping of the function I tried, so nothing checks the rule; `Fujitsu` (2), whose `FixedData` starts with `03`, which would be an `E` of 3 the ledger's frame cannot carry (not checked); and `RCA` at 56 kHz (D152).

**Not proven.** That a function does what its name says (§28); that Pioneer's 53.9% is the ledger's thinner coverage of laser disc and tape codes and not a fault (its CD player upgrade matches 23 of 25 and the other seven upgrades 149 of 294); that a single Pioneer frame written as two equal parts sends what the executor sends, which sends it once and repeats it (the IR Blaster import makes the same choice); that `RCA-38`'s 38.7 kHz is near enough to the upgrades' 38 kHz; D153's two single-upgrade rules, RECS80 (68) and Teac-K.

## 37. Registering NEC1-f16 and NEC2-f16

NEC1 and NEC2 send a fourth byte that is the complement of the third, and every importer so far has refused a code whose fourth byte is anything else: 635 of the IR Blaster database's codes (D67), 1,087 functions of the JP1 Yamaha combo's Y1 to Y3 styles (D140), and the upgrades of the `NEC1-f16 Official` executor (13). IrpTransmogrifier has the relaxed forms, `NEC1-f16` and `NEC2-f16`, in which that byte, `E`, is a field of its own. **They are registered here and nothing reads them yet**: this is D18's three gates for two protocols, and the importers that need them are separate changes. The code is `src/remote_ledger/protocols/nec.py`, the evidence `tests/vectors/CITATIONS.md` and `tools/misc_capture_audit.py`.

### D156 — What a ledger form carries

The IRPs are `{38.4k,564}<1,-1|1,-3>(16,-8,D:8,S:8,F:8,E:8,1,^108m,(16,-4,1,^108m)*)` for `NEC1-f16`, with NEC1's ditto repeat, and the same frame repeated whole (`)*`) for `NEC2-f16`, verbatim from IrpTransmogrifier's database at the commit the other NEC protocols cite and in its 1.2.14 release. A form has a device, a subdevice and a function and no fourth field, so **`function` is the sixteen bits `F * 256 + E`, the two bytes in the order they are sent** (a packing the ledger chose, as for Samsung36's `E:F` and Pioneer's `D0:D`; the pair is 0 to 65,535). A code whose `E` is `~F` is the plain protocol, and the encoder gives exactly NEC1's or NEC2's signal for it: a test holds that for every F.

### D157 — The three gates

**Gate 1**: the IRP strings and where they came from (above). **Gate 2a, on hardware**: IrpTransmogrifier's own test data holds IrScope captures of five remotes, and the audit tool decodes **84 keys** from them by a threshold decoder written from the frame layout, compares the fields with the decode IrpTransmogrifier's own test expects, and re-encodes each to the same bit pattern: 32 keys of an NEC1-f16 remote, 6 and 4 of two Yamaha receivers (NEC1-Yamaha Y1), 31 of an NEC2-f16 remote (one more, glitched, has no decode and is left out) and 11 of a remote whose frames are NEC1-rnc and also NEC2-f16. A capture carries instrument bias, so this says layout, bit order and framing, not durations; the NEC2-f16 remote's unit is nearer 504 µs than 564. **Gate 2b**: four vectors rendered from the 1.2.14 release, two for each protocol, whose parameters are keys those remotes send; our timings under the release's own rounding reproduce each exactly and the raw words differ at the places NEC1's and NEC2's do (4, 71, 72 and 75; 4 and 71). All four are **reproducible, none published**, which the test suite's warning says on every run. **Gate 3**: `tests/test_nec_f16.py`, with every `F:E` pair of both protocols round-tripped through a decoder written from the layout.

### D158 — What `E` is in the styles that matter

The Yamaha styles and the `rnc` style are rules for `E`, and the captures and the release both say what they are. **NEC1-Yamaha** (IrpTransmogrifier's own protocol, style `Y` 1 to 3): `E = F ^ 0x7F` for Y1 (bits 0 to 6 of F complemented), `F ^ 0xFE` for Y2 (bits 1 to 7) and `F ^ 0x7E` for Y3 (bits 1 to 6). **IrpTransmogrifier's render of `NEC1-Yamaha {D=127,S=1,F=88,Y=1}`, a key of one of the captured receivers, is byte for byte its render of `NEC1-f16` with `E = 88 ^ 0x7F = 39`**, which is what the ledger would write as function `88 * 256 + 39`. **NEC1-rnc**: `E` is F's complement with its nibbles swapped; the `NEC2-rnc` capture's 11 keys are that, as NEC2-f16.

### D159 — What it does to the rest, and what is not proven

Nothing changes in the corpus, the index or the bundle: the registry holds **30 protocols** instead of 28. What the registration opens is in TODO.md: D67's 635 IR Blaster codes (406 NEC1, 229 NEC2), the JP1 Yamaha styles (D140, 1,087 functions) and the `NEC1-f16 Official` upgrades. **Not proven**: that a receiver acts on a frame whose `E` is not `~F` (the captures show that remotes send them, not that a device needs them), the 38.4 kHz carrier (the captures measure 37.8 to 38.4 kHz, and a file's `carrierHz` is authoritative, D3), and `NECx1-f16`, `NECx2-f16` and `NEC-f16`, which IrpTransmogrifier also has and the ledger does not register.

## 38. The IR Blaster import reads the relaxed NEC codes

§37 registered `NEC1-f16` and `NEC2-f16`. The IR Blaster import (§17, §18) is the first reader: it had refused **964 distinct codes** (2,066 keys) of the database for want of a frame to hold them (D54), and 635 of those codes, 1,510 keys, are NEC and NEC2 frames whose fourth byte is not the complement of the third (D67). `src/remote_ledger/irblaster/hex_nec.py` and its tests change; the JP1 Yamaha styles are the next change.

### D160 — What the map does now

A `NEC` or `NEC2` code of the database whose fourth byte is not the complement of its third maps to **`NEC1-f16` or `NEC2-f16`**, its `function` the pair `F * 256 + E` (D156). A `NEC` code is NEC1's frame, one frame and no ditto, so it lands on `NEC1-f16` whatever its pattern; D61's lead-out finding is unchanged. `NECx1` and `NECx2` have no such code in the database and their relaxed forms are not registered, so a code of theirs with another fourth byte would still be refused, with the pattern named. The four patterns D67 counted (Yamaha y1, y2, y3 and rnc) and the "unrelated" ones are all held: the file does not say which a key is, since a form has no field for it (D158 has the rules).

**The import adds 71 files and 1,510 keys**: 53 files and 1,076 keys of `NEC1-f16`, 18 files and 434 keys of `NEC2-f16`, each in a file of its own beside the remote's NEC1 or NEC2 file (R3: one protocol a file), so no existing file changes. Ids split over several files go from 573 to 617, ids with no file from 27 to 7, and **the keys skipped from 2,066 to 556**: 329 distinct codes, which is 964 less the 635, all of them Aiwa and REC80 frames (Fujitsu, SharpDVD, Teac-K, Panasonic), none NEC.

### D161 — Checked against the app's own waveform

`tests/test_irblaster_hex_nec.py` holds a fixture of the app's own output for 140 NEC-family codes (`tools/irblaster_oracle_nec.py`, from SwiftRemote's own Dart code), and 24 of them, 16 NEC and 8 NEC2, have a fourth byte that is not the complement: they were "unrepresentable" and are now **matched**, every duration within the oracle's tolerance and the carrier within its own. The app sends these frames verbatim, so this is the independent check that the f16 reading is the frame the app plays. The signals the ledger holds rise by **635, the number D67 counted, to the code**.

### D162 — What it does to the rest

The corpus is **16,195 remote files and 646,151 keys**, 5,424 brands (2 more), 281,322 models (108 more) and 171,845 signals (635 more); the IR Blaster source is 10,084 files and 412,775 keys. Both bundle profiles are 56,832,000 bytes and `rl bundle --verify` passes on each. **70.7% of the new keys map to the vocabulary** (§22, 1,067 of 1,510): the `irblaster` row stays at 81.9%. The fold of D103 is measured again, over 9,381 devices: **357 fragments fold** (42 more), and the table there is updated. The search evaluation's draw moved again (D99).

### D163 — Not proven

That these are real devices' codes: D67 noted the "unrelated" ones may be mis-conversions, and the app and the database cannot say. That a device acts on a code whose fourth byte is not the complement (§37). That the 329 distinct codes still refused are all unrepresentable: they are other families' frames (D54).

## 39. More JP1 upgrades: the relaxed NEC frames

§37 registered `NEC1-f16` and `NEC2-f16` and §38 gave the IR Blaster import the first use of them. The JP1 importer had refused **1,087 functions** of the Yamaha combo's Y1 to Y3 styles (D140) and had not read `NEC1-f16 Official` (13 upgrades) or `NEC1-rnc` (3). **31 upgrades give 1,975 more keys, all `NEC1-f16`, Plausible**, in 29 more files; the JP1 source is now **2,595 files and 101,904 keys**. The reader's new pieces are three branches in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's. An upgrade that sends two protocols now has a protocol suffix on each of its files, so **13 existing Yamaha files are renamed** (`...-nec1.json`) and each has a sibling `...-nec1-f16.json`.

### D164 — The rules

- **`NEC 4DEV Yamaha Combo`**: the function and slot are D137's, and the Y style is the two bits at offsets 13 and 14 of the command, **high bit first** (`Translator(3,1,13,1) Translator(3,1,14)`): 0 is plain NEC, 1 to 3 are Y1 to Y3. The frame's fourth byte is then the third with bits complemented, as IrpTransmogrifier's `NEC-Yamaha` says (D158): `E = F ^ 0x7F` for Y1, `0xFE` for Y2, `0x7E` for Y3, and the signal is `NEC1-f16` or `NEC2-f16` by the function's style bit, with `function = F * 256 + E`. A Y style in an NECx frame has no registered form and is refused with that reason.
- **`NEC1-f16 Official`**: four OBC bytes, each `Translator(lsb,comp)`: device, sub device, `F` and `E`.
- **`NEC1-rnc`**: `Translator(lsb)`, so `F` is the OBC reversed (not complemented), and `E` is its complement with the nibbles swapped (IrpTransmogrifier's `NEC1-rnc`): `NEC1-f16` holds it. A `null` device or sub device is in `FixedData`, each byte reversed (`80 7F` is device 1, sub device 254).

### D165 — Each rule checked against the ledger, and against captures

| executor | upgrades | keys decoded | already in the ledger |
|---|---|---|---|
| NEC1-f16 Official | 13 | 751 | 75.2% |
| NEC 4DEV Yamaha Combo, Y1 | 15 | 575 | 53.6% |
| NEC 4DEV Yamaha Combo, Y2 | 15 | 261 | 20.7% |
| NEC 4DEV Yamaha Combo, Y3 | 15 | 251 | 7.2% |
| NEC1-rnc | 3 | 137 | 64.2% |

The ledger already held NEC1-f16 codes only from §38 (IR Blaster: 410 Y1, 75 Y2 and 19 Y3 keys, 47 rnc), so the Y rates are the share of the upgrades' keys that the IR Blaster data has, and the rule is what makes any of them match: **a wrong mapping of Y1, Y2 or Y3 would match almost nothing, and 308, 54 and 18 keys match**. Three independent checks stand beside them, each worked by hand from IrpTransmogrifier: the captured Yamaha receiver's FM, NEC1-Yamaha {D=127,S=1,F=88,Y=1}, is `NEC1-f16` with `E = 88 ^ 0x7F = 39` (a test decodes a Yamaha combo function to exactly that); the `NEC2-rnc` capture's D=1, F=1 is `E = 239` and F=41 is `E = 109` (a test decodes `NEC1-rnc` functions to both); and `9E D8 BF F9` is the NEC1-f16 capture D=134, S=228, F=2, E=96 (a test decodes a `NEC1-f16 Official` function to it).

### D166 — What is skipped

22 Yamaha combo functions and 12 `NEC1-rnc` ones are blank or have the wrong number of OBC bytes; nothing of the 31 upgrades yields nothing. `NEC1-rnc`'s second upgrade (`CM-7500GB16`, `FixedData` `80 00`, 47 keys) matches none of the ledger's codes under the executor's default sub device of 0, while the first (`80 7F`, sub device 254) matches 45 of 47: the upgrade may mean the complement, and the file says the default.

### D167 — What it does to the rest, what is not read, and what is not proven

The corpus is **16,224 remote files and 648,126 keys**, 5,426 brands (2 more), 281,366 models (44 more) and 172,617 signals (772 more). Both bundle profiles are 57,032,704 bytes and `rl bundle --verify` passes on each. **31.7% of the new keys map to the vocabulary** (§22, 626 of 1,975): receivers' sound modes and zone keys are the Yamaha combo's commands, so the `jp1` row goes from 71.5% to 70.8%. The search evaluation's draw moved again (D99): none of its ten misses is a device of this import.

**Not read**: `NEC1-f16x` (1 upgrade), `NEC Combo` (3) and `NEC 1or2 Combo` (5), and the 703 upgrades of D155's 719 that remain. **Not proven.** That a function does what its name says (§28); that the Yamaha receivers and their remotes need `E` as the Y style says (§37); that `NEC1-rnc`'s `null` sub device is the one in `FixedData` for the second upgrade (D166).

## 40. The matcher: a near miss of a long run no longer hides an exact part of it

D150 found a weakness the JP1 names make worse: `PHILIPS BDP 3390`, an exact query, was answered with `Philips BDP3300` and not `BDP 3390`; the next draw had `DIGIQUEST DGQ 600 HD` the same way. D96's rule 4 did not try a run that lies inside a longer run that gave a candidate by the exact or the edit rule at 900 or more, and the whole text `philipsbdp3390` is one edit (928) from the key of a JP1 model whose name repeats its brand, so `bdp3390` was never looked at, though it is the exact model of the brand the text names. The change is in `MatchIndex._rank`; the rule is in the module's docstring and in D96.

### D168 — The rule

A run inside a longer run that gave a candidate **by the exact rule** is not tried, as before (`bdp-s360` is `bdps360` and not also another brand's `s360`). A run inside a longer run that gave a candidate **by the edit rule at 900 or more** is now tried, **only among the models of the named brands, and only an exact match of it counts**. The restriction on the other brands keeps `3390` of another maker out, as the cover always did; the restriction to an exact match keeps the part's own near matches out. The first version of the change let any match of the part in, and **three typo queries went from the first answer to the second** (`BUSH ID LCD 26 TVD 72 HD`, `GRUNDIG ST 70 - 284 DOLBY VNG TITAN` and `TOSHIBA 32 XV 555 D(MEIDA)`), because the part's near match scored as high as the whole run's. That version found two more typos in the first five (`SONY HT-DDW709`, `GOLDEN INTERSTAR DSR 0701`: 278 and 313 against 280 and 310), and the exact-only rule gives them up: an exact match is the only thing the hidden part was owed, and a looser rule is a different change, to be made on its own evidence. With no brand named there is no brand to look inside, and nothing changes.

**Measured on the 320 queries of D99, the same catalog under both rules**: 280 at the first answer either way, and 311 against 310 in the first five. One query is fixed (`ONKYO TX-SR703`, a dropped suffix: the JP1 `Onkyo TX-SR702` covered it and the catalog's own `TX-SR703` is now first); one gets an extra candidate in front of the answer it had (`Laser Lsaer BLU-BD3000` is now `BLU-BD3000` then `Laser BLU-BD3000`, both hits); one typo goes from first to second (`GRUNDIG ST 70 - 284 DOLBY VNG TITAN`: the exact shorter model `ST 70 - 284 DOLBY` inside the typed text now outranks the longer one it was meant for, which is what the same text scores when no run covers it). On the real catalog both exact misses now answer as they should: `PHILIPS BDP 3390` gives `BDP 3390` then `Philips BDP3300`, and `DIGIQUEST DGQ 600 HD` gives `DGQ 600 HD` first.

### D169 — The vectors a port is held to, and what is not proven

`tests/vectors/matching_vectors.json` gains **three catalog entries and two queries and loses nothing** (71 lines added, none removed; the first 41 queries are unchanged): a model that repeats its brand beside one an edit away and another brand's part of it, asked once as `ZED ZX 7790` (the exact `ZX 7790` first, the repeating model second, the other brand's `7790` out) and once as the repeating model itself (exact, and everything inside it hidden). The old matcher answers the first with the repeating model alone. `suggest_vectors.json` carries the same catalog and gains the three entries, and ZED's list goes from 3 models to 5. A port of the matcher must add the rule: nothing else of the vectors moves. **Not proven**: that this is the right trade on real typing, which has still not been measured (D98); the matcher was changed on generated queries and checked on the same ones, and a change that fixes one of them and moves two is a small sample.

## 41. Registering MCE

The JP1 upgrades of the `MCE` executor are the largest group the importer had not read: 83, which D145 said needed a protocol the ledger did not have. **`MCE` is Windows Media Center's remote protocol, RC-6 mode 6 with a 32-bit payload**, and IrpTransmogrifier has it by that name. It is registered here through D18's three gates and **nothing reads it yet**: the corpus, the index and the bundle do not change, and the registry goes from 30 to 31 protocols. The code is `src/remote_ledger/protocols/mce.py`, the evidence `tests/vectors/CITATIONS.md` and `tests/test_mce.py`.

### D170 — What a form carries

The IRP is `{36k,444,msb}<-1,1|1,-1>((6,-2,1:1,6:3,-2,2,OEM1:8,S:8,T:1,D:7,F:8,^107m)*,T=1-T) {OEM1=128}[D:0..127,S:0..255,F:0..255,T@:0..1=0]`, verbatim from IrpTransmogrifier's database at the commit the other protocols cite and in its 1.2.14 release. **`device` is D (7 bits), `subdevice` is S (the second OEM byte, 8 bits) and `function` is F**; the first OEM byte is the IRP's 128 and the frame's mode is 6. The toggle `T` is not in the file (D3b), as for RC6: everything compiled has `T=0`, the IRP's default, and `encode` takes `toggle` for the tests. Microsoft's remotes send `S = 15` (`0x800F` is their code), and a form states it, as NEC's forms state a subdevice.

The shape is RC6's (`rc6.py`) with a longer payload: a 6-unit mark and 2-unit space, a start bit, the mode `110`, a double-width trailer that is a fixed 0, the 32 bits, and the `^107m` extent measured from the first mark. `RC6-M-32`, the generic form with another first OEM byte and other modes, is not registered: nothing here sends one.

### D171 — The three gates

**Gate 1**: the IRP and where it came from; RemoteMaster's own `MCE` executor has the same widths from another source (`Device:7=4`, `OEM2:8=15`, OEM1 128 in its fixed data). **Gate 2a**: IrpTransmogrifier's test data holds no MCE capture (its `OrtekMCE` files are another protocol), so the evidence is the LIRC project's: **225 keys of four MCE remotes' confs**, already in this repository as raw forms, which `tests/test_mce.py` reads with a decoder written from the frame layout (not from the encoder) at the unit each has: every one is mode 6, trailer 0, OEM1 128 and second OEM byte 15, and re-encodes, with its toggle, to the same sequence of levels. The Xbox 360 remote is device 116, which is the `116 15` of the forum's upgrade for it. **They are remotes' confs expanded by lircd and not hardware captures**, and say layout and bit order, not absolute durations (their bits are 427 to 455 µs, 0.96 to 1.03 of the IRP's 444). **Gate 2b**: two vectors rendered from the 1.2.14 release for keys of those remotes (the Xbox 360's OPEN, `800F7428`, and the mceusb remote's Media, `800F0480`); our timings under the release's rounding reproduce each exactly and the raw words differ only in the lead-out, as RC6's do. **Reproducible, none published**, which the suite's warning says on every run. **Gate 3**: `tests/test_mce.py`: every D and F for three values of S, every S for four devices, a seeded sample with the toggle either way, the order of the fields, the extent and the LIRC remotes.

### D172 — What it does to the rest, and what is not proven

Nothing in the corpus moves. What the registration opens is in TODO.md: the 83 JP1 `MCE` upgrades. **Not proven**: that a Media Center receiver acts on a frame sent with the toggle at 0 (a file cannot set it); the 36 kHz carrier (LIRC's confs set none and lircd sends 38 kHz, and a file's `carrierHz` is authoritative, D3); and **that the LIRC data is evidence of the protocol and not of itself**: its confs were written by people who read the same remotes, so it corroborates the layout, not the timings.

## 42. More JP1 upgrades: the MCE executor

§41 registered `MCE`, and the JP1 importer reads the executor that sends it: **83 upgrades, 81 files and 3,391 keys**, all Plausible. The JP1 source is now **2,676 files and 105,295 keys**. The reader's new piece is one branch of `simple_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D174 — The rule

`CmdTranslator=Translator(0)`: **the function is the OBC as it is** (`0C` is Power, `28` the Xbox 360 remote's OPEN). The device is the seven-bit parameter 0 (`Device:7=4`) and the second OEM byte the eight-bit parameter 1 (`OEM2:8=15`); the first OEM byte is the executor's 128 and the signal is `MCE` with `device`, `subdevice` and `function` as D141's executors have them. The executor's second variant keeps its device in doubled bits in `FixedData` (`BitDoubler`) and its parameters are the same, so both variants are read from the parameters; **an upgrade whose parameters are `null` is not read** (one upgrade, 48 functions), since the ledger has no copy of its codes to say what the default means.

### D175 — Checked against the LIRC project's remotes

The ledger held no MCE signal, so there is no hit rate against it. **The LIRC remotes of four MCE devices are the check**, from another project: D171's 225 keys, decoded by `tests/test_mce.py`'s layout decoder, are the devices 4, 20, 36 and 116 with second OEM byte 15. The forum's upgrades with the same device and second OEM byte send **every function** of the Xbox 360 remote (116: 45 of 45), the Vista remote (20: 43 of 43) and the Hauppauge remote (36: 43 of 43), and **69 of the 94** of the mceusb remote (4), whose upgrades differ in what they keep: 200 of the 225 in all. A test holds it over the committed files. A wrong device, second OEM byte or function mapping would match none of them.

### D176 — What is skipped

2 of the 83 upgrades yield nothing: `Windows_Media_Center` decodes no function, and `HP 5880 Discrete Upgrade on HTPro` has no functions. Of 3,693 functions 3,391 are keys and **302 are skipped**: 254 are blank in their upgrades (the Xbox 360 upgrades' `+100` among them) and 48 are the `null` upgrade's. All are in `IMPORT.md`.

### D177 — What it does to the rest, what is not read, and what is not proven

The corpus is **16,305 remote files and 651,517 keys**, 5,438 brands (12 more), 281,505 models (139 more) and 174,305 signals (1,688 more). Both bundle profiles are 57,376,768 bytes and `rl bundle --verify` passes on each. **80.0% of the new keys map to the vocabulary** (§22, 2,713 of 3,391): a media-center remote's keys are the vocabulary's, and the `jp1` row goes from 70.8% to 71.1%. The search evaluation's draw moved again (D99): none of its nine misses is a device of this import.

**Not read**: **620 upgrades of 158 executor names**, counted in `IMPORT.md`: `Nokia32` (40), which needs a protocol admitted through D18's gate (D145), `Manual Settings` (75 and 31 more of its spellings), `XMP` and `XMP (Slingbox)` (48), `GI Cable` (20), `Panasonic (old)` (19), `RC-5/5x Combo` (16), `TDC` (16), `RC6-M-20n` (15), `RC5/6 combo` (12), and 148 executors of fewer than 12 each (359 in all); and the 2,073 KeymapMaster files. **Not proven.** That a function does what its name says (§28); that a Media Center receiver acts on a frame sent with the toggle at 0 (§41); that the 25 of the mceusb remote's 94 functions that the forum's upgrades do not send are not a fault in the rule (the Xbox 360, Vista and Hauppauge remotes, 131 keys, match in full).

## 43. Registering Nokia32 and Panasonic_Old

The two largest groups of the JP1 upgrades that wait for a protocol after MCE: **`Nokia32`** (40 upgrades, 1,688 functions with a code) and **`Panasonic (old)`** (19 upgrades, 794), the cable and digital boxes' remotes. IrpTransmogrifier has both, with hardware captures in its own test data. They are registered through D18's three gates and **nothing reads them yet**: the corpus, the index and the bundle do not change, and the registry goes from 31 to 33 protocols. The code is `src/remote_ledger/protocols/nokia32.py` and `panasonic_old.py`, the evidence `tests/vectors/CITATIONS.md` and `tools/misc_capture_audit.py`.

### D178 — What a form carries

**`Nokia32`** is a pulse-position code at 36 kHz with two bits to a symbol (a 6-period mark, then a space of 10, 16, 22 or 28), every time **in periods of the file's carrier** (`1p`), so its durations follow `carrierHz`. A form's `device` is D, `subdevice` is S, and **`function` is `X * 256 + F`** (fifteen bits; a packing the ledger chose, since a form has no fourth field and X is part of the IRP's parameters). The toggle `T` is not in the file (D3b), as for RC6. **`Panasonic_Old`** is a 57.6 kHz pulse-distance frame of 833 µs units with a five-bit device and a six-bit command, each followed by its complement, and a 44 ms gap; `device` is D, `function` is F, and a `subdevice` is refused. It is **not** the ledger's `Panasonic`.

### D179 — The three gates

**Gate 1**: the IRPs, verbatim, at the commit the others cite and in the 1.2.14 release. **Gate 2a, on hardware**: `tools/misc_capture_audit.py` reads IrpTransmogrifier's captures with decoders written from the layouts and not from the encoders: **30 of 30 Nokia32 keys** (the odd ones with the toggle set), **4 of 4 Panasonic_Old keys** and **16 of 16 Pronto strings** of a cable box's remote (all D=27, the JP1 executor's own default device, `Device 1=27`), each decoding to the `.exp` fields and, for the captures, re-encoding to the same bit pattern. A capture carries instrument bias: the Nokia32 remote's carrier was 35,303 Hz and the strings' unit 849 µs against the IRP's 833, so none of the strings is a byte-for-byte vector. **Gate 2b**: four vectors rendered from the 1.2.14 release, two for each, whose parameters are keys of those remotes; our timings under the release's rounding reproduce each exactly and the raw words differ only in the lead-out. **Reproducible, none published**, which the suite's warning says on every run. **Gate 3**: `tests/test_nokia32.py` and `tests/test_panasonic_old.py`.

### D180 — What it does to the rest, and what is not proven

Nothing in the corpus moves. What the registration opens is in TODO.md: the Nokia32 and Panasonic (old) upgrades. **Not proven**: that a receiver acts on a Nokia32 frame sent with the toggle at 0; Nokia32's `X`, which no captured key uses (all have `X=0`) and which the JP1 executor takes as a parameter; and `Nokia32_endframe`, `Nokia12`, `Nokia36` and `Panasonic2`, which IrpTransmogrifier also has and the ledger does not register.

## 44. More JP1 upgrades: Nokia32 and Panasonic (old)

§43 registered `Nokia32` and `Panasonic_Old`, and the JP1 importer reads the two executors that send them: **59 upgrades, 58 files and 2,442 keys**, all Plausible (39 files and 1,648 keys of Nokia32, 19 and 794 of Panasonic (old)). The JP1 source is now **2,734 files and 107,737 keys**. The reader's new pieces are two branches of `simple_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D182 — The rules

- **`Nokia32`**: `CmdTranslator=Translator()`, so **F is the OBC as it is**. The device translators put the executor's four parameters in `FixedData` as bytes (the `High X-bit` mode, Device, Sub Device and X, in that order), so a `null` parameter is the byte there. A form's `function` is `X * 256 + F`, with X the **low seven bits** of the X parameter: its top bit is the IRP's toggle `T`. In the mode Toggle a set top bit is the toggle's start (`166` is `0x80 | 38`, X = 38, as the other upgrades of those boxes have it); **in the mode Constant a set top bit is a toggle bit that a form cannot send** (its toggle is not in a file, D3b) and the mode Release is not read, each said in `IMPORT.md`.
- **`Panasonic (old)`**: `Translator(comp,0,2,6) Translator(lsb,1,6,0)`: **the top six bits of the byte are F, reversed, and the low two are the complement of the device the function uses** (0 to 2 are Device 1 to 3, the parameters; 3 is `OBC/2`, which is not read). A device the upgrade leaves `null` is the one in `FixedData` (`Translator(lsb,comp,i,5,...)`: its five bits after the first, reversed and complemented: `90` is 27), which is the executor's default. The signal is `Panasonic_Old` with `device` and `function` and no subdevice.

### D183 — Each rule checked against another project's data

The ledger held neither protocol, so there is no hit rate against it, and **both rules are checked against data from elsewhere**.

**Panasonic (old)**: IrpTransmogrifier's test data holds sixteen Pronto strings of a cable box's remote, decoded as D=27 with the keys 1 to 9 and 0 as F = 16 to 24 and 25 (§43). **Seventeen of the nineteen upgrades name their ten digit keys, and every one of them sends exactly those functions**, the three Technics audio upgrades (devices 9 and 12) included; the other two name no digits. The rule gets the sequence from the bytes `9B 0B 8B 4B CB`, which are 25, 16, 17, 18 and 19; a wrong bit order or complement would give none of it.

**Nokia32**: the LIRC project's remotes for a Foxtel box (device 33, sub device 160), a Motorola VIP (35, 64), a Pace box (35, 128) and a Galaxis one (13, 80) decode with the layout decoder (154 keys, which `tests/test_nokia32.py` also re-encodes to the same symbols). **The forum's upgrades for the same device and sub device send 29 of the Foxtel remote's 30 keys, 40 of the Motorola's 41 and 40 of the Pace's 40: 109 of 111**, each with the same device, sub device, X and F. The Galaxis remote has X = 8 where the upgrades for device 13 have 45, another box that shares the address, and none of its 43 keys matches with X; **30 of its 43 functions are among theirs when X is left out**. A test holds both.

### D184 — What is skipped

One Nokia32 upgrade yields nothing: its mode is Release and its 40 functions are skipped for it. Of the 2,541 functions of the 59 upgrades 2,442 are keys and **99 are skipped**: 40 of that Release upgrade and 59 blank in their upgrades (17 Nokia32 and 42 Panasonic (old)). None is `OBC/2`. All are in `IMPORT.md`.

### D185 — What it does to the rest, what is not read, and what is not proven

The corpus is **16,363 remote files and 653,959 keys**, 5,446 brands (8 more), 281,604 models (99 more) and 175,271 signals (966 more). Both bundle profiles are 57,544,704 bytes and `rl bundle --verify` passes on each. **81.5% of the new keys map to the vocabulary** (§22, 1,990 of 2,442), so the `jp1` row goes from 71.1% to 71.3%. The search evaluation's draw moved again (D99): none of its seven misses is a device of this import.

**Not read**: **561 upgrades of 156 executor names**, counted in `IMPORT.md`: `Manual Settings` (75 and 31 more of its spellings), `XMP` and `XMP (Slingbox)` (48), `GI Cable` (20), `RC-5/5x Combo` (16), `TDC` (16), `RC6-M-20n` (15), `RC5/6 combo` (12), and 148 executors of fewer than 12 each (359 in all); and the 2,073 KeymapMaster files. **Not proven.** That a function does what its name says (§28); that a Nokia receiver acts on a frame sent with the toggle at 0 (§43); that the Galaxis remote's functions are the Thomson upgrade's with another X, which is what 30 of 43 say and not proof; that the executors' `OBC/2` and Release modes are what I took them to be, since I read neither.

## 45. Registering RCA, TDC-38, TDC-56 and GI_Cable

Four more families of the JP1 upgrades that wait for a protocol, each with hardware captures in IrpTransmogrifier's own test data: **`RCA`** at 56 kHz (the 18 upgrades of the executor's first variant that D152 left unread, and the `RCA Combo` ones), **`TDC`** (16 upgrades; the executor's variant chooses `TDC-38` or `TDC-56`) and **`GI Cable`** (20). They are registered through D18's three gates and **nothing reads them yet**: the corpus, the index and the bundle do not change, and the registry goes from 33 to 37 protocols. The code is `src/remote_ledger/protocols/rca.py`, `tdc.py` and `gicable.py` (and `rca38.py`, whose frame `RCA` shares), the evidence `tests/vectors/CITATIONS.md` and `tools/misc_capture_audit.py`.

### D186 — What a form carries

**`RCA`** is `RCA-38`'s frame (`rca38.py`'s `encode_frame`, so the two cannot drift apart) at the IRP's **58 kHz**; `device` is D, `function` is F, a `subdevice` is refused. The database holds four RCA entries and only this one and `RCA-38` are registered: `RCA(Old)` (a 40-unit first lead-in, a two-unit stop mark, a `+` repeat) and `RCA-38(Old)` are not, so a remote that needs them is still unread. **`TDC-38`** (38 kHz, 315 µs) and **`TDC-56`** (56.3 kHz, 213 µs) are one frame, biphase with a start of a mark and a space, a five-bit device, a five-bit subdevice and a seven-bit command, and an 89 ms gap: `device` is D, `subdevice` is S (required) and `function` is F as the IRP has it, so the remotes whose bit 0 is the complement of bit 1 and those whose is not are both carried. A last bit of 1 ends the frame on a space and the gap lengthens it (the tool renders one space of a unit and 89 ms). **`GI_Cable`** (the database's `G.I.Cable`, whose dots a ledger name does not take) is an intro of an 18,9-unit lead-in, F, D and a check nibble `C = -(D + F:4 + F:4:4)` least significant first, a stop mark and an 84-unit gap, and a repeat of 18, 4.5, 1 and 178 units; `device` is D, `function` is F, `C` is computed. None declares an extent: the gaps are gaps (D3).

**RCA's nominal carrier is the IRP's 58 kHz**, and that is a choice between neighbours: the captured RCA(Old) remote measured 57.3 kHz, the JP1 `RCA` executor sends 56 kHz and `RCA-38` is 38.7. `nominal_carrier_hz` is informational and a file's `carrierHz` is authoritative (D3); the JP1 reading, which comes next, will say which carrier it writes and why. D152's concern, that a 56 kHz code filed under a 38.7 kHz protocol names the wrong carrier, is what this removes.

### D187 — The three gates, and four things the audit found

**Gate 1**: the IRPs, verbatim, at the commit the others cite and in the 1.2.14 release. **Gate 2a, on hardware**: `tools/misc_capture_audit.py` reads the captures with decoders written from the layouts and not from the encoders: **13 of 13 RCA(Old) keys** and **31 of 31 RCA-38 keys** against `RCA` (the complements checked), **9 of 9 TDC-38** and **7 of 7 TDC-56 keys** (a biphase decoder), and **9 of 9 G.I.Cable keys** (the check nibble verified; four captures have no decode in the `.exp`), each decoding to the `.exp` fields; the audit also now **decodes the encoder's own frame again and requires the same fields**, for every protocol it covers, which makes "re-encodes to the same bit pattern" a check and not only a count of durations. **Gate 2b**: eight vectors rendered from the 1.2.14 release, two for each protocol, whose parameters are keys of those remotes; our timings under the release's rounding reproduce each exactly. **Reproducible, none published**, which the suite's warning says on every run (31 now, from 27). **Gate 3**: `tests/test_rca.py`, `test_tdc.py` and `test_gicable.py`.

The audit found four things the encoders do not say, **recorded in CITATIONS.md and not corrected**: (1) **RCA's unit is about 500 µs in both captures, not 460**: the RCA(Old) remote's bits are 1.089× the encoder's, as the RCA-38 remote's are (1.085×, D63), so a second remote at a second carrier agrees with the first; the encoder follows the IRP. (2) RCA(Old)'s first send has a 40-unit lead-in and every stop mark is two units, which is why only its bits are compared with `RCA`; the RCA-38 capture is `RCA`'s frame in full. (3) **G.I.Cable's capture pads every frame to one period of about 98.95 ms** (intro and repeat), where the IRP's gap is a fixed 84 units: the encoder's intro is 98.0 to 113.4 ms by the number of one bits, and the remote sends one length. Whether a receiver minds is not known. (4) TDC-56's remote measured 56,456 Hz against 56.3 kHz. RCA's byte differences from the tool are fifteen words and not the lead-in's two, because 58 kHz is a frequency word of 71, which is 58,384 Hz: the arithmetic is in the citation.

### D188 — What it does to the rest, and what is not proven

Nothing in the corpus moves. What the registration opens is in TODO.md: the `RCA` upgrades of 56 kHz and the `RCA Combo` ones, the `TDC` upgrades and the `GI Cable` ones. **Not proven**: that a receiver acts on RCA at 58 kHz with a 460 µs unit when the remotes measured send about 500; that a receiver minds G.I.Cable's variable intro period; TDC's `F` in remotes that do not follow the complement pattern (the database says there are some; the captured keys all follow it); and `RCA(Old)` and `RCA-38(Old)`, which IrpTransmogrifier also has and the ledger does not register.

## 46. More JP1 upgrades: RCA at 56 kHz, RCA Combo, TDC and GI Cable

The executors whose protocols §45 registered: **`RCA`** in its first variant (18 upgrades, the 56 kHz ones D152 left unread), **`RCA Combo (Official)`** (9), **`TDC`** (16) and **`GI Cable`** (20). **63 upgrades are read, 62 become files and 2,728 keys, all Plausible** (the 63rd, `Microsoft Xbox`, is a model another source already has: R19's fourth condition, in `IMPORT.md`); the JP1 source is now **2,796 files and 110,465 keys**. The reader's new pieces are `rca_signal`'s second branch, `rca_combo_signal`, `tdc_signal` and one line for GI Cable in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D189 — The rules

- **`RCA`, first variant:** `Translator()`, the function is the OBC as it is, the device is the four-bit parameter 0 (a `null` one is not read, as in D152); the ledger's `RCA` (58 kHz) where D152 had no protocol. The second variant and `RCA-56` keep their `Translator(comp)`, and where their carrier parameter is 0 (57 or 56 kHz) they now send `RCA` and not nothing; none of the three upgrades that have the parameter has that value, so the branch has a test and no data.
- **`RCA Combo (Official)`:** two OBC bytes. The first variant (`Translator(0,4,0) Translator(1,8,8)`): the device is the top nibble of the first byte, whose low nibble is the executor's fixed 15, and the function the second byte. The second variant (`Translator(0,4,0,comp) Translator(1,2,6) Translator(2,8,8,comp)`): the device and the function complemented, and the lowest two bits of the first byte a prefix choice, where anything but none sends another code first, which one signal cannot say (no function has one).
- **`TDC`:** `Variant:TDC-38|TDC-56, Device:5=10, Sub Device:5=26` are parameters 0 to 2, `Translator(0,7)` keeps the OBC byte's top seven bits, so **the function is `OBC >> 1`**; the variant is the ledger's `TDC-38` (0) or `TDC-56` (1). A `null` parameter is not read (no upgrade has one).
- **`GI Cable`:** `Translator(lsb)` and no device parameter (IrpTransmogrifier says `D==0` of the executor): **device 0, the function the OBC reversed**.

Every one of these writes the carrier of the registered protocol (D186): `RCA` 58 kHz, though the executor sends 56. `TDC (No Repeat)`, `TDC-38 Official` and `GI 4DTV Official` are other executors and are not read.

### D190 — Each rule checked against another project's data

The ledger held none of these four protocols, so there is no hit rate. The checks are from other projects, and each tests the rule it names:

- **G.I.Cable, LIRC and IrpTransmogrifier.** The LIRC remotes of cable boxes (777 keys of 23 files, decoded by `tests/test_gicable.py`'s layout decoder; no LIRC conf says what the protocol is) hold 68 functions of device 0. **The 20 upgrades send 973 of their 982 keys among them**; with the OBC unreversed 271 would be and complemented 32. IrpTransmogrifier's capture of a cable box names the digits 0, 1, 5, 6 and the four arrows (functions 0, 1, 5, 6, 52 to 55): **the upgrades agree on all 140 uses of those names**, and on Play (27) in 10 of 17, the other seven sending 56, a different box.
- **RCA, LIRC.** The LIRC remotes of RCA devices (878 keys of 22 files, 290 distinct codes, devices 5, 7, 8, 9, 11, 12, 13, 14 and 15: TVs, a game console's DVD kit, converter boxes, a VCR) against **the 18 upgrades of the first variant: 461 of 552 keys** (83.5%, devices 5, 7, 12, 14 and 15); complemented 32, bit-reversed 128. The misses are codes no LIRC remote has (a TV upgrade sends more keys than the LIRC remotes of its device) and the one upgrade of device 2, whose 24 keys have no LIRC remote at all. The nine **RCA Combo** upgrades: **403 of 552** (73.0%); the weakest of the four checks, the same device-by-device pattern (device 12: 110 of 126; device 15: 164 of 228).
- **TDC, IrpTransmogrifier's hardware capture of a TDC IPTV remote** (device 6, sub device 10): its keys 1 to 4 are 2, 5, 6 and 9 and Up, Down, Left, Right and OK are 25, 26, 29, 33 and 30. **The four upgrades for that device and sub device name those nine keys and send every function: 36 of 36.** The capture of a TDC-56 remote is device 14, which no upgrade has: **`TDC-56` (3 files, 120 keys) rests on the same translator and the variant parameter, and is not checked against a capture.**

### D191 — What is skipped

Of 2,884 functions of the 63 upgrades 156 are skipped: **128 are blank** (RCA 56, RCA Combo 17, TDC 19, GI Cable 36), one of RCA Combo's (`C0 3B`) has a low nibble that is not the executor's 15, and 27 are those of the upgrade that lost to an existing model. The files that "decode no function" go from 35 to 17, the 18 RCA upgrades that D152 said so of.

### D192 — What it does to the rest, what is not read, and what is not proven

The corpus is **16,425 remote files and 656,687 keys**, 5,451 brands (5 more), 281,716 models (112 more) and 175,991 signals (720 more). Both bundle profiles are 57,708,544 bytes and `rl bundle --verify` passes on each. **76.0% of the new keys map to the vocabulary** (§22, 2,073 of 2,728): a cable box's remote is the vocabulary's, and the `jp1` row goes from 71.3% to 71.4%. No new build warnings (157 as before), no file renamed. The search evaluation's draw moved again (D99): first answer **88.4%**, first five 99.4%, the thirteenth draw (about 89.4% on average); **neither of the two misses is a device of this import** and none of their answers holds one (`YAMAHA YAMAHA` expects a Yamaha tape deck of the JP1 source that is not among the 62; `SUNSTECH TL - X 19` an IR Blaster remote).

**Not read**: **516 upgrades of 153 executor names**, counted in `IMPORT.md`: `Manual Settings` (75 and 31 more of its spellings), `XMP` and `XMP (Slingbox)` (48), `RC-5/5x Combo` (16), `RC6-M-20n` (15), `RC5/6 combo` (12), and 147 executors of fewer than 12 each; and the 2,073 KeymapMaster files. **Not proven.** That a function does what its name says (§28); that the RCA files' 58 kHz is the carrier of the 56 kHz executor (D186) or that a receiver acts on RCA at 460 µs when the remotes measured use about 500 (§45); that `RCA Combo`'s 73% is not a fault in its rule (the same devices match in full in the first variant); and `TDC-56` (above).

## 47. Registering RC6-6-20

The Sky and Sky+ remotes' frame, which **`RC6-M-20n`** (15 JP1 upgrades: Sky, Sky+, Sky+ HD, Foxtel and Pace boxes) sends: RC-6 mode 6 with a 20-bit payload. IrpTransmogrifier has it with a hardware capture of a Sky+ remote in its own test data. It is registered through D18's three gates and **nothing reads it yet**: the corpus, the index and the bundle do not change, and the registry goes from 37 to 38 protocols. The code is `src/remote_ledger/protocols/rc6_6_20.py`, the evidence `tests/vectors/CITATIONS.md` and `tools/misc_capture_audit.py`.

### D193 — What a form carries

`{36k,444,msb}<-1,1|1,-1>((6,-2,1:1,6:3,<-2,2|2,-2>(T:1),D:8,S:4,F:8,-100m)*,T=1-T)`: a leader, a start bit, the fixed mode 6, the toggle `T` at double width (RC-6's place for it) and the payload, every other bit biphase. **`device` is D, `subdevice` is S (required) and `function` is F**; the toggle is not in the file (D3b), as for `RC6` and `MCE`, and the database says the executors do not toggle it. Two things differ from `MCE`'s frame, which `rc6_6_20.py` is modelled on: the gap is a gap and not an extent (`-100m`, so `extent_us` is `None`: a last bit of 1 ends the frame on a space that the gap lengthens, a 0 on a mark that gets a space of its own), and the toggle is in RC-6's place between the mode and the payload.

### D194 — The three gates, and one thing the audit found

**Gate 1**: the IRP verbatim at the commit the others cite and in the 1.2.14 release. **Gate 2a, on hardware**: `tools/misc_capture_audit.py` decodes **9 of 9 keys** of the Sky+ remote's capture with a decoder written from the layout, each to the `.exp` fields (D=0, S=12 and F), and the encoder's own frame must decode to the same fields (with the capture's toggle). The LIRC project's Sky, Pace, Foxtel and One For All remotes carry the frame too: `tests/test_rc6_6_20.py` decodes **451 keys of nine files** (all device 0, sub devices 0, 3 and 12, 197 distinct codes) by the layout decoder. They are confs expanded by lircd, not captures. **Gate 2b**: three vectors rendered from the 1.2.14 release, for keys of the capture (two ending on a mark, one on a space); our timings under its rounding reproduce each exactly. **Reproducible, none published** (32 now, from 31). **Gate 3**: `tests/test_rc6_6_20.py`.

**The audit found one thing the encoder does not say, recorded and not corrected: the capture's frames repeat at a constant 150 ms** whatever the key (the gap is 123.3 to 123.9 ms), where the IRP's gap is a fixed 100 ms. The encoder follows the IRP; the remote pads, as G.I.Cable's does (§45).

### D195 — What it does to the rest, and what is not proven

Nothing in the corpus moves. What the registration opens is in TODO.md: the 15 `RC6-M-20n` upgrades. **Not proven**: that a receiver acts on the frame with the toggle at 0; that a receiver minds a 100 ms gap where the remote's period is 150 ms; and the other modes of the RC-6 family (`RC6-M-16`, `RC6-M-24`, `RC6-M-28`, `RC6-M-56`), which IrpTransmogrifier also has and the ledger does not register.

## 48. More JP1 upgrades: RC6-M-20n

The executor whose protocol §47 registered: **`RC6-M-20n`**, the Sky, Sky+, Sky+ HD, Foxtel and Pace boxes' RC-6 (15 upgrades). **All 15 are read and become files, 535 keys, all Plausible**; the JP1 source is now **2,811 files and 111,000 keys**. The reader's new piece is `rc6_m_20n_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D196 — The rule

`Translator()`: **the function is the OBC as it is**. `DevParms` is `M=6, Device, Sub Device` (the `short` variant adds `T`), and the ledger's `RC6-6-20` is mode 6 alone (any other M is refused: none is). A `null` parameter is the value `FixedData` keeps, in the layout of the variant, which `rc6_m_20n_signal` reads: the first variant has M in bits 1 to 3 of the first byte, the sub device in its low nibble and the device in the second byte; the `short` variant doubles every bit as the bit and its complement (`BitDoubler`), M at offset 2, the device at 12 and the sub device's four bits at 28, 30, 36 and 38. **All 11 upgrades that give their parameters decode to the same signal from their `FixedData` alone**, which checks that layout (four upgrades have `null` parameters and need it). The third variant (a 33.6 kHz option) has no upgrade and is not read; the toggle `T` is not in a file.

### D197 — The rule checked against other projects' data

The ledger held no RC6-6-20 signal, so there is no hit rate. Two other projects' data check it:

- **LIRC.** The LIRC remotes of Sky and Pace boxes (451 keys of nine files, decoded by `tests/test_rc6_6_20.py`'s layout decoder at the unit each has; confs expanded by lircd) hold 38 codes of device 0 and sub device 0, 38 of sub device 12 and 121 of sub device 3, which no upgrade has. **The upgrades send 262 of their 278 keys of device 0 and sub device 0 and 179 of 181 of sub device 12 among them: 441 of 535.** The 76 keys of device 5 (the Amstrad DRX780 and Sky HD) have no LIRC remote.
- **IrpTransmogrifier's hardware capture of a Sky+ remote** (device 0, sub device 12; §47): its keys 0 to 3 are functions 0 to 3 and Up, Down, Left, Right and OK are 88 to 92. **Every one of the 15 upgrades names those nine keys and sends those functions: 135 of 135**, the eight of device 0 and sub device 0 and the two of device 5 included (their sub device and device are the parameters', and the functions are the same).

### D198 — What is skipped

Of 584 functions 49 are skipped, all blank (no OBC byte).

### D199 — What it does to the rest, what is not read, and what is not proven

The corpus is **16,440 remote files and 657,222 keys**, 5,451 brands, 281,739 models (23 more) and 176,113 signals (122 more). Both bundle profiles are 57,741,312 bytes and `rl bundle --verify` passes on each. **81.5% of the new keys map to the vocabulary** (§22, 436 of 535), so the `jp1` row goes from 71.4% to 71.5%. No new build warnings (157 as before), no file renamed, no upgrade lost to an existing model. The search evaluation's draw moved again (D99): first answer **88.4%**, first five 97.8%; none of its seven misses is a device of this import.

**Not read**: **501 upgrades of 152 executor names**, counted in `IMPORT.md`: `Manual Settings` (75 and 31 more of its spellings), `XMP` and `XMP (Slingbox)` (48), `RC-5/5x Combo` (16), `RC5/6 combo` (12), and 147 executors of fewer than 12 each; and the 2,073 KeymapMaster files. **Not proven.** That a function does what its name says (§28); that a receiver acts on the frame with the toggle at 0 and with a 100 ms gap where the remote's period is 150 ms (§47); that the 76 keys of device 5 are what the Amstrad DRX780 and Sky HD send (they rest on the parameters, which agree with the `FixedData`, and on the same function mapping the other 13 upgrades have).

## 49. More JP1 upgrades: Pioneer 3DEV and 4DEV

Two Pioneer executors that D155 said "need the executors' code read": **`Pioneer 3DEV`** (6 upgrades, receivers and a CD player) and **`Pioneer 4DEV`** (8, receivers and a plasma TV's catalogue). The code that matters is in RemoteMaster's `Pioneer3DevXlator` and `Pioneer4DevXlator` (the Java source at the pinned fork, `com/hifiremote/jp1/translate/`), not in `protocols.ini`. **All 14 are read and become files, 967 keys, all Plausible**; the JP1 source is now **2,825 files and 111,967 keys**. The reader's new piece is `pioneer_multi_signal` in `src/remote_ledger/jp1/rmdu.py`; the protocol is the ledger's `Pioneer-2Part`, which carries a signal of two parts as `D0:D` and `F0:F`.

### D200 — The rules

The devices are the parameters, one byte each (3DEV has three, 4DEV four), or where `null` the byte `FixedData` keeps **reversed, not complemented** (`Translator(lsb,...)`: `25` is 164; the plain `Pioneer` executor's translators complement as well). **3DEV:** the first byte is the OBC reversed; the top three bits of the second pick the device (4 is Device 1, 2 Device 2, anything else Device 3) and its low five, reversed, are the second part's OBC 2, to which `adjust` adds `0x80` if the OBC has bit 7 and `0x40` if it lacks bit 6; 0 says no second part. **4DEV:** each byte is an OBC reversed with the device bit (`0x04` of the byte) set or clear; the device is `2 * (first's bit) + (second's bit)` of the four and the two OBCs are the bytes reversed with that bit cleared. **Both parts are sent with the chosen device and the function is the OBC as it is**, a one-part signal being the two parts equal.

I did not read the executors' assembly, so what the Java does not say (which device a part uses, whether `F` is the OBC or its complement) was found by trying the readings against the ledger's own codes, and the readings tried are in D201.

### D201 — The rules checked against another source

The IR Blaster import holds 1,673 distinct `Pioneer-2Part` codes in 132 files. **The six 3DEV upgrades send 267 of their 292 keys among them (91.4%); the eight 4DEV upgrades 507 of 675 (75.1%)**, and five of the eight (`Elite VSX-40`, `SX-315`, `VSX-C100`, `VSX-521`, `VSX-818V`) 270 of 280 (96.4%); the other three (`VSX-D901S`, and the two catalogues of many devices, `Pioneer Master List` and `PD-TM2`) 237 of 395, because the database lacks the codes of those devices. **The readings that failed** show that the hits are the rule's: the OBCs complemented send **0 of 292 and 0 of 675**, bit-reversed 66 and 105; for 3DEV the second part's OBC 2 without `adjust` sends 42 of the 52 two-part functions against 48 with it (92.3%); and for 4DEV a different device for the two parts (the first device for one of them, or one device for each flag bit) or the OBCs with bit 5 kept send 137 of 675 (20.3%). A test holds the figures over the committed files.

### D202 — What is skipped and not read

23 functions are skipped, all blank. **`Pioneer DVD` (4 upgrades, 153 functions) is not read**: its translator, `Translator(lsb,comp,3) Translator(comp,0,1,5) Translator(comp,1,1,5) Translator(comp,2,1,5)`, puts three flag bits (a prefix device, a prefix command and which of two devices) over the low bits of the OBC byte and has a prefix command of its own, which I did not work out.

### D203 — What it does to the rest, and what is not proven

The corpus is **16,454 remote files and 658,189 keys**, 5,451 brands, 281,763 models (24 more) and 176,227 signals (114 more). Both bundle profiles are 57,819,136 bytes and `rl bundle --verify` passes on each. **35.4% of the new keys map to the vocabulary** (§22, 342 of 967): a receiver's commands are mostly not in the 156 keys, so the `jp1` row goes from 71.5% to 71.1%. No new build warnings (157 as before), no file renamed. The search evaluation's draw moved again (D99): first answer **88.8%**, first five 97.5%; none of its eight misses is a device of this import.

**Not read**: **487 upgrades of 150 executor names**, counted in `IMPORT.md`: `Manual Settings` (75 and 31 more of its spellings), `XMP` and `XMP (Slingbox)` (48), `RC-5/5x Combo` (16), `RC5/6 combo` (12), `Pioneer DVD` (4), and 145 executors of fewer than 12 each; and the 2,073 KeymapMaster files. **Not proven.** That a function does what its name says (§28); that Pioneer's second part is sent with the first's device, which the readings tried support and no Pioneer capture proves; that the 25 and 168 keys that the database lacks are what the receivers send (they rest on the same rule as the hits, and the catalogues' misses are the likeliest to be a fault).

## 50. The matcher: an exact model that repeats its brand no longer hides the same device listed without it

D168 (§40) stopped a near match of a long run from hiding the brand's own exact model inside it. The same fault was left in the exact case, and a regression floor of the search evaluation found it: after a catalog change (§51's import, which re-draws D99's table) the `punctuation` class of 40 queries had three misses where its floor is two, and one of them was **`YAMAHA YSP5100`**, whose answer was the model `Yamaha YSP-5100` and not the brand's `YSP-5100`, which the IR Blaster source holds for the same device. Rule 4 says a run inside a longer run that gave a candidate by the exact rule is not tried, and the whole text `yamahaysp5100` is exactly the key of the JP1 source's `Yamaha YSP-5100`, a model whose name repeats its brand.

### D204 — The rule, and what it was costing

**An exact match of a model whose key is a named brand's key and more** (`yamaha ysp-5100` of Yamaha) **now covers like an edit, not like an exact one**: a run inside it is tried only among the named brands' models and only an exact match of it counts, as D168 has it for an edit cover. The longer key answers first (rule 7) and the bare model second. Another brand's `7800` stays out, a near match of the part stays out, and with no brand named there is no brand to look inside, as before. `matching.py`'s `_repeats_brand` is the test and its docstring says so (rule 4).

The cost was large, because the catalog is full of these. The JP1 source's models mostly begin with their brand (D150: 2,061 of 2,351 of the models only it had), and where another source lists the same device by its bare name the catalog holds **both**: **4,991 models repeat their brand and 2,596 of them have the bare model beside them** (`ABOX A3` and `A3`, `4KUS DVR-320` and `DVR-320`). **A seeded sample of 400 of the pairs, typed as a person would, brand and bare model (`ABOX A3`), found both entries in 67 (16.8%) of the queries before the rule and in 363 (90.8%) after; the repeating model alone 321 and 24; the bare one alone 8 and 9; neither 4 and 4.** What stays hidden is a bare model of fewer than four characters or with no digit (`191`, `TV`, `HD`, `NowTV`), which rule 4 never tries as a run of the text outside a named brand's models, and is by that rule's design.

### D205 — What it moves, and what is not proven

**The 320 queries of D99 give the same table on the same catalog** (284 first answers of 320, 312 in the first five): none of their answers changes. The matching vectors gain **two queries and three catalog entries** (a repeating model, the bare model beside it and another brand's part, under brands the vector catalog already had, so that no brand id of the suggest vectors moves) and lose nothing but the wording of one note; the suggest vectors gain the same three entries (25 lines). **A port of the matcher must add the rule**; nothing else of the vectors moves. A test with a small catalog holds the rule and its limits (`tests/test_matching.py`). **Not proven**: the effect on real typing, which D98 says has not been measured; and aliases of a brand, which the rule does not count as its key (a model that begins with an alias of its brand is still an exact cover).

## 51. More JP1 upgrades: RC5/6 combo

**`RC5/6 combo`** (12 upgrades, all of its second variant): Philips and Krell audio and video gear, whose every function is a code of RC-5 or of RC-6, both registered protocols. **All 12 are read: 20 files, 501 keys, all Plausible** (228 RC-5 and 273 RC-6; eight upgrades send both and become two files each, with the `-rc5` and `-rc6` suffix the importer gives an upgrade that sends two protocols); the JP1 source is now **2,845 files and 112,468 keys**. The reader's new piece is `rc56_combo_signal` in `src/remote_ledger/jp1/rmdu.py`. Two of the upgrades are the same file twice under two names (`PhilipsFR965HomeTheater` and `Philips_FR965_Home_Theater`), and the importer, which keys files by name, keeps both.

### D206 — The rule

The function is two bytes and `CmdTranslator` is `Translator(0,1,15) Translator(1,5,3,comp) Translator(1,1,0,6) Translator(1,1,2,5) Translator(2,6,8,comp) Translator(2,1,1,6) Translator(3,2,13) Translator(4,8,0)`, so **the second byte's low bit is the type** (0 is RC-5). **RC-5:** the device is the low five bits of the first byte complemented, the command the six bits of the second byte above its low two complemented, and its seventh bit the first byte's `0x40` as it is, so `5F BC` is device 0 and function 80 (a device above 31, the two bits above, and a set bit 1 of the second byte are not read). **RC-6:** the first byte is the OBC as it is, and bits 1 and 2 of the second pick one of the upgrade's three RC-6 devices (3 is none): the parameters, or where `null` the eight bits `FixedData` keeps doubled as the bit and its complement from the third byte on (`BitDoubler`). The ledger's `RC5` (seven-bit command) and `RC6` (mode 0).

### D207 — The rule checked against the ledger's other sources

The ledger's other sources (IR Blaster, LIRC, SmartIR, hifi-remote and the manufacturers' tables) hold 2,428 distinct RC-5 codes and 1,237 RC-6. **The twelve upgrades send 219 of their 228 RC-5 keys and 257 of their 273 RC-6 keys among them: 476 of 501 (95.0%).** The measurement is a test over the committed files. **The seventh command bit needs more than the hit rate**: 34 RC-5 functions have the first byte's `0x40` (a function above 63) and 26 of them are among the other sources' codes, but without the bit 227 of the 228 are too, because the ledger has both a Philips code and the one 64 above it. **What tells them apart is the names**: the Philips 47PFL7422D upgrade's Up, Down, Menu, Left, Right and OK send RC-5 device 0 and functions 80, 81, 82, 85, 86 and 87, which the other sources label KEY_UP, KEY_DOWN, KEY_MENU, KEY_LEFT, KEY_RIGHT and KEY_OK more often than anything else (58, 59, 123, 53, 54 and 48 times); read as 16 and 17 the first two would be the volume keys (326 and 354 times). The first reading I tried (the seventh bit inverted) sent 61.6%.

### D208 — What is skipped

35 functions: 33 blank, one whose device is above 31 and one with a bit RC-5 does not use set.

### D209 — What it does to the rest, what is not read, and what is not proven

The corpus is **16,474 remote files and 658,690 keys**, 5,451 brands, 281,795 models (32 more) and 176,245 signals (18 more). Both bundle profiles are 57,835,520 bytes and `rl bundle --verify` passes on each. **69.9% of the new keys map to the vocabulary** (§22, 350 of 501), and the `jp1` row stays at 71.1%. No new build warnings (157 as before). The search evaluation's draw moved again (D99): first answer **88.1%**, first five 97.8%; none of its seven misses is a device of this import.

**Not read**: **475 upgrades of 149 executor names**, counted in `IMPORT.md`: `Manual Settings` (75 and 31 more of its spellings), `XMP` and `XMP (Slingbox)` (48), `RC-5/5x Combo` (16), `Pioneer DVD` (4), and 144 executors of fewer than 12 each; and the 2,073 KeymapMaster files. **Not proven.** That a function does what its name says (§28); that the seventh bit is the first byte's `0x40` for the 28 functions that have it besides the Up, Down, Menu, Left, Right and OK the names confirm (they rest on the translator); that a receiver acts on an RC-5 or RC-6 frame sent with the toggle at 0 (§16, D3b).

## 52. Registering XMP-1

The 38 kHz nibble code of Comcast's, Cisco's, Pace's, Dreambox's and many other set-top boxes' remotes, which the JP1 executors **`XMP`**, **`XMP (JP1)`**, **`XMP (Slingbox)`** and **`XMP (UEI)`** send (57 upgrades, the largest group left unread). IrpTransmogrifier has it with hardware captures in its own test data, and the LIRC project's remotes carry the frame. It is registered through D18's three gates and **nothing reads it yet**: the corpus, the index and the bundle do not change, and the registry goes from 38 to 39 protocols. The code is `src/remote_ledger/protocols/xmp.py`, the evidence `tests/vectors/CITATIONS.md` and `tools/misc_capture_audit.py`.

### D210 — What a form carries, and what it does not

Every symbol is a nibble: a 210 µs mark and a space of 760 µs plus 136 µs times its value (sixteen spaces, the sixth the database's own 1,449 µs). A transmission is two subframes of eight nibbles (S's high nibble, a checksum `C1`, S's low nibble, 15, OEM, D; then S's high nibble, `C2`, the toggle nibble, S's low nibble, F and two zero nibbles), each followed by a mark and a gap of 13.8 and 80.4 ms. **`device` is D, `subdevice` is S (required) and `function` is F**, eight bits, which the IRP puts in the high byte of the 16-bit command (`F*256`, XMP-1's own: the database's `XMP-2` has F in the low byte). The **intro** has the toggle nibble 0 and the **repeat** 8. Every duration is in microseconds, so a file's `unitUs` has nothing to scale and is refused; the gaps are gaps and `extent_us` is `None` (D3).

**Two things a form does not carry.** The **OEM** is the IRP's own 68: an upgrade that sets another cannot be written. And the **final frame** some remotes send on release (the database's `XMPff-1`, a third transmission with the toggle nibble 9) has no place in an `IrSignal`, whose `ending` is reserved (D1): it is not written, and an upgrade that asks for it is not read by the JP1 import.

### D211 — The three gates

**Gate 1**: the IRP verbatim at the commit the others cite and in the 1.2.14 release. **Gate 2a, on hardware**: `tools/misc_capture_audit.py` reads the capture of a set-top box's remote with a nibble decoder written from the layout (both checksums verified, the fixed nibble 15, the OEM 68, the toggle nibble): **42 of 42 keys** decode to the `.exp` fields (D=84, S=0 and F), **and the second transmission of each, the repeat, reads the same fields with the toggle nibble 8: 42 of 42**. The `.exp` names the decode `XMPff-1`, but the captures hold no third transmission: what they show is `XMP-1`'s intro and repeat. The capture's spaces run 2 to 3 percent long, which put a nibble at the boundary of two and made a first decoder fail three keys; the decoder now takes the capture's own scale off with the fixed nibble 15. The LIRC project's remotes of ten boxes carry the frame (**166 sequences, 72 distinct codes**, all OEM 68, decoded by `tests/test_xmp.py`). **Gate 2b**: three vectors rendered from the 1.2.14 release, whose parameters are keys of the capture; our timings under its rounding reproduce each exactly and the words differ only at the gaps. **Reproducible, none published** (33 now, from 32). **Gate 3**: `tests/test_xmp.py`, with the checksums written from the IRP's formulas and not taken from the encoder.

### D212 — What it does to the rest, and what is not proven

Nothing in the corpus moves. What the registration opens is in TODO.md: the XMP upgrades. **Not proven**: that a receiver acts on a transmission without the final frame, for the remotes that send one; an OEM other than 68; and `XMP-2`, `XMPff-1` and the rest of the family, which IrpTransmogrifier also has and the ledger does not register.

## 53. More JP1 upgrades: XMP

The executors whose protocol §52 registered: **`XMP`**, **`XMP (JP1)`**, **`XMP (Slingbox)`**, **`XMP (Slingbox)(No repeat)`** and **`XMP (UEI)`**, the set-top boxes of Comcast, Cisco, Pace, Dreambox, Thomson, Arris, Dish, DStv and others (62 upgrades, the largest group D145 had listed as not read). **46 are read and become files, 1,703 keys, all Plausible**; the JP1 source is now **2,891 files and 114,171 keys**. The reader's new piece is `xmp_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D213 — The rule

`Device, Subdevice, OEM Code, Final Frame Toggle` are parameters 0 to 3 and, in the variants of five parameters (`JP1` and `JP1Slingbox`), `XMP Type` the fifth; variant 2 and `XMP (UEI)` have four and a function of two OBC bytes, the second the low byte of XMP-2's 16-bit command. **The function is the OBC as it is.** A `null` parameter is the value `FixedData` keeps: the subdevice as its high nibble at the top of byte 0 and its low nibble at the top of byte 1, the OEM byte 2, the device byte 3, then the final frame the top bit of byte 6 and the type the complement of its next bit (variant 2: the final frame the complement of bit 3 of byte 1). `17 0F 44 3E 1F 00 40` is device 62, sub device 16, OEM 68, no final frame, XMP-1, the protocol's defaults, and **all 62 upgrades read the same, function by function and refusal by refusal, from their `FixedData` alone as from their parameters**, which checks that layout (the upgrades with `null` parameters need it). The ledger's `XMP-1` is the device, the sub device and one function byte, so **three things are refused**: an **OEM other than 68** (none is), a **final frame** (the third transmission on release, which an `IrSignal` has no place for, D210), and **type 1, XMP-2** (none is); and a second OBC byte that is not 0 (four functions). `XMP (Slingbox)(No repeat)` is read as the others, as `NEC1 (No Repeats)` is: whether a key repeats is the app's, not the file's.

### D214 — The rule checked against another project's data

The ledger held no XMP signal, so there is no hit rate. **The LIRC project's remotes of ten boxes** (166 sequences, decoded by `tests/test_xmp.py`'s layout decoder; confs expanded by lircd) hold 24 codes of device 62 and sub device 16, 11 of 49 and 16, 13 of 42 and 17, 3 of 71 and 17, and 21 of 72 and 18, which no upgrade has. **The upgrades send 24 of 24, 11 of 11, 12 of 13 and 3 of 3: 50 of 51 codes**, and they name them as LIRC does: the ten digits, the volume, channel, mute, power, cursor, OK, info, last and language keys of device 62 are the same functions under the upgrades' own spellings (24 of 24). That covers the device and sub devices of 754 of the 1,703 keys; **the 949 others (a Dreambox's 265, the DStv and ADB and Huawei boxes') have no LIRC remote and rest on the same rule and on their own parameters.**

### D215 — What is skipped

Of 2,389 functions of the 62 upgrades **623 are the final-frame upgrades'** (16 of the 62, 26%: the `Final Frame Toggle` is on in nine of `XMP`'s, five of `XMP (Slingbox)`'s and two of the no-repeat's), 59 are blank and 4 have a second OBC byte. **The final-frame ones are the weakest decision of the import**: their first two transmissions are XMP-1's, and a receiver may well act on them without the third, but I do not know that and the captures in §52 do not say, so they are listed in `IMPORT.md` and not written. If the owner accepts that, they are one change of one line and 623 keys. **Superseded: the owner chose to extend the format (§54, §55) and §56 reads them.**

### D216 — What it does to the rest, what is not read, and what is not proven

The corpus is **16,520 remote files and 660,393 keys**, 5,459 brands (8 more), 281,871 models (76 more) and 177,032 signals (787 more). Both bundle profiles are 58,015,744 bytes and `rl bundle --verify` passes on each. **78.0% of the new keys map to the vocabulary** (§22, 1,329 of 1,703): a set-top box's remote is the vocabulary's, and the `jp1` row goes from 71.1% to 71.2%. No new build warnings (157 as before), no file renamed. The search evaluation's draw moved again (D99): first answer **92.2%**, first five 97.5%; none of its eight misses is a device of this import.

**Not read**: **413 upgrades of 144 executor names**, counted in `IMPORT.md`: `Manual Settings` (75 and 31 more of its spellings), `RC-5/5x Combo` (16), `Pioneer DVD` (4), the 16 final-frame XMP upgrades (since read: §56), and 141 executors of fewer than 12 each; and the 2,073 KeymapMaster files. **Not proven.** That a function does what its name says (§28); that a receiver acts on a transmission without a final frame (§52); that the 949 keys without a LIRC remote are what their boxes send.

## 54. A signal's ending

Some remotes send one more frame when a key is **released**: XMP's final frame (the toggle nibble 9; the JP1 upgrades with its `Final Frame Toggle` on, 16 of the 62 XMP upgrades, which §53 could not read for want of a place for it) and Blaupunkt's closing sync (D66, dropped until now). `IrSignal.ending`, a third sequence, was the reserved place for it (D1) and Pronto Hex, which holds an intro and a repeat, could not carry it, so D1 refused it. This is the change that lets it, ahead of the first protocol that uses it (§55).

### D217 — What an ending is and where it goes

**An ending is a sequence of mark and space durations, as the others, that a player sends once when the key is released**, after the repeat passes of a held key (or, for a tap, after the press). `IrSignal.ending` accepts it now and checks its shape as it checks the others'; `IrSignal.sequences`, the two Pronto holds, is what it was. It is carried in three places and **no file on disk changes** (§9's routing rule: a remote file's `irp` form names a protocol and a code, and the protocol says whether its remotes send an ending; `raw` and `pronto` forms have none):

- **The compile stage** writes `endingHex` beside `prontoHex` for a candidate that has an ending, and only then: **a Pronto string of its own**, the intro of a signal with no repeat at the same frequency word (`0000 006D 0002 0000 …`), which every tool that reads Pronto reads (`pronto.encode_ending`, `Remote.compile_pair`; `rl encode` prints it as an `ending:` line). The signal's own `prontoHex` is what it was. The site's data and the redundancy check (D16: two candidates that differ only in their ending ship different artifacts) read it too.
- **The bundle's blob** (D91) has it after the Pronto words: the number of its burst pairs, then its durations in carrier cycles, at the frequency word of the blob. `bundle/signals.py` splits it (`blob_to_pronto` is the Pronto words, `blob_ending_pronto` the ending's string, `has_ending`), `decode_blob` gives an `IrSignal` with its ending, `rl bundle --verify` decodes and encodes it back and compares the compile stage's `endingHex` with it, and a vector of `bundle_vectors.json` has an `endingUs` where it has one. **The `signals` table and the schema version (1) do not change**; a blob with an ending is a different signal from one without (they are stored by their words) and **a reader that checks a blob's length against its header, as the Talismote backend and the ledger's own did, rejects it**: no bundle has one until §55, and the readers that exist are the owner's, changed with it.
- **A cross-check does not compare it.** D8's comparison reads the intro and the repeat; a `raw` capture of a remote that sends a final frame is compared on those, and its ending has nothing to be compared with.

### D218 — What it does to the rest, and what is not proven

Nothing in the corpus moves: no protocol produces an ending yet, so every `prontoHex`, every blob and the bundles are what they were (`rl build --check` reports 0 differences and `rl bundle --verify` passes on a bundle with the same `dataVersion`). **Not proven**: what a receiver does with an ending, which depends on the protocol and is for its registration (§55); and that a player sends it at the right moment, which is the player's (Talismote's `HoldPlan` and its release, in the same change).

## 55. Registering XMPff-1

**`XMPff-1`**: `XMP-1` (§52) with the final frame that some remotes send when a key is released, the first protocol whose signal has an ending (§54, D217). It is what the 16 XMP upgrades whose `Final Frame Toggle` is on send (§53 left them unread). IrpTransmogrifier has it, and a hardware capture of held keys of a remote that sends it is in its own test data. It is registered through D18's three gates and **nothing reads it yet**: the corpus, the index and the bundle do not change, and the registry goes from 39 to 40 protocols. The code is `src/remote_ledger/protocols/xmp.py` (the encoder is `XMP-1`'s with the ending), the evidence `tests/vectors/CITATIONS.md` and `tools/misc_capture_audit.py`.

### D219 — What a form carries

The same as `XMP-1`: `device` is D, `subdevice` is S (required) and `function` is F, the OEM the IRP's 68. **The signal is `XMP-1`'s intro (toggle nibble 0) and repeat (8), and its ending is the same frame with the toggle nibble 9 and a 13.8 ms last gap**, sent once when the key is released (the IRP's `[T=9]` and `[-13.8m]`). The compile stage writes it as `endingHex`, the bundle as the words after the Pronto words (D217).

### D220 — The three gates, and a thing the IRP does not say

**Gate 1**: the IRP verbatim at the commit the others cite and in the 1.2.14 release. **Gate 2a, on hardware**: `tools/misc_capture_audit.py` reads `XMP_1923_FinalFrame.ict`, thirteen captures of held keys of a remote (device 26, sub device 48), with the nibble decoder: **13 of 13 keys** read, every transmission of a key the same fields, the toggle nibbles 0, then 8s, then **9 at the end of 11** (two captures end before the release), the digits the functions they are named for and the cursor keys 33 to 37 as the `XMP-1` capture of another remote has them; and **the registered ending reads as each of the eleven captured final frames** (durations 0.94 to 1.11 times, median 1.00). **Gate 2b**: three vectors rendered from the 1.2.14 release: the Pronto string is the intro and the repeat (`XMP-1`'s words), and **the ending, which Pronto Hex cannot hold and the tool drops with a warning, is its raw rendering and ours must equal it exactly**, with no rounding rule in between (a new field of the gate, `gate2b_ending_raw`, which a protocol with an ending must cite). **Reproducible, none published** (34 now, from 33). **Gate 3**: `tests/test_xmp.py` and `tests/test_ending.py`, the latter taking a remote file of this protocol through the compile stage, a bundle, `rl bundle --verify` and the vectors.

**The capture shows what the IRP does not say: in every one of the eleven the transmission before the final frame has its last gap cut short, to 15-22 ms where the others have 80 ms**, so the final frame follows it at once. The ledger's signal keeps the 80.4 ms of the repeat, and a player that sends the ending after its pass sends it 80 ms after the last frame, not 18. The IRP's `[-13.8m]` is the final frame's own gap; the capture ends at the release and says nothing of what follows it.

### D221 — What it does to the rest, and what is not proven

Nothing in the corpus moves. What the registration opens is the JP1 reading of the 16 upgrades (§56). **Not proven**: what a receiver does with the final frame, and with it arriving 80 ms after the last frame and not 18 (the capture is of a remote and says nothing of a box); and `XMPff`, `XMPff-2` and `XMP-2`, which IrpTransmogrifier also has and the ledger does not register.

## 56. More JP1 upgrades: XMP with the final frame

The 16 XMP upgrades whose `Final Frame Toggle` is on, which §53 left unread for want of a place for the frame sent at release, are read now as **`XMPff-1`** (§55), the protocol whose signal has an ending (§54). All 62 XMP upgrades are read: **16 files and 623 keys are added, all Plausible, and the JP1 source is 2,907 files and 114,794 keys.** The change is one branch of `xmp_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D222 — The rule

D213's, unchanged but for one refusal: **a function of an upgrade with the final frame on is `XMPff-1` with the device, the sub device and the function as they are; one of an upgrade without it is `XMP-1`.** Nine of the 16 are `XMP`'s, five `XMP (Slingbox)`'s and two `XMP (Slingbox)(No repeat)`'s. The other refusals stand: an OEM other than 68 (none is), type 1, XMP-2 (none is), a second OBC byte that is not 0 (four functions) and a function with no OBC byte (59). The final frame is read from the `Final Frame Toggle` parameter, or from `FixedData` where it is `null` (the top bit of byte 6; variant 2, the complement of bit 3 of byte 1), and **all 62 upgrades still read the same from `FixedData` alone as from their parameters**, the 16 included.

### D223 — The rule checked against other data

Two sources, neither of them the upgrades. **The hardware capture** that registered the protocol (§55, `XMP_1923_FinalFrame`, held keys of a remote at device 26 and sub device 48): the Dreambox DM7020 upgrade is the one at that device and sub device, it names 13 keys the capture has (the digits 0 to 7 and the cursor and OK keys), and **it sends exactly their functions, 13 of 13** (0 to 7 and 33 to 37). **The LIRC remotes** (D214) hold 24 codes of device 62 and sub device 16 and 3 of 71 and 17, of which **the final-frame upgrades send 21 and 3**; the three that are not in them are in the other upgrades of the box (D214's 24 of 24). Neither tells that **the final frame is what these upgrades' boxes want**: the capture is of a remote that sends it, and LIRC records one transmission of a key. The `Final Frame Toggle` is the upgrade's own statement, as its device and sub device are.

### D224 — What it does to the rest

The corpus is **16,536 remote files and 661,016 keys**, 5,461 brands (2 more), 281,895 models (24 more) and 177,388 signals (356 more: the 623 keys are 356 distinct codes, and a signal with an ending is stored apart from any without one, D217, so none of them shares a blob with an `XMP-1` key). Both bundle profiles are 58,122,240 bytes (106,496 more), `rl bundle --verify` passes on each, and **four vectors of `bundle_vectors.json` have an `endingUs`**, the first of the vectors to. **85.7% of the new keys map to the vocabulary** (§22, 534 of 623): the `jp1` row goes from 71.2% to 71.3%. No new build warnings (157 as before). **One file's model is renamed**: two upgrades of the Huawei DN370T have the same model apart from its case (`DN370T` and `DN370t`), the importer gives the second of two such files, in file order, its file's name in brackets (`src/remote_ledger/jp1/importer.py`), and the new file (`Huawei DN370T TalkTalk-YouView`) sorts before the earlier one, so the earlier file's model is now `DN370T (Huawei DN370T)` and the new one's `DN370t`. No path changes, and a ref is the path. The search evaluation's draw moved again (D99): first answer **90.0%**, first five 97.2%; none of its nine misses is a device of these files.

### D225 — What is not proven, and what is not read

**Not proven.** That a receiver acts on the final frame, or on it 80 ms after the last frame of a held key rather than 18 (§55's capture); that the 623 functions do what their names say (§28); and that a player sends the ending at the right moment, which is Talismote's (§54). **Not read** is what it was without the 16: 413 upgrades of 144 executor names (`Manual Settings`, `RC-5/5x Combo` 16, `Pioneer DVD` 4 and 141 executors of fewer than 12 each) and the 2,073 KeymapMaster files. Blaupunkt's closing sync (D66) could use an ending now and does not yet.

## 57. Registering RC5x

**`RC5x`**: Philips' twenty-bit extension of RC-5, the same biphase code at 36 kHz and 889 us with a six-bit data field after four units of silence. Marantz's receivers and amplifiers take it with the data field as the **extension** of their charts (D124), and D126 left 1,034 of their rows unread for want of this protocol. It is registered through D18's three gates and **nothing reads it yet**: the corpus, the index and the bundle do not change, and the registry goes from 40 to 41 protocols. The code is `src/remote_ledger/protocols/rc5x.py`, the evidence `tests/vectors/CITATIONS.md`, `tests/test_rc5x.py` and `tools/misc_capture_audit.py`.

### D226 — What a form carries

`device` is D, the system (0 to 31); **`subdevice` is S, the seven-bit command, and is required**; `function` is F, the six-bit data (0 to 63), which Marantz's charts call the extension. This is IrpTransmogrifier's own naming (its documentation: "D is called System, S is called Command, and F is called Data") and the one `XMP-1` took for its S. The signal is one frame and has no intro: S1 (a 1), `~S:1:6` (the complement of bit 6 of S, as RC-5's second start bit), T, D:5, four units of silence, S:6, F:6; a frame opens on a lone mark (S1's idle half-bit is dropped, as in `RC5`) and its extent, 114 ms, is measured from that mark. **The toggle is not in a remote file** (D3b): everything compiled has T=0. `RC5` is not `RC5x` and the two do not decode each other; `RC5`'s docstring said `RC5x` stayed unregistered and now says where it is.

### D227 — The three gates

**Gate 1**: the IRP verbatim at the commit the others cite and in the 1.2.14 release. **Gate 2a, on hardware**: `tools/misc_capture_audit.py` reads IrpTransmogrifier's `RC5x.ict`, nine captures of a remote of system 16 and one of system 22, with a biphase decoder written from the layout: **9 of 9 keys** read as the `.exp` file's D, S, F and T and re-encode to the same bit pattern, capture durations 0.97 to 1.00 times the encoder's (median 0.99), the frame period 0.997 to 0.998. **Gate 2a, a second source, the maker's**: of Marantz's 395 distinct chart rows with an extension and the maker's own Pronto hex, **367 read as exactly the chart's system, command and extension** through the same layout; the other 28 are the chart disagreeing with itself (19 hex strings that read as *another row's* fields, 7 off by one in the command or the system, 2 not a frame of this shape). **Gate 2b**: three vectors rendered from the 1.2.14 release, ours equal in every word but the lead-out, by three cycles (the release rounds the extent another way, as it does for `RC5`); reproducible, none published (35 now, from 34). **Gate 3**: `tests/test_rc5x.py`.

### D228 — What it opens, and what is not proven

It opens two readings, each its own pull request: **Marantz's 1,034 rows with an extension and no hex** (D126), and the **16 JP1 `RC-5/5x Combo` upgrades** (D145). The Marantz check also says how far to trust the first: about one chart row in fourteen of those that can be checked disagrees with its own hex, so the fields of a row with no hex are the maker's and carry that risk. **Not proven**: that a receiver acts on a frame the encoder makes (the capture is of a remote, no frame was sent to a device); and **what an app does with the toggle**: Talismote's `ToggleFrame` recognises RC-5 and RC-6 frames only, so an RC5x key would be sent with the same T every time, which a receiver that tells presses apart by T may take as one held key. Marantz's charts say nothing of that.

## 58. Reading Marantz's RC-5x rows

The chart rows that D126 left unread, **the commands with an extension and no Pronto hex**, are read now through `RC5x` (§57): **1,013 of the 1,034 rows become keys, in 75 new files and 5,589 keys, all Plausible**; the manufacturers' tables are **264 files and 18,433 keys** (Marantz 237 and 16,481). The change is in `src/remote_ledger/official/marantz.py`; the pinned documents, the CSV and the importer are §29's.

### D229 — The rule

A row whose extension is a number and whose hex is empty is an `irp` form of `RC5x`: **`device` the row's system, `subdevice` its command, `function` its extension** (the chart writes all three in decimal), the reading D227 checked against Marantz's own hex. **A file names one protocol, so the RC5x keys are a file of their own, `<model> [RC-5x]`**, and it carries the model's bare name only when the model has nothing else. The order that gives the bare name to the largest file now puts the RC5x one last whatever its size: **no file that was there before changes its name, its keys or its bytes** (`git diff` of `remotes/` is `IMPORT.md` and 75 new files). The rest is as D124: the hex of a row that has one stays a `pronto` form, whatever its extension.

### D230 — What is not read, and what it rests on

**Not read: 21 of the 1,034 rows.** 18 have an extension that is a **range** (`00-63`, `01-60`, `00-05`: the volume, preset and channel commands, one row for a command per value, 3, 13 and 2 rows by workbook), which is a family of keys and not a key; 3 have no system or command number. The five direct-access sheets D126 named (`Direct Vol.`, `Zone Speaker Direct Vol.`, `AM FM Preset Direct`, `XM SIRIUS Preset Direct`, `XM SIRIUS Ch. Direct`) are other sheets of the workbooks and are not converted yet.

**What the 5,589 keys rest on is the chart's own fields and the layout, and nothing else.** No other source holds these frames: of every signal of the corpus, **4 read as an RC5x frame outside Marantz's tables (the LIRC project's) and none has the fields of a new key**, and 18 of the 695 distinct triples of the new keys are also the triple of a chart row that has its hex. What D227 measured is the chart's own consistency: **28 of the 395 rows that carry their hex (7.1%) disagree with it**, 19 of them because the hex belongs to another row, so **about the same share of the new keys, some 400 of the 5,589, may name a neighbouring command**. The tier is Plausible, as every key of a maker's table here.

### D231 — What it does to the rest, and what is not proven

The corpus is **16,611 remote files and 666,605 keys**, 5,461 brands, 281,970 models (75 more: the `[RC-5x]` files are models, as the `[36.7 kHz]` ones are) and 178,083 signals (695 more). Both bundle profiles are 58,396,672 bytes (274,432 more), dataVersion `bb293b7f4fe7`, and `rl bundle --verify` passes on each. **436 of the 5,589 new keys map to the vocabulary (7.8%, §22)**: a receiver's direct commands are not generic keys (D84), and the `official` row goes from 21.6% to 17.4%, the whole corpus from 74.5% to 73.9%. Four vectors of `bundle_vectors.json` are `RC5x`'s. No new build warnings (157 as before). The search evaluation's draw moved again (D99): **first answer 89.1%, first five 97.5%**; none of its eight misses is a device of these files.

**Not proven.** That a receiver acts on any of these frames, and **that a player alternates the toggle**: Talismote's `ToggleFrame` knows RC-5 and RC-6 only (§57). The JP1 `RC-5/5x Combo` upgrades (16) are the other reading `RC5x` opens, and are not read yet.

## 59. More JP1 upgrades: RC-5/5x Combo

The executor **`RC-5/5x Combo`**, the last of the sixteen that D145 named as needing a protocol the ledger did not have, is read now that `RC5x` is registered (§57): **16 upgrades, 26 files and 1,064 keys, all Plausible** (592 RC-5 and 472 RC-5x, in 16 and 10 files); the JP1 source is **2,933 files and 115,858 keys**. Nine of the sixteen are Marantz's (an AV7705, an NR1402, a 2020 receiver, a 5200, an ES-7001, two SR4300 remotes and two projectors), the rest Cambridge Audio's CXN, Arcam's AVR250, NHT's controller, a Media Melody and three Philips TVs; only ten send RC-5x at all, the nine Marantz ones and the Media Melody. The reader's new piece is `rc55x_combo_signal` in `src/remote_ledger/jp1/rmdu.py`; the pinned commit, the index and the importer are §28's.

### D232 — The rule

RemoteMaster's `RC5_5xComboTranslator`, over two OBC bytes (offsets from the most significant bit of the two). **The type is the second byte's low bit** (0 is RC-5). **RC-5** is the `RC5/6 combo`'s (D204): the device the first byte's low five bits complemented, the command the six bits of the second above its low two complemented and the seventh the first byte's `0x40` as it is. **RC-5x**: the sub device is the six bits at offsets 0 to 5 complemented **plus 64 where the slot's flag is set**, the OBC the six at offsets 6 to 11 complemented, the bit at 12 is zero and the two at 13 and 14 pick one of the upgrade's four RC-5x device slots. A slot's device and flag are the setup sheet's parameters or, where the device is `null`, the byte of `FixedData` that slot owns (the device its low five bits complemented, the flag its `0x40`). **The flag of a `null` device is not its parameter**: `Rc5FlagTranslator` gives a slot with no device the opposite of the flag before it, so 13 of the 25 `null` slots have a flag parameter that differs from `FixedData`'s, and the reader takes `FixedData`'s; the 39 slots with a device agree with `FixedData` in both fields, 39 of 39. The ledger's `RC5x`: `device` is D, `subdevice` S (the seven-bit command, the sub device) and `function` F (the six-bit data, the OBC), as §57 names them. Everything else is refused as the others are (an RC-5 device above 31, a bit RC-5 or RC-5x does not use, a slot with no device or flag).

### D233 — The rule checked against other data

**The RC-5x keys against Marantz's own chart (D124).** Nine upgrades are Marantz's, and **314 of their 334 RC-5x keys of system 16 are a (system, command, extension) row of the chart's three workbooks** (94.0%). The assignment is the one the check measures: with the sub device as the command and the OBC as the extension 314 match, **swapped 24, and without the slot's flag (the +64) 259**, so the flag is part of what is confirmed. The 20 that are not listed are an upgrade's own additions or rows the chart has under another number; the chart itself disagrees with its hex in one row of fourteen (D227). **The RC-5 keys against the other sources.** Of the 282 distinct RC-5 codes of the sixteen upgrades **253 (89.7%) are exactly a signal of another source's** (IR Blaster, LIRC, SmartIR, hifi-remote.com, the manufacturers' tables; 268 counting the JP1 source's own other upgrades). The chart lists four other systems and the Marantz upgrades' keys of them are **25 of 42 in the chart (system 17: 18 of 35)**; 339 of 376 in all (90.2%). **The other 96 RC-5x keys (the projectors' system 0, 56 keys, the Media Melody's system 21, 33, and 7 of system 11) are of a system the chart does not have, and rest on the rule alone.**

### D234 — What is skipped

12 functions have no two OBC bytes (the `#` ones, 8 of them the Marantz VP-12S4's); every other function of the sixteen becomes a key. All 16 are read: **`RC-5/5x Combo` leaves the list of executors this import does not read** (143 now, 397 upgrades).

### D235 — What it does to the rest, and what is not proven

The corpus is **16,637 remote files and 667,669 keys**, 5,462 brands (one more, NHT), 282,008 models (38 more) and 178,290 signals (207 more: most of the RC-5 codes were already there). Both bundle profiles are 58,462,208 bytes (65,536 more), dataVersion `0b734e69ffa7`, and `rl bundle --verify` passes on each. **No existing file changes**; no new build warnings. The new keys map to the vocabulary at **a share the `jp1` row shows moving from 71.3% to 71.0%** (§22). The search evaluation's draw moved again (D99): first answer **91.2%**, first five 98.1%; none of its six misses is a device of these files. **Not proven**: that a receiver acts on any of these frames; that the 138 RC-5x keys of the nine upgrades the chart cannot check are what their devices send; and the toggle of RC-5x (§57).

## 60. Marantz's direct-access sheets

The five sheets D126 left aside, **`Direct Vol.`, `Zone Speaker Direct Vol.`, `AM FM Preset Direct`, `XM SIRIUS Preset Direct` and `XM SIRIUS Ch. Direct`**, list the commands that set a volume level, a preset or a channel in one command, in a row for each value. They are read now through `RC5x` (§57): **44 new files and 15,995 keys, all Plausible**; the manufacturers' tables are **308 files and 34,428 keys** (Marantz 281 and 32,476). The sheets are converted to CSV by the same tool as the main chart (`tools/official_sheets_to_csv.py`, 7 more files pinned in the manifest), and the change is in `src/remote_ledger/official/marantz.py`.

### D236 — The rule

A sheet is `Zone`, a value (`-35 dB`, a preset number, a channel), the system, the command and the extension, then a column for each model (a column names a model and its aliases, as the main chart's). **The zone is written down the first column in groups of cells** (`MAIN` over `ZONE`, `MULTI` over `ZONE A` over `(2nd Zone)`) and holds until the next group begins, so a row's name is `<kind> <zone> <value>`: `Direct volume MAIN ZONE -35 dB`, `XM/SIRIUS channel MULTI ZONE A 123`. A row a model's column marks (`X`, `X/S`, `S`, `X(-∞)`; `-`, `---` and a blank are not) is **an `irp` form of `RC5x`**: `device` the system, `subdevice` the command, `function` the extension, as D229. **A model's rows are a file of their own, `<model> [direct]`** (a file names one protocol, and the 965 keys of a receiver's direct commands would bury the keys a person presses in its main files, which do not change). **A model takes each sheet from the newest workbook that lists it in that sheet**, as it takes its main file; a row that two sheets of one model both carried would be one key (none does: the 16 114 volume rows that the preset sheets repeat are marked `-` for every model). **The 2015 workbook has one column, `FY18AVR`**, which I read as every model of that workbook's main chart (four): an inference from its name and from the workbook, and the citation says which column the key is of.

### D237 — What is not read, and what it rests on

**Not read: 3 rows**, the 2015 workbook's `-∞ dB` of each zone, whose extension is `59-63` (five values that all mean the same: a range, and a range is not a key, D230); with the main chart's 18 ranges and 3 rows with no system number, **24 rows of Marantz's charts are not read** (and 3 more whose Pronto hex has a zero duration, D124). The `X(-∞)` of the older workbooks, which marks their own `-∞ dB` row (extension 61 to 63), is read.

**What the 15,995 keys rest on is the chart's own fields, as D230's, and the layout.** One thing outside the chart agrees: **a key of the JP1 forum's Marantz NR1402 upgrade called `Direct VOLUME` sends RC-5x system 16, command 111, extension 0, exactly the first row of `Direct Vol.`** (one of the 1,567 distinct triples; no other JP1 key is one). The chart's own consistency (D227: one row in fourteen of the main chart disagrees with its hex) cannot be measured here, the sheets have no hex. **What a row does** (that extension 35 is -35 dB of this model, that a preset is the preset it is numbered) is the chart's word: the labels are Marantz's, the `Code-2` and `Code-3` zones of `Direct Vol.` are its own names for commands 109 and 110 and I do not know what they are, and the levels (0 to -∞ dB in 64 steps) are the chart's.

### D238 — What it does to the rest, and what is not proven

The corpus is **16,681 remote files and 683,664 keys**, 5,462 brands, 282,052 models (44 more: the `[direct]` files are models, as the `[RC-5x]` ones are) and 179,856 signals (1,566 more: the keys are 1,567 distinct codes, and one of them was there). Both bundle profiles are 59,424,768 bytes (962,560 more), dataVersion `e069fc03fe1d`, and `rl bundle --verify` passes on each. **None of the 15,995 keys maps to the vocabulary (§22, D84)**: a volume level, a preset and a channel are not generic keys, so the `official` row goes from 17.4% to 9.3% mapped and the corpus from 73.9% to 72.1%. No existing file changes, and no new build warnings. The search evaluation's draw moved again (D99): first answer **90.9%**, first five 98.4%; none of its five misses is a device of these files. The size estimate of the selected profile (a flat 21 bytes a key, D95) is 4.6% under the file now, from 3.7%: the direct keys carry a label of 34 bytes each and the test that holds the estimate to a few percent allows 5% (it is informational since D117). **Not proven**: that a receiver acts on any of these frames; what the zone names mean for a given model; and that **the app has a use for 1,433 keys in a remote** (SR8002's, the largest): the layout generator and the remote's screen were written for the keys of D84's vocabulary and show a key it cannot map by its label, which Talismote's own layout work may or may not want to group.

## 61. More JP1 upgrades: Pioneer DVD

The executors **`Pioneer DVD`** (4 upgrades) and **`Pioneer DVD2`** (1), which D202 left unread, are read: **5 upgrades, 5 files and 184 keys, all Plausible** (147 and 37, both as the ledger's `Pioneer-2Part`, D151 and D200 having read the other Pioneer executors into it). The JP1 source is now 2,938 files and 116,042 keys.

### D239 — The rule

RemoteMaster's `protocols.ini` gives `Pioneer DVD` the command `Translator(lsb,comp,3) Translator(comp,0,1,5) Translator(comp,1,1,5) Translator(comp,2,1,5)` over a one-byte hex. **The first translator is the OBC complemented and reversed** (`C2` is 188), as every executor of the family has it. **The three others are one bit, not three: all name offset 5**, so D202's reading of "three flag bits over the low bits" was wrong, and the bit they fix is the one the executor's own notes call bit 5 of the OBC: in the byte, counted from its most significant bit (`0x04`), set is a signal of one part and clear one of two, and in the decimal OBC (the byte complemented and reversed) it is `0x20` and set is two parts. **Two parts** are `Device 1` with the `Prefix Command` and then `Device 2` with the OBC; **one part** is `Device 1` and the OBC. The parameters are `Device 1, Device 2, Prefix Command` in that order and, where `null`, the bytes of `FixedData` at 0, 2 and 1, each complemented and reversed (`3A 66 0A` is 163, 153 and 175). The ledger's `Pioneer-2Part` writes a frame as `D0:D` and `F0:F` and a signal of one part as the two equal, as D151 does. **`Pioneer DVD2`** has the same devices and a two-byte hex whose second byte is `FieldCopier(comp,5,15,1)`, the flag again: the reader holds the file to it (37 of 37 do) and refuses a function whose second byte is anything else.

### D240 — The rule checked against other data

- **The upgrades' own notes.** The DV-C505 upgrade writes the frame of 38 functions in their notes (`163 153 66 001 - 175 188 C2 028` for `Power`, `163 158 86 254` for `Play`): **33 of two parts and 5 of one, and all 38 are what the reader makes**. This is what settles the polarity of the bit.
- **The IR Blaster database**, against the corpus as it stood at the end of §35, before the JP1 source held any `Pioneer-2Part` key, and counting only its other sources: of the IR Blaster's 1,673 distinct `Pioneer-2Part` codes, **138 of the 147 keys of the four `Pioneer DVD` upgrades are there (93.9%), and all 37 of the `Pioneer DVD2` one**. Split by the flag, **all 21 one-part keys and 154 of the 163 two-part**, so both readings are tested. The nine that are not there are functions a database seldom has: `Discrete On` and `Off`, `Disc Skip`, `Power On` and `Off` of a Blu-ray player, a TV's `Sleep`. Every hit is the IR Blaster's: no other source of that corpus holds one.
- **The same device through another executor.** `Pioneer Improved DVD0571 setup` has a twin upgrade, `...-2`, written with `Pioneer MIX` (D151), whose flag byte and prefix commands are a different reading: **51 of the 53 codes of the first are codes of the second**, and 49 of the 50 keys the two both name send the same code. The two the twin lacks are `KEY_REWIND` and `KEY_FAST_FWD`.

### D241 — What is skipped, and the one file that changes

25 functions have no hex (the file leaves them blank): 6 of the `Pioneer DVD` upgrades' 153 and 19 of the `Pioneer DVD2` one's 56. Every other function is a key. Both executors leave the list of executors this import does not read (141 now, 392 upgrades). **One existing file changes**, and only its model name: `Pioneer Improved DVD0571-2 setup` (the `Pioneer MIX` twin, 55 keys) was alone under the model `Improved DVD/0571 setup`, and the importer's rule for two upgrades with one name gives the bare name to the first it reads, and the files go in the order of their names, so it is now the `Pioneer DVD` upgrade (`0571 setup` sorts before `0571-2 setup`); the twin becomes `Improved DVD/0571 setup (Pioneer Improved DVD0571-2 setup)`. No key or code of the twin changes.

### D242 — What it does to the rest, and what is not proven

The corpus is **16,686 remote files and 683,848 keys**, 5,462 brands, 282,060 models (8 more: a name for each of the five upgrades, one of them the twin's new name, and three of them again with the maker in front) and 179,856 signals (none new: every code of the 184 keys was already in the ledger, the IR Blaster's or the JP1 source's own Pioneer MIX, 3DEV and 4DEV upgrades'). Both bundle profiles are 59,428,864 bytes (4,096 more), dataVersion `4eb3dfdc70bb`, `--verify` passes. **152 of the 184 keys map to the vocabulary (82.6%)**; the JP1 row stays at 71.0% and the corpus at 72.1%. **The search evaluation: first answer 90.9% to 89.4%, first five 98.4% to 98.4%**, the 22nd draw (about 89.6% on average): a draw, D99, and none of its five misses involves these files (D99 says how that was checked). **Not proven:** that a Pioneer player acts on these frames, which the codes' presence in the IR Blaster's database for other Pioneer devices does not say; and what a `Discrete On`/`Off` of the DV-C505 does on a given model.

## 62. KEF's IR code sheets

KEF publishes a one-page PDF, **`IR Codes for Universal Remote Control`**, for each of its wireless speakers and the XIO soundbar: a table of the NEC codes a universal remote must send. **They are read: 6 products, 6 files and 124 keys, all Plausible.** It is the first source that is a PDF; the manufacturers' tables (§29, §31, §58, §60) are now 314 files and 34,552 keys.

### D243 — The documents, how a PDF is pinned, and the licence

Six documents, fetched on 2026-10-13 (the date is the machine clock's): the **LS50 Wireless**'s (18 pages: two tables, each in nine languages), the **LS50 Wireless II**'s, the **LS60 Wireless**'s (whose text says `LS60 Wireless II` and whose table is the LS50 Wireless II's; the file name says `LS60W`), the **LSX**'s (12 pages, English and other languages), the **LSX II**'s and the **XIO**'s. Four are on KEF's asset host (`assets.kef.com`); the LSX II's and the XIO's are the files KEF's product pages link, on the image host those pages use, at a URL that carries a 40-digit hex id. They are pinned as D123 does it: the document as the server sent it, its URL, size, `Last-Modified` and SHA-256 in `sources/official/kef/MANIFEST.json`. **A PDF page is converted to CSV as the lines of its text** (`tools/official_sheets_to_csv.py`, which needs PyMuPDF, one non-empty line a row, one cell, in the order the library reads the page); the importer parses those lines and the table's rules and cells are not kept, which suits a table of two columns and would not suit a long one. **The pages in other languages are not read**: of the LSX's and the LS50 Wireless's 30 pages, the 27 whose text has hex in it hold the same codes in the same order as their English page (hex case aside; checked once, page by page: the other three have no hex in their text). **Licence:** none of the six carries a copyright, rights or reproduction notice (their text was searched for copyright, the sign, rights, reproduc, trademark, confidential and permission), so by the rule stated in PR #42 they are public under D110, as Marantz's, Anthem's and Oppo's are; a document with an "All rights reserved" (Kaleidescape's, Anthem's SLM PDF) is not.

### D244 — The rule

A sheet says `Code Format: NEC` and `Factory Code: 0x01`, and each row is a number, a function and one code (`0x40`). **The ledger's NEC1 is the same frame with the bytes in the other bit order**: the device is the factory code reversed, `0x80`, its complement `0x7F` the sub device, and the function the code reversed (`0x40` is `0x02`, `0xA0` is `0x05`, `0x58` is `0x1A`). **The IR Blaster database's KEF remote is what settles the order: its eight keys (power, mute, play/pause, source, volume up and down, and two unnamed ones) are NEC1 device `0x80` with exactly these functions, and each of the eight is a code of each of the six sheets.** The sheets give **no carrier**: NEC1's 38 kHz is used, as for Anthem's and Oppo's; the IR Blaster's KEF remote carries 38.4 kHz, the value the IR Blaster import gives it, so the same codes are two signals in the ledger, 1% apart in carrier, which is not judged here. Four details: **a row with two codes** (the HDMI row of the LS50 Wireless II, the LS60 Wireless and the LSX II: `0x88` and `0x8A`) is two keys, the second labelled `(second code)`, and the sheet does not say how they differ (its discrete pairs, `0x38`/`0x3A` for power and `0x48`/`0x4A` for mute, are two apart as these are); **the XIO's 18th row is numbered 28** in the sheet, which is read in its place and reported; **the LS50 Wireless has two tables**, one for the speakers made before serial `LS50W06255` (8 codes) and one for those after `LS50W06256` (19), and the file is the 19 with each key's citation saying whether the first table has it (the 8 do; the other 11 are the ones an older speaker may not take, and the sheet's footnote says older firmware does not); **the LS60 Wireless is named for the product** and its keys say the sheet calls it `LS60 Wireless II`.

### D245 — The rule checked against other data

- **The IR Blaster's KEF remote, 8 of 8** (D244): the check measures the bit order and the sheets' fields together, since the sheets' `0x40` and the database's `0x02` are the same byte.
- **The six sheets against each other.** Written between 2019 and 2025, they hold 26 distinct function codes, and **a code means one function in all of them**: the labels differ only in case and spacing, except three pairs the two older sheets (LS50 Wireless, LSX) and the newer ones name differently (`Next`/`Forward`, `Source toggle`/`Next source`, `Previous`/`Backward`). The XIO adds three of its own, `Favourite`, `EQ1` and `EQ2`, which the KEF Connect app customizes.
- **What is not evidence:** nine of the 26 codes are frames a LIRC remote (`freecom/unknown-1`, a Freecom USB DVT receiver's) also sends, because NEC address `0x80` and low function numbers are common; it says nothing about KEF.

### D246 — What it does to the rest, and what is not proven

The corpus is **16,692 remote files and 683,972 keys**, 5,462 brands, 282,071 models (11 more) and 179,873 signals (17 more: the 26 distinct codes, nine of which another remote sends too). Both bundle profiles are 59,445,248 bytes (16,384 more), dataVersion `5b9d537c7cac`, `--verify` passes. **47 of the 124 keys map to the vocabulary (37.9%)**; the `official` row goes from 9.3% to 9.4% and the corpus stays at 72.1%. **The search evaluation: first answer 89.4% to 87.5%, first five 98.4% to 97.8%**, the 23rd draw (about 89.5% on average), a draw (D99 says how that was checked: **none of the 320 queries answers differently from the catalog before the import**). **Not proven:** that a KEF speaker acts on any of these frames, which no document says beyond its own table; that the carrier is 38 kHz and not 38.4; what the second HDMI code is; and that the LS50 Wireless of an older serial takes the 11 discrete codes (the file lists them with the table they come from).

## 63. JVC's projector IR emulation guide

JVC's **`Infrared Remote Control Command Emulation (Long Hex Format)`**, 2012 version 3.0, is a 37-page PDF for 22 D-ILA projectors (the DLA-HD350, HD750, HD550, HD950, HD990, X3, X7, X9, X30, X70R, X90R, RS10, RS20, RS15, RS25, RS35, RS40, RS50, RS60, RS45, RS55 and RS65): for each function a name, the models it applies to, `Device Code: 115 (0x73) Function: N` and the Pronto hex. **It is read: 211 entries (209 codes), 22 files and 3,047 keys**, 2,993 Plausible and 54 Untested. The manufacturers' tables (§29, §31, §58, §60, §62) are now 336 files and 37,599 keys.

### D247 — The document and its licence

Fetched on 2026-10-13 from `support.jvc.com` (142,812 bytes, `Last-Modified` 2012-08-23) and pinned as D123 does it. **Its last page is a licence of its own: "This document may be distributed freely, as long as the author and copyright information remain intact", (c) 2012 JVC, written by Dale McWilliams of JVC Consumer A/V Group**, with a disclaimer of warranty. It carries a grant, where KEF's sheets carry no notice at all (D243), and the condition is on distributing the document: **the PDF is kept whole in `sources/official/jvc/`** (so its author and copyright are intact where it is distributed), and the bundle's notices for the `official` source say who wrote it and what it permits. The keys are codes, which the repository treats as facts (D110). All 37 pages' lines are converted to one CSV, `pages`, a page number and a line of text to a row (`tools/official_sheets_to_csv.py`), because an entry's title can wrap and a page break can fall inside the hex.

### D248 — The rule

An entry is a title (the function's name, usually with the models it applies to in parentheses), a `Device Code: 115 (0x73) Function: 214 (0xD6)` line and the lines of hex after it; a line that looks like hex after the `Device Code` line is the entry's Pronto text however damaged, so a damaged line is not the next entry's title. **A key is the ledger's `JVC` as an `irp` form: device `0x73` and the function**, with the ledger's own block for the protocol (37.9 kHz). **A title is read as names and the models of each**: `HD350/750/RS10/20` is four models (a number takes the letters before it), the lists' `X70` and `X90` are the cover's `X70R` and `X90R`, a parenthesis that is not a list of models (`(PC)`, `(Color Transient Improvement)`) is part of the name, a dash or an `on` in one gives a note kept in the citation (`X7/X9 - Film Mode Only`, `PC input on HD750/X7`), and **a text after a list starts another name of the same code** (`Anamorphic - Off (X3/X7...)` and `Vertical Stretch - Off (HD350...)` are one code, whose key is named for the models that use each). **A key's label is the name tidied** (`tidy`; the citation keeps the title as the guide writes it): a description at the end (`Menu (On/Off toggle)`), the triangles of the cursor keys and the sentence after `Lens Aperture - If` are dropped, and a dash between words becomes a colon (`Power: On`), while a dash at the end is a minus and stays (`Brightness -`); the guide's `Power - On` matches nothing in the key vocabulary (D84) and `Power: On` is `POWER_ON`, **which is what gives a projector a power key**. A model gets a file of its own, `DLA-X30`, with every function whose models include it. **72 of the 211 entries name no model at all** (`Power`, `Menu`, the cursor keys, `Brightness +`) and are read as every model on the cover: 72 times 22, **1,584 of the 3,047 keys rest on that reading**, which the guide does not make and the citations say. Functions 35 and 36 are listed twice, each time for a group of models, and a model that is in both groups keeps the first (40 repeats counted).

### D249 — The rule checked against the guide itself and against other data

- **The guide against itself.** Each entry's hex is JVC's frame (`0000 006D 0001 0011`, a lead-in sent once, then sixteen bits and a stop mark, each bit a `0014` mark and a `0014` or `003C` space, the device byte and the function byte least significant bit first): **208 of the 211 decode to exactly the device and function the entry states**, and the decimal and the hex of the stated code agree in all 211. **Three entries' hex text is damaged** (`011014` and `00010` inside a word: `Color Management - Custom 3`, `Picture Mode - Cinema 3` and `Stage`, functions 99, 102 and 103): their keys are Untested, 54 of them in the models that have them, and say so, as an Oppo row the signal contradicts is (D132). Nothing is guessed about what the hex should have been.
- **The ledger's own signal against the guide's hex.** For each of the 206 distinct codes whose hex can be read, the ledger's compiled lead-in, sixteen bits and stop mark are the guide's pair for pair; only the final space of the repeat differs, by **one carrier cycle (143 codes) or two (63)**: the guide pads a frame to the period the ledger's `JVC` does, to within 0.1 ms. The guide sends the lead-in once and then the frame, the ledger puts the first frame in its intro, and on the wire they are one signal.
- **Other sources' projector remotes.** Nine remotes of the ledger send `JVC` with device 115 (seven of the IR Blaster database's and the JP1 forum's `DLA-RS2` and `X590R` upgrades): **300 of their 317 keys are a function the guide lists (94.6%)**, and where they have names these agree (power on is 5 and off 6, the cursor keys 1, 2, 54 and 52, the picture modes 104 to 111, the inputs 112 and 113). 89 of the guide's 209 codes are in them already.

### D250 — What is not read, what it does to the rest, and what is not proven

**Not read:** the model `X79` of `Color Profile`'s list, which the cover does not have and which is probably `X70`'s typo (it is not read as one; one key fewer for `DLA-X70R`). A JVC support page is reported to list a 2014 guide for the DLA-RS49 to RS67 and X500 to X900, which was not fetched. The corpus is **16,714 remote files and 687,019 keys**, 5,462 brands, 282,093 models (22 more) and 179,993 signals (120 more: the 209 codes, 89 of which were there). Both bundle profiles are 59,588,608 bytes (143,360 more), dataVersion `d9c192caa009`, `--verify` passes, and **`bundle_vectors.json` has 335 vectors, from 331**: the four new are `JVC` keys of the `official` source, a pair the vectors did not cover. **437 of the 3,047 keys map to the vocabulary (14.3%)** (132 before the labels were tidied, D248): a projector's `Lens Shift`, `Gamma` and `Color Temp.` are not generic keys (D84); the `official` row goes from 9.4% to 9.8% and the corpus from 72.1% to 71.9%. **The search evaluation: first answer 87.5% to 90.9%, first five 97.8% to 97.2%**, the 24th draw (about 89.6% on average), a draw (D99 says how that was checked: **none of the 320 queries answers differently from the catalog before §62 and §63**). **Not proven:** that a projector acts on a function the guide names for it, which for the 1,584 keys of the entries with no models is the reading of D248; that the first frame of the guide's signal and the ledger's are what a projector wants, beyond the 0.1 ms; and what an entry does that its title only names (`Lens Control (cycles through all options)`).

## 64. Arcam's custom installation notes

For each product family Arcam publishes a PDF, **`Custom Installation Notes`**, of its serial control commands and its **RC-5 remote codes**: a table of functions with the code as `system-command` in decimal and as `Data1 - Data2` in hex. **They are read: 8 documents, 19 products, 19 files and 1,816 keys, 1,815 Plausible and one Untested.** The manufacturers' tables (§29, §31, §58, §60, §62, §63) are now 355 files and 39,415 keys.

### D251 — The documents, how their tables are pinned, and the licence

Eight documents on Arcam's own host, `arcam.co.uk`, fetched on 2026-10-13 and pinned as D123 does it (the years are the server's `Last-Modified`): the **AVR380/450/750**'s (2015), the **AVR5/10/20/30/AV40 and AVR11/21/31/AV41**'s (2021), the **SA10/SA20**'s (2018), the **SA30**'s (2021), the **CDS50**'s (2019), the **UDP411**'s (2015), the **Solo**'s (a two-page table of its remote's codes) and the **Solo Mini**'s (2007 to 2008, its RC-5 codes as lines of text). **The tables are converted as the cells the PDF library finds** (`tools/official_sheets_to_csv.py`, `tables`: a row is the page, the table's number on it and the cells of one table row, every cell as text); the Solo Mini's lines are its pages' text. The importer reads the tables whose heading is `Function | RC5 code ...` or `System Code`, so the serial command tables beside them are in the CSV and not read. **The PA720/PA240/PA410's document has no IR table** and is not pinned. **Licence:** none of the eight carries a copyright, rights or reproduction notice (their text was searched as KEF's and the others' were, D243); the notes end with Arcam's address and an issue number. By the rule stated in PR #42 they are public under D110; the retailer-hosted copy of the AVR250/390/550/850/860 notes is a third party's and is not used.

### D252 — The rule

**A code is the ledger's `RC5`: the system code the device, the command the function** (RC-5 holds a command to 127, as `Standby On` is 124). Three layouts: the function tables (`Standby  16-12  0x10 - 0x0C`, in the six documents of the AVR, SA, CDS and UDP families); **the Solo's table**, a system code written on the first row of a group (16 for the amplifier, 17 for the tuner, 20 for the CD) and a decimal command, no hex; **the Solo Mini's lines** after `IR remote commands` (`16-12 Standby`). **A document's models are the ones its title lists**, one file each: `AVR380`, `AVR450`, `AVR750`; `AVR5`, `AVR10`, `AVR20`, `AVR30`, `AV40`, `AVR11`, `AVR21`, `AVR31`, `AV41` (the title writes `ARV31`, a typo, and its changelog `AV41`); `SA10`, `SA20`; `SA30`; `CDS50`; `UDP411`; `Solo`; `Solo Mini`. **A function the table writes `(not AVR5, AVR10)` or `(AVR450/750 only)` is left out of the files it is not for** (a number takes the letters before it): the AVR5 and AVR10 have 21 rows fewer of the AVR5-41 document's 120, the AVR380 two fewer of the AVR380/450/750's 127. A label is the function as the table writes it, the restriction and the footnote asterisk removed and the row as written in the citation; **the tables split their functions into `Basic` (on the supplied remote) and `Advanced` (created for custom installation, not on the remote), which the ledger does not tell apart**. **The same code means different things in different products' tables** (system 16 command 53 is `Select FM` in the Solo's, `Source FM` in the Solo Mini's, `NET` in the AVR380/450/750's and `Play` in the AV41's), and each file keeps its own document's name. **A digit row's command is the digit** (`Number 3` is command 3, `CD Number 7` is 20-7): it is in 79 of the 80 digit rows of the eight documents, and the one that is not is Untested (D253).

### D253 — The rule checked against the tables themselves and against other data

- **The tables against themselves.** Each of the 424 rows of the six function tables says its code twice and **all 424 agree**, the decimal `16-12` and the hex `0x10 - 0x0C`; three rows write the hex loosely (`0x10 - 4C`, `0X17` twice) and still say the code. A row that did not agree would be Untested (none does). The Solo's and the Solo Mini's 192 rows have a decimal only.
- **One row is Untested for another reason: the Solo's `Number 3` of system 20 (the CD's)**, whose command the table gives as 2, the command of `Number 2` above it, where every other digit row of every document has the digit for its command. The Solo Mini's table has `CD Number 3` as 20-3, which says the Solo's is a typo; the key is not corrected, and says so.
- **The Solo's table against the Solo Mini's.** They share 67 codes and, compared by name, **the function is the same in all 67** (`Select TV`/`Source TV`, `Vol Plus`/`Volume +`, `Number 0`/`Tuner Number 0`, `Next Track`/`CD Track Forward`...); the comparison is what found the typo.
- **Other sources' Arcam remotes.** The ledger holds eight RC-5 remotes of Arcam's (five of the IR Blaster database's and three JP1 upgrades: the AVR100, the AVR250 and an `Amp/Tuner/CD`): **337 of their 401 keys (84.0%) are a code some table of Arcam's lists**, with the meaning agreeing where it is plain (volume plus and minus 16 and 17, mute 13, power 12, the standby on and off 124 and 123, the tuner's digits). The rest are other products' and other generations' codes. **Only 28 signals are new** of the 272 distinct codes: the rest were in the ledger already.

### D254 — What it does to the rest, and what is not proven

The corpus is **16,733 remote files and 688,835 keys**, 5,462 brands, 282,130 models (37 more) and 180,021 signals (28 more). Both bundle profiles are 59,641,856 bytes (53,248 more), dataVersion `073a6cff14fa`, `--verify` passes. **520 of the 1,816 keys map to the vocabulary (28.6%)**: `Standby`, `Mute`, the digits and `Menu` do, and `Increase volume (+)`, `Navigate Up` and `Select CD` do not (D84's aliases do not have them, which is the owner's call, TODO); the `official` row goes from 9.8% to 10.6% and the corpus from 71.9% to 71.7%. **The search evaluation: first answer 90.9% to 85.9%, first five 97.2% to 97.5%**, the 25th draw (about 89.4% on average), a draw (D99 says how that was checked: **none of the 320 queries answers differently from the catalog before §62, §63 and §64**). **Not read:** Arcam's older products (the AVR300, AV8 and AV9 have their codes in handbooks), the serial commands the same PDFs document (the importer reads RC-5 tables only), and the retailer's copy of the AVR250/390/550/850/860 notes. **Not proven:** that a product accepts a code its table lists, which for the `Advanced` ones needs a programmable remote or the serial link the notes describe; that a `(not ...)` restriction is complete.

## 65. Cambridge Audio's NEC code lists

Cambridge Audio's driver-updates page links sixteen PDFs of IR codes and a list of the codes of three remotes. **Two of them carry no copyright or rights text, and they are read: the AXR85/AXR100 table and the 752BD, CXU and CXUHD remotes' list, 5 files and 198 keys, 197 Plausible and one Untested.** The other thirteen documents say "(c) Copyright Cambridge Audio Ltd" and are for the private repository (D130). The manufacturers' tables (§29, §31, §58, §60, §62, §63, §64) are now 360 files and 39,613 keys.

### D255 — The documents, the licence scan, and which are read

The page answers a script with a Cloudflare challenge (the PDFs on `/sites/default/files/compliance/doc/` do not), so it was read through a web-fetch tool on 2026-10-13: a table of PDFs beside the products each is for. **That table is reported, not pinned.** All 17 files were downloaded (the remotes list's page puts it under the CXUHD) and their text searched for `copyright`, `©`, `(c)`, `rights`, `reproduc`, `permission`, `licence`, `confidential`, `proprietary`, `trademark` and `Ltd`:

- **Two files have none of those words**: `AXR85 100 IR Codes.pdf` ("Topaz AXR85/100 Stereo Receiver Remote Control Codes", all codes NEC, made in 2019, on the server since 2023-03-31) and `752BD-CXU-CXUHD remotes - Sept. 2024 v2.pdf` (a spreadsheet printed to PDF, on the server since 2025-11-18). By the rule I stated in PR #42 (no copyright or no-reproduction text: public under D110) they are read here. They are pinned as D123 does it and converted as the cells the PDF library finds (`tools/official_sheets_to_csv.py`, `tables`: 24 and 58 rows).
- **Fifteen files, thirteen documents (the CXA and Edge ones are each listed twice, byte for byte the same), end with `© Copyright Cambridge Audio Ltd` (2008 to 2021), and eight of the thirteen add** "This document contains proprietary information protected by copyright. All rights are reserved. No part of this document may be reproduced by any mechanical, electronic or other means, in any form, without prior written permission of the manufacturer" (the Azur 851A, 851C, 851E, 851N and 851W, the CXN, the CXU's 2015 document and the ONE). A copyright claim with no grant is the private repository's rule (D130, as T+A's), so **those are not in this repository**; TODO says so.

### D256 — The rule

**A code is the ledger's `NEC1`**: the table's four bytes are the address, its complement, the function and its complement, so the device is the address, the subdevice its complement and the function the function. The IR Blaster database's AXR85 remote holds `A1` as device `0xC0`, subdevice `0x3F`, function `0x1B`, which is the table's `C0 3F 1B E4`: the bytes are **in the ledger's bit order** (KEF's sheets are in the other one, D244). **A row whose second and fourth bytes are not the complements is left out** (none is). The carrier is 38,000 Hz as KEF's and Anthem's are (the documents state none); the build warns that NEC1's nominal is 38,400, as it does for those. Two layouts: **the AXR85/AXR100 table** (`Remote Button | IR Code`, 23 rows, one file each for the two receivers its title names, `AXR85` and `AXR100`) and **the remotes list** (a header `NEC: 49 B6`, repeated on the second page, and for each row a function code with its complement and a name for each of the 752BD, CXU and CXUHD, `N/A` where the product has no such button): the 752BD has 52 keys, the CXU 52 and the CXUHD 48. **The `Backlight` row (N/A, Yes, Yes) sends no code** and is a note of the report. A label is the name as the document writes it (`standby`, in lower case, for the AXR85's). **The CXUHD column gives the name `Subtitle` to two codes** (`57 A8` and `53 AC`, which is `Repeat` on the other two products): the second is Untested and says so.

### D257 — The rule checked against other data

- **The AXR85's table against the IR Blaster database.** Its remote 320 (`CAMBRIDGE AUDIO | AXR85`, 46 keys) holds **all 23 codes of the table, as the same device, subdevice and function**; 15 under the same name (`MENU`, `PRESET 1/5`, `D1`...), the other 8 in other words for the same key (`POWER` for `standby`, `VOL+` for `Volume Up`, `AM/FM` for `FM/AM`, the skip arrows). Other remotes with the address `C0 3F`, a common one, hold some of the codes under unrelated names (the IR Blaster's ADS, Gold, Sansui...): they are not counted.
- **The 752BD, CXU and CXUHD are not corroborated by Oppo's tables, though they share the address.** `49 B6` is Oppo's, and **43 of the CXU's 52 function codes are in Oppo's tables, but under another name in 41 of the 43** (`Stop` and `Pop Up Menu` agree): Cambridge's remote is its own table, so the likeness of codes is no evidence for it, and it is not counted. The same overlap is why the new signals are 30 of the 78 distinct codes and not more: all 23 of the AXR85's (the IR Blaster's remote sends them at 38,400 Hz, so its Pronto word differs from this import's 38,000) and 7 of the 55 of the players'.
- **Cambridge's own CXU document of 2015** (one of the thirteen, so not here) has the same NEC table for the CXU: **51 of this list's 52 CXU codes are in it**, 49 under the same name (`Open/Close` is `Eject` there and `On/Standby toggle` is `Standby`), the 2024 list adds `Filter` (`2A D5`), and the 2015 document has two hidden commands it does not. Two documents of one maker nine years apart that agree are one source, not two, but they are not copies of one another.

### D258 — What it does to the rest, and what is not proven

The corpus is **16,738 remote files and 689,033 keys**, 5,462 brands, 282,138 models (8 more) and 180,051 signals (30 more). Both bundle profiles are 59,650,048 bytes (8,192 more), dataVersion `d49061b41e07`, `--verify` passes. **127 of the 198 keys map to the vocabulary (64.1%)**; the `official` row goes from 10.6% to 10.9% and the corpus stays at 71.7%. Four of the five files have a key the app's test order (`POWER`, `POWER_OFF`, `POWER_ON`, `VOLUME_UP`, `MUTE`) can pick; **the CXUHD has none of them** (`On/Standby toggle` is not an alias of the vocabulary, D84), so it can be browsed and not tried (15,083 of the 16,381 remotes can). **The search evaluation: first answer 85.9% to 90.0%, first five 97.5% to 97.2%**, the 26th draw (about 89.4% on average): a draw, as D99 says, and checked as before: **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the nine misses involves a Cambridge model. **Not read:** the thirteen documents with a copyright line (private repository); the page's other downloads. **Not proven:** that a product acts on a code its table lists (the AXR85's are corroborated by another source; the 752BD, CXU and CXUHD's by Cambridge's own two documents only); that the driver-updates page's product column, which was read by a tool and not pinned, is the maker's; what `Darbee`, `Res` or `TV Type` do beyond their names.

## 66. Hegel's RC-5 code tables

Hegel publishes on its support site (`support.hegel.com`, the Downloads of a product) a **one-page PDF of RC-5 codes** for most of its amplifiers, DACs and CD players: `Hegel H190 RC-5 code table`, a date, and a table `Function | Command | Descripton`. **They are read: 12 documents, 14 products and a questionable file, 15 files and 318 keys, 295 Plausible and 23 Untested.** Hegel was not in the ledger at all (a new brand, 5,463 now). The manufacturers' tables (§29, §31, §58, §60, §62, §63, §64, §65) are now 375 files and 39,931 keys.

### D259 — The documents, how their pages are pinned, and the licence

The site hands a file out by its number (`/component/jdownloads/download/<number>-<anything>`), so all 107 downloads it answers for (of the numbers 1 to 130) were listed by number on 2026-10-13 (`Content-Disposition` names each); **12 are RC-5 tables** (63, 65 to 70, 72, 73 and 85 to 87; 64 and 71 do not answer) and the rest are manuals, firmware, serial and IP control codes, driver and integration files, not read. **Nine are of 2019** (`Last-Modified` 13 May 2019; their tables are dated 2014 to 2019): the **Röst**, **H90**, **H190**, **H360**, **H590**, **HD12**, **HD25**, **HD30** and the CD player **Mohican**. **Three are of 2020** (14 September 2020; tables of 7 and 9 September): the **H390 and H590**, the **H95, H120 and H190**, and the **P20 and P30**, which **add each code's Pronto hex** beside its row, and an `(alt)` row for the code's twin with the other toggle bit, because "all commands meant to be used repeatedly must be programmed with two separate HEX codes" alternating (1st use the original, 2nd the alternate). **Licence:** none of the twelve has a copyright, rights or reproduction notice (searched for the words KEF's and Arcam's were, D243, D251); the page of the H95's downloads has none either. By the rule of PR #42 they are public under D110. The pages are Excel printed to PDF, so **`tools/official_sheets_to_csv.py` has a further mode, `flow`: a line of text is a row `page, text, y, line` and a table row `page, row, table, y, cells`, in reading order, and a cell much taller than a row (the 2020 tables' hex column is one tall cell) is `<block>` and its lines are rows of text**, so the Pronto hex of a row is the line at the row's `y`.

### D260 — The rule

**A code is the ledger's `RC5`: the system code the device and the command the function** (RC-5's 127; D3b: the toggle bit is not in a file, so an `(alt)` row is the same key and is not one). The system code is the table's heading: `System 16 (Preamp 1)` for the sources, volume, mute and power; `System 19 (Preamp 2)` for the display, the startup and maximum volume, the factory reset and the USB playback keys (and, in the HD DACs, everything); `System 20 (CD-Player)` for the Mohican. **A document's products are the ones its title lists**, one file each (`Röst`, `H90`, `H95`, `H120`, `H190`, `H360`, `H390`, `H590`, `P20`, `P30`, `HD12`, `HD25`, `HD30`, `Mohican`; the Röst's file is `Rost.json`), and **`XLR 2 (H590)` and `ECO (P30 only)` are rows of that model of the document's list only** (the H390 has 26 keys, the H590 28; the P20 11, the P30 12). A label is the function as the table writes it (`IN +`, `HT / An2`, `Set Startup Vol`), the description (`Source select`, `Low Power Toggle`) is in the citation, and a row whose command is `N/A` (the H390's and H590's `Optical 3` and `Network`) is no key. **Two functions with one code are two keys**: in the HD12 and HD25 `IN + / INPUT` and `DAC+` are both command 32. **A product with two documents has one file**: the H190 (2019 and 2020) and the H590 (2019 and 2020). The newer document's rows are the file's, the older document's rows for a code the newer lacks are added (the H190's `DAC+`, 32), and a key says what the other document calls its code (`Power`, command 79, is `ECO` in the 2019 table) or that it lists it too. **Unless the older document contradicts the newer**: where a code is named differently by the two (the synonyms `BAL`/`Balanced`/`XLR 1`, `An1`/`Analog 1`, `HT / An2`/`Analog 2`, `Coax1`/`Coaxial`, `Net`/`Network` and `ECO`/`Power` are not a difference) the older document's rows are kept as written, as the owner asked of questionable data (2026-10-13), **in a file named `<model> (<year> table)_questionable`, Untested, with the reason in every citation**.

### D261 — The rule checked against the pages' own hex, and against each other

- **Every Pronto hex decodes to its row.** 67 rows of the three 2020 tables give the code as hex; each is read as an RC-5 frame (32-cycle half-bits, the first mark standing for the start bit's second half) and **all 67 decode to the row's system and command, all with the toggle bit 0; the 24 `(alt)` rows are the same codes with the toggle bit 1** (none is not). **Each hex is also the ledger's own compile of the row's code word for word except the last, the gap after the frame, which is 8 carrier cycles shorter in 34 and 7 in 33**: so the hex and the ledger's RC5 are one waveform to within 0.23 ms (D3b's T=0 is the first press).
- **The 2019 and 2020 tables of the H190 agree**: they share 22 codes and name none differently (`Net` and `Network`, `An1` and `Analog 1`...). **The H590's do not: they share 22 codes and the 2019 table names 8 of them differently, all of them inputs** (`Net` is the 2020 table's `Analog 2`, `Optical1` its `Coaxial`, `Coax1` its `Optical 1`, `USB` its `Optical 2`, `HT / An2` its `USB`...). The five 2019 tables of the Röst, H90, H190, H360 and H590 are **one table of inputs** (`An1`, `BAL`, `Net`, `Optical1` to `3`, `HT / An2`, `Coax1`, `USB`; the H90's lacks `BAL`), and the 2020 table's inputs for the H590 (`XLR 2`, `Analog 3`, `BNC`) are not in it: **the 2019 H590 table is probably the Röst's table under another name**. The 2020 table is the H590's file, with its hex, and the 2019 table is `H590 (2019 table)_questionable`, 23 keys, which is also the one place the H590 has `DAC+`.
- **No other source:** the ledger held no Hegel remote, so nothing else could be compared. The independent evidence is the decoding above and the 2020 H190 table against the 2019 one.

### D262 — What it does to the rest, and what is not proven

The corpus is **16,753 remote files and 689,351 keys**, 5,463 brands, 282,168 models (30 more) and 180,052 signals (1 more: of the 62 distinct codes, 61 were in the ledger already). Both bundle profiles are 59,658,240 bytes (8,192 more), dataVersion `ab728bb69bb9`, `--verify` passes. **149 of the 318 keys map to the vocabulary (46.9%)**: `Power`, `Power On`, `Power Off`, `Volume Up`, `Volume Down`, `Mute`, `Display`, `Previous Track`, `Next Track`, `Play/Pause` and some inputs (`USB`, `Coaxial`, `Optical 1`, `CD`, `AUX`) do, and `Mute On`, `Mute Off`, `IN +`, `IN -`, `Set Startup Vol`, `Set Max Vol`, `Factory Reset`, `Analog 1`, `Balanced`, `Network` and the Mohican's `Track1` to `Track +10 (0)` do not (D84's aliases, the owner's call); the `official` row goes from 10.9% to 11.2% and the corpus stays at 71.7%. **Fourteen of the 15 files have a key the app's test order can pick; the Mohican, a CD player with no power, volume or mute key, has none** (15,097 of the 16,396 remotes can be tried). **The search evaluation: first answer 90.0% to 89.4%, first five 97.2% to 97.5%**, the 27th draw (about 89.4% on average), a draw (D99): **of the 320 queries 319 answer identically from the catalog before this section, and the one that does not, `Hegel H390`, is a query for a model the new catalog added**, and none of the eight misses involves a Hegel model. **Not read:** the other files of the downloads (manuals, the serial and IP control codes of the H120, H190, H390, H590 and Röst, firmware). **Not proven:** that a Hegel product acts on a code its table lists (the 2020 tables' own hex is consistent, and that is all the evidence there is); that the 2019 H590 table is wrong rather than the 2020 one right for a later firmware; that `Display`, `DAC+` and `Set Startup Vol` do what their names say.

## 67. Linn's RC-5 code table

**Moved:** the table's data, snapshot and reader are in the private repository since §70 (the owner's decision of 2026-10-13); this section is what was decided and measured when it was public.

LinnDocs, Linn's support wiki, has a page **`RC5 Codes`**: "the current list of RC5 codes supported by Linn's network controlled products (DS/DSM/etc). These codes can be used to program 3rd party remotes to work with these products." **It is read: one table of 128 command codes against the three device addresses the products answer to, 3 files and 116 keys, 115 Plausible and one Untested.** Linn had three RC-5 remotes in the ledger (from the IR Blaster database) and no table of its own. The manufacturers' tables (§29, §31, §58, §60, §62, §63, §64, §65, §66) are now 378 files and 40,047 keys.

### D263 — The documents, and the licence, which is an explicit grant with a condition

Three documents, fetched on 2026-10-13 and pinned as D123 does it: **the page** (`RC5_Codes.html`, MediaWiki revision 23074, "last edited on 30 November 2020", 37,647 bytes); **a one-page PDF of Pronto hex** (`Linn DS/DSM Pronto codes`, V1.0, 9 April 2019: the amplifier's 51 codes, "untested - derived via MAKEHEX conversion utility"); and **the wiki's general disclaimer** (`LinnDocs:General_disclaimer`, which the page's footer links to). **The disclaimer grants "a limited license to copy anything from this site" and says there is "no agreement or understanding between you and Linn Products Ltd regarding your use or modification of this information beyond the GNU Free Documentation License (GFDL)"**; the page and the PDF carry no other notice (their text was searched as KEF's and the others' were, D243). **This is the first copyleft grant among the manufacturers' tables**, as JVC's was the first with a condition (D247): the page and the disclaimer are kept whole in `sources/official/linn/`, the bundle's notices name the grant, and the ledger's files record the codes and the names the table gives them (facts, D110) and not the page's prose. **The owner may read the GFDL's share-alike differently** (it is on the PR). **Not read:** the wiki's third document, the 22-page PDF `Linn RC5 Codes` (revision 1.02, 24 October 2011, no notice text) that has the codes of "all other (legacy) products" by address (amplifiers 0x10 and 0x13, tuner 0x11, CD 0x14, DVD 0x19): **its tables' cells run over several lines and the text cannot say which line belongs to which code** (`0x12` is `Phono 1 Laser Auxiliary 2`, and `Auxiliary 2` is again on the line below the next code), so it would need the page images; it is in TODO. The page's table is read by `tools/official_sheets_to_csv.py`'s new `table` mode for HTML (the standard library's parser: `<br>` is ` / `, `<sup>1</sup>` is `^1`); the PDF by `flow`, as Hegel's (D259).

### D264 — The rule

**A code is the ledger's `RC5`: the address the device and the command the function.** The table's columns are **the amplifier 0x10 (16), the CD 0x14 (20) and the DVD 0x19 (25)**, and a cell is what that code does at that address (`Zero`, `Volume +`, `Skip +`), with `.` where the address has nothing. **A file is an address**, `DS/DSM Amplifier` (51 keys), `DS/DSM CD` (35) and `DS/DSM DVD` (30), controlling `Linn DS` and `Linn DSM`: the table says what the codes do for "DS/DSM/etc", not which product has which address, so the files are of the table's three device types. **A cell's first line is the label**, brackets removed (`[Direct Source PHONO]`, and one cell writes `Direct Source TUNER]`); **every other line is a note in the citation**: the held functions (`Held Function: Scroll Metadata`: a long press sends the same code), the product each source is on (`Linn DSM - HDMI2`, `Akurate Kontrol/1 - ANALOGUE1`) and the footnote marks. **A cell that begins `Reserved (REM019)` is a function reserved for that Linn remote** (REM018 to REM021), its label the bracketed function after it (`Audio Adjust: Balance +`, `Play/Pause`, `*`, `#`) and the citation says so; one that has no function after it (the DVD's `0x33`) is no key and is in the report. Two keys of one file with one label are told apart by the key name (the CD's `Skip -` is 0x21 and, reserved for REM021, 0x61).

### D265 — The rule checked against the PDF's hex, and against other data

- **The PDF's Pronto hex against the table.** The PDF has a row for each of the amplifier's 51 codes, each with its Pronto hex (`Device Code: 10 Function: 13`). Each is read as an RC-5 frame (Hegel's reader, D261): **50 of 51 decode to the table's address and command, all with the toggle bit 0; the one that does not is `Power Off` (0x7E, 126), whose hex decodes to command 127.** That key is Untested and its citation says why; nothing is guessed about which of the two is right (`Power On`, 0x7D, agrees).
- **The ledger's three Linn RC-5 remotes** (IR Blaster database: 3714, 3904 and 456, 125 keys) **hold 64 keys that are a code of the table, and where the vocabulary or the spelling compares the names, 30 agree and 4 do not**: one remote's cursor keys (`UP`, `DOWN`, `OK`, `YELLOW`) are on the CD's `Play`, `Pause` and `Stop`, a button named for where it sits and not for what it sends. The CD addresses of two of them (3714 and 456, which send the digits, `Pause`, `Play`, `Skip`, `Seek` and `Repeat` at address 20) and the amplifier keys of the third (`Volume +` and `-`, `Mute`, `Power`, the balance and the sources at 16) are the table's.

### D266 — What it does to the rest, and what is not proven

The corpus is **16,756 remote files and 689,467 keys**, 5,463 brands, 282,173 models (5 more) and 180,059 signals (7 more: of the 116 distinct codes, 109 were in the ledger already). Both bundle profiles are 59,666,432 bytes (8,192 more), dataVersion `63cb42ca2283`, `--verify` passes. **57 of the 116 keys map to the vocabulary (49.1%)**: the digits (`Zero` to `Nine`), `Power (Toggle)`, `Power On`, `Power Off`, `Volume +`, `Volume -`, `Mute (Toggle)`, the cursor keys, `Play`, `Pause`, `Stop`, `Play/Pause`, `Help`, `Favourite`, `*` and `#` do, and `Source +`, `Skip +`, `Seek +`, the `Direct Source ...` keys, `Balance`, `Lipsync`, `Pin1` to `Pin6` and `Display Mode Toggle` do not (D84's aliases, the owner's call); the `official` row goes from 11.2% to 11.3% and the corpus stays at 71.7%. **The amplifier file has a key the app's test order can pick; the CD's and the DVD's have none** (15,098 of the 16,399 remotes can be tried). **The search evaluation: first answer 89.4% (as §66's), first five 97.5% to 97.8%**, the 28th draw (about 89.4% on average), checked as before: **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the seven misses involves a Linn model. **Not proven:** that a Linn product acts on a code its table lists (the PDF's hex and three other remotes agree, which is all the evidence there is); which Linn products answer to which address; that the table is current ("the current list", last edited November 2020); what `Pin1` to `Pin6` or the `Reserved` functions do beyond their names.

## 68. Simaudio's table of IR codes

Simaudio, the maker of the Moon range, publishes a three-page **`Table of IR codes`** (Technical Notice, Rev. 1.1, December 2002): the RC-5 and RC-6 codes of its preamplifiers and integrated amplifiers, CD players and DVD players. **It is read: 8 products, 8 files and 170 keys, all Plausible.** Simaudio was not in the ledger (a new brand, 5,464 now). The manufacturers' tables (§29, §31, §58, §60, §62, §63, §64, §65, §66, §67) are now 386 files and 40,217 keys.

### D267 — The document and the licence

One PDF on Simaudio's own host (`simaudio.com/wp-content/uploads/2019/11/Simaudio-RC-5-IR-codes-rev1.1.pdf`, `Last-Modified` 6 November 2019; made from `Technical Notice Simaudio IR codes.doc`), 189,454 bytes, fetched on 2026-10-13 and pinned as D123 does it; **its pages are converted as their lines of text** (`tools/official_sheets_to_csv.py`, `pages`, as KEF's and JVC's were, D243 and D247). **Licence:** it has no copyright, rights or reproduction text (searched as KEF's and the others' were), only the title, a revision and "Page 1 of 3"; by the rule stated in PR #42 it is public under D110.

### D268 — The rule

Each page is one kind of product: **the preamplifiers and integrated amplifiers `Moon i-3, i-5, P-3, P-5`** (Philips RC-5, system code 16), **the CD players `Moon Nova and Moon Eclipse`** (RC-5, system code 20) and **the DVD players `Moon Stellar and Moon Orion`** (Philips RC-6, "Mode code : 00", "Control code: 04 (Customer code)", the commands in hexadecimal). **A key is the ledger's `RC5` (the system code the device, the command the function) or `RC6` (the control code the device, the command the function: the ledger's RC6 is mode 0 with an 8-bit address, D3b's toggle not in the file)**, and there is **one file for each product a page names** (`Moon i-3` ...: the brand is `Simaudio`, the name the one the page gives; `Moon Nova and Moon Eclipse` is two). A page's commands are bullets, `- Power : 12`, `- Shuffle` over `1C`, `- Search+ : 52`: **the name before the number is the label as written** (`Vol +`, `Bal R`, `OK (Acknowledge)`); a direct-access digit `0(track direct access)` is the key `0` with the parenthesis in the citation, and a name that begins with a glyph (`▲ (Cursor Up)`) is the words in its parenthesis. **The amplifiers' page ends with a note that only the P-3 has the eight inputs `0` to `7`** and that "the other units will simply ignore codes that don't apply to them"; the document does not say which model has which, so each of the four amplifiers has the eight and the note is in each of those keys' citations. The amplifiers' `Input` is 63 (a command above the digits; the page does not say what it selects).

### D269 — The rule checked against other data

**The table's 70 distinct codes (35 RC-5 and 35 RC-6) are held by other sources' remotes of the same protocol and device in 69 cases** (RC-5 system 16 and 20, RC-6 device 4), **and in 64 of them some other source's label is the same function by the vocabulary or the spelling.** The five that the comparison does not count are the same functions in other words except one (`Search+` is `FORWARD` or `DVD FWD` elsewhere, `OK (Acknowledge)` is `OK`, `Bal R` and `Bal L` are `Balance Right` and `Left`; **`A/B Repeat`, RC-6 0x3D, is `AV/SOURCE` and `EJECT` in the two keys that have the code**). **One code is nobody else's: `Search-`, RC-6 0x27.** The standard sets agree: the DVD players' `Standby` (0x0C), `Stop` (0x31), `Pause` (0x30), `Subtitle` (0x4B), the four cursor keys and `OK` are what most of the other RC-6 device-4 remotes name the same; and **the CD players' `Play` (53), `Stop` (54), `Pause` (48), `Next` (32), `Prev` (33), `Search+` (52), `Search-` (50) and `Repeat` (29) are Linn's CD address's (§67) with the same functions**, and the amplifiers' `Bal R` and `Bal L` (26 and 27) are Linn's `Balance Right` and `Left` (§67). No document of Simaudio's states the standard, so these are agreements and not confirmations.

### D270 — What it does to the rest, and what is not proven

The corpus is **16,764 remote files and 689,637 keys**, 5,464 brands, 282,189 models (16 more) and 180,060 signals (1 more: of the 70 distinct codes, 69 were in the ledger already, and the one that was not is `Search-`). Both bundle profiles are 59,670,528 bytes (4,096 more), dataVersion `431557f60ce5`, `--verify` passes. **148 of the 170 keys map to the vocabulary (87.1%)**: everything but `Bal L`, `Bal R`, `Search+`, `Search-`, `Scan`, `A/B Repeat` and `OK (Acknowledge)` (D84's aliases, the owner's call); the `official` row goes from 11.3% to 11.6% and the corpus stays at 71.7%. **All eight files have a key the app's test order can pick** (15,106 of the 16,407 remotes can be tried). **The search evaluation: first answer 89.4% to 89.7%, first five 97.8% (as §67's)**, the 29th draw (about 89.4% on average), a draw (D99): **of the 320 queries 319 answer identically from the catalog before this section, and the one that does not, `Simaudio Moon i-3 Simaudio`, is a query for a model the new catalog added**, and none of the seven misses involves a Simaudio model. **Not proven:** that a Moon product acts on a code its table lists (the other sources' agreement is the evidence); what the amplifiers' `Input` (63) selects, or which of them has the eight inputs; that a table of 2002 describes the products' later revisions.

## 69. Rotel's IR hex code PDFs

Rotel's **IR Codes** page lists 59 models of its amplifiers, CD players, tuners, processors and streamers, each with a link to a PDF of that model's **IR hex codes**: `ROTEL RA-1572 HEX CODES`, then sets of codes under `NEC 8311, MAIN IR CODES` and the like, every code a name and its Pronto hex. **They are read: 52 PDFs (51 read) for 57 of the 59 models, 93 files and 3,598 keys, 3,490 Plausible and 108 Untested.** Rotel had 25 remotes in the ledger (IR Blaster, JP1 and LIRC), 19 of them NEC. The manufacturers' tables (§29, §31, §58, §60, §62, §63, §64, §65, §66, §67, §68) are now 479 files and 43,815 keys.

### D271 — The documents, how they are pinned, and the licence

**The page** (`rotel.com/manuals-resources/ir-codes`, 60,903 bytes: 12 categories, 67 listings, 59 distinct models, a model in two categories having the same link) is kept whole as the evidence of which PDF is for which model, and read by `tools/official_sheets_to_csv.py`'s HTML mode `tables` (every table's rows, an anchor's address in braces after its text). **The 52 PDFs** are on Rotel's own host (`rotel.com/sites/default/files/product/ir/`), 14.8 MB with the page, fetched on 2026-10-13 and pinned as D123 does it (`Last-Modified` from 27 October 2020 for 32 of them to 7 May 2026 for the X430's; a PDF may be for two models: `A12-A14`); each is converted as its lines (`pages`, as KEF's and JVC's were). **Licence:** none of the 52 PDFs has a copyright, rights or reproduction notice (searched for the words KEF's and the others' were, D243); the page's only such line is the site footer, "© Grand Green Limited", with no terms of use. By the rule of PR #42 they are public under D110; **say so if you read the footer differently.** **The S14's and RAS-5000's PDF is not read**: its codes have three hex digits (`IR CODE 0x0E4`) and its Pronto carrier word is 0x82 (31.8 kHz), which is not NEC, and no protocol of the ledger's matches it (2 of the 59 models).

### D272 — The rule

A set's heading `NEC 8311, MAIN IR CODES` says **the address and the subaddress** (0x83 and 0x11: Rotel's NEC has a second byte of its own, as the other sources' Rotel remotes at `0x83` with `0x11`, `0x12`, `0x14`... show), a line `IR CODE 01` the **function** (the fourth byte of the frame is its complement, which the ledger's `NEC1` sends), and the lines up to the hex its name. **A key is the ledger's `NEC1` of the heading's two bytes and the code**; the carrier is 38,000 Hz (the hex's word 0x6D). **A model is a file** (`RA-1572`, `A12`, `DX-3`: the page's names, the PDF's models being the ones the page links it for); **a set headed `ALTERNATIVE` or `ALTERNATE` is the units' second IR code set** (a Rotel FAQ on changing the remote's code set, which I have not read, is reported to say that a unit can be switched between two) and **a file of its own**, `RA-1572 (alternative IR codes)`; every other set (the Bluetooth and USB codes, the Zone 2 to 4 discrete codes, the iPod codes, the `NEC 24DB` set, which is standard NEC with the address 0x24 and its complement 0xDB) is the model's file, **a name that an earlier set of the file has taking its set's words in parentheses**. **A set `RC5, SYSTEM CODE 17, ALTERNATE RC5 POWER & VOLUME CODES`** (four decimal commands, in the RSP-1570, RSP-1572, RSX-1550, RSX-1560 and RSX-1562) is **RC-5 and a file of its own** (`RSX-1562 (RC-5 codes)`). A name's `*` is dropped and the set's note (`requires X5 Main Software V1.05`) is in the key's citation. **R15: the JP1 files named `A11` and `CD11` have those names**, so these are kept as `A11 (Rotel hex codes)` and `CD11 (Rotel hex codes)` (the owner's rule of 2026-10-13: rename, do not skip). Four oddities of the documents are read as they are: **`IF CODE 0F` for `IR CODE 0F` (24 lines, 22 documents)**, a page's first line is its number (a name that is a digit, `1`, is a name), the RC-1550's PDF is titled `RC-1580` (the page's link decides) and the X3 Series 2's PDF has its first `IR CODE 01` line after its item.

### D273 — The rule checked against the hex, and against other data

- **Every Pronto hex is decoded and must agree with its heading and code**: the frame's four bytes are the heading's two, the code and its complement (and for the RC-5 sets the system code and the command). **Of the 3,119 codes 3,052 agree.** **The 67 that do not are all in three documents and are whole sets**: the **ZONE 4 DISCRETE CODES of the RSP-1570 (14), RSX-1550 and RSX-1560 (41, one document for both) are headed `NEC 8324` and their hex is the Zone 3's (`NEC 8323`)**, a copy that the RSX-1562's document, whose Zone 4 hex is `8324`, does not make; and **six codes of the RSP-1576's `NEC 8310` main set and six of its `NEC 8311` alternative set have the other set's address in their hex**. Each is kept as the document writes it, Untested, **in a file whose name ends `_questionable`** (`RSX-1560_questionable`, as the owner asked of questionable data), and the citation says what the hex decodes to.
- **Other sources' Rotel remotes** (the ledger's 19 NEC ones from the IR Blaster database, JP1 and LIRC): **528 of their 550 keys at the address 0x83 (96.0%) are a code some Rotel PDF lists**, and where the vocabulary or the spelling compares the names **306 agree and 12 do not** (`OK` and `ENTER`, `PRESET NEXT` and `TUNE UP`: the same keys in other words). **The PDFs' codes are the codes the other sources have**, and the PDFs add the sets those remotes do not hold: of the 584 distinct codes (580 NEC, 4 RC-5) **556 were not in the ledger**.

### D274 — What it does to the rest, and what is not proven

The corpus is **16,857 remote files and 693,235 keys**, 5,464 brands, 282,322 models (133 more) and 180,616 signals (556 more). Both bundle profiles are 59,875,328 bytes (204,800 more), dataVersion `7d48bfcc6175`, `--verify` passes. **1,540 of the 3,598 keys map to the vocabulary (42.8%)**: `POWER ON`, `POWER OFF`, `VOLUME UP`, `VOLUME DOWN`, `MUTE TOGGLE`, the cursor keys, `ENTER`, `SETUP`, `DIMMER`, `PLAY`, `PAUSE`, `STOP`, `EXIT` and the digits do, and `SOURCE CD`, `SOURCE TUNER`, `|<< TRACK`, `TRACK >>|`, `BASS`, `TREBLE`, `BALANCE` and the other source keys do not (D84's aliases, the owner's call); the `official` row goes from 11.6% to 14.2% and the corpus goes from 71.7% to 71.6%. **Of the 93 files six have no key the app's test order can pick** (the RCD-06SE, whose PDF has no power key, and the five questionable files: 15,193 of the 16,500 remotes can be tried). **The search evaluation: first answer 89.7% to 89.4%, first five 97.8% to 98.8%**, the 30th draw (about 89.4% on average), a draw (D99): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the four misses involves a Rotel model. **Not proven:** that a Rotel product acts on a code its PDF lists (the other sources' remotes agree on 96% of theirs, and that is the evidence); which of the two documents is right where a set's hex and heading disagree; that the alternative sets are for the code-2 setting, which the PDFs call `ALTERNATIVE` and do not explain.

## 70. Linn's RC-5 table moves to the private repository

**The owner decided that Linn's RC-5 table is kept in the private repository.** §67 had read it under the GNU Free Documentation License that LinnDocs' general disclaimer names, kept it public, and flagged the licence on the pull request ("whether to keep it public is your decision"); the answer was "keep in private repo". **The 3 files and 116 keys leave `remotes/official/linn/`** and are in `remote-ledger-private` as `remotes/linn/`, a source of its own (its `docs/linn.md`, LN-1 to LN-4). The manufacturers' tables (§29 and the sections after it) are now 476 files and 43,699 keys.

### D275 — The decision, and why

D130 put data with a stated "all rights reserved" in the private repository and data with no notice here (D110); §67 added a third case, an explicit grant with a condition, as JVC's was (D247), and kept it public with the grant named in the notices. **The GFDL's condition is share-alike, and the owner chose not to take it on in this repository**: a table of codes reduced to the codes and the names the table gives them may or may not be a modified copy of the page, and the owner did not want to argue it here. So **a document whose notice is a copyleft licence goes to the private repository** (`imports.json` says "GFDL, kept private by the owner's decision", kind `none`), and the rule of D130 is: no notice words, public; a notice that reserves rights or asks for a share-alike, private. It can come back by a public pull request of its own, with the bundle's notices naming the grant as §67's did.

### D276 — What moved, what stayed, and what the public history still has

- **Moved, unchanged:** the pinned snapshot (`sources/official/linn/` to the private `sources/linn/`, the same bytes and the same manifest, retrieval date 2026-10-13), the reader (`official/linn.py` to the private `remote_ledger_private/linn/importer.py`, which uses this repository's `official.common` and Hegel's RC-5 decoder), the fetch tool (a private `tools/fetch_linn.py`: the page's table and the PDF's pages are read from the pinned documents into the pinned CSVs, which `--sheets-only` checks) and the tests (the private suite: 58 tests, 10 of them Linn's). **The three files the private importer writes are byte for byte the three files that were here** (`cmp` against master's), so every citation, key name and tier is as §67 recorded it. Where a name collides with another source's remote the private importer keeps the file under `DS/DSM Amplifier (Linn RC-5 table)`, as the other private sources do (the owner's rule of 2026-10-13); none collides.
- **Removed here:** `official/linn.py` and its registration, the snapshot, `remotes/official/linn/`, the generated `build/pronto/official/linn/` and `site/r/official/linn/` (`rl build` does not delete an orphan, `rl build --check` names it, and they were removed by hand), the Linn tests of `test_official_import.py` (five of the 3,960), the fetch and CSV entries of the two tools, and the Linn sentences of the `official` source's notice (the GFDL one included). The IR Blaster database's three Linn remotes (`remotes/irblaster/LINN/`) stay: they are another source's.
- **Still in the public history:** the commits of pull request #78 and their generated trees, and the published site until the next Pages deploy. The GFDL allows those copies, so nothing is wrong with them, and rewriting this repository's history is not part of the decision; say so if it should be.
- **A statement of §68 can no longer be checked from this tree:** D269 compares Simaudio's CD and amplifier codes with "Linn's CD address's" (§67). It was prose and not a test; the Linn side of it is now in the private repository (its `docs/linn.md`, LN-3).
- **The lead that goes with it:** the wiki's 22-page legacy PDF (`Linn_RC5_Codes_v0102.pdf`, §67's D263, TODO), which needs the page images, is Linn's too; **if it is read it is read into the private repository**.

### D277 — What it does to the rest

The corpus is **16,854 remote files and 693,119 keys** (3 and 116 fewer), 5,464 brands (Linn stays one, with its three IR Blaster remotes), 282,317 models (5 fewer) and 180,609 signals (7 fewer: the table's codes that no other source held). Both bundle profiles are 59,871,232 bytes (4,096 fewer), dataVersion `f1ace2242dd2`, `--verify` passes. The `official` row goes from 14.2% to 14.1% of its keys mapped and the corpus stays at 71.6%. **The remotes the app's test order can try go from 15,193 of 16,500 to 15,192 of 16,497** (the amplifier file had a key it could pick; the other two had none). **The search evaluation: first answer 90.0%, first five 97.2%**, the 31st draw (about 89.4% on average; the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the nine misses involves a Linn model. **The README and TODO had said 477 files for the manufacturers' tables where the import report said 479 (§69's figure was the report's); they now say 476, which is the report's.** The README's total of remote files (16,855) was likewise two short of the catalog's 16,857 and is now the catalog's 16,854. **Not proven:** that the public Pages site has stopped serving the three Linn pages (it will when master is published again; I have not looked).

## 71. Cary Audio's DMS IR remote codes

Cary Audio's support files include a one-page **`DMS IR Remote Codes`** (Word to PDF, 2 November 2018): "All codes - to Philips RC-5 standard", a table of 38 keys with a key printing, a function, **two system codes, `A` (0x10) and `B` (0x09)**, and a command. **It is read: 2 files and 76 keys, all Plausible.** Cary Audio is a new brand (5,465 now); the ledger had one Cary remote, JP1's Cinema 11a (brand `Cary`, RC-5 system codes 18 and 19, another product). The manufacturers' tables (§29 and the sections after it) are now 478 files and 43,775 keys.

### D278 — The document and the licence

One PDF on Cary Audio's own host (`caryaudio.com/wp-content/uploads/2018/11/DMS-IR-Remote-Codes.pdf`, `Last-Modified` 2 November 2018, made by a Mac's Quartz PDF context from `DMS IR Remote Codes.doc`), 39,623 bytes, fetched on 2026-10-13 and pinned as D123 does it; **its table is converted as Arcam's were** (`tools/official_sheets_to_csv.py`, `tables`, D251): a row is the page, the table's number and the six cells. **Licence:** the PDF has no copyright, rights or reproduction text (its text and its bytes were searched for the words KEF's and the others' were, D243), only the title, the line about RC-5 and the table; by the rule stated in PR #42 it is public under D110. **I did not read the site's own terms of use**; say so if you want that done. The document names no product beyond `DMS`; reviews of Cary's DMS-500 and DMS-600 describe them as network music streamers with a button-festooned remote, which the table's keys (`NETWORK INPUT`, `INTERNET INPUT`, `QUEUE`, `NOW PLAYING`, `USB/ SD CARD INPUT`) fit, but that is not the document's word.

### D279 — The rule

**A key is the ledger's `RC5`: the system code the device, the command the function.** Every key has two system codes in the table, and **a file is one of them**: `DMS` is the column `A` (0x10, system 16) and `DMS (system code B)` the column `B` (0x09, system 9), the same 38 commands in both. The table does not say that a DMS answers to both, or to either, or why a key has two; a unit sends one system code, so a file for each is the reading that claims nothing. **The label is the key printing as written** (`POWER`, `UP`, `1`, `USB / SD CARD`, `*.-/`), **except a printing that is a sign beside a word** (`-  VOLUME`, `VOLUME  +`), whose label is the function's words (`VOLUME DOWN`, `VOLUME UP`): 24 of the 38 labels map to the key vocabulary (the function column would have mapped 8). **The function, as the table writes it, is in each citation**, with its misspellings (`NAVIGATION LIFT` for the key printed `LIFT`, which is presumably `LEFT` between `DOWN` and `RIGHT`, and `NETWORD INPUT` for `NETWORK`): nothing is corrected. The notes column is empty. The toggle bit is not in the file (D3b). A name another source has takes ` (Cary Audio table)` (R15), as Rotel's do; none does.

### D280 — The rule checked against other data

**Of the 76 codes, 67 are held by other sources' RC-5 remotes at the same system code and command** (all 38 of system 16, 29 of the 38 of system 9; 112 remotes hold some of system 16's and 8 some of system 9's), **and in only 3 does another source's label name the same function: `POWER` (0x0C: 30 of the 37 remotes that hold it agree), `VOLUME UP` (0x10) and `VOLUME DOWN` (0x11) (100 of 111 and 101 of 111).** (The official tables are not counted among the "other sources" here, as §68's check did not.) The other 35 keys of system 16 are the DMS's own assignment, not the standard's: its keys `5`, `6`, `7`, `9` and `0` send the commands (0x01 to 0x05) that 9 other remotes use for `1` to `5`, and its `1` is 0x0E, which none of the 10 that hold it calls `1`; the keys of system 9 have nothing in common with what the 8 remotes that use that system code name: 0 of 58 names agree. **So the table is the manufacturer's word and nothing corroborates it beyond power and volume**; the digits in particular are not what a remote with RC-5 digits sends, and if the DMS's own remote sends them they are as unusual as the table says. Plausible, as every manufacturer's table is (R19.3); nothing here says which of the two system codes a product answers to.

### D281 — What it does to the rest, and what is not proven

The corpus is **16,856 remote files and 693,195 keys**, 5,465 brands (Cary Audio is one more; `Cary` and `Cary Audio` are two brands in the catalog, as JP1's importer split the name, and there is no list that joins them: the brand aliases are Chinese names, D101), 282,320 models (3 more) and 180,618 signals (9 more: the nine codes of system 9 that no other source held). Both bundle profiles are 59,871,232 bytes (as before: the file grows in blocks of 4,096), dataVersion `75b8d44f58fd`, `--verify` passes. **24 of the 38 keys of each file map to the vocabulary (63.2%)**; the `official` row goes from 14.1% to 14.2% and the corpus stays at 71.6%. **Both files have a key the app's test order can pick** (15,194 of 16,499 remotes can be tried). **The search evaluation: first answer 90.0% to 87.8%, first five 97.2% to 97.8%**, the 32nd draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the seven misses involves a Cary model. **Not proven:** that a DMS acts on a code its table lists, or on which of the two system codes; which products the table is for; that the digits are right (they are unlike the standard's and nothing else holds them); that the table is current (2018).

## 72. NAD's IR command tables for its T-series

NAD's control documents for its T-series receivers and processors (two zips on its site, one for the "Txx5" models and one for the "Txx7") hold, beside the RS-232 command lists, a three-page **`IR Commands by Model`** for each model: every function the unit's remote sends, as a NEC address and a data byte. **They are read: 8 tables for 7 models, 8 files and 797 keys, all Plausible.** The ledger had 33 NAD remotes (the IR Blaster database's 21, LIRC's 7, JP1's 5), none of them named for a T-series model and none with its functions' names. The manufacturers' tables (§29 and the sections after it) are now 486 files and 44,572 keys.

### D282 — The documents, how they are pinned, and the licence

Eight PDFs, **each a member of one of two zips** (`NAD_TXX5_Control_Docs.zip`, 804,916 bytes: the T175, T765, T775 twice and T785; `NAD_TXX7_Control_Docs.zip`, 582,657 bytes: the T187, T777 and T787), on the Shopify CDN that serves nadelectronics.com's files, fetched on 2026-10-14 (the machine's date) and pinned as D123 does it: **the pin is the PDF** (7.4 to 8.8 KB; the zips also hold each model's RS-232 command list (44 to 46 pages), `nad_ethernet_rs232_spec_2.03.pdf`, `RS232Test.exe` and `IR Channels.doc`, which are not read and not kept), and **the manifest records the zip's URL and, new with this section, the member** (`tools/fetch_official.py`). **The manifest's `lastModified` is the CDN's, the time it served the file (2026-10-14), and is not a date of the document**: the PDFs' own dates are 24 March 2008 (the T175, T765, T775 and T785), 5 February 2008 (the older of the T775's two) and 27 April 2012 (the T187, T777 and T787). Each page is headed `IR Commands by Model` and footed `Page 52 of 127` or `Page 36 of 93`: extracts of a larger document. **Licence:** none of the eight, nor any other PDF in the zips, has a copyright, rights or reproduction text (searched for the words KEF's and the others' were, D243); by the rule stated in PR #42 they are public under D110.

### D283 — The rule

**A page is plain text with no ruling lines** (the PDF library finds no table), so it is read as **its cells at a place**: `tools/official_sheets_to_csv.py`'s new mode `cells` writes a row for each line (`page, y, x, text, x, text...`, the cells of one line within 2 points of `y`, by their left edge), and the importer takes the column places from the header row (`Function`, `Format`, `Address`, `Data`, `Address`, `Data`, `Comment`, with `Hex`, `Hex`, `Dec`, `Dec` under the four numbers) and puts each cell in the column it is nearest to, **and a cell more than 25 points from every column is an error**. **The hexadecimal and the decimal of an address and of a data byte are both read and must agree** (they do, in all 797 rows). `Model T187` on the page names the model, and must be the one the file's name says. **A key is the ledger's `NEC1`: the address `877C` is the device 0x87 and the subdevice 0x7C, the data byte the function**; the label is the function as written (`Zone Tune Up`, `DB (XM or DAB)`, `Dolby Pro Logic IIx - Movie`); the `Comment` column is empty in every row. **The carrier is NEC's 38 kHz, which the tables do not state**; `rl build` warns `carrier-off-nominal` (D32) for each file, as it does for KEF's (the ledger's NEC1 is nominally 38.4 kHz), and `build/warnings.json` has them. **A file is a table**: `T175`, `T765`, `T775`, `T785`, `T187`, `T777`, `T787`, and the T775's older table, of 5 February 2008, kept as `T775 (February 2008 table)` (the owner's rule: a second table of a model is kept, not skipped). **They are the codes of IR channel 0, the default**, which is standard NEC; NAD's `IR Channels` note (11 December 2007, in the zips, not pinned) says that a channel above 0 is not standard NEC and that the Txx5 receivers' Zone 3 and Zone 4 answer only to channels 3 and 4 and Zone 2 to the discrete commands and channel 2: **the tables list none of those**.

### D284 — The rule checked

**The tables are nearly one table.** The T187, T777, T785, T787 and the T775's table of March have the same 100 codes under the same names; **the T175 lacks `Speaker A` and `Speaker B` (98) and the T765 lacks `Audyssey` (99)**; the T775's older table has the same 100 codes under abbreviated names (`Vol +`, `Preset -`, `L Nite`, `Up`: 21 of the 100 differ), and nothing in the 797 rows is a different code for the same function. **Against other sources: the 100 distinct codes (NEC1 device 0x87, subdevice 0x7C) are held by other sources' NEC1 remotes in 58 cases** (15 remotes, all the IR Blaster database's NAD ones, which have the same address pair), **and in 43 of them some other source's label is the same function by the vocabulary or the spelling**; the 15 that the comparison does not count are mostly the same functions in other words (`Tuner 1` to `6` are those remotes' `1` to `6`, `A/V Preset 1` is `AV PRESET`, `Tone Defeat` is `TONE DFT.`) and two are not obviously the same (`Audyssey` is `BASS` or `EQ` in the remotes that hold it, `Delay` is `0`). **42 codes are nobody else's**: the 24 Zone commands (`Zone Power On`, `Zone Tuner`, `Zone Video 1 (DVD)`...), `A/V Preset 2` to `5`, the surround modes (`Dolby Pro Logic IIx - Movie`, `DTS NEO:6 - Cinema`, `Matrix 7.1`, `EARS`, `Enhanced Stereo 1`...), `Bypass`, `Direct`, `Stereo Downmix`, `Search Up` and `Down` and `DB (XM or DAB)`, which suits receivers later than the remotes the IR Blaster database has.

### D285 — What it does to the rest, and what is not proven

The corpus is **16,864 remote files and 693,992 keys**, 5,465 brands (NAD was one), 282,335 models (15 more) and 180,718 signals (100 more: the other sources' NAD NEC1 remotes are at 38.4 kHz, the IR Blaster database's carrier for NEC, and these are at 38 kHz as the other manufacturers' NEC tables are, so a code that both hold is two signals). Both bundle profiles are 59,916,288 bytes (45,056 more), dataVersion `cdf93eb87fea`, `--verify` passes. **187 of the 797 keys map to the vocabulary (23.5%)**: of a table's 100 keys 23 do (the four cursor keys, `Enter`, `Display`, `Dimmer`, `Mute`, `On`, `Off`, `Power Toggle`, `Volume Up` and `Down`, `Preset Up` and `Down`, `Tune Up` and `Down`, `Tuner`, `Sleep`, `Surround Mode` and `Input 1` to `3`) and the digits are absent (the tables have `Tuner 1` to `9`, which other sources' remotes call `1` to `9`), while the surround modes, the presets, `AM`, `FM`, the zone commands and `Input 4` to `7` do not (D84's aliases, the owner's call); the `official` row goes from 14.2% to 14.3% and the corpus from 71.6% to 71.5%. **All eight files have a key the app's test order can pick** (15,202 of 16,507 remotes can be tried). **The search evaluation: first answer 87.8% to 88.8%, first five 97.8% to 98.8%**, the 33rd draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the four misses involves a NAD model. **Not proven:** that a T-series unit acts on a code its table lists (15 remotes agree on the codes they hold, which is the evidence); which of the seven models has which function (the 2012 tables of three different units are one table); anything about IR channels above 0; and the 17 Crestron `.ir` files and the two Pronto `.ccf` files in NAD's other downloads, which have the older products' codes and are not read here (TODO).

## 73. NAD's Crestron IR files for its older products

NAD's software page has `NAD-CrestronIR.zip`: **16 binary `.ir` files of January 2005**, Crestron's IR driver format, and a `readme.pdf` that says which file is for which model (`Creston IR files for NAD`: receivers and preamps, DVD and CD players, a tuner, a tape deck and televisions). **They are read: 16 files, 645 keys, as 34 models, 34 files and 1,499 keys, all Plausible.** The format is not documented anywhere I found, so **it was worked out from the files (D287)**. The manufacturers' tables (§29 and the sections after it) are now 520 files and 46,071 keys, of which NAD's are 42 and 2,296.

### D286 — The documents, how they are pinned, and the licence

The zip is on the Shopify CDN that serves nadelectronics.com's files (58,241 bytes), fetched on 2026-10-14 and pinned as D123 does it: **each of the 16 `.ir` files (1,050 to 6,987 bytes) and the readme (48,609 bytes; January 20, 2005, made by a Ghostscript, author `KG`) is a member of the zip kept as the manifest says** (`crestron_cd1.ir`, the member `cd1.ir`...). The `.ir` files are not text, so **`tools/official_sheets_to_csv.py`'s new mode `records` writes only their framing** (`number, tag, body`, the body in hexadecimal; a record that does not fit the file, or a file that does not end with one zero byte, is an error) and the importer decodes the records; the readme is read as its lines (`pages`). **Licence:** the readme has no copyright, rights or reproduction text, and the `.ir` files say only `IR`, `Generated NAD remote codes`, `012005` and `NAD` in their headers; by the rule stated in PR #42 they are public under D110. **Not read:** the zip's neighbours on that page, `NADRCVRandDVD.ccf` and `NAD_C420.ccf` (the Pronto editor's binary files; TODO) and `NADToPronto.exe`.

### D287 — The format as it was worked out, and the rule

A file is **records**: `length`, `tag`, `length - 2` bytes (the length counts itself and the tag), and one zero byte at the end. **The header** is the tags `f0` to `f7`: `IR`, `Generated NAD remote codes`, `012005`, `NAD`, **the models** (`5170, 5240, 5340`, or the kind of product where the readme says `All Others`), `<none>`, two bytes (`04 32` in all 16, not understood) and the kind of product (`CD`, `DVD`, `PROCESSOR`, `RECEIVER`, `DUAL CASS`, `AM/FM TUNER`, `TV TUNER`). **The key names** follow, one record each, the tag its number (`01` `+10`, `02` `1`...), then a record `ff` and **one record for each key with its signal, under the same tag**. A signal is **a head, 32 data bytes that are 0 or 1, and a tail**; **there are two heads, and all 645 signals are one of them**: `69 22 02 07 00 15 00 40 00 56 00 ab 01 57 05 f1 0e 50 43` (the numbers after the first two bytes, read as shorts, are 21, 64, 86, 171 and 343: **carrier cycles at 38 kHz, 0.55 ms, 1.7 ms, 2.3 ms, 4.5 ms and 9.0 ms, which are NEC's marks, spaces, repeat lead, lead space and lead mark**, and the first byte `0x69` = 105 looks like 4 MHz over 38.1 kHz, which is a guess) with the tail `05 42 06` in 14 files, and `69 22 03 05 00 15 00 40 00 ab 06 9c 0e 7b 22` (21, 64, 171, 1,692 and 3,707: **no 9 ms, a 4.5 ms mark and a 4.5 ms space**) with the tail `03 22 00 04` in two, the C660's and the T5xx DVD players'. **The first is NEC and the second is the ledger's `NECx1`** (NEC's bits with an 8-unit lead-in, the frame that Samsung's remotes use with the same byte for both halves of the address). **The 32 bits go least significant first**, four bytes: **the device, the subdevice, the function and its complement**; **the complement must be the function's, and it is in all 645 keys**; a key that is neither head or has no name is in the report (none is). **A key is the ledger's `NEC1` or `NECx1`** (device and subdevice written, as KEF's 8-bit addresses are, §62); the carrier is 38 kHz; the label is the key's name as the file writes it (`A_DISC`, `BASS+`, `+10`, `CENTR-`: two keys of a file may have one name, as the receivers' `MONO`, and are told apart by the key name only); the citation names the file, the key's number and name, the record, the four bytes of the frame and what the readme says of the file. **A file is a model**: the readme's list is the header's (a different list is an error), each model of a list gets a file of its own with the same keys (the ten T5xx DVD players are ten files), **`All Others` is the header's kind of product with `(all others)`** (`Receiver, Amplifier (all others)`, `CD Player (all others)`, `Tuner (all others)`, `Tape Deck (all others)`) and `Second Zone Commands` is the header's `Receiver, Amplifier (Second Zone)`. **Which bytes I could not account for:** the second byte of the head (`0x22`), the `07`, `05` and `0e` of the two heads, the `05 f1 0e 50 43` (the `PC` is its ASCII), the tails and the header's `04 32`; **they are the same in every key of every file**, so they are not what tells one key from another, but nothing says what they do.

### D288 — The rule checked

- **The bit order.** The receivers, the CD player, the tape deck and the tuner of the generic files all decode to **device 0x87, subdevice 0x7C**, the address of every NAD remote in the T-series tables (§72) and in the IR Blaster database; read most significant bit first they would be 0xE1 and 0x3E.
- **Against NAD's own T-series table.** **The five generic files have 196 distinct codes at 0x87/0x7C, and 98 of them are in the T187's table; 40 of the 98 are named alike by the spelling or the vocabulary, and the other 58 are the same functions in older words** (`CENTR-` is `Center Volume Down`, `NEO6_CINEMA` is `DTS NEO:6 - Cinema`, `PSET1` is `A/V Preset 1`, `SURR_EX` is `Dolby Surround EX`, `1` to `9` are `Tuner 1` to `9`), **except three whose function was renamed between 2005 and 2008: `HALL` (0xC9) is the T187's `Audyssey`, `CD` (0x85) its `Front` and `PHONO` (0x89) its `MP`**: the same code, a name that moved with the product.
- **Against other sources' remotes.** By file, the codes another source's remote holds: **the T5xx DVD players' (`NECx1`, 0x2D/0x2D) 47 of 47 in 59 remotes, the L55's (0x85/0x9A) 49 of 50, the L75's 43 of 43 and the L70's 76 of 76 (0x85/0x6A and 0x85/0x9A), the generic CD player's 38 of 46, the tuner's 24 of 28, the tape deck's 13 of 15, the receivers' 71 of 109, the 5170's (0x2A/0xD5) 21 of 34, the MR13 television's (0x40/0xBF) 28 of 28 and the MR20's (0x04/0xFB) 18 of 18; and none of the 5060's (0x85/0x6F), the 5325's (0x87/0x7E), the C660's (0x2F/0x2F), the 118's (0x86/0x0F) nor the second-zone commands but three.** The T5xx DVD players are `NECx1` at an address that 59 other remotes use, which suggests the Samsung family: that is a guess and the ledger records no brand for it. **Four models are nobody else's** and have this file only.

### D289 — What it does to the rest, and what is not proven

The corpus is **16,898 remote files and 695,491 keys** (34 and 1,499 more), 5,465 brands, 282,398 models (63 more) and 181,071 signals (353 more). Both bundle profiles are 60,022,784 bytes (106,496 more), dataVersion `48c5d56ecf8b`, `--verify` passes. **892 of the 1,499 keys map to the vocabulary (59.5%)**: the digits, `POWER`, `MUTE`, the cursor keys, `PLAY`, `STOP`, `PAUSE`, `REPEAT`, `RANDOM`, `ENTER`, `DISPLAY` and the like do, and the surround-mode abbreviations and the tuner and level keys (`PLII_MOVIE`, `SPKR_A`, `PSET1`, `CENTR+`) do not (D84's aliases, the owner's call); the `official` row goes from 14.3% to 15.8% and the corpus stays at 71.5%. **30 of the 34 files have a key the app's test order can pick**: the 5060, the 5325, the second-zone commands and the tape deck have none (15,232 of 16,541 remotes can be tried). **The search evaluation: first answer 88.8% to 87.8%, first five 98.8% to 97.5%**, the 34th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the eight misses involves a NAD model. **Not proven:** that the format is read right beyond the evidence above (a Crestron driver was not run on these files, and the bytes in D287 that I did not account for are unexplained); that a NAD product acts on a code its file lists; that the readme's model lists are complete (it says `current NAD model` in 2005); and what the second-zone file's three codes that other sources hold mean.

## 74. Arcam's older products: two IR sheets and nine more documents

Arcam's site still serves, beside the custom installation notes of §64, **two three-page sheets of IR codes for its DiVA and FMJ amplifiers and CD players** (issue B, made in 2006), **eight more serial control notes of its older products, each ending with its RC-5 table** (the AVR500, AVR600 and AV888; the AVR400; the AVR390, AVR550, AVR850, AV860 and SR250; the BDP100 and BDP300; the CDS27; the D33; the ST60; the Solo Movie, Movie 2.1 and Music 2) **and a one-page table of the Solo bar's codes**. **They are read: 11 documents, 34 new files and 2,186 keys, 2,173 Plausible and 13 Untested**; Arcam now has 53 files and 4,002 keys in the ledger. §64 had left "Arcam's older products" for later and had not used the AVR390 family's notes, because only a retailer's copy was found; Arcam's own is on its site. The manufacturers' tables (§29 and the sections after it) are now 554 files and 48,257 keys.

### D290 — The documents, how they are pinned, and the licence

Eleven documents on Arcam's own host, `arcam.co.uk`, fetched on **2026-10-15**, two days after §64's eight, so **each has a `retrieved` of its own in the manifest** (`tools/fetch_official.py --only`, which leaves the others and the maker's date as they are) and the import report says "arcam 2026-10-13 (and 2026-10-15 for 11 documents)". Pinned as D123 does it:

- **The sheets**, `Infrared codes for Arcam amplifiers` (`amps_rc.pdf`, 822,415 bytes) and `... CD players` (`cd_rc.pdf`, 1,302,213 bytes): 3 pages each, "Custom Installation Notes", issue B, PDFs made on 6 September 2006 and changed on 15 December 2008 (the server's `Last-Modified` is 10 March 2015). They are on the product pages of the A18, A28 and A38 and of the CD17 and CD37 (hence the pinned names), **but each cover lists the models it covers, and those are the files**: the DiVA A65 Plus, A70, A80 and A90 and the FMJ A18, A28, A32, A38, C30 and C31 (ten amplifiers); the DiVA CD73 and CD192 and the FMJ CD17, CD36 and CD37 (five CD players). The PDFs' own keywords list fewer (`DiVA A65PLUS A80 fmj C31 A38 A32`; `DiVA CD73 CD192 fmj CD36`); the cover is what a reader sees and is used.
- **The notes** (each a "Custom Installation Notes: Serial programming interface and IR remote commands"): the **AVR500/AVR600/AV888** (SH217, issue 7.0, May 2012, 30 pages, 733,681 bytes; the changelog says issue 2.0 included the AV888 and issue 3.0 changed the text to refer to the AVR500), the **AVR400** (SH235, issue 1.2, June 2011, 30 pages, 445,202 bytes; Arcam's AVR360 page serves the same file, which names the AVR400 only), the **AVR390/AVR550/AVR850/AV860/SR250** (SH274E, issue D, 18 October 2018, 28 pages, 595,993 bytes), the **BDP100/BDP300** (SH231, issue 2: "BDP300 added", 10 pages, 337,236 bytes), the **CDS27** (SH272, issue 1, PDF of 7 August 2014, 6 pages, 209,610 bytes), the **D33** (SH236, issue D, February 2012, 8 pages, 339,678 bytes), the **ST60** (SH309, issue C, PDF of 11 November 2020, 20 pages, 215,337 bytes) and the **Solo Movie, Movie 2.1 and Music 2** (SH284E, issue A, PDF of 13 January 2016, 12 pages, 586,856 bytes).
- **The Solo bar's page** (`Solo_bar_IR_codes_V2.0.pdf`, PDF of 1 March 2016, 1 page, 73,795 bytes): headed `Solo bar IR codes`, a PDF title of `Solo bar software release notes`, two tables.

**The conversion.** The eight notes are converted as §64's are (`tables`, the cells the PDF library finds). **The sheets and the Solo bar's page have no ruling lines, so `tools/official_sheets_to_csv.py` has a new mode, `lines`**: each line of text is a cell (`page, y, x, text`), the lines within 2 points of one `y` are one row, and the spans of a line are one cell, in the order written, so that a name set in two fonts stays one name; **a line set wholly in italics is written `<i>text</i>`**, because the CD sheet says what its italics mean (D291). The mode is new, and no pinned CSV of another maker changes.

**Licence.** The eleven were searched for `copyright`, `©`, `(c)`, `all rights`, `reproduc`, `confidential`, `proprietary`, `licen[cs]e` and `trademark`: **none has any of them** (the word "Reserved" is in the notes' tables of reserved command bytes and nowhere else). By the rule of D110 they are public. **Not used:** the Radia Remote documents (issue 3, for the A5, A15 and A25, the Plus models, the CD5 and the ST5) say "© 2025 HARMAN International Industries, Incorporated. All rights reserved." and are for the private repository, a later step. **Not read, because there is no table to read:** the PA720, PA240 and PA410's, the P429's and the MZ8 and MZ12's notes (serial commands only), the CR402 remote's manual, the DV139's `dvd_rc.pdf` (the server's 404 page, live and in the Wayback Machine's copies) and the DV79's and A75's `dvd_rc.pdf` and `amps_rc.pdf` (FAQ documents under those names). **Not read, and a table is in them:** the Solo Movie 2.1 and 5.1's notes of 2008 (SH194, issue 3), whose IR tables are one code and one name to a line over several pages (§64's Solo table is the Solo Movie 5.1's page), and the superseded issues (the AVR600's 6.0, the AVR550/850/250's A and B).

### D291 — The rule

**The notes** are §64's rule (D252), unchanged: a code is the ledger's `RC5` (the system code the device, the command the function), a document's models are the ones its title lists and **one file each**, a label is the function as the table writes it, the decimal and the hex of each row are both read and must agree, and the tables' `Basic` and `Advanced` split (on the supplied remote, and created for custom installation) is not kept. Specific to these: the AVR500, AVR600 and AV888 have one table (171 keys each), the AVR390, AVR550, AVR850, AV860 and SR250 one (116 keys each), the BDP100 and BDP300 one (51), and the Solo Movie, Movie 2.1 and Music 2 one (83): **a title that lists models gives each the document's whole table**, as D252 does. The Solo Movie names are the title's, `Solo Movie, Movie 2.1 & Music 2`, read as `Solo Movie`, `Solo Movie 2.1` and `Solo Music 2`: the document does not say that the first of them is not the `Movie 2.1`'s sibling under another name, and the Arcam pages that host it are `solomovie`, `solomovie2` and `solomusic-cs`. A function written `(3D only)` (the AVR500/600/AV888's video bypass keys) keeps it in the label.

**The sheets** are tables of `Command | Decimal Code`, one or two side by side on a page under a heading (`Power commands`), the code written as `16–12` with an en dash, **the system code stated once on the first page (`The system code is 16`, `... is 20`)**. A row is a name and the code in the cell after it; the left table of a page comes before the right one. **A row whose system code is not the stated one is Untested** (none is), **and so are two rows with one code** (the CD sheet's `Scan 30 seconds` and `Scan off`, both 20-90: one of the two is wrong or the code does two things, and nothing is guessed). `Track number 'n', (e.g., 1)` is the digits 1 to 9, `20-n`, as the document says (`Track 1` ... `Track 9`; its example is 20-1 and it gives no n of 0). Three sentences of the sheets apply to a table or a row, and each is in the citation:

- **`The commands are accepted by the A90 and P38 Power amplifier.`** stands above the amplifier sheet's `Speaker control commands` table (six keys: `Speaker 1 toggle`, `Speaker 2 toggle`, `Speaker 1 on` and `off`, `Speaker 2 on` and `off`, 16-35, 39, 43, 44, 45, 46). **Those six keys are for the A90 and the P38 only**, which the table's place makes plain and the cover does not (the P38 is not on it): the A90 has 26 keys, **the P38 has the six and no other**, and the other nine amplifiers 20 each.
- **`Note that Arcam amplifers also responds to code 20–53 (the PLAY command for an Arcam CD player). Amplifiers switch automatically to CD input on receiving this command.`**: one more key in each amplifier's file, `Play (the CD player's command)`, system 20 command 53.
- **`Items shown in italics reference the buttons on the supplied CR-314 remote control. The items not in italics are additional commands accepted by the CD player.`** Each of the CD files' 51 keys says which it is: **18 are set in italics, buttons of the supplied CR-314** (`Power toggle`, `Track number 'n'` (nine keys), `Track selection 10+`, `Time`, `Track forward`, `Repeat toggle`, `Repeat A-B`, `Programme`, `Shuffle`, `Display toggle`) and **33 are additional commands** that a custom installation can send and the remote does not. The amplifier sheet has no such sentence and its rows say nothing of the kind.

**Not made: files for system code 12.** The sheets' third page says Arcam equipment "has a hidden menu that allows for the standard RC5 system code (i.e. 16) to be changed to system code 12", to avoid clashes with other makers' components, and the amplifier sheet gives the way in (setup, 6-3-1-8) and an example (`Play` is 12–53 and not 20–53). Every file here is the sheet's standard code; a unit set to 12 answers the same commands at system 12, which is a setting and not a table, and the sheet's example gives the CD player's code once as 16–53 under `Original system code 20`, a slip of its own (its table has 20–53).

### D292 — The rule checked

- **The documents against themselves.** The eight notes' 672 rows each say their code twice, and **671 of 672 agree**: the AVR500, AVR600 and AV888's `Zone 3 Vol+` is `23-20` and `0x17 - 0x17` (decimal 20 is 0x14), so it is Untested in the three files and says so. The sheets' rows (26 and 51 as the importer counts them, the digits as nine) have a decimal only; the Solo bar's 24 have a hex too, written without `0x` (`13-0C`), and **all 24 agree with their decimal**. **Three tables have two names for one code**: the AVR390 family's `HOME` and `Yellow` (16-43) and `Cycle between VFD information panels` and `Blue` (16-55), and the BDP's `ZOOM` and `Zoom` (25-68): a function and a colour key or a spelling, both kept, both Plausible.
- **The sheets against the notes of the same years.** **The A90's 25 system-16 codes against the AVR600's table: 16 are the same function** (the eight source selects as `Select Phono`... `Select Aux`, `Standby`, `Mute`, volume up and down, the display, `OK`, `Power On` and `Power Off`) **and 9 are not: the menu keys `Up` (16-32), `Down` (16-33) and `Select` (16-37) are the AVR600's `MODE`, `Display L1` and `Access Speaker Trim controls`, and the six speaker keys are its `Display L2`, `Access Bass control`, `HOME / yellow`, `Bass +1`, `Bass -1` and `Treble +1`**: two generations of one system code, as §64 found for command 53 (D252). The AVR400's table agrees with the AVR600's on those 25 codes in 23 (it has `Select BD` and `Select Display` where the AVR600 has `Select DVD` and `Select Tape`). **The CD17's 25 codes that the CDS27's table also has are the same function in all 25** (the nine digits, `Standby`, `Display`, `Repeat`, the track keys, `Eject`, `Pause`, the scan keys, `Play`, `Stop`, `Shuffle`, `Search`, `Power On` and `Power Off`; `Time` is the CDS27's `MODE (INFO)`, a display function, which is the nearest the comparison gets). The CD sheet's `Scan 30 seconds` (20-90) is the CDS50's `DISC input` and its `Programme off` (20-100) the CDS50's `COAX input`: a different product with inputs, and nothing is concluded from it.
- **The notes against §64's tables.** **The AVR390 family's 114 distinct codes: 113 are codes of the earlier files, 109 named alike; the BDP's 50: 50 of 50, 50 alike (it is the UDP411's table); the CDS27's 36: 34, 30 alike; the Solo Movie's 83: 80, 52 alike; the AVR500/600/AV888's 171: 152, 83 alike; the AVR400's 153: 144, 91 alike.** What is new is the **systems**: the D33's is 27 (0x1B), the ST60's 21 (0x15) and the Solo bar's 19 (0x13), which no earlier Arcam file has.
- **Other sources' Arcam remotes.** The ledger's eight RC-5 remotes of Arcam's (D253) hold 401 keys, **361 of them (90.0%) a code some Arcam table now lists, up from 337 (84.0%)**; the 53 files hold 399 distinct codes (272 in §64's 19). **Other sources' RC-5 remotes of any brand hold 20 of the A18's 20 codes, 39 of the CD sheet's 50, 160 of the AVR500/600/AV888's 171 and all 19 of the D33's, 33 of the ST60's 43 and 18 of the Solo bar's 24, and name the same function for 10, 14, 11, 1, 16 and 3 of them**: system codes 16, 17, 20, 19, 21 and 27 are used by many makers, so the codes being held says little, and the nine digits of the CD sheet are the digit keys of 30 to 32 other remotes each (`1` to `9`), which is the one plain agreement. **The signal table gains 25** for the 127 codes that Arcam's files did not have before: nearly every code was in the ledger already, and what the tables add is who it is for and what it is called.

### D293 — What it does to the rest, and what is not proven

The corpus is **16,932 remote files and 697,677 keys** (34 and 2,186 more), 5,465 brands (Arcam was one), 282,465 models (67 more) and 181,096 signals (25 more). Both bundle profiles are 60,088,320 bytes (65,536 more), dataVersion `5f8c349c50a7`, `--verify` passes. **642 of the 2,186 keys map to the vocabulary (29.4%)**: the digits, `Standby`, `Mute`, `Pause`, `Play`, `Stop` and `Menu` do, and `Increase volume (+)`, `Speaker 1 toggle`, `Fast forward`, `Select HDMI1` and `Zone 3 Vol+` do not (D84's aliases, the owner's call); the `official` row goes from 15.8% to 16.4% and the corpus from 71.5% to 71.4%. **33 of the 34 files have a key the app's test order can pick**: the P38's six speaker keys are not one. **The search evaluation: first answer 87.8% to 88.4%, first five 97.5% to 98.1%**, the 35th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the six misses involves an Arcam model. **Not proven:** that a product accepts a code its table lists; that the covers' lists are the products' (the PDFs' keywords list fewer, and a title that lists several models gives each the same keys); that **the P38 is the product the sentence above the speaker table means** (it is a power amplifier of Arcam's, not on the cover, and the table has no heading of its own); that the Solo Movie, Movie 2.1 and Music 2 are three products with the table in common and not two (D291); that the amplifiers' menu keys (`Up`, `Down`, `Select`) and the speaker keys do what the 2006 sheet says on units whose later tables give those codes other functions; which of `Scan 30 seconds` and `Scan off` is 20-90; the unit's behaviour at system code 12 beyond the sheets' two sentences. **Next from the same site:** the Radia Remote documents (private repository), and the Solo Movie 2.1 and 5.1's 2008 notes.

## 75. InFocus's IR code PDFs for its projectors

InFocus (the projector maker, whose site no longer serves these files) published for each family of projectors a short PDF of its remote's key codes: `The Work Big IN72, IN74, IN76 uses the NEC IR protocol. The customer code portion of the IR packet should use the hexadecimal value 0x4E87`, and a table of key names with the data code of each. **They are read: 29 documents, 74 files and 3,297 keys, 2,808 Plausible and 489 Untested.** InFocus had 19 remotes in the ledger (IR Blaster, JP1 and LIRC), 9 of them at the address these documents give. The manufacturers' tables (§29 and the sections after it) are now 628 files and 51,554 keys.

### D294 — The documents, how they are pinned, and the licence

**InFocus's own site no longer has them**, so the documents are the Wayback Machine's copies (as Oppo's, D131), each the snapshot named in its URL (`id_`, the raw bytes), the manifest recording the address it was archived from and the snapshot: **29 PDFs of 10 to 193 KB, 43 pages, made between 2002 and 2016 and archived between 2004 and 2017 (24 of them on 5 July 2014)**, fetched on 2026-10-15 (`tools/fetch_official.py infocus`, which now waits and asks again when the archive answers 429 or 5xx). They are on `infocus.com`'s product-support pages, and **a file is often the same on several: 10 more copies of the pinned bytes are on other pages and not pinned** (the LP540's and LP640's pages have the file pinned as `540-640-IR_codes.pdf`, the IN70's has the IN7x's, the IN34's the IN32's, the IN5310HD's the IN5310's, the IN5532/IN5534's and IN5533/IN5535's the IN5500's, the LP820's the LP815's, the LP850's and LP860's the LP840's). The sheets' tables are converted by the `lines` mode D290 added (one cell for each line, by its place), except the IN5310's, whose table is turned on its side and is read from its text (`pages`). **Licence:** the 29 have no copyright, rights, reproduction, licence or trademark text (searched as D290 did): public under D110. **Not pinned, and why:** the IN80's (`© 2007 InFocus Corporation`) and the JTouch's (`©2014 InFocus Corporation. All rights reserved`) are for the private repository (TODO); the LP500/LP530's address answers the archive's copy of the site's "page does not exist"; **the tables of the IN110's, the IN3114's (the IN2112 to IN3116), the IN3910's, the IN3118HD's and the SP8604's are images** (the first three have a decimal, a hex and a binary column that would check a hand reading: TODO); the IN5550's two tables of NEC codes state no customer code, the IN3900LB's is a keycode specification (rev G) in a layout of its own, the LP800's and LP810's two-digit codes state no protocol, and two Mondopad PDFs hold the same 22 rows as the one that names the models.

### D295 — The rule

**A key is the ledger's `NEC1`: the customer code `0x4E87` is the device 0x87 and the subdevice 0x4E (the low byte is sent first), the data code the function**, at 38 kHz as the other manufacturers' NEC tables are (the other sources' InFocus remotes are at 38.4 kHz, so a code both hold is two signals). **The customer code is read from each document's own sentence** (`The customer code portion of the IR packet should use the hexadecimal value 0x4E87`, `Device ID:0x4E87`, `Remote Key Customer Code: 0x0080`, or the IN514x's `CUSTOMER` column) and two in one document stop the import. **Three differ: 0x4E87 (16 documents), 0x83F4 (the IN100's) and 0x0080 (the Mondopad's); 11 state none.** A model is each the document's title lists (the IN1100's eight, the IN3100's twelve, the IN5500's eight), **one file each**, and where a document names none (the IN2100's, IN32's, IN5310's, LP650's...) or a family (`IN514x-series`, `P99 family`) the model is the product page that serves it, which the citation says. A label is the table's name for the key; a key name that is an arrow (`^`, `v`, `<`, `>` in the IN5310's) is `Up`, `Down`, `Left`, `Right` and the citation says what the table writes. A model that two documents list (the IN72, IN74 and IN76: the 2006 `IN2x` and `IN7x` sheets) has a file for each: the second is `<model> (IN7x_IR_Codes.pdf)`; and two names other sources already have (the IN72 and SP4805) are kept as `<model> (InFocus table)` (R15).

**The layouts** are seven. `name_code` (`Menu  0x02`), `name_desc_code` (`Menu  Toggles menu state  02h`), `code_name` (`0x02  Menu toggle`) and `keyno` (`K01  STANDBY  NEC  4E87  39`) are plain; **`hex16`** (the LP240's, LP540's, LP815's, LP840's and SP5000's `POWER  0xE817`) gives the code twice, **the data code and its bit reverse (0xE817 is 0x17, 0x1008 is 0x08), which the reader checks and which holds in all 107 rows**; **`dec_hex_bin`** (the IN2100's and IN5148HD's) gives the decimal, the hex and the binary of each code, **which must agree**; `numbered` is the IN5310's. **Tables whose rows are not on one line** (the IN1100's, IN20's, IN2100's and IN3100's last page: a code a point or two above or below its name, a name that goes on in the next line, `Discrete Resize` / `Letterbox`) are read by `banded`: a cell is a code's when it is within 3 points of the code's line, and a name cell near no code is the continuation of the name above it (within 20 points). The reader is checked against the PDFs by counting: **every code-like cell of each document is a row of it** (the first reading missed 12 `Discrete ...` rows of the IN1100's, 12 of the IN3100's, 2 of the IN20's and 5 of the IN2100's, and the count found them).

**Untested are 489 keys, for four reasons the citation gives.** (1) **A document that states no customer code** (11: the IN2100's, LP650's, SP4800's, SP4805's, X2's and X3's, and the five `hex16` ones) **is read with 0x4E87 and each key is Untested**: its codes are those of the same tables of the same years (the LP650's `Power` is 0x17 as in the LP120's, and each `hex16` row's low byte is a code of the 0x4E87 tables) and 0x4E87 is the one that every InFocus document stating a customer code gives but two; nothing in the document says so, and the report says it once for each. (2) **The IN100's 13 keys**: its customer code 0x83F4, read low byte first as 0x4E87 is, is device 0xF4 and subdevice 0x83, and **the IR Blaster database's InFocus remote that has exactly these 13 functions (Menu 0x0F, Source 0x04, Power 0x02, Blank 0x07...) has the bytes the other way round, device 0x83 and subdevice 0xF4**. One of the two is the other order, and nothing here says which. (3) A row whose decimal, hex and binary say two codes: the IN5148HD's `Computer` is `25 / 19h / 1000 1001` (the binary is 0x89). (4) **A code that the table gives to two functions**: the LP840's `Effect` and `Source 1` are both 0xD42B, where every other table has `Source1` at 0xB42D. Rows that are twice in one table with one name (`Auto_Image`, `Presets` in the five `hex16` tables) are one key; the IN5310's `Light` has no code (`n/a`) and is no key.

### D296 — The rule checked

- **The documents against the other sources.** **The ledger's nine InFocus remotes at 0x4E87 (six of the IR Blaster database's and three JP1 upgrades: the SP4805, the IN3138HD/IN8606HD and the IN72) hold 219 keys, and 215 of them (98.2%) are a code some table lists**; of the 106 codes the tables give at 0x4E87, 53 are held by those remotes, and 43 of the 53 are named alike by the vocabulary or the spelling. The IR Blaster's remote with 31 keys (1663) has every one in the tables. The IN72's JP1 upgrade has the 32 of them.
- **The documents against each other.** The 106 codes at 0x4E87 are the same function across the generations (`Power` 0x17, `Menu` 0x02, `Source` 0x10, volume 0x08 and 0x04, the cursors 0x13, 0x14, 0x11 and 0x12, the sources 0x2D to 0x30): **78 codes are in two or more tables, 60 of them under different spellings** (`Volume Plus`, `Increase volume`, `VOLUME_UP`, `Volume +`) **and 18 under one**. **Where the tables really differ it is in a few codes: 0x2B is `Effect` in 15 tables, `Custom` in 4 and `MY BUTTON 1` in the IN514x's (a programmable key), and 0x24 and 0x25 are `Next Slide` or `Next` and `Previous Slide` or `Prev` in the 17 tables that have them (the LP650 says they send PageUp and PageDn) and `Page Up` and `Page Down` in the IN5148HD's, but `PAGE DOWN` and `PAGE UP` in the IN514x's: its two names are the other way round against every other table, and the keys say so (the table is kept as it reads).**
- **The hex16 tables against the 0x4E87 ones.** Their data codes are the 0x4E87 tables' of the same years under shorter names (`VOLUME_UP` 0x08, `MUTE` 0x29, `EFFECT` 0x2B, `SOURCE1` 0x2D), and in all 107 rows the high byte is the bit reverse of the low: a table that gave a customer code of its own would be the proof, and none does.
- **Not proven:** that the 11 documents without a customer code are for the address they are read with (the evidence is the codes themselves); which of the two byte orders is the IN100's; that a PDF archived in 2014 says what the product does.

### D297 — What it does to the rest, and what is not proven

The corpus is **17,006 remote files and 700,974 keys** (74 and 3,297 more), 5,465 brands (InFocus was one), 282,565 models (100 more) and 181,237 signals (141 more: the 141 distinct codes are all signals the ledger lacked, since the other sources' InFocus remotes are at 38.4 kHz and these tables' at 38). Both bundle profiles are 60,223,488 bytes (135,168 more), dataVersion `516920ee30b7`, `--verify` passes. **1,269 of the 3,297 keys map to the vocabulary (38.5%)**: `Menu`, `Power`, `Standby`, `Blank`, `Freeze`, `Source Select`, the cursor keys, brightness, contrast and keystone plus and minus do, and `Select`, `Resize`, `Overscan`, `Presets`, `Auto Image`, `Source1` to `Source4`, `Effect`, `Computer Toggle` and `Next Slide` do not (D84's aliases, the owner's call); the `official` row goes from 16.4% to 17.8% and the corpus from 71.4% to 71.2%. **73 of the 74 files have a key the app's test order can pick**: the LS110's names (`Soft Power toggle`, `Audio Mute toggle`) are not the vocabulary's. **The search evaluation: first answer 88.4% to 90.6%, first five 98.1% to 98.4%**, the 36th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the five misses involves an InFocus model. **Not proven:** that PDFs archived between 2004 and 2017 say what the products do now; that the 11 documents without a customer code are for the address they are read with, which the codes support and no sentence does; **which byte order the IN100's customer code is in** and whether the Mondopad's `0x0080` is device 0x80 and subdevice 0x00 or the other way (it is read as the others are); that a model a document does not name (the IN5140, whose table says `IN514x-series`, the IN32 and IN34, the LP815 and LP820) is the product the page it is served on is; that the IN72, IN74 and IN76 are the same products in the 2006 `IN2x` sheet and the `IN7x` one (the two have the same models and different tables, 41 keys and 32, and both are kept). **Not read:** the image tables, the IN5550's, the IN3900LB's, the LP800's and LP810's, and the IN80's and JTouch's tables, which carry a copyright notice (D294, TODO).

## 76. InFocus's tables that are images

Four of the InFocus PDFs §75 left unread have their key table as a picture, not as text: **the IN110's (IN112, IN114, IN116), the IN3114's (IN2112, IN2114, IN2116, IN3114, IN3116), the IN3910's (IN3914, IN3916) and the IN3118HD's**. **They are read, by eye: 4 documents, 11 files and 509 keys, all Plausible.** The text of each says the customer code (0x4E87); the table is a PNG or a JPEG in the PDF. The manufacturers' tables (§29 and the sections after it) are now 639 files and 52,063 keys.

### D298 — The documents, and what "by eye" is here

The four are pinned as §75's are (Wayback copies, snapshots of 8 October 2013, 29 June 2014 and 5 July 2014, fetched on 2026-10-15; the IN3114's is also the file on the IN2110's and IN3110's pages), with the same licence finding (no copyright, rights or reproduction text) and the text layer converted by `lines` for the sentence that gives the customer code. **The table itself is an image**: the IN110's is one picture (790 x 1085), the IN3114's two (790 x 592 and 787 x 492, one table cut in two), the IN3910's one (793 x 1087, a JPEG), the IN3118HD's a JPEG of the remote with its code table beside it. **There is no tool for this, and the repository says so: the table was read by eye, by the assistant that wrote this section (Claude Code), from the images extracted from the PDFs at their own resolution and enlarged where a row was hard to make out, and written down as `sources/official/infocus/csv/<document>__transcribed.csv`** (`page, name, decimal, hex, binary` for the three 48-row tables; `page, key number, description, data code` for the IN3118HD's 30 rows). `tools/official_sheets_to_csv.py` keeps those files as they are (it stops if one is missing, and does not make it), the manifest pins each file's hash like any other sheet and says `transcribed`, and **every citation of such a row says that the table is an image and the row was read from it by eye**. The descriptions of the three tables (`Toggles menu state up or down`, `(not transmitted)`) are not transcribed, as the text tables' are not in the citation either.

**The SP8604's is not read**: its PDF is the page of a document made of 25 picture strips, one for each row of its code table, stacked in no reading order and overlapping, with the remote's photograph; and its customer code is 0x31CE, which none of the other documents has.

### D299 — The rule, and how a misreading would show

**The reader is the one for the text tables of the same kind** (D295): a key is `NEC1` with the customer code the text layer states, the data code its function, a label the table's name. **The three tables carry a decimal, a hex and a binary for each code, which must agree, and do in all 48 rows** (so a digit misread in one column fails against the other two); a row that did not agree would be Untested. **The three images are one table**: read one by one, the IN110's, the IN3114's two pictures and the IN3910's are the same 48 rows (Menu 2 to Discrete User Preset 3 66), and their three transcriptions are one file. **The names are checked against the text tables of the same family: all 48 codes are in the IN1100's, IN20's and IN3100's tables (59 rows each) under the same names (case and punctuation aside), and 46 in the IN5300's and IN5500's (which have no `Light` and no `Reset Bulb Counter`), with three names written otherwise (`New Mute` there `Mute`, `Effect` there `Custom`, `Discrete Resize Natural Wide` there `... Natural Wide/Panorama`)**: a misread name would show. **The IN3118HD's table has no key names, only a description of each key** (`Turns the projector on.`: the names are on the remote's picture beside it), and **it is the IN5310's `Code table (InFocus RC)`: each of its 29 codes is the IN5310's code of the same number** (the reader stops if one is not), so a row takes the IN5310's name for its number and says so, with the description as the document gives it; the 30th row, `LIGHT`, has no code and is no key.

**The comparison found a mistake of §75's, now corrected: ten of its files (the IN5302, IN5304 and the eight IN5500 models) had two labels wrong.** The IN5300's and IN5500's tables write `Discrete Resize Natural Wide/Panorama` over two lines with the code on the second, and the reader (`banded`, D295) gave the first line to the code above it: the 0x3E key was labelled `Discrete Resize Letterbox Discrete Resize Natural` and the 0x3F `Wide/Panorama`. **A name cell near no code now belongs to the nearer code within 20 points, the one above when they are as near** (the IN20's `Discrete Resize` / `Letterbox`, which goes on below its code, is the other case); the codes of the twenty keys were right and only their labels change.

### D300 — What it does to the rest, and what is not proven

The corpus is **17,017 remote files and 701,483 keys** (11 and 509 more), 5,465 brands, 282,576 models (11 more) and 181,237 signals (none more: every code was a signal already, §75's). Both bundle profiles are 60,239,872 bytes (16,384 more), dataVersion `e987f07d60e0`, `--verify` passes. **214 of the 509 keys map to the vocabulary (42.0%)**; the `official` row goes from 17.8% to 18.1% and the corpus stays at 71.2%. **All 11 files have a key the app's test order can pick.** **The search evaluation: first answer 90.6% to 88.4%, first five 98.4% to 98.1%**, the 37th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the six misses involves an InFocus model. **Not proven:** that the transcription is right where no check reaches it: the three tables' names are compared with the text tables' and their numbers with each other, but a name that the text tables also lack would not show; and the IN3118HD's names are the IN5310's, which its codes support and its own table does not say. **Not read:** the SP8604's (D298), the IN5550's two tables of NEC codes, the IN3900LB's specification, the LP800's and LP810's two-digit codes, and the IN80's and the JTouch's, which carry a copyright notice (TODO).

## 77. InFocus's SP8604, IN3900LB and IN5550, and the byte order of an InFocus customer code

Three more InFocus documents are read, **by eye (the SP8604's picture), by the specification's own table (the IN3900LB's, a landscape page) and by a table of two (the IN5550's): 5 files and 189 keys, 60 Plausible and 129 Untested**; and **a mistake of §75's about the IN100 is corrected: the document's customer code is the order it is sent in, not the other way round.** The manufacturers' tables (§29 and the sections after it) are now 644 files and 52,252 keys.

### D301 — The SP8604's picture, and which way InFocus writes a customer code

**§76 called the SP8604's PDF unreadable, and it is not**: it has no text at all (25 picture strips, the remote's photograph and the logo), but the page as it is shown is one clear table, `SP8600 Remote IR Information`, `Standard consumer IR (NEC framing) Device ID:Ox31CE`, `Repeat key: Send REPEAT frame at every 108ms` and 30 rows of `Key | Function | Description | Keycode` (`1 Power ON  Power on toggle  Ox90`); the stacking that made it unreadable was mine. It is read by eye as §76's tables are (`csv/SP8604_IRCodes__transcribed.csv`: a first row for the customer code, then the number, function, description and keycode of each row; the document writes `Ox` with the letter O for `0x`, and the transcription writes `0x`). **Its models are the SP8600, which it names, and the SP8604, whose page serves it** (the SP8600 is kept as `SP8600 (InFocus table)`, the LIRC database having one: R15).

**The byte order.** **LIRC's `SP8600` remote (15 keys, raw timings) decodes as NEC to the bytes `0x31 0xCE` and a function with its complement in every key: the first byte sent is 0x31, so the document's `0x31CE` is the order it is sent, the device 0x31 and the subdevice 0xCE**, and 9 of the remote's 15 functions are keys of this table (0x87 `MENU`, 0xC1 `Up arrow`, 0xC3, 0xC4, 0xC2, 0xC5 `ENTER`, 0x8E `FREEZE`, 0x86 `AUTO`, 0x83 `SOURCE`): the digits of the transcription are checked by an independent signal, which is the best a picture can have. The other InFocus documents' `0x4E87` is the other way round (the other sources' nine remotes at that address send 0x87 first: §75). **So InFocus writes a customer code in no one order**, and a reading has to be checked against a remote: `0x4E87` low byte first (confirmed), `0x31CE` first byte first (confirmed) and `0x83F4`, the IN100's: **the IR Blaster database's one remote that has the IN100's 13 functions on the same codes (Menu 0x0F, Source 0x04, Power 0x02, Blank 0x07...) has device 0x83 and subdevice 0xF4, which is `0x83F4` first byte first**, as the SP8604's is. §75 (D295, D296) read it low byte first, put the 13 keys at 0xF4/0x83 and said which of the two was the other order was unknown; **they are now at 0x83/0xF4** (two files, 26 keys), and **stay Untested**, since the document does not say and the match is another source's. The Mondopad's `0x0080` is read as the 0x4E87 documents' are and nothing confirms it.

### D302 — The IN3900LB's specification and the IN5550's two tables

**The IN3900LB's** is `InFocus Infrared Keycode Specification` (009-1067-00 revision G, 2009, 6 pages, for "the engineering and OEM-inbound partners"): the master list of keycodes 1 to 79, a table of `Key Name | Description | Decimal | Hexadecimal | Binary` on three **landscape pages (rotated 90 degrees: the sheet tool's `lines` mode now places a line where the page shows it, by `page.rotation_matrix`; no other pinned CSV changes)**. 79 rows, **17 named `N/A` (reserved codes: `Reserved (selects computer 1 source)`...) and no keys**, 62 keys; the decimal, the hex and the binary of every row agree. **It states no protocol and no customer code, it is the specification of InFocus's projectors as a family, and its model is the product page that serves it (IN3900LB)**: read with 0x4E87 and Untested as the other documents that state none are (D295). **Against the IN1100's text table: 51 of its 62 codes are the IN1100's and 45 are named alike**; the others are generations: 0x09 to 0x0C are `Focus Near`, `Focus Far`, `Zoom In` and `Zoom Out` in the IN1100's table and, since revision C ("Changed codes 9, 27-30 to reserved. Changed code 10 to help"), reserved, `Help`, `Right Mouse Button` and `Left Mouse Button`; `Mute` is `New Mute`, `Custom` is `Effect`; and **11 codes are the specification's alone** (`Discrete Resize Auto`, `15:9` and `16:10` and the motorized `Focus Near` and `Far`, `Zoom In` and `Out` and `Shift Left`, `Right`, `Up` and `Down`, 0x45 to 0x4F: "new codes 69-79 for new aspect ratios and motorized zoom/focus/shift").

**The IN5550's** (`IN555x-series Remote IR codes`, one page) is a table for two settings of a remote that can be set to ID modes: a `Table 0 (default)` and a `Table 1`, each a code and a function for 31 key numbers, the keys that differ being the numbers (`1` to `9` and `0` at 0x61 to 0x69 and 0x60 instead of `AUTO SYNC`, `ASPECT`, `PIP`, `OVERSCAN`, `FREEZE`, `LAMP Mode`, `INFO`, `LIGHT`, `SHUTTER` and `ID SET`), and **four special codes the ID-set sequences send** (`ID_SET + MENU` 0x6C `Table0`, 0x6E `Table1`, `ID_SET + No.` 0x6D `N-sets`, `ID_SET + CLEAR` 0x6F `ID set clear`). **A file for each: `IN5550` (33 keys) and `IN5550 (table 1)` (34)**; a row with `-` for its function in a table has none there (the 15th key, the 26th in Table 0). **It states no customer code and is read with 0x31CE, first byte first, as the SP8604's is: 18 of the SP8604's 30 codes are in the IN5550's tables and 14 of them mean the same function (`ON`, `OFF`, the arrows, `ENTER`, `MENU`, `ASPECT`, `PICTURE`, `INPUT`, `FREEZE`, `LIGHT`, `AUTO SYNC`); the other four (0x8B, 0x9A, 0x9B, 0x9C) do not, and the IR Blaster database's InFocus remote at 0x32CD shares only `ENTER` with it**: the family has more than one address, the IN5550's is a guess, and its 67 keys are Untested and say so.

### D303 — What it does to the rest, and what is not proven

The corpus is **17,022 remote files and 701,672 keys** (5 and 189 more), 5,465 brands, 282,585 models (9 more) and 181,290 signals (53 more: the SP8604's and IN5550's codes at 0x31CE and the IN3900LB's eleven). Both bundle profiles are 60,252,160 bytes (12,288 more), dataVersion `c2842bf987fa`, `--verify` passes. **104 of the 189 keys map to the vocabulary (55.0%)**; the `official` row goes from 18.1% to 18.2% and the corpus stays at 71.2%. **All 5 files have a key the app's test order can pick.** **The search evaluation: first answer 88.4% to 90.9%, first five 98.1% to 98.4%**, the 38th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the five misses involves an InFocus model. **Not proven:** that the SP8604's transcription is right where LIRC's remote does not reach it (9 of its 30 codes are confirmed by a signal, the others by the table's own order and the IN5550's 14 same-function codes); which byte order the IN100's is (the IR Blaster's remote says first byte first); that the IN3900LB's codes are the product it is served for's, and that the IN5550's address is 0x31CE. **Not read:** the LP800's and LP810's two-digit codes (no protocol, no customer code, no other source with them), the LP500/LP530's (the archive has only the site's 404 page), two Mondopad documents that carry the same 22 rows, and the JTouch's, which carries a copyright notice and no customer code (TODO).

## 78. Arcam's Solo Movie 2.1 and 5.1 notes

§74 left one Arcam document of the older products unread because its tables were one code and one name to a line: **the Solo Movie 2.1 and 5.1's serial control notes** (`SH194`, issue 3, 2008), whose last three pages are the IR tables of the AMP, TUN, AUX and DVD sections of a home-cinema system. **They are read: 1 document, 2 files and 464 keys, all Plausible.** The manufacturers' tables (§29 and the sections after it) are now 646 files and 52,716 keys.

### D304 — The document, the licence, and why it was left

The notes are Arcam's own at `arcam.co.uk/ugc/tor/solomovie21/RS232/SH194 iss3.pdf` (17 pages, 283,420 bytes, made on 27 June 2008 and changed on 11 October 2011, the server's `Last-Modified` 10 March 2015), "Custom Installation Notes: Serial programming interface and IR remote commands for Arcam Solo Movie 2.1 and 5.1", `Models covered: Solo Movie 2.1, Solo Movie 5.1`, issue 3, fetched on 2026-10-15 and pinned as §74's are (its own `retrieved`). **Licence:** none of the notice words in it (searched as D290 did), so public under D110. **Its tables are read by where the lines are** (`lines`, D290): on pages 15 and 16, under `IR remote commands`, two tables side by side, each row a decimal code (`16-12`) and its command beside it. §74 had left it because the Solo table of §64 is the same family; the codes of the Solo Movie 2.1 and 5.1 are named here for the models.

### D305 — The rule

**A key is the ledger's `RC5`** (D252): the system code the device, the command the function, the label the command as written. **A file for each model the cover lists: `Solo Movie 2.1` and `Solo Movie 5.1`**, each the whole table, **232 keys: the AMP commands (system 16, 59 keys), the tuner's (17, 59), the AUX commands for an iPod (21, 30) and the DVD's (25, 84)**. A name that goes on in the next line is one name (`HDMI Resolution SD` / `Interlaced`, indented in the right table), and `(DV137/9 only)` stays in the label (as `(3D only)` does, D291). **`25-611` of `Slow Backward x1/4` is a typo of the document's** (the order of the rows says 25-61, and the command 611 is no RC-5 command): it is no key, and the importer reports it for each file; nothing is corrected. **The SH284 notes (§74) already have a `Solo Movie 2.1` file** (the title of that document, `Solo Movie, Movie 2.1 & Music 2`, is read as three names), so this one is kept as `Solo Movie 2.1 (SH194)`: an importer's rule now for two documents of one maker that name one model (the owner's, partial data beats none).

### D306 — The rule checked, what it does to the rest, and what is not proven

**Against the other Solo tables** (the Solo's of §64, the Solo Mini's, the SH284's three): 87 of the 232 codes are in them and 63 of the 87 are named alike by the vocabulary or the spelling; the AMP and tuner commands share the most, the DVD's system 25 and the iPod's 21 are new to Arcam's files. **Against other sources' RC-5 remotes of any brand**: the codes held are 58 of the AMP's 59, 47 of the tuner's 59, 27 of the iPod's 30 and 46 of the DVD's 84, **named alike for 14, 6, 4 and 8**: system codes 16, 17, 21 and 25 are used by many makers, so the holding says little. **The ledger's eight RC-5 remotes of Arcam's from other sources (401 keys) now hold 369 keys that are a code of some Arcam table (92.0%), 8 more than with §74's files.**

The corpus is **17,024 remote files and 702,136 keys** (2 and 464 more), 5,465 brands, 282,589 models (4 more) and 181,334 signals (44 more). Both bundle profiles are 60,268,544 bytes (16,384 more), dataVersion `6ea7b6df5ff1`, `--verify` passes. **78 of the 464 keys map to the vocabulary (16.8%)**: the digits, `Standby`, `Mute`, `Menu`, the navigation keys and `OK` do, and `Source SAT`, `Decode Mode PLII Movie`, `Bass Correction +` and `Zone 2 Volume +` do not (D84's aliases, the owner's call); the `official` row stays at 18.2% and the corpus at 71.2%. **Both files have a key the app's test order can pick.** **The search evaluation: first answer 90.9% to 88.1%, first five 98.4% to 98.1%**, the 39th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the six misses involves an Arcam model. **Not proven:** that the document's tables are the Solo Movie 2.1's and 5.1's beyond its cover (the SH284's three products have a different table); that the `25-611` is the `25-61` it seems; that a Solo Movie is one product with a DVD player in it (the DVD section is for "DV137/9" in one row).

## 79. Lyngdorf's table of NEC codes

Lyngdorf's site has one short PDF of the codes a universal remote must send to its products, `NEC IR Code Format for Lyngdorf products`: a row for each of 38 buttons and a column for each product, each cell two 16-bit words (`0x10EF 0x0FF0`). **It is read: 1 document, 6 files and 218 keys, 201 Plausible and 17 Untested.** The ledger had one Lyngdorf remote, a JP1 upgrade for the TDAI-1120 (48 keys), and it sends 21 of this table's codes. The manufacturers' tables (§29 and the sections after it) are now 652 files and 52,934 keys.

### D307 — The document, how it is pinned, and the licence

The PDF was in the Wayback Machine's index of `lyngdorf.com` twice, as `Lyngdorf-DPA-1-IR-Codes.pdf` and `Lyngdorf-SDAI-2175-IR-Codes.pdf` in `wp-content/uploads/2017/09/`, **and the two are one file** (the same SHA-256, 298,718 bytes). **The site answers a script with 403 today** (a Cloudflare check), so the pinned copy is the archive's, as Oppo's (D131) and InFocus's (D294) are: the snapshot named in its URL (`id_`, the raw bytes), the manifest recording the address it was archived from, **snapshot 26 April 2019**, the server's `Last-Modified` 4 September 2017, the PDF made on 16 May 2011 with Word 2007 (author `kjeldsen`), fetched on 2026-10-16 (`tools/fetch_official.py lyngdorf`) and pinned as `Lyngdorf-IR-Codes.pdf`. It has two pages: the table, and a line that says `Further info about NEC can be found here` with the address of a Remote Central page. **Licence:** none of the notice words in it (searched as D290 did), so public under D110. The table is read by where its lines are (`lines`, D290): a column is the cells at one place.

### D308 — The rule

**A key is the ledger's `NEC1`, the first word the device and the subdevice (`0x10EF` is the device 0x10 and 0xEF) and the second word's high byte the function** (`0x0FF0`: 0x0F, and 0xF0 is its complement, which the reader checks and which holds in all 114 cells), at 38 kHz as the other manufacturers' tables are. **Unlike KEF's sheet (D244), whose bytes are the other way round, the bytes are in the ledger's order**: the JP1 upgrade of the TDAI-1120 sends 21 of the TDAI 2200's 38 codes with the device 0x10, the subdevice 0xEF and these functions, under the same names in other words (`Stand By` is `Standby` 0x0F, `0` to `9` are `Digit 0` to `Digit 9` (0x30, 0x00 to 0x08), `Mute` 0x13, `Volume +` and `Volume -` 0x1A and 0x10, `Channel +` and `Channel -` are `SRC Up` and `SRC Down` 0x32 and 0x31, `Skip Left` and `Skip Right` are `Previous` and `Next` 0x57 and 0x58, `Play/Pause` is `Play` 0x50, `Random` is `Shuffle` 0x56, and `Repeat` 0x54), and none under another function. **A file for each product**: the first column is four products' (`TDAI 2200`, `Millennium Mk. III+IV`, `DPA-1` and `SDAI 2175`, the same 38 keys each: the owner's, a file for a model is fine), then `RP-1` and `CD-1` with 33 keys each. **The subdevice is the product's**: the RP-1's cells are `0x10E0` and the CD-1's `0x10E1`, but for the three rows the sheet's note says are the same for all products (`Mute`, `Volume +`, `Volume -`, which are `0x10EF` in every column and say so in the citation). **`Video`, `Bypass`, `Max Volume`, `Polarity` and `Light` are for the first column only** (a `-` in the others: no key). A label is the table's name for the button.

**Untested are 17 keys, the CD-1's `Menu` to `Eject`** (`Menu`, `Info`, `Channel +` and `-`, the four arrows, `Enter`, `Skip Left` and `Right`, `Stop`, `Play/Pause`, `A/B`, `Random`, `Repeat`, `Eject`): **their first word is `0x10E` in the PDF, the last digit missing** (the page was rendered and looked at: the cell is as printed, and the RP-1's same rows are `0x10E0`). They are read as the column's own `0x10E1`, which is what its `Stand By`, digits, `Digital` and `Analog` cells say, and each citation says so: nothing in the document says the missing digit is a 1.

### D309 — The rule checked, what it does to the rest, and what is not proven

**Against the other source's remote:** 21 of the 48 keys of the JP1 TDAI-1120 are codes of the table, all under the same function (D308); the other 27 (`Power On` 0x80 and `Power Off` 0x81, the inputs 0x91 to 0xBF, the presets) are not in a table made in 2011, and are a later product's or firmware's. **Against every NEC remote of the other sources: 61 remotes of 47 brands (1,370 keys) hold 37 of the table's 98 distinct codes** (the address 0x10, 0xEF is shared with satellite receivers and the like), which says nothing for or against the table; the JP1 file is the only Lyngdorf remote of another source. The 98 distinct codes are the first column's 38, and the RP-1's and CD-1's 30 each that their subdevice makes different.

The corpus is **17,030 remote files and 702,354 keys** (6 and 218 more), 5,465 brands (Lyngdorf was one), 282,601 models (12 more) and 181,414 signals (80 more: of the 98 codes, 18 were held at 38 kHz already, all by LIRC's Audiovox `Sirius_PNP2`; the other 60 remotes that hold some are at 38.4 kHz). Both bundle profiles are 60,284,928 bytes (16,384 more), dataVersion `3aae8a8ee3a5`, `--verify` passes. **168 of the 218 keys map to the vocabulary (77.1%)**, the table's names being the plain ones (`Stand By`, the digits, `Mute`, `Menu`, `Info`, the arrows, `Enter`, `Stop`, the volume and channel keys, `Repeat`, `Eject`); `Digital`, `Analog`, `Skip Left`, `Skip Right`, `A/B`, `Video`, `Bypass`, `Max Volume`, `Polarity` and `Light` do not (D84's aliases, the owner's call); the `official` row goes from 18.2% to 18.4% and the corpus stays at 71.2%. **All 6 files have a key the app's test order can pick** (`Power`, `Mute`, `Volume Up`). **The search evaluation: first answer 88.1% (the same), first five 98.1% to 97.8%**, the 40th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the seven misses involves a Lyngdorf model. **Not proven:** that the missing digit of the CD-1's 17 cells is a 1 (it is the column's); that a PDF of 2011, archived in 2019, is what the products do now (the JP1 file has keys it does not); that the TDAI 2200, Millennium, DPA-1 and SDAI 2175 are four products with one table, as the column's header says and the file for each repeats; the carrier (the table says NEC, and 38 kHz is read as the other manufacturers' tables are, where the JP1 file says 38.4 kHz, so a code both hold is two signals).

## 80. Emotiva's PDFs of Pronto hex codes

Emotiva published for its UMC processors, USP preamplifier and ERC players a PDF of the codes "in Pronto format" its remote sends: a key name, and under it the key's Pronto hex. **They are read: 6 documents, 7 files and 239 keys, 223 Plausible and 16 Untested.** The ledger had 7 Emotiva remotes (the IR Blaster database's 7001 and six JP1 upgrades: the Fusion 8100, UMC-1, UMC-1 Metal, UMC a/v receiver, XDA-1 and a UMC-1 processor), and they hold 120 of the 123 distinct codes these tables give. The manufacturers' tables (§29 and the sections after it) are now 659 files and 53,173 keys.

### D310 — The documents, how they are pinned, and the licence

**Emotiva's own addresses answer 404 today** (the UMC-200's, checked on 2026-10-16; the UMC-1's moved to the same path on https and answers 404 there), so the documents are the Wayback Machine's copies, as InFocus's (D294) and Lyngdorf's (D307) are: each the snapshot named in its URL (`id_`, the raw bytes), the manifest recording the address it was archived from and the snapshot, fetched on 2026-10-16 (`tools/fetch_official.py emotiva`). **Six PDFs of 15 to 145 KB, 2 to 12 pages:** `UMC_remote_codes.pdf` (`UMC-1 Emotiva IR command List rev. 021110`, 51 keys, snapshot 2 December 2010), `remoteCodes_1-25-11.pdf` (`Emotiva UMC-1 Metal Remote Codes – rev 1/25/11`, 52 keys, snapshot 27 October 2011; the same bytes are at `resources/media/umc1/remote_codes.pdf`, snapshot 2014), `umc200_remote_codes.pdf` (`Emotiva UMC-200 IR HEX Codes`, "confirmed to work as of January 11, 2013", 51 keys, snapshot 14 March 2016), `usp_remote_codes.pdf` (`USP remote codes 12/8/10`, 13 keys, snapshot 8 October 2011), `erc1_hex.pdf` (no title, made in June 2009, 24 keys, snapshot 3 December 2011; **the same bytes are the ERC-2's page's** `resources/media/erc2/hexcodes.pdf`) and `ERC-3_IR_CODES.pdf` (24 keys, made in December 2015, snapshot 9 September 2016). Their text (a line to a row, `pages`) is converted to CSV and pinned as every maker's is. **Licence:** none of the notice words in them (searched as D290 did); the first two say the codes are "complimentary" and that Emotiva does "not guarantee the accuracy of these codes", which is a disclaimer and no restriction: public under D110. **Not read, and why:** `Emotiva Pronto codes revE2.pdf` (a 2006 document of Sunfire Corporation: 153 commands of a code that is "Lead-in + Data (MSB first) + Inverse of data (MSB first) + Lead-out", 12 data bits and their complement and no NEC, for which the ledger has no protocol; it would be Pronto-only files, and its names and numbers are in the text apart from the strings: TODO); `IR.zip` of 2007 (one Pronto CCF file, binary); the Fusion Flex amplifier's Pronto document (the archive's copy is an HTML page).

### D311 — The rule

**A key is the ledger's `NEC1`, and the Pronto hex is decoded, not carried** (as Rotel's is, §69): a leader of a mark twice the space (0x0155 and 0x00AA), 32 bits whose space is one mark for a 0 and three for a 1, each byte least significant bit first, the first byte the device, the second the subdevice (`20 5F`, `02 FD`, `00 FD`), the third the function and the fourth its complement, which the reader checks and **which holds in all 215 strings**. A string is the key's once sequence (34 pairs and the repeat's 2: 188 of them) or, where the document gave the repeat sequence only (27: 21 of the ERC-1's, 5 of the UMC-1's, 1 of the UMC-1 Metal's), its first 34 pairs, and the citation says which. **The carrier is 38 kHz for every key**, as the other manufacturers' tables are: the Pronto's frequency word is 0x006D (38.0 kHz) in 186 strings and 0x006C (38.4 kHz) in 29 (28 of them in the 2010 UMC-1 document, which mixes the two), and the citation gives the word the document has. **The label is the document's name for the key** (a no-break space or a hyphen's look-alike is a space or a hyphen), **but the UMC-200's `(VOLUME GROUP)`** (`VOL + (VOLUME GROUP)`, `ARROW UP (MENU GROUP)`: the table's own grouping of its keys) **is dropped from the label and cited**, which takes its keys the vocabulary names from 2 of 51 to 34; and a key's name has an en dash as a hyphen (`VOL –` is `KEY_VOL_MINUS`). **A file for each model**: the six documents name `UMC-1`, `UMC-1 Metal`, `UMC-200`, `USP` and `ERC-3`, and **the ERC-1 document names none** (it is on the ERC-1's page and the same bytes are the ERC-2's), so it is two files with the same 24 keys, the citation saying so. **The names `UMC-1` and `UMC-1 Metal` are two other sources' files already** (the JP1 upgrades), so these two are kept as `UMC-1 (Emotiva table)` and `UMC-1 Metal (Emotiva table)` (R15).

**Untested are 16 keys, all of the one reason: a code that the document gives to two keys** (D295's rule): the 2010 UMC-1's `Audio` and `STEREO` (02 FD 54), `CANCEL` and `EXIT` (02 FD 59), `TUNER CH-` and `SOURCE DOWN` (02 FD 55), `TUNER CH+` and `SOURCE UP` (02 FD 51), the 2011 UMC-1 Metal's `standBack Sur-` and `Back Sur+` (20 5F 58) and the ERC documents' `Play` and `Pause` (00 FD D9, in the three). **The Metal document's first label is printed `standBack Sur-`**, with a stray `stand` before what is, from the alphabetical order of its list (it comes before `Back Sur+`), `Back Sur-`: the label is kept as printed and the citation says so.

### D312 — The rule checked

- **The 2010 UMC-1 against the JP1 `UMC a/v receiver`**: **all 47 distinct codes are in the JP1 file**, 37 under the same label, 2 under the same two labels in the other order (`TUNER CH-` and `SOURCE DOWN`...), and the other 8, the source keys (`DVD`, `SAT`, `TUNER`, `CABLE`...), under the labels of the 2011 document (`Source 2 – UMC Source 1 - DVD`): the JP1 file was made from these two documents.
- **The 2011 UMC-1 Metal against the JP1 `UMC-1 Metal`**: all 51 distinct codes are in it. **Where they differ: the JP1 Metal file has `Channel Up` and `Channel Down` the other way round** (0x55 is `channel up` there and `Channel Down` in the document and `TuneDown` in the JP1 `UMC-1`), **and the document's `Back Sur+` is the row above it** (0x58, where both JP1 files have 0x54 for `Back Sur+` and 0x58 for `Back Sur-`): a copy error of the document's, which is why both keys are Untested and what is not corrected.
- **The UMC-200 against the IR Blaster's 7001**: all 41 codes of the IR Blaster's remote are in the table (so it is a UMC-200 remote): **22 have the same label, 7 the same function in other words** (`STANDBY` and `ON` are `POWER`, `ARROW LEFT` is `LEFT`, `ON-OFF` is `TUNER ON/OFF`...) **and 12 are named otherwise: the tuner's `FREQ +`, `CH +`, `FREQ –` and `CH –` are the other way round (0x08 and 0x1D, 0x0A and 0x0B), and the eight trims** (`B SUR UP` 0x1A, `SUR UP` 0x44, `SUB UP` 0x5A, `CTR UP` 0x45 and their four downs 0x1B, 0x42, 0x40, 0x48 are `CTR+`, `SUB+`, `SUR+`, `B SUR+` and `CTR-`, `SUB-`, `SUR-`, `B SUR-` in the IR Blaster's): the table is kept as it reads, and which names the keys is not decided here.
- **The USP against the same JP1 receiver file**: the USP's 13 codes are all in it, at the same address (02 FD), **and the power and volume keys are the same keys** (`Power On` 0x5D, `Power Off` 0x1D, `Volume Up` 0x11, `Volume Down` 0x15) **and the other nine are other keys** (the USP's `Aux1` is the UMC's `LEFT ARROW`): two products of one maker share an address and the codes of the keys they have in common.
- **The three ERC documents are one remote**: the ERC-3's 24 codes are the ERC-1's, and its key names are the other spellings of the same keys (`SKIP FORWARD` and `Skip Forward`, `FAST FORWARD` and `Scan Forward`, `EJECT` and `Open/Close`); no other source has an ERC remote or a USP, so nothing checks them but the arithmetic of the frames.

### D313 — What it does to the rest, and what is not proven

The corpus is **17,037 remote files and 702,593 keys** (7 and 239 more), 5,465 brands (Emotiva was one), 282,612 models (11 more) and 181,504 signals (90 more: of the 123 distinct codes, 120 are held by other sources' remotes, nearly all at 38.4 kHz as the Emotiva ones are, and 33 at 38 kHz by LIRC remotes of other brands whose address is 00 FD, which is why they are not new here). Both bundle profiles are 60,309,504 bytes (24,576 more), dataVersion `00051133d25d`, `--verify` passes. **147 of the 239 keys map to the vocabulary (61.5%)**: the digits, `Mute`, `Menu`, `Enter`, the arrows, `Standby`, the transport keys and `Dim` do, and `Scan Forward`, `Source 1 - Tuner`, `Zone 2 Power`, `Dolby Volume`, `Status` and the `INPUT +` and `MODE +` do not (D84's aliases, the owner's call); the `official` row goes from 18.4% to 18.6% and the corpus stays at 71.2%. **All 7 files have a key the app's test order can pick.** **The search evaluation: first answer 88.1% to 91.6%, first five 97.8% to 98.4%**, the 41st draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the five misses involves an Emotiva model. **Not proven:** that the 2010 and 2011 documents are what the UMC-1 does now (the JP1 files differ from them in the Channel keys); that the 29 strings of 38.4 kHz are not a different remote's (the files are at 38 kHz); that `Back Sur+` is 0x54 (two JP1 files say so, the document does not); that the USP's and the ERCs' tables are what the products send, which no other source says; that the model of the ERC-1 document is the ERC-1 and the ERC-2 (the pages it is on say so, the document does not); that the Sunfire document of 2006 is for a product the ledger could have (TODO).

## 81. Denon's AVR-4311CI and AVR-4311 remote code list

Denon's UK site published a 33-page PDF, `AVR-4311CI/AVR-4311 Remote Code List`, that gives every code of the two receivers' remote in the two formats it sends: **the 15-bit `Denon` code (`FORMAT SHARP`) and the 48-bit `Denon-K` (`FORMAT KASEIKYO`)**. **It is read: 1 document, 4 files and 1,024 keys, 944 Plausible and 80 Untested** (934 and 90 when it was written: §94's rule reads ten keys, five rows in each Kaseikyo file, as Plausible). The ledger had 86 `Denon` and 41 `Denon-K` remotes of other sources (IR Blaster and JP1), and they hold **every one of the table's 153 `Denon` codes and 286 of its 377 `Denon-K` ones**. The manufacturers' tables (§29 and the sections after it) are now 663 files and 54,197 keys.

### D314 — The document, how it is pinned, and the licence

`denon.co.uk/DocumentMaster/UK/AVR4311CI_AVR4311_IR-Codes.pdf` is **4,251,013 bytes and 33 pages** (made by Acrobat Distiller on 10 August 2010, the cover `Ver. 01`, `10.Apr.'10`, `Original`; the server's `Last-Modified` 20 October 2010). **Denon's UK address now redirects to the home page of denon.com's new site, and the path answers 404 there**, so the document is the Wayback Machine's copy as InFocus's (D294), Lyngdorf's (D307) and Emotiva's (D310) are: the snapshot named in its URL (`id_`, the raw bytes), **13 August 2011**, fetched on 2026-10-16 (`tools/fetch_official.py denon`). Its parts: the cover, **pages 2 to 7 the Sharp tables** (six tables of 64 codes, each for a system address and extension bits), page 8 the Kaseikyo frame and page 9 the table of remote IDs, **pages 10 to 33 the Kaseikyo tables**, a table of 48 bit columns for each key. **Licence:** none of the notice words in it (searched as D290 did): public under D110. It is converted by a new sheet mode, **`words`** (`tools/official_sheets_to_csv.py`): every word of every page by its place (a digit of a bit matrix is a word), and the rows of the ruled tables, which say which lines of a Sharp table belong to a row (a name of two lines has its row between them).

### D315 — The rule

**Sharp** (the ledger's `Denon`, D64): a row is a number, 6 data bits (`C6` to `C11`) and the key's name or none (the shaded rows are unused: 167 of the 320 rows have no name; 153 have one and three of them two). **The system address (`C1` to `C5`) is the device, first bit first (`01000` is 2), and the function is the data and the extension bits (`C12`, `C13`) above it** (`100000` is 1, the extension `11` is +192: `POWER` is 0xC1, as the ledger's Denon remotes have it), **and the data's value must be the row's number, which holds in all 320 rows.** The tables are `01000`/`11`, `00110`/`01`, `00110`/`10`, `00110`/`11`, `01000`/`10` and `00100`/`01` (devices 2, 12 and 4) and **the document gives remote ID 1 only**. **Kaseikyo** (`Denon-K`, D66): `GENRE1` (bits 21 to 24) is the device, `GENRE2` (25 to 28) the subdevice and bits 29 to 40 the function, each first bit first (`K4-1` is device 4, subdevice 1: `ALL POWER ON` is function 2); **the header of each table is checked (the vendor 0x54 0x32 and a parity nibble of 0), the ID bits 39 and 40 must be 00 (remote ID 1: page 9's table says so), and the parity byte (bits 41 to 48), the exclusive or of the 3rd, 4th and 5th bytes the document's page 8 says, is compared with the one the data bits give.** **Only the tables for remote ID 1 that give all 48 bits are read: 377 rows** (`K4-1 : MAIN ZONE` 164, `K4-3 : ZONE2` 84, `K4-5 : ZONE3` 83, `K4-7 : NET AUDIO` 38 and the analog tuner's `K4-1` 8); **not read are the tables for IDs 2 to 4 (`K4-2`, `K4-4`, `K4-6`, `K4-8` and the analog tuner's `K4-2`: their bits 39 to 48 are `*`) and the iPod's `K2-3`, Sirius's `K7-B` and HD Radio's `K7-4`** (`*` as well, for IDs 1 to 4): for ID 1 the document's own notes give the missing bits, so they could be derived (TODO).

**A file for each model and each format**: `AVR-4311CI` and `AVR-4311` (Sharp) and `AVR-4311CI (Kaseikyo)` and `AVR-4311 (Kaseikyo)` (the ledger has one protocol to a file). **A name says which model it is for**: `TUNER(except AVR4311CI)` is the AVR-4311's and `HD RADIO(AVR4311CI only)` the CI's (the same code, two names, one for each model), `※1` is the page's note `Except AVR4311CI` (26 names, the tuner's), `※2` is `EU only` (3: `DIRECT SEARCH (RDS SEARCH)`, `PTY`, `RT`) and is read as the AVR-4311's, the AVR-4311CI being the American model; and the analog tuner's table is headed `(Except AVR4311CI)`. A name goes on in the next line when that begins `(`, and a `/...` line is the rest of a name (`MAIN ZONE TUNING MODE AUTO/MANU.` and `/Fast Forward`). **The label is the document's name for the key**, kept as printed with its typos (`LEY9/WXYZ`, `INPUT MDOE AUTO`, `INPUT MDOE EXT.IN`), and a name that two tables of a file have is the second's with the table (`CURSOR UP (NET AUDIO)`).

**Untested are 80 keys, for two reasons the citation gives.** (1) **The 39 rows of the Kaseikyo tables whose parity byte is not the one their data bits give, nor another zone's** (37 in the CI's file, whose analog tuner rows are not there; §94: five more rows in each file, `ZONE2 TUNER BAND/Rewind`, `ZONE3 MEMORY`, `ZONE3 SIRIUS(NA only)` twice and `ZONE3 TUNER BAND/Rewind`, have another zone's byte, which a copied table keeps, and are Plausible): the data bits are read and the parity byte is not; **26 of the 39 rows' codes are held by other sources' `Denon-K` remotes, which says the data is right and the document's parity byte is wrong** (they are copies of one another's: 20 distinct wrong bytes in the 44 rows of the first reading, nine rows with `0xDB`, six with `0xFC` and six with `0x01`: `QUICK SELECT 1` to `3`, `ZONE2 CURSOR UP` to `ENTER`, `ZONE2 Internet Radio`, `Rhapsody`, `Napster` and `Media Server`, the zones' `TUNING UP` and `DOWN`...). (2) **A code the document gives to two names for one model: 4 keys of the AVR-4311's Sharp file**, `ZONE2 TUNER` and `ZONE2 HD RADIO` and `ZONE3 TUNER` and `ZONE3 HD RADIO`: the document prints the HD Radio names `(AVR4311 only)` where the main zone's says `(AVR4311CI only)` and the tuner's `(except AVR4311CI)`, so read as printed both are the AVR-4311's.

### D316 — The rule checked

- **Against the other sources' `Denon` remotes**: **all 153 distinct codes of the table are held by some** (47 of the 86 remotes hold some), **150 by the JP1 upgrade of the AVR3310 and 149 by the AVR-3808's**: **138 of the 150 have the same label** (after case and punctuation), and the 12 others are the same key under another name (`BD` is `HDP`, `GAME` is `VCR`, `MULTI CH STEREO` is `7CH STEREO`, `MultEQ XT 32` is `MultEQ`; the AVR-3808's file has 37). Nothing in the table is a code the other sources have as another function: the bit order of D315 is the ledger's.
- **Against the other sources' `Denon-K` remotes**: **286 of the 377 distinct codes are held by some** (27 of the 41 remotes hold some), **246 by the JP1 `AVR-x4000`** and 171 by the AVR-E400's, **the same function under another name in most** (`MAIN ZONE ON` is `Main On`, `KEY1/CODE1` is `KEY1`, `NET/USB` is `Network`, `BD` is `Blu-ray`: 72 of the 246 have the same label). **The 91 not held are the zones' and the net audio's keys** that no other file has (`ZONE2 ...`, `ZONE3 ...`, `Volume Leveler ...`, `Dolby Volume ...`).
- **Not proven:** that the document is right where no other source holds the code (the zones' keys); that the codes of the AVR-4311 are the AVR-3311's or any other receiver's (the other sources' remotes hold them, the document is for these two); that `※2` (`EU only`) is the AVR-4311's rather than the CI's too; that the two `(AVR4311 only)` of the HD Radio names are not typos for the CI.

### D317 — What it does to the rest

The corpus is **17,041 remote files and 703,617 keys** (4 and 1,024 more), 5,465 brands (Denon was one), 282,618 models (6 more) and 181,595 signals (91 more: **the Sharp codes are all signals the ledger had, and the 91 `Denon-K` codes no other source holds are new**). Both bundle profiles are 60,370,944 bytes (61,440 more), dataVersion `fb6846b9b275`, `--verify` passes. **72 of the 1,024 keys map to the vocabulary (7.0%)**: `POWER`, `PHONO`, `CD`, the cursor keys, `ENTER` and `MUTING` do, and the levels (`FL LEVEL UP`), the zones (`ZONE2 ...`), `QUICK SELECT 1`, `Dolby Volume ON`, `MultEQ XT 32 SELECT` and the receiver's input names (`SAT/CBL`, `V.AUX`, `DVR`) do not (D84's aliases, the owner's call); the `official` row goes from 18.6% to 18.4% and the corpus from 71.2% to 71.1%. **All 4 files have a key the app's test order can pick** (`Power` for the Sharp ones, `Volume Up` and `Mute` for the Kaseikyo ones). **The search evaluation: first answer 91.6% to 88.4%, first five 98.4% (the same)**, the 42nd draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the five misses involves a Denon model.

## 82. Hitachi America's TV remote IR code documents

Hitachi America published in a folder of its support site small tables of the codes its television remotes send, **two spreadsheets and four PDFs, 2003 to 2009**. **They are read: 6 documents, 10 files and 311 keys, 302 Plausible and 9 Untested.** The ledger had 108 Hitachi remotes (75 of them `NEC1`, and 38 of those at the address 0x50 0xAF these tables give), and they hold **every code of six of the ten files and 34 of the L19 and L26's 47, 25 of the L32 and larger's 27 and 22 of the PDP's 24; none of the AVC's 17 (the address 0x56 0xAB)**. The manufacturers' tables (§29 and the sections after it) are now 673 files and 54,508 keys.

### D318 — The documents, how they are pinned, and the licence

**`www.hitachi-america.us` no longer resolves**, so the documents are the Wayback Machine's copies as InFocus's (D294), Lyngdorf's (D307), Emotiva's (D310) and Denon's (D314) are: the snapshot named in its URL (`id_`, the raw bytes), 26 December 2011 (January 2011 for the AVC's), fetched on 2026-10-16 (`tools/fetch_official.py hitachi`). **Six files** (`remote_ir_codes/`): `2003_and_2004_HITACHI_IR.xls` (27,648 bytes, two sheets: `Sheet2`, the TV of the base model `HL01322` in 2003, and `Sheet1`, the same and the codes "New for 2004 models"), `2004_IR_Codes-1.xls` (`2004 Codes`, "RC IR CODES", and `Additional Codes`, "ADDITIONAL IR CODES FOR TV NOT INCLUDED IN RC"), `2007_Discrete_Codes-DW3.pdf` (the discrete layer of the remotes `HL02401`, `HL02402` and `HL02403`), `2009_Discrete_Codes_L32_and_larger_0211.pdf`, `2009_RC_Layouts_and_IR_Codes_L19_L26.pdf` (the remote's 47 keys) and `AVC_56_Discrete_Codes_UT_Monitor_PDP_Pronto_Hex.pdf` (8 pages, three sections of 2008, `AVC`, `UT Monitor` and `PDP`, each code with its Pronto hex). Their cells are converted to CSV (a sheet; a PDF's cells by place, `cells`, D283) and pinned as every maker's are. **Licence:** none of the notice words in them (searched as D290 did): public under D110. **Not pinned, and why:** the folder's `2008_Plasma_RS232C.pdf`, `2008_UT_RS232_Spec_0512_prelim.pdf` (which says `(c)` and `confidential`) and `Hitachi_2007_RS232Codes.pdf` are RS-232 control specifications and no IR table; `2006_Roll_Click_Remote_IR_code_instructions_12_09.pdf` names the discrete function of each button and gives no code. (Converting a spreadsheet needed a fix to the tool: its `flow` mode had a local named `text` that hid the function of that name, so no spreadsheet could be converted again.)

### D319 — The rule

**A key is the ledger's `NEC1`, the bytes in the order they are sent**: `50AF 17E8` is the device 0x50, the subdevice 0xAF, the function 0x17 and its complement 0xE8, which is the ledger's 38 Hitachi remotes' `Power` (0x17), and `Power On` and `Off` of the L32's table, 0x3E and 0x3F, are the ones two of them have; the L19 and L26's `Custom Code` and `Key Code` cells are the two words. At 38 kHz, as the other manufacturers' NEC tables are (the Pronto hex of the AVC's document has the frequency word 0x006C, and the PDP's 0x006B). **A file for each table**: `HL01322 (2003)` and `HL01322 (2004)` (`Sheet2`'s 48 keys and `Sheet1`'s 48 and the 15 new for 2004, 63 in all: its `ON` and `POWER ON` are both 0x3E), `2004 TV remote` (36), `2004 TV additional codes` (22), `DW3` (22), `L32 and larger` (27), `L19 and L26` (47) and the AVC document's three sections, **`AVC (2008)` (17), `UT Monitor (2008)` (5) and `PDP (2008)` (24)** (the year is the `08` of each section's title; without it the short names `AVC` and `PDP` were the catalog's filler for queries about other Hitachi models). **The label is the table's name for the key**, the DW3's `FUNCTION` (the key of its remote that sends it, `VOL DOWN` for `Air (or Cable)`, is in the citation), a spreadsheet's `1.0` the key `1` and the arrow keys of the 2003 sheet (`VOL \x81£1`, two characters of a Japanese code page) `VOL ▲1`. **A key a sheet says is a `MODE KEY`** (`TV`, `VCR`, `CBL`, `SAT`, `DVD`, `AV1`, `AV2`, `STB` and the light: 9 of each of the two sheets) **has no code and is no key.** **The AVC document's Pronto hex is decoded (as Emotiva's, D311) and must agree with the code beside it: 45 of the 46 do.**

**Untested are 9 keys: 8 of a code the table gives to two keys** (`ON` and `POWER ON`, `OFF` and `POWER OFF`, both in `HL01322 (2004)` and in the `2004 TV additional codes`: synonyms, kept by the rule of D295), **and the PDP's `HDMI1`, whose Pronto hex decodes to 50 AF 20 DF, the code of its `HDMI2`** (the hex column says 50AF 3DC2).

### D320 — The rule checked, what it does to the rest, and what is not proven

- **The tables against the other sources' Hitachi remotes**: all 48 codes of `HL01322 (2003)`, the 61 distinct of `HL01322 (2004)`, the 36 of `2004 TV remote`, the 20 of the additional codes, the DW3's 22 and the UT Monitor's 5 are held by other remotes at 0x50 0xAF; **the L19 and L26's 47 have 34 held** (`RETURN`, `REPEAT A-B`, `ANGLE`, `MARKER`, `JUMP`, `DVD MENU`, `TOP MENU`, `EJECT`, `SLOW REVERSE` and `FORWARD`, `TV/DVD`, `PLAY MODE` and `CURSOR DOWN / CH DOWN`: the DVD functions of a TV/DVD combination are new), the L32's 27 have 25 (`E.SAVE MEDIUM` and `PRO DAY` are new), the PDP's 24 have 22 (`Pro Mode` and `E.SAVE MED` are new). **The AVC's 17 (0x56 0xAB) are in no other source.**
- **The documents against each other**: 80 labels are in two or more tables, and **7 differ in the code**: `NIGHT` (0x69 in the 2004 tables, 0x75 in the DW3's and the PDP's), `16:9 ZOOM`, `4:3 EXPANDED` and `4:3 ZOOM 2` (the 2004 tables and the PDP's), `INFO` (0x2F in the 2003 and 2004 sheets and 0x10 in the 2004 remote's), **`C.C.` (0x35 in the 2003 and 2004 sheets, 0x37 in the 2004 remote's and 0x78 in the AVC's and the PDP's) and `EXPANDED`** (the AVC's and the L32's): the years' tables redefine the discrete layers, and each file is as its document has it.
- The corpus is **17,051 remote files and 703,928 keys** (10 and 311 more), 5,465 brands (Hitachi was one), 282,638 models (20 more) and 181,685 signals (90 more: of the 112 distinct codes, 22 were held at 38 kHz by LIRC's Hitachi `CLE-941`, and the other sources' Hitachi remotes are at 38.4 kHz). Both bundle profiles are 60,395,520 bytes (24,576 more), dataVersion `af3ba73043e1`, `--verify` passes. **165 of the 311 keys map to the vocabulary (53.1%)**: the digits, `Power`, `Input`, `Mute`, the volume and channel keys, `Menu`, `Exit`, `Info` and the cursor keys do, and `Aspect 16:9 STD1`, the `Zoom` and `E.SAVE` keys, `Day`, `Night`, `VID1` to `VID5`, `HDMI1` to `HDMI5` and the AVC's categories do not (D84's aliases, the owner's call); the `official` row goes from 18.4% to 18.6% and the corpus stays at 71.1%. **All 10 files have a key the app's test order can pick.** **The search evaluation: first answer 88.4% to 87.5%, first five 98.4% to 97.8%**, the 43rd draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the seven misses involves a Hitachi model. **Not proven:** that a document of 2003 to 2009 is what the models it was written for send now; that `HL01322`, `DW3` and the other names are the products the tables are for (they are the documents' own names for a base model, a remote or a size); that the PDP's `HDMI1` is 0x3DC2 rather than the 0x20 its Pronto hex says; the `MODE KEY` rows' codes (the remote sends none).

## 83. Classé's IR code lists and HEX code PDFs

Classé published for its amplifiers, preamplifiers, processors and CD players a PDF of the codes each remote sends: **an IR code list** (a table of `Sys code`, `IR code` and `Action`: `The Classé SSP-300 / SSP-600 uses RC6 protocol for infrared communications... The Classé Remote Control uses a frequency of 56.0 kHz`) and, for the newer products, **a HEX code PDF** (`RC6 MODE 0, SYSTEM CODE 016`, a name and the Pronto hex of each code, for `TOGGLE 0` and `TOGGLE 1`). **They are read: 12 documents, 18 files and 1,170 keys, all Plausible.** The ledger had two Classé remotes (a JP1 upgrade of the CAP-151 and a Classic DVD), neither RC6. The manufacturers' tables (§29 and the sections after it) are now 691 files and 55,678 keys.

### D321 — The documents, how they are pinned, and the licence

**Seven are Classé's own server's** (`support.classeaudio.com/files/documents/automation_and_control/ir_remote_codes/`, which serves them today: fetched on 2026-10-16, no archive), **and six are the Wayback Machine's copies** of the old downloads page's (`classeaudio.com/downloads/Download.htm?Path=IR_CODES/Delta_Series/IR_and_HEX_CODES&File=Classé ... IR codes.pdf`, a `Download.htm` that is the PDF itself, snapshots of 17 December 2010): the site no longer has the Delta series' lists. **The six of 2010 (made in 2005 and 2007):** the CAP-2100 (1 page), CDP-100 (2), CDP-102/202/300/502 and CDT-300 (3, `v1-19`), CP-500 (1), CP-700 (2) and SSP-300/600 (3, `v1-20`). **The six of the support site:** the SSP-800's list (3 pages, `Version 1.0 (2008/09/29), RC6 code list rev. 24`), the Delta amplifiers' `IR Command reference` (1 page, 2017, `RC6 Coded Mode 0 system 201`) and the HEX code PDFs of the CP-800 (13 pages), the Delta and CT amplifiers (5), the SSP-800 and CT-SSP (20) and the SSP-25 (6, an RC5 table of 2012). Their cells (`cells`, D283) and lines (`pages`) are converted to CSV and pinned as every maker's are. **Licence:** none of the notice words in them (searched as D290 did): public under D110. **Not read, and why:** the CP-800's `IR codes Rev1` (a spreadsheet page whose one heading `016(019)` covers every row, where the CP-800's HEX PDF has Mute and Volume under system 200: its codes are in the HEX PDF, TODO).

### D322 — The rule

**A key is the ledger's `RC6` (mode 0) at 56 kHz** (the lists say `56.0 kHz`, and the Pronto hex of every HEX code PDF has the frequency word 0x004A) **with the system code the device and the IR code the function** (`016` and `1` is device 16, function 1, which the ledger compiles with the toggle bit 0), **or, for the SSP-25's table, `RC5` at 36 kHz** (system 25). **A list's row** is a three-digit system code, an IR code and the action, and **a line in the action column with no code goes on the action above it** (`Cycle through display intensities` and `(low/medium/high)`). **The system is the row's**: a list uses the preamplifier's code for most keys and `200` for the Mute and the Volume keys (`Mute_toggle`, `Volume Up (+)`), as the HEX PDFs do. The Delta amplifiers' sheet states one system, 201, in its heading. **The names are the documents' own** (`Input01`, `Standby_toggle` of the SSP-800's list and `INPUT 1`, `STANDBY/OPERATE` of its HEX PDF) and a file is a document's: **the CDP-102/202/300/502 and CDT-300's table is five files** and the SSP-300/600's two, the same keys (the owner's, a file for a model is fine), and **a product with a list and a HEX PDF has a file of each** (`SSP-800` and `SSP-800 (HEX codes)`; the SSP-800/CT-SSP PDF is `SSP-800 (HEX codes)` and `CT-SSP (HEX codes)`, the Delta and CT amplifiers' `Delta and CT amplifiers (HEX codes)` and the CP-800's `CP-800 (HEX codes)`).

**The HEX PDFs' Pronto hex is decoded and must agree.** A leader of 6 and 2 units (the unit 0x19: 444 microseconds at 56 kHz), a start bit, 3 mode bits (0), a trailer bit of two units a half (the toggle) and 8 bits each of system and command, each a mark and a space or the other way: **every string of the three RC6 PDFs (323 of 323, 107 + 44 + 172) is the system code of its heading, the IR code of its line, mode 0 and the toggle bit of its heading**, and the SSP-25's 48 RC-5 strings (a half-bit of 0x22) are system 25, the command and the toggle. **One item has two strings: the CP-800's `IR CODE 153 TOGGLE 0` (`UNMUTE`) holds the strings of both toggles, because the heading of the second is missing** (the report says so; both agree with the code).

### D323 — The rule checked, what it does to the rest, and what is not proven

- **The lists against the HEX PDFs of the same products.** **The SSP-800's list and its HEX PDF have the same 86 codes** (system 116 and 200), **under the same name in 43 (after case and punctuation: `Input UP`)** and another in the rest (`Standby_toggle` and `STANDBY/OPERATE`, `Input01` and `INPUT 1`); the CT-SSP's file is the HEX PDF's again. **The Delta amplifiers' sheet has 25 codes and the Delta and CT amplifiers' HEX PDF 22, all in the sheet**: the sheet's `Program Mode Toggle` (68), `Store Execute` (70) and `Acknowledge` (92) are not in the PDF. The CP-800's HEX PDF has 54 codes of systems 16 and 200, the CDP-102 family's table 88 of systems 4 and 200.
- **Against the other sources**: none has a Classé RC6 remote; the 24 RC-5 codes of the SSP-25 are system 25 commands that other brands' remotes also have (14 of them are held), which says nothing for or against the table.
- The corpus is **17,069 remote files and 705,098 keys** (18 and 1,170 more), 5,465 brands (Classé was one), 282,673 models (35 more) and 182,009 signals (324 more: of the 343 distinct codes, 319 are RC6 at 56 kHz and 24 RC-5, and 19 compile to a signal the ledger had, 14 of them RC-5 commands of system 25). Both bundle profiles are 60,473,344 bytes (77,824 more), dataVersion `8e76d32256fc`, `--verify` passes. **216 of the 1,170 keys map to the vocabulary (18.5%)**: `Standby_toggle`, `Mute_toggle`, `Info`, `Menu`, `Home`, `Enter`, `Volume Up (+)`, `Stop` and the transport keys do, and `Input01`, `Change input to Line 1 (input 1)`, `Toggle Tape Monitor on/off`, `Function key F1` and the zone and trim keys do not (D84's aliases, the owner's call); the `official` row stays at 18.6% and the corpus goes from 71.1% to 71.0%. **12 of the 18 files have a key the app's test order can pick** (`POWER` and `MUTE`: the six without are the CAP-2100, CDP-100, CP-500, CP-700, SSP-300 and SSP-600, whose action texts (`Toggle Standby/Operate`, `Volume up (volume +)`) are not the vocabulary's names, D84's aliases). **The search evaluation: first answer 87.5% to 86.9%, first five 97.8% to 96.9%**, the 44th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the ten misses involves a Classé model. **Four queries of the classes that should answer first answer second** (`magnavox 00 t 103 agsa 02 easy functions`, `CYBERHOME DVR-1600`, `OPTICUM HD-9600` and `DENON Receiver DRA-275RD (proably others`): each has a model of the brand that the query also matches ahead of the device's (`Magnavox`, `Opticum HD-9600`, `Denon Receivers`), the same as before this section. **Not proven:** that the six documents of 2005 to 2008 are what the products send now; that the carrier of the RC6 lists, which they state, is the one the units use (the HEX PDFs' Pronto says 56 kHz too); that the SSP-300 and SSP-600, the CDP-102, 202, 300 and 502 and the CDT-300 each send every code of their shared table.

## 84. Denon's master IR hex spreadsheet

Denon's US site published for custom installers a spreadsheet of every command of its receivers, players and amplifiers with the Pronto hex of each, **`Denon Master IR Hex`** (the D+M Group's counterpart of the Marantz master of §29). **It is read: 1 document of 52 sheets, 57 files and 2,770 keys, 2,726 Plausible and 44 Untested.** The sheets are the receivers' of 2008 and after (`AVRxx08`: for each of the four remote IDs the main zone, zones 2 to 4, the tuner, network audio, the iPod, XM and Sirius), the Sharp and Kaseikyo codes of the receivers of 2007 and earlier, and the CD, Blu-ray and network players, the DRA receivers, the RCD-N7 and the D-M33. **2,770 of its 2,780 Pronto hex decode to a `Denon` (452) or `Denon-K` (2,318) code**; the ledger had both protocols' codes of §81's AVR-4311 list and of the JP1 and IR Blaster upgrades, and holds **229 of the master's 231 distinct Sharp codes and 433 of its 2,036 Kaseikyo ones: the rest are new, mostly the codes of the remote IDs 2 to 4**. The manufacturers' tables (§29 and the sections after it) are now 748 files and 58,448 keys.

### D324 — The document, how it is pinned, and the licence

`assets.denon.com/DocumentMaster/us/Denon Master IR Hex.xls` is **2,041,344 bytes** (an Excel 97 workbook of 52 sheets of command names and Pronto hex, a `Legend` sheet and an empty one), and **Denon's site no longer has it** (the address answers a redirect to the home page): the Wayback Machine's capture of 5 May 2022 (the capture of 11 March 2022 is the same bytes), the snapshot named in its URL (`id_`, the raw bytes), fetched on 2026-10-16 (`tools/fetch_official.py denon`) and pinned as `Denon_Master_IR_Hex.xls` beside §81's PDF. **The archive has earlier versions that are not pinned:** `usa.denon.com`'s of March 2014 (whose main zone sheet has 207 rows where this has 330), a `Denon Master IR Hex Commands 2014.xls` and one of 2016, and **about 75 per-model IR code PDFs of `assets.denon.com`** (the AVR-1508 to AVR-5805, the xx11 to xx13 and the DNP, DCD and DRA ones, 2003 to 2013, 0.5 to 4 MB each, in the layout of §81's AVR-4311 list: TODO). **Licence:** none of the notice words in it (searched as D290 did, over every cell): public under D110. Each sheet is converted to a CSV (a sheet's cells, as every maker's spreadsheets are).

### D325 — The rule

**A row with a Pronto hex is a key, named by the last cell before it** (a Sharp or Kaseikyo sheet has `Device Code` and `Function#` before the name, the CD and Blu-ray sheets a blank), and **a row with a name only below the title and the column heads is the heading of the rows under it** (`Power`, `Source Input`, `Surround Modes (DSP Modes)`: the citation names it). **The hex is decoded, not carried** (as Emotiva's, D311, and Rotel's, §69), as a `Denon` frame (two frames of 16 pairs, a mark and a space of three marks for a 0 and seven for a 1, the second with the function inverted and the extension bits 3: the device is the 5 address bits and the function the 8 data bits, each first bit first) **or a `Denon-K` one** (a leader of eight units and four, 48 bits whose space is a mark for a 0 and three for a 1, the vendor bytes 0x54 and 0x32, a parity nibble of 0 and a parity byte that is the exclusive or of the 3rd, 4th and 5th bytes: `GENRE1` the device, `GENRE2` the subdevice, bits 29 to 40 the function), **and every string that is read passes every one of those checks**. The frequency word of the capture (0x006B to 0x0070) is in the citation and is not kept: **the carriers are the protocols' own** (38 kHz and 37 kHz, as §81's files have). **Ten strings are neither**, five of the main zone ID#1's (two of 49 words and one of another shape, two with a leader that is not eight units and four) and five of the RCD-N7's (one of Sharp's size whose second half is not the first's inverse and four of another protocol): the report lists them, and nothing is carried. **A file for each sheet and protocol**: a sheet with both (`Main Zone ID#1` has 119 Sharp keys and 146 Kaseikyo) is two, `AVRxx08 Main Zone ID#1` and `AVRxx08 Main Zone ID#1 (Kaseikyo)`; a sheet with Kaseikyo codes only is that one. **The model is the sheet's own**: `AVRxx08 Main Zone ID#1` to `#4`, `Zone 2` to `Zone 4`, `Tuner`, `Network Audio`, `iPod`, `XM`, `HD Radio` and `Sirius` (the legend's names), `AVRxx07 and earlier Sharp Device 2`, `4` and `12` and `Kaseikyo 4.1` to `7.8`, and `RCD-N7`, `CD Player`, `DBP-1611UD`, `BD Players`, `S5-BD`, `ASD-51NW`, `D-M33 CD Receiver` and `DRA`.

**Untested are 44 keys, all of one reason: a code that the sheet gives to two keys** (D295's rule): the Zone 2 ID#1's 18 (`Favorite 3` and `Favorite 4` and others), the main zone ID#1's 14, Zone 3's 4, the tuner's, the HD Radio's, the S5-BD's and the Sharp Device 12's 2 each (`Ch. Select` and `Ch. Volume up`). **The Sharp and Kaseikyo sheets give each row's device code and function number beside the hex, and the hex says the same in 481 of the 482 rows**; the one that differs is the Sharp Device 12's `Ch. Select`, whose Function# 175 is 176 in the hex (and that is one of the two keys with a shared code).

### D326 — The rule checked, what it does to the rest, and what is not proven

- **Against §81's AVR-4311 list**: **150 of its 153 Sharp codes are in the master** (111 of them in `Main Zone ID#1`'s 116) and **187 of its 377 Kaseikyo ones** (68 of 142 in `Main Zone ID#1`'s): the list is the AVR-4311's of 2010 and the sheets the xx08 line's, so they have different commands. **Of the 30 names the two share, 29 have the same code** (`Internet Radio` is 691 in the list and 741 in the master: the AVR-4311's own).
- **Against the other sources' Denon remotes**: 229 of the master's 231 distinct Sharp codes are held (the JP1 upgrade of the AVR3310 has 150 of the AVR-4311's), and 433 of its 2,036 Kaseikyo ones. **The master has the codes of the remote IDs 2 to 4 that §81's list writes with `*`** (the Kaseikyo codes with the subdevice 2 are 508 distinct here, and the zones', network audio's and others' have theirs): the ID is the function's bits 10 and 11, which the sheets give for each ID.
- The corpus is **17,126 remote files and 707,868 keys** (57 and 2,770 more), 5,465 brands, 282,781 models (108 more) and 183,574 signals (1,565 more: of the 2,267 distinct codes, 702 were signals the ledger already had). Both bundle profiles are 60,919,808 bytes (446,464 more), dataVersion `5f5db5feda9a`, `--verify` passes. **709 of the 2,770 keys map to the vocabulary (25.6%)**: `Power`, `Mute`, the volume keys, the cursor keys, `Enter`, `Menu`, the transport keys and `Dimmer` do, and the zones' commands, `Quick Select 1`, the surround modes, `Audyssey` and `Dolby Atmos` ones and the input names do not (D84's aliases, the owner's call); the `official` row goes from 18.6% to 18.9% and the corpus from 71.0% to 70.8%. **24 of the 57 files have a key the app's test order can pick** (the 33 without are mostly the sheets of one function group: the tuner's, network audio's, the iPod's, XM's and zones 2 to 4's, and the older Kaseikyo sheets). **The search evaluation: first answer 86.9% to 88.4%, first five 96.9% (the same)**, the 45th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the ten misses involves a Denon model. **Two queries of the classes that should answer first answer second** (`SAMSUNG LE 4 GA 559 P 4 F/XKU-2` and `CYBERHOME DVR-1600`: each has a twin entry of the same brand ahead of the device's), the same as before. **Not proven:** that a master of 2022 is what the receivers of 2008 and after send (the sheets say which IDs, not which models); that the Kaseikyo codes of IDs 2 to 4 are what a receiver set to that ID answers (the ledger's `Denon-K` carries the function's two ID bits, and the codes decode and check); the ten strings that do not decode; whether the 2014 and 2016 versions and the per-model PDFs have codes this one lacks.

## 85. Cyrus Audio's RC-5 code tables

Cyrus (the English maker of the Cyrus amplifiers, CD players and tuners and the DVD 7) published in its downloads folder a one-page PDF of the remote codes of each product: **a table of the RC-5 codes 0 to 63 (and 64 to 127 beside them where the table has two) with a column of the names Philips' RC-5 standard gives the codes for a class of device and a column of the names the Cyrus product gives them**. **They are read: 13 documents, 20 files and 435 keys, all Plausible.** The ledger had two Cyrus remotes (JP1 upgrades: the aCA7.5 Pre's 16 keys and an AM/FM tuner's 22, both RC-5), and they hold **15 of the aCA7.5 table's 16 codes and 19 of the original tuner table's 20**. The manufacturers' tables (§29 and the sections after it) are now 768 files and 58,883 keys.

### D327 — The documents, how they are pinned, and the licence

The folder (`cyrusaudio.com/cms_downloads/`) **is gone: the addresses answer 404 on `https://www.cyrusaudio.com/`**, so the documents are the Wayback Machine's copies as InFocus's (D294) and the others' since are: the snapshot named in its URL (`id_`, the raw bytes), **6 March 2007 (3 December 2008 for the 5, 6, 7 and 8's)**, fetched on 2026-10-16 (`tools/fetch_official.py cyrus`). **Thirteen of the folder's fifteen PDFs are pinned** (3,469 to 22,275 bytes, made between 2000 and 2007): `ACA75PreX`, `AV5&AV8`, `AVMaster`, `CD`, `Cyrus3`, `Cyrus3i`, `Cyrus5&6&7&8`, `CyrusPre`, `DVD7`, `FM7FM7.5andFMX`, `Quattro`, `dad1` and `originaltuner` (`remotecodes.pdf` each). **Not pinned: the DAB 8's and DVD 8's** (2007: `DAB 8.0 RC5 code` and `DVD 8.0 RC5 codes`, whose tables say no system code, so no key's device is known: TODO). The tables' cells are converted to CSV by place (`cells`, D283) and pinned as every maker's are. **Licence:** none of the notice words in them (searched as D290 did): public under D110.

### D328 — The rule

**A key is the ledger's `RC5`** (36 kHz): **the system code of the heading is the device** (`RC5 Amplifier 1 (16)`: 16; the amplifiers 16 and 19, the CD players 20, the tuner 17, the DVD 7 25, the Quattro 21), **the row's code the function** (RC-5 has 7 command bits, and the second table of the AV5 and AV8's, the Cyrus 3's and the DVD 7's is the codes 64 to 127), **and the label the product's name for it**: `Cyrus 3i`'s `Standby`, `Volume +`, `Input inc`, `Phono`, `Power on`. **The columns are told apart by place**: a name left of the middle of the two headings is the standard's (`Mute/Demute`), right of it the product's (`Mute`), and the standard's name is in the citation. **A product with no name for a code has no key for it**: the 3i has 16 keys of the 64. **A file for each product the document names**: the aCA7.5 and PreX, AV5 and AV8, FM7, FM7.5 and FMX (the document names the FM7 and FM7.5 and its address the FMX too) and **the Cyrus 5, 6, 7 and 8 (the document names the Cyrus 5 and Cyrus 7, and its address is `5&6&7&8`: the citation says so)**, the `CD player` (the table's `Cyrus CD`), `dAD1`, the `DVD 7`, `Quattro`, `Pre`, `3`, `3i`, `AV Master` and the `original tuner` (its heading is `Mission Cyrus tuner`), **the same keys for the products of one document**. A name of the AV5 and AV8's table marked `*` (software version 1.2 and later) or `**` (AV8 only) keeps its mark off the label, and the citation names the note.

### D329 — The rule checked, what it does to the rest, and what is not proven

- **Against the ledger's JP1 Cyrus remotes**: **the aCA7.5's table and the JP1 aCA7.5 Pre have 15 of 16 codes, under the same names** (`Standby` 12, `Mute` 13, `Volume +` 16, `Input inc` 24, `Balance select` 40, `CD` 49, `Tuner` 50 and the others); **the sixteenth is `Power on`: 58 in the table, 59 in the JP1 file and the 3i's table**. **The original tuner's table and the JP1 AM/FM tuner have 19 of 20 and 22**: the digits, `Review`, `FM/AM`, `Manual` and the volume keys of system 17; the one that differs is `Store` (17 in the table, 10 in the JP1 file: another tuner's remote).
- **The documents against each other**: the amplifiers' tables are one family of codes (`Standby` 12, `Mute` 13, `Volume +` 16 and `-` 17, `Power on` 58 or 59, `Power off (standby)` 62) with the system 16 for the aCA7.5, the 3, the 3i, the 5 to 8, the Pre and the FMs, 19 for the AV5, AV8 and AV Master, which add the surround and tone keys.
- The corpus is **17,146 remote files and 708,303 keys** (20 and 435 more), 5,465 brands (Cyrus was one), 282,821 models (40 more) and 183,582 signals (8 more: the 435 keys are 192 distinct RC-5 codes, **and RC-5 codes of the systems 16 to 21 and 25 are other brands' remotes' too**, so all but 8 were signals the ledger had). Both bundle profiles are 60,940,288 bytes (20,480 more), dataVersion `c0b49f7f58aa`, `--verify` passes. **237 of the 435 keys map to the vocabulary (54.5%)**: `Standby`, `Mute`, `Volume +` and `-`, the digits, `Play`, `Stop`, `Pause`, `Next`, `Previous` and `Display` do, and `Input inc`, `Balance select`, `Power on` and `Power off (standby)`, the `Fast search` keys and the surround keys do not (D84's aliases, the owner's call); the `official` row goes from 18.9% to 19.2% and the corpus stays at 70.8%. **All 20 files have a key the app's test order can pick.** **The search evaluation: first answer 88.4% to 85.6%, first five 96.9% to 97.8%**, the 46th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the seven misses involves a Cyrus model. **Two queries of the classes that should answer first are misses of the first five** (`dsr 5000 fta` of the case class and `GRUNDIG CINARO-82FLATMFW8252108DOLBY` of the punctuation class: each has other brands' models of one name ahead of the device's) **and six answer second** (`GRUNDIG ELEGANCE 82 FLAT MFW 82 - 2501/8 DOLBY`, `CYBERHOME DVR-1600`, `EPSON POWERLITEW-6`, `7500 FTA`, `Hegel H590 (2019 table)_questionable` and `PHILETTA 1221`): the same with the catalog before this section. **Not proven:** that a table of 2000 to 2007 is what the products send now; that the Cyrus 6 and 8 send the codes of the 5 and 7's table (its address says so, the document does not); that the FMX has the FM7's; that the codes marked with the standard's name only (the keys the product does not name) are no keys of the product.

## 86. InFocus's IN5312a and IN5316HDa IR code sheets

The 36 InFocus documents of §75 to §77 are the Wayback Machine's copies of the site the company has left. **Two more one-page PDFs are served by the image host of its current site: `IN5312a_IRCodes.pdf` and `IN5316HDa_IRCodes.pdf`** (`res.cloudinary.com/iwh/image/upload/q_auto,g_center/assets/1/26/`), a web search found the first and the same name with the other model the second. **They are read: 2 documents, 2 files and 58 keys, all Plausible**, and the table of both is the IN5310's (§75) key for key. The manufacturers' tables (§29 and the sections after it) are now 770 files and 58,941 keys.

### D330 — The documents, how they are pinned, the licence and the rule

**The files are live, so they are pinned as live files** (`tools/fetch_official.py infocus --only IN5312a_IRCodes.pdf IN5316HDa_IRCodes.pdf`, fetched on 2026-10-16): the manifest has the address, the size (173,036 and 173,029 bytes), the SHA-256, the retrieval date and the server's `Last-Modified` (3 January 2020), **no snapshot: the Wayback Machine holds no copy of either address** (its index answers nothing for them), and the citation says `(retrieved 2026-10-16)` as the other makers' live files' do. The PDFs were made on 5 December 2011 and last changed on 5 June 2014. **About 150 other names on that host's folder were tried (the IN5314a, IN5318HDa, the IN5100, IN5300, IN5500 and IN1100 families' and the "a" series' other names, the IN8606HD and SP1080, each with `_IRCodes` and `-IRCodes`): none exists**, so these two are all this lead has. **Licence:** none of the notice words (searched as D290 did): public under D110.

**A sheet is `Remote IR Information`, `Standard consumer IR (NEC framing) Device ID:0x4E87` and 30 rows of a number, a key name, what it does and its data code** (`1 Power On  Turns the projector on.  3Ah`); the 30th, `Light`, is `n/a` and sends no code, so **29 keys**. The text of the two sheets is the same (the files differ in the date they were saved). **The rule is the IN5310's (D295's reader `numbered`, now on two more files):** `NEC1`, the document's customer code the device 0x87 and the subdevice 0x4E (the low byte first, as the 0x4E87 documents are read), the data code the function, the key `^ v < >` named Up, Down, Left and Right. **The document names no model: the file's name is the only place that does**, and the citation of each key says so (`the document names no model: the file is IN5312a_IRCodes.pdf on InFocus's image host, and its table is the IN5310's`, a `note` of the document).

### D331 — The rule checked, what it does to the rest, and what is not proven

- **Against the IN5310's table**: **all 29 (name, code) pairs are the same** (a test, key for key), and the IN5310's codes are those the ledger's InFocus remotes at 0x4E87 were checked against (§75: 215 of 219 keys a code some table lists), so the 58 keys are **no signal the ledger did not have**.
- The corpus is **17,148 remote files and 708,361 keys** (2 and 58 more), 5,465 brands, 282,825 models (4 more) and 183,582 signals (none more). Both bundle profiles are 60,940,288 bytes (the same size), dataVersion `c4b0a44da43a`, `--verify` passes. **28 of the 58 keys map to the vocabulary (48.3%)**: `Power On` and `Power Off`, `Source`, `Info`, `Mute`, the four arrows, `Menu`, `Blank`, `Volume+` and `Volume-` and `Freeze` do; `Display Mode`, `Contrast`, `Brightness`, `Select`, `Resize`, `Auto Image`, `Overscan` and the eight `Source` keys do not (D84's aliases, the owner's call). The `official` row stays at 19.2% (11,328 to 11,356 keys mapped) and the corpus at 70.8%. **Both files have a key the app's test order can pick** (`POWER_OFF`).
- **The search evaluation: first answer 85.6% to 85.6%, first five 97.8% to 96.9%**, the 47th draw (the draw moves because the models' ids do, §24): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the ten misses involves an InFocus model. **Two queries of the classes that should answer first are misses of the first five** (`dsr 5000 fta` and `GRUNDIG CINARO-82FLATMFW8252108DOLBY`) **and six answer second** (`GRUNDIG ELEGANCE 82 FLAT MFW 82 - 2501/8 DOLBY`, `CYBERHOME DVR-1600`, `EPSON POWERLITEW-6`, `FTA 8000`, `101 655 40` and `Hegel H590 (2019 table)_questionable`): the same with the catalog before this section.
- **Not proven:** that the two remotes send what a sheet of 2014 lists now; that a file named for a model is the sheet of that model (the sheets say the same, and nothing in them names a model); and that the host has no other sheet (the names tried are a guess at its pattern).

## 87. Yamaha's standard and extended IR code tables

Yamaha wrote for its custom installers a table of IR codes for each family of its receivers and amplifiers: **a row for each function (`POWER ON`, `STANDBY OFF`, `HDMI-1`, `SCENE 3`, `PROGRAM Hall in Vienna`, `MUTE ON (-40dB)`...) and zone it is addressed to (`MAIN`, `ZONE2`, `ZONE3`, `ZONE4`, `ALL`), two codes, `Code (ID1)` and `Code (ID2)`, and a `YES` or `NO` for each model of the family**. The *standard* tables are the codes of the remote's keys and the *extended* ones the discrete functions. **Six PDFs are read: 14 models, 28 files and 7,008 keys** (the RX-V385, RX-V485, RX-V585, RX-V685, RX-A780, RX-A880, RX-A1080, RX-A2080, RX-A3080, CX-A5200, WXA-50, WXC-50, XDA-QS5400RK and XDA-AMP5400RK, each with its ID1 and its ID2 codes), **all Plausible but 60**. The ledger's other sources already hold the codes of Yamaha's receivers (the IR Blaster database's remotes cite the same tables), and they hold **960 of the 1,062 distinct codes of these**: what is new is the models' names, a table's word for each, and 102 codes. The manufacturers' tables (§29 and the sections after it) are now 798 files and 65,949 keys.

### D332 — The documents, how they are pinned, the licence and the sheet mode `grid`

The PDFs are on the support-documents folder for amplifiers of **SnapAV's site (`snapav.com`, an American distributor of Yamaha's)**, and Yamaha's own site no longer has them. They are the Wayback Machine's copies, each the snapshot named in its URL (`id_`, the raw bytes; **the live files are the same bytes: the SHA-256 of each was compared on 2026-10-16**): `Extended_IR_Codes_RX-V685RX-A780-RX-A3080CX-A5200_6.pdf` (190,720 bytes, made 13 December 2018, snapshot of 31 October 2020), `Standard_IR_Codes_RX-V685RX-A780-RX-A3080CX-A5200_4.pdf` (126,823), `RX-V385_RX-V485_RX-V585_IR_Codes_2.pdf` (483,408, made 29 June 2018: both kinds in one document of seven pages), `WXA-50_WXC-50_Extended_IR_Codes_1.pdf` and `..._Standard_IR_Codes_1.pdf` (75,281 and 68,983; 7 October 2016; snapshots of February 2024) and `XDA-QS5400_XDA-AMP5400_IR_Commands.pdf` (101,839; 19 October 2018). Fetched on 2026-10-16 (`tools/fetch_official.py yamaha`). **Not pinned:** SnapAV's `CD-C600_IR_Commands` (one page, `Copyright© 2009 YAMAHA CORPORATION`: the private repository's, TODO), its `MAR-MM7055-IR-Codes` (a Marantz power amplifier's manual, `(c)`), and its `AV7706PresetCodes` and `AV8805_Remote_Control_Preset_Codes` (the setup codes of a universal remote, no IR). **Licence:** none of the notice words (copyright, all rights, reserved, ©, (c), confidential, reproduc, trademark: searched as D290 did) in the six: public under D110.

Each table is a ruled grid with merged cells that the text layer does not tell apart, so **the sheet tool has a mode `grid`** (`tools/official_sheets_to_csv.py`): for each row of each page's tables, `page, y` and a cell for each column. The group, the device and the zone are **the cell the row is in** (a cell a merged cell covers holds the merged cell's text; one the library finds no merge for is empty, as the document shows it); the codes and the models' marks are **the words on the row's own height** (a merged cell of two rows may hold two codes, one on each row's line); the two columns of names are given both ways.

### D333 — The rule

**A code is Yamaha's own notation, which its IR Hex Converter (`yamaha.com/ypab/irhex_converter.asp`, which the Wayback Machine has, with the script) writes down: `7E-2A`, `7A-453A`, `7F01-6C13`, `7E-2AD4`.** The first group is the custom code and the second the data; **a group of two digits is a byte and its complement** (`7E` is `7E 81`) and a group of four is two bytes as written (`7F01` is `7F 01`, `2AD4` is `2A D4`); the four bytes are sent in the order written, NEC's frame (the converter's Pronto has the intro of 34 pairs and the ditto repeat). **A key is the ledger's `NEC1-f16`** (NEC1's frame whose fourth byte is a field of its own, a protocol D18's gate admitted: the function is the sixteen bits `F:E`): the custom code's two bytes the device and the subdevice, the data and its check byte the function, **at 38 kHz, which the converter writes (the Pronto's `006D`)** and the ledger's other Yamaha NEC remotes do not (they have NEC1's nominal 38.4). `7E-2A` is device 0x7E, subdevice 0x81, function 0x2AD5 (a plain NEC1's, whose frame NEC1-f16 renders when `E` is `~F`), `7E-7E80` is 0x7E, 0x81 and 0x7E80, `7F01-6C13` is 0x7F, 0x01 and 0x6C13. **A file for each model and each of the two ID columns** (`RX-V685 (ID1)`, `RX-V685 (ID2)`): the document says only `ID1` and `ID2`; the ID is a setting of the receiver and its remote, and the tables keep one rule between the columns (D334).

**A model's file has the keys of every table that names it** whose cell for it is `YES` (the standard table's and the extended one's: the RX-V685's two tables are 284 keys; the RX-V385's one document is 158; the XDA-QS5400RK's 152 and the XDA-AMP5400RK's 28; the WXA-50 and WXC-50 share a column and so a table: 62 each). **A function is a run of rows of rising zones under one name**, which the table writes in cells the zones' rows share, and a key's label is the name with the device when it is not the receiver (`AUTO TUNING UP (TUNER DAB)`) and the zone when it is not the main one (`POWER ON (ZONE2)`); a name two functions share keeps its group. **The reader handles what the tables do to a name:** a blank cell is the function above (over a page break or a merged cell the library finds no cover for); a first line repeated with a blank second is the function above (`SCENE`, `5`); a name that goes on over the rows (`PAUSE`, `or`, `BAND`) is one name (`PAUSE or BAND`); a remark in a zone's row (`If Another input, Swich input to TUNER`, `and select band FM`) is that row's `extra`, which the citation quotes; **two functions of one name that alternate over the zones (`ENHANCER ON`, `OFF`, `ON` of `ZONE2`...) are two functions; and where one merged cell holds the names of several functions (MUTE's `ON (FULL)`, `ON (-40dB)`, `ON (-20dB)`, one cell of nine rows) each function's name is the words on its own rows.** A name keeps the document's own text, a region mark among it (`SUB INPUT Spotify *Except TK`) and a name the document cuts short (`SUR.` of `SUR. DECODE` in the 2018 extended table). **A mark `YES*J` (the table's legend: `J: Japan model`) is the Japan model's, so the key is Untested (60 keys, 3 in each of 20 files)**; `YES*No key` and `YES(No Key)` (the receiver has the code, its remote no key for it) are Plausible, and the citation says so. A function two of the tables list with one code is one key (the citation says where else it is), and a code two names share would make both Untested (none does).

### D334 — The rule checked, what it does to the rest, and what is not proven

- **One rule between the two columns, in every row**: **the ID2 code of a row is its ID1 code with the low bit of its last byte flipped** (`7E-2A` and `7E-2AD4`: `D5` and `D4`; `7F01-6C13` and `7F01-6C12`), in 1,316 of the 1,317 rows (the one left has no ID2 code: its cell is blank). The reader does not assume it and the tests check it key for key (**3,504 pairs**).
- **Against the ledger's other sources: 960 of the 1,062 distinct frames are in the ledger's other Yamaha NEC remotes** (the IR Blaster database's, the JP1 upgrades' and LIRC's), 90.4%, **under the same names** (`POWER ON` and its zone rows, `CURSOR UP (ZONE2)`, `SCENE 1`, `VOLUME UP`, `Spotify *Except TK`): the IR Blaster remotes keep the table's `id1 - ZONE3 - 3` in their key labels, so they come from the same tables. **A code is not one function across the tables**: the extended tables of 2016 and 2018 give the code 0x9AE5 of `SUB INPUT` to `Radiko *J` in one and to `Qobuz *BGEF` in the RX-V485's, so each file keeps its own table's name for a code.
- The corpus is **17,176 remote files and 715,369 keys** (28 and 7,008 more), 5,465 brands (Yamaha was one), 282,867 models (42 more) and 184,637 signals (1,055 more: the 1,062 distinct frames are at 38 kHz and the ledger's other Yamaha remotes at 38.4, so 960 of them are the same codes as signals the ledger had, at another carrier). Both bundle profiles are 61,358,080 bytes (417,792 more), dataVersion `4c4ae8874d89`, `--verify` passes. **880 of the 7,008 keys map to the vocabulary (12.6%)**: `POWER ON`, `MUTE TOGGLE`, `VOLUME UP` and `DOWN`, `STOP`, `USB`, `BLUETOOTH`, the cursor keys, `ENTER`, `RETURN` and the colour keys do, and the zones' rows (a label with `(ZONE2)`), the scenes, the sound programs and the `SUB INPUT` services do not (D84's aliases, the owner's call); the `official` row goes from 19.2% to 18.5% and the corpus from 70.8% to 70.2%. **All 28 files have a key the app's test order can pick** (`POWER_ON`). **The search evaluation: first answer 85.6% to 85.6%, first five 96.9% to 96.9%**, the 48th draw: the same ten misses as §86's, **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the ten misses involves a Yamaha model.
- **Not proven:** that a receiver answers the code its table lists (a table of 2016 to 2018, with no word on what `ID1` and `ID2` are: the setting is the reading of the one rule between them), and which of the two a receiver is set to (the files are named for the column); that the models of one column of a table have identical codes in every region (`*U`, `*B`, `*G`... marks are in the names, and the regional models' codes are the one code); that a name the table cuts short is the function the rest of the family's tables name; which models are the table's `RX-A780` beyond its column (it shares the RX-V685's, and the two files are the same keys).

## 88. The RC-5 code sets of Harman's JBL Synthesis and Lexicon custom installation notes

JBL Synthesis's AV receivers and processors (the SDR-35, SDR-38, SDP-55 and SDP-58) and Lexicon's MC-10, RV-9 and RV-6 are built on Arcam's platform, and their `Custom Installation Notes: Serial programming interface and IR remote commands` are Arcam's document (the same `SH289E` as Arcam's AVR11 to AVR41 of §64) with the product's name: **its last two pages are the RC-5 code set**, a table of functions with the code as `system-command` in decimal and as `Data1 - Data2` in hex, which §64's reader reads as it reads Arcam's own. **Two documents are read: 7 files and 828 keys, all Plausible but 28**. **Every one of the 118 (JBL) and 114 (Lexicon) distinct codes is in the public Arcam tables already**, so these files are the products' names for codes the ledger holds: what is new is the 14 models, with a table of the maker's own for each. The manufacturers' tables (§29 and the sections after it) are now 805 files and 66,777 keys.

### D335 — The documents, how they are pinned, the licence and the rule

**JBL Synthesis** (`jblsynthesis.com`): `RS232_SDR35_38_SDP55_58_SH289E_E_2Jun21.pdf` (740,534 bytes, 41 pages, `SH289E Issue E`, made 2 June 2021, the file's `Last-Modified` 26 July 2022), the Wayback Machine's copy of 17 June 2024 (`id_`, the raw bytes: **the live file is the same bytes, compared by SHA-256 on 2026-10-16**). Its earlier issue, `RS232_SDR35_SDP55__SH289E_D_29Oct20.pdf` (issue D, 29 October 2020, for the SDR-35 and SDP-55), **has the same 120 RC-5 rows, row for row, and is not pinned**. **Lexicon** (`lexicon.com`, `SH274L Issue B`): `RS232_Protocol_Documentation.pdf` for the MC-10, RV-9 and RV-6 (607,145 bytes, 28 pages, made 18 October 2018, `Last-Modified` 7 June 2021): the live file, **the Wayback Machine holds no copy of that address**, so the manifest has no snapshot and the citation names none. Fetched on 2026-10-16 (`tools/fetch_official.py harman`). **Licence:** none of the notice words (searched as D290 did) in either; the one `reserved` is the table of `Reserved Commands` of the serial protocol: public under D110. The tables are read from the `tables` sheet as §64's are.

**A key is the ledger's `RC5`** (36 kHz): the system code the device (16, and 23 for `Zone 2`), the command the function, the label the table's name for it (`Standby`, `Navigate Up`, `Zone 2 Vol+`, `Auro Native`). **A file for each product the title names, the same keys: `Synthesis SDR-35`, `Synthesis SDR-38`, `Synthesis SDP-55` and `Synthesis SDP-58` of manufacturer `JBL` (120 keys each: the document has one table for the four) and `MC-10`, `RV-9` and `RV-6` of `Lexicon` (116 each)**; the controls of a JBL file are `JBL Synthesis SDP-58`. A row whose decimal and hex are not one code, a digit row whose command is not the digit (§64), and **two rows with one code** make their keys Untested: **the table gives `HOME` and `Yellow` 16-43, and `INFO` (`Cycle between VFD information panels` in Lexicon's) and `Blue` 16-55 (4 keys in each file, 28 in all)**.

### D336 — The rule checked, what it does to the rest, and what is not proven

- **Against the public Arcam tables (§64): 118 of the JBL document's 118 distinct codes and 114 of Lexicon's 114 are Arcam's**, and the functions agree (`Standby` 16-12, `Mute` 16-13, `Navigate Up` 16-86, `Zone 2 Power Off` 23-124...). The JBL document's rows and issue D's are the same, and Lexicon's has ten rows the JBL one has not (`USB`, `VCR` and `Zone 2 VCR`, `Logic7 Immersion`, `DTS Virtual:X`, `Cycle between output resolutons`: a processor of 2018, not 2021).
- The corpus is **17,183 remote files and 716,197 keys** (7 and 828 more), 5,465 brands (neither JBL nor Lexicon is new), 282,881 models (14 more) and 184,637 signals (none: every code is a signal the ledger had). Both bundle profiles are 61,390,848 bytes (32,768 more), dataVersion `ec22c01c0da5`, `--verify` passes. The `official` row stays at 18.6% mapped (12,477 of 66,777 keys) and the corpus at 70.2%. **All 7 files have a key the app's test order can pick** (`POWER`, the table's `Standby`); 241 of the 828 keys map to the vocabulary (29.1%). **The search evaluation: first answer 85.6% to 87.2%, first five 96.9% to 95.9%**, the 49th draw (the draw moves because the models' ids do, §24; the models' ids moved by 14): **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the thirteen misses involves a JBL or Lexicon model. **Two queries of the classes that should answer first are misses of the first five** (`dsr 5000 fta` and `GRUNDIG CINARO-82FLATMFW8252108DOLBY`) **and five answer second** (`GRUNDIG ELEGANCE 82 FLAT MFW 82 - 2501/8 DOLBY`, `CYBERHOME DVR-1600`, `EPSON POWERLITEW-6`, `Hegel H590 (2019 table)_questionable` and `MP 1000`).
- **Not proven:** that a JBL or Lexicon product accepts every code its table lists (the table is the platform's, and the products' remotes send a part of them: the document says the others are for custom install); that the SDR-38 and SDP-58 have the SDR-35's and SDP-55's set beyond the one title; that Lexicon's three products have one set (the title names three and the table is one).

## 89. Marantz's FY20 receivers' IR command workbook

§57 to §62 read three of Marantz's workbooks of the commands of its AV receivers (`marantz.com`'s archive downloads: 2012, 2014 and FY18). **A fourth is read: `Marantz_FY20_SR_NR_IR_CODE_V03_20190827182355425.xls`, the chart of the SR6014, SR5014, NR1710, NR1510 and NR1200 (27 August 2019, by Marantz America): 5 models, 25 files and 2,209 keys, all Plausible**, by the same reader and rules as the others': a model's file of the Pronto hex Marantz publishes for the basic commands, its `[RC-5]` and `[RC-5x]` files, the `[37.3 kHz]` files of the strings at another carrier and its `[direct]` file. No model of the earlier workbooks is in it. The manufacturers' tables (§29 and the sections after it) are now 830 files and 68,986 keys.

### D337 — The document, how it is pinned, and the licence

The workbook (384,512 bytes, two sheets: `AVR Commands`, 587 rows, and `Direct Vol.`, 373) is **on the protocols-and-drivers folder for amplifiers of SnapAV's site** (`snapav.com`, an American distributor of Denon and Marantz), beside Denon's AVR-X IR code documents of 2020; it is not among the downloads of `marantz.com` the other three came from. It is the Wayback Machine's copy of 22 November 2025 (`id_`, the raw bytes: **the live file is the same bytes, compared by SHA-256 on 2026-10-16**), the file's `Last-Modified` 20 May 2022 and its own date 27 August 2019 (`V03`). **Pinned with `--only`** (`tools/fetch_official.py marantz --only ...`): the manifest's date stays 2026-10-08 for the three, and the workbook has its own, 2026-10-16. **Licence:** none of the notice words (searched as D290 did) in either sheet: public under D110. **A citation names the document's host and snapshot** (`snapav.com Marantz IR code workbook/...@9ebaf099 (retrieved 2026-10-16, from the Wayback Machine snapshot 20251122141557)`): the reader has a function for it, which gives the three older documents' citations as they were.

### D338 — The rule, checked, and what it does to the rest

- **The rule is §57's to §60's**: a command is RC-5 (system and command), RC-5x (the extension byte: `RC5x`, the system the device, the command the subdevice, the extension the function) or the chart's own Pronto hex where it has one; the one column of `Direct Vol.` (`FY18AVR`, which the workbook has kept) is every model of the workbook, whose `[direct]` file is 177 keys. **The files**: SR6014 138 + 5 + 24 + 151 + 177 keys, SR5014 137 + 2 + 24 + 137 + 177, NR1710 134 + 2 + 24 + 137 + 177, NR1510 88 + 2 + 23 + 70 + 177 and NR1200 107 + 2 + 26 + 91 + 177. **Refused (report): 5 rows whose extension is a range of values, a command for each, and 1 Pronto hex that does not compile** (`SURROUND MODE (Back)`, row 102: `repeat[70] is 0`).
- The corpus is **17,208 remote files and 718,406 keys** (25 and 2,209 more), 5,465 brands, 282,911 models (30 more) and 184,640 signals (3 more: nearly every code of a receiver of 2019 is a code of the receivers before it). Both bundle profiles are 61,485,056 bytes (94,208 more), dataVersion `6602859fac16`, `--verify` passes. 178 of the 2,209 keys map to the vocabulary (8.1%: the direct, RC-5x and 37.3 kHz files are keys of listening modes and levels); the `official` row goes from 18.6% to 18.3% and the corpus from 70.2% to 70.0%. **14 of the 25 files have a key the app's test order can pick** (`POWER`, `VOLUME_UP`: the main, RC-5 and some RC-5x files), **and 11 have none** (the five `[direct]` files, the five `[37.3 kHz]` files and the NR1510's `[RC-5x]`): the owner's decision of 2026-10-13 is that a remote with no test key is not hidden or demoted (the Marantz `[direct]` files). **The search evaluation: first answer 87.2% to 82.5%, first five 95.9% to 95.9%**, the 50th draw: **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the thirteen misses involves a Marantz model. **Two queries of the classes that should answer first are misses of the first five** (`dsr 5000 fta` and `GRUNDIG CINARO-82FLATMFW8252108DOLBY`) **and six answer second** (`GRUNDIG ELEGANCE 82 FLAT MFW 82 - 2501/8 DOLBY`, `CYBERHOME DVR-1600`, `EPSON POWERLITEW-6`, `Hegel H590 (2019 table)_questionable`, `M 61 - 1690 DPL` and `RC 1110(TV)`).
- **Not proven:** that a model of 2019 accepts every command its column marks `X` (the workbook is Marantz America's word, and the chart's own Pronto hex is carried as it is); that the FY20 workbook's columns are the only models of it (`NR1200`'s is a stereo receiver, listed beside them); and the Pronto hex of `SURROUND MODE (Back)` that the ledger cannot compile.

## 90. Denon's X-series remote code lists of 2020

§81 reads Denon's `AVR-4311CI/AVR-4311 Remote Code List` of 2010: the tables of the codes of the receiver's remote in **two formats**, *Sharp* (15 bits: the ledger's `Denon`) and *Kaseikyo* (48 bits: `Denon-K`). **Four lists of the same make are read here, Word documents of 2020, for nine products: the AVR-X2700H, its DAB version (`AVR-X2700H DAB`) and the AVR-S960H, the AVR-X3700H and AVC-X3700H, the AVR-X4700H and AVC-X4700H, and the AVR-X6700H and AVC-X6700H (the `AVR` is a receiver and the `AVC` a processor, which has no tuner). 18 files (a Sharp and a Kaseikyo one for each) and 4,547 keys, all Plausible but 27.** **One of the nine names was in the ledger** (`AVR-X2700H`, one of the controls of an IR Blaster remote of 3701 codes) and the rest were not, and the ledger held **all 137 distinct Sharp codes of the lists and 418 of their 454 distinct Kaseikyo ones** (they are the codes of the receivers before them, from the IR Blaster database, the JP1 upgrades and §84's master spreadsheet): what is new is the nine products' names with a table of the maker's own for each, and **36 Kaseikyo codes**, the tuner's keys (`TUNING UP` and `DOWN`, `PRESET CH UP`, `PRESET 1 / Numeric 1` to `Numeric 0`, `MODE AUTO/MANUAL`, `SEARCH`, `RT`, `PTY`), the speaker presets, the height and top channels' levels (`SHL`, `SHR`, `TS`, `Center Height LEVEL UP` and `DOWN`), `Video Mode Select Bypass`, `BAND DAB` (main zone and `ZONE2`) and `Bilingual Mode(toggle)`, and two the documents give wrongly (D340). The manufacturers' tables (§29 and the sections after it) are now 848 files and 73,533 keys.

### D339 — The documents, how they are pinned, the licence, the sheet mode `paragraphs` and the reader

**The documents** are on the protocols-and-drivers folder for amplifiers of SnapAV's site (`snapav.com`), the folder Marantz's FY20 workbook of §89 is on. They are the Wayback Machine's copies, each the snapshot named in its URL (`id_`, the raw bytes), **and the live files are the same bytes: the SHA-256 of each was compared on 2026-10-18**: `AVR-X2700H_S960H_IR_CODE_V01.doc` (1,294,848 bytes; the title `AVR-X2700H/AVR-X2700H DAB/AVR-S960H Remote Code List Ver. 01`, 12 May 2020; snapshot of 27 July 2022), `AVR-X3700H_AVC-X3700H_IR_CODE_V01_04062020.doc` (1,330,176; 12 May 2020), `AVR-X4700H_AVC-X4700H_IR_CODE_V01.doc` (1,531,392; 22 April 2020) and `AVR-X6700H_AVC-X6700H_IR_CODE_V01.doc` (1,443,840; 22 April 2020), the files' `Last-Modified` 20 May 2022 and their own dates 1 June 2020. **Pinned with `--only`** (`tools/fetch_official.py denon --only ...`): the manifest's date stays 2026-10-16 and each of the four has its own, 2026-10-18. **Licence:** none of the notice words (copyright, all rights, reserved, ©, (c), confidential, reproduc, trademark, proprietary: searched as D290 did) in the four: public under D110. **A citation** names the host (`snapav.com Denon IR code documents`), the file, its hash, the retrieval date and the snapshot, then the row's identifier and the cell's text (`row 'RCKSK0410001' ('ALL POWER ON/OFF', K4-1 : MAIN ZONE, bits 29 to 38 1000000000, the ID and parity bits '*')`). **Not read:** Denon's own site's per-model PDFs (`assets.denon.com`), which are a later lead.

**The sheet tool has a mode `paragraphs`** (`tools/official_sheets_to_csv.py`, which needs `olefile` for it): the text of a Word 97 to 2003 document is the pieces of its piece table in order, and a row of the CSV is a paragraph's text and how it ended, `cell` (the mark that ends a table's cell, and alone a row: an empty `cell` is an empty cell or a row's end) or `para`. The importer reads the CSV and does not need `olefile`; a test (which skips without it) reads each pinned document again and compares it with the CSV.

**The reader.** A **Sharp** table has a line `SYSTEM ADDRESS(C1~C5) : 01000 , EXTENSION BIT(C12,C13) : 11` and rows of two halves, `RCSHP0230016`, six data bits and a name (empty for a code the key table has none for): **131 to 137 of the 320 rows of each document are named**. A **Kaseikyo** group has a title (`MAIN ZONE`, `ZONE2`, `ZONE3`, `ANALOG TUNER`, `HEOS MUSIC`), a `REMOTE ID SET: 1` line, the numbers 1 to 28 and **the 28 bits of its header** (the vendor 0x54 0x32, a 0 parity nibble, `GENRE1` and `GENRE2`), then rows of one `RCKSK0410001`, **bits 29 to 38 (ten data bits), ten `*` (the ID bits and the parity byte) and a name**: 343, 358, 436 and 441 rows, all named. **The reader checks what each row says of itself**: a Sharp row's identifier has the table's address and extension and its number is its data's value (all 1,280 rows agree); a Kaseikyo row's identifier has its group's table (`K4-1`) and its number is the value of its data bits (D340). A name that is a paragraph of a cell with a mark is read as D340 says.

### D340 — The rule, what the marks do, and what the documents get wrong

- **The rule is §81's, for remote ID 1.** Sharp: the address and the data first bit first, the function the data and the extension bits above it (`POWER` is device 2 function 0xC1, which the ledger's Denon remotes have), `Denon` at 38 kHz. Kaseikyo: **device `GENRE1`, subdevice `GENRE2` (the rows' identifiers' table, `K4-1` is 4 and 1), function the ten data bits, the ID bits 0** (the documents' table for IDs 2 to 4 gives the others and is not read, as §81's was not), `Denon-K` at 37 kHz; the parity byte is the ledger's. **A file for each product the title names and each format** (`AVR-X2700H`, `AVR-X2700H (Kaseikyo)`).
- **A mark that names models restricts a key to them, and a region stays in the label.** `※AVR only` (the X3700H's and X4700H's tuner: 18 Sharp and 50 or 57 Kaseikyo keys) is the AVR and not the AVC, which has no tuner; `※X2700H only` (`FRONT SPEAKER`, `SP-A`, `SP-B`) the AVR-X2700H and its DAB and not the S960H; `(AVR-X2700H DAB only)` the DAB; `PTY ※EU only` is `PTY (EU only)` and `※AVR(NA/CHI/JP) only` on `TUNER MEMORY` is `TUNER MEMORY (NA/CHI/JP only)`. **A cell of two paragraphs is one name or two**: a paragraph that is only a mark, or begins or follows `/`, goes on the one before (`DIRECT SEARCH/` and `RDS SEARCH (AVR-X2700H/S960H EU only)` are `DIRECT SEARCH/RDS SEARCH (EU only)`), and otherwise it is a second name for the models the first is not for (`(FM/AM)TUNING UP` for the AVR-X2700H and S960H, `(DAB) STATION UP` for the DAB: **the DAB's Kaseikyo file has six keys of its own** (`BAND DAB`, `BAND FM/DAB`, `MAIN ZONE BAND DAB`, `ZONE2 BAND DAB` and the two `(DAB) STATION` ones) **and lacks the three of the others'**). **The X6700H's document says `※NA only` where the others say `※AVR only`, which is a region and not a model**, so the AVC-X6700H's file has the tuner keys the AVR-X6700H's has, though a processor has no tuner: read as written (not proven below).
- **What the documents get wrong, and the reader does not hide.** (1) **Five Kaseikyo rows whose identifier and data bits are not one function** (in 1, 2, 5 and 5 of the four documents): `Graphic EQ ON` (`RCKSK0410791`, bits 799), `ZONE2 SLEEP` and `ZONE3 SLEEP` (788; bits 836) and `ZONE2 VR PRESET(MUTE)` and `ZONE3 VR MUTE` (370; bits 371). The bits are read and **the key is Untested (27 keys in all)**, and **the ledger's other sources show neither is always the right one**: they hold `Graphic EQ ON` as 799 and `Z2 Sleep` as 836 (the bits'), and the mute of zones 2 and 3 as 370 (the identifier's: the 371 is in none). (2) **The `ZONE3` group's header (the X4700H's and X6700H's) gives `GENRE2` 3, which is the `ZONE2`'s**: its 77 (the X4700H's) and 76 rows' identifiers say `K4-5` and so does the document's table of remote IDs (its first block lists the groups' tables in order, `K4-1`, `K4-3`, `K4-5`, `K4-1`, `K4-7`, which the reader checks in all four documents), and the ledger's other Denon remotes have `K4-5`'s codes: read as `K4-5`, and each of the 296 citations of the rows of the group says the header gives another.
- **Against the ledger's other sources: all 137 Sharp codes and 418 of the 454 Kaseikyo ones are in remotes of other sources**, under names that agree (`FR LEVEL DOWN`, `POWER`, `ALL POWER ON/OFF`, the zones' rows); the 36 new are 34 Plausible and the two of the mute rows. The files: Sharp 137, 137 (the DAB), 134 (S960H), 134, 116 (AVC-X3700H), 131, 113, 131 and 131 keys; Kaseikyo 340, 343, 337, 358, 308, 436, 379, 441 and 441.
- The corpus is **17,226 remote files and 722,953 keys** (18 and 4,547 more), 5,465 brands (Denon was one), 282,937 models (26 more) and 184,676 signals (36 more: the 36 Kaseikyo codes; every Sharp code was a signal the ledger had). Both bundle profiles are 61,636,608 bytes (151,552 more), dataVersion `624a4ce57597`, `--verify` passes. **437 of the 4,547 keys map to the vocabulary (9.6%)**: `POWER`, `VOLUME UP` and `DOWN`, `MUTE`, the cursor keys, `ENTER`, the inputs do, and the zones' rows, the levels of channels and the sound modes do not (D84's aliases, the owner's call); the `official` row goes from 18.3% to 17.8% and the corpus from 70.0% to 69.6%. **All 18 files have a key the app's test order can pick** (`POWER` in each Sharp file, `VOLUME_UP` in each Kaseikyo one). **The search evaluation: first answer 82.5% to 87.5%, first five 95.9% to 97.5%**, the 51st draw: **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the eight misses involves a Denon model. **No query of the classes that should answer first is a miss of the first five, and three answer second** (`RCA SYSTEM*LINK 6 A/V - DireTV receiver`, `CYBERHOME DVR-1600` and `LG dvd/vcr combo V9120W DVD functions LG`).
- **Not proven:** that a receiver accepts every code its list gives (documents of 2020, `Ver. 01`); which of its identifier and bits is right in the five rows that disagree (the ledger's other sources split); that the codes of remote IDs 2 to 4 follow the documents' table of IDs (they are not read); that the AVC-X6700H has the tuner keys of its document (`NA only`); that the DAB version and the AVR-S960H have the AVR-X2700H's codes beyond the marks (the title names three products and one set of tables); and that the S960H's `ZONE2` keys work when its `Assign Mode` is not `ZONE2` (the document's footnote).

## 91. SnapAV's projector sheets: JVC's LX-NZ3 and LX-UH1 and BenQ's LK952

SnapAV's protocols-and-drivers folder for projection screens (the same site as §89's and §90's folders) holds the IR code sheets of three laser projectors. **Four PDFs are read: JVC's `LX-NZ3` and `LX-UH1` (a table of 24 codes each, and a third PDF that gives the LX-NZ3's codes again as Pronto hex) and BenQ's `LK952` (the 130 codes of the family's remote and whether the model supports each): 3 files and 82 keys, all Plausible.** The codes are NEC frames: **JVC's custom code `00 6A`** (the second byte is not the first's complement: the subdevice) and **BenQ's `0x0030`**, which are the custom codes of other sources' projector remotes in the ledger: **8 of the LX-NZ3's 24 codes and 21 of the LK952's 34 were held** (under the same names), and **29 are new**: the LX's picture modes, `HIDE`, `TEST`, `BACK`, `BRIGHTNESS`, `CONTRAST`, the discrete power and input keys, and the LK952's `INFO`, `All`, `ID SET`, `PICTURE` and its digits. None of the three product names was in the ledger. The manufacturers' tables (§29 and the sections after it) are now 851 files and 73,615 keys.

### D341 — The documents, how they are pinned, the licence and what is not read

**JVC** (the sheets say whose they are no further than their file properties, a Japanese author's name for the two tables and `Robert Budde` for the long hex, so **the long hex may be a third party's conversion of the table**: it says the same codes): `RCU Code of LX-NZ3_20191114.pdf` (338,651 bytes, 2 pages, 15 November 2019; the table and, on the second page, an image of the code format), `LX-UH1 Remote Control Codes.pdf` (356,209; 2 pages; 31 July 2018) and `LX-NZ3 long hex commands.pdf` (66,206; 5 pages; 17 December 2019: `INPUT: NEC1 Device: 0.106 Function: 94 S=106` and the Pronto hex of each of 24 codes). **BenQ**: `BenQ_LK952_IR_Codes.pdf` (232,835; 6 pages; 28 September 2020: `Appendix 2 - Code List: IR / RS232 / DDC Data`). They are the Wayback Machine's copies of 22 January 2022, 13 April 2024, 27 January 2022 and 7 February 2023 (`id_`, the raw bytes), **and the live files are the same bytes: the SHA-256 of each was compared on 2026-10-18**. **Pinned** with `tools/fetch_official.py`: the JVC ones with `--only` (the manifest's date stays 2026-10-13 and each of the three has its own, 2026-10-18) and BenQ as a maker of its own (`benq`, 2026-10-18). **Licence:** none of the notice words (copyright, all rights, reserved, ©, (c), confidential, reproduc, trademark, proprietary: searched as D290 did) in the four: public under D110. **Not read:** the two RS-232 command lists (`LX-NZ3_External_Command_List`, `LX-UH1_External_Command_List`: serial commands, no IR), JVC's `Firmware 3.1 IR Control` and `LEO3_Remote_Control_Codes_Table_ver_1-0` (read in §92: the `(c)` the search found in both is `Anamorphic(C)`, a button) and `HT5550_IR_Codes.pdf` (an image with no text layer). The tables are read from the sheet tool's `tables` (a row: the page, the table's number and the cells), and the long hex's and the LX-NZ3's footnote from its `pages`.

### D342 — The rule, and what the documents say that differs

- **JVC.** A key is the ledger's `NEC1`: **device 0x00, subdevice 0x6A, function the data code** (the fourth byte, its complement, is checked: all 48 rows agree), a file for each product (`LX-NZ3`, `LX-UH1`), **at 38.4 kHz, which is the long hex's frequency word (`0x006C`)**; the UH1's sheet gives none and is read as the NZ3's. A row of dashes (`LIGHT`) is no key (reported). **The LX-NZ3's long hex is decoded and must say the table's code: the Pronto hex of all 24 entries is a NEC frame of the four bytes the table says**, and a key's citation names the entry and the Pronto hex's frequency word. **The asterisk of `POWER ON*`, `POWER OFF*`, `HDMI 1*`, `HDMI 2*` and `PC*` is the footnote `*Remote Code only`, and the UH1's names say `(for Remote only)` and `(Direct Input Sel.)`: codes the projector takes and its remote does not have a key for**, so they are Plausible and the citation says what the sheet says of them; a label drops the asterisk and the notes (`POWER ON`, `HDMI 1`). **The UH1's sheet is the NZ3's table**: the same 24 functions, with `RGB` where the NZ3's names the code 0x59 `PC` (the long hex names it `RGB` too, and says so in the NZ3's citation).
- **BenQ.** A key is `NEC1` with **device 0x00, subdevice 0x30 (the custom code `0x0030`) and the code as the function, at 38 kHz**, all of which the sheet's table of the IR format says (`Frequency 38 KHz`, `Protocol NEC Format`, `Custom Code 0x0030`: the reader checks that this is what the table says). **Only the 34 rows that say `Support: Yes` are keys**: the other 96 are codes of the family the LK952 does not take (reported, with the reason). The sheet writes two codes with a letter O (`Ox4F`, `Ox50`): read as hex, and the citation of `Power On` says so (`Ox50` is a `No`). `Smart Eco` and `Light Mode` are one row, which the table cell reads `Smart Eco Light Mode`.
- **Against the ledger's other sources:** other sources' remotes with the custom code `00 6A` (nine, Canon's projectors among them) have **8 of the LX-NZ3's codes**, named alike (`POWER`, `INPUT`, `UP`, `DOWN`, `LEFT`, `RIGHT`, `MENU`, `OK`), and other sources' remotes under `00 30` (112 distinct codes, BenQ's mostly) have **21 of the LK952's 34** (`Power Off` 0x4E and `Power On` 0x4F as `POWER OFF` and `POWER`, `Source`, `Freeze`, `Menu/Exit`, `Mute`, `3D`...). **All 58 distinct codes are new signals, the 29 the ledger held too**: it holds them as `NEC2` (which repeats the whole frame) or at `NEC1`'s 38.4 kHz, where the BenQ sheet says 38 kHz (the `carrier-off-nominal` warning, as the other manufacturers' NEC tables have).
- The corpus is **17,229 remote files and 723,035 keys** (3 and 82 more), 5,465 brands (JVC and BenQ were two), 282,943 models (6 more) and 184,734 signals (58 more: the 24 codes of the LX-NZ3's table, which the LX-UH1's repeats, and the LK952's 34). Both bundle profiles are 61,657,088 bytes (20,480 more), dataVersion `7ca62c64b98e`, `--verify` passes. **52 of the 82 keys map to the vocabulary (63.4%)**: the cursor keys, `POWER`, `POWER ON` and `OFF`, `HDMI 1` and `2`, `INPUT`, `MENU`, `OK`, `BACK`, `MUTE`, the digits do, and the picture modes and the settings do not; the `official` row stays 17.8% and the corpus 69.6%. **All 3 files have a key the app's test order can pick** (`POWER` in the LX-NZ3 and LX-UH1, `POWER_OFF` and `POWER_ON` in the LK952). **The search evaluation: first answer 87.5% to 90.3%, first five 97.5% to 98.1%**, the 52nd draw: **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the six misses involves a JVC or BenQ model. **No query of the classes that should answer first is a miss of the first five, and two answer second** (`FCIS 7000` and `LA 52 A 577 P 2 M/XZF - 2`).
- **Not proven:** that a projector accepts every code its sheet lists (the LK952's `Support` column is BenQ's word for its family's code list, and its first row says `Projector must support 0x02, 0X4E, 0X4F power on/off command` while the row `Power 0x02` says `No`: read as the table says); that the LX-UH1's carrier is the NZ3's (its sheet gives none); that the `Remote Code only` codes (`POWER ON`, `POWER OFF`, the direct inputs) are what the remote sends and not only what the projector takes; and which of the LX-NZ3's codes the Canon remotes' `00 6A` shares because of a common maker (the names agree for the eight).

## 92. JVC's 2018 and LEO3 remote control code tables

§63 reads JVC's 2012 guide to the codes of its D-ILA projectors (the ledger's `JVC` protocol, device 115). **Two later tables of the same codes are read here: `2018 ILA-FPJ Remote Control Codes Table (V1.20)` (the SnapAV copy, with a Pronto hex for each of its 75 codes) and `LEO3 Remote Control Codes Table (ver 1.0)` (92 codes): 7 files and 582 keys, all Plausible**, a file for each model column of a table: the 2018 table's `DLA-RS3000/NX9 etc.`, `DLA-RS2000/NX7 etc.` and `DLA-RS1000/NX5 etc.` and the LEO3 table's `P`, `ZL`, `ZM` and `ZH`. **93 distinct codes, of which the ledger held 74** (the guide's and the other sources' projector remotes'): what is new is **19 codes** (`Anamorphic(C)` and `(D)`, `USER6`, the solenoid or colour-filter positions, `3D Format (OFF: 2D)`, `HLG`, installation modes 4 to 10, `Frame Adapt HDR`, `HDR10+`, `PanaPQ`, `Name Edit`, `Low Latency`), the 2018 models' names (the RS1000, RS2000 and RS3000, with the NX5, NX7 and NX9 the table names beside them) and the platforms' own names for the later ones. The manufacturers' tables (§29 and the sections after it) are now 858 files and 74,197 keys.

### D343 — The documents, how they are pinned, the licence and the model names

**The documents** are on the same folder of SnapAV's site as §91's (`ProjectionScreens/ProtocolsAndDrivers/Firmware 3.1 IR Control.pdf`, 282,256 bytes, one page, the table dated 2 October 2019 and the PDF made on 8 October 2020 by the author `Robert Budde`, who made §91's long hex: **the Pronto hex of each code is his addition to JVC's table**, which the PDF says in its heading `Decimal Long HEX codes (in case of 73H *code A)`) and the `SupportDocuments` folder (`LEO3_Remote_Control_Codes_Table_ver_1-0.pdf`, 183,917 bytes, one page, made on 20 July 2021). They are the Wayback Machine's copies of 22 May 2022 and 18 January 2022 (`id_`, the raw bytes), **and the live files are the same bytes: the SHA-256 of each was compared on 2026-10-18, and the LEO3 table is also a file on `images.salsify.com` (an image host: the same bytes)**. Pinned with `--only` (the manifest's date stays 2026-10-13, each has its own). **Licence:** none of the notice words (copyright, all rights, reserved, ©, (c), confidential, reproduc, trademark, proprietary: searched as D290 did; the one `(C)` in the two is `Anamorphic(C)`, a button) in either: public under D110. §91's TODO had named both as `(c)` candidates for the private repository: that was the same `Anamorphic(C)`, and is corrected there. **The tables are read from the sheet tool's `tables` (a row: `Update, Code, Button`, the remote's column, `Repeat`, a tick or dash for each model, `CONTENTS`, and in the 2018 table `Type`) and its `lines` (the title, the Pronto hex with its decimal before it, and the footnotes).**

**The models.** A column of the 2018 table is a model and its other name (`DLA-RS3000/NX9 etc.`: the file is `DLA-RS3000`, its alias `DLA-NX9`, its controls both: the table writes the pair as one, and `etc.` for the family, which is not named); **a column of the LEO3 table is a letter (`P`, `ZL`, `ZM`, `ZH`) and the table names no product, so the files are `LEO3 P`, `LEO3 ZL`, `LEO3 ZM` and `LEO3 ZH`**, the platform's name and the column's: that these are the DLA-NZ7, NZ8 and NZ9 and a fourth model (SnapAV's folder has the manual of the NZ9, NZ8 and NZ7 beside them) is a guess the data does not make.

### D344 — The rule, and what it does to the rest

- **A key is the ledger's `JVC` with device 115 (the maker code `73H`, Code A) and the operation code as the function** (`01H` is 1: `UP`; `05H` `ON`; `06H` `STAND BY`; `08H` `INPUT`), the labels as the tables write them (an arrow is its word: `UP`, `DOWN`, `RIGHT`, `LEFT`; a footnote mark and a trailing slash are dropped: `Anamorphic(D)*2`, `LENS.AP /`). **A tick in a model's column is the model's key and a dash a code it does not take** (reported: the 2018 table's RS1000 has 71 of the 75 codes, the RS2000 74 and the RS3000 all 75; the LEO3's P and ZL 89 of 92). The remote's column (`MH-27G`, `RM-MH27`) and `Repeat` are in the citation: a code with a dash in the remote's column is one the remote has no key for. **The second maker code, `63H` (Code B, device 99), is not read.**
- **The 2018 table's Pronto hex is decoded and must say the code (§63's check): all 75 do** (a lead-in sent once, sixteen bits, device 0x73 and the code), so every key of the 2018 files is checked twice; the LEO3 table has no hex and its keys rest on the table alone. A note of a name (`*2`, `This command is not effective for v2.xx`) is in the citation.
- **Against the ledger's other sources: 74 of the 93 distinct codes were in the ledger's JVC remotes** (the guide's 209 functions and the IR Blaster and JP1 projector remotes: 218 functions of device 0x73 in all), under names that agree for the cursor keys, the power keys, the inputs and the picture modes. The corpus is **17,236 remote files and 723,617 keys** (7 and 582 more), 5,465 brands (JVC was one), 282,963 models (20 more: the seven, their aliases and the controls' names) and 184,753 signals (19 more: the 19 new codes). Both bundle profiles are 61,677,568 bytes (20,480 more), dataVersion `45037fc9bf9d`, `--verify` passes. **98 of the 582 keys map to the vocabulary (16.8%): 14 in each file** (`ON`, `STAND BY`, the cursor keys, `OK`, `BACK`, `Menu`, `INPUT`, `HDMI 1` and `2`, `INFO`, `Picture Mode`); the `official` row stays 17.8% and the corpus goes from 69.6% to 69.5%. **All 7 files have a key the app's test order can pick** (`POWER`, `POWER_ON`). **The search evaluation: first answer 90.3% to 89.1%, first five 98.1% to 97.2%**, the 53rd draw: **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the nine misses involves a JVC model. **No query of the classes that should answer first is a miss of the first five, and one answers second** (`ORION TV 82100`).
- **Not proven:** that a projector accepts every code its column ticks (the tables are JVC's, and the 2018 table's models are `etc.`: the family is not listed); that the LEO3 columns are the NZ7, NZ8, NZ9 and a fourth model, and which; that the Pronto hex of the 2018 table is JVC's own and not SnapAV's author's work (it decodes to the table's codes, so it is the table's codes as hex); that device 99 (Code B) answers the same operation codes (the tables say it does).

## 93. Peerless-AV's Neptune outdoor TV IR code sheet

Peerless-AV's `IR REMOTE CONTROL CODES` for its **Neptune Partial Sun and Full Sun outdoor smart TVs** (2025) is one page of one ruled table, a row for each of **52 keys** (`Power`, the digits, the cursor keys, `HDMI 1` to `3`, `Discrete On` and `Discrete Off`, the colour keys, `Netflix`, `Prime Video`...) with the encoding, the 32-bit frame, the custom code and the function. **2 files (`Neptune Partial Sun` and `Neptune Full Sun`, the same keys) and 104 keys, 92 Plausible and 12 Untested.** **Every one of the 52 codes was in the ledger**: the custom code `04 FB` is the one of the Vizio, LG, HiSense and Haier remotes (251 codes of it in other sources, from remotes such as Vizio's XRV1 and D24f-F1 and LG's master file), so what is new is the line's name and the maker's own names for 52 codes. The manufacturers' tables (§29 and the sections after it) are now 860 files and 74,301 keys.

### D345 — The document, how it is pinned, the licence and who it is Peerless-AV's

The PDF (`Remote_Control_Codes_2025_Final.pdf`, 121,501 bytes, one page, made on 2 June 2023 and changed on 6 February 2025 by `Todd Mares`) is a **live file on a Shopify CDN** (`cdn.shopify.com/s/files/1/0508/3732/3934/files/`), pinned as it is (the manifest has no Wayback snapshot: the file is the origin's) with `tools/fetch_official.py peerless` on 2026-10-18. **That it is Peerless-AV's rests on three things, none of them the file's host:** its title names the Neptune line (`Netptune`, as the title misspells it), Neptune is Peerless-AV's line of outdoor TVs (its own site and the resellers' pages say so), and **a web search that found the file said that Peerless-AV's `neptune-full-sun-series` page links to an `IR Remote Control Codes` resource** (the page is built in the browser and its source has no link: that statement is the search's). **Licence:** none of the notice words (copyright, all rights, reserved, ©, (c), confidential, reproduc, trademark, proprietary: searched as D290 did; the title's `™` is a mark, not a notice) in the page: public under D110. The table is read from the sheet tool's `tables`.

### D346 — The rule and what it does to the rest

- **A key is the ledger's `NEC1`**: `GROUP` (`0x04FB`) is the two first bytes sent (the device 4 and the subdevice 0xFB, its complement) and `CODE` (`0x08` for `Power`) the function, **at 38.1 kHz, which the sheet's one `Frequency` cell says**. `DATA` is the frame as a 32-bit number whose bytes, least significant first, are the four sent (`0xF708FB04` is `04 FB 08 F7`), and **the reader checks that it says what `GROUP` and `CODE` say: all 52 do**. **Six rows name an encoding that is not `NEC T/3T-32`** (`NEC T/3T-33`, `-34` and `-35`: `AV Input`, `Discrete Off`, `Discrete On`, `HDMI 1`, `HDMI 2` and `HDMI 3`): the sheet does not say how they differ, so the frame their `DATA` gives is read as NEC1's and **they are Untested** (12 keys in the two files). The title's misspelling is reported and the files are named for Neptune.
- **Against the ledger's other sources: 52 of 52 codes** are in remotes of other sources under the custom code `04 FB`, under names that agree (`Power`, the digits, `Channel Up` and `Down`, `Volume Up` and `Down`, `Mute`, the colour keys): **the sheet is a Vizio-family code set under a maker's own names**. The corpus is **17,238 remote files and 723,721 keys** (2 and 104 more), 5,465 brands (Peerless-AV was one), 282,967 models (4 more) and 184,781 signals (28 more: the other 24 codes are signals the ledger had, which these files send at 38.1 kHz where the ledger's remotes send them at 38.4: the verify's `184,805 signals decoded` counts each signal once for each carrier it is sent at). Both bundle profiles are 61,681,664 bytes (4,096 more), dataVersion `209c0e33afdf`, `--verify` passes. **84 of the 104 keys map to the vocabulary (80.8%)**; the `official` row goes from 17.8% to 17.9% and the corpus stays 69.5%. **Both files have a key the app's test order can pick** (`POWER`). **The search evaluation: first answer 89.1% to 89.4%, first five 97.2% to 96.9%**, the 54th draw: **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and none of the ten misses involves a Peerless-AV model. **No query of the classes that should answer first is a miss of the first five, and two answer second** (`ORION TV 82100` and `LA 52 A 558 P 3 F/XXC - 2`).
- **Not proven:** that the six keys of other encodings send the NEC1 frame their `DATA` gives (the sheet's `T/3T-33` to `-35` are not explained); that the Neptune line is Peerless-AV's own and the sheet is its own (above: three indications and no statement of the file's); and that the `…` key (the sheet's first row, function 0x96) is the key a Neptune remote has under that name.

## 94. Denon's per-model remote code lists of 2003 to 2012

§81 reads Denon's `AVR-4311CI/AVR-4311 Remote Code List` of 2010 and §90 its X-series lists of 2020. **The lists between them are on Denon's own asset host, `assets.denon.com/documentmaster/`, one PDF for each receiver: 24 of them are read here, 72 files and 12,626 keys for 37 product names, all Plausible but 590.** A list is the tables of the codes of the receiver's remote in the two formats it sends, *Sharp* (15 bits: the ledger's `Denon`) and *Kaseikyo* (48 bits: `Denon-K`): the AVR-2106, AVR-2805, AVR-2807, AVR-987 and AVR-3803 (2003 to 2006: Sharp tables and, for the 2106, 2807 and 987, one page of Kaseikyo ones), the AVR-4806, AVR-5805 (and its CI and upgrade), AVR-3808CI, AVR-4308CI and AVR-2808CI/2808/988 (AVC-2808) of 2005 to 2007, the AVR-2309 and AVR-2809 of 2008, the AVR-3310CI/990 (AVC-3310), AVR-4310CI (AVC-4310), AVR-3311CI/991/3311, AVR-1911/791 and AVR-2311CI/891/2311 of 2009 to 2011, and **the identifier lists** of the AVR-2312 and AVR-3312 (2011) and of the AVR-1913/2113, 2313, 3313 and 4520 (2012). **The ledger held 181 of the 196 distinct Sharp codes of the lists and 715 of their 791 Kaseikyo ones** (the codes of the receivers before them, from the IR Blaster database, the JP1 upgrades and §84's master spreadsheet): what is new is the 37 names with a table of the maker's own for each (**four were in the ledger, as JP1 remotes: `AVR-2106`, `AVR-2807` and `AVR-988` are kept as `(Denon table)` files and `AVR-3808` is a control of the `AVR-3808CI`'s and no alias**), **15 Sharp codes** (`THX ULTRA2 CINEMA`, `THX MUSIC MODE`, `THX SURROUND EX`, `DTS ES MATRIX`, `DOLBY DIGITAL EX`, `INPUT MODE PCM`, `DTS`, `RF` and `EXT.IN-2`, `ZONE3 VCR-3` and `ZONE2 VCR-3`, `MULTI CH PLAY (INPUT CH)`, `VIDEO OFF`, `PRODUCT ON` and `VERSION ON`) and **76 Kaseikyo codes** (the additional sources `AUX3` to `AUX7` of the main zone and of zones 2 to 4, `VCR-1/2` and `VCR-3/4`, `CDR/TAPE`, `SHIFT A` to `G`, `WIDE SCREEN`, `SUPER STADIUM`, `Bass Sync Up` and `Down`, `ZONE2 L LEVEL UP` to `R LEVEL DOWN`, `STATUS REQUEST(for 2way RC)` and the keys of zone 4). The manufacturers' tables (§29 and the sections after it) are now 932 files and 86,927 keys.

### D347 — The documents, how they are pinned, the licence and the sheet tool

**The documents** are 24 PDFs of 33,457,456 bytes on `assets.denon.com/documentmaster/` (the `us/` folder 10, `fr/` 8 and `de/` 6: Denon's regional sites share the host), **live files: the server's `Last-Modified` is 21 November 2023 for 14 and 5 December 2023 for 10, and the PDFs' own dates run from 10 April 2003 (`avr-3803-ircodes.pdf`) to 25 October 2012 (`avr4520_ir_code_v02.pdf`)**; 5 to 47 pages each (the lists of 2003 to 2007 are 5 to 8 pages, the later ones 18 to 47), made by Acrobat Distiller and, the four `v02` lists of September and October 2012, by PrimoPDF. They are named by the Wayback Machine's index of that host (§84's lead in TODO: about 75 per-model lists) and **fetched from the live host on 2026-10-18** (`tools/fetch_official.py denon --only ...`; **the Wayback Machine's captures of five of those tried stop at exactly 1,048,576 bytes, a truncated copy, so none is pinned from there**). Each is pinned by SHA-256 with its URL, size, retrieval date and `Last-Modified`; the manifest's date stays 2026-10-16 for the earlier documents. **Licence:** none of the notice words (copyright, all rights, reserved, ©, (c), confidential, reproduc, trademark, proprietary: searched as D290 did, in the text of every page) in the 24: public under D110. **Not read here:** the nine lists of 2008 whose pages are stamped `CONFIDENTIAL` (the AVR-588, 688, 788, 888, 988, 1508, 1708, 1908 and 2308CI: the private repository's, by D110, TODO), the one-page zone sheets and the other X-series Word documents of the same host. **A citation** names the host (`assets.denon.com IR code documents`), the file, its hash and the retrieval date, then the row: a list with identifiers cites `row 'RCKSK0410002' (...)` where the row's bits are not all in the text, and otherwise (the lists of bits, which have none, and the 2011 lists' tables of ID 1) `page 10 table row 'K4-1 #1' ('ALL POWER ON', K4-1 : MAIN ZONE, bits 29 to 48 01000000000010000110): ...` (the page, the table's code and the row's place in it, or its identifier, and the bits).

**The sheet tool's mode `words` reads a page turned 90 degrees as it is shown.** §81's mode gives every word of every page by its place and the rows of its ruled tables (`R`, which say which lines are a row's); the lists of 2011 and 2012 are pages whose `rotation` is 90, so their words' places are turned with `page.rotation_matrix` and every list reads left to right. The 24 CSVs are 4.2 MB; a test reads two of the PDFs again (an unrotated and a rotated one) and compares them with the pinned CSV.

### D348 — The readers

§81 reads its list by fixed places (`denon.parse_sharp`, `denon.parse_kaseikyo`); **the lists differ in layout, so these readers (`official/denon_pdf.py`) find the places on each page**, and §81's list is a test of them (D350). **A list is one of two styles**: **18 lists of rows of bits with no identifier** (the Sharp table's number and data and the Kaseikyo table's 20 or 24 bit columns) **and 6 with the identifiers of §90** (`RCSHP0230016`, `RCKSK0410001`: the AVR-2312 and 3312 of 2011 and the AVR-1913/2113, 2313, 3313 and 4520 of 2012).

- **Sharp.** A table is a line `SYSTEM ADDRESS(C1~C5):01000,EXTENSION BIT(C12,C13):11` (in fullwidth digits in the lists of 2003 to 2007; the same line is written twice, over each half of a page) and rows of two halves, **a number (0 to 63), six data bits first bit first and a name** (empty for a code the list leaves free: **4,126 of the 7,680 rows read have none**, counted for each document in the report), the address the device (`01000` is 2) and the function the data with the extension bits above it (D315). **The 7,680 rows are 320 for each list but the AVR-2106's (288: its table `01000`/`10` gives rows 32 to 63 only) and the AVR-1911's (352: its table `00110`/`10` has 64 rows, not 32), and each row's data must be its number's bits: all agree.** A name is found **by the ruled rows**: the line of a name that has two is above the row's bits or below them, and the lines in the same ruled row are the cell's; **a footer (`DENON AVR-4806 RC CODE`), a footnote (`※1:Except AVR990`), the date at a page's head and the next group's heading are no names** (a page with no ruled row goes by the distance to the line above); **a table goes on over a page's end** (the AVR-4311's rows 47 and 63 are alone at the top of the next page).
- **Kaseikyo, bits.** A group has a header of 24 bits (the vendor 0x54 0x32, a 0 parity nibble, `GENRE1`: checked), a title above its `DENON CODE` line, and **either a `K4-1 : MAIN ZONE` line (the table's code, which gives `GENRE2`) or the four `GENRE2` bits at the start of its first row**; a row is 20 bits (bits 29 to 48: the 12 of the function, ten data bits and two ID bits, and the parity byte) and a name. **The place of the columns is read from the line of their numbers, `25 26 ... 48` or `29 30 ... 48`** (printed as one word where the PDF joins them: `2930`, `3536373839`) **and kept over the pages that have none**; a list with a `No.` column (the AVR-2807's and 987's) has the row's number there, which must be the data bits' value (all 84 agree). **Only the groups for the default remote ID are read**: `REMOTE ID SET : 1`, `ID1` (the AVR-1911's, whose iPod, USB and HD Radio tables are read, which §81's are not) or no such line; **the 68 groups for IDs 2 to 4 and 1 to 4 write the ID and parity bits as `*`** and are listed in the report as §81's were (a row of stars with a name; the page's legend of stars with none is not). **The parity byte is checked**, the exclusive or of the 3rd, 4th and 5th bytes: 1,755 of the 2,131 rows agree, and D349 says what the other 376 are.
- **Kaseikyo, identifiers.** A row is its identifier, **ten data bits (and four before them in a group's first row, its `GENRE2`, which must be its identifier's) and then either ten `*` (the ID bits and the parity: the 2012 lists' tables and the 2011 lists' tables for other IDs) or the two ID bits (`00`, checked) and the eight parity bits (the 2011 lists' tables for ID 1: 598 rows, whose parity is checked as the lists of bits' are)**, and a name that starts at its first word that is no bit (`FAVORITE STATION 1` ends in one). A group has a title (the table's own name, `K2-3 : iPod`, where the page gives it under `REMOTE ID SET`, else the line above) and a header of 24 bits (2011) or 28 (2012: with `GENRE2`), and **the identifiers' table (`K4-1`) is the rows' own: where a 2012 header's `GENRE2` is not the rows' (the AVR-3313's and 4520's `ZONE3` groups: 194 rows, which copy the `ZONE2`'s as §90's do) the citation says so**. **`REMOTE ID SET : 1` and `1-4` groups are read with the ID bits 0, and a `2-4` line that is followed by the table's own (`K4-2 : MAIN ZONE`) starts a group that is not read** (7 in the AVR-2312's and 3312's lists, listed: their tables are the other IDs', with another `GENRE2`, and a frame of `K4-2` with ID bits 0 is no remote's); **a `2-4` line that is a column's heading inside a group (the 2012 lists') is not a group**. **A cell too narrow for its identifier wraps it** (`RCKSK045000` and the digit under it, the bits in two or three pieces between: the AVR-3312's zone 3 table, 84 rows): everything between one identifier's line and the next is the row's. **One row has its data bits missing from the text layer** (the AVR-4520's zone 3 `Spotify` row, `RCKSK0450764`): its function is its identifier's alone, the citation says so, and the same code is in the other two zones' rows.
- **A name says which models it is for**, as §81's and §90's do and in one reader: **a parenthesis that says `(except AVR3311CI)` or `(AVR3311 only)` restricts the key to the models it names, a `※1` is the page's own footnote (`Except AVR990`, `EU only`: read as the parenthesis it stands for), a region (`NA only`, `EU only`, `※EU only` where the 2011 lists write the words at the mark) stays in the label, and a name of two (`TUNER(except AVR3311CI)/HD RADIO(AVR3311 only)`) is each side of the slash after a mark**; a name goes on in the next line when that begins with `(`, when it has a parenthesis not closed, or with `/`. **A version of a model is the model**: `HD Radio(AVR1911 E3 Only)` is the AVR-1911's and not the AVR-791's, and `(Except AVR1911 E3)` leaves the key to both (the model's other versions have it). **A group's title with a mark restricts its rows** (`ZONE3 (Except AVR991)`: the AVR-3311's and not the 991's) and a name that two groups of a file have is the second's with the group (`Main Zone Source CALL (ZONE3)`). **A file for each product name the title gives and each format** (the model the file's name gives, its title's other name an alias: the AVR-3808CI is `AVR-3808` in its title and `3808ci` in its file's name, and the AVR-4308CI likewise), **the model's other names are the Sharp file's aliases and every file's controls** (a name is one file's, R15), and the AVC-2808 (a processor) has its own files.

### D349 — What the documents get wrong, and what the reader does about it

- **The zones' tables are copies, and the parity byte of a copy is the original's.** 90 rows of bits have a parity byte that is not their table's but another zone's `GENRE2` (1, 3, 5 or 7: `ZONE2 TUNER BAND/Rewind` with the main zone's byte, `ZONE3 MEMORY` with zone 2's): **the document copied the row and kept the byte, and the data bits are the original's**. **Those keys are Plausible, and the citation says whose byte it is** (182 keys; **39 of their 42 distinct codes are held by other sources' remotes**). **This is the rule for §81's list too, which read the same rows as Untested: ten of its keys (five in each Kaseikyo file: `ZONE2 TUNER BAND/Rewind`, `ZONE3 MEMORY`, `ZONE3 SIRIUS(NA only)` twice and `ZONE3 TUNER BAND/Rewind`) are Plausible now, and all five codes are held by 6 to 36 other remotes** (§81 had 90 Untested keys and has 80).
- **The XM channel keys of the AVR-2807 and 987 sit in a `GENRE1 4` table with a `GENRE1 7` parity byte.** The ten `1` to `0 (XM Channel Input)` rows (one page, in both lists) have the byte that device 7 (the satellite tuners', `K7-5`) and the same `GENRE2` give, and the AVR-2106's list has the same ten codes under a table of `GENRE1` 7: **they are read as `K7-5`** (device 7, subdevice 5), cited, and Plausible (20 keys, 10 codes, all held by other sources). **The rule needs five such rows in a list**: one row in 256 that agrees with nothing agrees with device 7 by chance, and the AVR-5805CI's `ZONE2 AUDIO DELAY DOWN` did.
- **A parity byte that is nobody's: 338 rows, 532 keys, Untested** (270 rows of the lists of bits and 68 of the 2011 lists' tables of ID 1). The 2005 and 2006 lists' parity columns are not computed (the same byte on rows of different data: the AVR-5805's `ZONE2 CURSOR UP` to `ENTER` all have `0x01`, its `QUICK SELECT 1` to `3` all `0x12`): **87 of the AVR-5805's 92 Kaseikyo keys and 25 of the AVR-4806's 40 are Untested**, and the 2008 to 2011 lists have 4 to 14% of their rows so. **This is conservative: 130 of the 140 distinct codes of those rows are held by other sources' remotes**, so the data bits are mostly right and the byte is the document's slip; the tier follows D315's rule (a document that does not agree with itself is Untested) and not the likelihood.
- **Identifier and bits that are not one function: 27 rows of the 2012 lists and the AVR-3312's, 30 keys Untested.** §90's rows are here again: `ZONE2 VR MUTE` (370 and 371), `ZONE2 SLEEP` (788 and 836), and `FRONT SPEAKER` (518 and 522), `ZONE2 FAVORITE STATION 4` (245 and 244) and the AVR-3312's HD radio `CURSOR UP` and `DOWN` (69 and 71, 70 and 72): the bits are read and the number is not, and **the ledger's other sources hold the zone 2 mute as 370 (the identifier's) and the sleep as 836 (the bits')**, so neither is always the right one. **Two names for one code: 38 keys Untested** (the AVR-2311's `TUNER` and `HD RADIO`, which its list prints for both, the AVR-3311's zones' tuner and HD radio, the 2012 lists' `RDS(EU only)` and `SEARCH` and the zones' `FAVORITE STATION 3` and `4` (the same bits twice), and the HD radio's cursor keys), **and 2 keys for a header the page misprints** (below), by §81's and §90's rule that a code is one function.
- **A document's slips that the reader corrects, each cited: (1) the AVR-1911's page 7 prints the header of the second half of its table `00100`/`01` as `01000`/`01`** (over the rows 32 to 63, whose `INPUT MODE:AUTO` is the AVR-2311's `00100`/`01` row 51): read as the table's, and the one named cell is Untested; **(2) the AVR-2311's list is a copy of the AVR-3311's and prints `AVR3311` and `AVR3311CI` in its marks** (`HD RADIO(AVR3311 only)`): read as the AVR-2311's and the AVR-2311CI's, and each citation says `'AVR3311' is read as 'AVR2311'`.

### D350 — The rule checked, and what it does to the rest

- **§81's list is read again with the new readers and gives the same rows**: **all 377 Kaseikyo rows of the AVR-4311 (the same bits, the same codes, the same labels, and the same tiers but the ten above), all 320 Sharp rows, the same eight tables not read.** The Sharp files differ in one thing: **§81's reader takes a `※2` (`EU only`) for the AVR-4311 and drops the mark, and these keep the region in the label for both models** (`PTY (EU only)`, `RT (EU only)` and `DIRECT SEARCH (RDS SEARCH (EU only))` in both files, where §81's are `PTY`, `RT` and `DIRECT SEARCH (RDS SEARCH)` in the AVR-4311's alone: the document does not say the CI is the American model; not changed). The test that compares them is the readers' guard. **The first reading of the 2011 lists was wrong in two ways this found**: it took a first row's four `GENRE2` bits for data (`ALL POWER ON` was function 33 and not 2) and the tables for IDs 2 to 4 for ID 1's (672 keys under `K4-2` and `K4-4`, which are no remote's frames), and it lost the AVR-3312's zone 3 table; the files say what they say now (a test pins the groups of the 2011 lists: no key with `GENRE2` 2, 4, 6 or 8 under `GENRE1` 4).
- **Against the ledger's other sources: 181 of the 196 Sharp codes and 715 of the 791 Kaseikyo ones are held** (the new: 15 and 76), **173 and 572 of them under a label that agrees** (after case and punctuation: `POWER`, `CURSOR UP`, `ZONE2 POWER ON`, the sound modes; the others are the same key under another name, `BD` for `HDP`, `TUNER` for `HD RADIO`). The files: Sharp 5,376 keys in 37 files, Kaseikyo 7,250 in 35 (the AVR-2805 and AVR-3803 have no Kaseikyo table).
- The corpus is **17,310 remote files and 736,347 keys** (72 and 12,626 more), 5,465 brands, 283,077 models (110 more: the names and the controls) and 184,872 signals (91 more: the 15 and 76 codes). Both bundle profiles are 62,124,032 bytes (442,368 more), dataVersion `faa775ea1644`, `--verify` passes. **1,213 of the 12,626 keys map to the vocabulary (9.6%)** (`POWER`, the cursor keys, the inputs, the volume and mute keys do; the zones' rows, the sound modes and the network keys do not); the `official` row goes from 17.9% to 16.7% and the corpus from 69.5% to 68.5%. **50 of the 72 files have a key the app's test order can pick** (`POWER` in each of the 37 Sharp files, `VOLUME_UP` in 13 of the 35 Kaseikyo ones); **the other 22, Kaseikyo files of the lists of 2005 to 2011 (their tables are the zones' small ones, with no `POWER` or `VOLUME` under a name the vocabulary maps: `ALL POWER ON`, `MAIN ZONE ON`), have none**; all their keys are in the bundle (1,383 carrier remotes have no test key now, 22 more than before). **The Denon brand's name in the catalog is `Denon` now and not `DENON`**: the catalog shows a brand as its most used spelling (§23): the IR Blaster's models are `DENON` and the official and some other remotes' `Denon`, and the brand's 776 names are now 617 `Denon` against 561 `DENON` (and 118 `denon`), which the 110 new names tip; no remote changes (two of the 320 queries answer with `Denon` where they answered `DENON`, the same models).
- **The search evaluation: first answer 89.4% to 90.6%, first five 96.9% to 97.5%**, the 55th draw: **none of the 320 queries answers differently from the catalog before this section by model** (0 of 320, the first five each; the two above differ in the brand's case only), and **no answer of any of the 320 holds one of the 72 new files**. **None of the eight misses of the first five involves a Denon model** (one typo query and seven brand-partial ones of PANASONIC, CPC, UNITED, KIAMO, SHARP, SONY, CLASSIC and THOMSON). **No query of the classes that should answer first is a miss of the first five, and three answer after the first** (`SHARP AQUOS LC - 37 GA 6` second, `LG OLED 55 B 6` and `ZAPP 1545` third).
- **Not proven:** that a receiver accepts every code its list gives (lists of 2003 to 2012, `Ver. 01` or `02` where they say); that the 4 to 14% of the later lists' rows whose parity byte is nobody's have right data bits (the other sources hold 93% of their codes); that the zones' tables are copies of the main zone's data in all 90 rows (39 of the 42 codes are held); that the XM rows are `K7-5` in the AVR-2807 and 987 (the byte and the AVR-2106's list say so; no 2807 remote was tried); that the AVR-2311's marks are the AVR-3311's typed in by mistake and mean the AVR-2311's; that `EU only` is both models' (the first point above); that the tables for IDs 2 to 4 are what their `GENRE2` and `*` say (they are not read); and the AVR-2106's `01000`/`10` table, which has rows 32 to 63 and no more.

## 95. Denon's X1000/E300 and X2500H/S940H remote code lists

§90 reads four X-series Word lists of 2020 and §94 the per-model PDFs of 2003 to 2012. **Two Word lists of the same layout from 2013 and 2018 are on Denon's own host and are read here: the `AVR-X1000/E300 Remote Code List` (`Ver. 01`, 11 March 2013) and the `AVR-X2500H/S940H Remote Code List` (`Ver. 01`, 14 February 2018): 8 files and 1,867 keys, all Plausible but 8.** Four products, a Sharp and a Kaseikyo file each. **Every one of the 138 distinct Sharp codes and the 412 Kaseikyo ones was in the ledger** (all 138 and 300 of the 412 in remotes outside Denon's own tables, the rest in §84's, §90's and §94's), **137 and 408 of them under a label that agrees**: what is new is the four products' names with a table of the maker's own for each, and the reading of a layout that §90's reader refused. None of the four names was in the ledger. The manufacturers' tables (§29 and the sections after it) are now 940 files and 88,794 keys.

### D351 — The documents, how they are pinned, the licence and what the reader learned

**The documents** are Word 97 to 2003 files on Denon's asset host: `uk/avrx1000_e300_ir_code_v01.doc` (1,351,680 bytes; the file's `Last-Modified` 21 November 2023, its own dates 17 January to 15 March 2013) and `us/avr-x2500h_s940h_ir_code_v01-02072018.doc` (1,251,328 bytes; `Last-Modified` 5 December 2023, made 29 June 2018, the list dated 14 February 2018). **Live files, fetched on 2026-10-18 and pinned by SHA-256** (`tools/fetch_official.py denon --only ...`); the second's address is the lower-case path, the mixed-case one in the Wayback Machine's index (`DocumentMaster/us/AVR-X2500H_S940H_IR_CODE_V01-02072018.doc`) answering a 301 to it. **Licence:** none of the notice words (searched as D290 did, in the text of both): public under D110. Their text is converted by §90's `paragraphs` mode (`olefile`), and a citation names the host as §94's do (`assets.denon.com IR code documents/avrx1000_e300_ir_code_v01.doc@fe6f09ce (retrieved 2026-10-18) row 'RCKSK0410001' (...)`, no snapshot).

**What §90's reader did not read, and does now.** (1) **Each Kaseikyo row has four cells for its `GENRE2` before its ten data bits: the bits of the group's table in the group's first row, and empty cells after it** (the 2020 lists have none): the four cells are skipped, and the bits of a first row must be its identifier's `GENRE2` (the reader stops if not). (2) **A paragraph that is wholly one parenthesis goes on the name before** (`DIRECT SEARCH` and `(RDS SEARCH ※EU only )`, the 2013 and 2018 lists' two paragraphs for what the 2020 lists write as one with a slash, are `DIRECT SEARCH (RDS SEARCH (EU only))`); `(FM/AM)TUNING UP` and `(DAB) STATION UP (AVR-X2700H DAB only)`, which have text after their first parenthesis, are not, and §90's four lists give the same files as before.

### D352 — The rule, and what it does to the rest

- **The rule is §90's** (D340): Sharp device the address (`01000` is 2) and function the data with the extension bits above it, Kaseikyo device `GENRE1`, subdevice `GENRE2` (the rows' identifiers' table), function the ten data bits, the ID bits 0 (the document's tables for IDs 2 to 4 are not read), a mark that names models restricts a key and a region stays in the label. **The files**: `AVR-X1000` 138 Sharp keys and 376 Kaseikyo, `AVR-E300` 119 and 278, `AVR-X2500H` 137 and 344, `AVR-S940H` 134 and 341. **The AVR-E300 has no zone 2** (its list's group is `ZONE2 (X1000 NA only)`), so the X1000's file has 93 keys the E300's has not (`ZONE2 ...`, `All Zone Stereo ON/OFF (NA only)`, `PURE DIRECT (NA only)`, `Main Zone Source CALL`) and the E300's has none the X1000's lacks; the AVR-X2500H and AVR-S940H each have the 76 zone 2 names and the `HEOS MUSIC` group.
- **Untested are 8 keys: §90's slips again** (identifier and bits that are not one function): `Graphic EQ ON` (791 and 799) in the X2500H's and the S940H's files, and in the X1000's `ZONE2 VR MUTE` (370 and 371) and `ZONE2 SLEEP` (788 and 836), and `M.VR PRESET1(0dB)` and `M.VR PRESET3(-40dB)` (the code 372 the list gives to both, the second's identifier saying 374) in the X1000's and the E300's: the bits are read and the number is not.
- The corpus is **17,318 remote files and 738,214 keys** (8 and 1,867 more), 5,465 brands (Denon was one), 283,086 models (9 more) and **184,872 signals (none new: every code was a signal the ledger had)**. Both bundle profiles are 62,181,376 bytes (57,344 more), dataVersion `3ff7153379e0`, `--verify` passes. **244 of the 1,867 keys map to the vocabulary (13.1%)**; the `official` row goes from 16.7% to 16.6% and the corpus from 68.5% to 68.4%. **All 8 files have a key the app's test order can pick** (`POWER` in the Sharp files, `VOLUME_UP` in the Kaseikyo ones: the 2013 and 2018 lists have the volume keys in their tables).
- **The search evaluation: first answer 90.6% to 88.4%, first five 97.5% to 97.5%**, the 56th draw: **none of the 320 queries answers differently from the catalog before this section** (0 of 320, by brand and model, the first five each), and **no answer of any of the 320 holds one of the 8 new files**. **None of the eight misses of the first five involves a Denon model** (two typo queries, `SHADOW RC 570(DVB)` and `PANASONIC TH-50XP80U`, and six brand-partial ones of CPC, PHILIPS, UNITED, SONY, CLASSIC and JVC). **No query of the classes that should answer first is a miss of the first five, and five answer after the first** (`InFocus IN-3104`, `SONY KV-X252` and `YAMAHA yamaha DSP AX640` second, `LG TV LG OLED65E6` third and `PANASONIC TX28XD1` fifth).
- **Not proven:** that a receiver accepts every code its list gives (lists of 2013 and 2018, `Ver. 01`); which of its identifier and bits is right in the four rows that disagree (the other sources split, §90); that the AVR-E300 has the X1000's keys beyond the list's marks (one list names both products); that the codes of remote IDs 2 to 4 follow the lists' tables of IDs (not read).

## 96. Completing a thin remote from the remotes that share its address

A device is linked to the remotes that name it (D89), so a device whose only remote is a stub is offered the stub and nothing more, whatever else the ledger holds for the address the stub speaks. The case that showed it: Samsung's UN50NU6900F has one remote, `samsung/BN59-01199F`, written on 2026-09-19 (D18) to prove the NECx2 protocol, with 8 keys (Power, Source, Volume, Mute, Menu, Exit, OK) of the 38 that IRDB's table of the shared Samsung TV address, device 7 subdevice 7, holds. A service that offered it gave a person a remote of 8 keys for a TV whose own remote has 47, while the ledger held the same address with the digits, the arrows, the colours, the playback keys and the rest in 47 other remotes, none of which names that model. This section changes the exporter so that such a remote is **completed** in the bundle. It changes no remote file, no importer and no generated tree (`rl build --check` reports 0 differences); the bundle gains one table, additively, and `rl bundle family-eval` measures the rule where there is an answer to hold it against. SPEC R29 states the requirement. The code is `src/remote_ledger/bundle/family.py` (the rule), `family_eval.py` (the measurement), `catalog.py` (`Collected.completions`, `assemble`), `writer.py` and `verify.py`.

### D353 — The rule, and why it is made of signals

**The question is not which remote a device has but which keys its address answers to.** Two keys are the same key when they send the same signal (a blob of `signals`, D91: one id is one blob), and a signal is an address and a function. So the signals one remote shares with another say that they speak one address, and nothing is read from a name, a brand or a model, which is what would not carry from Samsung to the next maker. The names enter once, to say whether two remotes call the shared signals the same keys, and then to name what is added.

**The rule**, in `family.Family.complete_remote`. Each line is one decision, and every number is a constant of the file that can be changed alone:

1. A remote is **thin** when it has fewer than 30 keys (`THIN_BELOW`: the smallest quarter of the full bundle's remotes, whose quartiles are 29, 41 and 51 keys), and **completable** when it has at least three distinct signals (`MIN_SIGNALS`) to be recognised by. A remote that is not thin is carried as it is.
2. Another remote **corroborates** it when it **plays alike** (the carrier and D78's four play fields, `merge.play_of`: a key that is added is played as it was where it was found), shares **three quarters of its signals** (and at least three), and **names the shared keys alike**: of the shared signals that both remotes give a canonical key, at least three quarters the same one, and at least two. The last is what keeps apart two remotes that send one code for two functions.
3. A remote needs **at least three corroborators**, or it is left as it is: one remote agreeing is a coincidence of two files, three are a family.
4. A signal the remote lacks is **supported** by the corroborators that have it, and is added when **two fifths** of them do (and at least two). That is the family's core and not what one generation of one model has, which is why the keys a Samsung TV remote gets are those most Samsung files have and not every key that all of them together hold. Only a key of the tier plausible or better votes (`VOTES_FROM`): a guess is not evidence.
5. It is added as **the canonical key most of its supporters call it**, if at least two do and they are at least seven tenths of the supporters that name it at all. **A signal no source agrees a name for is not added**: an unnamed key is one a person cannot read, and the data says nothing on what it is. A canonical key the remote already has is not added again, and each is added once, to its best supported signal.
6. The most supported keys come first, and the remote grows **only to 60 keys** (`TARGET_KEYS`: only a tenth of the remotes have more, the 90th percentile is 61), so a remote is made as large as the remotes of its family usually are and no larger.
7. **A remote keeps its test key and its alternative key.** A service asks a person to send one key to try a remote (Power, else Power off, Power on, Volume up, Mute: `merge.TEST_KEY_ORDER`) and offers another when Power is no good (Volume up, else Mute), and the tier of the test key is what the person's answer is about. A key that would come before the one the remote has, in either order, is not added, so the test key, its signal, its tier and the alternative are what they were; a remote with none of them may be given one and can then be tried, by an untested key.

**What the added keys are.** Their tier is **untested**, SPEC section 5's "a reasoned candidate, offered but not yet confirmed either way": nothing here is evidence about the device, and the tier does not rise with the number of corroborators. They follow the remote's own keys (and the folded fragments', D103) in the order of the canonical vocabulary, carry no text of their own (their canonical key names them), and share signals the bundle already had, so no signal row is added. **The remote's tier is the weakest of its files' keys**, not of these (a remote of plausible keys is not made untested by 46 more), and a source is credited with its own keys only (`sources.key_count`). Completion reads the remotes as the ledger has them, never one that another completion has added to, so nothing chains.

**Why the bundle and not the files.** The remote files are what the sources say, and each key of one cites where it was taken from (R18). A completed key has no source to cite but the rule and the remotes it came from, and it changes whenever the ledger gains a remote: written into the files it would be a generated copy of the ledger inside the ledger. It is derived data, and the bundle is where this ledger derives (D103). `rl lookup` and the site show the file; the bundle's remote is the file's keys and then the completion, and `remote_completions` says which is which.

```sql
CREATE TABLE remote_completions (
  remote_id     INTEGER PRIMARY KEY,
  first_n       INTEGER NOT NULL,
  key_count     INTEGER NOT NULL,
  corroborators INTEGER NOT NULL
) WITHOUT ROWID;
```

One row for each remote that was completed: its keys `first_n` to `first_n + key_count - 1` are the added ones (they are the last of the remote, so `first_n + key_count` is `remotes.key_count`), and `corroborators` is how many remotes corroborated it. **`remote_refs` is unchanged** (D105): its runs cover the keys of the files, and the added keys are the run after them, which `--verify` checks. `meta` has `count.remoteCompletions` and `completionRule`, the manifest's `counts` has `remoteCompletions`, and the table is one of the digests of `dataVersion` (`writer.DIGEST_TABLES`, after `remote_refs`). **What a version-1 reader does** is what it did for D105: nothing, since `meta.schemaVersion` stays 1 by D101's rule (adding a table or a `meta` row does not raise it). It does see more keys on a remote, which is the change.

### D354 — What the rule does to the real ledger, and how it was checked

**The ledger, measured** (`rl bundle`, the tree of this commit): 4,441 thin remotes of the 16,961 (26%). Of them 199 have fewer than three signals, 2,391 have no corroborator (a code nobody else in the ledger sends), 504 have one and 216 two; of the 1,131 with three or more, 827 are completed and 304 have nothing to add, because the family holds nothing that two fifths of it agree on that the remote lacks. **827 are completed, with 7,969 keys** (median 7 a remote, at most 46; by source 502 IR Blaster, 208 JP1, 87 manufacturers' tables, 26 LIRC, 3 hifi-remote, 1 authored), and the median completed remote goes from 23 keys to 30. The remote the section began with has **8 keys and 47 corroborators and is completed with 46: 54 keys**, the digits, the arrows, the colours, the channel and playback keys, Home, Back, Info, Guide and the inputs. What it still lacks of the physical remote is the Netflix, Hulu and Prime Video keys: they are in 4, 5 and 7 of its 47 corroborators, under the 19 that two fifths is, and the vocabulary has no canonical key for them, so they need the remote's own file.

**What it does to a service's lists** (measured with the backend's own ranking, `catalog/candidates`, on this bundle and on the bundle of the same tree built with no completion): the device models whose best remote has 10 keys or fewer go from 778 to 674, those with 25 or fewer from 6,337 to 5,564, and 2,680 models have a remote with more keys than they had. Of the 827 completed remotes, **713 could be tried and 114 could not; after, 791 can and 36 cannot**: 78 remotes that had no Power, Volume up or Mute key were given one. **For all 713 that could be tried, the test signal, its tier and the alternative are the same**, which is the point of line 7 (`test_no_completion_of_the_real_ledger_touches_a_test_key_a_tier_or_a_name` checks it over every completion, from the records). The tables `brands`, `models`, `controls`, `brand_aliases`, `remote_refs`, `signals`, `ngram`, `sources` and `vocab_*` are **row for row what the same tree gives with no completion** (a test builds both and compares); `remotes` differs in `key_count` of the completed remotes, and `keys` by the added rows.

**Leave one out** (`rl bundle family-eval`, `bundle/family_eval.py`): a remote the ledger holds in full (40 keys or more, 25 of them named) is cut down to a stub of the first key of each of ten basics (Power, Volume up and down, Mute, OK, Input, Menu, Exit, Channel up and down), at least five of them, taken out of the ledger so that it cannot corroborate itself, and completed as a real thin remote is. 400 remotes by a hash of the seed 7 give 357 stubs:

| | |
|---|---|
| stubs cut from remotes held in full | 357 |
| completed | 190 (53%) |
| keys added | 3,748, 19.7 a completed stub |
| added keys the remote has (an upper bound of precision) | 3,427 (91%) |
| named right, where the remote names them | 2,975 of 3,101 (96%) |
| canonical keys of the remotes that the stub lacks, found and named right | 2,975 of 11,565 (26%; 49% of those of the stubs that were completed) |

The 47% that are not completed are mostly the remotes whose address has no family in the ledger, as the funnel above has it for the real thin remotes. **A key added that the remote does not hold is not a wrong key**: a family's table is a superset of one model's remote, which is why the third row is an upper bound and the last a lower bound. What is measured exactly is the name: **right for 96 of every 100 keys the remote names**, and the other 4 are differences between the source files (a signal that two makers call by two functions), which `untested` is there to say.

**What each line is worth**, the same evaluation with one line changed (`test_design_quotes_what_each_line_is_worth` runs every row):

| | completed | keys a stub | in the remote | named right | recall, completed |
|---|---|---|---|---|---|
| the rule | 53% | 19.7 | 91% | 96% | 49% |
| no share of the corroborators (any two supporters) | 53% | 26.2 | 73% | 95% | 52% |
| names by six tenths and not seven | 53% | 22.8 | 90% | 93% | 53% |
| no agreement on names (line 2) | 59% | 15.9 | 89% | 93% | 37% |
| half of the signals shared and not three quarters | 58% | 18.8 | 91% | 96% | 46% |
| two corroborators and not three | 61% | 19.2 | 92% | 96% | 49% |

The share of the corroborators (line 4) is what keeps the family's core apart from what one model has: without it a fifth of the added keys are not in the remote. The agreement on names (line 2) and the share that name a key (line 5) are what keep the names right. Two corroborators and not three is a gain in the evaluation at no cost in quality, and **was not taken**: on the real thin remotes it completes 904 and not 827 (8,444 keys and not 7,969), and the floor of three is the conservative end and one number (D355).

`tests/test_bundle_family.py` has the rule on hand-made records (every line, one at a time), the exporter on a small ledger made by the importer (the rows, the tables that did not move, `--verify` on a good bundle, on eleven damaged ones, on one with no table and on one with a wrong count) and the real ledger (what a completion never changes, and the figures of this section, recomputed). **Size and time**, same machine and SQLite as D95: the bundle 62,181,376 to 62,341,120 bytes (+159,744: the 827 rows and the 7,969 keys, and no signal; gzip 15,500,695 to 15,563,694), `dataVersion` `3ff7153379e0` to `795d05ac271d` (the same for both profiles: every brand is in `selected`, D117), `rl bundle` 45 s for `full` and 39 s for `selected` of which `family.complete` is 7 s, `--verify` 33 s, `rl bundle family-eval` 4 s.

### D355 — What is not proven, and how to widen the rule

- **That an added key works on the device.** It is untested and says so in the bundle. What is measured is that the name is the one most of the remotes at the address give it; whether a given TV answers to every function of its family's table is the device's (a TV with no PIP has no use for the PIP key). The service that offers the keys does not yet say which are added: the remote endpoint has no per-key tier, so a person sees the 54 keys of the remote as one remote. That is a change in the service (the `remote` answer and the screen), not here.
- **That the family is the right one when an address is shared.** One signal can be two keys in two device classes (a code a TV and a clone's player send for different functions): the names tell them apart (line 2), and a remote that has none to compare by is never completed. A remote whose few keys all agree with a family of another class would be completed from it; the evaluation measures that as a wrong name and it is the 4%.
- **The 2,391 thin remotes with no corroborator, and the 504 and 216 with one or two,** are not helped, but for the 216: they need more remotes at their address, which is the importers' work, not this rule's. A floor of two corroborators (`MIN_CORROBORATORS`) completes 904 remotes and not 827 and adds 475 keys (8,444 in all), and in the evaluation 61% of the stubs and not 53% at the same precision and names; it was not taken because two files agreeing may be two copies of one upstream, and three is the conservative end. It is the one number to change.
- **Keys that are in few of the family.** The Netflix, Hulu and Prime Video keys of 2018 and later Samsung remotes are in three of 47 corroborators and have no canonical key (the vocabulary's `apps` group has `APPS` and `BROWSER`). They need the vocabulary to name them, and the remote's own file, which is what `samsung/BN59-01315A` would be.
- **A remote of 30 to 40 keys is not completed** though its family may hold ten more: the bound is the one that says what thin is. `THIN_BELOW` at the median, 41, makes 8,543 remotes thin and completes 1,823 with 12,698 keys; at the upper quartile, 51, 12,721 are thin and 2,752 are completed with 16,086 keys. It was not taken for a first rule: the further from 8 keys the less the remote needs it, and the smallest quarter is the one a person complains of. It is one number.
- **The estimate of D92** counts the files' keys and not the ones added, and its 5% (D95) is 5.6% now; the test that holds it is at 6%.

**To widen the rule**: `THIN_BELOW` and `TARGET_KEYS` decide which remotes and how far; `MIN_CORROBORATORS`, `SHARED`, `AGREEING`, `SUPPORT` and `NAME_SHARE` decide how much a family has to agree; line 7 is `_outranks` and is the one that keeps the test key. `rl bundle family-eval` measures any of them on the stubs, and `test_design_quotes_the_numbers_of_the_completion` recomputes this section's figures, so a changed rule is re-measured and not guessed.
