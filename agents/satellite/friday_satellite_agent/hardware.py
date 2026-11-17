"""Hardware and network readings of the satellite (Raspberry Pi), sampled every 30 s.

Everything that touches the system goes through injectable readers so the parsing is testable.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from .wifi import _split, nmcli_available

SAMPLE_S = 30
THERMAL = "/sys/class/thermal/thermal_zone0/temp"
UPTIME = "/proc/uptime"
LOADAVG = "/proc/loadavg"
WIRELESS = "/proc/net/wireless"
MEMINFO = "/proc/meminfo"
IFACE_TYPES = {"ethernet": "ethernet", "wifi": "wifi"}


def parse_cpu_temperature(text: str) -> float | None:
    try:
        return round(int(text.strip()) / 1000, 1)
    except ValueError:
        return None


def parse_cpu_load(text: str, ncpu: int) -> float | None:
    """1-min load average as % of the cores: the same formula as the server's vm_cpu_load_pct."""
    try:
        return round(100 * float(text.split()[0]) / max(ncpu, 1), 1)
    except (ValueError, IndexError):
        return None


def parse_throttled(text: str) -> bool | None:
    """`throttled=0x50005` -> True; `throttled=0x0` -> False (problem = any flag set)."""
    m = re.search(r"0x([0-9a-fA-F]+)", text)
    return None if m is None else int(m.group(1), 16) != 0


def parse_throttle_flags(text: str) -> dict:
    """The bits the old status page showed (vcgencmd get_throttled): 0 undervoltage now, 16 undervoltage since boot,
    1-3 clock limited / throttled / soft temperature limit now (at ~85 °C bit 1 is the heat, not the power supply)."""
    m = re.search(r"0x([0-9a-fA-F]+)", text)
    if m is None:
        return {}
    v = int(m.group(1), 16)
    return {"undervoltage_now": bool(v & 0x1), "undervoltage_since_boot": bool(v & 0x10000),
            "heat_limited": bool(v & 0xE)}


def parse_clock_mhz(text: str) -> int | None:
    """`frequency(48)=1200000000` -> 1200."""
    m = re.search(r"=(\d+)", text)
    return None if m is None else int(m.group(1)) // 1_000_000


def parse_meminfo_mb(text: str, field: str) -> int | None:
    for line in text.splitlines():
        if line.startswith(field + ":"):
            try:
                return int(line.split()[1]) // 1024
            except (IndexError, ValueError):
                return None
    return None


def disk_usage_pct(st) -> int | None:
    """Used % of the root filesystem, like `df` (blocks free to non-root users count as free)."""
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    return None if used + avail <= 0 else round(100 * used / (used + avail))


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
                level = int(float(cols[2]))
            except (IndexError, ValueError):
                return None
            return level if -100 <= level <= -20 else None  # the Pi 3 driver sometimes reports nonsense (-11)
    return None


def dbm_from_percent(percent) -> int | None:
    """Rough dBm from nmcli's signal quality (the usual linear mapping)."""
    try:
        return round(int(percent) / 2 - 100)
    except (TypeError, ValueError):
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


