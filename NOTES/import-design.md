# Importing the SwiftRemote code database: decisions D46 to D56 (with D56a and D56b)

For the integrator, who folds this into DESIGN.md as a new section after
section 17 (the registry additions), numbered to continue from D45. Nothing
here edits DESIGN, SPEC, README, `build/`, `site/` or the generated part of
`remotes/`. Paths are relative to the repository unless they start with
`/home/shanjian/srcs/SwiftRemote`. The importer is
`src/remote_ledger/irblaster/importer.py`, the command `rl import irblaster
<checkout>`, the end-to-end oracle `tools/irblaster_oracle_import.py`.

The source is `assets/db_src/swiftremote.sql` at SwiftRemote
`6aafd15e1c95cf494ac729339b9a4701a4ab8f0a`: four tables, `brands`,
`remotes(id)`, `models(brand, model, id)` and `keys(id, label, hexcode,
protocol)`. 9,388 remote ids, 413,331 keys, 23 protocol names, 58,766 distinct
`(protocol, hexcode)` codes.

**D46 -- The licence boundary is a directory, and R19.1 is met by inheritance
only.** Serves R19.1. Everything imported lives under `remotes/irblaster/`,
registered in `paths.py` `IMPORTS` (name "IR Blaster database (as shipped in
SwiftRemote)", licence `GPL-3.0-only`, readme `remotes/irblaster/README.md`).
`LICENSE` is SwiftRemote's own, byte for byte. `README.md` says, without
softening it, what the licence rests on: SwiftRemote is GPL-3.0 and so is this
repository, so the data is republished under the licence it arrived under. That
is not a grant by the data's authors and not a reading of one (LIRC's is a
reading, D34), because nobody in the chain says where the data came from.
Lineage as far as it is written down: SwiftRemote forked IR Blaster
(`github.com/iodn/android-ir-blaster`, GPL-3.0, KaijinLab Inc.), itself a fork
of `github.com/TalkingPanda0/osram-remote`; no project in it names the data's
source, and IR Blaster's own audit (`report-source.md`) lists `REC80`,
`RCC2026` and `RCC0082` as having no public definition. The README closes with
the same exit as LIRC's: deleting the directory removes every imported file.
This is the one import whose condition 1 is weaker than the others', and the
owner should know it when they read R19.1.

**D47 -- One ledger remote per (database id, ledger protocol).** Serves R3,
D23. A file holds one protocol; 581 ids use several database protocols, and one
database protocol (`REC80`) lands on six ledger protocols. So an id becomes
`remotes/irblaster/<slug(manufacturer)>/<id>-<slug(ledger protocol)>.json`
(`importer.py` `target_path`, `Importer.import_id`). A database protocol that
maps to a ledger protocol gets one file per ledger protocol it reaches, and a
(id, protocol) whose keys are all unrepresentable gets none. On the real
database: 10,013 files from 9,388 ids; 573 ids are split over several files, 27
ids write none (every key refused, all listed). `slug` is the other importers'
(`[^A-Za-z0-9._+-]` becomes `_`), applied to the directory by `dir_slug`, which
also refuses `.` and `..` and turns a leading or trailing dot into `_` (13
brands end in a dot, `C.P.`, `T.V.E.`; a directory name ending in a dot cannot
be checked out on Windows, and a brand named `..` must never reach a path). No
brand in this database is a Windows reserved name or collides with another once
casefolded (checked).

**D48 -- Names, and why R2 cannot be honoured.** Serves R1, R2, R10. The database
has no remote model: an id is a bag of keys and the list of products it is
filed under, as with SmartIR (D43). So:
- **`manufacturer`** is the brand with the most `models` rows for the id; a tie
  goes to the casefolded alphabetical first, then to the exact string, so the
  rule is total (`pick_manufacturer`). The database's casing is kept. 3,934
  ids have several brands and 976 have a tie.
- **`model`** is `IR Blaster DB <id> (<ledger protocol>)`, always with the
  protocol, so the two files of one id never share a name (R15). R2's "`model`
  identifies the remote" is not met and cannot be: nothing in the data does.
