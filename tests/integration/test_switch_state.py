"""Switch state when the agent has no value (spec 2026-11-17-diagnostic-sensors, "Liga/desliga que vira dois raios").

The Lovelace toggle draws two lightning-bolt buttons for state `unknown` or `assumed_state`
(frontend 20260930.5, src/components/entity/ha-entity-toggle.ts, render(): `assumed_state || state === UNKNOWN`),
and a greyed-out regular switch for `unavailable`. A switch with no value must therefore be `unavailable`.
"""

from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

MUTE = {"key": "mute", "platform": "switch", "name": "Mute"}
VOICE_ENABLED = {"key": "voice_enabled", "platform": "switch", "name": "Voice Program"}
SERVER_VOICE = {"key": "voice", "platform": "switch", "name": "Voice"}
SERVER_OTA = {"key": "ota_auto_install", "platform": "switch", "name": "Auto install updates"}


def _hello(kind, did, caps, state=None):
    h = hello_msg()
    h["device"] = {"id": did, "kind": kind, "name": did}
    h["capabilities"] = caps
    h["state"] = state or {}
    return h


async def _setup(hass, agent, kind):
    await agent.start()
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, title="F",
        subentries_data=[ConfigSubentryData(
            data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
            subentry_type=kind, title="s0", unique_id=None, subentry_id="s0")])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data["s0"].hello is not None)
    await hass.async_block_till_done()
    return entry


async def _stop(hass, entry, agent):
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_switch_without_value_is_unknown_and_available(hass):
    """Documents the pre-0.3.8 behavior (the cause of the two bolts); Task 10 turns it into `unavailable`."""
    agent = FakeAgent(_hello("satellite", "sat1", [MUTE]))
    entry = await _setup(hass, agent, "satellite")
    st = hass.states.get("switch.friday_sat1_mute")
    assert st.state == "unknown" and "assumed_state" not in st.attributes
    await _stop(hass, entry, agent)


async def test_switch_with_value_is_on(hass):
    agent = FakeAgent(_hello("satellite", "sat1", [MUTE], {"mute": True}))
    entry = await _setup(hass, agent, "satellite")
    st = hass.states.get("switch.friday_sat1_mute")
    assert st.state == "on" and "assumed_state" not in st.attributes
    await _stop(hass, entry, agent)


async def test_voice_enabled_has_its_own_value(hass):
    """Mirrors 17/11: `voice_enabled` is on while the program's own switches have no value."""
    agent = FakeAgent(_hello("satellite", "sat1", [MUTE, VOICE_ENABLED], {"voice_enabled": True}))
    entry = await _setup(hass, agent, "satellite")
    assert hass.states.get("switch.friday_sat1_voice_enabled").state == "on"
    assert hass.states.get("switch.friday_sat1_mute").state == "unknown"
    await _stop(hass, entry, agent)


async def test_server_switches_without_value(hass):
    agent = FakeAgent(_hello("server", "server", [SERVER_VOICE, SERVER_OTA], {"ota_auto_install": False}))
    entry = await _setup(hass, agent, "server")
    assert hass.states.get("switch.friday_server_voice").state == "unknown"
    assert hass.states.get("switch.friday_server_ota_auto_install").state == "off"
    await _stop(hass, entry, agent)
