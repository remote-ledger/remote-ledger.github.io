# Remote Ledger — Design & Build Plan

**Draft v2.0** · Status: v1 complete; all three seed remotes authored; LIRC import designed (§14) · Implements [SPEC.md](SPEC.md) v0.9

SPEC.md says *what* the format has to hold and why. This says *how it gets
built*: the resolved open decisions, the one intermediate representation
everything funnels through, the protocol encoders, the repo layout, and a
seven-phase plan where each phase ends in something you can run.

Every design decision below is numbered **D*n*** and names the spec
requirement (**R*n*** / **OD*n***) it serves. Numbers are stable
identifiers assigned in the order decisions were *made*, not the order they
appear — D16–D20 came out of the v0.1 review, D21–D26 out of the v0.2
review, D27–D29 out of the v0.3 review, and D30–D33 out of the v0.4 review,
each sitting wherever it belongs topically. §11 lists what changed.

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
what that costs and how the test strategy pays for it.

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
`NEC1` 108 000 µs, `Sony20` 45 000 µs, `Samsung32` 108 000 µs.

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

That is the whole v1 registry, and the narrowness is the point: it matches
the project's own premise that coverage is built one lookup at a time
(R19), rather than declared up front and half-delivered.

**Adding a protocol is a self-contained change** requiring all three of:

1. Its IRP string in the module docstring, with the source it came from.
2. At least one **independently cited** golden vector (D10).
3. An invariant test — framing, bit count, total extent.

Backlog, each blocked on that gate and none scheduled: `NEC2`, `NEC`
(`S` defaulted to `~D`), `Sony12`, `Sony15`, `RC5`, `RC6`.

`NEC2` and `NEC` are the tempting ones — both are a few lines' difference
from `NEC1`, and waving them through on that basis is precisely how an
unverified encoder ships. The gate applies to them identically. RC6 is
further out regardless: its trailer bit is double-width, which needs a
bitspec exception none of the other protocols require.

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
  three v1 protocols declare an extent, so in practice this only bites
  raw-only files — which is exactly where truncation is most likely. A
  truncated form with neither an extent nor `defaultGapUs` is a validation
  error, never a guess (D4a).

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

**D3b — Deferred: the RC5/RC6 toggle bit.** RC5's `T` flips on each press,
so one Pronto string can carry only one of its two states. With RC5 moved to
the backlog (D18), nothing in v1 emits a toggle and the compiled artifact
(D20) has no such field — v0.1 promised one for a protocol it no longer
ships. When RC5 does land, the open choice is: emit `T=0` plus a
`toggle: true` marker for the player, or emit both states as two candidate
groups (D16), which reuses machinery that will already exist. Recorded so
it isn't rediscovered from scratch.

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
│   └── unresolved.schema.json          checkout to read from
├── remotes/                            hand-authored, the actual ledger.
│   ├── sony/RMT-B118P.json             `remotes/**/*.json` is uniformly
│   ├── topping/RC-15A.json             one remote — no reserved names
│   └── samsung/BN59-01199F.json
├── build/                              generated, committed (OD4), wholly
│   ├── index.json                      owned by the generators (D19)
│   ├── warnings.json                   every warning, corpus-wide (D32)
│   └── pronto/<mfr>/<model>.json
├── site/                               generated static site (R17)
├── src/remote_ledger/
│   ├── signal.py      IrSignal, cycle quantization        D1
│   ├── pronto.py      encode / decode                     D5 D6
│   ├── protocols/     nec.py sony.py samsung.py rc5.py    D3
│   ├── forms.py       form → IrSignal, precedence         D7
│   ├── crosscheck.py  pairwise comparison                 D8
│   ├── layout.py      grid-template-areas parser          D14
│   ├── model.py       load, normalize, dataclasses
│   ├── validate.py    R15 + R11 area→key
│   ├── index.py       R14                                 D13
│   ├── lookup.py      R16
│   ├── site.py        R17                                 D15
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
| `rl validate [path]` | R15, R11 | Schema + semantic checks; exit non-zero on any error |
| `rl compile [path] [--check]` | R12 | Write `build/pronto/…`; `--check` diffs instead |
| `rl check [path]` | R13 | Cross-check every candidate group; the data gate |
| `rl index [--check]` | R14 | Regenerate `build/index.json` |
| `rl site [--check]` | R17 | Generate `site/` |
| **`rl build [--check]`** | **D19** | **The whole pipeline — validate → check → compile → index → site — whole-tree; `--check` is the CI gate for drift *and* orphans** |
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
[{ "device": "Vizio D32h-J09", "checked": "2026-09-14",
   "searched": ["IRDB", "LIRC remotes", "SmartIR"],
   "note": "No capture found; remote is BLE, not IR." }]
