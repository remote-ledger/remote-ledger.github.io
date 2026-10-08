# Remote Ledger — Design & Build Plan

**Draft v2.1** · Status: v1 complete; all three seed remotes authored; LIRC, SmartIR and IR Blaster imports built (§14, §15, §17); 28 protocols (§18) · Implements [SPEC.md](SPEC.md) v0.11

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
    ending: tuple[int, ...]  # reserved; empty for every v1 protocol
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
to `~D`), and the relaxed `NEC1-f16` and `NEC2-f16`, which would hold 635 of
the database's codes that no registered NEC variant can (D67). `RC5` left the
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
  fifteen of the 28 protocols declare none (D3), so it now bites a file that
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
│   ├── protocols/     one module per family (28 protocols) D3 D18 §18
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

Phases 0-6 are implemented: 3351 tests, `jsonschema` the only runtime
dependency. Phase 2 landed its nine SPEC edits *before* its code, per §9 --
the spec change is what authorises the implementation. (That count is asserted by the suite itself -- see
`test_documented_test_count_is_current` -- so it cannot drift the way the
three stale "148" figures did. It was 890 before the IR Blaster work, which
added 1,367; it moves again whenever a test is added, and the figure here
has to be re-fixed with it.) Post-v1 work has landed outside the phases: RC5
(§16), the LIRC and SmartIR imports (§14, §15), and the IR Blaster importer
with the 24 protocols it needed (§17, §18), and the canonical key vocabulary
(§22). The registry holds 28 protocols.
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

source     remotes     keys      keys mapped  >=90% of keys  >=75% of keys   >=50% of keys
---------  -------  -------  ---------------  -------------  -------------  --------------
irblaster   10,013  411,265  81.9% (337,172)  44.8% (4,491)  69.9% (7,008)   93.1% (9,323)
lirc         3,138  112,789   58.3% (65,819)    18.8% (593)  49.6% (1,557)   79.9% (2,508)
smartir         62      914      81.7% (747)     54.8% (34)     67.7% (42)      79.0% (49)
authored         5      128       77.3% (99)      40.0% (2)      60.0% (3)       60.0% (3)
all         13,218  525,096  76.9% (403,837)  38.7% (5,120)  65.1% (8,610)  89.9% (11,883)

Remotes with at least 10 keys:

