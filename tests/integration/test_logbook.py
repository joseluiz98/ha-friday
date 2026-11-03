from homeassistant.components import logbook
from homeassistant.helpers import entity_registry as er  # noqa: F401

from .test_connection import wait_for
from .test_setup import CAPS, STATE, FakeAgent, entities, hello, setup

CAPS_EV = CAPS + [
    {"key": "wake", "platform": "event", "name": "Wake", "event_types": ["activated"]},
]


async def _push(agent, changes, origin):
    await agent.sockets[0].send_json({"type": "state", "changes": changes, "origin": origin})


async def test_voice_origin_fires_friday_change(hass):
    seen = []
    hass.bus.async_listen("friday_change", lambda e: seen.append(e.data))
    agent = FakeAgent(hello(CAPS, STATE))
    entry = await setup(hass, agent)
    await entities(hass, 3)
    await _push(agent, {"vol": 7}, "voice")
    await wait_for(lambda: seen)
    assert seen[0] == {"device_id": "dev1", "entity_id": "number.friday_dev1_vol",
                       "key": "vol", "value": 7, "origin": "voice"}
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_ha_origin_fires_nothing(hass):
    seen = []
    hass.bus.async_listen("friday_change", lambda e: seen.append(e.data))
    agent = FakeAgent(hello(CAPS, STATE))
    entry = await setup(hass, agent)
    await entities(hass, 3)
    await _push(agent, {"vol": 7}, "ha")
    await wait_for(lambda: float(hass.states.get("number.friday_dev1_vol").state) == 7)
    await hass.async_block_till_done()
    assert seen == []
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_logbook_describes_voice_change(hass):
    from custom_components.friday import logbook as fl

    descs = {}
    fl.async_describe_events(hass, lambda dom, ev, cb: descs.setdefault((dom, ev), cb))
    cb = descs[("friday", "friday_change")]

    class Ev:
        def __init__(self, origin):
            self.data = {"entity_id": "number.friday_dev1_vol", "key": "vol",
                         "value": 7, "origin": origin, "device_id": "dev1"}

    hass.states.async_set("number.friday_dev1_vol", "3", {"friendly_name": "Friday Volume"})
    out = cb(Ev("voice"))
    assert out["message"] == "set to 7 by voice" and out["entity_id"] == "number.friday_dev1_vol"
    assert cb(Ev("button"))["message"] == "set to 7 by button"
    assert cb(Ev("agent"))["message"] == "set to 7 by the satellite"


async def test_agent_log_becomes_logbook_entry(hass):
    entries = []
    hass.bus.async_listen(logbook.EVENT_LOGBOOK_ENTRY, lambda e: entries.append(e.data))
    agent = FakeAgent(hello(CAPS, STATE))
    entry = await setup(hass, agent)
    await entities(hass, 3)
    await agent.sockets[0].send_json({"type": "log", "message": "Timer done", "data": {}})
    await wait_for(lambda: entries)
    assert entries[0]["message"] == "Timer done" and entries[0]["domain"] == "friday"
    assert entries[0]["entity_id"].startswith("sensor.friday_dev1_")
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_activation_event_updates_event_entity(hass):
    agent = FakeAgent(hello(CAPS_EV, STATE))
    entry = await setup(hass, agent)
    await entities(hass, 4)
    await agent.sockets[0].send_json(
        {"type": "event", "key": "wake", "event_type": "activated", "data": {"word": "friday"}})
    await wait_for(lambda: hass.states.get("event.friday_dev1_wake").attributes.get("event_type") == "activated")
    assert hass.states.get("event.friday_dev1_wake").attributes["word"] == "friday"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()
