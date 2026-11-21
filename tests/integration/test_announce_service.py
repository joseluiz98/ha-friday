"""friday.announce: the integration asks the server agent to speak a text on the satellites."""

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceNotFound, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401
from .test_set_setting_service import device_id, finish, setup

BOT = {"id": "a1", "satelites": {"sat1": "vou_tocar", "sat2": "na_fila"}, "cache": "miss"}


def sat_device(hass, entry, subentry_id, dev_id):
    """The device HA records for a satellite: tied to its `satellite` subentry (entities are added with it)."""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, config_subentry_id=subentry_id, identifiers={(DOMAIN, dev_id)}).id


def agent(kind, dev_id, got=None, answer=None, name=None):
    h = hello_msg()
    h["device"] = {"id": dev_id, "kind": kind, "name": name or dev_id}
    h["capabilities"] = []
    h["state"] = {}

    async def on_command(ws, cmd):
        if got is not None:
            got.append({k: v for k, v in cmd.items() if k not in ("type", "id")})
        await ws.send_json({"type": "result", "id": cmd["id"], **(answer or {"ok": True, "data": BOT})})

    return FakeAgent(h, on_command)


async def announce(hass, **data):
    return await hass.services.async_call(DOMAIN, "announce", data, blocking=True, return_response=True)


@pytest.mark.parametrize("message", ["", "   ", "x" * 501])
async def test_bad_message_is_refused(hass, message):
    got = []
    agents = [("server", agent("server", "srv", got))]
    entry = await setup(hass, agents)
    with pytest.raises(vol.Invalid):
        await announce(hass, message=message)
    assert got == []
    await finish(hass, entry, agents)


async def test_message_is_stripped_and_500_chars_pass(hass):
    got = []
    agents = [("server", agent("server", "srv", got)), ("satellite", agent("satellite", "sat1"))]
    entry = await setup(hass, agents)
    sat_device(hass, entry, "s1", "sat1")
    await announce(hass, message="  " + "x" * 500 + "  ")
    assert got == [{"name": "announce", "text": "x" * 500, "satellites": ["sat1"]}]
    await finish(hass, entry, agents)


async def test_device_that_is_not_a_satellite_is_refused(hass):
    got = []
    agents = [("server", agent("server", "srv", got)), ("satellite", agent("satellite", "sat1"))]
    entry = await setup(hass, agents)
    sat_device(hass, entry, "s1", "sat1")
    server = sat_device(hass, entry, "s0", "srv")
    for dev in (server, device_id(hass, entry, "nope")):
        with pytest.raises(ServiceValidationError, match="Only satellites can announce.") as err:
            await announce(hass, message="Oi", device_id=dev)
        assert not isinstance(err.value, ServiceNotFound)
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    foreign = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("other", "x")}).id
    for dev in (foreign, "missing"):
        with pytest.raises(ServiceValidationError, match="Not a F.R.I.D.A.Y. device."):
            await announce(hass, message="Oi", device_id=dev)
    assert got == []
    await finish(hass, entry, agents)


async def test_no_device_means_every_satellite(hass):
    got = []
    """Every F.R.I.D.A.Y. satellite device in HA's registry, connected or not; never the server; never None."""
    got = []
    agents = [("server", agent("server", "srv", got)), ("satellite", agent("satellite", "sat1")),
              ("satellite", agent("satellite", "sat2"))]
    entry = await setup(hass, agents)
    sat_device(hass, entry, "s0", "srv")
    sat_device(hass, entry, "s2", "sat2")
    sat_device(hass, entry, "s1", "sat1")
    await announce(hass, message="Oi")
    assert got == [{"name": "announce", "text": "Oi", "satellites": ["sat1", "sat2"]}]
    await finish(hass, entry, agents)


async def test_no_device_and_no_satellite_device_is_refused(hass):
    got = []
    agents = [("server", agent("server", "srv", got))]
    entry = await setup(hass, agents)
    sat_device(hass, entry, "s0", "srv")
    with pytest.raises(ServiceValidationError, match="no satellite"):
        await announce(hass, message="Oi")
    assert got == []
    await finish(hass, entry, agents)


