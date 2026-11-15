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
import shutil
import tarfile
import tempfile
import time
from typing import Awaitable, Callable

from friday_agent_core.ota import retry
from friday_agent_core.ota.constants import HEALTH_TIMEOUT_S, HEAVY_IDLE_S, POLL_SATELLITE_S, UPDATE_PROTOCOL

LOG = logging.getLogger("friday_satellite_agent.ota")
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT = SHA256  # of the VM's TLS certificate (DER)
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
ACTIVATE_DEADLINE_S = HEALTH_TIMEOUT_S + 600  # no result by then: recover, and report it lost
ACTIVATING_POLL_S = 15
MAX_UNSENT = 20
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

    @staticmethod
    def _ssl(fingerprint: str):
        """The VM's self-signed certificate, pinned by its SHA-256 (no CA): anything else is refused."""
        import aiohttp
        return aiohttp.Fingerprint(bytes.fromhex(fingerprint))

    async def get_json(self, url: str, params: dict, token: str, fingerprint: str) -> dict:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._t)) as s:
            async with s.get(url, params=params, headers=self._h(token), ssl=self._ssl(fingerprint)) as r:
                if r.status != 200:
                    raise Failure("offer", f"http {r.status}")
                return await r.json()

    async def post_json(self, url: str, body: dict, token: str, fingerprint: str) -> dict:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._t)) as s:
            async with s.post(url, json=body, headers=self._h(token), ssl=self._ssl(fingerprint)) as r:
                if r.status != 200:
                    raise Failure("report", f"http {r.status}")
                return await r.json()

    async def download(self, url: str, token: str, dest: str, fingerprint: str) -> str:
        """Streams to `dest` (via .part); returns the sha256 of what arrived."""
        import aiohttp
        h = hashlib.sha256()
        part = dest + ".part"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._dt)) as s:
            async with s.get(url, headers=self._h(token), ssl=self._ssl(fingerprint)) as r:
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
                 on_change: Callable[[], Awaitable] | None = None, legacy_version: str | None = None) -> None:
        self.sat_id, self.program, self.http = sat_id, program, http or HttpClient()
        # the version line of the old layout (no release tag): only what HA shows, so the update entity has an
        # installed version and offers Install; the VM still gets installed="" (FINDING 3, 14/11)
        self.legacy_version = legacy_version
        self._helper, self._clock = helper, clock
        self.state_path, self.result_path, self.downloads = state_path, result_path, downloads
        self.channel_file, self.token_file, self.current = channel_file, token_file, current
        self.helper_path, self._sudo = helper_path, sudo or _sudo
        self._on_change = on_change
        self.data = _read_json(state_path)
        if isinstance(self.data.get("activating"), str):  # written by an earlier agent
            self.data["activating"] = {"tag": self.data["activating"], "at": 0}
        if isinstance(self.data.get("unsent"), dict):
            self.data["unsent"] = [self.data["unsent"]]
        # `ota_install` from HA: asked once to the VM (`install=1`, which also zeroes its attempts), then bound to the
        # tag the VM offers for it; never to a release that shows up later
        self.manual_asked = False
        self.manual_tag: str | None = None
        self.idle_since: float | None = None  # heavy updates: start of the current continuous idle stretch
        self._idle_mark = None  # the program's `ultima_abertura` when idle_since was set
        self.latest: str | None = self.data.get("latest") or self.data.get("staged")
        self.in_progress = bool(self.data.get("activating"))
        self._no_pending_cmd: str | None = None  # the installed tag whose program refused `update_pending`
        self._wake = asyncio.Event()

    # ---------------------------------------------------------------- settings
    def settings(self) -> dict:
        d = _read_json(self.channel_file)
        ch = d.get("channel") if d.get("channel") in CHANNELS else "stable"
        url = d.get("url") if isinstance(d.get("url"), str) else ""
        fp = d.get("fingerprint") if isinstance(d.get("fingerprint"), str) and FINGERPRINT.match(d["fingerprint"]) else ""
        return {"channel": ch, "url": url.rstrip("/") if url.startswith("https://") else "", "fingerprint": fp}

    def token(self) -> str:
        try:
            with open(self.token_file, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def has_credentials(self) -> bool:
        s = self.settings()
        return bool(self.token() and s["url"] and s["fingerprint"])

    def set_channel(self, channel: str) -> dict:
        if channel not in CHANNELS:
            return {"ok": False, "error": "invalid"}
        _write_json(self.channel_file, {**_read_json(self.channel_file), "channel": channel})
        self.kick()
        return {"ok": True}

    def set_credentials(self, token, url, fingerprint=None) -> dict:
        """HTTPS only, with the VM certificate's SHA-256 (it came with the token over HA's authenticated channel)."""
        if not isinstance(token, str) or not token.strip() or len(token) > 512 or not isinstance(url, str) \
                or not re.match(r"^https://[^/\s]+$", url.rstrip("/")) or not isinstance(fingerprint, str) \
                or not FINGERPRINT.match(fingerprint.lower()):
            return {"ok": False, "error": "invalid"}
        tmp = self.token_file + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token.strip())
        os.replace(tmp, self.token_file)
        _write_json(self.channel_file, {**_read_json(self.channel_file), "url": url.rstrip("/"),
                                        "fingerprint": fingerprint.lower()})
        self.kick()
        return {"ok": True}

    def request_install(self) -> dict:
        if not self.has_credentials():
            return {"ok": False, "error": "no_credentials"}
        if self.data.get("activating"):
            return {"ok": False, "error": "in_progress"}
        self.manual_asked, self.manual_tag = True, None
        self.kick()
        return {"ok": True}

    def kick(self) -> None:
        self._wake.set()

    # ---------------------------------------------------------------- what HA sees
    def entity_state(self) -> dict:
        tag, _ = installed(self.current)
        return {KEY: {"installed": tag or self.legacy_version or None, "latest": self.latest or tag, "in_progress": self.in_progress,
                      "title": "F.R.I.D.A.Y. satellite", "release_url": None, "summary": None},
                "ota_channel": self.settings()["channel"], "ota_credentials": self.has_credentials()}

    @staticmethod
    def descriptors() -> list[dict]:
        return [{"key": KEY, "platform": "update", "name": "Software"},
                {"key": "ota_channel", "platform": "select", "name": "Update channel", "category": "config",
                 "options": list(CHANNELS), "icon": "mdi:source-branch", "hidden": True}]  # Advanced step (14/11)

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

    def _set_latest(self, tag: str | None) -> bool:
        if self.latest == tag:
            return False
        self.latest = tag
        self.data["latest"] = tag
        self._save()
        return True

    async def _ring(self, body: dict) -> bool:
        try:
            await self.program.command(body)
            return True
        except Exception as err:  # noqa: BLE001 - the program may be restarting
            LOG.warning("OTA ring %s failed: %s", body, type(err).__name__)
            return False

    async def _pending(self, on: bool, status: dict | None) -> None:
        """The ring's `update_pending` lives in the program's memory: re-sent whenever /status disagrees. A program
        older than the OTA has no such command (nor the key): asked once per installed release, then left alone."""
        tag_now, _ = installed(self.current)
        if self._no_pending_cmd == (tag_now or ""):
            return
        if status is not None and "update_pendente" in status and bool(status["update_pendente"]) == on:
            return
        if not await self._ring({"acao": "update_pending", "ligado": on}) and status is not None \
                and "update_pendente" not in status:
            self._no_pending_cmd = tag_now or ""

    async def _ring_off_if_idle(self) -> None:
        """Only when nothing else owns the ring: `off` over a live conversation would blank its light."""
        st = await self._status(music=False)
        if st is not None and st.get("estado") == "parada":
            await self._ring({"acao": "ring", "state": "off"})

    # ---------------------------------------------------------------- reports (a queue: none is lost or replaced)
    def _queue(self, tag: str, result: str, stage: str = "", error: str = "") -> None:
        q = self.data.setdefault("unsent", [])
        del q[:-(MAX_UNSENT - 1)]  # the VM away for days: the newest reports are the ones that matter
        q.append(
            {"sat_id": self.sat_id, "tag": tag, "result": result, "stage": stage[:200], "error": error[:200],
             "protocol": UPDATE_PROTOCOL})

    async def _flush(self) -> None:
        q = self.data.get("unsent") or []
        s = self.settings()
        while q:
            try:
                await self.http.post_json(s["url"] + "/v1/ota/report", q[0], self.token(), s["fingerprint"])
            except Exception as err:  # noqa: BLE001 - kept and re-sent on the next poll
                LOG.warning("OTA report %s %s failed: %s", q[0].get("tag"), q[0].get("result"), type(err).__name__)
                break
            q.pop(0)
            self._save()
        if not q:
            self.data.pop("unsent", None)
            self._save()

    def _activating(self) -> dict | None:
        a = self.data.get("activating")
        return a if isinstance(a, dict) and a.get("tag") else None

    async def report_activation(self) -> None:
        """Report what the helper left for the activation this agent started (only a result written after it)."""
        a = self._activating()
        if not a:
            return
        res = _read_json(self.result_path)
        fresh = res.get("tag") == a["tag"] and float(res.get("at") or 0) >= float(a.get("at") or 0) - 5
        final = fresh and res.get("state") in ("installed", "rolled_back", "failed")
        if not final and self._clock() - float(a.get("at") or 0) > ACTIVATE_DEADLINE_S:
            if os.path.exists(self.helper_path):
                await self._helper(["recover"])  # a cut activation goes back; then its result is read
                res = _read_json(self.result_path)
                fresh = res.get("tag") == a["tag"] and float(res.get("at") or 0) >= float(a.get("at") or 0) - 5
                final = fresh and res.get("state") in ("installed", "rolled_back", "failed")
            if not final and not (fresh and res.get("state") == "activating"):
                res, final = {"state": "failed", "stage": "activate", "error": "lost"}, True
        if not final:
            return  # still activating: next poll
        tag, result = a["tag"], res["state"]
        stage, error = str(res.get("stage") or ""), str(res.get("error") or "")
        self.data.pop("activating", None)
        if result == "installed":
            self.data.pop("attempts", None)
            self.data.pop("staged", None)
        else:
            self._fail_local(tag, stage or "activate", error)
            if stage == "args":  # e.g. the release folder vanished: stage it again next time
                self.data.pop("staged", None)
        self._queue(tag, result, stage, error)  # saved together with the pop: a crash in between loses nothing
        self._save()
        self.in_progress = False
        await self._flush()
        await self._ring_off_if_idle()  # a failure before the switch never restarted the program
        if result == "installed":
            self._set_latest(None)
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
        self.manual_tag = None
        self._fail_local(tag, f.stage, f.error)
        self._queue(tag, "failed", f.stage, f.error)
        self._save()
        await self._flush()

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
        st = self.settings()
        url, token, fp = st["url"], self.token(), st["fingerprint"]
        tag = offer["tag"]
        pkg = os.path.join(self.downloads, f"{tag}-satellite.tar.gz")
        try:
            got = await self.http.download(url + offer["url"], token, pkg, fp)
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
                await self.http.download(url + offer["wheels_url"], token, wheels, fp)
            except Failure:
                _rm(pkg)
                raise
            except Exception as err:  # noqa: BLE001
                _rm(pkg)
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
            raise _refusal(out, "stage", rc)
        self.data.update(staged=offer["tag"], staged_sha256=offer["sha256"])
        self._save()

    async def _keep_pending(self, tag_now: str | None) -> None:
        """Found (or downloaded) and not installed — waiting for a retry, exhausted, held by the VM: the ring says so."""
        if self.latest and self.latest != tag_now:
            await self._pending(True, await self._status(music=False))

    async def step(self) -> None:
        if not self.has_credentials():
            return
        await self.report_activation()
        await self._flush()
        if self._activating():
            return  # an activation still running (detached helper): never two at once
        s = self.settings()
        tag_now, lock = installed(self.current)
        params = {"sat_id": self.sat_id, "channel": s["channel"], "installed": tag_now or "",
                  "protocol": str(UPDATE_PROTOCOL), "lock_sha256": lock}
        asked, self.manual_asked = self.manual_asked, False  # a press during the request below counts again
        if asked:
            params["install"] = "1"  # the VM zeroes its attempts and offers the target to install now
        try:
            offer = await self.http.get_json(s["url"] + "/v1/ota/offer", params, self.token(), s["fingerprint"])
        except Exception as err:  # noqa: BLE001 - the VM may be down: try again next poll
            LOG.info("OTA offer unavailable: %s", type(err).__name__)
            self.manual_asked = self.manual_asked or asked
            return
        tag = offer.get("tag") if isinstance(offer, dict) else None
        if not isinstance(tag, str) or not TAG.match(tag):
            self.manual_tag = None
            if offer.get("reason") in ("up_to_date", "newer_installed") if isinstance(offer, dict) else False:
                if self._set_latest(None):
                    await self._pending(False, None)
                    await self._changed()
            else:
                await self._keep_pending(tag_now)
            return
        if asked:
            self.manual_tag = tag
        manual = self.manual_tag == tag or bool(offer.get("install_requested"))
        now = self._clock()
        if self._set_latest(tag):
            await self._changed()
        if not manual and not retry.may_try(self._attempts(), tag, now):
            await self._keep_pending(tag_now)
            return
        if not (self.data.get("staged") == tag and self.data.get("staged_sha256") == offer.get("sha256")):
            if not SHA256.match(str(offer.get("sha256") or "")) or not str(offer.get("url", "")).startswith("/"):
                return
            try:
                await self._stage(offer)
            except Failure as f:
                await self._failed(tag, f)
                await self._keep_pending(tag_now)
                return
            except Exception as err:  # noqa: BLE001 - recorded like any failure: never a download every poll
                await self._failed(tag, Failure("stage", type(err).__name__))
                await self._keep_pending(tag_now)
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
        self.manual_tag = None
        self.idle_since = None
        self.in_progress = True
        await self._changed()
        await self._ring({"acao": "ring", "state": "updating"})
        self.data["activating"] = {"tag": tag, "at": self._clock()}
        self._save()
        try:
            rc, out = await self._helper(["activate", tag])
        except Exception as err:  # noqa: BLE001
            rc, out = 2, f"friday-ota-apply: refused at activate: {type(err).__name__}"
        if rc != 0:  # refused before detaching: nothing switched
            self.data.pop("activating", None)
            self.in_progress = False
            f = _refusal(out, "activate", rc)
            if f.stage == "args":
                self.data.pop("staged", None)
            await self._ring_off_if_idle()
            await self._failed(tag, f)
            await self._changed()
        # rc 0: the helper runs detached and restarts this agent on a switch; the result is read at start or soon

    # ---------------------------------------------------------------- start + loop
    async def startup(self) -> None:
        """Every start: an activation cut by a reboot goes back (the helper decides), then the result is reported."""
        if os.path.exists(self.helper_path):
            rc, _ = await self._helper(["recover"])
            if rc != 0:
                LOG.warning("OTA recover failed")
        if self.has_credentials():
            await self.report_activation()
        if self._activating():  # back mid-activation (the health check runs): HA and the ring show the install
            await self._ring({"acao": "ring", "state": "updating"})
            await self._changed()

    async def run(self) -> None:
        await self.startup()
        while True:
            try:
                await self.step()
            except Exception as err:  # noqa: BLE001 - the loop never dies
                LOG.warning("OTA step failed: %s", type(err).__name__)
            self._wake.clear()
            try:  # an activation in flight is checked often: a failure before the switch shows up in seconds
                await asyncio.wait_for(self._wake.wait(), ACTIVATING_POLL_S if self._activating() else POLL_SATELLITE_S)
            except asyncio.TimeoutError:
                pass


def _refusal(out: str, default_stage: str, rc: int) -> Failure:
    m = next((m for m in (_REFUSED.search(line) for line in reversed(out.splitlines())) if m), None)
    return Failure(m.group(1), m.group(2)) if m else Failure(default_stage, f"exit {rc}")


async def _sudo(argv: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec("sudo", "-n", *argv, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.DEVNULL)
    return await proc.wait()


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
