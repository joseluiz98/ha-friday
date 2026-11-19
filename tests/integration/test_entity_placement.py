"""Entity placement on the device page (spec 2026-11-17-diagnostic-sensors; rule in docs/protocol.md)."""

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday import RETIRED_KEYS
from custom_components.friday.const import DOMAIN

from tests.agents.test_entity_placement import SATELLITE_MAIN, SERVER_MAIN, all_descriptors

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401
from .test_proxmox import CFG as PROXMOX_CFG

CPU_TEMP = {"key": "cpu_temperature", "platform": "sensor", "name": "CPU temperature", "unit": "°C",
            "device_class": "temperature", "state_class": "measurement", "category": "diagnostic"}
ROOM_T = {"key": "room_temperature", "platform": "sensor", "name": "Room temperature"}


def _hello(kind, did, caps, state=None):
    h = hello_msg()
    h["device"] = {"id": did, "kind": kind, "name": did}
    h["capabilities"] = caps
    h["state"] = state or {}
    return h


def _sub(agent, kind, sub_id, extra=None):
    return ConfigSubentryData(data={"host": "127.0.0.1", "port": agent.port, "token": "t", **(extra or {})},
                              subentry_type=kind, title=sub_id, unique_id=None, subentry_id=sub_id)


async def _start(hass, entry, agents):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: len(entry.runtime_data) == len(agents)
                   and all(c.hello is not None for c in entry.runtime_data.values()))
    await hass.async_block_till_done()


async def _stop(hass, entry, agents):
    await hass.config_entries.async_unload(entry.entry_id)
    for agent in agents:
        await agent.stop()


async def test_existing_entity_moves_to_diagnostic(hass):
    """HA updates the category of an already registered entity from the platform: no migration needed."""
    agent = FakeAgent(_hello("satellite", "sat1", [CPU_TEMP], {"cpu_temperature": 50.0}))
    await agent.start()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[_sub(agent, "satellite", "s0")])
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    before = reg.async_get_or_create("sensor", DOMAIN, "sat1_cpu_temperature", config_entry=entry,
                                     suggested_object_id="friday_sat1_cpu_temperature", entity_category=None)
    assert before.entity_category is None
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data["s0"].hello is not None)
    await hass.async_block_till_done()
    after = reg.async_get(before.entity_id)
    assert after.entity_id == "sensor.friday_sat1_cpu_temperature"
    assert after.entity_category is EntityCategory.DIAGNOSTIC
    await _stop(hass, entry, [agent])


async def test_integration_built_entities_placement(hass):
    sat = FakeAgent(_hello("satellite", "sat1", [ROOM_T]))
    server = FakeAgent(_hello("server", "server", []))
    await sat.start()
    await server.start()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[
        _sub(sat, "satellite", "s0"), _sub(server, "server", "sub1", PROXMOX_CFG)])
    await _start(hass, entry, [sat, server])
    reg = er.async_get(hass)

    def category(domain, uid):
        ent = reg.async_get(reg.async_get_entity_id(domain, DOMAIN, uid))
        assert ent is not None, uid
        return ent.entity_category

    assert category("button", "sub1_reboot_vm") is EntityCategory.DIAGNOSTIC
    assert category("binary_sensor", "server_restart_pending") is EntityCategory.DIAGNOSTIC
    assert category("sensor", "sat1_next_reminder") is None
    assert category("event", "sat1_reminder") is None
    assert category("sensor", "sat1_room_temperature") is None
    await _stop(hass, entry, [sat, server])


def test_no_live_key_ends_with_a_retired_key(monkeypatch):
    """_remove_retired_entities compares by the END of the unique_id: a live key must not collide."""
    live = set(SATELLITE_MAIN) | set(SERVER_MAIN) | {"next_reminder", "reminder", "restart_pending", "reboot_vm"}
    for kind in ("satellite", "server"):
        live |= {d["key"] for d in all_descriptors(kind, monkeypatch) if not d.get("hidden")}
    clashes = sorted(k for k in live for r in RETIRED_KEYS if k == r or k.endswith(f"_{r}"))
    assert clashes == []


def test_memory_index_sensor_is_retired():
    assert "memory_index_last_success" in RETIRED_KEYS


def test_wakeword_sync_sensor_is_retired():
    """18/11: the server's office-only upload stamp left; each satellite reports its own upload."""
    assert "wakeword_sync_last_ok" in RETIRED_KEYS
