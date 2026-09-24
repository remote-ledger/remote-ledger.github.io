# lircd oracle vectors

Inputs and expected outputs for `tests/test_lirc_transmit.py`, which checks
`remote_ledger.lirc` against what lircd itself transmits.

**The `.conf` files are synthetic.** Each was written for this repository to
exercise specific lircd rules (its header comment says which); none is copied
from the LIRC remotes database. The `.out` and `.stdout` files are lircd's own
output for them, not hand-written.

## The oracle

- **Source:** the lirc 0.10.2 release tarball, `lirc-0.10.2.tar.bz2`,
  sha256 `3d44ec8274881cf262f160805641f0827ffcc20ade0d85e7e6f3b90e0d3d222a`.
- **One local patch**, to `plugins/file.c`, so that consecutive sends can be
  told apart. In `send_func`, after the line that writes the trailing gap:

  ```diff
   	write_line("space", remote->min_remaining_gap);
  +	chk_write(outfile_fd, "# end\n", 6);
   	return 1;
  ```

  Nothing else is changed; every `# end` line in a `.out` file comes from it.
- **Build** (gcc 13.3, x86-64 Linux), in the unpacked tree `$B`:

  ```sh
  ./configure --prefix=$B/../inst --without-x
  make -C lib && make -C plugins file.la && make -C tools irsimsend
  ```

- **Run**, for each entry of `index.json`, from an empty directory (irsimsend
  writes `simsend.out` into the current directory):

  ```sh
  LD_LIBRARY_PATH=$B/lib/.libs $B/tools/.libs/irsimsend \
      -U $B/plugins/.libs -c <count> [-k <keysym>] <conf>
  ```

  stdout is saved as `<id>.stdout`, `simsend.out` as `<id>.out`, and the exit
  status in `oracle.json`. The warnings irsimsend prints about a missing
  `lirc_options.conf` are expected and harmless.

`python tests/vectors/lirc/regen.py --lirc-build $B` does exactly that for
every entry.

## The one vector that needs more than irsimsend

irsimsend zeroes every remote's `min_repeat` before sending
(`tools/irsimsend.cpp:168,194`), which hides lircd's `repeat_countdown` path.
`min_repeat.c2.keep-min-repeat` is therefore generated with an `LD_PRELOAD`
shim — `SHIM_C` in `tools/lirc_oracle_compare.py` — run with
`LIRC_ORACLE_KEEP_MIN_REPEAT=1`. The shim interposes `read_config` to
remember each remote's parsed `min_repeat` and `send_ir_ncode` to restore it
before every send; it changes nothing else. `regen.py` builds it with `cc`.

## Reading the outputs

- `.out` lines are `pulse N` / `space N` in microseconds, alternating from a
  pulse; the last `space` of each send is the gap lircd waits after it, then
  `# end`. A remote with no bit timings gets a single `code N` line per send
  instead (`plugins/file.c:219-224`), with no `# end`. A send lircd refuses
  writes nothing.
- A `reject_*` file is one lircd refuses to parse: exit status 1,
  "Cannot parse", and no output.
- `eof.c2` exits on `SIGUSR1` (status -10): the file driver raises it after
  writing a value with the `LIRC_EOF` bit set (`plugins/file.c:206-209`). Its
  `.stdout` is empty because irsimsend dies before flushing stdout.
- `include_parent.conf` includes `include_child.conf`, which is not a vector
  of its own.
