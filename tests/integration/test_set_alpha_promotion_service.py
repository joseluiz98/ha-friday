"""friday.set_alpha_promotion: block or allow an alpha's promotion (plan 2026-11-22 Task 3)."""

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.friday.const import DOMAIN

from .test_connection import FakeAgent, fast_backoff, hello_msg, wait_for  # noqa: F401
from .test_set_setting_service import setup

SWITCH = {"key": "ota_alpha_promotion_allowed", "platform": "switch", "name": "Alpha promotion"}


def server(got, answer=None):
    h = hello_msg()
    h["device"] = {"id": "srv", "kind": "server", "name": "srv"}
    h["capabilities"] = [SWITCH]
    h["state"] = {"ota_alpha_promotion_allowed": True}

    async def on_command(ws, cmd):
        got.append({k: v for k, v in cmd.items() if k not in ("type", "id")})
        await ws.send_json({"type": "result", "id": cmd["id"], **(answer or {"ok": True, "tag": "v0.4.3"})})

    return FakeAgent(h, on_command)


async def test_block_sends_command(hass):
    got = []
    await setup(hass, [("server", server(got))])
    await hass.services.async_call(DOMAIN, "set_alpha_promotion", {"allowed": False}, blocking=True)
    assert got == [{"name": "alpha_promotion", "allowed": False}]


async def test_tag_normalized(hass):
    got = []
    await setup(hass, [("server", server(got))])
    await hass.services.async_call(DOMAIN, "set_alpha_promotion", {"tag": "0.4.3", "allowed": True}, blocking=True)
    assert got == [{"name": "alpha_promotion", "tag": "v0.4.3", "allowed": True}]


async def test_bad_tag_is_refused(hass):
    got = []
    await setup(hass, [("server", server(got))])
    with pytest.raises(ServiceValidationError, match="Invalid version"):
        await hass.services.async_call(DOMAIN, "set_alpha_promotion", {"tag": "latest", "allowed": True}, blocking=True)
    assert got == []


@pytest.mark.parametrize(("code", "text"), [
    ("failed", "failed its soak on its own"),
    ("no_alpha", "There is no alpha in soak."),
    ("unknown_tag", "Unknown alpha."),
    ("promoted", "already stable"),
    ("superseded", "A newer release replaced this alpha; it will not be promoted."),
    ("odd", "(odd)"),
])
async def test_error_messages(hass, code, text):
    got = []
    await setup(hass, [("server", server(got, {"ok": False, "error": code}))])
    with pytest.raises(ServiceValidationError, match=text.replace("(", r"\(").replace(")", r"\)")):
        await hass.services.async_call(DOMAIN, "set_alpha_promotion", {"allowed": True}, blocking=True)


async def test_server_not_connected(hass):
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError, match="not connected"):
        await hass.services.async_call(DOMAIN, "set_alpha_promotion", {"allowed": False}, blocking=True)


async def test_allowed_is_required(hass):
    got = []
    await setup(hass, [("server", server(got))])
    with pytest.raises(Exception, match="allowed"):
        await hass.services.async_call(DOMAIN, "set_alpha_promotion", {}, blocking=True)
    assert got == []
