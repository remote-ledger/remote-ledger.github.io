"""The generated site (R17, D15, D20, D29, D30, D40).

One HTML file, ``index.json``, and one small script per remote under ``r/``.
No framework, no build step, no server -- which is what keeps OD2's ongoing
cost near zero. If it ever grows a framework, that is the signal to revisit
OD2 rather than absorb the maintenance quietly.

The page embeds the *index* -- every remote's identity and roll-ups -- as a
JSON island, so search works with nothing fetched. A remote's keys, codes
and layouts arrive when it is opened, from ``r/<path>.js`` (D40). That file
is a script rather than JSON because a ``<script src>`` loads from
``file://`` where ``fetch`` does not, which keeps the page working offline.
Its payload is ``json.dumps`` output with ``ensure_ascii``, never string
concatenation, so D29's rule -- data enters as data -- still holds.

**The imported database is not in the page (D57).** Its index entries are a
shard, and embedding them put 10 MB of text in the page and in front of every
visitor. The page embeds only what ``index.json`` lists, and loads the shard
the first time the visitor searches, as one script per part,
``index/<name>/<key>.js`` -- scripts for D40's reason, so a page opened from
disk still finds them. Until every part has answered, the page says it is
still searching and does not say a device is absent (R20); a part that fails
to load is reported, not read as "nothing found".

``site/index.json`` is written by the same serializer as
``build/index.json`` and must be byte-identical to it: the site needs its
own copy because Pages serves only ``site/``. So are its shard files: the
manifest and the parts, as JSON for clients next to the scripts for the page.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import paths
from .encoding import css_ident, json_payload
from .forms import PRIMARY, select
from .index import Built, Shard, build_all, shard_files
from .remote import load_remote
from .serialize import dumps
from .validate import corpus_files

TITLE = "Remote Ledger"


def payload(
    root: Path, built: Built | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(index, embedded) -- the index verbatim, plus what the page also needs.

    The index alone cannot render a remote: it carries no codes and no
    layouts by design, since D13 keeps it a *view* over identity and
    confidence. The page needs both, so they ride alongside rather than
    being bolted into the index and changing what OD4 commits. ``embedded``
    covers every remote, those of the shards included: opening one loads its
    script like any other.
    """
    index = (built or build_all(root)).index
    extra: dict[str, Any] = {}
    for path in corpus_files(root):
        remote = load_remote(path)
        where = paths.rel(root, path)
        keys: dict[str, Any] = {}
        for key in sorted(remote.keys):
            candidates: dict[str, Any] = {}
            for name, group in remote.groups(key).items():
                chosen = select(group)
                entry: dict[str, Any] = {
                    "prontoHex": remote.compile_group(key, name),
                    "confidence": chosen.confidence,
                }
                if chosen.source:
                    entry["source"] = chosen.source
                if name != PRIMARY:
                    entry["label"] = remote.variants[name].label
                # D30: a derived form carries no `source`; its citation is
                # its parent, reached in two hops. Record the pointer so the
                # page can render "derived from X" and then X's own citation.
                derived = [f for f in group if f.is_derived]
                if derived:
                    parent = {f.id: f for f in group}.get(derived[0].derived_from)
                    entry["derivedFrom"] = {
                        "form": derived[0].derived_from,
                        "source": parent.source if parent else None,
                    }
                candidates[name] = entry
            keys[key] = candidates
        layouts = remote.raw.get("layouts") or {}
        extra[where] = {
            "keys": keys,
            "layouts": layouts,
            "minSends": remote.protocol.min_sends,
            "css": _grid_rules(where, layouts),
        }
    return index, extra


def _grid_rules(file: str, layouts: dict[str, Any]) -> str:
    """Emit each layout as a real CSS grid rule -- the whole point of R9."""
    rules = []
    for name, layout in sorted(layouts.items()):
        rows = layout.get("areas") or []
        ident = "".join(c if c.isalnum() else "-" for c in f"{file}-{name}")
        body = " ".join(f'"{r}"' for r in rows)
        rules.append(f'[data-grid="{ident}"] {{ grid-template-areas: {body}; }}')
        for key in sorted({c for r in rows for c in r.split() if c != "."}):
            rules.append(
                f'[data-grid="{ident}"] [data-area="{css_ident(key)}"]'
                f" {{ grid-area: {css_ident(key)}; }}"
            )
    return "\n".join(rules)


