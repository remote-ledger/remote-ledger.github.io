"""``rl import remotecentral`` (DESIGN D128 to D132): RemoteCentral's learned codes, the page reader, the
fetcher's walk, the snapshot, the importer, and the committed tree."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from remote_ledger.errors import ValidationError
from remote_ledger.remotecentral import importer as ri
from remote_ledger.remotecentral import page
from remote_ledger.serialize import load

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / ri.SNAPSHOT


def tool():
    spec = importlib.util.spec_from_file_location("fetch_remotecentral", ROOT / "tools" / "fetch_remotecentral.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the pages -----------------------------------------------------------------------------------------

NEC = "0000 006D 0000 0004 0155 00AA 0015 0015 0015 0040 0015 0E47"
SHORT = "0000 006D 0000 0001 0020 0020"


def code_row(n: int, label: str, hex_: str) -> str:
    return (f'<tr><td width="38%" class="filematchleft"><b>{label}</b><div class="copyclipboard">(<a href="javascript:void(0)">'
            f'Copy to Clipboard</a>)</div></td><td width="62%" class="filematchright hexcodes"><span id="HexCode{n}">\n {hex_}\n</span></td></tr>\n')


def model_html(title: str, announced: int, groups: list[tuple[str, list[tuple[str, str]]]], last: int = 1) -> str:
    out = (f'<div class="filetoptitle">{title} Infrared Codes</div><div class="smalltext"><b>This model contains a total of '
           f'<span class="bluetext">{announced}</span> IR codes.</b></div>\n')
    if last > 1:
        out += "".join(f'<a href="/cgi-bin/codes/b/m/page-{n}/">{n}</a> ' for n in range(2, last + 1))
    n = 0
    for remote, rows in groups:
        out += f'<tr><td colspan="2" class="textnorm"><b>Remote Model:</b> {remote}</td></tr>\n'
        for label, hex_ in rows:
            out += code_row(n, label, hex_)
            n += 1
    return out


def test_the_index_names_its_brands_and_how_many_it_holds():
    index = ('<td>This database contains 227 brands with 2,114 models.</td>'
             '<a href="/cgi-bin/codes/3m/"><b>3M</b></a><a href="/cgi-bin/codes/anthem/">Anthem</a>'
             '<a href="/cgi-bin/codes/anthem/">Anthem</a><a href="/cgi-bin/codes/page-2/">next</a>')
    assert page.banner(index) == (227, 2114)
    assert page.brands(index) == [("3m", "3M"), ("anthem", "Anthem")]
    assert page.banner("<p>nothing</p>") is None


def test_a_brand_page_lists_models_and_pages_and_says_how_many_there_are():
    brand = ('<span>a total of <span class="bluetext">257</span> models</span>'
             '<a href="/cgi-bin/codes/sony/100_disc/">100 Disc &amp; CD</a><a href="/cgi-bin/codes/sony/page-2/">2</a>'
             '<a href="/cgi-bin/codes/sony/page-4/">4</a><a href="/cgi-bin/codes/sony/100_disc/">again</a>'
             '<a href="/cgi-bin/codes/denon/other/">another brand</a>')
    assert page.models(brand, "sony") == [("100_disc", "100 Disc & CD")]
    assert page.last_page(brand) == 4 and page.total(brand) == 257
    assert page.last_page("<p>no page bar</p>") == 1 and page.total("<p></p>") is None


def test_a_model_page_is_groups_of_codes_under_the_remote_they_were_learned_from():
    html = model_html("Denon AVC-3020", 3, [("RC-134", [("Power", NEC), ("Vol+", NEC.lower())]), ("RC-135 &amp; co", [("Mute", NEC)])])
    parsed = page.model_page(html)
    assert (parsed.title, parsed.announced, parsed.codes) == ("Denon AVC-3020", 3, 3)
    assert [(g.remote, [r[0] for r in g.rows]) for g in parsed.groups] == [("RC-134", ["Power", "Vol+"]), ("RC-135 & co", ["Mute"])]
    assert parsed.groups[0].rows[1][1] == NEC.lower()          # the page's own spelling; the importer upper-cases
    headless = page.model_page(model_html("X Y", 1, [("", [("Power", NEC)])]).replace('<b>Remote Model:</b> ', '<b>Remote Model:</b>'))
    assert headless.groups[0].rows[0][0] == "Power"


# --- the fetcher's walk ------------------------------------------------------------------------------------


class FakeFetcher:
    """What ``tools/fetch_remotecentral.py`` reads pages through, over a dict of paths to HTML."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def get(self, path):
        self.asked.append(path)
        return self.pages.get(path)


