"""The F.R.I.D.A.Y. integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .connection import FridayConnection
from .const import CONF_AREA_ID, CONF_HOST, CONF_PORT, CONF_TOKEN, DOMAIN

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

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
        area_id = entry.subentries[subentry_id].data.get(CONF_AREA_ID)
        if area_id:
            entry.async_on_unload(_area_listener(hass, entry, subentry_id, connection))
        await connection.start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


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
