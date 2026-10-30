"""The F.R.I.D.A.Y. integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.components.logbook import async_log_entry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .connection import FridayConnection
from .const import (
    CONF_AREA_ID, CONF_HOST, CONF_PORT, CONF_TOKEN, DEVICE_KIND_SATELLITE, DEVICE_KIND_SERVER, DOMAIN, EVENT_CHANGE,
    ORIGIN_HA, READ_ONLY_PLATFORMS,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)
SERVER_VOICE_KEY = "voice"
SATELLITE_VOICE_KEY = "voice_enabled"

type FridayConfigEntry = ConfigEntry[dict[str, FridayConnection]]


async def async_setup_entry(hass: HomeAssistant, entry: FridayConfigEntry) -> bool:
    """Start one connection per subentry; entities appear once each hello arrives."""
    connections: dict[str, FridayConnection] = {}
    for subentry_id, subentry in entry.subentries.items():
        data = subentry.data
        connections[subentry_id] = FridayConnection(
            hass, data[CONF_HOST], data[CONF_PORT], data[CONF_TOKEN], subentry_id
        )
    entry.runtime_data = connections

    # Platforms register their listeners first so no hello is missed.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    for subentry_id, connection in connections.items():
        # A new capability list replaces the old one: rebuild the entities.
        def _reload() -> None:
            hass.config_entries.async_schedule_reload(entry.entry_id)

        entry.async_on_unload(connection.add_capabilities_listener(_reload))
        entry.async_on_unload(connection.add_listener(_change_listener(hass, connection)))
        entry.async_on_unload(connection.add_listener(_voice_propagation_listener(hass, entry, connection)))
        entry.async_on_unload(connection.add_availability_listener(_late_join_listener(hass, entry, connection)))
        entry.async_on_unload(connection.add_log_listener(_log_listener(hass, connection)))
        area_id = entry.subentries[subentry_id].data.get(CONF_AREA_ID)
        if area_id:
            entry.async_on_unload(_area_listener(hass, entry, subentry_id, connection))
        await connection.start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _entity_id(connection, descriptor) -> str:
    device_id = connection.hello.device["id"]
    return f"{descriptor.platform}.friday_{slugify(device_id)}_{slugify(descriptor.key)}"


def _change_listener(hass, connection):
    """Fire friday_change for changes the satellite made itself (not HA's own)."""

    def _on_changes(changes: dict, origin: str) -> None:
        if origin == ORIGIN_HA or connection.hello is None:
            return
        for d in connection.hello.capabilities:
            if d.key not in changes or d.hidden or d.platform in READ_ONLY_PLATFORMS:
                continue
            hass.bus.async_fire(EVENT_CHANGE, {
                "device_id": connection.hello.device["id"],
                "entity_id": _entity_id(connection, d),
                "key": d.key, "value": changes[d.key], "origin": origin,
            })

    return _on_changes


def _voice_propagation_listener(hass, entry, connection):
    """The server's global voice switch, whatever changed it, is mirrored to every satellite."""

    def _on_changes(changes: dict, origin: str) -> None:
        hello = connection.hello
        if (SERVER_VOICE_KEY not in changes or hello is None
                or hello.device.get("kind") != DEVICE_KIND_SERVER):
            return
        value = bool(changes[SERVER_VOICE_KEY])

        async def _push() -> None:
            for other in list(entry.runtime_data.values()):
                h = other.hello
                if other is connection or not other.connected or h is None \
                        or h.device.get("kind") != DEVICE_KIND_SATELLITE:
                    continue
                try:
                    await other.send("set", key=SATELLITE_VOICE_KEY, value=value)
                except HomeAssistantError as err:
                    _LOGGER.warning("Voice switch not applied on satellite %s: %s",
                                    h.device.get("id"), err)

        hass.async_create_background_task(_push(), "friday voice propagation")

    return _on_changes


def _late_join_listener(hass, entry, connection):
    """A satellite that (re)connects while the server voice is known off is told to turn off."""

    def _on_up(up: bool) -> None:
        hello = connection.hello
        if not up or hello is None or hello.device.get("kind") != DEVICE_KIND_SATELLITE:
            return
        if not any(c is not connection and c.hello is not None
                   and c.hello.device.get("kind") == DEVICE_KIND_SERVER
                   and c.state.get(SERVER_VOICE_KEY) is False
                   for c in entry.runtime_data.values()):
            return

        async def _push() -> None:
            try:
                await connection.send("set", key=SATELLITE_VOICE_KEY, value=False)
            except HomeAssistantError as err:
                _LOGGER.warning("Voice switch not applied on satellite %s: %s",
                                hello.device.get("id"), err)

        hass.async_create_background_task(_push(), "friday voice late join")

    return _on_up


def _log_listener(hass, connection):
    """Agent log messages become logbook entries on the device's first entity."""

    def _on_log(message: str, data: dict) -> None:
        if connection.hello is None:
            return
        first = next((d for d in connection.hello.capabilities if not d.hidden), None)
        async_log_entry(
            hass, connection.hello.device.get("name") or "F.R.I.D.A.Y.", message,
            DOMAIN, _entity_id(connection, first) if first else None,
        )

    return _on_log


def _area_listener(hass, entry, subentry_id, connection):
    """Give the satellite's device its area as soon as the agent has said who it is."""

    def _apply(_up: bool = True) -> None:
        if connection.hello is not None:
            apply_satellite_area(hass, entry, subentry_id, connection.hello.device["id"])

    _apply()
    return connection.add_availability_listener(_apply)


def apply_satellite_area(
    hass: HomeAssistant, entry: ConfigEntry, subentry_id: str, device_id: str
) -> None:
    """Set the subentry's area on its device, unless the user already placed it elsewhere."""
    area_id = entry.subentries[subentry_id].data.get(CONF_AREA_ID)
    if not area_id:
        return
    reg = dr.async_get(hass)
    device = reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        identifiers={(DOMAIN, device_id)},
    )
    if device.area_id is None:
        reg.async_update_device(device.id, area_id=area_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Subentry added, removed or edited: reload."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FridayConfigEntry) -> bool:
    """Stop the connections and unload the platforms."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        for connection in entry.runtime_data.values():
            await connection.stop()
    return ok
