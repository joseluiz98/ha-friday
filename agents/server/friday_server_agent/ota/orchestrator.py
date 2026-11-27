"""OTA orchestrator on the VM (spec 2026-11-10 §6, §13): GitHub → packages → offers to satellites → reports.

Satellites pull (`offer`) and report (`report`); the orchestrator never pushes to them. It decides the target of each
channel, the protocol gate, retries and the alpha soak, and drives the server-side installer (`server_install`) toward
the target of the server channel (spec §16 option a: the newest version an alpha device needs).

Locking: `store.lock` only guards reading/writing the state (fast). Slow work — GitHub, git, package builds, Telegram —
runs outside it; builds are serialized by `_build_lock`, so one tag is never built twice at once.
"""

from __future__ import annotations

import logging
import os
import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from friday_agent_core import version
from friday_agent_core.ota import channels as ch
from friday_agent_core.ota import order
from friday_agent_core.ota import retry
from friday_agent_core.ota.constants import KEEP_RELEASES, SOAK_S

from . import github as gh
from . import notify as nt
from . import packages as pk
from . import server_install as si
from . import wheels as wh

LOG = logging.getLogger("friday_server_agent.ota")
OTA_ROOT = "/srv/friday/ota"
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
_LEAD_TAG = re.compile(r"^(v\d+\.\d+\.\d+)(?:-\d+-g[0-9a-f]+)?(?:\s|$)")
CONTRACT_TAG = "v0.3.10"  # packages of older tags predate built_by: always kept as they are (spec 2026-11-18)


def lead_tag(value) -> str | None:
    """`vX.Y.Z` from a clean tag or a `git describe` version (`vX.Y.Z-N-gSHA`, written by deploy.sh); else None."""
    m = _LEAD_TAG.match(value.strip()) if isinstance(value, str) else None
    return m.group(1) if m else None
RESULTS = ("installed", "rolled_back", "failed")
REPORT_IDS_KEPT = 20  # per device: the newest report ids, to drop a resend
BUILD_RETRY_S = 3600  # a release that failed to package is retried at most once an hour
PROMOTE_RETRY_S = 3600  # a failed promotion on GitHub is retried at most once an hour
WAITING_PUSH_AFTER_S = 3600  # a satellite waiting for the server: first push after an hour of waiting
# offers with no package for a satellite that has a newer target: all are listed in `ota_waiting_server`
REFUSED_WHY = {"server_older": "o agente do servidor é mais antigo que a release (server_older)",
               "package_missing": "o pacote da release não pôde ser montado (package_missing)"}
