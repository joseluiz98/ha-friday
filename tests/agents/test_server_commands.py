"""Restart action of the server agent."""

from friday_agent_core import AgentCore
from friday_server_agent import commands as cm

HDR = {"Authorization": "Bearer t"}
OPEN = "x cliente conectado\n"
IDLE = "x cliente conectado\nx cliente desconectado\n"


def make(log_text=IDLE, log_error=None):
    calls, logs = [], []

    def log_tail():
        if log_error:
            raise log_error
        return log_text

    async def push_log(message, data):
        logs.append(message)

    handlers = cm.make_handlers(log_tail, lambda: calls.append("restart"), push_log)
    core = AgentCore("t", {"id": "server", "kind": "server", "name": "S"},
                     lambda: [cm.RESTART_DESCRIPTOR], lambda: {}, handlers)
    return core, calls, logs


async def press(aiohttp_client, core, key="restart_friday"):
    client = await aiohttp_client(core.app())
    ws = await client.ws_connect("/v1/ws", headers=HDR)
    await ws.receive_json()
    await ws.send_json({"type": "command", "id": 1, "name": "press", "key": key})
    return await ws.receive_json()


def test_descriptor():
    d = cm.RESTART_DESCRIPTOR
    assert d["platform"] == "button" and d["device_class"] == "restart"
    assert d["key"] == "restart_friday"


async def test_restart_refused_with_conversation_open(aiohttp_client):
    core, calls, logs = make(OPEN)
    r = await press(aiohttp_client, core)
    assert r["ok"] is False and r["error"] == "conversation_open"
    assert calls == [] and logs and "refused" in logs[0]


async def test_restart_refused_when_log_unavailable(aiohttp_client):
    core, calls, _ = make(log_error=OSError("x"))
    r = await press(aiohttp_client, core)
    assert r["ok"] is False and r["error"] == "log_unavailable" and calls == []


async def test_restart_runs_compose_when_idle(aiohttp_client):
    core, calls, logs = make(IDLE)
    r = await press(aiohttp_client, core)
    assert r["ok"] is True and calls == ["restart"] and logs


async def test_unknown_key(aiohttp_client):
    core, calls, _ = make()
    r = await press(aiohttp_client, core, "nope")
    assert r["ok"] is False and r["error"] == "unknown_key" and calls == []
