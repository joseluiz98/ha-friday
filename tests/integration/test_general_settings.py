"""General settings: stored in the entry options, applied to every satellite, never reloading the entry."""

import asyncio

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN
from custom_components.friday.general import async_apply_general

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

HIDDEN = [
    {"key": "end_of_speech_pause", "platform": "number", "name": "Pause", "min": 0.5, "max": 2.0, "step": 0.1,
     "hidden": True},
    {"key": "follow_up_window", "platform": "number", "name": "Window", "min": 0, "max": 30, "step": 1,
     "hidden": True},
    {"key": "reply_wait", "platform": "number", "name": "Wait", "min": 2, "max": 30, "step": 1, "hidden": True},
]
GENERAL = {"end_of_speech_pause": 1.2, "follow_up_window": 7}


def sat_hello(dev_id, state, caps=None):
    h = hello_msg()
    h["device"] = {"id": dev_id, "kind": "satellite", "name": dev_id}
    h["capabilities"] = list(HIDDEN if caps is None else caps) + [
        {"key": "vol", "platform": "number", "name": "Volume", "min": 0, "max": 10, "step": 1}]
    h["state"] = state
    return h


def recorder(got, answers=None):
    async def on_command(ws, cmd):
        got.append((cmd["key"], cmd["value"]))
        answer = (answers or {}).get(cmd["key"], {"ok": True})
        await ws.send_json({"type": "result", "id": cmd["id"], **answer})
    return on_command


async def start_entry(hass, agents, options=None):
    subs = []
    for i, agent in enumerate(agents):
        if agent.runner is None:
            await agent.start()
        subs.append(ConfigSubentryData(
            data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
            subentry_type="satellite", title=f"s{i}", unique_id=None, subentry_id=f"s{i}"))
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=subs,
                            options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def connected(entry, n):
    await wait_for(lambda: len(entry.runtime_data) == n and all(c.connected for c in entry.runtime_data.values()))


async def stop(hass, entry, agents):
    await hass.config_entries.async_unload(entry.entry_id)
    for agent in agents:
        await agent.stop()


def logbook_messages(hass):
    seen = []
    hass.bus.async_listen("logbook_entry", lambda e: seen.append(e.data.get("message", "")))
    return seen


async def test_nothing_applied_without_general_options(hass):
    got = []
    agent = FakeAgent(sat_hello("sat1", {"follow_up_window": 5, "end_of_speech_pause": 0.8}), recorder(got))
    entry = await start_entry(hass, [agent])
    await connected(entry, 1)
    await hass.async_block_till_done()
    await asyncio.sleep(0.2)
    assert got == []
    await stop(hass, entry, [agent])


async def test_general_applied_on_connect_and_skips_follow_up_off(hass):
    got1, got2 = [], []
    sat1 = FakeAgent(sat_hello("sat1", {"end_of_speech_pause": 0.8, "follow_up_window": 5}), recorder(got1))
    sat2 = FakeAgent(sat_hello("sat2", {"end_of_speech_pause": 0.8, "follow_up_window": 0}), recorder(got2))
    entry = await start_entry(hass, [sat1, sat2], {"general": GENERAL})
    await connected(entry, 2)
    await wait_for(lambda: len(got1) == 2 and len(got2) == 1)
    await asyncio.sleep(0.2)
    assert sorted(got1) == [("end_of_speech_pause", 1.2), ("follow_up_window", 7)]
    assert got2 == [("end_of_speech_pause", 1.2)]
    await stop(hass, entry, [sat1, sat2])


async def test_follow_up_turned_on_gets_general_duration(hass):
    got = []
    sat = FakeAgent(sat_hello("sat2", {"end_of_speech_pause": 1.2, "follow_up_window": 0}), recorder(got))
    entry = await start_entry(hass, [sat], {"general": GENERAL})
    await connected(entry, 1)
    await asyncio.sleep(0.3)
    assert got == []
    await sat.sockets[0].send_json({"type": "state", "changes": {"follow_up_window": 5}, "origin": "voice"})
    await wait_for(lambda: got == [("follow_up_window", 7)])
    await stop(hass, entry, [sat])


