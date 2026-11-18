import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))

from friday_server_agent import collectors as c  # noqa: E402

MEMINFO = "MemTotal:       4000000 kB\nMemFree: 100 kB\nMemAvailable:   1000000 kB\n"


def test_vm_metrics_from_proc_samples():
    m = c.vm_metrics(MEMINFO, "2.00 1.0 0.5 1/100 42", (4096, 1000, 400, 400), "86400.5 100.0", 4, cpu_pct=12.5)
    assert m["vm_memory_pct"] == 75.0 and m["vm_memory_pct_attributes"] == {"available_mb": 976, "total_mb": 3906}
    assert m["vm_cpu_usage_pct"] == 12.5
    assert m["vm_cpu_usage_pct_attributes"] == {"load_1m": 2.0, "load_5m": 1.0, "load_15m": 0.5}
    assert "vm_cpu_load_pct" not in m
    # first sample after start: no CPU Usage yet, never a false 0
    assert c.vm_metrics(MEMINFO, "0 0 0", (1, 1, 1, 1), "1 1", 1)["vm_cpu_usage_pct"] is None
    assert m["vm_disk_pct"] == 60.0
    assert m["vm_uptime_since"].endswith("+00:00")


def test_status_metrics_maps_fields():
    s = {"ready": True, "last_dream": "2026-10-29T05:00:00Z", "restarted_in_process": False,
         "openclaw_version": "2026.9.9",
         "lucid": {"finished_at": "2026-10-29T03:31:00Z", "date": "2026-10-28", "status": "ok",
                   "cost_usd": 0.012, "facts_added": 2, "user_md_chars": 900},
         "memory_views": {"mode": "ab"}, "conversation_open": True,
         "memory_watcher": {"degraded": True, "since": "2026-11-17T20:31:32.857Z",
                            "reason": "memory watcher unavailable; x"}}
    m = c.status_metrics(s)
    assert m == {"brain_ready": True, "brain_last_dream": "2026-10-29T05:00:00Z",
                 "brain_restarted_in_process": False, "brain_version": "2026.9.9",
                 "lucid_last_run": "2026-10-29T03:31:00Z",
                 "lucid_last_run_attributes": {"date": "2026-10-28", "status": "ok", "cost_usd": 0.012,
                                               "facts_added": 2},
                 "brain_memory_watcher_degraded": True,
                 "brain_memory_watcher_degraded_attributes": {"since": "2026-11-17T20:31:32.857Z",
                                                              "reason": "memory watcher unavailable; x"},
                 "memory_views_mode": "ab", "conversation_open": True, "conversation_open_attributes": None}
    assert c.status_metrics({})["lucid_last_run"] is None
    assert c.status_metrics({})["lucid_last_run_attributes"] is None


def test_memory_watcher_unknown_without_data():
    for status in ({}, {"memory_watcher": None}):
        m = c.status_metrics(status)
        assert m["brain_memory_watcher_degraded"] is None
        assert m["brain_memory_watcher_degraded_attributes"] is None


def test_conversation_open_logic():
    assert c.conversation_open("cliente conectado\n") is True
    assert c.conversation_open("cliente conectado\ncliente desconectado\n") is False


def test_proxmox_metrics_maps_fields():
    node = {"data": {"memory": {"total": 8589934592, "available": 2097152000}}}
    smart = {"data": {"text": "x\nTemperature:     47 Celsius\n"}}
    vz = {"data": [{"endtime": 1761700000, "status": "OK"}]}
    m = c.proxmox_metrics(node, smart, vz)
    assert m["host_memory_usage_pct"] == 75.6
    assert m["host_memory_usage_pct_attributes"] == {"available_mb": 2000, "total_mb": 8192}
    assert c.proxmox_metrics({"data": {"memory": {"available": 1}}}, {}, {})["host_memory_usage_pct"] is None
    assert c.proxmox_metrics({"data": {"memory": {"total": 8589934592}}}, {}, {})["host_memory_usage_pct"] is None
    assert m["nvme_temperature"] == 47
    assert m["last_backup"].startswith("2025-10-29")
    assert m["last_backup_attributes"] == {"status": "OK"}
    empty = c.proxmox_metrics({}, {}, {})
    assert empty == {"host_memory_usage_pct": None, "host_memory_usage_pct_attributes": None, "nvme_temperature": None, "last_backup": None,
                     "last_backup_attributes": None, "backup_oldest_guest": None,
                     "backup_oldest_guest_attributes": None, "backup_storage_used_pct": None,
                     "backup_storage_used_pct_attributes": None}


