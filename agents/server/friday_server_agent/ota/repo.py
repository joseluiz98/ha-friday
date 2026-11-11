"""The VM's own clone of the public repo (spec 2026-11-10 §16): never the owner's working copy on HAOS."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

from friday_agent_core.ota import manifest as mf


class RepoError(RuntimeError):
    pass


class VmRepo:
    def __init__(self, root: str = "/srv/friday/ota/repo", url: str | None = None, run: Callable = subprocess.run):
        self.root = root
        self.url = url or f"https://github.com/{os.environ.get('FRIDAY_OTA_REPO', 'joseluiz98/ha-friday')}.git"
        self._run = run

    def _git(self, *args: str, timeout: int = 300) -> bytes:
        r = self._run(["git", "-C", self.root, *args], capture_output=True, timeout=timeout)
        if r.returncode != 0:
            raise RepoError(f"git {args[0]} failed (rc {r.returncode})")
        return r.stdout

    def ensure(self) -> None:
        if os.path.isdir(os.path.join(self.root, ".git")) or os.path.exists(os.path.join(self.root, "HEAD")):
            return
        os.makedirs(os.path.dirname(self.root), exist_ok=True)
        r = self._run(["git", "clone", "--filter=blob:none", "--no-checkout", self.url, self.root],
                      capture_output=True, timeout=600)
        if r.returncode != 0:
            raise RepoError(f"git clone failed (rc {r.returncode})")

    def fetch_tags(self) -> None:
        self._git("fetch", "--tags", "--force", "--prune", "origin", timeout=600)

    def archive(self, sha: str, paths: list[str]) -> bytes:
        """Uncompressed tar of the `paths` that exist at `sha` (an older release may lack a newer folder)."""
        listed = self._git("ls-tree", "--name-only", sha, "--", *paths).decode().split()
        keep = [p for p in paths if p in listed]
        if not keep:
            raise RepoError("nothing to archive")
        return self._git("archive", "--format=tar", sha, "--", *keep)

    def changed_paths(self, old_sha: str | None, new_sha: str) -> list[str]:
        if old_sha is None:
            out = self._git("ls-tree", "-r", "--name-only", new_sha)
        else:
            out = self._git("diff", "--name-only", old_sha, new_sha)
        return [p for p in out.decode().splitlines() if p]

    def manifest(self, sha: str) -> mf.Manifest | None:
        """`ota/release.json` at `sha`, or None for a release older than the OTA (callers treat it as protocol 1)."""
        r = self._run(["git", "-C", self.root, "show", f"{sha}:ota/release.json"], capture_output=True, timeout=60)
        if r.returncode == 0:
            return mf.parse(json.loads(r.stdout))
        if self._git("ls-tree", "--name-only", sha, "--", "ota/release.json").strip():
            raise RepoError("git show failed for a manifest that exists")  # a fetch error is not an old release
        return None
