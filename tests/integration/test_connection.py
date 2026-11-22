"""Tests for FridayConnection against a fake aiohttp websocket agent."""

import asyncio

from aiohttp import WSMsgType, web
import pytest
from pytest_socket import socket_allow_hosts

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from custom_components.friday import connection as conn_mod
from custom_components.friday.connection import FridayConnection
from custom_components.friday.const import DOMAIN

TOKEN = "secret-token"


def hello_msg(protocol=1):
    return {
        "type": "hello",
        "protocol": protocol,
        "device": {"id": "dev1", "kind": "server", "name": "Friday"},
        "capabilities": [{"key": "a", "platform": "sensor", "name": "A"}],
        "state": {"a": 1},
    }


class FakeAgent:
    def __init__(self, hello=None, on_command=None):
        self.hello = hello or hello_msg()
        self.on_command = on_command
        self.sockets = []
        self.connections = 0
        self.auth = []
        self.runner = None
        self.port = None
        self.host = "127.0.0.1"

    async def handle(self, request):
        self.auth.append(request.headers.get("Authorization"))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        self.sockets.append(ws)
        await ws.send_json(self.hello)
        async for msg in ws:
            if msg.type == WSMsgType.TEXT and self.on_command:
                import json

                await self.on_command(ws, json.loads(msg.data))
        return ws

    async def start(self):
        app = web.Application()
        app.router.add_get("/v1/ws", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port or 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        await self.runner.cleanup()


@pytest.fixture(autouse=True)
def loopback_hosts():
    """The cable-first tests use 127.0.0.2 as a second address; the HA plugin allows 127.0.0.1 only."""
    socket_allow_hosts(["127.0.0.1", "127.0.0.2"])
    yield


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(conn_mod, "BACKOFF_START", 0.05)
    monkeypatch.setattr(conn_mod, "BACKOFF_MAX", 0.1)


async def make(hass, agent):
    await agent.start()
    c = FridayConnection(hass, "127.0.0.1", agent.port, TOKEN, "sub1")
    return c


async def wait_for(pred, timeout=3):
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.02)


async def test_connects_and_stores_hello(hass):
    agent = FakeAgent()
    c = await make(hass, agent)
    await c.start()
    await wait_for(lambda: c.connected)
    assert c.hello.device["id"] == "dev1"
    assert c.state == {"a": 1}
    assert agent.auth == [f"Bearer {TOKEN}"]
    await c.stop()
    assert not c.connected
    await agent.stop()


async def test_state_listener_receives_changes_and_origin(hass):
    agent = FakeAgent()
    c = await make(hass, agent)
    got = []
    events = []
    logs = []
    c.add_listener(lambda ch, origin: got.append((ch, origin)))
    c.add_event_listener(lambda k, t, d: events.append((k, t, d)))
    c.add_log_listener(lambda m, d: logs.append((m, d)))
    await c.start()
    await wait_for(lambda: c.connected)
    ws = agent.sockets[0]
    await ws.send_json({"type": "state", "changes": {"a": 2}, "origin": "voice"})
    await ws.send_json({"type": "event", "key": "e", "event_type": "x", "data": {"q": 1}})
    await ws.send_json({"type": "log", "message": "hi", "data": {}})
    await wait_for(lambda: got and events and logs)
    assert got == [({"a": 2}, "voice")]
    assert c.state["a"] == 2
    assert events == [("e", "x", {"q": 1})]
    assert logs == [("hi", {})]
    await c.stop()
    await agent.stop()


async def test_send_returns_data_and_raises_error(hass):
    async def on_command(ws, msg):
        if msg["name"] == "ok":
            await ws.send_json({"type": "result", "id": msg["id"], "ok": True, "data": {"v": 1}})
        else:
            await ws.send_json(
                {"type": "result", "id": msg["id"], "ok": False, "error": "conversation_open"}
            )

    agent = FakeAgent(on_command=on_command)
    c = await make(hass, agent)
    await c.start()
    await wait_for(lambda: c.connected)
    assert await c.send("ok", key="a") == {"v": 1}
    with pytest.raises(HomeAssistantError, match="conversation_open"):
        await c.send("bad")
    await c.stop()
    with pytest.raises(HomeAssistantError, match="not connected"):
        await c.send("ok")
    await agent.stop()


async def test_command_times_out_when_peer_drops(hass):
    async def on_command(ws, msg):
        await ws.close()

    agent = FakeAgent(on_command=on_command)
    c = await make(hass, agent)
    await c.start()
    await wait_for(lambda: c.connected)
    with pytest.raises(HomeAssistantError):
        await c.send("x", timeout=0.5)
    await c.stop()
    await agent.stop()


