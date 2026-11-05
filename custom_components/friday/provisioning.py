"""Provisioning of a newly adopted satellite (spec 2026-11-05 §6.3, §9, §10).

After the spoken-code adoption the integration does, in order: `info` (ask the satellite who it is), `registry`
(satellites.json, what used to be hand-written), `sync_key` (the nightly-upload key on the server VM), `update`
and `voices` (one-time bundles from the server agent, applied by the satellite) and `onboarding` (the satellite
tests the wake word and opens the presentation conversation). Each step has up to 3 attempts; the state of every
step is persisted so a reload or a Home Assistant restart never repeats a finished step.

The legacy satellite (no `provisioned` key in its subentry) never enters here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import os
import time
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.logbook import async_log_entry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.storage import Store

from .const import CONF_AREA_ID, DEVICE_KIND_SERVER, DOMAIN

_LOGGER = logging.getLogger(__name__)

SATELLITES_JSON = "/share/friday/satellites.json"  # constant so tests can point it elsewhere
STORE_KEY = "friday.provisioning"
STEPS = ("info", "registry", "sync_key", "update", "voices", "onboarding")
ATTEMPTS = 3
RETRY_DELAYS = (5, 30, 120)  # wait before attempt 2, attempt 3 (the third value is spare)
BUNDLE_TIMEOUT_S = 480  # download (300) + restart wait (120) + margin
CONNECT_WAIT_S = 90

DONE, FAILED = "done", "failed"


class StepError(Exception):
    """A step failed; the message is a short reason for the log."""


def write_registry(path: str, sat_id: str, fields: dict[str, Any], defaults: dict[str, Any] | None = None) -> None:
    """Merge `fields` into the satellite's entry of satellites.json; other entries untouched. Atomic, 600, and a
    `.bak.<date>` copy the first time the file is changed each day."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        raise StepError("registry_corrupt")
    entry = dict(data.get(sat_id) or defaults or {})
    entry.update(fields)
    if data.get(sat_id) == entry:
        return
    data[sat_id] = entry
    if os.path.exists(path):
        bak = f"{path}.bak.{time.strftime('%Y%m%d')}"
        if not os.path.exists(bak):
            with open(path, "rb") as src, open(bak, "wb") as dst:
                dst.write(src.read())
            os.chmod(bak, 0o600)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


CLOSE_SETTLE_S = 3  # the voice enrollment opens right after the presentation closes; `ready` waits for it


