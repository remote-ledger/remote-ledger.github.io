#!/usr/bin/env python3
"""Fetch RemoteCentral's Infrared Hex Code Database into ``sources/remotecentral/`` (DESIGN D128).

The database is about three thousand pages (an index, a page of models for each brand, a page of codes
for each model), too many to keep as HTML, so the pin of ``rl import remotecentral`` is what the pages
say: one gzipped JSON file for each brand (its models, and for each the remote models the codes were
learned from, and each function's Pronto hex as the page gives it, with its page) and a ``MANIFEST.json`` with the
retrieval date, the SHA-256 of each brand file's JSON, and what the index announced against what was
found. The importer reads only that directory.

    python3 tools/fetch_remotecentral.py [--out DIR] [--cache DIR] [--delay SECONDS] [--brands a,b]

Pages are fetched one at a time, a second apart, with a User-Agent that says what is asking. Each page
is kept in ``--cache`` (outside the repository) as it arrives, so an interrupted run resumes where it
stopped and a second run costs no requests. A server that starts refusing (HTTP 403, 429, 503) stops the
run: it is not retried into the ground. The retrieval date is not an option: it is the day (UTC) each page was asked for, and a run
that crosses midnight records both days.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from remote_ledger.remotecentral import page  # noqa: E402

USER_AGENT = "remote-ledger snapshot (+https://github.com/remote-ledger)"
REFUSED = (403, 429, 503)
FORMAT = 1


class Refused(RuntimeError):
    pass


class Fetcher:
    def __init__(self, cache: Path, delay: float) -> None:
        self.cache, self.delay, self.last, self.network = cache, delay, 0.0, 0
        #: the UTC days the pages this run used were asked for: today's for a page fetched now, the day a
        #: cached page was written for one fetched in an earlier (or interrupted) run
        self.days: set[str] = set()
        cache.mkdir(parents=True, exist_ok=True)

    def path_of(self, path: str) -> Path:
        return self.cache / (re.sub(r"[^A-Za-z0-9._-]", "_", path.strip("/")) or "index")

    def get(self, path: str) -> str | None:
        """The page at ``path`` (from the cache, else the network), None for a 404."""
        local = self.path_of(path)
        cached = local.with_suffix(local.suffix + ".html")
        if cached.is_file():
            self.days.add(datetime.fromtimestamp(cached.stat().st_mtime, timezone.utc).date().isoformat())
            return cached.read_text(encoding="utf-8")
        if local.with_suffix(local.suffix + ".404").is_file():
            return None
        waited = time.monotonic() - self.last
        if waited < self.delay:
            time.sleep(self.delay - waited)
        for attempt, pause in enumerate((0, 3, 10, 30)):
            time.sleep(pause)
            self.last = time.monotonic()
            request = urllib.request.Request(page.BASE + path, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    local.with_suffix(local.suffix + ".404").write_text("", encoding="utf-8")
                    return None
                if exc.code in REFUSED:
                    raise Refused(f"{path}: the server answered {exc.code}; stopping, not retrying") from exc
                print(f"  {path}: HTTP {exc.code}, attempt {attempt + 1}", file=sys.stderr)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"  {path}: {exc}, attempt {attempt + 1}", file=sys.stderr)
            else:
                self.network += 1
                local.with_suffix(local.suffix + ".html").write_text(body, encoding="utf-8")
                self.days.add(datetime.now(timezone.utc).date().isoformat())
                return body
        raise Refused(f"{path}: four attempts failed")


def listing(fetcher: Fetcher, path: str) -> tuple[list[str], int | None]:
    """Every page of a paginated listing, in order, and the count its first page announces."""
    first = fetcher.get(path)
    if first is None:
        return [], None
    pages = [first]
    for n in range(2, page.last_page(first) + 1):
        more = fetcher.get(f"{path}page-{n}/")
        if more is None:
            raise Refused(f"{path}page-{n}/ is listed and missing")
        pages.append(more)
    return pages, page.total(first)


def fetch_model(fetcher: Fetcher, brand: str, slug: str, notes: list[str]) -> dict | None:
    path = f"{page.ROOT}{brand}/{slug}/"
    pages, announced = listing(fetcher, path)
    if not pages:
        notes.append(f"{path}: 404")
        return None
    title, groups = "", []
    for number, html in enumerate(pages, 1):
        parsed = page.model_page(html)
        title = title or parsed.title
        for position, group in enumerate(parsed.groups):
            rows = [[label, hex_, number] for label, hex_ in group.rows]
            if groups and position == 0 and groups[-1]["remote"] == group.remote:
                groups[-1]["rows"] += rows              # a group that goes on over the page break
            else:
                groups.append({"remote": group.remote, "rows": rows})
    found = sum(len(g["rows"]) for g in groups)
    if announced is not None and announced != found:
        notes.append(f"{path}: announces {announced} codes, {found} found")
    return {"slug": slug, "title": title, "pages": len(pages), "announced": announced, "groups": groups}


def fetch_brand(fetcher: Fetcher, slug: str, name: str, notes: list[str]) -> dict:
    path = f"{page.ROOT}{slug}/"
    pages, announced = listing(fetcher, path)
    listed: dict[str, str] = {}
    for html in pages:
        for model, label in page.models(html, slug):
            listed.setdefault(model, label)
    if announced is not None and announced != len(listed):
        notes.append(f"{path}: announces {announced} models, {len(listed)} found")
    models = []
    for model, label in listed.items():
        fetched = fetch_model(fetcher, slug, model, notes)
        if fetched is not None:
            fetched["label"] = label
            models.append(fetched)
    return {"format": FORMAT, "brand": name, "slug": slug, "announced": announced, "models": models}


def write_brand(directory: Path, data: dict) -> tuple[str, dict]:
    """The brand's file, gzipped without a name or time so that the same JSON is the same bytes."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    name = f"{data['slug']}.json.gz"
    with (directory / name).open("wb") as handle, gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=9) as out:
        out.write(raw)
    codes = sum(len(g["rows"]) for m in data["models"] for g in m["groups"])
    return name, {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "models": len(data["models"]), "codes": codes}


