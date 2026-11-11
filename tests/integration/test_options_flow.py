"""Global options flow: models per role, voice settings, budget."""

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401

MODELS = {
    "voice": ["gemini-3.8-live", "gemini-3.9-live"], "brain": ["gemini-3.8-flash", "gemini-3.8-pro"],
    "lucid": ["gemini-3.8-flash", "gemini-3.8-pro"], "embeddings": ["gemini-embedding-2", "gemini-embedding-3"],
    "voices": ["Charon", "Kore"],
    "current": {"voice": "gemini-3.8-live", "brain": "gemini-3.8-flash", "lucid": "gemini-3.8-flash",
                "embeddings": "gemini-embedding-2", "voice_name": "Charon", "speaking_rate": "normal",
                "brief_mode": False},
}


def make_agent(sink, answers=None):
    answers = answers or {}

    async def on_command(ws, cmd):
        sink.append(cmd)
        reply = answers.get(cmd["name"], {"ok": True, "data": {}})
        if cmd["name"] == "list_models" and cmd["name"] not in answers:
            reply = {"ok": True, "data": MODELS}
        if cmd["name"] not in answers and cmd["name"] == "estimate_reindex":
            reply = {"ok": False, "error": "unknown_command"}
        await ws.send_json({"type": "result", "id": cmd["id"], **reply})

    h = hello_msg()
    h["device"] = {"id": "server", "kind": "server", "name": "S"}
    h["capabilities"] = []
    h["state"] = {"budget_brl": 150}
    return FakeAgent(h, on_command)


async def setup(hass, agent):
    await agent.start()
    sub = ConfigSubentryData(data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
                             subentry_type="server", title="s", unique_id=None, subentry_id="s0")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[sub])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data["s0"].hello is not None)
    return entry


async def open_models(hass, entry):
    """Menu -> Models, voice and budget."""
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] == FlowResultType.MENU and r["menu_options"] == ["models", "conversation"]
    return await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "models"})


async def test_options_flow_aborts_when_server_down(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    r = await open_models(hass, entry)
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "server_unavailable"


async def test_options_flow_aborts_when_listing_fails(hass):
    sink = []
    agent = make_agent(sink, {"list_models": {"ok": False, "error": "models_unavailable"}})
    entry = await setup(hass, agent)
    r = await open_models(hass, entry)
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "server_unavailable"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_full_flow_sends_only_changes(hass):
    sink = []
    agent = make_agent(sink)
    entry = await setup(hass, agent)
    r = await open_models(hass, entry)
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "models"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "voice": "gemini-3.8-live", "brain": "gemini-3.8-pro", "lucid": "gemini-3.8-flash"})
    assert r["step_id"] == "voice"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "voice": "Kore", "speaking_rate": "slow", "brief_mode": True})
    assert r["step_id"] == "budget"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"budget_brl": 200})
    assert r["type"] == FlowResultType.CREATE_ENTRY
    by_name = {c["name"]: c for c in sink}
    assert by_name["set_models"]["args"] == {"brain": "gemini-3.8-pro"}
    assert by_name["set_voice_settings"]["args"] == {"voice": "Kore", "speaking_rate": "slow", "brief_mode": True}
    assert by_name["set"]["key"] == "budget_brl" and by_name["set"]["value"] == 200
    assert "estimate_reindex" not in by_name
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_embeddings_is_read_only_in_the_form(hass):
    sink = []
    agent = make_agent(sink)
    entry = await setup(hass, agent)
    r = await open_models(hass, entry)
    assert "embeddings" not in {str(k) for k in r["data_schema"].schema}
    assert r["description_placeholders"]["embeddings"] == "gemini-embedding-2"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_models_save_keeps_general(hass):
    agent = make_agent([])
    entry = await setup(hass, agent)
    hass.config_entries.async_update_entry(entry, options={"general": {"reply_wait": 9}})
    r = await open_models(hass, entry)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "voice": "gemini-3.8-live", "brain": "gemini-3.8-flash", "lucid": "gemini-3.8-flash"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "voice": "Charon", "speaking_rate": "normal", "brief_mode": False})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"budget_brl": 200})
    assert r["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["general"] == {"reply_wait": 9}
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def sat_entry(hass, state):
    h = hello_msg()
    h["device"] = {"id": "sat1", "kind": "satellite", "name": "sat1"}
    h["capabilities"] = []
    h["state"] = state
    agent = FakeAgent(h)
    await agent.start()
    sub = ConfigSubentryData(data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
                             subentry_type="satellite", title="s", unique_id=None, subentry_id="s0")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[sub])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data["s0"].connected)
    return entry, agent


