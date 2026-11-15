"""Server-side OTA install and rollback (spec 2026-11-10 §6.6–6.10, §11, §13, §16; plan Task 10).

One release at a time, component by component in `COMPONENT_ORDER`, each one only when its code changed since what is
really installed (read from the FRIDAY_VERSION files and the image label, not from our own state):

- light components run the very recipe of `scripts/deploy.sh` (`ota/components.sh`) from the release tree, then prove
  their health; a failure rolls back every component this run touched, in reverse, to the version it replaced;
- `container` is heavy: 30 min of continuous idle (automatic installs), a Proxmox snapshot of the VM, build, switch
  (`FRIDAY_IMAGE_TAG` in `/opt/friday/image/.env`), health; a failure restores the snapshot (the VM reboots), or, when
  Proxmox refuses, switches back to the previous image;
- `integration` is never installed here: its target is published for HA (HACS installs it, Tasks 17/18);
- `server-agent` goes last and is replaced from outside the process (`systemd-run` + `ota/self_update.sh`).

The run is written to the state before every risky step (`server_ota.run`), so an agent that comes back from a crash,
a reboot or a snapshot rollback finds it (`recover`) and puts the server back on the versions the run replaced. The
reason of a snapshot rollback is written on the snapshot itself (Proxmox description), which the rollback keeps.
Never restarts the voice with a conversation open (activity.json; the same rule as the status port's restart).
"""

from __future__ import annotations

import glob
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from typing import Callable

from friday_agent_core.ota.constants import HEALTH_TIMEOUT_S, HEAVY_IDLE_S, KEEP_IMAGES, SNAPSHOT_KEEP_S
from friday_agent_core.ota.manifest import COMPONENT_ORDER

from .. import collectors

LOG = logging.getLogger("friday_server_agent.ota")
SHA = re.compile(r"\b[0-9a-f]{40}\b")
SERVER_COMPONENTS = tuple(c for c in COMPONENT_ORDER if c not in ("satellite", "integration"))
RUN_TIMEOUT_S = 1800  # an install script (pip, docker build) gets this long
ROLLBACK_MARK = "friday-ota rolling_back"

DEFAULT_PATHS = {
    "root": "/srv/friday/ota",
    "activity": "/srv/friday/state/activity.json",
    "image_dir": "/opt/friday/image",
    "workspace": "/srv/friday/home/.openclaw/workspace/friday",
    "status_url": "http://127.0.0.1:8800",
    "version:server-agent": "/opt/friday/agent/friday_agent_core/FRIDAY_VERSION",
    "version:server": "/opt/friday/status/FRIDAY_VERSION",
    "version:voice-gateway": "/srv/friday/app/FRIDAY_VERSION",
    "version:wwt": "/opt/friday/wwt/training/FRIDAY_VERSION",
}


class Busy(Exception):
    """Not a safe moment (conversation open, brain turn, heavy idle not reached): nothing changed, no attempt counted."""


class StepFailed(Exception):
    def __init__(self, stage: str, error: str):
        super().__init__(f"{stage}: {error}")
        self.stage, self.error = stage, error[:200]


