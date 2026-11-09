#!/usr/bin/env python3
"""The vault: F.R.I.D.A.Y.'s private artifact store for files that cannot live in the public repo.

Blobs live on the VM under <root>/sha256/<hex> (root: FRIDAY_VAULT, default /srv/friday/vault). The repo keeps only
vault/manifest.json: {"files": {name: {"sha256", "size", "source"}}}. Builds fetch by name and check the hash.

  vault.py put FILE NAME [--source REL]     store FILE under NAME (prints the sha256)
  vault.py fetch NAME DEST                  copy NAME to DEST (hash checked)
  vault.py verify                           re-hash every manifest entry (exit 1 on any bad entry)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(os.path.dirname(HERE), "vault", "manifest.json")


class VaultError(Exception):
    pass


def _root(root):
    return root or os.environ.get("FRIDAY_VAULT", "/srv/friday/vault")


def _manifest(manifest):
    return manifest or DEFAULT_MANIFEST


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(manifest: str) -> dict:
    if not os.path.exists(manifest):
        return {"files": {}}
    with open(manifest, encoding="utf-8") as f:
        return json.load(f)


def _save(manifest: str, data: dict) -> None:
    os.makedirs(os.path.dirname(manifest) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(manifest) or ".")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, manifest)


def put(path: str, name: str, root: str | None = None, manifest: str | None = None, source: str | None = None) -> str:
    root, manifest = _root(root), _manifest(manifest)
    sha = _sha(path)
    blob = os.path.join(root, "sha256", sha)
    os.makedirs(os.path.dirname(blob), exist_ok=True)
    if not os.path.exists(blob):
        shutil.copyfile(path, blob + ".tmp")
        os.replace(blob + ".tmp", blob)
    src = (source or os.path.basename(path)).lstrip("/")
    data = _load(manifest)
    data.setdefault("files", {})[name] = {"sha256": sha, "size": os.path.getsize(path), "source": src}
    _save(manifest, data)
    return sha


def fetch(name: str, dest: str, root: str | None = None, manifest: str | None = None) -> None:
    root, entry = _root(root), _load(_manifest(manifest)).get("files", {}).get(name)
    if entry is None:
        raise VaultError(f"{name}: not in the manifest")
    blob = os.path.join(root, "sha256", entry["sha256"])
    if not os.path.exists(blob):
        raise VaultError(f"{name}: missing from the vault")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".vault-tmp"
    shutil.copyfile(blob, tmp)
    if _sha(tmp) != entry["sha256"]:
        os.remove(tmp)
        raise VaultError(f"{name}: hash mismatch")
    os.replace(tmp, dest)


def fetch_all(names: list[str], dest_dir: str, root: str | None = None, manifest: str | None = None) -> None:
    written = []
    try:
        for name in names:
            dest = os.path.join(dest_dir, name)
            fetch(name, dest, root=root, manifest=manifest)
            written.append(dest)
    except VaultError:
        for p in written:
            try:
                os.remove(p)
            except OSError:
                pass
        raise


def verify(root: str | None = None, manifest: str | None = None) -> list[str]:
    root, bad = _root(root), []
    for name, entry in sorted(_load(_manifest(manifest)).get("files", {}).items()):
        blob = os.path.join(root, "sha256", entry["sha256"])
        if not os.path.exists(blob) or _sha(blob) != entry["sha256"]:
            bad.append(name)
    return bad


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if a[:1] in (["put"], ["fetch"], ["verify"]) and not os.path.isdir(_root(None)):
        print(f"vault root {_root(None)} does not exist (set FRIDAY_VAULT; the vault lives on the VM)", file=sys.stderr)
        return 2
    if a[:1] == ["put"] and len(a) >= 3:
        src = a[a.index("--source") + 1] if "--source" in a else None
        print(put(a[1], a[2], source=src))
    elif a[:1] == ["fetch"] and len(a) == 3:
        fetch(a[1], a[2])
    elif a[:1] == ["verify"]:
        bad = verify()
        for n in bad:
            print(f"BAD {n}")
        print(f"{len(_load(_manifest(None)).get('files', {})) - len(bad)} ok, {len(bad)} bad")
        return 1 if bad else 0
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
