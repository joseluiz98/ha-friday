import pytest
from friday_server_agent.ota import proxmox as p

API = "https://pve.example:8006/api2/json/nodes/pve"
TOKEN = "root@pam!ota=00000000-secret-token"
OK_TASK = {"status": "stopped", "exitstatus": "OK"}

def fake(responses=None):
    calls = []
    def request(method, url, token, data=None, ctx=None):
        assert token == TOKEN
        calls.append((method, url, data))
        v = (responses or {}).get((method, url), "UPID:pve:1:2:3:x:101:root@pam:")
        if isinstance(v, Exception):
            raise v
        return {"data": v}
    return request, calls

def test_create_posts_snapshot_form():
    request, calls = fake()
    s = p.ProxmoxSnapshots(API, "101", TOKEN, request=request, clock=lambda: 1000)
    name = s.snapshot_name("v0.3.0")
    assert name == "friday-ota-v030-1000"
    assert s.create(name) == "UPID:pve:1:2:3:x:101:root@pam:"
    method, url, data = calls[0]
    assert (method, url) == ("POST", f"{API}/qemu/101/snapshot")
    assert data == {"snapname": name, "vmstate": "0", "description": "friday-ota"}
    assert name[0].isalpha() and all(c.isalnum() or c in "-_" for c in name)

def test_rollback_and_delete_urls():
    request, calls = fake()
    s = p.ProxmoxSnapshots(API, "101", TOKEN, request=request)
    s.rollback("friday-ota-v030-1")
    s.delete("friday-ota-v030-1")
    assert calls[0][:2] == ("POST", f"{API}/qemu/101/snapshot/friday-ota-v030-1/rollback")
    assert calls[1][:2] == ("DELETE", f"{API}/qemu/101/snapshot/friday-ota-v030-1")

def test_wait_ok_and_polls_until_stopped():
    url = f"{API}/tasks/UPID%3Apve%3A1/status"
    seq = iter([{"status": "running"}, OK_TASK])
    def request(method, u, token, data=None, ctx=None):
        assert u == url
        return {"data": next(seq)}
    slept = []
    p.ProxmoxSnapshots(API, "101", TOKEN, request=request, sleep=slept.append).wait("UPID:pve:1")
    assert len(slept) == 1

def test_wait_raises_on_failed_task():
    request, _ = fake({("GET", f"{API}/tasks/U/status"): {"status": "stopped", "exitstatus": "snapshot failed"}})
    with pytest.raises(p.ProxmoxError) as e:
        p.ProxmoxSnapshots(API, "101", TOKEN, request=request).wait("U")
    assert "snapshot failed" in str(e.value) and TOKEN not in str(e.value) and "secret" not in str(e.value)

def test_wait_times_out():
    now = [0.0]
    def request(method, u, token, data=None, ctx=None):
        return {"data": {"status": "running"}}
    def sleep(s):
        now[0] += s
    s = p.ProxmoxSnapshots(API, "101", TOKEN, request=request, clock=lambda: now[0], sleep=sleep)
    with pytest.raises(p.ProxmoxError):
        s.wait("U", timeout=30)

def test_prune_only_old_friday_snapshots():
    lst = [{"name": "friday-ota-v030-1", "snaptime": 1}, {"name": "friday-ota-v040-9", "snaptime": 99990},
           {"name": "manual-backup", "snaptime": 1}, {"name": "current"}]
    request, calls = fake({("GET", f"{API}/qemu/101/snapshot"): lst,
                           ("GET", f"{API}/tasks/UPID%3Apve%3A1%3A2%3A3%3Ax%3A101%3Aroot%40pam%3A/status"): OK_TASK})
    s = p.ProxmoxSnapshots(API, "101", TOKEN, request=request, clock=lambda: 100000)
    assert s.prune(keep_s=86400) == ["friday-ota-v030-1"]
    deletes = [c for c in calls if c[0] == "DELETE"]
    assert [c[1] for c in deletes] == [f"{API}/qemu/101/snapshot/friday-ota-v030-1"]

def test_token_never_in_error():
    request, _ = fake({("GET", f"{API}/qemu/101/snapshot"): OSError(f"boom {TOKEN}")})
    with pytest.raises(p.ProxmoxError) as e:
        p.ProxmoxSnapshots(API, "101", TOKEN, request=request).list()
    assert TOKEN not in str(e.value) and "secret" not in str(e.value) and e.value.__cause__ is None