source     remotes     keys      keys mapped  >=90% of keys  >=75% of keys   >=50% of keys
---------  -------  -------  ---------------  -------------  -------------  --------------
irblaster    9,415  408,466  82.0% (335,226)  44.8% (4,224)  70.6% (6,653)   94.2% (8,877)
lirc         2,867  111,431   58.2% (64,915)    17.3% (496)  49.3% (1,415)   80.9% (2,320)
smartir         39      786      84.3% (663)     58.9% (23)     76.9% (30)      94.8% (37)
authored         4      120       75.8% (91)      25.0% (1)      50.0% (2)       50.0% (2)
all         12,325  520,803  76.9% (400,895)  38.4% (4,744)  65.7% (8,100)  91.1% (11,236)
```

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

**The data-size reasoning.** The IR Blaster data has **411,265 keys but only 57,709 distinct compiled signals** (14%): the whole ledger has 525,096 keys and 155,976 (30%); LIRC alone shares little (112,789 keys, 97,775 signals, the raw captures). A key row is 20 bytes and a signal is 129 on average (the longest, a LIRC capture, 628 words), so storing each signal once is what makes the catalog small: the signals are 20.1 MB of blobs for 155,964, where one blob per key would be 72.9 MB. The key rows are the other half, and the reason `keys` has no text where the canonical key says it (D89).

**The blob** is `signals.words`: a big-endian `uint16` **count of words**, then the words, each a big-endian `uint16`. The words are exactly those of the Pronto Hex string the ledger compiled the key to (`pronto.encode`): word 0 is `0000`, word 1 the frequency word, word 2 the number of burst pairs of the intro and word 3 of the repeat sequence, then the durations in carrier cycles, a mark then a space, first the intro and then the repeat (D6, D25, D78). The count is redundant with the length of the blob and with words 2 and 3 (`count = 4 + 2 x (n1 + n2)`); it is there so that a reader can reject a damaged blob without trusting anything else. One SQLite row per signal, `id` from 1 in the **sorted order of the blobs** as bytes: the length prefix makes that order by length first, then by content, so signals of one protocol and of one remote's family sit together, which is what compresses (D95). **Microseconds** are `round_half_up(cycles x period)` with `period = word1 x 0.241246 us` (D25: lossy in this direction by under half a cycle); the **carrier** is the catalog's (`remotes.carrier_hz`, what the file declared, `38000`) and the frequency word says another number (`006D` is 38,029 Hz): which an output transmits is its own decision, and both are in the vectors.

**Test vectors**, `tests/vectors/bundle_vectors.json` (`rl bundle vectors --file tests/vectors/bundle_vectors.json`): 151 keys, chosen by a hash of `(remote, n)`, 4 for each source and protocol of the full bundle (29 protocol families, with the raw captures as `(unnamed)`) and the extremes (the shortest and the longest signal, each rule and each intro, the most passes). Each vector carries the `blobHex`, `frequencyWord`, `frequencyHz`, `catalogCarrierHz`, `introUs`, `repeatUs` computed by **the ledger's own decoder run on the blob** (`pronto.decode`), and `play`: the remote's four D78 fields and `pressUs`, the intro once and then the repeat sequence `repeatPasses` times, which is what one press transmits. A Kotlin reader is right when it makes those from the blob and the carrier. `tests/test_bundle_vectors.py` holds the file to the decoder, to a second computation that shares no code with it (Decimal arithmetic on the words) and to D78's numbers. The vectors are self-contained, so they do not have to follow the catalog; they are regenerated when the decoder or the format changes.

### D92 — Profiles: `full` and `selected`

**`full`** is every brand and remote: what a backend serves and what `selected` is cut from. **`selected`** is the subset an app ships in its install, **at most 20,000,000 bytes** (the owner's target: an app that ships it keeps its whole install near 30 MB). A build over the cap fails (`--max-bytes N` overrides). The ledger has no popularity data, so the rule is a proxy plus a list a person reviews, and every number of it is a constant of `bundle/select.py` or a line of `bundle/data/selected_brands.txt`:

1. **The curated list**, `selected_brands.txt`: 112 well-known brands in the categories the owner named (television, AV receiver, set-top box, streaming box, projector, soundbar, disc player; air conditioners are out of scope), a brand per line, `#` to comment, matched by search key. **The order is the priority**: the bundle takes the brands from the top and carries each **whole** (all its models, remotes and signals) when what it adds still fits the budget of 19,000,000 bytes, by an estimate that is within 2.5% of the file; one that does not fit is skipped, the next is tried, and the command names the brands it skipped. The owner changes what ships by moving or adding a line.
2. **The proxy fill**: the other brands, ranked by **models per byte** (the number of models a brand is listed with, over what its remotes and signals would add), among those with at least 40 models and 60% of their keys mapped to a canonical key, are added in that order while they fit what the list left of the budget. Models per brand is the one popularity signal the catalog has; dividing by the cost keeps out the makers of generic replacement remotes (`BRAVO`: 2,945 models, 2,275 remotes, 112,034 keys). Of the three proxies the owner named, **models per brand ranks the brands, and remotes per brand count as the cost** (with their keys and signals); **the third, the share of keys with signals, is 100% for every brand** (every key the ledger holds has a compiled signal), so it carries no information and the share of keys that map to a canonical key stands in for it.

A remote is carried when **any** of its brands is chosen (its maker or a brand of a model it controls); a model when its brand is. A remote that is in for one brand keeps all its keys and signals, and the models it controls under brands that were not chosen are not in the bundle.

**Everything not in the subset is recorded**: `excluded_brands` holds each left-out brand (`name`, `norm`) and `api_key`, the ten hex digits (SHA-1 of the exact name, D76) of the shard of the app API (§21) that has its remotes, comma-separated when the import spells it two ways; **NULL where the app API has none**, because the API serves the IR Blaster import only. 4,992 brands are left out of the selected bundle, 228 of them with no shard, and **1,794 remotes of LIRC and SmartIR (the authored ones counted with them) that it leaves out are in no static file of the ledger at all** until the full bundle (or a catalog service built from it) is published. That is a finding for the owner, not a decision of this change: it is the reason `full` exists.