- **`controls`** is every `models` row of the id, sorted and de-duplicated
  (`importer.py` `Importer.import_id`). First written as `<BRAND> <MODEL>`, where
  two rows could spell the same string (280,956 rows gave 280,954 distinct);
  **since D56b it is `<BRAND> | <MODEL>`**, which cannot collide, so all 280,956
  rows are distinct entries. Ids carry up to 6,268 (id 286); 43 files have more
  than 1,000. A split id repeats its list in each file: 306,631 entries in the
  first import, 306,633 with D56b. The schema puts no bound on `controls`.
- **`aliases`** is `[]`.

**D49 -- Key names are the label folded mechanically, and every member of an
ambiguous name carries its code.** Serves R10, D29. `key_base`: ASCII
upper-case, then `??` becomes `UNLABELED`, `+` `PLUS`, `-` `MINUS`, `/` `SLASH`,
`*` `STAR`, `#` `HASH` (each as a separate word), every other run of non
`[A-Z0-9]` becomes one `_`, trimmed, prefixed `KEY_`. Three consequences to
know. A `-` inside a word is `MINUS` too (`A-B` is `KEY_A_MINUS_B`), where
LIRC's D37 learned to treat it as a separator; the decision was made, and it is
spelled out so nobody reads `MINUS` as a claim. Only ASCII letters are
upper-cased: `str.upper` also changes some non-ASCII letters (`ß` becomes `SS`)
and its tables move between Unicode versions, and a regenerated import must not
depend on the interpreter. And a label with nothing alphanumeric in it
(`►`, `⏩`, `?`: 6,942 keys) is `KEY_`, valid under the schema's `keyName`
pattern.

The labels are not unique in an id (16,367 repeats; `??` on 9,853 imported
keys), so `key_names` does what D21 does for ids and avoids position: when a
folded name carries more than one distinct `(label, hexcode)` in the file,
**every** member gets `_<HEXCODE>`, so a name does not depend on the order the
rows arrive in. A collision left over (two labels that fold alike on one code)
gets `_2`, `_3` in sorted order of `(label, DB protocol)`. A name that
the rule makes can collide with another key's plain name (`KEY_OK_A_2` against
`OK A 2`); the loop numbers again until the name is free. On the real database
360,384 keys have the plain name, 50,505 carry a code, 376 are numbered. Keys
are written in name order. Members are the keys *written* to the file, not the
rows of the id: a refused key does not make its neighbour's name ambiguous, and
the cost is that a hex map that later accepts a code can rename a key. The
original label, with its spacing, is in the citation (D51) and, since D56a, in the
key's `label`. An exact duplicate
row is dropped and counted (the real schema's primary key makes it
impossible, the code does not rely on that).

