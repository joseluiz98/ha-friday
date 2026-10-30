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


async def test_options_flow_aborts_when_server_down(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, title="F")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "server_unavailable"


async def test_options_flow_aborts_when_listing_fails(hass):
    sink = []
    agent = make_agent(sink, {"list_models": {"ok": False, "error": "models_unavailable"}})
    entry = await setup(hass, agent)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "server_unavailable"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_full_flow_sends_only_changes(hass):
    sink = []
    agent = make_agent(sink)
    entry = await setup(hass, agent)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "init"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "voice": "gemini-3.8-live", "brain": "gemini-3.8-pro", "lucid": "gemini-3.8-flash",
        "embeddings": "gemini-embedding-2"})
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


async def test_embeddings_change_aborts_when_agent_lacks_estimate(hass):
    sink = []
    agent = make_agent(sink)
    entry = await setup(hass, agent)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "voice": "gemini-3.8-live", "brain": "gemini-3.8-flash", "lucid": "gemini-3.8-flash",
        "embeddings": "gemini-embedding-3"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "embeddings_unavailable"
    assert [c["name"] for c in sink] == ["list_models", "estimate_reindex"]
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_embeddings_change_goes_through_confirmation(hass):
    sink = []
    agent = make_agent(sink, {"estimate_reindex": {"ok": True, "data": {"files": 12, "cost_brl": 0.4}}})
    entry = await setup(hass, agent)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "voice": "gemini-3.8-live", "brain": "gemini-3.8-flash", "lucid": "gemini-3.8-flash",
        "embeddings": "gemini-embedding-3"})
    assert r["type"] == FlowResultType.FORM and r["step_id"] == "embeddings_confirm"
    assert r["description_placeholders"]["model"] == "gemini-embedding-3"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"confirm": True})
    assert r["step_id"] == "voice"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()
