from aiohttp import web
from homeassistant.helpers import device_registry as dr
from homeassistant.components.http import HomeAssistantView  # noqa: F401

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401
from .test_setup import setup

WAV = b"RIFF....WAVEdata"


class RecAgent(FakeAgent):
    def __init__(self):
        h = hello_msg()
        h["device"] = {"id": "sat1", "kind": "satellite", "name": "Sat"}
        super().__init__(h)
        self.seen = []

    async def start(self):
        app = web.Application()
        app.router.add_get("/v1/ws", self.handle)
        app.router.add_get("/v1/recordings/days", self.days)
        app.router.add_get("/v1/recordings", self.listing)
        app.router.add_get("/v1/recordings/{name}", self.rec)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", self.port or 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def rec(self, request):
        self.seen.append(request.headers.get("Authorization"))
        if request.match_info["name"] == "missing.wav":
            return web.Response(status=404)
        return web.Response(body=WAV, content_type="audio/wav")

    async def days(self, request):
        return web.json_response({"days": ["20261103"]})

    async def listing(self, request):
        return web.json_response({"recordings": [
            {"name": "sem_voz_20261103_094322.wav", "size": 5},
            {"name": "20261103_100215.wav", "size": 7}]})


async def device_id(hass, agent):
    entry = await setup(hass, agent)
    conn = next(iter(entry.runtime_data.values()))
    await wait_for(lambda: conn.hello is not None)
    reg = dr.async_get(hass)
    await wait_for(lambda: reg.async_get_device_by_identifier((DOMAIN, "sat1"), entry.entry_id))
    dev = reg.async_get_device_by_identifier((DOMAIN, "sat1"), entry.entry_id)
    return entry, dev.id


async def test_view_streams_wav(hass, hass_client):
    agent = RecAgent()
    entry, dev = await device_id(hass, agent)
    client = await hass_client()
    r = await client.get(f"/api/friday/recording/{dev}/20261103_100215.wav")
    assert r.status == 200 and r.content_type == "audio/wav"
    assert await r.read() == WAV
    assert agent.seen == ["Bearer t"]
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_view_404s(hass, hass_client):
    agent = RecAgent()
    entry, dev = await device_id(hass, agent)
    client = await hass_client()
    assert (await client.get(f"/api/friday/recording/{dev}/x.txt")).status == 404
    assert (await client.get(f"/api/friday/recording/{dev}/%2e%2e%2fx.wav")).status in (400, 404)  # HA core rejects traversal itself
    assert (await client.get("/api/friday/recording/nope/20261103_100215.wav")).status == 404
    assert (await client.get(f"/api/friday/recording/{dev}/missing.wav")).status == 404
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_view_502_when_agent_down(hass, hass_client):
    agent = RecAgent()
    entry, dev = await device_id(hass, agent)
    client = await hass_client()
    await agent.stop()
    r = await client.get(f"/api/friday/recording/{dev}/20261103_100215.wav")
    assert r.status == 502
    await hass.config_entries.async_unload(entry.entry_id)


async def test_view_requires_auth(hass, hass_client_no_auth):
    agent = RecAgent()
    entry, dev = await device_id(hass, agent)
    client = await hass_client_no_auth()
    r = await client.get(f"/api/friday/recording/{dev}/20261103_100215.wav")
    assert r.status == 401
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


def test_title_with_attrs():
    from custom_components.friday.recordings import title_for
    attrs = {"word": "friday", "cause": "no_voice", "score": 0.86, "threshold": 0.35,
             "voice": 0.52, "voice_threshold": 0.6}
    assert title_for("sem_voz_20261103_094322.wav", attrs) == \
        "09:43:22 · friday · rejected (no voice) · 0.86/0.35 · voice 0.52/0.6"
    assert title_for("20261103_100215.wav", {"word": "friday", "score": 0.6}) == \
        "10:02:15 · friday · conversation · 0.6"


def test_title_from_prefix():
    from custom_components.friday.recordings import title_for
    assert title_for("barrada_20261103_094130.wav", None) == "09:41:30 · rejected (verifier)"
    assert title_for("sombra_sexta_feira_20261102_221000.wav", None) == "22:10:00 · shadow · sexta_feira"
    assert title_for("20261103_100215.wav", None) == "10:02:15 · conversation · friday"
    assert title_for("sexta_feira_quase_20261103_100215.wav", None) == "10:02:15 · near miss"