def test_the_fetcher_joins_a_group_that_goes_on_over_a_page_break_and_notes_what_does_not_add_up():
    fetch = tool()
    base = "/cgi-bin/codes/b/m/"
    pages = {
        base: model_html("B M", 5, [("RC-1", [("Power", NEC), ("Play", NEC)])], last=3),
        base + "page-2/": model_html("B M", 5, [("RC-1", [("Stop", NEC)]), ("RC-2", [("Power", NEC)])], last=3),
        base + "page-3/": model_html("B M", 5, [("RC-2", [("Mute", NEC)])], last=3),
    }
    notes: list[str] = []
    result = fetch.fetch_model(FakeFetcher(pages), "b", "m", notes)
    assert result["pages"] == 3 and result["announced"] == 5 and notes == []
    assert [(g["remote"], [(r[0], r[2]) for r in g["rows"]]) for g in result["groups"]] == [
        ("RC-1", [("Power", 1), ("Play", 1), ("Stop", 2)]), ("RC-2", [("Power", 2), ("Mute", 3)])]
    pages[base + "page-3/"] = model_html("B M", 5, [("RC-2", [])], last=3)
    notes = []
    fetch.fetch_model(FakeFetcher(pages), "b", "m", notes)
    assert notes == [f"{base}: announces 5 codes, 4 found"]
    notes = []
    assert fetch.fetch_model(FakeFetcher({}), "b", "gone", notes) is None and notes == ["/cgi-bin/codes/b/gone/: 404"]
    with pytest.raises(fetch.Refused, match="listed and missing"):
        del pages[base + "page-2/"]
        fetch.fetch_model(FakeFetcher(pages), "b", "m", [])


def test_the_fetcher_lists_a_brands_models_over_its_pages():
    fetch = tool()
    one = '<a href="/cgi-bin/codes/b/m1/">M1</a><a href="/cgi-bin/codes/b/page-2/">2</a><span>total of <span class="bluetext">3</span></span>'
    two = '<a href="/cgi-bin/codes/b/m2/">M2</a><a href="/cgi-bin/codes/b/m1/">M1</a>'
    pages = {"/cgi-bin/codes/b/": one, "/cgi-bin/codes/b/page-2/": two}
    for slug in ("m1", "m2"):
        pages[f"/cgi-bin/codes/b/{slug}/"] = model_html(f"B {slug.upper()}", 1, [("RC", [("Power", NEC)])])
    notes: list[str] = []
    brand = fetch.fetch_brand(FakeFetcher(pages), "b", "B", notes)
    assert [(m["slug"], m["label"], m["title"]) for m in brand["models"]] == [("m1", "M1", "B M1"), ("m2", "M2", "B M2")]
    assert notes == ["/cgi-bin/codes/b/: announces 3 models, 2 found"]


def test_a_brand_file_is_the_same_bytes_whenever_it_is_written(tmp_path):
    fetch = tool()
    data = {"format": 1, "brand": "B", "slug": "b", "announced": 1, "models": []}
    first = fetch.write_brand(tmp_path, data)
    one = (tmp_path / "b.json.gz").read_bytes()
    fetch.write_brand(tmp_path, data)
    assert (tmp_path / "b.json.gz").read_bytes() == one
    assert first == ("b.json.gz", {"sha256": hashlib.sha256(gzip.decompress(one)).hexdigest(), "bytes": len(gzip.decompress(one)), "models": 0, "codes": 0})


