"""Provisioning commands of the satellite agent (spec 2026-11-05): provision_info, apply_bundle (update | voices),
start_onboarding and the two-level factory reset. Every path and effect is injectable so tests touch nothing real.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import tarfile
import tempfile
import time
from typing import Awaitable, Callable
from urllib.parse import urlparse

import aiohttp

from . import capabilities as cap

LOG = logging.getLogger("friday_satellite_agent")

DOWNLOAD_TIMEOUT_S = 300
_SHA = re.compile(r"^[0-9a-f]{64}$")
VERIFIER = "friday_verificador.pkl"
RESTART_WAIT_S = 120


class BundleError(Exception):
    """A failed step; the message is the step name returned to HA (never a path or a secret)."""


async def fetch_to_file(url: str, dest: str) -> str:
    """Streams `url` into `dest`; returns the sha256 of the bytes."""
    digest = hashlib.sha256()
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(url) as r:
            if r.status != 200:
                raise BundleError("download")
            with open(dest, "wb") as f:
                async for chunk in r.content.iter_chunked(64 * 1024):
                    digest.update(chunk)
                    f.write(chunk)
    return digest.hexdigest()


def program_version(program_dir: str) -> str:
    """`VERSION` file if present, else a short hash of the program's .py files."""
    try:
        with open(os.path.join(program_dir, "VERSION"), encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            return v
    except OSError:
        pass
    h = hashlib.sha256()
    try:
        for name in sorted(n for n in os.listdir(program_dir) if n.endswith(".py")):
            h.update(name.encode())
            with open(os.path.join(program_dir, name), "rb") as f:
                h.update(f.read())
    except OSError:
        return "unknown"
    return h.hexdigest()[:12]


def _read(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return default


def _copy_file(src: str, dst: str, stamp: str) -> None:
    """Atomic copy; an existing different file is kept as <dst>.bak.<stamp>."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        with open(src, "rb") as a, open(dst, "rb") as b:
            if a.read() == b.read():
                return
        shutil.copy2(dst, f"{dst}.bak.{stamp}")
    tmp = f"{dst}.tmp-{secrets.token_hex(3)}"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _copy_tree(src: str, dst: str, stamp: str) -> int:
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        for name in files:
            if ".bak" in name or name.startswith("test_"):
                continue
            _copy_file(os.path.join(root, name), os.path.join(dst, rel, name) if rel != "." else os.path.join(dst, name),
                       stamp)
            n += 1
    return n


class Provisioner:
    def __init__(self, *, etc_dir: str, config_path: str, ssh_dir: str, program_dir: str, bundles_dir: str,
                 voices_dir: str, models_dir: str, program, restart_program: Callable[[], Awaitable[dict]],
                 apply_agent: Callable[[str], Awaitable[int]] | None = None,
                 fetch=fetch_to_file, exit_fn=os._exit, ip_fn=lambda: "", loop_sleep=asyncio.sleep,
                 now=time.time) -> None:
        self.etc_dir, self.config_path, self.ssh_dir = etc_dir, config_path, ssh_dir
        self.program_dir, self.bundles_dir = program_dir, bundles_dir
        self.voices_dir, self.models_dir = voices_dir, models_dir
        self.program = program
        self._restart_program = restart_program
        self._apply_agent = apply_agent
        self._fetch = fetch
        self._exit = exit_fn
        self._ip = ip_fn
        self._sleep = loop_sleep
        self._now = now
        self._pending_facts: dict | None = None
        self._exit_task: asyncio.Task | None = None

    # ---- provision_info
    async def info(self) -> dict:
        try:
            with open(self.config_path, encoding="utf-8") as f:
                password = json.load(f)["senha_porta_local"]
        except (OSError, ValueError, KeyError):
            return {"ok": False, "error": "no_config"}
        try:
            ip = self._ip()
        except OSError:
            ip = ""
        return {"ok": True, "data": {
            "program_password": password,
            "sync_pubkey": _read(os.path.join(self.ssh_dir, "id_satsync.pub")),
            "mic": _read(os.path.join(self.etc_dir, "mic"), "jabra-speak-410"),
            "versions": {"program": program_version(self.program_dir), "agent": cap.VERSION},
            "ip": ip}}

    # ---- apply_bundle
    @staticmethod
    def _check_args(kind, url, sha) -> None:
        if kind not in ("update", "voices"):
            raise BundleError("kind")
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise BundleError("url")
        if not isinstance(sha, str) or not _SHA.match(sha):
            raise BundleError("sha256")

    @staticmethod
    def _extract(archive: str, dest: str) -> None:
        try:
            with tarfile.open(archive, "r:*") as tar:
                tar.extractall(dest, filter="data")  # rejects absolute paths, .., links outside, devices
        except (tarfile.TarError, OSError, ValueError) as err:
            raise BundleError("extract") from err

    @staticmethod
    def _validate(kind: str, root: str) -> dict:
        def isdir(*p):
            return os.path.isdir(os.path.join(root, *p))

        if kind == "update":
            program = os.path.join(root, "program")
            has_py = os.path.isdir(program) and any(n.endswith(".py") for n in os.listdir(program))
            if not (has_py and os.path.isfile(os.path.join(program, "friday_satelite.py"))
                    and os.path.isfile(os.path.join(root, "agent", "friday_satellite_agent", "__main__.py"))
                    and isdir("agent", "friday_agent_core")):
                raise BundleError("layout")
            return {"program": program, "agent": os.path.join(root, "agent")}
        voices = os.path.join(root, "vozes")
        verifier = os.path.join(root, VERIFIER)
        if not os.path.isdir(voices) and not os.path.isfile(verifier):
            raise BundleError("layout")
        return {"voices": voices if os.path.isdir(voices) else None,
                "verifier": verifier if os.path.isfile(verifier) else None}

    async def apply_bundle(self, kind, url, sha256) -> dict:
        stage = None
        archive = None
        try:
            self._check_args(kind, url, sha256)
            os.makedirs(self.bundles_dir, mode=0o700, exist_ok=True)
            archive = os.path.join(self.bundles_dir, f"bundle-{secrets.token_hex(4)}.tar.gz")
            try:
                got = await self._fetch(url, archive)
            except BundleError:
                raise
            except Exception as err:  # noqa: BLE001 - never echo the URL (it is a one-time secret)
                raise BundleError("download") from err
            if got != sha256:
                raise BundleError("sha256")
            stage = tempfile.mkdtemp(prefix="stage-", dir=self.bundles_dir)
            await asyncio.to_thread(self._extract, archive, stage)
            parts = self._validate(kind, stage)  # everything validated before anything is applied
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(self._now())) + "_prov"
            if kind == "update":
                return await self._apply_update(parts, stamp)
            return await self._apply_voices(parts, stamp)
        except BundleError as err:
            return {"ok": False, "error": str(err)}
        except OSError as err:
            LOG.error("apply_bundle failed: %s", type(err).__name__)
            return {"ok": False, "error": "apply"}
        finally:
            for p in (stage,):
                if p:
                    shutil.rmtree(p, ignore_errors=True)
            if archive:
                try:
                    os.remove(archive)
                except OSError:
                    pass

    async def _apply_update(self, parts: dict, stamp: str) -> dict:
        def program_copy() -> int:
            n = 0
            src = parts["program"]
            for name in os.listdir(src):
                p = os.path.join(src, name)
                if os.path.isfile(p) and name.endswith(".py") and ".bak" not in name and not name.startswith("test_"):
                    _copy_file(p, os.path.join(self.program_dir, name), stamp)
                    n += 1
                elif os.path.isdir(p) and name in ("vendor", "sons"):
                    n += _copy_tree(p, os.path.join(self.program_dir, name), stamp)
            return n

        await asyncio.to_thread(program_copy)
        if self._apply_agent is not None:
            # the agent dir belongs to root: a sudoers-allowed helper copies it from the validated staging area
            agent_stage = parts["agent"]
            code = await self._apply_agent(agent_stage)
            if code != 0:
                raise BundleError("apply_agent")
        data = {"program_restart": await self._restart_program_when_idle()}
        self._schedule_agent_exit()
        return {"ok": True, "data": data}

    async def _restart_program_when_idle(self) -> str:
        """Same rule as `restart_program`: never with a conversation open. Waits up to RESTART_WAIT_S."""
        for _ in range(RESTART_WAIT_S // 5):
            res = await self._restart_program()
            if res.get("ok"):
                return "done"
            if res.get("error") != "conversation_open":
                return "failed"
            await self._sleep(5)
        return "deferred"

    async def _apply_voices(self, parts: dict, stamp: str) -> dict:
        def copy() -> None:
            if parts["voices"]:
                _copy_tree(parts["voices"], self.voices_dir, stamp)
            if parts["verifier"]:
                _copy_file(parts["verifier"], os.path.join(self.models_dir, VERIFIER), stamp)

        await asyncio.to_thread(copy)
        if parts["verifier"]:
            try:  # best effort: a satellite with the verifier file but no config entry would never use it
                cfg = await self.program.get_config()
                if not cfg.get("verificador"):
                    await self.program.put_config({"verificador": os.path.join(self.models_dir, VERIFIER)})
            except Exception as err:  # noqa: BLE001
                LOG.warning("verifier config not set: %s", type(err).__name__)
        return {"ok": True, "data": {}}

    def _schedule_agent_exit(self, delay: float = 1.0) -> None:
        """The reply must leave first; the unit has Restart=always, so exit(3) restarts the agent."""
        async def later():
            await self._sleep(delay)
            self._exit(3)
        self._exit_task = asyncio.ensure_future(later())

    # ---- onboarding
    async def start_onboarding(self, facts) -> dict:
        if not isinstance(facts, dict):
            return {"ok": False, "error": "invalid"}
        try:
            res = await self.program.command({"acao": "wake_word_test", "timeout_s": 30})
        except Exception as err:  # noqa: BLE001
            LOG.warning("wake_word_test failed: %s", type(err).__name__)
            return {"ok": False, "error": "program_error"}
        if not res.get("ok"):
            return {"ok": False, "error": "program_refused"}
        self._pending_facts = facts
        return {"ok": True}

    async def on_onboarding(self, result: str) -> None:
        """Program event: after the wake-word test, open the presentation conversation with the facts."""
        if result not in ("wake_word_ok", "wake_word_failed") or self._pending_facts is None:
            return
        facts, self._pending_facts = dict(self._pending_facts, wake_word_ok=result == "wake_word_ok"), None
        try:
            await self.program.command({"acao": "open_presentation", "facts": facts})
        except Exception as err:  # noqa: BLE001
            LOG.warning("open_presentation failed: %s", type(err).__name__)

    # ---- factory reset
    async def factory_reset(self, level) -> dict:
        if level == 1:
            # The network is learned only by rewriting the card (spec §4): erasing it would strand the Pi.
            # So this level is a hook: flag + spoken notice, NetworkManager untouched.
            with open(os.path.join(self.etc_dir, "reset-network"), "w", encoding="utf-8") as f:
                f.write("")
            await self._say(["reset_network"])
            return {"ok": True}
        if level == 2:
            def forget() -> None:
                # pairable first: a crash in between must leave the satellite adoptable, not stranded
                with open(os.path.join(self.etc_dir, "pairable"), "w", encoding="utf-8") as f:
                    f.write("")
                try:
                    os.remove(os.path.join(self.etc_dir, "ha_token"))
                except FileNotFoundError:
                    pass
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg["comodo"] = None
                tmp = self.config_path + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.config_path)

            await asyncio.to_thread(forget)
            await self._say(["reset_home"])
            self._schedule_agent_exit(delay=3.0)  # let the phrase start; the restart comes back pairable
            return {"ok": True}
        return {"ok": False, "error": "invalid"}

    async def _say(self, keys: list[str]) -> None:
        try:
            await self.program.command({"acao": "play_phrases", "keys": keys})
        except Exception as err:  # noqa: BLE001 - the reset never depends on the program being up
            LOG.warning("phrase failed: %s", type(err).__name__)