**What the numbers say** (D95): at the 20 MB cap the list does not fit. Samsung, LG, Sony, Panasonic and Philips, the first five lines, are 11.6 MB of the 19 by the estimate (Sony alone has 1,061 remotes, and most of the bytes of a brand with old equipment are LIRC raw captures: 5.0 MB of signals for 1,375 of the 4,394 remotes); the bundle carries **35 brands of the list and one by the proxy**, and **77 brands of the list are left out for lack of room** (the first are JVC, Grundig, Thomson, Telefunken, Loewe Opta, Beko, Vestel...). Levers the owner has, none taken here: reorder the list so the brands that matter most come first (the cheap ones are taken wherever the budget allows), shorten it, raise the cap, or drop what is bulkiest per remote (the LIRC raw captures, about 6 MB of the 19). **The owner signs off on the list**; until then it is a proposal.

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
| file | 50,311,168 B | 19,443,712 B |
| `gzip -9` | 12,720,486 B | 4,477,480 B |
| brands | 5,028 | 36 |
| models | 276,249 | 105,233 |
| remote files | 13,218 | 4,394 |
| remotes, the fragments of D103 folded | 12,903 | 4,096 |
| keys | 525,096 | 165,646 |
| signals | 155,976 | 65,691 |
| left out | none | 4,992 brands, 8,824 remotes, 359,450 keys |
| `rl bundle` (read, build, write) | 23.8 s, 0.8 GB peak | 12.7 s, 0.55 GB peak |
| `rl bundle --verify` | 21 s | 12 s |

Where the bytes are, `full` (selected is the same shape): signals 22.4 MB (44.5%), keys 10.8 (21.5%), models 6.9 (13.8%), ngram 5.2 (10.2%), controls 3.4 (6.7%), remotes 1.3 (2.7%). For the app API (§21) the same catalog was 57 MB and 9,817 files, and 12.9 MB gzipped; the whole ledger's compiled corpus is 358 MB. The IR Blaster signals alone are 8,793,390 bytes as blobs (8.8 MB of binary words) and 280,511 bytes gzipped on their own (all 155,964: 20.1 MB, 892,095 gzipped, 23 times smaller). Sorted, the signals compress far better than the rest of the file (the other 28 MB gzip to about 11.8 MB), so **on the wire the catalog is its rows and not its signals**.

**What `--verify` checks** (21 s on `full`, no check repaired): the manifest's sizes and hashes; `application_id`, `user_version`, `page_size`, `quick_check`; every count against `meta` and the manifest; `dataVersion` recomputed from the rows; every reference between tables; a brand's models are its range; the grams recomputed from the models' names; every remote is a file of the tree and that remote, with the files folded into it (D103: id in path order, carrier, protocol, tier, play numbers, and every key's canonical id, text, confidence and blob); `remote_refs` is the refs of the files the profile carries, each to its carrier (D105); the full profile has every remote of the tree and the selected one every remote of a brand it carries; 150 remotes (chosen by hash) compiled again from `build/pronto/` where it has them, the compile stage's own output, and their Pronto strings compared with the blobs; a sample of models and remotes linked both ways; and **every `(signal, carrier)` decoded by the ledger's decoder and encoded back to the same words** (155,964 of them on `full`). `tests/test_bundle_verify.py` breaks a good bundle in 24 ways at the row level and in several at the file level (a changed byte, a wrong size, a missing file, a stale tree, a stale compile artifact) and requires each to be reported.

**Not proven.**

