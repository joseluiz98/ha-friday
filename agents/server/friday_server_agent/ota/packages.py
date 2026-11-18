"""Per-release packages built on the VM (spec 2026-11-10 §6.4, §16): immutable once written.

`satellite.tar.gz` is what a satellite installs (program, agent, migrations, system files, locks, the vault files the
program needs, FRIDAY_VERSION). It keeps the layout of the old `update` bundle (program/, agent/), so a v0.2.x agent
can still apply it through the frozen `apply_bundle kind=update` path and simply ignores the new folders.
`archive.tar` is the release tree the server-side installer runs from. Wheels live apart, cached by lock hash.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
from typing import Callable

from .. import provisioning as pv

# fallback for releases without package.satellite in ota/release.json (the release is the source)
SATELLITE_PATHS = [
    "satellite/program", "satellite/migrations", "satellite/audio", "satellite/sync",
    "agents/satellite/friday_satellite_agent", "agents/satellite/friday-satellite-agent.service",
    "agents/satellite/sudoers.friday-satellite-agent", "agents/satellite/friday-agent-apply",
    "agents/satellite/friday-wifi-apply", "agents/satellite/friday-ota-apply", "agents/satellite/requirements.lock",
    "agents/common/friday_agent_core", "vault/manifest.json", "ota/release.json",
]
SERVER_PATHS = ["agents", "server", "voice_gateway", "brain", "scripts", "tools/wakeword", "custom_components", "ota",
                "vault/manifest.json"]
# fallback for releases without package.satellite in ota/release.json (the release is the source)
# repo path (file or folder) -> folder inside system/; only unit files, helpers and configs the Pi installs as root
SYSTEM_SOURCES = ["satellite/program", "satellite/sync", "satellite/audio/jabra.conf",
                  "satellite/audio/xvf3800.conf", "satellite/audio/friday-audio-select",
                  "satellite/audio/90-friday-audio.rules", "satellite/audio/audio-board.conf",
                  "satellite/audio/friday-silence.service",
                  "agents/satellite/friday-satellite-agent.service", "agents/satellite/sudoers.friday-satellite-agent",
                  "agents/satellite/friday-agent-apply", "agents/satellite/friday-wifi-apply",
                  "agents/satellite/friday-ota-apply"]
SYSTEM_SUFFIXES = (".service", ".timer", ".sh")
# a release with ota/release.json (OTA era) must carry these; only pre-OTA refs may lack them
REQUIRED_MODERN = ["satellite/program/requirements.lock", "agents/satellite/requirements.lock",
                   "agents/satellite/friday-ota-apply",
                   "satellite/program/friday-satelite.service", "agents/satellite/friday-satellite-agent.service",
                   "agents/satellite/sudoers.friday-satellite-agent"]
SCHEMA = 1


def declared(release: dict) -> tuple[list[str], list[str]]:
    """(paths to archive, system sources) for a release: its own `package.satellite`, else the built-in lists.
    The built-in required paths are always merged in; order is kept and nothing repeats."""
    sat = ((release or {}).get("package") or {}).get("satellite")
    if isinstance(sat, dict):
        paths, system = list(sat.get("paths") or []), list(sat.get("system") or [])
    else:
        paths, system = list(SATELLITE_PATHS), list(SYSTEM_SOURCES)
    out: list[str] = []
    for p in paths + REQUIRED_MODERN + ["ota/release.json", "vault/manifest.json"]:
        if p in out or any(p.startswith(q.rstrip("/") + "/") for q in out):
            continue
        out.append(p)
    sys_out: list[str] = []
    for p in system:
        if p not in sys_out:
            sys_out.append(p)
    return out, sys_out


def lock_sha256(program_lock: bytes, agent_lock: bytes) -> str:
    """One hash for the satellite venv: program and agent share it."""
    return hashlib.sha256(program_lock + b"\n--\n" + agent_lock).hexdigest()


def _read(root: str, rel: str) -> bytes:
    try:
        with open(os.path.join(root, rel), "rb") as f:
            return f.read()
    except OSError:
        return b""


def _add_bytes(tar: tarfile.TarFile, arc: str, data: bytes) -> None:
    info = tarfile.TarInfo(arc)
    info.size = len(data)
    tar.addfile(pv._public(info), io.BytesIO(data))


def _vault_files(vault, tmp: str) -> list[tuple[str, str]]:
    """(local file, arcname) for the vault sets the satellite needs; refuses a missing set (never ship without)."""
    manifest = os.path.join(tmp, "vault/manifest.json")
    if not os.path.exists(manifest):
        raise pv.ProvisioningError("vault_error")
    names = [n for n in vault._load(manifest).get("files", {})
             if any(n.startswith(p) and "/" not in n[len(p):] for p in pv.VAULT_FOR_BUNDLE)]
    if [p for p in pv.VAULT_FOR_BUNDLE if not any(n.startswith(p) for n in names)]:
        raise pv.ProvisioningError("vault_error")
    vdir = os.path.join(tmp, "_vault")
    try:
        vault.fetch_all(sorted(names), vdir, manifest=manifest)
    except vault.VaultError:
        raise pv.ProvisioningError("vault_error")
    out = []
    for n in sorted(names):
        prefix = next(p for p in pv.VAULT_FOR_BUNDLE if n.startswith(p))
        out.append((os.path.join(vdir, n), pv.VAULT_FOR_BUNDLE[prefix] + n[len(prefix):]))
    return out


def pack_satellite(tar: tarfile.TarFile, raw: bytes, tag: str, sha: str, vault, line: str | None = None) -> dict:
    """Writes the satellite package into `tar` from `raw` (a tar of the release's declared paths). Returns its manifest.
    `line` is written verbatim to FRIDAY_VERSION (default "<tag> <sha>")."""
    tmp = tempfile.mkdtemp()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as src:
            src.extractall(tmp, filter="data")
        rel_json = os.path.join(tmp, "ota/release.json")
        release = {}
        if os.path.exists(rel_json):
            with open(rel_json, encoding="utf-8") as f:
                release = json.load(f)
            missing = [p for p in REQUIRED_MODERN if not os.path.isfile(os.path.join(tmp, p))]
            if missing:
                pv.LOG.error("package: release %s lacks %s", tag, ", ".join(missing))
                raise pv.ProvisioningError("source_missing")
        declares = isinstance((release.get("package") or {}).get("satellite"), dict)
        _, system = declared(release)
        if declares:  # a declared system file that is not in the release fails loudly; the built-in list stays lenient
            gone = [p for p in system if not os.path.exists(os.path.join(tmp, p))]
            if gone:
                pv.LOG.error("package: release %s declares missing system files: %s", tag, ", ".join(gone))
                raise pv.ProvisioningError("source_missing")
        vault_files = _vault_files(vault, tmp)  # validated before anything is written
        prog = os.path.join(tmp, "satellite/program")
        for name in sorted(os.listdir(prog)):
            p = os.path.join(prog, name)
            if os.path.isfile(p) and name.endswith(".py") and not pv._skip(name):
                tar.add(p, arcname=f"program/{name}", recursive=False, filter=pv._public)
            elif os.path.isdir(p) and name == "vendor":
                pv._add_tree(tar, p, "program/vendor")
        for local, arc in vault_files:
            tar.add(local, arcname=arc, recursive=False, filter=pv._public)
        for src_dir, arc in (("agents/satellite/friday_satellite_agent", "agent/friday_satellite_agent"),
                             ("agents/common/friday_agent_core", "agent/friday_agent_core")):
            pv._add_tree(tar, os.path.join(tmp, src_dir), arc)
        program_lock = _read(tmp, "satellite/program/requirements.lock")
        agent_lock = _read(tmp, "agents/satellite/requirements.lock")
        _add_bytes(tar, "program/requirements.lock", program_lock)
        _add_bytes(tar, "agent/requirements.lock", agent_lock)
        migrations = []
        mdir = os.path.join(tmp, "satellite/migrations")
        if os.path.isdir(mdir):
            for name in sorted(os.listdir(mdir)):
                if os.path.isfile(os.path.join(mdir, name)) and not pv._skip(name):
                    tar.add(os.path.join(mdir, name), arcname=f"migrations/{name}", recursive=False, filter=pv._public)
                    migrations.append(name)
        for rel in system:
            p = os.path.join(tmp, rel)
            files = [p] if os.path.isfile(p) else (
                [os.path.join(p, n) for n in sorted(os.listdir(p))] if os.path.isdir(p) else [])
            for f in files:
                n = os.path.basename(f)
                if pv._skip(n) or n == "install.sh" or not os.path.isfile(f):  # the repo deploy script is not a system file
                    continue
                if os.path.isdir(p) and not n.endswith(SYSTEM_SUFFIXES):
                    continue
                tar.add(f, arcname=f"system/{n}", recursive=False, filter=pv._public)
        data = ((line or f"{tag} {sha}").strip() + "\n").encode()
        for arc in ("program/FRIDAY_VERSION", "agent/friday_agent_core/FRIDAY_VERSION"):
            _add_bytes(tar, arc, data)
        manifest = {"schema": SCHEMA, "tag": tag, "sha": sha, "protocol": release.get("protocol", {}),
                    "heavy": "satellite" in release.get("heavy", []),
                    "lock_sha256": lock_sha256(program_lock, agent_lock), "migrations": migrations}
        _add_bytes(tar, "manifest.json", json.dumps(manifest, sort_keys=True).encode())
        return manifest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_satellite(archive: Callable[[str, list], bytes], tag: str, sha: str, vault, out_dir: str) -> dict:
    """Builds `<out_dir>/satellite.tar.gz` atomically. Returns {path, sha256, size, lock_sha256, protocol}."""
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, "satellite.tar.gz")
    fd, tmp = tempfile.mkstemp(prefix=".satellite-", suffix=".tar.gz", dir=out_dir)
    os.close(fd)
    try:
        try:  # the release declares its own package: read that declaration first, then archive what it names
            with tarfile.open(fileobj=io.BytesIO(archive(sha, ["ota/release.json"]))) as t:
                m = t.extractfile("ota/release.json") if "ota/release.json" in t.getnames() else None
                release = json.loads(m.read().decode("utf-8")) if m else {}
        except (tarfile.TarError, ValueError, KeyError):
            release = {}
        raw = archive(sha, declared(release)[0])
        with tarfile.open(tmp, "w:gz") as tar:
            manifest = pack_satellite(tar, raw, tag, sha, vault)
        os.chmod(tmp, 0o644)
        os.replace(tmp, final)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return {"path": final, "sha256": _sha_file(final), "size": os.path.getsize(final),
            "lock_sha256": manifest["lock_sha256"], "protocol": manifest["protocol"], "heavy": manifest["heavy"]}


def build_release(repo, tag: str, sha: str, vault, root: str, wheels: Callable[[list, str], str],
                  built_by: str = "") -> dict:
    """`<root>/packages/<tag>/`: satellite.tar.gz, archive.tar (server side), meta.json, `.complete`. Idempotent.
    `built_by` is the version of the agent that packed it (spec 2026-11-18): an older packager may miss files."""
    folder = os.path.join(root, "packages", tag)
    done = os.path.join(folder, ".complete")
    meta_path = os.path.join(folder, "meta.json")
    if os.path.exists(done):
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
    os.makedirs(folder, exist_ok=True)
    sat = build_satellite(repo.archive, tag, sha, vault, folder)
    server_tar = os.path.join(folder, "archive.tar")
    with open(server_tar + ".tmp", "wb") as f:
        f.write(repo.archive(sha, SERVER_PATHS))
    os.replace(server_tar + ".tmp", server_tar)
    with tarfile.open(sat["path"], "r:gz") as t:
        locks = [t.extractfile("program/requirements.lock").read(), t.extractfile("agent/requirements.lock").read()]
    lock_dir = tempfile.mkdtemp()
    try:
        paths = []
        for i, data in enumerate(locks):
            p = os.path.join(lock_dir, f"{i}.lock")
            with open(p, "wb") as f:
                f.write(data)
            paths.append(p)
        sat["wheels"] = wheels(paths, sat["lock_sha256"])
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)
    meta = {"tag": tag, "sha": sha, "satellite": sat, "server": {"path": server_tar}, "built_by": built_by}
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, sort_keys=True)
    open(done, "w").close()
    return meta


def mark_offered(root: str, tag: str) -> None:
    """`<root>/packages/<tag>/.offered`: the package reached a device once, so it is never rebuilt under it."""
    folder = os.path.join(root, "packages", tag)
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, ".offered"), "a").close()


def is_offered(root: str, tag: str) -> bool:
    return os.path.exists(os.path.join(root, "packages", tag, ".offered"))


def prune_packages(root: str, keep: set[str]) -> list[str]:
    """Removes every package folder whose tag is not in `keep`. Returns the removed tags, sorted."""
    base = os.path.join(root, "packages")
    removed = []
    for tag in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if tag not in keep:
            shutil.rmtree(os.path.join(base, tag), ignore_errors=True)
            removed.append(tag)
    return removed