async def open_conversation(hass, entry):
    r = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "conversation"})


VALUES = {"end_of_speech_pause": 1.2, "reply_wait": 9.0, "follow_up_window": 6.0,
          "no_follow_up_while_music": False, "no_follow_up_after_home_commands": True}


async def test_conversation_works_without_server(hass):
    entry, agent = await sat_entry(hass, {"end_of_speech_pause": 0.9, "reply_wait": 8, "follow_up_window": 5})
    r = await open_conversation(hass, entry)
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "conversation"
    assert {str(k) for k in r["data_schema"].schema} == set(VALUES)
    r = await hass.config_entries.options.async_configure(r["flow_id"], VALUES)
    assert r["type"] == FlowResultType.CREATE_ENTRY
    general = entry.options["general"]
    assert general == {"end_of_speech_pause": 1.2, "reply_wait": 9, "follow_up_window": 6,
                       "no_follow_up_while_music": False, "no_follow_up_after_home_commands": True}
    assert isinstance(general["reply_wait"], int)
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_conversation_suggests_from_options_then_satellite_then_default(hass):
    entry, agent = await sat_entry(hass, {"end_of_speech_pause": 0.9, "reply_wait": 11})

    def suggested(r):
        return {str(k): k.description["suggested_value"] for k in r["data_schema"].schema
                if k.description and "suggested_value" in k.description}

    r = await open_conversation(hass, entry)
    s = suggested(r)
    assert s["end_of_speech_pause"] == 0.9 and s["reply_wait"] == 11  # from the satellite
    assert s["follow_up_window"] == 5 and s["no_follow_up_while_music"] is True  # program default
    hass.config_entries.options.async_abort(r["flow_id"])
    hass.config_entries.async_update_entry(entry, options={"general": {"reply_wait": 4}})
    r = await open_conversation(hass, entry)
    assert suggested(r)["reply_wait"] == 4  # from the options
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_conversation_rejects_out_of_range(hass):
    import pytest
    from homeassistant.data_entry_flow import InvalidData

    entry, agent = await sat_entry(hass, {"end_of_speech_pause": 0.8})
    r = await open_conversation(hass, entry)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(r["flow_id"], dict(VALUES, end_of_speech_pause=2.5))
    assert "general" not in entry.options
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_conversation_save_applies_and_logs(hass):
    seen = []
    hass.bus.async_listen("logbook_entry", lambda e: seen.append(e.data.get("message", "")))
    got = []

    h = hello_msg()
    h["device"] = {"id": "sat1", "kind": "satellite", "name": "sat1"}
    h["capabilities"] = [{"key": "reply_wait", "platform": "number", "name": "W", "min": 2, "max": 30, "step": 1,
                          "hidden": True}]
    h["state"] = {"reply_wait": 8}

    async def on_command(ws, cmd):
        got.append((cmd["key"], cmd["value"]))
        await ws.send_json({"type": "result", "id": cmd["id"], "ok": True})

    agent = FakeAgent(h, on_command)
    await agent.start()
    sub = ConfigSubentryData(data={"host": "127.0.0.1", "port": agent.port, "token": "t"},
                             subentry_type="satellite", title="s", unique_id=None, subentry_id="s0")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F", subentries_data=[sub])
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: entry.runtime_data["s0"].connected)
    r = await open_conversation(hass, entry)
    await hass.config_entries.options.async_configure(r["flow_id"], VALUES)
    await wait_for(lambda: got == [("reply_wait", 9)])
    assert any(m.startswith("General settings:") and "reply_wait" in m and "→ 9" in m for m in seen)
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_conversation_refuses_defaults_when_satellite_values_unknown(hass):
    """A satellite with its program off reports no settings: never suggest factory defaults over its real values."""
    entry, agent = await sat_entry(hass, {"program_state": "off"})
    r = await open_conversation(hass, entry)
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "satellite_values_unknown"
    hass.config_entries.async_update_entry(entry, options={"general": {"reply_wait": 4}})
    r = await open_conversation(hass, entry)
    assert r["type"] == FlowResultType.FORM  # the saved general settings are the source
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()
