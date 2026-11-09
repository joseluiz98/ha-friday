"""Server-side provisioning for new satellites (spec 2026-11-05 §6.3, §9): sync-key registration and one-time bundles.

`register_sync_key` replaces the manual `authorized_keys` + `incoming/<sat>` setup; `prepare_bundle` packs the
satellite program/agent (kind `update`) or the voices (kind `voices`) and hands HA a single-use URL.
Every path comes from an environment variable with the production default, so tests never touch the real ones.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import shutil
import tarfile
import tempfile
import time
from typing import Callable

from aiohttp import web

LOG = logging.getLogger("friday_server_agent")

SAT_ID = re.compile(r"^sat-[0-9a-f]{6}$")
PUBKEY = re.compile(r"^(ssh-ed25519) ([A-Za-z0-9+/]{43,}={0,2})(?: \S+)?$")
EXPIRES_S = 600
VERIFIER = "friday_verificador.pkl"
KINDS = ("update", "voices")


def data_dir() -> str:
    return os.environ.get("FRIDAY_WAKEWORD_DATA", "/srv/wakeword-data")


def haos_config() -> str:
    return os.environ.get("FRIDAY_HAOS_CONFIG", "/mnt/haos/config")


def bundles_dir() -> str:
    return os.environ.get("FRIDAY_BUNDLES_DIR", "/srv/nobackup/bundles")


def bundle_host() -> str:
    return os.environ.get("FRIDAY_BUNDLE_HOST") or f"{os.environ.get('FRIDAY_VM_IP', '127.0.0.1')}:8801"


class ProvisioningError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _rrsync_line(data: str, sat_id: str, key: str) -> str:
    return f'restrict,command="/usr/bin/rrsync -wo -no-del {data}/incoming/{sat_id}" {key} {sat_id}-satsync'


def register_sync_key(sat_id, pubkey, data: str | None = None, chown_user: str | None = "satsync") -> dict:
    """Idempotent: one line per satellite; a changed key replaces the line. Returns {"ok": True, "changed": bool}."""
    data = data or data_dir()
    if not isinstance(sat_id, str) or not SAT_ID.match(sat_id):
        raise ProvisioningError("invalid_sat_id")
    m = PUBKEY.match(pubkey.strip()) if isinstance(pubkey, str) else None
    if not m:
        raise ProvisioningError("invalid_key")
    key = f"{m.group(1)} {m.group(2)}"
    folder = os.path.join(data, "incoming", sat_id)
    os.makedirs(folder, exist_ok=True)
    if chown_user:
        try:
            shutil.chown(folder, user=chown_user)
        except (LookupError, PermissionError, OSError):
            pass  # no such user (tests) or not root
    ssh = os.path.join(data, ".ssh")
    os.makedirs(ssh, exist_ok=True)
    path = os.path.join(ssh, "authorized_keys")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        st = os.stat(path)
    except FileNotFoundError:
        lines, st = [], None
    marker = f" {sat_id}-satsync"
    mine = [ln for ln in lines if ln.endswith(marker)]
    new_line = _rrsync_line(data, sat_id, key)
    if mine == [new_line]:
        return {"ok": True, "changed": False}
    out = [ln for ln in lines if not ln.endswith(marker)] + [new_line]
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    if st is not None:
        try:
            os.chown(tmp, st.st_uid, st.st_gid)  # keep the owner sshd already trusts
            os.chmod(tmp, st.st_mode & 0o777)
        except OSError:
            pass
    os.replace(tmp, path)
    return {"ok": True, "changed": True}


def _skip(name: str) -> bool:
    return name.startswith("test_") or ".bak" in name or name == "__pycache__" or name.endswith(".pyc")


def _public(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Sources read through the HAOS mount look like 0600: what lands on the satellite must be readable by its user."""
    info.mode = 0o755 if info.isdir() else 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _add_tree(tar: tarfile.TarFile, src: str, arcname: str) -> None:
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if not _skip(d))
        for name in sorted(files):
            if _skip(name):
                continue
            full = os.path.join(root, name)
            tar.add(full, arcname=os.path.join(arcname, os.path.relpath(full, src)), recursive=False, filter=_public)


def newest_verifier(voices: str) -> str | None:
    best, best_t = None, -1.0
    for root, _d, files in os.walk(voices):
        if VERIFIER in files:
            p = os.path.join(root, VERIFIER)
            t = os.stat(p).st_mtime
            if t > best_t:
                best, best_t = p, t
    return best


def people_of(data: str) -> list[str]:
    folder = os.path.join(data, "voices")
    try:
        return sorted(n for n in os.listdir(folder) if os.path.isdir(os.path.join(folder, n)))
    except OSError:
        return []


