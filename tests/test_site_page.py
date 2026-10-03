"""The page's own script, run under node against the site it was generated
with (D57).

The page used to hold the whole index. Now the imported database arrives after
the visitor starts typing, so for a moment the page does not know whether a
device is in the ledger -- and SPEC R20 forbids it to say "nobody has looked"
then. That is behaviour, not markup, and no test of the generated text can see
it, so the script is run: against a stand-in for the DOM, driven the way a
visitor would drive it, reading the shard's scripts from the generated site.

Skipped when node is not installed; the structure of the script is still
checked by test_site_shards.py.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from remote_ledger import site
from shard_corpus import make_corpus

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("page_harness.js")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is not installed, so the page's script is not run")


@pytest.fixture(scope="module")
def site_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("page")
    make_corpus(root)
    assert site.build_site(root, root) == []
    return root / "site"


def _parts(requested):
    """The scripts a page asked for that are shard parts, not remotes' own."""
    return [r for r in requested if r.startswith("index/")]


def _visit(site_dir, *unavailable):
    done = subprocess.run(
        [NODE, str(HARNESS), str(site_dir), *unavailable],
        capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def visit(site_dir):
    return _visit(site_dir)


def test_the_page_opens_without_fetching_the_imported_database(visit):
    first = visit["initial"]
    assert _parts(first["requested"]) == []
    assert "3 more are in the IR Blaster database" in first["count"]
    assert "searched when you type" in first["count"]


def test_a_search_says_it_is_still_searching_and_does_not_say_absent(visit):
    """The first thing a visitor reads about a device that only the imported
    database knows must not be R20's third state."""
    waiting = visit["shardOnlyWhileLoading"]
    assert sorted(_parts(waiting["requested"])) == [   # one script per part
        "index/irblaster/0.js", "index/irblaster/a.js", "index/irblaster/z.js"]
    assert "Still searching the IR Blaster database" in waiting["count"]
    assert "0 of 3 files loaded" in waiting["count"]
    assert "Nothing yet for" in waiting["results"]
    assert "nobody has looked up yet" not in waiting["results"]


def test_a_device_only_in_the_shard_reads_as_in_the_ledger(visit):
    found = visit["shardOnlyLoaded"]
    assert "ZENITH" in found["results"] and "IR Blaster DB 1 (NEC1)" in found["results"]
    assert "nobody has looked up yet" not in found["results"]
    assert "1 remote(s) (1 from the IR Blaster database" in found["count"]
    assert "Still searching" not in found["count"]
    assert "imported from the IR Blaster database" in found["results"]


def test_checked_and_not_found_is_unchanged(visit):
    assert "checked, nothing found" in visit["checkedNotFound"]["results"]


def test_absent_is_only_said_once_every_part_has_answered(visit):
    assert "nobody has looked up yet" in visit["absent"]["results"]
    assert "nobody has looked up yet" not in visit["absentWhileLoading"]["results"]
    assert "Nothing yet for" in visit["absentWhileLoading"]["results"]
    assert "nobody has looked up yet" in visit["absentLoaded"]["results"]


def test_an_empty_query_lists_the_page_again_and_not_the_shard(visit):
    cleared = visit["cleared"]
    assert "searched when you type" in cleared["count"]
    assert "IR Blaster DB" not in cleared["results"]


def test_a_shard_remote_opens_from_its_own_script(visit):
    assert "KEY_POWER" in visit["opened"]


def test_a_part_that_does_not_load_is_reported_not_read_as_nothing_found(site_dir):
    """The failure R20 exists to prevent, by another route: a network error
    must not turn a device in the ledger into one nobody has looked up."""
    lost = _visit(site_dir, "index/irblaster/a.js")
    absent = lost["absentLoaded"]
    assert "did not load in full (1 of 3 files missing)" in absent["count"]
    assert absent["retry"] is True
    assert "nobody has looked up yet" not in absent["results"]
    assert "does not show that nobody has looked up the device" \
        in " ".join(absent["results"].split())
    # What did load is still searched: ZENITH's remote is in another part.
    assert "IR Blaster DB 1 (NEC1)" in lost["shardOnlyLoaded"]["results"]