class Orchestrator:
    """One per adopted satellite; survives config entry reloads (kept in hass.data)."""

    def __init__(self, hass: HomeAssistant, sat_id: str, subentry_id: str, sleep: Callable = asyncio.sleep) -> None:
        self.hass, self.sat_id, self.subentry_id = hass, sat_id, subentry_id
        self._sleep = sleep
        self._store: Store = Store(hass, 1, STORE_KEY)
        self._lock = asyncio.Lock()
        self.info: dict = {}
        self.people: list[str] = []
        self.ready_pending = False  # the area was chosen; waiting for the presentation conversation to close

    # ---- helpers
    def _entry(self):
        return next(e for e in self.hass.config_entries.async_entries(DOMAIN) if self.subentry_id in e.subentries)

    def connection(self):
        return self._entry().runtime_data.get(self.subentry_id)

    def server_connection(self):
        for c in self._entry().runtime_data.values():
            hello = c.hello
            if c.connected and hello is not None and hello.device.get("kind") == DEVICE_KIND_SERVER:
                return c
        return None

    async def _wait_connected(self, timeout: float = CONNECT_WAIT_S):
        waited = 0.0
        while waited <= timeout:
            c = self.connection()
            if c is not None and c.connected:
                return c
            await self._sleep(2)
            waited += 2
        raise StepError("satellite_offline")

    async def _load(self) -> dict:
        return (await self._store.async_load()) or {}

    async def _save_step(self, step: str, status: str, reason: str | None = None) -> None:
        data = await self._load()
        rec = data.setdefault(self.sat_id, {"steps": {}, "failed": {}})
        rec["steps"][step] = status
        if status == FAILED:
            rec["failed"][step] = reason or "error"
        else:
            rec["failed"].pop(step, None)
        await self._store.async_save(data)

    async def steps_state(self) -> dict:
        return ((await self._load()).get(self.sat_id) or {}).get("steps", {})

    async def say(self, keys: list[str]) -> None:
        """Pre-recorded phrase on the satellite; if it is not connected or refuses, nothing else depends on it."""
        c = self.connection()
        if c is None or not c.connected:
            return
        try:
            await c.send("say", timeout=15, keys=keys)
        except HomeAssistantError as err:
            _LOGGER.debug("Satellite %s phrase %s not played: %s", self.sat_id, keys, err)

    def _log(self, message: str) -> None:
        async_log_entry(self.hass, f"Satellite {self.sat_id}", message, DOMAIN)

    # ---- steps
    async def _step_info(self) -> None:
        c = await self._wait_connected()
        self.info = await c.send("provision_info", timeout=15)
        if not self.info.get("program_password"):
            raise StepError("no_info")

    def _need_info(self) -> dict:
        if not self.info:
            raise StepError("no_info")
        return self.info

    async def _step_registry(self) -> None:
        info = self._need_info()
        await self.hass.async_add_executor_job(
            write_registry, SATELLITES_JSON, self.sat_id,
            {"ip": info.get("ip", ""), "senha": info["program_password"]}, {"comodo": ""})

    async def _step_sync_key(self) -> None:
        info = self._need_info()
        server = self.server_connection()
        if server is None:
            raise StepError("server_offline")
        await server.send("register_sync_key", timeout=20, sat_id=self.sat_id, pubkey=info.get("sync_pubkey", ""))

    async def _bundle(self, kind: str) -> dict:
        server = self.server_connection()
        if server is None:
            raise StepError("server_offline")
        bundle = await server.send("prepare_bundle", timeout=120, kind=kind, sat_id=self.sat_id)
        c = await self._wait_connected()
        await c.send("apply_bundle", timeout=BUNDLE_TIMEOUT_S, kind=kind, url=bundle["url"], sha256=bundle["sha256"])
        return bundle

    async def _step_update(self) -> None:
        await self._bundle("update")
        await self._wait_connected()  # the agent restarts itself after an update

    async def _step_voices(self) -> None:
        try:
            bundle = await self._bundle("voices")
        except HomeAssistantError as err:
            if str(err) == "source_missing":  # no voices on the VM yet: nothing to deliver, not a failure
                self.people = []
                return
            raise
        self.people = [p for p in bundle.get("people", []) if isinstance(p, str)]

    async def _step_onboarding(self) -> None:
        failed = sorted((await self._load()).get(self.sat_id, {}).get("failed", {}), key=STEPS.index)
        areas = [{"id": a.id, "name": a.name} for a in ar.async_get(self.hass).async_list_areas()]
        c = await self._wait_connected()
        await c.send("start_onboarding", timeout=30,
                     facts={"areas": areas, "known_voices": self.people, "failed_steps": failed})

    # ---- running
    async def _attempt(self, step: str) -> None:
        fn = getattr(self, f"_step_{step}")
        last: Exception | None = None
        for n in range(ATTEMPTS):
            if n:
                await self._sleep(RETRY_DELAYS[n - 1])
            try:
                await fn()
                await self._save_step(step, DONE)
                return
            except (HomeAssistantError, StepError, KeyError, OSError) as err:
                last = err
                _LOGGER.warning("Provisioning %s step %s attempt %s failed: %s", self.sat_id, step, n + 1, err)
        reason = str(last) or type(last).__name__
        await self._save_step(step, FAILED, reason)
        self._log(f"provisioning step {step} failed: {reason}")
        await self.say(["step_failed"])
        persistent_notification.async_create(
            self.hass, f"Satellite {self.sat_id}: the step '{step}' failed ({reason}). "
                       "Call friday.retry_provisioning to try again.",
            title="F.R.I.D.A.Y. satellite setup", notification_id=f"friday_provisioning_{self.sat_id}_{step}")

    async def run(self, retry: bool = False) -> None:
        """Run the pending steps (all of them for a new satellite; only the failed ones with retry=True)."""
        if self._lock.locked():
            return
        async with self._lock:
            state = await self.steps_state()
            for step in STEPS:
                if state.get(step) == DONE:
                    continue
                if retry and state.get(step) != FAILED:
                    continue
                if step in ("registry", "sync_key") and not self.info:
                    try:
                        await self._step_info()  # a retried run in a fresh process has no info yet
                    except (HomeAssistantError, StepError) as err:
                        await self._save_step(step, FAILED, f"no_info: {err}")
                        continue
                persistent_notification.async_dismiss(self.hass, f"friday_provisioning_{self.sat_id}_{step}")
                await self._attempt(step)
                state = await self.steps_state()

    # ---- the area answer
    async def area_selected(self, area_id: str) -> None:
        areas = ar.async_get(self.hass)
        area = areas.async_get_area(area_id)
        if area is None:
            _LOGGER.warning("Satellite %s picked an unknown area", self.sat_id)
            return
        entry = self._entry()
        sub = entry.subentries[self.subentry_id]
        await self.hass.async_add_executor_job(write_registry, SATELLITES_JSON, self.sat_id, {"comodo": area.name})
        dev = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id, config_subentry_id=self.subentry_id, identifiers={(DOMAIN, self.sat_id)})
        dr.async_get(self.hass).async_update_device(dev.id, area_id=area_id)
        self._log(f"placed in {area.name}")
        self.ready_pending = True
        # last: this reloads the entry; `provisioned` flips when the presentation conversation closes
        self.hass.config_entries.async_update_subentry(
            entry, sub, title=f"Satellite {area.name}", data={**sub.data, CONF_AREA_ID: area_id})

    async def conversation_closed(self) -> None:
        """Called when the satellite's program_state goes back to idle: the satellite says `ready` once (only when no
        step failed) and the subentry becomes provisioned."""
        if not self.ready_pending:
            return
        await self._sleep(CLOSE_SETTLE_S)
        c = self.connection()
        if not self.ready_pending or (c is not None and getattr(c, "state", {}).get("program_state", "idle") != "idle"):
            return  # reopened (the voice enrollment right after the presentation): the next idle tries again
        self.ready_pending = False
        failed = any(v == FAILED for v in (await self.steps_state()).values())
        if not failed:
            await self.say(["ready"])
        entry = self._entry()
        sub = entry.subentries[self.subentry_id]
        self.hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, "provisioned": True})
