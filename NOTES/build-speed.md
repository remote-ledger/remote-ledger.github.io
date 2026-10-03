# Build speed: 17 minutes to about 80 seconds, with the same bytes

For the integrator. Nothing here edits DESIGN.md, SPEC.md or README.md; what
belongs in them is listed at the end. Decisions are not numbered: the integrator
numbers them. Paths are relative to the repository. The code is
`src/remote_ledger/parallel.py` (new), and small edits to `numeric.py`,
`pronto.py`, `signal.py`, `validate.py`, `cli.py`, `generators.py`, `index.py`
and `site.py`; the tests are `tests/test_build_speed.py`.

## The problem

After the SwiftRemote import the corpus is 13,217 remotes and 525,084 keys, and
`rl build --check`, which CI runs on every push, took 17 minutes on this machine
(`rl build` 17, `rl validate remotes/irblaster` 2, the suite with the data 3).
The importer measured 18 minutes; the numbers below are this branch's own
measurements of the code as it was (`df9ce03`), one core each.

## Where the time went

cProfile over a 500-remote sample (379 imported from the IR Blaster database, 121 others),
before any change, 93 s under the profiler:

- **69 % in `Remote.compile_group`**, 88 % of that in `pronto.quantize`. Every
  duration of every signal went through `us_to_cycles`, a Decimal division
  wrapped in *two nested* `decimal_context()` entries, each of which constructed a
  fresh `decimal.Context` and, as a generator-based context manager, a
  generator. A full build enters that context some 8 million times per 500
  remotes. And `compile_group` runs three times per key (`check_distinctness`,
  the compile stage, the site stage), plus once more in `check_key` for the
  comparison. Also in the same loop, `check_bounds` built its error message (an
  f-string) for every duration whether or not the bound was broken, and so did
  `IrSignal._check_sequence`.
- **20 % in jsonschema** (`Draft202012Validator.iter_errors`), of which a fifth
  was resolving `$ref`: a registry lookup and a new validator object per hop.
- The rest (loading, `load_forms`, serialisation) was a tenth. The loading is
  done about seven times per remote (validate twice, check, compile, index, the
  site twice), which is wasteful and is not worth removing: it is 4 s of 93.

On the full set, one core, with the changes of sections 1 and 2 below and
before any parallelism: `validate` 131 s, `check` 40 s, `compile` 43 s, `index` 12 s,
`site` 47 s (build_index again, then the scripts), `diff_tree` 4 s: 276 s in
all. Validation is 47 % of that, and about four fifths of it is jsonschema.

## What was changed, and why none of it is observable

**1. The arithmetic (commit "Stop building a Decimal context...").**
`numeric.decimal_context` is now a small class around one pinned `Context`
built at import (`decimal.localcontext` copies it on entry, as it already
copied the Context it was handed before; the template is never made current, so
its flags stay clear). `pronto.quantize` keeps, per frequency word, a table of
the cycle counts `us_to_cycles` has returned (the same function, so the same
values; bounded at 50,000 entries per word, dropped when exceeded, because a
raw capture may hold any duration); the bounds checks and
`IrSignal._check_sequence` take a fast path for the passing case and build their
message only when the check fails. The test compares `quantize` with
`us_to_cycles` called directly on 8 carriers and ~900 random durations, cold and
warm, with the table limit forced to 5, and checks that a duration that rounds
away is still refused when it comes from the table. Sample: 39.7 s to 12.1 s,
zero differences.

