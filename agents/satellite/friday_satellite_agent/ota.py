"""Satellite OTA client (spec 2026-11-10 §6, §16; plan Task 14).

Every POLL_SATELLITE_S the agent asks the VM for an offer (`GET /v1/ota/offer`, its own bearer token), downloads the
package (+ wheels when the lock changed) into /var/lib/friday/ota/downloads, checks the sha256 and has the root helper
stage it (`friday-ota-apply stage`). It installs at a safe moment (`/status`: estado parada, no timer, no alarm, no
music, nothing pending; a heavy update needs that continuously for HEAVY_IDLE_S), or at once when asked by hand (only
estado parada: never in the middle of a conversation). Installing = ring `updating` + `friday-ota-apply activate`,
which detaches, restarts the agent and leaves its result in /var/lib/friday/ota.json; the agent that comes back reports
it (`installed` / `rolled_back` / `failed`). A failure before the switch is reported as `failed` with its stage and
retried on the local schedule (retry.RETRY_DELAYS_S) as well as the VM's.

Without a credential (`/etc/friday-satelite/ota_token` + the VM url in `/etc/friday-satelite/ota.json`) it does
nothing but report `ota_credentials: false`. The token is never logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Awaitable, Callable

from friday_agent_core.ota import retry
from friday_agent_core.ota.constants import HEAVY_IDLE_S, POLL_SATELLITE_S, UPDATE_PROTOCOL

LOG = logging.getLogger("friday_satellite_agent.ota")
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CHANNELS = ("stable", "alpha")
KEY = "ota"  # the `update` entity of this satellite
STATE = os.path.expanduser("~/.local/state/friday-ota.json")
RESULT = "/var/lib/friday/ota.json"  # written by the helper (0644)
DOWNLOADS = "/var/lib/friday/ota/downloads"
CHANNEL_FILE = "/etc/friday-satelite/ota.json"  # {"channel": "stable", "url": "http://<vm>:8801"}
TOKEN_FILE = "/etc/friday-satelite/ota_token"
CURRENT = "/opt/friday/current"
HELPER = "/usr/local/sbin/friday-ota-apply"
HELPER_CMD = ["sudo", "-n", HELPER]
_REFUSED = re.compile(r"refused at (\S+): (.*)$")


def parse_package_manifest(data: dict) -> dict:
    """Contract point (update protocol v1, Task 19): the manifest.json inside a satellite package
    (`packages.build_satellite_package`). Raises ValueError naming the bad key."""
    if not isinstance(data, dict):
        raise ValueError("manifest")
    if data.get("schema") != 1:
        raise ValueError("schema")
    tag, sha = data.get("tag"), data.get("sha")
    if not isinstance(tag, str) or not TAG.match(tag):
        raise ValueError("tag")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("sha")
    lock = data.get("lock_sha256") or ""
    if not isinstance(lock, str) or (lock and not SHA256.match(lock)):
        raise ValueError("lock_sha256")
    proto = data.get("protocol", {})
    if not isinstance(proto, dict) or any(isinstance(v, bool) or not isinstance(v, int) for v in proto.values()):
        raise ValueError("protocol")
    migrations = data.get("migrations", [])
    if not isinstance(migrations, list) or not all(isinstance(m, str) for m in migrations):
        raise ValueError("migrations")
    return {"tag": tag, "sha": sha, "lock_sha256": lock, "protocol": dict(proto), "heavy": bool(data.get("heavy")),
            "migrations": list(migrations)}


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True)
    os.replace(tmp, path)


def installed(current: str = CURRENT) -> tuple[str | None, str]:
    """(tag, lock_sha256) of the running release; (None, "") on the old layout (the VM then sends wheels)."""
    try:
        tag = os.path.basename(os.readlink(current))
    except OSError:
        return None, ""
    if not TAG.match(tag):
        return None, ""
    lock = _read_json(os.path.join(current, "manifest.json")).get("lock_sha256") or ""
    return tag, lock if isinstance(lock, str) else ""


class Failure(Exception):
    def __init__(self, stage: str, error: str = "") -> None:
        super().__init__(f"{stage}: {error}")
        self.stage, self.error = stage, str(error)[:200]


class HttpClient:
    """The VM's OTA API over aiohttp (the real one; tests inject a fake with the same three methods)."""

    def __init__(self, timeout_s: float = 30, download_timeout_s: float = 1800) -> None:
        self._t, self._dt = timeout_s, download_timeout_s

    @staticmethod
    def _h(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    async def get_json(self, url: str, params: dict, token: str) -> dict:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._t)) as s:
            async with s.get(url, params=params, headers=self._h(token)) as r:
                if r.status != 200:
                    raise Failure("offer", f"http {r.status}")
                return await r.json()

    async def post_json(self, url: str, body: dict, token: str) -> dict:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._t)) as s:
            async with s.post(url, json=body, headers=self._h(token)) as r:
                if r.status != 200:
                    raise Failure("report", f"http {r.status}")
                return await r.json()

    async def download(self, url: str, token: str, dest: str) -> str:
        """Streams to `dest` (via .part); returns the sha256 of what arrived."""
        import aiohttp
        h = hashlib.sha256()
        part = dest + ".part"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._dt)) as s:
            async with s.get(url, headers=self._h(token)) as r:
                if r.status != 200:
                    raise Failure("download", f"http {r.status}")
                with open(part, "wb") as f:
                    async for chunk in r.content.iter_chunked(1 << 16):
                        h.update(chunk)
                        f.write(chunk)
        os.replace(part, dest)
        return h.hexdigest()


