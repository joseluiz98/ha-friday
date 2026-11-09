"""Pure collectors for the F.R.I.D.A.Y. Server agent (no I/O)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import cost, reminders
from .commands import RESTART_DESCRIPTOR
from .voice_switch import DESCRIPTOR
from friday_agent_core import version as _version

VERSION = _version.installed_version(_version.FILE)


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


LUCID_ATTRS = ("date", "status", "cost_usd", "turns_in", "facts_added", "facts_corrected",
               "directives_changed", "discarded")


ACTIVITY_STALE_S = 90


def activity_view(activity: dict | None, now: float) -> dict | None:
    """activity = the bot's /srv/friday/state/activity.json (sessions open, brain turns in flight,
    heartbeat every 20 s). None when absent/unreadable (caller falls back to the log rule).
    A stale heartbeat means the bot is not running: nothing is open."""
    if not isinstance(activity, dict):
        return None
    try:
        age = now - float(activity["updated_at"])
        sessions = len(activity.get("sessions") or [])
        brain = int(activity.get("brain_turns") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if age > ACTIVITY_STALE_S:
        return {"conversation_open": False, "sessions": 0, "brain_turns": 0}
    return {"conversation_open": sessions > 0, "sessions": sessions, "brain_turns": brain}


def is_busy(activity: dict | None, now: float, log_tail) -> bool:
    """Restart guard: busy with an open session OR a brain turn in flight. Without a readable
    activity file, the old log rule; `log_tail()` returning None raises OSError (fail closed)."""
    view = activity_view(activity, now)
    if view is not None:
        return view["conversation_open"] or view["brain_turns"] > 0
    text = log_tail()
    if text is None:
        raise OSError("no log")
    return conversation_open(text)


def status_metrics(status: dict) -> dict:
    """status = JSON of GET :8800/v1/status plus `conversation_open` added by the agent."""
    lucid = status.get("lucid") or {}
    views = status.get("memory_views") or {}
    lucid_attrs = {k: lucid[k] for k in LUCID_ATTRS if k in lucid} or None
    return {
        "brain_ready": bool(status.get("ready")) if "ready" in status else None,
        "brain_last_dream": status.get("last_dream"),
        "brain_restarted_in_process": status.get("restarted_in_process"),
        "brain_version": status.get("openclaw_version"),
        "lucid_last_run": lucid.get("finished_at"),
        "lucid_last_run_attributes": lucid_attrs,
        "memory_views_mode": views.get("mode"),
        "conversation_open": status.get("conversation_open"),
        "conversation_open_attributes": status.get("conversation_open_attributes"),
    }


def status_changes(status: dict) -> dict:
    """Like status_metrics, but omits conversation_open when the agent could not compute it
    (transient docker logs failure), so a valid value is not overwritten by None."""
    out = status_metrics(status)
    if "conversation_open" not in status:
        out.pop("conversation_open")
        out.pop("conversation_open_attributes")
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
    status = data[0].get("status") if data else None
    return {"host_available_mb": mb, "nvme_temperature": int(found[0]) if found else None,
            "last_backup": backup, "last_backup_attributes": {"status": status} if status else None}


def memory_index_metrics(stamp_mtime: float | None) -> dict:
    """stamp_mtime = mtime of the file the memory-index unit touches after a successful run (ExecStartPost)."""
    when = (datetime.fromtimestamp(int(stamp_mtime), timezone.utc).isoformat()
            if stamp_mtime is not None else None)
    return {"memory_index_last_success": when}


def stamp_iso(mtime: float | None) -> str | None:
    return datetime.fromtimestamp(int(mtime), timezone.utc).isoformat() if mtime is not None else None


def wakeword_pipeline_metrics(sync_mtime: float | None, cache_mtime: float | None) -> dict:
    """09/11/2026: nightly wake-word pipeline. sync = mark the Pi writes only after a fully OK upload
    (incoming/<sat>/.sync_ok); cache = stamp featcache_nightly.sh touches only after caching every pending day."""
    return {"wakeword_sync_last_ok": stamp_iso(sync_mtime), "wakeword_cache_last_ok": stamp_iso(cache_mtime)}


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
        s("memory_index_last_success", "Memory index last success", device_class="timestamp", **diag),
        s("wakeword_sync_last_ok", "Wake word recordings sync last OK", device_class="timestamp", **diag),
        s("wakeword_cache_last_ok", "Wake word feature cache last OK", device_class="timestamp", **diag),
        s("conversation_open", "Conversation open", platform="binary_sensor"),
        s("host_available_mb", "Proxmox host available memory", unit="MB", available=proxmox, **m),
        s("nvme_temperature", "Proxmox NVMe temperature", unit="°C", device_class="temperature",
          available=proxmox, **m),
        s("last_backup", "Proxmox last backup", device_class="timestamp", available=proxmox),
        *cost.descriptors(),
        cost.BUDGET_DESCRIPTOR,
        {**VOICE_DESCRIPTOR, "available": True},
        {**RESTART_DESCRIPTOR, "available": True},
        reminders.DESCRIPTOR,
    ]