**2. The schema (commit "Recognise a valid document with the schema's
references inlined").** `validate.schema_problems` first asks a copy of the
schema with every `#/$defs/<name>` reference inlined (the schema has no
recursion; one `$ref` has a `description` sibling, which is an annotation) whether
the document is valid. If it is, nothing more is done. If it is not, the document
is validated *again by the schema as written* and that validator's errors are
what is reported, so the text of every message and the order they are sorted into
(the sort key is `str(error)`, which includes the schema the error came from,
and the inlined schemas differ) are exactly what they were. Inlining can make a
valid file cheaper and cannot change a word said about an invalid one. The only
way it could weaken a check is to accept a document the schema rejects; the test
compares the two on 3,900 mutants of two documents (every value replaced by 18
edge values, every member deleted, every object given an unknown member, every
member renamed to an illegal name, then 400 random combinations), and I
verified the test has teeth by breaking the inliner ten ways (a citation that
accepts anything, `hexOrInt` without its maximum, `additionalProperties`
relaxed on `key` and on `irpForm`, the `derived` tier dropped, the key-name
pattern dropped, ...): each is caught. That is a strong test, not a proof. The
validator is 30 % faster on a valid document (5.7 s to 4.0 s on 500 files).

**3. Worker processes (commits "Run the per-remote loops...", tests).**
Each stage is a loop over the remotes whose iterations are independent, so
`parallel.ordered_map(func, items)` runs `func` over the items in forked
workers and yields the results in the order of the *input*. validate
(`validate.validate_files`, used by `rl validate` and the first step of
`rl build`), `rl check` and the build's check stage, the compile stage, the
index (`index.build_index`, which is also what `rl lookup` calls) and the site
stage all use it. Decisions:

- **Order is the input's.** A bounded window of chunks (3 per worker) is in
  flight; the parent takes them from the front. The ERROR lines, the warnings,
  the index and every file are therefore the same whatever the scheduler does.
  An exception raised for an item is raised when that item's turn comes, so the
  first failing item in input order is the one reported, as in the serial loop.
- **Workers write their own files** in the compile and site stages (each remote
  is its own artifact and its own script), so nothing large travels back and
  the site stage no longer holds every remote's keys at once (that map is, I
  believe, most of the old 736 MB peak; I did not measure it alone). `site.payload` still returns the whole mapping (tests and
  any caller use it); its loop body is unchanged and is now
  `site.site_extra(root, files)`, which `build_site` calls one remote at a time
  from the workers.
- **Worker count**: `--jobs N` / `-j N` on any subcommand (it sits in the
  shared parent parser like `--strict`), else `RL_JOBS`, else
  `min(usable CPUs, 8)`. "Usable" is `len(os.sched_getaffinity(0))` where it
  exists, not `os.cpu_count()`: the brief's `min(os.cpu_count(), 8)` would start
  8 workers under `taskset -c 0-3` and in a container with a CPU mask, which is
  the situation CI is in (GitHub's runner is 4 vCPUs; `cpu_count()` there says 4,
  so the two agree on the runner and differ only under a mask). `--jobs 1` and
  `RL_JOBS=1` run the old in-process loop, with no pool created. A bad value is
  a `ValidationError` (`ERROR $RL_JOBS is 'x'; ...`, exit 1), or argparse's
  usage error for the flag.
- **An automatic count leaves a small input serial** (under 64 items): a pool
  costs more than a handful of remotes. An explicit count is always honoured,
  which is what lets the test compare serial and parallel runs on a corpus of
  22 files.
- **One pool per loop, not one for the process.** Workers are forked when the
  loop starts, so they see the current directory and module state of that
  moment (`Remote.where` is relative to the working directory; a pool kept from
  an earlier call, in a process that has since changed directory or patched a
  module, as the tests do, would be stale) and nothing outlives the call. It
  costs a few milliseconds a stage, and the workers' tables start empty each
  time, which costs less than a second a stage. `fork` is requested explicitly
  where it exists (the default start method becomes `forkserver` in Python 3.14);
  elsewhere the platform default is used and everything sent to a worker is a
  module-level function.
- A worker never starts a pool of its own; `main()` clears the count in a
  `finally`, so one call's `--jobs` cannot leak into the next call in the same
  process; the standard library flushes stdout before forking, so buffered
  output is not written again by the workers (a test asserts it).

## What was not touched, on purpose

`compiled_artifact` (cli.py), the per-key entry in `site.site_extra`, `summarise`
(index.py) and `load_remote` are untouched, because the label change is
being made there. `diff_tree`, `Generator`, the registry, the order of the
stages, `--check`'s refusal on a dirty owned path (D11) and the exit codes are
untouched. The two loops I edited in index.py and site.py are the places a merge
with the index-shard change will meet mine: `build_index`'s loop became
`for summary, problem in ordered_map(partial(_summary_of, root), corpus_files(root))`
(the body is `_summary_of`, unchanged), and `site.payload` is now two lines
that call `site_extra` (the old body, with `for path in files:`). If the shard
split writes more than one file from the index, its writers can use
`ordered_map` the same way, with the worker writing the file.

## Proof that the output did not change

Every comparison below is of bytes (`diff -rq`, `cmp`, or `filecmp` with
`shallow=False` inside `rl build --check` itself).

- **Full tree, 13,217 remotes, 26,438 generated files.** `rl build --check`
  with the new code against the `build/` and `site/` the importer generated
  with the old code: 0 differences, at `--jobs 1`, 4 and 8 (and by default on 4
  and on 56 cores). `rl build` (write
  mode, `build/` and `site/` deleted first) at the default worker count and with
  4: `diff -rq` against what the old code's `rl build` wrote (which I
  regenerated myself, and which is itself identical to the importer's tree): no
  difference.
