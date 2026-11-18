"""friday.build_image: the stable release by default (the button), or a given version with or without the v."""

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.friday.const import DOMAIN
from custom_components.friday.services import normalize_version

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401
from .test_set_setting_service import setup

BUTTON = {"key": "image_build", "platform": "button", "name": "Build provisioning image"}


def server(got, answer=None):
    h = hello_msg()
    h["device"] = {"id": "srv", "kind": "server", "name": "srv"}
    h["capabilities"] = [BUTTON]
    h["state"] = {}

    async def on_command(ws, cmd):
        got.append({k: v for k, v in cmd.items() if k not in ("type", "id")})
        await ws.send_json({"type": "result", "id": cmd["id"], **(answer or {"ok": True})})

    return FakeAgent(h, on_command)


@pytest.mark.parametrize(("raw", "tag"), [
    (None, None), ("", None), ("  ", None), ("0.3.8", "v0.3.8"), ("v0.3.8", "v0.3.8"), (" V0.3.10 ", "v0.3.10"),
])
def test_normalize_version(raw, tag):
    assert normalize_version(raw) == tag


@pytest.mark.parametrize("raw", ["0.3", "vv0.3.8", "latest", "0.3.8-rc1", "v0.3.8; rm"])
def test_normalize_version_refuses_anything_else(raw):
    with pytest.raises(ServiceValidationError):
        normalize_version(raw)


async def test_without_version_is_the_button_press(hass):
    got = []
    await setup(hass, [("server", server(got))])
    await hass.services.async_call(DOMAIN, "build_image", {}, blocking=True)
    assert got == [{"name": "press", "key": "image_build"}]


async def test_version_without_v_builds_that_tag(hass):
    got = []
    await setup(hass, [("server", server(got))])
    await hass.services.async_call(DOMAIN, "build_image", {"version": "0.3.8"}, blocking=True)
    assert got == [{"name": "image_build", "tag": "v0.3.8"}]


async def test_the_button_calls_the_action_without_version(hass):
    got = []
    await setup(hass, [("server", server(got))])
    entity_id = next(s.entity_id for s in hass.states.async_all("button") if "image" in s.entity_id)
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    assert got == [{"name": "press", "key": "image_build"}]


async def test_agent_refusal_becomes_a_readable_error(hass):
    got = []
    await setup(hass, [("server", server(got, {"ok": False, "error": "package_missing"}))])
    with pytest.raises(ServiceValidationError, match="no OTA package"):
        await hass.services.async_call(DOMAIN, "build_image", {"version": "v9.9.9"}, blocking=True)


async def test_no_server_is_refused(hass):
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError, match="not connected"):
        await hass.services.async_call(DOMAIN, "build_image", {"version": "0.3.8"}, blocking=True)
