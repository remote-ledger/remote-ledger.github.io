// Drives the generated page's own script against a stand-in for the DOM, the
// way a visitor would (tests/test_site_page.py runs it under node).
//
//   node page_harness.js <site dir> [<published path to fail to load> ...]
//
// It prints one JSON object: the page's status line and result list after each
// step, so the Python test can assert what a visitor would have read, and in
// which order. Scripts the page injects (`index/<name>/<key>.js`, `r/<path>.js`)
// are read from the site directory and run in the page's context when the page
// appends them to <head>, which is what a browser does with a <script src>.
// The paths named after the site directory fail to load, as a network error or
// a missing file would.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const siteDir = path.resolve(process.argv[2]);
const unavailable = new Set(process.argv.slice(3));
const html = fs.readFileSync(path.join(siteDir, 'index.html'), 'utf8');
const island = /<script type="application\/json" id="ledger">([\s\S]*?)<\/script>/.exec(html)[1]
  .replace(/<\\\//g, '</');
const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].pop()[1];

const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 10000) {
  const start = Date.now();
  while (!fn()) {
    if (Date.now() - start > ms) throw new Error('timed out');
    await sleep(2);
  }
}

function element(id) {
  const el = {
    id, tagName: 'div', className: '', dataset: {}, value: '', children: [], listeners: {},
    textContent: '', slots: {},
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    append(...parts) {
      for (const part of parts) {
        if (typeof part === 'string') el.textContent += part; else el.children.push(part);
      }
    },
    // `.detail[data-i="3"]` is the one selector the page asks results for.
    querySelector(selector) {
      const i = /data-i="(\d+)"/.exec(selector);
      if (!i || !new RegExp(`class="detail" data-i="${i[1]}"`).test(el._html)) return null;
      return (el.slots[i[1]] = el.slots[i[1]] || { innerHTML: '' });
    },
    remove() {},
  };
  let htmlText = '';
  Object.defineProperty(el, '_html', { get: () => htmlText });
  Object.defineProperty(el, 'innerHTML', {
    get: () => htmlText,
    set(v) { htmlText = v; el.slots = {}; },
  });
  return el;
}

function page() {
  const ids = { ledger: element('ledger'), results: element('results'),
                count: element('count'), q: element('q') };
  ids.ledger.textContent = island;
  const requested = [];
  const document = {
    getElementById: id => ids[id] || ids.count.children.find(c => c.id === id) || null,
    createElement(tag) { const el = element(''); el.tagName = tag; return el; },
    head: {
      appendChild(el) {
        if (el.tagName !== 'script') return el;
        requested.push(el.src);
        setTimeout(() => {
          const file = path.join(siteDir, el.src);
          if (unavailable.has(el.src) || !fs.existsSync(file)) return el.onerror && el.onerror();
          vm.runInContext(fs.readFileSync(file, 'utf8'), context);
          if (el.onload) el.onload();
        }, 1);
        return el;
      },
    },
  };
  const context = vm.createContext({ document, navigator: {}, setTimeout, console });
  context.window = context;
  vm.runInContext(source, context);
  const type = text => { ids.q.value = text; ids.q.listeners.input.forEach(fn => fn()); };
  const view = () => ({ count: ids.count.textContent, results: ids.results.innerHTML,
                        retry: !!document.getElementById('retry') });
  // A click on `selector` inside results, whose target is `found`.
  const click = (selector, found) => ids.results.listeners.click.forEach(
    fn => fn({ target: { closest: sel => (sel === selector ? found : null) } }));
  const retry = () => ids.count.listeners.click.forEach(
    fn => fn({ target: { closest: sel => (sel === '#retry' ? document.getElementById('retry') : null) } }));
  return {
    type, view, requested, click, retry,
    slot: i => ids.results.slots[i] || { innerHTML: '' },
    loading: () => /Still searching/.test(ids.count.textContent),
  };
}

(async () => {
  const out = {};
  const p = page();
  out.initial = { ...p.view(), requested: [...p.requested] };

  p.type('AK 77');                         // only in a shard remote's controls
  out.shardOnlyWhileLoading = { ...p.view(), requested: [...p.requested] };
  await until(() => !p.loading());
  out.shardOnlyLoaded = p.view();

  p.type('Sony BDP-BX510');                // R20's middle row
  out.checkedNotFound = p.view();

  p.type('Nokia 3310');                    // nobody has looked
  out.absent = p.view();

  const fresh = page();                    // a page that has loaded nothing yet
  fresh.type('Nokia 3310');
  out.absentWhileLoading = fresh.view();
  await until(() => !fresh.loading());
  out.absentLoaded = fresh.view();

  p.type('');
  out.cleared = p.view();

  // Opening a shard remote loads its own r/<path>.js like any other.
  if (!unavailable.size) {
    const opener = page();
    opener.type('akai ak80');
    await until(() => !opener.loading());
    out.beforeOpen = opener.view();
    opener.click('button.more', { dataset: { i: '0' } });
    await until(() => opener.slot(0).innerHTML.includes('KEY_'));
    out.opened = opener.slot(0).innerHTML;
  }
  console.log(JSON.stringify(out));
})().catch(err => { console.error(err); process.exit(1); });
