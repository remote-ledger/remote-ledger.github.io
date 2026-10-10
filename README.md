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
codes came from (DESIGN.md §17); and hifi-remote.com's Sony code pages, 151
files and 6,772 keys under `remotes/hifi-remote/`, which are a table of
reference codes and so carry no licence (DESIGN.md §27); and the JP1 device
upgrades of hifi-remote.com's forum, 2,566 files and 99,929 keys under
`remotes/jp1/`, likewise (DESIGN.md §28); and the manufacturers' own IR code
tables, Marantz's, Anthem's and Oppo's, 189 files and 12,844 keys under
`remotes/official/`, likewise (DESIGN.md §29, §31). That makes 16,124 remote files with
the five authored ones. Data whose licence is unclear is not here: it is kept in
a private repository in the same layout, which the tools read through
`--extra-root` (DESIGN.md §30). The IR Blaster database stores each code as a hexcode, and for
ten of its protocols the ledger reads the code as the real remotes send it,
which is not what SwiftRemote itself transmits for 44,789 of its keys
(§18). Every imported key is Plausible and cites where it came from, down to
the upstream file and line, or the profile, or the remote id (SPEC R19).
Authored remotes are the five above, each checked against independent
sources, except the Topping RC-16A, which has one.

**More sources are coming.** Coverage grows from any database whose
licence permits republishing it. Each one gets its own `remotes/<source>/`
directory and must meet R19's five conditions. LIRC, SmartIR, the IR
Blaster database, hifi-remote.com's Sony pages and its forum's JP1 upgrades
are the first five.

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

What a key *means* is kept apart from how a source spells it. A versioned
**canonical key vocabulary** (about 150 keys in a dozen groups, each with a
display name, a standard Material Symbols icon, a glyph, a colour and whether
holding it repeats it) and a table of aliases live in
`src/remote_ledger/vocabulary/`;
`keys.canonical_id(key_name, label)` maps `KEY_VOLUMEUP`, `VOL+` and `Vol +` to
`VOLUME_UP`, or to nothing when it is not sure, so an app can show a generated
layout with standard icons for any remote. `rl keys report` says how much of
the corpus it maps, per remote (DESIGN.md §22).

**The catalog bundle.** `rl bundle` turns the ledger into one prebuilt SQLite
file an app can ship as an asset and open directly: brands (with the other
names a person types for them), models, remotes, each remote's keys by
canonical key, and the compiled signals once each as binary Pronto words. It needs nothing newer than Android 11's SQLite, is
byte-for-byte the same for the same tree, comes with a manifest, a detached
ECDSA P-256 signature and a notices file with every source's licence text, and
has two profiles: `full` (51 MB, 12.9 MB gzipped) and `selected`, which since D117 carries every brand too (51 MB, no limit).
It is a build artifact: never committed, never under `build/` or `site/`
(DESIGN.md §23).

**Finding a device.** `matching.py` matches a typed query, or what a photo's
text says, to the catalog (brand, model, part number; typo-tolerant, in
integers so a port gets the same order) and `rl bundle search-eval` measures
how often it offers the right remote, on generated queries and on hand-written
ones. The generated queries are friendlier than real typing and the report
says so (DESIGN.md §24). It also completes what a person has typed so far
(`MatchIndex.suggest`: the brands and the models that go on from it, in a
stable order) and knows other names for a brand, written out in each script, so
that 海信, 创维 and 創維 find Hisense and Skyworth and their models as the Latin
names do (`bundle/data/brand_aliases.json`, a reviewed list of 133 brands;
DESIGN.md §25, D108). `suggest(query, limit, prefer=["Sony", ...])` takes the brands a
person already uses and puts them first only where the rules cannot tell two
entries apart (the hint never lifts a worse match: D106), and
`MatchIndex.warm()` reads the biggest brands' model lists once at start and keeps
them, so that the first keystroke inside a big brand is as quick as the next
(D107).

**One remote for a device the import split.** The IR Blaster import files a
remote per protocol, and a fragment with no key a person could be asked to try
(Power, Volume up, Mute) is a row nobody can test. The exporter folds such a
fragment into a sibling of the same device that has one and plays the same
way, and never folds two fragments that both can be tried; the remote files do
not change, and `remote_refs` still maps every ref of the ledger to the remote
that carries its keys (315 of the 1,225 fragments of split devices in the full
bundle; DESIGN.md §26).

## Try it

```console
$ pip install -e ".[dev]"
$ rl encode --protocol NEC1 --device 0x88 --subdevice 0x77 \
      --function 0x18 --carrier 38000
0000 006D 0022 0002 0157 00AC 0015 0015 0015 0015 ...

$ pytest
$ rl lookup "DX3 Pro"      # offline lookup, R20's three states
$ rl keys report           # how much of the corpus the key vocabulary maps, per source and remote
$ rl bundle --profile selected --out bundle-out/selected   # the app's catalog file, manifest and notices
$ rl bundle --verify bundle-out/selected                     # check it against the tree
$ rl bundle search-eval --bundle bundle-out/selected             # how often a typed device finds its remote
$ rl bundle suggest-vectors --file tests/vectors/suggest_vectors.json --check   # the vectors a port of suggest is held to, with and without a hint
$ rl build --check         # the CI gate: drift and orphans (-j N or RL_JOBS sets the workers)
$ rl build && open site/index.html
```

## Read next

**[SPEC.md](SPEC.md)** — the requirements: problem statement, prior art (IRP
notation, Pronto Hex, LIRC, IRDB, SmartIR, the IR Blaster database), the data
model, layout, compiling and cross-validation, and the resolved decisions.

**[DESIGN.md](DESIGN.md)** — how it is built: decisions D1 to D108, the
Pronto contract to the byte, the seven-phase plan, the three imports, the key
vocabulary, the catalog bundle, and what is not yet proven.
