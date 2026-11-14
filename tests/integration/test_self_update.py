"""The integration installs itself through HACS when the server says so."""

from types import SimpleNamespace

import pytest
from homeassistant.core import ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.friday import self_update
from custom_components.friday.const import DOMAIN

from .test_connection import fast_backoff, wait_for  # noqa: F401,E402
from .test_connection import FakeAgent
from .test_setup import DEVICE, hello, setup

REPO = "joseluiz98/ha-friday"


@pytest.fixture
async def installs(hass):
    # the real `update` component first: the integration's own update platform would otherwise replace the mock
    assert await async_setup_component(hass, "update", {})
    calls: list[ServiceCall] = []

    async def _install(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("update", "install", _install)
    return calls


def _hacs_entity(hass, unique_id=f"1234567:{REPO}", platform="hacs", state="off", installed="v0.2.1"):
    ent = er.async_get(hass).async_get_or_create("update", platform, unique_id, suggested_object_id="renamed_by_user")
    if state is not None:
        hass.states.async_set(ent.entity_id, state, {"installed_version": installed})
    return ent


def test_version_key_numeric():
    assert sorted(["v0.10.0", "v0.9.1", "v0.2.0"], key=self_update.version_key) == ["v0.2.0", "v0.9.1", "v0.10.0"]
    assert self_update.version_key("0.2.1") == (0, 2, 1)


async def test_self_update_calls_hacs_install(hass, installs):
    ent = _hacs_entity(hass)
    assert await self_update.maybe_self_update(hass, "v0.3.0", "0.2.1") == "installing"
    assert len(installs) == 1
    assert installs[0].data["entity_id"] == ent.entity_id
    assert installs[0].data["version"] == "v0.3.0"


async def test_self_update_without_hacs_entity(hass, installs):
    _hacs_entity(hass, platform="other_platform")
    _hacs_entity(hass, unique_id="999:somebody/else")  # hacs entity of another repository
    assert await self_update.maybe_self_update(hass, "v0.3.0", "0.2.1") == "no_hacs_entity"
    assert installs == []


async def test_hacs_entity_found_through_repository_id(hass, installs):
    ent = _hacs_entity(hass, unique_id="1234567")
    repo = SimpleNamespace(data=SimpleNamespace(id=1234567))
    hass.data["hacs"] = SimpleNamespace(repositories=SimpleNamespace(
        get_by_full_name=lambda name: repo if name == REPO else None))
    assert await self_update.maybe_self_update(hass, "v0.3.0", "0.2.1") == "installing"
    assert installs[0].data["entity_id"] == ent.entity_id


async def test_same_tag_not_installed_twice(hass, installs):
    _hacs_entity(hass)
    assert await self_update.maybe_self_update(hass, "v0.3.0", "0.2.1") == "installing"
    assert await self_update.maybe_self_update(hass, "v0.3.0", "0.2.1") == "already"
    assert len(installs) == 1


async def test_none_and_not_newer(hass, installs):
    _hacs_entity(hass)
    assert await self_update.maybe_self_update(hass, None, "0.2.1") == "none"
    assert await self_update.maybe_self_update(hass, "v0.2.1", "0.2.1") == "already"
    assert await self_update.maybe_self_update(hass, "garbage", "0.2.1") == "none"
    assert installs == []


async def test_an_older_target_is_installed_rollback(hass, installs):
    _hacs_entity(hass)
    assert await self_update.maybe_self_update(hass, "v0.2.0", "0.2.1") == "installing"
    assert installs[0].data["version"] == "v0.2.0"


async def test_hacs_not_loaded_yet_installs_nothing(hass, installs):
    _hacs_entity(hass, state="unavailable")
    assert await self_update.maybe_self_update(hass, "v0.3.0", "0.2.1") == "no_hacs_entity"
    _hacs_entity(hass, unique_id=f"7:{REPO}x", state=None)
    assert installs == [] and "v0.3.0" not in hass.data[DOMAIN].get("self_update_tried", set())


async def test_what_hacs_already_has_is_never_reinstalled(hass, installs):
    _hacs_entity(hass, installed="v0.3.0")  # on disk, the restart (or a matching manifest) is what is missing
    assert await self_update.maybe_self_update(hass, "v0.3.0", "0.2.1") == "already"
    assert installs == []


async def test_hacs_not_ready_is_tried_again(hass, installs, monkeypatch):
    ent = _hacs_entity(hass, state="unavailable")
    later = []
    monkeypatch.setattr(self_update, "async_call_later", lambda h, s, fn: later.append(fn))
    conn = SimpleNamespace(state={"ota_integration_target": "v0.3.0"})
    assert await self_update.run(hass, conn) == "no_hacs_entity" and len(later) == 1
    hass.states.async_set(ent.entity_id, "off", {"installed_version": "v0.2.1"})
    await later[0](None)
    assert len(installs) == 1 and self_update.restart_pending(hass)


async def test_failed_install_can_be_retried(hass):
    _hacs_entity(hass)
    assert await async_setup_component(hass, "update", {})

    async def _boom(call):
        raise RuntimeError("hacs down")

    hass.services.async_register("update", "install", _boom)
    assert await self_update.maybe_self_update(hass, "v0.3.0", "0.2.1") == "none"
    assert "v0.3.0" not in hass.data[DOMAIN]["self_update_tried"]


async def test_server_target_installs_and_flags_restart(hass, installs):
    _hacs_entity(hass)
    agent = FakeAgent(hello([], {"ota_integration_target": "v99.0.0"}))
    entry = await setup(hass, agent)
    await wait_for(lambda: hass.states.get("binary_sensor.friday_dev1_restart_pending") is not None)
    await wait_for(lambda: len(installs) == 1)
    await wait_for(lambda: hass.states.get("binary_sensor.friday_dev1_restart_pending").state == "on")
    assert self_update.restart_pending(hass) is True
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_no_target_leaves_restart_not_pending(hass, installs):
    _hacs_entity(hass)
    agent = FakeAgent(hello([], {"ota_integration_target": None}))
    entry = await setup(hass, agent)
    await wait_for(lambda: hass.states.get("binary_sensor.friday_dev1_restart_pending") is not None)
    assert hass.states.get("binary_sensor.friday_dev1_restart_pending").state == "off"
    assert installs == []
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_restart_pending_only_on_server(hass, installs):
    agent = FakeAgent(hello([], {}))
    entry = await setup(hass, agent)
    await wait_for(lambda: hass.states.get("binary_sensor.friday_dev1_restart_pending") is not None)
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


def test_restart_pending_is_always_available():
    s = self_update.RestartPendingSensor.__new__(self_update.RestartPendingSensor)
    assert s.available is True