- **No Android has opened it.** The schema is written for SQLite 3.28 and a test refuses the newer features by name, but no bundle was read on a phone or by a SQLite older than 3.45, and no Kotlin reader exists: the vectors are the oracle for one, not a test of one. Room's `createFromAsset` validates a schema against its entities, and `WITHOUT ROWID` tables have not been tried with it; reading with `SQLiteDatabase` needs none of that.
- **Search quality.** The structures are what a matcher needs; how well a query finds its remote is measured in §24 (D99), and only on generated queries.
- **The selection is a proposal.** It is a judgement of popularity on a list nobody but its author has read. The owner signs off on it; 77 of its brands do not fit in 20 MB.
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
4. **Model keys.** A run is one to eight adjacent tokens of a line. The key of the whole `model` and each of its runs weigh 1000; the runs of each text weigh 900, among all models when they have 4 to 24 characters and a digit (a model number has one) and among the models of the named brands alone when they have none (`roku ultra`), never when the run is itself a brand's key. Sixty keys at most, **longest first**; a run inside a longer run that gave a candidate by exact or edit at 900 or more is not tried (`bdp-s360` is `bdps360` and not also another brand's `s360`).
5. **Candidates.** From the grams of a key (D90) the models sharing at least 40% of them, the forty sharing most; and the same inside each named brand's models.
6. **Score** = similarity (at 800 or more) x the key's weight x a **brand factor**: 1000 if the candidate's brand is a named one, 600 if a brand was *given* and it is not that one, else 950. Under 650 is dropped. **A model counts only if it matches a real entry**, so an invented model number gives nothing; and **when no model matches but a brand is named the answer is the brand and its models** (most remotes first, fifty at most).
7. **Order**: score, similarity, the longer catalog key, then names.

