import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))

from friday_server_agent import collectors as c  # noqa: E402

MEMINFO = "MemTotal:       4000000 kB\nMemFree: 100 kB\nMemAvailable:   1000000 kB\n"


def test_vm_metrics_from_proc_samples():
    m = c.vm_metrics(MEMINFO, "2.00 1.0 0.5 1/100 42", (4096, 1000, 400, 400), "86400.5 100.0", 4)
    assert m["vm_memory_pct"] == round(100 * (4000000 - 1000000) / 4000000, 1)
    assert m["vm_cpu_load_pct"] == 50.0
    assert m["vm_disk_pct"] == 60.0
    assert m["vm_uptime_since"].endswith("+00:00")


def test_status_metrics_maps_fields():
    s = {"ready": True, "last_dream": "2026-10-29T05:00:00Z", "restarted_in_process": False,
         "openclaw_version": "2026.9.9",
         "lucid": {"finished_at": "2026-10-29T03:31:00Z", "date": "2026-10-28", "status": "ok",
                   "cost_usd": 0.012, "facts_added": 2, "user_md_chars": 900},
         "memory_views": {"mode": "ab"}, "conversation_open": True}
    m = c.status_metrics(s)
    assert m == {"brain_ready": True, "brain_last_dream": "2026-10-29T05:00:00Z",
                 "brain_restarted_in_process": False, "brain_version": "2026.9.9",
                 "lucid_last_run": "2026-10-29T03:31:00Z",
                 "lucid_last_run_attributes": {"date": "2026-10-28", "status": "ok", "cost_usd": 0.012,
                                               "facts_added": 2},
                 "memory_views_mode": "ab", "conversation_open": True, "conversation_open_attributes": None}
    assert c.status_metrics({})["lucid_last_run"] is None
    assert c.status_metrics({})["lucid_last_run_attributes"] is None


def test_conversation_open_logic():
    assert c.conversation_open("cliente conectado\n") is True
    assert c.conversation_open("cliente conectado\ncliente desconectado\n") is False


def test_proxmox_metrics_maps_fields():
    node = {"data": {"memory": {"available": 2 * 1048576 * 1000}}}
    smart = {"data": {"text": "x\nTemperature:     47 Celsius\n"}}
    vz = {"data": [{"endtime": 1761700000, "status": "OK"}]}
    m = c.proxmox_metrics(node, smart, vz)
    assert m["host_available_mb"] == 2000
    assert m["nvme_temperature"] == 47
    assert m["last_backup"].startswith("2025-10-29")
    assert m["last_backup_attributes"] == {"status": "OK"}
    empty = c.proxmox_metrics({}, {}, {})
    assert empty == {"host_available_mb": None, "nvme_temperature": None, "last_backup": None,
                     "last_backup_attributes": None}


def test_memory_index_last_success_from_stamp_mtime():
    assert c.memory_index_metrics(1761700000.7) == {"memory_index_last_success": "2025-10-29T01:06:40+00:00"}
    assert c.memory_index_metrics(None) == {"memory_index_last_success": None}


def test_wakeword_pipeline_stamps():
    assert c.wakeword_pipeline_metrics(1761700000.7, None) == {
        "wakeword_sync_last_ok": "2025-10-29T01:06:40+00:00", "wakeword_cache_last_ok": None}


def test_capabilities_keys_match_metrics():
    keys = {d["key"] for d in c.capabilities(proxmox=True)}
    produced = set(c.status_metrics({})) | set(c.proxmox_metrics({}, {}, {})) | set(
        c.vm_metrics(MEMINFO, "0 0 0", (1, 1, 1, 1), "1 1", 1)) | set(c.memory_index_metrics(None)) | set(
        c.wakeword_pipeline_metrics(None, None))
    attrs = {k for k in produced if k.endswith("_attributes")}
    assert produced - attrs <= keys
    assert {k.removesuffix("_attributes") for k in attrs} <= keys


def test_proxmox_unavailable_without_token():
    caps = {d["key"]: d for d in c.capabilities(proxmox=False)}
    assert caps["host_available_mb"]["available"] is False
    assert caps["vm_memory_pct"]["available"] is True
    caps = {d["key"]: d for d in c.capabilities(proxmox=True)}
    assert caps["nvme_temperature"]["available"] is True


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
