"""Button commands of the server agent: Restart F.R.I.D.A.Y. (the whole container).

Rule copied from /opt/friday/status/friday_status.py POST /v1/restart: refuse while a voice
conversation is open (decided from the container log) or when the log cannot be read.

Voice Gateway / Brain have no separate restart: see README ("Process restarts").
"""

from __future__ import annotations

import asyncio
import subprocess

COMPOSE = "/opt/friday/image/docker-compose.yml"
RESTART_DESCRIPTOR = {"key": "restart_friday", "platform": "button", "name": "Restart F.R.I.D.A.Y.",
                      "device_class": "restart"}


def compose_restart() -> None:
    """Detached, so the reply to Home Assistant leaves before the container goes down."""
    subprocess.Popen(["docker", "compose", "-f", COMPOSE, "restart", "friday"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def make_handlers(log_tail, restart, push_log):
    """`log_tail()` returns the container log text (may raise OSError/SubprocessError);
    `restart()` starts the restart; `push_log` is an async callable(message, data)."""

    from .collectors import conversation_open

    async def press(cmd: dict) -> dict:
        if cmd.get("key") != "restart_friday":
            return {"ok": False, "error": "unknown_key"}
        try:
            text = await asyncio.to_thread(log_tail)
            if text is None:
                raise OSError("no log")
            busy = conversation_open(text)
        except (OSError, subprocess.SubprocessError):
            await push_log("Restart F.R.I.D.A.Y. refused: container log unavailable", {})
            return {"ok": False, "error": "log_unavailable"}
        if busy:
            await push_log("Restart F.R.I.D.A.Y. refused: a conversation is open", {})
            return {"ok": False, "error": "conversation_open"}
        try:
            restart()
        except (OSError, subprocess.SubprocessError):
            await push_log("Restart F.R.I.D.A.Y. failed to start", {})
            return {"ok": False, "error": "restart_failed"}
        await push_log("Restart F.R.I.D.A.Y. started", {})
        return {}

    return {"press": press}
