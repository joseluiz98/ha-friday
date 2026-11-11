"""Provisioning image built on the VM (spec 2026-11-10 §10; plan Task 12).

A stable release (or the `image_build` command) builds the satellite image of that tag: `stage.sh` from the release
tree assembles the payload with the release's OTA package (the image is born on the versioned layout), `build.sh`
builds it; the result goes to the HAOS share (`/mnt/haos/share/friday/imagens`, the PC sees it over Samba), keeping
`KEEP_IMAGES`. One build at a time (a file lock), detached from the agent (`systemd-run --unit friday-image-build`,
Nice=19, idle I/O), state in `/srv/friday/state/ota/image.json`:
{"tag", "built_at", "status": "ok|building|error", "error", "files": [...]}.
"""

from __future__ import annotations

import fcntl
import glob
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Callable

from friday_agent_core.ota.constants import KEEP_IMAGES

LOG = logging.getLogger("friday_server_agent.ota")
STATE = "/srv/friday/state/ota/image.json"
LOCK = "/srv/friday/state/ota/image.lock"
SHARE = "/mnt/haos/share/friday/imagens"
OTA_ROOT = "/srv/friday/ota"
UNIT = "friday-image-build"
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
NAME = "friday-satellite-{tag}"
BUILD_TIMEOUT_S = 4 * 3600


def _vkey(tag: str) -> tuple:
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", tag)
    return tuple(int(x) for x in m.groups()) if m else (-1,)