def test_a_run_that_crosses_midnight_records_both_days_the_pages_were_asked_for(tmp_path):
    import os

    fetch = tool()
    fetcher = fetch.Fetcher(tmp_path, 1.0)
    for name, stamp in (("a", 1_791_500_000), ("b", 1_791_540_000)):          # 2026-10-08 and 2026-10-09 (UTC)
        page_file = fetcher.path_of(f"/{name}/").with_suffix(".html")
        page_file.write_text("<p>x</p>", encoding="utf-8")
        os.utime(page_file, (stamp, stamp))
    assert fetcher.get("/a/") == "<p>x</p>" and fetch.retrieved(fetcher) == "2026-10-08"
    assert fetcher.get("/b/") == "<p>x</p>" and fetch.retrieved(fetcher) == "2026-10-08 to 2026-10-09"
    assert fetcher.network == 0


# --- reading a code ----------------------------------------------------------------------------------------


def test_a_learned_code_is_taken_as_the_page_gives_it_and_a_malformed_one_is_refused_with_its_reason():
    assert ri.read_code("Power", NEC.lower()) == (0x6D, False, NEC)
    assert ri.read_code("Power", "0000 006D 0001 0001 0155 00AA 0015 0E47")[:2] == (0x6D, True)
    for label, hex_, reason in (
            ("", NEC, "a code with no function name"),
            ("Power", "see manual", "the hex is not four-digit Pronto words"),
            ("Power", "0000 006D 0000", "fewer words than a header and one burst pair"),
            ("Power", NEC.replace("0000 006D", "0100 006D", 1), "Pronto type 0100, not the learned modulated type 0000"),
            ("Power", NEC + " 0015", "the length of the string is not the one its header gives"),
            ("Power", SHORT.replace("006D", "0000"), "a frequency word of 0")):
        assert ri.read_code(label, hex_) == reason, hex_


def test_the_title_is_the_model_without_the_brand_it_begins_with():
    assert ri.model_name("Denon", "Denon AVC-3020") == "AVC-3020"
    assert ri.model_name("3M", "3M  MP-8660") == "MP-8660"
    assert ri.model_name("Denon", "Denon") == "Denon" and ri.model_name("Denon", "Receiver") == "Receiver"


# --- the snapshot ------------------------------------------------------------------------------------------


def write_snapshot(directory: Path, brands: list[dict], retrieved: str = "2026-10-08", notes=()) -> Path:
    fetch = tool()
    directory.mkdir(parents=True, exist_ok=True)
    entries = {}
    for data in brands:
        name, entry = fetch.write_brand(directory, data)
        entries[name] = entry
    (directory / ri.MANIFEST).write_text(json.dumps({"retrieved": retrieved, "announced": None, "brands": entries, "notes": list(notes)}))
    return directory


def brand_of(name, slug, models):
    return {"format": 1, "brand": name, "slug": slug, "announced": len(models), "models": models}


def model_of(slug, title, groups):
    return {"slug": slug, "title": title, "pages": 1, "announced": None, "label": title,
            "groups": [{"remote": remote, "rows": [[label, hex_, 1] for label, hex_ in rows]} for remote, rows in groups]}


def test_a_changed_brand_file_or_one_the_manifest_does_not_list_stops_the_import(tmp_path):
    directory = write_snapshot(tmp_path / "snap", [brand_of("B", "b", [])])
    assert ri.load_snapshot(directory).retrieved == "2026-10-08"
    with (directory / "b.json.gz").open("wb") as handle:
        handle.write(gzip.compress(b'{"brand":"B","slug":"b","models":[1]}'))
    with pytest.raises(ValidationError, match="not the file the manifest pins"):
        ri.load_snapshot(directory)
    write_snapshot(directory, [brand_of("B", "b", [])])
    (directory / "other.json.gz").write_bytes(gzip.compress(b"{}"))
    with pytest.raises(ValidationError, match="the manifest does not list: other.json.gz"):
        ri.load_snapshot(directory)
    (directory / "other.json.gz").unlink()
    (directory / "b.json.gz").write_bytes(b"not gzip")
    with pytest.raises(ValidationError, match="unreadable"):
        ri.load_snapshot(directory)
    with pytest.raises(ValidationError, match="cannot read the snapshot manifest"):
        ri.load_snapshot(tmp_path / "nowhere")


