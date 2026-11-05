import sys
from ipaddress import ip_address
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
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
    assert sub.unique_id == "sat-1"

    # already configured -> abort
    r = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=info
    )
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_second_user_flow_aborts_single_instance(hass, monkeypatch):
    await _entry_with_noop(hass, monkeypatch)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "single_instance_allowed"


# ---- adoption of a pairable satellite with the spoken code (the satellite side is the real agent module)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))


@pytest.fixture
async def fake_satellite(aiohttp_server, monkeypatch):
    from friday_satellite_agent import pairing as agent_pairing

    from custom_components.friday import config_flow

    session = agent_pairing.PairingSession(code="4821", max_attempts=3)
    received = []

    async def on_paired(token):
        received.append(token)

    app = web.Application()
    agent_pairing.add_routes(app, session, "sat-abc123", on_paired)
    server = await aiohttp_server(app)
    client = aiohttp.ClientSession()
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda hass: client)
    yield server, session, received
    await client.close()


def _info(server, state="pairable"):
    props = {"id": "sat-abc123", "kind": "satellite", "port": str(server.port)}
    if state:
        props["state"] = state
    return ZeroconfInfo(server, props)


def ZeroconfInfo(server, props):
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

    return ZeroconfServiceInfo(
        ip_address=ip_address(server.host), ip_addresses=[ip_address(server.host)], port=server.port,
        hostname="sat.local.", type="_friday._tcp.local.", name="sat-abc123._friday._tcp.local.", properties=props)


async def test_pairable_discovery_right_code_creates_subentry_with_issued_token(hass, monkeypatch, fake_satellite):
    server, session, received = fake_satellite
    entry = await _entry_with_noop(hass, monkeypatch)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server))
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "pair"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "4821"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "satellite_added"
    await hass.async_block_till_done()
    sub = next(iter(entry.subentries.values()))
    assert sub.unique_id == "sat-abc123" and sub.title == "Satellite sat-abc123"
    assert sub.data["token"] == received[0] and len(sub.data["token"]) >= 32  # HA generated it; the satellite got it
    assert sub.data["provisioned"] is False
    assert sub.data["host"] == server.host and sub.data["port"] == server.port


async def test_pairable_wrong_code_shows_error_and_creates_nothing(hass, monkeypatch, fake_satellite):
    server, session, received = fake_satellite
    entry = await _entry_with_noop(hass, monkeypatch)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server))
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "0000"})
    assert r["type"] == FlowResultType.FORM and r["errors"] == {"base": "bad_code"}
    assert r["description_placeholders"]["attempts_left"] == "2"
    assert not entry.subentries and received == []
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "4821"})  # the right one still works
    assert r["reason"] == "satellite_added" and received


async def test_pairable_locked_after_too_many_errors(hass, monkeypatch, fake_satellite):
    server, session, received = fake_satellite
    entry = await _entry_with_noop(hass, monkeypatch)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server))
    for _ in range(2):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "1111"})
        assert r["errors"] == {"base": "bad_code"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "1111"})
    assert r["errors"] == {"base": "locked"}
    assert not entry.subentries and session.code != "4821"


async def test_pairable_unreachable_satellite(hass, monkeypatch, aiohttp_server):
    from custom_components.friday import config_flow

    entry = await _entry_with_noop(hass, monkeypatch)
    server = await aiohttp_server(web.Application())  # nothing under /v1/pair -> 404
    client = aiohttp.ClientSession()
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda hass: client)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server))
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "4821"})
    assert r["errors"] == {"base": "cannot_connect"} and not entry.subentries
    await client.close()


async def test_pairable_code_must_be_four_digits(hass, monkeypatch, fake_satellite):
    from homeassistant.data_entry_flow import InvalidData

    server, *_ = fake_satellite
    await _entry_with_noop(hass, monkeypatch)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server))
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "12"})


async def test_paired_and_legacy_discovery_keep_the_token_flow(hass, monkeypatch, fake_satellite):
    server, *_ = fake_satellite
    entry = await _entry_with_noop(hass, monkeypatch)
    for state in ("paired", None):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server, state))
        assert r["type"] == FlowResultType.FORM and r["step_id"] == "zeroconf_confirm"
        hass.config_entries.flow.async_abort(r["flow_id"])
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server, None))
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"token": "pasted"})
    assert r["reason"] == "satellite_added"
    sub = next(iter(entry.subentries.values()))
    assert sub.data["token"] == "pasted" and "provisioned" not in sub.data


async def test_known_satellite_announcing_pairable_is_readopted_with_new_token(hass, monkeypatch, fake_satellite):
    server, session, received = fake_satellite
    entry = await _entry_with_noop(hass, monkeypatch)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server, None))
    await hass.config_entries.flow.async_configure(r["flow_id"], {"token": "old", "area_id": "sala"})
    await hass.async_block_till_done()
    sub_id = next(iter(entry.subentries))
    # the satellite forgot its home: it announces pairable again
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "zeroconf"}, data=_info(server))
    assert r["step_id"] == "pair"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"code": "4821"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert list(entry.subentries) == [sub_id]
    sub = entry.subentries[sub_id]
    assert sub.data["token"] == received[0] != "old" and sub.data["area_id"] == "sala"