class ImageBuilder:
    def __init__(self, state_path: str = STATE, lock_path: str = LOCK, share: str = SHARE, root: str = OTA_ROOT,
                 run: Callable = subprocess.run, clock: Callable[[], float] = time.time,
                 work: str | None = None):
        self.state_path, self.lock_path, self.share, self.root = state_path, lock_path, share, root
        self._run, self._clock = run, clock
        self.work = work or os.path.join(root, "image-work")

    # ---------------------------------------------------------------- state
    def _read(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, **fields) -> dict:
        data = {"tag": None, "built_at": None, "status": None, "error": "", "files": [], **self._read(), **fields}
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
        os.replace(tmp, self.state_path)
        return data

    def _locked(self) -> bool:
        """True when a build holds the lock right now."""
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        with open(self.lock_path, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(f, fcntl.LOCK_UN)
        return False

    def status(self) -> dict:
        data = self._read()
        if data.get("status") == "building" and not self._locked() and not self._unit_active():
            data = self._write(status="error", error="interrupted")  # the VM rebooted mid-build, say
        data["files"] = sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.share, "friday-satellite-*")))
        return data

    def _unit_active(self) -> bool:
        try:
            return self._run(["systemctl", "is-active", "-q", UNIT], timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    # ---------------------------------------------------------------- start (agent) / build (detached unit)
    def start(self, tag: str, sha: str) -> dict:
        if not TAG.match(tag or "") or not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
            return {"ok": False, "error": "tag"}
        if self._locked() or self._unit_active():
            return {"ok": False, "error": "busy"}
        r = self._run(["systemd-run", "--unit", UNIT, "--collect", "--property=Nice=19",
                       "--property=IOSchedulingClass=idle", f"--setenv=PYTHONPATH={os.pathsep.join(sys.path[1:])}",
                       sys.executable, "-m", "friday_server_agent.ota.image", "build", tag, sha],
                      capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {"ok": False, "error": "systemd-run"}
        self._write(tag=tag, status="building", error="", started_at=self._clock())
        return {"ok": True}

    def build(self, tag: str, sha: str) -> dict:
        """The whole build, in the detached unit. One at a time: a second call while one runs returns busy."""
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        with open(self.lock_path, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ok": False, "error": "busy"}
            self._write(tag=tag, status="building", error="", started_at=self._clock())
            try:
                files = self._build(tag, sha)
            except Exception as err:  # noqa: BLE001 - recorded for HA, never silent
                msg = str(err)[:200] if isinstance(err, BuildError) else type(err).__name__
                LOG.error("image build %s failed: %s", tag, msg)
                return self._write(tag=tag, status="error", error=msg)
            finally:
                shutil.rmtree(os.path.join(self.work, "out"), ignore_errors=True)
            self.retain()
            return self._write(tag=tag, status="ok", error="", built_at=self._clock(), files=files)

    def _step(self, stage: str, cmd: list[str], **kw) -> None:
        try:
            r = self._run(cmd, capture_output=True, text=True, errors="replace", timeout=BUILD_TIMEOUT_S, **kw)
        except subprocess.TimeoutExpired:
            raise BuildError(f"{stage}: timeout") from None
        if r.returncode != 0:
            tail = ((r.stderr or r.stdout or "").strip().splitlines() or [f"exit {r.returncode}"])[-1]
            raise BuildError(f"{stage}: {tail}")

    def _build(self, tag: str, sha: str) -> list[str]:
        package = os.path.join(self.root, "packages", tag, "satellite.tar.gz")
        if not os.path.isfile(package):
            raise BuildError("package_missing")
        repo = os.path.join(self.root, "repo")
        tree = tempfile.mkdtemp(prefix="friday-image-src-")
        out = os.path.join(self.work, "out")
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        try:  # the image tooling of the release itself (stage.sh, build.sh, customize-chroot.sh)
            arch = self._run(["git", "-C", repo, "archive", "--format=tar", sha, "satellite/image"],
                             capture_output=True, timeout=300)
            if arch.returncode != 0:
                raise BuildError("source: git archive failed")
            with tarfile.open(fileobj=io.BytesIO(arch.stdout)) as t:
                t.extractall(tree, filter="data")
            src = os.path.join(self.work, "src")
            env = {**os.environ, "FRIDAY_IMG_SRC": src, "FRIDAY_IMG_OUT": out, "FRIDAY_IMG_NAME": NAME.format(tag=tag),
                   "FRIDAY_IMG_WORK": os.path.join(self.work, "build")}
            self._step("stage", ["bash", os.path.join(tree, "satellite/image/stage.sh"), "--repo", repo, "--ref", sha,
                                 "--local", "--package", package], env=env)
            self._step("build", ["bash", os.path.join(src, "build.sh")], env=env, cwd=src)
        finally:
            shutil.rmtree(tree, ignore_errors=True)
        name = NAME.format(tag=tag)
        produced = [f"{name}.img.xz", f"{name}.img.xz.sha256", f"{name}.json"]
        missing = [p for p in produced if not os.path.isfile(os.path.join(out, p))]
        if missing:
            raise BuildError(f"build: missing {', '.join(missing)}")
        os.makedirs(self.share, mode=0o755, exist_ok=True)
        for p in produced:
            shutil.copyfile(os.path.join(out, p), os.path.join(self.share, p + ".part"))
            os.replace(os.path.join(self.share, p + ".part"), os.path.join(self.share, p))
        return produced

    def retain(self) -> list[str]:
        """Keeps the images of the KEEP_IMAGES newest tags in the share. Returns the tags removed."""
        tags = set()
        for p in glob.glob(os.path.join(self.share, "friday-satellite-v*")):
            m = re.match(r"^friday-satellite-(v\d+\.\d+\.\d+)\.", os.path.basename(p))
            if m:
                tags.add(m.group(1))
        old = sorted(tags, key=_vkey)[:-KEEP_IMAGES] if len(tags) > KEEP_IMAGES else []
        for t in old:
            for p in glob.glob(os.path.join(self.share, f"friday-satellite-{t}.*")):
                os.remove(p)
        return old


class BuildError(RuntimeError):
    pass


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    if len(argv) == 3 and argv[0] == "build":
        return 0 if ImageBuilder().build(argv[1], argv[2]).get("status") == "ok" else 1
    print("usage: python -m friday_server_agent.ota.image build <tag> <sha>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