**Why these.** Integers because a port in another language must get the same order, and a float's last bit is not the same in two languages. The look-alike half-edit is the "letters for digits only where safe" the owner asked for: an OCR's `O` for `0` is close, not equal. The brand factor 600 and the floor 650 are how a model an LLM made up, or a real model of the wrong brand, is dropped when the provider named a brand. **What the first version got wrong, found by running it on the catalog** (D99): a two-letter brand (`LG`) was never named, because a brand needed three characters; a brand named by a stray word of a text (`DVD` in a model's own name) took every model of another brand away, so the penalty became the *given* brand's alone; a long run that matched a short model by its start (`PANASONIC` under another brand) hid the exact match inside it, so a prefix match does not hide; and a model of six tokens (`32 LC 2 RB - ZJ(DVD)`) was never tried, so a run may be eight.

**Speed.** A query takes **about 3 ms at the median, 11 ms at the 95th percentile and 28 ms at worst** over the full catalog (320 generated queries, one thread, a 64-core machine, SQLite 3.45, caches cold at the start; runs differ by a millisecond or two); about 1.6, 5.5 and 11 ms over the selected bundle. Opening the index reads the brands (5,028) and nothing else; a model's key, its remotes and a gram's posting list are read when a query needs them, with small caches (`rl bundle search-eval --timing` reports it). Two things keep it there: the distance stops as soon as it is certainly above what could reach 800, and the posting lists of a key are counted once for all the brands it is asked in.

### D97 — The vectors a port is held to: `tests/vectors/matching_vectors.json`

Written by `rl bundle matching-vectors --file tests/vectors/matching_vectors.json` (`--check` compares). **Self-contained and independent of the data**: a small catalog of 45 entries written in `bundle/matching_vectors.py` (a pair of models one character apart, one that starts another, a part of a longer model under another brand, one model under two brands, brands written with spaces, hyphens and accents, a brand with models to list, a model with no digit, a model whose name holds a brand alias, one company spelled two ways), and 62 queries, each with a note on the rule it shows, and the matcher's answer: for each candidate the brand, model, remote ids, integer score, evidence and, for a brand, its models. The first 41 are the queries the file had before brand aliases existed, unchanged; the other 21 are Chinese (D102). The file also holds the constants (and `HAN_RANGES`), the 11 aliases of the catalog's brands, the tokens and keys of 19 texts and the similarity of 23 pairs of keys. Because it does not follow the repository's data it can be held to the code exactly, and a test does (`test_the_committed_vectors_are_what_the_matcher_gives`); it changes when the rules do. A port builds the small catalog, asks the queries and compares integers. The similarity vectors are checked in the suite against a textbook matrix implementation written there, and the answers against the rules by hand (`tests/test_matching.py`).

### D98 — The search test set and its harness

`rl bundle search-eval [--bundle DIR] [--queries FILE] [--seed N] [--per-class N] [--out FILE] [--timing]` reads a written bundle, generates queries from it, runs each as **typed text** through the matcher over the bundle's own tables, and prints a Markdown report: a table of top-1 and top-5 per class and overall, the hand-written queries in a table of their own, the generated queries that missed, and with `--timing` the time each took. Same bundle and seed, same report.

**The generator** takes devices from the bundle (a `kind` 0 model whose search key has six characters or more and a digit, a name of at most 40 characters, at most two of a brand in a class, so that no big brand is the test set), ranked by a hash of the seed and the device, and applies **eight classes**, 40 devices each, 320 queries: `exact` (`SAMSUNG UN50NU6900F`), `case` (lower, upper or title), `punctuation` (separators stripped, or a space at every letter-digit boundary, or one hyphen), `dropped-suffix` (`UN50NU6900`, for a model with trailing letters), `typo` (one character dropped or two neighbours swapped, never the first), `brand-partial` (the brand and the first 60% of the model), `model-only` and `reversed` (`UN50NU6900F SAMSUNG`). **Expected** are the remote ids the bundle's `controls` give the device; **a hit** is an answer that is a model, not a brand, whose remote ids contain all of them. Top-1 is the first answer, top-5 any of the five.

**Honesty.** The queries are the catalog's own names changed by mechanical rules. They never misspell a brand, never name a device the catalog does not have, never use a nickname or a model written from memory, and the exact class is a lookup. Real people type worse. **The rates are an upper bound on search quality, not an estimate of it**, and the report says so in its own text. The classes were written once and not changed after a result was seen. What was changed after seeing results is the matcher (the four corrections in D96), and, once, how the devices of a class are sampled (for speed; the sample it gave was kept); the numbers below are those of the last version.

**Real queries.** `src/remote_ledger/bundle/data/real_queries.json` is the place, read by default (`--queries FILE` reads another) and reported in a table of its own. Format: `{"format": 1, "queries": [{"query": "samsung un50nu6900", "expect": {"brand": "SAMSUNG", "model": "UN50NU6900F"}, "note": "who typed it, where"}, ...]}`. `expect` names a device as the catalog spells it (case, spaces and punctuation do not matter) and the remotes that control it are what the answer must contain; `null` means the query names nothing the catalog has and is right when the answer holds no model (an invented model, a phrase). A device that the bundle being scored does not have is counted and left out of the rates, and the report says how many. The file ships empty, with its own description and one unscored example; **the owner's hand-written queries are the number that matters for the 95% target**, and none have been written.

### D99 — What was measured, and what is not proven

Seed 1, 40 devices a class. **Selected bundle** (36 brands, 105,233 models) and **full bundle** (5,028 brands, 276,249 models):

| class | selected top-1 | selected top-5 | full top-1 | full top-5 |
|---|---|---|---|---|
| exact | 40 (100.0%) | 40 (100.0%) | 39 (97.5%) | 40 (100.0%) |
| case | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) |
| punctuation | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) |
| dropped-suffix | 36 (90.0%) | 40 (100.0%) | 36 (90.0%) | 40 (100.0%) |
| typo | 36 (90.0%) | 40 (100.0%) | 32 (80.0%) | 39 (97.5%) |
| brand-partial | 26 (65.0%) | 35 (87.5%) | 24 (60.0%) | 34 (85.0%) |
| model-only | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) |
| reversed | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) | 40 (100.0%) |
| **all (320)** | **298 (93.1%)** | **315 (98.4%)** | **291 (90.9%)** | **313 (97.8%)** |

