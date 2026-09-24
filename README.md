# Remote Ledger

A database of IR remote configurations, organized so you can look up a
*device* — a Sony BDP-BX510, a Samsung UN50NU6900F — and get back the
remote codes that control it, along with **why each one is trustworthy**
and not just a guess someone copied from a forum.

**Status:** v1 is built — all seven phases of [DESIGN.md](DESIGN.md) §8.
Schema, compiler, cross-check, layouts, a generated index, an offline
lookup and a static site, with CI gating the whole generated tree for drift
*and* orphans.

Three protocols (NEC1, NECx2, Sony20) and **all three seed remotes
authored** — the Sony with 27 of 38 functions cross-checked across two
independent sources, zero mismatches. Every protocol's timings are
verified against another encoder's Pronto output (NEC1 and Sony20 against
published vectors, NECx2 against a pinned tool release). The bytes differ
in a few words, by design, because the tools round microseconds to cycles
differently. DESIGN.md §12 has the detail, and §13 records a contradiction
the research turned up in the spec's own opening example.

## Why

Existing remote-code databases (LIRC's `lircd.conf` collection, IRDB,
SmartIR) each organize by a different axis — remote model, protocol
address, or device model — and none of them keep track of *how sure* you
should be that a given code actually works, or coexist with the codes it's
still being checked against. Remote Ledger tries to hold all three: which
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
$ rl build --check         # the CI gate: drift and orphans
$ rl build && open site/index.html
```

## Read next

**[SPEC.md](SPEC.md)** — the requirements: problem statement, prior art (IRP
notation, Pronto Hex, LIRC, IRDB, SmartIR), the data model, layout,
compiling and cross-validation, and the resolved decisions.

**[DESIGN.md](DESIGN.md)** — how it is built: 33 numbered decisions, the
Pronto contract to the byte, the seven-phase plan, and what is not yet
proven.
