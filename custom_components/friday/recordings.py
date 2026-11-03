"""Satellite activation recordings: proxy view and helpers."""

from __future__ import annotations

import logging
import re

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.wav$")


def valid_name(name: str) -> bool:
    """Plain basename of a WAV, nothing path-like."""
    return bool(NAME_RE.fullmatch(name))


def find_connection(hass: HomeAssistant, device_id: str):
    """HA device-registry id -> the connection of that satellite, if loaded."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    sat_id = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None)
    if sat_id is None:
        return None
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        for conn in (getattr(entry, "runtime_data", None) or {}).values():
            if conn.hello is not None and conn.hello.device.get("id") == sat_id:
                return conn
    return None


class RecordingView(HomeAssistantView):
    """Streams one recording from the satellite behind HA auth."""

    url = "/api/friday/recording/{device_id}/{name}"
    name = "api:friday:recording"
    requires_auth = True

    async def get(self, request: web.Request, device_id: str, name: str) -> web.Response:
        hass = request.app["hass"]
        if not valid_name(name):
            return web.Response(status=404)
        conn = find_connection(hass, device_id)
        if conn is None:
            return web.Response(status=404)
        try:
            status, body, _ = await conn.agent_get(f"/v1/recordings/{name}")
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Satellite recording %s unavailable: %s", name, type(err).__name__)
            return web.Response(status=502)
        if status != 200:
            return web.Response(status=404)
        return web.Response(body=body, content_type="audio/wav")
