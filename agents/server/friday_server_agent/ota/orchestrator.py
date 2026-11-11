"""OTA orchestrator on the VM (spec 2026-11-10 §6, §13): GitHub → packages → offers to satellites → reports.

Satellites pull (`offer`) and report (`report`); the orchestrator never pushes to them. It decides the target of each
channel, the protocol gate, retries and the alpha soak; the server-side installer (Task 10) is plugged in later.

Locking: `store.lock` only guards reading/writing the state (fast). Slow work — GitHub, git, package builds, Telegram —
runs outside it; builds are serialized by `_build_lock`, so one tag is never built twice at once.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Callable

from friday_agent_core.ota import channels as ch
from friday_agent_core.ota import order
from friday_agent_core.ota import retry
from friday_agent_core.ota.constants import KEEP_RELEASES, SOAK_S

from . import notify as nt
from . import packages as pk
from . import wheels as wh

LOG = logging.getLogger("friday_server_agent.ota")
OTA_ROOT = "/srv/friday/ota"
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
RESULTS = ("installed", "rolled_back", "failed")
BUILD_RETRY_S = 3600  # a release that failed to package is retried at most once an hour


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


def offer_payload(tag: str | None, sat_id: str = "", sha: str = "", sha256: str = "", size: int = 0,
                  wheels: bool = False, heavy: bool = False, install_requested: bool = False,
                  auto_install: bool = True, reason: str = "") -> dict:
    """Contract point (update protocol v1): the offer a satellite receives. URLs are followed as given."""
    if tag is None:
        return {"tag": None, "reason": reason, "auto_install": auto_install, "install_requested": False}
    q = f"?sat_id={sat_id}"
    return {"tag": tag, "sha": sha, "sha256": sha256, "size": size,
            "url": f"/v1/ota/package/{tag}/satellite.tar.gz{q}",
            "wheels_url": f"/v1/ota/package/{tag}/wheels.tar.gz{q}" if wheels else None,
            "heavy": heavy, "install_requested": install_requested, "auto_install": auto_install, "reason": reason}


def _newer(tag: str, installed: str | None) -> bool:
    if not installed or not TAG.match(installed):
        return True
    return ch.version_key(tag) > ch.version_key(installed)


class Orchestrator:
    def __init__(self, store, tokens, watcher, repo, vault, notifier, build: Callable = pk.build_release,
                 wheels: Callable = wh.build, root: str = OTA_ROOT, clock: Callable[[], float] = time.time,
                 prune: Callable = pk.prune_packages):
        self.store, self.tokens, self.watcher, self.repo = store, tokens, watcher, repo
        self.vault, self.notifier, self._build, self._wheels = vault, notifier, build, wheels
        self.root, self._clock, self._prune = root, clock, prune
        self.meta: dict[str, dict] = {}  # tag -> package meta
        self._manifests: dict[str, object] = {}  # sha -> Manifest | None (immutable per sha)
        self._build_failed: dict[str, float] = {}
        self._build_lock = threading.Lock()

    # ---- reads under the state lock
    def _eligible(self) -> list[ch.Release]:
        with self.store.lock:
            return ch.eligible([i.release for i in self.store.release_infos().values()])

    def target(self, channel: str) -> str | None:
        with self.store.lock:
            return ch.target_for(channel, self._eligible(), self.store.soak(), self._clock())

    def _targets(self) -> set[str]:
        with self.store.lock:
            channels = [d["channel"] for d in self.store.data["devices"].values()]
            out = {self.target("stable")}
            if "alpha" in channels:
                out.add(self.target("alpha"))
        return {t for t in out if t}

    def _installed(self) -> set[str]:
        with self.store.lock:
            out = {d["installed"] for d in self.store.data["devices"].values() if d.get("installed")}
            out |= {s.get("installed_tag") for s in self.store.data["server"].values() if s.get("installed_tag")}
        return {t for t in out if t}

    def _keep(self) -> set[str]:
        with self.store.lock:
            soak, now = self.store.soak(), self._clock()
            stable = [r.tag for r in self._eligible() if ch.promoted(r, soak, now)]
        return self._targets() | self._installed() | set(stable[-KEEP_RELEASES:])

    # ---- slow work, outside the state lock
    def _manifest(self, sha: str):
        if sha not in self._manifests:
            self._manifests[sha] = self.repo.manifest(sha)
        return self._manifests[sha]

    def _package(self, tag: str) -> dict | None:
        with self._build_lock:
            if tag in self.meta:
                return self.meta[tag]
            if self._clock() - self._build_failed.get(tag, -BUILD_RETRY_S) < BUILD_RETRY_S:
                return None
            with self.store.lock:
                info = self.store.release_infos().get(tag)
            if info is None:
                return None
            try:
                meta = self._build(self.repo, tag, info.release.sha, self.vault, self.root, self._wheels)
            except Exception as err:  # noqa: BLE001 - a broken release must not stop the others
                LOG.error("OTA: package %s failed: %s", tag, type(err).__name__)
                self._build_failed[tag] = self._clock()
                with self.store.lock:
                    self.store.event("server", tag, "package_failed", "build", type(err).__name__)
                return None
            self._build_failed.pop(tag, None)
            self.meta[tag] = meta
            return meta

    def poll(self, full: bool = False) -> dict:
        """One cycle. A down GitHub or repo keeps the previous state; nothing here raises."""
        try:
            self.repo.ensure()
            self.repo.fetch_tags()
        except Exception as err:  # noqa: BLE001
            LOG.warning("OTA: repo update failed: %s", type(err).__name__)
        with self.store.lock:
            known = self.store.release_infos()
        try:
            fresh = self.watcher.poll(known, full=full)
            with self.store.lock:
                self.store.set_releases(fresh)
                self.store.save()
        except Exception as err:  # noqa: BLE001
            LOG.warning("OTA: GitHub poll failed: %s", type(err).__name__)
        built = []
        for t in sorted(self._targets(), key=ch.version_key):
            if self._package(t):
                built.append(t)
                with self.store.lock:
                    sha = self.store.release_infos()[t].release.sha
                try:
                    self._manifest(sha)
                except Exception as err:  # noqa: BLE001 - offer() retries it
                    LOG.warning("OTA: manifest %s failed: %s", t, type(err).__name__)
        keep = self._keep()
        with self._build_lock:
            try:
                for tag in self._prune(self.root, keep):
                    self.meta.pop(tag, None)
            except OSError as err:
                LOG.warning("OTA: prune failed: %s", type(err).__name__)
        self._flush_pushes()
        return {"targets": {c: self.target(c) for c in ch.CHANNELS}, "built": built}

    def _flush_pushes(self) -> None:
        with self.store.lock:
            orig = list(self.store.data["pushes"])
        todo = list(orig)
        try:
            self.notifier.flush(todo)
        except Exception as err:  # noqa: BLE001
            LOG.warning("OTA: push failed: %s", type(err).__name__)
            return
        with self.store.lock:
            newer = [p for p in self.store.data["pushes"] if not any(p is o for o in orig)]
            self.store.data["pushes"] = todo + newer
            self.store.save()

    def package_for_update(self) -> str | None:
        """The stable package, for the frozen `prepare_bundle kind=update` path (provisioning of a new satellite)."""
        tag = self.target("stable")
        meta = self._package(tag) if tag else None
        return meta["satellite"]["path"] if meta else None

    def package_file(self, tag: str, name: str) -> str | None:
        """Only channel targets and installed versions (for a rollback) are served."""
        if not TAG.match(tag or "") or name not in ("satellite.tar.gz", "wheels.tar.gz"):
            return None
        if tag not in self._targets() | self._installed():
            return None
        meta = self._package(tag)
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
            try:
                self.store.save()
            except OSError as err:
                LOG.warning("OTA: state save failed: %s", type(err).__name__)
            auto, requested, current = self.store.auto_install, bool(d["install_requested"]), d["installed"]
            tag = self.target(d["channel"])
            if not tag or tag == current:
                return offer_payload(None, auto_install=auto, reason="up_to_date")
            if not _newer(tag, current):
                return offer_payload(None, auto_install=auto, reason="newer_installed")
            sha = self.store.release_infos()[tag].release.sha
            server_proto = int(self.store.data["server_protocol"])
            a = self.store.attempts(sat_id)
        man = self._manifest(sha)
        if man is not None and not order.satellite_ready(man, server_proto):
            return offer_payload(None, auto_install=auto, reason="waiting_server")
        if not requested and not retry.may_try(a, tag, now):
            return offer_payload(None, auto_install=auto, reason="exhausted" if a and a.exhausted else "retry_later")
        meta = self._package(tag)
        if not meta:
            return offer_payload(None, auto_install=auto, reason="package_missing")
        sat = meta["satellite"]
        return offer_payload(tag, sat_id, sha, sat["sha256"], sat["size"],
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
                was_exhausted = a.exhausted
                retry.record_failure(a, r["stage"], r["error"], now)
                d["attempts"] = a.to_dict()
                d["install_requested"] = False
                # against promotion: a version that broke the device (rolled back) or kept failing (exhausted);
                # a transient download error does not count, and a version already promoted is not demoted
                if alpha and (r["result"] == "rolled_back" or a.exhausted) and not self._promoted(r["tag"], now):
                    self.store.soak_fail(r["tag"])
                if a.exhausted and not was_exhausted:
                    self.notifier.queue(nt.format_exhausted(r["sat_id"], r["tag"], r["stage"], r["error"], d["installed"]),
                                        self.store.data["pushes"])
            try:
                self.store.event(r["sat_id"], r["tag"], r["result"], r["stage"], r["error"])
            except OSError as err:
                LOG.warning("OTA: event log failed: %s", type(err).__name__)
            self.store.save()
        return {"ok": True}

    def _promoted(self, tag: str, now: float) -> bool:
        s = self.store.soak().get(tag)
        return bool(s and s.started_at is not None and not s.failed and now - s.started_at >= SOAK_S)

    def install(self, sat_id: str) -> str | None:
        """Manual (HA button or voice): zero the attempts and ask the satellite to install on its next poll."""
        with self.store.lock:
            d = self.store.device(sat_id)
            d.update(install_requested=True, attempts=None)
            tag = self.target(d["channel"])
            self.store.event(sat_id, tag, "install_requested")
            self.store.save()
            return tag

    def set_auto(self, enabled: bool) -> None:
        with self.store.lock:
            self.store.data["auto_install"] = bool(enabled)
            self.store.save()

    def status(self) -> dict:
        with self.store.lock:
            devices = {s: {k: d.get(k) for k in ("channel", "installed", "install_requested", "last_seen",
                                                    "last_report")}
                       for s, d in self.store.data["devices"].items()}
            releases = sorted((t for t in self.store.data["releases"] if TAG.match(t)), key=ch.version_key)
            return {"auto_install": self.store.auto_install, "targets": {c: self.target(c) for c in ch.CHANNELS},
                    "devices": devices, "releases": releases}