async def test_explicit_device_needs_no_live_hello(hass):
    """The kind comes from the registry (the device's subentry), not from a connection that said hello."""
    got = []
    agents = [("server", agent("server", "srv", got)), ("satellite", agent("satellite", "sat1"))]
    entry = await setup(hass, agents)
    await announce(hass, message="Oi", device_id=sat_device(hass, entry, "s1", "retired"))
    assert [c["satellites"] for c in got] == [["retired"]]
    await finish(hass, entry, agents)


async def test_single_device_id_and_a_list_both_work(hass):
    got = []
    agents = [("server", agent("server", "srv", got)),
              ("satellite", agent("satellite", "sat1")), ("satellite", agent("satellite", "sat2"))]
    entry = await setup(hass, agents)
    d1, d2 = sat_device(hass, entry, "s1", "sat1"), sat_device(hass, entry, "s2", "sat2")
    await announce(hass, message="Oi", device_id=d1)
    await announce(hass, message="Oi", device_id=[d1, d2])
    assert [c["satellites"] for c in got] == [["sat1"], ["sat1", "sat2"]]
    await finish(hass, entry, agents)


async def test_response_is_the_bot_answer(hass):
    agents = [("server", agent("server", "srv")), ("satellite", agent("satellite", "sat1"))]
    entry = await setup(hass, agents)
    sat_device(hass, entry, "s1", "sat1")
    assert await announce(hass, message="Oi") == BOT
    await finish(hass, entry, agents)


async def test_waits_30_seconds_for_the_bot(hass):
    """The bot answers within its 18 s first-round budget and the agent gives up at 25 s: the integration
    must outwait both (it used to give up at 20 s, before a slow TTS plus the first sends could finish)."""
    from unittest.mock import patch

    from custom_components.friday.connection import FridayConnection

    agents = [("server", agent("server", "srv")), ("satellite", agent("satellite", "sat1"))]
    entry = await setup(hass, agents)
    sat_device(hass, entry, "s1", "sat1")
    seen = []
    real = FridayConnection.send

    async def spy(self, name, timeout=10, **fields):
        seen.append((name, timeout))
        return await real(self, name, timeout=timeout, **fields)

    with patch.object(FridayConnection, "send", spy):
        await announce(hass, message="Oi")
    assert seen == [("announce", 30)]
    await finish(hass, entry, agents)


async def test_works_without_asking_for_the_response(hass):
    agents = [("server", agent("server", "srv")), ("satellite", agent("satellite", "sat1"))]
    entry = await setup(hass, agents)
    sat_device(hass, entry, "s1", "sat1")
    assert await hass.services.async_call(DOMAIN, "announce", {"message": "Oi"}, blocking=True) is None
    await finish(hass, entry, agents)


async def test_one_logbook_entry_per_call_with_device_names(hass):
    agents = [("server", agent("server", "srv")),
              ("satellite", agent("satellite", "sat1", name="MK1")), ("satellite", agent("satellite", "sat2", name="MK2"))]
    entry = await setup(hass, agents)
    registry = dr.async_get(hass)
    devs = []
    for sub, sid, name in (("s1", "sat1", "MK1"), ("s2", "sat2", "MK2")):
        devs.append(sat_device(hass, entry, sub, sid))
        registry.async_update_device(devs[-1], name=name)
    seen = []
    hass.bus.async_listen("logbook_entry", lambda e: seen.append(e.data.get("message", "")))
    await announce(hass, message="Oi", device_id=devs)
    await hass.async_block_till_done()
    assert seen == ["Announcement sent to MK1, MK2: MK1 vou_tocar, MK2 na_fila"]
    await finish(hass, entry, agents)


@pytest.mark.parametrize("code", ["bot_unreachable", "invalid", "bot_error", "timeout"])
async def test_server_error_carries_the_code(hass, code):
    agents = [("server", agent("server", "srv", answer={"ok": False, "error": code})),
              ("satellite", agent("satellite", "sat1"))]
    entry = await setup(hass, agents)
    sat_device(hass, entry, "s1", "sat1")
    seen = []
    hass.bus.async_listen("logbook_entry", lambda e: seen.append(e.data.get("message", "")))
    with pytest.raises(ServiceValidationError, match=code):
        await announce(hass, message="Oi")
    await hass.async_block_till_done()
    assert seen == [f"Announcement failed: {code}"]
    await finish(hass, entry, agents)


async def test_no_server_is_refused(hass):
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError, match="not connected"):
        await announce(hass, message="Oi")
