"""Hardware and network readings of the satellite (Raspberry Pi), sampled every 30 s.

CPU Usage and Memory Usage follow the Windows Task Manager: percent of time busy (from /proc/stat deltas, see
friday_agent_core.cpu) and percent of memory in use; the load average only survives as an attribute of CPU Usage.

Everything that touches the system goes through injectable readers so the parsing is testable.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from friday_agent_core.cpu import CpuUsage, parse_loadavg

from .wifi import _split, nmcli_available

SAMPLE_S = 30
THERMAL = "/sys/class/thermal/thermal_zone0/temp"
UPTIME = "/proc/uptime"
LOADAVG = "/proc/loadavg"
STAT = "/proc/stat"
WIRELESS = "/proc/net/wireless"
MEMINFO = "/proc/meminfo"
BOOT_ID = "/proc/sys/kernel/random/boot_id"
UNDERVOLTAGE_STATE = os.path.expanduser("~/.friday-agent/undervoltage.json")
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


def _meminfo_kb(text: str, field: str) -> int | None:
    for line in text.splitlines():
        if line.startswith(field + ":"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def parse_meminfo_mb(text: str, field: str) -> int | None:
    kb = _meminfo_kb(text, field)
    return None if kb is None else kb // 1024


def parse_memory_usage(meminfo: str) -> tuple[float, dict] | None:
    """Memory in use like the Task Manager: (MemTotal - MemAvailable) / MemTotal in %, plus the MB as attributes."""
    total = _meminfo_kb(meminfo, "MemTotal")
    available = _meminfo_kb(meminfo, "MemAvailable")
    if not total or available is None:
        return None
    return round(100 * (total - available) / total, 1), {"available_mb": available // 1024, "total_mb": total // 1024}


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


def parse_journal(text: str) -> tuple[int, str | None]:
    """(lines saying `Undervoltage detected`, cursor of the last `-- cursor: <c>` line or None)."""
    count, cursor = 0, None
    for line in text.splitlines():
        if line.startswith("-- cursor: "):
            cursor = line[len("-- cursor: "):].strip() or None
        elif "Undervoltage detected" in line:
            count += 1
    return count, cursor


class UndervoltageCounter:
    """Kernel `Undervoltage detected` messages since boot, read incrementally from the journal.

    State {boot_id, cursor, count} survives an agent restart (every OTA restarts it): the count resumes from the
    saved cursor instead of re-reading the whole boot, since a smaller recount (journald rotated) would look like a
    reset to the total_increasing statistics. A new boot starts again from zero. No permission to read the kernel
    log gives empty output with no cursor: the value is then None (unknown), never 0.
    """

    BASE = ["journalctl", "-k", "-b", "-o", "cat", "--no-pager", "--show-cursor"]

    def __init__(self, run=_run, read=_read_file, path: str = UNDERVOLTAGE_STATE) -> None:
        self._run, self._read, self._path = run, read, path

    def _load(self, boot_id: str) -> tuple[str | None, int]:
        try:
            with open(self._path, encoding="utf-8") as f:
                st = json.load(f)
            if st["boot_id"] == boot_id and isinstance(st["cursor"], str) and isinstance(st["count"], int):
                return st["cursor"], st["count"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None, 0

    def _save(self, boot_id: str, cursor: str, count: int) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"boot_id": boot_id, "cursor": cursor, "count": count}, f)
            os.replace(tmp, self._path)
        except OSError:
            pass  # the next sample resumes from the older cursor: the same total, just read again

    def sample(self) -> int | None:
        try:
            boot_id = self._read(BOOT_ID).strip()
            cursor, count = self._load(boot_id)
            argv = self.BASE + (["--after-cursor", cursor] if cursor else [])
            found, new_cursor = parse_journal(self._run(argv))
        except Exception:  # noqa: BLE001 - journalctl missing, timeout, unreadable boot id
            return None
        if new_cursor is None:
            return count if cursor else None  # nothing new since the saved cursor / kernel log unreadable
        count += found
        self._save(boot_id, new_cursor, count)
        return count


def sample(read=_read_file, run=_run, now=lambda: datetime.now(timezone.utc), with_network=True,
           statvfs=lambda: os.statvfs("/"), *, cpu: CpuUsage | None = None,
           undervoltage: UndervoltageCounter | None = None) -> dict:
    """One reading. A missing source simply leaves its key out (the entity stays at its last value).

    `cpu` keeps the previous /proc/stat sample between calls; without it (or on the first call) there is no
    `cpu_usage` key, so the entity is never fed a made-up 0.
    """
    out: dict = {}

    def attempt(key, fn):
        try:
            value = fn()
        except Exception:  # noqa: BLE001 - one broken source never blocks the others
            return
        if value is not None:
            out[key] = value

    attempt("cpu_temperature", lambda: parse_cpu_temperature(read(THERMAL)))
    if cpu is not None:
        try:
            usage = cpu.update(read(STAT))
            load = parse_loadavg(read(LOADAVG)) if usage is not None else None
        except Exception:  # noqa: BLE001
            usage = load = None
        if usage is not None:
            out["cpu_usage"] = usage
            if load is not None:
                out["cpu_usage_attributes"] = load
    try:
        throttled = run(["vcgencmd", "get_throttled"])  # one call feeds the summary and the flags
    except Exception:  # noqa: BLE001
        throttled = ""
    attempt("throttled", lambda: parse_throttled(throttled))
    out.update(parse_throttle_flags(throttled))
    attempt("uptime_since", lambda: parse_boot_time(read(UPTIME), now()))
    attempt("cpu_clock", lambda: parse_clock_mhz(run(["vcgencmd", "measure_clock", "arm"])))
    try:
        memory = parse_memory_usage(read(MEMINFO))
    except Exception:  # noqa: BLE001
        memory = None
    if memory is not None:
        out["memory_usage"], out["memory_usage_attributes"] = memory
    attempt("sd_usage", lambda: disk_usage_pct(statvfs()))
    if undervoltage is not None:
        attempt("undervoltage_events", undervoltage.sample)
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
         "device_class": "temperature", "state_class": "measurement", "category": "diagnostic"},
        {"key": "cpu_usage", "platform": "sensor", "name": "CPU Usage", "unit": "%", "icon": "mdi:cpu-64-bit",
         "state_class": "measurement", "category": "diagnostic"},
        {"key": "throttled", "platform": "binary_sensor", "name": "Throttled", "device_class": "problem",
         "category": "diagnostic"},
        {"key": "uptime_since", "platform": "sensor", "name": "Up since", "device_class": "timestamp",
         "category": "diagnostic"},
        # from the old status page (satelite-status.sh, retired 16/11)
        {"key": "undervoltage_now", "platform": "binary_sensor", "name": "Undervoltage", "device_class": "problem",
         "icon": "mdi:flash-alert", "category": "diagnostic"},
        {"key": "undervoltage_since_boot", "platform": "binary_sensor", "name": "Undervoltage since boot",
         "device_class": "problem", "icon": "mdi:flash-alert-outline", "category": "diagnostic"},
        {"key": "heat_limited", "platform": "binary_sensor", "name": "Limited by heat", "device_class": "problem",
         "icon": "mdi:thermometer-alert", "category": "diagnostic"},
        {"key": "cpu_clock", "platform": "sensor", "name": "CPU clock", "unit": "MHz", "device_class": "frequency",
         "state_class": "measurement", "category": "diagnostic"},
        {"key": "memory_usage", "platform": "sensor", "name": "Memory Usage", "unit": "%", "icon": "mdi:memory",
         "state_class": "measurement", "category": "diagnostic"},
        {"key": "sd_usage", "platform": "sensor", "name": "SD card usage", "unit": "%", "icon": "mdi:sd",
         "state_class": "measurement", "category": "diagnostic"},
        {"key": "undervoltage_events", "platform": "sensor", "name": "Undervoltage events",
         "state_class": "total_increasing", "icon": "mdi:flash-alert", "category": "diagnostic"},
    ]
    if network:
        d += [
            {"key": "network_interface", "platform": "sensor", "name": "Network interface",
             "device_class": "enum", "options": ["ethernet", "wifi"], "category": "diagnostic"},
            {"key": "ip_address", "platform": "sensor", "name": "IP address", "category": "diagnostic"},
            {"key": "mac_address", "platform": "sensor", "name": "MAC address", "category": "diagnostic"},
            {"key": "wifi_ip_address", "platform": "sensor", "name": "Wi-Fi IP address", "category": "diagnostic"},
            {"key": "wifi_ssid", "platform": "sensor", "name": "Wi-Fi network", "category": "diagnostic",
             "enabled_default": False},
            {"key": "wifi_signal", "platform": "sensor", "name": "Wi-Fi signal", "unit": "dBm",
             "device_class": "signal_strength", "state_class": "measurement", "category": "diagnostic"},
            {"key": "wifi_change", "platform": "sensor", "name": "Wi-Fi change", "device_class": "enum",
             "options": ["idle", "running", "ok", "rolled_back"], "category": "diagnostic"},
            {"key": "wifi_set", "platform": "button", "name": "Change Wi-Fi", "hidden": True,
             "available": False},
        ]
    return d
