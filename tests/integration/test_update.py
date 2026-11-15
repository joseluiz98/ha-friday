"""Generic update platform: the OTA entities of the satellite and the server."""

import pytest

from homeassistant.components.update import UpdateEntityFeature
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.config_entries import ConfigSubentryData

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

DESC = {"key": "ota", "platform": "update", "name": "Software"}
EID = "update.friday_sat1_ota"


def _state(**over):
    base = {"installed": "v0.2.1", "latest": "v0.3.0", "in_progress": False,
            "title": "F.R.I.D.A.Y. satellite", "release_url": None, "summary": None}
    base.update(over)
    return base


def _hello(state):
    h = hello_msg()
    h["device"] = {"id": "sat1", "kind": "satellite", "name": "sat1"}
    h["capabilities"] = [DESC]
    h["state"] = state
    return h


async def _setup(hass, state, on_command=None):
    agent = FakeAgent(_hello(state), on_command)
    await agent.start()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[
        ConfigSubentryData(data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
                           subentry_type="satellite", title="sat", unique_id=None, subentry_id="s0")])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data["s0"].hello is not None)
    await hass.async_block_till_done()
    return agent, entry


async def _stop(hass, entry, agent):
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_update_entity_shows_versions(hass):
    agent, entry = await _setup(hass, {"ota": _state()})
    st = hass.states.get(EID)
    assert st.state == "on"
    assert st.attributes["installed_version"] == "v0.2.1"
    assert st.attributes["latest_version"] == "v0.3.0"
    assert st.attributes["title"] == "F.R.I.D.A.Y. satellite"
    assert st.attributes["device_class"] == "firmware"
    assert st.attributes["supported_features"] == (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS | UpdateEntityFeature.RELEASE_NOTES)
    await _stop(hass, entry, agent)


async def test_up_to_date_is_off(hass):
    agent, entry = await _setup(hass, {"ota": _state(latest="v0.2.1")})
    assert hass.states.get(EID).state == "off"
    await _stop(hass, entry, agent)


async def test_install_sends_ota_install(hass):
    got = []

    async def on_command(ws, cmd):
        got.append(cmd)
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    agent, entry = await _setup(hass, {"ota": _state()}, on_command)
    await hass.services.async_call("update", "install", {"entity_id": EID}, blocking=True)
    assert [(c["name"], c["key"]) for c in got] == [("ota_install", "ota")]
    await _stop(hass, entry, agent)


async def test_install_error_propagates(hass):
    async def on_command(ws, cmd):
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": False, "error": "in_progress"})

    agent, entry = await _setup(hass, {"ota": _state()}, on_command)
    with pytest.raises(HomeAssistantError, match="in_progress"):
        await hass.services.async_call("update", "install", {"entity_id": EID}, blocking=True)
    await _stop(hass, entry, agent)


async def test_install_other_version_refused(hass):
    got = []

    async def on_command(ws, cmd):
        got.append(cmd)
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    agent, entry = await _setup(hass, {"ota": _state()}, on_command)
    # HA itself refuses a specific version (no SPECIFIC_VERSION feature)...
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "update", "install", {"entity_id": EID, "version": "v0.1.0"}, blocking=True)
    # ...and the entity does too if it is asked directly
    entity = hass.data["entity_components"]["update"].get_entity(EID)
    with pytest.raises(HomeAssistantError, match="only_latest"):
        await entity.async_install("v0.1.0", False)
    assert got == []
    await entity.async_install("v0.3.0", False)
    assert [c["name"] for c in got] == ["ota_install"]
    await _stop(hass, entry, agent)


async def test_in_progress_reflects_state(hass):
    agent, entry = await _setup(hass, {"ota": _state(in_progress=True)})
    assert hass.states.get(EID).attributes["in_progress"] is True
    await agent.sockets[0].send_json({
        "type": "state", "changes": {"ota": _state(in_progress=False)}, "origin": "agent"})
    await wait_for(lambda: hass.states.get(EID).attributes["in_progress"] is False)
    await _stop(hass, entry, agent)


@pytest.mark.parametrize("state", [{}, {"ota": None}, {"ota": "garbage"}, {"ota": {}}])
async def test_missing_state_is_unknown(hass, state):
    agent, entry = await _setup(hass, state)
    st = hass.states.get(EID)
    assert st.state == "unknown"
    assert st.attributes["installed_version"] is None
    assert st.attributes["in_progress"] is False
    await _stop(hass, entry, agent)


async def test_release_notes_full_text(hass, hass_ws_client):
    long = "x" * 300
    agent, entry = await _setup(hass, {"ota": _state(summary=long, release_url="https://example.test/r")})
    st = hass.states.get(EID)
    assert st.attributes["release_summary"] == long[:255]
    assert st.attributes["release_url"] == "https://example.test/r"
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "update/release_notes", "entity_id": EID})
    msg = await ws.receive_json()
    assert msg["success"] and msg["result"] == long
    await _stop(hass, entry, agent)


async def test_release_notes_absent_is_none(hass, hass_ws_client):
    agent, entry = await _setup(hass, {"ota": _state()})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "update/release_notes", "entity_id": EID})
    msg = await ws.receive_json()
    assert msg["success"] and msg["result"] is None
    await _stop(hass, entry, agent)


async def test_state_changes_are_not_logged_as_changes(hass):
    """Progress and version pushes only mirror a reading: no friday_change event."""
    events = []
    hass.bus.async_listen("friday_change", lambda e: events.append(e))
    agent, entry = await _setup(hass, {"ota": _state()})
    await agent.sockets[0].send_json({
        "type": "state", "changes": {"ota": _state(in_progress=True)}, "origin": "agent"})
    await wait_for(lambda: hass.states.get(EID).attributes["in_progress"] is True)
    await hass.async_block_till_done()
    assert events == []
    await _stop(hass, entry, agent)
