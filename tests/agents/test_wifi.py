"""Wi-Fi change with rollback; the runner is injected, nothing touches the system."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import wifi  # noqa: E402

PASSWORD = "s3cr3t-pass-123"


class Runner:
    def __init__(self, up_new=0, ping=0):
        self.calls, self.up_new, self.ping = [], up_new, ping

    async def __call__(self, argv):
        self.calls.append(argv)
        a = argv[3:] if argv[:3] == wifi.NMCLI else argv
        if a[:2] == ["-t", "-f"] and "connection" in a:
            return 0, "ExampleNet:802-11-wireless\nlo:loopback\n"
        if a[:3] == ["connection", "up", wifi.NEW_PROFILE]:
            return self.up_new, ""
        if "IP4.GATEWAY" in a:
            return 0, "192.0.2.1\n"
        if argv[0] == "ping":
            return self.ping, ""
        return 0, ""

    def nm(self):
        return [c[3:] for c in self.calls if c[:3] == wifi.NMCLI]


async def no_sleep(_s):
    return None


async def test_wifi_success_keeps_new_profile():
    r = Runner()
    assert await wifi.set_wifi("Casa", PASSWORD, run=r, wait_s=6, sleep=no_sleep) == {"ok": True}
    nm = r.nm()
    assert ["connection", "up", wifi.NEW_PROFILE, "ifname", "wlan0"] in nm
    assert ["connection", "up", "ExampleNet"] not in nm and ["connection", "delete", wifi.NEW_PROFILE] == nm[1]


async def test_wifi_rollback_when_new_network_fails():
    r = Runner(ping=1)  # connected but the gateway never answers
    assert await wifi.set_wifi("Casa", PASSWORD, run=r, wait_s=6, sleep=no_sleep) == {
        "ok": False, "error": "rollback"}
    nm = r.nm()
    assert nm[-2:] == [["connection", "up", "ExampleNet"], ["connection", "delete", wifi.NEW_PROFILE]]


async def test_wifi_rollback_when_activation_fails():
    r = Runner(up_new=4)
    assert (await wifi.set_wifi("Casa", PASSWORD, run=r, wait_s=6, sleep=no_sleep))["error"] == "rollback"
    assert r.nm()[-2:] == [["connection", "up", "ExampleNet"], ["connection", "delete", wifi.NEW_PROFILE]]


async def test_wifi_rejects_bad_credentials_without_touching_nm():
    r = Runner()
    for ssid, pw in (("", PASSWORD), ("Casa", "short"), ("x" * 33, PASSWORD), (None, PASSWORD), ("Casa", None)):
        assert await wifi.set_wifi(ssid, pw, run=r, sleep=no_sleep) == {"ok": False, "error": "invalid"}
    assert r.calls == []


async def test_wifi_password_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    for r in (Runner(), Runner(ping=1), Runner(up_new=4)):
        result = await wifi.set_wifi("Casa", PASSWORD, run=r, wait_s=4, sleep=no_sleep)
        assert PASSWORD not in repr(result)
    assert PASSWORD not in caplog.text


def test_active_wifi_profile_parse():
    assert wifi.active_wifi_profile("a\\:b:802-11-wireless\n") == "a:b"
    assert wifi.active_wifi_profile("lo:loopback\n") is None