- **Committed master tree, 3,204 remotes.** `rl build --check` with the new
  code reports **one** difference, `site/index.html`, exactly as the old code
  does on that tree: `paths.IMPORTS` gained the IR Blaster entry on the
  integration branch, the page embeds it, and master's committed page does not
  have it yet. The new code's `rl build` writes the same `site/index.html` as the
  old code (identical to the byte), and after committing that one file
  `rl build --check` reports 0 differences, in a real git repository, so
  D11's dirty-path guard ran too. Everything else in master's `build/` and
  `site/` is byte-identical to what the new code generates.
- **Failures and drift are reported the same.** Four damaged copies of the full
  tree, each run with the old code and with the new at two to eight workers,
  stdout, stderr and exit status compared byte for byte (all the same): (A) five
  invalid files spread across the sorted corpus (a missing `protocol`, a
  duplicate form id, an odd-length raw sequence, a file that is not JSON, an
  empty `manufacturer`), `rl validate` and `rl build`; (B) derived forms gone
  stale in three files and a raw form disagreeing with its irp form, `rl check`
  (4 errors and the 129 `carrier-off-nominal` warnings) and `rl build --check`
  (one of the three stale files fails *validation*, so the build stops there);
  (B2) the same without that file, so that the build's check stage is the one
  reporting (3 errors); (C) a clean corpus and a tampered generated tree: an
  artifact changed, one deleted, an orphan directory under `build/pronto/`, an
  orphan script, a changed script, a changed `build/index.json` and
  `build/warnings.json` -- seven differences, the same seven, in the same order.
  So `--check` detects every drift and orphan it detected, and `validate` runs
  every rule on every file.
- **Small corpora in the suite** (`tests/test_build_speed.py`): `build` writes
  the same tree at 1, 2, 3 and 8 workers; `build --check` agrees with the tree
  either way and reports drift, orphans and a missing file identically;
  `validate`, `check` and `build` give identical output on a corpus with broken
  files; `ordered_map` keeps input order when later items finish first and raises
  the first failure in input order.

## Timings