async def test_offline_satellite_gets_general_when_it_connects(hass):
    got = []
    sat = FakeAgent(sat_hello("sat1", {"end_of_speech_pause": 0.8, "follow_up_window": 5}), recorder(got))
    await sat.start()
    await sat.stop()  # keeps the port; the satellite is "off"
    entry = await start_entry(hass, [sat], {"general": GENERAL})
    await asyncio.sleep(0.3)
    assert not entry.runtime_data["s0"].connected and got == []
    await sat.start()
    await connected(entry, 1)
    await wait_for(lambda: len(got) == 2)
    assert sorted(got) == [("end_of_speech_pause", 1.2), ("follow_up_window", 7)]
    await stop(hass, entry, [sat])


async def test_options_change_does_not_reload(hass):
    sat = FakeAgent(sat_hello("sat1", {"end_of_speech_pause": 1.2, "follow_up_window": 7}), recorder([]))
    entry = await start_entry(hass, [sat])
    await connected(entry, 1)
    conn = entry.runtime_data["s0"]
    hass.config_entries.async_update_entry(entry, options={"general": {"reply_wait": 9}})
    await hass.async_block_till_done()
    await asyncio.sleep(0.2)
    assert entry.runtime_data["s0"] is conn and conn.connected and sat.connections == 1
    # subentry data changes still reload
    sub = entry.subentries["s0"]
    hass.config_entries.async_update_subentry(entry, sub, data={**sub.data, "extra": 1})
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data["s0"] is not conn)
    await stop(hass, entry, [sat])


async def test_retired_entities_removed(hass):
    sat = FakeAgent(sat_hello("sat1", {}), recorder([]))
    await sat.start()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[ConfigSubentryData(
        data={"host": "127.0.0.1", "port": sat.port, "token": "t"},
        subentry_type="satellite", title="s", unique_id=None, subentry_id="s0")])
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    for domain, uid in (("number", "sat1_reply_wait"), ("select", "sat1_wakeword_friday_mode"),
                        ("number", "sat1_wakeword_sexta_feira_sensitivity"), ("switch", "sat1_ducking"),
                        ("number", "sat1_vol")):
        reg.async_get_or_create(domain, DOMAIN, uid, config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await connected(entry, 1)
    await hass.async_block_till_done()
    left = {e.unique_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert "sat1_vol" in left and "sat1_ducking" in left  # 11/11: ducking is back on the device page
    assert not left & {"sat1_reply_wait", "sat1_wakeword_friday_mode", "sat1_wakeword_sexta_feira_sensitivity"}
    await stop(hass, entry, [sat])


async def test_apply_error_is_logged_and_others_continue(hass):
    got = []
    answers = {"end_of_speech_pause": {"ok": False, "error": "rejected"}}
    sat = FakeAgent(sat_hello("sat1", {"end_of_speech_pause": 0.8, "follow_up_window": 5}), recorder(got, answers))
    seen = logbook_messages(hass)
    entry = await start_entry(hass, [sat], {"general": GENERAL})
    await connected(entry, 1)
    await wait_for(lambda: len(got) == 2)
    result = await async_apply_general(hass, entry, entry.runtime_data["s0"])
    assert result == {"end_of_speech_pause": "rejected", "follow_up_window": "applied"}
    assert any("end_of_speech_pause" in m and "rejected" in m for m in seen)
    await stop(hass, entry, [sat])


async def test_unsupported_general_key_logged_once(hass):
    got = []
    sat = FakeAgent(sat_hello("sat1", {"end_of_speech_pause": 0.8}, caps=[HIDDEN[0]]), recorder(got))
    seen = logbook_messages(hass)
    entry = await start_entry(hass, [sat], {"general": {"reply_wait": 9}})
    await connected(entry, 1)
    await wait_for(lambda: any("reply_wait not supported" in m for m in seen))
    conn = entry.runtime_data["s0"]
    assert await async_apply_general(hass, entry, conn) == {}
    assert await async_apply_general(hass, entry, conn) == {}
    await hass.async_block_till_done()
    assert got == [] and sum("reply_wait not supported" in m for m in seen) == 1
    await stop(hass, entry, [sat])