def _http(method: str, url: str, token: str = "", timeout: float = 10) -> tuple[int, dict | None]:
    req = urllib.request.Request(url, data=b"{}" if method == "POST" else None, method=method,
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None
    try:
        return code, json.loads(body or b"null")
    except ValueError:
        return code, None


def _log_tail() -> str | None:
    try:
        r = subprocess.run(["docker", "logs", "--since", "6h", "friday"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=15, text=True, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def version_line_sha(line: str) -> str | None:
    m = SHA.search(line or "")
    return m.group(0) if m else None


def touches(changed: list[str] | None, paths) -> bool:
    if changed is None:
        return True
    return any(p == base or p.startswith(base.rstrip("/") + "/") for p in changed for base in paths)


class IdleTracker:
    """Continuous idle time, sampled every ~30 s by the agent (a conversation shorter than a sample can slip by)."""

    def __init__(self) -> None:
        self.since: float | None = None

    def sample(self, busy: bool, now: float) -> None:
        self.since = None if busy else (self.since if self.since is not None else now)

    def idle_for(self, now: float) -> float:
        return 0.0 if self.since is None else now - self.since


class ServerInstaller:
    def __init__(self, store, repo, snapshots=None, status_token: Callable[[], str] = lambda: "",
                 run: Callable = subprocess.run, http: Callable = _http, log_tail: Callable = _log_tail,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 paths: dict | None = None):
        self.store, self.repo, self.snapshots = store, repo, snapshots
        self._token, self._run, self._http, self._log_tail = status_token, run, http, log_tail
        self._clock, self._sleep = clock, sleep
        self.paths = {**DEFAULT_PATHS, **(paths or {})}
        self.idle = IdleTracker()

    # ---------------------------------------------------------------- state
    def _ota(self) -> dict:
        return self.store.data.setdefault("server_ota", {})

    def _save_run(self, run: dict | None) -> None:
        with self.store.lock:
            if run is None:
                self._ota().pop("run", None)
            else:
                self._ota()["run"] = dict(run)
            self.store.save()
        self._run(["sync"], timeout=60)

    def _record(self, comp: str, tag: str, sha: str) -> None:
        with self.store.lock:
            self.store.data["server"][comp] = {"installed_tag": tag, "installed_sha": sha}

    # ---------------------------------------------------------------- what is installed
    def _sh(self, cmd: list[str], timeout: float = 60, **kw) -> subprocess.CompletedProcess:
        return self._run(cmd, capture_output=True, text=True, timeout=timeout, **kw)

    def _image(self) -> str:
        """The image the friday container runs (`friday:<tag>`), or "" when there is no container."""
        r = self._sh(["docker", "inspect", "friday", "--format", "{{.Config.Image}}"])
        return r.stdout.strip() if r.returncode == 0 else ""

    def installed_line(self, comp: str) -> str:
        if comp == "container":
            r = self._sh(["docker", "inspect", self._image() or "friday", "--format",
                          '{{index .Config.Labels "friday.sha"}}'])
            sha = r.stdout.strip() if r.returncode == 0 else ""
            tag = self._image().partition(":")[2]
            return f"{tag} {sha}" if version_line_sha(sha) else ""
        if comp == "brain-skills":
            files = sorted(glob.glob(os.path.join(self.paths["workspace"], "skills", "*", "FRIDAY_VERSION")))
            path = files[0] if files else ""
        else:
            path = self.paths.get(f"version:{comp}", "")
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def plan(self, sha: str, manifest) -> list[str]:
        """Components whose code differs from what is installed; unknown or unreadable history = install."""
        out = []
        for comp in SERVER_COMPONENTS:
            old = version_line_sha(self.installed_line(comp))
            if old == sha:
                continue
            try:
                changed = self.repo.changed_paths(old, sha) if old else None
            except Exception:  # noqa: BLE001 - a sha the clone never saw: reinstall
                changed = None
            if touches(changed, manifest.components.get(comp, ())):
                out.append(comp)
        return out

    # ---------------------------------------------------------------- safe moment
    def busy(self) -> bool:
        try:
            with open(self.paths["activity"], encoding="utf-8") as f:
                activity = json.load(f)
        except (OSError, ValueError):
            activity = None
        try:
            return collectors.is_busy(activity, self._clock(), self._log_tail)
        except OSError:
            return True  # no proof the conversation ended: not a safe moment

    def sample_idle(self) -> None:
        self.idle.sample(self.busy(), self._clock())

    # ---------------------------------------------------------------- trees
    def _tree(self, tag: str, sha: str, dest: str) -> str:
        """Extracts the release's server archive (the retained package, else the VM clone) into `dest`."""
        pkg = os.path.join(self.paths["root"], "packages", tag, "archive.tar")
        if os.path.exists(pkg):
            with open(pkg, "rb") as f:
                raw = f.read()
        else:
            from .packages import SERVER_PATHS
            raw = self.repo.archive(sha, SERVER_PATHS)
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(raw)) as t:
            if hasattr(tarfile, "data_filter"):
                t.extractall(dest, filter="data")
            else:  # pragma: no cover - python < 3.11.4
                t.extractall(dest)  # noqa: S202 - our own archive from git
        recipe = os.path.join(dest, "ota", "components.sh")
        saved = os.path.join(self.paths["root"], "components.sh")
        if os.path.exists(recipe):
            os.makedirs(self.paths["root"], exist_ok=True)
            shutil.copyfile(recipe, saved)  # a pre-OTA release (rollback) has no recipe: it borrows the latest one
        elif os.path.exists(saved):
            os.makedirs(os.path.dirname(recipe), exist_ok=True)
            shutil.copyfile(saved, recipe)
        return dest

    def _install(self, tree: str, comp: str, line: str, extra: dict | None = None) -> None:
        env = {**os.environ, "FRIDAY_VERSION_LINE": line, "FRIDAY_NO_RESTART": "1", **(extra or {})}
        script = '. ota/components.sh && friday_component "$1" && eval "$CMD"'
        try:
            r = self._sh(["bash", "-c", script, "friday-ota", comp], timeout=RUN_TIMEOUT_S, cwd=tree, env=env)
        except subprocess.TimeoutExpired:
            raise StepFailed("install", "timeout") from None
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [f"exit {r.returncode}"]
            raise StepFailed("install", tail[0])

    # ---------------------------------------------------------------- health
    def _wait(self, check: Callable[[], bool], timeout: float = HEALTH_TIMEOUT_S) -> bool:
        end = self._clock() + timeout
        while True:
            if check():
                return True
            if self._clock() >= end:
                return False
            self._sleep(5)

    def _voice_up(self, since: float) -> bool:
        code, body = self._http("GET", self.paths["status_url"] + "/v1/status", self._token())
        if code != 200 or not isinstance(body, dict) or not body.get("ready"):
            return False
        r = self._run(["docker", "logs", "--since", str(int(since)), "friday"], stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, text=True, timeout=30)
        text = r.stdout or ""
        start = text.rfind("pronto:")  # the old process's shutdown output comes before the new start
        return r.returncode == 0 and start >= 0 and "Traceback" not in text[start:]

    def healthy(self, comp: str, tree: str, line: str, since: float) -> bool:
        if comp in ("voice-gateway", "container"):
            return self._wait(lambda: self._voice_up(since))
        if comp == "server":
            units = ["friday-status"] + sorted(os.path.basename(u) for u in
                                               glob.glob(os.path.join(tree, "server", "systemd", "*.timer")))
            return self._wait(lambda: self._sh(["systemctl", "is-active", "-q", *units]).returncode == 0, 30)
        if comp == "brain-skills":
            skills = [d for d in sorted(os.listdir(os.path.join(tree, "brain", "skills")))
                      if os.path.isdir(os.path.join(tree, "brain", "skills", d)) and d != "__pycache__"]
            for s in skills:
                try:
                    with open(os.path.join(self.paths["workspace"], "skills", s, "FRIDAY_VERSION"),
                              encoding="utf-8") as f:
                        if f.read().strip() != line:
                            return False
                except OSError:
                    return False
            return True
        if comp == "wwt":
            script = os.path.join(os.path.dirname(os.path.dirname(self.paths["version:wwt"])), "featcache_nightly.sh")
            return self._sh(["bash", "-n", script]).returncode == 0
        return True

    def _restart_voice(self) -> float:
        """Asks the status port to restart the container; it refuses (409) with a conversation open → Busy."""
        since = self._clock()
        code, _ = self._http("POST", self.paths["status_url"] + "/v1/restart", self._token(), 15)
        if code == 409:
            raise Busy("conversation_open")
        if code != 202:
            raise StepFailed("restart", f"HTTP {code}")
        return since

    # ---------------------------------------------------------------- container (heavy)
    def _compose_up(self, image_tag: str) -> None:
        env_path = os.path.join(self.paths["image_dir"], ".env")
        with open(env_path + ".tmp", "w", encoding="utf-8") as f:
            f.write(f"FRIDAY_IMAGE_TAG={image_tag}\n")
        os.replace(env_path + ".tmp", env_path)
        r = self._sh(["docker", "compose", "up", "-d", "--no-build"], timeout=300, cwd=self.paths["image_dir"])
        if r.returncode != 0:
            raise StepFailed("switch", (r.stderr or "").strip()[-200:] or f"exit {r.returncode}")

    def _prune_images(self, keep: list[str]) -> None:
        r = self._sh(["docker", "images", "friday", "--format", "{{.Tag}}"])
        tags = [t for t in r.stdout.split() if t and t != "<none>"] if r.returncode == 0 else []
        for t in tags:
            if t not in keep[:KEEP_IMAGES]:
                self._sh(["docker", "rmi", f"friday:{t}"], timeout=120)

    def _snapshot(self, run: dict, tag: str) -> None:
        """Before any change of a heavy run. The run is saved with the snapshot's name first, so the state the VM gets
        back from a rollback knows which snapshot it came from (and reads the reason written on it)."""
        if self.snapshots is None:
            raise StepFailed("snapshot", "no proxmox token")
        try:
            for s in self.snapshots.list():  # only the snapshot of the last update is kept
                if str(s.get("name", "")).startswith("friday-ota-"):
                    self.snapshots.wait(self.snapshots.delete(s["name"]))
            run.update(snapshot=self.snapshots.snapshot_name(tag), stage="snapshot")
            self._save_run(run)
            self.snapshots.wait(self.snapshots.create(run["snapshot"]))
        except Exception as err:  # noqa: BLE001
            run["snapshot"] = None
            raise StepFailed("snapshot", str(err)) from None

    def prune_snapshots(self) -> list[str]:
        if self.snapshots is None:
            return []
        try:
            return self.snapshots.prune(SNAPSHOT_KEEP_S)
        except Exception as err:  # noqa: BLE001
            LOG.warning("OTA: snapshot prune failed: %s", type(err).__name__)
            return []

    def _self_running(self) -> bool:
        try:
            return self._sh(["systemctl", "is-active", "-q", "friday-ota-self"]).returncode == 0
        except Exception:  # noqa: BLE001
            return False

    # ---------------------------------------------------------------- rollback
    def _rollback_light(self, run: dict, comps: list[str]) -> list[str]:
        """Reinstalls the previous version of each component, in reverse. Returns the ones that could not go back."""
        failed = []
        for comp in reversed(comps):
            prev = run["prev"].get(comp, "")
            sha = version_line_sha(prev)
            try:
                if not sha:
                    raise StepFailed("rollback", "previous version unknown")
                tree = self._tree(prev.split()[0], sha, os.path.join(run["work"], "prev", comp))
                self._install(tree, comp, prev)
            except Exception as err:  # noqa: BLE001 - keep returning the others
                LOG.error("OTA: rollback of %s failed: %s", comp, type(err).__name__)
                failed.append(comp)
        return failed

    def _restore_image_sources(self, backup: str) -> None:
        dest = self.paths["image_dir"]
        for n in os.listdir(dest):
            if n != ".env" and not os.path.exists(os.path.join(backup, n)):
                p = os.path.join(dest, n)
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
        shutil.copytree(backup, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".env"))

    def _rollback(self, run: dict, failed: str, err: StepFailed, comps: list[str] | None = None) -> dict:
        """Puts `comps` (default: every component this run touched) back on the versions the run replaced.

        A container that already switched goes back by restoring the VM snapshot (the VM stops and boots; `recover`
        reports it after the boot), or, when Proxmox refuses, by its previous sources and image. A revert that fails is
        reported as `failed`/`rollback` (the caller pushes it at once): the server may be broken."""
        done = list(run.get("done", []) if comps is None else comps)
        problems: list[str] = []
        snap = run.get("snapshot")
        if snap and "container" in done and run.get("switched") and self.snapshots is not None:
            run["stage"] = "rolling_back"
            try:
                self._save_run(run)
                self.snapshots.annotate(snap, f"{ROLLBACK_MARK} {err.stage}: {err.error}")
                # the VM stops inside this task; when it does not (refused, failed), the image path below runs
                self.snapshots.wait(self.snapshots.rollback(snap, start=True), timeout=900)
                self._sleep(120)
            except Exception as perr:  # noqa: BLE001 - Proxmox refused (e.g. no VM.PowerMgmt): image rollback
                LOG.error("OTA: snapshot rollback refused: %s", type(perr).__name__)
        if "container" in done:
            try:
                backup = run.get("image_backup")
                if backup and os.path.isdir(backup):
                    self._restore_image_sources(backup)
                prev_image = run.get("prev_image", "")
                if run.get("switched") and prev_image.startswith("friday:"):
                    self._compose_up(prev_image.partition(":")[2])
            except Exception as ierr:  # noqa: BLE001
                LOG.error("OTA: image rollback failed: %s", type(ierr).__name__)
                problems.append("container")
            done.remove("container")
        problems += self._rollback_light(run, done)
        if "voice-gateway" in done and err.stage != "busy":  # a refused restart left the old code running
            try:
                self._restart_voice()
            except Exception:  # noqa: BLE001
                LOG.warning("OTA: voice restart after rollback deferred")
        try:
            self._save_run(None)
        except Exception as serr:  # noqa: BLE001
            LOG.error("OTA: could not clear the run: %s", type(serr).__name__)
        shutil.rmtree(run.get("work") or "", ignore_errors=True)
        if problems:
            return {"result": "failed", "stage": "rollback", "component": failed,
                    "error": f"could not revert {', '.join(problems)} after {err.stage}: {err.error}"[:200]}
        return {"result": "rolled_back", "stage": err.stage, "error": err.error, "component": failed}

    def recover(self) -> dict | None:
        """Before every step: a run left behind (crash, reboot, snapshot rollback, silent self-update) is undone and
        reported. Never touches the container during a conversation (it comes back on a later step)."""
        with self.store.lock:
            run = self._ota().get("run")
        if not isinstance(run, dict) or not run.get("tag"):
            return None
        run = dict(run)
        run["work"] = run.get("work") or os.path.join(self.paths["root"], "work", "recover")
        os.makedirs(run["work"], exist_ok=True)
        if run.get("switched") and self.busy():
            return None  # undoing a switched container (snapshot or image) restarts the voice: after the conversation
        if run.get("stage") == "handoff":
            if self._self_running() or os.path.exists(self._self_paths()[2]):
                return None  # absorb_self_result() takes it from here
            out = self._rollback(run, "server-agent", StepFailed("handoff", "the self update left no result"))
            return {**out, "tag": run["tag"]}
        stage, error = "interrupted", run.get("stage", "")
        snap = run.get("snapshot")
        if snap and self.snapshots is not None:
            try:
                desc = self.snapshots.describe(snap)
            except Exception:  # noqa: BLE001
                desc = ""
            if desc.startswith(ROLLBACK_MARK):
                stage, _, error = desc[len(ROLLBACK_MARK):].strip().partition(": ")
        out = self._rollback({**run, "snapshot": None}, run.get("component", ""), StepFailed(stage, error))
        return {**out, "tag": run["tag"]}

    # ---------------------------------------------------------------- the run
    def apply(self, tag: str, sha: str, manifest, manual: bool = False) -> dict:
        """Installs the server side of `tag`. Returns {"result": installed|deferred|rolled_back|failed|handed_off,
        "component", "stage", "error", "installed": [...]}. Never raises."""
        try:
            return self._apply(tag, sha, manifest, manual)
        except Exception as err:  # noqa: BLE001 - before any change (plan, git): nothing to undo
            LOG.error("OTA: server apply failed: %s", type(err).__name__)
            return {"result": "failed", "stage": "prepare", "error": type(err).__name__, "tag": tag}

    def _apply(self, tag: str, sha: str, manifest, manual: bool) -> dict:
        with self.store.lock:
            pending = isinstance(self._ota().get("run"), dict)
        if pending:  # recover() has not been able to undo it yet (e.g. a conversation is open)
            return {"result": "deferred", "stage": "pending_run"}
        if self._self_running():
            return {"result": "deferred", "stage": "self_update"}  # the agent's own replacement is still deciding
        if self.busy():
            return {"result": "deferred", "stage": "busy"}
        comps = self.plan(sha, manifest)
        if not comps:
            self._published(tag)
            return {"result": "installed", "tag": tag, "installed": []}
        heavy = [c for c in comps if c in manifest.heavy]
        if heavy and not manual and self.idle.idle_for(self._clock()) < HEAVY_IDLE_S:
            return {"result": "deferred", "stage": "heavy_idle"}
        work = os.path.join(self.paths["root"], "work", f"{tag}-{int(self._clock())}")
        shutil.rmtree(os.path.join(self.paths["root"], "work"), ignore_errors=True)  # no run is pending here
        os.makedirs(work)
        line = f"{tag} {sha}"
        run = {"tag": tag, "sha": sha, "work": work, "done": [], "component": "", "stage": "prepare",
               "prev": {c: self.installed_line(c) for c in comps}, "prev_image": self._image(), "snapshot": None,
               "switched": False, "image_backup": None}
        try:
            tree = self._tree(tag, sha, os.path.join(work, "new"))
            if "container" in comps:
                self._snapshot(run, tag)  # before the first change: a VM rollback undoes the whole run
        except Exception as err:  # noqa: BLE001 - nothing changed yet
            self._save_run(None)
            shutil.rmtree(work, ignore_errors=True)
            e = err if isinstance(err, StepFailed) else StepFailed("prepare", type(err).__name__)
            return {"result": "failed", "stage": e.stage, "error": e.error, "tag": tag}
        for comp in comps:
            if comp == "server-agent":
                return self._hand_off(run, tree, line)
            run.update(component=comp, stage="install")
            try:
                if self.busy():
                    raise Busy("busy")
                if comp == "container":
                    self._container(run, tree, line)
                else:
                    run["done"].append(comp)  # written before the install: a crash mid-install is undone too
                    self._save_run(run)
                    self._install(tree, comp, line)
                    since = self._restart_voice() if comp == "voice-gateway" else self._clock()
                    if not self.healthy(comp, tree, line, since):
                        raise StepFailed("health", f"{comp} not healthy in {HEALTH_TIMEOUT_S}s")
                self._record(comp, tag, sha)
            except Busy:
                # earlier components stay (healthy, recorded; the next try skips them); only the one in progress,
                # if it was touched, goes back — its files would otherwise load untested on the next restart
                out = self._rollback(run, comp, StepFailed("busy", "busy"), [comp] if comp in run["done"] else [])
                return {**out, "tag": tag} if out["result"] == "failed" else {"result": "deferred", "stage": "busy",
                                                                             "tag": tag}
            except Exception as err:  # noqa: BLE001 - a timeout, a full disk: the run goes back like any failure
                e = err if isinstance(err, StepFailed) else StepFailed(run.get("stage") or "install",
                                                                       type(err).__name__)
                return {**self._rollback(run, comp, e), "tag": tag}
        self._save_run(None)
        shutil.rmtree(work, ignore_errors=True)
        self._published(tag)
        return {"result": "installed", "tag": tag, "installed": comps}

    def _published(self, tag: str) -> None:
        with self.store.lock:
            self._ota()["integration_target"] = tag  # HA installs the integration of the installed server (Task 17)

    def _container(self, run: dict, tree: str, line: str) -> None:
        backup = os.path.join(run["work"], "image-prev")
        if os.path.isdir(self.paths["image_dir"]):
            shutil.copytree(self.paths["image_dir"], backup, dirs_exist_ok=True)
            run["image_backup"] = backup
        run["stage"] = "build"
        run["done"].append("container")  # its sources change from here on
        self._save_run(run)
        self._install(tree, "container", line)
        if self.busy():
            raise Busy("conversation opened during the build")
        image_tag = line.split()[0]
        run.update(stage="switch", switched=True)
        self._save_run(run)
        since = self._clock()
        self._compose_up(image_tag)
        if not self.healthy("container", tree, line, since):
            raise StepFailed("health", f"container not healthy in {HEALTH_TIMEOUT_S}s")
        prev = run.get("prev_image", "").partition(":")[2]
        self._prune_images([image_tag] + ([prev] if prev else []))

    # ---------------------------------------------------------------- the agent itself
    def _self_paths(self) -> tuple[str, str, str]:
        base = os.path.join(self.paths["root"], "self")
        return os.path.join(base, "new"), os.path.join(base, "old"), os.path.join(self.paths["root"], "self.json")

    def _hand_off(self, run: dict, tree: str, line: str) -> dict:
        """Last step: copies the trees to a lasting place and lets systemd replace the agent (this process ends).
        The run stays saved (stage `handoff`) until the result is absorbed: an agent put back means the whole
        release goes back."""
        prev = self.installed_line("server-agent")
        new_dir, old_dir, result = self._self_paths()
        try:
            prev_sha = version_line_sha(prev)
            if not prev_sha:
                raise StepFailed("prepare", "previous agent version unknown")
            for d in (new_dir, old_dir):
                shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(tree, new_dir)
            self._tree(prev.split()[0], prev_sha, old_dir)
            run.update(component="server-agent", stage="handoff")
            self._save_run(run)
            r = self._sh(["systemd-run", "--unit", "friday-ota-self", "--collect", "bash",
                          os.path.join(new_dir, "ota", "self_update.sh"), new_dir, line, old_dir, prev, result,
                          str(HEALTH_TIMEOUT_S)])
            if r.returncode != 0:
                raise StepFailed("handoff", (r.stderr or "").strip()[-200:] or f"exit {r.returncode}")
        except Exception as err:  # noqa: BLE001 - the release fails as a whole: the others go back too
            failure = err if isinstance(err, StepFailed) else StepFailed("prepare", type(err).__name__)
            out = self._rollback(run, "server-agent", failure)
            if out["result"] == "rolled_back" and not run["done"]:
                out["result"] = "failed"
            return {**out, "tag": run["tag"]}
        return {"result": "handed_off", "component": "server-agent", "tag": run["tag"],
                "installed": list(run["done"])}

    def absorb_self_result(self) -> dict | None:
        """The outcome `self_update.sh` left for us (we may be the new agent or the old one put back)."""
        result = self._self_paths()[2]
        try:
            with open(result, encoding="utf-8") as f:
                out = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            out = {"tag": "", "result": "failed", "stage": "handoff"}
        if not isinstance(out, dict):
            out = {"tag": "", "result": "failed", "stage": "handoff"}
        with self.store.lock:
            run = self._ota().get("run")
        run = dict(run) if isinstance(run, dict) and run.get("stage") == "handoff" else None
        if run and out.get("result") != "installed" and run.get("switched") and self.busy():
            return None  # the revert restarts the voice: the result file waits for the end of the conversation
        try:
            os.remove(result)
        except OSError:
            pass
        tag = str(out.get("tag") or (run or {}).get("tag") or "")
        out.update(component="server-agent", tag=tag)
        if out.get("result") == "installed":
            sha = version_line_sha(self.installed_line("server-agent")) or ""
            self._record("server-agent", tag, sha)
            if run:
                self._save_run(None)
                shutil.rmtree(run.get("work") or "", ignore_errors=True)
            self._published(tag)
            return out
        if run:  # the agent went back: so does everything the run installed before it
            run["work"] = run.get("work") or os.path.join(self.paths["root"], "work", "recover")
            os.makedirs(run["work"], exist_ok=True)
            rb = self._rollback(run, "server-agent", StepFailed(str(out.get("stage") or "health"),
                                                                "the new agent did not come up"))
            if rb["result"] == "failed":
                return {**rb, "tag": tag}
        out["result"] = "rolled_back" if out.get("result") != "failed" else "failed"
        return out
