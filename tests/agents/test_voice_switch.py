"""Persistent global voice switch of the server agent."""

import pytest

from friday_agent_core import AgentCore
from friday_server_agent import voice_switch as vs

HDR = {"Authorization": "Bearer t"}


def test_missing_file_means_enabled(tmp_path):
    assert vs.read_enabled(str(tmp_path / "voice-enabled")) is True


def test_switch_persists_state(tmp_path):
    path = str(tmp_path / "voice-enabled")
    sw = vs.VoiceSwitch(path)
    assert sw.enabled is True
    assert sw.set(False) == {"voice": False}
    assert open(path).read().strip() == "off"
    assert vs.VoiceSwitch(path).enabled is False  # agent restart
    sw.set(True)
    assert open(path).read().strip() == "on"
    assert vs.VoiceSwitch(path).enabled is True


def test_descriptor():
    assert vs.DESCRIPTOR == {"key": "voice", "platform": "switch", "name": "F.R.I.D.A.Y.",
                             "icon": "mdi:account-voice"}


async def test_set_command_over_ws(aiohttp_client, tmp_path):
    path = str(tmp_path / "voice-enabled")
    sw = vs.VoiceSwitch(path)
    core = AgentCore("t", {"id": "server", "kind": "server", "name": "S"}, lambda: [], lambda: {},
                     {"set": vs.make_set_handler(sw)})
    client = await aiohttp_client(core.app())
    ws = await client.ws_connect("/v1/ws", headers=HDR)
    await ws.receive_json()
    await ws.send_json({"type": "command", "id": 1, "name": "set", "key": "voice", "value": False})
    assert (await ws.receive_json())["ok"] is True
    assert open(path).read().strip() == "off"
    await ws.send_json({"type": "command", "id": 2, "name": "set", "key": "other", "value": True})
    r = await ws.receive_json()
    assert r["ok"] is False and r["error"] == "unknown_key"


def test_capabilities_include_voice_switch():
    from friday_server_agent import collectors as c
    caps = {x["key"]: x for x in c.capabilities(False)}
    assert caps["voice"]["platform"] == "switch" and caps["voice"]["icon"] == "mdi:account-voice"
