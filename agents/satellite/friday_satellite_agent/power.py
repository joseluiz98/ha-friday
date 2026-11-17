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
# read-only: `systemctl show` needs no sudo (and the sudoers file stays exactly as it is)
SHOW = ["/usr/bin/systemctl", "show", SERVICE, "-p", "ActiveState", "-p", "SubState", "-p", "Result"]
WAIT_MAX_S = 120
START_WAIT_S = 30
# program_state values (capabilities.STATES plus "off") in which the voice program is really running
RUNNING_STATES = frozenset({"idle", "listening", "conversation"})
DEFAULT_PATH = os.path.expanduser("~/.friday-agent/voice-enabled")


async def run_command(argv: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return await proc.wait()


async def run_show(argv: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return out.decode("utf-8", "replace")


def voice_running(program_state: str | None) -> bool:
    """The "Voice Program" switch shows whether the program really runs, not what was last asked."""
    return program_state in RUNNING_STATES


def start_failure_reason(show_text: str) -> str:
    """Why the service did not come up, from `systemctl show` (ExecCondition skips it when no supported audio device)."""
    props = dict(line.split("=", 1) for line in show_text.splitlines() if "=" in line)
    if props.get("Result") == "exec-condition":
        return "voice program skipped: start condition not met (no supported audio device)"
    return " ".join(f"{k}={props.get(k, '?')}" for k in ("ActiveState", "SubState", "Result"))


def read_enabled(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() != "off"
    except OSError:
        return True


class Power:
    def __init__(self, runner=run_command, path: str = DEFAULT_PATH, program_state=lambda: "idle",
                 sleep=asyncio.sleep, log=None, show_runner=run_show) -> None:
        self._run = runner
        self._show = show_runner
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

    async def set_voice(self, enabled: bool):
        """On: start the service and return at once -> {"ok"} (the caller then runs `await_start` in the background:
        HA gives a command 10 s and the agent handles commands one at a time). Off: courtesy wait for an open
        conversation, then stop -> {"ok"}."""
        self.persist(enabled)  # first, so an agent restart mid-wait still ends up off
        if enabled:
            return self._result(await self._run(START))
        for _ in range(WAIT_MAX_S):
            if self._state() != "conversation":
                break
            await self._sleep(1)
        else:
            LOG.warning("voice off: conversation still open after %ss, stopping anyway", WAIT_MAX_S)
        return self._result(await self._run(STOP))

    async def await_start(self) -> bool:
        """After a successful start: wait up to START_WAIT_S for the program to answer; log why if it never does."""
        for _ in range(START_WAIT_S):
            await self._sleep(1)
            if voice_running(self._state()):
                return True
        await self._log_start_failure()
        return False

    async def _log_start_failure(self) -> None:
        if not self._log:
            return
        try:
            reason = start_failure_reason(await self._show(SHOW))
            await self._log(f"Voice program did not start: {reason}")
        except Exception as err:  # noqa: BLE001 - logging never blocks the action
            LOG.warning("start failure log failed: %s", type(err).__name__)

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
