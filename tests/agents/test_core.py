"""Tests for the shared websocket agent core."""

import asyncio

import pytest

from friday_agent_core import AgentCore

TOKEN = "t0ken"
HDR = {"Authorization": f"Bearer {TOKEN}"}


def _make(handlers=None, state=None):
    return AgentCore(
        token=TOKEN,
        device={"id": "d1", "kind": "satellite", "name": "Sat"},
        capabilities_fn=lambda: [{"key": "volume", "platform": "number"}],
        state_fn=lambda: state if state is not None else {"volume": 3},
        handlers=handlers or {},
    )


async def _connect(aiohttp_client, core):
    client = await aiohttp_client(core.app())
    ws = await client.ws_connect("/v1/ws", headers=HDR)
    hello = await ws.receive_json()
    return client, ws, hello


async def test_rejects_without_bearer(aiohttp_client):
    client = await aiohttp_client(_make().app())
    resp = await client.get("/v1/ws")
    assert resp.status == 401
    resp = await client.get("/v1/ws", headers={"Authorization": "Bearer nope"})
    assert resp.status == 401


async def test_hello_sent_on_connect(aiohttp_client):
    _, _, hello = await _connect(aiohttp_client, _make())
    assert hello["type"] == "hello"
    assert hello["protocol"] == 1
    assert hello["device"]["id"] == "d1"
    assert hello["state"] == {"volume": 3}
    assert hello["capabilities"][0]["key"] == "volume"


async def test_push_state_sends_only_changes(aiohttp_client):
    core = _make()
    _, ws, _ = await _connect(aiohttp_client, core)
    await core.push_state({"a": 1}, "agent")
    await core.push_state({"a": 1}, "agent")
    msg = await ws.receive_json()
    assert msg == {"type": "state", "changes": {"a": 1}, "origin": "agent"}
    with pytest.raises(asyncio.TimeoutError):
        await ws.receive_json(timeout=0.2)


async def test_command_routes_to_handler_and_replies(aiohttp_client):
    seen = []

    async def h_set(cmd):
        seen.append(cmd)
        return {"ok": True, "data": {"x": 1}}

    _, ws, _ = await _connect(aiohttp_client, _make({"set": h_set}))
    await ws.send_json({"type": "command", "id": 7, "name": "set", "key": "volume", "value": 5})
    msg = await ws.receive_json()
    assert msg["type"] == "result" and msg["id"] == 7 and msg["ok"] is True
    assert msg["data"] == {"x": 1}
    assert seen[0]["value"] == 5


async def test_unknown_command_returns_error(aiohttp_client):
    _, ws, _ = await _connect(aiohttp_client, _make())
    await ws.send_json({"type": "command", "id": 1, "name": "bogus"})
    msg = await ws.receive_json()
    assert msg == {"type": "result", "id": 1, "ok": False, "error": "unknown_command"}


async def test_handler_exception_returns_internal(aiohttp_client):
    async def boom(cmd):
        raise RuntimeError("secret-wifi-pass")

    _, ws, _ = await _connect(aiohttp_client, _make({"wifi": boom}))
    await ws.send_json({"type": "command", "id": 2, "name": "wifi", "args": {"p": "x"}})
    msg = await ws.receive_json()
    assert msg == {"type": "result", "id": 2, "ok": False, "error": "internal"}


async def test_multiple_clients_and_event_log_caps(aiohttp_client):
    core = _make()
    client = await aiohttp_client(core.app())
    w1 = await client.ws_connect("/v1/ws", headers=HDR)
    w2 = await client.ws_connect("/v1/ws", headers=HDR)
    await w1.receive_json()
    await w2.receive_json()
    await core.push_event("k", "pressed", {"n": 1})
    await core.push_log("hi", {})
    await core.push_capabilities()
    for w in (w1, w2):
        assert (await w.receive_json())["type"] == "event"
        assert (await w.receive_json())["type"] == "log"
        assert (await w.receive_json())["type"] == "capabilities"