def _run(end, *lines):
    return ({"endtime": end}, list(lines))


def test_backup_oldest_guest_ages_when_one_guest_fails():
    # 13/11/2026: the job ran today (LXC ok) but the VM's last good dump was two days before
    runs = [_run(1763017000, "INFO: Starting Backup of VM 101 (qemu)",
                 "ERROR: Backup of VM 101 failed - vma_queue_write: write error - Broken pipe",
                 "INFO: Finished Backup of VM 102 (00:00:13)"),
            _run(1762845000, "INFO: Finished Backup of VM 101 (00:04:35)", "INFO: Finished Backup of VM 102 (00:00:13)")]
    guests = {"data": [{"vmid": 101}, {"vmid": 102}, {"vmid": 900, "template": 1}]}
    m = c.backup_guest_metrics(runs, guests)
    assert m["backup_oldest_guest"] == "2025-11-11T07:10:00+00:00"
    assert m["backup_oldest_guest_attributes"] == {
        "guests": {"101": "2025-11-11T07:10:00+00:00", "102": "2025-11-13T06:56:40+00:00"}, "missing": []}


def test_backup_oldest_guest_unknown_when_a_guest_never_succeeded():
    runs = [_run(1763017000, "ERROR: Backup of VM 101 failed", "INFO: Finished Backup of VM 102 (00:00:13)")]
    m = c.backup_guest_metrics(runs, {"data": [{"vmid": 101}, {"vmid": 102}]})
    assert m["backup_oldest_guest"] is None
    assert m["backup_oldest_guest_attributes"]["missing"] == ["101"]


def test_backup_oldest_guest_falls_back_to_logged_ids_without_guest_list():
    m = c.backup_guest_metrics([_run(1763017000, "INFO: Finished Backup of VM 102 (00:00:13)")], {})
    assert m["backup_oldest_guest"] == "2025-11-13T06:56:40+00:00"
    assert c.backup_guest_metrics([], {}) == {"backup_oldest_guest": None, "backup_oldest_guest_attributes": None}


def test_backup_storage_used_pct():
    m = c.backup_storage_metrics({"data": {"total": 90_000_000_000, "used": 82_800_000_000,
                                           "avail": 2_700_000_000}})
    assert m == {"backup_storage_used_pct": 92.0, "backup_storage_used_pct_attributes": {"available_gb": 2.7}}
    assert c.backup_storage_metrics({"data": {"total": 0}})["backup_storage_used_pct"] is None


def test_wakeword_pipeline_stamps():
    assert c.wakeword_pipeline_metrics(1761700000.7, None) == {
        "wakeword_sync_last_ok": "2025-10-29T01:06:40+00:00", "wakeword_cache_last_ok": None}


def test_capabilities_keys_match_metrics():
    keys = {d["key"] for d in c.capabilities(proxmox=True)}
    produced = set(c.status_metrics({})) | set(c.proxmox_metrics({}, {}, {})) | set(
        c.vm_metrics(MEMINFO, "0 0 0", (1, 1, 1, 1), "1 1", 1)) | set(
        c.wakeword_pipeline_metrics(None, None))
    attrs = {k for k in produced if k.endswith("_attributes")}
    assert produced - attrs <= keys
    assert {k.removesuffix("_attributes") for k in attrs} <= keys


def test_proxmox_unavailable_without_token():
    caps = {d["key"]: d for d in c.capabilities(proxmox=False)}
    assert caps["host_memory_usage_pct"]["available"] is False
    assert caps["vm_memory_pct"]["available"] is True
    caps = {d["key"]: d for d in c.capabilities(proxmox=True)}
    assert caps["nvme_temperature"]["available"] is True


