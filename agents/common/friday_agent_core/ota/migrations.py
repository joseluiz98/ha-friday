"""Flyway-style migration runner: NNNN_<description>.sh|.py, each runs once, a failure stops everything
and the failed one is not recorded. The caller (privileged helper) supplies the environment."""
import json
import os
import re
import subprocess
import sys
import time

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.(sh|py)$")
TIMEOUT_S = 900


class MigrationError(RuntimeError):
    def __init__(self, name: str, message: str = ""):
        super().__init__(f"migration {name} failed" + (f": {message}" if message else ""))
        self.name = name


def discover(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    seen: dict[str, str] = {}
    names = []
    for n in sorted(os.listdir(folder)):
        m = _NAME.match(n)
        if not m:
            continue
        if m.group(1) in seen:
            raise MigrationError(n, f"same number as {seen[m.group(1)]}")
        seen[m.group(1)] = n
        names.append(n)
    return names


def load_done(record: str) -> dict[str, dict]:
    try:
        with open(record, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def pending(folder: str, record: str) -> list[str]:
    done = load_done(record)
    return [n for n in discover(folder) if n not in done]


def _save(record: str, done: dict[str, dict]) -> None:
    tmp = record + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(done, f, indent=1, sort_keys=True)
    os.chmod(tmp, 0o644)
    os.replace(tmp, record)


def run_pending(folder: str, record: str, env: dict[str, str], run=subprocess.run, now=time.time) -> list[str]:
    done = load_done(record)
    ran = []
    for name in [n for n in discover(folder) if n not in done]:
        path = os.path.join(folder, name)
        cmd = ["bash", path] if name.endswith(".sh") else [sys.executable, path]
        try:
            res = run(cmd, env={**os.environ, **env}, timeout=TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            raise MigrationError(name, "timeout") from None
        if res.returncode != 0:
            raise MigrationError(name, f"exit {res.returncode}")
        done[name] = {"at": now(), "tag": env.get("FRIDAY_TAG", "")}
        _save(record, done)
        ran.append(name)
    return ran