def remote_script(file: str, data: dict[str, Any]) -> str:
    """``r/<path>.js``: one call, its arguments JSON-encoded (D29, D40).

    ``ensure_ascii`` keeps U+2028/U+2029 escaped, which older JavaScript
    engines reject inside a string literal, and sorted keys keep the file
    byte-reproducible (D20).
    """
    args = json.dumps([file, data], sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"))
    return f"ledgerRemote(...{args});\n"


def shard_script(name: str, key: str, entries: list[dict[str, Any]]) -> str:
    """``index/<name>/<key>.js``: one part's entries, for the page (D57).

    The same encoding as :func:`remote_script`, for the same reasons.
    """
    args = json.dumps([name, key, entries], sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"))
    return f"ledgerShard(...{args});\n"


def shard_descriptors(shards: tuple[Shard, ...]) -> list[dict[str, Any]]:
    """What the page embeds about the shards: enough to load them."""
    return [
        {
            "name": shard.name,
            "parts": [
                {"key": key, "remotes": len(entries),
                 "script": paths.shard_script(shard.name, key)}
                for key, entries in shard.parts.items()
            ],
            "remotes": shard.remotes,
            "root": shard.root,
        }
        for shard in shards
    ]


STYLE = """
:root {
  --bg: #fbfbfa; --fg: #1b1b1a; --muted: #6b6b66; --line: #e2e1dc;
  --card: #ffffff; --accent: #8a5a2b;
  --confirmed: #1f6f43; --verified: #2c6e9b; --plausible: #8a6d1f;
  --untested: #8a3b3b; --derived: #6b6b66;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17171a; --fg: #e8e8e4; --muted: #9a9a94; --line: #2e2e33;
    --card: #1f1f23; --accent: #d0a06a;
    --confirmed: #6cc295; --verified: #7fb6da; --plausible: #d4b462;
    --untested: #e08a8a; --derived: #9a9a94;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 0 16px 64px; background: var(--bg); color: var(--fg);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }
main { max-width: 860px; margin: 0 auto; }
header { padding: 32px 0 8px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; letter-spacing: -0.01em; }
.lede { color: var(--muted); margin: 0 0 20px; max-width: 60ch; }
input[type=search] { width: 100%; padding: 11px 13px; font-size: 1rem;
  border: 1px solid var(--line); border-radius: 8px; background: var(--card);
  color: var(--fg); }
.count { color: var(--muted); font-size: 0.85rem; margin: 10px 0 18px; }
.card { background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 16px 18px; margin: 0 0 14px; }
.card h2 { font-size: 1.05rem; margin: 0 0 2px; }
.meta { color: var(--muted); font-size: 0.85rem; margin: 0 0 12px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; border: 1px solid currentColor; }
.confirmed { color: var(--confirmed); } .verified { color: var(--verified); }
.plausible { color: var(--plausible); } .untested { color: var(--untested); }
.derived { color: var(--derived); }
.key { border-top: 1px solid var(--line); padding: 10px 0 2px; }
.key h3 { font-size: 0.9rem; margin: 0 0 6px; font-family: ui-monospace, monospace; }
.cand { margin: 0 0 8px; padding-left: 12px; border-left: 2px solid var(--line); }
.cand .src { color: var(--muted); font-size: 0.8rem; word-break: break-word; }
.cand a { color: var(--accent); }
.hex { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem; color: var(--muted); word-break: break-all;
  margin: 4px 0 0; max-height: 3.2em; overflow: hidden; }
button.copy { font: inherit; font-size: 0.78rem; padding: 3px 10px;
  border: 1px solid var(--line); border-radius: 6px; background: transparent;
  color: var(--fg); cursor: pointer; margin-top: 6px; }
.grid { display: grid; gap: 6px; margin: 10px 0 4px; max-width: 320px; }
.grid [data-area] { border: 1px solid var(--line); border-radius: 6px;
  padding: 8px 4px; text-align: center; font-size: 0.7rem;
  font-family: ui-monospace, monospace; background: var(--bg); }
.unresolved { border-left: 3px solid var(--plausible); }
.imported { color: var(--muted); }
button.more { font: inherit; font-size: 0.8rem; padding: 3px 10px;
  border: 1px solid var(--line); border-radius: 6px; background: transparent;
  color: var(--accent); cursor: pointer; }
.absent { color: var(--muted); padding: 24px 0; }
footer { color: var(--muted); font-size: 0.8rem; margin-top: 40px;
  border-top: 1px solid var(--line); padding-top: 14px; }
"""

SCRIPT = r"""
const data = JSON.parse(document.getElementById('ledger').textContent);
const results = document.getElementById('results');
// D40: a remote's keys arrive from r/<path>.js when it is opened. Scripts
// rather than fetch(), because a <script src> loads from file:// too.
const AUTO_OPEN = 8, MAX_SHOWN = 200;
const details = {}, waiting = {};
window.ledgerRemote = (file, d) => {
  details[file] = d;
  if (d.css) {
    const st = document.createElement('style');
    st.textContent = d.css;
    document.head.appendChild(st);
  }
  for (const done of waiting[file] || []) done();
  delete waiting[file];
};
const scriptFor = file => 'r/' + file.slice('remotes/'.length, -'.json'.length) + '.js';
function load(file, done) {
  if (details[file]) return done();
  const first = !waiting[file];
  (waiting[file] = waiting[file] || []).push(done);
  if (!first) return;
  const s = document.createElement('script');
  s.src = scriptFor(file);
  document.head.appendChild(s);
}
const count = document.getElementById('count');
const box = document.getElementById('q');

const esc = s => String(s).replace(/[&<>"']/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SAFE = /^(https?|mailto):/i;
const cite = s => SAFE.test(s) && !s.startsWith('//')
  ? `<a href="${esc(s)}" rel="noopener noreferrer">${esc(s)}</a>` : esc(s);

function fields(r) {
  return [r.manufacturer, r.model, ...(r.aliases||[]), ...(r.controls||[])];
}
// lookup.normalise, in JavaScript: lower case, every space and punctuation
// mark removed, so BDP-S360, "BDP S360" and BDP.S360 are one string.
const norm = s => String(s).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
function matches(q, values) {
  const needle = norm(q);
  if (!needle) return true;
  const vs = values.map(norm);
  if (vs.some(v => v.includes(needle))) return true;
  const words = q.split(/\s+/).map(norm).filter(Boolean);
  return words.length > 1 && words.every(w => vs.some(v => v.includes(w)));
}
// The same rule, compiled once per query and run over each remote's fields
// normalised once (not once per keystroke): the imported database is 300,000
// controls strings, which cost a regex apiece to normalise.
function matcher(q) {
  const needle = norm(q);
  const words = q.split(/\s+/).map(norm).filter(Boolean);
  return vs => !needle || vs.some(v => v.includes(needle)) ||
    (words.length > 1 && words.every(w => vs.some(v => v.includes(w))));
}
const haystack = r => r._n || (r._n = fields(r).map(norm));
const hitUnresolved = (u, q) => matches(q, [u.device]);

// D57: the imported database is not in this page. Its index entries are in
// index/<name>/<key>.js, one script per part, fetched the first time the
// visitor searches. Scripts rather than fetch(), as above, so a page opened
// from disk finds them too.
const parts = [];
for (const shard of data.shards)
  for (const p of shard.parts)
    parts.push({ id: shard.name + '/' + p.key, script: p.script, state: 'idle', entries: [] });
const shardLabel = data.shards.map(s => (data.imports[s.root] || {}).name || s.name).join(' and ');
const shardTotal = data.shards.reduce((n, s) => n + s.remotes, 0);
const inState = state => parts.filter(p => p.state === state).length;
window.ledgerShard = (name, key, entries) => {
  const part = parts.find(p => p.id === name + '/' + key);
  if (!part || part.state !== 'loading') return;
  part.entries = entries;
  part.state = 'loaded';
  run();
};
function loadParts(retry) {
  for (const p of parts) {
    if (p.state !== 'idle' && !(retry && p.state === 'failed')) continue;
    p.state = 'loading';
    const s = document.createElement('script');
    s.src = p.script;
    // A script that ran has called ledgerShard, which leaves 'loading'; one
    // that loaded without doing so, or never loaded, has failed.
    const failed = () => {
      s.remove();
      if (p.state === 'loading') { p.state = 'failed'; run(); }
    };
    s.onload = s.onerror = failed;
    document.head.appendChild(s);
  }
}

function renderRemote(r, i) {
  let html = `<article class="card"><h2>${esc(r.manufacturer)} ${esc(r.model)}`;
  if (r.confidence) html += ` <span class="badge ${esc(r.confidence)}">${esc(r.confidence)}</span>`;
  html += `</h2><p class="meta">${esc(r.file)}`;
  if (r.protocol) html += ` &middot; ${esc(r.protocol)}`;
  html += ` &middot; ${r.keyCount} key(s)`;
  if (r.controls && r.controls.length) html += `<br>controls ${esc(r.controls.join(', '))}`;
  if (r.aliases && r.aliases.length) html += `<br>also sold as ${esc(r.aliases.join(', '))}`;
  const imp = r.importedFrom && data.imports[r.importedFrom];
  if (imp) html += `<br><span class="imported">imported from the ${esc(imp.name)} ` +
    `(${esc(imp.licence)}) &mdash; not authored here; no key above plausible</span>`;
  html += `</p><div class="detail" data-i="${i}">` +
    `<button class="more" data-i="${i}">Show keys</button></div>`;
  return html + `</article>`;
}

function renderDetail(r) {
  const extra = details[r.file];
  let html = extra.minSends ? `<p class="meta">min sends ${extra.minSends}</p>` : '';

  for (const [name, layout] of Object.entries(extra.layouts || {})) {
    const ident = (r.file + '-' + name).replace(/[^a-zA-Z0-9]/g, '-');
    const cells = [...new Set((layout.areas||[]).flatMap(row => row.split(/\s+/)))]
      .filter(c => c && c !== '.');
    html += `<div class="grid" data-grid="${esc(ident)}">` + cells.map(k =>
      `<div data-area="${esc(k)}">${esc((layout.printedLabels||{})[k] || k)}</div>`
    ).join('') + `</div>`;
    if (layout.source) html += `<p class="src">layout: ${cite(layout.source)}</p>`;
  }

  for (const key of Object.keys(extra.keys || {}).sort()) {
    html += `<div class="key"><h3>${esc(key)}</h3>`;
    const cands = extra.keys[key];
    const names = Object.keys(cands).sort((a,b) =>
      (a!=='primary') - (b!=='primary') || a.localeCompare(b));
    for (const n of names) {
      const c = cands[n];
      html += `<div class="cand"><span class="badge ${esc(c.confidence)}">${esc(c.confidence)}</span> `;
      html += esc(c.label || n);
      if (c.source) html += `<div class="src">${cite(c.source)}</div>`;
      else if (c.derivedFrom) {
        html += `<div class="src">derived from <code>${esc(c.derivedFrom.form)}</code>`;
        if (c.derivedFrom.source) html += ` &mdash; ${cite(c.derivedFrom.source)}`;
        html += `</div>`;
      }
      html += `<p class="hex">${esc(c.prontoHex)}</p>`;
      html += `<button class="copy" data-hex="${esc(c.prontoHex)}">Copy Pronto</button>`;
      html += `</div>`;
    }
    html += `</div>`;
  }
  return html;
}

let shown = [];
// The remotes the visitor has opened for this query: a part of the imported
// database arriving can redraw the list, and the redraw must not close them.
const opened = new Set();
function open(i) {
  const r = shown[i];
  const slot = results.querySelector(`.detail[data-i="${i}"]`);
  if (!r || !slot) return;
  opened.add(r.file);
  load(r.file, () => { slot.innerHTML = renderDetail(r); });
}

function renderUnresolved(u) {
  return `<article class="card unresolved"><h2>${esc(u.device)} ` +
    `<span class="badge plausible">checked, nothing found</span></h2>` +
    `<p class="meta">checked ${esc(u.checked)}</p></article>`;
}

let drawn = null, drawnFor = null;
function run() {
  const q = box.value.trim().toLowerCase();
  const searching = !!norm(q);
  if (searching) loadParts(false);
  const match = matcher(q);
  const here = data.remotes.filter(r => match(haystack(r)));
  const imported = [];
  // Without a query the page lists what it holds, as it always did; the
  // imported database is only ever searched.
  if (searching)
    for (const p of parts)
      if (p.state === 'loaded') for (const r of p.entries) if (match(haystack(r))) imported.push(r);
  const remotes = here.concat(imported);
  const unresolved = data.unresolved.filter(u => hitUnresolved(u, q));

  // R20: "nobody has looked" may only be said once every part has answered.
  const pending = inState('idle') + inState('loading'), failed = inState('failed');
  let line = `${remotes.length} remote(s)`;
  if (searching && parts.length) line += ` (${imported.length} from the ${shardLabel})`;
  line += `, ${unresolved.length} recorded as checked-and-not-found`;
  if (remotes.length > MAX_SHOWN)
    line += `. Showing ${MAX_SHOWN} of ${remotes.length}; refine the search to see the rest`;
  if (!searching && parts.length)
    line += `. ${shardTotal.toLocaleString()} more are in the ${shardLabel}, searched when you type`;
  else if (pending)
    line += `. Still searching the ${shardLabel}: ${inState('loaded')} of ${parts.length} files loaded`;
  else if (failed)
    line += `. The ${shardLabel} did not load in full (${failed} of ${parts.length} files missing)`;
  count.textContent = line;
  if (searching && !pending && failed) {
    const retry = document.createElement('button');
    retry.className = 'more';
    retry.id = 'retry';
    retry.textContent = 'Try again';
    count.append(' ', retry);
  }

  let html;
  if (!remotes.length && !unresolved.length) {
    if (!q) html = `<p class="absent">The ledger is empty.</p>`;
    else if (pending) html = `<p class="absent">Nothing yet for &ldquo;${esc(box.value)}&rdquo;
         in the part of the ledger loaded so far; still searching the ${esc(shardLabel)}.</p>`;
    else if (failed) html = `<p class="absent">Nothing for &ldquo;${esc(box.value)}&rdquo; in
         the part of the ledger that loaded, but ${failed} of ${parts.length} files of the
         ${esc(shardLabel)} did not, so this does not show that nobody has looked up the device.</p>`;
    else html = `<p class="absent">Nothing for &ldquo;${esc(box.value)}&rdquo;, and nothing recorded as
         having been searched for it either &mdash; so this is a device nobody has looked up yet,
         not one checked and found to have no known remote.</p>`;
    shown = [];
  } else {
    shown = remotes.slice(0, MAX_SHOWN);
    html = shown.map(renderRemote).join('') + unresolved.map(renderUnresolved).join('');
  }
  if (q !== drawnFor) { opened.clear(); drawnFor = q; }
  // A part arriving that adds nothing to the page must not redraw it, and one
  // that does must not close what the visitor opened.
  if (html === drawn) return;
  drawn = html;
  results.innerHTML = html;
  const reopen = [...opened];
  shown.forEach((r, i) => {
    if (shown.length <= AUTO_OPEN || reopen.includes(r.file)) open(i);
  });
}

count.addEventListener('click', ev => {
  if (ev.target.closest('#retry')) { loadParts(true); run(); }
});
results.addEventListener('click', ev => {
  const more = ev.target.closest('button.more');
  if (more) return open(Number(more.dataset.i));
  const btn = ev.target.closest('button.copy');
  if (!btn) return;
  navigator.clipboard?.writeText(btn.dataset.hex);
  const was = btn.textContent; btn.textContent = 'Copied';
  setTimeout(() => { btn.textContent = was; }, 1200);
});
box.addEventListener('input', run);
run();
"""


def render_html(index: dict[str, Any], shards: tuple[Shard, ...] = ()) -> str:
    embedded = {
        "remotes": index["remotes"],
        "shards": shard_descriptors(shards),
        "unresolved": index["unresolved"],
        "imports": paths.IMPORTS,
    }
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<header>
<h1>{TITLE}</h1>
<p class="lede">IR remote configurations, looked up by the <em>device</em> you
own &mdash; with a citation for every code and an honest tier saying how far
it has been checked.</p>
<input type="search" id="q" placeholder="Search a device, model or maker &mdash; e.g. DX3 Pro"
       autocomplete="off" spellcheck="false">
<p class="count" id="count"></p>
</header>
<div id="results"></div>
<footer>
Generated from the ledger &mdash; never hand-edited. A device shown as
<em>checked, nothing found</em> was looked for and not found; one that
returns nothing at all is one nobody has looked up yet.
</footer>
</main>
<script type="application/json" id="ledger">{json_payload(embedded)}</script>
<script>{SCRIPT}</script>
</body>
</html>
"""


def build_site(root: Path, out_root: Path) -> list[str]:
    built = build_all(root)
    index, extra = payload(root, built)
    target = out_root / "site"
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_text(
        render_html(index, built.shards), encoding="utf-8", newline="\n"
    )
    # D20: the same serializer, so this is byte-identical to build/index.json.
    (target / "index.json").write_text(dumps(index), encoding="utf-8", newline="\n")

    # D57: the shards' manifests and parts, byte for byte what build/ holds
    # (JSON, for clients), and the page's copy of each part as a script. The
    # directory is the site's alone, so a part that is gone is removed.
    shutil.rmtree(target / paths.SHARD_DIR, ignore_errors=True)
    files = shard_files(built.shards)
    for shard in built.shards:
        for key, entries in shard.parts.items():
            files[paths.shard_script(shard.name, key)] = shard_script(shard.name, key, entries)
    for rel, text in files.items():
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")

    for file, data in sorted(extra.items()):
        script = target / paths.site_script(file)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(remote_script(file, data), encoding="utf-8", newline="\n")
    return []
