# Remote Ledger — Requirements Spec

**Draft v0.9** · Status: §12 resolved; v1 implemented (Phases 0-6); LIRC
import specified (R19) · Depends on nothing upstream (self-contained)

A self-contained JSON file per remote, where every key can hold several
independently-sourced representations at once — each with its own confidence
and citation — compiled to Pronto Hex for actual use.

## 1. Problem

People know their *device* — a BDP-BX510, a UN50NU6900F — not the remote
model that ships with it, and not which of several candidate codes is
actually trustworthy. Every lookup starts over from a web search, a
retailer's "compatible with" list, or a service manual, and the trust
question — is this code *right*, or just plausible — usually gets lost once
the code is copied out.

Three real lookups hit that pattern. Each one came back with several
candidates, each trusted for a different reason. The table records them as
they came back, claims and all:

| Device | Candidate | Claimed | Why |
|---|---|---|---|
| Sony BDP-BX510 | RMT-B118P, subdevice 218 | **Verified** | Every derived function value cross-checked against hifi-remote.com's official Sony BD command table — zero mismatches. |
| Sony BDP-BX510 | alternate subdevice 234 | Untested | Same buttons, offered as a fallback, not yet confirmed on real hardware. |
| Sony BDP-BX510 | alternate subdevice 242 | Untested | Same buttons, a second fallback address. |
| Topping RC-15A | RC-15A | **Verified** | NEC address/command complement check (byte1==~byte0) passed on all 8 captured codes. |
| Samsung UN50NU6900F | BN59-01199F | Plausible | No independent capture of this exact remote; inherited from a sibling remote's shared universal address. |

One device, several candidates, different reasons to trust each. The format
has to keep the device, the candidates, *and* why each one is believed to
work — not collapse them into a single answer the moment someone copies out
"the" code.

**Checking them proved the point by overturning two.** What the ledger now
holds for each lookup:

- **`sony/RMT-B118P.json` uses subdevice 226, not 218.** A 2015 hardware
  capture of the remote and IRDB's Sony Blu-ray entry agree on 226, and
  IRDB's `26,218` is a PlayStation button set. 27 of the 38 keys agree across
  the two sources with zero mismatches and are Verified. The other 11 rest on
  the capture alone and are Plausible, so the file as a whole is Plausible.
  The hifi-remote table cited for 218 was never retrieved. No source mentions
  234 or 242. The capture names the BDP-S185, not the BX510, as the player
  the remote shipped with. The BX510 therefore stays in `unresolved.json`
  (R20) as a contradiction between sources, not as a settled error.
  DESIGN.md §13 has the detail.
- **`topping/RC-15A.json` uses device 0x88, subdevice 0x77, not 0x11/0xEE.**
  The cited capture is IRremoteESP8266 output, and that library prints NEC
  MSB-first. Read as NEC1's LSB-first fields, `0x11EE18E7` is `88 77 18 E7`.
  The ledger's first entry had transcribed the digits directly, so its
  compiled Power code never matched the remote. The complement check the
  lookup relied on passed anyway: reversing the bits of a byte and of its
  complement leaves a complement pair, so the check cannot see a bit-order
  error, the commonest NEC transcription mistake. The capture holds 12
  codes, not 8. All 13 keys, including OK from a second capture, are now
  Verified against two Flipper-IRDB files that record the address as `88`
  in a different encoding.
- **`samsung/BN59-01199F.json`** holds as claimed: Plausible, NECx2 from
  IRDB's shared `7,7` Samsung TV address.

Both "Verified" claims were wrong about the address. That is exactly why
the format keeps a claim, its citation and the file as separate things
(R5, R18). The Topping's did get copied into a file as its tier, and for a
while the error looked authoritative.

## 2. Prior art

Nothing here is governed by a standards body — it's a stack of community
formats that each grew to own one layer of the problem.