def test_capabilities_names_units_and_retired_keys():
    caps = {d["key"]: d for d in c.capabilities(proxmox=True)}
    assert caps["vm_cpu_usage_pct"]["name"] == "CPU Usage" and caps["vm_memory_pct"]["name"] == "Memory Usage"
    assert caps["vm_cpu_usage_pct"]["unit"] == "%" and caps["vm_cpu_usage_pct"]["state_class"] == "measurement"
    assert caps["host_memory_usage_pct"]["name"] == "Proxmox host memory usage"
    assert caps["host_memory_usage_pct"]["unit"] == "%" and caps["host_memory_usage_pct"]["available"] is True
    assert not {"vm_cpu_load_pct", "host_available_mb"} & set(caps)
    w = caps["brain_memory_watcher_degraded"]
    assert w["platform"] == "binary_sensor" and w["device_class"] == "problem"
    assert w["category"] == "diagnostic" and w["name"] == "Brain memory watcher degraded"
    assert "memory_index_last_success" not in caps
    for k in ("vm_memory_pct", "vm_cpu_usage_pct", "vm_disk_pct", "brain_ready", "brain_last_dream", "lucid_last_run",
              "conversation_open", "host_memory_usage_pct", "nvme_temperature", "last_backup",
              "backup_oldest_guest", "backup_storage_used_pct", "image_latest", "ota_server"):
        assert caps[k]["category"] == "diagnostic", k


def test_source_nulls_keys_after_three_failures_and_recovers():
    src = c.Source(["a", "b"])
    assert src.failed() == {} and src.failed() == {}
    assert src.failed() == {"a": None, "b": None}
    assert src.failed() == {}  # pushed once, not repeated
    assert src.ok({"a": 1}) == {"a": 1}
    assert src.failed() == {} and src.failed() == {}
    assert src.failed() == {"a": None, "b": None}


def test_success_resets_failure_count():
    src = c.Source(["a"])
    src.failed(); src.failed(); src.ok({"a": 1})
    assert src.failed() == {}


def test_conversation_open_not_overwritten_when_not_computed():
    s = {"ready": True}
    assert "conversation_open" not in c.status_changes(s)
    assert c.status_changes({**s, "conversation_open": False})["conversation_open"] is False


def test_reminders_only_home_enabled_future_sorted():
    import json
    from friday_server_agent import reminders as r
    home = {"completionDestination": {"to": "http://127.0.0.1:7860/reminder"}}
    rows = [
        (json.dumps({"name": "b", "enabled": True, "delivery": home}), json.dumps({"nextRunAtMs": 3_000_000})),
        (json.dumps({"name": "a", "enabled": True, "delivery": home}), json.dumps({"nextRunAtMs": 2_000_000})),
        (json.dumps({"name": "off", "enabled": False, "delivery": home}), json.dumps({"nextRunAtMs": 2_500_000})),
        (json.dumps({"name": "telegram", "enabled": True, "delivery": {"mode": "announce"}}),
         json.dumps({"nextRunAtMs": 2_500_000})),
        (json.dumps({"name": "past", "enabled": True, "delivery": home}), json.dumps({"nextRunAtMs": 500_000})),
        ("not json", None),
    ]
    out = r.pending(rows, 1_000_000)
    assert [x["name"] for x in out] == ["a", "b"]
    assert out[0]["at"] == "1970-01-01T00:33:20+00:00"
    assert r.DESCRIPTOR in c.capabilities(False)


def test_activity_view_counts_sessions_and_brain_turns():
    a = {"updated_at": 1000.0, "sessions": ["voz-1", "voz-2"], "brain_turns": 1}
    assert c.activity_view(a, 1010.0) == {"conversation_open": True, "sessions": 2, "brain_turns": 1}
    assert c.activity_view({"updated_at": 1000.0, "sessions": [], "brain_turns": 0}, 1010.0)["conversation_open"] is False


def test_activity_stale_heartbeat_means_nothing_open():
    """Minor 13: a crash mid-session used to block restarts for up to 6 h (log rule)."""
    a = {"updated_at": 1000.0, "sessions": ["voz-1"], "brain_turns": 2}
    assert c.activity_view(a, 1000.0 + c.ACTIVITY_STALE_S + 1) == {
        "conversation_open": False, "sessions": 0, "brain_turns": 0}
    assert c.is_busy(a, 1000.0 + c.ACTIVITY_STALE_S + 1, lambda: "x cliente conectado\n") is False


def test_is_busy_with_brain_turn_and_no_session():
    a = {"updated_at": 1000.0, "sessions": [], "brain_turns": 1}
    assert c.is_busy(a, 1001.0, lambda: None) is True


def test_is_busy_falls_back_to_log_and_fails_closed():
    assert c.is_busy(None, 0, lambda: "x cliente conectado\n") is True
    assert c.is_busy({"bad": 1}, 0, lambda: "x cliente conectado\nx cliente desconectado\n") is False
    import pytest
    with pytest.raises(OSError):
        c.is_busy(None, 0, lambda: None)
