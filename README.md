# Remote Ledger

A database of IR remote configurations, organized so you can look up a
*device* — a Sony BDP-BX510, a Samsung UN50NU6900F — and get back the
remote codes that control it, along with **why each one is trustworthy**
and not just a guess someone copied from a forum.

**Status:** v1 is built — all seven phases of [DESIGN.md](DESIGN.md) §8.
Schema, compiler, cross-check, layouts, a generated index, an offline
lookup and a static site (which also serves the SwiftRemote app's API under
`site/app/v1/`), with CI gating the whole generated tree for drift *and*
orphans.

28 protocols (NEC1, NECx2, RC5 and Sony20, then 24 more added to read the
IR Blaster database, DESIGN.md §18) and **all three seed remotes
authored** — the Sony with all 38 functions cross-checked across independent
sources, zero mismatches. Every protocol's timings are
verified against another encoder's Pronto output: six against published
vectors, and 22 against a pinned IrpTransmogrifier release because no
published vector exists. For those 22 that checks our encoder against the same
IRP the tool holds, not the IRP itself, and three (RECS80-0068, JVC-48,
SharpDVD) have no capture behind the IRP either (DESIGN.md D68). The bytes
differ in a few words, by design, because the tools round microseconds to
cycles differently. DESIGN.md §12 has the detail, and §13 records a
contradiction the research turned up in the spec's own opening example.

A fourth remote, the Meridian MSR, was curated out of the LIRC import
because the import's frames lacked RC-5's first start bit; §16 records what
was wrong with it, what replaced it, and what is still unknown.

**Imported:** the LIRC remotes database, 3,138 remotes and 112,789 keys
under `remotes/lirc/`, republished under GPL-2.0-or-later; SmartIR's
`media_player`/`fan` codes, 62 remotes and 914 keys under
`remotes/smartir/`, republished under MIT (DESIGN.md §15 — `climate` and
`light` are state matrices, not buttons, and stay out of scope for now);
and the IR Blaster code database as shipped in SwiftRemote, 10,013 remote
files and 411,265 keys under `remotes/irblaster/`, republished under GPL-3.0
by inheritance and nothing more, since nobody in its lineage says where the
codes came from (DESIGN.md §17). That makes 13,217 remote files with the four
authored ones. The IR Blaster database stores each code as a hexcode, and for
ten of its protocols the ledger reads the code as the real remotes send it,
which is not what SwiftRemote itself transmits for 44,789 of its keys
(§18). Every imported key is Plausible and cites where it came from, down to
the upstream file and line, or the profile, or the remote id (SPEC R19).
Authored remotes stay the four above, each checked against independent
sources.

## Why

Existing remote-code databases (LIRC's `lircd.conf` collection, IRDB,
SmartIR, the IR Blaster database) each organize by a different axis —
remote model, protocol address, or device model — and none of them keep
track of *how sure* you should be that a given code actually works, or
coexist with the codes it's still being checked against. Remote Ledger tries to hold all three: which
remote, which device, and how confident.

## Shape

One remote = one self-contained JSON file. A button (`key`) can hold
several independently-sourced representations at once, each with its own
confidence tier and citation:

```json
{
  "manufacturer": "Topping",
  "model": "RC-15A",
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
        }
      ]
    }
  }
}
```

A compiler renders whichever form is most trusted down to Pronto Hex for
actual playback, and cross-checks it against any other form the same key
holds — turning what would otherwise be manual verification into something
enforced on every change.

Physical layout (including a factory arrangement transcribed from a real
remote, or a person's own rearrangement) is described separately from the
code, using CSS's own `grid-template-areas` syntax rather than a bespoke
coordinate system.

## Try it

```console
$ pip install -e ".[dev]"
$ rl encode --protocol NEC1 --device 0x88 --subdevice 0x77 \
      --function 0x18 --carrier 38000
0000 006D 0022 0002 0157 00AC 0015 0015 0015 0015 ...

$ pytest
$ rl lookup "DX3 Pro"      # offline lookup, R20's three states
$ rl build --check         # the CI gate: drift and orphans (-j N or RL_JOBS sets the workers)
$ rl build && open site/index.html
```

## Read next

**[SPEC.md](SPEC.md)** — the requirements: problem statement, prior art (IRP
notation, Pronto Hex, LIRC, IRDB, SmartIR, the IR Blaster database), the data
model, layout, compiling and cross-validation, and the resolved decisions.

**[DESIGN.md](DESIGN.md)** — how it is built: decisions D1 to D73, the
Pronto contract to the byte, the seven-phase plan, the three imports, and what
is not yet proven.
