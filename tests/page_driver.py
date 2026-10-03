"""Run the generated page's own JavaScript under node, against a stub document.

No browser is needed to check what the page does with its data: the script only
touches ``document.getElementById``, ``results.innerHTML`` and ``<script src>``
loading, all of which are stubbed here. ``run_page`` opens the one remote the
page lists (the script auto-opens up to eight) and returns the HTML it would
show for its keys, plus the value of any expression evaluated in the page's own
scope (``matches``, ``norm``, ...).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

HAVE_NODE = shutil.which("node") is not None

DRIVER = textwrap.dedent("""
    const fs = require('fs'), vm = require('vm');
    const [, , page, site, expr] = process.argv;
    const html = fs.readFileSync(page, 'utf8');
    const island = /<script type="application\\/json" id="ledger">(.*?)<\\/script>/s.exec(html)[1]
      .replace(/<\\\\\\//g, '</');
    const script = /<script>(.*?)<\\/script>\\s*<\\/body>/s.exec(html)[1];
    const slot = { innerHTML: '' };
    const results = { innerHTML: '', addEventListener() {}, querySelector: () => slot };
    const els = {
      ledger: { textContent: island }, results,
      count: { textContent: '', children: [], addEventListener() {}, append() {} },
      q: { value: '', addEventListener() {} },
    };
    const ctx = {
      document: {
        getElementById: id => els[id],
        createElement: () => ({}),
        head: { appendChild(s) {
          if (s.src) vm.runInContext(fs.readFileSync(site + '/' + s.src, 'utf8'), ctx);
        } },
      },
      navigator: {}, setTimeout() {}, console,
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(script, ctx);
    console.log(JSON.stringify({
      detail: slot.innerHTML,
      value: expr ? vm.runInContext(expr, ctx) : null,
    }));
""")


def run_page(root: Path, workdir: Path, expr: str | None = None) -> dict:
    """``{"detail": <html of the opened remote's keys>, "value": <expr>}``.

    ``root`` is a ledger root whose ``site/`` was built by ``build_site``.
    """
    driver = workdir / "page_driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    args = ["node", str(driver), str(root / "site" / "index.html"), str(root / "site")]
    if expr:
        args.append(expr)
    out = subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads(out.stdout)
