"""friday.set_wifi routing and the Change Wi-Fi reconfigure step (the password is never stored)."""

import json
import logging

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

PASSWORD = "s3cr3t-pass-123"
WIFI_CAP = {"key": "wifi_set", "platform": "button", "name": "Change Wi-Fi", "hidden": True, "available": False}
SENSOR = {"key": "a", "platform": "sensor", "name": "A"}


def _hello(kind, dev_id, caps):
    h = hello_msg()
    h["device"] = {"id": dev_id, "kind": kind, "name": dev_id}
    h["capabilities"] = caps
    return h


async def _setup(hass, agents):
    subs = []
    for i, (kind, agent) in enumerate(agents):
        await agent.start()
        subs.append(ConfigSubentryData(
            data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
            subentry_type=kind, title=kind, unique_id=None, subentry_id=f"s{i}"))
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=subs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    for i in range(len(agents)):
        await wait_for(lambda i=i: entry.runtime_data[f"s{i}"].hello is not None)
    await hass.async_block_till_done()
    return entry


def _device_id(hass, entry, dev_id):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, dev_id)}).id


async def test_set_wifi_service_routes_to_device(hass):
    got_sat1, got_sat2 = [], []

    def handler(sink, answer):
        async def on_command(ws, cmd):
            sink.append(cmd)
            await ws.send_json({"type": "result", "id": cmd["id"], **answer})
        return on_command

    sat1 = FakeAgent(_hello("satellite", "sat1", [SENSOR, WIFI_CAP]), handler(got_sat1, {"ok": True}))
    sat2 = FakeAgent(_hello("satellite", "sat2", [SENSOR, WIFI_CAP]), handler(got_sat2, {"ok": True}))
    entry = await _setup(hass, [("satellite", sat1), ("satellite", sat2)])
    await hass.services.async_call(
        DOMAIN, "set_wifi",
        {"device_id": _device_id(hass, entry, "sat2"), "ssid": "Casa", "password": PASSWORD}, blocking=True)
    assert got_sat1 == []
    assert got_sat2[0]["name"] == "wifi_set" and got_sat2[0]["args"] == {"ssid": "Casa", "password": PASSWORD}
    await hass.config_entries.async_unload(entry.entry_id)
    await sat1.stop()
    await sat2.stop()


async def test_set_wifi_error_is_reported_without_the_password(hass, caplog):
    caplog.set_level(logging.DEBUG)

    async def on_command(ws, cmd):
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": False, "error": "invalid"})

    sat = FakeAgent(_hello("satellite", "sat1", [SENSOR, WIFI_CAP]), on_command)
    entry = await _setup(hass, [("satellite", sat)])
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, "set_wifi",
            {"device_id": _device_id(hass, entry, "sat1"), "ssid": "Casa", "password": PASSWORD}, blocking=True)
    assert "invalid" in str(err.value) and PASSWORD not in str(err.value)
    assert PASSWORD not in caplog.text
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_set_wifi_refuses_server_and_agents_without_nmcli(hass):
    server = FakeAgent(_hello("server", "srv", [SENSOR, WIFI_CAP]))
    sat = FakeAgent(_hello("satellite", "sat1", [SENSOR]))  # no wifi_set capability: no nmcli
    entry = await _setup(hass, [("server", server), ("satellite", sat)])
    for dev in ("srv", "sat1"):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "set_wifi",
                {"device_id": _device_id(hass, entry, dev), "ssid": "Casa", "password": PASSWORD}, blocking=True)
    await hass.config_entries.async_unload(entry.entry_id)
    await server.stop()
    await sat.stop()


async def test_reconfigure_change_wifi_does_not_store_password(hass):
    got = []

    async def on_command(ws, cmd):
        got.append(cmd)
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {"status": "started"}})

    sat = FakeAgent(_hello("satellite", "sat1", [SENSOR, WIFI_CAP]), on_command)
    entry = await _setup(hass, [("satellite", sat)])
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "satellite"), context={"source": "reconfigure", "subentry_id": "s0"})
    assert r["type"] == FlowResultType.MENU and r["menu_options"] == ["connection", "wifi", "room_sensors", "advanced"]
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "wifi"})
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "wifi"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"ssid": "Casa", "password": PASSWORD})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "wifi_changed"
    assert got[0]["args"] == {"ssid": "Casa", "password": PASSWORD}
    assert PASSWORD not in json.dumps(dict(entry.subentries["s0"].data)) and "Casa" not in json.dumps(
        dict(entry.subentries["s0"].data))
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_reconfigure_change_wifi_failure_shows_error(hass):
    async def on_command(ws, cmd):
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": False, "error": "invalid"})

    sat = FakeAgent(_hello("satellite", "sat1", [SENSOR, WIFI_CAP]), on_command)
    entry = await _setup(hass, [("satellite", sat)])
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "satellite"), context={"source": "reconfigure", "subentry_id": "s0"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "wifi"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"ssid": "Casa", "password": PASSWORD})
    assert r["type"] == FlowResultType.FORM and r["errors"] == {"base": "wifi_failed"}
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_reconfigure_connection_step_still_updates_satellite(hass):
    sat = FakeAgent(_hello("satellite", "sat1", [SENSOR]))
    entry = await _setup(hass, [("satellite", sat)])
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "satellite"), context={"source": "reconfigure", "subentry_id": "s0"})
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "connection"})
    assert r["step_id"] == "connection"
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], {"host": "127.0.0.1", "port": sat.port})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    assert entry.subentries["s0"].data["token"] == "t"
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()
