# Remote Ledger — Requirements Spec

**Draft v0.3** · Status: proposal · Depends on nothing upstream (self-contained)

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

Three real lookups hit that pattern, each resolving to several candidates,
each trusted for a different reason:

| Device | Candidate | Confidence | Why |
|---|---|---|---|
| Sony BDP-BX510 | `sony/RMT-B118P.json` | **Verified** | Every derived function value cross-checked against hifi-remote.com's official Sony BD command table — zero mismatches. |
| Sony BDP-BX510 | alternate subdevice 234 | Untested | Same buttons, offered as a fallback, not yet confirmed on real hardware. |
| Sony BDP-BX510 | alternate subdevice 242 | Untested | Same buttons, a second fallback address. |
| Topping RC-15A | `topping/RC-15A.json` | **Verified** | NEC address/command complement check (byte1==~byte0) passed on all 8 captured codes. |
| Samsung UN50NU6900F | `samsung/BN59-01199F.json` | Plausible | No independent capture of this exact remote; inherited from a sibling remote's shared universal address. |

One device, several candidates, different reasons to trust each. The format
has to keep the device, the candidates, *and* why each one is believed to
work — not collapse them into a single answer the moment someone copies out
"the" code.

## 2. Prior art

Nothing here is governed by a standards body — it's a stack of community
formats that each grew to own one layer of the problem.

| Layer | Format | Notes |
|---|---|---|
| Protocol | **IRP notation** | A small formal grammar for describing a protocol — Sony12, Sony20, NEC1 — as parameters, timing, and checksum in one line. Maintained on the JP1/hifi-remote wiki; `IrpTransmogrifier` is the actively-developed reference implementation. Closest thing to an actual standard in this space. |
| Waveform | **Pronto Hex** | Philips' proprietary format for a Pronto remote's learned/generated codes, adopted everywhere as the raw interchange format because it needs no protocol decoder — just carrier frequency and burst-pair timings. The compiled output this project produces. |
| Remote file | **LIRC's `lircd.conf`** | The de facto standard remote-configuration file, and the shape of most public captures in the wild. A common authoring source, not a dependency. |
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
  built one lookup at a time.

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
- Bulk-importing any existing database wholesale (see §10) — entries are
  authored and cited one at a time.

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
- **R4 — Each key holds a `forms` array, not a single code.** A parametric
  form (device/function values against the remote's declared protocol), a
  raw capture, and a compiled Pronto Hex string can all coexist for one
  button. None of them is required to agree until the compiler checks (§7).
- **R5 — Confidence and citation attach to the form, not the file.** One
  remote's Power button can be Verified while its Home button is Untested.
  Every form names *how* it was established: a URL, "re-derived from
  `<file>`'s protocol, see commit `<sha>`," "confirmed on hardware,
  2026-09-14."
- **R6 — A stated precedence rule resolves conflicting forms.** Confidence
  first — confirmed > verified > plausible > untested. Among equal tiers,
  form type breaks the tie: `irp` > `raw` > `pronto`, since a parametric
  form is what you'd want to re-render from anyway. The compiler follows
  this rule; it's never a coin flip.

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
          "device": "0x11", "subdevice": "0xEE", "function": "0x18",
          "confidence": "verified",
          "verifiedBy": "nec-complement-check",
          "source": "audiosciencereview.com/.../10708 (user halfSpinDoctor)"
        },
        {
          "type": "pronto",
          "hex": "0000 006D 0022 0000 0156 00AB ...",
          "confidence": "derived",
          "derivedFrom": "irp"
        }
      ]
    }
  }
}
```

**Confidence tiers:**

| Tier | Means | Example |
|---|---|---|
| Verified | Cross-checked against an independent authoritative source. | RMT-B118P vs. Sony's official function-code table; Topping's NEC complement check. |
| Confirmed | A person tested it against real hardware and it worked. | *(none yet)* |
| Plausible | Retailer/aftermarket "compatible with" claims only. | BN59-01199F, inherited from a sibling remote's address. |
| Untested | A reasoned candidate, offered but not yet confirmed either way. | The BX510 mode2/mode3 subdevice guesses. |
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
- **R13 — Cross-check every additional form the key holds.** Render each
  stored form independently and diff them. Bit/pulse pattern must match
  exactly; only the trailing gap-fill may drift (harmless capture rounding,
  on the order of a few cycles / under 150µs out of a ~13ms pad). A real
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
  or explicitly marked unknown. Two files claiming the same alias is a
  conflict the validator surfaces, not silently allows.

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
- **R19 — Sources inform entries; they don't get bulk-imported.** LIRC
  configs, IRDB rows, forum posts — any of them can source a form's data
  and citation, one key at a time. No wholesale copy of another project's
  files ever lands in this repo wearing someone else's confidence tier.

## 11. Non-functional

- **R20 — Partial coverage reads as partial, not absent.** A device with no
  entry yet must look different from one that was checked and found to
  have no known remote.
- **R21 — CI runs the validator and the cross-check on every change.** R11
  and R13, enforced automatically on every commit.

## 12. Open decisions

Everything above holds regardless of how these land — but each one
reshapes the build materially enough that it's worth answering before
writing code.

1. **Personal tool, or open to contributions?** Decides whether R18's
   citations need to survive a stranger's review, and whether there's a
   PR/issue workflow at all.
2. **Git-only, or a hosted lookup surface?** R16 (a script) is nearly free.
   R17 (a searchable site) is a real, ongoing commitment.
3. **Which form types are first-class in v1?** `irp` and `pronto` cover
   R12–R13 on their own. Is a `raw` (lircd-style microsecond timing) form
   also first-class, or normalized into `irp`/`pronto` at authoring time
   and not stored separately?
4. **Is the generated index (R14) checked into git, or computed on
   demand?** A committed index is diffable and works with a plain file
   browser; computing it on demand means one less generated artifact to
   keep in sync.
5. **Does "Untested" belong in the format at all?** The BX510's
   alternate-subdevice guesses are genuinely useful *as an open question
   for someone to resolve* — but only if a form can say "unconfirmed" out
   loud instead of every stored form implying it works.
6. **Do custom layouts get committed to this repo, or just supported by
   the schema?** The `original` layout (R8) is an objective, citable fact
   worth sharing like any other entry. A person's own rearrangement is a
   preference, not a fact about the remote — it may belong in an app's
   local storage instead, using the same shape without ever reaching this
   repo.

---

**Smallest useful first slice:** R1, R4–R6, R12–R13 don't need layouts, a
hosted index, or a resolved Open Decision 1 to be worth building. The
schema, a compiler that renders and cross-checks one remote's keys, and the
three devices above re-authored in this format as seed data is enough to
prove the shape before deciding how far to take it.