# --- the importer ------------------------------------------------------------------------------------------


def docs_of(tmp_path, brands, authored=None):
    directory = write_snapshot(tmp_path / "snap", brands)
    docs, report = ri.import_tree(tmp_path, directory, authored or {})
    return docs, report


def test_a_model_page_becomes_a_file_of_untested_pronto_keys_that_cite_their_page(tmp_path):
    other = NEC.replace("0015 0040", "0015 0015")
    model = model_of("avc-3020", "Denon AVC-3020", [("RC-134", [("Power", NEC), ("Vol+", other), ("Vol+", NEC), ("Power", NEC.lower())])])
    model["groups"][0]["rows"][2][2] = 2                                     # the third code is on page 2
    docs, report = docs_of(tmp_path, [brand_of("Denon", "denon", [model])])
    assert list(docs) == ["remotes/remotecentral/denon/AVC-3020.json"]
    doc = docs["remotes/remotecentral/denon/AVC-3020.json"]
    assert (doc["manufacturer"], doc["model"], doc["aliases"], doc["controls"]) == ("Denon", "AVC-3020", [], ["Denon AVC-3020"])
    assert doc["protocol"] == {"carrierHz": 38029, "minSends": 1}
    assert list(doc["keys"]) == ["KEY_POWER", "KEY_VOL_PLUS", "KEY_VOL_PLUS_2"]      # the same code twice under one name is one key
    first, third = doc["keys"]["KEY_POWER"]["forms"][0], doc["keys"]["KEY_VOL_PLUS_2"]["forms"][0]
    assert (first["type"], first["hex"], first["confidence"]) == ("pronto", NEC, "untested")
    sha = ri.load_snapshot(tmp_path / "snap").sha("denon.json.gz")
    assert first["source"] == (f"remotecentral.com/cgi-bin/codes/denon/avc-3020/ (snapshot denon.json.gz@{sha}, "
                               "retrieved 2026-10-08) remote model 'RC-134', function 'Power'")
    assert "/avc-3020/page-2/ (snapshot" in third["source"]
    assert doc["keys"]["KEY_VOL_PLUS_2"]["label"] == "Vol+"
    assert report.unrepresented == {("remotecentral", "denon.json.gz", "the same code listed again under the same name"): 1}
    assert dict(report.counts["remotecentral"]) == {"models read": 1, "codes read": 4, "keys": 3, "files": 1}


def test_codes_of_other_carriers_and_intros_go_to_files_of_their_own_and_a_bad_code_is_counted(tmp_path):
    word = lambda w, intro=False: NEC.replace("006D", w).replace("0000 0004", "0001 0003" if intro else "0000 0004")
    model = model_of("m", "B Model", [("RC", [("A", NEC), ("B", NEC.replace("0040", "0041")), ("C", word("006B")), ("D", word("006D", True)),
                                               ("E", SHORT + " 0020"), ("F", "garbage")])])
    docs, report = docs_of(tmp_path, [brand_of("B", "b", [model])])
    assert sorted(d["model"] for d in docs.values()) == ["Model", "Model [38.0 kHz, intro]", "Model [38.7 kHz]"]
    assert {d["model"]: list(d["keys"]) for d in docs.values()} == {
        "Model": ["KEY_A", "KEY_B"], "Model [38.7 kHz]": ["KEY_C"], "Model [38.0 kHz, intro]": ["KEY_D"]}
    assert {d["model"]: d["protocol"]["carrierHz"] for d in docs.values()}["Model"] == ri._word_to_hz(0x6D)
    reasons = {r: n for (_m, _b, r), n in report.unrepresented.items()}
    assert reasons == {"the length of the string is not the one its header gives": 1, "the hex is not four-digit Pronto words": 1}


