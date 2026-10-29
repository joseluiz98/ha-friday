"""Persistent global voice switch: a one-word file the Voice Gateway reads on every new offer."""

from __future__ import annotations

import os

DESCRIPTOR = {"key": "voice", "platform": "switch", "name": "F.R.I.D.A.Y.", "icon": "mdi:account-voice"}
DEFAULT_PATH = "/srv/friday/state/voice-enabled"


def read_enabled(path: str) -> bool:
    """Missing or unreadable file means enabled; only "off" disables."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip().lower() != "off"
    except OSError:
        return True


class VoiceSwitch:
    def __init__(self, path: str = DEFAULT_PATH) -> None:
        self.path = path
        self.enabled = read_enabled(path)

    def set(self, value: bool) -> dict:
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("on\n" if value else "off\n")
        os.replace(tmp, self.path)
        self.enabled = bool(value)
        return {"voice": self.enabled}


def make_set_handler(sw: VoiceSwitch):
    async def handle(cmd: dict) -> dict:
        if cmd.get("key") != "voice":
            return {"ok": False, "error": "unknown_key"}
        return {"data": sw.set(bool(cmd.get("value")))}
    return handle