**D50 -- The form is one `irp` form per key, read the way the data means it.**
Serves R19.3. `confidence: plausible`, `id: primary.irp` (`rl fmt` writes the
auto id, so leaving it out would not survive `rl fmt --check`), ledger protocol
and `device`/`subdevice`/`function` from `FROM_DB_HEX[<DB protocol>]`, with
`subdevice` left out when the protocol has none. **The hexcode is read as the
wire reading**: the database stores every code in wire order, and for Sony,
Pioneer, JVC, Sharp, Denon, Thomson7, Proton and RCC2026 SwiftRemote's own
encoder reads it differently (NOTES/japan.md, philips.md; the sony, misc and
unknown findings are in the integrator's scratch notes). The ledger holds what
the data means, per independent evidence (published decodes of real remotes,
real LIRC frames, structure). No second form is added for the app's reading,
and no non-primary candidate either: the raw pattern would fail D8 (NOTES/japan.md,
"For the importer"), and a candidate group per reading would put 44,789 keys'
worth of a bug into the data. The disagreement is measured and reported
instead (D55).

**D51 -- The citation is short and fixed.** Serves R19.2, R18. The shape, 94.9
characters on average (median 95, longest 134, because of long labels), is
```
irblaster-db@6aafd15 remote 286, 'VOL+' 20DF40BF NEC: 32 wire bits, bytes bit-reversed as NEC1
```
The prefix is the pinned SwiftRemote commit's first seven characters, the
remote id, the label as the database spells it, the hexcode and the database
protocol. `<how>` is a fixed phrase **per database protocol**, with the ledger
protocol appended (`importer.py` `HOW`; REC80's six ledger protocols need the
suffix to be told apart). The brief's example, `wire-order hex read as Sony12
D=1 F=21`, carries the parameters; the form already does, 413,331 times, so
the phrase does not, and a reader checks the claim by applying the phrase to
the hexcode. No claim about trust is made beyond `plausible`, and none about
where the code came from, because that is not known (D46).

**D52 -- The protocol block.** Serves R3, D3, D24, D27.
- `name` is the ledger protocol, `carrierHz` the registry's
  `nominal_carrier_hz`, so no `carrier-off-nominal` warning appears (D32). The
  app's own carriers are within 5 % of every one (the oracle tools check it).
  One consequence worth seeing: NEC1's 38,400 is 1.0 % from the 38,000 the app
  sends for a database NEC code (NOTES/nec.md), and the LIRC import and the
  authored Topping use 38,000; the files differ in carrier word (`006C` against
  `006D`) from those.
- `minSends` is `hex_*.MIN_SENDS`, default 1: Sony 3, NECx2 2, Thomson7 2,
  Aiwa (RCC2026) 2. Sharp and Denon stay at 1 although the app sends three
  frames per press (their intro plus one pass of the repeat, NOTES/japan.md);
  the oracle tool plays their whole signal for that reason and says so.
- **Samsung36 alone** gets `unitUs: 500` and `claims.unitUs`. The IRP says 560
  us; the app, IrpTransmogrifier's eight real captures (median unit about 496
  us) and IRremoteESP8266's `sendSamsung36` (512 us marks, 490/1468 us spaces)
  agree on about 500 (NOTES/misc.md 3.2). The claim's source cites the capture
  audit and the IRremoteESP8266 line range. At 500 us the compiled signal equals
  the app's to the microsecond but for the lead-out, which no field changes.
  `rl build --check` passes on it.
- **Raised, not decided** (the brief asked for that where another protocol
  shows the same pattern: the IRP's unit disagrees with the app and with real
  captures): `Blaupunkt` (IRP 512 us, app 528, `Blaupunkt.ict` about 532; the
  app's sync gap is 9.1 % short of the IRP's and nearer the capture) and
  `Thomson7` (IRP 500/2000/4500, app 460/2000/4600, capture marks 0.961x and
  spaces 1.020x of the IRP). Both agree with the app against the IRP by a few
  percent, and neither has a second source like IRremoteESP8266's. `RCA-38` is a
  different case (IRP and app say 460 us, only the capture says 500) and
  `Pioneer-2Part` is mixed (app marks 500 us, IRP 564, IRremoteESP8266 568, the
  capture 548). No override is written for any of them.

**D53 -- The hex maps have one contract.** Serves D50. Every
`irblaster/hex_*.py` exports `FROM_DB_HEX` (DB protocol name -> function(hexcode)
-> `(ledger protocol, device, subdevice, function)`, the wire reading, what the
importer writes), `FROM_DB_HEX_APP` (the same keys and signature, what
SwiftRemote does today, used only by the oracle tools and by the report in
D55) and `MIN_SENDS`. Where the app and the data agree the APP entry **is** the
same function object, so the set of protocols on which they differ is a
property a test can state (`tests/test_irblaster_hex_contract.py`): the ten
Sony12, Sony15, Sony20, Pioneer, JVC, Sharp, Denon, Thomson7, Proton, RCC2026.
The old names are gone: `hex_japan` had them the other way round
(`FROM_DB_HEX_WIRE`), `hex_misc` kept Proton's wire reading outside the table
(`proton_wire_order`), `hex_unknown` called its table `FROM_DB_HEX_SWIFTREMOTE`.
The per-family oracle tools keep proving the encoders against what the app
transmits, now through the APP tables, and their docstrings say so
(`irblaster_oracle_japan.py --reading app`, the default, is the proof;
`--reading wire` is the importer's reading and cannot fail).

**D54 -- What cannot be represented is skipped and listed, never dropped.**
Serves R19.5. A hex map that raises `ValueError` (its text has no hexcode in
it, so the report groups on it), a DB protocol with no map (none today: all 23
have one) and a form that does not compile to Pronto
(`import_common.form_compiles`, memoised per distinct signal, since 413,331
keys are 58,766 distinct codes and the answer is a function of the protocol,
carrier, unit and parameters) are skipped key by key. An id with no
representable key writes no file and is listed as a skipped remote; so is an id
with no `models` row (no manufacturer to file it under; none today). An authored
collision (`import_common.authored_names`) skips the file and counts its keys
(none today, the synthetic model names make it unlikely, as D45 said of
SmartIR). On the real database: **2,066 keys, 964 distinct codes skipped**, all
for reasons the family notes already give: NEC 406 codes / 1,076 keys and NEC2
229 / 434 (byte 4 is not the complement of byte 3, and the only registry
protocol that could hold it, the `-f16` form, is not registered), RCC2026 21 / 75
(not an Aiwa frame), REC80 308 / 481 (a Kaseikyo-family layout the registry does
not hold). Every reason is a row of `IMPORT.md`, every key one row. The brief
expected about 2,500; the notes' counts add to 2,066 exactly.

**D55 -- The report says where SwiftRemote's reading differs.** Serves R19.5,
D50. `IMPORT.md` ends with a section, per DB protocol, of imported distinct
codes, codes whose `(ledger protocol, D, S, F)` under `FROM_DB_HEX_APP` differs
from the wire reading (or which the app's reading cannot send as a frame at
all), imported keys, keys affected, and the first three codes in hexcode order
with both readings. A code the wire reading refuses is not in it: it is in the
skipped list. **Measured: 9,140 distinct codes, 44,789 imported keys**
(Sony12 892, Sony15 733, Sony20 1,213, Pioneer 1,667, JVC 1,020, Sharp 586,
Denon 332, Thomson7 29, Proton 1,458, RCC2026 1,210 codes). The brief expected
about 12,800; the eight families' distinct codes add to 9,399 in all, so 12,800
cannot be reached from this database, and the figure the tool recomputes
independently is 9,140. The full list is not committed (it would double
`IMPORT.md`); `tools/irblaster_oracle_import.py --list-differences` prints it
from the dump for whoever fixes the app.

**D56 -- Authored data wins; the import is a regenerable cache.** Serves R19.4,
R19.5. As D39/D45: `rl import irblaster <checkout> [--commit SHA]` rewrites
`remotes/irblaster/` wholesale, `*.json` and `IMPORT.md` are the importer's,
`README.md` and `LICENSE` are authored and never touched, files are written as
they are produced (peak 260 MB, not the whole tree), stale files are removed
afterwards, and every file passes through `fmt.format_document` so `rl fmt
--check` stays clean. The input is the SQL executed into an in-memory sqlite
(what `tools/build_ir_db.py` does to a file), and the pinned commit is the
checkout's `git rev-parse HEAD`. One addition over LIRC and SmartIR: `cmd_import`
refuses a checkout whose `assets/db_src/swiftremote.sql` differs from the
commit (`INPUT` on the importer module), because an importer that reads one
named file would otherwise cite a tree the data did not come from. Output is a
pure function of the dump, the commit and the code: every ordering is sorted,
the key names do not depend on row order, and a test shuffles the dump four
ways and compares the bytes.

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

## Measurements: the full run, in a scratch copy

Nothing below is in the worktree. The real database was imported into a copy
of `remotes/`, `src/`, `tools/`, `build/`, `site/` and `pyproject.toml`
(`scratchpad/fullrun/`), then built. Commit 0d093c2 of this branch, SwiftRemote
`6aafd15`.

| | Before (LIRC + SmartIR + authored) | After |
|---|---|---|
| remote files | 3,204 | 13,217 (+10,013) |
| keys | 113,819 | 525,084 (+411,265) |
| `remotes/` | 79.8 MB | 240.9 MB; `remotes/irblaster/` 161.2 MB (9.0 MB gzipped tar) |
| `build/pronto/` | 76.3 MB | 336.5 MB; `irblaster/` 260.2 MB, 10,013 files (10.1 MB gzipped) |
| `site/r/` | 65.2 MB | 285.2 MB; `irblaster/` 219.9 MB, 10,013 scripts (9.1 MB gzipped) |
| `build/index.json` | 1,251,008 B | **15,075,980 B** (12.0x) |
| `site/index.json` | 1,251,008 B | 15,075,980 B (a copy, D20) |
| `site/index.html` | 1,006,251 B | **11,537,234 B** (11.5x) |
| `build/warnings.json` | 129 warnings | 129 warnings |

Largest single files: 223 KB in `remotes/irblaster/`, 204 KB in
`build/pronto/irblaster/`, 180 KB in `site/r/irblaster/`; nothing is near a
host's per-file limit. The 129 warnings are all `carrier-off-nominal`, all
there before, none from this import: every file carries the registry's own
carrier (D52), so the import adds no warning of any kind.

Times, one core, on this machine (64 cores, 125 GB):

| | wall | peak RSS |
|---|---|---|
| `rl import irblaster` (from an empty directory, and again over its own output) | 64.8 s, 68.9 s | 260 MB |
| `rl validate remotes/irblaster` | 2 min 9 s | 32 MB |
| `rl build` (validate, check, compile, index, site) | **18 min 2 s** | 736 MB |
| `rl build --check` | 17 min 44 s, 0 differences against the tree `rl build` wrote | 732 MB |
| `tools/irblaster_oracle_import.py` (16 worker processes; 6 min 13 s of CPU) | 30 s | |
| the whole test suite, in the scratch copy with the data and the fixes below | 3 min 7 s (1 min 37 s without the data) | 957 MB |

D40 recorded 3.5 minutes for the LIRC-era tree (113,819 keys; the machine is not
recorded). Scaled by keys that is 16 minutes for 525,084, so 18 is about linear.
Re-running the import over its own output gives byte-identical files (`diff -r`,
and the report's sha256 matches), and so does importing into an empty tree.

**What does not scale**, in the order it will be felt:

1. **The index and the site page.** `build/index.json` grows 12x, to 15 MB, and
   `site/index.html` embeds it (11.5 MB) and parses it at load; D40's promise that
   "nothing committed grows with the whole corpus" holds for the per-remote files
   and no longer for the index. 63 % of it is `controls`: 306,631 strings (a split
   id repeats its list), 7.2 MB of the 11.5 MB the index serialises to. Nothing
   in the index needs more than a prefix of them to search by, and the per-remote
   artifact already has them. I did not change `index.py` or `site.py`. I could
   not measure the page in a browser here, so how long it takes to open is not
   known.
2. **`rl lookup`.** It calls `index.build_index(root)`, which loads every remote
   file; the query `BDP-S360` takes 2.5 s on the committed tree and 12 s with the
   import, and a query that matches many remotes (`TELEFUNKEN`) 17 s.
3. **`rl build` and `rl build --check`**: 18 minutes each, CI's `--check` doing
   the whole thing again (D11, D19). `validate` is 2 minutes of that for the
   import alone.
4. **The repository.** +641 MB in the working tree (161 + 260 + 220), about 28 MB
   compressed per generation of the data, and a regeneration that changes a hex
   map rewrites a large share of three trees.
5. **`tests/test_seed_data.py` treated every directory but `remotes/lirc/` as
   authored**, so the 10,013 imported files would have become 20,000 parametrized
   tests (22,282 collected, an hour). Fixed here: it now scans the IR Blaster tree
   as it scans LIRC's (`test_the_irblaster_import_keeps_r19`, skipped until the
   tree exists) and samples every 200th file for the reproducibility test.
   SmartIR's 62 files are still tested as authored, as before.
6. Directory names: brands with non-ASCII names slug to underscores
   (`_________` holds one remote, a nine-letter Cyrillic brand); 223 of 1,755
   directories contain an underscore. As with LIRC's `slug`, nothing is
   transliterated.

## The end-to-end oracle

`tools/irblaster_oracle_import.py` over the scratch output, with the app's
signals for all 58,766 distinct codes (`oracle/by_protocol/*.jsonl`): **0
unexplained**, no problem of any kind (the tool also re-derives every file's
manufacturer, model, controls and path from the dump's `models` table, and
checks `IMPORT.md`'s totals, skipped list and difference table against the
files). Distinct codes, then keys, per DB protocol:

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
- **matched** is the family tools' rule (carrier within 5 %, same number of
  durations, each within 12 % or 150 us), applied to the key compiled *through
  its file* and decoded from Pronto, with only these allowances, each counted
  under its own note and none folded in: NEC's frame without a lead-out
  (273,006 keys); a toggle bit a file cannot carry, D3b (RC5 34,813, RC6 6,633,
  RECS80 5,165, RECS80_L 526: **every key** of these four compiles `T=0` and
  matches only at `T=1`, which is what the app's preview shows); RC6's longer
  final space (6,633); the lead-out of Samsung36 (1,488) and of the REC80
  vendors Fujitsu, Teac-K and SharpDVD (1,061); idle gaps of JVC, Pioneer,
  Sharp and Denon, only where both signals have a gap; Sharp's and Denon's three
  frames; Blaupunkt's closing sync. Nothing else differs by more than the
  tolerance for any of the 366,476 keys.
- **differs by reading** is the 9,140 codes (44,789 keys) on which `FROM_DB_HEX_APP`
  gives other fields from `FROM_DB_HEX` or cannot send the code. The tool
  recomputes that set from the two tables and requires it to equal `IMPORT.md`'s
  counts (it does, per protocol), and it requires, for every such key, that the
  app's signal is what the app's own reading compiles to (the encoding, within
  the tolerance, and to the microsecond before Pronto's rounding for 21,013 keys:
  Proton, Sony12, Sony20, most of Sony15 and 586 of RCC2026's), or, for the 1,890 Sony15 keys whose
  codes the app masks to 15 bits, what the masked code compiles to, or, for the
  4,219 RCC2026 keys the app reads as no Aiwa frame, what a port of its stale
  encoder produces, exactly. That confirms the classification. It is not a claim
  that either reading is right (D50).
- **unrepresentable** is a key `IMPORT.md` lists, whose code the wire reading
  refuses, and which no file holds: 964 codes, the 2,066 keys of D54.
- Thirteen codes on which the two readings happen to agree match (Pioneer 6,
  JVC 3, Sharp 4): the japan note's "13 Pioneer, JVC and Sharp codes".

Per family, the counts agree with the family notes' own: NEC 33,116 / 4,855 /
1,439 / 1,238 codes matched, 635 unrepresentable; REC80 2,171 + 244 lead-out
codes matched, 308 unrepresentable; Pioneer 1,667, JVC 1,020, Sharp 586, Denon 332
codes differ (japan.md); Sony 2,838 of 2,858 differ.

## After D56a and D56b: the re-run

In a fresh scratch copy (`scratchpad/fullrun2/`: `src/`, `tools/`,
`pyproject.toml` and `remotes/` as committed, without the data), the same
command as before, `rl import irblaster /home/shanjian/srcs/SwiftRemote --commit
6aafd15`. Nothing below is in the worktree.

- **The import**: 10,013 remotes, 411,265 keys (as before), 65.3 s, peak 260 MB.
  `IMPORT.md` is byte-identical to the first import's. Compared with the first
  import's files, parsed, **every one of the 10,013 differs in exactly two ways
  and no other**: each key has a `label` first in its object, and `controls` is the
  pipe form (joining each entry's two parts with a space and de-duplicating gives
  the old list). The two files whose old list had collapsed two rows into one
  string now carry both: `ATLAS/7767-NEC1.json` and `BRAIN_WAVE/4736-RC5.json`
  (306,633 entries against 306,631). 411,265 of 411,265 keys have a label; 9,853
  are `??` (the notes' D49 count), 3,880 have a lower-case letter.
- **Sizes** (bytes; MB is 1e6, as above):

| | first import | with labels and pipes |
|---|---|---|
| `remotes/irblaster/` (10,016 files) | 161,188,850 | **171,427,573** (+10,238,723, +6.35 %) |
| the same, as a `.tar.gz` (gzip -6) | 9,010,442 | 9,854,277 (+9.4 %) |
| `remotes/` (13,226 files) | 240,946,000 | 251,184,723 |
| `build/pronto/irblaster/` | 260.2 MB | +9,624,014 B, to about 269.8 MB (computed) |
| `site/r/irblaster/` | 219.9 MB | +8,806,926 B, to about 228.7 MB (computed) |
| `build/index.json`, its copy, the page's island | 15,075,980 B | +613,332 B, +4.1 % (computed) |

  The last three rows were not built: another worktree is making `rl build`
  fast, and the full build is 18 minutes. They are arithmetic on the files'
  labels and controls, with the label's cost per key checked against a real
  build of a 299-file sample (below), where it predicted the growth of
  `build/pronto/irblaster/` (292,722 B) and `site/r/irblaster/` (266,265 B)
  exactly. In an artifact a label costs 17 bytes of JSON around its quoted
  text; in a script, the quoted text and a key name, once per remote that has
  labels.
- **The oracle** (`tools/irblaster_oracle_import.py`, 16 workers, 30.9 s): **0
  unexplained, 0 problems**, the same totals and per-protocol table as above
  (58,766 codes; 366,476 matched, 44,789 differ by reading, 2,066 unrepresentable;
  413,331 keys). It now also requires every key's label to equal the label of the
  row its citation names, and that row to be a row of the dump; `controls`
  re-derived as `<BRAND> | <MODEL>` and split back into the id's `models` rows;
  and it reports a pipe in any brand or model of the dump. A negative control on
  the real tree (one label with a letter added) is reported as `label '0x' is
  not the database's '0'`.
- **Reproducible**: the tree's manifest hash (sha256 of every file's sha256,
  sorted) is the same after a first import, after re-importing over its own
  output, and after importing into an empty tree; `diff -rq` of the first and
  the last is empty.
- `rl fmt --check remotes/irblaster`: 10,013 files, 0 would change (12.8 s).
  `rl validate remotes/irblaster`: 10,013 files, 0 errors (2 min 6 s).
- **A build of a sample** (299 irblaster files, every 34th plus the ones holding
  the Sony `KD - 49 X 8088`, a `??` key, a lower-case label and the tab label,
  with the authored Topping, 25 s): all 12,520 keys carry their label in their
  `build/pronto/` artifact and in their `site/r/` script's `labels`; Topping's
  files carry none and `build/index.json` has no `label`. The page's own script,
  run under node on the real script of the Sony file, renders `<h3>KEY_0 <span
  class="klabel">0</span></h3>`, and `'1 \t\t\t'` intact for the tab label.
  `rl lookup "sony kd 49x8088"` finds the Sony remote through `SONY | KD - 49 X
  8088` and prints each key as `KEY_0  "0"`.

**Nothing that existed changes** (the check the brief asked for). A copy of the
committed tree (`remotes/`, `build/`, `site/`, `unresolved.json`: 3,204 remotes)
built with the code before these changes and with the code after them, each with
`rl build` into its own directory, gives `diff -r` equal for `build/` and
`site/` (6,412 files), 3 min 30 s each. But `rl build --check` against the
*committed* tree reports **one difference, with the old code as with the new**:
`site/index.html`, whose island lacks the `remotes/irblaster/` entry of
`paths.IMPORTS` (added in 0d093c2 without regenerating the page; with the island
cut out the committed page and the regenerated one are identical). That is the
"second expected failure" of the previous section, not a consequence of the label.
Re-running `rl build --check` over the tree the new code regenerated: 0
differences. A labelled page differs from an unlabelled one by 244 bytes of
script and style, and only when a label exists (D56a).

## What is not proven

- **No hardware.** Every claim is a comparison of waveforms with the app's own,
  or with published decodes of real remotes. Nothing here says a device obeys a
  compiled key, and for the eight protocols whose reading differs from the app's
  (Sony, Pioneer, JVC, Sharp, Denon, Thomson7, Proton, RCC2026) the evidence is
  the families' (published decodes, LIRC frames, structure), not a receiver.
- **The provenance of the data.** Nobody says where it came from (D46). `plausible`
  is the tier for one source nothing cross-checked, and here the source is itself
  unattributed.
- **`matched` means the same signal as the app's, within 12 %.** Where the app and
  the IRP differ in a unit (D52) the ledger follows the IRP, except Samsung36.
- **Sharp's complement-frame inversion and Denon's `11` flag** (NOTES/japan.md) are
  still the weakest parts of those two readings.
- **How the site page behaves in a browser** with the 11.5 MB island (above).

## Two tests of the existing suite that the data breaks

With both fixed, the suite in the scratch copy passes but for three tests:
`test_documented_test_count_is_current` (expected), and
`test_setuptools_scratch_under_build_is_ignored` and
`test_gitignore_negations_mirror_the_generator_owner_table`, which need a git
repository and a `.gitignore` that the scratch copy does not have. Whether the
repository's `.gitignore` needs a line for `remotes/irblaster/` was not checked.

Found by running the whole suite in the scratch copy (`tests/`, `DESIGN.md`,
`SPEC.md` copied beside the generated tree); both are fixed on this branch.
1. `tests/test_seed_data.py` (above): without the fix, 22,282 tests and an
   hour.
2. `tests/test_site.py::test_no_external_resources_are_loaded` asserted `"cdn"`
   is not in the whole of `site/index.html`, data included. The import's controls
   hold `ORION G 20 LCDN` and fourteen more, so the test failed, and pytest then
   spent half an hour building a text diff of an 11.5 MB string for the failure
   message (`difflib`; found with `faulthandler`). It now checks the page with the
   data island cut out.

## What the integrator has to do

- **After D56a and D56b the first import's files are stale**: run `rl import
  irblaster /home/shanjian/srcs/SwiftRemote --commit 6aafd15` again (the result
  of the re-run above), then `rl build`. The label code in `site/index.html`
  appears with the first labelled remote, so the regenerated page differs from
  today's by it as well as by the `imports` entry (D56a).
- D56a and D56b are for DESIGN.md too: the schema's `key.label`, the compiled
  artifact's `keys.<key>.label` and the site script's `labels` are new public
  shapes (SPEC section 5's key object, D20's artifact, D40's script), and the
  `controls` format is this tree's convention. The suite grew by 66 tests:
  2,245 passed before, 2,311 after (9 skipped both times), the same two
  expected failures in both (`test_documented_test_count_is_current`,
  `test_the_island_parses_and_carries_the_ledger`).
- `tests/page_driver.py` is a new helper: it needs `node` and skips without it.
- Run `rl import irblaster /home/shanjian/srcs/SwiftRemote --commit 6aafd15`, then
  `rl build`, and commit `remotes/irblaster/`, `build/` and `site/`. Until then
  `tests/test_site.py::test_the_island_parses_and_carries_the_ledger` fails, as
  the committed `site/index.html` embeds `paths.IMPORTS` and was built before the
  entry was added: it is the second expected failure, beside
  `test_documented_test_count_is_current`.
- Fold D46 to D56 in; update the test count; D18's table and the registry's
  docstring already name the protocols (the other agents' notes list what in
  DESIGN section 12 and D3b changes).
- Decide about the index (above) before the data lands in `main`: 15 MB in
  `build/index.json`, a copy in `site/`, and 11.5 MB in the page, rewritten by every
  regeneration.
- The notes for the sony, misc, nec and unknown families (cited above as
  NOTES/sony.md, misc.md, nec.md, unknown.md) are in the shared scratchpad
  (`.../scratchpad/notes/`) and are not yet in this directory.

