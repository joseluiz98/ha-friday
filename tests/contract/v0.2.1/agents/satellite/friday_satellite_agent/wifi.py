"""Wi-Fi change with automatic, bounded rollback. Commands go through an injectable runner.

The password travels only on the stdin of the root helper `friday-wifi-apply` (see sudoers): never in argv,
never in a log, never in a result. Read-only nmcli queries run without sudo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time

LOG = logging.getLogger("friday_satellite_agent")

HELPER = ["sudo", "-n", "/usr/local/sbin/friday-wifi-apply"]
IFACE = "wlan0"
PING_INTERVAL_S = 2
CMD_TIMEOUT_S = 30        # a helper call activates with `-w 20`; this is the outer bound
DEADLINE_S = 75           # whole attempt (apply + connectivity checks)
ROLLBACK_WAIT_S = 20
MARKER = os.path.expanduser("~/.friday-agent/wifi-change.json")
_NAME = re.compile(r"friday-wifi-[0-9]+")


async def run_command(argv: list[str], stdin: str | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await proc.communicate(stdin.encode() if stdin is not None else None)
    except asyncio.CancelledError:
        proc.kill()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace")


def nmcli_available() -> bool:
    return shutil.which("nmcli") is not None


def _split(line: str) -> list[str]:
    """Split an `nmcli -t` line on unescaped colons."""
    parts, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def active_wifi_profile(output: str) -> str | None:
    for line in output.splitlines():
        parts = _split(line)
        if len(parts) >= 2 and parts[1] == "802-11-wireless":
            return parts[0]
    return None


def valid_credentials(ssid, password) -> bool:
    return (isinstance(ssid, str) and isinstance(password, str)
            and 1 <= len(ssid.encode("utf-8")) <= 32 and 8 <= len(password.encode("utf-8")) <= 63
            and not any(c in ssid + password for c in "\n\r\0"))


def _write_marker(path: str, data: dict | None) -> None:
    try:
        if data is None:
            os.unlink(path)
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:  # profile names only, no secrets
            json.dump(data, f)
    except OSError:
        pass


class Changer:
    def __init__(self, run=run_command, sleep=asyncio.sleep, marker: str = MARKER, clock=time.time,
                 cmd_timeout: float = CMD_TIMEOUT_S, deadline_s: float = DEADLINE_S,
                 rollback_wait_s: int = ROLLBACK_WAIT_S) -> None:
        self._run, self._sleep, self.marker, self._clock = run, sleep, marker, clock
        self.cmd_timeout, self.deadline_s, self.rollback_wait_s = cmd_timeout, deadline_s, rollback_wait_s

    async def _call(self, argv: list[str], stdin: str | None = None) -> tuple[int, str]:
        """One bounded command; a hang or crash is just a failure."""
        try:
            if stdin is None:
                return await asyncio.wait_for(self._run(argv), self.cmd_timeout)
            return await asyncio.wait_for(self._run(argv, stdin), self.cmd_timeout)
        except (asyncio.TimeoutError, OSError):
            return 1, ""

    async def _gateway_answers(self, wait_s: int, server_host: str | None) -> bool:
        for _ in range(max(1, wait_s // PING_INTERVAL_S)):
            code, out = await self._call(["nmcli", "-g", "IP4.GATEWAY", "device", "show", IFACE])
            gateway = out.strip().splitlines()[0] if code == 0 and out.strip() else ""
            if gateway:
                code, _ = await self._call(["ping", "-c", "1", "-W", "2", gateway])
                if code == 0:
                    if not server_host:
                        return True
                    code, _ = await self._call(["ping", "-c", "1", "-W", "2", server_host])
                    if code == 0:
                        return True
            await self._sleep(PING_INTERVAL_S)
        return False

    async def _attempt(self, name: str, ssid: str, password: str, server_host, wait_s: int) -> bool:
        payload = json.dumps({"name": name, "ssid": ssid, "password": password})
        code, _ = await self._call([*HELPER, "apply"], payload)
        return code == 0 and await self._gateway_answers(wait_s, server_host)

    async def rollback(self, new: str, previous: str | None) -> dict:
        """Reactivate the previous profile (verifying the gateway) and drop only the new attempt."""
        if previous:
            await self._call([*HELPER, "up", previous])
            await self._gateway_answers(self.rollback_wait_s, None)
        if new and new != previous and _NAME.fullmatch(new):
            await self._call([*HELPER, "delete", new])
        _write_marker(self.marker, None)
        return {"ok": False, "error": "rollback"}

    async def set_wifi(self, ssid: str, password: str, server_host: str | None = None,
                       wait_s: int = 40) -> dict:
        if not valid_credentials(ssid, password):
            return {"ok": False, "error": "invalid"}
        code, out = await self._call(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"])
        previous = active_wifi_profile(out) if code == 0 else None
        name = f"friday-wifi-{int(self._clock())}"
        _write_marker(self.marker, {"new": name, "previous": previous})
        try:
            ok = await asyncio.wait_for(self._attempt(name, ssid, password, server_host, wait_s), self.deadline_s)
        except (asyncio.TimeoutError, OSError):
            ok = False
        if ok:
            _write_marker(self.marker, None)
            return {"ok": True}
        LOG.warning("wifi change failed, rolling back")
        return await self.rollback(name, previous)

    async def recover(self) -> bool:
        """Agent restart with an unfinished attempt: roll it back. Returns True if there was one."""
        try:
            with open(self.marker, encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            return False
        await self.rollback(str(m.get("new") or ""), m.get("previous"))
        return True