```

The index (D13) folds these in, so `rl lookup` and the site can say
"checked, nothing found, here's when and where we looked" instead of
falling through to the same blank as an unsearched device. Small addition,
and R20 doesn't hold without it.

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
| **Set-like** — order carries nothing | `aliases`, `controls`, `warnings` (D32), `expandedFrom.inherited` / `overridden`, `unresolved.json`'s `searched` | In a generated artifact, emitted in a declared canonical sort — required, since byte-stability has to come from somewhere. In a hand-authored file, left exactly as written; `rl fmt --sort` normalizes on request, never silently |

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
model, aliases, and controls; a per-remote view showing each key's forms
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
the data.

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
| A hand-written encoder is subtly wrong, with no oracle to catch it | **High** | D10's cited golden vectors, gated: no protocol ships without one. The honest residual: a protocol whose only published vectors share a common ancestor error stays wrong. If a vector can't be sourced independently, don't ship the protocol. |
| Rounding drift breaks byte-identical output (R12) | Medium | D6 pins `ROUND_HALF_UP` on `Decimal` and the clock constant; corpus snapshot test in CI |
| ~~Samsung32's lead-in is disputed~~ — **resolved, and it was never a timing dispute** | — | The protocol was misidentified. The BN59-01199F speaks `NECx2`: an **8**-unit lead-in at 564 µs, 8 × 564 = 4512 µs, which is what the "real captures" were showing (D18, PR #7). Phase 3's interim explanation, Samsung36's 9-unit lead-in at 500 µs, fitted the same ~4500 µs and was wrong. It was recorded as a hypothesis only, never as data, and the device waited in `unresolved.json` until a cited source settled it (D12) |
| `raw` jitter tolerance passes a genuinely wrong code | Medium | D8's tolerances are tight by LIRC standards, symmetric so order can't change a verdict, and per-file overridable; an override needs a `source` explaining itself |
| Candidate groups (D16) let untested alternates accumulate and never get resolved | Medium | They're open questions by design, so make them visible rather than silent: `rl lookup` and the site surface every non-`primary` group with its tier, and the index rolls up an "unresolved alternates" count per file |
| Site upkeep outgrows its value (OD2) | Low | D15 keeps it dependency-free; a framework is the signal to revisit OD2 |
| Committed `build/` creates merge noise | Low | Single-author repo (OD1); D19's tree check makes drift loud |
| The LIRC import makes the repo and the site large (~2,800 upstream files, ~115k keys with timings) | Medium | D40 shards the index and site per remote, so no committed file grows with the corpus. D20's one-line integer arrays cut raw forms roughly in half. The size is measured before the data PR, not after |
| Imported data is taken for authored data | Medium | R19's conditions, enforced: the `remotes/lirc/` path is the licence *and* trust boundary, every form's citation names its upstream file and line, nothing is above Plausible, and the site labels imported remotes as imported |
| The GPL reading of the LIRC database is wrong | Low | It is Debian's reading, cited, and the only one on record (the upstream repository states no licence). The boundary is one directory, so reversing it is one deletion and one re-import |
| Provenance quietly degrades as variants are expanded and re-expanded | Low | D22's field partition is mechanical and `expandedFrom` is machine-checkable; `rl fmt --expand` shows exactly what a variant produced. D21's non-positional ids remove the silent-retarget path |
| A `claims` entry gets written to satisfy the validator rather than to inform | Low | **Presence is machine-validated; truthfulness is not.** The validator confirms `reason` and `source` are there and non-empty — it cannot confirm the source says what the claim says, or that it exists. Reviewing that is a human job, and with OD1 the human is you, reading the diff. The gain over v0.3 is narrow but real: nothing had to be written down at all before |

---

**Start here:** Phase 0 and Phase 1. `signal.py` + `pronto.py` +
`protocols/nec.py` + one cited NEC vector is roughly 400 lines and settles
the question the whole project rests on — whether a hand-written encoder
reproduces a published Pronto string byte for byte.

---

## 11. Revision history

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

Phases 0-6 are implemented: 684 tests, `jsonschema` the only runtime
dependency. Phase 2 landed its nine SPEC edits *before* its code, per §9 --
the spec change is what authorises the implementation. (That count is asserted by the suite itself -- see
`test_documented_test_count_is_current` -- so it cannot drift the way the
three stale "148" figures did.) `rl encode --protocol NEC1 --device 0x88 --subdevice 0x77
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
2. **Gate 2b, a golden vector per protocol** (post-v1). The strongest is
   IrpTransmogrifier's own test assertion for NEC1 `D=12,F=34`. Our timings
   reproduce it exactly, but our bytes differ in 4 words. The cause is
   quantization, not a constant: IrpTransmogrifier rounds against the
   nominal carrier and D6 rule 4 against the word's period, and MakeHex does
   a third thing (D6, after rule 10). So gate 2b now checks timings under
   the tool's own rule and pins the byte differences (D10).

| Protocol | Gate 2b vector | Provenance |
|---|---|---|
| NEC1 | IrpTransmogrifier test assertions, two parameter sets | published |
| Sony20 | IrpTransmogrifier `Decoder.java` string, plus a 128-function MakeHex sweep matching our bytes on 127 | published, and reproducible |
| NECx2 | IrpTransmogrifier 1.2.14 `render` output | reproducible only. **No published NECx2 vector was found**, and `test_registry` warns about it on every run |

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
figure, and that table could not be retrieved. So this is a contradiction
between sources rather than a settled error, and `unresolved.json` records
it as one. What is *not* in doubt is the protocol and the function codes:
27 of 38 functions cross-check between the capture and IRDB with zero
mismatches, which is the methodology §1 itself describes.

The irony is worth stating plainly. §1's BX510 row is what motivated
candidate groups (D16) — competing subdevices that must coexist without
being cross-checked against each other. That machinery is right and stays;
it is the specific subdevice values that the evidence disputes.

SPEC v0.8 carries this into §1 itself (§9's post-6 row). §1's table now
states the lookups' claims as claims, and a paragraph after it records what
the ledger actually holds, so the spec no longer describes a file that
differs from the one on disk.

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

**The result, first import** (lirc-remotes @ `291b40f`, recorded in
`remotes/lirc/IMPORT.md`):

- **Imported:** 3,139 remotes from 2,655 upstream files, 112,846 keys.
  Of those, 108,565 are `raw`, 3,348 NEC1, 766 Sony20 and 167 NECx2 `irp`.
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

