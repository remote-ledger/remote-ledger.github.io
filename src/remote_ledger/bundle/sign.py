"""Signing the manifest and checking the signature (D93).

**Scheme.** ECDSA over the curve P-256 with SHA-256, the signature DER encoded,
detached: ``manifest.sig`` is the signature of the bytes of ``manifest.json``, and
the manifest holds the SHA-256 of the bundle, so one signature covers the bundle,
the notices and the manifest's own fields. It is chosen because every Android
version has it (``Signature.getInstance("SHA256withECDSA")``; Ed25519 is only on
newer ones) and because the ``openssl`` command line makes and checks it, so
nothing here is cryptography written for this project: the two commands below run
``openssl dgst`` and say so when it is missing.

**Key id.** The first sixteen hex digits of the SHA-256 of the public key's
DER-encoded SubjectPublicKeyInfo. It is written into the manifest before the
manifest is signed (``signature.keyId``), so the signature covers the claim of who
signed. An app keeps a set of trusted public keys by id; a rotation is a new key
with a new id, shipped in the app before the first bundle signed with it, and a
manifest naming a key the app does not hold is rejected.

**Custody.** The private key is never in this repository (``.gitignore`` keeps
``*.pem`` out as a net), never in a bundle and never in a test: a test makes a
throwaway one.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from ..app_api import compact
from ..errors import ValidationError
from .build import BUNDLE_FILE, MANIFEST_FILE, NOTICES_FILE, SIGNATURE_FILE

ALGORITHM = "ECDSA-P256-SHA256"
SIGNATURE_FORMAT = "DER"
KEY_ID_DIGITS = 16


def openssl() -> str:
    """The ``openssl`` executable, or the reason there is none."""
    found = shutil.which("openssl")
    if found is None:
        raise ValidationError(
            "signing and verifying use the openssl command line, which was not found on PATH; "
            "install OpenSSL (any version with `dgst -sha256 -sign`) and run the command again")
    return found


def _run(args: list[str], what: str, input_bytes: bytes | None = None) -> bytes:
    try:
        done = subprocess.run([openssl(), *args], input=input_bytes, capture_output=True,
                              timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError(f"openssl could not be run to {what}: {exc}") from None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).decode("utf-8", "replace").strip()
        raise ValidationError(f"openssl failed to {what}: {detail}")
    return done.stdout


def key_id(key_file: Path, *, public: bool) -> str:
    """The id of a key: a private key file gives its public key's id."""
    args = ["pkey", *(["-pubin"] if public else []), "-in", str(key_file), "-pubout",
            "-outform", "DER"]
    der = _run(args, f"read {key_file}")
    return hashlib.sha256(der).hexdigest()[:KEY_ID_DIGITS]


def _require_p256(key_file: Path, *, public: bool) -> None:
    text = _run(["pkey", *(["-pubin"] if public else []), "-in", str(key_file), "-noout",
                 "-text"], f"read {key_file}").decode("utf-8", "replace")
    if "prime256v1" not in text and "P-256" not in text:
        raise ValidationError(
            f"{key_file} is not an ECDSA P-256 key, which is the one scheme of the bundle "
            "(`openssl ecparam -name prime256v1 -genkey -noout -out key.pem`)")


def sign_directory(directory: Path, key_file: Path) -> str:
    """Add ``signature`` to the manifest of ``directory`` and write ``manifest.sig``.
    Returns the key id."""
    manifest_path = directory / MANIFEST_FILE
    if not manifest_path.is_file():
        raise ValidationError(f"{manifest_path}: no manifest to sign; build a bundle first")
    if not key_file.is_file():
        raise ValidationError(f"{key_file}: no such key file")
    _require_p256(key_file, public=False)
    identifier = key_id(key_file, public=False)
    manifest = json.loads(manifest_path.read_bytes())
    manifest["signature"] = {
        "algorithm": ALGORITHM, "file": SIGNATURE_FILE, "format": SIGNATURE_FORMAT,
        "keyId": identifier,
    }
    manifest_path.write_bytes(compact(manifest))
    _run(["dgst", "-sha256", "-sign", str(key_file), "-out", str(directory / SIGNATURE_FILE),
          str(manifest_path)], "sign the manifest")
    return identifier


def verify_signature(directory: Path, public_key: Path) -> list[str]:
    """Problems with the signature and with what it covers; none means the bundle is
    the one the key signed: the signature is valid over ``manifest.json``, the key is
    the one the manifest names, and the bundle and notices are the bytes it lists."""
    manifest_path = directory / MANIFEST_FILE
    signature_path = directory / SIGNATURE_FILE
    for path in (manifest_path, signature_path):
        if not path.is_file():
            return [f"{path}: missing"]
    if not public_key.is_file():
        return [f"{public_key}: no such key file"]
    try:
        _require_p256(public_key, public=True)
        identifier = key_id(public_key, public=True)
        _run(["dgst", "-sha256", "-verify", str(public_key), "-signature", str(signature_path),
              str(manifest_path)], "verify the signature")
    except ValidationError as exc:
        return [f"{signature_path}: not a valid signature of {MANIFEST_FILE} by {public_key} ({exc})"]
    manifest = json.loads(manifest_path.read_bytes())
    problems = []
    named = (manifest.get("signature") or {}).get("keyId")
    if named != identifier:
        problems.append(f"{MANIFEST_FILE} names the key {named!r}, this key is {identifier!r}")
    for part, default in (("bundle", BUNDLE_FILE), ("notices", NOTICES_FILE)):
        entry = manifest.get(part) or {}
        path = directory / entry.get("file", default)
        if not path.is_file():
            problems.append(f"{path}: missing")
            continue
        data = path.read_bytes()
        if len(data) != entry.get("bytes"):
            problems.append(f"{path}: {len(data):,} bytes, the manifest says {entry.get('bytes')}")
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            problems.append(f"{path}: its SHA-256 is not the manifest's")
    return problems
