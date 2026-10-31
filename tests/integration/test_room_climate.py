"""Room temperature/humidity: always created by the integration, each with its own source."""

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

TEMP = {"key": "room_temperature", "platform": "sensor", "name": "Room temperature"}
HUM = {"key": "room_humidity", "platform": "sensor", "name": "Room humidity"}
PRESS = {"key": "room_pressure", "platform": "sensor", "name": "Room pressure"}
OTHER = {"key": "a", "platform": "sensor", "name": "A"}


def _hello(caps, state=None):
    h = hello_msg()
    h["device"] = {"id": "sat1", "kind": "satellite", "name": "sat1"}
    h["capabilities"] = caps
    h["state"] = state or {}
    return h


async def _setup(hass, agent, data=None):
    await agent.start()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[
        ConfigSubentryData(data={"host": "127.0.0.1", "port": agent.port, "token": "t", **(data or {})},
                           subentry_type="satellite", title="sat", unique_id=None, subentry_id="s0")])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data["s0"].hello is not None)
    await hass.async_block_till_done()
    return entry


async def _stop(hass, entry, agent):
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_unconfigured_sensors_exist_and_are_unavailable(hass):
    agent = FakeAgent(_hello([OTHER], {"a": 1}))
    entry = await _setup(hass, agent)
    for name in ("room_temperature", "room_humidity"):
        assert hass.states.get(f"sensor.friday_sat1_{name}").state == "unavailable"
    await _stop(hass, entry, agent)


async def test_room_temperature_mirrors_ha_entity(hass):
    hass.states.async_set("sensor.office_temperature", "22.5")
    agent = FakeAgent(_hello([OTHER]))
    entry = await _setup(hass, agent, {"room_temperature_source": "ha_entity",
                                       "room_temperature_entity": "sensor.office_temperature"})
    assert hass.states.get("sensor.friday_sat1_room_temperature").state == "22.5"
    hass.states.async_set("sensor.office_temperature", "23.0")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.friday_sat1_room_temperature").state == "23.0"
    hass.states.async_set("sensor.office_temperature", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.friday_sat1_room_temperature").state == "unavailable"
    assert hass.states.get("sensor.friday_sat1_room_humidity").state == "unavailable"  # own source
    await _stop(hass, entry, agent)


async def test_satellite_source_uses_pushed_value_and_descriptors_are_consumed(hass):
    agent = FakeAgent(_hello([TEMP, HUM, PRESS], {"room_temperature": 21.5, "room_humidity": 48.0,
                                                   "room_pressure": 1013.2}))
    entry = await _setup(hass, agent, {"room_temperature_source": "satellite",
                                       "room_humidity_source": "satellite"})
    assert hass.states.get("sensor.friday_sat1_room_temperature").state == "21.5"
    assert hass.states.get("sensor.friday_sat1_room_humidity").state == "48.0"
    assert hass.states.get("sensor.friday_sat1_room_pressure").state == "1013.2"
    await agent.sockets[0].send_json({"type": "state", "changes": {"room_temperature": 22.0}, "origin": "agent"})
    await wait_for(lambda: hass.states.get("sensor.friday_sat1_room_temperature").state == "22.0")
    # one entity per quantity: the descriptors were not turned into duplicates
    reg = er.async_get(hass)
    ids = [e.unique_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)]
    assert sorted(ids) == ["sat1_room_humidity", "sat1_room_pressure", "sat1_room_temperature"]
    await _stop(hass, entry, agent)


async def test_satellite_source_without_descriptor_is_unavailable(hass):
    agent = FakeAgent(_hello([OTHER]))
    entry = await _setup(hass, agent, {"room_temperature_source": "satellite"})
    assert hass.states.get("sensor.friday_sat1_room_temperature").state == "unavailable"
    await _stop(hass, entry, agent)


async def _flow(hass, entry):
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "satellite"), context={"source": "reconfigure", "subentry_id": "s0"})
    return await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "room_sensors"})


def _source_options(form, name):
    for key, validator in form["data_schema"].schema.items():
        if str(key) == name:
            return [o["value"] if isinstance(o, dict) else o for o in validator.config["options"]]
    raise AssertionError(name)


async def test_satellite_source_offered_even_without_descriptor(hass):
    agent = FakeAgent(_hello([TEMP], {"room_temperature": 21.0}))
    entry = await _setup(hass, agent)
    r = await _flow(hass, entry)
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "room_sensors"
    assert "satellite" in _source_options(r, "room_temperature_source")
    assert _source_options(r, "room_humidity_source") == ["satellite", "ha_entity"]
    await _stop(hass, entry, agent)


async def test_room_sensors_step_saves_choices(hass):
    agent = FakeAgent(_hello([TEMP]))
    entry = await _setup(hass, agent)
    r = await _flow(hass, entry)
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {
        "room_temperature_source": "satellite",
        "room_humidity_source": "ha_entity", "room_humidity_entity": "sensor.some_humidity"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    data = entry.subentries["s0"].data
    assert data["room_temperature_source"] == "satellite" and data["token"] == "t"
    assert data["room_humidity_entity"] == "sensor.some_humidity"
    await hass.async_block_till_done()
    await _stop(hass, entry, agent)


async def test_ha_entity_source_requires_an_entity(hass):
    agent = FakeAgent(_hello([OTHER]))
    entry = await _setup(hass, agent)
    r = await _flow(hass, entry)
    r = await hass.config_entries.subentries.async_configure(
        r["flow_id"], {"room_humidity_source": "ha_entity"})
    assert r["type"] == FlowResultType.FORM and r["errors"] == {"room_humidity_entity": "entity_required"}
    await _stop(hass, entry, agent)
