import asyncio

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

DEVICE = {"id": "dev1", "kind": "server", "name": "Friday", "model": "m",
          "sw_version": "1", "hw_version": "2"}


def hello(caps, state=None):
    h = hello_msg()
    h["device"] = DEVICE
    h["capabilities"] = caps
    h["state"] = state or {}
    return h


CAPS = [
    {"key": "temp", "platform": "sensor", "name": "Temp", "unit": "°C",
     "device_class": "temperature", "state_class": "measurement"},
    {"key": "vol", "platform": "number", "name": "Volume", "min": 0, "max": 10, "step": 1},
    {"key": "dnd", "platform": "switch", "name": "DND"},
]
STATE = {"temp": 20.5, "vol": 3, "dnd": False}


async def setup(hass, agent, sub_id="sub1"):
    await agent.start()
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, title="F.R.I.D.A.Y.",
        subentries_data=[ConfigSubentryData(
            data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
            subentry_type="server", title="Server", unique_id=None, subentry_id=sub_id)],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def entities(hass, n):
    reg = er.async_get(hass)
    await wait_for(lambda: len(er.async_entries_for_config_entry(
        reg, hass.config_entries.async_entries(DOMAIN)[0].entry_id)) >= n)
    return reg


async def test_entities_created_from_hello(hass):
    agent = FakeAgent(hello(CAPS, STATE))
    entry = await setup(hass, agent)
    reg = await entities(hass, 3)
    ents = er.async_entries_for_config_entry(reg, entry.entry_id)
    assert len(ents) == 3
    dev = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "dev1"), entry.entry_id)
    assert dev.name == "Friday" and dev.manufacturer == "F.R.I.D.A.Y."
    assert all(e.device_id == dev.id and e.config_subentry_id == "sub1" for e in ents)
    assert {e.unique_id for e in ents} == {"dev1_temp", "dev1_vol", "dev1_dnd"}
    assert hass.states.get("sensor.friday_dev1_temp").state == "20.5"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_number_set_sends_command(hass):
    got = []

    async def on_command(ws, cmd):
        got.append(cmd)
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    agent = FakeAgent(hello(CAPS, STATE), on_command)
    entry = await setup(hass, agent)
    await entities(hass, 3)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.friday_dev1_vol", "value": 7}, blocking=True)
    assert got[0]["name"] == "set" and got[0]["key"] == "vol" and got[0]["value"] == 7
    # no optimistic write: state still the agent's
    assert float(hass.states.get("number.friday_dev1_vol").state) == 3
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_state_push_updates_entity(hass):
    agent = FakeAgent(hello(CAPS, STATE))
    entry = await setup(hass, agent)
    await entities(hass, 3)
    await agent.sockets[0].send_json({"type": "state", "changes": {"temp": 22.0}, "origin": "agent"})
    await wait_for(lambda: hass.states.get("sensor.friday_dev1_temp").state == "22.0")
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_no_command_sent_on_startup(hass):
    got = []

    async def on_command(ws, cmd):
        got.append(cmd)

    agent = FakeAgent(hello(CAPS, STATE), on_command)
    entry = await setup(hass, agent)
    await entities(hass, 3)
    await hass.async_block_till_done()
    await asyncio.sleep(0.2)
    assert got == []
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_unknown_platform_descriptor_is_skipped(hass):
    caps = CAPS + [{"key": "x", "platform": "lawnmower", "name": "X"}]
    agent = FakeAgent(hello(caps, STATE))
    entry = await setup(hass, agent)
    reg = await entities(hass, 3)
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(reg, entry.entry_id)) == 3
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_hidden_descriptor_is_not_an_entity(hass):
    caps = CAPS + [{"key": "restart", "platform": "button", "name": "Restart", "hidden": True}]
    agent = FakeAgent(hello(caps, STATE))
    entry = await setup(hass, agent)
    reg = await entities(hass, 3)
    await hass.async_block_till_done()
    ents = er.async_entries_for_config_entry(reg, entry.entry_id)
    assert len(ents) == 3 and not any(e.domain == "button" for e in ents)
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_capabilities_message_reloads_entry(hass):
    agent = FakeAgent(hello(CAPS, STATE))
    entry = await setup(hass, agent)
    await entities(hass, 3)
    before = agent.connections
    old_conn = entry.runtime_data["sub1"]
    agent.hello = hello(CAPS[:1], STATE)  # what the agent will say after the reload
    await agent.sockets[0].send_json({"type": "capabilities", "capabilities": CAPS[:1]})
    await wait_for(lambda: agent.connections > before)
    await wait_for(lambda: entry.runtime_data["sub1"] is not old_conn and entry.runtime_data["sub1"].connected)
    await hass.async_block_till_done()
    assert [d.key for d in entry.runtime_data["sub1"].hello.capabilities] == ["temp"]
    assert hass.states.get("sensor.friday_dev1_temp").state == "20.5"
    assert hass.states.get("number.friday_dev1_vol").state == "unavailable"  # no longer built
    assert hass.states.get("number.friday_dev1_vol").attributes.get("restored") is True
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_second_hello_with_different_capabilities_rebuilds_entities(hass):
    agent = FakeAgent(hello(CAPS, STATE))
    entry = await setup(hass, agent)
    await entities(hass, 3)
    old_conn = entry.runtime_data["sub1"]
    agent.hello = hello(CAPS[:1], STATE)
    await agent.sockets[0].close()  # reconnect: the second hello differs, no capabilities message
    await wait_for(lambda: entry.runtime_data["sub1"] is not old_conn and entry.runtime_data["sub1"].connected)
    await hass.async_block_till_done()
    assert [d.key for d in entry.runtime_data["sub1"].hello.capabilities] == ["temp"]
    assert hass.states.get("number.friday_dev1_vol").attributes.get("restored") is True
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_entities_unavailable_on_drop_and_recover_on_reconnect(hass):
    agent = FakeAgent(hello(CAPS, STATE))
    entry = await setup(hass, agent)
    await entities(hass, 3)
    assert hass.states.get("sensor.friday_dev1_temp").state == "20.5"
    port = agent.port
    await agent.stop()
    await wait_for(lambda: hass.states.get("sensor.friday_dev1_temp").state == "unavailable")
    assert hass.states.get("number.friday_dev1_vol").state == "unavailable"
    agent2 = FakeAgent(hello(CAPS, dict(STATE, temp=23.0)))
    agent2.port = port
    await agent2.start()
    await wait_for(lambda: hass.states.get("sensor.friday_dev1_temp").state == "23.0")
    assert float(hass.states.get("number.friday_dev1_vol").state) == 3
    await hass.config_entries.async_unload(entry.entry_id)
    await agent2.stop()


