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


_STAMP_RE = re.compile(r"^(?P<prefix>.*?)_?(?P<day>\d{8})_(?P<time>\d{6})\.wav$")
_CAUSE_WORDS = {"verifier": "verifier", "no_voice": "no voice", "patience": "patience"}


def _g(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _pair(a, b) -> str | None:
    if a is None:
        return None
    return f"{_g(a)}/{_g(b)}" if b is not None else _g(a)


def title_for(name: str, attrs: dict | None) -> str:
    """Readable title of a recording, from the event attributes or the file name."""
    m = _STAMP_RE.match(name)
    if not m:
        return name
    t = m["time"]
    parts = [f"{t[0:2]}:{t[2:4]}:{t[4:6]}"]
    prefix = m["prefix"]
    if attrs:
        cause = attrs.get("cause")
        if cause:
            result = "rejected"
            if cause in _CAUSE_WORDS:
                result += f" ({_CAUSE_WORDS[cause]})"
        elif prefix.startswith("sombra_"):
            result = "shadow"
        else:
            result = "conversation"
        parts += [p for p in (
            attrs.get("word"), result,
            _pair(attrs.get("score"), attrs.get("threshold")),
            f"voice {_pair(attrs['voice'], attrs.get('voice_threshold'))}"
            if attrs.get("voice") is not None else None,
        ) if p]
        return " · ".join(parts)
    if prefix == "barrada":
        parts.append("rejected (verifier)")
    elif prefix == "sem_voz":
        parts.append("rejected (no voice)")
    elif prefix.startswith("sombra_"):
        parts += ["shadow", prefix[len("sombra_"):]]
    elif prefix == "quase" or prefix.endswith("_quase"):
        parts.append("near miss")
    elif prefix == "contexto":
        parts.append("context")
    else:
        parts += ["conversation", prefix or "friday"]
    return " · ".join(parts)


async def day_details(hass: HomeAssistant, device_id: str, day: str) -> dict[str, dict]:
    """{recording: event attributes} for that device's activation events on a local day."""
    from datetime import datetime, timedelta

    from homeassistant.components.recorder import get_instance, history
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    conn = find_connection(hass, device_id)
    if conn is None or conn.hello is None:
        return {}
    eid = er.async_get(hass).async_get_entity_id(
        "event", DOMAIN, f"{conn.hello.device['id']}_activation")
    if eid is None:
        return {}
    start = dt_util.as_utc(datetime.strptime(day, "%Y%m%d").replace(
        tzinfo=dt_util.get_default_time_zone()))
    end = start + timedelta(days=1)
    try:
        states = await get_instance(hass).async_add_executor_job(
            lambda: history.state_changes_during_period(
                hass, start, end, eid, include_start_time_state=False, no_attributes=False))
    except Exception:  # noqa: BLE001 - recorder missing/failing: titles fall back to the name
        return {}
    return {s.attributes["recording"]: dict(s.attributes)
            for s in states.get(eid, []) if s.attributes.get("recording")}
