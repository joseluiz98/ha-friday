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
