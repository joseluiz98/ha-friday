"""Wi-Fi change with automatic rollback. Commands go through an injectable runner.

The password only ever lives in the argv of the one `nmcli ... add` call. It is never logged, never part of
a result, and error paths report short machine-readable codes only.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

LOG = logging.getLogger("friday_satellite_agent")

NMCLI = ["sudo", "-n", "/usr/bin/nmcli"]
IFACE = "wlan0"
NEW_PROFILE = "friday-wifi-new"
PING_INTERVAL_S = 2


async def run_command(argv: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


def nmcli_available() -> bool:
    return shutil.which("nmcli") is not None


def _unescape(field: str) -> str:
    return field.replace("\\:", ":").replace("\\\\", "\\")


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
    """Name of the active Wi-Fi connection from `nmcli -t -f NAME,TYPE connection show --active`."""
    for line in output.splitlines():
        parts = _split(line)
        if len(parts) >= 2 and parts[1] == "802-11-wireless":
            return parts[0]
    return None


def valid_credentials(ssid, password) -> bool:
    return (isinstance(ssid, str) and isinstance(password, str)
            and 1 <= len(ssid.encode("utf-8")) <= 32 and 8 <= len(password) <= 63)


async def set_wifi(ssid: str, password: str, run=run_command, wait_s: int = 60, sleep=asyncio.sleep) -> dict:
    """Create and activate a new Wi-Fi profile; back to the previous one if the gateway does not answer."""
    if not valid_credentials(ssid, password):
        return {"ok": False, "error": "invalid"}

    async def nm(*args: str) -> tuple[int, str]:
        return await run([*NMCLI, *args])

    code, out = await nm("-t", "-f", "NAME,TYPE", "connection", "show", "--active")
    previous = active_wifi_profile(out) if code == 0 else None

    async def rollback() -> dict:
        if previous:
            await nm("connection", "up", previous)
        await nm("connection", "delete", NEW_PROFILE)
        return {"ok": False, "error": "rollback"}

    await nm("connection", "delete", NEW_PROFILE)  # a leftover from an interrupted attempt
    code, _ = await nm("connection", "add", "type", "wifi", "ifname", IFACE, "con-name", NEW_PROFILE,
                       "ssid", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
                       "connection.autoconnect-priority", "10")
    if code != 0:
        await nm("connection", "delete", NEW_PROFILE)
        return {"ok": False, "error": "rollback"}
    code, _ = await nm("connection", "up", NEW_PROFILE, "ifname", IFACE)
    if code != 0:
        return await rollback()
    if await _gateway_answers(nm, run, wait_s, sleep):
        return {"ok": True}
    LOG.warning("wifi change failed: gateway unreachable, rolling back")
    return await rollback()


async def _gateway_answers(nm, run, wait_s: int, sleep) -> bool:
    for _ in range(max(1, wait_s // PING_INTERVAL_S)):
        code, out = await nm("-g", "IP4.GATEWAY", "device", "show", IFACE)
        gateway = out.strip().splitlines()[0] if code == 0 and out.strip() else ""
        if gateway:
            code, _ = await run(["ping", "-c", "1", "-W", "2", gateway])
            if code == 0:
                return True
        await sleep(PING_INTERVAL_S)
    return False
