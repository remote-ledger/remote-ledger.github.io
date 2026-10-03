# Scaling the index to the imported database: decisions D57 to D59

For the integrator, who folds this into DESIGN.md after the import's D46 to
D56 (`NOTES/import-design.md`). Nothing here edits DESIGN, SPEC or README. The
code is `src/remote_ledger/index.py` (`build_all`, `Shard`, `shard_files`,
`inputs_record`, `load_committed`), `generators.py` (`run_index`), `site.py`
(`shard_script`, the page's script) and `cli.py` (`cmd_lookup`). Numbers are
from the full import in a scratch copy of the tree (13,217 remotes), measured
with `gzip -9` where gzip is named; GitHub Pages' own compression level was
not measured. Decision numbers are provisional: renumber if D57 is taken.

## The problem, as measured before the change

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

## D57 -- The imported database is indexed in shard files; index.json keeps what it had

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
script copy of each part for the page (D59). Raw and gzipped, on the full import:

| | raw | gzipped |
|---|---|---|
| `index.json` | 1,251,172 | 86,311 |
| 28 parts, JSON | 13,826,204 | 1,698,104 |
| 28 parts, the page's scripts (compact JSON) | 10,015,255 | 1,625,528 |
| the manifest | 2,875 | 525 |

The shard is committed twice more than `index.json` is (JSON in `build/` and in
`site/`, scripts in `site/`): about 38 MB of text, 5 MB gzipped, against the
641 MB the import already adds.

## D58 -- rl lookup reads the committed index when it is the index of these files

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

## D59 -- The page embeds the core and loads the shard when the visitor searches

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

## The other changes, and one pre-existing bug

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

## What was verified, and what was not

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

## What the integrator has to do

- Fold D57 to D59 in. The edits to existing text: D13 (the index's shape gains
  `shards`, and the shard files), D15 (the page embeds the core index and loads
  the shard on search), D19's owner table (`index` owns `build/index.json` and
  `build/index/`), D20 (the site's shard copies are byte-identical to `build/`'s),
  D40's list (index and site shard the imports, not only per remote), SPEC R16 (the
  lookup reads the committed index when it is the index of the files) and R20's
  third state (it holds across the shard, and a failed or pending load is not that
  state); and the number of tests in section 12.
- This branch's `build/` and `site/` were regenerated for the core corpus, in the
  last commit, so `rl build --check` is clean here and `test_site.py`'s island test
  passes (the committed page predated `paths.IMPORTS`'s third entry). After the
  import lands, run `rl build` again and commit `build/`, `site/` and
  `remotes/irblaster/`. Expect conflicts with the branches that touch `site.py`,
  `cli.py` and `generators.py` and in every generated file; regenerate them. Two of
  them are in `cmd_build` and the per-remote work, which this change does not touch.
- If the other branch changes the form of `controls` to `BRAND | MODEL` (the
  imported entries then carry 2 more characters per pair, about 0.6 MB more raw in
  the shard, nothing in `index.json`), nothing here depends on it: a part holds
  whatever the entry holds.
- Nothing in `remotes/` or the schema changed.