**Read it as the upper bound it is** (D98). **A partial name is ambiguous**: `LG 42 LC 45` is the whole model `42 LC 45` and the start of `42 LC 45 - ZA`, and the device asked for was the second; `PHILIPS 24 CE 75` starts forty models. All five misses of the selected bundle, and six of the seven of the full one, are partial names (a person typing half a model number has to be shown a list, which the answer is: five candidates, and the brand's models when none matches). The seventh is a typo test on a name with two spaces in it (`UTV 21  70`). The 95% target is not established by this: it needs real queries.

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

**The vectors left behind**: `tests/vectors/suggest_vectors.json` (`rl bundle suggest-vectors --file FILE`, `--check` compares) holds 238 cases of `suggest` and 26 of `suggest_models`, written by the Python once it gave what the Kotlin gave, over a catalog of 79 entries (the small catalog of D97, a few brands more) and 14 brand aliases (D101), self-contained and independent of the data. Each case has a note that says which rule or tie-break it shows; six queries are typed a character at a time (76 cases); limits of -3 to 20, among them every limit that falls on the edge between two brands' models; brands, models with their ids and remote ids, a part number. `tests/test_suggest.py` holds the file to the code exactly, to its notes (each rule and tie-break must be among them), and to an independent reference written in the test (what is offered inside a brand and the brands that start a query, worked out again from the entries). The 210 of its 264 cases with no Chinese in them were also answered by the Kotlin over its small catalog: all equal. The ids in the file are the small catalog's numbering (`MatchIndex.from_entries`): brands from 1 in the order of their search keys, models from 1 in that order inside a brand, which a tie that goes to the lower id depends on.

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

**The data, both profiles, the rule as it stands** (`merge.fold` over the whole ledger; `selected` counts the files its brands carry, D92):

| | full | selected |
|---|---|---|
| IR Blaster devices | 9,361 | 2,549 |
| devices with two or more fragments, and their fragments | 573, 1,225 | 364, 798 |
| fragments with no test key, and with one | 479, 746 | 348, 450 |
| fragments with no test key that **can be folded** | **315** | **298** |
| of those, with more than one qualifying sibling (best key decides in 8 and the lowest id in 4, both profiles) | 12 | 12 |
| fragments with no test key that **cannot** | 164 | 50 |
| no sibling with a test key | 21 (10 devices) | 13 (6 devices) |
| only siblings at another carrier | 64 | 27 |
| only siblings with the same carrier and another play rule | 79 | 10 |
| pairs of testable fragments | 183 | 92 |
| of those, with the same carrier and play rule (a full merge would join them) | 36 | 32 |
| of those, with the same test key and different signals | 10 | 8 |
| rows a **full** merge (every fragment of one carrier and rule into one remote) would remove | 357 | 336 |
| rows the rule removes | 315 | 298 |

In words: 9,361 devices are in the ledger as IR Blaster remotes, and 573 have two or more fragments (1,225 fragments). 479 fragments have no test key and 746 have one. 315 of them can be folded, 12 have more than one qualifying sibling and 164 cannot: 21 have no sibling with a test key, 64 only siblings at another carrier and 79 only siblings with another play rule (the NEC family: an NEC1 fragment beside an NEC2 one has the same carrier and a different intro; Sony fragments nearly always share both). Of the 183 pairs of testable fragments, 36 pairs of testable fragments share a carrier and a play rule, and 10 of them have the same test key with different signals: a Power in two Sony protocols for one device in seven, a Power in RC5 and another in RC6, and a Power in NEC1 and another in NECx1 (twice). The other 26 are a Power in one fragment and a Volume up (22) or a Mute (4) in the other. A full merge would remove 357 remotes, 42 more than the rule does (36 are those pairs, 6 are fragments none of which has a test key, so that the merged remote could still not be tried).

**Why the conservative rule, and why the data does not say it is wrong.** The physical remote has both sets of keys, so a fragment with no test key loses nothing when it rides on a sibling: the sibling's test key is still the one tried, and its keys are all there. Two fragments that each have a test key may be **two encodings of one key**, and a device may answer to only one of them: 10 devices have a Power in two protocols with different signals, and a full merge would join 36 pairs, 26 of which put a Volume up or a Mute in the shadow of a Power in the other encoding. Folding them would remove a signal that a person could try, and the device may answer only to the other. The data shows the case is real, and mostly Sony, so the rule keeps each of them a row; that costs 42 rows the full merge would remove, which the table counts so the owner can see them. The measurement does not show the rule is wrong in the other direction either: a folded fragment has no key to try, so folding it takes no row a person could try away (the 12,018 remotes that can be tried before can be tried after, with the same test key and signal; a test holds this over the whole ledger).

**What it leaves, and why.** 164 fragments stay a row that cannot be tried (143 of them with a testable sibling that plays differently). They need a remote that has a carrier and a play rule **per key**, which the format does not have: that is a change of format and not of this rule. 21 have no sibling with a test key at all, and a wider test key rule (Volume down, Channel up, Play, Stop) would reach some of them; that is a different question from folding and is not touched here.

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
