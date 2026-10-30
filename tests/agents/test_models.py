"""list_models / set_models / set_voice_settings of the server agent."""

import json

import pytest

from friday_agent_core import AgentCore
from friday_server_agent import models as m

HDR = {"Authorization": "Bearer t"}
CATALOG = [
    {"name": "models/gemini-3.8-live", "supportedGenerationMethods": ["bidiGenerateContent"]},
    {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemini-3.8-pro", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]},
    {"name": "models/aqa", "supportedGenerationMethods": ["generateAnswer"]},
]


def make(tmp_path, catalog=CATALOG, busy=False, log_error=None, fetch_error=None):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"other": {"secret": "keep-me"},
                              "agents": {"defaults": {"model": {"primary": "google/gemini-3.8-flash"}}}}))
    calls = []

    def fetch():
        if fetch_error:
            raise fetch_error
        return catalog

    def log_tail():
        if log_error:
            raise log_error
        return "x cliente conectado\n" if busy else "x cliente conectado\nx cliente desconectado\n"

    store = m.ModelStore(str(state), str(oc), fetch, log_tail, lambda: calls.append("restart"))
    core = AgentCore("t", {"id": "server", "kind": "server", "name": "S"}, lambda: [], lambda: {},
                     m.make_handlers(store))
    return core, store, state, oc, calls


async def call(aiohttp_client, core, name, **args):
    client = await aiohttp_client(core.app())
    ws = await client.ws_connect("/v1/ws", headers=HDR)
    await ws.receive_json()
    await ws.send_json({"type": "command", "id": 1, "name": name, "args": args})
    return await ws.receive_json()


async def test_list_models_groups_by_method(aiohttp_client, tmp_path):
    core, *_ = make(tmp_path)
    r = await call(aiohttp_client, core, "list_models")
    d = r["data"]
    assert d["voice"] == ["gemini-3.8-live"]
    assert d["brain"] == ["gemini-3.8-flash", "gemini-3.8-pro"]
    assert d["lucid"] == d["brain"]
    assert d["embeddings"] == ["gemini-embedding-2"]
    assert "Charon" in d["voices"]
    assert d["current"]["brain"] == "gemini-3.8-flash"
    assert d["current"]["lucid"] == "gemini-3.8-flash"
    assert d["current"]["voice"] == "gemini-3.8-live"
    assert d["current"]["speaking_rate"] == "normal" and d["current"]["brief_mode"] is False


async def test_list_models_unavailable(aiohttp_client, tmp_path):
    core, *_ = make(tmp_path, fetch_error=OSError("x"))
    r = await call(aiohttp_client, core, "list_models")
    assert r["ok"] is False and r["error"] == "models_unavailable"


async def test_voice_and_lucid_models_are_files(aiohttp_client, tmp_path):
    core, store, state, oc, calls = make(tmp_path)
    r = await call(aiohttp_client, core, "set_models", voice="gemini-3.9-live", lucid="gemini-3.8-pro")
    assert r["ok"] is True
    assert json.loads((state / "voice-settings.json").read_text())["model"] == "models/gemini-3.9-live"
    assert json.loads((state / "models.json").read_text())["lucid"] == "gemini-3.8-pro"
    assert calls == []


async def test_brain_model_change_waits_for_idle(aiohttp_client, tmp_path):
    core, store, state, oc, calls = make(tmp_path, busy=True)
    r = await call(aiohttp_client, core, "set_models", brain="gemini-3.8-pro")
    assert r["ok"] is True and r["data"]["brain"] == "pending"
    assert calls == []
    assert "gemini-3.8-flash" in oc.read_text()  # untouched while a conversation is open
    assert json.loads((state / "models.json").read_text())["pending_brain"] == "gemini-3.8-pro"
    # conversation closes: the next check applies it, once
    store.log_tail = lambda: "x cliente conectado\nx cliente desconectado\n"
    assert store.apply_pending() is True
    assert store.apply_pending() is False
    cfg = json.loads(oc.read_text())
    assert cfg["agents"]["defaults"]["model"]["primary"] == "google/gemini-3.8-pro"
    assert cfg["other"] == {"secret": "keep-me"}
    assert calls == ["restart"]
    assert "pending_brain" not in json.loads((state / "models.json").read_text())
    assert any(p.name.startswith("openclaw.json.bak.") for p in oc.parent.iterdir())


async def test_brain_model_applies_at_once_when_idle(aiohttp_client, tmp_path):
    core, store, state, oc, calls = make(tmp_path)
    r = await call(aiohttp_client, core, "set_models", brain="gemini-3.8-pro")
    assert r["data"]["brain"] == "applied" and calls == ["restart"]


async def test_brain_pending_when_log_unreadable(aiohttp_client, tmp_path):
    core, store, state, oc, calls = make(tmp_path, log_error=OSError("x"))
    r = await call(aiohttp_client, core, "set_models", brain="gemini-3.8-pro")
    assert r["data"]["brain"] == "pending" and calls == []


async def test_embeddings_not_handled_here(aiohttp_client, tmp_path):
    core, *_ = make(tmp_path)
    r = await call(aiohttp_client, core, "set_models", embeddings="gemini-embedding-2")
    assert r["ok"] is False and r["error"] == "embeddings_unavailable"


async def test_invalid_model_name(aiohttp_client, tmp_path):
    core, *_ = make(tmp_path)
    r = await call(aiohttp_client, core, "set_models", lucid="../x")
    assert r["ok"] is False and r["error"] == "invalid_value"


async def test_voice_settings(aiohttp_client, tmp_path):
    core, store, state, oc, calls = make(tmp_path)
    r = await call(aiohttp_client, core, "set_voice_settings", voice="Kore", speaking_rate="slow", brief_mode=True)
    assert r["ok"] is True
    assert json.loads((state / "voice-settings.json").read_text()) == {
        "voice": "Kore", "speaking_rate": "slow", "brief_mode": True}
    r = await call(aiohttp_client, core, "set_voice_settings", voice="Kore", speaking_rate="warp", brief_mode=True)
    assert r["ok"] is False and r["error"] == "invalid_value"
    r = await call(aiohttp_client, core, "set_voice_settings", voice="Nope", speaking_rate="slow", brief_mode=True)
    assert r["ok"] is False and r["error"] == "invalid_value"