def test_a_string_the_compile_gate_refuses_is_reported_with_the_gates_reason(tmp_path):
    zero_on = "0000 006C 0000 0002 0000 0099 0000 0017"
    model = model_of("m", "B Model", [("RC", [("Power", zero_on), ("Play", NEC)])])
    docs, report = docs_of(tmp_path, [brand_of("B", "b", [model])])
    assert [list(d["keys"]) for d in docs.values()] == [["KEY_PLAY"]]
    (reason, count), = [(r, n) for (_m, _b, r), n in report.unrepresented.items()]
    assert reason.startswith("the hex does not compile: repeat[0] is 0; D28 requires") and count == 1


def test_two_pages_that_name_one_model_alike_get_distinct_names_and_an_authored_name_wins(tmp_path):
    models = [model_of("a", "B Model", [("RC", [("Power", NEC)])]), model_of("b", "B MODEL", [("RC", [("Play", NEC)])])]
    docs, report = docs_of(tmp_path, [brand_of("B", "b", models)])
    assert sorted(d["model"] for d in docs.values()) == ["MODEL (2)", "Model"]
    assert len(docs) == 2
    docs, report = docs_of(tmp_path / "again", [brand_of("B", "b", models)], authored={("b", "model"): "remotes/b/Model.json"})
    assert [d["model"] for d in docs.values()] == ["MODEL (2)"] and len(report.collisions) == 1


def test_names_that_make_one_path_are_told_apart_too(tmp_path):
    models = [model_of("a", "B 52CC(0)", [("RC", [("Power", NEC)])]), model_of("b", "B 52CC_0", [("RC", [("Play", NEC)])])]
    docs, _ = docs_of(tmp_path, [brand_of("B", "b", models)])
    assert sorted(d["model"] for d in docs.values()) == ["52CC(0)", "52CC_0 (2)"]
    assert sorted(docs) == ["remotes/remotecentral/b/52CC_0.json", "remotes/remotecentral/b/52CC_0__2.json"]


def test_a_page_with_no_usable_code_makes_no_file(tmp_path):
    docs, report = docs_of(tmp_path, [brand_of("B", "b", [model_of("m", "B Model", [("RC", [("Power", "garbage")])])])])
    assert docs == {} and report.keys == 0


# --- the committed tree --------------------------------------------------------------------------------------


def test_the_committed_import_is_exactly_what_the_pinned_snapshot_yields(tmp_path):
    """SPEC R19.5: re-running the import over the pinned snapshot reproduces every file byte for byte."""
    from remote_ledger.import_common import authored_names

    root = tmp_path / "repo"
    (root / "remotes").mkdir(parents=True)
    # the other imports' names, which a page of this one may not share (ten do), are the real tree's
    ri.write_import(root, SNAPSHOT, authored_names(ROOT, ri.IMPORT_ROOT))
    committed = {p.relative_to(ROOT / ri.IMPORT_ROOT).as_posix(): p.read_bytes()
                 for p in (ROOT / ri.IMPORT_ROOT).rglob("*") if p.is_file() and p.name != "README.md"}
    fresh = {p.relative_to(root / ri.IMPORT_ROOT).as_posix(): p.read_bytes()
             for p in (root / ri.IMPORT_ROOT).rglob("*") if p.is_file()}
    assert fresh == committed