def sample(read=_read_file, run=_run, now=lambda: datetime.now(timezone.utc), with_network=True,
           cores=os.cpu_count, statvfs=lambda: os.statvfs("/")) -> dict:
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
    attempt("cpu_load", lambda: parse_cpu_load(read(LOADAVG), cores() or 1))
    try:
        throttled = run(["vcgencmd", "get_throttled"])  # one call feeds the summary and the flags
    except Exception:  # noqa: BLE001
        throttled = ""
    attempt("throttled", lambda: parse_throttled(throttled))
    out.update(parse_throttle_flags(throttled))
    attempt("uptime_since", lambda: parse_boot_time(read(UPTIME), now()))
    attempt("cpu_clock", lambda: parse_clock_mhz(run(["vcgencmd", "measure_clock", "arm"])))
    attempt("memory_available", lambda: parse_meminfo_mb(read(MEMINFO), "MemAvailable"))
    attempt("memory_total", lambda: parse_meminfo_mb(read(MEMINFO), "MemTotal"))
    attempt("sd_usage", lambda: disk_usage_pct(statvfs()))
    if not with_network:
        return out
    devices = parse_devices(run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]))
    found = main_interface(devices)
    if found is None:
        return out
    dev, kind = found
    wlan = next((d for d, t, st in devices if t == "wifi" and st == "connected"), None)
    if wlan is not None:  # the doorbell's fallback when the cable does not answer (spec 2026-10-12 D9)
        attempt("wifi_ip_address",
                lambda: run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", wlan]).split("/")[0].strip() or None)
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
        def signal():
            dbm = parse_signal_dbm(read(WIRELESS), dev)
            if dbm is not None:
                return dbm
            for line in run(["nmcli", "-t", "-f", "ACTIVE,SIGNAL", "device", "wifi"]).splitlines():
                p = _split(line)
                if len(p) >= 2 and p[0] == "yes":
                    return dbm_from_percent(p[1])
        attempt("wifi_signal", signal)
    return out


async def sample_async(**kw) -> dict:
    return await asyncio.to_thread(sample, **kw)


def descriptors(network: bool | None = None) -> list[dict]:
    """Sensor descriptors; the network ones only when nmcli exists."""
    network = nmcli_available() if network is None else network
    d = [
        {"key": "cpu_temperature", "platform": "sensor", "name": "CPU temperature", "unit": "°C",
         "device_class": "temperature", "state_class": "measurement"},
        {"key": "cpu_load", "platform": "sensor", "name": "CPU load", "unit": "%", "icon": "mdi:cpu-64-bit",
         "state_class": "measurement"},
        {"key": "throttled", "platform": "binary_sensor", "name": "Throttled", "device_class": "problem"},
        {"key": "uptime_since", "platform": "sensor", "name": "Up since", "device_class": "timestamp"},
        # from the old status page (satelite-status.sh, retired 16/11)
        {"key": "undervoltage_now", "platform": "binary_sensor", "name": "Undervoltage", "device_class": "problem",
         "icon": "mdi:flash-alert"},
        {"key": "undervoltage_since_boot", "platform": "binary_sensor", "name": "Undervoltage since boot",
         "device_class": "problem", "icon": "mdi:flash-alert-outline", "category": "diagnostic"},
        {"key": "heat_limited", "platform": "binary_sensor", "name": "Limited by heat", "device_class": "problem",
         "icon": "mdi:thermometer-alert"},
        {"key": "cpu_clock", "platform": "sensor", "name": "CPU clock", "unit": "MHz", "device_class": "frequency",
         "state_class": "measurement", "category": "diagnostic"},
        {"key": "memory_available", "platform": "sensor", "name": "Memory available", "unit": "MB",
         "device_class": "data_size", "state_class": "measurement", "icon": "mdi:memory"},
        {"key": "memory_total", "platform": "sensor", "name": "Memory total", "unit": "MB",
         "device_class": "data_size", "category": "diagnostic", "enabled_default": False},
        {"key": "sd_usage", "platform": "sensor", "name": "SD card usage", "unit": "%", "icon": "mdi:sd",
         "state_class": "measurement"},
    ]
    if network:
        d += [
            {"key": "network_interface", "platform": "sensor", "name": "Network interface",
             "device_class": "enum", "options": ["ethernet", "wifi"]},
            {"key": "ip_address", "platform": "sensor", "name": "IP address"},
            {"key": "mac_address", "platform": "sensor", "name": "MAC address", "category": "diagnostic"},
            {"key": "wifi_ip_address", "platform": "sensor", "name": "Wi-Fi IP address", "category": "diagnostic"},
            {"key": "wifi_ssid", "platform": "sensor", "name": "Wi-Fi network", "category": "diagnostic",
             "enabled_default": False},
            {"key": "wifi_signal", "platform": "sensor", "name": "Wi-Fi signal", "unit": "dBm",
             "device_class": "signal_strength", "state_class": "measurement"},
            {"key": "wifi_change", "platform": "sensor", "name": "Wi-Fi change", "device_class": "enum",
             "options": ["idle", "running", "ok", "rolled_back"], "category": "diagnostic"},
            {"key": "wifi_set", "platform": "button", "name": "Change Wi-Fi", "hidden": True,
             "available": False},
        ]
    return d
