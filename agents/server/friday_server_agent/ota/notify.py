"""Telegram push for OTA failures, through the `oc` shortcut (OpenClaw inside the friday container).

Pushes wait in a persisted list (`store_pushes`, owned by the caller) until the quiet-hours window opens.
"""
from __future__ import annotations

import subprocess
import time

from friday_agent_core.ota import retry

SEND_TIMEOUT_S = 30


def format_exhausted(device: str, tag: str, stage: str, error: str, staying_on: str | None) -> str:
    stay = f"Segue na versão {staying_on}." if staying_on else "Segue na versão anterior."
    return (f"F.R.I.D.A.Y.: a atualização {tag} de {device} falhou em {stage} ({error}) "
            f"depois de 5 tentativas. {stay}")


class Notifier:
    def __init__(self, chat: str, run=subprocess.run, clock=time.time, tz=None):
        if not chat or not chat.strip():
            raise ValueError("telegram chat is empty")
        self.chat = chat.strip()
        self._run = run
        self._clock = clock
        self._tz = tz

    def queue(self, text: str, store_pushes: list[dict]) -> None:
        store_pushes.append({"at": retry.push_at(self._clock(), self._tz), "text": text})

    def _send(self, text: str) -> bool:
        cmd = ["oc", "message", "send", "--channel", "telegram", "--target", self.chat, "-m", text]
        try:
            res = self._run(cmd, timeout=SEND_TIMEOUT_S, capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0

    def flush(self, store_pushes: list[dict]) -> int:
        now = self._clock()
        kept, sent = [], 0
        for push in store_pushes:
            if push.get("at", 0) <= now and self._send(push["text"]):
                sent += 1
            else:
                kept.append(push)
        store_pushes[:] = kept
        return sent


class NullNotifier:
    """No FRIDAY_TELEGRAM_CHAT: pushes stay queued in the state (visible in HA) and are never sent."""

    def queue(self, text: str, store_pushes: list[dict]) -> None:
        store_pushes.append({"at": 0, "text": text})

    def flush(self, store_pushes: list[dict]) -> int:
        return 0


def notifier_for(chat: str | None):
    """The real Notifier when a Telegram chat is configured; a blank or missing chat gives the NullNotifier."""
    return Notifier(chat) if chat and chat.strip() else NullNotifier()
