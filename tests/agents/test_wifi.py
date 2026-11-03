"""Wi-Fi change with bounded rollback; the runner is injected, nothing touches the system."""

import asyncio
import io
import json
import logging
import stat
import sys
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import wifi  # noqa: E402

PASSWORD = "s3cr3t-pass-123"
HELPER_PATH = Path(__file__).resolve().parents[2] / "agents" / "satellite" / "friday-wifi-apply"


class Runner:
    """Fake system: tracks the active Wi-Fi profile the way NetworkManager would."""

    def __init__(self, active="ExampleNet", apply_ok=True, ping_ok=True, server_ok=True, hang_apply=False):
        self.calls, self.active, self.apply_ok, self.ping_ok = [], active, apply_ok, ping_ok
        self.server_ok, self.hang_apply, self.deleted, self.upped = True and server_ok, hang_apply, [], []

    async def __call__(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        if argv[:3] == wifi.HELPER:
            cmd = argv[3]
            if cmd == "apply":
                if self.hang_apply:
                    await asyncio.sleep(3600)
                if self.apply_ok:
                    self.active = json.loads(stdin)["name"]
                return (0 if self.apply_ok else 4), ""
            if cmd == "up":
                self.active = argv[4]
                self.upped.append(argv[4])
            if cmd == "delete":
                self.deleted.append(argv[4])
            return 0, ""
        if argv[:2] == ["nmcli", "-t"]:
            return 0, f"{self.active}:802-11-wireless\nlo:loopback\n"
        if "IP4.GATEWAY" in argv:
            return 0, "192.0.2.1\n"
        if argv[0] == "ping":
            ok = self.server_ok if argv[-1] == "10.0.0.2" else self.ping_ok
            return (0 if ok else 1), ""
        return 0, ""


async def no_sleep(_s):
    return None


def changer(runner, tmp_path, clock=None, **kw):
    ticks = iter(range(1000, 2000))
    return wifi.Changer(run=runner, sleep=no_sleep, marker=str(tmp_path / "wifi.json"),
                        clock=clock or (lambda: next(ticks)), **kw)


async def test_wifi_success_keeps_new_profile(tmp_path):
    r = Runner()
    c = changer(r, tmp_path)
    assert await c.set_wifi("Casa", PASSWORD, wait_s=6) == {"ok": True}
    assert r.active == "friday-wifi-1000" and r.deleted == [] and not (tmp_path / "wifi.json").exists()


async def test_wifi_rollback_when_new_network_fails(tmp_path):
    r = Runner(ping_ok=False)
    assert await changer(r, tmp_path).set_wifi("Casa", PASSWORD, wait_s=6) == {"ok": False, "error": "rollback"}
    assert r.active == "ExampleNet" and r.deleted == ["friday-wifi-1000"]


async def test_wifi_rollback_when_activation_fails(tmp_path):
    r = Runner(apply_ok=False)
    assert (await changer(r, tmp_path).set_wifi("Casa", PASSWORD, wait_s=6))["error"] == "rollback"
    assert r.upped == ["ExampleNet"] and r.deleted == ["friday-wifi-1000"]


async def test_wifi_success_requires_reaching_the_server(tmp_path):
    bad = Runner(server_ok=False)
    out = await changer(bad, tmp_path).set_wifi("Casa", PASSWORD, server_host="10.0.0.2", wait_s=4)
    assert out["error"] == "rollback" and bad.active == "ExampleNet"
    good = Runner()
    assert (await changer(good, tmp_path).set_wifi("Casa", PASSWORD, server_host="10.0.0.2", wait_s=4))["ok"]


async def test_two_changes_then_rollback_never_touch_the_active_profile(tmp_path):
    r = Runner()
    c = changer(r, tmp_path)
    assert (await c.set_wifi("Casa", PASSWORD, wait_s=4))["ok"]
    assert r.active == "friday-wifi-1000"
    assert (await c.set_wifi("Casa2", PASSWORD, wait_s=4))["ok"]
    assert r.active == "friday-wifi-1001"
    r.apply_ok = False  # third change fails: previous is the profile from the prior success
    assert (await c.set_wifi("Casa3", PASSWORD, wait_s=4))["error"] == "rollback"
    assert r.active == "friday-wifi-1001"
    assert r.upped == ["friday-wifi-1001"] and r.deleted == ["friday-wifi-1002"]
    assert "friday-wifi-1001" not in r.deleted


async def test_hanging_runner_is_bounded(tmp_path):
    r = Runner(hang_apply=True)
    c = changer(r, tmp_path, cmd_timeout=0.05, deadline_s=0.2)
    t0 = time.monotonic()
    out = await c.set_wifi("Casa", PASSWORD, wait_s=4)
    assert out == {"ok": False, "error": "rollback"} and time.monotonic() - t0 < 2
    assert r.upped == ["ExampleNet"] and r.deleted == ["friday-wifi-1000"]


async def test_password_only_on_stdin_never_argv_never_log(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    for r in (Runner(), Runner(ping_ok=False), Runner(apply_ok=False)):
        result = await changer(r, tmp_path).set_wifi("Casa", PASSWORD, wait_s=4)
        assert PASSWORD not in repr(result)
        assert all(PASSWORD not in " ".join(argv) for argv, _ in r.calls)
        carriers = [(a, s) for a, s in r.calls if s is not None]
        assert len(carriers) == 1 and carriers[0][0] == [*wifi.HELPER, "apply"]
    assert PASSWORD not in caplog.text


async def test_marker_written_during_and_recover_rolls_back(tmp_path):
    seen = {}
    r = Runner()
    orig = r.__call__

    async def spy(argv, stdin=None):
        if argv[:3] == wifi.HELPER and argv[3] == "apply":
            seen["marker"] = json.loads((tmp_path / "wifi.json").read_text())
        return await orig(argv, stdin)

    c = changer(spy, tmp_path)
    await c.set_wifi("Casa", PASSWORD, wait_s=4)
    assert seen["marker"] == {"new": "friday-wifi-1000", "previous": "ExampleNet"}
    # an interrupted attempt: marker left behind, agent restarts
    (tmp_path / "wifi.json").write_text(json.dumps({"new": "friday-wifi-5", "previous": "ExampleNet"}))
    r2 = Runner(active="friday-wifi-5")
    assert await changer(r2, tmp_path).recover() is True
    assert r2.active == "ExampleNet" and r2.deleted == ["friday-wifi-5"] and not (tmp_path / "wifi.json").exists()
    assert await changer(r2, tmp_path).recover() is False


async def test_wifi_rejects_bad_credentials_without_touching_nm(tmp_path):
    r = Runner()
    for ssid, pw in (("", PASSWORD), ("Casa", "short"), ("x" * 33, PASSWORD), (None, PASSWORD), ("Casa", None),
                     ("Casa", "é" * 33), ("Ca\nsa", PASSWORD)):  # 33 x 2 bytes > 63
        assert await changer(r, tmp_path).set_wifi(ssid, pw) == {"ok": False, "error": "invalid"}
    assert r.calls == []


def test_active_wifi_profile_parse():
    assert wifi.active_wifi_profile("a\\:b:802-11-wireless\n") == "a:b"
    assert wifi.active_wifi_profile("lo:loopback\n") is None


# ---- the root helper

def load_helper():
    return SourceFileLoader("friday_wifi_apply", str(HELPER_PATH)).load_module()


class Cp:
    returncode = 0


def test_helper_writes_0600_keyfile_and_activates(tmp_path):
    h, calls = load_helper(), []
    rc = h.main(["x", "apply"], io.StringIO(json.dumps({"name": "friday-wifi-9", "ssid": "Ca\\sa", "password": PASSWORD})),
                run=lambda argv, **kw: calls.append(argv) or Cp(), conn_dir=str(tmp_path))
    f = tmp_path / "friday-wifi-9.nmconnection"
    assert rc == 0 and stat.S_IMODE(f.stat().st_mode) == 0o600
    text = f.read_text()
    assert f"psk={PASSWORD}" in text and "ssid=Ca\\\\sa" in text and "id=friday-wifi-9" in text
    assert calls[0] == ["nmcli", "connection", "reload"]
    assert calls[1][:3] == ["nmcli", "-w", "20"] and "friday-wifi-9" in calls[1]
    assert all(PASSWORD not in " ".join(c) for c in calls)


def test_helper_refuses_bad_input_and_foreign_names(tmp_path):
    h = load_helper()
    run = lambda argv, **kw: Cp()  # noqa: E731
    for payload in ({"name": "ExampleNet", "ssid": "a", "password": PASSWORD},
                    {"name": "friday-wifi-1", "ssid": "a", "password": "short"},
                    {"name": "friday-wifi-1", "ssid": "a\nb", "password": PASSWORD}):
        assert h.main(["x", "apply"], io.StringIO(json.dumps(payload)), run=run, conn_dir=str(tmp_path)) == 2
    assert list(tmp_path.iterdir()) == []
    assert h.main(["x", "delete", "ExampleNet"], io.StringIO(""), run=run, conn_dir=str(tmp_path)) == 2
    assert h.main(["x", "delete", "friday-wifi-3"], io.StringIO(""), run=run, conn_dir=str(tmp_path)) == 0
    assert h.main(["x", "up", "../etc"], io.StringIO(""), run=run, conn_dir=str(tmp_path)) == 2


def test_helper_rejects_leading_dash_and_trailing_newline(tmp_path):
    h = load_helper()
    calls = []
    run = lambda argv, **kw: calls.append(argv) or Cp()  # noqa: E731
    for name in ("-w", "--help", "-"):
        assert h.main(["x", "up", name], io.StringIO(""), run=run, conn_dir=str(tmp_path)) == 2
    assert not calls


def test_helper_name_fullmatch_no_trailing_newline(tmp_path):
    h = load_helper()
    run = lambda argv, **kw: Cp()  # noqa: E731
    payload = {"name": "friday-wifi-1\n", "ssid": "a", "password": PASSWORD}
    assert h.main(["x", "apply"], io.StringIO(json.dumps(payload)), run=run, conn_dir=str(tmp_path)) == 2
    assert h.main(["x", "delete", "friday-wifi-3\n"], io.StringIO(""), run=run, conn_dir=str(tmp_path)) == 2
    assert list(tmp_path.iterdir()) == []


def test_helper_up_uses_explicit_id(tmp_path):
    h, calls = load_helper(), []
    run = lambda argv, **kw: calls.append(argv) or Cp()  # noqa: E731
    assert h.main(["x", "up", "ExampleNet"], io.StringIO(""), run=run, conn_dir=str(tmp_path)) == 0
    assert calls[0] == ["nmcli", "-w", "20", "connection", "up", "id", "ExampleNet", "ifname", "wlan0"]
    h.main(["x", "apply"], io.StringIO(json.dumps({"name": "friday-wifi-4", "ssid": "a", "password": PASSWORD})),
           run=run, conn_dir=str(tmp_path))
    assert calls[-1] == ["nmcli", "-w", "20", "connection", "up", "id", "friday-wifi-4", "ifname", "wlan0"]


def test_helper_existing_file_and_oserror_exit_cleanly(tmp_path, capsys):
    h = load_helper()
    run = lambda argv, **kw: Cp()  # noqa: E731
    (tmp_path / "friday-wifi-5.nmconnection").write_text("x")
    payload = json.dumps({"name": "friday-wifi-5", "ssid": "a", "password": PASSWORD})
    assert h.main(["x", "apply"], io.StringIO(payload), run=run, conn_dir=str(tmp_path)) == 5
    assert (tmp_path / "friday-wifi-5.nmconnection").read_text() == "x"  # never overwritten
    assert h.main(["x", "apply"], io.StringIO(payload), run=run, conn_dir=str(tmp_path / "missing")) == 6
    assert PASSWORD not in capsys.readouterr().err


async def test_helper_call_shape_sudo_argv_and_stdin_only(tmp_path):
    """The real subprocess path: argv is exactly the sudoers-allowed command, the password is only on stdin."""
    assert wifi.HELPER == ["sudo", "-n", "/usr/local/sbin/friday-wifi-apply"]
    out = tmp_path / "seen.json"
    fake = tmp_path / "fake-helper"
    fake.write_text(f"#!{sys.executable}\nimport json,sys\n"
                    f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}, open({str(out)!r}, 'w'))\n")
    fake.chmod(0o755)
    code, _ = await wifi.run_command([str(fake), "apply"], json.dumps({"name": "friday-wifi-1", "ssid": "a", "password": PASSWORD}))
    seen = json.loads(out.read_text())
    assert code == 0 and seen["argv"] == ["apply"] and PASSWORD in seen["stdin"]
    # and the Changer builds exactly HELPER + ["apply"] with the password in stdin only
    r = Runner()
    await changer(r, tmp_path).set_wifi("Casa", PASSWORD)
    argv, stdin = next(c for c in r.calls if c[0][:3] == wifi.HELPER)
    assert argv == ["sudo", "-n", "/usr/local/sbin/friday-wifi-apply", "apply"]
    assert PASSWORD in stdin and not any(PASSWORD in " ".join(a) for a, _ in r.calls)
