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
         "openclaw_version": "2026.9.9", "lucid": {"finished_at": "2026-10-29T03:31:00Z"},
         "memory_views": {"mode": "ab"}, "conversation_open": True}
    m = c.status_metrics(s)
    assert m == {"brain_ready": True, "brain_last_dream": "2026-10-29T05:00:00Z",
                 "brain_restarted_in_process": False, "brain_version": "2026.9.9",
                 "lucid_last_run": "2026-10-29T03:31:00Z", "memory_views_mode": "ab",
                 "conversation_open": True}
    assert c.status_metrics({})["lucid_last_run"] is None


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
    empty = c.proxmox_metrics({}, {}, {})
    assert empty == {"host_available_mb": None, "nvme_temperature": None, "last_backup": None}


def test_capabilities_keys_match_metrics():
    keys = {d["key"] for d in c.capabilities(proxmox=True)}
    produced = set(c.status_metrics({})) | set(c.proxmox_metrics({}, {}, {})) | set(
        c.vm_metrics(MEMINFO, "0 0 0", (1, 1, 1, 1), "1 1", 1))
    assert produced <= keys


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