def retrieved(fetcher: Fetcher) -> str:
    """The day the pages were asked for, or ``first to last`` when a long run crossed midnight (UTC)."""
    days = sorted(fetcher.days)
    return days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path("sources/remotecentral"))
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "remote-ledger" / "remotecentral")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests to the server")
    parser.add_argument("--brands", help="only these brand slugs, comma separated (for trying it out)")
    args = parser.parse_args(argv)
    fetcher = Fetcher(args.cache, max(args.delay, 0.5))
    index = fetcher.get(page.ROOT)
    if index is None:
        print("the index page is gone", file=sys.stderr)
        return 1
    announced = page.banner(index)
    listed = page.brands(index)
    wanted = set(args.brands.split(",")) if args.brands else None
    args.out.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    files: dict[str, dict] = {}
    try:
        for slug, name in listed:
            if wanted is not None and slug not in wanted:
                continue
            data = fetch_brand(fetcher, slug, name, notes)
            file, entry = write_brand(args.out, data)
            files[file] = entry
            print(f"{slug}: {entry['models']} models, {entry['codes']} codes ({fetcher.network} requests so far)", flush=True)
    except Refused as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    if wanted is None:
        for stale in sorted(args.out.glob("*.json.gz")):
            if stale.name not in files:
                stale.unlink()
    manifest = {
        "source": page.BASE + page.ROOT, "retrieved": retrieved(fetcher), "tool": "tools/fetch_remotecentral.py",
        "format": FORMAT, "announced": {"brands": announced[0], "models": announced[1]} if announced else None,
        "brands": files, "notes": notes,
    }
    (args.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(f["codes"] for f in files.values())
    print(f"{len(files)} brands, {sum(f['models'] for f in files.values())} models, {total} codes, {len(notes)} notes; {fetcher.network} requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