This machine: 64 cores, 125 GB, shared with other agents (load average 4-6 while
these ran), Python 3.12.3, jsonschema 4.26.0. One run each unless a range is
given; the timing runs are not repeated enough for more than a rough figure; "4 cores" is `taskset -c 0-3` with the worker count left at its default,
which the affinity makes 4 (`--jobs 4` forced gave the same: 99.8 s against
102.9 s in the first pair, which is the machine's noise, see below). "64 cores"
is the whole machine, default, which is 8 workers. "1 core" is `--jobs 1` on one
core. Before is the code at `df9ce03`, one core.

| | before | 1 core | 4 cores | 64 cores (8 workers) |
|---|---|---|---|---|
| `rl validate remotes/irblaster` (10,013 files) | 123.5 s | 79.6 s | 28.0 s | 10.2 s |
| `rl build --check` (13,217 remotes) | 1,025.9 s | 280.0 s | 77 to 103 s | 43.8 s |
| `rl build` (13,217 remotes) | 1,036.7 s | 270.9 s | 75.2 s | 38.4 s |
| `rl validate` (committed master tree, 3,204) | 62.7 s | 52.0 s | 14.8 s | |
| `rl build --check` (committed master tree) | 216.1 s | 82.3 s | 23.6 s | 13.5 s |
| `rl build` (committed master tree) | 213.6 s | | 22.6 s | |
| the suite with the data, full tree | 179.5 s | 138.4 s | 96.7 to 108.1 s | 88.5 s |
| the suite, no data (this worktree's own tree) | 87.0 s | | | 48.8 s |
| `rl lookup BDP-S360` (full tree) | 14.1 s | | 4.4 s | |

The 4-core `--check` varied between 77 s (twice, back to back) and 103 s
(when other jobs were running on the machine); I would quote the upper figure.
Total CPU time at 4 cores is about 275 s of user time, against 1,019 s before,
so the single-core gain is about 3.7x and the four-core scaling about 3.5x of
that. Peak memory: before, 739 MB in one process. After, no process exceeds
165 MB (GNU time's maximum; sampling gave 141 to 153 MB), and together the
processes hold 216 MB at 4 workers and 230 to 244 MB at 8 (proportional set
size, which counts each shared page once; the sum of their resident sizes, which
counts it once per process, is 417 MB and 743 MB). The suite's own peak (960 MB) is the
test process, which the speed-ups did not change.

The three failures of the suite in the scratch copy with the data (the
documented test count; two tests that need a git repository and a `.gitignore`
the scratch copy lacks) are the same three as before; in this worktree two fail,
as they do at `df9ce03`: the documented count, and
`test_the_island_parses_and_carries_the_ledger`, which reads the committed
`site/index.html` whose imports map predates the IR Blaster entry.

## What could not be sped up

- **jsonschema itself.** After the inlining it is still about 40 % of the
  serial work (validate is 131 s of 276 on the full tree, about four fifths of
  it the schema), because it descends into 1,800 subschemas per remote at 4
  microseconds a descent in pure Python. The remedies I did not take, in the
  order of what they would save: a hand-written accept-only validator for the
  schema's own keywords (I estimate two thirds of the schema's time, not
  measured, and it is a second implementation
  of the schema that has to be kept equal to it: the differential test used here
  would be its guard, but I did not think it right to put an unrequested second
  validator in the path of `rl validate` without being asked); skipping
  `check_cache_content` for a file with no variants (provably the same, 5 % of
  validation, but it edits semantic code for a small gain); a per-process memo of
  the key specs seen (the `source` text is unique per key, so nothing repeats).
- **What is serial.** `diff_tree` (4 s: it walks and compares ~26,000 files),
  building and writing the 15 MB index and the 11.5 MB page (a few seconds), the
  second `build_index` that the site stage runs (3.7 s of CPU at 4 workers: it
  could reuse the index stage's result, but the stages are independent callables
  and sharing a result across them means state that outlives a call, which I
  preferred not to add), and the process start of each stage.
- **The suite.** With the data it is 96 to 108 s at 4 cores. The remainder is
  thousands of small tests and a few tests that load every remote one at a time
  from the test process (`test_a_resolved_device_leaves_unresolved_json`, 8 s,
  loads all 13,217 with `load_remote`). Those loops are the tests' own and I did
  not change them; they could use `ordered_map` too.

## Beyond the brief

- The worker count follows `os.sched_getaffinity`, not `os.cpu_count()` (above).
- One pool per loop rather than a persistent one (above).
- `rl lookup` is faster as a side effect (14.1 s to 4.4 s at 4 cores) because
  `build_index` is parallel; it is not a stage and I did not otherwise touch it.
- The inlined-schema accept path: the brief's list had "compile the schema
  validator once", which was already done (`lru_cache`); the cost was in
  jsonschema's per-hop work, not in building the validator.
- `rl check` (the per-file command) is parallel too, since it is the same loop.
  `rl compile`, `rl fmt` and `rl import` are not.
- `main()` clears the worker count when it returns.
- 25 tests were added (`tests/test_build_speed.py`). The test count that
  DESIGN.md section 12 asserts (`test_documented_test_count_is_current`) needs
  the integrator's recount; it fails at `df9ce03` already.

## Not proven, or not measured

- **CI timings were not measured**; there was no CI to run. A 4-vCPU GitHub
  runner is a slower core than this machine's, and may be shared; the figure
  to expect is this table's 4-core column scaled by that, not the column itself.
  The 4-core numbers are the 64-core machine constrained with `taskset`, with
  other jobs running beside them. The suite and `rl build --check` run in
  sequence in CI, so a CI job is their sum.
- The inlined validator is tested, not proven, to accept only what the schema
  as written accepts (3,900 mutants, ten injected faults all caught). If a
  future schema uses a reference the inliner leaves alone, jsonschema still
  resolves it, so the result stays right and only slower; a test fails if one
  appears, so that the loss is noticed.
- `fork` was the only start method exercised. Under `spawn` (Windows) the code
  is written to work (module-level functions, picklable arguments) but was not
  run.
- Python 3.12.3 and jsonschema 4.26.0 only; CI installs the newest jsonschema
  that satisfies `>=4.20`. A different jsonschema may change the time, not the
  result: whatever the inlined validator accepts is the schema's own verdict
  anyway, and what it does not accept goes to the schema as written.
- Output equality was shown on this corpus and on five damaged copies of it; it
  rests on the loops being independent per remote, which holds for every stage
  as the code is today. A stage that began to carry state from one remote to the
  next would need to stay serial, and `ordered_map` would not notice.

## For DESIGN.md and README.md (the docs agent)

- A short section on the build driver: the five stages run their per-remote
  loops in worker processes, ordered by input; `--jobs`/`-j`, `RL_JOBS`; default
  `min(usable CPUs, 8)`; `1` forces the serial path for debugging; output does
  not depend on the count (R12), and a test says so.
- The measured times (the table above, 13,217 remotes): `rl build --check` and
  `rl build` about 1.5 minutes on four cores; the suite with the data about
  1.5 to 2 minutes. D40's "3.5 minutes" and the importer note's "18 minutes"
  are out of date.
- `README.md` may mention `RL_JOBS` and `--jobs`.
- The section-12 test count changes by +25 for this branch.
