# Remote Ledger

A database of IR remote configurations, organized so you can look up a
*device* — a Sony BDP-BX510, a Samsung UN50NU6900F — and get back the
remote codes that control it, along with **why each one is trustworthy**
and not just a guess someone copied from a forum.

**Status:** design phase. The schema below is a proposal (`SPEC.md`); no
tooling exists yet. See §12 of the spec for what's still an open decision.

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
          "device": "0x11", "subdevice": "0xEE", "function": "0x18",
          "confidence": "verified",
          "verifiedBy": "nec-complement-check",
          "source": "audiosciencereview.com/.../10708 (user halfSpinDoctor)"
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

## Read next

**[SPEC.md](SPEC.md)** — the full requirements spec: problem statement,
prior art (IRP notation, Pronto Hex, LIRC, IRDB, SmartIR), the data model,
layout, compiling and cross-validation, and the open decisions still on the
table.
