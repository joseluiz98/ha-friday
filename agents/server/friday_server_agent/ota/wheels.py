"""arm64 wheels for the satellite venv, built on the VM (spec 2026-11-10 §16), cached by the lock hash.

A Pi 3 building numpy/onnxruntime itself takes ages; the VM runs `pip wheel` in an emulated arm64 container with the
Pi's Python (3.13, Trixie) only when the lock changed, and the satellite installs offline from the result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
from typing import Callable

IMAGE = "python:3.13-slim-trixie"


class WheelsError(RuntimeError):
    pass


def build(locks: list[str], lock_sha256: str, out_root: str = "/srv/friday/ota/wheels",
          run: Callable = subprocess.run) -> str:
    """Returns `<out_root>/<lock_sha256>/wheels.tar.gz` (members `wheels/<file>.whl`), building it once."""
    folder = os.path.join(out_root, lock_sha256)
    final = os.path.join(folder, "wheels.tar.gz")
    if os.path.exists(final):
        return final
    os.makedirs(folder, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".build-", dir=folder)
    try:
        out = os.path.join(work, "out")
        os.makedirs(out)
        cmd = ["docker", "run", "--rm", "--platform", "linux/arm64", "-v", f"{out}:/w"]
        reqs = []
        for i, lock in enumerate(locks):
            cmd += ["-v", f"{os.path.abspath(lock)}:/l{i}:ro"]
            reqs += ["-r", f"/l{i}"]
        cmd += [IMAGE, "pip", "wheel", "--no-deps", "--no-cache-dir", "-w", "/w", *reqs]  # the locks are a full freeze, installed --no-deps
        try:
            r = run(cmd, capture_output=True, timeout=7200)
        except subprocess.TimeoutExpired:
            raise WheelsError("pip wheel timed out")
        if r.returncode != 0:
            raise WheelsError(f"pip wheel failed (rc {r.returncode})")
        tmp = os.path.join(work, "wheels.tar.gz")
        with tarfile.open(tmp, "w:gz") as tar:
            for name in sorted(os.listdir(out)):
                tar.add(os.path.join(out, name), arcname=f"wheels/{name}", recursive=False)
        os.chmod(tmp, 0o644)
        os.replace(tmp, final)
        return final
    finally:
        shutil.rmtree(work, ignore_errors=True)
