"""Tests for FridayConnection against a fake aiohttp websocket agent."""

import asyncio

from aiohttp import WSMsgType, web
import pytest

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
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        await self.runner.cleanup()


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
