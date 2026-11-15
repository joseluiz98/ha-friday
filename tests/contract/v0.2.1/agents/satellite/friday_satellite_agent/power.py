"""Voice on/off (with courtesy), reboot and program restart. Commands go through an injectable runner."""

from __future__ import annotations

import asyncio
import logging
import os

LOG = logging.getLogger("friday_satellite_agent")

SERVICE = "friday-satelite"
START = ["sudo", "-n", "/usr/bin/systemctl", "start", SERVICE]
STOP = ["sudo", "-n", "/usr/bin/systemctl", "stop", SERVICE]
RESTART = ["sudo", "-n", "/usr/bin/systemctl", "restart", SERVICE]
REBOOT = ["sudo", "-n", "/usr/sbin/reboot"]
WAIT_MAX_S = 120
DEFAULT_PATH = os.path.expanduser("~/.friday-agent/voice-enabled")


async def run_command(argv: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return await proc.wait()


def read_enabled(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() != "off"
    except OSError:
        return True


class Power:
    def __init__(self, runner=run_command, path: str = DEFAULT_PATH, program_state=lambda: "idle",
                 sleep=asyncio.sleep, log=None) -> None:
        self._run = runner
        self.path = path
        self._state = program_state
        self._sleep = sleep
        self._log = log
        self.enabled = read_enabled(path)

    def persist(self, enabled: bool) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("on\n" if enabled else "off\n")
        self.enabled = enabled

    async def startup(self) -> None:
        """Agent boot: if voice is off, make sure the service is stopped."""
        if not self.enabled:
            try:
                await self._run(STOP)
            except Exception as err:  # noqa: BLE001 - the agent must still start
                LOG.warning("startup stop failed: %s", type(err).__name__)

    async def set_voice(self, enabled: bool) -> int:
        self.persist(enabled)  # first, so an agent restart mid-wait still ends up off
        if enabled:
            return await self._run(START)
        for _ in range(WAIT_MAX_S):
            if self._state() != "conversation":
                break
            await self._sleep(1)
        else:
            LOG.warning("voice off: conversation still open after %ss, stopping anyway", WAIT_MAX_S)
        return await self._run(STOP)

    @staticmethod
    def _result(code) -> dict:
        return {"ok": True} if code == 0 else {"ok": False, "error": "command_failed"}

    async def reboot(self) -> dict:
        if self._state() == "conversation":
            return {"ok": False, "error": "conversation_open"}
        if self._log:
            try:
                await self._log("Reboot requested")
            except Exception as err:  # noqa: BLE001 - logging never blocks the action
                LOG.warning("reboot log failed: %s", type(err).__name__)
        return self._result(await self._run(REBOOT))

    async def restart_program(self) -> dict:
        if self._state() == "conversation":
            return {"ok": False, "error": "conversation_open"}
        return self._result(await self._run(RESTART))