HELD_REASONS = ("waiting_server", *REFUSED_WHY)


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
    rid = data.get("id")  # optional (agents before v0.3.6 send none): a resent report counts once
    if rid is not None and (not isinstance(rid, str) or not re.match(r"^[0-9a-f]{8,64}$", rid)):
        raise ValueError("id")
    return {"sat_id": sat, "tag": tag, "result": result, "stage": str(data.get("stage") or "")[:200],
            "error": str(data.get("error") or "")[:200], "protocol": proto, "id": rid}


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
                 prune: Callable = pk.prune_packages, installer=None, images=None, publisher=None,
                 own_version: Callable[[], str] = version.installed_version,
                 own_sha: Callable[[], str | None] = version.installed_sha):
        self.store, self.tokens, self.watcher, self.repo = store, tokens, watcher, repo
        self.vault, self.notifier, self._build, self._wheels = vault, notifier, build, wheels
        self.root, self._clock, self._prune = root, clock, prune
        self.meta: dict[str, dict] = {}  # tag -> package meta
        self._manifests: dict[str, object] = {}  # sha -> Manifest | None (immutable per sha)
        self._build_failed: dict[str, float] = {}
        self._build_lock = threading.Lock()
        self.installer = installer
        self.images = images
        self.publisher = publisher  # None (no GitHub token file): no automatic promotion
        self._promote_tried: dict[str, float] = {}
        self._own_version = own_version
        self._own_sha = own_sha
        self._matched: set[tuple[str, str]] = set()  # (agent sha, release sha) proven to hold the same agent code
        self._older_refused: set[str] = set()  # tags whose stale package an older agent refused to serve

    # ---- reads under the state lock
    def _eligible(self) -> list[ch.Release]:
        """CI-green releases that know the OTA (have `ota/release.json`). A release older than the OTA is history: it
        is never a channel target — a satellite sent there could never update itself again, and the frozen bootstrap
        path would hand a new satellite an agent without the OTA client."""
        with self.store.lock:
            return [r for r in ch.eligible([i.release for i in self.store.release_infos().values()])
                    if self._ota_aware(r.sha)]

    def _ota_aware(self, sha: str) -> bool:
        try:
            return self._manifest(sha) is not None
        except Exception as err:  # noqa: BLE001 - the VM repo unreadable now: not a target until it reads again
            LOG.warning("OTA: manifest of %s unreadable: %s", sha[:7], type(err).__name__)
            return False

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
                meta = self._build(self.repo, tag, info.release.sha, self.vault, self.root, self._wheels,
                                   built_by=self._own_version())
            except Exception as err:  # noqa: BLE001 - a broken release must not stop the others
                LOG.error("OTA: package %s failed: %s", tag, type(err).__name__)
                self._build_failed[tag] = self._clock()
                with self.store.lock:
                    self.store.event("server", tag, "package_failed", "build", type(err).__name__)
                return None
            self._build_failed.pop(tag, None)
            self.meta[tag] = meta
            return meta

    def _held(self, tag: str) -> bool:
        """A package some device already has (or was offered): rebuilding it would change bytes under that device."""
        if pk.is_offered(self.root, tag):
            return True
        with self.store.lock:
            for d in self.store.data["devices"].values():
                if d.get("installed") == tag:
                    return True
                # the device's last attempt or report names this tag: it may be downloading or holding it
                if isinstance(d.get("attempts"), dict) and d["attempts"].get("version") == tag:
                    return True
                if isinstance(d.get("last_report"), dict) and d["last_report"].get("tag") == tag:
                    return True
            path = self.store.events_path
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    # satellite results only: the server's own install event is about archive.tar, not this package
                    if (isinstance(e, dict) and e.get("tag") == tag
                            and (e.get("event") in RESULTS or e.get("event") == "install_requested")
                            and str(e.get("device") or "").startswith("sat-")):
                        return True
        except OSError:
            pass
        return False

    def _agent_matches(self, tag: str, sha: str) -> bool:
        """True when the installer's own rule would keep this agent for the release at `sha`: its code is the
        release's, even with an older version label. Anything not proven is False."""
        own = self._own_sha()
        if not own:
            return False
        if (own, sha) in self._matched:
            return True
        try:
            man = self._manifest(sha)
        except Exception as err:  # noqa: BLE001 - the VM repo unreadable now: not proven
            LOG.warning("OTA: manifest of %s unreadable: %s", tag, type(err).__name__)
            return False
        paths = man.components.get("server-agent") if man else None
        if not paths or si.needs_install(self.repo, own, sha, paths):
            return False  # a failed diff is not cached: tried again at the next call
        self._matched.add((own, sha))
        return True

    def _satellite_package(self, tag: str) -> dict | None:
        """The only door for satellite consumers (offer, package files, provisioning, image). A package packed by an
        agent older than the release may miss files the release declares (spec 2026-11-18): it is rebuilt by a new
        enough agent, refused by an older one, and kept as is once a device holds it. New enough = its version is at
        least the release's, or its code is the release's (`_agent_matches`: a release that leaves the agent alone
        never changes its version)."""
        meta = self._package(tag)
        if not meta:
            return None
        by = lead_tag(meta.get("built_by"))
        if ch.version_key(tag) < ch.version_key(CONTRACT_TAG):  # pre-contract tag: kept as it is, never deleted
            self._older_refused.discard(tag)
            return meta
        if by is not None and ch.version_key(by) >= ch.version_key(tag):
            self._older_refused.discard(tag)
            return meta
        if self._held(tag):
            self._older_refused.discard(tag)
            return meta
        own_raw = self._own_version()
        own = lead_tag(own_raw)
        with self.store.lock:
            info = self.store.release_infos().get(tag)
        if info is None:
            return None
        if own is None or ch.version_key(own) < ch.version_key(tag):
            if not self._agent_matches(tag, info.release.sha):
                self._older_refused.add(tag)
                return None
            if meta.get("built_by") == own_raw:
                self._older_refused.discard(tag)
                return meta
        self._older_refused.discard(tag)
        with self._build_lock:
            if self.meta.get(tag) is not meta:  # another thread already rebuilt it
                return self.meta.get(tag)
            LOG.info("OTA: package %s packed by %s; rebuilding its satellite part with %s", tag,
                     meta.get("built_by") or "an older agent", own_raw)
            try:  # satellite.tar.gz only: the server's archive.tar stays (spec 2026-11-18, review finding 2)
                new = self._build(self.repo, tag, info.release.sha, self.vault, self.root, self._wheels,
                                  built_by=own_raw, satellite_only=True)
            except Exception as err:  # noqa: BLE001 - the old package stays; retried at the next offer
                LOG.error("OTA: satellite rebuild %s failed: %s", tag, type(err).__name__)
                with self.store.lock:
                    self.store.event("server", tag, "package_failed", "rebuild", type(err).__name__)
                return None
            self.meta[tag] = new
            return new

    def _missing_reason(self, tag: str) -> str:
        return "server_older" if tag in self._older_refused else "package_missing"

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
        try:
            self._promote_step()
        except Exception as err:  # noqa: BLE001 - the rest of the cycle must go on
            LOG.error("OTA: promote step failed: %s", type(err).__name__)
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
        try:
            self._server_step()
        except Exception as err:  # noqa: BLE001 - the satellites' side must go on
            LOG.error("OTA: server step failed: %s", type(err).__name__)
        try:
            self._image_step()
        except Exception as err:  # noqa: BLE001
            LOG.error("OTA: image step failed: %s", type(err).__name__)
        keep = self._keep()
        with self._build_lock:
            try:
                for tag in self._prune(self.root, keep):
                    self.meta.pop(tag, None)
            except OSError as err:
                LOG.warning("OTA: prune failed: %s", type(err).__name__)
        self._flush_pushes()
        return {"targets": {c: self.target(c) for c in ch.CHANNELS}, "built": built}

    def _newest_stable_key(self) -> tuple | None:
        """Version key of the newest stable release: the releases GitHub reports as stable, plus the tags this agent
        promoted (the stored flag stays pre-release until a full poll). None when there is none."""
        with self.store.lock:
            stable_keys = [ch.version_key(i.release.tag) for i in self.store.release_infos().values()
                           if not i.release.prerelease and ch.eligible([i.release])]
            stable_keys += [ch.version_key(t) for t, p in self.store.data.setdefault("promotions", {}).items()
                            if isinstance(p, dict) and p.get("result") == "promoted"]
        return max(stable_keys, default=None)

    def _promote_step(self) -> None:
        """A pre-release that passed the alpha soak becomes the stable release on GitHub (the release page is then the
        truth for the stable channel too). Once per tag; a newer stable release supersedes an older soaked one."""
        pub = self.publisher
        if pub is None:
            return
        now = self._clock()
        with self.store.lock:
            promotions = self.store.data.setdefault("promotions", {})
            soak = self.store.soak()
            candidates = [r for r in self._eligible()
                          if r.prerelease and ch.promoted(r, soak, now) and r.tag not in promotions]
            newest_stable = self._newest_stable_key()
            best = max(candidates, key=lambda r: ch.version_key(r.tag), default=None)
            if best is not None and newest_stable is not None and ch.version_key(best.tag) <= newest_stable:
                best = None
            # only the newest candidate is promoted; every other one is superseded (recomputed on each call, so a
            # failed older tag is never promoted after a newer one, which would move GitHub "latest" backwards)
            todo = [best] if best is not None else []
            for r in candidates:
                if r is not best:
                    promotions[r.tag] = {"at": now, "result": "superseded"}
            if len(candidates) > len(todo):
                self.store.save()
        for r in todo:
            if now - self._promote_tried.get(r.tag, -PROMOTE_RETRY_S) < PROMOTE_RETRY_S:
                continue
            with self.store.lock:  # a block (or a failure) that arrived since the selection above wins
                rec = self.store.soak().get(r.tag)
                if rec is not None and (rec.blocked or rec.failed):
                    continue
            self._promote_tried[r.tag] = now
            try:
                pub.promote(r.tag)  # network: outside the state lock
            except gh.GitHubError as err:
                LOG.warning("OTA: promotion of %s failed: %s", r.tag, err)
                continue
            with self.store.lock:
                self.store.data.setdefault("promotions", {})[r.tag] = {"at": now, "result": "promoted"}
                self.notifier.queue(nt.format_promoted(r.tag), self.store.data["pushes"])
                rec = self.store.soak().get(r.tag)
                if rec is not None and rec.blocked:  # the owner blocked it while the GitHub call was in flight
                    LOG.warning("OTA: the block on %s came too late; it was already being promoted", r.tag)
                    self.notifier.queue(nt.format_block_too_late(r.tag), self.store.data["pushes"])
                try:
                    self.store.event("server", r.tag, "promoted")
                except OSError as err:
                    LOG.warning("OTA: event log failed: %s", type(err).__name__)
                self.store.save()

    # ---- server side
    def _server_step(self) -> None:
        inst = self.installer
        if inst is None:
            return
        out = inst.recover()  # a run cut by a crash, a reboot, a snapshot rollback or a silent self update
        if out:
            self._server_result(out)
        out = inst.absorb_self_result()
        if out:
            self._server_result(out)
        inst.prune_snapshots()
        now = self._clock()
        with self.store.lock:
            so = self.store.data["server_ota"]
            channel = ch.server_channel([d["channel"] for d in self.store.data["devices"].values()])
            tag = self.target(channel)
            if not tag:
                if so.get("install_requested"):  # a press with nothing to install must not latch for a later release
                    so["install_requested"] = False
                    self.store.save()
                return
            sha = self.store.release_infos()[tag].release.sha
            manual, auto = bool(so.get("install_requested")), self.store.auto_install
            current = so.get("installed_tag")
            a = retry.Attempts.from_dict(so["attempts"]) if so.get("attempts") else None
        man = self._manifest(sha)
        if tag == current or not _newer(tag, current) or man is None:
            # nothing to install (up to date; the server never goes back by itself; a pre-OTA release is installed
            # by hand): a pending manual request must not wait for a later release and skip its checks
            if manual:
                with self.store.lock:
                    so["install_requested"] = False
                    self.store.save()
            return
        if not (auto or manual) or (not manual and not retry.may_try(a, tag, now)):
            return
        if self.images is not None and self.images.status().get("status") == "building":
            return  # a server install (maybe a snapshot rollback) never runs during the 1-hour image build
        self._package(tag)  # the retained archive (rollback source); the installer falls back to the clone
        out = inst.apply(tag, sha, man, manual=manual)
        self._server_result({**out, "tag": tag})

    def _server_result(self, out: dict) -> None:
        result, tag = out.get("result"), str(out.get("tag") or "")
        if result == "deferred" or not TAG.match(tag):
            return
        now = self._clock()
        man = None
        with self.store.lock:
            info = self.store.release_infos().get(tag)
        if result == "installed" and info is not None:
            try:
                man = self._manifest(info.release.sha)
            except Exception:  # noqa: BLE001
                man = None
        with self.store.lock:
            so = self.store.data["server_ota"]
            so["install_requested"] = False
            stage, error = str(out.get("stage") or ""), str(out.get("error") or "")
            if result == "installed":
                so.update(installed_tag=tag, attempts=None)
                if man is not None:
                    self.store.data["server_protocol"] = int(man.protocol.get("server", 1))
            elif result == "failed" and stage == "rollback":  # the way back failed too: the server may be down
                self.notifier.queue(nt.format_rollback_failed(tag, error), self.store.data["pushes"])
                so["attempts"] = retry.Attempts(version=tag, failures=99, exhausted=True, last_stage=stage,
                                                last_error=error[:200]).to_dict()
                if info is not None and info.release.prerelease and not self._promoted(tag, now):
                    self.store.soak_fail(tag)  # a broken way back is the strongest evidence against promotion
            elif result in ("rolled_back", "failed"):
                a = retry.Attempts.from_dict(so["attempts"]) if so.get("attempts") else None
                if a is None or a.version != tag:
                    a = retry.reset(tag)
                was_exhausted = a.exhausted
                retry.record_failure(a, f"{out.get('component', '')} {stage}".strip(), error, now)
                so["attempts"] = a.to_dict()
                alpha = info is not None and info.release.prerelease
                if alpha and (result == "rolled_back" or a.exhausted) and not self._promoted(tag, now):
                    self.store.soak_fail(tag)
                if a.exhausted and not was_exhausted:
                    self.notifier.queue(nt.format_exhausted("o servidor", tag, a.last_stage, error,
                                                            so.get("installed_tag")), self.store.data["pushes"])
            try:
                self.store.event("server", tag, str(result), stage, error)
            except OSError as err:
                LOG.warning("OTA: event log failed: %s", type(err).__name__)
            self.store.save()

    # ---- provisioning image (spec §10): built once when a tag becomes the stable target; by hand at any time
    def _image_step(self) -> None:
        if self.images is None:
            return
        tag = self.target("stable")
        st = self.images.status()
        with self.store.lock:
            so = self.store.data["server_ota"]
            if tag and st.get("tag") == tag and st.get("status") == "ok" and so.get("image_auto") != tag:
                so["image_auto"] = tag  # already built (by hand, or before this agent): nothing to do
                self.store.save()
                return
            if not tag or so.get("image_auto") == tag or st.get("status") == "building":
                return  # once per stable tag: an error waits for the button (no retry storm of a 1-hour build)
            if isinstance(so.get("run"), dict):
                return  # never alongside a server install (both are heavy on the VM)
            sha = self.store.release_infos()[tag].release.sha
        if self._manifest(sha) is None:
            return  # a release older than the OTA cannot make an image that updates itself
        out = self.image_build(tag)
        if out.get("ok") or out.get("error") not in ("busy", "server_busy"):
            with self.store.lock:
                self.store.data["server_ota"]["image_auto"] = tag
                self.store.save()

    def image_build(self, tag: str | None = None) -> dict:
        if self.images is None:
            return {"ok": False, "error": "unavailable"}
        tag = tag or self.target("stable")
        if not tag:
            return {"ok": False, "error": "no_stable_release"}
        with self.store.lock:
            info = self.store.release_infos().get(tag)
            if isinstance(self.store.data["server_ota"].get("run"), dict):
                return {"ok": False, "error": "server_busy"}
        if info is None:
            return {"ok": False, "error": "package_missing"}
        if not self._satellite_package(tag):
            return {"ok": False, "error": self._missing_reason(tag)}
        out = self.images.start(tag, info.release.sha)
        try:
            with self.store.lock:
                self.store.event("image", tag, "image_build" if out.get("ok") else "image_refused", "",
                                 str(out.get("error") or ""))
        except OSError as err:
            LOG.warning("OTA: event log failed: %s", type(err).__name__)
        return out

    def image_status(self) -> dict:
        return self.images.status() if self.images is not None else {"status": None}

    def install_server(self) -> str | None:
        """Manual (HA button or voice): zero the attempts; the next poll installs without waiting for heavy idle."""
        with self.store.lock:
            so = self.store.data["server_ota"]
            so.update(install_requested=True, attempts=None)
            tag = self.target(ch.server_channel([d["channel"] for d in self.store.data["devices"].values()]))
            self.store.event("server", tag, "install_requested")
            self.store.save()
            return tag

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
        meta = self._satellite_package(tag) if tag else None
        return meta["satellite"]["path"] if meta else None

    def package_file(self, tag: str, name: str) -> str | None:
        """Only channel targets and installed versions (for a rollback) are served."""
        if not TAG.match(tag or "") or name not in ("satellite.tar.gz", "wheels.tar.gz"):
            return None
        if tag not in self._targets() | self._installed():
            return None
        meta = self._satellite_package(tag)
        if not meta:
            return None
        path = meta["satellite"]["path"] if name == "satellite.tar.gz" else meta["satellite"].get("wheels")
        if not (path and os.path.exists(path)):
            return None
        try:  # served once: a device may hold these bytes now, so the package is never rebuilt under it
            pk.mark_offered(self.root, tag, self._clock())
        except OSError as err:
            LOG.warning("OTA: offered marker failed: %s", type(err).__name__)
        return path

    # ---- satellites
    def offer(self, sat_id: str, channel: str, installed: str | None, protocol: int, lock_sha256: str) -> dict:
        out, tag = self._offer(sat_id, channel, installed, protocol, lock_sha256)
        try:
            reason = out.get("reason")
            self._waiting_server(sat_id, tag, reason if out.get("tag") is None and reason in HELD_REASONS else None)
        except Exception as err:  # noqa: BLE001 - the offer itself must reach the satellite
            LOG.warning("OTA: waiting-server bookkeeping failed: %s", type(err).__name__)
        return out

    def _waiting_reason(self) -> str:
        building = self.images is not None and self.images.status().get("status") == "building"
        with self.store.lock:
            so = self.store.data["server_ota"]
            a = retry.Attempts.from_dict(so["attempts"]) if so.get("attempts") else None
            if not self.store.auto_install and not so.get("install_requested"):
                return "atualização automática desligada"
        if building:
            return "build de imagem em andamento"
        if a is not None and a.exhausted:
            return "tentativas esgotadas"
        return "aguardando a janela de instalação"

    def _waiting_server(self, sat_id: str, tag: str | None, reason: str | None) -> None:
        """A satellite held back from a newer target (the server-first gate, or a package it cannot get): remembered
        with the tag it waits for. The owner is told by Home Assistant (sensor `ota_waiting_server`, house push in
        civil hours), never by this agent. A refused package is also logged, once per tag and reason."""
        now = self._clock()
        with self.store.lock:
            d = self.store.device(sat_id)
            if not reason or not tag:
                if d.get("waiting_server_since") is not None:
                    for key in ("waiting_server_since", "waiting_server_tag", "waiting_server_reason"):
                        d.pop(key, None)
                    self._save_quiet()
                return
            new = d.get("waiting_server_since") is None or d.get("waiting_server_tag") != tag
            if not new and d.get("waiting_server_reason") == reason:
                return
            if new:
                d["waiting_server_since"], d["waiting_server_tag"] = now, tag
            d["waiting_server_reason"] = reason
            if reason in REFUSED_WHY:
                LOG.warning("OTA: package %s refused to %s: %s", tag, sat_id, reason)
            self._save_quiet()

    def alpha_promotion(self) -> tuple[str | None, dict]:
        """(state, attributes) of the sensor `ota_alpha_promotion`: when the tracked alpha will be promoted to stable.
        Tracked = the newest eligible pre-release not yet decided and not older than the newest stable (that one can
        only end superseded); none left: the last decided tag, so a promotion stays visible with its result. The state
        is the promotion time (None without a running clock). `soaked_hours` is the only value that moves with time;
        it is recomputed only when this is called (every OTA cycle), never by a timer of its own."""
        now = self._clock()

        def iso(ts: float | None) -> str | None:
            return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat()

        with self.store.lock:
            promotions = self.store.data.setdefault("promotions", {})
            soak = self.store.soak()
            newest_stable = self._newest_stable_key()
            alphas = sorted((r for r in self._eligible()
                             if r.prerelease and r.tag not in promotions
                             and (newest_stable is None or ch.version_key(r.tag) > newest_stable)),
                            key=lambda r: ch.version_key(r.tag), reverse=True)

            def clock(tag: str) -> tuple[float | None, bool]:
                rec = soak.get(tag)
                return (rec.started_at, rec.failed) if rec is not None else (None, False)

            def blocked(tag: str) -> bool:
                rec = soak.get(tag)
                return rec is not None and rec.blocked

            def status(tag: str) -> str:
                started, failed = clock(tag)
                if failed:
                    return "failed"
                if blocked(tag):
                    return "blocked"
                if started is None:
                    return "waiting_install"
                return "ready" if now - started >= SOAK_S else "soaking"

            def promotes_at(tag: str) -> float | None:
                started, failed = clock(tag)
                return None if started is None or failed or blocked(tag) else started + SOAK_S

            rows = [{"tag": r.tag, "status": status(r.tag), "promotes_at": iso(promotes_at(r.tag))} for r in alphas]
            attrs: dict = {"tag": None, "status": None, "started_at": None, "promotes_at": None,
                           "soaked_hours": None, "newer_stable": False, "alphas": rows}
            tag, end, at = None, now, None
            if alphas:
                # shown: the alpha promoted first (owner, 23/11), i.e. the earliest promotion time; none has one
                # (blocked, failed or not installed): the newest, as before
                timed = [(promotes_at(r.tag), r.tag) for r in alphas if promotes_at(r.tag) is not None]
                tag = min(timed)[1] if timed else alphas[0].tag
                attrs["status"] = status(tag)
                at = promotes_at(tag)
            else:
                decided = []
                for t, p in promotions.items():
                    try:
                        decided.append((ch.version_key(t), t, p))
                    except ValueError:
                        continue
                if decided:
                    _key, tag, p = max(decided, key=lambda d: d[0])
                    result = p.get("result") if isinstance(p, dict) else None
                    attrs["status"] = result if result in ("promoted", "superseded") else None
                    if isinstance(p, dict) and isinstance(p.get("at"), (int, float)):
                        end = p["at"]
                    if result == "promoted":
                        at = promotes_at(tag)
            if tag is not None:
                started, _failed = clock(tag)
                attrs.update(tag=tag, started_at=iso(started), promotes_at=iso(at),
                             newer_stable=newest_stable is not None and ch.version_key(tag) < newest_stable)
                if started is not None:
                    attrs["soaked_hours"] = round(max(end - started, 0.0) / 3600, 1)
        return attrs["promotes_at"], attrs

    def tracked_promotion_tag(self) -> str | None:
        """The alpha the Alpha promotion switch follows (owner, 22/11): the newest one still promotable (eligible
        pre-release, not decided, not older than the newest stable) that has not failed, blocked or not. The sensor
        keeps listing every alpha; this one skips the failed."""
        _state, attrs = self.alpha_promotion()
        return next((row["tag"] for row in attrs["alphas"] if row["status"] != "failed"), None)

    def promotion_allowed(self) -> bool | None:
        """Value of the switch `ota_alpha_promotion_allowed`: True = the tracked alpha will be promoted, False = blocked
        by the owner, None (unavailable) = no promotable alpha."""
        with self.store.lock:
            tag = self.tracked_promotion_tag()
            if tag is None:
                return None
            rec = self.store.soak().get(tag)
            return not (rec is not None and rec.blocked)

    def set_promotion(self, tag: str | None, allowed: bool) -> dict:
        """The owner blocks (allowed=False) or allows the promotion of an alpha; `tag` None = the tracked alpha (the
        one the switch shows). Allowing clears only the hand block, never `failed`; a failed tag refuses both.
        Reply: {"ok": True, "tag"} or {"ok": False, "error"} with error in
        no_alpha | unknown_tag | failed | promoted | superseded | value."""
        if not isinstance(allowed, bool):
            return {"ok": False, "error": "value"}
        with self.store.lock:
            if tag is None:
                tag = self.tracked_promotion_tag()
                if tag is None:
                    return {"ok": False, "error": "no_alpha"}
            elif not any(r.tag == tag and r.prerelease for r in self._eligible()):
                return {"ok": False, "error": "unknown_tag"}
            decided = self.store.data.setdefault("promotions", {}).get(tag)
            if isinstance(decided, dict):  # already decided: promoted, or replaced by a newer release
                return {"ok": False, "error": "superseded" if decided.get("result") == "superseded" else "promoted"}
            rec = self.store.soak().get(tag)
            if rec is not None and rec.failed:
                return {"ok": False, "error": "failed"}
            self.store.soak_block(tag, not allowed)
            try:
                self.store.event("server", tag, "promotion_allowed" if allowed else "promotion_blocked")
            except OSError as err:
                LOG.warning("OTA: event log failed: %s", type(err).__name__)
            self.store.save()
        return {"ok": True, "tag": tag}

    def waiting_server(self) -> dict:
        """{sat_id: {tag, since, why}} for satellites held by the server for at least WAITING_PUSH_AFTER_S."""
        now = self._clock()
        with self.store.lock:
            held = {s: (d["waiting_server_tag"], d["waiting_server_since"], d.get("waiting_server_reason"))
                    for s, d in self.store.data["devices"].items()
                    if d.get("waiting_server_since") is not None and d.get("waiting_server_tag")
                    and now - d["waiting_server_since"] >= WAITING_PUSH_AFTER_S}
        if not held:
            return {}
        why = self._waiting_reason()
        return {s: {"tag": t, "since": since, "why": REFUSED_WHY.get(reason, why)}
                for s, (t, since, reason) in sorted(held.items())}

    def _offer(self, sat_id: str, channel: str, installed: str | None, protocol: int,
               lock_sha256: str) -> tuple[dict, str | None]:
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
            if not tag or tag == current or not _newer(tag, current):
                if requested:  # a press with nothing to install must not latch for a later release
                    d["install_requested"] = False
                    self._save_quiet()
                return offer_payload(None, auto_install=auto,
                                     reason="up_to_date" if not tag or tag == current else "newer_installed"), tag
            sha = self.store.release_infos()[tag].release.sha
            server_proto = int(self.store.data["server_protocol"])
            server_tag = self.store.data["server_ota"].get("installed_tag")
            a = self.store.attempts(sat_id)
        man = self._manifest(sha)
        if man is not None and not order.satellite_ready(man, server_proto):
            return offer_payload(None, auto_install=auto, reason="waiting_server"), tag
        if not (isinstance(server_tag, str) and TAG.match(server_tag)) or \
                ch.version_key(server_tag) < ch.version_key(tag):
            # server first (spec 2026-11-18): the server's agent packs the satellite package, so it runs the release
            return offer_payload(None, auto_install=auto, reason="waiting_server"), tag
        if not requested and not retry.may_try(a, tag, now):
            return offer_payload(None, auto_install=auto,
                                 reason="exhausted" if a and a.exhausted else "retry_later"), tag
        meta = self._satellite_package(tag)
        if not meta:
            return offer_payload(None, auto_install=auto, reason=self._missing_reason(tag)), tag
        try:
            pk.mark_offered(self.root, tag, now)
        except OSError as err:
            LOG.warning("OTA: offered marker failed: %s", type(err).__name__)
        sat = meta["satellite"]
        wheels = sat.get("lock_sha256") != lock_sha256 and bool(sat.get("wheels"))
        # spec §16: new wheels (a pip install on the Pi 3) make the update heavy, whatever release.json says
        return offer_payload(tag, sat_id, sha, sat["sha256"], sat["size"], wheels=wheels,
                             heavy=bool(sat.get("heavy")) or wheels, install_requested=requested,
                             auto_install=auto), tag

    def _save_quiet(self) -> None:
        try:
            self.store.save()
        except OSError as err:
            LOG.warning("OTA: state save failed: %s", type(err).__name__)

    def report(self, data: dict) -> dict:
        r = parse_report(data)
        now = self._clock()
        with self.store.lock:
            d = self.store.device(r["sat_id"])
            seen = d.setdefault("report_ids", [])
            if r["id"] is not None:
                if r["id"] in seen:
                    return {"ok": True, "duplicate": True}
                seen.append(r["id"])
                del seen[:-REPORT_IDS_KEPT]
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
                    rec = self.store.soak().get(r["tag"])
                    if not (rec and rec.failed) and not a.exhausted:
                        # FINDING 7 (15/11): the release leaves alpha, so no retry follows and no exhaustion push
                        self.notifier.queue(nt.format_withdrawn(r["sat_id"], r["tag"], r["stage"], r["error"],
                                                                d["installed"]), self.store.data["pushes"])
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
        return bool(s and s.started_at is not None and not s.failed and not s.blocked and now - s.started_at >= SOAK_S)

    def install(self, sat_id: str) -> str | None:
        """Manual (HA button or voice): zero the attempts and ask the satellite to install on its next poll."""
        with self.store.lock:
            d = self.store.device(sat_id)
            d.update(install_requested=True, attempts=None)
            tag = self.target(d["channel"])
            self.store.event(sat_id, tag, "install_requested")
            so = self.store.data["server_ota"]
            if tag and _newer(tag, so.get("installed_tag")):  # server first: the satellite waits for it
                so.update(install_requested=True, attempts=None)
                self.store.event("server", tag, "install_requested")
            self.store.save()
            return tag

    def set_auto(self, enabled: bool) -> None:
        with self.store.lock:
            self.store.data["auto_install"] = bool(enabled)
            self.store.save()

    def server_entity(self) -> dict:
        """State of the server's `update` entity (integration `update` platform, plan Task 17)."""
        with self.store.lock:
            so = self.store.data["server_ota"]
            channel = ch.server_channel([d["channel"] for d in self.store.data["devices"].values()])
            installed = so.get("installed_tag")
            latest = self.target(channel) or installed
            return {"installed": installed, "latest": latest, "in_progress": bool((so.get("run") or {}).get("stage")),
                    "title": "F.R.I.D.A.Y. server", "release_url": None, "summary": None}

    def status(self) -> dict:
        with self.store.lock:
            devices = {s: {k: d.get(k) for k in ("channel", "installed", "install_requested", "last_seen",
                                                    "last_report")}
                       for s, d in self.store.data["devices"].items()}
            releases = sorted((t for t in self.store.data["releases"] if TAG.match(t)), key=ch.version_key)
            so = self.store.data["server_ota"]
            server = {"installed": so.get("installed_tag"), "install_requested": bool(so.get("install_requested")),
                      "attempts": so.get("attempts"), "integration_target": so.get("integration_target"),
                      "running": (so.get("run") or {}).get("stage"),
                      "components": {c: v.get("installed_tag") for c, v in self.store.data["server"].items()
                                     if isinstance(v, dict)}}
            return {"auto_install": self.store.auto_install, "targets": {c: self.target(c) for c in ch.CHANNELS},
                    "devices": devices, "releases": releases, "server": server}
