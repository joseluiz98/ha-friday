"""OTA orchestrator on the VM (spec 2026-11-10 §6, §13): GitHub → packages → offers to satellites → reports.

Satellites pull (`offer`) and report (`report`); the orchestrator never pushes to them. It decides the target of each
channel, the protocol gate, retries and the alpha soak; the server-side installer (Task 10) is plugged in later.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable

from friday_agent_core.ota import channels as ch
from friday_agent_core.ota import order
from friday_agent_core.ota import retry
from friday_agent_core.ota.constants import KEEP_RELEASES

from . import notify as nt
from . import packages as pk
from . import wheels as wh

LOG = logging.getLogger("friday_server_agent.ota")
OTA_ROOT = "/srv/friday/ota"
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
RESULTS = ("installed", "rolled_back", "failed")


def parse_report(data: dict) -> dict:
    """Contract point (update protocol v1): validates a satellite's report. Raises ValueError naming the bad key."""
    if not isinstance(data, dict):
        raise ValueError("report")
    sat, tag, result = data.get("sat_id"), data.get("tag"), data.get("result")
    if not isinstance(sat, str) or not re.match(r"^sat-[0-9a-f]{6}$", sat):
        raise ValueError("sat_id")
    if not isinstance(tag, str) or not TAG.match(tag):
        raise ValueError("tag")
    if result not in RESULTS:
        raise ValueError("result")
    proto = data.get("protocol", 1)
    if not isinstance(proto, int) or isinstance(proto, bool):
        raise ValueError("protocol")
    return {"sat_id": sat, "tag": tag, "result": result, "stage": str(data.get("stage") or "")[:200],
            "error": str(data.get("error") or "")[:200], "protocol": proto}


def offer_payload(tag: str | None, sha: str = "", sha256: str = "", size: int = 0, wheels: bool = False,
                  heavy: bool = False, install_requested: bool = False, auto_install: bool = True,
                  reason: str = "") -> dict:
    """Contract point (update protocol v1): the offer a satellite receives."""
    if tag is None:
        return {"tag": None, "reason": reason, "auto_install": auto_install, "install_requested": False}
    return {"tag": tag, "sha": sha, "sha256": sha256, "size": size,
            "url": f"/v1/ota/package/{tag}/satellite.tar.gz",
            "wheels_url": f"/v1/ota/package/{tag}/wheels.tar.gz" if wheels else None,
            "heavy": heavy, "install_requested": install_requested, "auto_install": auto_install, "reason": reason}