async def run_helper(args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*HELPER_CMD, *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, (out or b"").decode("utf-8", "replace")


class OtaClient:
    def __init__(self, sat_id: str, program, http=None, helper: Callable[[list[str]], Awaitable] = run_helper,
                 clock: Callable[[], float] = time.time, state_path: str = STATE, result_path: str = RESULT,
                 downloads: str = DOWNLOADS, channel_file: str = CHANNEL_FILE, token_file: str = TOKEN_FILE,
                 current: str = CURRENT, helper_path: str = HELPER, sudo: Callable[[list[str]], Awaitable] | None = None,
                 on_change: Callable[[], Awaitable] | None = None) -> None:
        self.sat_id, self.program, self.http = sat_id, program, http or HttpClient()
        self._helper, self._clock = helper, clock
        self.state_path, self.result_path, self.downloads = state_path, result_path, downloads
        self.channel_file, self.token_file, self.current = channel_file, token_file, current
        self.helper_path, self._sudo = helper_path, sudo or _sudo
        self._on_change = on_change
        self.data = _read_json(state_path)
        self.manual = False  # `ota_install` from HA, until the next install attempt
        self.idle_since: float | None = None  # heavy updates: start of the current continuous idle stretch
        self._idle_mark = None  # the program's `ultima_abertura` when idle_since was set
        self.latest: str | None = self.data.get("staged")
        self.in_progress = False
        self._wake = asyncio.Event()

    # ---------------------------------------------------------------- settings
    def settings(self) -> dict:
        d = _read_json(self.channel_file)
        ch = d.get("channel") if d.get("channel") in CHANNELS else "stable"
        url = d.get("url") if isinstance(d.get("url"), str) else ""
        return {"channel": ch, "url": url.rstrip("/")}

    def token(self) -> str:
        try:
            with open(self.token_file, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def has_credentials(self) -> bool:
        return bool(self.token() and self.settings()["url"])

    def set_channel(self, channel: str) -> dict:
        if channel not in CHANNELS:
            return {"ok": False, "error": "invalid"}
        _write_json(self.channel_file, {**_read_json(self.channel_file), "channel": channel})
        self.kick()
        return {"ok": True}

    def set_credentials(self, token, url) -> dict:
        if not isinstance(token, str) or not token.strip() or len(token) > 512 or not isinstance(url, str) \
                or not re.match(r"^https?://[^/\s]+$", url.rstrip("/")):
            return {"ok": False, "error": "invalid"}
        tmp = self.token_file + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token.strip())
        os.replace(tmp, self.token_file)
        _write_json(self.channel_file, {**_read_json(self.channel_file), "url": url.rstrip("/")})
        self.kick()
        return {"ok": True}

    def request_install(self) -> dict:
        if not self.has_credentials():
            return {"ok": False, "error": "no_credentials"}
        self.manual = True
        self.kick()
        return {"ok": True}

    def kick(self) -> None:
        self._wake.set()

    # ---------------------------------------------------------------- what HA sees
    def entity_state(self) -> dict:
        tag, _ = installed(self.current)
        return {KEY: {"installed": tag, "latest": self.latest or tag, "in_progress": self.in_progress,
                      "title": "F.R.I.D.A.Y. satellite", "release_url": None, "summary": None},
                "ota_channel": self.settings()["channel"], "ota_credentials": self.has_credentials()}

    @staticmethod
    def descriptors() -> list[dict]:
        return [{"key": KEY, "platform": "update", "name": "Software"},
                {"key": "ota_channel", "platform": "select", "name": "Update channel", "category": "config",
                 "options": list(CHANNELS), "icon": "mdi:source-branch"}]

    # ---------------------------------------------------------------- state
    def _save(self) -> None:
        try:
            _write_json(self.state_path, self.data, 0o600)
        except OSError as err:
            LOG.warning("OTA state save failed: %s", type(err).__name__)

    def _attempts(self) -> retry.Attempts | None:
        a = self.data.get("attempts")
        return retry.Attempts.from_dict(a) if isinstance(a, dict) and a.get("version") else None

    async def _changed(self) -> None:
        if self._on_change:
            try:
                await self._on_change()
            except Exception as err:  # noqa: BLE001 - HA being away never stops the OTA
                LOG.warning("OTA state push failed: %s", type(err).__name__)

    async def _ring(self, body: dict) -> None:
        try:
            await self.program.command(body)
        except Exception as err:  # noqa: BLE001 - the program may be restarting
            LOG.warning("OTA ring %s failed: %s", body, type(err).__name__)

    async def _pending(self, on: bool, status: dict | None) -> None:
        """The ring's `update_pending` lives in the program's memory: re-sent whenever /status disagrees."""
        if status is not None and bool(status.get("update_pendente")) == on:
            return
        await self._ring({"acao": "update_pending", "ligado": on})

    # ---------------------------------------------------------------- reports
    async def _report(self, tag: str, result: str, stage: str = "", error: str = "") -> bool:
        s = self.settings()
        body = {"sat_id": self.sat_id, "tag": tag, "result": result, "stage": stage, "error": error[:200],
                "protocol": UPDATE_PROTOCOL}
        try:
            await self.http.post_json(s["url"] + "/v1/ota/report", body, self.token())
            return True
        except Exception as err:  # noqa: BLE001 - kept and re-sent on the next poll
            LOG.warning("OTA report %s %s failed: %s", tag, result, type(err).__name__)
            self.data["unsent"] = body
            self._save()
            return False

    async def _flush_unsent(self) -> None:
        body = self.data.get("unsent")
        if isinstance(body, dict) and await self._report(body["tag"], body["result"], body.get("stage", ""),
                                                         body.get("error", "")):
            self.data.pop("unsent", None)
            self._save()

    async def report_activation(self) -> None:
        """The agent's start: report what the helper left for the activation this agent started."""
        tag = self.data.get("activating")
        if not tag:
            return
        res = _read_json(self.result_path)
        if res.get("tag") != tag or res.get("state") not in ("installed", "rolled_back", "failed"):
            return  # still activating (or the helper never got to write): next poll
        result = res["state"]
        self.data.pop("activating", None)
        if result == "installed":
            self.data.pop("attempts", None)
            self.data.pop("staged", None)
        else:
            self._fail_local(tag, res.get("stage") or "activate", res.get("error") or "")
        self._save()
        self.in_progress = False
        await self._report(tag, result, res.get("stage") or "", res.get("error") or "")
        await self._ring({"acao": "ring", "state": "off"})  # a failure before the switch never restarted the program
        if result == "installed":
            self.latest = None
            await self._pending(False, None)
            rc, _ = await self._helper(["prune"])
            if rc != 0:
                LOG.warning("OTA prune failed")
        await self._changed()

    def _fail_local(self, tag: str, stage: str, error: str) -> None:
        a = self._attempts()
        if a is None or a.version != tag:
            a = retry.reset(tag)
        retry.record_failure(a, stage, error, self._clock())
        self.data["attempts"] = a.to_dict()

    async def _failed(self, tag: str, f: Failure) -> None:
        LOG.warning("OTA %s failed at %s: %s", tag, f.stage, f.error)
        self._fail_local(tag, f.stage, f.error)
        self._save()
        await self._report(tag, "failed", f.stage, f.error)

    # ---------------------------------------------------------------- the step
    async def _status(self, music: bool) -> dict | None:
        try:
            return await self.program.status(music=music)
        except TypeError:  # an old ProgramClient without the argument
            return await self.program.status()

    def _safe(self, st: dict | None, manual: bool) -> bool:
        if not st or st.get("estado") != "parada":
            return False
        if manual:
            return True
        return not (st.get("timers") or st.get("alarme") or st.get("musica") or st.get("pendentes")
                    or st.get("fila_entregas"))

    def _heavy_ready(self, st: dict | None, now: float) -> bool:
        if not self._safe(st, False):
            self.idle_since, self._idle_mark = None, None
            return False
        mark = st.get("ultima_abertura")
        if self.idle_since is None or mark != self._idle_mark:  # a conversation came and went between polls
            self.idle_since, self._idle_mark = now, mark
        return now - self.idle_since >= HEAVY_IDLE_S

    async def _download(self, offer: dict) -> tuple[str, str | None]:
        url, token = self.settings()["url"], self.token()
        tag = offer["tag"]
        os.makedirs(self.downloads, exist_ok=True)
        pkg = os.path.join(self.downloads, f"{tag}-satellite.tar.gz")
        try:
            got = await self.http.download(url + offer["url"], token, pkg)
        except Failure:
            raise
        except Exception as err:  # noqa: BLE001
            raise Failure("download", type(err).__name__) from None
        if got != offer.get("sha256"):
            _rm(pkg)
            raise Failure("download", "sha256 mismatch")
        wheels = None
        if offer.get("wheels_url"):
            wheels = os.path.join(self.downloads, f"{tag}-wheels.tar.gz")
            try:
                await self.http.download(url + offer["wheels_url"], token, wheels)
            except Failure:
                raise
            except Exception as err:  # noqa: BLE001
                raise Failure("download", type(err).__name__) from None
        return pkg, wheels

    async def _prepare(self) -> None:
        """A satellite of an old image (before the OTA) has neither the downloads folder nor the helper. Those images
        give jose NOPASSWD:ALL, so the agent sets both up; the helper comes from the package being installed."""
        try:
            os.makedirs(self.downloads, exist_ok=True)
        except OSError:
            pass  # /var/lib/friday is root's on an old image
        if not os.access(self.downloads, os.W_OK):
            rc = await self._sudo(["install", "-d", "-o", "jose", "-g", "jose", "-m", "0755", self.downloads])
            if rc != 0 or not os.access(self.downloads, os.W_OK):
                raise Failure("prepare", "downloads folder")

    async def _install_helper(self, pkg: str) -> None:
        if os.path.exists(self.helper_path):
            return
        import tarfile
        import tempfile
        tmp = tempfile.mkdtemp(prefix="friday-ota-helper-")
        try:
            try:
                with tarfile.open(pkg, "r:*") as t:
                    t.extract("system/friday-ota-apply", tmp, filter="data")
            except (KeyError, tarfile.TarError, OSError) as err:
                raise Failure("prepare", f"helper not in package ({type(err).__name__})") from None
            rc = await self._sudo(["install", "-m", "0755", os.path.join(tmp, "system", "friday-ota-apply"),
                                   self.helper_path])
            if rc != 0:
                raise Failure("prepare", "helper install")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    async def _stage(self, offer: dict) -> None:
        await self._prepare()
        pkg, wheels = await self._download(offer)
        try:
            await self._install_helper(pkg)
            rc, out = await self._helper(["stage", pkg, offer["tag"]] + (["--wheels", wheels] if wheels else []))
        finally:
            _rm(pkg)
            if wheels:
                _rm(wheels)
        if rc != 0:
            m = next((_REFUSED.search(line) for line in reversed(out.splitlines()) if _REFUSED.search(line)), None)
            raise Failure(m.group(1), m.group(2)) if m else Failure("stage", f"exit {rc}")
        self.data.update(staged=offer["tag"], staged_sha256=offer["sha256"])
        self._save()

    async def step(self) -> None:
        if not self.has_credentials():
            return
        await self.report_activation()
        await self._flush_unsent()
        if self.data.get("activating"):
            return  # an activation still running (detached helper): never two at once
        s = self.settings()
        tag_now, lock = installed(self.current)
        params = {"sat_id": self.sat_id, "channel": s["channel"], "installed": tag_now or "",
                  "protocol": str(UPDATE_PROTOCOL), "lock_sha256": lock}
        try:
            offer = await self.http.get_json(s["url"] + "/v1/ota/offer", params, self.token())
        except Exception as err:  # noqa: BLE001 - the VM may be down: try again next poll
            LOG.info("OTA offer unavailable: %s", type(err).__name__)
            return
        tag = offer.get("tag") if isinstance(offer, dict) else None
        if not isinstance(tag, str) or not TAG.match(tag):
            if self.latest and self.latest != tag_now and offer.get("reason") in ("up_to_date", "newer_installed"):
                self.latest = None
                await self._pending(False, None)
                await self._changed()
            return
        manual = self.manual or bool(offer.get("install_requested"))
        now = self._clock()
        if not manual and not retry.may_try(self._attempts(), tag, now):
            return
        if self.latest != tag:
            self.latest = tag
            await self._changed()
        if not (self.data.get("staged") == tag and self.data.get("staged_sha256") == offer.get("sha256")):
            if not SHA256.match(str(offer.get("sha256") or "")) or not str(offer.get("url", "")).startswith("/"):
                return
            try:
                await self._stage(offer)
            except Failure as f:
                self.manual = False
                await self._failed(tag, f)
                return
        st = await self._status(music=True)
        if manual:
            ready = self._safe(st, True)
        elif not offer.get("auto_install", True):
            ready = False
        elif offer.get("heavy"):
            ready = self._heavy_ready(st, now)
        else:
            ready = self._safe(st, False)
        if not ready:
            await self._pending(True, st)
            return
        await self._activate(tag)

    async def _activate(self, tag: str) -> None:
        self.manual = False
        self.idle_since = None
        self.in_progress = True
        await self._changed()
        await self._ring({"acao": "ring", "state": "updating"})
        self.data["activating"] = tag
        self._save()
        rc, out = await self._helper(["activate", tag])
        if rc != 0:  # refused before detaching: nothing switched
            self.data.pop("activating", None)
            self.in_progress = False
            m = next((_REFUSED.search(line) for line in reversed(out.splitlines()) if _REFUSED.search(line)), None)
            await self._ring({"acao": "ring", "state": "off"})
            await self._failed(tag, Failure(m.group(1), m.group(2)) if m else Failure("activate", f"exit {rc}"))
            await self._changed()
        # rc 0: the helper runs detached and will restart this agent; the next start reports

    # ---------------------------------------------------------------- start + loop
    async def startup(self) -> None:
        """Every start: an activation cut by a reboot goes back (the helper decides), then the result is reported."""
        if os.path.exists(self.helper_path):
            rc, _ = await self._helper(["recover"])
            if rc != 0:
                LOG.warning("OTA recover failed")
        if self.has_credentials():
            await self.report_activation()

    async def run(self) -> None:
        await self.startup()
        while True:
            try:
                await self.step()
            except Exception as err:  # noqa: BLE001 - the loop never dies
                LOG.warning("OTA step failed: %s", type(err).__name__)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), POLL_SATELLITE_S)
            except asyncio.TimeoutError:
                pass


async def _sudo(argv: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec("sudo", "-n", *argv, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.DEVNULL)
    return await proc.wait()


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