class BundleStore:
    """Builds bundles and hands out single-use tokens (10 minutes)."""

    def __init__(self, clock: Callable[[], float] = time.time, consolidate: Callable[[str], None] | None = None) -> None:
        self._clock = clock
        self._tokens: dict[str, tuple[str, float]] = {}
        self._consolidate = consolidate

    def _gc(self) -> None:
        now = self._clock()
        for t, (path, exp) in list(self._tokens.items()):
            if exp <= now:
                self._tokens.pop(t)
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _pack(self, kind: str, dest: str) -> None:
        cfg, data = haos_config(), data_dir()
        with tarfile.open(dest, "w:gz") as tar:
            if kind == "update":
                prog = os.path.join(cfg, "tools/satelite/programa")
                if not os.path.isdir(prog):
                    raise ProvisioningError("source_missing")
                for name in sorted(os.listdir(prog)):
                    p = os.path.join(prog, name)
                    if os.path.isfile(p) and name.endswith(".py") and not _skip(name):
                        tar.add(p, arcname=f"program/{name}", recursive=False, filter=_public)
                    elif os.path.isdir(p) and name in ("vendor", "sons"):
                        _add_tree(tar, p, f"program/{name}")
                repo = os.path.join(cfg, "dev/ha-friday/agents")
                for src, arc in ((os.path.join(repo, "satellite/friday_satellite_agent"), "agent/friday_satellite_agent"),
                                 (os.path.join(repo, "common/friday_agent_core"), "agent/friday_agent_core")):
                    if not os.path.isdir(src):
                        raise ProvisioningError("source_missing")
                    _add_tree(tar, src, arc)
            else:
                if self._consolidate:
                    self._consolidate(data)
                voices = os.path.join(data, "voices")
                if not os.path.isdir(voices):
                    raise ProvisioningError("source_missing")
                for root, dirs, files in os.walk(voices):
                    dirs.sort()
                    for name in sorted(files):
                        if name.endswith(".pkl") or ".bak" in name:
                            continue
                        full = os.path.join(root, name)
                        tar.add(full, arcname=os.path.join("vozes", os.path.relpath(full, voices)), recursive=False, filter=_public)
                ver = newest_verifier(voices)
                if ver:
                    tar.add(ver, arcname=VERIFIER, recursive=False, filter=_public)

    def prepare(self, kind, sat_id) -> dict:
        if kind not in KINDS:
            raise ProvisioningError("invalid_kind")
        if not isinstance(sat_id, str) or not SAT_ID.match(sat_id):
            raise ProvisioningError("invalid_sat_id")
        self._gc()
        folder = bundles_dir()
        os.makedirs(folder, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=f"{kind}-{sat_id}-", suffix=".tar.gz", dir=folder)
        os.close(fd)
        try:
            self._pack(kind, path)
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (path, self._clock() + EXPIRES_S)
        out = {"url": f"http://{bundle_host()}/v1/bundles/{token}", "sha256": h.hexdigest(), "expires_in": EXPIRES_S}
        if kind == "voices":  # additive: who the voices belong to (the presentation only offers the others)
            out["people"] = people_of(data_dir())
        return out

    def take(self, token: str) -> bytes | None:
        """Single use: the token is consumed and the file removed whether or not it was still valid."""
        entry = self._tokens.pop(token, None)
        if entry is None:
            return None
        path, exp = entry
        try:
            if exp <= self._clock():
                return None
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def make_handlers(store: BundleStore):
    def args(cmd: dict) -> dict:
        return cmd["args"] if isinstance(cmd.get("args"), dict) else cmd

    async def register(cmd: dict) -> dict:
        a = args(cmd)
        try:
            return register_sync_key(a.get("sat_id"), a.get("pubkey"))
        except ProvisioningError as err:
            return {"ok": False, "error": err.code}
        except OSError as err:
            LOG.error("register_sync_key failed: %s", type(err).__name__)
            return {"ok": False, "error": "io"}

    async def prepare(cmd: dict) -> dict:
        import asyncio
        a = args(cmd)
        try:
            data = await asyncio.to_thread(store.prepare, a.get("kind"), a.get("sat_id"))
        except ProvisioningError as err:
            return {"ok": False, "error": err.code}
        except OSError as err:
            LOG.error("prepare_bundle failed: %s", type(err).__name__)
            return {"ok": False, "error": "io"}
        return {"ok": True, "data": data}

    return {"register_sync_key": register, "prepare_bundle": prepare}


def add_routes(app: web.Application, store: BundleStore) -> None:
    async def serve(request: web.Request) -> web.StreamResponse:
        body = store.take(request.match_info["token"])
        if body is None:
            return web.Response(status=404)
        return web.Response(body=body, content_type="application/gzip")

    app.router.add_get("/v1/bundles/{token}", serve)
