from homeassistant.helpers import device_registry as dr

from custom_components.friday.const import DOMAIN
from custom_components.friday.diagnostics import async_get_device_diagnostics

from .test_setup import CAPS, STATE, FakeAgent, entities, hello, setup


async def _device(hass, entry):
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "dev1"), entry.entry_id)


async def test_diagnostics_redacts_secrets(hass):
    async def on_command(ws, cmd):
        assert cmd["name"] == "diagnostics"
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {
            "config": {"senha_porta_local": "hunter2", "porta_local": 8700, "wifi_psk": "x"},
            "logs": {"u": "ok", "v": "GET /x?key=AbCd1234 api_token=zzz Bearer abcdefgh12"}}})

    agent = FakeAgent(hello(CAPS, STATE), on_command)
    entry = await setup(hass, agent)
    await entities(hass, 3)
    out = await async_get_device_diagnostics(hass, entry, await _device(hass, entry))
    text = str(out)
    assert out["agent"]["config"]["porta_local"] == 8700
    assert out["agent"]["config"]["senha_porta_local"] == "**REDACTED**"
    assert out["agent"]["config"]["wifi_psk"] == "**REDACTED**"
    assert out["entry"]["subentry"]["token"] == "**REDACTED**"
    assert "hunter2" not in text
    assert out["agent"]["logs"]["u"] == "ok"
    assert "AbCd1234" not in text and "zzz" not in text and "abcdefgh12" not in text
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_diagnostics_agent_error_does_not_raise(hass):
    async def on_command(ws, cmd):
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": False, "error": "boom"})

    agent = FakeAgent(hello(CAPS, STATE), on_command)
    entry = await setup(hass, agent)
    await entities(hass, 3)
    out = await async_get_device_diagnostics(hass, entry, await _device(hass, entry))
    assert out["agent"] == "unavailable" and out["device"]["id"] == "dev1"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()
