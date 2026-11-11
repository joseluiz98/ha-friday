"""OTA state on the VM (spec 2026-11-10 §13): one JSON file written atomically, an NDJSON event log, satellite tokens.

Nothing secret goes into the state or the events; the satellite tokens live apart, hashed, in a 0600 file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from friday_agent_core.ota import channels as ch
from friday_agent_core.ota import retry

from .github import ReleaseInfo

STATE_PATH = "/srv/friday/state/ota/state.json"
EVENTS_PATH = "/srv/friday/state/ota/events.ndjson"
TOKENS_PATH = "/srv/friday/secrets/ota_tokens.json"


def _write_json(path: str, data, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _empty() -> dict:
    return {"releases": {}, "soak": {}, "devices": {}, "server": {}, "auto_install": True, "pushes": [],
            "server_protocol": 1}


class OtaStore:
    def __init__(self, path: str = STATE_PATH, events_path: str = EVENTS_PATH, clock=time.time):
        self.path, self.events_path, self._clock = path, events_path, clock
        self.lock = threading.RLock()
        try:
            with open(path, encoding="utf-8") as f:
                self.data = {**_empty(), **json.load(f)}
        except (OSError, ValueError):
            self.data = _empty()

    def save(self) -> None:
        with self.lock:
            _write_json(self.path, self.data)

    def event(self, device: str, tag: str | None, event: str, stage: str = "", error: str = "") -> None:
        line = {"at": self._clock(), "device": device, "tag": tag, "event": event, "stage": stage[:200],
                "error": error[:200]}
        os.makedirs(os.path.dirname(self.events_path), exist_ok=True)
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")

    # releases
    def set_releases(self, infos: dict[str, ReleaseInfo]) -> None:
        with self.lock:
            self.data["releases"] = {t: {"tag": i.release.tag, "sha": i.release.sha,
                                         "published_at": i.release.published_at, "ci": i.release.ci,
                                         "prerelease": i.release.prerelease, "notes": i.notes, "url": i.url}
                                     for t, i in infos.items()}

    def release_infos(self) -> dict[str, ReleaseInfo]:
        return {t: ReleaseInfo(release=ch.Release(r["tag"], r["sha"], r["published_at"], r["ci"], r["prerelease"]),
                               notes=r.get("notes", ""), url=r.get("url", ""))
                for t, r in self.data["releases"].items()}

    def soak(self) -> dict[str, ch.SoakRecord]:
        return {t: ch.SoakRecord(s.get("started_at"), bool(s.get("failed"))) for t, s in self.data["soak"].items()}

    def soak_start(self, tag: str, now: float) -> None:
        s = self.data["soak"].setdefault(tag, {"started_at": None, "failed": False})
        if s["started_at"] is None:
            s["started_at"] = now

    def soak_fail(self, tag: str) -> None:
        self.data["soak"].setdefault(tag, {"started_at": None, "failed": False})["failed"] = True

    # devices
    def device(self, sat_id: str) -> dict:
        return self.data["devices"].setdefault(sat_id, {"channel": "stable", "installed": None, "protocol": 1,
                                                        "lock_sha256": "", "attempts": None, "last_report": None,
                                                        "install_requested": False, "last_seen": None})

    def attempts(self, sat_id: str) -> retry.Attempts | None:
        a = self.device(sat_id)["attempts"]
        return retry.Attempts.from_dict(a) if a else None

    @property
    def auto_install(self) -> bool:
        return bool(self.data["auto_install"])

    def set_server_protocol(self, proto: int) -> None:
        with self.lock:
            self.data["server_protocol"] = int(proto)
            self.save()


class TokenBook:
    """Per-satellite bearer tokens: only sha256 stored (0600 root); issuing again replaces the old one."""

    def __init__(self, path: str = TOKENS_PATH):
        self.path = path

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, sat_id: str) -> str:
        token = secrets.token_urlsafe(32)
        book = self._load()
        book[sat_id] = self._hash(token)
        _write_json(self.path, book, 0o600)
        return token

    def check(self, sat_id: str, token: str) -> bool:
        want = self._load().get(sat_id)
        return bool(want and token) and hmac.compare_digest(want, self._hash(token))
