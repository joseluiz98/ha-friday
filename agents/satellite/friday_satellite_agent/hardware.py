"""Hardware and network readings of the satellite (Raspberry Pi), sampled every 30 s.

Everything that touches the system goes through injectable readers so the parsing is testable.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from datetime import datetime, timedelta, timezone

from .wifi import _split, nmcli_available

SAMPLE_S = 30
THERMAL = "/sys/class/thermal/thermal_zone0/temp"
UPTIME = "/proc/uptime"
WIRELESS = "/proc/net/wireless"
IFACE_TYPES = {"ethernet": "ethernet", "wifi": "wifi"}


def parse_cpu_temperature(text: str) -> float | None:
    try:
        return round(int(text.strip()) / 1000, 1)
    except ValueError:
        return None


def parse_throttled(text: str) -> bool | None:
    """`throttled=0x50005` -> True; `throttled=0x0` -> False (problem = any flag set)."""
    m = re.search(r"0x([0-9a-fA-F]+)", text)
    return None if m is None else int(m.group(1), 16) != 0


def parse_boot_time(uptime_text: str, now: datetime) -> str | None:
    """ISO boot time, rounded to the minute so the state does not change on every sample."""
    try:
        up = float(uptime_text.split()[0])
    except (ValueError, IndexError):
        return None
    boot = now - timedelta(seconds=up)
    boot = (boot + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return boot.isoformat()


def parse_signal_dbm(text: str, iface: str) -> int | None:
    """Level column of /proc/net/wireless (dBm)."""
    for line in text.splitlines():
        if line.strip().startswith(iface + ":"):
            cols = line.split(":", 1)[1].split()
            try:
                return int(float(cols[2]))
            except (IndexError, ValueError):
                return None
    return None


def parse_devices(text: str) -> list[tuple[str, str, str]]:
    """`nmcli -t -f DEVICE,TYPE,STATE device` -> [(device, type, state)]."""
    out = []
    for line in text.splitlines():
        p = _split(line)
        if len(p) >= 3:
            out.append((p[0], p[1], p[2]))
    return out


def main_interface(devices: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    """Connected ethernet wins over Wi-Fi (the cable is the route in use)."""
    for want in ("ethernet", "wifi"):
        for dev, kind, state in devices:
            if kind == want and state == "connected":
                return dev, IFACE_TYPES[want]
    return None


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _run(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False).stdout


def sample(read=_read_file, run=_run, now=lambda: datetime.now(timezone.utc), with_network=True) -> dict:
    """One reading. A missing source simply leaves its key out (the entity stays at its last value)."""
    out: dict = {}

    def attempt(key, fn):
        try:
            value = fn()
        except Exception:  # noqa: BLE001 - one broken source never blocks the others
            return
        if value is not None:
            out[key] = value

    attempt("cpu_temperature", lambda: parse_cpu_temperature(read(THERMAL)))
    attempt("throttled", lambda: parse_throttled(run(["vcgencmd", "get_throttled"])))
    attempt("uptime_since", lambda: parse_boot_time(read(UPTIME), now()))
    if not with_network:
        return out
    found = main_interface(parse_devices(run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])))
    if found is None:
        return out
    dev, kind = found
    out["network_interface"] = kind
    attempt("ip_address", lambda: run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", dev]).split("/")[0].strip() or None)
    attempt("mac_address", lambda: run(["nmcli", "-g", "GENERAL.HWADDR", "device", "show", dev])
            .strip().replace("\\:", ":") or None)
    if kind == "wifi":
        def ssid():
            for line in run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"]).splitlines():
                p = _split(line)
                if len(p) >= 2 and p[0] == "yes":
                    return p[1]
        attempt("wifi_ssid", ssid)
        attempt("wifi_signal", lambda: parse_signal_dbm(read(WIRELESS), dev))
    return out


async def sample_async(**kw) -> dict:
    return await asyncio.to_thread(sample, **kw)


def descriptors(network: bool | None = None) -> list[dict]:
    """Sensor descriptors; the network ones only when nmcli exists."""
    network = nmcli_available() if network is None else network
    d = [
        {"key": "cpu_temperature", "platform": "sensor", "name": "CPU temperature", "unit": "°C",
         "device_class": "temperature", "state_class": "measurement"},
        {"key": "throttled", "platform": "binary_sensor", "name": "Throttled", "device_class": "problem"},
        {"key": "uptime_since", "platform": "sensor", "name": "Up since", "device_class": "timestamp"},
    ]
    if network:
        d += [
            {"key": "network_interface", "platform": "sensor", "name": "Network interface",
             "device_class": "enum", "options": ["ethernet", "wifi"]},
            {"key": "ip_address", "platform": "sensor", "name": "IP address"},
            {"key": "mac_address", "platform": "sensor", "name": "MAC address", "category": "diagnostic"},
            {"key": "wifi_ssid", "platform": "sensor", "name": "Wi-Fi network"},
            {"key": "wifi_signal", "platform": "sensor", "name": "Wi-Fi signal", "unit": "dBm",
             "device_class": "signal_strength", "state_class": "measurement"},
            {"key": "wifi_set", "platform": "button", "name": "Change Wi-Fi", "hidden": True,
             "available": False},
        ]
    return d
