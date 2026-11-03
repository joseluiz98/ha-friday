"""Satellite activation recordings in the Media panel."""

from __future__ import annotations

from datetime import datetime
import logging
import re

import aiohttp

from homeassistant.components.media_player import BrowseError, MediaClass
from homeassistant.components.media_source import (
    BrowseMediaSource, MediaSource, MediaSourceItem, PlayMedia, Unresolvable,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .recordings import day_details, find_connection, title_for, valid_name

_LOGGER = logging.getLogger(__name__)
_DAY_RE = re.compile(r"^\d{8}$")
OFFLINE = "satellite offline"


async def async_get_media_source(hass: HomeAssistant) -> FridayMediaSource:
    return FridayMediaSource(hass)


class FridayMediaSource(MediaSource):
    name = "F.R.I.D.A.Y."

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _node(self, identifier, title, *, children=None, can_play=False, can_expand=True,
              media_class=MediaClass.DIRECTORY, content_type="") -> BrowseMediaSource:
        node = BrowseMediaSource(
            domain=DOMAIN, identifier=identifier, media_class=media_class,
            media_content_type=content_type, title=title,
            can_play=can_play, can_expand=can_expand)
        node.children = children
        return node

    async def _get(self, conn, path, params=None):
        try:
            status, body, _ = await conn.agent_get(path, params)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrowseError(OFFLINE) from err
        if status != 200:
            raise BrowseError(f"satellite answered {status}")
        import json
        return json.loads(body)

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        parts = item.identifier.split("/")
        if len(parts) != 3 or not valid_name(parts[2]) or not _DAY_RE.match(parts[1]):
            raise Unresolvable("invalid recording")
        device_id, _, name = parts
        if find_connection(self.hass, device_id) is None:
            raise Unresolvable(OFFLINE)
        return PlayMedia(f"/api/friday/recording/{device_id}/{name}", "audio/wav")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        parts = [p for p in item.identifier.split("/") if p] if item.identifier else []
        if len(parts) > 2:
            raise BrowseError("invalid identifier")
        if not parts:
            return self._root()
        device_id = parts[0]
        conn = find_connection(self.hass, device_id)
        if conn is None or conn.hello is None:
            raise BrowseError(OFFLINE)
        if len(parts) == 1:
            data = await self._get(conn, "/v1/recordings/days")
            kids = [self._node(f"{device_id}/{d}", _day_title(d)) for d in data.get("days", [])
                    if _DAY_RE.match(str(d))]
            return self._node(device_id, conn.hello.device.get("name") or "Satellite", children=kids)
        day = parts[1]
        if not _DAY_RE.match(day):
            raise BrowseError("invalid day")
        data = await self._get(conn, "/v1/recordings", {"date": day})
        details = await day_details(self.hass, device_id, day)
        kids = []
        for rec in data.get("recordings", []):
            name = rec.get("name", "")
            if not valid_name(name):
                continue
            kids.append(self._node(
                f"{device_id}/{day}/{name}", title_for(name, details.get(name)),
                can_play=True, can_expand=False, media_class=MediaClass.MUSIC,
                content_type="audio/wav"))
        return self._node(f"{device_id}/{day}", _day_title(day), children=kids)

    def _root(self) -> BrowseMediaSource:
        reg = dr.async_get(self.hass)
        kids = []
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            for conn in (getattr(entry, "runtime_data", None) or {}).values():
                hello = conn.hello
                if hello is None or hello.device.get("kind") != "satellite":
                    continue
                dev = reg.async_get_device_by_identifier(
                    (DOMAIN, hello.device["id"]), entry.entry_id)
                if dev:
                    kids.append(self._node(dev.id, hello.device.get("name") or dev.id))
        return self._node("", self.name, children=kids)


def _day_title(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y%m%d").strftime("%d/%m/%Y")
    except ValueError:
        return day
