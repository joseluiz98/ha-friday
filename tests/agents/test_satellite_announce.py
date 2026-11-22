"""Satellite zeroconf announcement: the cable and Wi-Fi addresses it declares (spec 2026-11-21-satellite-lan-first)."""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import announce as an  # noqa: E402


class _Zc:
    def __init__(self): self.closed = False
    def close(self): self.closed = True


def fake_run(devices, ips):
    """nmcli stand-in: `devices` is the `device` listing, `ips` maps a device to its IP4.ADDRESS."""
    def run(argv):
        if argv[:2] == ["nmcli", "-t"] and argv[-1] == "device":
            return devices
        if "IP4.ADDRESS" in argv and "show" in argv:
            return ips.get(argv[-1], "")
        return ""
    return run


BOTH = "eth0:ethernet:connected\nwlan0:wifi:connected\nlo:loopback:unmanaged\n"


def test_addresses_both():
    run = fake_run(BOTH, {"eth0": "10.0.0.5/24\n", "wlan0": "10.0.0.6/24\n"})
    assert an.addresses(run) == {"wired": "10.0.0.5", "wifi": "10.0.0.6"}


def test_addresses_wifi_only():
    run = fake_run("eth0:ethernet:unavailable\nwlan0:wifi:connected\n", {"wlan0": "10.0.0.6/24\n"})
    assert an.addresses(run) == {"wifi": "10.0.0.6"}


def test_addresses_empty_when_nmcli_fails():
    def boom(argv):
        raise FileNotFoundError("nmcli")
    assert an.addresses(boom) == {}


def test_announce_props_carry_addresses(monkeypatch):
    seen = {}

    class FakeInfo:
        def __init__(self, type_, name, addresses, port, properties, server):
            seen.update(addresses=addresses, properties=properties)

    class FakeZc:
        def __init__(self, **kw): pass
        def register_service(self, info): pass

    fake = types.SimpleNamespace(IPVersion=types.SimpleNamespace(V4Only=4), ServiceInfo=FakeInfo, Zeroconf=FakeZc)
    monkeypatch.setitem(sys.modules, "zeroconf", fake)
    assert an.announce("sat-1", 8701, "paired", {"wired": "10.0.0.5", "wifi": "10.0.0.6"}) is not None
    assert seen["properties"]["wired"] == "10.0.0.5" and seen["properties"]["wifi"] == "10.0.0.6"
    assert seen["properties"]["id"] == "sat-1"
    assert seen["addresses"] == [bytes([10, 0, 0, 5]), bytes([10, 0, 0, 6])]  # preferred (cable) first


def test_announce_wifi_only_omits_wired(monkeypatch):
    seen = {}

    class FakeInfo:
        def __init__(self, type_, name, addresses, port, properties, server):
            seen.update(addresses=addresses, properties=properties)

    fake = types.SimpleNamespace(IPVersion=types.SimpleNamespace(V4Only=4), ServiceInfo=FakeInfo,
                                 Zeroconf=lambda **kw: types.SimpleNamespace(register_service=lambda i: None))
    monkeypatch.setitem(sys.modules, "zeroconf", fake)
    an.announce("sat-1", 8701, "paired", {"wifi": "10.0.0.6"})
    assert "wired" not in seen["properties"] and seen["properties"]["wifi"] == "10.0.0.6"
    assert seen["addresses"] == [bytes([10, 0, 0, 6])]


def test_announce_without_addresses_falls_back_to_the_route(monkeypatch):
    seen = {}

    class FakeInfo:
        def __init__(self, type_, name, addresses, port, properties, server):
            seen.update(addresses=addresses, properties=properties)

    fake = types.SimpleNamespace(IPVersion=types.SimpleNamespace(V4Only=4), ServiceInfo=FakeInfo,
                                 Zeroconf=lambda **kw: types.SimpleNamespace(register_service=lambda i: None))
    monkeypatch.setitem(sys.modules, "zeroconf", fake)
    monkeypatch.setattr(an, "lan_ip", lambda: "10.0.0.9")
    an.announce("sat-1", 8701, "paired", {})
    assert seen["addresses"] == [bytes([10, 0, 0, 9])]
    assert "wired" not in seen["properties"] and "wifi" not in seen["properties"]


def make_keeper(addr_seq, box):
    made = []
    seq = list(addr_seq)

    def fake(sat_id, port, state, addrs):
        made.append((dict(addrs), _Zc()))
        return made[-1][1]

    k = an.Keeper("sat-1", 8701, "paired", box, announce_fn=fake, addr_fn=lambda: seq.pop(0),
                  ip_fn=lambda: (_ for _ in ()).throw(OSError("no route")))
    return k, made


async def test_keeper_reannounces_when_wifi_changes():
    box = []
    k, made = make_keeper([{"wired": "10.0.0.5", "wifi": "10.0.0.6"},
                           {"wired": "10.0.0.5", "wifi": "10.0.0.7"}], box)
    await k.step(); await k.step()
    assert [m[0] for m in made] == [{"wired": "10.0.0.5", "wifi": "10.0.0.6"},
                                    {"wired": "10.0.0.5", "wifi": "10.0.0.7"}]
    assert made[0][1].closed and box == [made[1][1]]


async def test_keeper_same_addresses_no_reannounce():
    box = []
    same = {"wired": "10.0.0.5", "wifi": "10.0.0.6"}
    k, made = make_keeper([same, dict(same), dict(same)], box)
    await k.step(); await k.step(); await k.step()
    assert len(made) == 1


async def test_keeper_ignores_loopback_addresses():
    box = []
    k, made = make_keeper([{"wired": "127.0.0.1"}], box)
    await k.step()
    assert made == []
