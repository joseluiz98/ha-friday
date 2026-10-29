"""Reboot VM via the Proxmox API, the restart chain and the reconfigure step."""

import pytest

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.config_entries import ConfigSubentryData

from custom_components.friday.const import DOMAIN
from custom_components.friday.proxmox import reboot_vm

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

CFG = {"proxmox_url": "https://pve:8006", "proxmox_node": "hypervisor", "proxmox_vmid": 101,
       "proxmox_token_id": "ha@pve!friday-reboot", "proxmox_token_secret": "s3cr3t"}
URL = "https://pve:8006/api2/json/nodes/hypervisor/qemu/101/status/reboot"


class Conn:
    def __init__(self, connected, state):
        self.connected, self.state = connected, state


async def test_reboot_vm_refused_when_conversation_open(hass, aioclient_mock):
    aioclient_mock.post(URL, json={})
    with pytest.raises(HomeAssistantError, match="conversation_open"):
        await reboot_vm(hass, CFG, Conn(True, {"conversation_open": True}))
    assert aioclient_mock.call_count == 0


async def test_reboot_vm_calls_proxmox_when_server_down(hass, aioclient_mock):
    aioclient_mock.post(URL, json={"data": "UPID"})
    # server down: stale conversation_open is ignored
    await reboot_vm(hass, CFG, Conn(False, {"conversation_open": True}))
    assert aioclient_mock.call_count == 1
    method, url, _, headers = aioclient_mock.mock_calls[0]
    assert method.lower() == "post" and str(url) == URL
    assert headers["Authorization"] == "PVEAPIToken=ha@pve!friday-reboot=s3cr3t"


async def test_reboot_vm_proxmox_error(hass, aioclient_mock):
    aioclient_mock.post(URL, status=403)
    with pytest.raises(HomeAssistantError, match="proxmox_error_403"):
        await reboot_vm(hass, CFG, None)


async def _setup(hass, agent, data):
    await agent.start()
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, title="F.R.I.D.A.Y.",
        subentries_data=[ConfigSubentryData(
            data={"host": "127.0.0.1", "port": agent.port, "token": "t", **data},
            subentry_type="server", title="Server", unique_id=None, subentry_id="sub1")])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


def _hello(kind, did, caps):
    h = hello_msg()
    h["device"] = {"id": did, "kind": kind, "name": did}
    h["capabilities"] = caps
    h["state"] = {}
    return h


async def test_reboot_button_only_when_configured(hass):
    agent = FakeAgent(_hello("server", "server", []))
    entry = await _setup(hass, agent, {})
    await hass.async_block_till_done()
    assert hass.states.get("button.friday_server_reboot_vm") is None
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_reboot_button_press_posts_and_logs(hass, aioclient_mock):
    aioclient_mock.post(URL, json={})
    agent = FakeAgent(_hello("server", "server", []))
    entry = await _setup(hass, agent, CFG)
    await hass.async_block_till_done()
    assert hass.states.get("button.friday_server_reboot_vm") is not None
    ent = er.async_get(hass).async_get("button.friday_server_reboot_vm")
    assert ent.config_subentry_id == "sub1"
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.friday_server_reboot_vm"}, blocking=True)
    assert aioclient_mock.call_count == 1
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_reboot_button_press_error_raises(hass, aioclient_mock):
    aioclient_mock.post(URL, status=500)
    agent = FakeAgent(_hello("server", "server", []))
    entry = await _setup(hass, agent, CFG)
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError, match="proxmox_error_500"):
        await hass.services.async_call("button", "press",
                                       {"entity_id": "button.friday_server_reboot_vm"}, blocking=True)
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


RESTART = [{"key": "restart_friday", "platform": "button", "name": "Restart F.R.I.D.A.Y.",
            "device_class": "restart"}]


async def test_restart_friday_presses_restart_program_on_satellites(hass):
    got_sat, got_srv = [], []

    async def on_srv(ws, cmd):
        got_srv.append(cmd)
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    async def on_sat(ws, cmd):
        got_sat.append(cmd)
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    srv = FakeAgent(_hello("server", "server", RESTART), on_srv)
    sat = FakeAgent(_hello("satellite", "sat1", [
        {"key": "restart_program", "platform": "button", "name": "r", "hidden": True}]), on_sat)
    await sat.start()
    await srv.start()
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, title="F.R.I.D.A.Y.",
        subentries_data=[
            ConfigSubentryData(data={"host": "127.0.0.1", "port": srv.port, "token": "t"},
                               subentry_type="server", title="S", unique_id=None, subentry_id="s"),
            ConfigSubentryData(data={"host": "127.0.0.1", "port": sat.port, "token": "t"},
                               subentry_type="server", title="Sat", unique_id=None, subentry_id="t")])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: hass.states.get("button.friday_server_restart_friday") is not None)
    await wait_for(lambda: all(c.connected for c in entry.runtime_data.values()))
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.friday_server_restart_friday"}, blocking=True)
    assert [c["key"] for c in got_srv] == ["restart_friday"]
    assert [(c["name"], c["key"]) for c in got_sat] == [("press", "restart_program")]
    await hass.config_entries.async_unload(entry.entry_id)
    await srv.stop()
    await sat.stop()


async def test_restart_friday_refused_does_not_touch_satellites(hass):
    got_sat = []

    async def on_srv(ws, cmd):
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": False,
                            "error": "conversation_open"})

    async def on_sat(ws, cmd):
        got_sat.append(cmd)

    srv = FakeAgent(_hello("server", "server", RESTART), on_srv)
    agent_sat = FakeAgent(_hello("satellite", "sat1", []), on_sat)
    await agent_sat.start()
    entry = await _setup(hass, srv, {})
    await wait_for(lambda: hass.states.get("button.friday_server_restart_friday") is not None)
    with pytest.raises(HomeAssistantError, match="conversation_open"):
        await hass.services.async_call("button", "press",
                                       {"entity_id": "button.friday_server_restart_friday"},
                                       blocking=True)
    assert got_sat == []
    await hass.config_entries.async_unload(entry.entry_id)
    await srv.stop()
    await agent_sat.stop()


async def test_reconfigure_fills_proxmox_fields(hass, monkeypatch):
    from custom_components.friday import connection as c

    async def noop(self):
        return None

    monkeypatch.setattr(c.FridayConnection, "start", noop)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, title="F.R.I.D.A.Y.",
        subentries_data=[ConfigSubentryData(
            data={"host": "10.0.0.5", "port": 8801, "token": "t0k"},
            subentry_type="server", title="Server", unique_id=None, subentry_id="sub1")])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "server"), context={"source": "reconfigure", "subentry_id": "sub1"})
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "reconfigure"
    r = await hass.config_entries.subentries.async_configure(
        r["flow_id"], {"host": "10.0.0.5", "port": 8801, **CFG})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    data = entry.subentries["sub1"].data
    assert data["token"] == "t0k"  # blank token keeps the stored one
    assert data["proxmox_vmid"] == 101 and data["proxmox_token_secret"] == "s3cr3t"
