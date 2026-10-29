"""Pure collectors for the F.R.I.D.A.Y. Server agent (no I/O)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .voice_switch import DESCRIPTOR

VERSION = "0.1.0"


def vm_metrics(meminfo: str, loadavg: str, statvfs: tuple, uptime: str, ncpu: int) -> dict:
    """statvfs = (f_frsize, f_blocks, f_bfree, f_bavail); percentage computed like df."""
    mem = {}
    for line in meminfo.splitlines():
        m = re.match(r"(MemTotal|MemAvailable):\s+(\d+)\s*kB", line)
        if m:
            mem[m.group(1)] = int(m.group(2))
    total = mem.get("MemTotal", 0)
    mem_pct = round(100 * (total - mem.get("MemAvailable", 0)) / total, 1) if total else None
    cpu = round(100 * float(loadavg.split()[0]) / max(ncpu, 1), 1)
    _, blocks, bfree, bavail = statvfs
    used = blocks - bfree
    disk = round(100 * used / (used + bavail), 1) if used + bavail else None
    since = datetime.now(timezone.utc) - timedelta(seconds=float(uptime.split()[0]))
    since = since.replace(second=0, microsecond=0)
    return {"vm_memory_pct": mem_pct, "vm_cpu_load_pct": cpu, "vm_disk_pct": disk,
            "vm_uptime_since": since.isoformat()}


def conversation_open(log_text: str) -> bool:
    """Copied from /opt/friday/status/friday_status.py (conversation_open): open only if the
    last connect/disconnect line is a connect."""
    aberta = False
    for linha in log_text.splitlines():
        if "cliente desconectado" in linha:
            aberta = False
        elif "cliente conectado" in linha:
            aberta = True
    return aberta


def status_metrics(status: dict) -> dict:
    """status = JSON of GET :8800/v1/status plus `conversation_open` added by the agent."""
    lucid = status.get("lucid") or {}
    views = status.get("memory_views") or {}
    return {
        "brain_ready": bool(status.get("ready")) if "ready" in status else None,
        "brain_last_dream": status.get("last_dream"),
        "brain_restarted_in_process": status.get("restarted_in_process"),
        "brain_version": status.get("openclaw_version"),
        "lucid_last_run": lucid.get("finished_at"),
        "memory_views_mode": views.get("mode"),
        "conversation_open": status.get("conversation_open"),
    }


def status_changes(status: dict) -> dict:
    """Like status_metrics, but omits conversation_open when the agent could not compute it
    (transient docker logs failure), so a valid value is not overwritten by None."""
    out = status_metrics(status)
    if "conversation_open" not in status:
        out.pop("conversation_open")
    return out


class Source:
    """Tracks consecutive failures of one sample source; after `limit` failures its keys go to None."""

    def __init__(self, keys, limit: int = 3) -> None:
        self.keys = list(keys)
        self.limit = limit
        self.failures = 0

    def ok(self, changes: dict) -> dict:
        self.failures = 0
        return changes

    def failed(self) -> dict:
        self.failures += 1
        return {k: None for k in self.keys} if self.failures == self.limit else {}


def proxmox_metrics(node_status: dict, smart: dict, last_vzdump: dict) -> dict:
    mem = (node_status.get("data") or {}).get("memory") or {}
    avail = mem.get("available", mem.get("free"))
    mb = round(avail / 1048576) if isinstance(avail, (int, float)) else None
    found = re.findall(r"Temperature:\s+(\d+)", (smart.get("data") or {}).get("text") or "")
    data = last_vzdump.get("data") or []
    end = data[0].get("endtime") if data else None
    backup = datetime.fromtimestamp(end, timezone.utc).isoformat() if end else None
    return {"host_available_mb": mb, "nvme_temperature": int(found[0]) if found else None,
            "last_backup": backup}


VOICE_DESCRIPTOR = DESCRIPTOR


def capabilities(proxmox: bool) -> list[dict]:
    def s(key, name, **kw):
        return {"key": key, "platform": kw.pop("platform", "sensor"), "name": name,
                "available": kw.pop("available", True), **kw}

    diag = {"category": "diagnostic"}
    m = {"state_class": "measurement"}
    return [
        s("vm_memory_pct", "VM memory", unit="%", icon="mdi:memory", **m),
        s("vm_cpu_load_pct", "VM CPU load", unit="%", icon="mdi:cpu-64-bit", **m),
        s("vm_disk_pct", "VM disk", unit="%", icon="mdi:harddisk", **m),
        s("vm_uptime_since", "VM up since", device_class="timestamp", **diag),
        s("brain_ready", "Brain ready", platform="binary_sensor", device_class="connectivity"),
        s("brain_last_dream", "Brain last dream", device_class="timestamp"),
        s("brain_restarted_in_process", "Brain restarted in process", platform="binary_sensor", **diag),
        s("brain_version", "Brain version", **diag),
        s("lucid_last_run", "Lucid Dreaming last run", device_class="timestamp"),
        s("memory_views_mode", "Memory views mode", **diag),
        s("conversation_open", "Conversation open", platform="binary_sensor"),
        s("host_available_mb", "Proxmox host available memory", unit="MB", available=proxmox, **m),
        s("nvme_temperature", "Proxmox NVMe temperature", unit="°C", device_class="temperature",
          available=proxmox, **m),
        s("last_backup", "Proxmox last backup", device_class="timestamp", available=proxmox),
        {**VOICE_DESCRIPTOR, "available": True},
    ]
