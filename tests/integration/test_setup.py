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
    await agent.sockets[0].send_json({"type": "capabilities", "capabilities": CAPS[:1]})
    await wait_for(lambda: agent.connections > before)
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


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
