import asyncio

from .test_connection import wait_for
from .test_setup import CAPS, STATE, FakeAgent, entities, hello, setup


async def test_volume_change_from_voice_is_not_echoed(hass):
    got = []

    async def on_command(ws, cmd):
        got.append(cmd)

    agent = FakeAgent(hello(CAPS, STATE), on_command)
    entry = await setup(hass, agent)
    await entities(hass, 3)
    await agent.sockets[0].send_json(
        {"type": "state", "changes": {"vol": 7}, "origin": "voice"})
    await wait_for(lambda: float(hass.states.get("number.friday_dev1_vol").state) == 7)
    await hass.async_block_till_done()
    await asyncio.sleep(0.2)
    assert got == []
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()