async def test_reconnects_after_close(hass):
    agent = FakeAgent()
    c = await make(hass, agent)
    await c.start()
    await wait_for(lambda: c.connected)
    await agent.sockets[0].close()
    await wait_for(lambda: agent.connections == 2 and c.connected)
    await c.stop()
    await agent.stop()


async def test_protocol_mismatch_raises_repair(hass):
    agent = FakeAgent(hello=hello_msg(protocol=2))
    c = await make(hass, agent)
    await c.start()
    await wait_for(
        lambda: ir.async_get(hass).async_get_issue(DOMAIN, "protocol_sub1") is not None
    )
    assert not c.connected
    await c.stop()
    await agent.stop()


async def test_bool_protocol_is_mismatch(hass):
    agent = FakeAgent(hello=hello_msg(protocol=True))
    c = await make(hass, agent)
    await c.start()
    await wait_for(
        lambda: ir.async_get(hass).async_get_issue(DOMAIN, "protocol_sub1") is not None
    )
    assert not c.connected
    await c.stop()
    await agent.stop()


async def test_entities_unavailable_while_disconnected(hass):
    agent = FakeAgent()
    c = await make(hass, agent)
    avail = []
    c.add_availability_listener(avail.append)
    await c.start()
    await wait_for(lambda: c.connected)
    await agent.sockets[0].close()
    await wait_for(lambda: False in avail)
    assert avail[0] is True
    await c.stop()
    await agent.stop()


async def test_capabilities_listener(hass):
    agent = FakeAgent()
    c = await make(hass, agent)
    seen = []
    c.add_capabilities_listener(lambda: seen.append(1))
    await c.start()
    await wait_for(lambda: c.connected)
    await agent.sockets[0].send_json({"type": "capabilities", "capabilities": []})
    await wait_for(lambda: seen)
    await c.stop()
    await agent.stop()


async def test_silent_peer_detected_by_heartbeat(hass, monkeypatch):
    monkeypatch.setattr(conn_mod, "HEARTBEAT", 0.2)

    class Mute(FakeAgent):
        async def handle(self, request):
            ws = web.WebSocketResponse(autoping=False)
            await ws.prepare(request)
            self.connections += 1
            self.sockets.append(ws)
            await ws.send_json(self.hello)
            await asyncio.sleep(1.5)  # never reads: pings go unanswered
            return ws

    agent = Mute()
    c = await make(hass, agent)
    avail = []
    c.add_availability_listener(avail.append)
    await c.start()
    await wait_for(lambda: c.connected)
    await wait_for(lambda: False in avail, timeout=3)
    assert not c.connected
    await c.stop()
    await agent.stop()


async def test_stop_survives_a_failing_availability_listener(hass):
    """18/11: one listener raising inside stop() left the whole config entry in FAILED_UNLOAD."""
    agent = FakeAgent()
    c = await make(hass, agent)
    avail = []

    def boom(_up):
        raise KeyError("gone")

    c.add_availability_listener(boom)
    c.add_availability_listener(avail.append)
    await c.start()
    await wait_for(lambda: c.connected)
    await c.stop()  # must not raise
    assert avail[-1] is False and not c.connected
    await agent.stop()



# --- cable first (spec 2026-11-21-satellite-lan-first): two hosts, the cable first ---
# 127.0.0.1 and 127.0.0.2 are both loopback; each fake agent listens on one of them only, so "the other host is down"
# is a real refused connection.

DEAD, LIVE = "127.0.0.2", "127.0.0.1"


async def second_agent(first):
    """A second fake agent on DEAD at the same port as `first` (which listens on LIVE)."""
    other = FakeAgent()
    other.host, other.port = DEAD, first.port
    await other.start()
    return other


async def test_connects_on_first_host(hass):
    agent = FakeAgent()
    await agent.start()
    c = FridayConnection(hass, [LIVE, DEAD], agent.port, TOKEN, "sub1")
    await c.start()
    await wait_for(lambda: c.connected)
    assert c.host == LIVE
    await c.stop()
    await agent.stop()


async def test_falls_back_to_second_host_without_backoff(hass, monkeypatch):
    monkeypatch.setattr(conn_mod, "BACKOFF_START", 30.0)  # a backoff between the two hosts would blow the wait
    agent = FakeAgent()
    await agent.start()
    c = FridayConnection(hass, [DEAD, LIVE], agent.port, TOKEN, "sub1")
    await c.start()
    await wait_for(lambda: c.connected, timeout=2)
    assert c.host == LIVE
    await c.stop()
    await agent.stop()


