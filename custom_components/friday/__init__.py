"""The F.R.I.D.A.Y. integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .connection import FridayConnection
from .const import CONF_HOST, CONF_PORT, CONF_TOKEN

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
        await connection.start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


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
