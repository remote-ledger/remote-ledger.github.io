"""Signing the manifest and checking the signature (D93).

The scheme is ECDSA P-256 with SHA-256 and a DER signature, made and checked by the
``openssl`` command line. The tests make throwaway keys of their own and never read, let
alone write, a key of the repository. Where ``openssl`` is not installed they are skipped
with a reason (``-rs`` prints it) and the run says so in its warnings summary: a missing
tool is never a pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest

from bundle_corpus import floor_of_the_small_ledgers, make_corpus
from remote_ledger import app_api, cli, parallel
from remote_ledger.bundle import build as bb
from remote_ledger.bundle import sign
from remote_ledger.errors import ValidationError

ROOT = Path(__file__).resolve().parent.parent
OPENSSL = shutil.which("openssl")
REASON = "the openssl command line is not installed, so signing cannot be tested here"
if OPENSSL is None:
    warnings.warn(f"tests/test_bundle_sign.py: {REASON}", stacklevel=1)
needs_openssl = pytest.mark.skipif(OPENSSL is None, reason=REASON)


@pytest.fixture(autouse=True)
def _clean_parallel_state(monkeypatch):
    monkeypatch.delenv(parallel.ENV_JOBS, raising=False)
    parallel.configure(None)
    yield
    parallel.configure(None)


def openssl(*args: str) -> None:
    subprocess.run([OPENSSL, *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """Two throwaway P-256 keys, and an Ed25519 and an RSA one that must be refused."""
    if OPENSSL is None:
        pytest.skip(REASON)
    directory = tmp_path_factory.mktemp("keys")
    out = {}
    for name, make in {
        "a": ["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out"],
        "b": ["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out"],
        "ed25519": ["genpkey", "-algorithm", "ed25519", "-out"],
        "rsa": ["genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out"],
    }.items():
        private, public = directory / f"{name}.pem", directory / f"{name}.pub.pem"
        openssl(*make, str(private))
        openssl("pkey", "-in", str(private), "-pubout", "-out", str(public))
        out[name] = (private, public)
    return out


@pytest.fixture(scope="module")
def good(tmp_path_factory):
    root = make_corpus(tmp_path_factory.mktemp("sign") / "ledger")
    with floor_of_the_small_ledgers():
        built = bb.build_bundle(root, "selected")
    directory = tmp_path_factory.mktemp("signed-source")
    bb.write_bundle(built, directory, root)
    return directory


@pytest.fixture
def bundle(good, tmp_path):
    directory = tmp_path / "bundle"
    shutil.copytree(good, directory)
    return directory


@needs_openssl
def test_a_signed_bundle_verifies_and_the_manifest_names_the_key(bundle, keys):
    private, public = keys["a"]
    identifier = sign.sign_directory(bundle, private)
    assert len(identifier) == 16 and int(identifier, 16) >= 0
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["signature"] == {
        "algorithm": "ECDSA-P256-SHA256", "file": "manifest.sig", "format": "DER", "keyId": identifier}
    assert sign.key_id(public, public=True) == identifier == sign.key_id(private, public=False)
    assert sign.key_id(keys["b"][1], public=True) != identifier
    assert sign.verify_signature(bundle, public) == []


@needs_openssl
def test_the_signature_is_a_der_ecdsa_signature_of_the_manifest_bytes(bundle, keys):
    private, public = keys["a"]
    sign.sign_directory(bundle, private)
    der = (bundle / "manifest.sig").read_bytes()
    assert der[0] == 0x30 and der[1] == len(der) - 2 and der[2] == 0x02   # SEQUENCE { INTEGER r, ...
    assert 68 <= len(der) <= 72                                            # two integers of 32 bytes
    # the exact command a reader would run
    done = subprocess.run([OPENSSL, "dgst", "-sha256", "-verify", str(public), "-signature",
                           str(bundle / "manifest.sig"), str(bundle / "manifest.json")],
                          capture_output=True, text=True)
    assert done.returncode == 0 and "Verified OK" in done.stdout


@needs_openssl
def test_a_changed_manifest_bundle_or_notices_is_rejected(bundle, keys):
    private, public = keys["a"]
    sign.sign_directory(bundle, private)
    original = {n: (bundle / n).read_bytes() for n in ("manifest.json", "catalog.sqlite", "notices.json")}

    manifest = json.loads(original["manifest.json"])
    manifest["dataVersion"] = "000000000000"
    (bundle / "manifest.json").write_bytes(app_api.compact(manifest))
    got = sign.verify_signature(bundle, public)
    assert len(got) == 1 and "not a valid signature of manifest.json" in got[0]
    (bundle / "manifest.json").write_bytes(original["manifest.json"])

    for name in ("catalog.sqlite", "notices.json"):
        data = bytearray(original[name])
        data[len(data) // 2] ^= 0xFF
        (bundle / name).write_bytes(bytes(data))
        got = sign.verify_signature(bundle, public)
        assert any(f"{name}: its SHA-256 is not the manifest's" in p for p in got), name
        (bundle / name).write_bytes(original[name])
    (bundle / "catalog.sqlite").write_bytes(original["catalog.sqlite"] + b"\x00")
    assert any("catalog.sqlite: " in p and "the manifest says" in p
               for p in sign.verify_signature(bundle, public))
    (bundle / "catalog.sqlite").write_bytes(original["catalog.sqlite"])
    assert sign.verify_signature(bundle, public) == []


@needs_openssl
def test_a_signature_of_another_key_is_rejected(bundle, keys):
    sign.sign_directory(bundle, keys["a"][0])
    got = sign.verify_signature(bundle, keys["b"][1])
    assert got and "not a valid signature" in got[0]
    # a valid signature whose manifest names another key is not accepted either: put the other
    # key's id in the manifest and sign it again with the first key
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    manifest["signature"]["keyId"] = sign.key_id(keys["b"][1], public=True)
    (bundle / "manifest.json").write_bytes(app_api.compact(manifest))
    openssl("dgst", "-sha256", "-sign", str(keys["a"][0]), "-out", str(bundle / "manifest.sig"),
            str(bundle / "manifest.json"))
    got = sign.verify_signature(bundle, keys["a"][1])
    assert any("names the key" in p and "this key is" in p for p in got)


@needs_openssl
def test_an_unsigned_or_garbled_signature_is_rejected(bundle, keys):
    assert sign.verify_signature(bundle, keys["a"][1]) == [f"{bundle / 'manifest.sig'}: missing"]
    (bundle / "manifest.sig").write_bytes(b"\x30\x00")
    assert "not a valid signature" in sign.verify_signature(bundle, keys["a"][1])[0]


@needs_openssl
@pytest.mark.parametrize("name", ["ed25519", "rsa"])
def test_only_p256_is_accepted_for_signing_and_for_verifying(bundle, keys, name):
    private, public = keys[name]
    with pytest.raises(ValidationError, match="not an ECDSA P-256 key"):
        sign.sign_directory(bundle, private)
    assert not (bundle / "manifest.sig").exists()
    sign.sign_directory(bundle, keys["a"][0])
    assert "not a valid signature" in sign.verify_signature(bundle, public)[0]


@needs_openssl
def test_signing_needs_a_manifest_and_a_key_file(tmp_path, keys):
    with pytest.raises(ValidationError, match="no manifest to sign"):
        sign.sign_directory(tmp_path, keys["a"][0])
    (tmp_path / "manifest.json").write_text("{}")
    with pytest.raises(ValidationError, match="no such key file"):
        sign.sign_directory(tmp_path, tmp_path / "missing.pem")
    assert sign.verify_signature(tmp_path, tmp_path / "x.pem") == [f"{tmp_path / 'manifest.sig'}: missing"]


@needs_openssl
def test_the_commands_sign_and_verify_signature(bundle, keys, monkeypatch, capsys):
    private, public = keys["a"]
    monkeypatch.chdir(ROOT)
    assert cli.main(["bundle", "sign", "--key", str(private), str(bundle)]) == 0
    assert sign.key_id(private, public=False) in capsys.readouterr().out
    assert cli.main(["bundle", "verify-signature", "--pub", str(public), str(bundle)]) == 0
    assert "the signature is valid" in capsys.readouterr().out
    data = bytearray((bundle / "catalog.sqlite").read_bytes())
    data[100] ^= 1
    (bundle / "catalog.sqlite").write_bytes(bytes(data))
    assert cli.main(["bundle", "verify-signature", "--pub", str(public), str(bundle)]) == 1
    assert "its SHA-256 is not the manifest's" in capsys.readouterr().err
    assert cli.main(["bundle", "sign", "--key", str(bundle / "no-such.pem"), str(bundle)]) == 1
    assert "no such key file" in capsys.readouterr().err


def test_a_missing_openssl_is_a_clear_error_and_not_a_pass(bundle, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    key = tmp_path / "k.pem"
    key.write_text("x")
    with pytest.raises(ValidationError, match="openssl command line, which was not found on PATH"):
        sign.sign_directory(bundle, key)
    monkeypatch.chdir(ROOT)
    assert cli.main(["bundle", "sign", "--key", str(key), str(bundle)]) == 1
    assert "install OpenSSL" in capsys.readouterr().err
    assert not (bundle / "manifest.sig").exists()
    assert "signature" not in json.loads((bundle / "manifest.json").read_bytes())


def test_no_private_key_is_in_the_repository_and_none_can_be_added_by_accident():
    listing = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    assert [f for f in listing.split("\n") if f.endswith((".pem", ".key", ".p12", ".pfx"))] == []
    marker = "-----BEGIN [A-Z ]*" + "PRIVATE KEY-----"
    found = subprocess.run(
        ["git", "grep", "-l", "-E", "-e", marker, "--", ".", ":!tests/test_bundle_sign.py"],
        cwd=ROOT, capture_output=True, text=True)
    assert found.returncode == 1 and found.stdout == ""        # 1: no match
    ignore = (ROOT / ".gitignore").read_text()
    assert "*.pem" in ignore and "!*.pub.pem" in ignore