async def test_backoff_starts_only_after_every_host_failed(hass, monkeypatch):
    monkeypatch.setattr(conn_mod, "BACKOFF_START", 0.3)
    monkeypatch.setattr(conn_mod, "BACKOFF_MAX", 0.3)
    attempts = []

    async def failing(self, host):
        attempts.append((host, hass.loop.time()))
        raise OSError("down")

    monkeypatch.setattr(FridayConnection, "_session", failing)
    c = FridayConnection(hass, ["10.0.0.5", "10.0.0.6"], 8701, TOKEN, "sub1")
    await c.start()
    await wait_for(lambda: len(attempts) >= 4)
    await c.stop()
    assert [a[0] for a in attempts[:4]] == ["10.0.0.5", "10.0.0.6", "10.0.0.5", "10.0.0.6"]
    assert attempts[1][1] - attempts[0][1] < 0.15  # next host at once
    assert attempts[2][1] - attempts[1][1] >= 0.25  # the backoff came after the second failure


def script_probe(monkeypatch, results, calls):
    """Replace the cable probe: `results` is consumed in order, the last value repeats."""
    async def probe(self, host, port):
        calls.append((host, hass_time()))
        return results.pop(0) if len(results) > 1 else results[0]

    monkeypatch.setattr(FridayConnection, "_probe", probe)


def hass_time():
    return asyncio.get_running_loop().time()


async def test_returns_only_after_60s_stable(hass, monkeypatch):
    """Scaled down: probe 0.02 s, stable 0.2 s. A streak shorter than the stable time, a failure, then answers again:
    the switch happens a full stable time after the first answer following the failure."""
    monkeypatch.setattr(conn_mod, "CABLE_PROBE_S", 0.02)
    monkeypatch.setattr(conn_mod, "CABLE_STABLE_S", 0.2)
    calls = []
    script_probe(monkeypatch, [True] * 8 + [False, True], calls)
    live = FakeAgent()
    await live.start()
    c = FridayConnection(hass, [DEAD, LIVE], live.port, TOKEN, "sub1")
    await c.start()
    await wait_for(lambda: c.connected and c.host == LIVE)
    cable = await second_agent(live)  # the cable is back: a real reconnect can now land there
    await wait_for(lambda: c.host == DEAD, timeout=3)
    t_first_ok_after_fail = calls[9][1]  # call 0-7 true, 8 false, 9 = first true after the failure
    assert c.host == DEAD and cable.connections == 1
    assert live.sockets[0].closed
    # the last probe before the switch is at least one stable time after that first answer
    assert calls[-1][1] - t_first_ok_after_fail >= 0.2 - 0.03
    await c.stop()
    await cable.stop()
    await live.stop()


async def test_single_host_no_probe(hass, monkeypatch):
    monkeypatch.setattr(conn_mod, "CABLE_PROBE_S", 0.02)
    calls = []
    script_probe(monkeypatch, [True], calls)
    agent = FakeAgent()
    await agent.start()
    c = FridayConnection(hass, LIVE, agent.port, TOKEN, "sub1")  # a bare str = one host
    await c.start()
    await wait_for(lambda: c.connected)
    await asyncio.sleep(0.2)
    assert calls == [] and c._watch_task is None
    await c.stop()
    await agent.stop()


async def test_no_probe_while_on_the_first_host(hass, monkeypatch):
    monkeypatch.setattr(conn_mod, "CABLE_PROBE_S", 0.02)
    calls = []
    script_probe(monkeypatch, [True], calls)
    agent = FakeAgent()
    await agent.start()
    c = FridayConnection(hass, [LIVE, DEAD], agent.port, TOKEN, "sub1")
    await c.start()
    await wait_for(lambda: c.connected)
    await asyncio.sleep(0.2)
    assert calls == [] and agent.connections == 1
    await c.stop()
    await agent.stop()


async def test_set_hosts_reconnects_when_host_in_use_removed(hass):
    live = FakeAgent()
    await live.start()
    other = await second_agent(live)
    c = FridayConnection(hass, [LIVE], live.port, TOKEN, "sub1")
    await c.start()
    await wait_for(lambda: c.connected and c.host == LIVE)
    c.set_hosts([DEAD], live.port)
    await wait_for(lambda: c.connected and c.host == DEAD, timeout=3)
    assert other.connections == 1 and live.connections == 1
    await c.stop()
    await other.stop()
    await live.stop()


async def test_set_hosts_same_in_use_keeps_session(hass, monkeypatch):
    monkeypatch.setattr(conn_mod, "CABLE_PROBE_S", 0.02)
    script_probe(monkeypatch, [False], [])
    live = FakeAgent()
    await live.start()
    c = FridayConnection(hass, [LIVE], live.port, TOKEN, "sub1")
    await c.start()
    await wait_for(lambda: c.connected)
    c.set_hosts([DEAD, LIVE], live.port)  # the host in use is still in the list (now second)
    await asyncio.sleep(0.3)
    assert c.connected and live.connections == 1 and c.host == LIVE
    await c.stop()
    await live.stop()