async def test_entity_id_pattern_and_timestamp_sensors(hass):
    caps = CAPS + [
        {"key": "t_off", "platform": "sensor", "name": "A", "device_class": "timestamp"},
        {"key": "t_naive", "platform": "sensor", "name": "B", "device_class": "timestamp"},
        {"key": "t_bad", "platform": "sensor", "name": "C", "device_class": "timestamp"},
    ]
    state = dict(STATE, t_off="2026-10-29T10:00:00-03:00", t_naive="2026-10-29T10:00:00",
                 t_bad="garbage")
    agent = FakeAgent(hello(caps, state))
    entry = await setup(hass, agent)
    await entities(hass, 6)
    assert hass.states.get("sensor.friday_dev1_t_off").state == "2026-10-29T13:00:00+00:00"
    assert hass.states.get("sensor.friday_dev1_t_naive").state == "2026-10-29T10:00:00+00:00"
    assert hass.states.get("sensor.friday_dev1_t_bad").state == "unknown"
    assert hass.states.get("switch.friday_dev1_dnd") is not None
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_enabled_default_false_creates_disabled_entity(hass):
    caps = CAPS + [{"key": "ssid", "platform": "sensor", "name": "SSID", "category": "diagnostic",
                    "enabled_default": False}]
    agent = FakeAgent(hello(caps, dict(STATE, ssid="Home")))
    entry = await setup(hass, agent)
    reg = await entities(hass, 4)
    ent = reg.async_get("sensor.friday_dev1_ssid")
    assert ent.disabled_by is not None and hass.states.get("sensor.friday_dev1_ssid") is None
    assert reg.async_get("sensor.friday_dev1_temp").disabled_by is None
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_every_subentry_gets_its_entities(hass):
    """Regression: the per-subentry `added` flag was shared by all subentries, so the
    first device to say hello blocked the entities of every other device."""
    from homeassistant.config_entries import ConfigSubentryData
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.friday.const import DOMAIN

    from .test_connection import wait_for

    server = FakeAgent(hello([{"key": "voice", "platform": "switch", "name": "Voice"}], {"voice": True}))
    sat_hello = hello([{"key": "voice_enabled", "platform": "switch", "name": "Voice"}], {"voice_enabled": True})
    sat_hello["device"] = dict(DEVICE, id="sat1", kind="satellite")
    sat = FakeAgent(sat_hello)
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
    await hass.async_block_till_done()
    await wait_for(lambda: hass.states.get("switch.friday_sat1_voice_enabled") is not None
                   and hass.states.get(f"switch.friday_{DEVICE['id']}_voice") is not None)
    await hass.config_entries.async_unload(entry.entry_id)
    await server.stop()
    await sat.stop()