class Orchestrator:
    def __init__(self, store, tokens, watcher, repo, vault, notifier, build: Callable = pk.build_release,
                 wheels: Callable = wh.build, root: str = OTA_ROOT, clock: Callable[[], float] = time.time,
                 prune: Callable = pk.prune_packages):
        self.store, self.tokens, self.watcher, self.repo = store, tokens, watcher, repo
        self.vault, self.notifier, self._build, self._wheels = vault, notifier, build, wheels
        self.root, self._clock, self._prune = root, clock, prune
        self.meta: dict[str, dict] = {}  # tag -> package meta (built this process or found on disk)

    # ---- releases and packages
    def _eligible(self) -> list[ch.Release]:
        return ch.eligible([i.release for i in self.store.release_infos().values()])

    def target(self, channel: str) -> str | None:
        return ch.target_for(channel, self._eligible(), self.store.soak(), self._clock())

    def _channels(self) -> list[str]:
        return [d["channel"] for d in self.store.data["devices"].values()]

    def _targets(self) -> set[str]:
        out = {self.target("stable")}
        if "alpha" in self._channels():
            out.add(self.target("alpha"))
        return {t for t in out if t}

    def _keep(self) -> set[str]:
        keep = set(self._targets())
        keep |= {d["installed"] for d in self.store.data["devices"].values() if d.get("installed")}
        keep |= {s.get("installed_tag") for s in self.store.data["server"].values() if s.get("installed_tag")}
        stable = [r.tag for r in self._eligible() if ch.promoted(r, self.store.soak(), self._clock())]
        keep |= set(stable[-KEEP_RELEASES:])
        return {t for t in keep if t}

    def _package(self, tag: str) -> dict | None:
        if tag in self.meta:
            return self.meta[tag]
        infos = self.store.release_infos()
        if tag not in infos:
            return None
        try:
            meta = self._build(self.repo, tag, infos[tag].release.sha, self.vault, self.root, self._wheels)
        except Exception as err:  # noqa: BLE001 - a broken release must not stop the others
            LOG.error("OTA: package %s failed: %s", tag, type(err).__name__)
            self.store.event("server", tag, "package_failed", "build", type(err).__name__)
            return None
        self.meta[tag] = meta
        return meta

    def poll(self, full: bool = False) -> dict:
        """One cycle. GitHub errors keep the previous list (the watcher raises, we log and go on)."""
        with self.store.lock:
            try:
                self.store.set_releases(self.watcher.poll(self.store.release_infos(), full=full))
            except Exception as err:  # noqa: BLE001
                LOG.warning("OTA: GitHub poll failed: %s", type(err).__name__)
            built = [t for t in sorted(self._targets(), key=ch.version_key) if self._package(t)]
            try:
                self._prune(self.root, self._keep())
            except OSError as err:
                LOG.warning("OTA: prune failed: %s", type(err).__name__)
            self.store.save()
            pushes = self.store.data["pushes"]
            self.notifier.flush(pushes)
            self.store.save()
            return {"targets": {c: self.target(c) for c in ch.CHANNELS}, "built": built}

    def package_for_update(self) -> str | None:
        """The stable package, for the frozen `prepare_bundle kind=update` path (provisioning of a new satellite)."""
        tag = self.target("stable")
        meta = self._package(tag) if tag else None
        return meta["satellite"]["path"] if meta else None

    def package_file(self, tag: str, name: str) -> str | None:
        if not TAG.match(tag or "") or name not in ("satellite.tar.gz", "wheels.tar.gz"):
            return None
        meta = self.meta.get(tag) or self._package(tag)
        if not meta:
            return None
        path = meta["satellite"]["path"] if name == "satellite.tar.gz" else meta["satellite"].get("wheels")
        return path if path and os.path.exists(path) else None

    # ---- satellites
    def offer(self, sat_id: str, channel: str, installed: str | None, protocol: int, lock_sha256: str) -> dict:
        now = self._clock()
        with self.store.lock:
            d = self.store.device(sat_id)
            d.update(channel=channel if channel in ch.CHANNELS else "stable", installed=installed or d["installed"],
                     protocol=protocol, lock_sha256=lock_sha256, last_seen=now)
            self.store.save()
            auto, requested = self.store.auto_install, bool(d["install_requested"])
            tag = self.target(d["channel"])
            if not tag or tag == d["installed"]:
                return offer_payload(None, auto_install=auto, reason="up_to_date")
            info = self.store.release_infos()[tag]
            man = self.repo.manifest(info.release.sha)
            if man is not None and not order.satellite_ready(man, int(self.store.data["server_protocol"])):
                return offer_payload(None, auto_install=auto, reason="waiting_server")
            a = self.store.attempts(sat_id)
            if not requested and not retry.may_try(a, tag, now):
                return offer_payload(None, auto_install=auto, reason="exhausted" if a and a.exhausted else "retry_later")
            meta = self._package(tag)
            if not meta:
                return offer_payload(None, auto_install=auto, reason="package_missing")
            sat = meta["satellite"]
            return offer_payload(tag, info.release.sha, sat["sha256"], sat["size"],
                                 wheels=sat.get("lock_sha256") != lock_sha256 and bool(sat.get("wheels")),
                                 heavy=bool(sat.get("heavy")), install_requested=requested, auto_install=auto)

    def report(self, data: dict) -> dict:
        r = parse_report(data)
        now = self._clock()
        with self.store.lock:
            d = self.store.device(r["sat_id"])
            d["last_report"] = {"at": now, **{k: r[k] for k in ("tag", "result", "stage", "error")}}
            alpha = d["channel"] == "alpha"
            if r["result"] == "installed":
                d.update(installed=r["tag"], attempts=None, install_requested=False, protocol=r["protocol"])
                if alpha:
                    self.store.soak_start(r["tag"], now)
            else:
                a = self.store.attempts(r["sat_id"])
                if a is None or a.version != r["tag"]:
                    a = retry.reset(r["tag"])
                retry.record_failure(a, r["stage"], r["error"], now)
                d["attempts"] = a.to_dict()
                d["install_requested"] = False
                if alpha:
                    self.store.soak_fail(r["tag"])
                if a.exhausted:
                    self.notifier.queue(nt.format_exhausted(r["sat_id"], r["tag"], r["stage"], r["error"], d["installed"]),
                                        self.store.data["pushes"])
            self.store.event(r["sat_id"], r["tag"], r["result"], r["stage"], r["error"])
            self.store.save()
        return {"ok": True}

    def install(self, sat_id: str) -> None:
        """Manual (HA button or voice): zero the attempts and ask the satellite to install on its next poll."""
        with self.store.lock:
            d = self.store.device(sat_id)
            d.update(install_requested=True, attempts=None)
            self.store.event(sat_id, self.target(d["channel"]), "install_requested")
            self.store.save()

    def set_auto(self, enabled: bool) -> None:
        with self.store.lock:
            self.store.data["auto_install"] = bool(enabled)
            self.store.save()

    def status(self) -> dict:
        devices = {s: {k: d.get(k) for k in ("channel", "installed", "install_requested", "last_seen", "last_report")}
                   for s, d in self.store.data["devices"].items()}
        return {"auto_install": self.store.auto_install, "targets": {c: self.target(c) for c in ch.CHANNELS},
                "devices": devices, "releases": sorted((t for t in self.store.data["releases"] if TAG.match(t)), key=ch.version_key)}
