#!/usr/bin/env python3
"""Drift check: does a host run exactly what a version of the repo says? (run on HAOS, read-only on the hosts)

  scripts/drift.py <host> <git-ref>      host: vm | ha | mk1 | mk2 | user@address (a satellite)
Exit 1 on any difference. Each line: <install dir>/<file>  changed | missing | extra.
Ignored everywhere: __pycache__, *.pyc, venv/, FRIDAY_VERSION, *.bak*. Brain skills are rendered from local config,
so only their presence is checked by the install script, not here.
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
IGNORE_DIRS = {"__pycache__", "venv", ".venv"}


def _ignored(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (any(p in IGNORE_DIRS for p in parts[:-1]) or name == "FRIDAY_VERSION" or name.endswith(".pyc")
            or ".bak" in name)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def expected(files: dict, prefix: str, skip_tests: bool = False, skip=(), only_ext=None, flat: bool = False) -> dict:
    """{path relative to the install dir: sha256} for repo files under `prefix`."""
    out = {}
    for path, data in files.items():
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        name = rel.rsplit("/", 1)[-1]
        if (flat and "/" in rel) or name in skip or (skip_tests and name.startswith("test_")) or _ignored(rel):
            continue
        if only_ext and not name.endswith(only_ext):
            continue
        out[rel] = _sha(data)
    return out


def drift(expected_tree: dict, installed_root: str, check_extra: bool = True) -> list:
    found = {}
    for dp, dn, fn in os.walk(installed_root):
        dn[:] = [d for d in dn if d not in IGNORE_DIRS]
        for name in fn:
            full = os.path.join(dp, name)
            rel = os.path.relpath(full, installed_root).replace(os.sep, "/")
            if _ignored(rel):
                continue
            with open(full, "rb") as f:
                found[rel] = _sha(f.read())
    diffs = []
    for rel, sha in expected_tree.items():
        if rel not in found:
            diffs.append((rel, "missing"))
        elif found[rel] != sha:
            diffs.append((rel, "changed"))
    if check_extra:
        diffs += [(rel, "extra") for rel in found if rel not in expected_tree]
    return sorted(diffs)


def _repo_files(ref: str) -> dict:
    raw = subprocess.run(["git", "-C", REPO, "archive", "--format=tar", ref], check=True, capture_output=True).stdout
    out = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        for m in t.getmembers():
            if m.isfile():
                out[m.name] = t.extractfile(m).read()
    return out


def _vm() -> str:
    with open("/config/.friday/vm", encoding="utf-8") as f:
        return f.read().strip()


# (repo prefix, install dir, options, check extras)
PLANS = {
    "vm": [("voice_gateway/", "/srv/friday/app", dict(skip=("install.sh",)), False),
           *[(f"server/{d}/", f"/opt/friday/{d}", {}, False) for d in ("status", "essentials", "cataloger", "cost", "aec", "asr", "image")],
           ("agents/server/friday_server_agent/", "/opt/friday/agent/friday_server_agent", {}, True),
           ("agents/common/friday_agent_core/", "/opt/friday/agent/friday_agent_core", {}, True),
           ("tools/wakeword/training/", "/opt/friday/wwt/training", dict(skip_tests=True), False)],
    "ha": [("custom_components/friday/", "/config/custom_components/friday", {}, True)],
    "satellite": [("satellite/program/", "/home/jose/satelite", dict(skip_tests=True, only_ext=".py", flat=True), False),
                  ("agents/satellite/friday_satellite_agent/", "/opt/friday-satellite-agent/friday_satellite_agent", {}, True),
                  ("agents/common/friday_agent_core/", "/opt/friday-satellite-agent/friday_agent_core", {}, True)],
}


def _fetch(target: str | None, path: str, dest: str) -> bool:
    if target is None:
        if not os.path.isdir(path):
            return False
        subprocess.run(["cp", "-a", path + "/.", dest], check=True)
        return True
    cmd = f"sudo -n tar -C {path} -cf - . 2>/dev/null || tar -C {path} -cf - ."
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", target, cmd], capture_output=True)
    if r.returncode != 0 and not r.stdout:
        return False
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
        t.extractall(dest, filter="data")
    return True


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    host, ref = a[0], a[1]
    files = _repo_files(ref)
    if host in ("vm", "ha"):
        plan, target = PLANS[host], (_vm() if host == "vm" else None)
    else:
        plan, target = PLANS["satellite"], {"mk1": "jose@" + os.environ.get("FRIDAY_MK1", ""), "mk2": "jose@" + os.environ.get("FRIDAY_MK2", "")}.get(host, host)
    bad = 0
    for prefix, path, opts, extra in plan:
        exp = expected(files, prefix, **opts)
        with tempfile.TemporaryDirectory() as tmp:
            if not _fetch(target, path, tmp):
                print(f"{path}: unreadable or absent")
                bad += 1
                continue
            for rel, kind in drift(exp, tmp, check_extra=extra):
                print(f"{path}/{rel}  {kind}")
                bad += 1
    print(f"drift {host} vs {ref}: {'clean' if not bad else f'{bad} difference(s)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
