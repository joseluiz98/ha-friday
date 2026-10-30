from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN


async def test_user_flow_creates_single_entry(hass):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] == FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] == FlowResultType.CREATE_ENTRY
    assert r["title"] == "F.R.I.D.A.Y."
    r2 = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r2["type"] == FlowResultType.ABORT
    assert r2["reason"] == "single_instance_allowed"


async def test_server_subentry_flow_creates_subentry(hass, monkeypatch):
    # Don't connect anywhere: the flow is what is under test.
    from custom_components.friday import connection as c

    async def noop(self):
        return None

    monkeypatch.setattr(c.FridayConnection, "start", noop)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F.R.I.D.A.Y.")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "server"), context={"source": "user"}
    )
    assert r["type"] == FlowResultType.FORM
    r = await hass.config_entries.subentries.async_configure(
        r["flow_id"], {"host": "10.0.0.5", "port": 8801, "token": "t0k"}
    )
    assert r["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(iter(entry.subentries.values()))
    assert sub.subentry_type == "server"
    assert sub.data == {"host": "10.0.0.5", "port": 8801, "token": "t0k"}


async def _entry_with_noop(hass, monkeypatch):
    from custom_components.friday import connection as c

    async def noop(self):
        return None

    monkeypatch.setattr(c.FridayConnection, "start", noop)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F.R.I.D.A.Y.")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def test_satellite_subentry_sets_area(hass, monkeypatch):
    from homeassistant.helpers import area_registry as ar, device_registry as dr

    entry = await _entry_with_noop(hass, monkeypatch)
    area = ar.async_get(hass).async_create("Office")
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "satellite"), context={"source": "user"}
    )
    assert r["type"] == FlowResultType.FORM
    r = await hass.config_entries.subentries.async_configure(
        r["flow_id"],
        {"host": "10.0.0.9", "port": 8701, "token": "t0k", "area_id": area.id},
    )
    assert r["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(iter(entry.subentries.values()))
    assert sub.subentry_type == "satellite"
    assert sub.data["area_id"] == area.id

    # the device gets the area once it exists (created by the entities; here directly)
    from custom_components.friday import apply_satellite_area

    reg = dr.async_get(hass)
    dev = reg.async_get_or_create(
        config_entry_id=entry.entry_id, config_subentry_id=sub.subentry_id,
        identifiers={(DOMAIN, "sat-1")},
    )
    apply_satellite_area(hass, entry, sub.subentry_id, "sat-1")
    assert reg.async_get(dev.id).area_id == area.id


async def test_zeroconf_discovery_offers_satellite(hass, monkeypatch):
    from ipaddress import ip_address

    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

    entry = await _entry_with_noop(hass, monkeypatch)
    info = ZeroconfServiceInfo(
        ip_address=ip_address("10.0.0.9"), ip_addresses=[ip_address("10.0.0.9")], port=8701,
        hostname="sat.local.", type="_friday._tcp.local.", name="sat-1._friday._tcp.local.",
        properties={"id": "sat-1", "kind": "satellite", "port": "8701"},
    )
    r = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=info
    )
    assert r["type"] == FlowResultType.FORM
    assert r["step_id"] == "zeroconf_confirm"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"token": "t0k"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "satellite_added"
    await hass.async_block_till_done()
    sub = next(iter(entry.subentries.values()))
    assert sub.subentry_type == "satellite"
    assert sub.data["host"] == "10.0.0.9" and sub.data["port"] == 8701

    # already configured -> abort
    r = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=info
    )
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "already_configured"
