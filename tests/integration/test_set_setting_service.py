"""friday.set_setting: the same validation and logbook as the forms, for automations and the assistant."""

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

CAPS = [
    {"key": "reply_wait", "platform": "number", "name": "W", "min": 2, "max": 30, "step": 1, "hidden": True},
    {"key": "ducking", "platform": "switch", "name": "D", "hidden": True},
    {"key": "ducking_level", "platform": "number", "name": "L", "min": 1, "max": 25, "step": 1, "hidden": True},
    {"key": "wakeword_friday_mode", "platform": "select", "name": "M", "options": ["active", "shadow", "off"],
     "hidden": True},
    {"key": "wakeword_other_mode", "platform": "select", "name": "M", "options": ["active", "shadow", "off"],
     "hidden": True},
    {"key": "volume", "platform": "number", "name": "Volume", "min": 0, "max": 10, "step": 1},
]


def make(kind, dev_id, got, state):
    h = hello_msg()
    h["device"] = {"id": dev_id, "kind": kind, "name": dev_id}
    h["capabilities"] = CAPS
    h["state"] = state

    async def on_command(ws, cmd):
        got.append((cmd["key"], cmd["value"]))
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    return FakeAgent(h, on_command)


async def setup(hass, agents):
    subs = []
    for i, (kind, agent) in enumerate(agents):
        await agent.start()
        subs.append(ConfigSubentryData(
            data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
            subentry_type=kind, title=kind, unique_id=None, subentry_id=f"s{i}"))
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=subs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: len(entry.runtime_data) == len(agents)
                   and all(c.connected for c in entry.runtime_data.values()))
    await hass.async_block_till_done()
    return entry


def device_id(hass, entry, dev_id):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, dev_id)}).id


async def call(hass, **data):
    await hass.services.async_call(DOMAIN, "set_setting", data, blocking=True)


async def refused(hass, **data):
    """The service exists and refuses the call (a missing service also raises a ServiceValidationError subclass)."""
    from homeassistant.exceptions import ServiceNotFound

    with pytest.raises(ServiceValidationError) as err:
        await call(hass, **data)
    assert not isinstance(err.value, ServiceNotFound)


async def finish(hass, entry, agents):
    await hass.config_entries.async_unload(entry.entry_id)
    for _, agent in agents:
        await agent.stop()


async def test_set_setting_general_updates_options_and_all_satellites(hass):
    got1, got2 = [], []
    agents = [("satellite", make("satellite", "sat1", got1, {"reply_wait": 8})),
              ("satellite", make("satellite", "sat2", got2, {"reply_wait": 8}))]
    entry = await setup(hass, agents)
    await call(hass, key="reply_wait", value=10)
    assert entry.options["general"]["reply_wait"] == 10
    await wait_for(lambda: got1 == [("reply_wait", 10)] and got2 == [("reply_wait", 10)])
    await finish(hass, entry, agents)


async def test_set_setting_advanced_routes_to_device(hass):
    got1, got2 = [], []
    agents = [("satellite", make("satellite", "sat1", got1, {"wakeword_friday_mode": "active", "wakeword_other_mode": "active"})),
              ("satellite", make("satellite", "sat2", got2,
                                       {"wakeword_friday_mode": "active", "wakeword_other_mode": "active"}))]
    entry = await setup(hass, agents)
    seen = []
    hass.bus.async_listen("logbook_entry", lambda e: seen.append(e.data.get("message", "")))
    await call(hass, device_id=device_id(hass, entry, "sat2"), key="wakeword_friday_mode", value="shadow")
    assert got1 == [] and got2 == [("wakeword_friday_mode", "shadow")]
    await hass.async_block_till_done()
    assert any("Advanced settings" in m and "active → shadow" in m for m in seen)
    await finish(hass, entry, agents)


async def test_set_setting_refuses_last_active_word(hass):
    got = []
    agents = [("satellite", make("satellite", "sat1", got, {"wakeword_friday_mode": "active", "wakeword_other_mode": "off"}))]
    entry = await setup(hass, agents)
    await refused(hass, device_id=device_id(hass, entry, "sat1"), key="wakeword_friday_mode", value="off")
    assert got == []
    await finish(hass, entry, agents)


async def test_set_setting_validation_errors(hass):
    got = []
    agents = [("satellite", make("satellite", "sat1", got, {"ducking": False, "wakeword_friday_mode": "active"})),
              ("server", make("server", "srv", [], {}))]
    entry = await setup(hass, agents)
    sat = device_id(hass, entry, "sat1")
    await refused(hass, key="reply_wait", value=50)
    await refused(hass, key="close_mic_awaiting_reply", value=True)
    await refused(hass, key="wakeword_friday_mode", value="shadow")  # advanced key needs a device
    await refused(hass, device_id=device_id(hass, entry, "srv"), key="wakeword_friday_mode", value="shadow")
    await refused(hass, device_id=sat, key="ducking_level", value=15)  # an entity on the device page
    await refused(hass, device_id=sat, key="reply_wait", value=9)  # general keys take no device
    await refused(hass, device_id=sat, key="volume", value=3)  # entity keys do not go through the service
    assert got == [] and "general" not in entry.options
    await finish(hass, entry, agents)
