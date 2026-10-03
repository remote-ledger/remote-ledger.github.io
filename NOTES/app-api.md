# The app API v1: decisions D74 to D82

For the integrator. DESIGN.md, SPEC.md and README.md are not edited on this
branch; section "For the integrator" at the end lists what they should say. The
contract is the brief's `api-spec.md`, plus the playback note of the app-side
analysis (`play`); where the code differs from either, it is said below, under
the decision that made the difference.

What was built: a fifth generated stage, `app`, that writes `site/app/v1/` from
the committed `remotes/irblaster/` tree. The SwiftRemote app is to read it online
instead of bundling its 51 MB sqlite. It is published because it sits in `site/`,
the only directory GitHub Pages serves. `index.json` is untouched: `build/index.json`
and `site/index.json` are byte for byte what they were (sha256 `614e2918...6526de`
before and after), and so is everything else of `build/` and `site/` that existed.

## D74 -- The API is a stage of its own, `app`, which owns `site/app/v1`

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

## D75 -- A pure function of the tree, and the importer owns the formats it reads

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

## D76 -- The files, and where they differ from the spec

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

## D77 -- `appReadingDiffers` is computed

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

## D78 -- How one press is played: `play`

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

## D79 -- `power.json`

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

## D80 -- Checked against the database it replaces

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

## D81 -- Parallel, like the other stages

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

## D82 -- Sizes, requests, and what is the app's to decide

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

## For the integrator

- **The documented test count.** `tests/test_review_regressions.py::
  test_documented_test_count_is_current` fails on this branch: DESIGN.md section 12
  says 2,419 tests and the suite collects 2,557 (`pytest --collect-only -q`; 2,548 pass, 8 skip). It is
  the only failure; everything else passes (8 skipped as before, the data being present).
- **DESIGN.md.** A new section (this document's D74 to D82), and: section 2's picture
  and section 6's repo layout and CLI get `rl app`, `site/app/v1/` and `NOTES`; D19's
  owner table gets `site/app/v1` (app) and `Generator.excludes`; D69's "`diff_tree`
  takes the stages to compare" gains the exclusion; section 17 or 18 should say
  `parse_citation`, `format_citation`, `split_controls_entry` and `app_reading_differs`
  are the importer's exports.
- **SPEC.md.** R17 (the site) should say `site/app/v1/` is generated there too; R19.2's
  citation shape is now read back by one parser.
- **README.md.** The command table and the layout (`rl app`; `site/app/v1`; the
  verification tool).
- **CI and Pages** need no change: `rl build --check` already covers the stage (about
  10 s of the 89), and `pages.yml` uploads `site`.
- **`tools/app_api_vs_sql.py`** needs the SQL dump and is not run by CI.

## Not proven

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
