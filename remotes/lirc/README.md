# Imported from the LIRC remotes database

Everything under `remotes/lirc/` except this file and `COPYING` is generated
by `rl import lirc` from the LIRC remotes database. It is **not authored
here**. SPEC.md R19 sets the conditions an import must meet, and DESIGN.md
§14 describes how this one meets them.

- **Upstream:** <https://sourceforge.net/p/lirc-remotes/code/> (git:
  `git.code.sf.net/p/lirc-remotes/code`). The commit every file was built
  from is named in `IMPORT.md` and in every citation.
- **Licence:** GPL-2.0-or-later; see `COPYING`. The upstream repository
  states no licence. Debian ships the database's pre-0.9.0 subset as
  `lirc-compat-remotes` and records it as `Files: * License: GPL-2.0+`
  ([debian/copyright](https://sources.debian.org/data/main/l/lirc-compat-remotes/0.9.0-2/debian/copyright)).
  Later files come from the same project, and nothing on record says
  otherwise. **This is a reading of the licence, not a grant.** If it
  proves wrong, deleting this directory removes every imported file.
- **Attribution:** each form's citation names the upstream file's
  contributor as its header does. Email addresses are not copied here; the
  pinned upstream file keeps the full notice.

## What an imported remote is, and is not

- **Every form is `plausible`**: one source that nothing has cross-checked
  (SPEC §5). Upstream's own claims survive only as citation text.
- **Each citation says how the form was produced:**
  - a `raw_codes` capture;
  - a parametric block decoded to NEC1, NECx2 or Sony20, accepted only
    because our encoder's rendering matched lircd's own expansion; or
  - the block expanded to raw timings by a port of lircd 0.10.2's transmit
    code. That port matches lircd on every button of the upstream corpus.
- **Authored data wins.** An upstream remote that collides with an authored
  one is skipped. To curate an imported remote, move its file out of this
  directory: from then on it is authored, and the import leaves it alone.
- **Nothing is dropped silently.** `IMPORT.md` lists every file, block and
  button the import could not represent, with the reason.

Do not edit files here by hand; the next import overwrites them.
