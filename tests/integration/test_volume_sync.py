import asyncio

from .test_connection import wait_for
from .test_setup import CAPS, STATE, FakeAgent, entities, hello, setup


async def test_volume_change_from_voice_is_not_echoed(hass):
    got = []

    async def on_command(ws, cmd):
        got.append(cmd)

    agent = FakeAgent(hello(CAPS, STATE), on_command)
    entry = await setup(hass, agent)
    await entities(hass, 3)
    await agent.sockets[0].send_json(
        {"type": "state", "changes": {"vol": 7}, "origin": "voice"})
    await wait_for(lambda: float(hass.states.get("number.friday_dev1_vol").state) == 7)
    await hass.async_block_till_done()
    await asyncio.sleep(0.2)
    assert got == []
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_server_voice_switch_propagates_to_satellites(hass):
    from homeassistant.config_entries import ConfigSubentryData
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.friday.const import DOMAIN

    from .test_setup import DEVICE

    got = []

    async def on_sat(ws, cmd):
        got.append(cmd)
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    server = FakeAgent(hello([{"key": "voice", "platform": "switch", "name": "F.R.I.D.A.Y."}],
                             {"voice": True}))
    sat_hello = hello([{"key": "voice_enabled", "platform": "switch", "name": "Voice"}], {"voice_enabled": True})
    sat_hello["device"] = dict(DEVICE, id="sat1", kind="satellite")
    sat = FakeAgent(sat_hello, on_sat)
    await server.start()
    await sat.start()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[
        ConfigSubentryData(data={"host": "127.0.0.1", "port": server.port, "token": "t"},
                           subentry_type="server", title="S", unique_id=None, subentry_id="s1"),
        ConfigSubentryData(data={"host": "127.0.0.1", "port": sat.port, "token": "t"},
                           subentry_type="satellite", title="Sat", unique_id=None, subentry_id="s2")])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: all(c.connected for c in entry.runtime_data.values()))
    await server.sockets[0].send_json({"type": "state", "changes": {"voice": False}, "origin": "voice"})
    await wait_for(lambda: len(got) == 1)
    assert got[0]["name"] == "set" and got[0]["key"] == "voice_enabled" and got[0]["value"] is False
    # an unrelated change does not propagate
    await server.sockets[0].send_json({"type": "state", "changes": {"other": 1}, "origin": "agent"})
    await asyncio.sleep(0.2)
    assert len(got) == 1
    await hass.config_entries.async_unload(entry.entry_id)
    await server.stop()
    await sat.stop()


async def _late_join(hass, server_voice):
    from homeassistant.config_entries import ConfigSubentryData
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.friday.const import DOMAIN

    from .test_setup import DEVICE

    got = []

    async def on_sat(ws, cmd):
        got.append(cmd)
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    server = FakeAgent(hello([{"key": "voice", "platform": "switch", "name": "F"}], {"voice": server_voice}))
    sat_hello = hello([{"key": "voice_enabled", "platform": "switch", "name": "Voice"}], {"voice_enabled": True})
    sat_hello["device"] = dict(DEVICE, id="sat1", kind="satellite")
    sat = FakeAgent(sat_hello, on_sat)
    await server.start()
    await sat.start()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[
        ConfigSubentryData(data={"host": "127.0.0.1", "port": server.port, "token": "t"},
                           subentry_type="server", title="S", unique_id=None, subentry_id="s1"),
        ConfigSubentryData(data={"host": "127.0.0.1", "port": sat.port, "token": "t"},
                           subentry_type="satellite", title="Sat", unique_id=None, subentry_id="s2")])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: all(c.connected for c in entry.runtime_data.values()))
    await asyncio.sleep(0.4)
    await hass.config_entries.async_unload(entry.entry_id)
    await server.stop()
    await sat.stop()
    return got


async def test_late_joining_satellite_gets_voice_off(hass):
    got = await _late_join(hass, False)
    assert [(c["key"], c["value"]) for c in got] == [("voice_enabled", False)]


async def test_late_joining_satellite_untouched_when_voice_on(hass):
    assert await _late_join(hass, True) == []
