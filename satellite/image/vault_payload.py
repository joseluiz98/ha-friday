#!/usr/bin/env python3
"""Fills the image payload with the files that live in the vault (vendor file, sounds, models, HA public key,
Sendspin), all or nothing. Runs on the VM, where the vault is.

  vault_payload.py PAYLOAD_DIR [--manifest vault/manifest.json]

Sounds land flat in programa/sons/ (top level first; the 12/10 snapshot only fills names the top level lacks) and
the first-boot phrases in programa/sons/onboarding/, as the production Pi has them."""
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_VAULT = os.environ.get("FRIDAY_VAULT_PY") or next(
    p for p in (os.path.join(HERE, "vault.py"), os.path.join(HERE, "..", "..", "scripts", "vault.py")) if os.path.exists(p))
_spec = importlib.util.spec_from_file_location("vault", _VAULT)
vault = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vault)

SNAPSHOT = "satellite/sounds/atuais-20261012/"
FIXED = {"satellite/program/vendor/": "programa/vendor/", "satellite/sounds/onboarding/": "programa/sons/onboarding/",
         SNAPSHOT: "programa/sons/", "satellite/sounds/": "programa/sons/", "satellite/models/": "modelos/",
         "satellite/sendspin/": "sendspin/"}


def payload_path(name: str):
    if name == "satellite/image/ha_authorized_key.pub":
        return "ha_authorized_key.pub"
    for prefix, dest in FIXED.items():
        if name.startswith(prefix):
            rest = name[len(prefix):]
            return dest + rest if "/" not in rest else None
    return None


REQUIRED = ("satellite/program/vendor/", "satellite/sounds/", "satellite/sounds/onboarding/", "satellite/models/",
            "satellite/sendspin/", "satellite/image/ha_authorized_key.pub")


def fill(payload_dir: str, root=None, manifest=None, required=REQUIRED) -> int:
    names = sorted(vault._load(vault._manifest(manifest)).get("files", {}))
    wanted = [n for n in names if payload_path(n)]
    empty = [p for p in required if not any(n.startswith(p) for n in wanted)]
    if empty:  # the image never ships without one of its private sets
        raise vault.VaultError("vault manifest has nothing under " + ", ".join(empty))
    tmp = tempfile.mkdtemp()
    try:
        vault.fetch_all(wanted, tmp, root=root, manifest=manifest)
        ordered = [n for n in wanted if not n.startswith(SNAPSHOT)] + [n for n in wanted if n.startswith(SNAPSHOT)]
        for n in ordered:
            dest = os.path.join(payload_dir, payload_path(n))
            if n.startswith(SNAPSHOT) and os.path.exists(dest):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(os.path.join(tmp, n), dest)
        return len(wanted)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    man = a[a.index("--manifest") + 1] if "--manifest" in a else None
    print(f"vault: {fill(a[0], manifest=man)} file(s) into {a[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