def test_the_committed_remotecentral_import_keeps_r19():
    """Conditions 2 and 3 over every form: README and report beside it, one form a key, Untested, and a
    citation that names a page of the site and the snapshot file it is pinned in, by hash."""
    assert (ROOT / ri.IMPORT_ROOT / "README.md").is_file() and (ROOT / ri.IMPORT_ROOT / ri.REPORT).is_file()
    manifest = json.loads((SNAPSHOT / ri.MANIFEST).read_text(encoding="utf-8"))
    shape = re.compile(r"^remotecentral\.com/cgi-bin/codes/([^/]+)/([^/]+)/(?:page-\d+/)? \(snapshot (\S+\.json\.gz)@([0-9a-f]{8}), "
                       r"retrieved (\d{4}-\d{2}-\d{2}(?: to \d{4}-\d{2}-\d{2})?)\) (?:remote model '.*', )?function '.*'$")
    files = sorted((ROOT / ri.IMPORT_ROOT).rglob("*.json"))
    assert len(files) > 1000
    bad = []
    for path in files:
        for key, spec in load(path)["keys"].items():
            if len(spec["forms"]) != 1:
                bad.append(f"{path.name}:{key}")
            for form in spec["forms"]:
                m = shape.match(form.get("source", ""))
                ok = (form["confidence"] == "untested" and "verifiedBy" not in form and form["type"] == "pronto"
                      and m is not None and manifest["brands"].get(m[3], {}).get("sha256", "").startswith(m[4])
                      and m[5] == manifest["retrieved"])
                if not ok:
                    bad.append(f"{path.name}:{key}")
    assert bad == []


def test_a_code_of_the_committed_tree_is_the_string_the_page_gave():
    """Denon AVC-3020's Power, read by eye from https://www.remotecentral.com/cgi-bin/codes/denon/avc-3020/ on
    2026-10-08: the strings are the pages' own, in upper case, and the carrier is their frequency word's."""
    doc = load(ROOT / ri.IMPORT_ROOT / "denon" / "AVC-3020.json")
    assert (doc["model"], doc["controls"], doc["aliases"]) == ("AVC-3020", ["Denon AVC-3020"], [])
    form = doc["keys"]["KEY_POWER"]["forms"][0]
    assert form["hex"].startswith("0000 006E 0000 0020 000A 001D 000A 0044 0009 001D 000A 001D 000A 001D 000A 0044")
    assert doc["protocol"] == {"carrierHz": ri._word_to_hz(0x6E), "minSends": 1}
    assert form["source"].endswith("remote model 'RC-134', function 'Power'") and "/avc-3020/ (snapshot denon.json.gz@" in form["source"]


def test_design_quotes_the_numbers_the_import_report_prints():
    """DESIGN section 30 states what the import did; each figure is read from ``IMPORT.md`` and the snapshot
    here, so that the text cannot go stale."""
    report = (ROOT / ri.IMPORT_ROOT / ri.REPORT).read_text(encoding="utf-8")
    total = lambda what: int(re.search(rf"\| remotecentral: {what} \| ([\d,]+) \|", report)[1].replace(",", ""))
    read, files, keys, skipped, pages = (total(w) for w in ("codes read", "files", "keys", "keys of the skipped pages", "models read"))
    manifest = json.loads((SNAPSHOT / ri.MANIFEST).read_text(encoding="utf-8"))
    assert (pages, read) == (sum(b["models"] for b in manifest["brands"].values()), sum(b["codes"] for b in manifest["brands"].values()))
    section = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    section = section[section.index("## 30. Importing RemoteCentral"):]
    skipped_pages = int(re.search(r"## Pages skipped because [^\n]*\((\d+)\)\n", report)[1])
    not_imported = read - keys - skipped
    for fact in (f"{files:,} files and {keys:,} keys", f"{read:,} codes",
                 f"**{not_imported:,} of the {read:,} codes ({not_imported / read:.1%}) are not imported**",
                 f"{manifest['announced']['brands']} and {pages:,} were found", f"({skipped_pages} pages, {skipped} keys"):
        assert fact in section, fact
    for name, entry in manifest["brands"].items():
        assert entry["models"] > 0 and entry["codes"] > 0, name
    assert sum(b["bytes"] for b in manifest["brands"].values()) / 1e6 == pytest.approx(23.9, abs=0.05)
