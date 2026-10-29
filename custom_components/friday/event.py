"""Event entities from descriptors."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import callback

from .entity import FridayEntity, setup_platform


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(hass, entry, async_add_entities, "event", FridayEvent)


class FridayEvent(FridayEntity, EventEntity):
    def __init__(self, connection, descriptor):
        super().__init__(connection, descriptor)
        self._attr_event_types = list(descriptor.event_types or [])
        self._attr_device_class = descriptor.device_class

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._connection.add_event_listener(self._on_event))

    @callback
    def _on_event(self, key: str, event_type: str, data: dict) -> None:
        if key != self.descriptor.key or event_type not in self.event_types:
            return
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