| Layer | Format | Notes |
|---|---|---|
| Protocol | **IRP notation** | A small formal grammar for describing a protocol — Sony12, Sony20, NEC1 — as parameters, timing, and checksum in one line. Maintained on the JP1/hifi-remote wiki; `IrpTransmogrifier` is the actively-developed reference implementation. Closest thing to an actual standard in this space. |
| Waveform | **Pronto Hex** | Philips' proprietary format for a Pronto remote's learned/generated codes, adopted everywhere as the raw interchange format because it needs no protocol decoder — just carrier frequency and burst-pair timings. The compiled output this project produces. |
| Remote file | **LIRC's `lircd.conf`** | The de facto standard remote-configuration file, and the shape of most public captures in the wild. The one database R19 admits for import, and otherwise a common authoring source, not a dependency. |
| By address | **IRDB** ([probonopd/irdb](https://github.com/probonopd/irdb)) | A large crowd-sourced code database, organized `<manufacturer>/<devicetype>/<device>,<subdevice>.csv` — by *protocol address*, not model name. |
| By model | **SmartIR** ([smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR)) | Each JSON file carries an explicit `manufacturer` and a `supportedModels` array. Closest existing prior art to Remote Ledger's shape — but no confidence tier, no citation field, one code per function, not several coexisting ones. |
| Layout | **CSS Grid's `grid-template-areas`** | A named cell per line, `.` for a gap, spans by repeating a name — an already-standardized grammar, not a bespoke one. See §6. |

**What that means here:** IRDB dedupes by address and loses the retail model
name; SmartIR keeps the model name but has no notion of confidence,
citation, or competing representations. §5 borrows a piece from each: IRP's
parametric (protocol, device, function) shape as one of a key's possible
`forms`, and SmartIR's per-file model metadata as the seed of a generated
index — but adds the citation and multi-form structure neither one has.

## 3. Goals

- One remote → one self-contained JSON file: manufacturer, model, aliases,
  the devices it controls, its keys, and how it's laid out, all in one place.
- Let a key hold several coexisting representations — a parametric
  (IRP-style) form, a raw capture, a Pronto Hex string — each independently
  tagged with a confidence tier and a citation.
- Compile whichever form is trusted into Pronto Hex for actual playback, and
  cross-check it against any other form the same key holds.
- Keep a button's physical arrangement separate from its code, so the
  factory layout and a person's own rearrangement are the same kind of
  object.
- Generate the manufacturer+model index as a view over every file's own
  metadata. Nothing hand-maintained can go stale.
- Make adding one device cheap and require nothing upstream. Coverage is
  built one lookup at a time, and seeded by importing what an openly
  licensed source already holds, at a tier that says it is only imported
  (R19).

## 4. Scope

**In scope:**
- The JSON schema: one file per remote, multi-form keys, confidence and
  citation per form, optional layouts.
- A compiler: renders the trusted form(s) to Pronto Hex; cross-validates
  when a key holds more than one.
- A validator: schema conformance, every key has a usable form, every
  alias/controls/layout reference is well-formed.
- A generated manufacturer+model index and a lookup script over it.

**Out of scope, v1:**
- Capturing new remotes from real hardware (IR receivers). This project
  authors, compiles, and cross-checks; it doesn't replace `irrecord`.
- A multi-contributor review workflow, unless Open Decision 1 resolves
  that way.
- Importing any source whose licence does not permit republishing it here.
  That rules out IRDB's conditional, revocable permission, Flipper-IRDB
  files from before its CC0 cutoff, Global Caché and Remote Central. The one
  source R19 admits is LIRC's remotes database.

## 5. Data model

One JSON file, self-contained. No cross-file references needed to
understand or compile it on its own.

- **R1 — One remote = one JSON file.** Manufacturer, model, aliases, the
  devices it controls, protocol defaults, its keys, and its layouts — all
  in one place, under `<manufacturer>/<model>.json`.
- **R2 — Aliases and controls are different relationships.** `aliases`:
  other model numbers for the identical physical remote (RMT-B118P/B119A/
  B117A/B109C — one hardware design, several SKUs). `controls`: the
  products it operates (BDP-BX510, BDP-S1100...). Conflating these was
  exactly the ambiguity that made the BX510 lookup hard in the first place.
- **R3 — Protocol and repeat behavior declared once per remote.** Carrier
  frequency, bit timing, and minimum-sends-per-press live at the remote
  level, not repeated on every key. Minimum sends is a fact about the
  hardware (Sony SIRC needs 3) — it has to survive independent of which
  form produced a given key's code.

  **Exactly one protocol per file.** No form and no variant may override it.
  A remote whose buttons genuinely speak two protocols — some universal
  remotes switch by mode — is out of scope for v1; it gets an entry for the
  protocol that is known, with the rest recorded as absent, the same honest
  fallback R7 and R20 use elsewhere.

  The block's fields: `carrierHz` and `minSends` are required. `name` names
  a protocol in the registry and may be **omitted** for a capture whose
  protocol has not been identified, which then forbids `irp` forms in that
  file. `unitUs` overrides the registry's IRP unit, `defaultGapUs` supplies
  a terminal gap where the protocol declares no extent, and `tolerance`
  loosens the cross-check of §7. Each of those three widens what passes, so
  each requires a citation — see R18.
- **R4 — Each key holds a `forms` array, not a single code.** A parametric
  form (device/function values against the remote's declared protocol), a
  raw capture, and a compiled Pronto Hex string can all coexist for one
  button. None of them is required to agree until the compiler checks (§7).

  **Forms are partitioned into candidate groups, and the distinction is the
  whole point.** "Several forms on one key" does two unrelated jobs:

  | Relationship | Means | Must agree? |
  |---|---|---|
  | Forms *within* a candidate | Several representations of **one signal** — an `irp` form, its `raw` capture, a `pronto` string | **Yes.** Disagreement is a real error (R13) |
  | Candidates *within* a key | Several hypotheses about **which signal** the device answers to | **No.** Disagreement is the entire point |

  Every form carries an optional `candidate` tag, defaulting to `"primary"`.
  §1's own BX510 example — subdevice 218 verified, 234 and 242 offered as
  untested fallbacks — is unrepresentable without this: different subdevices
  necessarily produce different waveforms, so cross-checking them against
  each other would reject exactly the data this format exists to hold. The
  `primary` group is a key's default answer; every other group compiles
  alongside it as a labelled fallback.

  **Every form has an `id`, unique within its key and never positional.**
  The id is `<candidate>.<type>`, assigned automatically only where that is
  unique within the group; a group holding two forms of one type requires an
  explicit `id` on each. Positional ids would silently retarget a reference
  when two same-type forms were reordered.

  **A `raw` form is a list of alternating mark/space microsecond
  durations.** A present sequence holds at least one duration — the way to
  say "no repeat sequence" is to omit the key, not to write `[]` — and has
  even length, so it ends on a space. The exception is `truncated`, which
  **declares** that the final space is not evidence, because a capture tool
  stops when the button is released. A truncated sequence may end on a mark;
  its final space is discarded rather than adjusted, and replaced by what
  the protocol's extent implies, or by `defaultGapUs` where there is no
  extent. A capture whose marks already exceed the extent is an error, never
  clamped — clamping would fabricate a waveform that fails the extent it
  claims. Nothing is *inferred*: an undeclared short final gap is a
  mismatch, and fails.
- **R5 — Confidence and citation attach to the form, not the file.** One
  remote's Power button can be Verified while its Home button is Untested.
  Every form names *how* it was established: a URL, "re-derived from
  `<file>`'s protocol, see commit `<sha>`," "confirmed on hardware,
  2026-09-14."

  **A `derived` form is the one exception, and it is a stronger citation,
  not a missing one.** It must be `type: pronto` — the compiler's only
  output is Pronto Hex, so nothing can produce a derived `irp` or `raw`
  form — and it must name its parent with `derivedFrom`, a form id in the
  same candidate group that is not itself derived. It must **not** carry a
  `source`: a second, human-written claim about mechanically produced data
  could only be unverifiable decoration, or a contradiction. `derivedFrom`
  *is* its citation, and R18 already admits that shape. It is also the only
  citation in the ledger that is checked automatically — §7
  re-derives it from the named parent and compares, on every build. Every
  other citation is a pointer a person must go and check.
- **R6 — A stated precedence rule resolves conflicting forms.** Selection
  runs independently **within each candidate group** (R4), and is total:

  1. **`derived` forms are excluded from selection entirely.** A derived
     form is not independent evidence, so it can never *be* the answer; it
     exists only to be checked (R5, §7).
  2. Confidence: confirmed > verified > plausible > untested.
  3. Form type: `irp` > `raw` > `pronto`, since a parametric form is what
     you'd want to re-render from anyway.
  4. Array order, lowest index first.

  Without the fourth rule two same-tier, same-type forms would be a coin
  flip and §7's byte-identical guarantee would not hold. Every candidate
  group must therefore contain at least one non-derived form: a group that
  cannot be selected from is one that cannot compile, and that is a
  validation error rather than a surprise at build time.

  **Variants declare the non-`primary` groups.** A `variants` block at the
  remote level names each one with a label, a confidence and a citation of
  its own. An entry carrying an `override` of `device`, `subdevice` or
  `function` additionally *expands*: for every key whose `primary` group has
  an `irp` form, the selected one is copied with the override applied and
  tagged with that candidate. Stating it once is what keeps the BX510 from
  needing forty near-identical forms hand-written to change one byte.

  Expansion never inherits evidence. The copy takes the variant's own
  confidence and source; any `verifiedBy` is **stripped**, because a check
  that held for the original address is simply false at a different one; and
  an `expandedFrom` record names the parent form and which fields were
  overridden versus inherited, so both claims — where the address came from,
  and where the untouched parameters came from — stay separately checkable.
  An expanded form is a **regenerated cache**, recomputed and compared on
  every build; deleting its `expandedFrom` is what converts it into
  hand-authored data the variant no longer governs.

**Example — Topping RC-15A, Power key, two coexisting forms:**

```json
{
  "manufacturer": "Topping",
  "model": "RC-15A",
  "aliases": [],
  "controls": ["DX3 Pro", "D50s", "D70", "DX7s"],
  "protocol": { "name": "NEC1", "carrierHz": 38000, "minSends": 1 },
  "keys": {
    "KEY_POWER": {
      "forms": [
        {
          "type": "irp",
          "device": "0x88", "subdevice": "0x77", "function": "0x18",
          "confidence": "verified",
          "verifiedBy": "nec1-cross-source-check",
          "source": "audiosciencereview.com/.../10708/post-639756 (capture), cross-checked against Flipper-IRDB"
        },
        {
          "type": "pronto",
          "hex": "0000 006D 0022 0002 0157 00AC ...",
          "confidence": "derived",
          "derivedFrom": "primary.irp"
        }
      ]
    }
  }
}
```

**Confidence tiers:**

| Tier | Means | Example |
|---|---|---|
| Verified | Cross-checked against an independent authoritative source. A self-consistency check such as NEC's complement relation is **not** a cross-check: it holds under per-byte bit reversal, which is the RC-15A's original error. | RMT-B118P's hardware capture vs. IRDB's Sony Blu-ray table (27 keys); the RC-15A's capture vs. two Flipper-IRDB files (13 keys). |
| Confirmed | A person tested it against real hardware and it worked. | *(none yet)* |
| Plausible | A single source, not cross-checked: a retailer or aftermarket "compatible with" claim, a sibling remote's shared address, or one capture with nothing to check it against. | BN59-01199F, inherited from a sibling remote's address; the 11 RMT-B118P keys that only its capture records. |
| Untested | A reasoned candidate, offered but not yet confirmed either way. | The BX510 mode2/mode3 subdevice guesses from §1's lookup, which no source has since corroborated. |
| Derived | Mechanically rendered from another form in the same key — not independent evidence on its own. | The Pronto Hex row in the example above. |

## 6. Layout

Where a button sits and what it transmits are independent facts. A person
rearranging their own on-screen remote should never mean touching a form, a
confidence tier, or a citation.

- **R7 — `layouts` is optional and holds any number of named arrangements.**
  Not every file will have layout data right away. A remote with codes but
  no known physical arrangement is still a complete, usable file.
- **R8 — Exactly one layout, if present, is the factory arrangement.**
  Flagged `"original": true`. Everything else is free-form and named by
  whoever authors it. The original's own claim ("this button is physically
  top-right") gets a `source` citation, the same discipline R5 already
  applies to a code — it's a transcribable fact, not just an assertion.

  "Exactly one" is enforced, not merely stated: two layouts both claiming
  `original` is a contradiction about a physical fact, and whichever a
  renderer picked would be arbitrary. A file with *no* original is fine —
  R7 already allows a remote whose factory arrangement nobody has
  transcribed.
- **R9 — A layout's structure is a CSS `grid-template-areas` string.** One
  row per line, a key name per cell, `.` for a gap — real CSS. A web
  renderer drops the array straight into a `grid-template-areas` rule and
  gives each button `grid-area: <key>`; no translation needed. Any other
  renderer parses the same lines by splitting on whitespace — no CSS engine
  required. Repeating a name across adjacent cells spans it, so there's no
  separate `rowSpan`/`colSpan` field to keep in sync.
- **R10 — Printed labels stay a sibling map, not part of the grid.**
  `printedLabels: {key: text}` holds what's actually silkscreened —
  "RETURN", "OPTIO" — which can read differently by region even within one
  `aliases` family. Area names in `areas` stay the stable semantic key,
  always. Cosmetic hints like button `shape` (rect default, circle, rocker)
  live the same way — a sibling map, optional, never reaching the compiler.

  **A key name is a CSS identifier: `^[A-Za-z_][A-Za-z0-9_]*$`.** R11 leans
  on CSS grid to do the collision-checking, and that only holds while every
  area name is one — the pattern also excludes `.`, which means "gap" in
  `grid-template-areas`, and whitespace, which separates cells. Constraining
  the namespace beats escaping at render time: these names are ours to
  define, and a name that cannot be mistaken for grid syntax cannot break
  the grid.

  **Every key in a sibling map must resolve to a real key.** An orphan here
  is the quiet kind of error: a label for `KEY_OPTIN` renders nothing and
  reports nothing, and the whole premise of these maps is that they track
  the grid without being part of it.
- **R11 — CSS's own grid rules do the collision-checking for free.** A
  `grid-template-areas` string can't express two keys claiming one cell,
  and every named area must form a single rectangle — both are constraints
  of the format itself, not something the validator separately enforces.
  The validator's only remaining job is confirming every area name refers
  to a real key in `keys`.

**Known limit, named rather than glossed over:** CSS requires a named
area's cells to form one rectangle, so a single key can't have an L-shaped
or disconnected footprint, and two elements can never overlap (a button
nested inside a volume dial, which a few minimalist remotes actually do).
Every remote encountered so far decomposes cleanly into rows and columns
anyway — even a D-pad, which reads as a plus shape but is just five
ordinary cells with the corners left empty. A remote that genuinely doesn't
fit is out of scope for v1: it simply has no `layouts` entry yet, the same
honest fallback R7 already allows for a remote whose codes haven't been
captured either.

**Example — RMT-B118P's D-pad cluster, transcribed from its own lircd.conf
ASCII art:**

```json
"layouts": {
  "original": {
    "original": true,
    "label": "Factory face",
    "source": "ASCII diagram in RMT-B118P.lirc.conf (jose1711/lirc_remotes)",
    "areas": [
      ".        KEY_UP    .         .",
      "KEY_LEFT KEY_OK    KEY_RIGHT .",
      "KEY_BACK KEY_DOWN  .         KEY_OPTION"
    ],
    "printedLabels": { "KEY_BACK": "RETURN", "KEY_OPTION": "OPTIO" }
  }
}
```

## 7. Compiling & cross-validation

Multiple forms per key aren't just redundancy — they're a built-in
consistency check, if the tooling actually runs it.

- **R12 — Compile the trusted form to Pronto Hex, deterministically.** R6
  picks the form; the compiler renders it. Same JSON input always produces
  byte-identical Pronto Hex output. This compiled artifact is what playback
  apps actually load — never hand-edited, always regenerated.

  "Deterministically" is only a requirement if the rules are pinned, so the
  contract is normative and lives in DESIGN.md D6 (emission), D25 (parsing)
  and D28 (bounds and arithmetic). In outline:

  - Words are uppercase, four hex digits, single-space separated. The
    frequency word is `round_half_up(1000000 / (carrier_hz × 0.241246))`;
    each duration becomes `round_half_up(duration_us / period_us)` carrier
    cycles. That rule is a choice, and not every tool makes it:
    IrpTransmogrifier rounds against the nominal carrier, so the bytes
    differ in a few words while the timings agree (§12).
  - Rounding is `ROUND_HALF_UP` on `Decimal`, never Python's `round()`,
    which is banker's rounding and would make `x.5` cases depend on parity.
  - Durations are computed from **IRP units multiplied out exactly**, never
    from rounded nominal values: `16 × 564 = 9024 µs`, not "9 ms". That one
    choice is the difference between a lead-in word of `0157` and `0156`.
  - All arithmetic runs in a locally pinned `Decimal` context, because
    `decimal`'s is process-wide and writable by any library in the process.
  - No duration may round to **zero cycles**, and every emitted word must
    fit four hex digits. A vanished burst is a real failure, and emitting
    `0000` would hide it.
  - Parsing accepts only `0000` in word 0. `0100` (unmodulated) is deferred;
    `5000`/`5001` are references into a Pronto-internal protocol table, not
    waveforms. Carrier agreement is checked by comparing frequency *words*,
    never hertz — decoding `006D` yields 38 028.9 Hz, so comparing that to a
    declared `38000` would reject correctly generated output.
- **R13 — Cross-check every additional form in the same candidate group.**
  Render each stored form independently and diff it against the form R6
  selected. Comparison is scoped **within** a group and never across:
  different candidates are competing hypotheses and are *supposed* to
  disagree (R4). `derived` forms are excluded from this check entirely —
  they are the compiler's own prior output, and §7's string comparison is
  both stronger and the only check they face.

  Everything compares as integer carrier-cycle counts at the file's declared
  carrier, so an `irp`-versus-`pronto` check is exact by construction.
  Sequence lengths must match exactly: a differing burst *count* is a
  structural disagreement, never rounding. Bit and pulse pattern must then
  match exactly, except that a comparison involving a `raw` capture allows
  real instrument jitter, and the trailing gap-fill may drift on the order
  of a few cycles (under 150µs out of a ~13ms pad). A capture declared
  `truncated` has its terminal gap skipped rather than compared. Any
  tolerance loosened beyond the defaults requires a citation (R18). A real
  mismatch fails the build.

## 8. Automatic linking

"Automatic" means the index is computed from the files, never authored
separately.

- **R14 — The manufacturer+model index is a generated view.** Scan every
  file's `manufacturer`, `model`, `aliases`, and `controls` fields to build
  it. There's no second copy of "which remote goes with which device" to
  keep in sync by hand.
- **R15 — Validator: schema-valid, every key usable, no orphaned
  metadata.** Every file parses against the schema; every key resolves to
  at least one compilable form (R12); every `controls` entry is non-empty
  or explicitly marked unknown. Two files of the same manufacturer claiming
  the same model or alias is a conflict the validator surfaces, not
  silently allows. The check is scoped to a manufacturer (v0.9) because a
  model name is only unique within one maker's catalogue, as R1's
  `<manufacturer>/<model>` path already says: Apple's "CD" remote and
  Pioneer's are two remotes, not one name claimed twice.

## 9. Lookup experience

- **R16 — Minimum viable: a local lookup script.**
  `lookup "Sony BDP-BX510"` prints matching files, matching keys'
  confidence tiers, and citations. Works offline, no hosting, ships first.
- **R17 — Optional: a hosted, searchable index.** A generated static site
  with search by device or by remote model. Real added value, real added
  upkeep — gated on Open Decision 2.

## 10. Sourcing & citation

With no upstream dataset to inherit trust from, every form's citation (R5)
is the *only* place trust comes from — so it has to hold up on its own.

- **R18 — A citation must be independently checkable.** A URL to a forum
  post, a manual, or an official code table; or a same-repo cross-reference
  ("re-derived from `<file>`'s protocol, see commit `<sha>`"). Never just a
  tier with no trail.

  **This extends to any field that loosens a check**, not only to a form's
  evidence. `unitUs`, `defaultGapUs`, `tolerance` (R3) and a `raw` form's
  `truncated` (R4) each widen what passes, and each carries an entry in a
  `claims` sidecar giving both a *reason* — why this deviates, for whoever
  reads the diff — and a *source* that is independently checkable. A reason
  justifies; only a source lets someone else re-derive. Any field added
  later that widens what passes joins that set; the rule matters more than
  the list. Note what tooling can and cannot do here: that both are present
  and non-empty is checked mechanically, but whether the source says what
  the claim says is a human judgement, and stays one.
- **R19 — Sources inform entries; an import never launders their trust.**
  LIRC configs, IRDB rows, forum posts — any of them can source a form's
  data and citation, one key at a time. v1 forbade importing whole
  databases. As of v0.9 an import is permitted, on five conditions, each
  checkable:

  1. **The licence permits republishing.** This repository is public. The
     imported files carry their source's licence and attribution, and live
     under their own directory so the licence boundary is a path:
     `remotes/lirc/`, under GPL-2.0-or-later (Debian's reading of the LIRC
     remotes database, whose repository states none), crediting each file's
     contributor.
  2. **Every form cites exactly where it came from:** the upstream
     repository, pinned commit, file, remote block and line, and *how* the
     form was produced. It is either a `raw_codes` capture, a parametric
     block decoded to an `irp` form, or a parametric block expanded to raw
     timings by lircd's own transmit rules. R18 then holds per form, as for
     any authored entry.
  3. **Nothing is imported above Plausible.** The upstream's own claims
     carry over as text in the citation, never as a tier. A single capture
     that nothing cross-checks is Plausible by definition (§5). A key
     earns Verified the way any key does: by a second, independent source.
  4. **Authored data wins.** An import that collides with an authored
     remote, by manufacturer and model or by alias, is not written. To
     curate an imported remote, move it out of `remotes/lirc/`: from then
     on it is authored, and the import skips it.
  5. **The import is regenerable.** Re-running it over the same pinned
     upstream commit reproduces every imported file byte for byte. What it
     could not represent is listed in a committed report, with reasons, not
     dropped silently.

  What stays forbidden is the laundering this requirement always existed
  to stop: an upstream file landing here wearing its own confidence, or
  anyone's.

## 11. Non-functional

- **R20 — Partial coverage reads as partial, not absent.** A device with no
  entry yet must look different from one that was checked and found to
  have no known remote.

  The mechanism is `unresolved.json` at the repo root: a list of devices
  that were looked for and not found, each recording the date, the sources
  searched, and what stopped it. It sits outside `remotes/` on purpose, so
  `remotes/**/*.json` stays uniformly one-file-one-remote with no reserved
  names. The generated index folds it in, which gives a lookup **three**
  distinguishable answers rather than two:

  | State | Reads as |
  |---|---|
  | In the ledger | the remote, its candidates, their tiers and citations |
  | In `unresolved.json` | *checked on `<date>`, searched `<sources>`, blocked by `<reason>`* |
  | Neither | *not in the ledger* — nobody has looked |

  The middle row is the one R20 exists for. Without it a device someone
  spent an afternoon failing to find is indistinguishable from one nobody
  has ever typed in, and that afternoon gets repeated.
- **R21 — CI runs the validator and the cross-check on every change.** R11
  and R13, enforced automatically on every commit.

## 12. Resolved decisions

These six were open in v0.3. All are now closed; the reasoning is kept
because it is what makes each reversible.

| # | Decision | Resolution | Consequence |
|---|---|---|---|
| 1 | Personal tool, or open to contributions? | **Personal tool** for v1 | No PR/review workflow. R18's citations hold to the same standard regardless — they are what makes the data re-checkable later, by you. Reversible without a rewrite. |
| 2 | Git-only, or a hosted lookup surface? | **Both** — R16 *and* R17 | The site is a generated static page with no framework and no server, so upkeep stays near zero. If it ever grows a framework, revisit this rather than absorb the maintenance quietly. |
| 3 | Which form types are first-class? | `irp`, `raw`, `pronto` — **all three** | Most public captures are lircd-style microsecond timings; normalizing at authoring time would discard the evidence the citation points at. Costs `raw` its own comparison tolerance and a `truncated` flag. |
| 4 | Is the generated index committed? | **Committed** | Diffable, browsable without running anything. CI regenerates the whole tree and fails on drift *or* orphans, so it cannot go stale. |
| 5 | Does "Untested" belong in the format? | **Yes** | The BX510 fallback subdevices are useful precisely *as open questions*; without the tier they would have to masquerade as working codes. The tier alone is not enough to represent them — competing candidates need candidate groups, see R4. |
| 6 | Do custom layouts get committed? | **Schema-only** | The repo commits `original: true` layouts, an objective citable fact (R8). A personal rearrangement uses the identical shape but lives in an app's local storage. Follows from decision 1. |

**Implementation choices**, recorded here because they are load-bearing for
R12: Python 3.12+, with `jsonschema` as the only runtime dependency.
Protocol encoders are hand-written, with no external reference
implementation at runtime — which makes an **independently cited golden
vector per protocol** the only test layer that can catch a wrong constant,
and therefore a hard gate on adding one. See DESIGN.md §5 and D18.

A golden vector verifies **timings**, not rounding. Encoders disagree on
how to turn microseconds into Pronto cycles. IrpTransmogrifier rounds
against the nominal carrier, while R12's contract rounds against the period
the frequency word implies, which is closer to what a player transmits. So
a vector must be reproduced exactly by our timings under the tool's own
rule, and our bytes may differ from it only at declared words. All three
protocols meet this. NEC1's and Sony20's vectors are published; NECx2's is
generated by a pinned IrpTransmogrifier release, because no published one
was found.

---

**Build plan:** DESIGN.md §8 has the seven phases, and all seven are
implemented: v1 is complete. DESIGN.md §9 lists the edits each phase made to
this document, and DESIGN.md §12 records what is still unproven.
