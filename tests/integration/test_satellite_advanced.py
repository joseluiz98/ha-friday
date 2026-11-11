"""The Advanced step in each satellite's settings: per-satellite engineering values, sent only when changed."""

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

CAPS = [
    {"key": "ducking", "platform": "switch", "name": "D", "hidden": True},
    {"key": "ducking_level", "platform": "number", "name": "L", "min": 1, "max": 25, "step": 1, "hidden": True},
    {"key": "ducking_restore", "platform": "number", "name": "R", "min": 0, "max": 10, "step": 0.5, "hidden": True},
    {"key": "continuous_speech_limit", "platform": "number", "name": "C", "min": 0, "max": 120, "step": 5,
     "hidden": True},
    {"key": "wakeword_friday_mode", "platform": "select", "name": "M", "options": ["active", "shadow", "off"],
     "hidden": True},
    {"key": "wakeword_friday_sensitivity", "platform": "number", "name": "T", "min": 0.05, "max": 0.99,
     "step": 0.01, "hidden": True},
    {"key": "wakeword_sexta_feira_mode", "platform": "select", "name": "M", "options": ["active", "shadow", "off"],
     "hidden": True},
    {"key": "wakeword_sexta_feira_sensitivity", "platform": "number", "name": "T", "min": 0.05, "max": 0.99,
     "step": 0.01, "hidden": True},
    {"key": "vol", "platform": "number", "name": "Volume", "min": 0, "max": 10, "step": 1},
]
STATE = {"ducking": True, "ducking_level": 20, "ducking_restore": 1.0, "continuous_speech_limit": 0,
         "wakeword_friday_mode": "off", "wakeword_friday_sensitivity": 0.5,
         "wakeword_sexta_feira_mode": "active", "wakeword_sexta_feira_sensitivity": 0.35}


def make_sat(got, state=None, answer=None):
    h = hello_msg()
    h["device"] = {"id": "sat1", "kind": "satellite", "name": "sat1"}
    h["capabilities"] = CAPS
    h["state"] = dict(STATE if state is None else state)

    async def on_command(ws, cmd):
        got.append((cmd["key"], cmd["value"]))
        await ws.send_json({"type": "result", "id": cmd["id"], **(answer or {"ok": True})})

    return FakeAgent(h, on_command)


async def setup(hass, sat):
    await sat.start()
    sub = ConfigSubentryData(data={"host": "127.0.0.1", "port": sat.port, "token": "t"},
                             subentry_type="satellite", title="s", unique_id=None, subentry_id="s0")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[sub])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data["s0"].connected)
    return entry


async def open_advanced(hass, entry):
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "satellite"), context={"source": "reconfigure", "subentry_id": "s0"})
    assert r["type"] == FlowResultType.MENU
    return await hass.config_entries.subentries.async_configure(r["flow_id"], {"next_step_id": "advanced"})


def form_values(**changes):
    return {**STATE, **changes}


async def test_menu_has_advanced(hass):
    sat = make_sat([])
    entry = await setup(hass, sat)
    r = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "satellite"), context={"source": "reconfigure", "subentry_id": "s0"})
    assert r["menu_options"] == ["connection", "wifi", "room_sensors", "advanced"]
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_advanced_form_shows_current_values(hass):
    sat = make_sat([])
    entry = await setup(hass, sat)
    r = await open_advanced(hass, entry)
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "advanced"
    suggested = {str(k): k.description["suggested_value"] for k in r["data_schema"].schema
                 if k.description and "suggested_value" in k.description}
    assert suggested == STATE
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_advanced_sends_only_changes_and_logs(hass):
    seen = []
    hass.bus.async_listen("logbook_entry", lambda e: seen.append(e.data.get("message", "")))
    got = []
    sat = make_sat(got)
    entry = await setup(hass, sat)
    r = await open_advanced(hass, entry)
    r = await hass.config_entries.subentries.async_configure(
        r["flow_id"], form_values(wakeword_friday_mode="shadow", ducking_level=15))
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "advanced_saved"
    assert sorted(got) == [("ducking_level", 15), ("wakeword_friday_mode", "shadow")]
    assert any("wakeword_friday_mode off → shadow" in m and m.startswith("Advanced settings") for m in seen)
    assert dict(entry.subentries["s0"].data) == {"host": "127.0.0.1", "port": sat.port, "token": "t"}
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_advanced_needs_one_active_word(hass):
    got = []
    sat = make_sat(got)
    entry = await setup(hass, sat)
    r = await open_advanced(hass, entry)
    r = await hass.config_entries.subentries.async_configure(
        r["flow_id"], form_values(wakeword_sexta_feira_mode="off"))
    assert r["type"] == FlowResultType.FORM and r["errors"] == {"base": "one_active_word"}
    assert got == []
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_advanced_aborts_when_disconnected(hass):
    sat = make_sat([])
    entry = await setup(hass, sat)
    await sat.stop()
    await wait_for(lambda: not entry.runtime_data["s0"].connected)
    r = await open_advanced(hass, entry)
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "satellite_unavailable"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_advanced_ducking_off_does_not_send_level(hass):
    got = []
    sat = make_sat(got)
    entry = await setup(hass, sat)
    r = await open_advanced(hass, entry)
    r = await hass.config_entries.subentries.async_configure(
        r["flow_id"], form_values(ducking=False, ducking_level=15))
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "advanced_saved"
    assert got == [("ducking", False)]
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_advanced_apply_failure_shows_error(hass):
    sat = make_sat([], answer={"ok": False, "error": "rejected"})
    entry = await setup(hass, sat)
    r = await open_advanced(hass, entry)
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], form_values(ducking_level=15))
    assert r["type"] == FlowResultType.FORM and r["errors"] == {"base": "apply_failed"}
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_advanced_without_changes_sends_nothing(hass):
    got = []
    sat = make_sat(got)
    entry = await setup(hass, sat)
    r = await open_advanced(hass, entry)
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], form_values())
    assert r["type"] == FlowResultType.ABORT and got == []
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()


async def test_advanced_swapping_the_active_word_activates_first(hass):
    """Turning friday on and sexta_feira off in one submit never leaves the Pi without an active word."""
    got = []
    sat = make_sat(got, state=dict(STATE, wakeword_friday_mode="active", wakeword_sexta_feira_mode="off"))
    entry = await setup(hass, sat)
    r = await open_advanced(hass, entry)
    r = await hass.config_entries.subentries.async_configure(r["flow_id"], form_values(
        wakeword_friday_mode="off", wakeword_sexta_feira_mode="active"))
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "advanced_saved"
    assert got == [("wakeword_sexta_feira_mode", "active"), ("wakeword_friday_mode", "off")]
    await hass.config_entries.async_unload(entry.entry_id)
    await sat.stop()
