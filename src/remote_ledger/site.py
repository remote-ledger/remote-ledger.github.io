"""The generated site (R17, D15, D20, D29, D30).

One HTML file plus ``index.json``. No framework, no build step, no server --
which is what keeps OD2's ongoing cost near zero. If it ever grows a
framework, that is the signal to revisit OD2 rather than absorb the
maintenance quietly.

The whole ledger is embedded as a JSON island rather than fetched, so the
page works from ``file://`` as well as from Pages, and so D29's
script-payload rule applies in the one place data enters the document.

``site/index.json`` is written by the same serializer as
``build/index.json`` and must be byte-identical to it: the site needs its
own copy because Pages serves only ``site/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .encoding import css_ident, json_payload
from .forms import PRIMARY, select
from .index import build_index
from .remote import load_remote
from .serialize import dumps
from .validate import corpus_files

TITLE = "Remote Ledger"


def payload(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """(index, embedded) -- the index verbatim, plus what the page also needs.

    The index alone cannot render a remote: it carries no codes and no
    layouts by design, since D13 keeps it a *view* over identity and
    confidence. The page needs both, so they ride alongside rather than
    being bolted into the index and changing what OD4 commits.
    """
    index, _ = build_index(root)
    extra: dict[str, Any] = {}
    for path in corpus_files(root):
        remote = load_remote(path)
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
        extra[remote.where] = {
            "keys": keys,
            "layouts": remote.raw.get("layouts") or {},
            "minSends": remote.protocol.min_sends,
        }
    return index, extra


def _grid_rules(extra: dict[str, Any]) -> str:
    """Emit each layout as a real CSS grid rule -- the whole point of R9."""
    rules = []
    for file, data in sorted(extra.items()):
        for name, layout in sorted((data.get("layouts") or {}).items()):
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
.state { font-size: 0.8rem; color: var(--muted); }
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
.absent { color: var(--muted); padding: 24px 0; }
footer { color: var(--muted); font-size: 0.8rem; margin-top: 40px;
  border-top: 1px solid var(--line); padding-top: 14px; }
"""

SCRIPT = r"""
const data = JSON.parse(document.getElementById('ledger').textContent);
const results = document.getElementById('results');
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
function hit(r, q) {
  if (!q) return true;
  const hay = fields(r).join(' ').toLowerCase();
  return hay.includes(q) || q.split(/\s+/).every(w => hay.includes(w));
}
function hitUnresolved(u, q) {
  if (!q) return true;
  const d = u.device.toLowerCase();
  return d.includes(q) || q.split(/\s+/).every(w => d.includes(w));
}

function renderRemote(r) {
  const extra = data.extra[r.file] || {keys:{}, layouts:{}};
  let html = `<article class="card"><h2>${esc(r.manufacturer)} ${esc(r.model)}`;
  if (r.confidence) html += ` <span class="badge ${esc(r.confidence)}">${esc(r.confidence)}</span>`;
  html += `</h2><p class="meta">${esc(r.file)}`;
  if (r.protocol) html += ` &middot; ${esc(r.protocol)}`;
  if (extra.minSends) html += ` &middot; min sends ${extra.minSends}`;
  if (r.controls && r.controls.length) html += `<br>controls ${esc(r.controls.join(', '))}`;
  if (r.aliases && r.aliases.length) html += `<br>also sold as ${esc(r.aliases.join(', '))}`;
  html += `</p>`;

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
  return html + `</article>`;
}

function renderUnresolved(u) {
  return `<article class="card unresolved"><h2>${esc(u.device)} ` +
    `<span class="badge plausible">checked, nothing found</span></h2>` +
    `<p class="meta">checked ${esc(u.checked)} &middot; searched ` +
    `${esc((u.searched||[]).join(', '))}</p>` +
    (u.note ? `<p class="state">${esc(u.note)}</p>` : '') + `</article>`;
}

function run() {
  const q = box.value.trim().toLowerCase();
  const remotes = data.remotes.filter(r => hit(r, q));
  const unresolved = data.unresolved.filter(u => hitUnresolved(u, q));
  count.textContent = `${remotes.length} remote(s), ${unresolved.length} recorded as checked-and-not-found`;
  if (!remotes.length && !unresolved.length) {
    results.innerHTML = q
      ? `<p class="absent">Nothing for &ldquo;${esc(box.value)}&rdquo;, and nothing recorded as
         having been searched for it either &mdash; so this is a device nobody has looked up yet,
         not one checked and found to have no known remote.</p>`
      : `<p class="absent">The ledger is empty.</p>`;
    return;
  }
  results.innerHTML = remotes.map(renderRemote).join('') +
                      unresolved.map(renderUnresolved).join('');
}

results.addEventListener('click', ev => {
  const btn = ev.target.closest('button.copy');
  if (!btn) return;
  navigator.clipboard?.writeText(btn.dataset.hex);
  const was = btn.textContent; btn.textContent = 'Copied';
  setTimeout(() => { btn.textContent = was; }, 1200);
});
box.addEventListener('input', run);
run();
"""


def render_html(index: dict[str, Any], extra: dict[str, Any]) -> str:
    embedded = {
        "remotes": index["remotes"],
        "unresolved": index["unresolved"],
        "extra": extra,
    }
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
<style>{STYLE}
{_grid_rules(extra)}
</style>
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
    index, extra = payload(root)
    target = out_root / "site"
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_text(
        render_html(index, extra), encoding="utf-8", newline="\n"
    )
    # D20: the same serializer, so this is byte-identical to build/index.json.
    (target / "index.json").write_text(dumps(index), encoding="utf-8", newline="\n")
    return []
